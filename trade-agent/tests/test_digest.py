"""Дайджест: разделы, фильтрация шума, запись файлов."""
from datetime import date

from trade_agent.db import Database
from trade_agent.digest import DigestBuilder, write_digest
from trade_agent.digest import run as digest_run
from trade_agent.models import (
    Analysis, Company, Match, RawItem, Review, Signal, SIGNAL_REJECTED,
)
from trade_agent.utils import content_hash


def _seed(db, settings):
    def raw(**kwargs):
        item = RawItem(**kwargs)
        item.hash = content_hash(item.source, item.external_id, item.title, item.raw_text)
        return db.upsert_raw_item(item)[0]

    db.upsert_company(Company(slug="td-vik", name="ТД ВИК", hs_codes=["0203"],
                              categories=["MEAT"], products=["свинина"]))

    urgent_raw = raw(source="bfar_bids", source_type="tender", external_id="ITB-1",
                     title="ITB — Supply of Frozen Fish", raw_text="frozen fish supply",
                     source_url="https://bfar/1",
                     meta={"agency": "BFAR", "closing_date": "2026-09-03",
                           "deadline_status": "urgent", "status": "open",
                           "tender_match_score": 5, "eligibility_notes": ["проверить допуск"]})
    urgent_id, _ = db.upsert_signal(Signal(raw_item_id=urgent_raw, category="TENDER",
                                           relevance_score=5, reason="совпал профиль"))

    closed_raw = raw(source="da_bid", source_type="tender", external_id="ITB-2",
                     title="ITB — Supply of Rice (closed)", raw_text="rice",
                     meta={"deadline_status": "closed", "status": "closed",
                           "tender_match_score": 5})
    db.upsert_signal(Signal(raw_item_id=closed_raw, category="TENDER", relevance_score=4,
                            reason="просрочено"))

    reg_raw = raw(source="bai", source_type="web", external_id="reg-1",
                  title="BAI opens pork accreditation", raw_text="pork accreditation",
                  source_url="https://bai/1")
    reg_id, _ = db.upsert_signal(Signal(raw_item_id=reg_raw, category="REGULATION",
                                        relevance_score=4, reason="регуляторное изменение"))

    weak_raw = raw(source="tg:x", source_type="telegram", external_id="tg-1",
                   title="Слабый сигнал про порт", raw_text="порт")
    db.upsert_signal(Signal(raw_item_id=weak_raw, category="LOGISTICS", relevance_score=2,
                            reason="слабая связь"))

    noise_raw = raw(source="tg:x", source_type="telegram", external_id="tg-2",
                    title="Шум", raw_text="розыгрыш призов")
    db.upsert_signal(Signal(raw_item_id=noise_raw, category="OTHER", relevance_score=0,
                            reason="явный шум по стоп-словам", status=SIGNAL_REJECTED))

    good = Analysis(signal_id=reg_id, company="ТД ВИК", summary="BAI открыл аккредитацию",
                    opportunity="подать заявку", confidence=0.8, sources=["https://bai/1"],
                    next_step="запросить перечень документов")
    good.id = db.insert_analysis(good)
    db.insert_review(Review(analysis_id=good.id, verdict="PASS"))

    weak = Analysis(signal_id=urgent_id, summary="слабый вывод", confidence=0.1)
    weak.id = db.insert_analysis(weak)
    db.insert_review(Review(analysis_id=weak.id, verdict="PASS"))

    db.upsert_match(Match(company_slug="td-vik", signal_id=reg_id, match_score=4,
                          reason="совпал HS 0203", recommended_action="проверить аккредитацию"))
    return {"urgent": urgent_id, "reg": reg_id}


def test_digest_has_all_required_sections(db, settings):
    _seed(db, settings)
    builder = DigestBuilder(db, settings)
    markdown = builder.build(builder.collect(30), 30, date(2026, 9, 1))
    for heading in ("## Срочно", "## Возможности", "## Регуляторные изменения",
                    "## Тендеры", "## Наблюдать", "## Исключено"):
        assert heading in markdown


def test_urgent_tender_is_on_top(db, settings):
    _seed(db, settings)
    builder = DigestBuilder(db, settings)
    markdown = builder.build(builder.collect(30), 30, date(2026, 9, 1))
    urgent_block = markdown.split("## Возможности")[0]
    assert "Frozen Fish" in urgent_block


def test_closed_tender_is_not_shown(db, settings):
    _seed(db, settings)
    builder = DigestBuilder(db, settings)
    markdown = builder.build(builder.collect(30), 30, date(2026, 9, 1))
    body = markdown.split("## Наблюдать")[0]
    assert "Supply of Rice" not in body


def test_low_confidence_analysis_is_not_an_opportunity(db, settings):
    _seed(db, settings)
    builder = DigestBuilder(db, settings)
    markdown = builder.build(builder.collect(30), 30, date(2026, 9, 1))
    opportunities = markdown.split("## Возможности")[1].split("## Регуляторные")[0]
    assert "слабый вывод" not in opportunities
    assert "BAI открыл аккредитацию" in opportunities


def test_company_matches_are_shown(db, settings):
    _seed(db, settings)
    builder = DigestBuilder(db, settings)
    markdown = builder.build(builder.collect(30), 30, date(2026, 9, 1))
    assert "ТД ВИК" in markdown and "проверить аккредитацию" in markdown


def test_noise_is_only_counted(db, settings):
    _seed(db, settings)
    builder = DigestBuilder(db, settings)
    markdown = builder.build(builder.collect(30), 30, date(2026, 9, 1))
    excluded = markdown.split("## Исключено")[1]
    assert "Отброшено как шум: 1" in excluded
    assert "явный шум по стоп-словам: 1" in excluded


def test_signal_is_not_repeated_across_sections(db, settings):
    _seed(db, settings)
    builder = DigestBuilder(db, settings)
    markdown = builder.build(builder.collect(30), 30, date(2026, 9, 1))
    assert markdown.count("### ITB — Supply of Frozen Fish") == 1


def test_section_cap_is_respected(db, settings):
    settings.digest_max_per_section = 1
    _seed(db, settings)
    builder = DigestBuilder(db, settings)
    markdown = builder.build(builder.collect(30), 30, date(2026, 9, 1))
    urgent_block = markdown.split("## Возможности")[0]
    assert urgent_block.count("### ") <= 1


def test_write_digest_creates_latest_and_archive(settings, tmp_path):
    paths = write_digest("# test", tmp_path / "d", date(2026, 9, 1))
    assert (tmp_path / "d" / "latest.md").exists()
    assert (tmp_path / "d" / "archive" / "2026-09-01.md").exists()
    assert paths["written"] == "yes"


def test_dry_run_writes_nothing(settings, tmp_path):
    write_digest("# test", tmp_path / "d", date(2026, 9, 1), dry_run=True)
    assert not (tmp_path / "d" / "latest.md").exists()


def test_write_digest_does_not_leave_temporary_files(settings, tmp_path):
    directory = tmp_path / "d"
    write_digest("# atomic", directory, date(2026, 9, 1))
    assert not list(directory.glob(".*.tmp"))
    assert (directory / "latest.md").read_text("utf-8") == "# atomic"


def test_digest_run_logs_a_run(db, settings):
    _seed(db, settings)
    db.close()
    stats = digest_run(settings, days=30)
    database = Database(settings.db_path)
    try:
        runs = database.recent_runs(1, stage="digest")
        assert runs and runs[0].status == "ok"
    finally:
        database.close()
    assert stats["errors"] == 0
