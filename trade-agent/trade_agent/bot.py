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
from .models import LINK_DIRECT
from .timeutil import manila_date, manila_stamp
from .utils import setup_logging, truncate

LOG = logging.getLogger("trade_agent.bot")
API = "https://api.telegram.org/bot{token}/{method}"
MAX_MESSAGE = 3800
PRIVATE_CHAT = "private"


class TelegramApiError(RuntimeError):
    """Telegram ответил ошибкой. Текст очищен от токена."""


class TelegramUncertain(TelegramApiError):
    """
    Результат вызова неизвестен: запрос ушёл, ответ не получен.

    Сообщение могло дойти. Считать такую отправку ни успешной, ни
    провалившейся нельзя, поэтому она выделена в отдельный случай.
    """


HELP = (
    "Доступные команды:\n"
    "/status — состояние системы и охват сбора\n"
    "/latest — последний выпуск\n"
    "/companies — список компаний\n"
    "/companies <id> [страница] — все компании по карточке выпуска\n"
    "/opportunities — последние найденные связи\n"
    "/help — эта справка"
)

# Сколько компаний показывать на одной странице полного списка.
COMPANIES_PAGE = 15


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
        except (requests.Timeout, requests.ConnectionError) as exc:
            # Запрос мог дойти до Telegram, а ответ потеряться. Это не
            # отказ: повторять отправку вслепую нельзя.
            raise TelegramUncertain(
                self._sanitize(f"{method}: ответ не получен: {exc}")) from None
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

    def send_post(self, chat_id: int, text: str) -> int:
        """
        Отправляет одну готовую часть выпуска и возвращает id сообщения.

        Id нужен состоянию доставки: по нему видно, что именно ушло,
        и повторная рассылка подтверждённой части не выполняется.
        """
        if not self._chat_allowed(chat_id):
            raise TelegramApiError("чат не разрешён")
        data = self._call("sendMessage", chat_id=chat_id, text=text,
                          disable_web_page_preview=True)
        return int((data.get("result") or {}).get("message_id") or 0)

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
        arguments = normalized.split()[1:]
        handlers = {
            "/status": lambda: self.cmd_status(),
            "/health": lambda: self.cmd_status(),
            "/latest": lambda: self.cmd_latest(),
            "/companies": lambda: self.cmd_companies(arguments),
            "/opportunities": lambda: self.cmd_opportunities(),
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
        """
        Технические подробности, которых нет в выпуске.

        Статусы сбора, анализа, выпуска и доставки показываются раздельно:
        по одному зелёному статусу службы о качестве выпуска судить нельзя.
        Ошибки старых ожидающих сигналов здесь видны независимо от периода
        последнего выпуска.
        """
        db = Database(self.settings.db_path)
        try:
            stats = db.stats()
            runs = db.recent_runs(5)
            sources = db.all_source_states()
            review_failures = db.review_failures(5)
            issue = db.latest_issue("built")
            deliveries = db.deliveries_for_issue(int(issue.id)) if issue else []
            pending = db.signals_needing_attention(10)
        finally:
            db.close()

        lines = [
            "Состояние системы",
            "",
            "Сбор:",
            f"- сырьё: {stats['raw_items']}, в очереди на разбор: {stats['queue']}",
        ]
        for state in sources:
            if state.last_error:
                mark = f"ошибка загрузки: {truncate(state.last_error, 120)}"
            elif not state.coverage_complete:
                mark = "период покрыт не полностью, есть остаток для догрузки"
            elif state.never_ran:
                mark = "не подключён, ни одной успешной проверки"
            else:
                mark = (f"последняя проверка {manila_stamp(state.last_success_at)}, "
                        f"свежая публикация {manila_date(state.last_published_at) or '—'}")
            lines.append(f"- {state.source_id}: {mark}")

        lines += [
            "",
            "Анализ:",
            f"- сигналы: {stats['signals']} (новых {stats['signals_new']}, "
            f"подтверждено {stats['signals_analyzed']}, "
            f"сбой {stats['signals_failed']}, ручная проверка {stats['signals_needs_review']})",
            f"- анализы: {stats['analyses']}, прошли рецензию: {stats['reviews_pass']}, "
            f"рецензия не состоялась: {stats['reviews_failed']}",
            f"- компании: {stats['companies']}, связей с событиями: {stats['matches']}",
        ]
        if pending:
            lines.append("- ожидают вмешательства:")
            for signal in pending[:5]:
                lines.append(f"  #{signal.id} {signal.status}: "
                             f"{signal.last_error or 'причина не записана'}")
        if review_failures:
            lines.append("- сбои рецензии (модель, причина остановки, объём ответа):")
            for row in review_failures:
                lines.append(
                    f"  {row['error']} ×{row['count']}: {row['model']}, "
                    f"stop_reason={row['stop_reason']}, "
                    f"в среднем {row['avg_response_chars']} симв. / "
                    f"{row['avg_output_tokens']} токенов")

        lines += ["", "Выпуск:"]
        if issue is None:
            lines.append("- успешно собранных выпусков ещё нет")
        else:
            counters = issue.counters or {}
            lines.append(f"- №{issue.id} от {manila_stamp(issue.built_at)}: "
                         f"карточек {counters.get('cards', 0)}, "
                         f"наблюдать {counters.get('watch', 0)}, "
                         f"поздних подтверждений {counters.get('late_confirmations', 0)}")
            if not deliveries:
                lines.append("- доставка: не выполнялась")
            for delivery in deliveries:
                lines.append(f"- доставка в чат {delivery.chat_id}: {delivery.status}, "
                             f"частей {delivery.parts_sent}/{delivery.parts_total}"
                             + (f", {truncate(delivery.error, 120)}" if delivery.error else ""))

        lines += ["", "Последние запуски:"]
        for run in runs:
            lines.append(
                f"- {run.stage}: {run.status}, обработано {run.processed}, "
                f"новых {run.created}, ошибок {run.errors}, {run.duration_sec}s "
                f"({manila_stamp(run.started_at)})"
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

    def cmd_companies(self, arguments: Optional[list[str]] = None) -> str:
        """
        Список компаний.

        Без аргументов — каталог постранично. С номером сигнала — ПОЛНЫЙ
        список компаний по этой карточке выпуска: сначала прямая
        применимость, затем компании отрасли. В карточку выпуска
        помещаются только первые три, остальные доступны здесь.
        """
        arguments = [a for a in (arguments or []) if a]
        signal_id = 0
        page = 1
        if arguments and arguments[0].lstrip("#").isdigit():
            signal_id = int(arguments[0].lstrip("#"))
            if len(arguments) > 1 and arguments[1].isdigit():
                page = max(1, int(arguments[1]))
        elif arguments and arguments[0].isdigit():
            page = max(1, int(arguments[0]))

        db = Database(self.settings.db_path)
        try:
            companies = {c.slug: c for c in db.all_companies()}
            matches = db.matches_for_signals([signal_id]) if signal_id else []
        finally:
            db.close()

        if signal_id:
            return self._companies_for_signal(signal_id, matches, companies, page)

        if not companies:
            return "Профили компаний не загружены. Положите файлы в brain/companies/."
        rows = sorted(companies.values(), key=lambda c: c.name)
        return self._paginate(
            f"Компаний в базе: {len(rows)}",
            [f"- {c.name} [{c.slug}]: "
             + (", ".join(c.products[:3]) or "номенклатура не заполнена")
             for c in rows],
            page, "/companies")

    def _companies_for_signal(self, signal_id: int, matches: list[Any],
                              companies: dict[str, Any], page: int) -> str:
        if not matches:
            return f"По карточке #{signal_id} связей с компаниями не найдено."
        direct = [m for m in matches if m.link_type == LINK_DIRECT]
        sector = [m for m in matches if m.link_type != LINK_DIRECT]

        def row(match: Any) -> str:
            company = companies.get(match.company_slug)
            name = company.name if company else match.company_slug
            return f"- {name} ({match.match_score}/5) — {match.recommended_action}"

        lines: list[str] = []
        if direct:
            lines.append("ПРЯМАЯ ПРИМЕНИМОСТЬ: в материале назван товар, код или компания")
            lines += [row(m) for m in direct]
        if sector:
            if lines:
                lines.append("")
            lines.append("КОМПАНИИ ОТРАСЛИ: применимость к продукции не установлена")
            lines += [row(m) for m in sector]
        header = (f"Карточка #{signal_id}: прямая применимость — {len(direct)}, "
                  f"компании отрасли — {len(sector)}")
        return self._paginate(header, lines, page, f"/companies {signal_id}")

    @staticmethod
    def _paginate(header: str, lines: list[str], page: int, command: str) -> str:
        """Страница списка с понятной навигацией."""
        total_pages = max(1, (len(lines) + COMPANIES_PAGE - 1) // COMPANIES_PAGE)
        page = min(max(1, page), total_pages)
        start = (page - 1) * COMPANIES_PAGE
        body = lines[start:start + COMPANIES_PAGE]
        result = [header, f"Страница {page} из {total_pages}", ""] + body
        if page < total_pages:
            result += ["", f"Дальше: {command} {page + 1}"]
        return "\n".join(result)

    def cmd_opportunities(self) -> str:  # noqa: C901 - линейный вывод списка
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
