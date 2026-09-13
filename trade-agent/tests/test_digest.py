"""
Выпуск: единый состав, честные счётчики, два направления торговли.

Главное свойство, которое здесь проверяется: карточки, счётчики и список
компаний строятся из одного сохранённого набора issue_items. Шапка не
может противоречить содержимому.
"""
from trade_agent.db import Database
from trade_agent.digest import (
    IssueBuilder, SECTION_PH_TO_RU, SECTION_RU_TO_PH, content_hash,
    readable_title, write_digest,
)
from trade_agent.digest import run as digest_run
from trade_agent.models import (
    Analysis, Company, DIRECTION_PH_TO_RU, DIRECTION_RU_TO_PH, EVENT_DEMAND,
    EVENT_RULE_CHANGE, LINK_DIRECT, LINK_SECTOR, Match, RawItem, Review, Signal,
    SIGNAL_ANALYZED, SIGNAL_REJECTED, SourceState, utcnow,
)
from trade_agent.utils import content_hash as hash_content


def _raw(db, **kwargs):
    item = RawItem(**kwargs)
    item.hash = hash_content(item.source, item.external_id, item.title, item.raw_text)
    return db.upsert_raw_item(item)[0]


def _seed(db, settings):
    db.upsert_company(Company(slug="dalryba", name="Дальрыба", products=["треска"],
                              product_aliases=["треска", "cod"], hs_codes=["0303"],
                              sectors=["FISH_SEAFOOD"]))
    db.upsert_company(Company(slug="rybflot", name="Рыбфлот", products=["минтай"],
                              product_aliases=["минтай"], sectors=["FISH_SEAFOOD"]))
    db.save_source_state(SourceState(source_id="telegram", last_success_at=utcnow()))

    # 1. Прямое направление, подтверждённый вывод.
    forward_raw = _raw(db, source="bfar", source_type="web", external_id="f1",
                       title="Филиппины увеличили импорт трески",
                       raw_text="Импорт трески (HS 0303) вырос на 20%.",
                       source_url="https://bfar/1", published_at="2026-09-12T02:00:00Z")
    forward_id, _ = db.upsert_signal(Signal(
        raw_item_id=forward_raw, category="FOOD", relevance_score=4,
        reason="рост спроса влияет на конкуренцию для Приморья",
        trade_direction=DIRECTION_RU_TO_PH, event_type=EVENT_DEMAND,
        sectors=["FISH_SEAFOOD"], jurisdiction="PH", event_key="ev-forward",
        status=SIGNAL_ANALYZED))
    analysis = Analysis(signal_id=forward_id, company="Дальрыба",
                        summary="Филиппинский импорт трески вырос на 20%.",
                        opportunity="Окно для приморских поставщиков.",
                        confidence=0.8, next_step="запросить условия допуска")
    analysis.id = db.insert_analysis(analysis)
    db.insert_review(Review(analysis_id=analysis.id, verdict="PASS"))
    db.upsert_match(Match(company_slug="dalryba", signal_id=forward_id, match_score=4,
                          link_type=LINK_DIRECT, reason="в материале назван товар",
                          evidence=["импорт трески (HS 0303)"],
                          recommended_action="оценить влияние на поставки"))
    db.upsert_match(Match(company_slug="rybflot", signal_id=forward_id, match_score=2,
                          link_type=LINK_SECTOR, reason="отрасль совпала",
                          recommended_action="отраслевая связь: проверить применимость"))

    # 2. Обратное направление.
    back_raw = _raw(db, source="da", source_type="web", external_id="b1",
                    title="Филиппины нарастили экспорт кокосового масла в Россию",
                    raw_text="Поставки кокосового масла в РФ выросли.",
                    source_url="https://da/1", published_at="2026-09-11T02:00:00Z")
    back_id, _ = db.upsert_signal(Signal(
        raw_item_id=back_raw, category="FOOD", relevance_score=4,
        reason="филиппинский экспорт в РФ по нужной товарной группе",
        trade_direction=DIRECTION_PH_TO_RU, event_type=EVENT_DEMAND,
        sectors=["FOOD_AGRI"], event_key="ev-back", status=SIGNAL_ANALYZED))
    back_analysis = Analysis(signal_id=back_id, summary="Экспорт кокосового масла вырос.",
                             opportunity="Появился филиппинский поставщик.",
                             confidence=0.7)
    back_analysis.id = db.insert_analysis(back_analysis)
    db.insert_review(Review(analysis_id=back_analysis.id, verdict="PASS"))

    # 3. Шум — только в счётчике.
    noise_raw = _raw(db, source="tg:x", source_type="telegram", external_id="n1",
                     title="Шум", raw_text="розыгрыш призов")
    db.upsert_signal(Signal(raw_item_id=noise_raw, category="OTHER", relevance_score=0,
                            reason="явный шум по стоп-словам", status=SIGNAL_REJECTED,
                            event_key="ev-noise"))
    return {"forward": forward_id, "back": back_id}


