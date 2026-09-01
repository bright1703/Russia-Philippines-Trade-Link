"""
Общая таксономия категорий, ключевых слов и HS-кодов.

Используется предфильтром Scout (дешёвый отсев шума до вызова модели)
и Opportunity Radar (сопоставление сигналов с профилями компаний).

HS-коды здесь — это ПОДСКАЗКИ на уровне товарной группы, а не готовая
классификация. Точный код всегда подтверждается человеком.
"""

from __future__ import annotations

import re
from typing import Iterable

from ..models import CATEGORIES

CATEGORY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "MEAT": ("meat", "pork", "beef", "poultry", "chicken", "offal", "carcass",
             "slaughter", "abattoir", "мясо", "свинина", "говядина", "птица",
             "халяль", "halal", "asf", "african swine fever", "foot and mouth"),
    "FOOD": ("food", "foodstuff", "dairy", "milk", "cheese", "confection",
             "canned", "processed food", "beverage", "seafood", "fish", "продукты",
             "молочн", "рыба", "консерв", "кондитер"),
    "AGRICULTURE": ("agriculture", "agricultural", "farm", "crop", "harvest",
                    "livestock", "aquaculture", "fisheries", "сельхоз", "аграр",
                    "урожай", "животноводств"),
    "FERTILIZER": ("fertilizer", "fertiliser", "urea", "ammonium", "potash",
                   "npk", "phosphate", "agrochemical", "удобрен", "карбамид",
                   "аммиачн", "селитра", "калий"),
    "GRAIN": ("grain", "wheat", "corn", "maize", "barley", "soybean", "rice",
              "flour", "milling", "зерно", "пшениц", "кукуруз", "соя", "мука", "ячмен"),
    "AUTO": ("vehicle", "automotive", "truck", "bus", "car ", "spare parts",
             "tire", "автомобил", "грузовик", "автобус", "запчаст", "шины"),
    "EQUIPMENT": ("machinery", "equipment", "generator", "pump", "engine",
                  "compressor", "boiler", "turbine", "industrial", "оборудован",
                  "станок", "генератор", "насос", "двигател"),
    "PACKAGING": ("packaging", "packing", "carton", "pallet", "container bag",
                  "label", "упаковк", "тара", "паллет"),
    "ENERGY": ("coal", "petroleum", "diesel", "lng", "lpg", "power plant",
               "electricity", "fuel", "energy", "уголь", "нефтепродукт",
               "дизель", "топлив", "энергет"),
    "LOGISTICS": ("logistics", "shipping", "freight", "cargo", "port", "terminal",
                  "vessel", "container", "cold chain", "warehouse", "customs",
                  "логистик", "перевозк", "порт", "контейнер", "склад", "таможн"),
    "REGULATION": ("regulation", "memorandum order", "administrative order",
                   "circular", "accreditation", "certificate", "sanitary",
                   "phytosanitary", "import permit", "ban", "quota", "tariff",
                   "регулирован", "разрешен", "сертифик", "запрет", "квота",
                   "пошлин", "аккредитац"),
    "TENDER": ("invitation to bid", "request for quotation", "procurement",
               "bidding", "philgeps", "bid notice", "тендер", "закупк", "аукцион"),
    "IMPORT": ("import", "importation", "importer", "trade volume", "exports to",
               "supplier country", "импорт", "поставк", "внешнеторг"),
}

# Подсказки HS на уровне групп (2–4 знака).
CATEGORY_HS_HINTS: dict[str, tuple[str, ...]] = {
    "MEAT": ("02", "0203", "0207", "0201", "0202", "0206", "1602"),
    "FOOD": ("03", "04", "16", "19", "20", "21", "0303", "0304"),
    "AGRICULTURE": ("06", "07", "08", "12", "23"),
    "FERTILIZER": ("31", "3102", "3104", "3105"),
    "GRAIN": ("10", "11", "1001", "1005", "1101", "1201"),
    "AUTO": ("87", "8703", "8704", "8708"),
    "EQUIPMENT": ("84", "85", "8408", "8413", "8502"),
    "PACKAGING": ("39", "48", "4819", "3923"),
    "ENERGY": ("27", "2701", "2710", "2711"),
    "LOGISTICS": (),
    "REGULATION": (),
    "TENDER": (),
    "IMPORT": (),
    "OTHER": (),
}

# Явный шум: такие материалы отбрасываются предфильтром.
NOISE_MARKERS = (
    "job vacancy", "hiring", "scholarship", "birthday", "condolence",
    "happy holidays", "webinar registration", "photo release",
    "basketball", "beauty pageant", "raffle",
)

_WORD_RE = re.compile(r"[^\w\s]", re.UNICODE)


def normalize(text: str) -> str:
    return _WORD_RE.sub(" ", (text or "").lower())


def guess_categories(text: str, limit: int = 3) -> list[tuple[str, int]]:
    """Возвращает [(категория, число совпадений)] по убыванию."""
    low = (text or "").lower()
    scores: list[tuple[str, int]] = []
    for category, keywords in CATEGORY_KEYWORDS.items():
        hits = sum(1 for kw in keywords if kw in low)
        if hits:
            scores.append((category, hits))
    scores.sort(key=lambda x: (-x[1], x[0]))
    return scores[:limit]


def hs_hints(categories: Iterable[str]) -> list[str]:
    hints: list[str] = []
    for category in categories:
        for code in CATEGORY_HS_HINTS.get(category, ()):
            if code not in hints:
                hints.append(code)
    return hints


def looks_like_noise(text: str) -> bool:
    low = (text or "").lower()
    return any(marker in low for marker in NOISE_MARKERS)


def valid_category(value: str) -> str:
    upper = (value or "").strip().upper()
    return upper if upper in CATEGORIES else "OTHER"
