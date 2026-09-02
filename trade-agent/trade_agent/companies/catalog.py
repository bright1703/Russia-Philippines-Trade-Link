"""Нормализация каталога экспортеров в записи для SQLite.

Исходный Excel не нужен приложению во время работы. Этот модуль превращает
его строки в компактный JSON/CSV-совместимый формат и сохраняет источник,
строку и предупреждения о неполных данных.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

from ..agents import taxonomy
from .loader import slugify

SOURCE_NAME = "Каталог экспортеров Приморского края"

_CODE_RE = re.compile(r"(?<!\d)(\d{4})\s+(\d{4,6})(?!\d)")
_DIGITS_RE = re.compile(r"(?<!\d)\d{4,10}(?!\d)")

# Минимальный словарь нужен для новостей на английском языке. Он не заменяет
# проверку HS-кода и не утверждает юридическую классификацию товара.
TERM_ALIASES: dict[str, tuple[str, ...]] = {
    "судов": ("судовые двигатели", "marine engine", "marine engines",
                "ship engine", "ship engines", "marine propulsion"),
    "двигател": ("engine", "engines", "motor", "motors"),
    "мяс": ("meat", "pork", "beef", "poultry", "chicken", "offal"),
    "рыб": ("fish", "seafood", "fishery", "fisheries"),
    "морепродукт": ("seafood", "marine products"),
    "удобрен": ("fertilizer", "fertiliser", "agrochemical"),
    "зерн": ("grain", "wheat", "corn", "maize"),
    "мед": ("honey",),
    "напит": ("beverage", "drinks"),
    "упаков": ("packaging", "packing"),
    "космет": ("cosmetics", "cosmetic products"),
}

GENERIC_PRODUCT_WORDS = {
    "основной", "дополнительные", "дополнительный", "вид", "виды",
    "деятельность", "деятельности", "производство", "торговля", "оптовая",
    "прочие", "прочая", "прочий", "иные", "услуг", "услуги", "продукты",
    "продукция", "материалов", "изделий", "включенные", "включенная",
    "поименованные", "другом", "месте", "наук", "все", "вся", "оквэд",
}


def _text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").replace("\xa0", " ")).strip()


def _parts(value: Any) -> list[str]:
    text = str(value or "").replace("\xa0", " ")
    result: list[str] = []
    for raw in re.split(r"[\r\n;]+", text):
        part = re.sub(r"^\s*[-•*]\s*", "", raw)
        part = _text(part)
        if part and part not in result:
            result.append(part)
    return result


def _countries(value: Any) -> list[str]:
    result: list[str] = []
    for raw in re.split(r"[\r\n;,]+", str(value or "")):
        part = _text(raw)
        if part and part not in result:
            result.append(part)
    return result


def _hs_codes(value: Any) -> list[str]:
    text = str(value or "").replace("\xa0", " ")
    result: list[str] = []
    combined_parts: set[str] = set()
    for first, second in _CODE_RE.findall(text):
        code = first + second
        if code not in result:
            result.append(code)
        combined_parts.update((first, second))
    for code in _DIGITS_RE.findall(text):
        if code in combined_parts:
            continue
        if code not in result:
            result.append(code)
    return result


def _products(product_value: Any, hs_value: Any) -> list[str]:
    products = _parts(product_value)
    if products:
        return products
    # Если отдельная колонка пуста, берем названия после HS-кодов, но не сам код.
    for part in _parts(hs_value):
        cleaned = re.sub(r"^\s*[\d\s.-]{4,}\s*[-:]?\s*", "", part).strip()
        if len(cleaned) >= 3 and cleaned not in products:
            products.append(cleaned)
    return products


def _aliases(products: Iterable[str]) -> list[str]:
    aliases: list[str] = []
    for product in products:
        value = _text(product)
        if len(value) >= 4 and value.lower() not in aliases:
            aliases.append(value.lower())
        words = re.findall(r"[\w-]{4,}", value.lower(), re.UNICODE)
        for word in words:
            if word in GENERIC_PRODUCT_WORDS or word.startswith("http"):
                continue
            if word not in aliases:
                aliases.append(word)
        low = value.lower()
        for marker, extra in TERM_ALIASES.items():
            if marker in low:
                for alias in extra:
                    if alias not in aliases:
                        aliases.append(alias)
    return aliases[:80]


def normalize_catalog_row(row: dict[str, Any], source_row: int,
                          source_name: str = SOURCE_NAME) -> dict[str, Any] | None:
    name = _text(row.get("Название компании") or row.get("name") or row.get("Название"))
    if not name:
        return None
    description = _text(row.get("Описание компании") or row.get("description"))
    hs_value = row.get("Перечень товаров/услуг с кодами ТН ВЭД") or row.get("hs_codes") or ""
    products = _products(row.get("Продукция") or row.get("products"), hs_value)
    hs_codes = _hs_codes(hs_value)
    countries = _countries(row.get("Страны экспорта") or row.get("export_countries"))
    industry = _text(row.get("Отрасль экспорта") or row.get("industry"))
    classification_text = " ".join([industry, *products])
    categories = [category for category, _ in taxonomy.guess_categories(classification_text)]
    if not categories:
        categories = ["OTHER"]
    quality: list[str] = []
    if not description:
        quality.append("нет описания")
    if not products:
        quality.append("нет продукции")
    if not hs_codes:
        quality.append("нет кодов ТН ВЭД")
    if not countries:
        quality.append("нет стран экспорта")
    if not _text(row.get("Сайт") or row.get("website")):
        quality.append("нет сайта")
    return {
        "name": name,
        "slug": slugify(name),
        "website": _text(row.get("Сайт") or row.get("website")),
        "description": description,
        "history": description,
        "inn": _text(row.get("ИНН") or row.get("inn")),
        "products": products,
        "product_aliases": _aliases(products),
        "hs_codes": hs_codes,
        "categories": categories,
        "export_countries": countries,
        "export_experience": ", ".join(countries),
        "industry": industry,
        "contact_name": _text(row.get("ФИО руководителя")),
        "address": _text(row.get("Юридический и фактический адрес")),
        "contacts": _text(row.get("Контакты")),
        "source_name": source_name,
        "source_row": source_row,
        "data_quality": quality,
        "status": "catalog_imported",
        "region": "Приморский край",
    }


def normalize_catalog(rows: Iterable[dict[str, Any]],
                      source_name: str = SOURCE_NAME) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    # В исходной таблице первая строка данных — Excel-строка 3.
    for index, row in enumerate(rows, start=3):
        company = normalize_catalog_row(row, index, source_name)
        if company:
            result.append(company)
    return result
