"""Одноразовая доставка последнего дайджеста в личный Telegram-чат."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Optional

from .bot import TelegramApiError, TelegramBot
from .config import load_settings
from .exit_codes import EXIT_CRITICAL, EXIT_OK
from .utils import setup_logging


SECTION_PREFIXES = {
    "Срочно": "🚨 Срочно",
    "Возможности": "💼 Возможность",
    "Регуляторные изменения": "📌 Регуляторное изменение",
    "Тендеры": "📋 Тендер",
    "Наблюдать": "👀 Наблюдать",
}


def _clean_markdown(text: str) -> str:
    """Превращает внутренний Markdown в обычный текст для Telegram."""
    text = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", r"\1: \2", text)
    text = text.replace("**", "").replace("__", "")
    lines: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line == "---" or line.startswith("> "):
            continue
        if line.startswith("- "):
            line = "• " + line[2:]
        lines.append(line)
    return "\n".join(lines).strip()


def _header_post(header: str) -> str:
    """Собирает короткий первый пост из служебной части дайджеста."""
    date_line = re.search(r"^Дата:\s*(.+)$", header, re.MULTILINE)
    period_line = re.search(r"^Период:\s*(.+)$", header, re.MULTILINE)
    stats_line = re.search(
        r"^Новых сигналов:\s*(\d+)\s*\|\s*Проверенных выводов:\s*(\d+)\s*\|\s*"
        r"Совпадений с компаниями:\s*(\d+)",
        header,
        re.MULTILINE,
    )
    if not (date_line and stats_line):
        return _clean_markdown(header)
    date_text = date_line.group(1)
    period_text = period_line.group(1) if period_line else ""
    signals, analyses, matches = stats_line.groups()
    lines = [
        "🗞 Trade Agent",
        date_text,
        period_text,
        "",
        f"Новых сигналов: {signals}",
        f"Проверено: {analyses}",
        f"Совпадений с компаниями: {matches}",
    ]
    return "\n".join(line for line in lines if line != "").strip()


def _watch_posts(content: str) -> list[str]:
    posts: list[str] = []
    for raw_line in content.splitlines():
        line = raw_line.strip()
        match = re.match(r"^- \[([^\]]+)\]\s+(.+)$", line)
        if not match:
            continue
        label, rest = match.groups()
        title = rest
        url = ""
        url_match = re.search(r"\s+—\s+(https?://\S+)$", rest)
        if url_match:
            title = rest[:url_match.start()].rstrip()
            url = url_match.group(1)
        category, _, score = label.partition(" ")
        post_lines = ["👀 Наблюдать", "", title, "", f"Категория: {category}"]
        if score:
            post_lines.append(f"Оценка Scout: {score}")
        if url:
            post_lines += ["", url]
        posts.append("\n".join(post_lines))
    return posts


def format_digest_posts(markdown: str) -> list[str]:
    """Делит дайджест на короткие Telegram-посты, а не на одну простыню."""
    matches = list(re.finditer(r"^##\s+(.+?)\s*$", markdown, re.MULTILINE))
    if not matches:
        return [_clean_markdown(markdown)] if markdown.strip() else []

    posts = [_header_post(markdown[:matches[0].start()])]
    for index, match in enumerate(matches):
        title = match.group(1).strip()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(markdown)
        content = markdown[match.end():end].strip()
        if title == "Наблюдать":
            posts.extend(_watch_posts(content))
            continue
        if title not in SECTION_PREFIXES or not content:
            continue
        blocks = re.split(r"(?=^###\s+)", content, flags=re.MULTILINE)
        detail_blocks = [block.strip() for block in blocks if block.strip().startswith("### ")]
        if detail_blocks:
            for block in detail_blocks:
                posts.append(
                    SECTION_PREFIXES[title] + "\n\n" + _clean_markdown(block[4:])
                )
        elif not re.match(
            r"^(Ничего|Проверенных возможностей|Изменений не|Подходящих закупок)",
            content,
        ):
            posts.append(SECTION_PREFIXES[title] + "\n\n" + _clean_markdown(content))
    return [post for post in posts if post.strip()]


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
    posts = format_digest_posts(text)
    bot = TelegramBot(settings)
    for chat_id in chats:
        for post in posts:
            bot.send(chat_id, post)
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
