#!/usr/bin/env python3
"""
Образец выпуска на воспроизводимом наборе материалов.

    python tools/sample_issue.py --out digest/samples/after.md

Набор собран из случаев, найденных при аудите рабочей базы: пошлины ЮАР
на китайскую сталь рядом с косметикой, обзор трески в срочном
регулировании, «HS 2023» из истории бренда, потерянная бизнес-миссия,
перепост одной новости двумя каналами, анализ, завершившийся с опозданием.

Скрипт намеренно написан так, чтобы запускаться и на прежней версии кода:
он работает через публичные точки входа (обязательный триггер, радар,
сборщик выпуска) и заполняет только те поля, которые есть в текущей схеме.
Поэтому один и тот же набор данных даёт сопоставимые образцы «до» и «после».

Сеть и модели не используются: оценки Scout заданы в наборе явно.
"""

from __future__ import annotations

import argparse
import dataclasses
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from trade_agent import digest as digest_module            # noqa: E402
from trade_agent.alerts import detect_mandatory_policy_alert  # noqa: E402
from trade_agent.config import load_settings               # noqa: E402
from trade_agent.db import Database                        # noqa: E402
from trade_agent.models import (                           # noqa: E402
    Analysis, Company, RawItem, Review, Signal,
)
from trade_agent.radar import OpportunityRadar             # noqa: E402
from trade_agent.utils import content_hash                 # noqa: E402


