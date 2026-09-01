"""Дедупликация одинаковых закупок из разных источников."""
from datetime import date

import normalize as N

TODAY = date(2026, 9, 1)


def _mk(source_id, priority, title, agency="", notice_id="", closing="2026-09-03",
        docs=None, url=""):
    n = N.Notice(
        source_id=source_id, source_name=source_id, title=title, agency=agency,
        notice_id=notice_id, closing_date=closing, source_priority=priority,
        attachment_urls=list(docs or []), original_url=url,
        description="x" * 100,
    )
    n.source_links = [url] if url else []
    n.source_ids = [source_id]
    n.canonical_id, n.id_basis = N.canonical_id(n)
    return n


def test_dedupe_by_notice_number():
    a = _mk("philgeps", 5, "Supply of Frozen Fish", notice_id="2026-0912")
    b = _mk("da", 5, "Invitation to Bid — Supply of Frozen Fish",
            agency="Department of Agriculture", notice_id="2026-0912")
    merged = N.dedupe_notices([a, b])
    assert len(merged) == 1
    assert set(merged[0].source_ids) == {"philgeps", "da"}


def test_dedupe_by_document_link():
    doc = "https://da.gov.ph/files/itb.pdf"
    a = _mk("da", 5, "Cold Storage Facility", docs=[doc])
    b = _mk("bfar", 4, "Cold Storage Facility Navotas", docs=[doc])
    assert len(N.dedupe_notices([a, b])) == 1


def test_dedupe_fuzzy_title_same_agency():
    a = _mk("da", 5, "Supply and Delivery of Urea Fertilizer 46-0-0", agency="DA")
    b = _mk("philgeps", 5, "Supply and Delivery of Urea Fertilizer (46-0-0)", agency="DA")
    assert len(N.dedupe_notices([a, b])) == 1


def test_different_notices_not_merged():
    a = _mk("da", 5, "Supply of Frozen Fish", notice_id="2026-0912")
    b = _mk("da", 5, "Supply of Urea Fertilizer", notice_id="2026-0918")
    assert len(N.dedupe_notices([a, b])) == 2


def test_far_apart_deadlines_not_merged():
    a = _mk("da", 5, "Supply of Rice", agency="NFA", closing="2026-09-03")
    b = _mk("nfa", 4, "Supply of Rice", agency="NFA", closing="2026-12-20")
    assert len(N.dedupe_notices([a, b])) == 2


def test_merge_keeps_all_links_and_documents():
    a = _mk("philgeps", 5, "Cold Storage", notice_id="2026-0977",
            url="https://notices.philgeps.gov.ph/a", docs=["https://x/1.pdf"])
    b = _mk("bfar", 5, "Cold Storage", notice_id="2026-0977",
            url="https://bfar.da.gov.ph/b", docs=["https://x/2.pdf"])
    merged = N.dedupe_notices([a, b])[0]
    assert len(merged.source_links) == 2
    assert len(merged.attachment_urls) == 2
