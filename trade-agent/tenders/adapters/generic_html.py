"""
Универсальный адаптер официальных HTML-страниц ведомств.

Работает по CSS-селекторам из sources.yml, а если они не заданы или
не сработали — переходит на эвристику: ищет ссылки, текст или адрес
которых похож на закупочное объявление.
"""

from __future__ import annotations

from typing import Any, Optional
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from .base import BaseAdapter, FetchResult, SourceError

DOC_EXTENSIONS = (".pdf", ".doc", ".docx", ".xls", ".xlsx", ".zip")
SKIP_PATTERNS = (
    "javascript:", "mailto:", "tel:", "#",
    "/wp-login", "/feed", "facebook.com", "twitter.com", "youtube.com",
)


class GenericHtmlAdapter(BaseAdapter):
    adapter_id = "generic_html"

    def fetch(self, days: int = 7) -> FetchResult:
        url = self.config.get("url") or ""
        result = FetchResult(source_id=self.source_id)
        if not url:
            result.error = "не задан URL источника"
            return result

        response = self.client.get(url)
        result.pages_fetched += 1
        soup = BeautifulSoup(response.text, "html.parser")
        container = self._container(soup)
        candidates = self._candidates(container, url)

        limit = int(self.config.get("max_items_per_source") or 60)
        follow = bool(self.config.get("follow_detail"))
        max_details = int(self.config.get("max_detail_pages") or 20)
        followed = 0

        for cand in candidates[:limit]:
            item = dict(cand)
            if follow and followed < max_details and self._is_html_page(item["url"], url):
                try:
                    detail = self.client.get(item["url"])
                    followed += 1
                    result.pages_fetched += 1
                    detail_soup = BeautifulSoup(detail.text, "html.parser")
                    body = self._container(detail_soup)
                    item["html"] = str(body)
                    item["detail_fetched"] = True
                except SourceError as exc:
                    self.log.warning("не удалось открыть карточку %s: %s", item["url"], exc)
            result.items.append(item)
        return result

    # ------------------------------------------------------------------
    def _container(self, soup: BeautifulSoup):
        selectors = (self.config.get("selectors") or {}).get("container")
        if selectors:
            for sel in [s.strip() for s in selectors.split(",") if s.strip()]:
                node = soup.select_one(sel)
                if node is not None and len(node.get_text(strip=True)) > 80:
                    return node
        return soup.body or soup

    def _candidates(self, container, base_url: str) -> list[dict[str, Any]]:
        seen: set[str] = set()
        items: list[dict[str, Any]] = []
        for anchor in container.find_all("a", href=True):
            href = anchor["href"].strip()
            low_href = href.lower()
            if not href or any(p in low_href for p in SKIP_PATTERNS):
                continue
            text = " ".join(anchor.get_text(" ", strip=True).split())
            absolute = urljoin(base_url, href)
            is_doc = low_href.split("?")[0].endswith(DOC_EXTENSIONS)
            if not (self.looks_like_procurement(text) or self.looks_like_procurement(href) or is_doc):
                continue
            if len(text) < 8 and not is_doc:
                continue
            if absolute in seen:
                continue
            seen.add(absolute)

            block = self._block_of(anchor)
            item: dict[str, Any] = {
                "title": text or self._filename(absolute),
                "url": absolute,
                "html": str(block),
                "text": block.get_text(" ", strip=True) if block else text,
                "agency": self.config.get("default_agency", ""),
                "category": self.config.get("default_category", ""),
                "language": self.config.get("language", "en"),
                "attachment_urls": [absolute] if is_doc else [],
            }
            items.append(item)
        return items

    @staticmethod
    def _block_of(anchor):
        """
        Ближайший контейнер вокруг ссылки, который содержит не только сам
        заголовок: даты, бюджет и контакты часто лежат в соседней ячейке.
        """
        anchor_len = len(anchor.get_text(" ", strip=True))
        node = anchor
        fallback = anchor.parent or anchor
        for _ in range(5):
            parent = node.parent
            if parent is None:
                break
            node = parent
            if node.name in ("li", "tr", "article", "div", "td", "p", "section"):
                text_len = len(node.get_text(" ", strip=True))
                if text_len - anchor_len >= 40:
                    return node
                fallback = node
        return fallback

    @staticmethod
    def _filename(url: str) -> str:
        return urlsplit(url).path.rsplit("/", 1)[-1] or url

    @staticmethod
    def _is_html_page(url: str, base_url: str) -> bool:
        path = urlsplit(url).path.lower()
        if path.endswith(DOC_EXTENSIONS):
            return False
        return urlsplit(url).netloc.lower() == urlsplit(base_url).netloc.lower()
