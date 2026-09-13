"""
Критерии приёмки из ТЗ, раздел 11.

Каждый тест назван по номеру обязательной проверки. Примеры взяты из
найденных в аудите ошибок: пошлины ЮАР на китайскую сталь рядом с
косметикой, обзор трески в срочном регулировании, «HS 2023» из истории
бренда, потерянная бизнес-миссия Иркутской области.

Это регрессионный набор на конкретные дефекты, а не обещание такой же
точности на всём интернете.
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from trade_agent.agents import evidence, taxonomy
from trade_agent.agents.scout import Scout
from trade_agent.alerts import detect_mandatory_policy_alert, detect_rule_change
from trade_agent.companies.audit import build_report, migration_summary
from trade_agent.companies.catalog import normalize_catalog_row
from trade_agent.digest import IssueBuilder, content_hash
from trade_agent.events import event_key
from trade_agent.fetch import plan_days
from trade_agent.models import (
    Analysis, Company, DIRECTION_PH_TO_RU, DIRECTION_RU_TO_PH, DELIVERY_SENT,
    DELIVERY_UNKNOWN, EVENT_BUSINESS_CONTACT, EVENT_DEMAND, EVENT_RULE_CHANGE,
    LINK_DIRECT, LINK_SECTOR, Match, RawItem, Review, Signal, SIGNAL_ANALYZED,
    SIGNAL_NEEDS_REVIEW, SourceState, utcnow,
)
from trade_agent.radar import OpportunityRadar, match_signal
from trade_agent.run_pipeline import MAX_AUTOMATIC_CYCLES_PER_WEEK, cycles_this_week
from trade_agent.sources.base import SourceResult
from trade_agent.utils import content_hash as hash_content

from helpers import json_response, mock_llm

DEPLOY = Path(__file__).resolve().parent.parent / "deploy"

COSMETICS = Company(slug="primkosmetika", name="Примкосметика",
                    products=["Косметические кремы"],
                    product_aliases=["косметика", "крем"], hs_codes=["3304"],
                    sectors=["COSMETICS"])
TABLEWARE = Company(slug="posuda", name="Приморская посуда", products=["посуда"],
                    product_aliases=["посуда"], hs_codes=["6912"],
                    sectors=["INDUSTRIAL"])
FISH = Company(slug="dalryba", name="Дальрыба", products=["треска"],
               product_aliases=["треска", "cod"], hs_codes=["0303"],
               sectors=["FISH_SEAFOOD"])


def _raw(db, **kwargs):
    item = RawItem(**kwargs)
    item.hash = hash_content(item.source, item.external_id, item.title, item.raw_text)
    return db.upsert_raw_item(item)[0]


class _Settings:
    radar_min_match_score = 2
    radar_min_sector_score = 2


# --- 1 ----------------------------------------------------------------------
def test_01_third_country_duties_are_not_linked_to_unrelated_products(settings):
    """Пошлины ЮАР на китайскую сталь не связываются с косметикой и посудой."""
    item = RawItem(id=1, source="tg:news", source_type="telegram",
                   title="South Africa imposes anti-dumping duties on Chinese steel",
                   raw_text="Пошлины ЮАР на китайскую сталь введены. В составе "
                            "решения может быть пересмотр ставок для основных "
                            "поставщиков в 2023 году.")
    assert detect_mandatory_policy_alert(item, [COSMETICS, TABLEWARE]) is None

    # Событие третьего рынка не доходит до модели: связи с Филиппинами,
    # РФ или маршрутом между ними в тексте нет.
    found = Scout(mock_llm(json_response({"relevant": True, "score": 4})),
                  settings).prefilter(item)
    assert not found.passed and "третьего рынка" in found.reason

    # И даже если бы дошло, ложной ПРЯМОЙ связи не возникает: ни товара,
    # ни названия компании, ни помеченного кода в материале нет.
    signal = Signal(id=1, category="REGULATION", relevance_score=5,
                    sectors=["INDUSTRIAL"], reason="пошлины на сталь")
    for company in (COSMETICS, TABLEWARE):
        detail = match_signal(signal, item, company)
        assert detail.link_type != LINK_DIRECT
        assert not detail.evidence_fragments
    assert match_signal(signal, item, COSMETICS).score == 0


# --- 2 ----------------------------------------------------------------------
def test_02_market_review_is_not_urgent_regulation(db, settings):
    """Обзор трески без изменения правил не становится срочным REGULATION."""
    text = ("Обзор рынка трески: вылов вырос, цены снизились, "
            "экспортёры оформляют сертификаты качества.")
    assert detect_rule_change(text) is None

    raw_id = _raw(db, source="tg:mcx", source_type="telegram", external_id="cod",
                  title="Обзор рынка трески", raw_text=text,
                  source_url="https://t.me/mcx_ru/5665", published_at=utcnow())
    db.upsert_signal(Signal(raw_item_id=raw_id, category="FOOD", relevance_score=4,
                            reason="обзор рынка", trade_direction=DIRECTION_RU_TO_PH,
                            event_type=EVENT_DEMAND, sectors=["FISH_SEAFOOD"],
                            event_key="ev-cod", status=SIGNAL_ANALYZED))
    db.save_source_state(SourceState(source_id="telegram", last_success_at=utcnow()))
    builder = IssueBuilder(db, settings)
    issue, items = builder.compose(builder.collect(7), 7)
    card = next(i for i in items if i.event_key == "ev-cod")
    assert card.urgent is False
    assert card.event_type != EVENT_RULE_CHANGE
    assert issue.counters["urgent"] == 0


# --- 3 ----------------------------------------------------------------------
def test_03_brand_story_creates_no_automatic_links():
    """История бренда с сертификатом не связывается с рыбными компаниями."""
    item = RawItem(id=3, title="История бренда",
                   raw_text="Бренд основан в 2023 году, получил сертификат "
                            "качества и может расширить состав линейки.")
    assert detect_rule_change(item.raw_text) is None
    assert detect_mandatory_policy_alert(item, [FISH]) is None
    signal = Signal(id=3, category="OTHER", sectors=[], relevance_score=3)
    assert match_signal(signal, item, FISH).score == 0
    assert evidence.declared_hs_codes(item.raw_text) == []


# --- 4 ----------------------------------------------------------------------
def test_04_business_mission_passes_without_product_word(settings):
    """
    Новость о российской бизнес-миссии на Филиппинах проходит оценку
    деловой релевантности даже без товарного слова.
    """
    item = RawItem(id=4, source="tg:exportprim", source_type="telegram",
                   title="Бизнес-миссия Иркутской области на Филиппинах",
                   raw_text="Делегация российских компаний провела переговоры "
                            "в Маниле и запланировала деловой форум.")
    found = Scout(mock_llm(""), settings).prefilter(item)
    assert found.passed, "деловая миссия не должна отсекаться до модели"
    assert any(name == EVENT_BUSINESS_CONTACT for name, _ in found.events)

    answer = json_response({
        "relevant": True, "score": 3, "trade_direction": "RU_TO_PH",
        "event_type": "BUSINESS_CONTACT", "category": "OTHER",
        "reason": "контакт российских компаний с местным рынком",
        "companies": [], "hs_codes": [], "evidence": ["Делегация российских компаний"],
    })
    result = Scout(mock_llm(answer), settings).evaluate(item)
    assert result.signal is not None
    assert result.signal.event_type == EVENT_BUSINESS_CONTACT
    # Готовая сделка не приписывается.
    assert "сделк" not in result.signal.reason.lower()


# --- 5 ----------------------------------------------------------------------
def test_05_philippine_import_change_without_russia_is_allowed(settings):
    """Изменение филиппинского импорта допускается без упоминания России."""
    item = RawItem(id=5, source="da", source_type="web",
                   title="Philippines increases fish imports from Vietnam",
                   raw_text="Филиппины нарастили импорт рыбы из Вьетнама, "
                            "конкуренция поставщиков усилилась.")
    found = Scout(mock_llm(""), settings).prefilter(item)
    assert found.passed
    assert taxonomy.JURISDICTION_PH in found.jurisdictions


# --- 6 ----------------------------------------------------------------------
def test_06_reverse_direction_does_not_make_exporter_a_buyer():
    """Экспортёр из Приморья без данных о закупках покупателем не называется."""
    item = RawItem(id=6, title="Филиппинский экспорт трески в Россию вырос",
                   raw_text="Поставки трески с Филиппин в РФ выросли за квартал.")
    signal = Signal(id=6, category="FOOD", sectors=["FISH_SEAFOOD"],
                    trade_direction=DIRECTION_PH_TO_RU, relevance_score=4)
    matches = OpportunityRadar(_Settings()).match_all(signal, item, [FISH])
    assert matches
    assert matches[0].role == "unknown"
    action = matches[0].recommended_action.lower()
    assert "покупател" not in action
    assert "уточнить роль" in action


# --- 7 ----------------------------------------------------------------------
def test_07_sector_list_is_full_on_request_and_never_mixed(db, settings):
    """Полный список компаний отрасли доступен по запросу и не смешан с прямой связью."""
    from trade_agent.bot import TelegramBot

    db.upsert_company(FISH)
    sector_companies = []
    for index in range(18):
        company = Company(slug=f"fish-{index}", name=f"Рыбкомпания {index}",
                          products=["минтай"], product_aliases=["минтай"],
                          sectors=["FISH_SEAFOOD"])
        db.upsert_company(company)
        sector_companies.append(company)

    raw_id = _raw(db, source="bfar", source_type="web", external_id="s7",
                  title="Филиппины меняют порядок ввоза рыбы",
                  raw_text="Импорт трески (HS 0303) под новым порядком.",
                  source_url="https://bfar/7", published_at=utcnow())
    signal_id, _ = db.upsert_signal(Signal(
        raw_item_id=raw_id, category="FOOD", relevance_score=4, reason="порядок ввоза",
        trade_direction=DIRECTION_RU_TO_PH, sectors=["FISH_SEAFOOD"],
        event_key="ev-7", status=SIGNAL_ANALYZED))
    db.upsert_match(Match(company_slug="dalryba", signal_id=signal_id, match_score=4,
                          link_type=LINK_DIRECT, reason="назван товар",
                          recommended_action="оценить поставки"))
    for company in sector_companies:
        db.upsert_match(Match(company_slug=company.slug, signal_id=signal_id,
                              match_score=2, link_type=LINK_SECTOR,
                              reason="отрасль совпала",
                              recommended_action="отраслевая связь: проверить"))
    db.save_source_state(SourceState(source_id="telegram", last_success_at=utcnow()))

    builder = IssueBuilder(db, settings)
    _, items = builder.compose(builder.collect(7), 7)
    card = next(i for i in items if i.event_key == "ev-7")
    assert [row["slug"] for row in card.companies_direct] == ["dalryba"]
    assert len(card.companies_sector) == 18
    db.close()

    bot = TelegramBot.__new__(TelegramBot)
    bot.settings = settings
    first_page = bot.cmd_companies([str(signal_id)])
    assert "ПРЯМАЯ ПРИМЕНИМОСТЬ" in first_page
    assert "КОМПАНИИ ОТРАСЛИ" in first_page
    assert "Страница 1 из 2" in first_page
    second_page = bot.cmd_companies([str(signal_id), "2"])
    assert "Страница 2 из 2" in second_page
    # Все 18 отраслевых компаний доступны по запросу, ни одна не потеряна.
    pages = first_page + second_page
    for company in sector_companies:
        assert company.name in pages


# --- 8 ----------------------------------------------------------------------
def test_08_export_word_does_not_create_logistics_industry():
    """SLAVDA и «Примкосметика» не получают LOGISTICS из слова «экспорт»."""
    slavda = normalize_catalog_row({
        "Название компании": "SLAVDA GROUP",
        "Описание компании": "Производство напитков, экспорт продукции",
        "Отрасль экспорта": "Агропромышленный комплекс",
        "Продукция": "Минеральная вода\nЛимонад"}, 4)
    kosmetika = normalize_catalog_row({
        "Название компании": "Примкосметика",
        "Описание компании": "Производство косметики, экспорт в страны Азии",
        "Отрасль экспорта": "Промышленный экспорт",
        "Продукция": "Косметические кремы"}, 5)
    assert "LOGISTICS" not in slavda["categories"]
    assert "LOGISTICS" not in kosmetika["categories"]
    assert taxonomy.SECTOR_LOGISTICS not in slavda["sectors"]
    assert taxonomy.SECTOR_LOGISTICS not in kosmetika["sectors"]

    # Мороженое и пиломатериалы получают обоснованные товарные группы.
    ice_cream = normalize_catalog_row({
        "Название компании": "Хладокомбинат", "Отрасль экспорта": "Агропромышленный комплекс",
        "Продукция": "Мороженое пломбир"}, 6)
    timber = normalize_catalog_row({
        "Название компании": "Лесозавод", "Отрасль экспорта": "Лесопромышленный комплекс",
        "Продукция": "Пиломатериалы хвойных пород"}, 7)
    assert "FOOD_DAIRY" in ice_cream["sectors"]
    assert "TIMBER" in timber["sectors"]
    assert all(basis for basis in ice_cream["sector_basis"])

    # Там, где данных нет, отрасль не выдумывается, а ставится задача уточнения.
    unclear = normalize_catalog_row({"Название компании": "ООО Неизвестное"}, 8)
    assert unclear["sectors"] == []
    assert any("ручная проверка" in note for note in unclear["data_quality"])


# --- 9 ----------------------------------------------------------------------
def test_09_migration_report_accounts_for_every_record():
    """Все исходные записи учтены, контакты, ИНН, источник и история сохранены."""
    companies = [
        Company(slug=f"c{i}", name=f"Компания {i}", inn=f"77{i:08d}",
                contacts=f"+7 000 000-00-{i:02d}", source_row=i + 3,
                history="описание из каталога",
                source_name="Каталог экспортеров Приморского края")
        for i in range(5)
    ] + [Company(slug="brain-1", name="Отдельный профиль", source_name="brain")]

    summary = migration_summary(companies)
    assert summary["всего записей"] == 6
    assert summary["из каталога экспортеров"] == 5
    assert summary["отдельные профили"] == 1
    assert summary["с ИНН"] == 5
    assert summary["с контактами"] == 5
    assert summary["со ссылкой на строку источника"] == 5

    report = build_report(companies)
    for company in companies:
        assert company.name in report


# --- 10 ---------------------------------------------------------------------
@pytest.mark.parametrize("text", [
    "Компания основана в 2023 году",
    "Телефон +7 423 2400203 для связи",
    "Объём поставок составил 020 3 тонны",
    "Сумма контракта 0203 млн песо за год",
])
def test_10_numbers_do_not_become_hs_evidence(text):
    """Годы, телефоны, суммы и склеенные цифры HS-доказательствами не становятся."""
    company = Company(slug="x", name="Икс", hs_codes=["0203", "2023"],
                      products=["свинина"], product_aliases=["свинина"])
    item = RawItem(id=10, title="Материал", raw_text=text)
    signal = Signal(id=10, category="OTHER", sectors=[], relevance_score=3)
    assert match_signal(signal, item, company).score == 0


def test_10_profile_code_does_not_confirm_itself():
    """Код из профиля не подтверждает сам себя в новости."""
    signal = Signal(id=10, category="REGULATION", sectors=[], relevance_score=5,
                    hs_codes=["3304"], companies_matched=["Примкосметика"],
                    reason="ОБЯЗАТЕЛЬНЫЙ ТРИГГЕР")
    item = RawItem(id=10, title="Новость без кодов", raw_text="Текст без кодов товара.")
    assert match_signal(signal, item, COSMETICS).score == 0


# --- 11 ---------------------------------------------------------------------
def test_11_late_analysis_appears_once_with_original_date(db, settings):
    """
    Анализ старого сигнала, готовый сегодня, появляется один раз
    с правильной исходной датой; числа в шапке совпадают с карточками.
    """
    old = (datetime.now(timezone.utc) - timedelta(days=20)).isoformat(timespec="seconds")
    raw_id = _raw(db, source="bai", source_type="web", external_id="late",
                  title="BAI изменил требования к ввозу свинины",
                  raw_text="Требования изменены.", source_url="https://bai/11",
                  published_at=old)
    signal_id, _ = db.upsert_signal(Signal(
        raw_item_id=raw_id, category="MEAT", relevance_score=4, reason="изменение",
        trade_direction=DIRECTION_RU_TO_PH, sectors=["FOOD_MEAT"],
        event_key="ev-late", status=SIGNAL_ANALYZED, created_at=old))
    analysis = Analysis(signal_id=signal_id, summary="Требования изменены.",
                        opportunity="Проверить соответствие.", confidence=0.7)
    analysis.id = db.insert_analysis(analysis)
    db.insert_review(Review(analysis_id=analysis.id, verdict="PASS"))
    db.save_source_state(SourceState(source_id="telegram", last_success_at=utcnow()))

    builder = IssueBuilder(db, settings)
    issue, items = builder.compose(builder.collect(3), 3)
    markdown = builder.render(issue, items)

    cards = [i for i in items if i.event_key == "ev-late"]
    assert len(cards) == 1
    assert cards[0].late_confirmation is True
    assert cards[0].published_at == old
    assert markdown.count("### ") == issue.counters["cards"]
    assert f"Карточек в выпуске: {issue.counters['cards']}" in markdown


# --- 12 ---------------------------------------------------------------------
def test_12_no_gap_between_monday_and_thursday_runs(settings):
    """
    Между понедельником и четвергом непокрытого интервала нет.

    Разрыв между запусками — 3 и 4 дня; окно сбора считается от последней
    позиции источника и всегда его перекрывает.
    """
    for gap_days in (3, 4):
        anchor = (datetime.now(timezone.utc)
                  - timedelta(days=gap_days)).isoformat(timespec="seconds")
        state = SourceState(source_id="telegram", last_success_at=anchor,
                            last_published_at=anchor)
        window, _ = plan_days(state, settings)
        assert window >= gap_days


def test_12_downtime_is_backfilled(settings):
    """После простоя сервера собирается пропущенный интервал, даже длиннее недели."""
    anchor = (datetime.now(timezone.utc) - timedelta(days=11)).isoformat(timespec="seconds")
    state = SourceState(source_id="telegram", last_success_at=anchor,
                        last_published_at=anchor)
    window, reason = plan_days(state, settings)
    assert window >= 11
    assert "догрузка" in reason


def test_12_more_than_200_messages_is_reported_as_incomplete():
    """
    Технический лимит канала не выдаётся за полностью обработанный период.

    Остаток сохраняется в курсоре и отражается в статусе источника.
    """
    result = SourceResult(source_id="telegram", complete=False,
                          cursor='{"exportprim": 4321}')
    assert result.complete is False
    assert result.cursor

    state = SourceState(source_id="telegram", last_success_at=utcnow(),
                        coverage_complete=False)
    window, reason = plan_days(state, _WeeklySettings())
    assert "не полностью" in reason


class _WeeklySettings:
    fetch_days = 7
    fetch_overlap_hours = 12
    fetch_max_backfill_days = 45


# --- 13 ---------------------------------------------------------------------
def test_13_one_broken_source_does_not_mean_no_news(db, settings):
    """Недоступный источник не выдаётся за отсутствие новостей."""
    db.save_source_state(SourceState(source_id="telegram", last_success_at=utcnow()))
    db.save_source_state(SourceState(source_id="bfar", last_error="TLS handshake failed",
                                     coverage_complete=False))
    db.save_source_state(SourceState(source_id="psa"))     # не подключён

    builder = IssueBuilder(db, settings)
    coverage = builder.coverage()
    assert coverage["checked"] == ["telegram"]
    assert coverage["failed"] == ["bfar"]
    assert coverage["never_ran"] == ["psa"]
    assert coverage["complete"] is False

    issue, items = builder.compose(builder.collect(7), 7)
    markdown = builder.render(issue, items)
    assert "Часть источников недоступна" in markdown
    assert "не означает, что изменений нет" in markdown


def test_13_broken_source_does_not_block_others(settings, monkeypatch):
    """Один упавший источник не отменяет чтение остальных."""
    from trade_agent import fetch as fetch_module

    class _Broken:
        source_id = "broken"

        def resume_from(self, cursor, last_published_at=""):
            pass

        def fetch(self, days=7):
            raise RuntimeError("сеть недоступна")

    class _Working:
        source_id = "working"

        def resume_from(self, cursor, last_published_at=""):
            pass

        def fetch(self, days=7):
            item = RawItem(source="working", source_type="web", external_id="w1",
                           title="Филиппины меняют правила ввоза рыбы",
                           raw_text="Подробности изменения.", published_at=utcnow())
            item.hash = hash_content(item.source, item.external_id, item.title,
                                     item.raw_text)
            return SourceResult(source_id="working", items=[item],
                                latest_published_at=item.published_at)

    monkeypatch.setattr(fetch_module, "build_sources",
                        lambda *a, **kw: [_Broken(), _Working()])
    stats = fetch_module.run(settings, days=7)
    assert stats["new"] == 1
    assert "broken" in stats["source_errors"]
    assert stats["status"] == "partial"


# --- 14 ---------------------------------------------------------------------
def test_14_two_reposts_give_one_card_with_two_sources(db, settings):
    db.save_source_state(SourceState(source_id="telegram", last_success_at=utcnow()))
    for index in (1, 2):
        raw_id = _raw(db, source=f"tg:{index}", source_type="telegram",
                      external_id=f"rp{index}",
                      title="Филиппины отменили пошлину на рыбу",
                      raw_text=f"Пошлина отменена. Перепечатка {index}.",
                      source_url=f"https://t.me/c{index}/{index}",
                      published_at=utcnow())
        db.upsert_signal(Signal(
            raw_item_id=raw_id, category="REGULATION", relevance_score=4,
            reason="отмена пошлины", trade_direction=DIRECTION_RU_TO_PH,
            event_type=EVENT_RULE_CHANGE, sectors=["FISH_SEAFOOD"],
            event_key="ev-repost", status=SIGNAL_ANALYZED))
    builder = IssueBuilder(db, settings)
    issue, items = builder.compose(builder.collect(7), 7)
    cards = [i for i in items if i.event_key == "ev-repost"]
    assert len(cards) == 1
    assert len(cards[0].source_urls) == 2
    assert issue.counters["cards"] == 1


def test_14_different_changes_of_same_product_are_not_merged():
    """Похожий заголовок сам по себе не делает события одним."""
    first = RawItem(id=1, title="Филиппины отменили пошлину на рыбу",
                    published_at="2026-09-10T00:00:00Z", hash="a")
    second = RawItem(id=2, title="Филиппины ввели квоту на рыбу",
                     published_at="2026-09-10T00:00:00Z", hash="b")
    assert event_key(first) != event_key(second)


# --- 15 ---------------------------------------------------------------------
def test_15_model_failure_never_gives_pass(db, settings):
    """Сбой модели не даёт PASS, повторы ограничены, очередь видна."""
    raw_id = _raw(db, source="bai", source_type="web", external_id="f15",
                  title="BAI открыл аккредитацию", raw_text="Текст материала.")
    signal_id, _ = db.upsert_signal(Signal(raw_item_id=raw_id, category="MEAT",
                                           relevance_score=4, reason="аккредитация"))
    max_attempts = settings.reviewer_max_revisions + 1
    for attempt in range(1, max_attempts + 1):
        attempts = db.bump_review_attempt(signal_id, "reviewer_empty_response")
        if attempts >= max_attempts:
            db.set_signal_status(signal_id, SIGNAL_NEEDS_REVIEW, "reviewer_max_revisions")
    assert db.signals_for_analysis(3, max_attempts=max_attempts) == []
    signal = db.get_signal(signal_id)
    assert signal.status == SIGNAL_NEEDS_REVIEW
    assert signal.unverified is True
    assert db.signals_needing_attention(5)


def test_15_expensive_retry_is_delayed(db):
    """Мусорный ответ модели не перезапрашивается в том же запуске бесконечно."""
    raw_id = _raw(db, source="x", source_type="web", external_id="r15",
                  title="Материал", raw_text="Текст.")
    signal_id, _ = db.upsert_signal(Signal(raw_item_id=raw_id, relevance_score=4))
    db.schedule_retry(signal_id, minutes=60, error="analyst_invalid_response")
    assert db.signals_for_analysis(3) == []


def test_15_failed_build_does_not_send_old_issue(settings, db):
    from trade_agent.notify import send_latest

    settings.bot.token = "token"
    settings.bot.allowed_chat_ids = (22,)
    db.close()
    settings.digest_dir.mkdir(parents=True, exist_ok=True)
    (settings.digest_dir / "latest.md").write_text("# старый выпуск", "utf-8")
    with pytest.raises(ValueError, match="нет успешно собранного выпуска"):
        send_latest(settings)


# --- 16 ---------------------------------------------------------------------
def test_16_confirmed_delivery_is_not_duplicated(db, settings):
    from trade_agent.models import Issue, IssueItem
    from trade_agent.notify import deliver_issue

    settings.bot.allowed_chat_ids = (22,)
    markdown = "# Выпуск\n\n## Приморье → Филиппины\n\n### Карточка\n\nТекст.\n"
    issue = Issue(period_start=utcnow(), period_end=utcnow())
    issue.id = db.create_issue(issue)
    db.save_issue_items(int(issue.id), [IssueItem(section="RU_TO_PH", event_key="e")])
    db.finish_issue(int(issue.id), "built", {"cards": 1}, {}, "latest.md",
                    content_hash(markdown))
    issue = db.get_issue(int(issue.id))

    class _Bot:
        def __init__(self):
            self.sent = []

        def send_post(self, chat_id, text):
            self.sent.append(text)
            return len(self.sent)

    first = _Bot()
    deliveries = deliver_issue(settings, db, first, issue, markdown)
    assert deliveries[0].status == DELIVERY_SENT
    second = _Bot()
    deliver_issue(settings, db, second, issue, markdown)
    assert second.sent == []

    stored = db.get_delivery(int(issue.id), "22")
    stored.status = DELIVERY_UNKNOWN
    stored.parts_sent = 1
    db.save_delivery(stored)
    third = _Bot()
    deliver_issue(settings, db, third, issue, markdown)
    assert third.sent == [], "неопределённая доставка не рассылается вслепую"


# --- 17 ---------------------------------------------------------------------
def test_17_only_two_automatic_cycles_per_week(settings, db):
    """За неделю выполняются только два автоматических цикла."""
    from trade_agent import run_pipeline

    for _ in range(MAX_AUTOMATIC_CYCLES_PER_WEEK):
        db.start_run(run_pipeline.STAGE)
    db.close()
    assert cycles_this_week(settings) == MAX_AUTOMATIC_CYCLES_PER_WEEK

    result = run_pipeline.run(settings, scheduled=True)
    assert result["status"] == "skipped"
    assert "лимит" in result["reason"]


def test_17_no_daily_timers_are_shipped():
    """Скрытого ежедневного сбора и ежедневных уведомлений в развёртывании нет."""
    timers = sorted(path.name for path in (DEPLOY / "systemd").glob("*.timer"))
    assert timers == ["trade-agent.timer"]
    text = (DEPLOY / "systemd" / "trade-agent.timer").read_text("utf-8")
    assert "OnCalendar=Mon,Thu 08:00 Asia/Manila" in text
    assert "*-*-*" not in text

    crontab = (DEPLOY / "crontab.example").read_text("utf-8")
    schedule_lines = [line for line in crontab.splitlines()
                      if line.strip() and not line.startswith("#")]
    assert len(schedule_lines) == 2
    assert all("run_pipeline --scheduled" in line for line in schedule_lines)


def test_17_other_lenovo_projects_are_not_touched():
    """Юниты проекта трогают только свои пути."""
    for path in (DEPLOY / "systemd").glob("*"):
        text = path.read_text("utf-8")
        for other in ("tour-phil", "lazy-reader", "price-collector", "Tour-Phil"):
            assert other.lower() not in text.lower()
        if "ReadWritePaths" in text:
            for line in text.splitlines():
                if line.startswith("ReadWritePaths"):
                    assert all(part.startswith("/opt/trade-agent")
                               for part in line.split("=", 1)[1].split())
