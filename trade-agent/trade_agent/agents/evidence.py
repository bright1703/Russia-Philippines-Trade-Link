"""
Доказательства по тексту исходного материала.

Модуль отвечает ровно на один вопрос: что действительно написано в материале.
Он ничего не знает про компании, отрасли и направления торговли — и поэтому
не может подтвердить сам себя. Всё, что построено на его выводах, обязано
показывать фрагмент текста, на котором вывод основан.

Три правила, из-за которых модуль появился:

1. Совпадение ищется по границам слова. Голая подстрока «порт» внутри
   «экспорт» и «транспорт» доказательством не является.
2. HS-код считается найденным в тексте, только если он там явно помечен
   как код («HS 0303», «ТН ВЭД 0303 14», «tariff heading 8408»). Годы,
   телефоны, суммы и склеенные цифры статьи кодами не становятся.
3. Общие слова («состав», «может», «экспорт», «бизнес») доказательством
   не являются ни при каких условиях.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Iterable, Optional

# Буквы, из которых состоит «слово» для целей поиска.
_WORD_CHARS = r"0-9A-Za-zА-Яа-я_"
_NON_WORD_RE = re.compile(f"[^{_WORD_CHARS}]")
_SPACES_RE = re.compile(r"\s+")
_SENTENCE_RE = re.compile(r"(?<=[.!?;\n])\s+")

# Русская словоформа: к основе допускается не больше трёх букв окончания.
# «мука» → «мукой» да; «мед» → «медицинский» нет (поэтому короткие
# термины сравниваются только целиком).
MAX_INFLECTION = 3
MIN_STEM_LENGTH = 4
# Окончания, которые отбрасываются перед поиском словоформ: без этого
# «мука» не находит «мукой», а «рыба» не находит «рыбы».
_TRAILING_VOWELS = "аяоеиыуюьй"
MIN_STEM_AFTER_CUT = 3

# Слова, которые никогда не являются самостоятельным доказательством.
# Половина ложных связей в архиве выросла именно из них.
STOP_TERMS = frozenset({
    "основной", "основная", "основные", "дополнительный", "дополнительные",
    "состав", "составе", "может", "могут", "бизнес", "экспорт", "импорт",
    "поставка", "поставки", "продукция", "продукты", "товар", "товары",
    "услуга", "услуги", "деятельность", "производство", "торговля",
    "компания", "компании", "предприятие", "рынок", "цена", "цены",
    "год", "года", "году", "новый", "новая", "россия", "российский",
    "филиппины", "manila", "russia", "russian", "philippines", "philippine",
    "business", "company", "product", "products", "export", "import",
    "market", "price", "prices", "trade", "new", "may", "can", "other",
})


def fold(text: str) -> str:
    """
    Нормализация с сохранением длины строки.

    Длина важна: по смещению найденного совпадения потом вырезается
    фрагмент из ОРИГИНАЛЬНОГО текста, который увидит человек.
    """
    lowered = (text or "").lower().replace("ё", "е")
    if len(lowered) != len(text or ""):
        # Редкие символы меняют длину при lower(). Тогда точное смещение
        # не гарантируется, и фрагмент берётся из нормализованного текста.
        lowered = re.sub(r"\s", " ", lowered)
    return _NON_WORD_RE.sub(" ", lowered)


def collapse(text: str) -> str:
    """Схлопывает пробелы — для отображения и сравнения коротких значений."""
    return _SPACES_RE.sub(" ", str(text or "")).strip()


def normalize(text: str) -> str:
    """Нормализованный текст без лишних пробелов (для простых проверок)."""
    return collapse(fold(text))


def is_stop_term(term: str) -> bool:
    words = normalize(term).split()
    return not words or all(word in STOP_TERMS for word in words)


@lru_cache(maxsize=8192)
def term_regex(term: str) -> Optional[re.Pattern[str]]:
    """
    Регулярное выражение для термина с учётом границ слова и словоформ.

    Слова длиной от четырёх букв ищутся как основа плюс не больше трёх
    букв окончания. Короткие слова — только целиком.
    """
    words = normalize(term).split()
    if not words:
        return None
    parts: list[str] = []
    for word in words:
        if len(word) >= MIN_STEM_LENGTH:
            stem = word
            if stem[-1] in _TRAILING_VOWELS and len(stem) - 1 >= MIN_STEM_AFTER_CUT:
                stem = stem[:-1]
            parts.append(f"{re.escape(stem)}[{_WORD_CHARS}]{{0,{MAX_INFLECTION}}}")
        else:
            parts.append(re.escape(word))
    body = r"\s+".join(parts)
    return re.compile(f"(?<![{_WORD_CHARS}]){body}(?![{_WORD_CHARS}])")


def fragment_at(text: str, start: int, end: int, width: int = 160) -> str:
    """Короткий фрагмент вокруг совпадения — то, что показывается человеку."""
    left = max(0, start - width // 2)
    right = min(len(text), end + width // 2)
    piece = collapse(text[left:right])
    if left > 0:
        piece = "…" + piece
    if right < len(text):
        piece = piece + "…"
    return piece


@dataclass
class TermHit:
    """Найденный в тексте термин и фрагмент, где он найден."""
    term: str
    fragment: str
    start: int = 0
    end: int = 0


def find_term(text: str, term: str) -> Optional[TermHit]:
    if is_stop_term(term):
        return None
    pattern = term_regex(term)
    if pattern is None:
        return None
    folded = fold(text)
    match = pattern.search(folded)
    if match is None:
        return None
    return TermHit(term=collapse(term), start=match.start(), end=match.end(),
                   fragment=fragment_at(text, match.start(), match.end()))


def find_terms(text: str, terms: Iterable[str], limit: int = 8) -> list[TermHit]:
    """Все найденные термины без повторов, в порядке появления в тексте."""
    folded = fold(text)
    hits: list[TermHit] = []
    seen: set[str] = set()
    for term in terms:
        normalized = normalize(term)
        if not normalized or normalized in seen or is_stop_term(term):
            continue
        pattern = term_regex(term)
        if pattern is None:
            continue
        match = pattern.search(folded)
        if match is None:
            continue
        seen.add(normalized)
        hits.append(TermHit(term=collapse(term), start=match.start(), end=match.end(),
                            fragment=fragment_at(text, match.start(), match.end())))
        if len(hits) >= limit:
            break
    hits.sort(key=lambda hit: hit.start)
    return hits


def sentences(text: str) -> list[str]:
    return [collapse(part) for part in _SENTENCE_RE.split(str(text or "")) if collapse(part)]


# --- HS-коды ---------------------------------------------------------------
# Код засчитывается только рядом с явным маркером классификации.
_HS_MARKER = (
    r"(?:hs\s*(?:code|codes|heading)?|hts|ahtn|тн\s*вэд|тнвэд|"
    r"тарифн\w*\s+(?:позици\w+|лини\w+|код\w*)|товарн\w*\s+позици\w+|"
    r"код\w*\s+тн\s*вэд|tariff\s+(?:heading|line|code|item)|"
    r"customs\s+code|commodity\s+code)"
)
_HS_CODE = r"(\d{2,4}(?:[.\s]?\d{2,4}){0,3})"
_HS_AFTER_RE = re.compile(_HS_MARKER + r"[\s:№#-]*" + _HS_CODE, re.IGNORECASE)
_HS_BEFORE_RE = re.compile(_HS_CODE + r"\s*[-–—,]?\s*" + _HS_MARKER, re.IGNORECASE)


def digits_only(value: str) -> str:
    return re.sub(r"\D", "", str(value or ""))


@dataclass
class HsHit:
    code: str
    fragment: str


def declared_hs_codes(text: str, limit: int = 8) -> list[HsHit]:
    """
    HS-коды, ЯВНО помеченные в тексте как коды.

    Всё остальное — годы, объёмы, номера телефонов, номера документов —
    кодами не считается. Цифры разных чисел статьи никогда не склеиваются.
    """
    source = str(text or "")
    found: list[HsHit] = []
    seen: set[str] = set()
    for pattern in (_HS_AFTER_RE, _HS_BEFORE_RE):
        for match in pattern.finditer(source):
            code = digits_only(match.group(1))
            if len(code) < 2 or len(code) > 12 or code in seen:
                continue
            seen.add(code)
            found.append(HsHit(code=code,
                               fragment=fragment_at(source, match.start(), match.end())))
            if len(found) >= limit:
                return found
    return found


def hs_relation(source_code: str, company_code: str) -> Optional[str]:
    """
    Отношение кода из материала к коду из профиля компании.

    Возвращает 'exact', 'group4', 'group2' или None. Национальный
    10-значный код РФ не приравнивается к филиппинскому: совпадением
    считается общее начало на уровне товарной группы, и это прямо
    написано в объяснении связи.
    """
    left = digits_only(source_code)
    right = digits_only(company_code)
    if len(left) < 2 or len(right) < 2:
        return None
    if left == right:
        return "exact"
    if len(left) >= 4 and len(right) >= 4 and left[:4] == right[:4]:
        return "group4"
    if left[:2] == right[:2]:
        return "group2"
    return None


@dataclass
class Evidence:
    """Набор доказательств одного вывода."""
    terms: list[TermHit] = field(default_factory=list)
    hs: list[HsHit] = field(default_factory=list)
    fragments: list[str] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not (self.terms or self.hs or self.fragments)

    def all_fragments(self, limit: int = 5) -> list[str]:
        result: list[str] = []
        for value in [*(hit.fragment for hit in self.terms),
                      *(hit.fragment for hit in self.hs),
                      *self.fragments]:
            if value and value not in result:
                result.append(value)
            if len(result) >= limit:
                break
        return result
