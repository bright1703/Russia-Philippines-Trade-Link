"""
Вежливый HTTP-клиент для веб-источников.

Только чтение. Никаких cookies браузера, авторизации, обхода CAPTCHA.
Таймауты, повторы, пауза между запросами к одному хосту, robots.txt.
"""

from __future__ import annotations

import logging
import time
import urllib.robotparser
from typing import Any, Optional
from urllib.parse import urlsplit

import requests

LOG = logging.getLogger("trade_agent.http")

DEFAULT_UA = "TradeAgentIntelligence/0.1 (public information monitoring; read-only)"


class HttpError(RuntimeError):
    """Источник недоступен или ответил неожиданно."""


class PoliteClient:
    def __init__(self, timeout: float = 30.0, retries: int = 3, backoff: float = 3.0,
                 delay: float = 2.0, user_agent: str = DEFAULT_UA,
                 respect_robots: bool = True):
        self.timeout = timeout
        self.retries = max(1, retries)
        self.backoff = backoff
        self.delay = delay
        self.user_agent = user_agent
        self.respect_robots = respect_robots
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": user_agent,
            "Accept": "text/html,application/xhtml+xml,application/rss+xml,application/json;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        })
        self._last: dict[str, float] = {}
        self._robots: dict[str, Optional[urllib.robotparser.RobotFileParser]] = {}

    def _host(self, url: str) -> str:
        return urlsplit(url).netloc.lower()

    def _wait(self, host: str) -> None:
        last = self._last.get(host)
        if last is not None:
            gap = self.delay - (time.monotonic() - last)
            if gap > 0:
                time.sleep(gap)
        self._last[host] = time.monotonic()

    def allowed(self, url: str) -> bool:
        if not self.respect_robots:
            return True
        host = self._host(url)
        if host not in self._robots:
            parser: Optional[urllib.robotparser.RobotFileParser] = urllib.robotparser.RobotFileParser()
            try:
                resp = self.session.get(f"{urlsplit(url).scheme}://{host}/robots.txt", timeout=self.timeout)
                if resp.status_code == 200:
                    parser.parse(resp.text.splitlines())
                else:
                    parser = None
            except requests.RequestException:
                parser = None
            self._robots[host] = parser
        parser = self._robots[host]
        if parser is None:
            return True
        try:
            return parser.can_fetch(self.user_agent, url)
        except Exception:  # noqa: BLE001
            return True

    def get(self, url: str, **kwargs: Any) -> requests.Response:
        if not self.allowed(url):
            raise HttpError(f"robots.txt запрещает обращение к {url}")
        host = self._host(url)
        last_error: Optional[BaseException] = None
        for attempt in range(1, self.retries + 1):
            self._wait(host)
            try:
                resp = self.session.get(url, timeout=self.timeout, **kwargs)
                if resp.status_code in (429, 503):
                    raise HttpError(f"{resp.status_code} от {host}")
                resp.raise_for_status()
                return resp
            except (requests.RequestException, HttpError) as exc:
                last_error = exc
                if attempt < self.retries:
                    time.sleep(self.backoff * attempt)
        raise HttpError(f"GET {url}: {last_error}")
