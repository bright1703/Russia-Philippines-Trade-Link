#!/usr/bin/env python3
"""
Одноразовый интерактивный вход в Telegram для получения session string.

ВАЖНО: запускать ТОЛЬКО локально, на своей машине, вручную. Не запускать
через Claude/CI/удалённую сессию — вход требует код, который Telegram
пришлёт лично вам (в приложение или по SMS), и, возможно, пароль 2FA.
Никому не передавайте итоговую session string — это полный доступ к
вашему Telegram-аккаунту, эквивалент пароля.

Использование:
    pip install telethon
    python generate_session.py

Скрипт спросит api_id, api_hash (см. https://my.telegram.org), номер
телефона и код подтверждения, затем выведет TELEGRAM_SESSION_STRING.
Сохраните её в свой секрет-менеджер (.env локально, либо GitHub Actions
secret) — не коммитьте в репозиторий.
"""

from telethon.sync import TelegramClient
from telethon.sessions import StringSession

def main():
    print("Получение session string для Telegram (одноразовая настройка).")
    print("Учётные данные берутся с https://my.telegram.org -> API development tools.\n")

    api_id = input("api_id: ").strip()
    api_hash = input("api_hash: ").strip()

    with TelegramClient(StringSession(), int(api_id), api_hash) as client:
        session_string = client.session.save()

    print("\nГотово. Сохраните эту строку как TELEGRAM_SESSION_STRING")
    print("(в .env локально или как секрет в GitHub Actions). Никому её не показывайте:\n")
    print(session_string)


if __name__ == "__main__":
    main()
