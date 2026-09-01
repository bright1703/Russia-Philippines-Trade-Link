"""Повторные попытки с экспоненциальной задержкой и подсчётом попыток."""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Iterable, Optional

LOG = logging.getLogger("trade_agent.retry")


class RetryError(RuntimeError):
    """Все попытки исчерпаны."""

    def __init__(self, message: str, attempts: int, last_error: Optional[BaseException] = None):
        super().__init__(message)
        self.attempts = attempts
        self.last_error = last_error


def retry_call(func: Callable[[], Any], *, retries: int = 3, backoff: float = 2.0,
               exceptions: Iterable[type[BaseException]] = (Exception,),
               label: str = "call", sleep: Callable[[float], None] = time.sleep,
               counter: Optional[dict[str, int]] = None) -> Any:
    """
    Выполняет func с повторами. `retries` — общее число попыток (не дополнительных).
    В counter["retries"] накапливается число повторов для журнала runs.
    """
    attempts = max(1, int(retries))
    last: Optional[BaseException] = None
    for attempt in range(1, attempts + 1):
        try:
            return func()
        except tuple(exceptions) as exc:
            last = exc
            if attempt >= attempts:
                break
            if counter is not None:
                counter["retries"] = counter.get("retries", 0) + 1
            delay = backoff * attempt
            LOG.warning("%s: попытка %d/%d не удалась (%s), пауза %.1fs",
                        label, attempt, attempts, exc, delay)
            sleep(delay)
    raise RetryError(f"{label}: не удалось за {attempts} попыток: {last}", attempts, last)
