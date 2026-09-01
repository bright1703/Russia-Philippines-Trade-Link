"""Reviewer: статические проверки, вердикты, ограничение числа доработок."""
from datetime import date

from helpers import json_response, mock_llm
from trade_agent.agents import Reviewer
from trade_agent.llm import LLMClient
from trade_agent.models import Analysis, Company, RawItem, Signal

PASS = json_response({"verdict": "PASS", "problems": [], "corrected_fields": {}, "confidence": 0.8})
REVISE = json_response({"verdict": "REVISE", "problems": ["HS-код не соответствует товару"],
                        "corrected_fields": {"regulation": "BAI"}, "confidence": 0.5})
REJECT = json_response({"verdict": "REJECT", "problems": ["вывод построен на выдуманных фактах"],
                        "confidence": 0.9})


def _fixture(**kwargs):
    item = RawItem(id=1, source="bai", source_type="web", title="BAI accreditation",
                   raw_text="pork accreditation opens for foreign establishments",
                   source_url="https://bai.gov.ph/x")
    signal = Signal(id=1, raw_item_id=1, category="MEAT", relevance_score=4)
    analysis = Analysis(id=1, signal_id=1, company="ТД ВИК", summary="BAI открыл аккредитацию.",
                        opportunity="подать заявку", regulation="BAI",
                        market_data="нет данных в источнике",
                        sources=["https://bai.gov.ph/x"], confidence=0.7)
    for key, value in kwargs.items():
        setattr(analysis, key, value)
    company = Company(slug="td-vik", name="ТД ВИК")
    return analysis, signal, item, [company]


def test_clean_analysis_passes(settings):
    analysis, signal, item, companies = _fixture()
    review = Reviewer(mock_llm(PASS), settings).review(analysis, signal, item, companies)
    assert review.verdict == "PASS" and review.problems == []


def test_missing_sources_forces_revision(settings):
    analysis, signal, item, companies = _fixture(sources=[])
    review = Reviewer(mock_llm(PASS), settings).review(analysis, signal, item, companies)
    assert review.verdict == "REVISE"
    assert any("нет ни одной ссылки" in p for p in review.problems)


def test_invented_source_is_flagged(settings):
    analysis, signal, item, companies = _fixture(sources=["https://придуманный.example/a"])
    review = Reviewer(mock_llm(PASS), settings).review(analysis, signal, item, companies)
    assert any("отсутствует в исходном материале" in p for p in review.problems)


def test_unsourced_number_is_flagged(settings):
    analysis, signal, item, companies = _fixture(
        market_data="Импорт вырос на 47% за год")
    review = Reviewer(mock_llm(PASS), settings).review(analysis, signal, item, companies)
    assert any("не найдена в исходном материале" in p for p in review.problems)


def test_future_year_is_flagged(settings):
    year = date.today().year + 2
    analysis, signal, item, companies = _fixture(summary=f"Правило вступит в силу в {year} году")
    review = Reviewer(mock_llm(PASS), settings).review(analysis, signal, item, companies)
    assert any("год в будущем" in p for p in review.problems)


def test_mixed_regulators_are_flagged(settings):
    analysis, signal, item, companies = _fixture(
        regulation="Решение согласуют BAI, BFAR и FDA одновременно")
    review = Reviewer(mock_llm(PASS), settings).review(analysis, signal, item, companies)
    assert any("смешаны" in p for p in review.problems)


def test_russian_and_philippine_regulators_confusion(settings):
    analysis, signal, item, companies = _fixture(
        regulation="Разрешение выдаёт Россельхознадзор совместно с BAI")
    review = Reviewer(mock_llm(PASS), settings).review(analysis, signal, item, companies)
    assert any("путаница" in p for p in review.problems)


def test_unknown_company_is_flagged(settings):
    analysis, signal, item, companies = _fixture(company="ООО Неизвестная")
    review = Reviewer(mock_llm(PASS), settings).review(analysis, signal, item, companies)
    assert any("отсутствует среди переданных профилей" in p for p in review.problems)


def test_foreign_restriction_must_be_reflected(settings):
    analysis, signal, item, companies = _fixture()
    item.source_type = "tender"
    item.meta = {"eligibility_notes": ["ОГРАНИЧЕНИЕ: заявлено участие только филиппинских поставщиков"]}
    review = Reviewer(mock_llm(PASS), settings).review(analysis, signal, item, companies)
    assert any("ограничение" in p.lower() for p in review.problems)


