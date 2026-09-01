"""
Чтение профилей компаний из brain/companies/*.md.

Формат профиля — Markdown с YAML-заголовком (front matter).
Человек правит Markdown, система читает его в базу.
Ничего не выдумывается: пустые поля остаются пустыми.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Optional

import yaml

from ..models import Company

LOG = logging.getLogger("trade_agent.companies")

FRONT_MATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", re.S)

LIST_FIELDS = ("products", "hs_codes", "categories", "documents",
               "restrictions", "potential_buyers", "regulators")


def _as_list(value: Any) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, (list, tuple)):
        return [str(v).strip() for v in value if str(v).strip()]
    return [part.strip() for part in str(value).replace(";", ",").split(",") if part.strip()]


def slugify(value: str) -> str:
    text = (value or "").strip().lower()
    text = re.sub(r"[^a-z0-9а-яё]+", "-", text)
    return text.strip("-") or "company"


def parse_profile_markdown(text: str, path: Optional[Path] = None) -> Optional[Company]:
    """Разбирает один Markdown-профиль. Возвращает None, если это не профиль."""
    match = FRONT_MATTER_RE.match(text or "")
    if not match:
        return None
    try:
        meta = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError as exc:
        LOG.warning("профиль %s: некорректный YAML-заголовок: %s", path, exc)
        return None
    if not isinstance(meta, dict) or not meta.get("name"):
        return None

    body = match.group(2).strip()
    company = Company(
        slug=str(meta.get("slug") or slugify(str(meta["name"]))),
        name=str(meta["name"]).strip(),
        website=str(meta.get("website") or "").strip(),
        export_experience=str(meta.get("export_experience") or "").strip(),
        status=str(meta.get("status") or "").strip(),
        history=str(meta.get("history") or body[:2000]).strip(),
        next_step=str(meta.get("next_step") or "").strip(),
        region=str(meta.get("region") or "Приморский край").strip(),
        profile_path=str(path) if path else "",
    )
    for field_name in LIST_FIELDS:
        setattr(company, field_name, _as_list(meta.get(field_name)))
    return company


def load_from_brain(brain_dir: Path) -> list[Company]:
    directory = Path(brain_dir) / "companies"
    if not directory.exists():
        return []
    companies: list[Company] = []
    for path in sorted(directory.glob("*.md")):
        if path.name.upper().startswith("_TEMPLATE"):
            continue
        try:
            company = parse_profile_markdown(path.read_text("utf-8"), path)
        except OSError as exc:
            LOG.warning("не удалось прочитать %s: %s", path, exc)
            continue
        if company:
            companies.append(company)
        else:
            LOG.info("файл %s пропущен: нет корректного YAML-заголовка с полем name", path.name)
    return companies


def sync_companies(db: Any, brain_dir: Path) -> tuple[int, int]:
    """
    Заливает профили из brain/companies в базу. Возвращает (создано, обновлено).

    ИСТОЧНИК ПРАВДЫ — brain/companies/*.md. Синхронизация всегда идёт
    в режиме merge: пустое поле Markdown-профиля не затирает данные,
    которые уже есть в базе (например, импортированные из каталога ЦПЭ).
    Полная перезапись возможна только явным флагом импорта --overwrite.
    """
    created = updated = 0
    for company in load_from_brain(brain_dir):
        _, is_new = db.upsert_company(company, mode="merge")
        created += int(is_new)
        updated += int(not is_new)
    return created, updated
