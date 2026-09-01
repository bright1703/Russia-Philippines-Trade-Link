"""
Универсальные веб-адаптеры.

GenericWebAdapter — список новостей/пресс-релизов на HTML-странице.
RssAdapter       — RSS/Atom-лента.

Для нового ведомства обычно достаточно записи в web_sources.yml.
Если сайт нестандартный, создаётся отдельный класс в этом пакете
и регистрируется в sources/__init__.py — основной конвейер не меняется.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional
from urllib.parse import urljoin, urlsplit
from xml.etree import ElementTree

from bs4 import BeautifulSoup

from ...models import RawItem
from ...utils import collapse, content_hash, truncate
from ...utils.http import HttpError, PoliteClient
from ..base import SourceAdapter, SourceResult

SKIP = ("javascript:", "mailto:", "tel:", "#", "facebook.com", "twitter.com",
        "youtube.com", "instagram.com", "/login", "/wp-admin")

# Что делать с публикацией, у которой не удалось определить дату.
#   keep (по умолчанию) — оставить: лучше лишний материал, чем пропуск;
#   drop               — отбросить: для источников, где дата есть всегда.
UNDATED_POLICY = ("keep", "drop")


class _WebBase(SourceAdapter):
    source_type = "web"

    def _cutoff(self, days: int) -> datetime:
        return datetime.now(timezone.utc) - timedelta(days=max(1, int(days)))

    def _keep_by_date(self, published: str, cutoff: datetime) -> bool:
        """
        Фильтр периода. Публикация без распознанной даты попадает в выборку
        только если источник настроен как undated_policy=keep (по умолчанию).
        """
        if not published:
            return (self.config.get("undated_policy") or "keep") == "keep"
        try:
            value = datetime.fromisoformat(published.replace("Z", "+00:00"))
        except ValueError:
            try:
                value = datetime.combine(date.fromisoformat(published[:10]),
                                         datetime.min.time())
            except ValueError:
                return (self.config.get("undated_policy") or "keep") == "keep"
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value >= cutoff

    def _client(self) -> PoliteClient:
        return PoliteClient(
            timeout=float(self.config.get("timeout", 30)),
            retries=int(self.config.get("retries", 3)),
            backoff=float(self.config.get("retry_backoff", 3)),
            delay=float(self.config.get("rate_limit_delay", 2)),
            respect_robots=bool(self.config.get("respect_robots", True)),
        )

    def _make_item(self, title: str, url: str, text: str, published: str = "") -> RawItem:
        item = RawItem(
            source=self.source_id,
            source_type=self.source_type,
            source_url=url,
            external_id="",
            title=truncate(collapse(title), 300),
            raw_text=collapse(text),
            published_at=published,
            meta={
                "agency": self.config.get("agency", ""),
                "topic": self.config.get("topic", ""),
                "source_name": self.config.get("name", self.source_id),
            },
        )
        item.hash = content_hash(item.source, url, item.title, item.raw_text)
        return item


class GenericWebAdapter(_WebBase):
    """Разбирает страницу со списком новостей по CSS-селекторам."""

    adapter_id = "web_html"

    def fetch(self, days: int = 7) -> SourceResult:
        result = SourceResult(source_id=self.source_id)
        url = self.config.get("url") or ""
        if not url:
            result.error = "не задан URL источника"
            return result
        client = self._client()
        try:
            response = client.get(url)
        except HttpError as exc:
            result.error = str(exc)
            return result
        result.fetched_pages = 1

        soup = BeautifulSoup(response.text, "html.parser")
        container = self._container(soup)
        limit = int(self.config.get("max_items", 40))
        follow = bool(self.config.get("follow_detail", False))
        max_details = int(self.config.get("max_detail_pages", 8))
        cutoff = self._cutoff(days)
        followed = 0
        skipped_old = 0
        seen: set[str] = set()

        for anchor in container.find_all("a", href=True):
            href = anchor["href"].strip()
            low = href.lower()
            if not href or any(s in low for s in SKIP):
                continue
            title = collapse(anchor.get_text(" ", strip=True))
            if len(title) < 15:
                continue
            absolute = urljoin(url, href)
            if absolute in seen:
                continue
            seen.add(absolute)

            block = self._block(anchor)
            text = collapse(block.get_text(" ", strip=True)) if block else title
            published = self._date(text)

            if follow and followed < max_details and self._same_host(absolute, url):
                try:
                    detail = client.get(absolute)
                    followed += 1
                    result.fetched_pages += 1
                    detail_soup = BeautifulSoup(detail.text, "html.parser")
                    for tag in detail_soup(["script", "style", "nav", "footer", "header"]):
                        tag.decompose()
                    body = self._container(detail_soup)
                    text = collapse(body.get_text(" ", strip=True))[:12000]
                    published = published or self._date(text)
                except HttpError as exc:
                    self.log.warning("карточка недоступна %s: %s", absolute, exc)

            if not self._keep_by_date(published, cutoff):
                skipped_old += 1
                continue
            result.items.append(self._make_item(title, absolute, text, published))
            if len(result.items) >= limit:
                break
        if skipped_old:
            self.log.info("отброшено как устаревшее: %d", skipped_old)
        return result

    def _container(self, soup: BeautifulSoup):
        selectors = self.config.get("container_selector") or ""
        for sel in [s.strip() for s in selectors.split(",") if s.strip()]:
            node = soup.select_one(sel)
            if node is not None and len(node.get_text(strip=True)) > 80:
                return node
        return soup.body or soup

    @staticmethod
    def _block(anchor):
        anchor_len = len(anchor.get_text(" ", strip=True))
        node = anchor
        fallback = anchor.parent or anchor
        for _ in range(5):
            parent = node.parent
            if parent is None:
                break
            node = parent
            if node.name in ("li", "tr", "article", "div", "td", "p", "section"):
                if len(node.get_text(" ", strip=True)) - anchor_len >= 40:
                    return node
                fallback = node
        return fallback

    @staticmethod
    def _same_host(url: str, base: str) -> bool:
        return urlsplit(url).netloc.lower() == urlsplit(base).netloc.lower()

    _DATE_RE = re.compile(
        r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2},?\s+\d{4}\b"
        r"|\b\d{4}-\d{2}-\d{2}\b", re.I)

    @classmethod
    def _date(cls, text: str) -> str:
        match = cls._DATE_RE.search(text or "")
        if not match:
            return ""
        try:
            from dateutil import parser as date_parser
            return date_parser.parse(match.group(0), dayfirst=False).date().isoformat()
        except Exception:  # noqa: BLE001
            return ""


class RssAdapter(_WebBase):
    """RSS/Atom-лента: самый надёжный вариант для пресс-релизов и СМИ."""

    adapter_id = "web_rss"

    def fetch(self, days: int = 7) -> SourceResult:
        result = SourceResult(source_id=self.source_id)
        url = self.config.get("url") or ""
        if not url:
            result.error = "не задан URL ленты"
            return result
        try:
            response = self._client().get(url)
        except HttpError as exc:
            result.error = str(exc)
            return result
        result.fetched_pages = 1

        try:
            root = ElementTree.fromstring(response.content)
        except ElementTree.ParseError as exc:
            result.error = f"лента не является валидным XML: {exc}"
            return result

        cutoff = self._cutoff(days)
        limit = int(self.config.get("max_items", 40))
        for entry in self._entries(root):
            title, link, summary, published = entry
            if not self._keep_by_date(published, cutoff):
                continue
            result.items.append(self._make_item(title, link, f"{title}\n\n{summary}", published))
            if len(result.items) >= limit:
                break
        return result

    @staticmethod
    def _text(node: Optional[Any]) -> str:
        return collapse(node.text or "") if node is not None else ""

    def _entries(self, root: Any) -> list[tuple[str, str, str, str]]:
        entries: list[tuple[str, str, str, str]] = []
        for item in root.iter():
            tag = item.tag.split("}")[-1].lower()
            if tag not in ("item", "entry"):
                continue
            title = link = summary = published = ""
            for child in item:
                name = child.tag.split("}")[-1].lower()
                if name == "title":
                    title = self._text(child)
                elif name == "link":
                    link = (child.get("href") or child.text or "").strip()
                elif name in ("description", "summary", "content"):
                    summary = BeautifulSoup(child.text or "", "html.parser").get_text(" ", strip=True)
                elif name in ("pubdate", "published", "updated", "date"):
                    published = self._normalize_date(self._text(child))
            if title:
                entries.append((title, link, summary, published))
        return entries

    @staticmethod
    def _normalize_date(value: str) -> str:
        if not value:
            return ""
        try:
            from dateutil import parser as date_parser
            return date_parser.parse(value).astimezone(timezone.utc).isoformat(timespec="seconds")
        except Exception:  # noqa: BLE001
            return ""

