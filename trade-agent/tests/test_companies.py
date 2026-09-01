"""Профили компаний: Markdown, CSV/JSON-импорт, синхронизация с базой."""
import json

from helpers import FIXTURES
from trade_agent.companies.import_companies import (
    import_from_file, row_to_company, write_profile,
)
from trade_agent.companies.loader import load_from_brain, parse_profile_markdown, sync_companies

PROFILE = """---
name: ТД ВИК
slug: td-vik
website: https://example.ru
products: ["свинина", "субпродукты"]
hs_codes: ["0203", "0206"]
categories: ["MEAT"]
status: "профиль заполнен частично"
regulators: ["BAI"]
next_step: "подтвердить аккредитацию"
---

# ТД ВИК

История работы отсутствует.
"""


def test_parse_profile_markdown():
    company = parse_profile_markdown(PROFILE)
    assert company.slug == "td-vik"
    assert company.products == ["свинина", "субпродукты"]
    assert company.hs_codes == ["0203", "0206"]
    assert company.regulators == ["BAI"]


def test_profile_without_front_matter_is_skipped():
    assert parse_profile_markdown("# Просто заметка") is None


def test_profile_without_name_is_skipped():
    assert parse_profile_markdown("---\nslug: x\n---\n\ntext") is None


def test_broken_yaml_does_not_crash():
    assert parse_profile_markdown("---\nname: [unclosed\n---\n\ntext") is None


def test_load_from_brain_skips_template(settings):
    directory = settings.brain_dir / "companies"
    (directory / "td-vik.md").write_text(PROFILE, "utf-8")
    (directory / "_TEMPLATE.md").write_text(PROFILE.replace("ТД ВИК", "Шаблон"), "utf-8")
    companies = load_from_brain(settings.brain_dir)
    assert [c.slug for c in companies] == ["td-vik"]


def test_sync_companies_is_idempotent(db, settings):
    (settings.brain_dir / "companies" / "td-vik.md").write_text(PROFILE, "utf-8")
    created, updated = sync_companies(db, settings.brain_dir)
    assert (created, updated) == (1, 0)
    created, updated = sync_companies(db, settings.brain_dir)
    assert (created, updated) == (0, 1)
    assert len(db.all_companies()) == 1


def test_csv_import(db, settings):
    stats = import_from_file(FIXTURES / "companies.csv", db)
    assert stats["read"] == 3 and stats["created"] == 3
    company = db.get_company("test-meat")
    assert company.hs_codes == ["0203", "0206"]
    assert company.categories == ["MEAT"]


def test_csv_import_is_idempotent(db, settings):
    import_from_file(FIXTURES / "companies.csv", db)
    stats = import_from_file(FIXTURES / "companies.csv", db)
    assert stats["created"] == 0 and stats["updated"] == 3
    assert len(db.all_companies()) == 3


def test_json_import_and_profile_writing(db, settings, tmp_path):
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps([
        {"name": "Новая компания", "products": ["мука"], "hs_codes": ["1101"],
         "categories": ["GRAIN"]},
    ], ensure_ascii=False), "utf-8")
    stats = import_from_file(path, db, settings.brain_dir, write_profiles=True)
    assert stats["created"] == 1 and stats["profiles"] == 1
    written = settings.brain_dir / "companies" / "novaya-kompaniya.md"
    assert written.exists() or list((settings.brain_dir / "companies").glob("*.md"))


def test_row_without_name_is_skipped():
    assert row_to_company({"products": "x"}) is None


def test_existing_profile_is_not_overwritten(settings):
    company = row_to_company({"name": "ТД ВИК", "slug": "td-vik"})
    path = settings.brain_dir / "companies" / "td-vik.md"
    path.write_text("оригинал", "utf-8")
    assert write_profile(company, settings.brain_dir) is None
    assert path.read_text("utf-8") == "оригинал"
    assert write_profile(company, settings.brain_dir, overwrite=True) is not None
    assert path.read_text("utf-8") != "оригинал"


def test_written_profile_escapes_yaml_scalars(settings):
    company = row_to_company({
        "name": "ACME: Inc.",
        "slug": "acme-inc",
        "website": "https://example.test/?a=1&b=2",
        "status": 'A "quoted" status',
    })
    path = write_profile(company, settings.brain_dir)
    parsed = parse_profile_markdown(path.read_text("utf-8"), path)
    assert parsed is not None
    assert parsed.name == "ACME: Inc."
    assert parsed.website == "https://example.test/?a=1&b=2"
    assert parsed.status == 'A "quoted" status'


