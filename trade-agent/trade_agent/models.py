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

# --- направление торговли --------------------------------------------------
# Отрасль отвечает на вопрос «какой товар», направление — «куда идёт товар».
# Смешивать их нельзя: одна и та же рыба интересна и в ту, и в другую сторону.
DIRECTION_RU_TO_PH = "RU_TO_PH"
DIRECTION_PH_TO_RU = "PH_TO_RU"
DIRECTION_BOTH = "BOTH"
DIRECTION_UNKNOWN = "UNKNOWN"
TRADE_DIRECTIONS = (DIRECTION_RU_TO_PH, DIRECTION_PH_TO_RU,
                    DIRECTION_BOTH, DIRECTION_UNKNOWN)

# --- тип события -----------------------------------------------------------
# Пошлина, тендер, логистика, спрос — это НЕ отрасли, а типы событий.
EVENT_DEMAND = "DEMAND"                  # изменение спроса
EVENT_SUPPLY = "SUPPLY"                  # изменение предложения
EVENT_MARKET_ACCESS = "MARKET_ACCESS"    # доступ на рынок, допуск предприятия
EVENT_RULE_CHANGE = "RULE_CHANGE"        # изменение правил, пошлин, квот
EVENT_LOGISTICS = "LOGISTICS"            # маршруты, ставки, сроки
EVENT_BUYER_REQUEST = "BUYER_REQUEST"    # запрос покупателя, тендер
EVENT_BUSINESS_CONTACT = "BUSINESS_CONTACT"  # миссия, встреча, выставка
EVENT_OTHER = "OTHER"
EVENT_TYPES = (EVENT_DEMAND, EVENT_SUPPLY, EVENT_MARKET_ACCESS, EVENT_RULE_CHANGE,
               EVENT_LOGISTICS, EVENT_BUYER_REQUEST, EVENT_BUSINESS_CONTACT, EVENT_OTHER)

# --- уровень проверки ------------------------------------------------------
# «Проверка текста моделью» и «проверка первичного документа» — разные вещи,
# и в выпуске они не должны выглядеть одинаково.
VERIFY_SOURCE_CLAIM = "source_claim"        # по сообщению источника
VERIFY_PRIMARY = "primary_verified"         # проверен первичный документ
VERIFY_NEEDS_CHECK = "needs_check"          # требуется уточнение
VERIFICATION_STATUSES = (VERIFY_SOURCE_CLAIM, VERIFY_PRIMARY, VERIFY_NEEDS_CHECK)

# --- вид связи компании с событием ----------------------------------------
# Прямая применимость и отраслевая связь не смешиваются никогда.
LINK_DIRECT = "direct"      # в материале есть товар, код или название компании
LINK_SECTOR = "sector"      # событие в отрасли компании, применимость не доказана
LINK_TYPES = (LINK_DIRECT, LINK_SECTOR)

# --- роль компании в сделке -----------------------------------------------
# Экспортёр из каталога не становится покупателем филиппинского товара
# только потому, что товар совпал.
ROLE_PRODUCER = "producer"
ROLE_EXPORTER = "exporter"
ROLE_IMPORTER = "importer"
ROLE_DISTRIBUTOR = "distributor"
ROLE_PROCESSOR = "processor"
ROLE_LOGISTICS = "logistics_provider"
ROLE_UNKNOWN = "unknown"
COMPANY_ROLES = (ROLE_PRODUCER, ROLE_EXPORTER, ROLE_IMPORTER, ROLE_DISTRIBUTOR,
                 ROLE_PROCESSOR, ROLE_LOGISTICS, ROLE_UNKNOWN)

