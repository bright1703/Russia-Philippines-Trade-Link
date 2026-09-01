"""Общий интерфейс источника данных."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from ..models import RawItem


@dataclass
class SourceResult:
    source_id: str
    items: list[RawItem] = field(default_factory=list)
    error: str = ""
    fetched_pages: int = 0
    retries: int = 0


class SourceAdapter:
    """
    Базовый класс источника.

    Новый источник = новый подкласс + регистрация в sources/__init__.py.
    Основной конвейер при этом не меняется.
    """

    source_id = "base"
    source_type = "web"

    def __init__(self, config: dict[str, Any], settings: Any):
        self.config = config or {}
        self.settings = settings
        self.source_id = self.config.get("id") or self.source_id
        self.log = logging.getLogger(f"trade_agent.source.{self.source_id}")

    @property
    def enabled(self) -> bool:
        return bool(self.config.get("enabled", True))

    def fetch(self, days: int = 7) -> SourceResult:  # pragma: no cover - интерфейс
        raise NotImplementedError
