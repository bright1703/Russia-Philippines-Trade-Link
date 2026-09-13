"""
Opportunity Radar: связь события с компаниями каталога.

Модуль детерминированный, LLM здесь не нужен.

Что изменилось по сравнению с прежней версией и почему.

Раньше доказательством связи служил сам сигнал: `signal.companies_matched`
и `signal.hs_codes`, которые перед этим заполнил обязательный триггер из
профиля компании. Код товара, скопированный из профиля, превращался
в «найденный в новости» код, и связь подтверждала сама себя. Теперь
доказательства берутся ТОЛЬКО из исходного материала: заголовка и текста.
Поля сигнала используются для темы и направления, но не как улика.

Второе изменение — два разных вида связи, которые больше не смешиваются:

  * прямая применимость — в материале назван товар компании, её название
    или точный код; показывается вместе с фрагментом текста;
  * отраслевая связь — событие в отрасли компании, но применимость к её
    продукции не установлена; показывается как «компании отрасли,
    применимость уточнить».

Третье — роль. Для направления «Филиппины → РФ» экспортёр из каталога
не становится покупателем только потому, что товар совпал. Пока роль
не подтверждена, предлагается уточнить роль, а не написать покупателю.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from ..agents import evidence, taxonomy
from ..models import (
    Company, DIRECTION_PH_TO_RU, DIRECTION_RU_TO_PH, EVENT_BUYER_REQUEST,
    EVENT_RULE_CHANGE, LINK_DIRECT, LINK_SECTOR, Match, RawItem, ROLE_UNKNOWN,
    Signal,
)
from ..utils import truncate

# Вклад каждого признака в сырой балл.
W_COMPANY_NAME = 7.0     # прямое упоминание компании — сильнейшее доказательство
W_HS_EXACT = 3.0         # точный код, явно помеченный в материале
W_PRODUCT = 1.5          # товар компании, названный в материале
W_HS_GROUP4 = 1.5        # товарная позиция: тематическая, а не точная связь
W_HS_GROUP2 = 0.75       # товарная группа: широкая тематическая связь
W_SECTOR = 2.0           # отрасль компании совпала с отраслью события
W_TENDER_BONUS = 1.0

# Юридические формы не участвуют в поиске названия по тексту.
LEGAL_FORMS = ("ооо", "оао", "зао", "пао", "ао", "ип", "ltd", "llc", "inc",
               "co", "corp", "jsc", "group", "групп")

DEFAULT_ACTION = "уточнить применимость к продукции компании"


def _clean_name(name: str) -> str:
    """Название без юридической формы и кавычек — для поиска в тексте."""
    words = [word for word in evidence.normalize(name).split()
             if word not in LEGAL_FORMS]
    return " ".join(words)


def _sectors_of(signal: Signal) -> list[str]:
    """Отрасли события: явные, иначе выведенные из старого поля category."""
    if signal.sectors:
        return list(signal.sectors)
    return list(taxonomy.CATEGORY_SECTORS.get(signal.category, ()))


def _company_sectors(company: Company) -> list[str]:
    if company.sectors:
        return list(company.sectors)
    sectors = taxonomy.sectors_from_industry(company.industry)
    if sectors:
        return sectors
    result: list[str] = []
    for category in company.categories:
        result.extend(taxonomy.CATEGORY_SECTORS.get(category, ()))
    return list(dict.fromkeys(result))


def recommended_action(signal: Signal, company: Company, link_type: str) -> str:
    """
    Что предлагается сделать. Система только предлагает — пишет человек.

    Для обратного направления и неподтверждённой роли действие не может
    называть компанию покупателем: совпадение товара так же вероятно
    означает конкуренцию, как и закупочный интерес.
    """
    if link_type == LINK_SECTOR:
        return "отраслевая связь: проверить, касается ли это продукции компании"
    role_known = [role for role in company.roles if role and role != ROLE_UNKNOWN]
    if signal.trade_direction == DIRECTION_PH_TO_RU:
        if not role_known:
            return ("уточнить роль компании по этому товару (закупка, переработка "
                    "или конкуренция) — данных о закупках в каталоге нет")
        return "оценить условия ввоза этого товара в РФ для роли " + ", ".join(role_known)
    if signal.event_type == EVENT_BUYER_REQUEST:
        return "проверить допуск иностранного поставщика и сроки, затем решить об участии"
    if signal.event_type == EVENT_RULE_CHANGE:
        return "проверить по первичному документу, затрагивает ли требование продукцию компании"
    if signal.trade_direction == DIRECTION_RU_TO_PH:
        return "оценить влияние на условия поставки компании на филиппинский рынок"
    return DEFAULT_ACTION


@dataclass
class MatchDetail:
    """Разбор одной пары «событие — компания» вместе с доказательствами."""
    company_slug: str
    score: int
    raw_score: float
    reasons: list[str]
    link_type: str = LINK_SECTOR
    evidence_fragments: list[str] = field(default_factory=list)
    role: str = ROLE_UNKNOWN

    @property
    def direct(self) -> bool:
        return self.link_type == LINK_DIRECT


def _raw_to_score(raw: float) -> int:
    if raw >= 7.0:
        return 5
    if raw >= 5.0:
        return 4
    if raw >= 3.0:
        return 3
    if raw >= 1.5:
        return 2
    if raw > 0:
        return 1
    return 0


def match_signal(signal: Signal, item: Optional[RawItem], company: Company) -> MatchDetail:
    """
    Сопоставляет одно событие с одним профилем компании.

    Источник доказательств — только исходный материал. Если материала нет,
    возможна лишь отраслевая связь: доказывать нечем.
    """
    source_text = ""
    if item is not None:
        source_text = f"{item.title}\n{item.raw_text}"

    raw = 0.0
    reasons: list[str] = []
    fragments: list[str] = []
    direct = False

    # 1. Название компании прямо в тексте материала.
    clean = _clean_name(company.name)
    if source_text and len(clean) >= 5:
        hit = evidence.find_term(source_text, clean)
        if hit is not None:
            raw += W_COMPANY_NAME
            direct = True
            reasons.append(f"в материале прямо упомянута компания «{company.name}»")
            fragments.append(hit.fragment)

    # 2. Товары компании, названные в материале.
    product_terms = [value for value in
                     dict.fromkeys([*company.product_aliases, *company.products])
                     if len(evidence.normalize(value)) >= 4]
    product_hits = evidence.find_terms(source_text, product_terms, limit=3) if source_text else []
    if product_hits:
        direct = True
        raw += W_PRODUCT * len(product_hits)
        for hit in product_hits:
            reasons.append(f"в материале назван товар компании «{hit.term}»")
            fragments.append(hit.fragment)

    # 3. HS-коды. Сравниваются коды профиля с кодами, ЯВНО помеченными
    #    как коды в самом материале. Предположения Scout уликой не служат.
    declared = evidence.declared_hs_codes(source_text) if source_text else []
    best_relation = ""
    for found in declared:
        for company_code in company.hs_codes:
            relation = evidence.hs_relation(found.code, company_code)
            if relation is None:
                continue
            if relation == "exact":
                raw += W_HS_EXACT
                direct = True
                reasons.append(f"в материале указан HS-код {found.code}, "
                               f"он же в профиле компании")
                fragments.append(found.fragment)
                best_relation = "exact"
                break
            if relation == "group4" and best_relation != "exact":
                raw += W_HS_GROUP4
                reasons.append(f"товарная позиция HS {found.code[:4]} совпала с кодом "
                               f"компании {company_code}; это тематическая связь, "
                               f"а не подтверждение точного кода")
                fragments.append(found.fragment)
                best_relation = "group4"
                break
            if relation == "group2" and not best_relation:
                raw += W_HS_GROUP2
                reasons.append(f"товарная группа HS {found.code[:2]} совпала с кодом "
                               f"компании {company_code}; связь широкая")
                fragments.append(found.fragment)
                best_relation = "group2"
                break
        if best_relation == "exact":
            break

    # 4. Отраслевая связь. Это отдельная функция обзора, а не доказательство
    #    коммерческой возможности.
    shared = taxonomy.sectors_overlap(_sectors_of(signal), _company_sectors(company))
    if shared:
        raw += W_SECTOR
        reasons.append("событие относится к отрасли компании: "
                       + ", ".join(taxonomy.sector_label(s) for s in shared[:2]))

    if signal.event_type == EVENT_BUYER_REQUEST and direct:
        raw += W_TENDER_BONUS
        reasons.append("это запрос покупателя — возможность прямого участия или поставки партнёру")

    if company.restrictions:
        reasons.append("в профиле указаны ограничения: " + "; ".join(company.restrictions[:3]))

    if not direct and not shared:
        return MatchDetail(company.slug, 0, 0.0, [], LINK_SECTOR, [], ROLE_UNKNOWN)

    link_type = LINK_DIRECT if direct else LINK_SECTOR
    if link_type == LINK_SECTOR:
        reasons.append("применимость к продукции компании не установлена")

    roles = [role for role in company.roles if role and role != ROLE_UNKNOWN]
    return MatchDetail(
        company_slug=company.slug,
        score=_raw_to_score(raw),
        raw_score=raw,
        reasons=reasons,
        link_type=link_type,
        evidence_fragments=[_short(f) for f in dict.fromkeys(fragments)][:3],
        role=roles[0] if roles else ROLE_UNKNOWN,
    )


def _short(fragment: str, limit: int = 160) -> str:
    return truncate(fragment, limit)


class OpportunityRadar:
    def __init__(self, settings: Any):
        self.min_score = int(getattr(settings, "radar_min_match_score", 2))
        self.min_sector_score = int(getattr(settings, "radar_min_sector_score", 2))

    def match_all(self, signal: Signal, item: Optional[RawItem],
                  companies: list[Company]) -> list[Match]:
        """
        Все связи события с каталогом, прямые и отраслевые.

        Отраслевой список сохраняется целиком: показывать его коротко —
        задача дайджеста, а не радара.
        """
        matches: list[Match] = []
        for company in companies:
            detail = match_signal(signal, item, company)
            threshold = self.min_score if detail.direct else self.min_sector_score
            if detail.score < threshold:
                continue
            matches.append(Match(
                company_slug=company.slug,
                signal_id=int(signal.id or 0),
                match_score=detail.score,
                reason=truncate("; ".join(detail.reasons), 800),
                recommended_action=recommended_action(signal, company, detail.link_type),
                link_type=detail.link_type,
                evidence=detail.evidence_fragments,
                role=detail.role,
            ))
        # Порядок воспроизводимый: сначала прямая применимость, затем балл,
        # затем slug — чтобы один и тот же выпуск собирался одинаково.
        matches.sort(key=lambda m: (0 if m.direct else 1, -m.match_score, m.company_slug))
        return matches

    def direct_matches(self, signal: Signal, item: Optional[RawItem],
                       companies: list[Company]) -> list[Company]:
        """
        Компании с прямой применимостью — и только они.

        Список может быть пустым. Подставлять «первые пять из каталога»,
        когда подходящих нет, нельзя: это заставляло Analyst писать про
        компании, к которым событие отношения не имеет.
        """
        by_slug = {company.slug: company for company in companies}
        result: list[Company] = []
        for company in companies:
            detail = match_signal(signal, item, company)
            if detail.direct and detail.score >= self.min_score:
                result.append(by_slug[company.slug])
        return result
