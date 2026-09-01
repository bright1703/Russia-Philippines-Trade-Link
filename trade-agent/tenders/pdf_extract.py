"""
Работа с PDF-приложениями к тендерным объявлениям.

PDF скачивается во временную папку .raw/, из него извлекается текст и
недостающие поля объявления. Сами файлы в репозиторий не попадают
(.raw/ в .gitignore) и по умолчанию удаляются после обработки.
"""

from __future__ import annotations

import hashlib
import logging
import shutil
from pathlib import Path
from typing import Any, Optional

from normalize import (
    Notice, clean_ws, detect_status, days_until, deadline_status,
    extract_budget, extract_contacts, extract_deadline, extract_notice_number,
)

LOG = logging.getLogger("tenders.pdf")

MAX_PDF_BYTES = 15 * 1024 * 1024
MAX_PAGES = 12


def raw_path_for(url: str, raw_dir: Path) -> Path:
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]
    return raw_dir / f"{digest}.pdf"


def download_pdf(client: Any, url: str, raw_dir: Path) -> Optional[Path]:
    """Скачивает PDF. Возвращает путь или None, если файл недоступен."""
    raw_dir.mkdir(parents=True, exist_ok=True)
    target = raw_path_for(url, raw_dir)
    if target.exists() and target.stat().st_size > 0:
        return target
    try:
        response = client.get(url, stream=True)
    except Exception as exc:  # noqa: BLE001 - ошибка источника не должна ронять запуск
        LOG.warning("PDF недоступен (%s): %s", url, exc)
        return None

    content_type = (response.headers.get("Content-Type") or "").lower()
    if "pdf" not in content_type and not url.lower().split("?")[0].endswith(".pdf"):
        LOG.info("ссылка не является PDF (%s): %s", content_type or "?", url)
        return None

    size = 0
    try:
        with open(target, "wb") as handle:
            for chunk in response.iter_content(64 * 1024):
                size += len(chunk)
                if size > MAX_PDF_BYTES:
                    LOG.warning("PDF слишком большой, пропускаем: %s", url)
                    handle.close()
                    target.unlink(missing_ok=True)
                    return None
                handle.write(chunk)
    except OSError as exc:
        LOG.warning("не удалось сохранить PDF %s: %s", url, exc)
        target.unlink(missing_ok=True)
        return None
    return target if target.exists() and target.stat().st_size > 0 else None


def extract_text(path: Path, max_pages: int = MAX_PAGES) -> str:
    """Текст из PDF. Пустая строка, если файл повреждён, зашифрован или отсутствует."""
    if path is None or not Path(path).exists():
        return ""
    try:
        from pypdf import PdfReader
    except ImportError:  # pragma: no cover
        LOG.warning("pypdf не установлен — PDF не разбирается")
        return ""
    try:
        reader = PdfReader(str(path))
        if getattr(reader, "is_encrypted", False):
            try:
                reader.decrypt("")
            except Exception:  # noqa: BLE001
                LOG.info("PDF зашифрован, пропускаем: %s", path.name)
                return ""
        pages = reader.pages[:max_pages]
        return clean_ws("\n".join((p.extract_text() or "") for p in pages))
    except Exception as exc:  # noqa: BLE001 - битые PDF встречаются регулярно
        LOG.warning("не удалось разобрать PDF %s: %s", path.name, exc)
        return ""


def enrich_from_pdf_text(notice: Notice, text: str, today=None) -> list[str]:
    """Дозаполняет пустые поля объявления данными из PDF. Возвращает список изменённых полей."""
    if not text:
        return []
    filled: list[str] = []

    if not notice.notice_id:
        found = extract_notice_number(text)
        if found:
            notice.notice_id = found
            filled.append("notice_id")

    if not notice.closing_date:
        deadline = extract_deadline(text)
        if deadline:
            notice.closing_date = deadline.isoformat()
            notice.days_until_deadline = days_until(deadline, today)
            notice.deadline_status = deadline_status(notice.days_until_deadline)
            filled.append("closing_date")

    if not notice.estimated_budget:
        budget, currency = extract_budget(text)
        if budget:
            notice.estimated_budget = budget
            notice.currency = notice.currency or currency
            filled.append("estimated_budget")

    contacts = extract_contacts(text)
    for key in ("contact_name", "contact_email", "contact_phone"):
        if not getattr(notice, key) and contacts[key]:
            setattr(notice, key, contacts[key])
            filled.append(key)

    if len(text) > len(notice.raw_text or ""):
        notice.raw_text = (notice.raw_text + "\n" + text)[:8000]
        filled.append("raw_text")

    notice.status = detect_status(notice.searchable_text(), notice.closing_date_obj, today)
    return filled


def enrich_notice(notice: Notice, client: Any, raw_dir: Path, max_pdfs: int = 2,
                  today=None) -> list[str]:
    """Скачивает до max_pdfs приложений и дозаполняет объявление."""
    filled: list[str] = []
    pdfs = [u for u in notice.attachment_urls if u.lower().split("?")[0].endswith(".pdf")]
    for url in pdfs[:max_pdfs]:
        path = download_pdf(client, url, raw_dir)
        if path is None:
            continue
        text = extract_text(path)
        filled += enrich_from_pdf_text(notice, text, today)
    return filled


def cleanup_raw(raw_dir: Path, keep: bool = False) -> int:
    """Удаляет временные файлы. Возвращает количество удалённых файлов."""
    raw_dir = Path(raw_dir)
    if keep or not raw_dir.exists():
        return 0
    removed = 0
    for item in raw_dir.iterdir():
        if item.name == ".gitkeep":
            continue
        try:
            if item.is_dir():
                shutil.rmtree(item)
            else:
                item.unlink()
            removed += 1
        except OSError as exc:
            LOG.warning("не удалось удалить %s: %s", item, exc)
    return removed
