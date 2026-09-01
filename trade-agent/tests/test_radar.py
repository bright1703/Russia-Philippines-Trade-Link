"""Opportunity Radar: прямые и косвенные совпадения."""
from trade_agent.models import Company, RawItem, Signal
from trade_agent.radar import OpportunityRadar, match_signal

MEAT = Company(slug="td-vik", name="ТД ВИК", products=["свинина", "субпродукты"],
               hs_codes=["0203", "0206"], categories=["MEAT"])
FERT = Company(slug="primfert", name="ПримАгроХим", products=["карбамид"],
               hs_codes=["3102"], categories=["FERTILIZER"])
LOG = Company(slug="dv-log", name="ДВ Логистика", products=["контейнерные перевозки"],
              hs_codes=[], categories=["LOGISTICS"])


def _signal(**kwargs):
    base = dict(id=1, raw_item_id=1, category="MEAT", relevance_score=4,
                reason="аккредитация по свинине", hs_codes=["0203"], companies_matched=[])
    base.update(kwargs)
    return Signal(**base)


def test_exact_hs_match_scores_high():
    detail = match_signal(_signal(), RawItem(title="pork accreditation", raw_text="свинина"), MEAT)
    assert detail.score >= 4
    assert any("HS-код 0203" in r for r in detail.reasons)


def test_company_name_in_text_is_strong_signal():
    item = RawItem(title="ТД ВИК получил аккредитацию", raw_text="ТД ВИК")
    assert match_signal(_signal(), item, MEAT).score == 5


def test_indirect_match_through_category_hs_hints(settings):
    """Сигнал не упоминает компанию, но категория ведёт к её товарной группе."""
    signal = _signal(category="FERTILIZER", hs_codes=[], reason="FPA обновил регистрацию удобрений")
    item = RawItem(title="FPA updates fertilizer registration", raw_text="urea npk")
    detail = match_signal(signal, item, FERT)
    assert detail.score >= 2
    assert any("косвенная связь" in r or "категория" in r for r in detail.reasons)


def test_unrelated_company_is_not_matched():
    detail = match_signal(_signal(), RawItem(title="pork", raw_text="свинина"), FERT)
    assert detail.score == 0


def test_radar_respects_threshold(settings):
    settings.radar_min_match_score = 4
    radar = OpportunityRadar(settings)
    matches = radar.match_all(_signal(), RawItem(title="pork", raw_text="свинина"), [MEAT, FERT])
    assert [m.company_slug for m in matches] == ["td-vik"]


def test_radar_sorts_by_score(settings):
    settings.radar_min_match_score = 1
    signal = _signal(category="LOGISTICS", hs_codes=[], reason="порт, контейнеры")
    item = RawItem(title="Port congestion hits cold chain",
                   raw_text="контейнерные перевозки задерживаются в порту")
    matches = OpportunityRadar(settings).match_all(signal, item, [MEAT, FERT, LOG])
    assert matches[0].company_slug == "dv-log"
    assert matches[0].match_score >= matches[-1].match_score


def test_recommended_action_depends_on_category(settings):
    radar = OpportunityRadar(settings)
    tender = radar.match_all(_signal(category="TENDER"),
                             RawItem(title="ITB pork supply", raw_text="свинина 0203"), [MEAT])
    assert "допуск" in tender[0].recommended_action


def test_restrictions_are_surfaced_in_reason(settings):
    company = Company(slug="x", name="X", hs_codes=["0203"], categories=["MEAT"],
                      restrictions=["нет аккредитации BAI"])
    detail = match_signal(_signal(), RawItem(title="pork", raw_text="pork"), company)
    assert any("ограничения" in r for r in detail.reasons)
