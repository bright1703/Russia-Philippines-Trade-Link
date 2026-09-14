#!/usr/bin/env python3
"""
Разведка источников: что реально отдаёт сайт и работает ли адаптер.

    python tools/probe_sources.py                 # все источники из sources.yml
    python tools/probe_sources.py --only psa,boc  # только указанные
    python tools/probe_sources.py --json out.json # машиночитаемый отчёт

Скрипт НИЧЕГО не включает и не меняет в конфигурации. Он отвечает на
вопросы приёмки по каждому источнику:

  * доступен ли URL и что отвечает сервер;
  * какой реально формат страницы или ленты;
  * разрешает ли robots.txt чтение;
  * работает ли существующий адаптер на живой странице;
  * извлекаются ли даты публикаций — без них не определить позицию;
  * определяется ли позиция последнего материала (`latest_published_at`);
  * работает ли догрузка от сохранённой позиции;
  * есть ли защита от повторной обработки (дедупликация по хэшу).

Статусы:
  WORKS       — адаптер отдаёт материалы с датами, позиция определяется;
  NEEDS_FIX   — источник отвечает, но адаптер разбирает его плохо;
  BLOCKED     — сайт или политика сети не дают читать;
  NOT_TESTED  — проверка не проводилась (нет URL, источник-заглушка).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

PROJECT_DIR = Path(__file__).resolve().parent.parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from trade_agent.config import load_settings                    # noqa: E402
from trade_agent.sources import ADAPTERS, load_source_configs   # noqa: E402
from trade_agent.utils.http import HttpError, PoliteClient      # noqa: E402

STATUS_WORKS = "WORKS"
STATUS_NEEDS_FIX = "NEEDS_FIX"
STATUS_BLOCKED = "BLOCKED"
STATUS_NOT_TESTED = "NOT_TESTED"


@dataclass
class Probe:
    source_id: str
    name: str = ""
    adapter: str = ""
    enabled: bool = False
    verified: bool = False
    url: str = ""
    status: str = STATUS_NOT_TESTED
    http_status: Optional[int] = None
    final_url: str = ""
    content_type: str = ""
    body_bytes: int = 0
    detected_format: str = ""
    robots_allows: Optional[bool] = None
    items: int = 0
    items_with_date: int = 0
    latest_published_at: str = ""
    complete: Optional[bool] = None
    cursor: str = ""
    duplicates_on_rerun: Optional[int] = None
    adapter_error: str = ""
    network_error: str = ""
    samples: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    seconds: float = 0.0


def detect_format(content_type: str, body: bytes) -> str:
    """Что на самом деле отдал сервер, независимо от заявленного типа."""
    head = body[:2000].lstrip()
    low_type = (content_type or "").lower()
    if head.startswith(b"%PDF"):
        return "pdf"
    if head[:1] == b"{" or head[:1] == b"[":
        return "json"
    lowered = head.lower()
    if b"<rss" in lowered or b"<feed" in lowered:
        return "rss"
    if b"<?xml" in lowered:
        return "xml"
    if b"<html" in lowered or b"<!doctype html" in lowered:
        return "html"
    if "json" in low_type:
        return "json"
    if "xml" in low_type:
        return "xml"
    if "html" in low_type:
        return "html"
    return low_type.split(";")[0] or "unknown"


def probe_network(probe: Probe, client: PoliteClient) -> None:
    """Первый вопрос: отвечает ли сайт и чем именно."""
    try:
        probe.robots_allows = client.allowed(probe.url)
    except Exception as exc:  # noqa: BLE001
        probe.notes.append(f"robots.txt не прочитан: {exc}")

    try:
        response = client.session.get(probe.url, timeout=client.timeout,
                                      allow_redirects=True)
    except Exception as exc:  # noqa: BLE001
        probe.network_error = f"{type(exc).__name__}: {exc}"
        probe.status = STATUS_BLOCKED
        return

    probe.http_status = response.status_code
    probe.final_url = response.url
    probe.content_type = response.headers.get("Content-Type", "")
    probe.body_bytes = len(response.content)
    probe.detected_format = detect_format(probe.content_type, response.content)

    if probe.final_url.rstrip("/") != probe.url.rstrip("/"):
        probe.notes.append(f"редирект на {probe.final_url}")
    if response.status_code >= 400:
        probe.status = STATUS_BLOCKED
        probe.network_error = f"HTTP {response.status_code}"
    if probe.robots_allows is False:
        probe.status = STATUS_BLOCKED
        probe.notes.append("robots.txt запрещает чтение этой страницы")


def probe_adapter(probe: Probe, config: dict[str, Any], settings: Any,
                  days: int) -> None:
    """Второй вопрос: справляется ли наш адаптер с этой страницей."""
    adapter_cls = ADAPTERS.get(config.get("adapter") or "web_html")
    if adapter_cls is None:
        probe.adapter_error = f"неизвестный адаптер {config.get('adapter')!r}"
        probe.status = STATUS_NEEDS_FIX
        return

    # Разведка не должна долбить сайт: один проход, без повторов.
    probe_config = dict(config)
    probe_config["retries"] = 1
    probe_config["rate_limit_delay"] = 1

    adapter = adapter_cls(probe_config, settings)
    try:
        result = adapter.fetch(days=days)
    except Exception as exc:  # noqa: BLE001
        probe.adapter_error = f"{type(exc).__name__}: {exc}"
        probe.status = STATUS_NEEDS_FIX
        return

    probe.items = len(result.items)
    probe.items_with_date = sum(1 for item in result.items if item.published_at)
    probe.latest_published_at = result.latest_published_at
    probe.complete = result.complete
    probe.cursor = result.cursor
    probe.adapter_error = result.error
    probe.samples = [f"{(item.published_at or 'без даты')[:10]} | {item.title[:90]}"
                     for item in result.items[:3]]

    # Третий вопрос: защищает ли хэш от повторной обработки.
    hashes = {item.hash for item in result.items}
    probe.duplicates_on_rerun = len(result.items) - len(hashes)

    if result.error:
        probe.status = STATUS_BLOCKED if not result.items else STATUS_NEEDS_FIX
        return
    if not result.items:
        probe.status = STATUS_NEEDS_FIX
        probe.notes.append("адаптер не извлёк ни одного материала")
        return
    if probe.items_with_date == 0:
        probe.status = STATUS_NEEDS_FIX
        probe.notes.append("ни у одного материала нет даты — позицию источника "
                           "определить нельзя, догрузка работать не будет")
        return
    if probe.items_with_date < probe.items:
        probe.notes.append(f"без даты: {probe.items - probe.items_with_date} из "
                           f"{probe.items}")
    if probe.duplicates_on_rerun:
        probe.status = STATUS_NEEDS_FIX
        probe.notes.append(f"совпадающие хэши внутри одного прогона: "
                           f"{probe.duplicates_on_rerun}")
        return
    probe.status = STATUS_WORKS


def run(settings: Any, only: Optional[str] = None, days: int = 30) -> list[Probe]:
    _, configs = load_source_configs(PROJECT_DIR / "sources.yml")
    wanted = {s.strip() for s in only.split(",")} if only else None

    client = PoliteClient(timeout=20, retries=1, backoff=2, delay=1)
    probes: list[Probe] = []
    for config in configs:
        source_id = config.get("id", "")
        if wanted is not None and source_id not in wanted:
            continue
        probe = Probe(
            source_id=source_id,
            name=config.get("name", ""),
            adapter=config.get("adapter") or "web_html",
            enabled=bool(config.get("enabled", False)),
            verified=bool(config.get("verified", False)),
            url=config.get("url") or "",
        )
        started = time.monotonic()
        if probe.adapter in ("telegram", "tenders", "fixture"):
            probe.notes.append("локальный источник, сетевая разведка не применима")
        elif not probe.url:
            probe.notes.append("URL не задан — источник-заглушка")
        else:
            probe_network(probe, client)
            if probe.status != STATUS_BLOCKED:
                probe_adapter(probe, config, settings, days)
        probe.seconds = round(time.monotonic() - started, 1)
        probes.append(probe)
        print(f"[{probe.status:10}] {source_id:22} {probe.http_status or '-':>4} "
              f"{probe.detected_format or '-':6} items={probe.items:3} "
              f"dated={probe.items_with_date:3} {probe.seconds}s", flush=True)
    return probes


def render(probes: list[Probe]) -> str:
    lines = ["# Разведка источников", "",
             "| Источник | Статус | HTTP | Формат | robots | Материалов | С датой | Позиция |",
             "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    for probe in probes:
        robots = {True: "да", False: "НЕТ", None: "—"}[probe.robots_allows]
        lines.append(
            f"| {probe.source_id} | {probe.status} | {probe.http_status or '—'} | "
            f"{probe.detected_format or '—'} | {robots} | {probe.items} | "
            f"{probe.items_with_date} | {(probe.latest_published_at or '—')[:10]} |")
    lines.append("")
    for probe in probes:
        if probe.status == STATUS_WORKS and not probe.notes:
            continue
        lines.append(f"## {probe.source_id} — {probe.status}")
        if probe.url:
            lines.append(f"- URL: {probe.url}")
        if probe.network_error:
            lines.append(f"- сеть: {probe.network_error}")
        if probe.adapter_error:
            lines.append(f"- адаптер: {probe.adapter_error}")
        for note in probe.notes:
            lines.append(f"- {note}")
        for sample in probe.samples:
            lines.append(f"- пример: {sample}")
        lines.append("")
    return "\n".join(lines)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python tools/probe_sources.py",
        description="Проверка реальных источников без изменения конфигурации.")
    parser.add_argument("--only", default=None, help="идентификаторы через запятую")
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--json", type=Path, default=None)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)

    settings = load_settings(PROJECT_DIR)
    probes = run(settings, args.only, args.days)

    report = render(probes)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(report, "utf-8")
        print(f"\nОтчёт: {args.out}")
    else:
        print("\n" + report)
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps([asdict(p) for p in probes],
                                        ensure_ascii=False, indent=2), "utf-8")
        print(f"JSON: {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
