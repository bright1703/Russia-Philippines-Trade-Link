"""
Каталог и обязательные триггеры.

Обязательный триггер требует трёх вещей: предмета изменения, юрисдикции
и подтверждающего фрагмента. Ни одно поле сигнала не заполняется данными
из профиля компании.
"""
from trade_agent.alerts import detect_mandatory_policy_alert, detect_rule_change
from trade_agent.companies.audit import build_report, disputes, proposed_sectors
from trade_agent.companies.catalog import normalize_catalog_row
from trade_agent.models import Company, RawItem, VERIFY_NEEDS_CHECK

MARINE = Company(
    slug="marine-motors", name="ООО Морские моторы",
    products=["Судовые двигатели"],
    product_aliases=["судовые двигатели", "marine engines", "ship engines"],
    hs_codes=["8408109900"], sectors=["IND_MACHINERY"])
FLOUR = Company(slug="food", name="ООО Еда", products=["Мука"],
                product_aliases=["мука", "flour"], sectors=["FOOD_GRAIN"])
COSMETICS = Company(slug="primkosmetika", name="Примкосметика",
                    products=["Косметические кремы"],
                    product_aliases=["косметика", "крем"], hs_codes=["3304"],
                    sectors=["COSMETICS"])


# --- нормализация каталога --------------------------------------------------
def test_catalog_row_keeps_source_and_extracts_products_and_hs_codes():
    company = normalize_catalog_row({
        "Название компании": "ООО Морские моторы",
        "Описание компании": "Производитель судовых двигателей.",
        "ИНН": 1234567890,
        "Продукция": "Судовые двигатели\nДвигатели для катеров",
        "Страны экспорта": "Китай\nРеспублика Корея",
        "Отрасль экспорта": "Промышленный экспорт",
        "Перечень товаров/услуг с кодами ТН ВЭД": "8408 109900 - Двигатели",
        "Сайт": "https://example.test",
    }, 17)
    assert company["name"] == "ООО Морские моторы"
    assert company["source_row"] == 17
    assert company["hs_codes"] == ["8408109900"]
    assert "судовые двигатели" in company["product_aliases"]
    assert "marine engines" in company["product_aliases"]
    assert company["data_quality"] == []


def test_catalog_row_marks_missing_fields():
    company = normalize_catalog_row({
        "Название компании": "ООО Без данных",
        "Отрасль экспорта": "Промышленный экспорт",
    }, 5)
    assert "нет продукции" in company["data_quality"]
    assert "нет кодов ТН ВЭД" in company["data_quality"]
    assert "нет сайта" in company["data_quality"]


def test_beverage_and_cosmetics_do_not_become_logistics():
    """
    «Экспорт» в описании больше не делает компанию логистической.

    Подстрока «порт» внутри «экспорт» давала LOGISTICS 126 компаниям.
    """
    drinks = normalize_catalog_row({
        "Название компании": "Славда Групп",
        "Описание компании": "Производство напитков, экспорт продукции",
        "Отрасль экспорта": "Агропромышленный комплекс",
        "Продукция": "Минеральная вода\nЛимонад",
    }, 4)
    cosmetics = normalize_catalog_row({
        "Название компании": "Примкосметика",
        "Описание компании": "Производство косметики, экспорт в страны Азии",
        "Отрасль экспорта": "Промышленный экспорт",
        "Продукция": "Косметические кремы",
    }, 5)
    assert "LOGISTICS" not in drinks["categories"]
    assert "LOGISTICS" not in cosmetics["categories"]
    assert "FOOD_BEVERAGE" in drinks["sectors"]
    assert "COSMETICS" in cosmetics["sectors"]
    # У каждой предложенной отрасли есть основание.
    assert len(drinks["sector_basis"]) == len(drinks["sectors"])


def test_sector_is_not_invented_when_data_is_missing():
    """Выдуманную отрасль ради устранения OTHER не назначаем."""
    company = normalize_catalog_row({"Название компании": "ООО Ничего"}, 9)
    assert company["sectors"] == []
    assert "отрасль не определена автоматически, нужна ручная проверка" \
        in company["data_quality"]


