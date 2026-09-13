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
    # Покрыт ли запрошенный период целиком. False означает, что источник
    # упёрся в технический лимит и часть периода не прочитана. Делать вид,
    # что весь период обработан, нельзя: в выпуске это разные состояния.
    complete: bool = True
    # Позиция, с которой продолжить в следующий раз (формат — дело адаптера).
    cursor: str = ""
    # Самая свежая публикация источника: по ней видно, молчит ли исправный
    # источник или просто давно ничего не публиковал.
    latest_published_at: str = ""


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
        # Сохранённая позиция предыдущего запуска. Сбор продолжается
        # от неё, а не от «последних суток».
        self.cursor = ""
        self.last_published_at = ""

    def resume_from(self, cursor: str, last_published_at: str = "") -> None:
        """Передаёт адаптеру позицию, на которой он остановился в прошлый раз."""
        self.cursor = str(cursor or "")
        self.last_published_at = str(last_published_at or "")

    @property
    def enabled(self) -> bool:
        return bool(self.config.get("enabled", True))

    def fetch(self, days: int = 7) -> SourceResult:  # pragma: no cover - интерфейс
        raise NotImplementedError
