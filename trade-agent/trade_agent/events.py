"""
Устойчивый идентификатор события.

Один факт, перепечатанный двумя каналами, должен стать одной карточкой
с несколькими источниками. При этом два разных изменения по одному товару
объединять нельзя — похожий заголовок сам по себе не делает события одним.

Ключ строится детерминированно из трёх частей:

  * значимые слова заголовка (без служебных слов и без эмодзи);
  * дата события или публикации с точностью до дня;
  * основная отрасль.

Если у материала есть ссылка на первичный документ, она сильнее заголовка:
две перепечатки одного приказа склеиваются, даже если заголовки разные.
"""

from __future__ import annotations

import hashlib
import re
from typing import Optional
from urllib.parse import urlsplit, urlunsplit

from .agents import evidence
from .models import RawItem, Signal

# Служебные слова заголовка, которые не различают события.
_TITLE_STOPWORDS = frozenset({
    "новости", "новость", "сегодня", "срочно", "важно", "фото", "видео",
    "читайте", "подробности", "news", "update", "breaking", "read", "more",
    "в", "на", "по", "за", "из", "для", "и", "или", "the", "a", "an", "of",
    "in", "on", "for", "to", "and", "or", "at", "from", "with",
})
_TITLE_WORDS = 12


def _significant_words(title: str) -> list[str]:
    words = [word for word in evidence.normalize(title).split()
             if len(word) >= 3 and word not in _TITLE_STOPWORDS]
    # Порядок слов в перепечатке может измениться, набор — нет.
    return sorted(dict.fromkeys(words))[:_TITLE_WORDS]


def _day(value: str) -> str:
    match = re.search(r"(\d{4})-(\d{2})-(\d{2})", str(value or ""))
    return match.group(0) if match else ""


def canonical_url(url: str) -> str:
    """URL без параметров отслеживания: два перепоста дают одну ссылку."""
    raw = str(url or "").strip()
    if not raw:
        return ""
    try:
        parts = urlsplit(raw)
    except ValueError:
        return raw
    host = parts.netloc.lower().removeprefix("www.")
    path = parts.path.rstrip("/")
    return urlunsplit((parts.scheme.lower(), host, path, "", ""))


def event_key(item: RawItem, signal: Optional[Signal] = None) -> str:
    """
    Ключ события. Одинаковый ключ означает «это тот же факт».

    Пустая строка не возвращается никогда: у любого материала есть хотя бы
    собственный хэш, и тогда событие считается отдельным.
    """
    meta = item.meta or {}
    primary = canonical_url(str(meta.get("primary_source_url") or ""))
    if primary:
        return "doc:" + hashlib.sha1(primary.encode("utf-8")).hexdigest()[:20]

    # Номер документа или закупки — тоже надёжнее заголовка.
    reference = str(meta.get("document_no") or meta.get("reference_no") or "").strip()
    if reference:
        return "ref:" + hashlib.sha1(
            evidence.normalize(reference).encode("utf-8")).hexdigest()[:20]

    words = _significant_words(item.title)
    if not words:
        return "raw:" + (item.hash or str(item.id or ""))[:20]

    day = ""
    sector = ""
    if signal is not None:
        day = _day(signal.event_date) or _day(signal.effective_from)
        sector = (signal.sectors or [""])[0]
    day = day or _day(item.published_at)

    payload = "|".join([" ".join(words), day, sector])
    return "ttl:" + hashlib.sha1(payload.encode("utf-8")).hexdigest()[:20]


def same_event(left: RawItem, right: RawItem,
               left_signal: Optional[Signal] = None,
               right_signal: Optional[Signal] = None) -> bool:
    return event_key(left, left_signal) == event_key(right, right_signal)


# Человекочитаемые названия полей отпечатка: по ним в выпуске пишется,
# что именно изменилось с прошлой публикации события.
FINGERPRINT_LABELS = {
    "event_type": "тип события",
    "trade_direction": "направление торговли",
    "jurisdiction": "юрисдикция",
    "effective_from": "дата вступления в силу",
    "deadline": "срок",
    "hs_codes": "коды товара",
    "tender_status": "статус закупки",
}


def fingerprint_fields(signal: Signal, item: RawItem) -> dict[str, str]:
    """
    Поля, по которым событие считается изменившимся.

    Оценка значимости сюда НЕ входит: важность события — не его
    обновление. Иначе любая важная новость выходила бы в каждом выпуске
    как «существенно обновлённая».
    """
    return {
        "event_type": signal.event_type,
        "trade_direction": signal.trade_direction,
        "jurisdiction": signal.jurisdiction,
        "effective_from": _day(signal.effective_from),
        "deadline": _day(signal.deadline),
        "hs_codes": ",".join(sorted(signal.hs_codes)),
        "tender_status": str((item.meta or {}).get("status") or ""),
    }


def changed_fields(previous: dict[str, str],
                   current: dict[str, str]) -> list[str]:
    """Что изменилось относительно прошлой публикации, словами."""
    changes: list[str] = []
    for key, label in FINGERPRINT_LABELS.items():
        was = str((previous or {}).get(key) or "")
        now = str((current or {}).get(key) or "")
        if was == now:
            continue
        if was and now:
            changes.append(f"{label}: было «{was}», стало «{now}»")
        elif now:
            changes.append(f"{label}: появилось «{now}»")
        else:
            changes.append(f"{label}: больше не указано (было «{was}»)")
    return changes


def update_fingerprint(signal: Signal, item: RawItem) -> str:
    """
    Отпечаток содержания события.

    Повторное событие публикуется только при существенном обновлении:
    сменились предмет правила, дата вступления в силу, срок или статус.
    Косметическая правка текста отпечаток не меняет.
    """
    fields = fingerprint_fields(signal, item)
    payload = "|".join(f"{key}={fields[key]}" for key in sorted(fields))
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]