# --- отчёт проверки классификации ------------------------------------------
def test_audit_report_lists_every_record_and_keeps_source_fields():
    companies = [
        Company(slug="a", name="Альфа", inn="111", source_row=3, contacts="тел.",
                industry="Рыба и морепродукты", products=["треска"],
                source_name="Каталог экспортеров Приморского края"),
        Company(slug="b", name="Бета", source_row=4, categories=["OTHER"],
                source_name="Каталог экспортеров Приморского края"),
        Company(slug="c", name="Гамма", profile_path="brain/companies/c.md",
                source_name="brain"),
    ]
    report = build_report(companies)
    for company in companies:
        assert company.name in report
    assert "всего записей: 3" in report
    assert "из каталога экспортеров: 2" in report
    assert "отдельные профили: 1" in report
    assert "с ИНН: 1" in report


def test_audit_flags_national_ten_digit_code():
    company = Company(slug="a", name="Альфа", hs_codes=["8408109900"],
                      products=["двигатели"], sectors=["IND_MACHINERY"])
    notes = disputes(company, ["IND_MACHINERY"])
    assert any("10-значный" in note for note in notes)


def test_audit_proposes_sector_from_catalog_industry():
    company = Company(slug="a", name="Альфа", industry="Лесопромышленный комплекс")
    sectors, basis = proposed_sectors(company)
    assert sectors == ["TIMBER"]
    assert basis and "колонка каталога" in basis[0]


# --- обязательные триггеры --------------------------------------------------
def test_policy_change_for_company_product_is_mandatory():
    item = RawItem(
        id=10, title="DTI removes export duties on marine engines",
        raw_text="The Philippines will waive the tariff for marine engines.",
        source="dti", source_type="web", source_url="https://example.test/news")
    signal = detect_mandatory_policy_alert(item, [MARINE])
    assert signal is not None
    assert signal.must_alert is True
    assert signal.relevance_score == 5
    assert signal.trade_direction == "RU_TO_PH"
    assert signal.jurisdiction == "PH"
    assert "marine engines" in signal.matched_products
    # Код из профиля в сигнал не копируется: в материале его нет.
    assert signal.hs_codes == []
    assert signal.evidence_fragments
    assert signal.verification_status == VERIFY_NEEDS_CHECK


def test_policy_article_without_product_match_is_not_forced():
    item = RawItem(
        id=11, title="DTI removes duties on marine engines",
        raw_text="The Philippines changes the tariff for ship engines.",
        source="dti", source_type="web")
    assert detect_mandatory_policy_alert(item, [FLOUR]) is None


def test_third_country_rule_is_not_mandatory():
    """Пошлины ЮАР на китайскую сталь не связываются с косметикой."""
    item = RawItem(
        id=12, title="South Africa imposes duties on Chinese steel",
        raw_text="Пошлины ЮАР на китайскую сталь введены. В составе может быть "
                 "изменение ставок для основных поставщиков.",
        source="tg:news", source_type="telegram")
    assert detect_mandatory_policy_alert(item, [COSMETICS]) is None


def test_market_review_is_not_a_rule_change():
    """Обзор трески без изменения правил регуляторным событием не становится."""
    text = ("Обзор рынка трески: вылов вырос, цены снизились, "
            "экспортёры оформляют сертификаты качества.")
    assert detect_rule_change(text) is None


def test_brand_story_with_certificate_is_not_a_rule_change():
    text = "Бренд основан в 2023 году и получил сертификат качества на выставке."
    assert detect_rule_change(text) is None


def test_russian_import_rule_goes_to_reverse_direction():
    item = RawItem(
        id=13, title="Россельхознадзор ограничил ввоз",
        raw_text="Россельхознадзор ввёл ограничение на ввоз трески "
                 "с 1 октября 2026 года.",
        source="fsvps", source_type="web")
    fish = Company(slug="f", name="Рыбзавод", products=["треска"],
                   product_aliases=["треска"], sectors=["FISH_SEAFOOD"])
    signal = detect_mandatory_policy_alert(item, [fish])
    assert signal is not None
    assert signal.trade_direction == "PH_TO_RU"
    assert signal.uncertainties == []      # есть дата вступления в силу


def test_mandatory_trigger_without_act_reference_is_marked_uncertain():
    item = RawItem(
        id=14, title="DTI removes export duties on marine engines",
        raw_text="The Philippines will waive the tariff for marine engines.",
        source="dti", source_type="web")
    signal = detect_mandatory_policy_alert(item, [MARINE])
    assert signal.uncertainties
    assert "акт" in signal.uncertainties[0]
