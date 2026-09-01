"""
Telegram-бот: whitelist, только личный чат, ok:false, устойчивый offset.

Сеть не используется: HTTP-сессия подменена фейковой.
Ни одно сообщение реально не отправляется.
"""
import json

import pytest
import requests

from trade_agent.bot import TelegramApiError, TelegramBot

OWNER = 111
STRANGER = 222
GROUP_CHAT = -100500


class _Response:
    def __init__(self, payload, status_code=200, text=""):
        self._payload = payload
        self.status_code = status_code
        self._text = text

    def json(self):
        if self._payload is None:
            raise ValueError("не JSON")
        return self._payload


class _FakeSession:
    """Фейковая сессия: очередь ответов + журнал вызовов."""

    def __init__(self, responses=None):
        self.responses = list(responses or [])
        self.calls = []
        self.raise_network = False

    def post(self, url, json=None, timeout=None):
        self.calls.append({"url": url, "payload": json})
        if self.raise_network:
            raise requests.RequestException(f"connection refused to {url}")
        if self.responses:
            return self.responses.pop(0)
        return _Response({"ok": True, "result": []})

    def sent_messages(self):
        return [c["payload"] for c in self.calls if c["url"].endswith("/sendMessage")]


def _update(update_id, user_id=OWNER, chat_id=OWNER, chat_type="private", text="/status"):
    return {
        "update_id": update_id,
        "message": {
            "message_id": update_id,
            "from": {"id": user_id},
            "chat": {"id": chat_id, "type": chat_type},
            "text": text,
        },
    }


def _bot(settings, session):
    settings.bot.token = "123456:TEST-TOKEN-DO-NOT-USE"
    settings.bot.allowed_user_ids = (OWNER,)
    settings.bot.allowed_chat_ids = ()
    settings.bot.offset_path = settings.db_path.parent / "bot_offset.json"
    settings.bot.poll_timeout = 0
    return TelegramBot(settings, session=session)


# --- whitelist и тип чата ---------------------------------------------------
def test_allowed_user_in_private_chat_is_served(settings):
    session = _FakeSession([_Response({"ok": True, "result": [_update(1)]})])
    bot = _bot(settings, session)
    assert bot.poll_once() == 1
    messages = session.sent_messages()
    assert len(messages) == 1 and messages[0]["chat_id"] == OWNER


def test_same_user_in_group_chat_is_ignored(settings):
    session = _FakeSession([_Response(
        {"ok": True, "result": [_update(1, user_id=OWNER, chat_id=GROUP_CHAT,
                                        chat_type="supergroup")]})])
    bot = _bot(settings, session)
    assert bot.poll_once() == 0
    assert session.sent_messages() == []


def test_channel_post_is_ignored(settings):
    session = _FakeSession([_Response(
        {"ok": True, "result": [_update(1, chat_id=GROUP_CHAT, chat_type="channel")]})])
    assert _bot(settings, session).poll_once() == 0
    assert session.sent_messages() == []


def test_stranger_is_ignored_and_gets_no_reply(settings):
    session = _FakeSession([_Response(
        {"ok": True, "result": [_update(1, user_id=STRANGER, chat_id=STRANGER)]})])
    assert _bot(settings, session).poll_once() == 0
    assert session.sent_messages() == []


def test_bot_does_not_reply_into_foreign_chat(settings):
    """user_id разрешён, но чат — чужой: ответа быть не должно."""
    session = _FakeSession([_Response(
        {"ok": True, "result": [_update(1, user_id=OWNER, chat_id=999999)]})])
    assert _bot(settings, session).poll_once() == 0
    assert session.sent_messages() == []


def test_explicit_chat_whitelist_is_respected(settings):
    session = _FakeSession([_Response({"ok": True, "result": [_update(1)]})])
    settings.bot.allowed_chat_ids = (424242,)
    bot = _bot(settings, session)
    bot.allowed_chats = {424242}
    assert bot.poll_once() == 0
    assert session.sent_messages() == []


# --- ошибки Telegram --------------------------------------------------------
def test_ok_false_with_http_200_is_an_error(settings):
    session = _FakeSession([_Response({"ok": False, "description": "Unauthorized"})])
    bot = _bot(settings, session)
    with pytest.raises(TelegramApiError) as exc:
        bot.poll_once()
    assert "ok=false" in str(exc.value)


def test_non_json_response_is_an_error(settings):
    session = _FakeSession([_Response(None)])
    with pytest.raises(TelegramApiError):
        _bot(settings, session).poll_once()


def test_http_error_is_an_error(settings):
    session = _FakeSession([_Response({"ok": True}, status_code=500)])
    with pytest.raises(TelegramApiError):
        _bot(settings, session).poll_once()


