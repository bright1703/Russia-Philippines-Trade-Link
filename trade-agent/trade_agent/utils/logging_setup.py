"""Настройка логирования с вырезанием секретов."""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional


class _RedactFilter(logging.Filter):
    """Убирает значения известных секретов из сообщений."""

    def filter(self, record: logging.LogRecord) -> bool:
        from ..config import redact  # локальный импорт: избегаем цикла
        try:
            record.msg = redact(str(record.getMessage()))
            record.args = ()
        except Exception:  # noqa: BLE001 - логирование не должно падать
            pass
        return True


def setup_logging(log_dir: Optional[Path] = None, verbose: bool = False,
                  filename: str = "trade_agent.log") -> logging.Logger:
    level = logging.DEBUG if verbose else logging.INFO
    root = logging.getLogger("trade_agent")
    root.setLevel(level)
    root.handlers.clear()
    root.propagate = False

    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")

    stream = logging.StreamHandler(sys.stderr)
    stream.setLevel(logging.WARNING if not verbose else logging.DEBUG)
    stream.setFormatter(fmt)
    stream.addFilter(_RedactFilter())
    root.addHandler(stream)

    if log_dir:
        try:
            Path(log_dir).mkdir(parents=True, exist_ok=True)
            file_handler = RotatingFileHandler(
                Path(log_dir) / filename, maxBytes=2_000_000, backupCount=5, encoding="utf-8"
            )
            file_handler.setLevel(level)
            file_handler.setFormatter(fmt)
            file_handler.addFilter(_RedactFilter())
            root.addHandler(file_handler)
        except OSError:
            pass
    return root
