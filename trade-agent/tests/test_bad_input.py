"""
Пустые и повреждённые входные данные не должны ронять конвейер.

Ничего сетевого: только локальные файлы и моки.
"""
import json

import pytest

from helpers import FIXTURES, json_response, mock_llm
from trade_agent import fetch as fetch_module
from trade_agent import process as process_module
from trade_agent.agents import Scout
from trade_agent.companies.import_companies import import_from_file
from trade_agent.companies.loader import load_from_brain
from trade_agent.db import Database
from trade_agent.models import RawItem
from trade_agent.sources import build_sources, load_source_configs
from trade_agent.sources.telegram_source import TelegramSource
from trade_agent.sources.web.fixture import FixtureWebAdapter
from trade_agent.utils import content_hash


# --- пустые и битые страницы ------------------------------------------------
def test_empty_html_yields_no_items(settings, tmp_path):
    path = tmp_path / "empty.html"
    path.write_text("", "utf-8")
    result = FixtureWebAdapter({"id": "x", "fixture_path": str(path),
                                "url": "https://x.ph/"}, settings).fetch()
    assert result.items == [] and not result.error


def test_broken_html_does_not_crash(settings, tmp_path):
    path = tmp_path / "broken.html"
    path.write_text("<html><body><a href=", "utf-8")
    result = FixtureWebAdapter({"id": "x", "fixture_path": str(path),
                                "url": "https://x.ph/"}, settings).fetch()
    assert isinstance(result.items, list)


def test_binary_garbage_file_does_not_crash(settings, tmp_path):
    path = tmp_path / "garbage.html"
    path.write_bytes(bytes(range(256)) * 20)
    result = FixtureWebAdapter({"id": "x", "fixture_path": str(path),
                                "url": "https://x.ph/"}, settings).fetch()
    assert isinstance(result.items, list)


# --- повреждённые выгрузки Telegram ----------------------------------------
def test_broken_jsonl_lines_are_skipped(settings, tmp_path):
    directory = tmp_path / "tg"
    directory.mkdir()
    (directory / "export.jsonl").write_text(
        '{"id":1,"text":"импорт свинины на Филиппины"}\n'
        'это не json\n'
        '{"id":2}\n'                       # нет текста
        '{"id":3,"text":"поставки удобрений"}\n', "utf-8")
    result = TelegramSource({"id": "telegram", "mode": "export",
                             "export_dir": str(directory)}, settings).fetch(days=3650)
    assert len(result.items) == 2
    assert not result.error


def test_empty_json_export_is_handled(settings, tmp_path):
    directory = tmp_path / "tg"
    directory.mkdir()
    (directory / "export.json").write_text("", "utf-8")
    result = TelegramSource({"id": "telegram", "mode": "export",
                             "export_dir": str(directory)}, settings).fetch()
    assert result.items == []


# --- повреждённая конфигурация ----------------------------------------------
def test_broken_sources_yaml_raises_clearly(tmp_path):
    path = tmp_path / "sources.yml"
    path.write_text("sources: [ unclosed", "utf-8")
    with pytest.raises(Exception):
        load_source_configs(path)


def test_empty_sources_yaml_gives_no_adapters(settings, tmp_path):
    path = tmp_path / "sources.yml"
    path.write_text("", "utf-8")
    assert build_sources(settings, config_path=path) == []


# --- повреждённые каталоги компаний -----------------------------------------
def test_empty_csv_imports_nothing(db, tmp_path):
    path = tmp_path / "empty.csv"
    path.write_text("name,slug\n", "utf-8")
    stats = import_from_file(path, db)
    assert stats == {"read": 0, "created": 0, "updated": 0, "skipped": 0,
                     "profiles": 0, "profiles_kept": 0}


def test_csv_with_broken_rows_skips_them(db, tmp_path):
    path = tmp_path / "partial.csv"
    path.write_text("name,slug,hs_codes\n,no-name,0203\nХорошая,good,0203\n", "utf-8")
    stats = import_from_file(path, db)
    assert stats["created"] == 1 and stats["skipped"] == 1


def test_broken_json_catalog_raises_not_corrupts_db(db, tmp_path):
    path = tmp_path / "catalog.json"
    path.write_text("{сломано", "utf-8")
    with pytest.raises(ValueError):
        import_from_file(path, db)
    assert db.all_companies() == []


def test_profile_with_broken_yaml_is_skipped(settings):
    directory = settings.brain_dir / "companies"
    (directory / "bad.md").write_text("---\nname: [unclosed\n---\n\ntext", "utf-8")
    (directory / "good.md").write_text("---\nname: Хорошая\nslug: good\n---\n\nтекст", "utf-8")
    companies = load_from_brain(settings.brain_dir)
    assert [c.slug for c in companies] == ["good"]


# --- пустой материал в конвейере --------------------------------------------
def test_empty_raw_text_is_dropped_by_scout(settings):
    scout = Scout(mock_llm(json_response({"relevant": True, "score": 5})), settings)
    result = scout.evaluate(RawItem(id=1, source="x", title="", raw_text=""))
    assert result.dropped


def test_raw_item_with_only_whitespace_is_dropped(settings):
    scout = Scout(mock_llm(json_response({"relevant": True, "score": 5})), settings)
    result = scout.evaluate(RawItem(id=1, source="x", title="   ", raw_text="\n\n \t"))
    assert result.dropped


def test_pipeline_survives_empty_database(settings, monkeypatch):
    """process на пустой базе не падает."""
    monkeypatch.setenv("TRADE_AGENT_FIXTURES", str(FIXTURES))
    stats = process_module.run(settings, limit=10)
    assert stats["scouted"] == 0 and stats["errors"] == 0


def test_raw_item_with_null_bytes_is_stored(settings):
    db = Database(settings.db_path)
    try:
        item = RawItem(source="x", source_type="web", external_id="1",
                       title="Импорт\x00свинины", raw_text="текст\x00с нулевым байтом")
        item.hash = content_hash(item.source, item.external_id, item.title, item.raw_text)
        raw_id, created = db.upsert_raw_item(item)
        assert created and db.get_raw_item(raw_id) is not None
    finally:
        db.close()