def _build(db, settings, days=30):
    builder = IssueBuilder(db, settings)
    data = builder.collect(days)
    issue, items = builder.compose(data, days)
    return issue, items, builder.render(issue, items)


# --- формат -----------------------------------------------------------------
def test_issue_has_two_direction_sections(db, settings):
    _seed(db, settings)
    _, items, markdown = _build(db, settings)
    assert "## Приморье → Филиппины" in markdown
    assert "## Филиппины → РФ" in markdown
    sections = {item.section for item in items}
    assert SECTION_RU_TO_PH in sections and SECTION_PH_TO_RU in sections


def test_empty_sections_are_not_printed(db, settings):
    """Пустые разделы в выпуск не попадают."""
    db.save_source_state(SourceState(source_id="telegram", last_success_at=utcnow()))
    _, _, markdown = _build(db, settings)
    assert "## Приморье → Филиппины" not in markdown
    assert "За период не отобрано" in markdown


def test_counters_match_visible_cards(db, settings):
    """Числа в шапке совпадают с видимыми карточками."""
    _seed(db, settings)
    issue, items, markdown = _build(db, settings)
    visible = markdown.count("### ")
    assert issue.counters["cards"] == visible
    assert f"Карточек в выпуске: {visible}" in markdown


def test_internal_scores_are_not_shown(db, settings):
    """Баллы Scout, сырые подсказки HS и reason модели — в /status, не в выпуске."""
    _seed(db, settings)
    _, _, markdown = _build(db, settings)
    assert "оценка Scout" not in markdown
    assert "Предполагаемые HS" not in markdown
    assert "/status" in markdown


def test_noise_is_only_counted(db, settings):
    _seed(db, settings)
    issue, _, markdown = _build(db, settings)
    assert issue.counters["dropped_as_noise"] == 1
    assert "розыгрыш призов" not in markdown


# --- компании ---------------------------------------------------------------
def test_direct_and_sector_links_are_not_mixed(db, settings):
    _seed(db, settings)
    _, items, markdown = _build(db, settings)
    card = next(i for i in items if i.event_key == "ev-forward")
    assert [row["slug"] for row in card.companies_direct] == ["dalryba"]
    assert [row["slug"] for row in card.companies_sector] == ["rybflot"]
    assert "Прямая применимость:" in markdown
    assert "Компании отрасли:" in markdown
    assert "Применимость уточнить" in markdown


def test_full_company_list_is_available_by_command(db, settings):
    _seed(db, settings)
    _, items, markdown = _build(db, settings)
    card = next(i for i in items if i.event_key == "ev-forward")
    assert f"/companies {card.signal_id}" in markdown


# --- время и поздние подтверждения -----------------------------------------
def test_late_analysis_keeps_its_signal_and_original_date(db, settings):
    """
    Анализ старого сигнала, готовый сегодня, выходит один раз
    с исходной датой и пометкой о позднем подтверждении.
    """
    old_raw = _raw(db, source="bai", source_type="web", external_id="old",
                   title="BAI изменил требования к ввозу свинины",
                   raw_text="Требования к ввозу свинины изменены.",
                   source_url="https://bai/1", published_at="2026-08-01T02:00:00Z")
    old_signal = Signal(raw_item_id=old_raw, category="MEAT", relevance_score=4,
                        reason="изменение требований", trade_direction=DIRECTION_RU_TO_PH,
                        event_type=EVENT_RULE_CHANGE, sectors=["FOOD_MEAT"],
                        event_key="ev-old", status=SIGNAL_ANALYZED,
                        created_at="2026-08-01T02:00:00+00:00")
    old_id, _ = db.upsert_signal(old_signal)
    analysis = Analysis(signal_id=old_id, summary="BAI изменил требования.",
                        opportunity="Проверить соответствие.", confidence=0.7)
    analysis.id = db.insert_analysis(analysis)
    db.insert_review(Review(analysis_id=analysis.id, verdict="PASS"))
    db.save_source_state(SourceState(source_id="telegram", last_success_at=utcnow()))

    issue, items, markdown = _build(db, settings, days=2)
    card = next(i for i in items if i.event_key == "ev-old")
    assert card.late_confirmation is True
    assert issue.counters["late_confirmations"] == 1
    assert "01.08.2026" in markdown
    assert "позднее подтверждение" in markdown
    assert markdown.count("BAI изменил требования к ввозу свинины") == 1


