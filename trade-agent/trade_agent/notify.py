"""
Доставка выпуска в личный Telegram-чат.

Доставка привязана к КОНКРЕТНОМУ собранному выпуску, а не к файлу
`latest.md` на диске. Если сборка текущего выпуска упала, старый файл
не будет отправлен как новый: содержимое сверяется с хэшем выпуска,
записанным при сборке.

Состояние доставки хранится по частям. Подтверждённая доставка при
повторном запуске не повторяется. Если результат сетевого вызова неясен
(запрос ушёл, ответ не получен), доставка помечается неопределённой:
слепая повторная рассылка в этом случае не выполняется, потому что
сообщение могло дойти. Внешний API не даёт оснований обещать абсолютную
однократность, и делать вид, что даёт, нечестно.
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
from pathlib import Path
from typing import Any, Optional

from .bot import TelegramApiError, TelegramBot, TelegramUncertain, MAX_MESSAGE
from .config import load_settings
from .db import Database
from .digest import content_hash
from .exit_codes import EXIT_CRITICAL, EXIT_OK, EXIT_PARTIAL
from .models import (
    DELIVERY_FAILED, DELIVERY_PARTIAL, DELIVERY_SENT, DELIVERY_UNKNOWN, Delivery,
)
from .utils import setup_logging

LOG = logging.getLogger("trade_agent.notify")

# Ориентир — весь краткий выпуск в 1-2 сообщениях. Отдельный пост
# на каждое техническое поле человеку не нужен.
TARGET_POSTS = 2


def _clean_markdown(text: str) -> str:
    """Превращает внутренний Markdown в обычный текст для Telegram."""
    text = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", r"\1: \2", text)
    text = text.replace("**", "").replace("__", "")
    lines: list[str] = []
    for line in text.splitlines():
        stripped = line.rstrip()
        if stripped.strip() == "---":
            continue
        if stripped.startswith("# "):
            stripped = stripped[2:]
        elif stripped.startswith("## "):
            stripped = "\n" + stripped[3:].upper()
        elif stripped.startswith("### "):
            stripped = "\n" + stripped[4:]
        elif stripped.lstrip().startswith("- "):
            indent = len(stripped) - len(stripped.lstrip())
            stripped = " " * indent + "• " + stripped.lstrip()[2:]
        lines.append(stripped)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def _blocks(markdown: str) -> list[str]:
    """Режет выпуск по границам карточек и разделов, а не по символам."""
    parts = re.split(r"(?=^##+\s)", markdown, flags=re.MULTILINE)
    return [part.strip() for part in parts if part.strip()]


def pack_posts(markdown: str, limit: int = MAX_MESSAGE) -> list[str]:
    """
    Складывает выпуск в минимальное число сообщений.

    Блоки склеиваются, пока помещаются в одно сообщение. Слишком длинный
    блок режется по строкам, а не посередине слова.
    """
    posts: list[str] = []
    current = ""
    for block in _blocks(markdown):
        text = _clean_markdown(block)
        if not text:
            continue
        if len(text) > limit:
            if current:
                posts.append(current)
                current = ""
            chunk = ""
            for line in text.splitlines():
                if len(chunk) + len(line) + 1 > limit:
                    posts.append(chunk.strip())
                    chunk = ""
                chunk += line + "\n"
            if chunk.strip():
                current = chunk.strip()
            continue
        if not current:
            current = text
        elif len(current) + len(text) + 2 <= limit:
            current += "\n\n" + text
        else:
            posts.append(current)
            current = text
    if current:
        posts.append(current)
    return posts


def target_chat_ids(settings: Any) -> tuple[int, ...]:
    """Разрешённые личные чаты. Идентификаторы из текста новостей не берутся."""
    if settings.bot.allowed_chat_ids:
        return settings.bot.allowed_chat_ids
    # В личном чате Telegram id пользователя совпадает с id чата.
    return settings.bot.allowed_user_ids


def deliver_issue(settings: Any, db: Any, bot: TelegramBot, issue: Any,
                  markdown: str) -> list[Delivery]:
    """
    Отправляет один выпуск во все разрешённые чаты.

    Возвращает состояние доставки по каждому чату. Уже подтверждённые
    доставки пропускаются, частичные — досылаются с той части, на которой
    остановились.
    """
    posts = pack_posts(markdown)
    deliveries: list[Delivery] = []
    for chat_id in target_chat_ids(settings):
        delivery = db.get_delivery(int(issue.id), str(chat_id)) or Delivery(
            issue_id=int(issue.id), chat_id=str(chat_id))
        delivery.parts_total = len(posts)

        if delivery.status == DELIVERY_SENT and delivery.parts_sent >= len(posts):
            LOG.info("выпуск %s в чат %s уже доставлен — повтор не выполняется",
                     issue.id, chat_id)
            deliveries.append(delivery)
            continue
        if delivery.status == DELIVERY_UNKNOWN and delivery.parts_sent:
            # Результат прошлой попытки неизвестен: сообщение могло дойти.
            # Слепо рассылать заново нельзя, решение принимает человек.
            LOG.warning("выпуск %s в чат %s: прошлая доставка неопределённая, "
                        "повтор не выполняется автоматически", issue.id, chat_id)
            deliveries.append(delivery)
            continue

        start = delivery.parts_sent if delivery.status == DELIVERY_PARTIAL else 0
        for index in range(start, len(posts)):
            try:
                message_id = bot.send_post(int(chat_id), posts[index])
            except TelegramUncertain as exc:
                delivery.status = DELIVERY_UNKNOWN
                delivery.error = str(exc)[:500]
                LOG.error("неопределённый результат отправки части %d: %s", index + 1, exc)
                break
            except TelegramApiError as exc:
                delivery.status = DELIVERY_PARTIAL if delivery.parts_sent else DELIVERY_FAILED
                delivery.error = str(exc)[:500]
                LOG.error("ошибка отправки части %d: %s", index + 1, exc)
                break
            if message_id:
                delivery.message_ids.append(int(message_id))
            delivery.parts_sent = index + 1
        else:
            delivery.status = DELIVERY_SENT
            delivery.error = ""

        db.save_delivery(delivery)
        deliveries.append(delivery)
    return deliveries


def send_latest(settings: Any, path: Optional[Path] = None) -> int:
    """
    Отправляет последний УСПЕШНО собранный выпуск.

    Возвращает число чатов, куда доставка подтверждена.
    """
    chats = target_chat_ids(settings)
    if not settings.bot.token or not chats:
        raise ValueError(
            "для доставки нужны TELEGRAM_BOT_TOKEN и "
            "TELEGRAM_ALLOWED_USER_ID или TELEGRAM_ALLOWED_CHAT_ID"
        )

    db = Database(settings.db_path)
    try:
        issue = db.latest_issue("built")
        digest_path = Path(path or (Path(settings.digest_dir) / "latest.md"))
        if not digest_path.exists():
            raise FileNotFoundError(f"выпуск не найден: {digest_path}")
        markdown = digest_path.read_text("utf-8")

        if issue is None:
            raise ValueError("в базе нет успешно собранного выпуска — "
                             "старый файл latest.md как новый выпуск не отправляется")
        if path is None and issue.content_hash and \
                issue.content_hash != content_hash(markdown):
            raise ValueError(
                "latest.md не совпадает с сохранённым составом последнего выпуска: "
                "вероятно, сборка текущего выпуска упала. Отправка отменена.")

        bot = TelegramBot(settings)
        deliveries = deliver_issue(settings, db, bot, issue, markdown)
    finally:
        db.close()
    return sum(1 for d in deliveries if d.confirmed)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m trade_agent.notify",
        description="Отправить последний собранный выпуск в разрешённый личный чат.",
    )
    parser.add_argument("--file", type=Path, default=None,
                        help="отправить конкретный файл (сверка с выпуском отключается)")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args(argv)
    settings = load_settings()
    setup_logging(settings.log_dir, args.verbose, filename="notify.log")
    try:
        count = send_latest(settings, args.file)
    except (FileNotFoundError, ValueError, TelegramApiError, OSError) as exc:
        print(f"Доставка не выполнена: {exc}", file=sys.stderr)
        return EXIT_CRITICAL
    if count == 0:
        print("Доставка не подтверждена ни в одном чате", file=sys.stderr)
        return EXIT_PARTIAL
    print(f"Выпуск доставлен в чатов: {count}")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
