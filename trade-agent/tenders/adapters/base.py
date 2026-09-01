"""
Базовый слой адаптеров: аккуратный HTTP-клиент и общий интерфейс источника.

Принципы:
  * вежливый доступ — User-Agent с контактом, robots.txt, пауза между запросами;
  * никаких cookies браузера, сессий Telegram, личных аккаунтов и обхода защит;
  * ошибка одного источника не должна ронять весь запуск.
"""

from __future__ import annotations

import logging
import time
import urllib.robotparser
from dataclasses import dataclass, field
from typing import Any, Optional
from urllib.parse import urlsplit

import requests

LOG = logging.getLogger("tenders.http")

PROCUREMENT_HINTS = (
    "invitation to bid", "invitation for bid", "request for quotation",
    "request for proposal", "bid notice", "bid opportunit", "bids and awards",
    "bidding", "procurement", "itb", "rfq", "rfp", "iaeb", "bac ",
    "notice to proceed", "supply and delivery", "public bidding",
    "notice of award", "early procurement", "auction", "canvass",
)


class SourceError(RuntimeError):
    """Источник недоступен или вернул неожиданный ответ."""


@dataclass
class FetchResult:
    source_id: str
    items: list[dict[str, Any]] = field(default_factory=list)
    error: str = ""
    pages_fetched: int = 0


class HttpClient:
    """Общий HTTP-клиент: таймауты, повторы, ограничение частоты, robots.txt."""

    def __init__(self, defaults: dict[str, Any]):
        self.timeout = float(defaults.get("request_timeout", 30))
        self.retries = int(defaults.get("retries", 2))
        self.backoff = float(defaults.get("retry_backoff", 3.0))
        self.delay = float(defaults.get("rate_limit_delay", 2.5))
        self.respect_robots = bool(defaults.get("respect_robots", True))
        self.user_agent = defaults.get("user_agent") or "RPTL-TenderRadar/1.0"
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": self.user_agent,
            "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        })
        self._last_call: dict[str, float] = {}
        self._robots: dict[str, Optional[urllib.robotparser.RobotFileParser]] = {}

    # -- вежливость -------------------------------------------------------
    def _host(self, url: str) -> str:
        return urlsplit(url).netloc.lower()

    def _wait(self, host: str) -> None:
        last = self._last_call.get(host)
        if last is not None:
            gap = self.delay - (time.monotonic() - last)
            if gap > 0:
                time.sleep(gap)
        self._last_call[host] = time.monotonic()

    def allowed(self, url: str) -> bool:
        if not self.respect_robots:
            return True
        host = self._host(url)
        if host not in self._robots:
            parser = urllib.robotparser.RobotFileParser()
            robots_url = f"{urlsplit(url).scheme}://{host}/robots.txt"
            try:
                resp = self.session.get(robots_url, timeout=self.timeout)
                if resp.status_code == 200:
                    parser.parse(resp.text.splitlines())
                else:
                    parser = None  # нет robots.txt — считаем разрешённым
            except requests.RequestException:
                parser = None
            self._robots[host] = parser
        parser = self._robots[host]
        if parser is None:
            return True
        try:
            return parser.can_fetch(self.user_agent, url)
        except Exception:  # noqa: BLE001 - robotparser бывает капризен
            return True

    # -- запросы ----------------------------------------------------------
    def get(self, url: str, **kwargs: Any) -> requests.Response:
        return self._request("GET", url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> requests.Response:
        return self._request("POST", url, **kwargs)

    def _request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        if not self.allowed(url):
            raise SourceError(f"robots.txt запрещает обращение к {url}")
        host = self._host(url)
        last_error: Optional[Exception] = None
        for attempt in range(self.retries + 1):
            self._wait(host)
            try:
                resp = self.session.request(method, url, timeout=self.timeout, **kwargs)
                if resp.status_code in (429, 503):
                    raise SourceError(f"{resp.status_code} от {host} (ограничение частоты)")
                resp.raise_for_status()
                return resp
            except (requests.RequestException, SourceError) as exc:
                last_error = exc
                if attempt < self.retries:
                    time.sleep(self.backoff * (attempt + 1))
        raise SourceError(f"{method} {url}: {last_error}")


class BaseAdapter:
    """Интерфейс источника. Новый источник = новый подкласс + запись в sources.yml."""

    adapter_id = "base"

    def __init__(self, config: dict[str, Any], client: HttpClient):
        self.config = config
        self.client = client
        self.source_id = config.get("id", "")
        self.log = logging.getLogger(f"tenders.{self.source_id or self.adapter_id}")

    def fetch(self, days: int = 7) -> FetchResult:  # pragma: no cover - интерфейс
        raise NotImplementedError

    # -- утилиты для подклассов ------------------------------------------
    @staticmethod
    def looks_like_procurement(text: str) -> bool:
        low = (text or "").lower()
        return any(hint in low for hint in PROCUREMENT_HINTS)