def test_token_never_leaks_into_error_text(settings):
    session = _FakeSession()
    session.raise_network = True
    bot = _bot(settings, session)
    with pytest.raises(TelegramApiError) as exc:
        bot.poll_once()
    assert bot.token not in str(exc.value)
    assert "redacted" in str(exc.value)


def test_send_failure_does_not_acknowledge_update(settings):
    session = _FakeSession([
        _Response({"ok": True, "result": [_update(1), _update(2)]}),
        _Response({"ok": False, "description": "chat not found"}),   # первый sendMessage
    ])
    bot = _bot(settings, session)
    assert bot.poll_once() == 0        # команда не подтверждается до успешной отправки
    assert bot.offset == 0


def test_long_single_line_is_split_without_empty_or_oversized_chunks(settings):
    bot = _bot(settings, _FakeSession())
    chunks = bot._split("x" * 10000)
    assert chunks
    assert all(0 < len(chunk) <= 3800 for chunk in chunks)
    assert "".join(chunks) == "x" * 10000


def test_handle_none_is_treated_as_empty_text(settings):
    bot = _bot(settings, _FakeSession())
    assert "Неизвестная команда" in bot.handle(None)


def test_offset_save_failure_is_not_silent(settings, tmp_path):
    offset_path = tmp_path / "not-a-directory" / "offset.json"
    offset_path.parent.write_text("file", "utf-8")
    bot = _bot(settings, _FakeSession())
    bot.offset_path = offset_path
    with pytest.raises(TelegramApiError):
        bot._save_offset(10)


def test_offset_is_not_advanced_when_persistence_fails(settings, tmp_path):
    session = _FakeSession([_Response({"ok": True, "result": [_update(7)]})])
    bot = _bot(settings, session)
    bot.offset_path = tmp_path / "not-a-directory" / "offset.json"
    bot.offset_path.parent.write_text("file", "utf-8")
    with pytest.raises(TelegramApiError):
        bot.poll_once()
    assert bot.offset == 0


# --- offset -----------------------------------------------------------------
def test_offset_is_persisted_atomically(settings):
    session = _FakeSession([_Response({"ok": True, "result": [_update(7)]})])
    bot = _bot(settings, session)
    bot.poll_once()
    payload = json.loads(bot.offset_path.read_text("utf-8"))
    assert payload["offset"] == 8
    assert not list(bot.offset_path.parent.glob(".bot_offset.*.tmp"))


def test_restart_does_not_reprocess_old_update(settings):
    session = _FakeSession([_Response({"ok": True, "result": [_update(7)]})])
    first = _bot(settings, session)
    first.poll_once()

    session2 = _FakeSession([_Response({"ok": True, "result": []})])
    second = _bot(settings, session2)
    assert second.offset == 8                       # прочитан с диска
    assert second.poll_once() == 0
    assert session2.calls[0]["payload"]["offset"] == 8


def test_offset_advances_even_for_rejected_updates(settings):
    """Чужое сообщение не должно возвращаться при каждом опросе."""
    session = _FakeSession([_Response(
        {"ok": True, "result": [_update(5, user_id=STRANGER, chat_id=STRANGER)]})])
    bot = _bot(settings, session)
    bot.poll_once()
    assert bot.offset == 6
    assert json.loads(bot.offset_path.read_text("utf-8"))["offset"] == 6


def test_corrupt_offset_file_does_not_crash(settings):
    path = settings.db_path.parent / "bot_offset.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{сломано", "utf-8")
    session = _FakeSession([_Response({"ok": True, "result": []})])
    assert _bot(settings, session).offset == 0


def test_drain_backlog_skips_old_updates_on_first_start(settings):
    """Первый запуск: накопившиеся обновления пропускаются, а не выполняются."""
    session = _FakeSession([_Response({"ok": True, "result": [_update(42)]})])
    bot = _bot(settings, session)
    assert bot.drain_backlog() == 43
    assert session.sent_messages() == []
    assert json.loads(bot.offset_path.read_text("utf-8"))["offset"] == 43


def test_drain_backlog_is_noop_when_offset_known(settings):
    session = _FakeSession([_Response({"ok": True, "result": [_update(7)]})])
    bot = _bot(settings, session)
    bot.poll_once()
    calls_before = len(session.calls)
    assert bot.drain_backlog() == 8
    assert len(session.calls) == calls_before      # лишнего запроса не было


# --- команды ----------------------------------------------------------------
def test_unknown_command_gets_help(settings):
    session = _FakeSession()
    assert "/status" in _bot(settings, session).handle("/nope")


def test_status_command_works_offline(settings):
    session = _FakeSession()
    text = _bot(settings, session).handle("/status")
    assert "Состояние системы" in text
    assert settings.bot.token not in text
