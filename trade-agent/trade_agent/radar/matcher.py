"""
Opportunity Radar.

Логика: сигнал → категории → товары → HS-коды → профили компаний → совпадения.

Ключевая идея: возможность должна находиться даже тогда, когда сигнал
изначально не был связан с компанией напрямую. Поэтому сопоставление идёт
не только по названию компании, но и по товарным группам, HS-подсказкам
категории и ключевым словам продукции.

Модуль детерминированный: LLM здесь не нужен.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, Optional

from ..agents import taxonomy
from ..models import Company, Match, RawItem, Signal
from ..utils import truncate

# Вклад каждого признака в сырой балл.
W_COMPANY_NAME = 4.0
W_HS_EXACT = 3.0
W_HS_GROUP = 1.5
W_CATEGORY = 2.0
W_PRODUCT = 1.5
W_HS_HINT = 1.0
W_TENDER_BONUS = 1.0

RECOMMENDED_ACTIONS = {
    "TENDER": "проверить допуск иностранного поставщика и сроки, затем решить об участии",
    "REGULATION": "проверить, затрагивает ли требование продукцию компании, и запросить разъяснение у регулятора",
    "IMPORT": "сверить объёмы и страны-поставщики с возможностями компании",
    "MEAT": "проверить статус аккредитации предприятия и требования BAI/NMIS",
    "FOOD": "уточнить требования FDA/BAI по конкретной товарной позиции",
    "FERTILIZER": "проверить требования регистрации продукта в FPA",
    "GRAIN": "оценить логистику и требования по фитосанитарии",
    "LOGISTICS": "оценить влияние на маршрут и стоимость доставки",
    "ENERGY": "проверить применимость к номенклатуре компании",
    "EQUIPMENT": "проверить наличие локального партнёра и сервисных требований",
}
DEFAULT_ACTION = "передать профильному менеджеру и уточнить детали у источника"


def _norm_hs(code: str) -> str:
    return re.sub(r"[^\d]", "", str(code or ""))


def _hs_relation(signal_codes: Iterable[str], company_codes: Iterable[str]) -> tuple[float, list[str]]:
    """Точное совпадение кода весомее совпадения товарной группы."""
    score = 0.0
    reasons: list[str] = []
    company_norm = [(_norm_hs(c), c) for c in company_codes if _norm_hs(c)]
    for raw_code in signal_codes:
        code = _norm_hs(raw_code)
        if not code:
            continue
        for norm, original in company_norm:
            if code == norm:
                score += W_HS_EXACT
                reasons.append(f"совпал HS-код {original}")
                break
            if len(code) >= 2 and len(norm) >= 2 and code[:2] == norm[:2]:
                score += W_HS_GROUP
                reasons.append(f"совпала товарная группа HS {norm[:2]} (код компании {original})")
                break
    return score, reasons


def _raw_to_score(raw: float) -> int:
    if raw >= 7.0:
        return 5
    if raw >= 5.0:
        return 4
    if raw >= 3.0:
        return 3
    if raw >= 1.5:
        return 2
    if raw > 0:
        return 1
    return 0


@dataclass
class MatchDetail:
    company_slug: str
    score: int
    raw_score: float
    reasons: list[str]


def match_signal(signal: Signal, item: Optional[RawItem], company: Company) -> MatchDetail:
    """Сопоставляет один сигнал с одним профилем компании."""
    text = " ".join(filter(None, [
        signal.reason, signal.category, " ".join(signal.companies_matched),
        item.title if item else "", item.raw_text if item else "",
    ])).lower()

    raw = 0.0
    reasons: list[str] = []

    if company.name and company.name.lower() in text:
        raw += W_COMPANY_NAME
        reasons.append(f"в тексте прямо упомянута компания «{company.name}»")
    for mentioned in signal.companies_matched:
        if mentioned and company.name and mentioned.lower() in company.name.lower():
            raw += W_COMPANY_NAME / 2
            reasons.append(f"Scout указал связь с «{mentioned}»")
            break

    hs_score, hs_reasons = _hs_relation(signal.hs_codes, company.hs_codes)
    raw += hs_score
    reasons += hs_reasons

    if signal.category and signal.category in company.categories:
        raw += W_CATEGORY
        reasons.append(f"категория сигнала {signal.category} входит в профиль компании")

    product_hits = 0
    product_values = list(dict.fromkeys([*company.product_aliases, *company.products]))
    for product in product_values:
        token = str(product).strip().lower()
        if len(token) >= 4 and token in text:
            product_hits += 1
            reasons.append(f"в тексте упомянут товар компании «{product}»")
            if product_hits >= 3:
                break
    raw += product_hits * W_PRODUCT

    if signal.matched_products:
        matched_text = " ".join(signal.matched_products).lower()
        if any(alias.lower() in matched_text for alias in company.product_aliases):
            raw += W_PRODUCT
            reasons.append("товар совпал с обязательным триггером каталога")

    # Косвенная связь: подсказки HS по категории сигнала.
    if not hs_score:
        hint_score, hint_reasons = _hs_relation(taxonomy.hs_hints([signal.category]), company.hs_codes)
        if hint_score:
            raw += min(hint_score, W_HS_HINT * 2)
            reasons.append("косвенная связь: " + hint_reasons[0])

    if signal.category == "TENDER" and (company.categories or company.hs_codes) and raw > 0:
        raw += W_TENDER_BONUS
        reasons.append("это открытая закупка — возможность прямого участия или поставки партнёру")

    if company.restrictions:
        reasons.append("в профиле указаны ограничения: " + "; ".join(company.restrictions[:3]))

    return MatchDetail(company.slug, _raw_to_score(raw), raw, reasons)


class OpportunityRadar:
    def __init__(self, settings: Any):
        self.min_score = int(getattr(settings, "radar_min_match_score", 2))

    def match_all(self, signal: Signal, item: Optional[RawItem],
                  companies: list[Company]) -> list[Match]:
        matches: list[Match] = []
        for company in companies:
            detail = match_signal(signal, item, company)
            if detail.score < self.min_score:
                continue
            matches.append(Match(
                company_slug=company.slug,
                signal_id=int(signal.id or 0),
                match_score=detail.score,
                reason=truncate("; ".join(detail.reasons), 800),
                recommended_action=RECOMMENDED_ACTIONS.get(signal.category, DEFAULT_ACTION),
            ))
        matches.sort(key=lambda m: -m.match_score)
        return matches
