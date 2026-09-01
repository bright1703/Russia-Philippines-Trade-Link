"""
Fail-closed на уровне конвейера.

Проверяем главное: если рецензия не состоялась, сигнал не публикуется,
matches не создаются, в обычный дайджест он не попадает, причина видна
в БД, и дорогого бесконечного повтора LLM-запросов не происходит.
"""
from helpers import FIXTURES, json_response, mock_llm
from trade_agent import digest as digest_module
from trade_agent import process as process_module
from trade_agent.companies.import_companies import import_from_file
from trade_agent.db import Database
from trade_agent.llm import LLMClient, MockProvider
from trade_agent.models import (
    RawItem, SIGNAL_ANALYZED, SIGNAL_FAILED, SIGNAL_NEEDS_REVIEW, SIGNAL_NEW,
)
from trade_agent.utils import content_hash

SCOUT_OK = json_response({
    "relevant": True, "category": "MEAT", "score": 5, "reason": "аккредитация",
    "companies": [], "hs_codes": ["0203"], "geography": "Philippines",
    "needs_deep_analysis": True,
})
ANALYSIS_OK = json_response({
    "company": "нет прямого совпадения", "summary": "BAI открыл аккредитацию.",
    "opportunity": "подать заявку", "risks": [], "regulation": "BAI",
    "market_data": "нет данных в источнике", "what_to_verify": ["перечень документов"],
    "suggested_actions": ["собрать пакет"], "next_step": "запросить перечень",
    "confidence": 0.8, "sources": ["https://bai.gov.ph/x"],
})


class _Scripted:
    """Мок-провайдер: ответы зависят от роли агента."""
    name = "scripted"

    def __init__(self, reviewer_answer, reviewer_exc=None):
        self.reviewer_answer = reviewer_answer
        self.reviewer_exc = reviewer_exc
        self.reviewer_calls = 0
        self.calls = []

    def complete(self, system, user, model, max_tokens, temperature, timeout):
        from trade_agent.llm import LLMResponse, LLMUnavailable
        self.calls.append({"system": system, "user": user, "model": model})
        if "Scout" in system:
            text = SCOUT_OK
        elif "рецензент" in system:
            self.reviewer_calls += 1
            if self.reviewer_exc is not None:
                raise LLMUnavailable(self.reviewer_exc)
            text = self.reviewer_answer
        else:
            text = ANALYSIS_OK
        return LLMResponse(text=text, model=model, provider=self.name)


def _seed_raw(settings):
    db = Database(settings.db_path)
    item = RawItem(source="bai", source_type="web", external_id="reg-1",
                   title="BAI opens pork accreditation for foreign establishments",
                   raw_text="pork accreditation for foreign meat establishments, "
                            "import permit, veterinary clearance " * 3,
                   source_url="https://bai.gov.ph/x")
    item.hash = content_hash(item.source, item.external_id, item.title, item.raw_text)
    db.upsert_raw_item(item)
    import_from_file(FIXTURES / "companies.csv", db)
    db.close()


def _run(settings, reviewer_answer=None, reviewer_exc=None):
    provider = _Scripted(reviewer_answer, reviewer_exc)
    client = LLMClient(provider, model_fast="m", model_deep="m", retries=1)
    stats = process_module.run(settings, limit=50, llm_client=client)
    return stats, provider


def _state(settings):
    db = Database(settings.db_path)
    try:
        signals = db.signals_since(3650)
        return {
            "signals": signals,
            "matches": db.matches_since(3650, 0),
            "stats": db.stats(),
        }
    finally:
        db.close()


# --- Reviewer недоступен, анализ выглядит корректно -------------------------
def test_unavailable_reviewer_blocks_publication(settings):
    _seed_raw(settings)
    stats, _ = _run(settings, reviewer_exc="сеть недоступна")
    state = _state(settings)
    assert stats["matches"] == 0
    assert state["matches"] == []
    assert all(s.status != SIGNAL_ANALYZED for s in state["signals"])
    assert state["stats"]["reviews_failed"] >= 1


