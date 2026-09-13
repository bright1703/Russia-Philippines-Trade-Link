"""
Opportunity Radar: доказательства только из материала, два вида связи.

Главное, что здесь проверяется: радар не принимает за улику поля, которые
в сигнал записал обязательный триггер из профиля компании. Иначе связь
подтверждает сама себя.
"""
from trade_agent.models import (
    Company, EVENT_BUYER_REQUEST, EVENT_RULE_CHANGE, DIRECTION_PH_TO_RU,
    DIRECTION_RU_TO_PH, LINK_DIRECT, LINK_SECTOR, RawItem, ROLE_IMPORTER, Signal,
)
from trade_agent.radar import OpportunityRadar, match_signal

MEAT = Company(slug="td-vik", name="ТД ВИК", products=["свинина", "субпродукты"],
               product_aliases=["свинина", "pork"], hs_codes=["0203", "0206"],
               categories=["MEAT"], sectors=["FOOD_MEAT"])
FERT = Company(slug="primfert", name="ПримАгроХим", products=["карбамид"],
               product_aliases=["карбамид", "urea"], hs_codes=["3102"],
               categories=["FERTILIZER"], sectors=["AGRI_INPUTS"])
LOG = Company(slug="dv-log", name="ДВ Логистика", products=["контейнерные перевозки"],
              product_aliases=["контейнерные перевозки"], hs_codes=[],
              categories=["LOGISTICS"], sectors=["LOGISTICS_SERVICES"])
FISH = Company(slug="dalryba", name="Дальрыба", products=["треска"],
               product_aliases=["треска", "cod"], hs_codes=["0303"],
               sectors=["FISH_SEAFOOD"])


def _signal(**kwargs):
    base = dict(id=1, raw_item_id=1, category="MEAT", relevance_score=4,
                reason="аккредитация по свинине", hs_codes=[], companies_matched=[],
                sectors=["FOOD_MEAT"])
    base.update(kwargs)
    return Signal(**base)


# --- круговое подтверждение -------------------------------------------------
def test_company_name_in_signal_is_not_evidence():
    """
    Поля сигнала, заполненные из профиля, уликой не являются.

    Раньше сюда попадали companies_matched и hs_codes обязательного
    триггера, и совпадение подтверждало само себя.
    """
    signal = _signal(companies_matched=["ТД ВИК"], hs_codes=["0203"],
                     sectors=["INDUSTRIAL"],
                     reason="ОБЯЗАТЕЛЬНЫЙ ТРИГГЕР ТД ВИК: 0203")
    item = RawItem(title="South Africa imposes duties on Chinese steel",
                   raw_text="Пошлины на сталь введены в 2023 году.")
    detail = match_signal(signal, item, MEAT)
    assert detail.score == 0


def test_year_in_text_is_not_an_hs_code():
    """Год 2023 и другие числа статьи HS-кодом не становятся."""
    company = Company(slug="x", name="Икс", hs_codes=["2023"],
                      products=["посуда"], product_aliases=["посуда"])
    item = RawItem(title="История бренда", raw_text="Бренд основан в 2023 году.")
    assert match_signal(_signal(sectors=[]), item, company).score == 0


def test_digits_of_different_numbers_are_not_glued():
    company = Company(slug="y", name="Игрек", hs_codes=["0203"], products=["свинина"],
                      product_aliases=["свинина"])
    item = RawItem(title="Отчёт", raw_text="Объём 02 тыс. тонн, рост 03 процента.")
    assert match_signal(_signal(sectors=[]), item, company).score == 0


# --- прямая применимость ----------------------------------------------------
def test_declared_hs_code_in_text_is_direct_evidence():
    item = RawItem(title="Импорт свинины", raw_text="Поставки свинины (HS 0203) выросли.")
    detail = match_signal(_signal(), item, MEAT)
    assert detail.link_type == LINK_DIRECT
    assert detail.score >= 4
    assert any("HS-код 0203" in reason for reason in detail.reasons)
    assert detail.evidence_fragments


def test_company_name_in_material_is_strongest_evidence():
    item = RawItem(title="ТД ВИК получил аккредитацию", raw_text="ТД ВИК прошёл проверку")
    detail = match_signal(_signal(), item, MEAT)
    assert detail.score == 5
    assert detail.link_type == LINK_DIRECT


def test_product_word_boundaries_are_respected():
    """«порт» внутри «экспорт» товаром логистической компании не делает."""
    company = Company(slug="p", name="Портовик", products=["порт"],
                      product_aliases=["порт"], sectors=[])
    item = RawItem(title="Компания расширяет экспорт",
                   raw_text="Транспортная составляющая экспорта выросла.")
    assert match_signal(_signal(sectors=[]), item, company).score == 0


# --- отраслевая связь -------------------------------------------------------
def test_sector_link_is_separate_from_direct():
    signal = _signal(category="FOOD", sectors=["FISH_SEAFOOD"],
                     reason="BFAR меняет порядок для импортёров")
    item = RawItem(title="BFAR обновил порядок ввоза морепродуктов",
                   raw_text="Изменения касаются импортёров морепродуктов.")
    detail = match_signal(signal, item, FISH)
    assert detail.link_type == LINK_SECTOR
    assert "применимость к продукции компании не установлена" in detail.reasons


def test_unrelated_company_is_not_matched():
    """Удобрения и мясо оба относятся к АПК, но связи между ними нет."""
    item = RawItem(title="pork", raw_text="свинина")
    assert match_signal(_signal(), item, FERT).score == 0


