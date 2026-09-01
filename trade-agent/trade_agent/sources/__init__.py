"""
Реестр источников.

Добавление источника:
  1. запись в trade-agent/sources.yml;
  2. при необходимости — новый класс-адаптер и строка в ADAPTERS.
Основной конвейер при этом не меняется.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Optional

import yaml

from .base import SourceAdapter, SourceResult
from .telegram_source import TelegramSource
from .tenders_source import TendersSource
from .web.generic import GenericWebAdapter, RssAdapter
from .web.fixture import FixtureWebAdapter

LOG = logging.getLogger("trade_agent.sources")

ADAPTERS: dict[str, type[SourceAdapter]] = {
    "telegram": TelegramSource,
    "tenders": TendersSource,
    "web_html": GenericWebAdapter,
    "web_rss": RssAdapter,
    "fixture": FixtureWebAdapter,
}


def register(name: str, adapter_cls: type[SourceAdapter]) -> None:
    ADAPTERS[name] = adapter_cls


def load_source_configs(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    data = yaml.safe_load(Path(path).read_text("utf-8")) or {}
    defaults = data.get("defaults") or {}
    merged: list[dict[str, Any]] = []
    for source in data.get("sources") or []:
        cfg = dict(defaults)
        cfg.update(source)
        merged.append(cfg)
    return defaults, merged


def apply_fixtures(configs: list[dict[str, Any]], fixtures_dir: Path) -> list[dict[str, Any]]:
    """
    Офлайн-режим: подменяет сетевые адаптеры локальными фикстурами.
    Используется тестами и проверочными прогонами без интернета.
    """
    fixtures_dir = Path(fixtures_dir)
    for cfg in configs:
        source_id = cfg.get("id", "")
        if cfg.get("adapter") == "tenders":
            cfg["fixtures_dir"] = str(fixtures_dir / "tenders")
            continue
        if cfg.get("adapter") == "telegram":
            cfg["mode"] = "export"
            cfg["export_dir"] = str(fixtures_dir / "telegram")
            cfg["enabled"] = (fixtures_dir / "telegram").exists()
            continue
        candidate = fixtures_dir / f"{source_id}.html"
        if candidate.exists():
            cfg["adapter"] = "fixture"
            cfg["fixture_path"] = str(candidate)
            cfg["enabled"] = True
        else:
            cfg["enabled"] = False
    return configs


def build_sources(settings: Any, only: Optional[str] = None,
                  config_path: Optional[Path] = None,
                  fixtures_dir: Optional[Path] = None) -> list[SourceAdapter]:
    path = Path(config_path or (settings.project_dir / "sources.yml"))
    _, configs = load_source_configs(path)

    fixtures = fixtures_dir or os.environ.get("TRADE_AGENT_FIXTURES")
    if fixtures:
        configs = apply_fixtures(configs, Path(fixtures))

    wanted = {s.strip() for s in only.split(",")} if only else None

    built: list[SourceAdapter] = []
    for cfg in configs:
        source_id = cfg.get("id", "")
        if wanted is not None:
            if source_id not in wanted:
                continue
        elif not cfg.get("enabled", False):
            continue
        adapter_name = cfg.get("adapter") or "web_html"
        adapter_cls = ADAPTERS.get(adapter_name)
        if adapter_cls is None:
            LOG.error("источник %s: неизвестный адаптер '%s'", source_id, adapter_name)
            continue
        built.append(adapter_cls(cfg, settings))
    return built


__all__ = ["SourceAdapter", "SourceResult", "ADAPTERS", "register",
           "build_sources", "load_source_configs", "apply_fixtures"]