def test_unavailable_reviewer_keeps_signal_retryable(settings):
    _seed_raw(settings)
    _run(settings, reviewer_exc="503")
    signal = [s for s in _state(settings)["signals"] if s.relevance_score > 0][0]
    assert signal.status == SIGNAL_NEW           # вернётся в следующий запуск
    assert signal.review_attempts == 1
    assert signal.last_error == "reviewer_unavailable"


def test_retries_are_bounded_and_end_in_failed(settings):
    """Дорогого бесконечного повтора быть не должно."""
    _seed_raw(settings)
    total_reviewer_calls = 0
    for _ in range(6):
        _, provider = _run(settings, reviewer_exc="503")
        total_reviewer_calls += provider.reviewer_calls
    signal = [s for s in _state(settings)["signals"] if s.relevance_score > 0][0]
    assert signal.status == SIGNAL_FAILED
    assert signal.review_attempts <= settings.reviewer_max_revisions + 1
    # После исчерпания попыток сигнал больше не берётся в работу.
    assert total_reviewer_calls <= settings.reviewer_max_revisions + 1


# --- пустой / сломанный / неизвестный ответ --------------------------------
def test_empty_reviewer_response_blocks_publication(settings):
    _seed_raw(settings)
    stats, _ = _run(settings, reviewer_answer="")
    assert stats["matches"] == 0
    assert _state(settings)["matches"] == []


def test_broken_json_blocks_publication(settings):
    _seed_raw(settings)
    stats, _ = _run(settings, reviewer_answer="{сломано")
    assert stats["matches"] == 0
    assert _state(settings)["matches"] == []


def test_unknown_verdict_marks_signal_failed_immediately(settings):
    _seed_raw(settings)
    _run(settings, reviewer_answer=json_response({"verdict": "ХОРОШО"}))
    signal = [s for s in _state(settings)["signals"] if s.relevance_score > 0][0]
    assert signal.status == SIGNAL_FAILED        # постоянная ошибка, без повторов
    assert signal.last_error == "reviewer_unknown_verdict"


# --- исчерпание доработок ---------------------------------------------------
def test_max_revisions_ends_in_needs_review(settings):
    _seed_raw(settings)
    revise = json_response({"verdict": "REVISE", "problems": ["исправь HS-код"]})
    stats, _ = _run(settings, reviewer_answer=revise)
    signal = [s for s in _state(settings)["signals"] if s.relevance_score > 0][0]
    assert signal.status == SIGNAL_NEEDS_REVIEW
    assert signal.last_error == "reviewer_max_revisions"
    assert stats["needs_review"] == 1
    assert stats["matches"] == 0
    assert _state(settings)["matches"] == []


# --- дайджест ---------------------------------------------------------------
def test_unverified_signal_is_absent_from_digest(settings):
    _seed_raw(settings)
    _run(settings, reviewer_exc="503")
    digest_module.run(settings, days=3650)
    text = (settings.digest_dir / "latest.md").read_text("utf-8")
    body = text.split("## Исключено")[0]
    assert "BAI opens pork accreditation" not in body
    assert "Не подтверждено рецензентом" in text


def test_needs_review_signal_is_counted_but_not_published(settings):
    _seed_raw(settings)
    _run(settings, reviewer_answer=json_response({"verdict": "REVISE", "problems": ["x"]}))
    digest_module.run(settings, days=3650)
    text = (settings.digest_dir / "latest.md").read_text("utf-8")
    assert "BAI opens pork accreditation" not in text.split("## Исключено")[0]
    assert "reviewer_max_revisions" in text


# --- контрольный положительный сценарий -------------------------------------
def test_pass_verdict_still_publishes(settings):
    """Проверяем, что fail-closed не сломал нормальный путь."""
    _seed_raw(settings)
    stats, _ = _run(settings, reviewer_answer=json_response(
        {"verdict": "PASS", "problems": [], "corrected_fields": {}, "confidence": 0.9}))
    signal = [s for s in _state(settings)["signals"] if s.relevance_score > 0][0]
    assert signal.status == SIGNAL_ANALYZED
    assert stats["matches"] > 0
    assert _state(settings)["matches"]
