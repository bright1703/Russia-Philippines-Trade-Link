"""
Блокировка одновременных запусков.

Два одновременных прогона конвейера портят и данные, и доставку: один
пишет выпуск, второй в это же время его рассылает. Блокировка файловая,
на flock, и снимается вместе с процессом — после падения или reboot
замок не остаётся висеть навсегда.

Чтение сохранённых данных (бот, /latest, /status) блокировку не берёт
и сбор не запускает.
"""

from __future__ import annotations

import errno
import fcntl
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional


class AlreadyRunning(RuntimeError):
    """Другой прогон уже выполняется. Это не ошибка, а причина не начинать."""


@contextmanager
def exclusive_run(lock_path: Path | str, label: str = "pipeline") -> Iterator[Path]:
    """
    Берёт исключительную блокировку на время прогона.

    Поднимает AlreadyRunning, если замок занят: ждать второй прогон
    смысла нет, он делает ровно ту же работу.
    """
    path = Path(lock_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = os.open(str(path), os.O_RDWR | os.O_CREAT, 0o644)
    try:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno in (errno.EACCES, errno.EAGAIN):
                holder = _read_holder(handle)
                raise AlreadyRunning(
                    f"{label}: прогон уже выполняется{holder}") from None
            raise
        os.ftruncate(handle, 0)
        os.write(handle, f"{os.getpid()}\n".encode("utf-8"))
        os.fsync(handle)
        yield path
    finally:
        try:
            fcntl.flock(handle, fcntl.LOCK_UN)
        finally:
            os.close(handle)


def _read_holder(handle: int) -> str:
    try:
        os.lseek(handle, 0, os.SEEK_SET)
        pid = os.read(handle, 32).decode("utf-8", "replace").strip()
    except OSError:
        return ""
    return f" (pid {pid})" if pid else ""


def current_holder(lock_path: Path | str) -> Optional[int]:
    """PID держателя замка, если он записан. Для диагностики."""
    try:
        text = Path(lock_path).read_text("utf-8").strip()
    except OSError:
        return None
    return int(text) if text.isdigit() else None
