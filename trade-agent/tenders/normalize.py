"""
Нормализация тендерных объявлений филиппинских источников.

Модуль не ходит в сеть. На вход приходят «сырые» словари от адаптеров,
на выходе — единые объекты Notice, пригодные для сопоставления,
дедупликации и рендера дайджеста.

Правило проекта: ничего не выдумывать. Если поле не найдено —
оно остаётся пустым (""), а не заполняется догадкой.
"""

from __future__ import annotations

import difflib
import hashlib
import html as html_lib
import re
import unicodedata
from dataclasses import dataclass, field, asdict
from datetime import date, datetime, timedelta
from typing import Any, Iterable, Optional
from urllib.parse import urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup
from dateutil import parser as date_parser

UNKNOWN = "unknown"

# --------------------------------------------------------------------------
# Текст и HTML
# --------------------------------------------------------------------------

_WS_RE = re.compile(r"[ \t ​]+")
_MULTINL_RE = re.compile(r"\n{3,}")


def clean_ws(value: Any) -> str:
    """Схлопывает пробелы, приводит переводы строк к \n, обрезает края."""
    if value is None:
        return ""
    text = str(value)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = unicodedata.normalize("NFKC", text)
    text = _WS_RE.sub(" ", text)
    text = "\n".join(line.strip() for line in text.split("\n"))
    text = _MULTINL_RE.sub("\n\n", text)
    return text.strip()


