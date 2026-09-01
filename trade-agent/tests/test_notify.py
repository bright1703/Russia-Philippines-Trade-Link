import pytest

from trade_agent.notify import send_latest, target_chat_ids


class _Bot:
    def __init__(self, settings):
        self.sent = []

    def send(self, chat_id, text):
        self.sent.append((chat_id, text))


def test_target_chat_ids_prefers_explicit_chat_ids(settings):
    settings.bot.allowed_user_ids = (11,)
    settings.bot.allowed_chat_ids = (22,)
    assert target_chat_ids(settings) == (22,)


def test_target_chat_ids_uses_private_user_ids(settings):
    settings.bot.allowed_user_ids = (11,)
    settings.bot.allowed_chat_ids = ()
    assert target_chat_ids(settings) == (11,)


def test_send_latest_requires_delivery_credentials(settings, tmp_path):
    path = tmp_path / "latest.md"
    path.write_text("news", "utf-8")
    settings.bot.token = ""
    settings.bot.allowed_user_ids = ()
    with pytest.raises(ValueError):
        send_latest(settings, path)


def test_send_latest_sends_to_allowed_chats(settings, tmp_path, monkeypatch):
    path = tmp_path / "latest.md"
    path.write_text("news", "utf-8")
    settings.bot.token = "token"
    settings.bot.allowed_user_ids = (11,)
    settings.bot.allowed_chat_ids = (22,)
    fake = _Bot(settings)
    monkeypatch.setattr("trade_agent.notify.TelegramBot", lambda s: fake)
    assert send_latest(settings, path) == 1
    assert fake.sent == [(22, "news")]
