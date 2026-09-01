#!/usr/bin/env python3
"""
Импорт каталога компаний из CSV или JSON.

Рассчитан на дальнейшую загрузку каталога ЦПЭ (300-500 профилей)
без изменения кода: достаточно подготовить файл с колонками.

    python -m trade_agent.companies.import_companies catalog.csv
    python -m trade_agent.companies.import_companies catalog.json --write-profiles

Ожидаемые колонки (все необязательные, кроме name):
    name, slug, website, products, hs_codes, categories, export_experience,
    documents, status, restrictions, potential_buyers, regulators,
    history, next_step, region

Списки в CSV разделяются запятой или точкой с запятой внутри ячейки.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any, Iterable, Optional

from ..config import load_settings
from ..db import Database
from ..models import Company
from .loader import LIST_FIELDS, _as_list, slugify

PROFILE_TEMPLATE = """---
name: {name}
slug: {slug}
website: {website}
products: {products}
hs_codes: {hs_codes}
categories: {categories}
export_experience: {export_experience}
documents: {documents}
status: {status}
restrictions: {restrictions}
potential_buyers: {potential_buyers}
regulators: {regulators}
next_step: {next_step}
region: {region}
---

# {name}

## История работы

{history}

## Заметки

<!-- Заполняется человеком. Не добавляйте сюда непроверенные данные. -->
"""


def _yaml_string(value: Any) -> str:
    """Сериализует строковое поле так, чтобы оно оставалось валидным YAML."""
    return json.dumps(str(value or ""), ensure_ascii=False)


def _rows(path: Path) -> Iterable[dict[str, Any]]:
    if path.suffix.lower() == ".json":
        payload = json.loads(path.read_text("utf-8"))
        rows = payload if isinstance(payload, list) else payload.get("companies", [])
        for row in rows:
            if isinstance(row, dict):
                yield row
        return
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            yield row


def row_to_company(row: dict[str, Any]) -> Optional[Company]:
    name = str(row.get("name") or row.get("Название") or "").strip()
    if not name:
        return None
    company = Company(
        slug=str(row.get("slug") or slugify(name)),
        name=name,
        website=str(row.get("website") or "").strip(),
        export_experience=str(row.get("export_experience") or "").strip(),
        status=str(row.get("status") or "").strip(),
        history=str(row.get("history") or "").strip(),
        next_step=str(row.get("next_step") or "").strip(),
        region=str(row.get("region") or "Приморский край").strip(),
    )
    for field_name in LIST_FIELDS:
        setattr(company, field_name, _as_list(row.get(field_name)))
    return company


def write_profile(company: Company, brain_dir: Path, overwrite: bool = False) -> Optional[Path]:
    """
    Создаёт Markdown-профиль. Существующий профиль молча не перезаписывается:
    без overwrite=True возвращается None, и вызывающий код это учитывает.
    """
    directory = Path(brain_dir) / "companies"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{company.slug}.md"
    if path.exists() and not overwrite:
        return None                    # существующий профиль не перезаписываем
    text = PROFILE_TEMPLATE.format(
        name=_yaml_string(company.name), slug=_yaml_string(company.slug),
        website=_yaml_string(company.website),
        products=json.dumps(company.products, ensure_ascii=False),
        hs_codes=json.dumps(company.hs_codes, ensure_ascii=False),
        categories=json.dumps(company.categories, ensure_ascii=False),
        export_experience=_yaml_string(company.export_experience),
        documents=json.dumps(company.documents, ensure_ascii=False),
        status=_yaml_string(company.status),
        restrictions=json.dumps(company.restrictions, ensure_ascii=False),
        potential_buyers=json.dumps(company.potential_buyers, ensure_ascii=False),
        regulators=json.dumps(company.regulators, ensure_ascii=False),
        next_step=_yaml_string(company.next_step), region=_yaml_string(company.region),
        history=company.history or "Данных пока нет.",
    )
    path.write_text(text, "utf-8")
    return path


def import_from_file(path: Path, db: Any, brain_dir: Optional[Path] = None,
                     write_profiles: bool = False, overwrite: bool = False,
                     overwrite_profiles: bool = False) -> dict[str, int]:
    """
    Импорт каталога.

    По умолчанию режим merge: импорт дополняет профиль, но не обнуляет
    уже заполненные поля. overwrite=True — полная замена полей в базе.
    overwrite_profiles=True — разрешает перезапись существующих
    Markdown-профилей (по умолчанию они не трогаются).
    """
    stats = {"read": 0, "created": 0, "updated": 0, "skipped": 0,
             "profiles": 0, "profiles_kept": 0}
    mode = "overwrite" if overwrite else "merge"
    for row in _rows(Path(path)):
        stats["read"] += 1
        company = row_to_company(row)
        if company is None:
            stats["skipped"] += 1
            continue
        _, is_new = db.upsert_company(company, mode=mode)
        stats["created" if is_new else "updated"] += 1
        if write_profiles and brain_dir:
            written = write_profile(company, Path(brain_dir), overwrite_profiles)
            if written is None:
                stats["profiles_kept"] += 1
            else:
                stats["profiles"] += 1
    return stats


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Импорт каталога компаний (CSV/JSON).")
    parser.add_argument("path", help="файл каталога")
    parser.add_argument("--write-profiles", action="store_true",
                        help="создать Markdown-профили в brain/companies для новых компаний")
    parser.add_argument("--overwrite", action="store_true",
                        help="ОПАСНО: полностью заменить поля профилей в базе, "
                             "включая обнуление пустыми значениями")
    parser.add_argument("--overwrite-profiles", action="store_true",
                        help="ОПАСНО: перезаписать существующие Markdown-профили")
    args = parser.parse_args(argv)

    settings = load_settings()
    settings.ensure_dirs()
    db = Database(settings.db_path)
    try:
        stats = import_from_file(Path(args.path), db, settings.brain_dir,
                                 args.write_profiles, args.overwrite,
                                 args.overwrite_profiles)
    finally:
        db.close()
    print(f"Rows read: {stats['read']}")
    print(f"Companies created: {stats['created']}")
    print(f"Companies updated: {stats['updated']}")
    print(f"Skipped: {stats['skipped']}")
    print(f"Profiles written: {stats['profiles']}")
    print(f"Profiles kept (не перезаписаны): {stats['profiles_kept']}")
    print(f"Mode: {'overwrite' if args.overwrite else 'merge'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