def strip_html(raw: Any) -> str:
    """Убирает HTML-разметку, скрипты и стили, оставляя читаемый текст."""
    if not raw:
        return ""
    text = str(raw)
    if "<" not in text:
        return clean_ws(html_lib.unescape(text))
    soup = BeautifulSoup(text, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()
    for br in soup.find_all(["br", "p", "li", "tr", "div", "h1", "h2", "h3", "h4"]):
        br.append("\n")
    return clean_ws(soup.get_text(" "))


def slugify(value: str) -> str:
    """Нормализованный ключ для сравнения названий и агентств."""
    text = clean_ws(value).lower()
    text = re.sub(r"[^a-z0-9а-яё ]+", " ", text)
    text = re.sub(r"\b(the|for|of|and|to|in|on|a|an|no|nos|re)\b", " ", text)
    return re.sub(r"\s+", " ", text).strip()


# --------------------------------------------------------------------------
# Даты
# --------------------------------------------------------------------------

_MONTHS = (
    "jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|"
    "aug(?:ust)?|sep(?:t|tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?"
)

_DATE_PATTERNS = [
    re.compile(rf"\b(?:{_MONTHS})\.?\s+\d{{1,2}}(?:st|nd|rd|th)?,?\s+\d{{4}}\b", re.I),
    re.compile(rf"\b\d{{1,2}}(?:st|nd|rd|th)?\s+(?:{_MONTHS})\.?,?\s+\d{{4}}\b", re.I),
    re.compile(r"\b\d{4}-\d{1,2}-\d{1,2}\b"),
    re.compile(r"\b\d{1,2}[/.]\d{1,2}[/.]\d{2,4}\b"),
]

_TIME_RE = re.compile(r"\b\d{1,2}:\d{2}\s*(?:[AaPp]\.?[Mm]\.?)?\b")


def parse_date(value: Any) -> Optional[date]:
    """
    Разбирает дату из строки/объекта. Возвращает date или None.
    Формат Филиппин — американский (MM/DD/YYYY), поэтому dayfirst=False.
    """
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = clean_ws(value)
    if not text:
        return None
    text = re.sub(r"(\d{1,2})(st|nd|rd|th)\b", r"\1", text, flags=re.I)
    for pattern in _DATE_PATTERNS:
        found = pattern.search(text)
        if found:
            try:
                return date_parser.parse(found.group(0), dayfirst=False, fuzzy=True).date()
            except (ValueError, OverflowError, TypeError):
                continue
    try:
        return date_parser.parse(text, dayfirst=False, fuzzy=True).date()
    except (ValueError, OverflowError, TypeError):
        return None


def find_dates(text: str) -> list[date]:
    """Все даты, встречающиеся в тексте, в порядке появления."""
    result: list[date] = []
    if not text:
        return result
    cleaned = re.sub(r"(\d{1,2})(st|nd|rd|th)\b", r"\1", text, flags=re.I)
    spans: list[tuple[int, int, str]] = []
    for pattern in _DATE_PATTERNS:
        for m in pattern.finditer(cleaned):
            spans.append((m.start(), m.end(), m.group(0)))
    spans.sort(key=lambda s: s[0])
    seen_pos = -1
    for start, end, raw in spans:
        if start < seen_pos:
            continue
        seen_pos = end
        parsed = parse_date(raw)
        if parsed:
            result.append(parsed)
    return result


# Метки, после которых обычно стоит дедлайн подачи заявок.
_DEADLINE_LABELS = [
    "deadline of submission",
    "deadline for submission",
    "deadline for the submission",
    "deadline",
    "closing date and time",
    "closing date",
    "closing",
    "submission and receipt of bids",
    "submission and opening of bids",
    "submission of bids",
    "submission of quotation",
    "receipt of bids",
    "last day of submission",
    "on or before",
    "not later than",
    "until",
    "bid opening",
    "opening of bids",
    "pre-bid conference",  # запасной вариант, используется последним
]

_PUBLISH_LABELS = [
    "date of posting",
    "posting date",
    "date posted",
    "published",
    "publication date",
    "date of publication",
    "advertisement date",
]


def _date_after_label(text: str, labels: Iterable[str], window: int = 160) -> Optional[date]:
    low = text.lower()
    for label in labels:
        idx = low.find(label)
        while idx != -1:
            chunk = text[idx + len(label): idx + len(label) + window]
            dates = find_dates(chunk)
            if dates:
                return dates[0]
            idx = low.find(label, idx + 1)
    return None


def extract_deadline(text: str) -> Optional[date]:
    """Определяет дедлайн подачи. Приоритет — у явных меток."""
    if not text:
        return None
    normalized = clean_ws(text)
    labelled = _date_after_label(normalized, _DEADLINE_LABELS[:-1])
    if labelled:
        return labelled
    return _date_after_label(normalized, _DEADLINE_LABELS[-1:])


def extract_publish_date(text: str) -> Optional[date]:
    if not text:
        return None
    return _date_after_label(clean_ws(text), _PUBLISH_LABELS)


def extract_deadline_time(text: str) -> str:
    """Возвращает время дедлайна, если оно указано рядом с меткой."""
    if not text:
        return ""
    normalized = clean_ws(text)
    low = normalized.lower()
    for label in _DEADLINE_LABELS[:-1]:
        idx = low.find(label)
        if idx == -1:
            continue
        chunk = normalized[idx: idx + len(label) + 160]
        m = _TIME_RE.search(chunk)
        if m:
            return m.group(0).strip()
    return ""


# --------------------------------------------------------------------------
# Номер закупки, бюджет, контакты
# --------------------------------------------------------------------------

_PHILGEPS_REF_RE = re.compile(
    r"(?:philgeps\s*(?:reference|ref\.?)\s*(?:no\.?|number)?\s*[:#-]?\s*)(\d{6,12})", re.I
)
_BARE_REF_RE = re.compile(r"\b(?:reference|ref\.?)\s*(?:no\.?|number)\s*[:#-]?\s*([A-Z0-9][A-Z0-9\-/_.]{4,})", re.I)
_NOTICE_NO_RE = re.compile(
    r"\b(?:ITB|IB|RFQ|RFP|PB|PR|BAC|GOODS|INFRA|SVP|NP|IAEB)\s*"
    r"(?:no\.?|number|#)?\s*[:\-]?\s*([0-9][A-Z0-9\-/_.]{3,})",
    re.I,
)
_PROJECT_NO_RE = re.compile(
    r"\b(?:project|bid|solicitation|procurement|contract)\s+(?:no\.?|number|id)\s*[:\-#]?\s*([A-Z0-9][A-Z0-9\-/_.]{3,})",
    re.I,
)


def extract_notice_number(text: str) -> str:
    """Официальный номер закупки. Пустая строка, если не найден."""
    if not text:
        return ""
    normalized = clean_ws(text)
    for pattern in (_NOTICE_NO_RE, _PHILGEPS_REF_RE, _PROJECT_NO_RE, _BARE_REF_RE):
        m = pattern.search(normalized)
        if m:
            value = m.group(1).strip(" .,:;")
            if len(value) >= 4:
                return value.upper()
    return ""


def extract_philgeps_ref(text: str) -> str:
    if not text:
        return ""
    m = _PHILGEPS_REF_RE.search(clean_ws(text))
    return m.group(1) if m else ""


_BUDGET_RE = re.compile(
    r"(?:approved\s+budget(?:\s+for\s+the\s+contract)?|\babc\b|budget|amount|"
    r"estimated\s+cost|contract\s+cost|total\s+cost)"
    r"[^0-9\n]{0,40}"
    r"([0-9][0-9,]{2,}(?:\.[0-9]{1,2})?)",
    re.I,
)
_CURRENCY_RE = re.compile(r"(₱|\bPHP\b|\bUSD\b|\bUS\$|\$)", re.I)


def extract_budget(text: str) -> tuple[Optional[float], str]:
    """Возвращает (сумма, валюта). Валюта по умолчанию PHP, если найден ₱/PHP."""
    if not text:
        return None, ""
    normalized = clean_ws(text)
    m = _BUDGET_RE.search(normalized)
    if not m:
        return None, ""
    raw = m.group(1).strip().rstrip(".,")
    raw = raw.replace(" ", "")
    # Отбрасываем случаи вида "2026" (просто год) и слишком короткие числа.
    digits = re.sub(r"[^0-9]", "", raw)
    if len(digits) < 4:
        return None, ""
    try:
        if re.search(r",\d{3}", raw):
            value = float(raw.replace(",", ""))
        else:
            value = float(raw.replace(",", "."))
    except ValueError:
        return None, ""
    if value < 1000:
        return None, ""
    window = normalized[max(0, m.start() - 40): m.end() + 10]
    cur = _CURRENCY_RE.search(window)
    currency = "PHP"
    if cur:
        token = cur.group(1).upper()
        currency = "USD" if token in ("USD", "US$") else "PHP"
    return value, currency


_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_PHONE_RE = re.compile(r"(?:\+63|\(0?\d{2,4}\)|\b0)[\d\-\s().]{6,16}\d")
_CONTACT_NAME_RE = re.compile(
    r"(?:BAC\s+(?:Secretariat|Chairperson|Chairman)|Contact\s+Person|For\s+further\s+information[^:\n]*)"
    r"\s*[:\-]?\s*([A-Z][A-Za-z.\-]+(?:\s+[A-Z][A-Za-z.\-]+){1,3})"
)


def extract_contacts(text: str) -> dict[str, str]:
    if not text:
        return {"contact_name": "", "contact_email": "", "contact_phone": ""}
    normalized = clean_ws(text)
    email = _EMAIL_RE.search(normalized)
    phone = _PHONE_RE.search(normalized)
    name = _CONTACT_NAME_RE.search(normalized)
    return {
        "contact_name": clean_ws(name.group(1)) if name else "",
        "contact_email": email.group(0) if email else "",
        "contact_phone": clean_ws(phone.group(0)) if phone else "",
    }


# --------------------------------------------------------------------------
# Вложения
# --------------------------------------------------------------------------

_DOC_EXT_RE = re.compile(r"\.(pdf|docx?|xlsx?|zip|rar|7z)(?:$|\?)", re.I)


def canonical_url(url: str) -> str:
    """Убирает якорь и утилитарные query-параметры, нормализует хвост."""
    if not url:
        return ""
    parts = urlsplit(url.strip())
    query = "&".join(
        p for p in parts.query.split("&")
        if p and not p.lower().startswith(("utm_", "fbclid", "gclid"))
    )
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, query, ""))


