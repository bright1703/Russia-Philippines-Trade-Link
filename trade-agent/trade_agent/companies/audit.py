#!/usr/bin/env python3
"""
Отчёт проверки классификации каталога.

    python -m trade_agent.companies.audit
    python -m trade_agent.companies.audit --out ../digest/catalog-audit.md

Зачем нужен. В рабочей базе 332 компании: 329 из каталога экспортеров
Приморского края и 3 отдельных профиля. У 89 компаний единственная
категория OTHER, у 126 стоит LOGISTICS — в том числе у производителя
напитков и у производителя косметики, потому что слово «порт» искалось
как подстрока внутри «экспорт». Такой классификации доверять нельзя,
но и переписывать её молча тоже нельзя.

Отчёт ничего не меняет в базе. Он показывает по каждой записи: что было
в каталоге, что предлагается, на каком основании и что осталось спорным.
Решение принимает человек.

Отдельно считается сводка миграции: все ли исходные записи на месте,
сохранены ли ИНН, контакты и ссылка на строку источника.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Optional

from ..agents import taxonomy
from ..config import load_settings
from ..db import Database
from ..models import Company

CATALOG_SOURCE = "Каталог экспортеров Приморского края"


def _escape(value: str) -> str:
    return str(value or "").replace("|", "/").replace("\n", " ").strip()


def proposed_sectors(company: Company) -> tuple[list[str], list[str]]:
    """
    Что предлагается по отрасли и на каком основании.

    Сначала исходная отрасль каталога, затем уточнение по продукции.
    Если ни то, ни другое не сработало, запись выносится на проверку,
    а не получает выдуманную отрасль ради формального устранения OTHER.
    """
    if company.sectors:
        return list(company.sectors), list(company.sector_basis)

    sectors: list[str] = []
    basis: list[str] = []
    for sector in taxonomy.sectors_from_industry(company.industry):
        sectors.append(sector)
        basis.append(f"колонка каталога: {company.industry}")
    text = " ".join([*company.products, *company.product_aliases, company.description])
    for sector, hits in taxonomy.guess_sectors(text, limit=3):
        if sector in sectors:
            continue
        sectors.append(sector)
        basis.append(f"продукция ({hits} совпад.): {taxonomy.sector_label(sector)}")
    return sectors, basis


def disputes(company: Company, sectors: list[str]) -> list[str]:
    """Спорные места, которые нужно посмотреть глазами."""
    notes: list[str] = []
    if not sectors:
        notes.append("отрасль не определена")
    if company.categories == ["OTHER"]:
        notes.append("в рабочей базе единственная категория OTHER")
    if ("LOGISTICS" in company.categories
            and taxonomy.SECTOR_LOGISTICS not in sectors):
        notes.append("категория LOGISTICS не подтверждается продукцией "
                     "(вероятно, ложное совпадение «порт» внутри «экспорт»)")
    if not company.products:
        notes.append("нет продукции")
    if not company.hs_codes:
        notes.append("нет кодов ТН ВЭД — компанию нельзя исключать из обзора из-за этого")
    for code in company.hs_codes:
        digits = "".join(ch for ch in code if ch.isdigit())
        if len(digits) == 10:
            notes.append(f"код {code} — национальный 10-значный код РФ, "
                         "филиппинским точным кодом он не является")
            break
        if len(digits) < 4:
            notes.append(f"код {code} слишком короткий для проверки")
            break
    if len(company.sectors) > 3:
        notes.append("предложено больше трёх отраслей — проверить")
    return notes


def build_report(companies: list[Company]) -> str:
    """Таблица проверки классификации по каждой записи каталога."""
    rows: list[str] = []
    unresolved = 0
    for company in sorted(companies, key=lambda c: (c.source_row or 0, c.name)):
        sectors, basis = proposed_sectors(company)
        notes = disputes(company, sectors)
        if not sectors:
            unresolved += 1
        rows.append(" | ".join([
            str(company.source_row or ""),
            _escape(company.name),
            _escape(company.inn),
            _escape(company.industry or "не заполнена"),
            _escape(", ".join(taxonomy.sector_label(s) for s in sectors) or "—"),
            _escape("; ".join(basis) or "—"),
            _escape(", ".join(company.products[:3]) or "—"),
            _escape(", ".join(company.hs_codes[:3]) or "—"),
            _escape("; ".join(company.data_quality) or "—"),
            _escape("; ".join(notes) or "—"),
        ]))

    summary = migration_summary(companies)
    lines = [
        "# Проверка классификации каталога",
        "",
        "Отчёт ничего не меняет в базе. Исходная отрасль каталога — отправная",
        "точка, а не гарантия точности. Предложенная отрасль без основания",
        "не назначается: спорные записи вынесены на проверку человеком.",
        "",
        "## Сводка миграции",
        "",
    ]
    for key, value in summary.items():
        lines.append(f"- {key}: {value}")
    lines += [
        "",
        f"- записей без определённой отрасли (на ручную проверку): {unresolved}",
        "",
        "## Записи",
        "",
        "| Строка | Компания | ИНН | Отрасль каталога | Предлагаемые отрасли | "
        "Основание | Продукция | ТН ВЭД | Качество данных | Спорные места |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    lines += [f"| {row} |" for row in rows]
    lines.append("")
    return "\n".join(lines)


def migration_summary(companies: list[Company]) -> dict[str, Any]:
    """
    Все ли исходные записи учтены и сохранены ли поля источника.

    Неизвестные поля не выдумываются: пустое значение так и считается
    пустым и попадает в отчёт.
    """
    catalog = [c for c in companies if c.source_name == CATALOG_SOURCE]
    separate = [c for c in companies if c.source_name != CATALOG_SOURCE]
    by_inn: dict[str, list[str]] = {}
    for company in companies:
        if company.inn:
            by_inn.setdefault(company.inn, []).append(company.name)
    duplicates = {inn: names for inn, names in by_inn.items() if len(names) > 1}
    return {
        "всего записей": len(companies),
        "из каталога экспортеров": len(catalog),
        "отдельные профили": len(separate),
        "с ИНН": sum(1 for c in companies if c.inn),
        "с контактами": sum(1 for c in companies if c.contacts or c.contact_name),
        "со ссылкой на строку источника": sum(1 for c in companies if c.source_row),
        "с продукцией": sum(1 for c in companies if c.products),
        "с кодами ТН ВЭД": sum(1 for c in companies if c.hs_codes),
        "совпадающий ИНН у разных записей (проверить, не дубли ли)": (
            "; ".join(f"{inn}: {', '.join(names)}" for inn, names in duplicates.items())
            or "нет"),
    }


def run(settings: Any, out: Optional[Path] = None) -> dict[str, Any]:
    db = Database(settings.db_path)
    try:
        companies = db.all_companies()
    finally:
        db.close()
    report = build_report(companies)
    if out is not None:
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_text(report, "utf-8")
    return {"companies": len(companies), "report": report,
            "path": str(out) if out else ""}


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m trade_agent.companies.audit",
        description="Отчёт проверки классификации каталога.")
    parser.add_argument("--out", type=Path, default=None,
                        help="файл отчёта; без него отчёт печатается в stdout")
    args = parser.parse_args(argv)
    settings = load_settings()
    stats = run(settings, args.out)
    if args.out:
        print(f"Компаний в отчёте: {stats['companies']}")
        print(f"Отчёт: {stats['path']}")
    else:
        print(stats["report"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
