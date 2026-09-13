#!/usr/bin/env python3
"""
Этап 1 конвейера: сбор сырья.

    python -m trade_agent.fetch
    python -m trade_agent.fetch --source tenders
    python -m trade_agent.fetch --days 14 --dry-run

Сбор не зависит от LLM. Сырьё сохраняется в raw_items и ждёт обработки,
поэтому недоступность модели или сбой одного источника ничего не теряют.

Сбор продолжается от последней успешной позиции КАЖДОГО источника, а не
от фиксированных «последних суток». При двух запусках в неделю суточное
окно оставляло дыры, а после простоя сервера (как 6–8 сентября, когда
Telegram трижды не отвечал) пропущенные дни не догружались вовсе.

К периоду добавляется небольшое перекрытие: поздние публикации и правки
попадают в сбор, а дубли отсекаются по хэшу материала.
"""

from __future__ import annotations

import argparse
import logging
import math
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from .config import load_settings
from .exit_codes import EXIT_CRITICAL, EXIT_OK, EXIT_PARTIAL
from .db import Database
from .models import RunLog, SourceState, utcnow
from .sources import build_sources
from .timeutil import parse_utc
from .utils import setup_logging

LOG = logging.getLogger("trade_agent.fetch")
STAGE = "fetch"


def plan_days(state: Optional[SourceState], settings: Any,
              requested: Optional[int] = None) -> tuple[int, str]:
    """
    Сколько дней читать у источника и почему.

    Первый запуск — период по умолчанию (7 дней). Дальше — от последней
    успешной позиции плюс перекрытие. После долгого простоя догружается
    весь пропущенный интервал, даже если он длиннее недели; сверху стоит
    предохранитель, чтобы случайно не начать читать всю историю канала.
    """
    if requested:
        return max(1, int(requested)), "период задан вручную"
    if state is None or not state.last_success_at:
        return int(settings.fetch_days), "первый запуск источника"

    anchor = parse_utc(state.last_published_at) or parse_utc(state.last_success_at)
    if anchor is None:
        return int(settings.fetch_days), "позиция источника нечитаема"

    overlap = timedelta(hours=int(getattr(settings, "fetch_overlap_hours", 12)))
    gap = datetime.now(timezone.utc) - anchor + overlap
    days = max(1, math.ceil(gap.total_seconds() / 86400))
    limit = int(getattr(settings, "fetch_max_backfill_days", 45))
    if days > limit:
        return limit, (f"пропущено больше {limit} дн., догружается верхняя граница; "
                       "остаток периода остаётся непокрытым")
    if not state.coverage_complete:
        return days, "в прошлый раз период покрыт не полностью, продолжаем догрузку"
    return days, "догрузка от последней успешной позиции с перекрытием"


def run(settings: Any, days: Optional[int] = None, only: Optional[str] = None,
        dry_run: bool = False) -> dict[str, Any]:
    started = time.monotonic()
    settings.ensure_dirs()
    db = Database(settings.db_path)
    run_id = None if dry_run else db.start_run(STAGE)

    log = RunLog(stage=STAGE)
    source_errors: dict[str, str] = {}
    per_source: dict[str, int] = {}
    incomplete: list[str] = []
    plans: dict[str, str] = {}

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
            state = db.get_source_state(source_id) or SourceState(source_id=source_id)
            window, why = plan_days(state, settings, days)
            plans[source_id] = f"{window} дн. — {why}"
            adapter.resume_from(state.cursor, state.last_published_at)

            try:
                result = adapter.fetch(days=window)
            except Exception as exc:  # noqa: BLE001 - сбой источника изолируется
                # Один упавший источник не отменяет чтение остальных.
                source_errors[source_id] = f"непредвиденная ошибка: {exc}"
                log.errors += 1
                LOG.exception("источник %s упал", source_id)
                if not dry_run:
                    state.last_error = str(exc)[:500]
                    state.coverage_complete = False
                    db.save_source_state(state)
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
            if not result.complete:
                incomplete.append(source_id)

            if not dry_run:
                # Три состояния хранятся раздельно: успешная проверка,
                # ошибка загрузки и неполное покрытие периода.
                if not result.error:
                    state.last_success_at = utcnow()
                    state.last_error = ""
                else:
                    state.last_error = result.error[:500]
                if result.latest_published_at:
                    state.last_published_at = max(state.last_published_at or "",
                                                  result.latest_published_at)
                if result.cursor:
                    state.cursor = result.cursor
                state.coverage_complete = bool(result.complete and not result.error)
                state.items_last_run = len(result.items)
                state.new_last_run = created_here
                db.save_source_state(state)

            LOG.info("источник %s: получено %d, новых %d, период %s",
                     source_id, len(result.items), created_here, plans[source_id])

        log.status = "partial" if (source_errors or incomplete) else "ok"
    except Exception as exc:  # noqa: BLE001
        log.status = "error"
        log.error_text = str(exc)
        LOG.exception("этап fetch прерван")
    finally:
        log.duration_sec = round(time.monotonic() - started, 2)
        log.details = {"per_source": per_source, "source_errors": source_errors,
                       "incomplete": incomplete, "plans": plans, "days": days}
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
        "incomplete": incomplete,
        "plans": plans,
        "dry_run": dry_run,
        "duration": log.duration_sec,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m trade_agent.fetch",
                                     description="Сбор сырья из источников.")
    parser.add_argument("--days", type=int, default=None,
                        help="жёстко задать период в днях; без него период "
                             "считается от последней успешной позиции источника")
    parser.add_argument("--source", default=None, help="только указанные источники (через запятую)")
    parser.add_argument("--dry-run", action="store_true", help="ничего не писать в базу")
    parser.add_argument("--verbose", "-v", action="store_true")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    settings = load_settings()
    setup_logging(settings.log_dir, args.verbose)
    stats = run(settings, args.days, args.source, args.dry_run)

    print(f"Источников проверено: {stats['sources']}")
    for source_id, plan in stats["plans"].items():
        print(f"  {source_id}: {plan}")
    print(f"Получено материалов: {stats['fetched']}")
    print(f"Новых: {stats['new']}")
    print(f"Дублей пропущено: {stats['duplicates']}")
    print(f"Ошибок: {stats['errors']}")
    if stats["incomplete"]:
        print("Период покрыт не полностью: " + ", ".join(stats["incomplete"]))
    print(f"Очередь на обработку: {stats['queue']}")
    if stats["dry_run"]:
        print("Dry-run: база не изменялась")

    if stats["status"] == "error":
        print("Статус: КРИТИЧЕСКИЙ СБОЙ — этап не выполнен", file=sys.stderr)
        return EXIT_CRITICAL
    if stats["errors"] or stats["incomplete"]:
        print(f"Статус: частичный сбор — источников с ошибкой: {stats['errors']}, "
              f"с неполным покрытием: {len(stats['incomplete'])}", file=sys.stderr)
        return EXIT_PARTIAL
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