def extract_attachments(raw_html: str, base_url: str = "") -> list[str]:
    """Ссылки на PDF и другие документы из HTML-фрагмента."""
    if not raw_html:
        return []
    found: list[str] = []
    soup = BeautifulSoup(str(raw_html), "html.parser")
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.lower().startswith(("javascript:", "mailto:", "#")):
            continue
        absolute = urljoin(base_url, href) if base_url else href
        if _DOC_EXT_RE.search(absolute):
            normalized = canonical_url(absolute)
            if normalized not in found:
                found.append(normalized)
    return found


# --------------------------------------------------------------------------
# Статусы
# --------------------------------------------------------------------------

_CANCEL_RE = re.compile(r"\b(cancell?ed|cancellation|withdrawn|rescinded)\b", re.I)
_POSTPONE_RE = re.compile(r"\b(postponed|rescheduled|deferred|extended)\b", re.I)
_AWARD_RE = re.compile(r"\b(notice of award|awarded to|contract awarded|award notice)\b", re.I)
_CLOSED_RE = re.compile(r"\b(closed|bidding closed|no longer accepting|expired)\b", re.I)


def detect_status(text: str, closing: Optional[date], today: Optional[date] = None) -> str:
    """
    Статус объявления: cancelled | awarded | closed | open | unknown.
    Текст важнее даты: отменённая закупка остаётся отменённой.
    """
    today = today or date.today()
    normalized = clean_ws(text or "")
    if _CANCEL_RE.search(normalized):
        return "cancelled"
    if _AWARD_RE.search(normalized):
        return "awarded"
    if closing is not None:
        return "closed" if closing < today else "open"
    if _CLOSED_RE.search(normalized):
        return "closed"
    return UNKNOWN


