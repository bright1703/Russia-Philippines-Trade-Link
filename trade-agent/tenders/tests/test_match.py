"""Ключевые слова, расчёт оценки, eligibility, приоритет."""
from datetime import date
from pathlib import Path

import pytest

import match as M
import normalize as N

TODAY = date(2026, 9, 1)
PROFILES = M.load_profiles(Path(__file__).resolve().parent.parent / "profiles.yml")


def mk(title, text="", agency="", category="", priority=5, source_id="da"):
    cfg = {"id": source_id, "name": source_id, "priority": priority,
           "default_agency": agency, "default_category": category}
    return N.normalize_notice({"title": title, "text": text, "url": "https://x.ph/a"}, cfg, TODAY)


# --- поиск ключевых слов ---------------------------------------------------
def test_keyword_word_boundary_for_short_terms():
    assert M._contains("supply of fish", "fish")
    assert not M._contains("official finish line", "fish")     # не подстрока
    assert M._contains("cold storage facility", "cold storage")


def test_exclude_keyword_drops_profile():
    n = mk("Request for Quotation — Office Bond Paper and Ink Cartridge",
           "Approved Budget: PHP 180,000.00. Closing Date: September 20, 2026")
    M.score_notice(n, PROFILES)
    assert "Оборудование и промышленность" not in n.matched_profiles


# --- расчёт оценки ---------------------------------------------------------
def test_strong_seafood_match_scores_high():
    n = mk("Supply and Delivery of Frozen Fish and Cold Storage Equipment",
           "Closing Date: September 25, 2026. Approved Budget for the Contract: PHP 12,500,000.00",
           agency="BFAR", category="fisheries")
    M.score_notice(n, PROFILES)
    assert n.match_score == 5
    assert "Рыба и морепродукты" in n.matched_profiles
    assert any("seafood" in r for r in n.match_reasons)


def test_irrelevant_notice_scores_zero():
    n = mk("Procurement of Janitorial Services for the Regional Office",
           "Closing Date: September 20, 2026")
    M.score_notice(n, PROFILES)
    assert n.match_score == 0
    assert n.matched_profiles == []


def test_logistics_profile_matches():
    n = mk("Lease of Refrigerated Container Vans and Cold Chain Trucking Services",
           "Closing Date: September 20, 2026")
    M.score_notice(n, PROFILES)
    assert "Логистика и порты" in n.matched_profiles
    assert n.match_score >= 4


def test_cancelled_notice_scored_zero():
    n = mk("Supply of Fishing Gear and Nets",
           "This bidding has been cancelled. Closing Date: September 15, 2026")
    M.score_notice(n, PROFILES)
    assert n.status == "cancelled" and n.match_score == 0


def test_closed_notice_capped():
    n = mk("Supply and Delivery of Frozen Fish",
           "Closing Date: August 12, 2026")
    M.score_notice(n, PROFILES)
    assert n.deadline_status == "closed" and n.match_score <= 1


# --- объявление без дедлайна ----------------------------------------------
def test_notice_without_deadline():
    n = mk("Invitation to Bid — Procurement of Imported Thermal Coal, 150,000 MT",
           "Date of Posting: August 30, 2026. Deadline of Submission of Bids to be announced.")
    M.score_notice(n, PROFILES)
    assert n.closing_date == ""
    assert n.deadline_status == "deadline_unknown"
    assert n.days_until_deadline is None
    assert any("дедлайн не указан" in r for r in n.match_reasons)
    assert n.match_score >= 1          # не выбрасываем, но и не поднимаем


# --- только для филиппинских поставщиков ----------------------------------
def test_filipino_only_notice_is_capped_and_flagged():
    n = mk("Lease of Warehouse and Grains Storage Facility",
           "Only Filipino citizens or corporations with at least seventy-five percent "
           "Filipino ownership are eligible. Closing Date: September 20, 2026")
    M.score_notice(n, PROFILES)
    assert M.FOREIGN_RESTRICTED_MARK in n.eligibility_notes
    assert n.match_score <= 2


def test_eligibility_always_has_verification_note():
    n = mk("Supply of Anything", "Closing Date: September 20, 2026")
    M.score_notice(n, PROFILES)
    assert n.eligibility_notes[-1] == M.DEFAULT_ELIGIBILITY


def test_eligibility_detects_philgeps_and_local_permits():
    n = mk("Supply of Generator Sets",
           "Bidders must be registered with PhilGEPS and submit a Mayor's Permit and "
           "notarized Omnibus Sworn Statement. Closing Date: September 20, 2026")
    M.score_notice(n, PROFILES)
    joined = " ".join(n.eligibility_notes)
    assert "PhilGEPS" in joined and "mayor's permit" in joined.lower()


# --- приоритет -------------------------------------------------------------
def test_priority_score_prefers_urgent_over_distant():
    urgent = mk("Supply and Delivery of Frozen Fish", "Closing Date: September 02, 2026")
    later = mk("Supply and Delivery of Frozen Fish", "Closing Date: December 02, 2026")
    M.score_notice(urgent, PROFILES)
    M.score_notice(later, PROFILES)
    assert urgent.deadline_status == "urgent"
    assert urgent.priority_score > later.priority_score