# ---------------------------------------------------------------------------
# Регрессия: обычная синхронизация не должна терять данные, импортированные
# из каталога. Раньше пустой Markdown-профиль затирал товары и HS-коды.
# ---------------------------------------------------------------------------

EMPTY_PROFILE = """---
name: Тестовый мясокомбинат
slug: test-meat
website: ""
products: []
hs_codes: []
categories: []
status: ""
---

# Тестовый мясокомбинат

Профиль не заполнен.
"""


def test_empty_profile_does_not_erase_imported_data(db, settings):
    """CSV → sync → данные на месте."""
    import_from_file(FIXTURES / "companies.csv", db)
    before = db.get_company("test-meat")
    assert before.products and before.hs_codes and before.categories

    (settings.brain_dir / "companies" / "test-meat.md").write_text(EMPTY_PROFILE, "utf-8")
    sync_companies(db, settings.brain_dir)

    after = db.get_company("test-meat")
    assert after.products == before.products
    assert after.hs_codes == before.hs_codes
    assert after.categories == before.categories
    assert after.name == "Тестовый мясокомбинат"


def test_repeated_sync_is_stable(db, settings):
    import_from_file(FIXTURES / "companies.csv", db)
    (settings.brain_dir / "companies" / "test-meat.md").write_text(EMPTY_PROFILE, "utf-8")
    for _ in range(3):
        sync_companies(db, settings.brain_dir)
    assert db.get_company("test-meat").hs_codes == ["0203", "0206"]


def test_filled_profile_updates_fields(db, settings):
    """Заполненный профиль всё-таки обновляет данные — merge не значит «игнорировать»."""
    import_from_file(FIXTURES / "companies.csv", db)
    profile = EMPTY_PROFILE.replace('products: []', 'products: ["говядина"]') \
                           .replace('hs_codes: []', 'hs_codes: ["0201"]')
    (settings.brain_dir / "companies" / "test-meat.md").write_text(profile, "utf-8")
    sync_companies(db, settings.brain_dir)
    company = db.get_company("test-meat")
    assert company.products == ["говядина"]
    assert company.hs_codes == ["0201"]
    assert company.categories == ["MEAT"]        # пустое поле профиля не обнулило


def test_overwrite_mode_is_explicit(db, settings, tmp_path):
    """Полная перезапись возможна, но только явным флагом."""
    import_from_file(FIXTURES / "companies.csv", db)
    path = tmp_path / "wipe.csv"
    path.write_text("name,slug,products,hs_codes,categories\n"
                    "Тестовый мясокомбинат,test-meat,,,\n", "utf-8")
    import_from_file(path, db)                    # merge — данные целы
    assert db.get_company("test-meat").hs_codes == ["0203", "0206"]
    import_from_file(path, db, overwrite=True)    # явная перезапись
    assert db.get_company("test-meat").hs_codes == []


def test_existing_markdown_profile_is_not_overwritten_silently(db, settings):
    path = settings.brain_dir / "companies" / "test-meat.md"
    path.write_text("оригинал", "utf-8")
    stats = import_from_file(FIXTURES / "companies.csv", db, settings.brain_dir,
                             write_profiles=True)
    assert path.read_text("utf-8") == "оригинал"
    assert stats["profiles_kept"] >= 1


def test_matching_uses_data_that_survived_sync(db, settings):
    """Сквозная регрессия: после синхронизации matching всё ещё находит компанию."""
    from trade_agent.models import RawItem, Signal
    from trade_agent.radar import OpportunityRadar

    import_from_file(FIXTURES / "companies.csv", db)
    (settings.brain_dir / "companies" / "test-meat.md").write_text(EMPTY_PROFILE, "utf-8")
    sync_companies(db, settings.brain_dir)

    signal = Signal(id=1, raw_item_id=1, category="MEAT", relevance_score=4,
                    reason="BAI аккредитация по свинине", hs_codes=["0203"])
    item = RawItem(id=1, title="BAI opens pork accreditation", raw_text="pork свинина")
    matches = OpportunityRadar(settings).match_all(signal, item, db.all_companies())
    assert any(m.company_slug == "test-meat" and m.match_score >= 3 for m in matches)


def test_unknown_upsert_mode_is_rejected(db):
    from trade_agent.models import Company
    import pytest as _pytest
    with _pytest.raises(ValueError):
        db.upsert_company(Company(slug="x", name="X"), mode="wipe")
