"""Источники: read-only Telegram, дедупликация, изоляция сбоев, фикстуры."""
from pathlib import Path

import pytest

from helpers import FIXTURES
from trade_agent.config import load_settings
from trade_agent.sources import ADAPTERS, apply_fixtures, build_sources, load_source_configs
from trade_agent.sources.base import SourceAdapter, SourceResult
from trade_agent.sources.telegram_source import (
    FORBIDDEN_TELETHON_METHODS, ReadOnlyTelegram, TelegramSource,
)
from trade_agent.sources.tenders_source import TendersSource
from trade_agent.sources.web.fixture import FixtureWebAdapter

PROJECT = Path(__file__).resolve().parent.parent


# --- Telegram read-only ----------------------------------------------------
class _FakeTelethon:
    def iter_messages(self, *a, **k):
        return []

    def send_message(self, *a, **k):
        raise AssertionError("этот метод не должен быть достижим")

    def join_chat(self, *a, **k):
        raise AssertionError("этот метод не должен быть достижим")


@pytest.mark.parametrize("method", FORBIDDEN_TELETHON_METHODS)
def test_telegram_write_methods_are_blocked(method):
    client = ReadOnlyTelegram(_FakeTelethon())
    with pytest.raises(PermissionError):
        getattr(client, method)


def test_start_is_not_in_the_allowed_interface():
    """Авторизация выполняется человеком, а не системой."""
    assert "start" not in ReadOnlyTelegram.ALLOWED
    for name in ("sign_in", "log_out", "send_code_request"):
        assert name not in ReadOnlyTelegram.ALLOWED
    with pytest.raises(PermissionError):
        ReadOnlyTelegram(_FakeTelethon()).start


def test_raw_client_is_not_exposed():
    """Сырой Telethon-клиент наружу не отдаётся."""
    fake = _FakeTelethon()
    client = ReadOnlyTelegram(fake)
    for attr in ("_client", "client", "__dict__", "session"):
        with pytest.raises((PermissionError, AttributeError)):
            getattr(client, attr)
    assert "read-only" in repr(client)


def test_allowed_interface_contains_only_read_methods():
    forbidden_prefixes = ("send", "edit", "delete", "join", "leave", "sign",
                          "start", "log_out", "upload", "pin", "set_")
    for name in ReadOnlyTelegram.ALLOWED:
        assert not name.startswith(forbidden_prefixes), name


def test_attribute_deletion_is_blocked():
    client = ReadOnlyTelegram(_FakeTelethon())
    with pytest.raises(PermissionError):
        del client.iter_messages


def test_telegram_read_methods_are_allowed():
    client = ReadOnlyTelegram(_FakeTelethon())
    assert client.iter_messages("channel") == []


def test_telegram_client_attributes_are_immutable():
    client = ReadOnlyTelegram(_FakeTelethon())
    with pytest.raises(PermissionError):
        client.session = "x"


# --- Telegram export -------------------------------------------------------
def test_telegram_export_mode_reads_jsonl(settings):
    source = TelegramSource({"id": "telegram", "mode": "export",
                             "export_dir": str(FIXTURES / "telegram")}, settings)
    result = source.fetch(days=3650)
    assert len(result.items) == 3
    assert all(i.source_type == "telegram" for i in result.items)
    assert all(i.hash for i in result.items)


def test_telegram_missing_export_dir_reports_error(settings, tmp_path):
    source = TelegramSource({"id": "telegram", "mode": "export",
                             "export_dir": str(tmp_path / "no-such-dir")}, settings)
    result = source.fetch()
    assert result.items == [] and "не найден" in result.error


def test_telegram_off_mode(settings):
    result = TelegramSource({"id": "telegram", "mode": "off"}, settings).fetch()
    assert result.items == [] and result.error


# --- мост к tenders --------------------------------------------------------
def test_tenders_bridge_returns_normalized_items(settings):
    source = TendersSource({"id": "tenders",
                            "fixtures_dir": str(FIXTURES / "tenders")}, settings)
    result = source.fetch(days=3650)
    assert result.items
    tender = result.items[0]
    assert tender.source_type == "tender"
    assert "tender_match_score" in tender.meta
    assert "eligibility_notes" in tender.meta


