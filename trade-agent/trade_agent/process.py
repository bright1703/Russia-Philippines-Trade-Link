#!/usr/bin/env python3
"""
Этап 2 конвейера: Scout → Analyst → Reviewer → Opportunity Radar.

    python -m trade_agent.process
    python -m trade_agent.process --limit 20
    python -m trade_agent.process --stage scout

Если LLM недоступен, материал остаётся в очереди и обрабатывается позже —
данные не теряются. Число доработок Analyst ограничено настройкой
reviewer_max_revisions, поэтому бесконечных циклов нет.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from typing import Any, Optional

from .agents import Analyst, Reviewer, Scout, ScoutResult
from .alerts import detect_mandatory_policy_alert
from .companies.loader import sync_companies
from .config import load_settings
from .exit_codes import EXIT_CRITICAL, EXIT_OK, EXIT_PARTIAL
from .db import Database
from .llm import build_client
from .models import (
    REVIEW_ERROR_MAX_REVISIONS, RunLog, SIGNAL_ANALYZED, SIGNAL_FAILED,
    SIGNAL_NEEDS_REVIEW, SIGNAL_NEW, SIGNAL_REJECTED, UNPUBLISHED_STATUSES,
    VERDICT_FAILED, VERDICT_PASS, VERDICT_REJECT, VERDICT_REVISE, Review,
)
from .radar import OpportunityRadar
from .utils import setup_logging

LOG = logging.getLogger("trade_agent.process")
STAGE = "process"


def run(settings: Any, limit: int = 100, stage: str = "all",
        dry_run: bool = False, llm_client: Optional[Any] = None) -> dict[str, Any]:
    started = time.monotonic()
    settings.ensure_dirs()
    db = Database(settings.db_path)
    run_id = None if dry_run else db.start_run(STAGE)
    log = RunLog(stage=STAGE)

    counters = {
        "scouted": 0, "signals": 0, "dropped": 0, "deferred": 0,
        "analysed": 0, "revised": 0, "rejected": 0, "matches": 0,
        "review_retry": 0, "review_failed": 0, "needs_review": 0,
    }

    try:
        created, updated = sync_companies(db, settings.brain_dir)
        LOG.info("профили компаний: создано %d, обновлено %d", created, updated)
        companies = db.all_companies()

        llm = llm_client or build_client(settings)
        scout = Scout(llm, settings)
        analyst = Analyst(llm, settings)
        reviewer = Reviewer(llm, settings)
        radar = OpportunityRadar(settings)

        # --- Scout ------------------------------------------------------
        if stage in ("all", "scout"):
            for item in db.raw_items_without_signal(limit):
                counters["scouted"] += 1
                log.processed += 1
                forced_signal = detect_mandatory_policy_alert(item, companies)
                if forced_signal is not None:
                    LOG.info("обязательный товарный триггер: %s", item.title[:120])
                    result = ScoutResult(signal=forced_signal)
                else:
                    result = scout.evaluate(item)
                if result.deferred:
                    counters["deferred"] += 1
                    continue                      # остаётся в очереди
                if result.dropped or result.signal is None:
                    counters["dropped"] += 1
                    if not dry_run:
                        from .models import Signal
                        db.upsert_signal(Signal(
                            raw_item_id=int(item.id or 0), category="OTHER",
                            relevance_score=0, reason=result.drop_reason,
                            status=SIGNAL_REJECTED,
                        ))
                    continue
                counters["signals"] += 1
                if not dry_run:
                    signal_id, _ = db.upsert_signal(result.signal)
                    result.signal.id = signal_id

        # --- Analyst + Reviewer + Radar ---------------------------------
        # Принцип fail-closed: matches и публикация возможны ТОЛЬКО после
        # вердикта PASS. Любой другой исход оставляет сигнал неопубликованным.
        if stage in ("all", "analyst"):
            known_titles = set()
            max_attempts = settings.reviewer_max_revisions + 1
            for signal in db.signals_for_analysis(settings.analyst_min_score, limit,
                                                  max_attempts=max_attempts):
                signal_id = int(signal.id or 0)
                item = db.get_raw_item(signal.raw_item_id)
                if item is None:
                    continue
                relevant = [c for c in companies
                            if radar.match_all(signal, item, [c])] or companies[:5]

                analysis = None
                review: Optional[Review] = None
                problems: list[str] = []
                previous = None

                for revision in range(settings.reviewer_max_revisions + 1):
                    analysis = analyst.analyse(signal, item, relevant, revision, problems, previous)
                    if analysis is None:
                        break                     # LLM недоступен — вернуть позже
                    if dry_run:
                        break
                    analysis.id = db.insert_analysis(analysis)
                    review = reviewer.review(analysis, signal, item, companies, known_titles)
                    db.insert_review(review)
                    if review.verdict in (VERDICT_PASS, VERDICT_REJECT, VERDICT_FAILED):
                        break
                    # VERDICT_REVISE — доработка, но не бесконечно
                    problems = review.problems
                    previous = analysis
                    counters["revised"] += 1
                    log.retries += 1
                else:
                    review = None                 # цикл исчерпан без решения

                if analysis is None:
                    counters["deferred"] += 1
                    continue
                if dry_run:
                    counters["analysed"] += 1
                    continue

                counters["analysed"] += 1

                # --- разбор исхода ---------------------------------------
                if review is not None and review.verdict == VERDICT_PASS:
                    db.set_signal_status(signal_id, SIGNAL_ANALYZED)
                    for match in radar.match_all(signal, item, companies):
                        db.upsert_match(match)
                        counters["matches"] += 1
                    known_titles.add((item.title or "").strip().lower())
                    continue

                if review is not None and review.verdict == VERDICT_REJECT:
                    counters["rejected"] += 1
                    db.set_signal_status(signal_id, SIGNAL_REJECTED, "reviewer_reject")
                    continue

                if review is not None and review.verdict == VERDICT_FAILED:
                    attempts = db.bump_review_attempt(signal_id, review.error)
                    if review.retryable and attempts < max_attempts:
                        counters["review_retry"] += 1
                        LOG.warning("сигнал %s: рецензия не состоялась (%s), попытка %d/%d",
                                    signal_id, review.error, attempts, max_attempts)
                        # статус остаётся new — материал вернётся в следующий запуск
                    else:
                        counters["review_failed"] += 1
                        db.set_signal_status(signal_id, SIGNAL_FAILED, review.error)
                        LOG.error("сигнал %s помечен failed: %s", signal_id, review.error)
                    continue

                # Ревизии исчерпаны, согласия нет — публиковать нельзя.
                counters["needs_review"] += 1
                db.bump_review_attempt(signal_id, REVIEW_ERROR_MAX_REVISIONS)
                db.set_signal_status(signal_id, SIGNAL_NEEDS_REVIEW, REVIEW_ERROR_MAX_REVISIONS)
                LOG.warning("сигнал %s: исчерпаны доработки, требуется ручная проверка", signal_id)

        # --- Radar для сигналов без анализа ------------------------------
        if stage in ("all", "radar") and not dry_run:
            for signal in db.signals_since(settings.digest_lookback_days + 1, min_score=1):
                # Неподтверждённые сигналы не порождают совпадений.
                # Сигналы, которые вообще не доходили до Analyst (низкая
                # оценка Scout), матчатся как подсказки для раздела
                # «Наблюдать» — у них нет непроверенного вывода.
                if signal.unverified:
                    continue
                item = db.get_raw_item(signal.raw_item_id)
                for match in radar.match_all(signal, item, companies):
                    _, is_new = db.upsert_match(match)
                    counters["matches"] += int(is_new)

        usage = llm.usage() if hasattr(llm, "usage") else {}
        log.status = "ok"
        log.created = counters["signals"]
        log.skipped = counters["dropped"]
        log.details = {**counters, "llm_usage": usage}
    except Exception as exc:  # noqa: BLE001
        log.status = "error"
        log.error_text = str(exc)
        log.errors += 1
        LOG.exception("этап process прерван")
        usage = {}
    finally:
        log.duration_sec = round(time.monotonic() - started, 2)
        if run_id is not None:
            db.finish_run(run_id, log)
        stats_snapshot = db.stats()
        db.close()

    return {**counters, "llm_usage": usage, "queue": stats_snapshot["queue"],
            "status": log.status, "errors": log.errors, "dry_run": dry_run,
            "duration": log.duration_sec}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m trade_agent.process",
                                     description="Обработка сырья агентами.")
    parser.add_argument("--limit", type=int, default=100, help="сколько материалов взять за запуск")
    parser.add_argument("--stage", choices=("all", "scout", "analyst", "radar"), default="all")
    parser.add_argument("--dry-run", action="store_true", help="ничего не писать в базу")
    parser.add_argument("--mock-llm", action="store_true",
                        help="офлайн-проверка конвейера без API-ключа (ответы синтетические)")
    parser.add_argument("--verbose", "-v", action="store_true")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    settings = load_settings()
    setup_logging(settings.log_dir, args.verbose)
    client = None
    if args.mock_llm:
        from .llm.offline import build_offline_client
        client = build_offline_client(settings)
        print("ВНИМАНИЕ: --mock-llm, ответы синтетические, это не анализ")
    stats = run(settings, args.limit, args.stage, args.dry_run, client)

    print(f"Scouted: {stats['scouted']}")
    print(f"Signals kept: {stats['signals']}")
    print(f"Dropped as noise: {stats['dropped']}")
    print(f"Deferred (LLM unavailable): {stats['deferred']}")
    print(f"Analysed: {stats['analysed']}")
    print(f"Revisions: {stats['revised']}")
    print(f"Rejected by reviewer: {stats['rejected']}")
    print(f"Review retry (вернутся позже): {stats['review_retry']}")
    print(f"Review failed (постоянная ошибка): {stats['review_failed']}")
    print(f"Needs manual review: {stats['needs_review']}")
    print(f"Opportunity matches: {stats['matches']}")
    print(f"Queue left: {stats['queue']}")
    print(f"LLM calls: {stats['llm_usage'].get('calls', 0)}, "
          f"tokens in/out: {stats['llm_usage'].get('input_tokens', 0)}/"
          f"{stats['llm_usage'].get('output_tokens', 0)}")
    if stats["dry_run"]:
        print("Dry-run: база не изменялась")

    if stats["status"] == "error":
        print("Статус: КРИТИЧЕСКИЙ СБОЙ — этап не выполнен", file=sys.stderr)
        return EXIT_CRITICAL
    unfinished = (stats["deferred"] + stats["review_retry"]
                  + stats["review_failed"] + stats["needs_review"])
    if stats["errors"] or unfinished:
        print(f"Статус: частичный результат — отложено {stats['deferred']}, "
              f"повтор рецензии {stats['review_retry']}, "
              f"ошибок рецензии {stats['review_failed']}, "
              f"ручная проверка {stats['needs_review']}", file=sys.stderr)
        return EXIT_PARTIAL
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