def test_duplicate_signal_is_flagged(settings):
    analysis, signal, item, companies = _fixture()
    review = Reviewer(mock_llm(PASS), settings).review(
        analysis, signal, item, companies, known_titles={item.title.lower()})
    assert any("дубликат" in p for p in review.problems)


def test_model_reject_is_respected(settings):
    analysis, signal, item, companies = _fixture()
    review = Reviewer(mock_llm(REJECT), settings).review(analysis, signal, item, companies)
    assert review.verdict == "REJECT"


def test_model_revise_carries_corrections(settings):
    analysis, signal, item, companies = _fixture()
    review = Reviewer(mock_llm(REVISE), settings).review(analysis, signal, item, companies)
    assert review.verdict == "REVISE" and review.corrected_fields == {"regulation": "BAI"}


def test_static_checks_still_work_without_llm(settings):
    """Без модели статические проверки работают, но вердикт — не PASS."""
    analysis, signal, item, companies = _fixture(sources=[])
    review = Reviewer(LLMClient(None), settings).review(analysis, signal, item, companies)
    assert review.verdict == "FAILED"
    assert any("нет ни одной ссылки" in p for p in review.problems)


# ---------------------------------------------------------------------------
# Fail-closed: отсутствие подтверждения никогда не равно PASS.
# ---------------------------------------------------------------------------

from trade_agent.models import (                                  # noqa: E402
    REVIEW_ERROR_EMPTY, REVIEW_ERROR_INVALID, REVIEW_ERROR_UNAVAILABLE,
    REVIEW_ERROR_UNKNOWN_VERDICT, VERDICT_FAILED,
)


def _clean():
    """Анализ без единого замечания — раньше такой получал PASS «по инерции»."""
    return _fixture()


def test_reviewer_unavailable_is_not_pass(settings):
    analysis, signal, item, companies = _clean()
    review = Reviewer(LLMClient(None), settings).review(analysis, signal, item, companies)
    assert review.verdict == VERDICT_FAILED
    assert review.error == REVIEW_ERROR_UNAVAILABLE
    assert review.retryable is True          # временная ошибка — можно повторить


def test_reviewer_empty_response_is_not_pass(settings):
    analysis, signal, item, companies = _clean()
    review = Reviewer(mock_llm("   "), settings).review(analysis, signal, item, companies)
    assert review.verdict == VERDICT_FAILED
    assert review.error == REVIEW_ERROR_EMPTY


def test_reviewer_broken_json_is_not_pass(settings):
    analysis, signal, item, companies = _clean()
    review = Reviewer(mock_llm("{verdict: PASS, это не json"), settings).review(
        analysis, signal, item, companies)
    assert review.verdict == VERDICT_FAILED
    assert review.error == REVIEW_ERROR_INVALID


def test_reviewer_unknown_verdict_is_not_pass(settings):
    analysis, signal, item, companies = _clean()
    review = Reviewer(mock_llm(json_response({"verdict": "ОДОБРЯЮ", "problems": []})),
                      settings).review(analysis, signal, item, companies)
    assert review.verdict == VERDICT_FAILED
    assert review.error == REVIEW_ERROR_UNKNOWN_VERDICT
    assert review.retryable is False         # постоянная ошибка, повторять бессмысленно


def test_reviewer_json_that_is_not_an_object_is_not_pass(settings):
    analysis, signal, item, companies = _clean()
    review = Reviewer(mock_llm("[1, 2, 3]"), settings).review(analysis, signal, item, companies)
    assert review.verdict == VERDICT_FAILED


def test_failed_review_is_never_approved(settings):
    analysis, signal, item, companies = _clean()
    review = Reviewer(LLMClient(None), settings).review(analysis, signal, item, companies)
    assert review.approved is False


def test_revise_without_problems_gets_explicit_reason(settings):
    analysis, signal, item, companies = _clean()
    review = Reviewer(mock_llm(json_response({"verdict": "REVISE", "problems": []})),
                      settings).review(analysis, signal, item, companies)
    assert review.verdict == "REVISE" and review.problems
