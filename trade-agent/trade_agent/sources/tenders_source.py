"""
Мост к существующему модулю trade-agent/tenders/.

Модуль tenders не изменяется и продолжает работать самостоятельно
(`python fetch_tenders.py`). Здесь мы переиспользуем его функции сбора,
нормализации, дедупликации и оценки, а результат кладём в общую базу
как raw_items с source_type='tender'.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Optional

from ..models import RawItem
from ..utils import content_hash
from .base import SourceAdapter, SourceResult


def _import_tenders(tenders_dir: Path):
    """Импортирует модули tenders, добавив их каталог в sys.path."""
    path = str(Path(tenders_dir).resolve())
    if path not in sys.path:
        sys.path.insert(0, path)
    import adapters as tender_adapters          # noqa: WPS433
    import fetch_tenders                        # noqa: WPS433
    import match as tender_match                # noqa: WPS433
    import normalize as tender_normalize        # noqa: WPS433
    return fetch_tenders, tender_normalize, tender_match, tender_adapters


class TendersSource(SourceAdapter):
    source_id = "tenders"
    source_type = "tender"

    def fetch(self, days: int = 7) -> SourceResult:
        from datetime import date

        result = SourceResult(source_id=self.source_id)
        tenders_dir = Path(self.config.get("path") or self.settings.tenders_dir)
        if not (tenders_dir / "fetch_tenders.py").exists():
            result.error = f"модуль tenders не найден: {tenders_dir}"
            return result

        try:
            fetch_tenders, tender_normalize, tender_match, tender_adapters = _import_tenders(tenders_dir)
        except ImportError as exc:
            result.error = f"не удалось импортировать модуль tenders: {exc}"
            return result

        today = date.today()
        try:
            defaults, all_sources = fetch_tenders.load_sources(tenders_dir / "sources.yml")
            profiles = tender_match.load_profiles(tenders_dir / "profiles.yml")
        except Exception as exc:  # noqa: BLE001
            result.error = f"конфигурация tenders повреждена: {exc}"
            return result

        selected = [s for s in all_sources if s.get("enabled")]
        fixtures = self.config.get("fixtures_dir")
        if fixtures:                       # офлайн-режим для тестов и отладки
            for cfg in selected:
                cfg["adapter"] = "fixture"
                cfg["fixture_path"] = str(Path(fixtures) / f"{cfg['id']}.html")
                cfg["follow_detail"] = False

        client = tender_adapters.HttpClient(defaults)
        notices, errors, checked = fetch_tenders.collect(selected, client, days, today)
        result.fetched_pages = checked
        notices = [n for n in notices if fetch_tenders.within_window(n, days, today)]
        notices = tender_normalize.dedupe_notices(notices)
        tender_match.score_all(notices, profiles)

        if errors:
            result.error = "; ".join(f"{k}: {v}" for k, v in list(errors.items())[:3])

        for notice in notices:
            # Отменённые и локальные закупки уже помечены модулем tenders;
            # сохраняем их как сырьё, отбор делает Scout и дайджест.
            item = RawItem(
                source=notice.source_id or "tenders",
                source_type="tender",
                source_url=notice.original_url,
                external_id=notice.notice_id or notice.canonical_id,
                title=notice.title,
                raw_text=notice.description or notice.raw_text,
                published_at=notice.publish_date,
                meta={
                    "agency": notice.agency,
                    "category": notice.category,
                    "closing_date": notice.closing_date,
                    "deadline_status": notice.deadline_status,
                    "status": notice.status,
                    "estimated_budget": notice.estimated_budget,
                    "currency": notice.currency,
                    "tender_match_score": notice.match_score,
                    "matched_profiles": notice.matched_profiles,
                    "eligibility_notes": notice.eligibility_notes,
                    "attachment_urls": notice.attachment_urls,
                    "source_ids": notice.source_ids,
                    "canonical_id": notice.canonical_id,
                },
            )
            item.hash = content_hash(item.source, item.external_id, item.title, item.raw_text)
            result.items.append(item)
        return result