# --- состояние доставки ----------------------------------------------------
DELIVERY_SENT = "sent"            # подтверждено
DELIVERY_PARTIAL = "partial"      # часть постов ушла
DELIVERY_UNKNOWN = "unknown"      # результат сетевого вызова неясен
DELIVERY_FAILED = "failed"
DELIVERY_STATUSES = (DELIVERY_SENT, DELIVERY_PARTIAL, DELIVERY_UNKNOWN, DELIVERY_FAILED)

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

    # --- раздельные ответы на раздельные вопросы --------------------------
    # «что произошло», «на какое направление влияет» и «кого это касается»
    # хранятся отдельно и не подменяют друг друга.
    trade_direction: str = DIRECTION_UNKNOWN
    event_type: str = EVENT_OTHER
    sectors: list[str] = field(default_factory=list)
    jurisdiction: str = ""              # чьё правило меняется
    destination_market: str = ""        # рынок назначения, не место новости
    origin_countries: list[str] = field(default_factory=list)
    # Фрагменты исходного текста, на которых основан вывод. Пустой список
    # означает, что доказательств нет, а не что они не понадобились.
    evidence_fragments: list[str] = field(default_factory=list)
    # Устойчивый идентификатор события: два перепоста одной новости
    # дают одну карточку.
    event_key: str = ""
    event_date: str = ""                # когда произошло событие
    effective_from: str = ""            # с какой даты действует правило
    deadline: str = ""
    first_seen_at: str = ""
    verification_status: str = VERIFY_SOURCE_CLAIM
    uncertainties: list[str] = field(default_factory=list)
    # Когда можно повторить дорогую попытку анализа. Пустое значение —
    # можно сейчас. Защита от бесконечных повторов в одном запуске.
    next_retry_at: str = ""

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
        for key in ("companies_matched", "hs_codes", "matched_products",
                    "sectors", "origin_countries", "evidence_fragments",
                    "uncertainties"):
            row[key] = _json(getattr(self, key))
        row["needs_deep_analysis"] = int(self.needs_deep_analysis)
        row["must_alert"] = int(self.must_alert)
        row.pop("id", None)
        return row

    @staticmethod
    def from_row(row: Any) -> "Signal":
        data = dict(row)
        for key in ("companies_matched", "hs_codes", "matched_products",
                    "sectors", "origin_countries", "evidence_fragments",
                    "uncertainties"):
            data[key] = _unjson(data.get(key), [])
        data["needs_deep_analysis"] = bool(data.get("needs_deep_analysis"))
        data["must_alert"] = bool(data.get("must_alert"))
        data["review_attempts"] = int(data.get("review_attempts") or 0)
        for key, default in (("last_error", ""), ("next_retry_at", ""),
                             ("jurisdiction", ""),
                             ("destination_market", ""), ("event_key", ""),
                             ("event_date", ""), ("effective_from", ""),
                             ("deadline", ""), ("first_seen_at", "")):
            if not data.get(key):
                data[key] = default
        if not data.get("trade_direction"):
            data["trade_direction"] = DIRECTION_UNKNOWN
        if not data.get("event_type"):
            data["event_type"] = EVENT_OTHER
        if not data.get("verification_status"):
            data["verification_status"] = VERIFY_SOURCE_CLAIM
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

    # --- диагностика без секретов -----------------------------------------
    # Причина пустых и неразобранных ответов модели в аудите установлена
    # не была, а увеличивать лимит наугад — не исправление. Здесь
    # сохраняется то, по чему причину можно установить: роль, модель,
    # причина остановки, длина итогового текста и расход токенов.
    # Ни промпт, ни ключи, ни текст источника сюда не попадают.
    role: str = "reviewer"
    model: str = ""
    provider: str = ""
    stop_reason: str = ""
    response_chars: int = 0
    input_tokens: int = 0
    output_tokens: int = 0

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
    # Отрасли и товарные группы новой таксономии. Исходная колонка
    # industry сохраняется как есть и не перезаписывается.
    sectors: list[str] = field(default_factory=list)
    # На чём основано отнесение к отрасли: колонка каталога, слово из
    # описания продукции, ручная проверка.
    sector_basis: list[str] = field(default_factory=list)
    # Роли в сделке. Экспортёр из каталога не становится покупателем
    # филиппинского товара автоматически.
    roles: list[str] = field(default_factory=lambda: [ROLE_UNKNOWN])
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
                    "restrictions", "potential_buyers", "regulators", "data_quality",
                    "sectors", "sector_basis", "roles"):
            row[key] = _json(getattr(self, key))
        row.pop("id", None)
        return row

    @staticmethod
    def from_row(row: Any) -> "Company":
        data = dict(row)
        for key in ("products", "product_aliases", "hs_codes", "categories", "export_countries", "documents",
                    "restrictions", "potential_buyers", "regulators", "data_quality",
                    "sectors", "sector_basis", "roles"):
            data[key] = _unjson(data.get(key), [])
        if not data.get("roles"):
            data["roles"] = [ROLE_UNKNOWN]
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
    """
    Результат Opportunity Radar: связь сигнала с компанией.

    Вид связи обязателен и виден человеку. «Прямая применимость» и
    «компании отрасли, применимость уточнить» — разные утверждения,
    и выдавать второе за первое нельзя.
    """
    id: Optional[int] = None
    company_slug: str = ""
    signal_id: int = 0
    match_score: int = 0
    reason: str = ""
    recommended_action: str = ""
    created_at: str = field(default_factory=utcnow)
    link_type: str = LINK_SECTOR
    # Фрагменты исходного материала, подтверждающие связь.
    evidence: list[str] = field(default_factory=list)
    # Роль компании в этом событии. Для PH_TO_RU роль unknown означает,
    # что покупателем компанию называть нельзя.
    role: str = ROLE_UNKNOWN

    @property
    def direct(self) -> bool:
        return self.link_type == LINK_DIRECT

    def to_row(self) -> dict[str, Any]:
        row = asdict(self)
        row["evidence"] = _json(self.evidence)
        row.pop("id", None)
        return row

    @staticmethod
    def from_row(row: Any) -> "Match":
        data = dict(row)
        data["evidence"] = _unjson(data.get("evidence"), [])
        if not data.get("link_type"):
            data["link_type"] = LINK_SECTOR
        if not data.get("role"):
            data["role"] = ROLE_UNKNOWN
        return Match(**data)


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


