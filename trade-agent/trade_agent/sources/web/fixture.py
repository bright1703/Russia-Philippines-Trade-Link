"""Веб-адаптер локальных файлов — для офлайн-тестов и отладки."""

from __future__ import annotations

from pathlib import Path

from bs4 import BeautifulSoup

from ..base import SourceResult
from .generic import GenericWebAdapter


class FixtureWebAdapter(GenericWebAdapter):
    adapter_id = "fixture"

    def fetch(self, days: int = 7) -> SourceResult:
        result = SourceResult(source_id=self.source_id)
        path = Path(self.config.get("fixture_path") or "")
        if not path.exists():
            result.error = f"файл фикстуры не найден: {path}"
            return result
        soup = BeautifulSoup(path.read_text("utf-8", errors="replace"), "html.parser")
        result.fetched_pages = 1
        base = self.config.get("url") or f"file://{path}"
        container = self._container(soup)
        limit = int(self.config.get("max_items", 40))
        cutoff = self._cutoff(days)
        seen: set[str] = set()
        for anchor in container.find_all("a", href=True):
            title = anchor.get_text(" ", strip=True)
            if len(title) < 15:
                continue
            from urllib.parse import urljoin
            url = urljoin(base, anchor["href"])
            if url in seen:
                continue
            seen.add(url)
            block = self._block(anchor)
            text = block.get_text(" ", strip=True) if block else title
            published = self._date(text)
            if not self._keep_by_date(published, cutoff):
                continue
            result.items.append(self._make_item(title, url, text, published))
            if len(result.items) >= limit:
                break
        return result
