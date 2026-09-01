#!/usr/bin/env python3
"""
Read-only сбор новых сообщений из публичных Telegram-каналов, указанных
в RUBRIC.md, для мониторинга торгового агента Приморского края на
Филиппинах.

Что делает:
  - подключается к Telegram под уже существующей сессией (только чтение);
  - для каждого канала из state.json забирает сообщения новее
    last_message_id (или за последние 7 дней, если last_message_id
    отсутствует — первый запуск);
  - пишет сырые сообщения в trade-agent/telegram/.raw/pending.json.

Чего НЕ делает (сознательно, в коде этих вызовов просто нет):
  - не отправляет сообщения;
  - не вступает в группы/каналы и не покидает их;
  - не меняет профиль, настройки или контакты аккаунта;
  - не читает личные переписки — только публичные каналы из RUBRIC.md.

state.json НЕ обновляется этим скриптом. Обновление last_message_id
происходит только после того, как сырые сообщения успешно превращены в
дайджест (latest.md / archive) — так ни одно сообщение не потеряется,
если сборка дайджеста упадёт на середине.

Использование:
    pip install telethon python-dotenv
    python fetch.py            # обычный запуск (новые сообщения)
    python fetch.py --first-run-days 7   # явно ограничить окно первого запуска
"""

import argparse
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv
from telethon.sync import TelegramClient
from telethon.sessions import StringSession

HERE = Path(__file__).resolve().parent
STATE_PATH = HERE / "state.json"
RAW_DIR = HERE / ".raw"
PENDING_PATH = RAW_DIR / "pending.json"


def load_state() -> dict:
    with open(STATE_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--first-run-days", type=int, default=7,
        help="Глубина окна (в днях) для каналов без сохранённого last_message_id.",
    )
    args = parser.parse_args()

    load_dotenv(HERE / ".env")

    api_id = os.environ.get("TELEGRAM_API_ID")
    api_hash = os.environ.get("TELEGRAM_API_HASH")
    session_string = os.environ.get("TELEGRAM_SESSION_STRING")

    if not (api_id and api_hash and session_string):
        raise SystemExit(
            "Не найдены TELEGRAM_API_ID / TELEGRAM_API_HASH / TELEGRAM_SESSION_STRING.\n"
            "См. trade-agent/telegram/SETUP.md — учётные данные берутся с "
            "my.telegram.org, session string генерируется локально через "
            "generate_session.py и никогда не коммитится в репозиторий."
        )

    state = load_state()
    channels = state["channels"]
    cutoff = datetime.now(timezone.utc) - timedelta(days=args.first_run_days)

    RAW_DIR.mkdir(exist_ok=True)

    collected = {}
    with TelegramClient(StringSession(session_string), int(api_id), api_hash) as client:
        for channel_username, info in channels.items():
            last_id = info.get("last_message_id")
            messages = []

            # Read-only: только iter_messages по публичному каналу.
            # Никаких send_message / join_channel / edit-вызовов в этом файле.
            for msg in client.iter_messages(channel_username, min_id=last_id or 0):
                if last_id is None and msg.date < cutoff:
                    break
                if not msg.text:
                    continue
                messages.append({
                    "channel": channel_username,
                    "message_id": msg.id,
                    "date_utc": msg.date.astimezone(timezone.utc).isoformat(),
                    "text": msg.text,
                    "link": f"https://t.me/{channel_username}/{msg.id}",
                })

            messages.reverse()  # хронологический порядок
            collected[channel_username] = messages
            print(f"{channel_username}: {len(messages)} новых сообщений")

    with open(PENDING_PATH, "w", encoding="utf-8") as f:
        json.dump({
            "fetched_at_utc": datetime.now(timezone.utc).isoformat(),
            "channels": collected,
        }, f, ensure_ascii=False, indent=2)

    total = sum(len(v) for v in collected.values())
    print(f"\nВсего собрано {total} сообщений -> {PENDING_PATH}")
    print("Дальше: обработать pending.json по правилам RUBRIC.md и обновить "
          "latest.md, archive/YYYY-MM-DD.md и state.json.")


if __name__ == "__main__":
    main()
