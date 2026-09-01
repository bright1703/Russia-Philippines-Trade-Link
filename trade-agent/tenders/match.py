"""
Сопоставление тендеров с профилями компаний Приморского края
и оценка релевантности по шкале 0–5.

Отдельно формируются:
  * причины оценки (match_reasons);
  * заметки о допуске иностранного поставщика (eligibility_notes) —
    система никогда не утверждает, что российская компания вправе
    участвовать, а перечисляет, что нужно проверить.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Optional

import yaml

from normalize import Notice, clean_ws

# --------------------------------------------------------------------------
# Профили
# --------------------------------------------------------------------------

def load_profiles(path: str | Path) -> list[dict[str, Any]]:
    data = yaml.safe_load(Path(path).read_text("utf-8")) or {}
    profiles = data.get("profiles") or []
    for profile in profiles:
        profile.setdefault("base_weight", 1.0)
        profile.setdefault("keywords", [])
        profile.setdefault("strong_keywords", [])
        profile.setdefault("exclude_keywords", [])
        profile.setdefault("company_types", [])
    return profiles


def _contains(haystack: str, needle: str) -> bool:
    """Поиск ключевого слова с учётом границ слов для коротких терминов."""
    if not haystack or not needle:
        return False
    if " " in needle or len(needle) > 6:
        return needle in haystack
    return re.search(rf"\b{re.escape(needle)}\b", haystack) is not None


# --------------------------------------------------------------------------
# Веса полей
# --------------------------------------------------------------------------

FIELD_WEIGHTS = {
    "title": 2.0,
    "category": 1.5,
    "description": 1.0,
    "agency": 0.7,
    "location": 0.3,
}
STRONG_BONUS = 1.5
MAX_DESCRIPTION_HITS = 3


def _profile_score(notice: Notice, profile: dict[str, Any]) -> tuple[float, list[str]]:
    fields = {
        "title": (notice.title or "").lower(),
        "category": (notice.category or "").lower(),
        "description": ((notice.description or "") + " " + (notice.raw_text or "")).lower(),
        "agency": (notice.agency or "").lower(),
        "location": (notice.location or "").lower(),
    }
    joined = " ".join(fields.values())

    for bad in profile.get("exclude_keywords", []):
        if _contains(joined, bad.lower()):
            return 0.0, [f"исключено словом «{bad}»"]

    total = 0.0
    hits: list[str] = []
    description_hits = 0
    strong = {k.lower() for k in profile.get("strong_keywords", [])}

    for keyword in profile.get("keywords", []):
        kw = keyword.lower()
        for field_name, text in fields.items():
            if not _contains(text, kw):
                continue
            if field_name == "description":
                if description_hits >= MAX_DESCRIPTION_HITS:
                    continue
                description_hits += 1
            weight = FIELD_WEIGHTS[field_name]
            if kw in strong:
                weight += STRONG_BONUS
            total += weight
            hits.append(f"«{keyword}» в поле {field_name}")
            break

    return total * float(profile.get("base_weight", 1.0)), hits


def _raw_to_score(raw: float) -> int:
    if raw >= 5.0:
        return 5
    if raw >= 3.2:
        return 4
    if raw >= 2.0:
        return 3
    if raw >= 1.0:
        return 2
    if raw > 0:
        return 1
    return 0


# --------------------------------------------------------------------------
# Допуск иностранного поставщика
# --------------------------------------------------------------------------

ELIGIBILITY_RULES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"philgeps\s+(?:registration|registered|platinum|membership)|registered\s+with\s+philgeps", re.I),
     "требуется регистрация на PhilGEPS (указана в объявлении)"),
    (re.compile(r"\bplatinum\s+membership\b", re.I),
     "требуется PhilGEPS Platinum Membership"),
    (re.compile(r"\b(?:tax identification number|\bTIN\b|BIR\s+registration)\b"),
     "упомянут филиппинский TIN / регистрация в BIR"),
    (re.compile(r"\b(?:mayor'?s permit|business permit|DTI\s+registration|SEC\s+registration|"
                r"certificate of registration)\b", re.I),
     "требуются местные разрешения (SEC/DTI, mayor's permit) — значит, нужно филиппинское юрлицо или партнёр"),
    (re.compile(r"(?:filipino citizens?|100%\s*filipino|at least seventy[- ]five percent|"
                r"\b75%\s*filipino|duly licensed filipino|domestic bidders only|"
                r"open only to (?:filipino|local))", re.I),
     "ОГРАНИЧЕНИЕ: заявлено участие только филиппинских поставщиков"),
    (re.compile(r"(?:foreign bidders?|foreign suppliers?|international competitive bidding|"
                r"foreign nationals may)", re.I),
     "иностранные участники упомянуты — условия допуска надо уточнить"),
    (re.compile(r"(?:joint venture|local partner|local agent|authorized distributor|"
                r"exclusive distributor)", re.I),
     "вероятно нужен локальный партнёр / дистрибьютор"),
    (re.compile(r"(?:notarized|apostille|consularized|sworn statement|omnibus sworn)", re.I),
     "документальные требования: нотариальное заверение / апостиль"),
    (re.compile(r"(?:english|filipino language|translated)", re.I),
     "языковые требования к документам"),
]

FOREIGN_RESTRICTED_MARK = "ОГРАНИЧЕНИЕ: заявлено участие только филиппинских поставщиков"
DEFAULT_ELIGIBILITY = "Eligibility: requires verification with the procuring entity"

STANDARD_CHECKLIST = [
    "допускаются ли иностранные поставщики",
    "нужна ли регистрация на PhilGEPS",
    "нужен ли филиппинский партнёр или юрлицо",
    "какие документы требуются (и в какой форме)",
    "кто контактное лицо в закупочной комиссии",
]


def assess_eligibility(notice: Notice) -> list[str]:
    text = notice.searchable_text()
    notes: list[str] = []
    for pattern, note in ELIGIBILITY_RULES:
        if pattern.search(text) and note not in notes:
            notes.append(note)
    notes.append(DEFAULT_ELIGIBILITY)
    return notes


# --------------------------------------------------------------------------
# Итоговая оценка
# --------------------------------------------------------------------------

DEADLINE_BONUS = {
    "urgent": 8.0,
    "closing_soon": 5.0,
    "open": 2.0,
    "deadline_unknown": 0.0,
    "closed": -25.0,
}


def score_notice(notice: Notice, profiles: list[dict[str, Any]]) -> Notice:
    """Заполняет matched_profiles, match_score, match_reasons, eligibility_notes,
    priority_score. Возвращает тот же объект."""
    per_profile: list[tuple[float, dict[str, Any], list[str]]] = []
    for profile in profiles:
        raw, hits = _profile_score(notice, profile)
        if raw > 0:
            per_profile.append((raw, profile, hits))

    per_profile.sort(key=lambda x: -x[0])
    reasons: list[str] = []
    matched: list[str] = []

    if per_profile:
        best_raw = per_profile[0][0]
        # Совпадение по нескольким направлениям слегка усиливает оценку.
        bonus = min(len(per_profile) - 1, 2) * 0.4
        score = _raw_to_score(best_raw + bonus)
        for raw, profile, hits in per_profile:
            matched.append(profile["name"])
            reasons.append(
                f"совпало направление {profile['id']} ({profile['name']}): " + ", ".join(hits[:4])
            )
    else:
        score = 0
        reasons.append("нет совпадений с профилями компаний")

    eligibility = assess_eligibility(notice)

    # Ограничения понижают оценку.
    if FOREIGN_RESTRICTED_MARK in eligibility:
        if score > 2:
            reasons.append("оценка понижена: тендер объявлен только для филиппинских поставщиков")
        score = min(score, 2)

    if notice.status == "cancelled":
        reasons.append("закупка отменена")
        score = 0
    elif notice.status == "awarded":
        reasons.append("контракт уже присуждён")
        score = min(score, 1)
    elif notice.deadline_status == "closed":
        reasons.append("дедлайн уже прошёл")
        score = min(score, 1)
    elif notice.deadline_status == "urgent" and score >= 3:
        reasons.append("дедлайн слишком близкий — на подготовку документов почти нет времени")

    if notice.deadline_status == "deadline_unknown":
        reasons.append("дедлайн не указан — требуется уточнение у закупочной комиссии")

    if not notice.description or len(notice.description) < 60:
        reasons.append("мало информации в объявлении — нужна проверка первоисточника")

    if notice.unconfirmed:
        reasons.append("источник неофициальный (ранний сигнал) — требуется подтверждение")

    notice.matched_profiles = matched
    notice.match_score = int(score)
    notice.match_reasons = reasons
    notice.eligibility_notes = eligibility
    notice.priority_score = round(
        score * 10.0
        + DEADLINE_BONUS.get(notice.deadline_status, 0.0)
        + float(notice.source_priority or 0),
        2,
    )
    return notice


def score_all(notices: list[Notice], profiles: list[dict[str, Any]]) -> list[Notice]:
    return [score_notice(n, profiles) for n in notices]


def company_types_for(profiles: list[dict[str, Any]], names: list[str]) -> list[str]:
    result: list[str] = []
    for profile in profiles:
        if profile["name"] in names:
            for company in profile.get("company_types", []):
                if company not in result:
                    result.append(company)
    return result


def explain(notice: Notice) -> str:
    """Короткое объяснение «почему это важно» для дайджеста."""
    if not notice.matched_profiles:
        return "Прямых совпадений с профилями нет."
    parts = [f"Закупка попадает в направление: {', '.join(notice.matched_profiles)}."]
    if notice.estimated_budget:
        parts.append(f"Заявленный бюджет — {notice.estimated_budget:,.0f} {notice.currency or ''}".strip() + ".")
    if notice.deadline_status in ("urgent", "closing_soon"):
        parts.append("Дедлайн близкий, решение нужно принимать быстро.")
    if FOREIGN_RESTRICTED_MARK in notice.eligibility_notes:
        parts.append("Внимание: в тексте есть признаки ограничения только для филиппинских поставщиков.")
    return " ".join(parts)