@dataclass
class IssueItem:
    """
    Одна карточка выпуска.

    Карточка — единственный источник правды о том, что человек увидел.
    Счётчики шапки, список компаний и посты в Telegram считаются по
    карточкам, а не по независимо выбранным строкам таблиц.
    """
    id: Optional[int] = None
    issue_id: int = 0
    position: int = 0
    section: str = ""
    event_key: str = ""
    signal_id: Optional[int] = None
    analysis_id: Optional[int] = None
    title: str = ""
    fact: str = ""
    meaning: str = ""
    trade_direction: str = DIRECTION_UNKNOWN
    event_type: str = EVENT_OTHER
    sectors: list[str] = field(default_factory=list)
    source_urls: list[str] = field(default_factory=list)
    published_at: str = ""
    verification_status: str = VERIFY_SOURCE_CLAIM
    # Анализ старого сигнала, готовый только сегодня, выходит один раз
    # с исходной датой и пометкой о позднем подтверждении.
    late_confirmation: bool = False
    urgent: bool = False
    action: str = ""
    updates: list[str] = field(default_factory=list)
    companies_direct: list[dict[str, Any]] = field(default_factory=list)
    companies_sector: list[dict[str, Any]] = field(default_factory=list)
    companies_total: int = 0
    # Отпечаток содержания события на момент публикации. По нему
    # следующий выпуск решает, было ли существенное обновление, и
    # показывает, что именно изменилось.
    fingerprint: dict[str, str] = field(default_factory=dict)
    created_at: str = field(default_factory=utcnow)

    _JSON_FIELDS = ("sectors", "source_urls", "companies_direct",
                    "companies_sector", "fingerprint", "updates")

    def to_row(self) -> dict[str, Any]:
        row = asdict(self)
        for key in self._JSON_FIELDS:
            row[key] = _json(getattr(self, key))
        row["late_confirmation"] = int(self.late_confirmation)
        row["urgent"] = int(self.urgent)
        row.pop("id", None)
        return row

    @staticmethod
    def from_row(row: Any) -> "IssueItem":
        data = dict(row)
        for key in IssueItem._JSON_FIELDS:
            data[key] = _unjson(data.get(key), {} if key == "fingerprint" else [])
        data["late_confirmation"] = bool(data.get("late_confirmation"))
        data["urgent"] = bool(data.get("urgent"))
        data["companies_total"] = int(data.get("companies_total") or 0)
        return IssueItem(**data)


