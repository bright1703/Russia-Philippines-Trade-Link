"""
Адаптер локальных файлов-фикстур.

Нужен для офлайн-прогонов и тестов: сеть не используется,
разбор идёт тем же кодом, что и для настоящих страниц.
"""

from __future__ import annotations

from pathlib import Path

from bs4 import BeautifulSoup

from .base import FetchResult
from .generic_html import GenericHtmlAdapter


class FixtureAdapter(GenericHtmlAdapter):
    adapter_id = "fixture"

    def fetch(self, days: int = 7) -> FetchResult:
        result = FetchResult(source_id=self.source_id)
        path = Path(self.config.get("fixture_path") or "")
        if not path.exists():
            result.error = f"файл фикстуры не найден: {path}"
            return result
        soup = BeautifulSoup(path.read_text("utf-8", errors="replace"), "html.parser")
        result.pages_fetched = 1
        base = self.config.get("url") or f"file://{path}"
        limit = int(self.config.get("max_items_per_source") or 60)
        result.items = self._candidates(self._container(soup), base)[:limit]
        return result
