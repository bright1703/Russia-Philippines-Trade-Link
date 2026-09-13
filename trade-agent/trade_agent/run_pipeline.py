"""
Единичный полный прогон: сбор → обработка → выпуск → доставка.

    python -m trade_agent.run_pipeline              # ручной прогон
    python -m trade_agent.run_pipeline --scheduled  # плановый прогон по таймеру

Требование пользователя — ВСЕГО два автоматических запуска в неделю.
Поэтому плановый прогон считает, сколько автоматических циклов уже было
с начала календарной недели по Маниле, и третий не начинает: догоняющий
старт после простоя тоже считается циклом. Ручной прогон человеком этим
ограничением не связан, но и он не может идти одновременно с другим —
одновременные запуски исключены блокировкой.
"""

from __future__ import annotations

import argparse
import logging
from datetime import timedelta
from typing import Any, Optional

from .config import load_settings
from .db import Database
from .digest import run as run_digest
from .exit_codes import EXIT_CRITICAL, EXIT_OK, EXIT_PARTIAL
from .fetch import run as run_fetch
from .locking import AlreadyRunning, exclusive_run
from .notify import send_latest
from .process import run as run_process
from .timeutil import manila_now
from .utils import setup_logging

LOG = logging.getLogger("trade_agent.run_pipeline")

STAGE = "pipeline"
MAX_AUTOMATIC_CYCLES_PER_WEEK = 2


def week_start_utc() -> str:
    """Начало календарной недели (понедельник, 00:00 по Маниле) в UTC."""
    now = manila_now()
    monday = (now - timedelta(days=now.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0)
    from datetime import timezone

    return monday.astimezone(timezone.utc).isoformat(timespec="seconds")


def cycles_this_week(settings: Any) -> int:
    db = Database(settings.db_path)
    try:
        return db.count_runs_since(STAGE, week_start_utc())
    finally:
        db.close()


def run(settings: Any, *, days: Optional[int] = None, limit: int = 100,
        sources: Optional[str] = None, notify: bool = True,
        scheduled: bool = False) -> dict[str, Any]:
    """
    Выполняет этапы последовательно, не теряя результат частичного сбоя.

    Период сбора по умолчанию не задаётся: каждый источник догружает
    интервал от своей последней успешной позиции. Жёсткий `--days`
    оставлен для ручной перепроверки.
    """
    result: dict[str, Any] = {"fetch": {}, "process": {}, "digest": {}, "notify": {}}

    if scheduled:
        already = cycles_this_week(settings)
        if already >= MAX_AUTOMATIC_CYCLES_PER_WEEK:
            LOG.warning("на этой неделе уже выполнено автоматических циклов: %d — "
                        "плановый прогон пропущен", already)
            result["status"] = "skipped"
            result["reason"] = (f"лимит {MAX_AUTOMATIC_CYCLES_PER_WEEK} автоматических "
                                f"циклов в неделю уже исчерпан ({already})")
            return result

    db = Database(settings.db_path)
    run_id = db.start_run(STAGE)
    db.close()

    result["fetch"] = run_fetch(settings, days=days, only=sources)
    result["process"] = run_process(settings, limit=limit)
    result["digest"] = run_digest(settings, days=days or settings.digest_lookback_days)

    if notify:
        try:
            count = send_latest(settings)
            result["notify"] = {"status": "ok" if count else "partial", "chats": count}
        except Exception as exc:  # noqa: BLE001 - доставка не отменяет выпуск
            LOG.error("выпуск собран, но доставка не выполнена: %s", exc)
            result["notify"] = {"status": "error", "error": str(exc)}

    process_needs_attention = any(
        int(result["process"].get(name, 0) or 0) > 0
        for name in ("deferred", "review_retry", "review_failed", "needs_review")
    )
    if process_needs_attention and result["process"].get("status") == "ok":
        result["process"]["status"] = "partial"
    failed = any(value.get("status") in ("error", "partial")
                 for value in result.values() if isinstance(value, dict))
    result["status"] = "partial" if failed else "ok"
    result["run_id"] = run_id

    database = Database(settings.db_path)
    try:
        from .models import RunLog

        log = RunLog(stage=STAGE, status=result["status"])
        log.details = {stage: result[stage].get("status", "")
                       for stage in ("fetch", "process", "digest", "notify")}
        log.processed = int(result["fetch"].get("new", 0) or 0)
        log.created = int(result["digest"].get("cards", 0) or 0)
        log.errors = int(result["fetch"].get("errors", 0) or 0) \
            + int(result["process"].get("errors", 0) or 0) \
            + int(result["digest"].get("errors", 0) or 0)
        database.finish_run(run_id, log)
    finally:
        database.close()
    return result


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m trade_agent.run_pipeline",
        description="Полный прогон Trade Agent.",
    )
    parser.add_argument("--days", type=int, default=None,
                        help="жёсткий период сбора; без него каждый источник "
                             "догружает от своей последней позиции")
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--sources", default=None,
                        help="источники через запятую; без него берутся все "
                             "включённые в sources.yml")
    parser.add_argument("--scheduled", action="store_true",
                        help="плановый прогон: учитывать лимит двух циклов в неделю")
    parser.add_argument("--no-notify", action="store_true")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args(argv)
    settings = load_settings()
    setup_logging(settings.log_dir, args.verbose, filename="pipeline.log")
    settings.ensure_dirs()

    try:
        with exclusive_run(settings.db_path.parent / "pipeline.lock", STAGE):
            result = run(settings, days=args.days, limit=args.limit,
                         sources=args.sources, notify=not args.no_notify,
                         scheduled=args.scheduled)
    except AlreadyRunning as exc:
        print(f"Прогон не начат: {exc}")
        return EXIT_OK

    if result["status"] == "skipped":
        print(f"Плановый прогон пропущен: {result['reason']}")
        return EXIT_OK
    for stage in ("fetch", "process", "digest", "notify"):
        print(f"{stage}: {result[stage]}")
    if result["status"] == "ok":
        return EXIT_OK
    if result["fetch"].get("status") == "error" or \
            result["process"].get("status") == "error":
        return EXIT_CRITICAL
    return EXIT_PARTIAL


if __name__ == "__main__":
    raise SystemExit(main())
