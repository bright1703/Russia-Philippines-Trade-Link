"""Сущности конвейера. Простые dataclass-объекты без внешних зависимостей."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

# --- категории Scout -------------------------------------------------------
CATEGORIES = (
    "MEAT", "FOOD", "AGRICULTURE", "FERTILIZER", "GRAIN", "AUTO", "EQUIPMENT",
    "PACKAGING", "ENERGY", "LOGISTICS", "REGULATION", "TENDER", "IMPORT", "OTHER",
)

SOURCE_TYPES = ("telegram", "tender", "web", "manual")

# FAILED — служебный вердикт fail-closed: рецензия не состоялась.
# Он НИКОГДА не означает «проверено и годно».
VERDICT_PASS = "PASS"
VERDICT_REVISE = "REVISE"
VERDICT_REJECT = "REJECT"
VERDICT_FAILED = "FAILED"
VERDICTS = (VERDICT_PASS, VERDICT_REVISE, VERDICT_REJECT, VERDICT_FAILED)

# Причины несостоявшейся рецензии.
REVIEW_ERROR_UNAVAILABLE = "reviewer_unavailable"      # временная: сеть/лимит/нет ключа
REVIEW_ERROR_EMPTY = "reviewer_empty_response"         # временная: модель вернула пустоту
REVIEW_ERROR_INVALID = "reviewer_invalid_response"     # временная: не разобрался JSON
REVIEW_ERROR_UNKNOWN_VERDICT = "reviewer_unknown_verdict"   # постоянная: мусор в поле
REVIEW_ERROR_MAX_REVISIONS = "reviewer_max_revisions"       # постоянная: не сошлось

SIGNAL_NEW = "new"
SIGNAL_ANALYZED = "analyzed"          # разобрано и подтверждено рецензентом
SIGNAL_REJECTED = "rejected"          # шум или REJECT рецензента
SIGNAL_FAILED = "failed"              # постоянная ошибка, повтор бесполезен
SIGNAL_NEEDS_REVIEW = "needs_review"  # требует ручной проверки человеком

# Статусы, при которых сигнал НЕ публикуется и не порождает matches.
UNPUBLISHED_STATUSES = (SIGNAL_REJECTED, SIGNAL_FAILED, SIGNAL_NEEDS_REVIEW)


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _unjson(value: Any, default: Any) -> Any:
    if value in (None, ""):
        return default
    if isinstance(value, (list, dict)):
        return value
    try:
        return json.loads(value)
    except (ValueError, TypeError):
        return default


@dataclass
class RawItem:
    """Исходный материал: сообщение, объявление, страница, документ."""
    id: Optional[int] = None
    source: str = ""              # конкретный источник, напр. "bfar_bids"
    source_type: str = "web"      # telegram | tender | web | manual
    source_url: str = ""
    external_id: str = ""         # id сообщения / номер закупки
    title: str = ""
    raw_text: str = ""
    published_at: str = ""
    fetched_at: str = field(default_factory=utcnow)
    hash: str = ""
    meta: dict[str, Any] = field(default_factory=dict)

    def to_row(self) -> dict[str, Any]:
        row = asdict(self)
        row["meta"] = _json(self.meta)
        row.pop("id", None)
        return row

    @staticmethod
    def from_row(row: Any) -> "RawItem":
        data = dict(row)
        data["meta"] = _unjson(data.get("meta"), {})
        return RawItem(**data)


@dataclass
class Signal:
    """Материал, прошедший Scout."""
    id: Optional[int] = None
    raw_item_id: int = 0
    category: str = "OTHER"
    relevance_score: int = 0
    reason: str = ""
    companies_matched: list[str] = field(default_factory=list)
    hs_codes: list[str] = field(default_factory=list)
    matched_products: list[str] = field(default_factory=list)
    geography: str = ""
    needs_deep_analysis: bool = False
    must_alert: bool = False
    status: str = SIGNAL_NEW
    review_attempts: int = 0       # сколько раз пытались получить рецензию
    last_error: str = ""           # код последней ошибки конвейера
    created_at: str = field(default_factory=utcnow)

    @property
    def published(self) -> bool:
        """Сигнал считается подтверждённым только после PASS рецензента."""
        return self.status == SIGNAL_ANALYZED

    @property
    def unverified(self) -> bool:
        """
        True, если сигнал НЕ подтверждён рецензентом.

        Сюда попадают и явные статусы (failed / needs_review / rejected),
        и промежуточное состояние «анализ выполнялся, но подтверждения нет»
        (status=new при review_attempts > 0). Такой сигнал не публикуется
        и не порождает matches.
        """
        if self.status in UNPUBLISHED_STATUSES:
            return True
        return self.status != SIGNAL_ANALYZED and int(self.review_attempts or 0) > 0

    def to_row(self) -> dict[str, Any]:
        row = asdict(self)
        row["companies_matched"] = _json(self.companies_matched)
        row["hs_codes"] = _json(self.hs_codes)
        row["matched_products"] = _json(self.matched_products)
        row["needs_deep_analysis"] = int(self.needs_deep_analysis)
        row["must_alert"] = int(self.must_alert)
        row.pop("id", None)
        return row

    @staticmethod
    def from_row(row: Any) -> "Signal":
        data = dict(row)
        data["companies_matched"] = _unjson(data.get("companies_matched"), [])
        data["hs_codes"] = _unjson(data.get("hs_codes"), [])
        data["matched_products"] = _unjson(data.get("matched_products"), [])
        data["needs_deep_analysis"] = bool(data.get("needs_deep_analysis"))
        data["must_alert"] = bool(data.get("must_alert"))
        data["review_attempts"] = int(data.get("review_attempts") or 0)
        data.setdefault("last_error", "")
        if data.get("last_error") is None:
            data["last_error"] = ""
        return Signal(**data)


@dataclass
class Analysis:
    """Результат Analyst — ответ на вопрос «что это значит для нашей работы»."""
    id: Optional[int] = None
    signal_id: int = 0
    company: str = ""
    summary: str = ""
    opportunity: str = ""
    risks: list[str] = field(default_factory=list)
    regulation: str = ""
    market_data: str = ""
    suggested_actions: list[str] = field(default_factory=list)
    what_to_verify: list[str] = field(default_factory=list)
    next_step: str = ""
    confidence: float = 0.0
    sources: list[str] = field(default_factory=list)
    revision: int = 0
    created_at: str = field(default_factory=utcnow)

    def to_row(self) -> dict[str, Any]:
        row = asdict(self)
        for key in ("risks", "suggested_actions", "what_to_verify", "sources"):
            row[key] = _json(getattr(self, key))
        row.pop("id", None)
        return row

    @staticmethod
    def from_row(row: Any) -> "Analysis":
        data = dict(row)
        for key in ("risks", "suggested_actions", "what_to_verify", "sources"):
            data[key] = _unjson(data.get(key), [])
        return Analysis(**data)


@dataclass
class Review:
    """
    Результат Reviewer — критика Analyst.

    По умолчанию вердикт FAILED: отсутствие подтверждения никогда
    не должно случайно оказаться разрешением к публикации.
    """
    id: Optional[int] = None
    analysis_id: int = 0
    verdict: str = VERDICT_FAILED
    problems: list[str] = field(default_factory=list)
    corrected_fields: dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.0
    error: str = ""            # код причины, если рецензия не состоялась
    retryable: bool = False    # можно ли повторить попытку позже
    created_at: str = field(default_factory=utcnow)

    @property
    def approved(self) -> bool:
        return self.verdict == VERDICT_PASS

    def to_row(self) -> dict[str, Any]:
        row = asdict(self)
        row["problems"] = _json(self.problems)
        row["corrected_fields"] = _json(self.corrected_fields)
        row["retryable"] = int(self.retryable)
        row.pop("id", None)
        return row

    @staticmethod
    def from_row(row: Any) -> "Review":
        data = dict(row)
        data["problems"] = _unjson(data.get("problems"), [])
        data["corrected_fields"] = _unjson(data.get("corrected_fields"), {})
        data["retryable"] = bool(data.get("retryable"))
        data.setdefault("error", "")
        return Review(**data)


@dataclass
class Company:
    """Профиль компании (источник — brain/companies/*.md, CSV или JSON)."""
    id: Optional[int] = None
    slug: str = ""
    name: str = ""
    website: str = ""
    products: list[str] = field(default_factory=list)
    product_aliases: list[str] = field(default_factory=list)
    hs_codes: list[str] = field(default_factory=list)
    categories: list[str] = field(default_factory=list)
    description: str = ""
    inn: str = ""
    export_countries: list[str] = field(default_factory=list)
    industry: str = ""
    contact_name: str = ""
    address: str = ""
    contacts: str = ""
    source_name: str = ""
    source_row: int = 0
    data_quality: list[str] = field(default_factory=list)
    export_experience: str = ""
    documents: list[str] = field(default_factory=list)
    status: str = ""
    restrictions: list[str] = field(default_factory=list)
    potential_buyers: list[str] = field(default_factory=list)
    regulators: list[str] = field(default_factory=list)
    history: str = ""
    next_step: str = ""
    region: str = "Приморский край"
    profile_path: str = ""
    updated_at: str = field(default_factory=utcnow)

    def to_row(self) -> dict[str, Any]:
        row = asdict(self)
        for key in ("products", "product_aliases", "hs_codes", "categories", "export_countries", "documents",
                    "restrictions", "potential_buyers", "regulators", "data_quality"):
            row[key] = _json(getattr(self, key))
        row.pop("id", None)
        return row

    @staticmethod
    def from_row(row: Any) -> "Company":
        data = dict(row)
        for key in ("products", "product_aliases", "hs_codes", "categories", "export_countries", "documents",
                    "restrictions", "potential_buyers", "regulators", "data_quality"):
            data[key] = _unjson(data.get(key), [])
        data.setdefault("product_aliases", [])
        data.setdefault("description", "")
        data.setdefault("inn", "")
        data.setdefault("export_countries", [])
        data.setdefault("industry", "")
        data.setdefault("contact_name", "")
        data.setdefault("address", "")
        data.setdefault("contacts", "")
        data.setdefault("source_name", "")
        data.setdefault("source_row", 0)
        data.setdefault("data_quality", [])
        return Company(**data)


@dataclass
class Match:
    """Результат Opportunity Radar: связь сигнала с компанией."""
    id: Optional[int] = None
    company_slug: str = ""
    signal_id: int = 0
    match_score: int = 0
    reason: str = ""
    recommended_action: str = ""
    created_at: str = field(default_factory=utcnow)

    def to_row(self) -> dict[str, Any]:
        row = asdict(self)
        row.pop("id", None)
        return row

    @staticmethod
    def from_row(row: Any) -> "Match":
        return Match(**dict(row))


@dataclass
class RunLog:
    """Журнал выполнения этапа конвейера."""
    id: Optional[int] = None
    stage: str = ""
    started_at: str = field(default_factory=utcnow)
    finished_at: str = ""
    status: str = "running"       # running | ok | partial | error
    processed: int = 0
    created: int = 0
    skipped: int = 0
    errors: int = 0
    retries: int = 0
    duration_sec: float = 0.0
    error_text: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    def to_row(self) -> dict[str, Any]:
        row = asdict(self)
        row["details"] = _json(self.details)
        row.pop("id", None)
        return row

    @staticmethod
    def from_row(row: Any) -> "RunLog":
        data = dict(row)
        data["details"] = _unjson(data.get("details"), {})
        return RunLog(**data)
