"""
Источник Telegram — строго read-only.

Telethon-аккаунт остаётся только сборщиком данных существующего модуля
trade-agent/telegram/. Здесь мы НЕ отправляем сообщения, НЕ вступаем в
группы, НЕ ставим реакции и не меняем аккаунт. Доступны только чтение
истории публичных каналов и чтение уже собранных экспортов.

Режимы (telegram.mode в sources.yml):
  export   — читать выгрузки, созданные существующим модулем
             (JSONL/JSON в каталоге telegram/);  режим по умолчанию;
  telethon — читать напрямую через Telethon, если библиотека установлена
             и заданы TELEGRAM_API_ID/TELEGRAM_API_HASH/TELEGRAM_SESSION;
  off      — источник выключен.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

from ..models import RawItem
from ..utils import collapse, content_hash
from .base import SourceAdapter, SourceResult

# Методы Telethon, запрещённые в этом проекте на уровне кода.
# Список используется тестами; фактический запрет обеспечивает белый
# список ReadOnlyTelegram.ALLOWED — всё, чего в нём нет, недоступно.
FORBIDDEN_TELETHON_METHODS = (
    "send_message", "send_file", "send_read_acknowledge",
    "edit_message", "delete_messages", "forward_messages",
    "join_chat", "leave_chat",
    "start", "sign_in", "sign_up", "log_out", "edit_2fa",
    "send_code_request", "upload_file", "set_profile_photo",
    "edit_admin", "edit_permissions", "pin_message",
    "session", "api_id", "api_hash",
)


class ReadOnlyTelegram:
    """
    Обёртка над Telethon-клиентом, физически закрывающая любые действия
    от имени пользователя.

    Разрешено ТОЛЬКО чтение уже существующей авторизованной сессии.
    Методов авторизации (`start`, `sign_in`, `log_out`) в белом списке нет:
    авторизацию выполняет человек вне этой системы. Сырой клиент наружу
    не отдаётся — доступ к нему возможен только через этот белый список.
    """

    # Полный белый список. Всё, чего здесь нет, запрещено.
    ALLOWED = frozenset({
        "iter_messages", "get_messages", "get_entity",
        "connect", "disconnect", "is_connected", "is_user_authorized",
    })

    __slots__ = ("_client",)

    def __init__(self, client: Any):
        object.__setattr__(self, "_client", client)

    def __getattribute__(self, name: str) -> Any:
        # Единственная точка доступа. Всё, чего нет в белом списке,
        # включая сам объект Telethon-клиента, недоступно снаружи.
        if name in ReadOnlyTelegram.ALLOWED:
            return getattr(object.__getattribute__(self, "_client"), name)
        raise PermissionError(
            f"Telegram-аккаунт работает только на чтение; доступ к '{name}' запрещён"
        )

    def __getattr__(self, name: str) -> Any:      # подстраховка
        raise PermissionError(
            f"Telegram-аккаунт работает только на чтение; доступ к '{name}' запрещён"
        )

    def __setattr__(self, name: str, value: Any) -> None:
        raise PermissionError("изменение Telegram-клиента запрещено")

    def __delattr__(self, name: str) -> None:
        raise PermissionError("изменение Telegram-клиента запрещено")

    def __repr__(self) -> str:            # сырой клиент не раскрываем
        return "<ReadOnlyTelegram read-only>"


def _iter_export_records(path: Path) -> Iterable[dict[str, Any]]:
    if path.suffix.lower() == ".jsonl":
        for line in path.read_text("utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if isinstance(record, dict):
                yield record
        return
    try:
        payload = json.loads(path.read_text("utf-8", errors="replace"))
    except (ValueError, OSError):
        return
    if isinstance(payload, list):
        for record in payload:
            if isinstance(record, dict):
                yield record
    elif isinstance(payload, dict):
        for key in ("messages", "items", "data"):
            value = payload.get(key)
            if isinstance(value, list):
                for record in value:
                    if isinstance(record, dict):
                        yield record
                return


def _pick(record: dict[str, Any], *names: str, default: str = "") -> str:
    for name in names:
        value = record.get(name)
        if value not in (None, ""):
            return str(value)
    return default


class TelegramSource(SourceAdapter):
    source_id = "telegram"
    source_type = "telegram"

    def fetch(self, days: int = 7) -> SourceResult:
        mode = (self.config.get("mode") or "export").lower()
        if mode == "off":
            return SourceResult(source_id=self.source_id, error="источник выключен в конфигурации")
        if mode == "telethon":
            return self._fetch_telethon(days)
        return self._fetch_export(days)

    # -- режим export -----------------------------------------------------
    def _fetch_export(self, days: int) -> SourceResult:
        result = SourceResult(source_id=self.source_id)
        base = Path(self.config.get("export_dir") or self.settings.telegram_dir)
        if not base.exists():
            result.error = (f"каталог Telegram-модуля не найден: {base}. "
                            "Источник будет работать после переноса в реальный репозиторий.")
            return result

        patterns = self.config.get("export_globs") or ["**/*.jsonl", "**/*.json"]
        files: list[Path] = []
        for pattern in patterns:
            files += [p for p in base.glob(pattern) if p.is_file()]
        files = sorted(set(files))
        if not files:
            result.error = f"в {base} нет JSON/JSONL-выгрузок Telegram"
            return result

        cutoff = datetime.now(timezone.utc) - timedelta(days=max(1, days))
        for path in files:
            result.fetched_pages += 1
            for record in _iter_export_records(path):
                text = collapse(_pick(record, "text", "message", "raw_text", "body"))
                if not text:
                    continue
                published = _pick(record, "date", "published_at", "timestamp")
                if published and not self._within(published, cutoff):
                    continue
                channel = _pick(record, "channel", "chat", "peer", "source",
                                default=path.stem)
                external = _pick(record, "id", "message_id", "external_id")
                item = RawItem(
                    source=f"tg:{channel}",
                    source_type="telegram",
                    source_url=_pick(record, "url", "link"),
                    external_id=str(external),
                    title=collapse(text.split("\n")[0])[:200],
                    raw_text=text,
                    published_at=published,
                    meta={"channel": channel, "export_file": path.name},
                )
                item.hash = content_hash(item.source, item.external_id, item.title, item.raw_text)
                result.items.append(item)
        return result

    @staticmethod
    def _within(published: str, cutoff: datetime) -> bool:
        try:
            value = datetime.fromisoformat(published.replace("Z", "+00:00"))
        except ValueError:
            return True                     # неизвестная дата не отбрасывается
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value >= cutoff

    # -- режим telethon ---------------------------------------------------
    def _fetch_telethon(self, days: int) -> SourceResult:
        result = SourceResult(source_id=self.source_id)
        api_id = os.environ.get("TELEGRAM_API_ID", "")
        api_hash = os.environ.get("TELEGRAM_API_HASH", "")
        session = os.environ.get("TELEGRAM_SESSION", "")
        channels = self.config.get("channels") or []
        if not (api_id and api_hash and session and channels):
            result.error = ("Telethon-режим не настроен: нужны TELEGRAM_API_ID, "
                            "TELEGRAM_API_HASH, TELEGRAM_SESSION и список channels")
            return result
        try:
            from telethon.sync import TelegramClient  # noqa: WPS433
        except ImportError:
            result.error = "Telethon не установлен — используйте режим export"
            return result

        cutoff = datetime.now(timezone.utc) - timedelta(days=max(1, days))
        client: Optional[ReadOnlyTelegram] = None
        try:
            # Клиент создаётся только для чтения уже существующей сессии.
            # Никакого процесса авторизации здесь нет и быть не должно.
            client = ReadOnlyTelegram(TelegramClient(session, int(api_id), api_hash))
            client.connect()
            if not client.is_user_authorized():
                result.error = ("Telegram-сессия не авторизована. "
                                "Авторизацию выполняет человек вне этой системы.")
                return result
            for channel in channels:
                result.fetched_pages += 1
                for message in client.iter_messages(channel, limit=int(self.config.get("limit", 100))):
                    text = collapse(getattr(message, "message", "") or "")
                    if not text:
                        continue
                    when = getattr(message, "date", None)
                    if when is not None:
                        when_utc = when.replace(tzinfo=timezone.utc) if when.tzinfo is None \
                            else when.astimezone(timezone.utc)
                        if when_utc < cutoff:
                            break
                    item = RawItem(
                        source=f"tg:{channel}",
                        source_type="telegram",
                        source_url=f"https://t.me/{str(channel).lstrip('@')}/{getattr(message, 'id', '')}",
                        external_id=str(getattr(message, "id", "")),
                        title=text.split("\n")[0][:200],
                        raw_text=text,
                        published_at=when.isoformat() if when else "",
                        meta={"channel": str(channel)},
                    )
                    item.hash = content_hash(item.source, item.external_id, item.title, item.raw_text)
                    result.items.append(item)
        except PermissionError as exc:
            result.error = f"нарушение read-only режима: {exc}"
        except Exception as exc:  # noqa: BLE001 - сбой источника не роняет конвейер
            result.error = f"Telethon: {exc}"
        finally:
            if client is not None:
                try:
                    client.disconnect()
                except Exception:  # noqa: BLE001
                    pass
        return result