def test_tenders_bridge_reports_missing_module(settings):
    result = TendersSource({"id": "tenders", "path": "/nonexistent"}, settings).fetch()
    assert result.items == [] and "не найден" in result.error


# --- реестр и фикстуры -----------------------------------------------------
def test_registry_contains_expected_adapters():
    for name in ("telegram", "tenders", "web_html", "web_rss", "fixture"):
        assert name in ADAPTERS


def test_unknown_adapter_is_skipped_not_crashing(settings, tmp_path):
    path = tmp_path / "sources.yml"
    path.write_text("sources:\n  - id: broken\n    adapter: nope\n    enabled: true\n", "utf-8")
    assert build_sources(settings, config_path=path) == []


def test_apply_fixtures_disables_network_sources():
    _, configs = load_source_configs(PROJECT / "sources.yml")
    configs = apply_fixtures(configs, FIXTURES)
    by_id = {c["id"]: c for c in configs}
    assert by_id["bai"]["adapter"] == "fixture" and by_id["bai"]["enabled"]
    assert by_id["dti"]["enabled"] is False        # фикстуры нет — источник выключен
    assert by_id["tenders"]["fixtures_dir"].endswith("tenders")
    assert by_id["telegram"]["enabled"] is True


def test_fixture_web_adapter_extracts_items(settings):
    adapter = FixtureWebAdapter({"id": "bai", "fixture_path": str(FIXTURES / "bai.html"),
                                 "url": "https://www.bai.gov.ph/"}, settings)
    result = adapter.fetch()
    assert len(result.items) >= 2
    assert any("accreditation" in i.title.lower() for i in result.items)
    assert all(i.hash for i in result.items)


def test_missing_fixture_reports_error(settings):
    adapter = FixtureWebAdapter({"id": "x", "fixture_path": "/nope.html"}, settings)
    result = adapter.fetch()
    assert result.items == [] and "не найден" in result.error


# ---------------------------------------------------------------------------
# Фильтрация по периоду: параметр days должен реально работать.
# ---------------------------------------------------------------------------

def test_days_filter_drops_old_publications(settings):
    """Публикация двухлетней давности не должна попасть в выборку за 30 дней."""
    adapter = FixtureWebAdapter({"id": "dated", "url": "https://example.ph/",
                                 "fixture_path": str(FIXTURES / "dated_news.html")}, settings)
    titles = [i.title for i in adapter.fetch(days=30).items]
    assert any("this week" in t for t in titles)
    assert not any("two years ago" in t for t in titles)


def test_large_window_keeps_old_publications(settings):
    adapter = FixtureWebAdapter({"id": "dated", "url": "https://example.ph/",
                                 "fixture_path": str(FIXTURES / "dated_news.html")}, settings)
    titles = [i.title for i in adapter.fetch(days=3650).items]
    assert any("two years ago" in t for t in titles)


def test_undated_publication_is_kept_by_default(settings):
    adapter = FixtureWebAdapter({"id": "dated", "url": "https://example.ph/",
                                 "fixture_path": str(FIXTURES / "dated_news.html")}, settings)
    titles = [i.title for i in adapter.fetch(days=30).items]
    assert any("without a date" in t for t in titles)


def test_undated_publication_can_be_dropped_explicitly(settings):
    adapter = FixtureWebAdapter({"id": "dated", "url": "https://example.ph/",
                                 "undated_policy": "drop",
                                 "fixture_path": str(FIXTURES / "dated_news.html")}, settings)
    titles = [i.title for i in adapter.fetch(days=30).items]
    assert not any("without a date" in t for t in titles)


def test_all_web_sources_are_disabled_by_default():
    """Ни один сетевой веб-источник не должен быть включён без ручной проверки."""
    _, configs = load_source_configs(PROJECT / "sources.yml")
    web = [c for c in configs if c.get("adapter") in ("web_html", "web_rss")]
    assert web, "веб-источники должны быть описаны в конфигурации"
    assert all(not c.get("enabled") for c in web)


def test_enabled_sources_are_only_tenders_by_default():
    _, configs = load_source_configs(PROJECT / "sources.yml")
    enabled = {c["id"] for c in configs if c.get("enabled")}
    assert enabled == {"tenders"}
