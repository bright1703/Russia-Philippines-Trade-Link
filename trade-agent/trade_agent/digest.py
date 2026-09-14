#!/usr/bin/env python3
"""
Этап 3 конвейера: выпуск обзора.

    python -m trade_agent.digest
    python -m trade_agent.digest --days 7 --dry-run

Главное изменение по сравнению с прежней версией — состав выпуска
формируется ОДИН раз. Карточки, счётчики, список компаний и посты
в Telegram строятся из одного сохранённого набора `issue_items`.

Раньше сигналы, анализы и совпадения выбирались независимо, каждый по
своему created_at. Анализ старого сигнала попадал в подтверждённые,
но сам сигнал в окно не попадал: подставлялся пустой Signal, карточка
отфильтровывалась, а счётчики считались до окончательного отбора. Так
и получалась шапка «0 новых сигналов, 2 проверенных вывода, 9 совпадений»
над разделами без единой карточки.

Формат выпуска — два направления торговли и короткий список того, что
стоит проверить. Внутренние баллы Scout, сырые подсказки HS и причины
модели в выпуск не выводятся: им место в /status.
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import os
import re
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from .config import load_settings
from .exit_codes import EXIT_CRITICAL, EXIT_OK, EXIT_PARTIAL
from .db import Database
from .agents import taxonomy
from .models import (
    Analysis, Company, DIRECTION_BOTH, DIRECTION_PH_TO_RU, DIRECTION_RU_TO_PH,
    EVENT_RULE_CHANGE, Issue, IssueItem, LINK_DIRECT,
    Match, RawItem, RunLog, Review, Signal, SIGNAL_REJECTED, VERIFY_NEEDS_CHECK,
    VERIFY_PRIMARY, VERIFY_SOURCE_CLAIM, utcnow,
)
from .timeutil import manila_date, manila_stamp, to_manila
from .utils import setup_logging, truncate

LOG = logging.getLogger("trade_agent.digest")
STAGE = "digest"

SECTION_RU_TO_PH = "RU_TO_PH"
SECTION_PH_TO_RU = "PH_TO_RU"
SECTION_WATCH = "WATCH"
SECTION_TITLES = {
    SECTION_RU_TO_PH: "Приморье → Филиппины",
    SECTION_PH_TO_RU: "Филиппины → РФ",
    SECTION_WATCH: "Наблюдать",
}

VERIFICATION_LABELS = {
    VERIFY_SOURCE_CLAIM: "по сообщению источника",
    VERIFY_PRIMARY: "проверен первичный документ",
    VERIFY_NEEDS_CHECK: "требуется уточнение",
}

URGENT_DEADLINE_STATES = ("urgent", "closing_soon")
MAX_ACTIONS = 3
COMPANIES_IN_CARD = 3

_EMOJI_RE = re.compile(
    "[\U0001F000-\U0001FAFF☀-➿️←-⇿⬀-⯿]+")


def _clean_title(value: str) -> str:
    """Заголовок без эмодзи и служебных значков."""
    return re.sub(r"\s+", " ", _EMOJI_RE.sub(" ", str(value or ""))).strip(" -—:·|")


def readable_title(item: Optional[RawItem], fallback: str = "") -> str:
    """
    Понятный заголовок карточки.

    Заголовок из одних эмодзи заменяется кратким пересказом начала текста:
    человек должен понимать, о чём карточка, не открывая ссылку.
    """
    title = _clean_title(item.title if item else "")
    if len(title) >= 12:
        return truncate(title, 160)
    body = _clean_title((item.raw_text if item else "") or fallback)
    first = re.split(r"(?<=[.!?])\s", body)[0] if body else ""
    return truncate(first or title or fallback or "Без заголовка", 160)


def _fmt_list(values: list[str], bullet: str = "- ") -> list[str]:
    return [f"{bullet}{v}" for v in values if v]


@dataclass
class Candidate:
    """
    Одно событие-кандидат со всем, что о нём известно.

    Сигнал, материал, анализ и совпадения собираются здесь ВМЕСТЕ, до
    всякой фильтрации. Разные временные окна для разных таблиц — ровно
    та ошибка, из-за которой карточка теряла свой сигнал.
    """
    signal: Signal
    item: Optional[RawItem] = None
    analysis: Optional[Analysis] = None
    review: Optional[Review] = None
    matches: list[Match] = field(default_factory=list)
    late: bool = False
    source_urls: list[str] = field(default_factory=list)

    @property
    def signal_id(self) -> int:
        return int(self.signal.id or 0)

    # Что изменилось с прошлой публикации этого события (заполняется
    # при отборе, если событие уже выходило).
    updates: list[str] = field(default_factory=list)

    @property
    def event_key(self) -> str:
        return self.signal.event_key or f"signal:{self.signal_id}"

    def fingerprint(self) -> dict[str, str]:
        from .events import fingerprint_fields

        return fingerprint_fields(self.signal, self.item or RawItem())


class IssueBuilder:
    """Сборка выпуска: собрать → отобрать один раз → отрисовать."""

    def __init__(self, db: Any, settings: Any):
        self.db = db
        self.settings = settings
        self.min_confidence = float(settings.digest_min_confidence)
        self.max_cards = int(getattr(settings, "digest_max_cards",
                                     settings.digest_max_per_section))
        self.min_score = int(settings.analyst_min_score)

    # -- 1. сбор -----------------------------------------------------------
    def collect(self, days: int) -> dict[str, Any]:
        signals = self.db.signals_since(days, min_score=0)
        passed: list[tuple[Analysis, Review]] = self.db.passed_analyses_since(days)

        by_id: dict[int, Signal] = {int(s.id or 0): s for s in signals}
        # Сигналы запоздавших анализов подтягиваются по идентификатору,
        # без фильтра по времени: анализ, готовый сегодня, не должен
        # терять свою новость только потому, что она старше окна.
        late_ids = [a.signal_id for a, _ in passed if a.signal_id not in by_id]
        late_signals = self.db.signals_by_ids(late_ids) if late_ids else {}
        by_id.update(late_signals)

        raw = {sid: self.db.get_raw_item(signal.raw_item_id)
               for sid, signal in by_id.items()}
        matches = self.db.matches_for_signals(
            by_id.keys(), self.settings.radar_min_match_score)
        matches_by_signal: dict[int, list[Match]] = {}
        for match in matches:
            matches_by_signal.setdefault(int(match.signal_id), []).append(match)

        return {
            "signals": signals,
            "by_id": by_id,
            "raw": raw,
            "passed": passed,
            "late_ids": set(late_signals),
            "matches_by_signal": matches_by_signal,
            "companies": {c.slug: c for c in self.db.all_companies()},
            "coverage": self.coverage(),
            "published": self.db.published_events(),
        }

    def coverage(self) -> dict[str, Any]:
        """
        Честный охват сбора.

        Молчание исправного источника, ошибка загрузки и неподключённый
        источник — разные состояния. Из них складывается фраза в шапке:
        «часть источников недоступна» вместо «изменений нет».
        """
        states = self.db.all_source_states()
        checked = [s.source_id for s in states if s.last_success_at and not s.last_error]
        failed = [s.source_id for s in states if s.last_error]
        incomplete = [s.source_id for s in states
                      if not s.coverage_complete and not s.last_error]
        # Неподключённый источник — это не ошибка загрузки: у него ещё
        # не было ни одной успешной проверки и нет записанной ошибки.
        never = [s.source_id for s in states if s.never_ran and not s.last_error]
        return {
            "checked": checked,
            "failed": failed,
            "incomplete": incomplete,
            "never_ran": never,
            "complete": not (failed or incomplete or never),
            "tenders_checked": any(s.source_id == "tenders" and s.last_success_at
                                   for s in states),
        }

    # -- 2. отбор ----------------------------------------------------------
    def _candidates(self, data: dict[str, Any]) -> list[Candidate]:
        by_id: dict[int, Signal] = data["by_id"]
        raw = data["raw"]
        late_ids: set[int] = data["late_ids"]
        best_analysis: dict[int, tuple[Analysis, Review]] = {}
        for analysis, review in data["passed"]:
            # Вывод с низкой уверенностью не выдаётся за проверенный:
            # карточка тогда строится из факта, без рекомендаций.
            if analysis.confidence < self.min_confidence:
                continue
            current = best_analysis.get(analysis.signal_id)
            if current is None or analysis.confidence > current[0].confidence:
                best_analysis[analysis.signal_id] = (analysis, review)

        candidates: list[Candidate] = []
        for signal_id, signal in by_id.items():
            item = raw.get(signal_id)
            pair = best_analysis.get(signal_id)
            candidates.append(Candidate(
                signal=signal,
                item=item,
                analysis=pair[0] if pair else None,
                review=pair[1] if pair else None,
                matches=data["matches_by_signal"].get(signal_id, []),
                late=signal_id in late_ids,
                source_urls=[item.source_url] if item and item.source_url else [],
            ))
        return candidates

    def _worth_showing(self, candidate: Candidate) -> bool:
        signal = candidate.signal
        item = candidate.item
        if signal.status == SIGNAL_REJECTED:
            return False
        if signal.must_alert:
            return True
        # Непроверенный вывод не публикуется как проверенный. Короткая
        # фактическая новость без анализа выйти может — но только если
        # прошла отбор по релевантности и не содержит рекомендаций.
        if signal.unverified:
            return False
        if signal.relevance_score < self.min_score:
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

    def _is_urgent(self, candidate: Candidate) -> bool:
        signal = candidate.signal
        item = candidate.item
        if signal.must_alert:
            return True
        if item is not None and item.source_type == "tender":
            meta = item.meta or {}
            return (meta.get("deadline_status") in URGENT_DEADLINE_STATES
                    and signal.relevance_score >= self.min_score
                    and int(meta.get("tender_match_score") or 0) >= 3)
        # Срочность не следует из слова «пошлина»: нужен подтверждённый
        # предмет изменения и максимальная оценка.
        return (signal.event_type == EVENT_RULE_CHANGE
                and signal.relevance_score >= 5
                and bool(signal.evidence_fragments))

    @staticmethod
    def _merge(primary: Candidate, extra: Candidate) -> None:
        """Перепечатка добавляет источник, а не вторую карточку."""
        for url in extra.source_urls:
            if url and url not in primary.source_urls:
                primary.source_urls.append(url)
        known = {(m.company_slug, m.link_type) for m in primary.matches}
        for match in extra.matches:
            if (match.company_slug, match.link_type) not in known:
                primary.matches.append(match)
        if primary.analysis is None and extra.analysis is not None:
            primary.analysis, primary.review = extra.analysis, extra.review

    def _deduplicate(self, candidates: list[Candidate]) -> list[Candidate]:
        merged: dict[str, Candidate] = {}
        for candidate in candidates:
            key = candidate.event_key
            existing = merged.get(key)
            if existing is None:
                merged[key] = candidate
                continue
            # Ведущей остаётся карточка с проверенным выводом, затем
            # с более ранней датой: исходная дата события важнее того,
            # какой канал перепечатал новость первым.
            if (existing.analysis is None and candidate.analysis is not None):
                candidate_wins = True
            elif (existing.analysis is not None and candidate.analysis is None):
                candidate_wins = False
            else:
                candidate_wins = (candidate.signal.relevance_score
                                  > existing.signal.relevance_score)
            if candidate_wins:
                self._merge(candidate, existing)
                merged[key] = candidate
            else:
                self._merge(existing, candidate)
        return list(merged.values())

    @staticmethod
    def _section(signal: Signal) -> str:
        if signal.trade_direction == DIRECTION_PH_TO_RU:
            return SECTION_PH_TO_RU
        if signal.trade_direction in (DIRECTION_RU_TO_PH, DIRECTION_BOTH):
            return SECTION_RU_TO_PH
        return SECTION_WATCH

    def _companies_block(self, candidate: Candidate,
                         companies: dict[str, Company]) -> tuple[list[dict[str, Any]],
                                                                 list[dict[str, Any]], int]:
        """
        Компании карточки, раздельно по виду связи.

        Прямая применимость и отраслевая связь не смешиваются: второе —
        это функция обзора, а не доказательство коммерческой возможности.
        """
        direct: list[dict[str, Any]] = []
        sector: list[dict[str, Any]] = []
        for match in sorted(candidate.matches,
                            key=lambda m: (0 if m.direct else 1, -m.match_score,
                                           m.company_slug)):
            company = companies.get(match.company_slug)
            row = {
                "slug": match.company_slug,
                "name": company.name if company else match.company_slug,
                "score": match.match_score,
                "action": match.recommended_action,
                "evidence": (match.evidence or [])[:1],
                "role": match.role,
            }
            (direct if match.link_type == LINK_DIRECT else sector).append(row)
        return direct[:20], sector[:20], len(candidate.matches)

    def compose(self, data: dict[str, Any], days: int,
                today: Optional[Any] = None) -> tuple[Issue, list[IssueItem]]:
        """
        Единственное место, где решается, что попадёт в выпуск.

        Возвращает выпуск и его карточки. Счётчики считаются по карточкам,
        поэтому шапка не может разойтись с содержимым.
        """
        companies: dict[str, Company] = data["companies"]
        published: dict[str, str] = data["published"]

        candidates = [c for c in self._candidates(data) if self._worth_showing(c)]
        candidates = self._deduplicate(candidates)

        # Повторное событие выходит снова только при существенном
        # обновлении — иначе один факт всплывает в каждом выпуске.
        repeats = 0
        fresh: list[Candidate] = []
        for candidate in candidates:
            previous = published.get(candidate.event_key)
            if previous is not None and not candidate.late:
                changes = self._material_changes(candidate, previous)
                if not changes:
                    repeats += 1
                    continue
                candidate.updates = changes
            fresh.append(candidate)
        candidates = fresh

        def sort_key(candidate: Candidate) -> tuple:
            return (0 if self._is_urgent(candidate) else 1,
                    -candidate.signal.relevance_score,
                    0 if candidate.analysis is not None else 1,
                    candidate.event_key)

        candidates.sort(key=sort_key)

        items: list[IssueItem] = []
        watch: list[IssueItem] = []
        for candidate in candidates:
            section = self._section(candidate.signal)
            item = self._to_issue_item(candidate, section, companies, published)
            if section == SECTION_WATCH:
                watch.append(item)
            elif len(items) < self.max_cards:
                items.append(item)
            else:
                item.section = SECTION_WATCH
                watch.append(item)

        items.extend(watch[:10])

        counters = self._counters(items, data, repeats)
        issue = Issue(
            kind="scheduled",
            period_start=self._period_start(days),
            period_end=utcnow(),
            counters=counters,
            coverage=data["coverage"],
        )
        return issue, items

    @staticmethod
    def _material_changes(candidate: Candidate,
                          previous: dict[str, Any]) -> list[str]:
        """
        Что существенно изменилось с прошлой публикации события.

        Пустой список означает «ничего не изменилось» — событие
        не повторяется. Важность события обновлением не является:
        иначе любая новость с оценкой 5 выходила бы в каждом выпуске.
        """
        from .events import changed_fields

        return changed_fields(previous.get("fingerprint") or {},
                              candidate.fingerprint())

    @staticmethod
    def _period_start(days: int) -> str:
        from datetime import datetime, timedelta, timezone
        return (datetime.now(timezone.utc)
                - timedelta(days=max(0, days))).isoformat(timespec="seconds")

    def _to_issue_item(self, candidate: Candidate, section: str,
                       companies: dict[str, Company],
                       published: dict[str, Any]) -> IssueItem:
        signal = candidate.signal
        item = candidate.item
        analysis = candidate.analysis
        direct, sector, total = self._companies_block(candidate, companies)

        if analysis is not None:
            fact = analysis.summary
            meaning = analysis.opportunity
            verification = (VERIFY_PRIMARY if signal.verification_status == VERIFY_PRIMARY
                            else VERIFY_SOURCE_CLAIM)
        else:
            # Без завершённого анализа выводов не публикуем: только факт
            # и объяснение, почему это отобрано.
            fact = truncate(_clean_title(item.raw_text if item else ""), 400) or signal.reason
            meaning = signal.reason
            verification = VERIFY_NEEDS_CHECK

        if signal.verification_status == VERIFY_NEEDS_CHECK:
            verification = VERIFY_NEEDS_CHECK

        action = ""
        if analysis is not None and analysis.next_step:
            action = analysis.next_step
        elif direct:
            action = direct[0]["action"]

        return IssueItem(
            section=section,
            event_key=candidate.event_key,
            signal_id=candidate.signal_id,
            analysis_id=int(analysis.id or 0) if analysis else None,
            title=readable_title(item, signal.reason),
            fact=truncate(fact, 700),
            meaning=truncate(meaning, 700),
            trade_direction=signal.trade_direction,
            event_type=signal.event_type,
            sectors=list(signal.sectors),
            source_urls=candidate.source_urls[:5],
            published_at=(item.published_at if item else "") or signal.first_seen_at,
            verification_status=verification,
            late_confirmation=candidate.late,
            urgent=self._is_urgent(candidate),
            action=truncate(action, 300),
            updates=list(candidate.updates),
            companies_direct=direct,
            companies_sector=sector,
            companies_total=total,
            fingerprint=candidate.fingerprint(),
        )

    def _counters(self, items: list[IssueItem], data: dict[str, Any],
                  repeats: int) -> dict[str, Any]:
        """Счётчики считаются по РЕАЛЬНО показанным карточкам."""
        shown = [i for i in items if i.section != SECTION_WATCH]
        signals: list[Signal] = data["signals"]
        dropped = [s for s in signals
                   if s.status == SIGNAL_REJECTED or s.relevance_score == 0]
        unverified = [s for s in signals if s.unverified and s.status != SIGNAL_REJECTED]
        companies = {row["slug"] for i in items for row in i.companies_direct}
        return {
            "cards": len(shown),
            "watch": len(items) - len(shown),
            "urgent": sum(1 for i in shown if i.urgent),
            "ru_to_ph": sum(1 for i in shown if i.section == SECTION_RU_TO_PH),
            "ph_to_ru": sum(1 for i in shown if i.section == SECTION_PH_TO_RU),
            "late_confirmations": sum(1 for i in items if i.late_confirmation),
            "companies_direct": len(companies),
            "company_links": sum(i.companies_total for i in items),
            "repeats_skipped": repeats,
            "dropped_as_noise": len(dropped),
            "unverified": len(unverified),
        }

    # -- 3. отрисовка ------------------------------------------------------
    def render(self, issue: Issue, items: list[IssueItem]) -> str:
        counters = issue.counters
        coverage = issue.coverage or {}
        lines: list[str] = [
            "# Trade Agent — обзор торговли",
            "",
            f"Период: {manila_date(issue.period_start)} — {manila_date(issue.period_end)}",
            f"Собрано: {manila_stamp(issue.period_end)}",
            f"Карточек в выпуске: {counters.get('cards', 0)}"
            + (f", срочных: {counters['urgent']}" if counters.get("urgent") else ""),
            self._coverage_line(coverage),
            "",
        ]

        for section in (SECTION_RU_TO_PH, SECTION_PH_TO_RU, SECTION_WATCH):
            block = [i for i in items if i.section == section]
            if not block:
                continue          # пустые секции в выпуск не попадают
            lines += [f"## {SECTION_TITLES[section]}", ""]
            if section == SECTION_WATCH:
                lines += self._watch_block(block)
                continue
            for entry in block:
                lines += self._card(entry)

        actions = self._actions(items)
        if actions:
            lines += ["## Что проверить или обсудить", ""]
            lines += _fmt_list(actions)
            lines.append("")

        if counters.get("cards", 0) == 0:
            lines += ["За период не отобрано ни одного события, заслуживающего карточки.",
                      ""]

        lines += self._footer(counters, coverage)
        return "\n".join(lines)

    @staticmethod
    def _coverage_line(coverage: dict[str, Any]) -> str:
        checked = coverage.get("checked") or []
        failed = coverage.get("failed") or []
        incomplete = coverage.get("incomplete") or []
        never = coverage.get("never_ran") or []
        if not (checked or failed or incomplete or never):
            return "Охват сбора: данных о проверке источников нет."
        parts = [f"Охват сбора: проверено источников {len(checked)}"]
        if failed:
            parts.append(f"недоступны: {', '.join(failed)}")
        if incomplete:
            parts.append(f"период покрыт не полностью: {', '.join(incomplete)}")
        if never:
            parts.append(f"не подключены: {', '.join(never)}")
        line = "; ".join(parts) + "."
        if failed or incomplete:
            line += " Часть источников недоступна — отсутствие новостей по ним " \
                    "не означает, что изменений нет."
        return line

    def _card(self, entry: IssueItem) -> list[str]:
        head = "### " + ("СРОЧНО. " if entry.urgent else "") + entry.title
        lines = [head, ""]
        if entry.fact:
            lines += [entry.fact, ""]
        if entry.meaning and entry.meaning != entry.fact:
            lines += [f"Что это значит: {entry.meaning}", ""]

        facts: list[str] = []
        if entry.sectors:
            facts.append("Отрасль: "
                         + ", ".join(taxonomy.sector_label(s) for s in entry.sectors[:3]))
        date_text = manila_date(entry.published_at) if entry.published_at else ""
        if date_text:
            facts.append(f"Дата: {date_text}")
        if entry.late_confirmation:
            facts.append("позднее подтверждение: анализ завершён уже после события")
        if entry.updates:
            facts.append("обновление ранее опубликованного события — "
                         + "; ".join(entry.updates[:2]))
        facts.append("Проверка: " + VERIFICATION_LABELS.get(entry.verification_status,
                                                            entry.verification_status))
        for url in entry.source_urls[:3]:
            facts.append(f"Источник: {url}")
        if len(entry.source_urls) > 1:
            facts.append(f"Событие подтверждено источниками: {len(entry.source_urls)}")
        lines += _fmt_list(facts)

        lines += self._companies_lines(entry)
        if entry.action:
            lines += ["", f"Предлагаемое действие: {entry.action}"]
        lines.append("")
        return lines

    @staticmethod
    def _companies_lines(entry: IssueItem) -> list[str]:
        lines: list[str] = []
        if entry.companies_direct:
            lines += ["", "Прямая применимость:"]
            for row in entry.companies_direct[:COMPANIES_IN_CARD]:
                suffix = f" — {row['action']}" if row.get("action") else ""
                lines.append(f"- {row['name']}{suffix}")
                if row.get("evidence"):
                    lines.append(f"  Основание: {truncate(row['evidence'][0], 160)}")
            rest = len(entry.companies_direct) - COMPANIES_IN_CARD
            if rest > 0:
                lines.append(f"- ещё компаний с прямой применимостью: {rest} "
                             f"(полный список: /companies {entry.signal_id})")
        if entry.companies_sector:
            lines += ["", f"Компании отрасли: {len(entry.companies_sector)}. "
                          "Применимость уточнить "
                          f"(список: /companies {entry.signal_id})"]
        return lines

    @staticmethod
    def _watch_block(items: list[IssueItem]) -> list[str]:
        lines: list[str] = []
        for entry in items:
            url = f" — {entry.source_urls[0]}" if entry.source_urls else ""
            companies = ""
            if entry.companies_total:
                companies = (f" Компаний по теме: {entry.companies_total} "
                             f"(/companies {entry.signal_id})")
            lines.append(f"- {entry.title}{url}.{companies}")
        lines.append("")
        return lines

    @staticmethod
    def _actions(items: list[IssueItem]) -> list[str]:
        """Не больше трёх действий, и только обоснованные."""
        actions: list[str] = []
        for entry in items:
            if entry.section == SECTION_WATCH or not entry.action:
                continue
            text = f"{entry.title}: {entry.action}"
            if text not in actions:
                actions.append(text)
            if len(actions) >= MAX_ACTIONS:
                break
        return actions

    @staticmethod
    def _footer(counters: dict[str, Any], coverage: dict[str, Any]) -> list[str]:
        lines = ["---", ""]
        notes: list[str] = []
        if counters.get("repeats_skipped"):
            notes.append(f"повторов без существенного обновления не показано: "
                         f"{counters['repeats_skipped']}")
        if counters.get("unverified"):
            notes.append(f"сигналов без завершённой проверки (не публикуются): "
                         f"{counters['unverified']}")
        if not coverage.get("tenders_checked"):
            notes.append("закупки в этом выпуске не проверялись — "
                         "это не означает, что подходящих закупок нет")
        if notes:
            lines += _fmt_list(notes) + [""]
        lines += ["Технические подробности выпуска — команда /status.",
                  "Проверка допуска и юридических условий остаётся за человеком. "
                  "Система предлагает действие, но никому не пишет.",
                  ""]
        return lines


def write_digest(markdown: str, digest_dir: Path, today: Optional[Any] = None,
                 dry_run: bool = False) -> dict[str, str]:
    from datetime import date

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


def content_hash(markdown: str) -> str:
    """Хэш отрисованного текста — для сверки файла выпуска с базой."""
    return hashlib.sha256(markdown.encode("utf-8")).hexdigest()[:32]


def composition_hash(items: list[IssueItem]) -> str:
    """
    Хэш состава выпуска: что именно показано человеку.

    Время сборки, длительность и прочие метки сюда НЕ входят — иначе два
    одинаковых по содержанию выпуска, собранных в разные минуты, выглядели
    бы разными, повторный запуск плодил бы выпуски-двойники, а доставка
    считала бы их новыми.
    """
    parts: list[str] = []
    for item in items:
        parts.append("|".join([
            item.section,
            item.event_key,
            str(item.signal_id or ""),
            str(item.analysis_id or ""),
            item.title,
            item.verification_status,
            "1" if item.urgent else "0",
            "1" if item.late_confirmation else "0",
            ",".join(sorted(row["slug"] for row in item.companies_direct)),
            ",".join(sorted(row["slug"] for row in item.companies_sector)),
            ";".join(f"{k}={v}" for k, v in sorted(item.fingerprint.items())),
        ]))
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()[:32]


def run(settings: Any, days: Optional[int] = None, dry_run: bool = False) -> dict[str, Any]:
    started = time.monotonic()
    settings.ensure_dirs()
    days = days or settings.digest_lookback_days
    db = Database(settings.db_path)
    run_id = None if dry_run else db.start_run(STAGE)
    log = RunLog(stage=STAGE)
    paths = {"latest": "", "archive": "", "written": "no"}
    counters: dict[str, Any] = {}
    issue_id: Optional[int] = None
    reused = False

    try:
        builder = IssueBuilder(db, settings)
        data = builder.collect(days)
        issue, items = builder.compose(data, days)
        markdown = builder.render(issue, items)
        counters = issue.counters
        digest_hash = content_hash(markdown)
        issue.composition_hash = composition_hash(items)

        # Повторный запуск с тем же составом не создаёт выпуск-двойник.
        # Иначе доставка, привязанная к идентификатору выпуска, увидела бы
        # новый выпуск и отправила бы то же самое ещё раз.
        existing = None if dry_run else db.issue_by_composition(issue.composition_hash)
        if existing is not None:
            issue_id = int(existing.id or 0)
            reused = True
            LOG.info("состав совпадает с выпуском %s — повторный выпуск "
                     "не создаётся", issue_id)
        else:
            reused = False
            if not dry_run:
                # Выпуск создаётся до записи файла, а помечается собранным
                # только после успешной записи: сбой сборки не должен
                # оставлять «готовый» выпуск для рассылки.
                issue.status = "building"
                issue_id = db.create_issue(issue)
                db.save_issue_items(issue_id, items)

        today = to_manila(issue.period_end)
        paths = write_digest(markdown, settings.digest_dir,
                             today.date() if today else None, dry_run=dry_run)

        if issue_id is not None and not reused:
            db.finish_issue(issue_id, "built", counters, issue.coverage,
                            paths["latest"], digest_hash, issue.composition_hash)
        elif issue_id is not None and reused and not dry_run:
            # Состав тот же, но в файле новая метка времени сборки.
            # Обновляем только ссылку на файл и хэш текста.
            db.refresh_issue_text(issue_id, paths["latest"], digest_hash)

        log.processed = len(data["signals"])
        log.created = counters.get("cards", 0)
        log.status = "ok"
        log.details = {**counters, "issue_id": issue_id, "reused_issue": reused}
    except Exception as exc:  # noqa: BLE001
        log.status = "error"
        log.error_text = str(exc)
        log.errors += 1
        if issue_id is not None and not reused:
            # Чужой, уже доставленный выпуск сбойным не помечаем.
            try:
                db.finish_issue(issue_id, "failed", counters, {}, "", "")
            except Exception:  # noqa: BLE001
                LOG.exception("не удалось пометить выпуск сбойным")
        LOG.exception("этап digest прерван")
    finally:
        log.duration_sec = round(time.monotonic() - started, 2)
        if run_id is not None:
            db.finish_run(run_id, log)
        db.close()

    return {**counters, "paths": paths, "status": log.status, "errors": log.errors,
            "dry_run": dry_run, "duration": log.duration_sec, "issue_id": issue_id,
            "reused_issue": bool(log.details.get("reused_issue")),
            "unverified": counters.get("unverified", 0)}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m trade_agent.digest",
                                     description="Формирование выпуска обзора.")
    parser.add_argument("--days", type=int, default=None, help="период в днях")
    parser.add_argument("--dry-run", action="store_true", help="не записывать файлы")
    parser.add_argument("--verbose", "-v", action="store_true")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    settings = load_settings()
    setup_logging(settings.log_dir, args.verbose)
    stats = run(settings, args.days, args.dry_run)
    print(f"Карточек в выпуске: {stats.get('cards', 0)}")
    print(f"  Приморье → Филиппины: {stats.get('ru_to_ph', 0)}")
    print(f"  Филиппины → РФ: {stats.get('ph_to_ru', 0)}")
    print(f"  Наблюдать: {stats.get('watch', 0)}")
    print(f"Компаний с прямой применимостью: {stats.get('companies_direct', 0)}")
    print(f"Поздних подтверждений: {stats.get('late_confirmations', 0)}")
    print(f"Повторов пропущено: {stats.get('repeats_skipped', 0)}")
    print(f"Не подтверждено рецензентом (не публикуется): {stats.get('unverified', 0)}")
    print(f"Ошибок: {stats['errors']}")
    print(f"Выпуск: {stats['paths']['latest']}"
          + (" (dry-run, файл не записан)" if stats["dry_run"] else ""))
    if stats.get("reused_issue"):
        print(f"Состав не изменился — переиспользован выпуск №{stats['issue_id']}, "
              "новый не создавался")

    if stats["status"] == "error":
        print("Статус: КРИТИЧЕСКИЙ СБОЙ — выпуск не сформирован", file=sys.stderr)
        return EXIT_CRITICAL
    if stats.get("unverified", 0):
        print(f"Статус: есть неподтверждённые сигналы: {stats['unverified']}",
              file=sys.stderr)
        return EXIT_PARTIAL
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
