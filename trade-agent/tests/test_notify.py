"""
Доставка: привязка к выпуску, идемпотентность, неопределённый результат.

Проверяем главное: подтверждённая доставка не повторяется, сбой сборки
не приводит к отправке старого файла, а неясный результат сетевого вызова
не считается ни успехом, ни поводом для слепой повторной рассылки.
"""
import pytest

from trade_agent.bot import TelegramApiError, TelegramUncertain
from trade_agent.digest import content_hash
from trade_agent.models import (
    DELIVERY_SENT, DELIVERY_UNKNOWN, Issue, IssueItem, utcnow,
)
from trade_agent.notify import (
    deliver_issue, pack_posts, send_latest, target_chat_ids,
)

SAMPLE = """# Trade Agent — обзор торговли

Период: 09.09.2026 — 13.09.2026
Карточек в выпуске: 1

## Приморье → Филиппины

### Филиппины увеличили импорт трески

Импорт вырос на 20%.

- Отрасль: рыба и морепродукты
- Источник: https://bfar/1

## Что проверить или обсудить

- Филиппины увеличили импорт трески: запросить условия допуска
"""


class _Bot:
    """Подставной бот: сеть не используется."""

    def __init__(self, fail_at=None, exception=TelegramApiError):
        self.sent: list[tuple[int, str]] = []
        self.fail_at = fail_at
        self.exception = exception

    def send_post(self, chat_id, text):
        if self.fail_at is not None and len(self.sent) == self.fail_at:
            raise self.exception("сбой")
        self.sent.append((chat_id, text))
        return 1000 + len(self.sent)


def _issue(db, markdown=SAMPLE):
    issue = Issue(period_start=utcnow(), period_end=utcnow(),
                  counters={"cards": 1}, coverage={})
    issue.id = db.create_issue(issue)
    db.save_issue_items(int(issue.id), [IssueItem(section="RU_TO_PH", event_key="e1",
                                                  title="Треска")])
    db.finish_issue(int(issue.id), "built", {"cards": 1}, {}, "latest.md",
                    content_hash(markdown))
    return db.get_issue(int(issue.id))


# --- адресаты ---------------------------------------------------------------
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


# --- разбиение на посты -----------------------------------------------------
def test_short_issue_fits_into_one_or_two_posts():
    posts = pack_posts(SAMPLE)
    assert 1 <= len(posts) <= 2
    assert "Филиппины увеличили импорт трески" in posts[0]


def test_long_issue_is_split_by_blocks_not_mid_word():
    long_text = SAMPLE + "\n".join(f"### Карточка {i}\n\nТекст " + "x" * 500
                                   for i in range(20))
    posts = pack_posts(long_text, limit=1000)
    assert len(posts) > 1
    assert all(len(post) <= 1000 for post in posts)


def test_markdown_markers_are_cleaned():
    posts = pack_posts(SAMPLE)
    assert "###" not in posts[0]
    assert "- Отрасль" not in posts[0]      # маркеры списка заменены на «•»
    assert "• Отрасль" in posts[0]


# --- состояние доставки -----------------------------------------------------
def test_delivery_is_recorded_per_chat(settings, db):
    settings.bot.allowed_chat_ids = (22,)
    issue = _issue(db)
    bot = _Bot()
    deliveries = deliver_issue(settings, db, bot, issue, SAMPLE)
    assert [d.status for d in deliveries] == [DELIVERY_SENT]
    assert deliveries[0].parts_sent == deliveries[0].parts_total
    assert deliveries[0].message_ids
    stored = db.get_delivery(int(issue.id), "22")
    assert stored is not None and stored.confirmed


def test_confirmed_delivery_is_not_repeated(settings, db):
    settings.bot.allowed_chat_ids = (22,)
    issue = _issue(db)
    first = _Bot()
    deliver_issue(settings, db, first, issue, SAMPLE)
    second = _Bot()
    deliver_issue(settings, db, second, issue, SAMPLE)
    assert second.sent == []


def test_uncertain_result_is_not_retried_blindly(settings, db):
    """
    Запрос ушёл, ответ не получен — сообщение могло дойти.

    Такая доставка помечается неопределённой, и автоматическая повторная
    рассылка не выполняется.
    """
    settings.bot.allowed_chat_ids = (22,)
    issue = _issue(db)
    bot = _Bot(fail_at=0, exception=TelegramUncertain)
    deliveries = deliver_issue(settings, db, bot, issue, SAMPLE)
    assert deliveries[0].status == DELIVERY_UNKNOWN

    stored = db.get_delivery(int(issue.id), "22")
    stored.parts_sent = 1
    db.save_delivery(stored)
    again = _Bot()
    deliver_issue(settings, db, again, issue, SAMPLE)
    assert again.sent == []


def test_partial_delivery_resumes_from_the_missing_part(settings, db):
    settings.bot.allowed_chat_ids = (22,)
    long_text = SAMPLE + "\n".join(f"### Карточка {i}\n\nТекст " + "x" * 500
                                   for i in range(20))
    issue = _issue(db, long_text)
    first = _Bot(fail_at=1)
    deliveries = deliver_issue(settings, db, first, issue, long_text)
    assert deliveries[0].parts_sent == 1
    assert deliveries[0].status == "partial"

    second = _Bot()
    resumed = deliver_issue(settings, db, second, issue, long_text)
    assert resumed[0].status == DELIVERY_SENT
    assert second.sent          # досланы только недостающие части


# --- защита от отправки чужого файла ----------------------------------------
def test_failed_build_does_not_send_old_digest(settings, db):
    settings.bot.token = "token"
    settings.bot.allowed_chat_ids = (22,)
    _issue(db, SAMPLE)
    db.close()
    (settings.digest_dir).mkdir(parents=True, exist_ok=True)
    (settings.digest_dir / "latest.md").write_text("СТАРЫЙ ВЫПУСК", "utf-8")
    with pytest.raises(ValueError, match="не совпадает"):
        send_latest(settings)


def test_no_built_issue_means_no_delivery(settings, tmp_path):
    settings.bot.token = "token"
    settings.bot.allowed_chat_ids = (22,)
    settings.digest_dir.mkdir(parents=True, exist_ok=True)
    (settings.digest_dir / "latest.md").write_text(SAMPLE, "utf-8")
    with pytest.raises(ValueError, match="нет успешно собранного выпуска"):
        send_latest(settings)
