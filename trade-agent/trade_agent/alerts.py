"""
Детерминированные обязательные уведомления.

Раньше этот модуль работал так: нашлись общие маркеры правила, общее
слово перемены и любое слово из описания компании — значит это срочное
регуляторное событие с оценкой 5/5. Дальше он сам записывал в сигнал
названия найденных компаний и их HS-коды, а радар принимал эти же поля
за доказательство связи. Круг замыкался, и пошлины ЮАР на китайскую
сталь оказывались срочной новостью для производителя косметики.

Теперь обязательный триггер требует трёх вещей одновременно:

1. Предмет изменения — конкретный регуляторный инструмент (пошлина,
   квота, сертификат, допуск), к которому применено действие. Само по
   себе слово «сертификат» или «пошлина» ничего не значит.
2. Юрисдикция — чьё правило меняется. Правило третьей страны без связи
   с Филиппинами или РФ обязательным триггером не является.
3. Подтверждающий фрагмент исходного текста для каждого вывода.

Ни одно поле сигнала не заполняется данными из профиля компании:
что не найдено в материале, того в сигнале нет.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Optional

from .agents import evidence, taxonomy
from .models import (
    Company, DIRECTION_BOTH, DIRECTION_PH_TO_RU, DIRECTION_RU_TO_PH,
    DIRECTION_UNKNOWN, EVENT_RULE_CHANGE, RawItem, Signal, SIGNAL_NEW,
    VERIFY_NEEDS_CHECK,
)

# Предмет изменения: конкретный регуляторный инструмент.
_SUBJECTS = (
    r"пошлин\w{0,3}|тариф\w{0,3}|квот\w{0,3}|запрет\w{0,3}|ограничени\w{0,3}|"
    r"сертификат\w{0,3}|сертификаци\w{0,3}|аккредитаци\w{0,3}|регистраци\w{0,3}|"
    r"разрешени\w{0,3}|лицензи\w{0,3}|стандарт\w{0,3}|техреглам\w{0,5}|"
    r"ветеринарн\w{0,3}\s+требовани\w{0,3}|фитосанитарн\w{0,3}\s+требовани\w{0,3}|"
    r"duty|duties|tariffs?|quotas?|bans?|restrictions?|certificates?|"
    r"accreditation|registration|permits?|licences?|licenses?|standards?|levy|levies"
)

# Действие над предметом. Окончания перечислены явно: «снизили пошлину»
# считается изменением правила, «цены снизились» — нет.
_VERBS = (
    r"отменил[аио]?|отменен[аоы]?|отмен[ыу]|ввел[аио]?|введен[аоы]?|"
    r"снизил[аио]?|снижен[аоы]?|повысил[аио]?|повышен[аоы]?|"
    r"установил[аио]?|установлен[аоы]?|приостановил[аио]?|приостановлен[аоы]?|"
    r"ограничил[аио]?|ограничен[аоы]?|обнулил[аио]?|обнулен[аоы]?|"
    r"продлил[аио]?|продлен[аоы]?|запретил[аио]?|запрещен[аоы]?|"
    r"разрешил[аио]?|разрешен[аоы]?|утвердил[аио]?|утвержден[аоы]?|"
    r"расширил[аио]?|сократил[аио]?|сокращен[аоы]?|освободил[аио]?|освобожден[аоы]?|"
    r"removes?|removed|removal|waives?|waived|waiver|imposes?|imposed|"
    r"reduces?|reduced|cuts?|lifts?|lifted|suspends?|suspended|"
    r"introduces?|introduced|abolish(?:es|ed)?|exempts?|exempted|exemption|"
    r"raises?|raised|approves?|approved|bans?|banned|restricts?|restricted|"
    r"extends?|extended|sets?|zero[- ]rat(?:e|ed|ing)"
)

_BOUNDARY_LEFT = r"(?<![0-9A-Za-zА-Яа-я_])"
_BOUNDARY_RIGHT = r"(?![0-9A-Za-zА-Яа-я_])"
# Действие и предмет должны стоять рядом в одном предложении.
_GAP = 60

_VERB_THEN_SUBJECT = re.compile(
    f"{_BOUNDARY_LEFT}(?:{_VERBS}){_BOUNDARY_RIGHT}"
    f"[^.!?\n]{{0,{_GAP}}}?{_BOUNDARY_LEFT}(?:{_SUBJECTS}){_BOUNDARY_RIGHT}"
)
_SUBJECT_THEN_VERB = re.compile(
    f"{_BOUNDARY_LEFT}(?:{_SUBJECTS}){_BOUNDARY_RIGHT}"
    f"[^.!?\n]{{0,{_GAP}}}?{_BOUNDARY_LEFT}(?:{_VERBS}){_BOUNDARY_RIGHT}"
)

# Ссылка на акт или дату вступления в силу. Не обязательна, но повышает
# уверенность: заголовок «DTI removes export duties» ссылки не содержит.
_ACT_ANCHOR = re.compile(
    r"(?:administrative order|memorandum order|memorandum circular|department order|"
    r"executive order|republic act|customs memorandum|circular no|order no\.?|"
    r"приказ\w{0,3}|постановлени\w{0,3}|распоряжени\w{0,3}|решени\w{0,3}\s+коллегии|"
    r"федеральн\w{0,3}\s+закон\w{0,3}|вступает\s+в\s+силу|вступил\w{0,2}\s+в\s+силу|"
    r"с\s+\d{1,2}\s+\w+\s+20\d\d|effective\s+(?:from|on|date)?|takes?\s+effect|"
    r"with\s+effect\s+from)", re.IGNORECASE)

# Юрисдикции, чьи правила нам вообще интересны.
OUR_JURISDICTIONS = (taxonomy.JURISDICTION_PH, taxonomy.JURISDICTION_RU,
                     taxonomy.JURISDICTION_EAEU)


@dataclass
class RuleChange:
    """Найденное изменение правила с доказательством из текста."""
    subject: str
    fragment: str
    jurisdictions: list[str] = field(default_factory=list)
    third_countries: list[str] = field(default_factory=list)
    act_anchor: str = ""

    @property
    def ours(self) -> bool:
        """Меняется ли правило в интересующей нас юрисдикции."""
        return any(code in OUR_JURISDICTIONS for code in self.jurisdictions)


def detect_rule_change(text: str) -> Optional[RuleChange]:
    """
    Изменение правила — это действие, применённое к конкретному предмету.

    Обзор рынка, история бренда и статья со словом «сертификат»
    регуляторным событием не становятся.
    """
    source = str(text or "")
    folded = evidence.fold(source)
    match = _VERB_THEN_SUBJECT.search(folded) or _SUBJECT_THEN_VERB.search(folded)
    if match is None:
        return None
    anchor = _ACT_ANCHOR.search(folded)
    return RuleChange(
        subject=evidence.collapse(match.group(0)),
        fragment=evidence.fragment_at(source, match.start(), match.end(), width=220),
        jurisdictions=taxonomy.detect_jurisdictions(source),
        third_countries=taxonomy.detect_third_countries(source),
        act_anchor=evidence.collapse(anchor.group(0)) if anchor else "",
    )


def _direction(change: RuleChange) -> str:
    """
    Куда влияет изменение.

    Филиппинское правило — это условия ввоза к ним (Приморье → Филиппины).
    Российское или союзное — условия ввоза к нам (Филиппины → РФ).
    """
    ph = taxonomy.JURISDICTION_PH in change.jurisdictions
    ru = (taxonomy.JURISDICTION_RU in change.jurisdictions
          or taxonomy.JURISDICTION_EAEU in change.jurisdictions)
    if ph and ru:
        return DIRECTION_BOTH
    if ph:
        return DIRECTION_RU_TO_PH
    if ru:
        return DIRECTION_PH_TO_RU
    return DIRECTION_UNKNOWN


def company_product_hits(text: str, company: Company,
                         limit: int = 4) -> list[evidence.TermHit]:
    """
    Товары компании, НАЙДЕННЫЕ в тексте материала.

    Совпадение ищется по границам слова, общие слова описания отсеяны,
    HS-коды профиля здесь не участвуют вовсе: код из профиля не может
    подтвердить сам себя в новости.
    """
    terms = [value for value in [*company.product_aliases, *company.products]
             if len(evidence.normalize(value)) >= 4]
    return evidence.find_terms(text, terms, limit=limit)


def detect_mandatory_policy_alert(item: RawItem,
                                  companies: Iterable[Company]) -> Optional[Signal]:
    """
    Возвращает сигнал, если изменение правила в нашей юрисдикции
    затрагивает товар, прямо названный в материале.

    Возвращает None, если хотя бы одно из трёх условий не выполнено.
    Отсутствие сигнала здесь не означает, что новость не нужна: она
    просто идёт обычным путём через Scout, а не через срочный триггер.
    """
    text = f"{item.title}\n{item.raw_text}"
    change = detect_rule_change(text)
    if change is None or not change.ours:
        return None

    names: list[str] = []
    products: list[str] = []
    fragments: list[str] = [change.fragment]
    same_sentence = False
    change_sentence = evidence.normalize(change.fragment)

    for company in companies:
        hits = company_product_hits(text, company)
        if not hits:
            continue
        names.append(company.name)
        for hit in hits:
            if hit.term not in products:
                products.append(hit.term)
            if hit.fragment not in fragments:
                fragments.append(hit.fragment)
            if evidence.normalize(hit.term) in change_sentence:
                same_sentence = True

    if not names:
        return None

    # Оценка следует из качества доказательств, а не из наличия слова
    # «пошлина». 5/5 — только когда товар назван там же, где изменение,
    # и есть ссылка на акт или дату вступления в силу.
    score = 4
    if same_sentence or change.act_anchor:
        score = 5

    jurisdiction = ", ".join(change.jurisdictions) or ""
    reason = (
        "Обязательный триггер: в материале найдено изменение правила "
        f"({change.subject}) в юрисдикции {jurisdiction or 'не определена'} "
        f"и назван товар из профиля компании: {', '.join(products[:5])}. "
        "Применимость к конкретной продукции проверяет человек."
    )
    return Signal(
        raw_item_id=int(item.id or 0),
        category="REGULATION",
        event_type=EVENT_RULE_CHANGE,
        relevance_score=score,
        reason=reason,
        # Названия компаний здесь — подсказка для человека, а не
        # доказательство: радар это поле не читает.
        companies_matched=list(dict.fromkeys(names))[:20],
        matched_products=products[:20],
        # Только коды, ЯВНО помеченные как коды в самом материале.
        hs_codes=[hit.code for hit in evidence.declared_hs_codes(text)][:8],
        geography=jurisdiction,
        jurisdiction=jurisdiction,
        trade_direction=_direction(change),
        evidence_fragments=fragments[:5],
        uncertainties=([] if change.act_anchor else
                       ["в материале нет ссылки на акт или дату вступления в силу"]),
        verification_status=VERIFY_NEEDS_CHECK,
        needs_deep_analysis=True,
        must_alert=True,
        status=SIGNAL_NEW,
    )
