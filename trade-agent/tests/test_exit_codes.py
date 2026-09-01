"""
Коды завершения CLI.

0 — успех, 1 — частичный сбой, 2 — критический сбой.
Реальных источников и API не используется.
"""
import pytest

from helpers import FIXTURES, json_response
from trade_agent import digest as digest_module
from trade_agent import fetch as fetch_module
from trade_agent import process as process_module
from trade_agent.exit_codes import EXIT_CRITICAL, EXIT_OK, EXIT_PARTIAL
from trade_agent.llm import LLMClient, MockProvider


@pytest.fixture
def offline(settings, monkeypatch):
    monkeypatch.setenv("TRADE_AGENT_FIXTURES", str(FIXTURES))
    monkeypatch.setattr("trade_agent.fetch.load_settings", lambda: settings)
    monkeypatch.setattr("trade_agent.process.load_settings", lambda: settings)
    monkeypatch.setattr("trade_agent.digest.load_settings", lambda: settings)
    return settings


# --- fetch ------------------------------------------------------------------
def test_fetch_success_returns_zero(offline):
    assert fetch_module.main(["--days", "3650"]) == EXIT_OK


def test_fetch_unknown_source_is_critical(offline):
    assert fetch_module.main(["--source", "no_such_source"]) == EXIT_CRITICAL


def test_fetch_broken_config_is_critical(offline, monkeypatch, tmp_path):
    broken = tmp_path / "sources.yml"
    broken.write_text("sources: [ unclosed", "utf-8")
    monkeypatch.setattr(offline, "project_dir", tmp_path)
    assert fetch_module.main([]) == EXIT_CRITICAL


def test_fetch_partial_source_failure_is_one(offline, monkeypatch):
    """Сбой одного источника: код 1, но собранные данные не теряются."""
    from trade_agent.sources.base import SourceAdapter, SourceResult

    class _Broken(SourceAdapter):
        source_id = "broken"

        def fetch(self, days=7):
            raise RuntimeError("источник недоступен")

    real_build = fetch_module.build_sources

    def build(settings_, only=None):
        return list(real_build(settings_, only)) + [_Broken({"id": "broken"}, settings_)]

    monkeypatch.setattr(fetch_module, "build_sources", build)
    assert fetch_module.main(["--days", "3650"]) == EXIT_PARTIAL

    from trade_agent.db import Database
    db = Database(offline.db_path)
    try:
        assert db.count_raw_items() > 0          # данные сохранены
    finally:
        db.close()


# --- process ----------------------------------------------------------------
def test_process_with_unavailable_llm_is_partial(offline):
    fetch_module.main(["--days", "3650"])
    assert process_module.main(["--limit", "50"]) == EXIT_PARTIAL


def test_process_success_returns_zero(offline, monkeypatch):
    fetch_module.main(["--days", "3650"])

    def handler(system, user):
        if "Scout" in system:
            return json_response({"relevant": False, "category": "OTHER", "score": 0,
                                  "reason": "не связано"})
        return json_response({})

    client = LLMClient(MockProvider(handler=handler), model_fast="m", model_deep="m")
    monkeypatch.setattr(process_module, "build_client", lambda s: client)
    assert process_module.main(["--limit", "50"]) == EXIT_OK


def test_process_critical_failure(offline, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("база недоступна")

    monkeypatch.setattr(process_module, "sync_companies", boom)
    assert process_module.main([]) == EXIT_CRITICAL


# --- digest -----------------------------------------------------------------
def test_digest_success_returns_zero(offline):
    assert digest_module.main(["--days", "3650"]) == EXIT_OK


def test_digest_critical_when_cannot_write(offline, monkeypatch):
    def boom(*args, **kwargs):
        raise OSError("нет доступа на запись")

    monkeypatch.setattr(digest_module, "write_digest", boom)
    assert digest_module.main([]) == EXIT_CRITICAL


def test_digest_partial_when_unverified_signals_exist(offline, monkeypatch):
    """Неподтверждённые сигналы видны в коде возврата."""
    from trade_agent.db import Database
    from trade_agent.models import RawItem, SIGNAL_NEEDS_REVIEW, Signal
    from trade_agent.utils import content_hash

    db = Database(offline.db_path)
    item = RawItem(source="x", source_type="web", external_id="1", title="t", raw_text="text")
    item.hash = content_hash("x", "1")
    raw_id, _ = db.upsert_raw_item(item)
    db.upsert_signal(Signal(raw_item_id=raw_id, relevance_score=4,
                            status=SIGNAL_NEEDS_REVIEW, review_attempts=3))
    db.close()
    assert digest_module.main(["--days", "3650"]) == EXIT_PARTIAL