def test_broad_company_sector_still_catches_subgroup_news():
    """Компания с широкой отраслью получает связь с новостью про подгруппу."""
    broad = Company(slug="broad", name="Пищепром", products=["консервы"],
                    product_aliases=["консервы"], sectors=["FOOD_AGRI"])
    item = RawItem(title="Поставки свинины", raw_text="Свинина подорожала.")
    assert match_signal(_signal(), item, broad).link_type == LINK_SECTOR


def test_sector_subgroup_inherits_parent_sector():
    honey = Company(slug="med", name="Медовик", products=["мёд"],
                    product_aliases=["мед натуральный"], sectors=["FOOD_HONEY"])
    signal = _signal(category="FOOD", sectors=["FOOD_AGRI"], reason="правила для пищевой продукции")
    item = RawItem(title="Филиппины меняют требования к пищевой продукции",
                   raw_text="Новые требования к продуктам питания.")
    assert match_signal(signal, item, honey).link_type == LINK_SECTOR


# --- роли и направление -----------------------------------------------------
def test_exporter_is_not_called_a_buyer_for_reverse_direction():
    """
    Экспортёр из каталога не становится покупателем филиппинского товара.

    Совпадение продукции может означать конкуренцию, а не закупочный интерес.
    """
    signal = _signal(category="FOOD", sectors=["FISH_SEAFOOD"],
                     trade_direction=DIRECTION_PH_TO_RU)
    item = RawItem(title="Филиппинский экспорт трески в Россию",
                   raw_text="Поставки трески из Филиппин в РФ выросли.")
    detail = match_signal(signal, item, FISH)
    assert detail.role == "unknown"
    radar_matches = OpportunityRadar(_settings()).match_all(signal, item, [FISH])
    action = radar_matches[0].recommended_action
    assert "покупател" not in action.lower()
    assert "уточнить роль" in action


def test_confirmed_importer_role_changes_the_action():
    importer = Company(slug="imp", name="Импортёр", products=["треска"],
                       product_aliases=["треска"], sectors=["FISH_SEAFOOD"],
                       roles=[ROLE_IMPORTER])
    signal = _signal(category="FOOD", sectors=["FISH_SEAFOOD"],
                     trade_direction=DIRECTION_PH_TO_RU)
    item = RawItem(title="Экспорт трески с Филиппин", raw_text="Треска идёт в Россию.")
    matches = OpportunityRadar(_settings()).match_all(signal, item, [importer])
    assert "importer" in matches[0].recommended_action


# --- радар целиком ----------------------------------------------------------
class _Settings:
    radar_min_match_score = 2
    radar_min_sector_score = 2


def _settings():
    return _Settings()


def test_radar_sorts_direct_before_sector():
    signal = _signal(category="MEAT", sectors=["FOOD_MEAT"])
    item = RawItem(title="Поставки свинины", raw_text="Свинина подорожала.")
    sector_only = Company(slug="other-meat", name="Мясокомбинат", products=["колбаса"],
                          product_aliases=["колбаса"], sectors=["FOOD_MEAT"])
    matches = OpportunityRadar(_settings()).match_all(signal, item, [sector_only, MEAT])
    assert [m.company_slug for m in matches] == ["td-vik", "other-meat"]
    assert matches[0].link_type == LINK_DIRECT
    assert matches[1].link_type == LINK_SECTOR


def test_radar_respects_threshold():
    settings = _Settings()
    settings.radar_min_match_score = 4
    settings.radar_min_sector_score = 4
    item = RawItem(title="Поставки свинины", raw_text="Свинина, HS 0203, подорожала.")
    matches = OpportunityRadar(settings).match_all(_signal(), item, [MEAT, FERT])
    assert [m.company_slug for m in matches] == ["td-vik"]


def test_direct_matches_can_be_empty():
    """
    Список компаний для Analyst может быть пустым.

    Подстановка «первых пяти компаний каталога» заставляла модель писать
    про тех, к кому событие отношения не имеет.
    """
    signal = _signal(category="OTHER", sectors=[])
    item = RawItem(title="Общая новость", raw_text="Ничего о наших товарах.")
    assert OpportunityRadar(_settings()).direct_matches(signal, item, [MEAT, FERT, LOG]) == []


def test_tender_bonus_only_with_direct_evidence():
    signal = _signal(category="TENDER", event_type=EVENT_BUYER_REQUEST)
    item = RawItem(title="ITB pork supply", raw_text="Закупка свинины, HS 0203.")
    matches = OpportunityRadar(_settings()).match_all(signal, item, [MEAT])
    assert "допуск" in matches[0].recommended_action


def test_rule_change_action_points_to_primary_document():
    signal = _signal(category="REGULATION", event_type=EVENT_RULE_CHANGE,
                     trade_direction=DIRECTION_RU_TO_PH)
    item = RawItem(title="Филиппины снизили пошлину на свинину",
                   raw_text="Пошлина на свинину снижена.")
    matches = OpportunityRadar(_settings()).match_all(signal, item, [MEAT])
    assert "первичному документу" in matches[0].recommended_action


def test_restrictions_are_surfaced_in_reason():
    company = Company(slug="x", name="Икс", hs_codes=["0203"], products=["свинина"],
                      product_aliases=["свинина"], sectors=["FOOD_MEAT"],
                      restrictions=["нет аккредитации BAI"])
    item = RawItem(title="Свинина", raw_text="Поставки свинины выросли.")
    detail = match_signal(_signal(), item, company)
    assert any("ограничения" in reason for reason in detail.reasons)
