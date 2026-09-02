from trade_agent.alerts import detect_mandatory_policy_alert
from trade_agent.companies.catalog import normalize_catalog_row
from trade_agent.digest import DigestBuilder
from trade_agent.models import Company, RawItem, Signal


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


def test_policy_change_for_company_product_is_mandatory():
    company = Company(
        slug="marine-motors", name="ООО Морские моторы",
        products=["Судовые двигатели"],
        product_aliases=["судовые двигатели", "marine engines", "ship engines"],
        hs_codes=["8408109900"],
    )
    item = RawItem(
        id=10, title="DTI removes export duties on marine engines",
        raw_text="The Philippines will waive the tariff for marine engines.",
        source="dti", source_type="web", source_url="https://example.test/news",
    )
    signal = detect_mandatory_policy_alert(item, [company])
    assert signal is not None
    assert signal.must_alert is True
    assert signal.relevance_score == 5
    assert signal.companies_matched == [company.name]
    assert "marine engines" in signal.matched_products


def test_policy_article_without_product_match_is_not_forced():
    company = Company(
        slug="food", name="ООО Еда", products=["Мука"],
        product_aliases=["мука", "flour"],
    )
    item = RawItem(
        id=11, title="DTI removes duties on marine engines",
        raw_text="The Philippines changes the tariff for ship engines.",
        source="dti", source_type="web",
    )
    assert detect_mandatory_policy_alert(item, [company]) is None


def test_mandatory_signal_reaches_digest_even_before_review(settings, db):
    item_id, _ = db.upsert_raw_item(RawItem(
        source="dti", source_type="web", title="DTI changes marine engine duties",
        raw_text="The Philippines removed duties for marine engines.",
        source_url="https://example.test/alert", hash="mandatory-alert-hash",
    ))
    db.upsert_signal(Signal(
        raw_item_id=item_id, category="REGULATION", relevance_score=5,
        reason="ОБЯЗАТЕЛЬНЫЙ ТОВАРНЫЙ ТРИГГЕР", matched_products=["marine engines"],
        must_alert=True,
    ))
    builder = DigestBuilder(db, settings)
    digest = builder.build(builder.collect(3650), 3650)
    assert "DTI changes marine engine duties" in digest
    assert "Обязательный товарный триггер" in digest
