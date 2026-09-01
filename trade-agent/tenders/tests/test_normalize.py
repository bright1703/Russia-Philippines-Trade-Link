"""Даты, дедлайны, статусы, бюджет, контакты, вложения, канонический ID."""
from datetime import date

import pytest

import normalize as N

TODAY = date(2026, 9, 1)


# --- извлечение даты -------------------------------------------------------
@pytest.mark.parametrize("raw,expected", [
    ("September 10, 2026", date(2026, 9, 10)),
    ("Sept. 10, 2026", date(2026, 9, 10)),
    ("10 September 2026", date(2026, 9, 10)),
    ("2026-09-10", date(2026, 9, 10)),
    ("09/10/2026", date(2026, 9, 10)),          # филиппинский формат MM/DD/YYYY
    ("3rd October 2026", date(2026, 10, 3)),
    ("posted on August 26, 2026 at 3:00 PM", date(2026, 8, 26)),
])
def test_parse_date(raw, expected):
    assert N.parse_date(raw) == expected


@pytest.mark.parametrize("raw", ["", None, "не дата", "TBA", "to be announced"])
def test_parse_date_returns_none(raw):
    assert N.parse_date(raw) is None


def test_find_dates_order():
    text = "Posting: August 26, 2026. Deadline: September 03, 2026."
    assert N.find_dates(text) == [date(2026, 8, 26), date(2026, 9, 3)]


# --- извлечение дедлайна ---------------------------------------------------
def test_extract_deadline_prefers_labelled_date():
    text = ("Date of Posting: August 26, 2026. "
            "Deadline of Submission of Bids: September 03, 2026, 10:00 AM.")
    assert N.extract_deadline(text) == date(2026, 9, 3)
    assert N.extract_deadline_time(text) == "10:00 AM"


def test_extract_deadline_closing_date_label():
    assert N.extract_deadline("Closing Date: September 25, 2026") == date(2026, 9, 25)


def test_extract_deadline_absent():
    assert N.extract_deadline("Deadline of Submission of Bids to be announced.") is None
    assert N.extract_deadline("") is None


def test_extract_publish_date():
    assert N.extract_publish_date("Date of Posting: August 26, 2026") == date(2026, 8, 26)


# --- статусы ---------------------------------------------------------------
def test_detect_status_open_and_closed():
    assert N.detect_status("regular notice", date(2026, 9, 10), TODAY) == "open"
    assert N.detect_status("regular notice", date(2026, 8, 10), TODAY) == "closed"


def test_detect_status_cancelled_beats_date():
    text = "This bidding has been cancelled by the Bids and Awards Committee."
    assert N.detect_status(text, date(2026, 9, 30), TODAY) == "cancelled"


def test_detect_status_awarded_and_unknown():
    assert N.detect_status("Notice of Award issued", None, TODAY) == "awarded"
    assert N.detect_status("no dates here", None, TODAY) == "unknown"


@pytest.mark.parametrize("days,expected", [
    (None, "deadline_unknown"), (-1, "closed"), (0, "urgent"), (2, "urgent"),
    (3, "closing_soon"), (7, "closing_soon"), (8, "open"), (60, "open"),
])
def test_deadline_status(days, expected):
    assert N.deadline_status(days) == expected


# --- номер, бюджет, контакты, вложения ------------------------------------
def test_extract_notice_number_prefers_official_over_philgeps():
    text = "PhilGEPS Reference No: 11552901. ITB No. 2026-0912."
    assert N.extract_notice_number(text) == "2026-0912"
    assert N.extract_philgeps_ref(text) == "11552901"


def test_extract_budget():
    assert N.extract_budget("Approved Budget for the Contract (ABC): PHP 12,500,000.00") == (12500000.0, "PHP")
    assert N.extract_budget("Estimated cost of USD 250,000")[1] == "USD"
    assert N.extract_budget("Posted on September 2026") == (None, "")


def test_extract_contacts():
    text = "BAC Secretariat: Maria Santos, bac@da.gov.ph, +63 2 8920 1234"
    contacts = N.extract_contacts(text)
    assert contacts["contact_name"] == "Maria Santos"
    assert contacts["contact_email"] == "bac@da.gov.ph"
    assert "8920" in contacts["contact_phone"]


def test_extract_attachments_absolute_and_filtered():
    html = ('<div><a href="/files/itb.pdf">Bid docs</a>'
            '<a href="/page">HTML</a><a href="mailto:x@y.z">mail</a></div>')
    assert N.extract_attachments(html, "https://www.da.gov.ph/bids/") == [
        "https://www.da.gov.ph/files/itb.pdf"
    ]


def test_strip_html_removes_scripts():
    assert "alert" not in N.strip_html("<div>Bid<script>alert(1)</script> notice</div>")


# --- канонический идентификатор -------------------------------------------
def test_canonical_id_priority():
    n = N.Notice(source_id="da", title="T", notice_id="ITB-1")
    cid, basis = N.canonical_id(n)
    assert basis == "notice_number" and cid.startswith("no:")

    n2 = N.Notice(source_id="da", title="T", raw_text="PhilGEPS Reference No: 11552901")
    _, basis2 = N.canonical_id(n2)
    assert basis2 == "philgeps_reference"

    n3 = N.Notice(source_id="da", title="T", agency="DA", closing_date="2026-09-03")
    _, basis3 = N.canonical_id(n3)
    assert basis3 == "source_agency_title_closing"

    n4 = N.Notice(source_id="da", original_url="https://x.ph/a")
    _, basis4 = N.canonical_id(n4)
    assert basis4 == "canonical_url"


def test_canonical_url_strips_tracking():
    assert N.canonical_url("https://X.PH/a/?utm_source=fb&id=3#top") == "https://x.ph/a?id=3"
