"""
Доказательства по тексту: границы слов, словоформы, HS-коды.

Этот модуль — фундамент всех связей. Если он начнёт находить «порт»
внутри «экспорт» или склеивать цифры статьи в код товара, ошибки
вернутся во все остальные части системы.
"""
import pytest

from trade_agent.agents import evidence, taxonomy
from trade_agent.events import canonical_url, event_key, update_fingerprint
from trade_agent.models import RawItem, Signal


# --- границы слова ----------------------------------------------------------
@pytest.mark.parametrize("text,term,expected", [
    ("Компания расширяет экспорт продукции", "порт", False),
    ("Транспортная компания открылась", "порт", False),
    ("Задержки в порту Владивостока", "порт", True),
    ("Отгрузка мукой в мешках", "мука", True),
    ("Цены на рыбу выросли", "рыба", True),
    ("Мукомольное производство", "мука", False),
    ("tariff on marine engines", "marine engines", True),
    ("ремонт судовых двигателей", "судовые двигатели", True),
    ("водитель автобуса", "вода", False),
])
def test_term_matching_respects_word_boundaries(text, term, expected):
    assert bool(evidence.find_term(text, term)) is expected


def test_yo_is_normalized():
    assert evidence.find_term("поставки мёда выросли", "меда")
    assert evidence.normalize("ёлка") == "елка"


def test_generic_words_are_never_evidence():
    for word in ("состав", "может", "экспорт", "бизнес", "продукция"):
        assert evidence.find_term(f"в тексте есть слово {word} и другое", word) is None


def test_hit_carries_fragment_from_original_text():
    hit = evidence.find_term("Филиппины увеличили импорт трески на 20% за квартал.",
                             "треска")
    assert hit is not None
    assert "трески" in hit.fragment


# --- HS-коды ----------------------------------------------------------------
@pytest.mark.parametrize("text,expected", [
    ("Товар HS 0303 попал под пошлину", ["0303"]),
    ("ТН ВЭД 8408 10 9900 — двигатели", ["8408109900"]),
    ("tariff heading 0203 for pork", ["0203"]),
    ("Бренд основан в 2023 году", []),
    ("Телефон +7 423 2400203", []),
    ("Объём 02 тыс. тонн, рост 03 процента", []),
])
def test_only_declared_codes_are_extracted(text, expected):
    assert [hit.code for hit in evidence.declared_hs_codes(text)] == expected


@pytest.mark.parametrize("left,right,relation", [
    ("0303", "0303", "exact"),
    ("030314", "0303", "group4"),
    ("0303", "0306", "group2"),
    ("0303", "8408", None),
])
def test_hs_relation_levels(left, right, relation):
    assert evidence.hs_relation(left, right) == relation


def test_national_ten_digit_code_is_only_a_group_match():
    """Национальный 10-значный код РФ точным филиппинским кодом не считается."""
    assert evidence.hs_relation("8408", "8408109900") == "group4"
    assert evidence.hs_relation("8408109900", "8408109900") == "exact"


# --- отрасли ----------------------------------------------------------------
def test_sector_keywords_do_not_confuse_industry_with_event():
    sectors = dict(taxonomy.guess_sectors("Филиппины ввели пошлину на треску"))
    events = dict(taxonomy.guess_event_types("Филиппины ввели пошлину на треску"))
    assert taxonomy.SECTOR_FISH in sectors
    assert "RULE_CHANGE" in events
    # Пошлина — это тип события, а не отрасль.
    assert "RULE_CHANGE" not in sectors


def test_catalog_industry_maps_to_sector():
    assert taxonomy.sectors_from_industry("Рыба и морепродукты") == ["FISH_SEAFOOD"]
    assert taxonomy.sectors_from_industry("неизвестная отрасль") == []


# --- ключ события -----------------------------------------------------------
def test_reposts_share_event_key():
    first = RawItem(id=1, title="Филиппины отменили пошлину на рыбу",
                    published_at="2026-09-10T08:00:00Z", hash="a")
    second = RawItem(id=2, title="Пошлину на рыбу отменили Филиппины",
                     published_at="2026-09-10T20:00:00Z", hash="b")
    assert event_key(first) == event_key(second)


def test_primary_document_url_wins_over_title():
    first = RawItem(id=1, title="Заголовок один", hash="a",
                    meta={"primary_source_url": "https://customs.gov.ph/order/1?utm=x"})
    second = RawItem(id=2, title="Совсем другой заголовок", hash="b",
                     meta={"primary_source_url": "https://www.customs.gov.ph/order/1/"})
    assert event_key(first) == event_key(second)


def test_canonical_url_drops_tracking():
    assert canonical_url("https://WWW.Example.com/news/1/?utm_source=tg") == \
        "https://example.com/news/1"


def test_fingerprint_changes_only_on_material_update():
    item = RawItem(id=1, title="t", hash="h")
    base = Signal(id=1, event_type="RULE_CHANGE", trade_direction="RU_TO_PH",
                  jurisdiction="PH", effective_from="2026-10-01", relevance_score=4)
    same = Signal(id=1, event_type="RULE_CHANGE", trade_direction="RU_TO_PH",
                  jurisdiction="PH", effective_from="2026-10-01", relevance_score=4,
                  reason="другой пересказ того же")
    changed = Signal(id=1, event_type="RULE_CHANGE", trade_direction="RU_TO_PH",
                     jurisdiction="PH", effective_from="2026-11-01", relevance_score=4)
    assert update_fingerprint(base, item) == update_fingerprint(same, item)
    assert update_fingerprint(base, item) != update_fingerprint(changed, item)
