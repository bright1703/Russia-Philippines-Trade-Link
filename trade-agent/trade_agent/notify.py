"""Одноразовая доставка последнего дайджеста в личный Telegram-чат."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional

from .bot import TelegramApiError, TelegramBot
from .config import load_settings
from .exit_codes import EXIT_CRITICAL, EXIT_OK
from .utils import setup_logging


def target_chat_ids(settings) -> tuple[int, ...]:
    """Возвращает разрешенные личные чаты без принятия id из текста новости."""
    if settings.bot.allowed_chat_ids:
        return settings.bot.allowed_chat_ids
    # В личном чате Telegram id пользователя совпадает с id чата.
    return settings.bot.allowed_user_ids


def send_latest(settings, path: Optional[Path] = None) -> int:
    digest_path = Path(path or settings.digest_dir / "latest.md")
    if not digest_path.exists():
        raise FileNotFoundError(f"дайджест не найден: {digest_path}")
    chats = target_chat_ids(settings)
    if not settings.bot.token or not chats:
        raise ValueError(
            "для доставки нужны TELEGRAM_BOT_TOKEN и "
            "TELEGRAM_ALLOWED_USER_ID или TELEGRAM_ALLOWED_CHAT_ID"
        )
    text = digest_path.read_text("utf-8")
    bot = TelegramBot(settings)
    for chat_id in chats:
        bot.send(chat_id, text)
    return len(chats)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m trade_agent.notify",
        description="Отправить последний дайджест в разрешенный личный чат.",
    )
    parser.add_argument("--file", type=Path, default=None)
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args(argv)
    settings = load_settings()
    setup_logging(settings.log_dir, args.verbose, filename="notify.log")
    try:
        count = send_latest(settings, args.file)
    except (FileNotFoundError, ValueError, TelegramApiError, OSError) as exc:
        print(f"Доставка не выполнена: {exc}", file=sys.stderr)
        return EXIT_CRITICAL
    print(f"Дайджест отправлен в чатов: {count}")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
