#!/usr/bin/env python3
"""
Этап 3 конвейера: ежедневный дайджест.

    python -m trade_agent.digest
    python -m trade_agent.digest --days 3 --dry-run

Цель дайджеста — фильтрация, а не пересылка всего подряд.
Человек должен увидеть только то, что требует решения.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import tempfile
import time
from datetime import date
from pathlib import Path
from typing import Any, Optional

from .config import load_settings
from .exit_codes import EXIT_CRITICAL, EXIT_OK, EXIT_PARTIAL
from .db import Database
from .models import (
    Analysis, Company, Match, RawItem, RunLog, Review, Signal,
    SIGNAL_FAILED, SIGNAL_NEEDS_REVIEW, SIGNAL_REJECTED, UNPUBLISHED_STATUSES,
)
from .utils import setup_logging, truncate

LOG = logging.getLogger("trade_agent.digest")
STAGE = "digest"

URGENT_DEADLINE_STATES = ("urgent", "closing_soon")


def _fmt_list(values: list[str], bullet: str = "  - ") -> list[str]:
    return [f"{bullet}{v}" for v in values if v]


class DigestBuilder:
    def __init__(self, db: Any, settings: Any):
        self.db = db
        self.settings = settings

    def collect(self, days: int) -> dict[str, Any]:
        signals = self.db.signals_since(days, min_score=0)
        by_id = {int(s.id or 0): s for s in signals}
        raw_by_signal = {
            int(s.id or 0): self.db.get_raw_item(s.raw_item_id) for s in signals
        }
        passed = self.db.passed_analyses_since(days)
        matches = self.db.matches_since(days, self.settings.radar_min_match_score)
        companies = {c.slug: c for c in self.db.all_companies()}
        return {
            "signals": signals,
            "by_id": by_id,
            "raw": raw_by_signal,
            "passed": passed,
            "matches": matches,
            "companies": companies,
        }

    # -- разделы -----------------------------------------------------------
    def _is_urgent(self, signal: Signal, item: Optional[RawItem]) -> bool:
        if item is None:
            return False
        if item.source_type == "tender":
            meta = item.meta or {}
            if meta.get("status") in ("cancelled", "awarded"):
                return False
            return (meta.get("deadline_status") in URGENT_DEADLINE_STATES
                    and signal.relevance_score >= self.settings.analyst_min_score
                    and int(meta.get("tender_match_score") or 0) >= 3)
        # Регуляторное изменение считается срочным только при максимальной оценке.
        return signal.category == "REGULATION" and signal.relevance_score >= 5

    def _worth_showing(self, signal: Signal, item: Optional[RawItem]) -> bool:
        """Фильтр шума: в основные разделы попадает только значимое."""
        if signal.relevance_score < self.settings.analyst_min_score:
            return False
        if item is not None and item.source_type == "tender":
            meta = item.meta or {}
            if meta.get("status") in ("cancelled", "awarded"):
                return False
            if meta.get("deadline_status") == "closed":
                return False
            if int(meta.get("tender_match_score") or 0) < 3:
                return False
        return True

    def build(self, data: dict[str, Any], days: int, today: Optional[date] = None) -> str:
        today = today or date.today()
        signals: list[Signal] = data["signals"]
        raw = data["raw"]
        passed: list[tuple[Analysis, Review]] = data["passed"]
        matches: list[Match] = data["matches"]
        companies: dict[str, Company] = data["companies"]

        # Неподтверждённые сигналы (rejected / failed / needs_review) в обычный
        # дайджест не попадают — только в статистику раздела «Исключено».
        kept = [s for s in signals if not s.unverified and s.relevance_score > 0]
        dropped = [s for s in signals if s.status == SIGNAL_REJECTED or s.relevance_score == 0]
        unverified = [s for s in signals
                      if s.unverified and s.status != SIGNAL_REJECTED]

        cap = int(self.settings.digest_max_per_section)
        min_conf = float(self.settings.digest_min_confidence)

        shown: set[int] = set()

        urgent = [s for s in kept if self._is_urgent(s, raw.get(int(s.id or 0)))]
        urgent.sort(key=lambda s: -s.relevance_score)
        urgent = urgent[:cap]
        shown.update(int(s.id or 0) for s in urgent)

        # Возможности: только проверенные выводы с достаточной уверенностью.
        opportunities = [
            (a, r) for a, r in passed
            if a.confidence >= min_conf
            and a.signal_id not in shown
            and self._worth_showing(data["by_id"].get(a.signal_id) or Signal(),
                                    raw.get(a.signal_id))
        ]
        seen_signals: set[int] = set()
        deduped: list[tuple[Analysis, Review]] = []
        for analysis, review in opportunities:
            if analysis.signal_id in seen_signals:
                continue
            seen_signals.add(analysis.signal_id)
            deduped.append((analysis, review))
        opportunities = deduped[:cap]
        shown.update(a.signal_id for a, _ in opportunities)

        regulation = [s for s in kept
                      if s.category == "REGULATION"
                      and int(s.id or 0) not in shown
                      and self._worth_showing(s, raw.get(int(s.id or 0)))][:cap]
        shown.update(int(s.id or 0) for s in regulation)

        tenders = [s for s in kept
                   if (raw.get(int(s.id or 0)) or RawItem()).source_type == "tender"
                   and int(s.id or 0) not in shown
                   and self._worth_showing(s, raw.get(int(s.id or 0)))][:cap]
        shown.update(int(s.id or 0) for s in tenders)

        watch = [s for s in kept if int(s.id or 0) not in shown]
        low_value = len(watch)

        lines: list[str] = [
            "# Trade Agent — ежедневный дайджест",
            "",
            f"Дата: {today.isoformat()}",
            f"Период: последние {days} дн.",
            f"Новых сигналов: {len(kept)} | Проверенных выводов: {len(passed)} | "
            f"Совпадений с компаниями: {len(matches)}",
            "",
            "> Система показывает только то, что требует решения. "
            "Все выводы проверены рецензентом, но окончательное решение принимает человек.",
            "",
        ]

        # --- Срочно -------------------------------------------------------
        lines += ["## Срочно", ""]
        if urgent:
            passed_by_signal = {a.signal_id: (a, r) for a, r in passed if a.confidence >= min_conf}
            for signal in urgent:
                sid = int(signal.id or 0)
                pair = passed_by_signal.get(sid)
                if pair is not None:
                    # У срочного пункта есть проверенный вывод — показываем его целиком.
                    lines += self._analysis_block(pair[0], pair[1], signal,
                                                  raw.get(sid), matches, companies)
                else:
                    lines += self._signal_block(signal, raw.get(sid), matches, companies)
        else:
            lines += ["Ничего, что требует реакции сегодня.", ""]

        # --- Возможности --------------------------------------------------
        lines += ["## Возможности", ""]
        if opportunities:
            for analysis, review in opportunities:
                signal = data["by_id"].get(analysis.signal_id)
                item = raw.get(analysis.signal_id)
                lines += self._analysis_block(analysis, review, signal, item, matches, companies)
        else:
            lines += ["Проверенных возможностей за период нет.", ""]

        # --- Регуляторные изменения ---------------------------------------
        lines += ["## Регуляторные изменения", ""]
        if regulation:
            for signal in regulation:
                lines += self._signal_block(signal, raw.get(int(signal.id or 0)), matches, companies)
        else:
            lines += ["Изменений не зафиксировано.", ""]

        # --- Тендеры ------------------------------------------------------
        lines += ["## Тендеры", ""]
        if tenders:
            for signal in tenders:
                lines += self._signal_block(signal, raw.get(int(signal.id or 0)), matches, companies)
        else:
            lines += ["Подходящих закупок за период нет.", ""]

        # --- Наблюдать ----------------------------------------------------
        lines += ["## Наблюдать", ""]
        if watch:
            for signal in watch[:15]:
                item = raw.get(int(signal.id or 0))
                lines.append(
                    f"- [{signal.category} {signal.relevance_score}/5] "
                    f"{truncate((item.title if item else signal.reason), 140)}"
                    + (f" — {item.source_url}" if item and item.source_url else "")
                )
            lines.append("")
        else:
            lines += ["Пока нечего наблюдать.", ""]

        # --- Исключено ----------------------------------------------------
        reasons: dict[str, int] = {}
        for signal in dropped:
            key = (signal.reason or "без причины").split(":")[0][:60]
            reasons[key] = reasons.get(key, 0) + 1
        lines += ["## Исключено", "", f"Отброшено как шум: {len(dropped)}",
                  f"Не показано (низкая значимость или уже показано выше): {low_value}",
                  f"Не подтверждено рецензентом (не публикуется): {len(unverified)}"]
        if unverified:
            reasons_unverified: dict[str, int] = {}
            for signal in unverified:
                key = signal.last_error or signal.status
                reasons_unverified[key] = reasons_unverified.get(key, 0) + 1
            for reason, count in sorted(reasons_unverified.items(), key=lambda x: -x[1]):
                lines.append(f"  - {reason}: {count}")
            lines.append("  Проверить вручную: команда /status в боте или таблица signals.")
        for reason, count in sorted(reasons.items(), key=lambda x: -x[1])[:8]:
            lines.append(f"- {reason}: {count}")
        lines += ["", "---", "",
                  "Проверка допуска и юридических условий всегда остаётся за человеком. "
                  "Система не подтверждает право компании участвовать в конкретной закупке.",
                  ""]
        return "\n".join(lines)

    # -- блоки -------------------------------------------------------------
    def _matches_for(self, signal_id: int, matches: list[Match],
                     companies: dict[str, Company]) -> list[str]:
        rows: list[str] = []
        for match in matches:
            if match.signal_id != signal_id:
                continue
            company = companies.get(match.company_slug)
            name = company.name if company else match.company_slug
            rows.append(f"{name} ({match.match_score}/5) — {match.recommended_action}")
        return rows

    def _signal_block(self, signal: Signal, item: Optional[RawItem],
                      matches: list[Match], companies: dict[str, Company]) -> list[str]:
        title = item.title if item else signal.reason
        lines = [f"### {truncate(title, 180)}", ""]
        if item:
            lines.append(f"- Источник: {item.source} ({item.source_type})")
            if item.source_url:
                lines.append(f"- Ссылка: {item.source_url}")
            if item.published_at:
                lines.append(f"- Дата: {item.published_at}")
            meta = item.meta or {}
            if item.source_type == "tender":
                lines.append(f"- Ведомство: {meta.get('agency', 'unknown')}")
                lines.append(f"- Дедлайн: {meta.get('closing_date') or 'не указан'} "
                             f"({meta.get('deadline_status', '')})")
                budget = meta.get("estimated_budget")
                lines.append(f"- Бюджет: {budget if budget else 'не указан'} {meta.get('currency', '')}".rstrip())
                notes = meta.get("eligibility_notes") or []
                if notes:
                    lines.append("- Допуск: " + "; ".join(notes[:3]))
        lines.append(f"- Категория: {signal.category}, оценка Scout: {signal.relevance_score}/5")
        if signal.hs_codes:
            lines.append(f"- Предполагаемые HS: {', '.join(signal.hs_codes[:6])}")
        lines.append(f"- Почему отобрано: {truncate(signal.reason, 300)}")

        company_rows = self._matches_for(int(signal.id or 0), matches, companies)
        if company_rows:
            lines += ["", "Кого это касается:", ""] + _fmt_list(company_rows, "- ")
        lines.append("")
        return lines

    def _analysis_block(self, analysis: Analysis, review: Review, signal: Optional[Signal],
                        item: Optional[RawItem], matches: list[Match],
                        companies: dict[str, Company]) -> list[str]:
        title = item.title if item else analysis.summary
        lines = [f"### {truncate(title, 180)}", ""]
        lines.append(f"- Компания: {analysis.company or 'нет прямого совпадения'}")
        if item:
            lines.append(f"- Источник: {item.source} ({item.source_type})")
            if item.source_url:
                lines.append(f"- Ссылка: {item.source_url}")
        lines.append(f"- Уверенность анализа: {analysis.confidence:.2f}; "
                     f"вердикт рецензента: {review.verdict}")
        lines += ["", f"Что произошло: {analysis.summary}", "",
                  f"Что это значит: {analysis.opportunity}", ""]
        if analysis.regulation:
            lines += [f"Регулирование: {analysis.regulation}", ""]
        if analysis.market_data:
            lines += [f"Рыночные данные: {analysis.market_data}", ""]
        if analysis.risks:
            lines += ["Риски:", ""] + _fmt_list(analysis.risks, "- ") + [""]
        if analysis.what_to_verify:
            lines += ["Что проверить:", ""] + _fmt_list(analysis.what_to_verify, "- ") + [""]
        if analysis.suggested_actions:
            lines += ["Предлагаемые действия:", ""] + _fmt_list(analysis.suggested_actions, "- ") + [""]
        if analysis.next_step:
            lines += [f"Следующий шаг: {analysis.next_step}", ""]
        if review.problems:
            lines += ["Замечания рецензента (учтены):", ""] + _fmt_list(review.problems[:5], "- ") + [""]
        if analysis.sources:
            lines += ["Источники:", ""] + _fmt_list(analysis.sources, "- ") + [""]

        company_rows = self._matches_for(analysis.signal_id, matches, companies)
        if company_rows:
            lines += ["Кого ещё это касается:", ""] + _fmt_list(company_rows, "- ") + [""]
        return lines


def write_digest(markdown: str, digest_dir: Path, today: Optional[date] = None,
                 dry_run: bool = False) -> dict[str, str]:
    today = today or date.today()
    digest_dir = Path(digest_dir)
    latest = digest_dir / "latest.md"
    archive = digest_dir / "archive" / f"{today.isoformat()}.md"
    if dry_run:
        return {"latest": str(latest), "archive": str(archive), "written": "no"}
    digest_dir.mkdir(parents=True, exist_ok=True)
    archive.parent.mkdir(parents=True, exist_ok=True)

    def atomic_write(path: Path) -> None:
        temporary_name = None
        try:
            with tempfile.NamedTemporaryFile(
                "w", encoding="utf-8", dir=str(path.parent),
                prefix=f".{path.name}.", suffix=".tmp", delete=False,
            ) as handle:
                temporary_name = handle.name
                handle.write(markdown)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_name, path)
        except OSError:
            if temporary_name:
                try:
                    Path(temporary_name).unlink(missing_ok=True)
                except OSError:
                    pass
            raise

    atomic_write(latest)
    atomic_write(archive)
    return {"latest": str(latest), "archive": str(archive), "written": "yes"}


def run(settings: Any, days: Optional[int] = None, dry_run: bool = False) -> dict[str, Any]:
    started = time.monotonic()
    settings.ensure_dirs()
    days = days or settings.digest_lookback_days
    db = Database(settings.db_path)
    run_id = None if dry_run else db.start_run(STAGE)
    log = RunLog(stage=STAGE)
    paths = {"latest": "", "archive": "", "written": "no"}
    counts: dict[str, int] = {}

    try:
        data = DigestBuilder(db, settings).collect(days)
        markdown = DigestBuilder(db, settings).build(data, days)
        paths = write_digest(markdown, settings.digest_dir, dry_run=dry_run)
        counts = {
            "signals": len(data["signals"]),
            "analyses": len(data["passed"]),
            "matches": len(data["matches"]),
            "unverified": sum(1 for s in data["signals"] if s.unverified
                              and s.status != "rejected"),
        }
        log.processed = counts["signals"]
        log.created = counts["analyses"]
        log.status = "ok"
        log.details = counts
    except Exception as exc:  # noqa: BLE001
        log.status = "error"
        log.error_text = str(exc)
        log.errors += 1
        LOG.exception("этап digest прерван")
    finally:
        log.duration_sec = round(time.monotonic() - started, 2)
        if run_id is not None:
            db.finish_run(run_id, log)
        db.close()

    return {**counts, "paths": paths, "status": log.status, "errors": log.errors,
            "dry_run": dry_run, "duration": log.duration_sec,
            "unverified": counts.get("unverified", 0)}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m trade_agent.digest",
                                     description="Формирование ежедневного дайджеста.")
    parser.add_argument("--days", type=int, default=None, help="период в днях")
    parser.add_argument("--dry-run", action="store_true", help="не записывать файлы")
    parser.add_argument("--verbose", "-v", action="store_true")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    settings = load_settings()
    setup_logging(settings.log_dir, args.verbose)
    stats = run(settings, args.days, args.dry_run)
    print(f"Signals in period: {stats.get('signals', 0)}")
    print(f"Verified analyses: {stats.get('analyses', 0)}")
    print(f"Company matches: {stats.get('matches', 0)}")
    print(f"Errors: {stats['errors']}")
    print(f"Unverified signals (не опубликованы): {stats.get('unverified', 0)}")
    print(f"Digest: {stats['paths']['latest']}"
          + (" (dry-run, файл не записан)" if stats["dry_run"] else ""))

    if stats["status"] == "error":
        print("Статус: КРИТИЧЕСКИЙ СБОЙ — дайджест не сформирован", file=sys.stderr)
        return EXIT_CRITICAL
    if stats.get("unverified", 0):
        print(f"Статус: есть неподтверждённые сигналы: {stats['unverified']}",
              file=sys.stderr)
        return EXIT_PARTIAL
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