def days_until(closing: Optional[date], today: Optional[date] = None) -> Optional[int]:
    if closing is None:
        return None
    return (closing - (today or date.today())).days


def deadline_status(days: Optional[int]) -> str:
    """urgent | closing_soon | open | closed | deadline_unknown."""
    if days is None:
        return "deadline_unknown"
    if days < 0:
        return "closed"
    if days < 3:
        return "urgent"
    if days <= 7:
        return "closing_soon"
    return "open"


# --------------------------------------------------------------------------
# Объект тендера
# --------------------------------------------------------------------------

@dataclass
class Notice:
    source_id: str = ""
    source_name: str = ""
    notice_id: str = ""
    title: str = ""
    agency: str = ""
    category: str = ""
    description: str = ""
    location: str = ""
    publish_date: str = ""
    closing_date: str = ""
    closing_time: str = ""
    status: str = UNKNOWN
    estimated_budget: Optional[float] = None
    currency: str = ""
    contact_name: str = ""
    contact_email: str = ""
    contact_phone: str = ""
    original_url: str = ""
    attachment_urls: list[str] = field(default_factory=list)
    language: str = "en"
    matched_profiles: list[str] = field(default_factory=list)
    match_score: int = 0
    match_reasons: list[str] = field(default_factory=list)
    eligibility_notes: list[str] = field(default_factory=list)
    first_seen: str = ""
    last_seen: str = ""
    # служебные поля
    canonical_id: str = ""
    id_basis: str = ""
    source_links: list[str] = field(default_factory=list)
    source_ids: list[str] = field(default_factory=list)
    source_priority: int = 0
    days_until_deadline: Optional[int] = None
    deadline_status: str = "deadline_unknown"
    priority_score: float = 0.0
    unconfirmed: bool = False
    raw_text: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def closing_date_obj(self) -> Optional[date]:
        return parse_date(self.closing_date)

    def searchable_text(self) -> str:
        return " \n".join(
            x for x in (self.title, self.category, self.agency, self.description, self.raw_text) if x
        )


# --------------------------------------------------------------------------
# Канонический идентификатор
# --------------------------------------------------------------------------

def _digest(prefix: str, payload: str) -> str:
    return f"{prefix}:{hashlib.sha1(payload.encode('utf-8')).hexdigest()[:16]}"


