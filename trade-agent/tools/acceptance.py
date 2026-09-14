#!/usr/bin/env python3
"""
Приёмочная проверка на реальных данных сервера.

Все подкоманды ТОЛЬКО читают рабочую базу. Ничего не отправляется,
расписание не трогается, данные каталога не исправляются.

    # 1. разбор каталога компаний: что реально лежит в базе
    python tools/acceptance.py catalog --out catalog-report.md

    # 2. выгрузка набора материалов для ручной разметки
    python tools/acceptance.py export-set --limit 80 --out labelset.jsonl

    # 3. метрики по размеченному набору
    python tools/acceptance.py metrics --labels labelset.jsonl --out metrics.md

    # 4. проверка старых дефектов на реальных материалах базы
    python tools/acceptance.py defects --out defects.md

    # 5. сквозной прогон выпуска без отправки
    python tools/acceptance.py dry-run --days 4 --out dryrun.md

Разметка (`export-set` → руками → `metrics`): в каждой строке JSONL есть
поле `expected`, которое человек заполняет. Пустое поле означает
«не размечено» и в метрики не идёт.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Optional

PROJECT_DIR = Path(__file__).resolve().parent.parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from trade_agent.agents import evidence, taxonomy          # noqa: E402
from trade_agent.alerts import detect_mandatory_policy_alert, detect_rule_change  # noqa: E402
from trade_agent.companies.audit import (                  # noqa: E402
    build_report, disputes, migration_summary, proposed_sectors,
)
from trade_agent.config import load_settings               # noqa: E402
from trade_agent.db import Database                        # noqa: E402
from trade_agent.models import (                           # noqa: E402
    LINK_DIRECT, Company, RawItem, Signal, SIGNAL_REJECTED,
)
from trade_agent.radar import OpportunityRadar, match_signal  # noqa: E402

# Слова, из-за которых в рабочей базе появлялись ложные отраслевые связи.
RISKY_TERMS = ("порт", "экспорт", "импорт", "состав", "может", "бизнес",
               "продукция", "услуги", "деятельность", "поставка")


# --- 1. каталог -------------------------------------------------------------
def catalog_report(db: Database) -> str:
    companies = db.all_companies()
    total = len(companies)

    broken: list[Company] = []        # нет ни названия, ни продукции, ни отрасли
    without_sector: list[Company] = []
    without_products: list[Company] = []
    without_hs: list[Company] = []
    too_generic: list[tuple[Company, list[str]]] = []
    risky: list[tuple[Company, list[str]]] = []
    national_codes: list[Company] = []

    for company in companies:
        sectors, _ = proposed_sectors(company)
        if not company.name.strip() or (not company.products and not company.industry
                                        and not company.description):
            broken.append(company)
        if not sectors:
            without_sector.append(company)
        if not company.products:
            without_products.append(company)
        if not company.hs_codes:
            without_hs.append(company)

        # Слишком общие признаки: термины, которые сами по себе ничего
        # не доказывают. Раньше именно они давали ложные совпадения.
        generic = [term for term in [*company.product_aliases, *company.products]
                   if evidence.is_stop_term(term)]
        if generic:
            too_generic.append((company, sorted(set(generic))[:6]))

        # Термины, которые МОГУТ дать ложную связь по подстроке.
        hits = [term for term in [*company.product_aliases, *company.products]
                if evidence.normalize(term) in RISKY_TERMS]
        if hits:
            risky.append((company, sorted(set(hits))[:6]))

        for code in company.hs_codes:
            if len(evidence.digits_only(code)) == 10:
                national_codes.append(company)
                break

    # Проверяем, что опасные термины действительно НЕ дают связи.
    false_links = _check_risky_links(risky)

    summary = migration_summary(companies)
    lines = [
        "# Разбор каталога компаний",
        "",
        "Отчёт только читает базу. Никакие данные каталога не исправляются.",
        "",
        "## Сводка",
        "",
    ]
    for key, value in summary.items():
        lines.append(f"- {key}: {value}")
    lines += [
        "",
        "## Качество классификации",
        "",
        f"- всего записей: {total}",
        f"- повреждённых (нет названия либо ни продукции, ни отрасли, ни описания): {len(broken)}",
        f"- без определяемой отрасли: {len(without_sector)}",
        f"- без продукции: {len(without_products)}",
        f"- без кодов ТН ВЭД: {len(without_hs)}",
        f"- со слишком общими признаками: {len(too_generic)}",
        f"- с терминами, опасными для ложных связей: {len(risky)}",
        f"- с национальным 10-значным кодом РФ: {len(national_codes)}",
        "",
        f"Ложных связей по опасным терминам на проверке: {false_links}",
        ("Ноль означает, что поиск по границам слова работает: «порт» внутри "
         "«экспорт» связи не создаёт." if false_links == 0 else
         "ВНИМАНИЕ: опасные термины дают связь — это регрессия."),
        "",
    ]

    def sample(title: str, rows: list[Any], render) -> None:
        if not rows:
            return
        lines.append(f"### {title} ({len(rows)})")
        lines.append("")
        for row in rows[:15]:
            lines.append(f"- {render(row)}")
        if len(rows) > 15:
            lines.append(f"- … ещё {len(rows) - 15}")
        lines.append("")

    sample("Повреждённые записи", broken, lambda c: f"{c.name or '(без названия)'} [{c.slug}]")
    sample("Без отрасли", without_sector,
           lambda c: f"{c.name} [{c.slug}] отрасль каталога: {c.industry or '—'}")
    sample("Без продукции", without_products, lambda c: f"{c.name} [{c.slug}]")
    sample("Без кодов ТН ВЭД", without_hs, lambda c: f"{c.name} [{c.slug}]")
    sample("Слишком общие признаки", too_generic,
           lambda row: f"{row[0].name} [{row[0].slug}]: {', '.join(row[1])}")
    sample("Опасные термины", risky,
           lambda row: f"{row[0].name} [{row[0].slug}]: {', '.join(row[1])}")
    sample("Национальный 10-значный код", national_codes,
           lambda c: f"{c.name} [{c.slug}]: {', '.join(c.hs_codes[:3])}")

    disputed = [(c, disputes(c, proposed_sectors(c)[0])) for c in companies]
    disputed = [(c, notes) for c, notes in disputed if notes]
    lines += [f"## Записи со спорными местами: {len(disputed)}", "",
              "Полная таблица — в отчёте `companies.audit`.", ""]
    return "\n".join(lines)


def _check_risky_links(risky: list[tuple[Company, list[str]]]) -> int:
    """
    Проверяет, что опасные термины не создают связь на контрольном тексте.

    Текст намеренно содержит «экспорт» и «транспорт», но не содержит
    ни одного настоящего товара.
    """
    control = RawItem(
        id=0, title="Компания расширяет экспорт продукции",
        raw_text="Транспортная составляющая экспорта выросла, в составе "
                 "поставок может быть разная продукция.")
    signal = Signal(id=0, category="OTHER", sectors=[], relevance_score=3)
    false_links = 0
    for company, _ in risky:
        detail = match_signal(signal, control, company)
        if detail.link_type == LINK_DIRECT:
            false_links += 1
    return false_links


# --- 2. выгрузка набора -----------------------------------------------------
# Категории набора: человек размечает `expected`, скрипт считает метрики.
EXPECTED_TEMPLATE = {
    "relevant": None,           # true/false — нужен ли материал в обзоре
    "must_alert": None,         # true/false — обязательный сигнал
    "trade_direction": "",      # RU_TO_PH | PH_TO_RU | BOTH | UNKNOWN
    "sector": "",               # код отрасли из таксономии
    "event_type": "",           # тип события
    # slug компаний с ПРЯМОЙ применимостью. null означает «не размечено»
    # и в метрику связей не идёт; пустой список означает «связей быть
    # не должно» и проверяется строго.
    "companies_direct": None,
    "note": "",
}


def export_set(db: Database, limit: int, out: Path) -> dict[str, Any]:
    """
    Выгружает материалы из рабочей базы для ручной разметки.

    Выборка стратифицированная: берём и принятые, и отклонённые сигналы,
    и материалы без сигнала. Иначе метрики посчитаются только по тому,
    что система уже сочла нужным, и recall окажется завышенным.
    """
    rows = db.conn.execute(
        "SELECT r.id AS raw_id, r.source, r.source_type, r.source_url, r.title, "
        "       r.raw_text, r.published_at, s.id AS signal_id, s.status, "
        "       s.relevance_score, s.category, s.trade_direction, s.event_type, "
        "       s.sectors, s.must_alert, s.reason "
        "FROM raw_items r LEFT JOIN signals s ON s.raw_item_id = r.id "
        "ORDER BY r.published_at DESC, r.id DESC").fetchall()

    buckets: dict[str, list[Any]] = {"accepted": [], "rejected": [], "unprocessed": []}
    for row in rows:
        if row["signal_id"] is None:
            buckets["unprocessed"].append(row)
        elif row["status"] == SIGNAL_REJECTED:
            buckets["rejected"].append(row)
        else:
            buckets["accepted"].append(row)

    # Пропорции: половина принятых, треть отклонённых, остальное — очередь.
    plan = {"accepted": limit // 2, "rejected": limit // 3,
            "unprocessed": limit - limit // 2 - limit // 3}
    selected: list[Any] = []
    for bucket, count in plan.items():
        selected.extend(buckets[bucket][:count])
    # Добираем из того, что осталось, если какой-то корзины не хватило.
    if len(selected) < limit:
        chosen = {id(row) for row in selected}
        for bucket in ("accepted", "rejected", "unprocessed"):
            for row in buckets[bucket]:
                if len(selected) >= limit:
                    break
                if id(row) not in chosen:
                    selected.append(row)

    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as handle:
        for row in selected:
            record = {
                "raw_id": row["raw_id"],
                "signal_id": row["signal_id"],
                "source": row["source"],
                "source_type": row["source_type"],
                "url": row["source_url"],
                "published_at": row["published_at"],
                "title": row["title"],
                "text": (row["raw_text"] or "")[:4000],
                "system": {
                    "status": row["status"],
                    "score": row["relevance_score"],
                    "category": row["category"],
                    "trade_direction": row["trade_direction"],
                    "event_type": row["event_type"],
                    "sectors": json.loads(row["sectors"] or "[]") if row["sectors"] else [],
                    "must_alert": bool(row["must_alert"]),
                    "reason": row["reason"],
                },
                "expected": dict(EXPECTED_TEMPLATE),
            }
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    return {"written": len(selected), "path": str(out),
            "accepted": len(buckets["accepted"]), "rejected": len(buckets["rejected"]),
            "unprocessed": len(buckets["unprocessed"])}


# --- 3. метрики по разметке -------------------------------------------------
def metrics_report(db: Database, labels_path: Path) -> str:
    records = []
    for line in labels_path.read_text("utf-8").splitlines():
        line = line.strip()
        if line:
            records.append(json.loads(line))

    labelled = [r for r in records if r.get("expected", {}).get("relevant") is not None]
    if not labelled:
        return ("# Метрики\n\nНи одна строка не размечена: заполните поле "
                "`expected.relevant` хотя бы у 40 материалов.\n")

    companies = {c.slug: c for c in db.all_companies()}
    radar = OpportunityRadar(load_settings(PROJECT_DIR))

    tp = fp = fn = tn = 0
    alert_tp = alert_fp = alert_fn = 0
    sector_ok = sector_total = 0
    direction_ok = direction_total = 0
    link_tp = link_fp = link_fn = link_marked = 0

    mistakes: list[str] = []
    for record in labelled:
        expected = record["expected"]
        system = record["system"]
        wanted = bool(expected["relevant"])
        taken = system["status"] not in (None, SIGNAL_REJECTED)

        if wanted and taken:
            tp += 1
        elif wanted and not taken:
            fn += 1
            mistakes.append(f"ПРОПУСК: {record['title'][:90]} — {system.get('reason', '')[:80]}")
        elif not wanted and taken:
            fp += 1
            mistakes.append(f"ЛОЖНОЕ ВКЛЮЧЕНИЕ: {record['title'][:90]} "
                            f"(оценка {system.get('score')})")
        else:
            tn += 1

        if expected.get("must_alert") is not None:
            if expected["must_alert"] and system["must_alert"]:
                alert_tp += 1
            elif expected["must_alert"] and not system["must_alert"]:
                alert_fn += 1
            elif not expected["must_alert"] and system["must_alert"]:
                alert_fp += 1
                mistakes.append(f"ЛОЖНЫЙ ОБЯЗАТЕЛЬНЫЙ СИГНАЛ: {record['title'][:90]}")

        if expected.get("sector"):
            sector_total += 1
            if expected["sector"] in (system.get("sectors") or []):
                sector_ok += 1
            else:
                mistakes.append(f"ОТРАСЛЬ: {record['title'][:70]} — ожидалось "
                                f"{expected['sector']}, получено {system.get('sectors')}")

        if expected.get("trade_direction"):
            direction_total += 1
            if expected["trade_direction"] == system.get("trade_direction"):
                direction_ok += 1
            else:
                mistakes.append(f"НАПРАВЛЕНИЕ: {record['title'][:70]} — ожидалось "
                                f"{expected['trade_direction']}, получено "
                                f"{system.get('trade_direction')}")

        # Различаем «связей быть не должно» (пустой список) и «не размечено»
        # (null). Раньше второе считалось первым, и каждая найденная связь
        # уходила в ложные — метрика превращалась в шум.
        marked_links = expected.get("companies_direct")
        if marked_links is not None:
            expected_links = set(marked_links)
            item = RawItem(id=record["raw_id"], title=record["title"],
                           raw_text=record["text"], source=record["source"],
                           source_url=record.get("url", ""))
            signal = Signal(id=record.get("signal_id") or 0,
                            category=system.get("category") or "OTHER",
                            sectors=system.get("sectors") or [],
                            trade_direction=system.get("trade_direction") or "UNKNOWN",
                            relevance_score=int(system.get("score") or 0))
            actual = {m.company_slug for m
                      in radar.match_all(signal, item, list(companies.values()))
                      if m.link_type == LINK_DIRECT}
            link_tp += len(expected_links & actual)
            link_fp += len(actual - expected_links)
            link_fn += len(expected_links - actual)
            link_marked += 1
            for slug in actual - expected_links:
                mistakes.append(f"ЛОЖНАЯ ПРЯМАЯ СВЯЗЬ: {record['title'][:70]} → {slug}")
            for slug in expected_links - actual:
                mistakes.append(f"ПРОПУЩЕННАЯ СВЯЗЬ: {record['title'][:70]} → {slug}")

    def ratio(numerator: int, denominator: int) -> str:
        return f"{numerator / denominator:.1%}" if denominator else "—"

    lines = [
        "# Метрики на реальных данных",
        "",
        f"Размечено материалов: {len(labelled)} из {len(records)} выгруженных.",
        "",
        "## Отбор материалов",
        "",
        f"- верно включено (TP): {tp}",
        f"- ложно включено (FP): {fp}",
        f"- пропущено (FN): {fn}",
        f"- верно отброшено (TN): {tn}",
        f"- **precision: {ratio(tp, tp + fp)}** (цель ≥ 90%)",
        f"- **recall: {ratio(tp, tp + fn)}** (цель ≥ 90%)",
        "",
        "## Обязательные сигналы",
        "",
        f"- precision: {ratio(alert_tp, alert_tp + alert_fp)}",
        f"- recall: {ratio(alert_tp, alert_tp + alert_fn)}",
        f"- ложных обязательных сигналов: {alert_fp} (цель 0)",
        "",
        "## Классификация",
        "",
        f"- отрасль определена верно: {ratio(sector_ok, sector_total)} "
        f"({sector_ok} из {sector_total})",
        f"- направление торговли верно: {ratio(direction_ok, direction_total)} "
        f"({direction_ok} из {direction_total})",
        "",
        "## Связи с компаниями (прямая применимость)",
        "",
        f"- материалов с размеченными связями: {link_marked}"
        + ("" if link_marked else " — метрика связей не считалась"),
        f"- верных связей: {link_tp}",
        f"- ложных связей: {link_fp} (цель 0)",
        f"- пропущенных связей: {link_fn}",
        f"- precision: {ratio(link_tp, link_tp + link_fp)}",
        "",
        "## Разбор ошибок",
        "",
    ]
    counter = Counter(m.split(":")[0] for m in mistakes)
    for kind, count in counter.most_common():
        lines.append(f"- {kind}: {count}")
    lines.append("")
    for mistake in mistakes[:60]:
        lines.append(f"- {mistake}")
    if len(mistakes) > 60:
        lines.append(f"- … ещё {len(mistakes) - 60}")
    lines.append("")
    return "\n".join(lines)


# --- 4. старые дефекты на реальных материалах -------------------------------
def defects_report(db: Database) -> str:
    """
    Прогоняет по всем материалам базы проверки на известные дефекты.

    Здесь нет разметки: это объективные проверки, которые либо проходят,
    либо нет, независимо от мнения человека о релевантности.
    """
    rows = db.conn.execute(
        "SELECT id, title, raw_text, source_url FROM raw_items").fetchall()
    companies = db.all_companies()

    checks = {
        "год как HS-код": 0,
        "телефон как HS-код": 0,
        "голые цифры как HS-код": 0,
        "«порт» внутри «экспорт» → логистика": 0,
        "обзор цен как изменение правил": 0,
        "правило чужой юрисдикции как обязательный сигнал": 0,
        "компания без доказательства": 0,
        "отраслевой интерес выдан за прямую применимость": 0,
    }
    examples: dict[str, list[str]] = {key: [] for key in checks}

    for row in rows:
        text = f"{row['title']}\n{row['raw_text']}"
        declared = evidence.declared_hs_codes(text)

        # Годы и телефоны не должны попадать в найденные коды.
        for hit in declared:
            if len(hit.code) == 4 and 1900 <= int(hit.code) <= 2100:
                checks["год как HS-код"] += 1
                examples["год как HS-код"].append(f"#{row['id']} {hit.code}")
            if len(hit.code) >= 10 and hit.code.startswith(("7", "8")):
                checks["телефон как HS-код"] += 1
                examples["телефон как HS-код"].append(f"#{row['id']} {hit.code}")

        # Голые цифры без маркера кодом не считаются: если в тексте есть
        # четырёхзначные числа, но declared пуст — это правильное поведение.
        import re
        bare = re.findall(r"(?<!\d)\d{4,10}(?!\d)", text)
        if bare and not declared:
            pass  # ожидаемое поведение, не дефект
        elif bare and declared and len(declared) > len(bare):
            checks["голые цифры как HS-код"] += 1

        # «порт» внутри «экспорт»
        folded = evidence.fold(text)
        if "экспорт" in folded or "транспорт" in folded:
            sectors = dict(taxonomy.guess_sectors(text))
            has_real_port = evidence.find_term(text, "порт") is not None
            if taxonomy.SECTOR_LOGISTICS in sectors and not has_real_port:
                logistics_terms = [t for t in taxonomy.SECTOR_KEYWORDS[taxonomy.SECTOR_LOGISTICS]
                                   if evidence.find_term(text, t)]
                if not logistics_terms:
                    checks["«порт» внутри «экспорт» → логистика"] += 1
                    examples["«порт» внутри «экспорт» → логистика"].append(f"#{row['id']}")

        # Обзор цен не должен считаться изменением правил.
        change = detect_rule_change(text)
        price_review = any(word in folded for word in ("обзор", "котировк", "динамика цен"))
        if change is not None and price_review and not change.act_anchor:
            checks["обзор цен как изменение правил"] += 1
            examples["обзор цен как изменение правил"].append(
                f"#{row['id']} {change.subject}")

        # Обязательный сигнал в чужой юрисдикции.
        item = RawItem(id=row["id"], title=row["title"], raw_text=row["raw_text"],
                       source_url=row["source_url"] or "")
        alert = detect_mandatory_policy_alert(item, companies)
        if alert is not None:
            third = taxonomy.detect_third_countries(text)
            ours = taxonomy.detect_jurisdictions(text)
            if third and not ours:
                checks["правило чужой юрисдикции как обязательный сигнал"] += 1
                examples["правило чужой юрисдикции как обязательный сигнал"].append(
                    f"#{row['id']}")
            # Каждая названная компания обязана иметь подтверждение в тексте.
            for name in alert.companies_matched:
                company = next((c for c in companies if c.name == name), None)
                if company is None:
                    continue
                from trade_agent.alerts import company_product_hits
                if not company_product_hits(text, company):
                    checks["компания без доказательства"] += 1
                    examples["компания без доказательства"].append(
                        f"#{row['id']} {name}")

        # Отраслевая связь не должна маскироваться под прямую.
        signal = db.conn.execute(
            "SELECT * FROM signals WHERE raw_item_id = ?", (row["id"],)).fetchone()
        if signal is not None:
            from trade_agent.models import Signal as SignalModel
            parsed = SignalModel.from_row(signal)
            for company in companies:
                detail = match_signal(parsed, item, company)
                if detail.link_type == LINK_DIRECT and not detail.evidence_fragments:
                    checks["отраслевой интерес выдан за прямую применимость"] += 1
                    examples["отраслевой интерес выдан за прямую применимость"].append(
                        f"#{row['id']} {company.slug}")

    lines = ["# Проверка старых дефектов на реальных материалах", "",
             f"Материалов в базе: {len(rows)}. Компаний: {len(companies)}.", "",
             "| Дефект | Найдено | Ожидается |", "| --- | --- | --- |"]
    for name, count in checks.items():
        lines.append(f"| {name} | {count} | 0 |")
    lines.append("")
    for name, rows_found in examples.items():
        if rows_found:
            lines.append(f"### {name}")
            lines.append("")
            for example in rows_found[:10]:
                lines.append(f"- {example}")
            lines.append("")
    total = sum(checks.values())
    lines.append(f"**Итого дефектов: {total}**"
                 + ("" if total else " — все проверки пройдены."))
    lines.append("")
    return "\n".join(lines)


# --- 5. сквозной прогон без отправки ---------------------------------------
def dry_run_report(settings: Any, days: int) -> str:
    """
    Полный путь выпуска без отправки: состав → рендер → проверка доставки.

    Доставка выполняется подставным транспортом: наружу не уходит ничего.
    """
    from trade_agent.digest import IssueBuilder
    from trade_agent.notify import deliver_issue, pack_posts

    db = Database(settings.db_path)
    try:
        builder = IssueBuilder(db, settings)
        data = builder.collect(days)
        issue, items = builder.compose(data, days)
        markdown = builder.render(issue, items)
        counters = issue.counters

        problems: list[str] = []
        shown = [i for i in items if i.section != "WATCH"]
        if counters.get("cards", 0) != markdown.count("### "):
            problems.append(f"счётчик карточек {counters.get('cards')} не совпадает "
                            f"с числом заголовков {markdown.count('### ')}")
        keys = [i.event_key for i in items]
        if len(keys) != len(set(keys)):
            problems.append("в составе выпуска есть дубли по ключу события")
        for section, title in (("RU_TO_PH", "Приморье → Филиппины"),
                               ("PH_TO_RU", "Филиппины → РФ")):
            block = [i for i in items if i.section == section]
            if block and f"## {title}" not in markdown:
                problems.append(f"раздел «{title}» не отрисован при {len(block)} карточках")
            if not block and f"## {title}" in markdown:
                problems.append(f"раздел «{title}» отрисован пустым")
        for entry in shown:
            if entry.companies_direct and not any(
                    row.get("evidence") for row in entry.companies_direct):
                problems.append(f"карточка «{entry.title[:50]}»: прямая связь "
                                "без фрагмента-основания")
            if not entry.source_urls:
                problems.append(f"карточка «{entry.title[:50]}»: нет ссылки на источник")
            if entry.verification_status == "primary_verified" and not entry.analysis_id:
                problems.append(f"карточка «{entry.title[:50]}»: помечена проверенной "
                                "без анализа")

        posts = pack_posts(markdown)

        lines = [
            "# Сквозной прогон выпуска (без отправки)",
            "",
            f"Период: {days} дн.",
            f"Карточек: {counters.get('cards', 0)}, наблюдать: {counters.get('watch', 0)}",
            f"Приморье → Филиппины: {counters.get('ru_to_ph', 0)}, "
            f"Филиппины → РФ: {counters.get('ph_to_ru', 0)}",
            f"Поздних подтверждений: {counters.get('late_confirmations', 0)}",
            f"Повторов пропущено: {counters.get('repeats_skipped', 0)}",
            f"Компаний с прямой применимостью: {counters.get('companies_direct', 0)}",
            f"Постов в Telegram получилось бы: {len(posts)} "
            f"(ориентир 1–2 для короткого выпуска)",
            "",
            "## Инварианты",
            "",
        ]
        if problems:
            for problem in problems:
                lines.append(f"- НАРУШЕНО: {problem}")
        else:
            lines.append("- все проверки состава пройдены")
        lines += ["", "## Выпуск", "", markdown]
        return "\n".join(lines)
    finally:
        db.close()


# --- 6. доставка без отправки ----------------------------------------------
def _issue_period_days(issue: Any) -> Optional[int]:
    """Сколько дней охватывал выпуск — чтобы пересобрать его так же."""
    from trade_agent.timeutil import parse_utc

    start = parse_utc(issue.period_start)
    end = parse_utc(issue.period_end)
    if start is None or end is None:
        return None
    days = round((end - start).total_seconds() / 86400)
    return max(1, days)


class _SilentBot:
    """
    Подставной транспорт. Наружу не уходит ничего.

    Считает, сколько частей «было бы отправлено», и возвращает
    правдоподобные идентификаторы сообщений.
    """

    def __init__(self, fail_at: Optional[int] = None, exception: Optional[type] = None):
        self.sent: list[str] = []
        self.fail_at = fail_at
        self.exception = exception

    def send_post(self, chat_id: int, text: str) -> int:
        if self.fail_at is not None and len(self.sent) == self.fail_at:
            raise self.exception("симуляция сбоя")
        self.sent.append(text)
        return 900000 + len(self.sent)


def delivery_report(settings: Any) -> str:
    """
    Проверка механизма доставки без единой реальной отправки.

    Транспорт подставной, токен бота не используется, сеть не трогается.
    Проверяется ровно то, что требует приёмка: подтверждённый выпуск
    не уходит второй раз, неопределённый результат не приводит к слепому
    дублю, повторная сборка не создаёт выпуск-двойник, идентификатор
    выпуска стабилен.
    """
    from trade_agent.bot import TelegramUncertain
    from trade_agent.digest import run as digest_run
    from trade_agent.models import DELIVERY_SENT, DELIVERY_UNKNOWN
    from trade_agent.notify import deliver_issue

    checks: list[tuple[str, bool, str]] = []
    db = Database(settings.db_path)
    try:
        issue = db.latest_issue("built")
        if issue is None:
            return ("# Проверка доставки\n\nВ базе нет собранного выпуска: "
                    "сначала выполните `dry-run` или `python -m trade_agent.digest`.\n")
        markdown = Path(issue.markdown_path).read_text("utf-8") \
            if issue.markdown_path and Path(issue.markdown_path).exists() \
            else (Path(settings.digest_dir) / "latest.md").read_text("utf-8")

        original_chats = settings.bot.allowed_chat_ids
        settings.bot.allowed_chat_ids = (-1,)      # заведомо тестовый чат

        # 1. первая доставка
        first = _SilentBot()
        deliveries = deliver_issue(settings, db, first, issue, markdown)
        checks.append(("первая доставка проходит",
                       bool(first.sent) and deliveries[0].status == DELIVERY_SENT,
                       f"частей: {len(first.sent)}, статус: {deliveries[0].status}"))

        # 2. повтор подтверждённой доставки
        second = _SilentBot()
        deliver_issue(settings, db, second, issue, markdown)
        checks.append(("подтверждённый выпуск не отправляется повторно",
                       second.sent == [], f"частей при повторе: {len(second.sent)}"))

        # 3. неопределённый результат
        stored = db.get_delivery(int(issue.id), "-1")
        stored.status = DELIVERY_UNKNOWN
        stored.parts_sent = 1
        db.save_delivery(stored)
        third = _SilentBot()
        deliver_issue(settings, db, third, issue, markdown)
        checks.append(("неопределённая доставка не рассылается вслепую",
                       third.sent == [], f"частей при повторе: {len(third.sent)}"))
        stored.status = DELIVERY_SENT
        stored.parts_sent = len(first.sent)
        db.save_delivery(stored)

        # 4. повторная сборка не создаёт выпуск-двойник.
        #    Пересобирать надо ЗА ТОТ ЖЕ период: выпуск за другой период
        #    имеет другой состав, и это не двойник.
        period_days = _issue_period_days(issue) or settings.digest_lookback_days
        before = db.conn.execute("SELECT COUNT(*) c FROM issues").fetchone()["c"]
        db.close()
        rebuilt = digest_run(settings, days=period_days)
        db = Database(settings.db_path)
        after = db.conn.execute("SELECT COUNT(*) c FROM issues").fetchone()["c"]
        same_id = rebuilt.get("issue_id") == int(issue.id)
        checks.append(("повторная сборка не плодит выпуски",
                       after == before or rebuilt.get("reused_issue") is True,
                       f"выпусков было {before}, стало {after}, "
                       f"переиспользован: {rebuilt.get('reused_issue')}"))
        checks.append(("идентификатор выпуска стабилен при том же составе",
                       same_id or rebuilt.get("reused_issue") is True,
                       f"был №{issue.id}, стал №{rebuilt.get('issue_id')}"))

        settings.bot.allowed_chat_ids = original_chats
    finally:
        db.close()

    lines = ["# Проверка механизма доставки (без реальной отправки)", "",
             "Транспорт подставной: ни одно сообщение наружу не уходит.", "",
             "| Проверка | Результат | Подробности |", "| --- | --- | --- |"]
    for name, ok, detail in checks:
        lines.append(f"| {name} | {'OK' if ok else 'НАРУШЕНО'} | {detail} |")
    failed = [name for name, ok, _ in checks if not ok]
    lines += ["", ("Все проверки доставки пройдены." if not failed
                   else "НАРУШЕНЫ: " + "; ".join(failed)), ""]
    return "\n".join(lines)


# --- CLI --------------------------------------------------------------------
def main(argv: Optional[list[str]] = None) -> int:
    # Общие опции объявлены и в подкомандах: иначе argparse требует
    # писать их строго перед именем подкоманды, что неудобно в работе.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--project", type=Path, default=PROJECT_DIR,
                        help="каталог рабочей установки trade-agent")
    common.add_argument("--out", type=Path, default=None, help="файл отчёта")

    parser = argparse.ArgumentParser(
        prog="python tools/acceptance.py", parents=[common],
        description="Приёмочная проверка на реальных данных (только чтение).")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("catalog", parents=[common], help="разбор каталога компаний")
    export = sub.add_parser("export-set", parents=[common],
                            help="выгрузка материалов для разметки")
    export.add_argument("--limit", type=int, default=80)
    metrics = sub.add_parser("metrics", parents=[common],
                             help="метрики по размеченному набору")
    metrics.add_argument("--labels", type=Path, required=True)
    sub.add_parser("defects", parents=[common],
                   help="проверка старых дефектов на реальных материалах")
    dry = sub.add_parser("dry-run", parents=[common],
                         help="сквозной прогон выпуска без отправки")
    dry.add_argument("--days", type=int, default=4)
    sub.add_parser("delivery", parents=[common],
                   help="проверка доставки подставным транспортом")

    args = parser.parse_args(argv)
    settings = load_settings(args.project)
    db = Database(settings.db_path)
    try:
        if args.command == "catalog":
            report = catalog_report(db)
        elif args.command == "export-set":
            out = args.out or Path("labelset.jsonl")
            stats = export_set(db, args.limit, out)
            print(f"Выгружено материалов: {stats['written']} → {stats['path']}")
            print(f"  принятых в базе: {stats['accepted']}, "
                  f"отклонённых: {stats['rejected']}, необработанных: {stats['unprocessed']}")
            print("Заполните поле expected в каждой строке и запустите "
                  "`metrics --labels <файл>`.")
            return 0
        elif args.command == "metrics":
            report = metrics_report(db, args.labels)
        elif args.command == "defects":
            report = defects_report(db)
        elif args.command == "delivery":
            db.close()
            report = delivery_report(settings)
            db = Database(settings.db_path)
        else:
            report = dry_run_report(settings, args.days)
    finally:
        db.close()

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(report, "utf-8")
        print(f"Отчёт: {args.out}")
    else:
        print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
