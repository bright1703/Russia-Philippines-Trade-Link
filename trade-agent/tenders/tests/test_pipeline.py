"""Сквозной прогон на локальных фикстурах, изоляция сбоя источника, хранилище."""
from datetime import date
from pathlib import Path

import pytest

import adapters
import fetch_tenders as F
import normalize as N
import render_digest as R
import storage as S
from adapters.base import HttpClient, SourceError
from match import load_profiles, score_all

BASE = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures"
TODAY = date(2026, 9, 1)
PROFILES = load_profiles(BASE / "profiles.yml")


def _fixture_sources():
    _, sources = F.load_sources(BASE / "sources.yml")
    chosen = []
    for cfg in sources:
        if not cfg.get("enabled"):
            continue
        path = FIXTURES / f"{cfg['id']}.html"
        if not path.exists():
            continue
        cfg = dict(cfg)
        cfg["adapter"] = "fixture"
        cfg["fixture_path"] = str(path)
        cfg["follow_detail"] = False
        chosen.append(cfg)
    return chosen


@pytest.fixture(scope="module")
def processed():
    client = HttpClient({"rate_limit_delay": 0, "retries": 0})
    notices, errors, checked = F.collect(_fixture_sources(), client, days=30, today=TODAY)
    notices = [n for n in notices if F.within_window(n, 30, TODAY)]
    notices = N.dedupe_notices(notices)
    score_all(notices, PROFILES)
    return notices, errors, checked


# --- сквозной прогон -------------------------------------------------------
def test_all_five_sources_parsed(processed):
    _, errors, checked = processed
    assert checked == 5
    assert errors == {}


def test_pipeline_finds_relevant_opportunities(processed):
    notices, _, _ = processed
    buckets = R.split_notices(notices)
    assert len(buckets["urgent"]) >= 1
    assert len(buckets["urgent"]) + len(buckets["other"]) >= 5


def test_duplicate_across_three_sources_merged(processed):
    notices, _, _ = processed
    fish = [n for n in notices if "Frozen Fish" in n.title]
    assert len(fish) == 1
    assert len(fish[0].source_ids) >= 2


def test_cancelled_and_local_only_excluded_from_main_list(processed):
    notices, _, _ = processed
    buckets = R.split_notices(notices)
    listed = {n.title for n in buckets["urgent"] + buckets["other"]}
    assert not any("Fishing Gear" in t for t in listed)          # отменённая
    assert not any("Warehouse and Grains" in t for t in listed)  # только для филиппинцев


def test_digest_has_required_sections(processed):
    notices, errors, checked = processed
    stats = {"days": 30, "sources_checked": checked, "new_notices": len(notices),
             "updated_notices": 0, "errors": len(errors), "source_errors": errors}
    md = R.render(notices, stats, TODAY)
    for heading in ("# Tender Radar — Philippines", "## Срочно",
                    "## Другие релевантные объявления", "## Не включено в основной список"):
        assert heading in md
    assert "Что проверить:" in md
    assert "Eligibility: requires verification with the procuring entity" in md


# --- недоступность одного источника ---------------------------------------
class _BrokenAdapter(adapters.BaseAdapter):
    adapter_id = "broken"

    def fetch(self, days=7):
        raise SourceError("timeout after 3 attempts")


def test_one_broken_source_does_not_stop_the_rest():
    adapters.register(_BrokenAdapter)
    client = HttpClient({"rate_limit_delay": 0, "retries": 0})
    sources = [{"id": "broken", "name": "Broken", "adapter": "broken", "priority": 5}]
    sources += _fixture_sources()
    notices, errors, checked = F.collect(sources, client, days=30, today=TODAY)
    assert checked == len(sources)
    assert "broken" in errors and "timeout" in errors["broken"]
    assert len(notices) > 0


def test_missing_fixture_reports_error_not_crash():
    client = HttpClient({"rate_limit_delay": 0, "retries": 0})
    cfg = {"id": "ghost", "name": "Ghost", "adapter": "fixture",
           "fixture_path": "/nonexistent/ghost.html", "priority": 1}
    notices, errors, checked = F.collect([cfg], client, days=7, today=TODAY)
    assert notices == [] and "ghost" in errors and checked == 1


# --- хранилище и история изменений ----------------------------------------
def test_store_tracks_new_and_changes(tmp_path):
    store = S.SqliteStore(tmp_path / "t.db")
    n = N.Notice(canonical_id="no:1", title="Frozen Fish", closing_date="2026-09-10",
                 status="open", match_score=5)
    is_new, changes = store.upsert(n)
    assert is_new and changes[0]["type"] == S.CHANGE_NEW

    n2 = N.Notice(canonical_id="no:1", title="Frozen Fish", closing_date="2026-09-20",
                  status="cancelled", match_score=0, attachment_urls=["https://x/1.pdf"])
    is_new, changes = store.upsert(n2)
    kinds = {c["type"] for c in changes}
    assert not is_new
    assert {S.CHANGE_DEADLINE, S.CHANGE_CANCELLED, S.CHANGE_DOCUMENT} <= kinds
    assert len(store.history("no:1")) >= 4
    assert store.all_notices()[0].first_seen  # первое обнаружение сохранено
    store.close()


def test_dry_run_writes_nothing(tmp_path, monkeypatch):
    md = R.render([], {"days": 7, "sources_checked": 0}, TODAY)
    paths = R.write_digest(md, tmp_path, TODAY, dry_run=True)
    assert paths["written"].startswith("no")
    assert not (tmp_path / "latest.md").exists()
    assert not (tmp_path / "archive" / f"{TODAY.isoformat()}.md").exists()


def test_write_digest_creates_latest_and_archive(tmp_path):
    md = R.render([], {"days": 7, "sources_checked": 0}, TODAY)
    R.write_digest(md, tmp_path, TODAY)
    assert (tmp_path / "latest.md").exists()
    assert (tmp_path / "archive" / f"{TODAY.isoformat()}.md").exists()
