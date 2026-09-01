"""PDF: отсутствующий, повреждённый, недоступный; дозаполнение из текста."""
from datetime import date
from pathlib import Path

import normalize as N
import pdf_extract as P

TODAY = date(2026, 9, 1)


class _FailingClient:
    def get(self, url, **kwargs):
        raise RuntimeError("connection refused")


def test_extract_text_missing_file(tmp_path):
    assert P.extract_text(tmp_path / "nope.pdf") == ""


def test_extract_text_corrupt_file(tmp_path):
    broken = tmp_path / "broken.pdf"
    broken.write_bytes(b"%PDF-1.4 this is not a real pdf")
    assert P.extract_text(broken) == ""


def test_download_pdf_handles_unreachable_source(tmp_path):
    assert P.download_pdf(_FailingClient(), "https://x.ph/a.pdf", tmp_path) is None


def test_enrich_notice_without_attachments_is_noop(tmp_path):
    n = N.Notice(title="Supply of Fish")
    assert P.enrich_notice(n, _FailingClient(), tmp_path, today=TODAY) == []


def test_enrich_from_pdf_text_fills_empty_fields():
    n = N.Notice(title="Supply and Delivery of Frozen Fish")
    text = ("ITB No. 2026-0912. Approved Budget for the Contract: PHP 12,500,000.00. "
            "Deadline of Submission of Bids: September 10, 2026. "
            "BAC Secretariat: Maria Santos, bac@da.gov.ph")
    filled = P.enrich_from_pdf_text(n, text, TODAY)
    assert n.notice_id == "2026-0912"
    assert n.closing_date == "2026-09-10"
    assert n.estimated_budget == 12500000.0
    assert n.contact_email == "bac@da.gov.ph"
    assert n.deadline_status == "open"
    assert "closing_date" in filled


def test_enrich_from_pdf_text_does_not_overwrite():
    n = N.Notice(title="X", notice_id="KEEP-ME", closing_date="2026-09-05")
    P.enrich_from_pdf_text(n, "ITB No. 2026-9999. Closing Date: December 01, 2026", TODAY)
    assert n.notice_id == "KEEP-ME" and n.closing_date == "2026-09-05"


def test_cleanup_raw_removes_files(tmp_path):
    (tmp_path / "a.pdf").write_bytes(b"x")
    (tmp_path / ".gitkeep").write_text("")
    assert P.cleanup_raw(tmp_path) == 1
    assert (tmp_path / ".gitkeep").exists()


def test_cleanup_raw_respects_keep_flag(tmp_path):
    (tmp_path / "a.pdf").write_bytes(b"x")
    assert P.cleanup_raw(tmp_path, keep=True) == 0
    assert (tmp_path / "a.pdf").exists()
