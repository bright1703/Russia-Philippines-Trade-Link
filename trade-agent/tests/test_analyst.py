"""Analyst: разбор ответа модели, ограничение источников, недоступность LLM."""
from helpers import json_response, mock_llm
from trade_agent.agents import Analyst
from trade_agent.llm import LLMClient
from trade_agent.models import Company, RawItem, Signal

GOOD = json_response({
    "company": "ТД ВИК",
    "summary": "BAI открыл приём заявок на аккредитацию.",
    "opportunity": "Возможность подать заявку на аккредитацию предприятия.",
    "risks": ["сроки рассмотрения неизвестны"],
    "regulation": "BAI, Memorandum Order 2026",
    "market_data": "нет данных в источнике",
    "what_to_verify": ["перечень документов у BAI"],
    "suggested_actions": ["собрать пакет документов"],
    "next_step": "запросить перечень документов",
    "confidence": 0.7,
    "sources": ["https://bai.gov.ph/x", "https://выдуманный-источник.example/y"],
})


def _fixture():
    signal = Signal(id=1, raw_item_id=1, category="MEAT", relevance_score=4,
                    reason="аккредитация", hs_codes=["0203"])
    item = RawItem(id=1, source="bai", source_type="web", title="BAI accreditation",
                   raw_text="pork accreditation", source_url="https://bai.gov.ph/x")
    company = Company(slug="td-vik", name="ТД ВИК", products=["свинина"],
                      hs_codes=["0203"], categories=["MEAT"])
    return signal, item, company


def test_analyst_parses_full_response(settings):
    signal, item, company = _fixture()
    analysis = Analyst(mock_llm(GOOD), settings).analyse(signal, item, [company])
    assert analysis.company == "ТД ВИК"
    assert analysis.confidence == 0.7
    assert analysis.risks == ["сроки рассмотрения неизвестны"]
    assert analysis.next_step.startswith("запросить")


def test_analyst_drops_sources_not_present_in_material(settings):
    signal, item, company = _fixture()
    analysis = Analyst(mock_llm(GOOD), settings).analyse(signal, item, [company])
    assert analysis.sources == ["https://bai.gov.ph/x"]


def test_analyst_clamps_confidence(settings):
    signal, item, company = _fixture()
    payload = json_response({"summary": "s", "confidence": 7})
    analysis = Analyst(mock_llm(payload), settings).analyse(signal, item, [company])
    assert analysis.confidence == 1.0


def test_analyst_handles_broken_json(settings):
    signal, item, company = _fixture()
    assert Analyst(mock_llm("сломанный ответ"), settings).analyse(signal, item, [company]) is None


def test_analyst_returns_none_when_llm_unavailable(settings):
    signal, item, company = _fixture()
    assert Analyst(LLMClient(None), settings).analyse(signal, item, [company]) is None


def test_revision_prompt_includes_reviewer_problems(settings):
    signal, item, company = _fixture()
    llm = mock_llm(GOOD, GOOD)
    analyst = Analyst(llm, settings)
    previous = analyst.analyse(signal, item, [company])
    analyst.analyse(signal, item, [company], revision=1,
                    problems=["цифра без источника"], previous=previous)
    assert "цифра без источника" in llm.provider.calls[1]["user"]
    assert "рецензент" in llm.provider.calls[1]["user"]


def test_tender_context_reaches_the_prompt(settings):
    signal, item, company = _fixture()
    item.source_type = "tender"
    item.meta = {"agency": "BFAR", "closing_date": "2026-09-25",
                 "eligibility_notes": ["ОГРАНИЧЕНИЕ: только для филиппинских поставщиков"]}
    llm = mock_llm(GOOD)
    Analyst(llm, settings).analyse(signal, item, [company])
    prompt = llm.provider.calls[0]["user"]
    assert "BFAR" in prompt and "2026-09-25" in prompt and "ОГРАНИЧЕНИЕ" in prompt
