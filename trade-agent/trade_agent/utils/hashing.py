"""Стабильные хэши для дедупликации и идемпотентной записи."""

from __future__ import annotations

import hashlib
import re
import unicodedata

_WS = re.compile(r"\s+")


def _norm(value: str) -> str:
    text = unicodedata.normalize("NFKC", (value or "")).lower()
    text = re.sub(r"https?://\S+", " ", text)      # ссылки часто содержат метки сессии
    text = re.sub(r"[^\w\s]", " ", text)
    return _WS.sub(" ", text).strip()


def content_hash(source: str, external_id: str = "", title: str = "", text: str = "") -> str:
    """
    Хэш содержимого. Если есть внешний идентификатор — он определяющий,
    иначе используется нормализованный текст (первые 2000 значимых символов).
    """
    if external_id:
        payload = f"{source}|id|{external_id}".lower()
    else:
        payload = f"{source}|txt|{_norm(title)}|{_norm(text)[:2000]}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def stable_id(*parts: str) -> str:
    payload = "|".join(_norm(p) for p in parts if p)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]
