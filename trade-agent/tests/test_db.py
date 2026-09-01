"""База: идемпотентность, атомарность, история запусков."""
import sqlite3

import pytest

from trade_agent.db import Database
from trade_agent.models import (
    Analysis, Company, Match, RawItem, Review, RunLog, Signal, SIGNAL_ANALYZED,
)
from trade_agent.utils import content_hash


def _item(external_id="ITB-1", source="bfar", title="Frozen fish", text="pork and fish"):
    item = RawItem(source=source, source_type="tender", external_id=external_id,
                   title=title, raw_text=text)
    item.hash = content_hash(source, external_id, title, text)
    return item


def test_raw_item_insert_is_idempotent(db):
    first_id, created = db.upsert_raw_item(_item())
    second_id, created_again = db.upsert_raw_item(_item())
    assert created is True and created_again is False
    assert first_id == second_id
    assert db.count_raw_items() == 1


def test_same_text_without_external_id_deduplicates(db):
    a = RawItem(source="tg:x", source_type="telegram", title="Импорт свинины",
                raw_text="Филиппины увеличивают импорт свинины")
    a.hash = content_hash(a.source, "", a.title, a.raw_text)
    b = RawItem(source="tg:x", source_type="telegram", title="Импорт  свинины!",
                raw_text="Филиппины  увеличивают импорт свинины.")
    b.hash = content_hash(b.source, "", b.title, b.raw_text)
    db.upsert_raw_item(a)
    _, created = db.upsert_raw_item(b)
    assert created is False


def test_signal_unique_per_raw_item(db):
    raw_id, _ = db.upsert_raw_item(_item())
    sid, created = db.upsert_signal(Signal(raw_item_id=raw_id, relevance_score=4))
    sid2, created2 = db.upsert_signal(Signal(raw_item_id=raw_id, relevance_score=5))
    assert created and not created2 and sid == sid2


def test_queue_shrinks_after_scout(db):
    raw_id, _ = db.upsert_raw_item(_item())
    assert db.stats()["queue"] == 1
    db.upsert_signal(Signal(raw_item_id=raw_id, relevance_score=3))
    assert db.stats()["queue"] == 0


def test_transaction_rolls_back_on_error(db):
    raw_id, _ = db.upsert_raw_item(_item())
    with pytest.raises(sqlite3.IntegrityError):
        with db.tx() as conn:
            conn.execute("INSERT INTO signals (raw_item_id, created_at) VALUES (?,?)",
                         (raw_id, "now"))
            conn.execute("INSERT INTO signals (raw_item_id, created_at) VALUES (?,?)",
                         (raw_id, "now"))
    assert db.conn.execute("SELECT COUNT(*) FROM signals").fetchone()[0] == 0


def test_match_upsert_updates_score(db):
    raw_id, _ = db.upsert_raw_item(_item())
    sid, _ = db.upsert_signal(Signal(raw_item_id=raw_id, relevance_score=4))
    db.upsert_company(Company(slug="td-vik", name="ТД ВИК"))
    db.upsert_match(Match(company_slug="td-vik", signal_id=sid, match_score=3))
    _, created = db.upsert_match(Match(company_slug="td-vik", signal_id=sid, match_score=5))
    matches = db.matches_since(1)
    assert created is False and len(matches) == 1 and matches[0].match_score == 5


def test_company_upsert_updates_profile(db):
    db.upsert_company(Company(slug="x", name="X", products=["a"]))
    db.upsert_company(Company(slug="x", name="X", products=["a", "b"]))
    companies = db.all_companies()
    assert len(companies) == 1 and companies[0].products == ["a", "b"]


def test_run_log_records_errors(db):
    run_id = db.start_run("fetch")
    db.finish_run(run_id, RunLog(stage="fetch", status="error", errors=2,
                                 error_text="источник недоступен", retries=3))
    runs = db.recent_runs(1)
    assert runs[0].status == "error" and runs[0].errors == 2 and runs[0].retries == 3
    assert "недоступен" in db.stats()["last_error"]


def test_passed_analyses_only_returns_pass(db):
    raw_id, _ = db.upsert_raw_item(_item())
    sid, _ = db.upsert_signal(Signal(raw_item_id=raw_id, relevance_score=4))
    good = Analysis(signal_id=sid, summary="ok", confidence=0.8, revision=0)
    good.id = db.insert_analysis(good)
    db.insert_review(Review(analysis_id=good.id, verdict="PASS"))
    bad = Analysis(signal_id=sid, summary="bad", confidence=0.2, revision=1)
    bad.id = db.insert_analysis(bad)
    db.insert_review(Review(analysis_id=bad.id, verdict="REJECT"))
    passed = db.passed_analyses_since(7)
    assert len(passed) == 1 and passed[0][0].summary == "ok"
    db.set_signal_status(sid, SIGNAL_ANALYZED)
    assert db.stats()["signals_analyzed"] == 1
