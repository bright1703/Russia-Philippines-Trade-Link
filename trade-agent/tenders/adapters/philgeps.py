"""
Адаптер PhilGEPS (notices.philgeps.gov.ph).

Публичный поиск отдаётся SPA-приложением, поэтому адаптер сначала
пробует открытые JSON-эндпоинты из sources.yml, затем — обычный HTML.
Если оба пути не сработали, адаптер честно сообщает об ошибке:
остальные источники продолжают обрабатываться.

Playwright здесь намеренно не используется в первой версии.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from bs4 import BeautifulSoup

from .base import BaseAdapter, FetchResult, SourceError
from .generic_html import GenericHtmlAdapter

FIELD_ALIASES = {
    "title": ("title", "notice_title", "bidNoticeTitle", "solicitationTitle", "subject"),
    "notice_id": ("referenceNumber", "reference_no", "refId", "notice_id", "solicitationNumber"),
    "agency": ("procuringEntity", "agency", "organization", "entityName"),
    "category": ("category", "classification", "businessCategory"),
    "closing_date": ("closingDate", "closing_date", "submissionDeadline", "deadline"),
    "publish_date": ("publishDate", "publish_date", "postedDate", "datePublished"),
    "budget": ("approvedBudget", "abc", "budget", "estimatedValue"),
    "location": ("areaOfDelivery", "location", "deliveryArea"),
    "description": ("description", "notice_description", "details"),
}


def _pick(row: dict[str, Any], names: tuple[str, ...]) -> Any:
    for name in names:
        if name in row and row[name] not in (None, ""):
            return row[name]
        lowered = {k.lower(): v for k, v in row.items()}
        if name.lower() in lowered and lowered[name.lower()] not in (None, ""):
            return lowered[name.lower()]
    return ""


class PhilgepsAdapter(BaseAdapter):
    adapter_id = "philgeps"

    def fetch(self, days: int = 7) -> FetchResult:
        result = FetchResult(source_id=self.source_id)
        errors: list[str] = []

        for endpoint in self.config.get("api_candidates") or []:
            try:
                items = self._try_api(endpoint, days)
                result.pages_fetched += 1
                if items:
                    result.items = items
                    return result
            except SourceError as exc:
                errors.append(f"{endpoint}: {exc}")
            except (ValueError, TypeError) as exc:
                errors.append(f"{endpoint}: неожиданный формат ответа ({exc})")

        try:
            fallback = GenericHtmlAdapter(self.config, self.client).fetch(days)
            result.pages_fetched += fallback.pages_fetched
            if fallback.items:
                result.items = fallback.items
                return result
            errors.append("HTML-страница не содержит распознаваемых объявлений (SPA)")
        except SourceError as exc:
            errors.append(f"HTML: {exc}")

        result.error = (
            "PhilGEPS недоступен для автоматического разбора. "
            + "; ".join(errors[:3])
        )
        return result

    # ------------------------------------------------------------------
    def _try_api(self, endpoint: str, days: int) -> list[dict[str, Any]]:
        since = (date.today() - timedelta(days=max(days, 1))).isoformat()
        params = {
            "PublishDateFrom": since,
            "PublishDateTo": date.today().isoformat(),
            "PageNumber": 1,
            "PageSize": int(self.config.get("max_items_per_source") or 60),
        }
        response = self.client.get(endpoint, params=params)
        content_type = response.headers.get("Content-Type", "")
        if "json" not in content_type.lower():
            raise SourceError(f"ожидался JSON, получен {content_type or 'неизвестный тип'}")
        payload = response.json()
        rows = self._rows(payload)
        if not rows:
            return []
        return [self._to_item(row) for row in rows]

    @staticmethod
    def _rows(payload: Any) -> list[dict[str, Any]]:
        if isinstance(payload, list):
            return [r for r in payload if isinstance(r, dict)]
        if isinstance(payload, dict):
            for key in ("items", "data", "results", "records", "value", "Notices"):
                value = payload.get(key)
                if isinstance(value, list):
                    return [r for r in value if isinstance(r, dict)]
                if isinstance(value, dict):
                    for nested in value.values():
                        if isinstance(nested, list):
                            return [r for r in nested if isinstance(r, dict)]
        return []

    def _to_item(self, row: dict[str, Any]) -> dict[str, Any]:
        notice_id = str(_pick(row, FIELD_ALIASES["notice_id"]) or "")
        template = self.config.get("detail_url_template") or ""
        url = template.format(notice_id=notice_id) if (template and notice_id) else self.config.get("url", "")
        description = _pick(row, FIELD_ALIASES["description"])
        if isinstance(description, str) and "<" in description:
            description = BeautifulSoup(description, "html.parser").get_text(" ", strip=True)
        return {
            "title": str(_pick(row, FIELD_ALIASES["title"]) or ""),
            "notice_id": notice_id,
            "agency": str(_pick(row, FIELD_ALIASES["agency"]) or ""),
            "category": str(_pick(row, FIELD_ALIASES["category"]) or ""),
            "closing_date": _pick(row, FIELD_ALIASES["closing_date"]),
            "publish_date": _pick(row, FIELD_ALIASES["publish_date"]),
            "budget": _pick(row, FIELD_ALIASES["budget"]) or None,
            "currency": "PHP",
            "location": str(_pick(row, FIELD_ALIASES["location"]) or ""),
            "text": str(description or ""),
            "url": url,
            "language": "en",
            "attachment_urls": [],
        }
