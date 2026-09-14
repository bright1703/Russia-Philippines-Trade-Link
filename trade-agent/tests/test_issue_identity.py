"""
Тождество выпуска и повторная публикация события.

Проверяются два дефекта, найденные при приёмочной проверке:

1. Повторный запуск сборки создавал НОВЫЙ выпуск с тем же составом.
   Доставка привязана к идентификатору выпуска, поэтому такой двойник
   приводил к повторной отправке уже доставленного обзора.

2. «Существенным обновлением» считалась высокая оценка значимости,
   поэтому событие с оценкой 5 выходило снова в каждом выпуске.
   Важность события — не его обновление.
"""
from pathlib import Path

from trade_agent.db import Database
from trade_agent.digest import run as digest_run
from trade_agent.events import changed_fields, fingerprint_fields, update_fingerprint
from trade_agent.models import (
    DIRECTION_RU_TO_PH, EVENT_RULE_CHANGE, RawItem, Signal, SIGNAL_ANALYZED,
    SourceState, utcnow,
)
from trade_agent.utils import content_hash


def _seed(db, effective_from: str = "2026-10-01", score: int = 5) -> int:
    db.save_source_state(SourceState(source_id="telegram", last_success_at=utcnow()))
    item = RawItem(source="bfar", source_type="web",
                   title="Филиппины отменили пошлину на мороженую рыбу",
                   raw_text="Пошлина отменена приказом ведомства.",
                   source_url="https://bfar/1", published_at=utcnow())
    item.hash = content_hash(item.source, item.external_id, item.title, item.raw_text)
    raw_id, _ = db.upsert_raw_item(item)
    signal_id, _ = db.upsert_signal(Signal(
        raw_item_id=raw_id, category="REGULATION", relevance_score=score,
        reason="отмена пошлины", trade_direction=DIRECTION_RU_TO_PH,
        event_type=EVENT_RULE_CHANGE, sectors=["FISH_SEAFOOD"], jurisdiction="PH",
        effective_from=effective_from, event_key="ev-duty", status=SIGNAL_ANALYZED))
    return signal_id


def _issues(settings) -> list[tuple[int, str, str]]:
    db = Database(settings.db_path)
    try:
        rows = db.conn.execute(
            "SELECT id, status, content_hash FROM issues ORDER BY id").fetchall()
        return [(int(r["id"]), r["status"], r["content_hash"]) for r in rows]
    finally:
        db.close()


# --- 1. тождество выпуска ---------------------------------------------------
def test_rebuild_with_same_content_reuses_the_issue(db, settings):
    """Повторная сборка того же состава не создаёт выпуск-двойник."""
    _seed(db)
    db.close()

    first = digest_run(settings, days=7)
    second = digest_run(settings, days=7)     # событие уже опубликовано, состав пуст
    third = digest_run(settings, days=7)      # состав совпадает со вторым

    assert first["reused_issue"] is False
    assert third["reused_issue"] is True
    assert third["issue_id"] == second["issue_id"]

    issues = _issues(settings)
    hashes = [row[2] for row in issues]
    assert len(hashes) == len(set(hashes)), "выпуски с одинаковым составом продублированы"


def test_reused_issue_keeps_confirmed_delivery(db, settings):
    """
    Доставленный выпуск не отправляется повторно после пересборки.

    Раньше пересборка давала новый issue_id, и доставка считала обзор
    новым, хотя человек его уже получил.
    """
    from trade_agent.models import Delivery
    from trade_agent.notify import deliver_issue

    _seed(db)
    db.close()

    digest_run(settings, days=7)
    digest_run(settings, days=7)
    stats = digest_run(settings, days=7)
    issue_id = stats["issue_id"]

    settings.bot.allowed_chat_ids = (22,)
    database = Database(settings.db_path)
    try:
        issue = database.get_issue(int(issue_id))
        markdown = Path(settings.digest_dir / "latest.md").read_text("utf-8")

        class _Bot:
            def __init__(self):
                self.sent = []

            def send_post(self, chat_id, text):
                self.sent.append(text)
                return len(self.sent)

        first_bot = _Bot()
        deliver_issue(settings, database, first_bot, issue, markdown)
        assert first_bot.sent, "первая доставка должна пройти"

        # Пересборка без изменений возвращает тот же выпуск.
        again = digest_run(settings, days=7)
        assert again["issue_id"] == issue_id
        assert again["reused_issue"] is True

        second_bot = _Bot()
        deliver_issue(settings, database, second_bot,
                      database.get_issue(int(issue_id)), markdown)
        assert second_bot.sent == [], "подтверждённая доставка повторилась"
    finally:
        database.close()


# --- 2. что считается существенным обновлением ------------------------------
def test_high_score_alone_is_not_an_update(db, settings):
    """Оценка 5 сама по себе не делает событие обновлённым."""
    _seed(db, score=5)
    db.close()

    first = digest_run(settings, days=7)
    second = digest_run(settings, days=7)
    assert first["cards"] == 1
    assert second["cards"] == 0
    assert second["repeats_skipped"] == 1


def test_changed_effective_date_republishes_with_explanation(db, settings):
    """Существенное обновление выходит снова и объясняет, что изменилось."""
    signal_id = _seed(db, effective_from="2026-10-01")
    db.close()

    digest_run(settings, days=7)
    digest_run(settings, days=7)

    database = Database(settings.db_path)
    try:
        database.update_signal_fields(signal_id, effective_from="2026-11-15")
    finally:
        database.close()

    updated = digest_run(settings, days=7)
    assert updated["cards"] == 1
    markdown = Path(settings.digest_dir / "latest.md").read_text("utf-8")
    assert "обновление ранее опубликованного события" in markdown
    assert "2026-10-01" in markdown and "2026-11-15" in markdown


def test_issue_item_stores_fingerprint(db, settings):
    """Отпечаток события сохраняется вместе с карточкой."""
    _seed(db)
    db.close()
    stats = digest_run(settings, days=7)

    database = Database(settings.db_path)
    try:
        items = database.issue_items(int(stats["issue_id"]))
        assert items and items[0].fingerprint
        assert items[0].fingerprint["effective_from"] == "2026-10-01"
    finally:
        database.close()


# --- отпечаток события ------------------------------------------------------
def test_fingerprint_ignores_relevance_and_wording():
    item = RawItem(title="t", raw_text="x")
    base = Signal(event_type=EVENT_RULE_CHANGE, trade_direction=DIRECTION_RU_TO_PH,
                  jurisdiction="PH", effective_from="2026-10-01", relevance_score=3,
                  reason="первый пересказ")
    louder = Signal(event_type=EVENT_RULE_CHANGE, trade_direction=DIRECTION_RU_TO_PH,
                    jurisdiction="PH", effective_from="2026-10-01", relevance_score=5,
                    reason="другой пересказ того же события")
    assert update_fingerprint(base, item) == update_fingerprint(louder, item)
    assert changed_fields(fingerprint_fields(base, item),
                          fingerprint_fields(louder, item)) == []


def test_changed_fields_speaks_plainly():
    was = {"effective_from": "2026-10-01", "deadline": ""}
    now = {"effective_from": "2026-11-15", "deadline": "2026-12-01"}
    changes = changed_fields(was, now)
    assert any("дата вступления в силу" in line and "2026-11-15" in line
               for line in changes)
    assert any("срок" in line and "появилось" in line for line in changes)
