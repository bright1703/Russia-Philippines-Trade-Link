"""Scout: предфильтр, порог, поведение при недоступности модели."""
import json

import pytest

from helpers import json_response, mock_llm
from trade_agent.agents import Scout
from trade_agent.agents import taxonomy
from trade_agent.llm import LLMClient
from trade_agent.models import RawItem

RELEVANT = json_response({
    "relevant": True, "category": "MEAT", "score": 4,
    "reason": "BAI открыл аккредитацию для иностранных предприятий",
    "companies": ["мясопереработка"], "hs_codes": ["0203"],
    "geography": "Philippines", "needs_deep_analysis": True,
})
IRRELEVANT = json_response({"relevant": False, "category": "OTHER", "score": 1, "reason": "не связано"})


def _item(title, text, source_type="web", meta=None, item_id=1):
    return RawItem(id=item_id, source="bai", source_type=source_type, title=title,
                   raw_text=text, source_url="https://bai.gov.ph/x", meta=meta or {})


def test_prefilter_drops_short_material(settings):
    scout = Scout(mock_llm(RELEVANT), settings)
    result = scout.evaluate(_item("x", "мало"))
    assert result.dropped and "коротк" in result.drop_reason


def test_prefilter_drops_obvious_noise(settings):
    scout = Scout(mock_llm(RELEVANT), settings)
    result = scout.evaluate(_item("Job vacancy", "hiring a driver, scholarship raffle " * 5))
    assert result.dropped and "шум" in result.drop_reason


def test_prefilter_drops_material_without_industry_keywords(settings):
    scout = Scout(mock_llm(RELEVANT), settings)
    result = scout.evaluate(_item("Office memo", "The regional office will hold a meeting " * 5))
    assert result.dropped


def test_relevant_material_becomes_signal(settings):
    scout = Scout(mock_llm(RELEVANT), settings)
    result = scout.evaluate(_item(
        "BAI opens accreditation for foreign pork establishments",
        "pork import accreditation for foreign meat establishments " * 3))
    assert result.signal is not None
    assert result.signal.category == "MEAT"
    assert result.signal.relevance_score == 4
    assert result.signal.hs_codes == ["0203"]


def test_low_score_is_dropped_by_threshold(settings):
    settings.scout_min_score = 3
    scout = Scout(mock_llm(json_response({"relevant": True, "category": "FOOD", "score": 2,
                                          "reason": "слабо"})), settings)
    result = scout.evaluate(_item("Fish supply note", "fish seafood supply update " * 5))
    assert result.dropped and "ниже порога" in result.drop_reason


def test_unknown_category_falls_back_to_other(settings):
    scout = Scout(mock_llm(json_response({"relevant": True, "category": "НЕЧТО", "score": 4,
                                          "reason": "r"})), settings)
    result = scout.evaluate(_item("Fertilizer news", "urea fertilizer registration " * 5))
    assert result.signal.category == "OTHER"


def test_llm_unavailable_defers_material(settings):
    scout = Scout(LLMClient(None), settings)
    result = scout.evaluate(_item("Fertilizer news", "urea fertilizer registration " * 5))
    assert result.deferred and result.signal is None


def test_broken_model_answer_defers_material(settings):
    scout = Scout(mock_llm("это не json"), settings)
    result = scout.evaluate(_item("Fertilizer news", "urea fertilizer registration " * 5))
    assert result.deferred


def test_heuristic_fallback_when_explicitly_allowed(settings):
    settings.scout_allow_heuristic = True
    scout = Scout(LLMClient(None), settings)
    result = scout.evaluate(_item("Urea fertilizer import", "urea fertilizer import npk " * 5))
    assert result.signal is not None and result.signal.category == "FERTILIZER"


# --- жёсткие правила для тендеров -----------------------------------------
def test_cancelled_tender_is_dropped(settings):
    scout = Scout(mock_llm(RELEVANT), settings)
    result = scout.evaluate(_item("ITB Fishing gear", "supply of fishing gear " * 5,
                                  source_type="tender",
                                  meta={"status": "cancelled", "tender_match_score": 5}))
    assert result.dropped and "cancelled" in result.drop_reason


def test_closed_tender_is_dropped(settings):
    scout = Scout(mock_llm(RELEVANT), settings)
    result = scout.evaluate(_item("ITB Rice", "supply of rice " * 5, source_type="tender",
                                  meta={"deadline_status": "closed", "tender_match_score": 5}))
    assert result.dropped and "дедлайн" in result.drop_reason


def test_irrelevant_tender_is_dropped_by_tender_score(settings):
    scout = Scout(mock_llm(RELEVANT), settings)
    result = scout.evaluate(_item("RFQ Office paper", "office bond paper ink cartridge " * 5,
                                  source_type="tender",
                                  meta={"deadline_status": "open", "tender_match_score": 0}))
    assert result.dropped and "тендерный модуль" in result.drop_reason


# --- таксономия -------------------------------------------------------------
def test_taxonomy_guesses_category_and_hs():
    assert taxonomy.guess_categories("urea fertilizer registration")[0][0] == "FERTILIZER"
    assert "0203" in taxonomy.hs_hints(["MEAT"])
    assert taxonomy.valid_category("meat") == "MEAT"
    assert taxonomy.valid_category("бред") == "OTHER"
