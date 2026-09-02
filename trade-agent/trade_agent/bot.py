#!/usr/bin/env python3
"""
Telegram Bot доставки результатов.

Это ОТДЕЛЬНЫЙ бот, а не Telethon-аккаунт сбора данных.

Жёсткие правила:
  * бот отвечает только пользователям из белого списка
    (TELEGRAM_ALLOWED_USER_ID, можно несколько через запятую);
  * бот ничего не рассылает сам и не пишет третьим лицам;
  * бот не выполняет действий — только показывает то, что уже собрано.

    python -m trade_agent.bot            # long polling
    python -m trade_agent.bot --once     # разобрать очередь и выйти
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Optional

import requests

from .config import load_settings
from .db import Database
from .utils import setup_logging, truncate

LOG = logging.getLogger("trade_agent.bot")
API = "https://api.telegram.org/bot{token}/{method}"
MAX_MESSAGE = 3800
PRIVATE_CHAT = "private"


class TelegramApiError(RuntimeError):
    """Telegram ответил ошибкой. Текст очищен от токена."""

HELP = (
    "Доступные команды:\n"
    "/status — состояние системы\n"
    "/latest — последний дайджест\n"
    "/companies — список компаний\n"
    "/opportunities — последние найденные возможности\n"
    "/help — эта справка"
)


class TelegramBot:
    def __init__(self, settings: Any, session: Optional[Any] = None):
        self.settings = settings
        self.token = settings.bot.token
        self.allowed = set(settings.bot.allowed_user_ids)
        self.allowed_chats = set(getattr(settings.bot, "allowed_chat_ids", ()) or ())
        self.session = session or requests.Session()
        self.offset_path = Path(getattr(settings.bot, "offset_path", None)
                                or (Path(settings.project_dir) / "data" / "bot_offset.json"))
        self.offset = self._load_offset()

    # -- транспорт ---------------------------------------------------------
    def _sanitize(self, text: str) -> str:
        """Убирает токен из любых сообщений об ошибках и логов."""
        result = str(text)
        if self.token:
            result = result.replace(self.token, "<TELEGRAM_BOT_TOKEN:redacted>")
        return result

    def _call(self, method: str, **params: Any) -> dict[str, Any]:
        """
        Вызов Telegram API. HTTP 200 с ok:false — это ошибка, а не успех.
        Токен никогда не попадает в текст исключения или лога.
        """
        try:
            response = self.session.post(
                API.format(token=self.token, method=method), json=params, timeout=60
            )
        except requests.RequestException as exc:
            raise TelegramApiError(self._sanitize(f"{method}: сеть недоступна: {exc}")) from None

        if response.status_code != 200:
            raise TelegramApiError(
                self._sanitize(f"{method}: HTTP {response.status_code}")
            )
        try:
            data = response.json()
        except ValueError:
            raise TelegramApiError(f"{method}: ответ не является JSON") from None
        if not isinstance(data, dict) or data.get("ok") is not True:
            description = ""
            if isinstance(data, dict):
                description = str(data.get("description") or "")
            raise TelegramApiError(
                self._sanitize(f"{method}: Telegram вернул ok=false: {description}")
            )
        return data

    def send(self, chat_id: int, text: str) -> None:
        """Отправка только в разрешённый чат. В чужой чат бот не пишет."""
        if not self._chat_allowed(chat_id):
            LOG.warning("попытка отправки в неразрешённый чат отклонена")
            return
        for chunk in self._split(text):
            self._call("sendMessage", chat_id=chat_id, text=chunk,
                       disable_web_page_preview=True)

    @staticmethod
    def _split(text: str) -> list[str]:
        """Режет длинный ответ на части, помещающиеся в одно сообщение."""
        if len(text) <= MAX_MESSAGE:
            return [text]
        # Простая нарезка по символам также покрывает одну очень длинную
        # строку. Вариант через split("\n") оставлял такую строку больше
        # лимита Telegram и иногда добавлял пустой первый кусок.
        return [text[start:start + MAX_MESSAGE]
                for start in range(0, len(text), MAX_MESSAGE)]

    def _chat_allowed(self, chat_id: int) -> bool:
        if self.allowed_chats:
            return chat_id in self.allowed_chats
        # Без явного списка чатов разрешён только личный чат владельца:
        # в личном чате Telegram chat.id совпадает с user.id.
        return chat_id in self.allowed

    # -- команды -----------------------------------------------------------
    def handle(self, text: str) -> str:
        normalized = (text or "").strip()
        command = normalized.split()[0].lower() if normalized else ""
        command = command.split("@")[0]
        handlers = {
            "/status": self.cmd_status,
            "/health": self.cmd_status,
            "/latest": self.cmd_latest,
            "/companies": self.cmd_companies,
            "/opportunities": self.cmd_opportunities,
            "/start": lambda: HELP,
            "/help": lambda: HELP,
        }
        handler = handlers.get(command)
        if handler is None:
            return "Неизвестная команда.\n\n" + HELP
        try:
            return handler()
        except Exception as exc:  # noqa: BLE001 - бот не должен падать от одной команды
            LOG.error("команда %s завершилась ошибкой: %s", command,
                      self._sanitize(str(exc)))
            return "Команда не выполнена. Подробности записаны в журнал."

    def cmd_status(self) -> str:
        db = Database(self.settings.db_path)
        try:
            stats = db.stats()
            runs = db.recent_runs(5)
        finally:
            db.close()
        lines = [
            "Состояние системы",
            f"Сырьё: {stats['raw_items']} (в очереди: {stats['queue']})",
            f"Сигналы: {stats['signals']} (новых {stats['signals_new']}, "
            f"разобрано {stats['signals_analyzed']})",
            f"Анализы: {stats['analyses']}, прошли рецензию: {stats['reviews_pass']}",
            f"Компании: {stats['companies']}, совпадений: {stats['matches']}",
            "",
            "Последние запуски:",
        ]
        for run in runs:
            lines.append(
                f"- {run.stage}: {run.status}, обработано {run.processed}, "
                f"новых {run.created}, ошибок {run.errors}, {run.duration_sec}s "
                f"({run.started_at})"
            )
        if stats["last_error"]:
            lines += ["", f"Последняя ошибка: {truncate(stats['last_error'], 300)}"]
        cfg = self.settings.public_dict()
        lines += ["", f"LLM настроен: {'да' if cfg['llm_configured'] else 'нет'}"]
        return "\n".join(lines)

    def cmd_latest(self) -> str:
        path = Path(self.settings.digest_dir) / "latest.md"
        if not path.exists():
            return "Дайджест ещё не сформирован. Запустите python -m trade_agent.digest"
        return truncate(path.read_text("utf-8"), MAX_MESSAGE * 3)

    def cmd_companies(self) -> str:
        db = Database(self.settings.db_path)
        try:
            companies = db.all_companies()
        finally:
            db.close()
        if not companies:
            return "Профили компаний не загружены. Положите файлы в brain/companies/."
        lines = [f"Компаний в базе: {len(companies)}", ""]
        for company in companies:
            products = ", ".join(company.products[:4]) or "номенклатура не заполнена"
            lines.append(f"- {company.name} [{company.slug}]: {products}"
                         + (f" — {company.status}" if company.status else ""))
        return "\n".join(lines)

    def cmd_opportunities(self) -> str:
        db = Database(self.settings.db_path)
        try:
            matches = db.matches_since(7, self.settings.radar_min_match_score)
            companies = {c.slug: c for c in db.all_companies()}
            signals = {int(s.id or 0): s for s in db.signals_since(7)}
            raw = {sid: db.get_raw_item(s.raw_item_id) for sid, s in signals.items()}
        finally:
            db.close()
        if not matches:
            return "За последние 7 дней совпадений нет."
        lines = [f"Возможности за 7 дней: {len(matches)}", ""]
        for match in matches[:20]:
            item = raw.get(match.signal_id)
            company = companies.get(match.company_slug)
            title = item.title if item else f"сигнал #{match.signal_id}"
            lines.append(
                f"- [{match.match_score}/5] {company.name if company else match.company_slug}: "
                f"{truncate(title, 120)}"
            )
            if item and item.source_url:
                lines.append(f"  {item.source_url}")
            lines.append(f"  Действие: {match.recommended_action}")
        return "\n".join(lines)

    # -- offset ------------------------------------------------------------
    def _load_offset(self) -> int:
        """Читает сохранённый offset. Битый файл не должен ронять бота."""
        try:
            payload = json.loads(self.offset_path.read_text("utf-8"))
            return max(0, int(payload.get("offset", 0)))
        except (OSError, ValueError, TypeError, AttributeError):
            return 0

    def _save_offset(self, offset: int) -> None:
        """
        Атомарная запись: временный файл в том же каталоге + os.replace.
        Так после сбоя не останется обрезанного файла, и бот не начнёт
        заново обрабатывать старые обновления.
        """
        temporary_name = None
        try:
            self.offset_path.parent.mkdir(parents=True, exist_ok=True)
            handle = tempfile.NamedTemporaryFile(
                "w", encoding="utf-8", dir=str(self.offset_path.parent),
                prefix=".bot_offset.", suffix=".tmp", delete=False)
            temporary_name = handle.name
            with handle as tmp:
                json.dump({"offset": int(offset)}, tmp)
                tmp.flush()
                os.fsync(tmp.fileno())
            os.replace(handle.name, self.offset_path)
        except OSError as exc:
            if temporary_name:
                try:
                    Path(temporary_name).unlink(missing_ok=True)
                except OSError:
                    pass
            raise TelegramApiError(
                self._sanitize(f"не удалось сохранить offset: {exc}")) from None

    def drain_backlog(self) -> int:
        """
        Первый запуск без сохранённого offset: пропустить накопившиеся
        обновления, не выполняя их. Возвращает установленный offset.
        """
        if self.offset:
            return self.offset
        data = self._call("getUpdates", offset=-1, timeout=0, limit=1)
        updates = data.get("result") or []
        if updates:
            new_offset = int(updates[-1].get("update_id", 0)) + 1
            self._save_offset(new_offset)
            self.offset = new_offset
            LOG.info("пропущены накопившиеся обновления, offset=%d", self.offset)
        else:
            self._save_offset(self.offset)
        return self.offset

    # -- цикл --------------------------------------------------------------
    def poll_once(self) -> int:
        data = self._call("getUpdates", offset=self.offset,
                          timeout=self.settings.bot.poll_timeout,
                          limit=getattr(self.settings.bot, "updates_limit", 20),
                          allowed_updates=["message"])
        handled = 0
        highest = self.offset
        for update in data.get("result", []):
            try:
                handled += int(self._handle_update(update))
            except TelegramApiError as exc:
                LOG.warning("обновление не обработано: %s", exc)
                # Не подтверждаем неотправленную команду. Иначе сбой
                # sendMessage теряет обновление навсегда.
                break
            highest = max(highest, int(update.get("update_id", 0)) + 1)
        # Offset двигается и для отклонённых обновлений: иначе чужое
        # сообщение будет вечно возвращаться при каждом опросе.
        if highest != self.offset:
            self._save_offset(highest)
            self.offset = highest
        return handled

    def _handle_update(self, update: dict[str, Any]) -> bool:
        message = update.get("message") or update.get("edited_message") or {}
        chat = message.get("chat") or {}
        chat_id = int(chat.get("id") or 0)
        chat_type = str(chat.get("type") or "")
        user_id = int((message.get("from") or {}).get("id") or 0)
        text = message.get("text") or ""

        if not chat_id:
            return False
        if chat_type != PRIVATE_CHAT:
            # Группы, супергруппы и каналы игнорируются полностью.
            LOG.warning("сообщение из чата типа %r отклонено", chat_type)
            return False
        if user_id not in self.allowed:
            LOG.warning("отклонён запрос от неразрешённого пользователя id=%s", user_id)
            return False
        if not self._chat_allowed(chat_id):
            LOG.warning("отклонён запрос из неразрешённого чата")
            return False

        self.send(chat_id, self.handle(text))
        return True

    def run_forever(self) -> None:
        LOG.info("бот запущен; разрешённых пользователей: %d", len(self.allowed))
        try:
            self.drain_backlog()
        except TelegramApiError as exc:
            LOG.warning("не удалось пропустить накопившиеся обновления: %s", exc)
        while True:
            try:
                self.poll_once()
            except TelegramApiError as exc:
                LOG.warning("Telegram API: %s", exc)
                time.sleep(10)
            except Exception:  # noqa: BLE001
                LOG.exception("ошибка цикла бота")
                time.sleep(10)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m trade_agent.bot",
                                     description="Telegram Bot доставки результатов.")
    parser.add_argument("--once", action="store_true", help="разобрать очередь один раз и выйти")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args(argv)

    settings = load_settings()
    setup_logging(settings.log_dir, args.verbose, filename="bot.log")
    if not settings.bot.configured:
        print("Бот не настроен: нужны TELEGRAM_BOT_TOKEN и TELEGRAM_ALLOWED_USER_ID в .env")
        return 1
    bot = TelegramBot(settings)
    if args.once:
        bot.drain_backlog()
        print(f"Обработано сообщений: {bot.poll_once()}")
        return 0
    bot.run_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