def canonical_id(notice: Notice) -> tuple[str, str]:
    """
    Возвращает (canonical_id, id_basis).

    Приоритет: официальный номер закупки → номер PhilGEPS →
    source_id + agency + title + closing_date → канонический URL.
    """
    philgeps_ref = extract_philgeps_ref(notice.searchable_text())
    if notice.notice_id:
        return _digest("no", notice.notice_id.upper()), "notice_number"
    if philgeps_ref:
        return _digest("gep", philgeps_ref), "philgeps_reference"
    if notice.title:
        payload = "|".join(
            [notice.source_id, slugify(notice.agency), slugify(notice.title), notice.closing_date or ""]
        )
        return _digest("cmp", payload), "source_agency_title_closing"
    if notice.original_url:
        return _digest("url", canonical_url(notice.original_url)), "canonical_url"
    return _digest("raw", notice.raw_text[:500]), "raw_text"


# --------------------------------------------------------------------------
# Сборка Notice из «сырых» данных адаптера
# --------------------------------------------------------------------------

def normalize_notice(raw: dict[str, Any], source_cfg: dict[str, Any],
                     today: Optional[date] = None) -> Notice:
    """
    raw ожидает ключи (все необязательные):
        title, url, html, text, agency, category, location,
        notice_id, publish_date, closing_date, budget, currency,
        attachment_urls, language
    """
    today = today or date.today()
    detail_html = raw.get("html") or ""
    body_text = clean_ws(raw.get("text") or "") or strip_html(detail_html)
    title = clean_ws(raw.get("title") or "")
    if not title and body_text:
        title = body_text.split("\n")[0][:200]

    full_text = "\n".join(x for x in (title, body_text) if x)

    closing = parse_date(raw.get("closing_date")) or extract_deadline(full_text)
    publish = parse_date(raw.get("publish_date")) or extract_publish_date(full_text)

    notice_id = clean_ws(raw.get("notice_id") or "") or extract_notice_number(full_text)

    budget = raw.get("budget")
    currency = clean_ws(raw.get("currency") or "")
    if budget in (None, "", 0):
        budget, detected_currency = extract_budget(full_text)
        currency = currency or detected_currency

    contacts = extract_contacts(full_text)

    attachments = list(raw.get("attachment_urls") or [])
    attachments += [u for u in extract_attachments(detail_html, raw.get("url", "")) if u not in attachments]

    status = detect_status(full_text, closing, today)
    dleft = days_until(closing, today)

    notice = Notice(
        source_id=source_cfg.get("id", ""),
        source_name=source_cfg.get("name", ""),
        notice_id=notice_id,
        title=title or UNKNOWN,
        agency=clean_ws(raw.get("agency") or source_cfg.get("default_agency") or ""),
        category=clean_ws(raw.get("category") or source_cfg.get("default_category") or ""),
        description=body_text[:4000],
        location=clean_ws(raw.get("location") or ""),
        publish_date=publish.isoformat() if publish else "",
        closing_date=closing.isoformat() if closing else "",
        closing_time=extract_deadline_time(full_text),
        status=status,
        estimated_budget=budget if budget else None,
        currency=currency,
        contact_name=contacts["contact_name"],
        contact_email=contacts["contact_email"],
        contact_phone=contacts["contact_phone"],
        original_url=canonical_url(raw.get("url") or source_cfg.get("url") or ""),
        attachment_urls=attachments,
        language=clean_ws(raw.get("language") or source_cfg.get("language") or "en"),
        source_priority=int(source_cfg.get("priority") or 0),
        days_until_deadline=dleft,
        deadline_status=deadline_status(dleft),
        unconfirmed=bool(source_cfg.get("requires_confirmation")),
        raw_text=body_text[:8000],
    )
    notice.source_links = [notice.original_url] if notice.original_url else []
    notice.source_ids = [notice.source_id] if notice.source_id else []
    notice.canonical_id, notice.id_basis = canonical_id(notice)
    return notice


# --------------------------------------------------------------------------
# Дедупликация
# --------------------------------------------------------------------------