@dataclass
class Issue:
    """Выпуск: период, охват сбора, честные счётчики и путь к файлу."""
    id: Optional[int] = None
    kind: str = "scheduled"
    period_start: str = ""
    period_end: str = ""
    built_at: str = field(default_factory=utcnow)
    status: str = "built"          # built | failed
    coverage: dict[str, Any] = field(default_factory=dict)
    counters: dict[str, Any] = field(default_factory=dict)
    markdown_path: str = ""
    # Хэш отрисованного текста — для сверки файла latest.md с выпуском.
    content_hash: str = ""
    # Хэш СОСТАВА выпуска: карточки, их порядок и отпечатки событий.
    # В него намеренно не входят время сборки и другие метки, иначе два
    # одинаковых по смыслу выпуска всегда выглядели бы разными.
    composition_hash: str = ""

    def to_row(self) -> dict[str, Any]:
        row = asdict(self)
        row["coverage"] = _json(self.coverage)
        row["counters"] = _json(self.counters)
        row.pop("id", None)
        return row

    @staticmethod
    def from_row(row: Any) -> "Issue":
        data = dict(row)
        data["coverage"] = _unjson(data.get("coverage"), {})
        data["counters"] = _unjson(data.get("counters"), {})
        return Issue(**data)


@dataclass
class Delivery:
    """
    Состояние доставки одного выпуска в один чат.

    Неопределённый результат сетевого вызова не считается ни успехом,
    ни поводом для слепой повторной рассылки.
    """
    id: Optional[int] = None
    issue_id: int = 0
    channel: str = "telegram"
    chat_id: str = ""
    status: str = DELIVERY_UNKNOWN
    parts_total: int = 0
    parts_sent: int = 0
    message_ids: list[int] = field(default_factory=list)
    error: str = ""
    created_at: str = field(default_factory=utcnow)
    updated_at: str = field(default_factory=utcnow)

    @property
    def confirmed(self) -> bool:
        return self.status == DELIVERY_SENT

    def to_row(self) -> dict[str, Any]:
        row = asdict(self)
        row["message_ids"] = _json(self.message_ids)
        row.pop("id", None)
        return row

    @staticmethod
    def from_row(row: Any) -> "Delivery":
        data = dict(row)
        data["message_ids"] = _unjson(data.get("message_ids"), [])
        return Delivery(**data)


@dataclass
class SourceState:
    """
    Позиция источника между запусками.

    Молчание исправного источника, ошибка загрузки и неподключённый
    источник — три разных состояния, и в выпуске они выглядят по-разному.
    """
    source_id: str = ""
    last_success_at: str = ""
    last_published_at: str = ""
    cursor: str = ""
    coverage_complete: bool = True
    last_error: str = ""
    items_last_run: int = 0
    new_last_run: int = 0
    updated_at: str = field(default_factory=utcnow)

    @property
    def never_ran(self) -> bool:
        return not self.last_success_at

    def to_row(self) -> dict[str, Any]:
        row = asdict(self)
        row["coverage_complete"] = int(self.coverage_complete)
        return row

    @staticmethod
    def from_row(row: Any) -> "SourceState":
        data = dict(row)
        data["coverage_complete"] = bool(data.get("coverage_complete"))
        return SourceState(**data)
