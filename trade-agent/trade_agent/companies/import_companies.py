#!/usr/bin/env python3
"""
Импорт каталога компаний из CSV или JSON.

Рассчитан на дальнейшую загрузку каталога ЦПЭ (300-500 профилей)
без изменения кода: достаточно подготовить файл с колонками.

    python -m trade_agent.companies.import_companies catalog.csv
    python -m trade_agent.companies.import_companies catalog.json --write-profiles

Ожидаемые колонки (все необязательные, кроме name):
    name, slug, website, description, inn, products, product_aliases,
    hs_codes, categories, export_countries, industry, source_name, source_row,
    data_quality, export_experience, documents, status, restrictions,
    potential_buyers, regulators, history, next_step, region

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
description: {description}
inn: {inn}
products: {products}
product_aliases: {product_aliases}
hs_codes: {hs_codes}
categories: {categories}
export_countries: {export_countries}
industry: {industry}
source_name: {source_name}
source_row: {source_row}
data_quality: {data_quality}
contact_name: {contact_name}
address: {address}
contacts: {contacts}
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
    name = str(row.get("name") or row.get("Название") or
               row.get("Название компании") or "").strip()
    if not name:
        return None
    description = str(row.get("description") or row.get("Описание компании") or "").strip()
    products = row.get("products") or row.get("Продукция") or []
    hs_codes = row.get("hs_codes") or row.get("Перечень товаров/услуг с кодами ТН ВЭД") or []
    countries = row.get("export_countries") or row.get("Страны экспорта") or []
    industry = str(row.get("industry") or row.get("Отрасль экспорта") or "").strip()
    source_row = row.get("source_row") or row.get("№ п/п") or 0
    try:
        source_row = int(source_row or 0)
    except (TypeError, ValueError):
        source_row = 0
    company = Company(
        slug=str(row.get("slug") or slugify(name)),
        name=name,
        website=str(row.get("website") or "").strip(),
        description=description,
        inn=str(row.get("inn") or row.get("ИНН") or "").strip(),
        industry=industry,
        contact_name=str(row.get("contact_name") or row.get("ФИО руководителя") or "").strip(),
        address=str(row.get("address") or row.get("Юридический и фактический адрес") or "").strip(),
        contacts=str(row.get("contacts") or row.get("Контакты") or "").strip(),
        source_name=str(row.get("source_name") or "").strip(),
        source_row=source_row,
        export_experience=str(row.get("export_experience") or "").strip(),
        status=str(row.get("status") or "").strip(),
        history=str(row.get("history") or "").strip(),
        next_step=str(row.get("next_step") or "").strip(),
        region=str(row.get("region") or "Приморский край").strip(),
    )
    for field_name in LIST_FIELDS:
        value = row.get(field_name)
        if field_name == "products":
            value = products
        elif field_name == "hs_codes":
            value = hs_codes
        elif field_name == "export_countries":
            value = countries
        setattr(company, field_name, _as_list(value))
    if not company.export_experience and company.export_countries:
        company.export_experience = ", ".join(company.export_countries)
    if not company.history:
        company.history = description
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
        description=_yaml_string(company.description), inn=_yaml_string(company.inn),
        products=json.dumps(company.products, ensure_ascii=False),
        product_aliases=json.dumps(company.product_aliases, ensure_ascii=False),
        hs_codes=json.dumps(company.hs_codes, ensure_ascii=False),
        categories=json.dumps(company.categories, ensure_ascii=False),
        export_countries=json.dumps(company.export_countries, ensure_ascii=False),
        industry=_yaml_string(company.industry),
        source_name=_yaml_string(company.source_name), source_row=company.source_row,
        data_quality=json.dumps(company.data_quality, ensure_ascii=False),
        contact_name=_yaml_string(company.contact_name),
        address=_yaml_string(company.address), contacts=_yaml_string(company.contacts),
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