def _signatures(n: Notice) -> list[str]:
    keys: list[str] = []
    if n.notice_id:
        keys.append("num:" + n.notice_id.upper())
    philgeps_ref = extract_philgeps_ref(n.searchable_text())
    if philgeps_ref:
        keys.append("num:GEPS" + philgeps_ref)
    title_key = slugify(n.title)
    agency_key = slugify(n.agency)
    if title_key:
        if agency_key:
            keys.append(f"ta:{agency_key}|{title_key}")
        if n.closing_date:
            keys.append(f"tc:{title_key}|{n.closing_date}")
    for url in n.attachment_urls:
        keys.append("doc:" + canonical_url(url))
    if n.original_url:
        keys.append("url:" + canonical_url(n.original_url))
    return keys


def _close_dates(a: Notice, b: Notice, tolerance_days: int = 3) -> bool:
    da, db = a.closing_date_obj, b.closing_date_obj
    if da is None or db is None:
        return True  # неизвестная дата не опровергает совпадение
    return abs((da - db).days) <= tolerance_days


def _fuzzy_same(a: Notice, b: Notice, threshold: float = 0.90) -> bool:
    ta, tb = slugify(a.title), slugify(b.title)
    if not ta or not tb:
        return False
    if slugify(a.agency) and slugify(b.agency) and slugify(a.agency) != slugify(b.agency):
        return False
    if difflib.SequenceMatcher(None, ta, tb).ratio() < threshold:
        return False
    return _close_dates(a, b)


def _merge_into(primary: Notice, other: Notice) -> Notice:
    """Дополняет primary данными из other, не затирая заполненные поля."""
    for f_name in (
        "notice_id", "agency", "category", "location", "publish_date", "closing_date",
        "closing_time", "currency", "contact_name", "contact_email", "contact_phone",
    ):
        if not getattr(primary, f_name) and getattr(other, f_name):
            setattr(primary, f_name, getattr(other, f_name))
    if primary.estimated_budget in (None, 0) and other.estimated_budget:
        primary.estimated_budget = other.estimated_budget
    if len(other.description) > len(primary.description):
        primary.description = other.description
    if len(other.raw_text) > len(primary.raw_text):
        primary.raw_text = other.raw_text
    for url in other.attachment_urls:
        if url not in primary.attachment_urls:
            primary.attachment_urls.append(url)
    for url in other.source_links:
        if url and url not in primary.source_links:
            primary.source_links.append(url)
    for sid in other.source_ids:
        if sid and sid not in primary.source_ids:
            primary.source_ids.append(sid)
    # Подтверждённость: если хотя бы один источник официальный — снимаем флаг.
    if not other.unconfirmed:
        primary.unconfirmed = False
    # Статус: отмена/присуждение важнее «открыто».
    rank = {"cancelled": 3, "awarded": 2, "closed": 1, "open": 0, UNKNOWN: -1}
    if rank.get(other.status, -1) > rank.get(primary.status, -1):
        primary.status = other.status
    return primary


def dedupe_notices(notices: list[Notice]) -> list[Notice]:
    """
    Объединяет дубли одной закупки, пришедшие из разных источников.
    Приоритет отдаётся источнику с большим priority.
    """
    ordered = sorted(
        notices,
        key=lambda n: (-n.source_priority, 0 if n.notice_id else 1, -len(n.description or "")),
    )
    kept: list[Notice] = []
    index: dict[str, int] = {}

    for notice in ordered:
        target: Optional[int] = None
        sigs = _signatures(notice)
        for sig in sigs:
            if sig in index:
                candidate = index[sig]
                if sig.startswith("num:") or _close_dates(kept[candidate], notice):
                    target = candidate
                    break
        if target is None:
            for i, existing in enumerate(kept):
                if _fuzzy_same(existing, notice):
                    target = i
                    break
        if target is None:
            kept.append(notice)
            target = len(kept) - 1
        else:
            _merge_into(kept[target], notice)
        for sig in _signatures(kept[target]):
            index.setdefault(sig, target)
    return kept
