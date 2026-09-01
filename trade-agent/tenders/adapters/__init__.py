"""Реестр адаптеров источников."""

from __future__ import annotations

from typing import Any

from .base import BaseAdapter, FetchResult, HttpClient, SourceError
from .generic_html import GenericHtmlAdapter
from .fixture import FixtureAdapter
from .philgeps import PhilgepsAdapter

REGISTRY: dict[str, type[BaseAdapter]] = {
    GenericHtmlAdapter.adapter_id: GenericHtmlAdapter,
    PhilgepsAdapter.adapter_id: PhilgepsAdapter,
    FixtureAdapter.adapter_id: FixtureAdapter,
}


def register(adapter_cls: type[BaseAdapter]) -> None:
    """Точка расширения: новый источник добавляется одной строкой."""
    REGISTRY[adapter_cls.adapter_id] = adapter_cls


def build_adapter(source_cfg: dict[str, Any], client: HttpClient) -> BaseAdapter:
    name = source_cfg.get("adapter") or "generic_html"
    if name not in REGISTRY:
        raise SourceError(f"неизвестный адаптер '{name}' для источника {source_cfg.get('id')}")
    return REGISTRY[name](source_cfg, client)


__all__ = [
    "BaseAdapter", "FetchResult", "HttpClient", "SourceError",
    "GenericHtmlAdapter", "PhilgepsAdapter", "FixtureAdapter", "REGISTRY", "register", "build_adapter",
]
