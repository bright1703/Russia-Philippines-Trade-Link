#!/usr/bin/env python3
"""
Tender Radar — Philippines.

Ежедневный обход официальных филиппинских источников закупок,
нормализация, дедупликация, оценка релевантности для компаний
Приморского края и формирование дайджеста.

Запуск:
    python fetch_tenders.py
    python fetch_tenders.py --dry-run
    python fetch_tenders.py --source philgeps
    python fetch_tenders.py --days 7

Модуль не читает .env, не использует cookies браузера, сессию Telegram
и личные аккаунты. Секреты не запрашиваются и не выводятся.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Optional

import yaml

import adapters
from adapters.base import HttpClient, SourceError
from match import load_profiles, score_all
from normalize import Notice, dedupe_notices, normalize_notice, parse_date
from pdf_extract import cleanup_raw, enrich_notice
from render_digest import RELEVANT_THRESHOLD, render, split_notices, write_digest
from storage import MemoryStore, SqliteStore, open_store

BASE_DIR = Path(__file__).resolve().parent
LOG = logging.getLogger("tenders")


# --------------------------------------------------------------------------
# Конфигурация
# --------------------------------------------------------------------------

def load_sources(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    data = yaml.safe_load(path.read_text("utf-8")) or {}
    defaults = data.get("defaults") or {}
    sources = data.get("sources") or []
    merged: list[dict[str, Any]] = []
    for source in sources:
        cfg = dict(defaults)
        cfg.update(source)
        merged.append(cfg)
    return defaults, merged


def select_sources(sources: list[dict[str, Any]], only: Optional[str]) -> list[dict[str, Any]]:
    if only:
        wanted = {s.strip() for s in only.split(",") if s.strip()}
        chosen = [s for s in sources if s.get("id") in wanted]
        missing = wanted - {s.get("id") for s in chosen}
        for source_id in sorted(missing):
            LOG.error("источник '%s' не найден в sources.yml", source_id)
        return chosen
    return [s for s in sources if s.get("enabled")]


# --------------------------------------------------------------------------
# Сбор
# --------------------------------------------------------------------------

def collect(sources: list[dict[str, Any]], client: HttpClient, days: int,
            today: date) -> tuple[list[Notice], dict[str, str], int]:
    """Обходит источники. Ошибка одного источника не останавливает остальные."""
    notices: list[Notice] = []
    errors: dict[str, str] = {}
    checked = 0

    for cfg in sources:
        source_id = cfg.get("id", "?")
        checked += 1
        try:
            adapter = adapters.build_adapter(cfg, client)
            result = adapter.fetch(days=days)
        except SourceError as exc:
            errors[source_id] = str(exc)
            LOG.error("источник %s не ответил: %s", source_id, exc)
            continue
        except Exception as exc:  # noqa: BLE001 - изоляция сбоя источника
            errors[source_id] = f"непредвиденная ошибка: {exc}"
            LOG.exception("источник %s: непредвиденная ошибка", source_id)
            continue

        if result.error:
            errors[source_id] = result.error
            LOG.error("источник %s: %s", source_id, result.error)

        for raw in result.items:
            try:
                notices.append(normalize_notice(raw, cfg, today))
            except Exception as exc:  # noqa: BLE001
                LOG.warning("источник %s: не удалось нормализовать запись: %s", source_id, exc)
        LOG.info("источник %s: страниц %d, записей %d", source_id, result.pages_fetched, len(result.items))

    return notices, errors, checked


def within_window(notice: Notice, days: int, today: date) -> bool:
    """Фильтр периода: свежие и ещё открытые объявления."""
    horizon = today - timedelta(days=days)
    published = parse_date(notice.publish_date)
    closing = notice.closing_date_obj
    if closing is not None and closing >= today:
        return True                       # открытое — всегда в работе
    if published is not None:
        return published >= horizon
    if closing is not None:
        return closing >= horizon         # недавно закрытое — покажем в исключённых
    return True                           # дат нет — решает сопоставление


# --------------------------------------------------------------------------
# Запуск
# --------------------------------------------------------------------------

def run(args: argparse.Namespace) -> dict[str, Any]:
    today = date.today()
    base_dir = BASE_DIR
    defaults, all_sources = load_sources(base_dir / "sources.yml")
    profiles = load_profiles(base_dir / "profiles.yml")

    sources = select_sources(all_sources, args.source)
    if args.fixtures:
        fixture_dir = Path(args.fixtures)
        for cfg in sources:
            cfg["adapter"] = "fixture"
            cfg["fixture_path"] = str(fixture_dir / f"{cfg['id']}.html")
            cfg["follow_detail"] = False

    client = HttpClient(defaults)
    notices, errors, checked = collect(sources, client, args.days, today)

    before_dedupe = len(notices)
    notices = [n for n in notices if within_window(n, args.days, today)]
    notices = dedupe_notices(notices)
    duplicates_merged = before_dedupe - len(notices)

    if not args.no_pdf:
        for notice in notices:
            needs = not notice.closing_date or not notice.notice_id or not notice.estimated_budget
            if needs and notice.attachment_urls:
                try:
                    enrich_notice(notice, client, base_dir / ".raw", today=today)
                except Exception as exc:  # noqa: BLE001
                    LOG.warning("PDF-обработка не удалась для %s: %s", notice.title[:60], exc)

    score_all(notices, profiles)

    # Хранилище: в dry-run работаем с копией в памяти.
    db_path = base_dir / "tenders.db"
    if args.dry_run:
        seed: list[Notice] = []
        if args.store == "sqlite" and db_path.exists():
            existing = SqliteStore(db_path)
            seed = existing.all_notices()
            existing.close()
        store = MemoryStore(seed)
    else:
        store = open_store(args.store, db_path if args.store == "sqlite" else base_dir / "tenders.json")

    new_count = 0
    updated_count = 0
    for notice in notices:
        is_new, changes = store.upsert(notice)
        if is_new:
            new_count += 1
        elif changes:
            updated_count += 1

    buckets = split_notices(notices)
    relevant = len(buckets["urgent"]) + len(buckets["other"])

    stats = {
        "days": args.days,
        "sources_checked": checked,
        "new_notices": new_count,
        "updated_notices": updated_count,
        "relevant": relevant,
        "urgent": len(buckets["urgent"]),
        "errors": len(errors),
        "source_errors": errors,
        "duplicates_merged": duplicates_merged,
    }

    markdown = render(notices, stats, today, duplicates_merged=duplicates_merged)
    paths = write_digest(markdown, base_dir, today, dry_run=args.dry_run)

    if not args.dry_run:
        store.record_run(stats)
    store.close()

    removed = cleanup_raw(base_dir / ".raw", keep=args.keep_raw)
    LOG.info("временных файлов удалено: %d", removed)

    stats["digest"] = paths["latest"]
    stats["archive"] = paths["archive"]
    stats["dry_run"] = args.dry_run
    return stats


def print_summary(stats: dict[str, Any]) -> None:
    """Единственный вывод в stdout. Никаких секретов и содержимого .env."""
    digest = stats["digest"]
    try:
        digest = str(Path(digest).relative_to(Path.cwd()))
    except ValueError:
        pass
    print(f"Sources checked: {stats['sources_checked']}")
    print(f"New notices: {stats['new_notices']}")
    print(f"Updated notices: {stats['updated_notices']}")
    print(f"Relevant notices: {stats['relevant']}")
    print(f"Urgent notices: {stats['urgent']}")
    print(f"Errors: {stats['errors']}")
    print(f"Digest: {digest}" + (" (dry-run, файл не перезаписан)" if stats["dry_run"] else ""))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fetch_tenders.py",
        description="Ежедневный радар филиппинских тендеров для компаний Приморского края.",
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="не изменять архив, дайджест и базу")
    parser.add_argument("--source", default=None,
                        help="проверить только указанные источники (id через запятую)")
    parser.add_argument("--days", type=int, default=7,
                        help="глубина периода проверки в днях (по умолчанию 7)")
    parser.add_argument("--store", choices=("sqlite", "json"), default="sqlite",
                        help="тип хранилища (по умолчанию sqlite)")
    parser.add_argument("--no-pdf", action="store_true", help="не скачивать и не разбирать PDF")
    parser.add_argument("--keep-raw", action="store_true", help="не удалять временные файлы .raw/")
    parser.add_argument("--fixtures", default=None,
                        help="офлайн-режим: каталог с локальными HTML-фикстурами (<source_id>.html)")
    parser.add_argument("--verbose", "-v", action="store_true", help="подробный лог в stderr")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    try:
        stats = run(args)
    except KeyboardInterrupt:
        print("Прервано пользователем.", file=sys.stderr)
        return 130
    except Exception as exc:  # noqa: BLE001 - последний рубеж
        LOG.error("критическая ошибка запуска: %s", exc)
        return 1
    print_summary(stats)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
