"""
Формирование ежедневного дайджеста tenders/latest.md и архивной копии.
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable, Optional

from match import STANDARD_CHECKLIST, FOREIGN_RESTRICTED_MARK, explain
from normalize import Notice

RELEVANT_THRESHOLD = 3
DEADLINE_LABELS = {
    "urgent": "менее 3 дней",
    "closing_soon": "3–7 дней",
    "open": "более 7 дней",
    "closed": "дедлайн прошёл",
    "deadline_unknown": "дедлайн не указан",
}


def _fmt_budget(notice: Notice) -> str:
    if not notice.estimated_budget:
        return "не указан"
    return f"{notice.estimated_budget:,.2f} {notice.currency or 'PHP'}".replace(",", " ")


def _fmt_deadline(notice: Notice) -> str:
    if not notice.closing_date:
        return "не указан"
    tail = f" {notice.closing_time}" if notice.closing_time else ""
    return f"{notice.closing_date}{tail}"


def _fmt_days(notice: Notice) -> str:
    if notice.days_until_deadline is None:
        return "неизвестно"
    if notice.days_until_deadline < 0:
        return f"просрочено на {abs(notice.days_until_deadline)}"
    return str(notice.days_until_deadline)


def _fmt_links(urls: Iterable[str]) -> str:
    urls = [u for u in urls if u]
    return ", ".join(urls) if urls else "нет"


def _notice_block(notice: Notice) -> list[str]:
    lines = [f"### {notice.title}", ""]
    sources = notice.source_name or notice.source_id
    if len(notice.source_ids) > 1:
        sources += f" (+{len(notice.source_ids) - 1} дублирующих источника: {', '.join(notice.source_ids[1:])})"
    lines += [
        f"- Источник: {sources}",
        f"- Агентство: {notice.agency or 'unknown'}",
        f"- Категория: {notice.category or 'unknown'}",
        f"- Номер закупки: {notice.notice_id or 'unknown'}",
        f"- Дедлайн: {_fmt_deadline(notice)}",
        f"- Осталось дней: {_fmt_days(notice)} ({DEADLINE_LABELS.get(notice.deadline_status, notice.deadline_status)})",
        f"- Статус: {notice.status}",
        f"- Оценка: {notice.match_score}/5",
        f"- Подходящие профили: {', '.join(notice.matched_profiles) if notice.matched_profiles else 'нет'}",
        f"- Бюджет: {_fmt_budget(notice)}",
        f"- Ссылка: {notice.original_url or 'нет'}",
        f"- Документы: {_fmt_links(notice.attachment_urls)}",
    ]
    contact_bits = [b for b in (notice.contact_name, notice.contact_email, notice.contact_phone) if b]
    lines.append(f"- Контакты: {', '.join(contact_bits) if contact_bits else 'не указаны'}")
    if notice.unconfirmed:
        lines.append("- ВНИМАНИЕ: ранний сигнал, не подтверждён официальным источником")
    lines += ["", "Почему это важно:", "", explain(notice), ""]
    lines += ["Причины оценки:", ""]
    lines += [f"- {reason}" for reason in notice.match_reasons]
    lines += ["", "Допуск и ограничения:", ""]
    lines += [f"- {note}" for note in notice.eligibility_notes]
    lines += ["", "Что проверить:", ""]
    lines += [f"- {item};" for item in STANDARD_CHECKLIST]
    lines += [""]
    return lines


def split_notices(notices: list[Notice]) -> dict[str, list[Notice]]:
    """Разделяет объявления на секции дайджеста и на исключённые."""
    urgent: list[Notice] = []
    other: list[Notice] = []
    excluded: dict[str, list[Notice]] = {
        "closed": [], "irrelevant": [], "thin": [], "local_only": [], "cancelled": [],
    }

    for notice in notices:
        if notice.status == "cancelled":
            excluded["cancelled"].append(notice)
            continue
        if notice.deadline_status == "closed" or notice.status in ("closed", "awarded"):
            excluded["closed"].append(notice)
            continue
        if FOREIGN_RESTRICTED_MARK in notice.eligibility_notes and notice.match_score < RELEVANT_THRESHOLD:
            excluded["local_only"].append(notice)
            continue
        if notice.match_score < RELEVANT_THRESHOLD:
            if notice.match_score > 0 and len(notice.description or "") < 60:
                excluded["thin"].append(notice)
            else:
                excluded["irrelevant"].append(notice)
            continue
        if notice.deadline_status in ("urgent", "closing_soon"):
            urgent.append(notice)
        else:
            other.append(notice)

    urgent.sort(key=lambda n: (-n.priority_score, n.closing_date or "9999"))
    other.sort(key=lambda n: (-n.priority_score, n.closing_date or "9999"))
    return {"urgent": urgent, "other": other, "excluded": excluded}


def render(notices: list[Notice], stats: dict[str, Any], today: Optional[date] = None,
           duplicates_merged: int = 0) -> str:
    today = today or date.today()
    buckets = split_notices(notices)
    urgent, other, excluded = buckets["urgent"], buckets["other"], buckets["excluded"]
    relevant_count = len(urgent) + len(other)

    lines: list[str] = [
        "# Tender Radar — Philippines",
        "",
        f"Дата обновления: {today.isoformat()}",
        f"Период проверки: последние {stats.get('days', 7)} дн. "
        f"(запуск {datetime.now().strftime('%Y-%m-%d %H:%M')})",
        f"Источников проверено: {stats.get('sources_checked', 0)}"
        + (f", с ошибками: {stats.get('errors', 0)}" if stats.get("errors") else ""),
        f"Найдено новых объявлений: {stats.get('new_notices', 0)}",
        f"Обновлено объявлений: {stats.get('updated_notices', 0)}",
        f"Релевантных возможностей: {relevant_count}",
        f"Срочных: {len(urgent)}",
        "",
        "> Радар покрывает официальные публичные закупки и объявления ведомств.",
        "> Он не гарантирует полноту по частным и непубличным закупкам.",
        "",
        "## Срочно",
        "",
    ]

    if urgent:
        for notice in urgent:
            lines += _notice_block(notice)
    else:
        lines += ["Срочных релевантных объявлений нет.", ""]

    lines += ["## Другие релевантные объявления", ""]
    if other:
        for notice in other:
            lines += _notice_block(notice)
    else:
        lines += ["Нет.", ""]

    lines += ["## Не включено в основной список", ""]
    reasons = [
        ("закрытые / просроченные / уже присуждённые", excluded["closed"]),
        ("отменённые", excluded["cancelled"]),
        ("только для локальных поставщиков", excluded["local_only"]),
        ("без достаточной информации", excluded["thin"]),
        ("нерелевантные", excluded["irrelevant"]),
    ]
    total_excluded = sum(len(items) for _, items in reasons)
    for label, items in reasons:
        lines.append(f"- {label}: {len(items)}")
    lines.append(f"- дубликаты (объединены в одну запись): {duplicates_merged}")
    lines.append(f"- всего исключено: {total_excluded}")
    lines.append("")

    if stats.get("source_errors"):
        lines += ["## Источники, которые не ответили", ""]
        for source_id, message in stats["source_errors"].items():
            lines.append(f"- {source_id}: {message}")
        lines.append("")

    lines += [
        "---",
        "",
        "Проверка допуска обязательна: система не подтверждает право российской компании "
        "участвовать в конкретной закупке. Все ограничения уточняются у закупочной комиссии.",
        "",
    ]
    return "\n".join(lines)


def write_digest(markdown: str, base_dir: Path, today: Optional[date] = None,
                 dry_run: bool = False) -> dict[str, str]:
    """Пишет latest.md и архивную копию. В режиме dry-run ничего не пишет."""
    today = today or date.today()
    base_dir = Path(base_dir)
    latest = base_dir / "latest.md"
    archive = base_dir / "archive" / f"{today.isoformat()}.md"
    if dry_run:
        return {"latest": str(latest), "archive": str(archive), "written": "no (dry-run)"}
    base_dir.mkdir(parents=True, exist_ok=True)
    archive.parent.mkdir(parents=True, exist_ok=True)
    latest.write_text(markdown, "utf-8")
    archive.write_text(markdown, "utf-8")
    return {"latest": str(latest), "archive": str(archive), "written": "yes"}
