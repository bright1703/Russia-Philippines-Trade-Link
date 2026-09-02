"""
Сквозной прогон конвейера на локальных фикстурах.

Внешние вызовы не выполняются: источники читаются из файлов,
LLM подменён детерминированной заглушкой.
"""
import json

import pytest

from helpers import FIXTURES, json_response
from trade_agent import digest as digest_module
from trade_agent import fetch as fetch_module
from trade_agent import process as process_module
from trade_agent import run_pipeline as pipeline_module
from trade_agent.agents import taxonomy
from trade_agent.companies.import_companies import import_from_file
from trade_agent.db import Database
from trade_agent.llm import LLMClient, MockProvider


def pipeline_handler(system: str, user: str) -> str:
    """Детерминированные ответы вместо модели."""
    if "Scout" in system:
        categories = taxonomy.guess_categories(user)
        category = categories[0][0] if categories else "OTHER"
        score = min(5, 2 + (categories[0][1] if categories else 0))
        return json_response({
            "relevant": score >= 2, "category": category, "score": score,
            "reason": "тестовая заглушка", "companies": [],
            "hs_codes": taxonomy.hs_hints([category])[:2],
            "geography": "Philippines", "needs_deep_analysis": score >= 3,
        })
    if "рецензент" in system:
        return json_response({"verdict": "PASS", "problems": [], "corrected_fields": {},
                              "confidence": 0.8})
    url = ""
    for line in user.split("\n"):
        if line.startswith("URL: ") and line[5:].strip() not in ("", "нет"):
            url = line[5:].strip()
            break
    return json_response({
        "company": "нет прямого совпадения", "summary": "Тестовый разбор.",
        "opportunity": "Тестовая возможность.", "risks": [], "regulation": "не определено",
        "market_data": "нет данных в источнике", "what_to_verify": ["проверить у регулятора"],
        "suggested_actions": ["связаться с источником"], "next_step": "проверить",
        "confidence": 0.7, "sources": [url] if url else [],
    })


@pytest.fixture
def pipeline(settings, monkeypatch):
    monkeypatch.setenv("TRADE_AGENT_FIXTURES", str(FIXTURES))
    fetch_stats = fetch_module.run(settings, days=3650)
    db = Database(settings.db_path)
    import_from_file(FIXTURES / "companies.csv", db)
    db.close()
    client = LLMClient(MockProvider(handler=pipeline_handler),
                       model_fast="mock", model_deep="mock")
    process_stats = process_module.run(settings, limit=200, llm_client=client)
    digest_stats = digest_module.run(settings, days=3650)
    return fetch_stats, process_stats, digest_stats, settings


def test_fetch_collects_from_all_fixture_sources(pipeline):
    fetch_stats, *_ = pipeline
    assert fetch_stats["new"] > 10
    assert fetch_stats["errors"] == 0
    assert fetch_stats["queue"] > 0


def test_fetch_is_idempotent(pipeline):
    _, _, _, settings = pipeline
    second = fetch_module.run(settings, days=3650)
    assert second["new"] == 0
    assert second["duplicates"] > 0


def test_process_filters_noise_and_creates_signals(pipeline):
    _, process_stats, _, _ = pipeline
    assert process_stats["signals"] > 0
    assert process_stats["dropped"] > 0
    assert process_stats["deferred"] == 0
    assert process_stats["queue"] == 0


def test_process_creates_analyses_and_matches(pipeline):
    _, process_stats, _, _ = pipeline
    assert process_stats["analysed"] > 0
    assert process_stats["matches"] > 0


def test_process_is_idempotent(pipeline):
    _, _, _, settings = pipeline
    client = LLMClient(MockProvider(handler=pipeline_handler), model_fast="m", model_deep="m")
    second = process_module.run(settings, limit=200, llm_client=client)
    assert second["signals"] == 0          # всё сырьё уже разобрано
    assert second["scouted"] == 0


def test_digest_is_written(pipeline):
    _, _, digest_stats, settings = pipeline
    latest = settings.digest_dir / "latest.md"
    assert latest.exists()
    text = latest.read_text("utf-8")
    assert "# Trade Agent" in text
    for heading in ("## Срочно", "## Возможности", "## Тендеры", "## Исключено"):
        assert heading in text
    assert digest_stats["errors"] == 0


def test_digest_stays_compact(pipeline):
    _, _, _, settings = pipeline
    text = (settings.digest_dir / "latest.md").read_text("utf-8")
    assert text.count("### ") <= settings.digest_max_per_section * 4


def test_runs_are_logged_for_every_stage(pipeline):
    _, _, _, settings = pipeline
    db = Database(settings.db_path)
    try:
        stages = {run.stage for run in db.recent_runs(20)}
        assert {"fetch", "process", "digest"} <= stages
        assert all(run.status in ("ok", "partial") for run in db.recent_runs(20))
    finally:
        db.close()


def test_llm_outage_keeps_material_in_queue(settings, monkeypatch):
    """Если модель недоступна, сырьё не теряется и ждёт следующего запуска."""
    monkeypatch.setenv("TRADE_AGENT_FIXTURES", str(FIXTURES))
    fetch_module.run(settings, days=3650)
    before = Database(settings.db_path)
    queue_before = before.stats()["queue"]
    before.close()

    stats = process_module.run(settings, limit=200, llm_client=LLMClient(None))
    assert stats["signals"] == 0
    assert stats["deferred"] > 0
    # Очевидный шум отсеивается предфильтром без модели, всё остальное
    # остаётся в очереди и будет обработано при следующем запуске.
    assert stats["queue"] == stats["deferred"]
    assert stats["queue"] + stats["dropped"] == queue_before


def test_pipeline_marks_partial_when_processing_is_deferred(monkeypatch, settings):
    monkeypatch.setattr(
        pipeline_module,
        "run_fetch",
        lambda *args, **kwargs: {"status": "ok"},
    )
    monkeypatch.setattr(
        pipeline_module,
        "run_process",
        lambda *args, **kwargs: {"status": "ok", "deferred": 2},
    )
    monkeypatch.setattr(
        pipeline_module,
        "run_digest",
        lambda *args, **kwargs: {"status": "ok"},
    )
    monkeypatch.setattr(pipeline_module, "send_latest", lambda *args, **kwargs: 1)

    result = pipeline_module.run(settings)

    assert result["process"]["status"] == "partial"
    assert result["status"] == "partial"
