"""Детерминированные обязательные уведомления по профилям компаний."""

from __future__ import annotations

import re
from typing import Iterable, Optional

from .models import Company, RawItem, Signal, SIGNAL_NEW

POLICY_MARKERS = (
    "tariff", "duty", "duties", "customs duty", "export tax", "import tax",
    "пошлин", "тариф", "налог", "таможенн", "quota", "квот", "ban",
    "запрет", "restriction", "ограничен", "permit", "разрешен",
    "accreditation", "аккредитац", "certificate", "сертифик",
    "registration", "регистрац", "standards", "стандарт",
)

CHANGE_MARKERS = (
    "remove", "removed", "removal", "waive", "waived", "waiver", "zero",
    "reduce", "reduced", "cut", "exempt", "exemption", "abolish",
    "отмен", "сня", "нулев", "сниз", "уменьш", "освобод", "ввел",
    "вступил", "изменил", "разрешил", "запретил", "расширил",
)


def _norm(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").lower().replace("ё", "е")).strip()


def _digits(value: str) -> str:
    return re.sub(r"[^0-9]", "", str(value or ""))


def _terms(company: Company) -> list[str]:
    result: list[str] = []
    for value in [*company.product_aliases, *company.products]:
        term = _norm(value)
        if len(term) >= 5 and term not in result:
            result.append(term)
    return result


def _hit(company: Company, text: str) -> list[str]:
    hits = [term for term in _terms(company) if term in text]
    if hits:
        return hits[:8]
    text_digits = _digits(text)
    for code in company.hs_codes:
        normalized = _digits(code)
        if len(normalized) >= 4 and normalized in text_digits:
            return [f"HS {code}"]
    return []


def detect_mandatory_policy_alert(item: RawItem,
                                  companies: Iterable[Company]) -> Optional[Signal]:
    """Возвращает сигнал, если новость затрагивает товар компании."""
    text = _norm(f"{item.title}\n{item.raw_text}")
    if not any(marker in text for marker in POLICY_MARKERS):
        return None
    if not any(marker in text for marker in CHANGE_MARKERS):
        return None

    names: list[str] = []
    products: list[str] = []
    codes: list[str] = []
    reasons: list[str] = []
    for company in companies:
        hits = _hit(company, text)
        if not hits:
            continue
        names.append(company.name)
        products.extend(hits)
        codes.extend(company.hs_codes[:8])
        reasons.append(f"{company.name}: {', '.join(hits[:3])}")

    if not names:
        return None
    return Signal(
        raw_item_id=int(item.id or 0), category="REGULATION", relevance_score=5,
        reason=("ОБЯЗАТЕЛЬНЫЙ ТОВАРНЫЙ ТРИГГЕР. В новости найдено "
                "регуляторное изменение и совпадение с профилем компании: "
                + "; ".join(reasons)),
        companies_matched=list(dict.fromkeys(names))[:20],
        matched_products=list(dict.fromkeys(products))[:20],
        hs_codes=list(dict.fromkeys(codes))[:20], geography="Philippines",
        needs_deep_analysis=True, must_alert=True, status=SIGNAL_NEW,
    )
