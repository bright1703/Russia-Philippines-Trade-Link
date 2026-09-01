#!/usr/bin/env python3
"""
Этап 1 конвейера: сбор сырья.

    python -m trade_agent.fetch
    python -m trade_agent.fetch --source tenders
    python -m trade_agent.fetch --days 14 --dry-run

Сбор не зависит от LLM. Сырьё сохраняется в raw_items и ждёт обработки,
поэтому недоступность модели или сбой одного источника ничего не теряют.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from typing import Any, Optional

from .config import load_settings
from .exit_codes import EXIT_CRITICAL, EXIT_OK, EXIT_PARTIAL
from .db import Database
from .models import RunLog
from .sources import build_sources
from .utils import setup_logging

LOG = logging.getLogger("trade_agent.fetch")
STAGE = "fetch"


def run(settings: Any, days: int, only: Optional[str] = None,
        dry_run: bool = False) -> dict[str, Any]:
    started = time.monotonic()
    settings.ensure_dirs()
    db = Database(settings.db_path)
    run_id = None if dry_run else db.start_run(STAGE)

    log = RunLog(stage=STAGE)
    source_errors: dict[str, str] = {}
    per_source: dict[str, int] = {}

    try:
        adapters = build_sources(settings, only)
        if not adapters:
            # Явно запрошенный источник, которого нет, — это ошибка запуска,
            # а не «успешный сбор нуля материалов».
            raise RuntimeError(
                f"не найдено ни одного источника по фильтру '{only}'" if only
                else "в sources.yml нет включённых источников"
            )

        for adapter in adapters:
            source_id = adapter.source_id
            try:
                result = adapter.fetch(days=days)
            except Exception as exc:  # noqa: BLE001 - сбой источника изолируется
                source_errors[source_id] = f"непредвиденная ошибка: {exc}"
                log.errors += 1
                LOG.exception("источник %s упал", source_id)
                continue

            log.retries += result.retries
            if result.error:
                source_errors[source_id] = result.error
                log.errors += 1
                LOG.error("источник %s: %s", source_id, result.error)

            created_here = 0
            for item in result.items:
                log.processed += 1
                if dry_run:
                    created_here += 1
                    continue
                try:
                    _, created = db.upsert_raw_item(item)
                except Exception as exc:  # noqa: BLE001
                    log.errors += 1
                    LOG.warning("не удалось сохранить материал из %s: %s", source_id, exc)
                    continue
                if created:
                    created_here += 1
                else:
                    log.skipped += 1
            log.created += created_here
            per_source[source_id] = created_here
            LOG.info("источник %s: получено %d, новых %d", source_id, len(result.items), created_here)

        log.status = "partial" if source_errors else "ok"
    except Exception as exc:  # noqa: BLE001
        log.status = "error"
        log.error_text = str(exc)
        LOG.exception("этап fetch прерван")
    finally:
        log.duration_sec = round(time.monotonic() - started, 2)
        log.details = {"per_source": per_source, "source_errors": source_errors, "days": days}
        if run_id is not None:
            db.finish_run(run_id, log)
        queue = db.stats()["queue"]
        db.close()

    return {
        "status": log.status,
        "sources": len(per_source) + len(source_errors),
        "fetched": log.processed,
        "new": log.created,
        "duplicates": log.skipped,
        "errors": log.errors,
        "queue": queue,
        "source_errors": source_errors,
        "dry_run": dry_run,
        "duration": log.duration_sec,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m trade_agent.fetch",
                                     description="Сбор сырья из источников.")
    parser.add_argument("--days", type=int, default=None, help="глубина периода в днях")
    parser.add_argument("--source", default=None, help="только указанные источники (через запятую)")
    parser.add_argument("--dry-run", action="store_true", help="ничего не писать в базу")
    parser.add_argument("--verbose", "-v", action="store_true")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    settings = load_settings()
    setup_logging(settings.log_dir, args.verbose)
    stats = run(settings, args.days or settings.fetch_days, args.source, args.dry_run)

    print(f"Sources checked: {stats['sources']}")
    print(f"Items fetched: {stats['fetched']}")
    print(f"New raw items: {stats['new']}")
    print(f"Duplicates skipped: {stats['duplicates']}")
    print(f"Errors: {stats['errors']}")
    print(f"Queue for processing: {stats['queue']}")
    if stats["dry_run"]:
        print("Dry-run: база не изменялась")

    if stats["status"] == "error":
        print("Статус: КРИТИЧЕСКИЙ СБОЙ — этап не выполнен", file=sys.stderr)
        return EXIT_CRITICAL
    if stats["errors"]:
        print(f"Статус: частичный сбой, источников с ошибкой: {stats['errors']}",
              file=sys.stderr)
        return EXIT_PARTIAL
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