def _ago(days: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")


COMPANIES = [
    dict(slug="dalryba", name="Дальрыба", products=["Треска мороженая"],
         product_aliases=["треска", "cod"], hs_codes=["0303"],
         categories=["FOOD"], sectors=["FISH_SEAFOOD"],
         industry="Рыба и морепродукты", inn="2500000001",
         source_name="Каталог экспортеров Приморского края", source_row=12),
    dict(slug="rybflot", name="Рыбфлот", products=["Минтай"],
         product_aliases=["минтай"], hs_codes=["0303"], categories=["FOOD"],
         sectors=["FISH_SEAFOOD"], industry="Рыба и морепродукты",
         source_name="Каталог экспортеров Приморского края", source_row=13),
    dict(slug="primkosmetika", name="Примкосметика", products=["Косметические кремы"],
         product_aliases=["косметика", "крем"], hs_codes=["3304"],
         categories=["OTHER"], sectors=["COSMETICS"], industry="Промышленный экспорт",
         source_name="Каталог экспортеров Приморского края", source_row=88),
    dict(slug="slavda", name="SLAVDA GROUP", products=["Минеральная вода", "Лимонад"],
         product_aliases=["минеральная вода", "лимонад"], categories=["FOOD"],
         sectors=["FOOD_BEVERAGE"], industry="Агропромышленный комплекс",
         source_name="Каталог экспортеров Приморского края", source_row=44),
]

# (материал, поля сигнала). Оценки Scout заданы явно — модель не вызывается.
MATERIALS = [
    (
        dict(source="tg:news", source_type="telegram", external_id="za-steel",
             title="ЮАР ввела пошлины на китайскую сталь",
             raw_text="ЮАР ввела антидемпинговые пошлины на китайскую сталь. "
                      "В составе решения может быть пересмотр ставок для "
                      "основных поставщиков в 2023 году.",
             source_url="https://t.me/news/1", published_at=_ago(1.2)),
        dict(category="REGULATION", relevance_score=5, reason="пошлины на сталь",
             sectors=["INDUSTRIAL"], event_type="RULE_CHANGE",
             trade_direction="UNKNOWN", event_key="sample-za-steel"),
    ),
    (
        dict(source="tg:mcx", source_type="telegram", external_id="cod-review",
             title="Обзор рынка трески",
             raw_text="Вылов трески вырос, цены снизились, экспортёры оформляют "
                      "сертификаты качества. Подробный обзор рынка за квартал.",
             source_url="https://t.me/mcx_ru/5665", published_at=_ago(1.5)),
        dict(category="FOOD", relevance_score=3, reason="обзор рынка трески",
             sectors=["FISH_SEAFOOD"], event_type="DEMAND",
             trade_direction="RU_TO_PH", event_key="sample-cod-review"),
    ),
    (
        dict(source="tg:brand", source_type="telegram", external_id="brand-story",
             title="История бренда морепродуктов",
             raw_text="Бренд основан в 2023 году, получил сертификат качества "
                      "и может расширить состав линейки.",
             source_url="https://t.me/brand/2", published_at=_ago(2.0)),
        dict(category="OTHER", relevance_score=2, reason="история бренда",
             sectors=[], event_type="OTHER", trade_direction="UNKNOWN",
             event_key="sample-brand"),
    ),
    (
        dict(source="tg:exportprim", source_type="telegram", external_id="mission",
             title="Бизнес-миссия Иркутской области на Филиппинах",
             raw_text="Делегация российских компаний провела переговоры в Маниле, "
                      "запланирован деловой форум и встречи с местными партнёрами.",
             source_url="https://t.me/exportprim/291", published_at=_ago(0.8)),
        dict(category="OTHER", relevance_score=3,
             reason="контакт российских компаний с филиппинским рынком",
             sectors=[], event_type="BUSINESS_CONTACT", trade_direction="RU_TO_PH",
             jurisdiction="PH", event_key="sample-mission"),
    ),
    (
        dict(source="da", source_type="web", external_id="ph-import",
             title="Филиппины нарастили импорт рыбы из Вьетнама",
             raw_text="Импорт рыбы (HS 0303) из Вьетнама вырос на 20% за квартал, "
                      "конкуренция поставщиков на филиппинском рынке усилилась.",
             source_url="https://www.da.gov.ph/news/1", published_at=_ago(1.0)),
        dict(category="IMPORT", relevance_score=4,
             reason="рост филиппинского импорта влияет на спрос и конкуренцию "
                    "для приморских поставщиков",
             sectors=["FISH_SEAFOOD"], event_type="DEMAND",
             trade_direction="RU_TO_PH", jurisdiction="PH",
             destination_market="Philippines", origin_countries=["Вьетнам"],
             event_key="sample-ph-import",
             evidence_fragments=["Импорт рыбы (HS 0303) из Вьетнама вырос на 20%"]),
    ),
    (
        dict(source="dti", source_type="web", external_id="ph-export",
             title="Филиппины увеличили экспорт кокосового масла в Россию",
             raw_text="Поставки кокосового масла из Филиппин в РФ выросли, "
                      "экспортёры ищут российских партнёров.",
             source_url="https://tradelinephilippines.dti.gov.ph/1",
             published_at=_ago(1.1)),
        dict(category="FOOD", relevance_score=4,
             reason="филиппинский экспорт в РФ по нужной товарной группе",
             sectors=["FOOD_AGRI"], event_type="SUPPLY",
             trade_direction="PH_TO_RU", destination_market="Россия",
             origin_countries=["Филиппины"], event_key="sample-ph-export"),
    ),
    (
        dict(source="tg:a", source_type="telegram", external_id="repost-1",
             title="Филиппины отменили пошлину на мороженую рыбу",
             raw_text="Пошлина на мороженую рыбу отменена приказом ведомства, "
                      "мера вступает в силу с 1 октября 2026 года.",
             source_url="https://t.me/a/10", published_at=_ago(0.9)),
        dict(category="REGULATION", relevance_score=5,
             reason="отмена пошлины на нужную товарную группу",
             sectors=["FISH_SEAFOOD"], event_type="RULE_CHANGE",
             trade_direction="RU_TO_PH", jurisdiction="PH",
             effective_from="2026-10-01", event_key="sample-duty",
             evidence_fragments=["Пошлина на мороженую рыбу отменена приказом"]),
    ),
    (
        dict(source="tg:b", source_type="telegram", external_id="repost-2",
             title="Пошлину на мороженую рыбу отменили Филиппины",
             raw_text="Тот же приказ: пошлина на мороженую рыбу отменена "
                      "с 1 октября 2026 года.",
             source_url="https://t.me/b/11", published_at=_ago(0.85)),
        dict(category="REGULATION", relevance_score=4,
             reason="перепечатка новости об отмене пошлины",
             sectors=["FISH_SEAFOOD"], event_type="RULE_CHANGE",
             trade_direction="RU_TO_PH", jurisdiction="PH",
             effective_from="2026-10-01", event_key="sample-duty"),
    ),
    (
        dict(source="bai", source_type="web", external_id="late-one",
             title="BAI изменил требования к ввозу мяса",
             raw_text="Требования к ввозу мяса изменены административным приказом.",
             source_url="https://www.bai.gov.ph/2", published_at=_ago(20)),
        dict(category="REGULATION", relevance_score=4,
             reason="изменение требований к ввозу",
             sectors=["FOOD_MEAT"], event_type="RULE_CHANGE",
             trade_direction="RU_TO_PH", jurisdiction="PH",
             event_key="sample-late", created_at=_ago(20)),
    ),
]

ANALYSES = {
    "sample-ph-import": dict(
        company="Дальрыба",
        summary="Филиппинский импорт рыбы из Вьетнама вырос на 20% за квартал.",
        opportunity="Спрос на рынке растёт, но усиливается конкуренция вьетнамских "
                    "поставщиков: условия входа стоит пересчитать.",
        confidence=0.8, next_step="сверить цены и условия поставки с вьетнамскими"),
    "sample-ph-export": dict(
        company="нет прямого совпадения",
        summary="Филиппины увеличили экспорт кокосового масла в Россию.",
        opportunity="Появилось предложение филиппинской стороны; роль компаний "
                    "каталога по этому товару не подтверждена.",
        confidence=0.6, next_step="уточнить, кто из каталога закупает это сырьё"),
    "sample-late": dict(
        company="нет прямого совпадения",
        summary="BAI изменил требования к ввозу мяса.",
        opportunity="Изменение касается условий доступа на филиппинский рынок.",
        confidence=0.7, next_step="проверить требования по первичному документу"),
}


def _fields(model) -> set[str]:
    return {field.name for field in dataclasses.fields(model)}


def _keep(model, payload: dict) -> dict:
    """Оставляет только те поля, которые есть в текущей версии модели."""
    allowed = _fields(model)
    return {key: value for key, value in payload.items() if key in allowed}


def seed(db, settings) -> None:
    known = _fields(Company)
    for payload in COMPANIES:
        db.upsert_company(Company(**{k: v for k, v in payload.items() if k in known}))

    if hasattr(db, "save_source_state"):
        from trade_agent.models import SourceState, utcnow

        db.save_source_state(SourceState(source_id="telegram", last_success_at=utcnow()))
        db.save_source_state(SourceState(source_id="da", last_success_at=utcnow()))
        db.save_source_state(SourceState(source_id="bfar",
                                         last_error="TLS handshake failed"))

    companies = db.all_companies()
    radar = OpportunityRadar(settings)
    for raw_payload, signal_payload in MATERIALS:
        item = RawItem(**raw_payload)
        item.hash = content_hash(item.source, item.external_id, item.title, item.raw_text)
        raw_id, _ = db.upsert_raw_item(item)
        item.id = raw_id

        forced = detect_mandatory_policy_alert(item, companies)
        if forced is not None:
            signal = forced
        else:
            signal = Signal(raw_item_id=raw_id, **_keep(Signal, signal_payload))
        signal.raw_item_id = raw_id
        if not getattr(signal, "status", ""):
            signal.status = "new"
        signal.status = "analyzed"
        signal_id, _ = db.upsert_signal(signal)
        signal.id = signal_id

        for match in radar.match_all(signal, item, companies):
            db.upsert_match(match)

        analysis_payload = ANALYSES.get(signal_payload.get("event_key", ""))
        if analysis_payload:
            analysis = Analysis(signal_id=signal_id, **_keep(Analysis, analysis_payload))
            analysis.id = db.insert_analysis(analysis)
            db.insert_review(Review(analysis_id=analysis.id, verdict="PASS"))


def build(days: int = 4) -> str:
    workdir = Path(tempfile.mkdtemp(prefix="trade-agent-sample-"))
    try:
        settings = load_settings(PROJECT_DIR)
        settings.db_path = workdir / "sample.db"
        settings.digest_dir = workdir / "digest"
        settings.log_dir = workdir / "logs"
        settings.ensure_dirs()

        db = Database(settings.db_path)
        try:
            seed(db, settings)
            builder_cls = getattr(digest_module, "IssueBuilder", None)
            if builder_cls is not None:
                builder = builder_cls(db, settings)
                data = builder.collect(days)
                issue, items = builder.compose(data, days)
                return builder.render(issue, items)
            # Прежняя версия кода: другой сборщик, другой формат.
            builder = digest_module.DigestBuilder(db, settings)
            return builder.build(builder.collect(days), days)
        finally:
            db.close()
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python tools/sample_issue.py",
        description="Образец выпуска на воспроизводимом наборе материалов.")
    parser.add_argument("--days", type=int, default=4)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)

    markdown = build(args.days)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(markdown, "utf-8")
        print(f"Образец записан: {args.out}")
    else:
        print(markdown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