def test_two_reposts_of_one_event_give_one_card(db, settings):
    db.save_source_state(SourceState(source_id="telegram", last_success_at=utcnow()))
    for index, source in enumerate(("tg:a", "tg:b"), start=1):
        raw_id = _raw(db, source=source, source_type="telegram", external_id=f"r{index}",
                      title="Филиппины отменили пошлину на рыбу",
                      raw_text=f"Пошлина отменена. Перепечатка {index}.",
                      source_url=f"https://t.me/{source}/{index}",
                      published_at="2026-09-12T02:00:00Z")
        db.upsert_signal(Signal(
            raw_item_id=raw_id, category="REGULATION", relevance_score=4,
            reason="отмена пошлины", trade_direction=DIRECTION_RU_TO_PH,
            event_type=EVENT_RULE_CHANGE, sectors=["FISH_SEAFOOD"],
            event_key="ev-same", status=SIGNAL_ANALYZED))
    issue, items, markdown = _build(db, settings)
    cards = [i for i in items if i.event_key == "ev-same"]
    assert len(cards) == 1
    assert len(cards[0].source_urls) == 2
    assert issue.counters["cards"] == 1


# --- охват сбора ------------------------------------------------------------
def test_unavailable_source_is_named_not_hidden(db, settings):
    db.save_source_state(SourceState(source_id="telegram", last_success_at=utcnow()))
    db.save_source_state(SourceState(source_id="bfar", last_error="TLS error",
                                     coverage_complete=False))
    _, _, markdown = _build(db, settings)
    assert "недоступны: bfar" in markdown
    assert "Часть источников недоступна" in markdown


def test_tenders_are_not_declared_empty_when_not_checked(db, settings):
    db.save_source_state(SourceState(source_id="telegram", last_success_at=utcnow()))
    _, _, markdown = _build(db, settings)
    assert "закупки в этом выпуске не проверялись" in markdown
    # Утверждения «подходящих закупок за период нет» быть не должно:
    # закупки не проверялись, а это разные вещи.
    assert "Подходящих закупок за период нет" not in markdown
    assert "## Тендеры" not in markdown


# --- заголовки и файлы ------------------------------------------------------
def test_emoji_title_is_replaced_by_readable_text():
    item = RawItem(title="🔥🚨", raw_text="Филиппины отменили пошлину на рыбу. Подробности ниже.")
    assert readable_title(item).startswith("Филиппины отменили пошлину")


def test_write_digest_creates_latest_and_archive(settings, tmp_path):
    from datetime import date

    paths = write_digest("# test", tmp_path / "d", date(2026, 9, 1))
    assert (tmp_path / "d" / "latest.md").exists()
    assert (tmp_path / "d" / "archive" / "2026-09-01.md").exists()
    assert paths["written"] == "yes"


def test_dry_run_writes_nothing(settings, tmp_path):
    from datetime import date

    write_digest("# test", tmp_path / "d", date(2026, 9, 1), dry_run=True)
    assert not (tmp_path / "d" / "latest.md").exists()


def test_write_digest_does_not_leave_temporary_files(settings, tmp_path):
    from datetime import date

    directory = tmp_path / "d"
    write_digest("# atomic", directory, date(2026, 9, 1))
    assert not list(directory.glob(".*.tmp"))
    assert (directory / "latest.md").read_text("utf-8") == "# atomic"


# --- сохранение состава -----------------------------------------------------
def test_issue_and_items_are_stored(db, settings):
    _seed(db, settings)
    db.close()
    stats = digest_run(settings, days=30)
    database = Database(settings.db_path)
    try:
        issue = database.latest_issue("built")
        assert issue is not None
        items = database.issue_items(int(issue.id))
        assert len(items) == stats["cards"] + stats["watch"]
        markdown = (settings.digest_dir / "latest.md").read_text("utf-8")
        assert issue.content_hash == content_hash(markdown)
        runs = database.recent_runs(1, stage="digest")
        assert runs and runs[0].status == "ok"
    finally:
        database.close()
    assert stats["errors"] == 0
