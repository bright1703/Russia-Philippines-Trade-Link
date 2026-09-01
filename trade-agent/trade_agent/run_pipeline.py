"""Единичный полный прогон сбора, анализа, дайджеста и доставки."""

from __future__ import annotations

import argparse
import logging
import sys
from typing import Optional

from .config import load_settings
from .digest import run as run_digest
from .exit_codes import EXIT_CRITICAL, EXIT_OK, EXIT_PARTIAL
from .fetch import run as run_fetch
from .notify import send_latest
from .process import run as run_process
from .utils import setup_logging

LOG = logging.getLogger("trade_agent.run_pipeline")


def run(settings, *, days: Optional[int] = None, limit: int = 100,
        sources: Optional[str] = "telegram", notify: bool = True) -> dict:
    """Выполняет этапы последовательно, не теряя результат частичного сбоя."""
    days = days or settings.fetch_days
    result = {"fetch": {}, "process": {}, "digest": {}, "notify": {}}
    result["fetch"] = run_fetch(settings, days=days, only=sources)
    result["process"] = run_process(settings, limit=limit)
    result["digest"] = run_digest(settings, days=days)

    if notify:
        try:
            count = send_latest(settings)
            result["notify"] = {"status": "ok", "chats": count}
        except Exception as exc:  # noqa: BLE001 - доставка не отменяет дайджест
            LOG.error("дайджест создан, но доставка не выполнена: %s", exc)
            result["notify"] = {"status": "error", "error": str(exc)}

    failed = any(value.get("status") in ("error", "partial")
                 for value in result.values() if isinstance(value, dict))
    if result["notify"].get("status") == "error":
        failed = True
    result["status"] = "partial" if failed else "ok"
    return result


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m trade_agent.run_pipeline",
        description="Полный прогон Trade Agent.",
    )
    parser.add_argument("--days", type=int, default=None)
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--sources", default="telegram",
                        help="источники через запятую, по умолчанию telegram")
    parser.add_argument("--no-notify", action="store_true")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args(argv)
    settings = load_settings()
    setup_logging(settings.log_dir, args.verbose, filename="pipeline.log")
    result = run(settings, days=args.days, limit=args.limit,
                 sources=args.sources, notify=not args.no_notify)
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
