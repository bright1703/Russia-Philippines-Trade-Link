"""Мелкие текстовые утилиты."""

from __future__ import annotations

import re

_WS = re.compile(r"[ \t]+")


def collapse(value: str) -> str:
    if not value:
        return ""
    text = str(value).replace("\r\n", "\n").replace("\r", "\n")
    text = _WS.sub(" ", text)
    text = "\n".join(line.strip() for line in text.split("\n"))
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def truncate(value: str, limit: int, suffix: str = "…") -> str:
    text = value or ""
    if len(text) <= limit:
        return text
    return text[: max(0, limit - len(suffix))] + suffix
