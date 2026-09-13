"""
Scout — первичный отбор.

Scout отвечает на три РАЗНЫХ вопроса и не смешивает их:

  1. что произошло и насколько надёжен источник;
  2. влияет ли это на нужное направление торговли и товарную группу;
  3. кого из каталога это может касаться (и в какой роли).

Два направления работы равноправны:

  * Приморье → Филиппины: спрос, импорт Филиппин, конкурирующие
    поставщики, условия доступа, закупки и логистика;
  * Филиппины → РФ: экспорт Филиппин и условия ввоза в РФ.

Упоминание России в каждой новости не требуется. Изменение филиппинского
импорта рыбы из третьей страны влияет на спрос и конкуренцию для Приморья,
и такая новость нужна — если влияние объяснимо. А вот новость про третий
рынок без связи с Филиппинами, РФ или маршрутом между ними исключается,
даже когда товар совпал.

Порядок работы:
  1. дешёвый предфильтр — явный шум и чужие рынки отбрасываются без модели;
  2. вызов LLM для оставшегося;
  3. при недоступности модели материал НЕ теряется: он остаётся
     в очереди raw_items и будет обработан позже.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from ..llm import LLMUnavailable
from ..models import (
    DIRECTION_BOTH, DIRECTION_PH_TO_RU, DIRECTION_RU_TO_PH, DIRECTION_UNKNOWN,
    EVENT_BUSINESS_CONTACT, EVENT_BUYER_REQUEST, EVENT_OTHER, EVENT_RULE_CHANGE,
    RawItem, Signal, SIGNAL_NEW, VERIFY_NEEDS_CHECK, VERIFY_SOURCE_CLAIM,
)
from ..utils import truncate
from . import evidence, taxonomy
from .prompting import (
    UNTRUSTED_INPUT_RULES, as_bool, as_int, as_str, as_str_list,
    looks_like_injection, wrap_untrusted,
)

LOG = logging.getLogger("trade_agent.scout")

SYSTEM_PROMPT = """Ты — Scout торгового представителя Приморского края,
который работает на Филиппинах. Тебя интересуют ДВА направления торговли:

  RU_TO_PH — поставки из Приморья и России на Филиппины: филиппинский
             спрос, импорт Филиппин, конкурирующие поставщики, условия
             доступа на рынок, закупки, логистика;
  PH_TO_RU — поставки с Филиппин в Россию: филиппинский экспорт и условия
             ввоза в РФ по тем же товарным группам.

Твоя единственная задача — быстрый отбор. Исследование ты не проводишь.

Что БЕРЁМ:
- изменение филиппинского импорта нужного товара — даже если Россия
  в новости не упомянута, при условии, что влияние на спрос, конкуренцию
  или условия для Приморья объяснимо;
- возможности и ограничения филиппинских поставок в Россию;
- деловые контакты: бизнес-миссии, делегации, переговоры, выставки
  российских компаний на Филиппинах. Товарное слово для них
  НЕ обязательно. Готовую сделку при этом не выдумывай.

Что НЕ берём:
- события третьих рынков без связи с Филиппинами, РФ или маршрутом
  торговли между ними — совпадения товара для этого недостаточно;
- экспорт России в Китай, Монголию и другие страны сам по себе;
- общие новости о Филиппинах без деловой связи.

Оценивай значимость по шкале 0-5:
5 — прямое изменение условий или прямая возможность по нужному направлению
4 — высокая вероятность влияния, нужен анализ
3 — потенциально влияет
2 — слабая связь
1 — почти не связано
0 — не связано

Жёсткие правила:
- Не выдумывай факты, даты, коды и названия регуляторов.
- Изменение правила утверждай только тогда, когда в тексте есть предмет
  изменения (пошлина, квота, сертификат, допуск) и сказано, что с ним
  сделали. Само слово «сертификат» или «пошлина» изменением не является.
- Обычный обзор товара или история бренда — это не регуляторное событие.
- HS-коды указывай, только если они прямо помечены в тексте как коды.
- Для каждого вывода приводи короткую цитату из текста в evidence.
- Если данных мало, ставь низкую оценку и напиши об этом в reason,
  а не придумывай применимость.

Ответ — только JSON без пояснений:
{"relevant": true/false,
 "score": 0-5,
 "trade_direction": "RU_TO_PH" | "PH_TO_RU" | "BOTH" | "UNKNOWN",
 "event_type": "DEMAND" | "SUPPLY" | "MARKET_ACCESS" | "RULE_CHANGE" |
               "LOGISTICS" | "BUYER_REQUEST" | "BUSINESS_CONTACT" | "OTHER",
 "category": "MEAT|FOOD|AGRICULTURE|FERTILIZER|GRAIN|AUTO|EQUIPMENT|PACKAGING|ENERGY|LOGISTICS|REGULATION|TENDER|IMPORT|OTHER",
 "sectors": ["FISH_SEAFOOD|FOOD_AGRI|FOOD_MEAT|FOOD_DAIRY|FOOD_HONEY|FOOD_BEVERAGE|FOOD_CONFECTIONERY|FOOD_GRAIN|AGRI_INPUTS|TIMBER|INDUSTRIAL|IND_MACHINERY|IND_AUTO|IND_PACKAGING|IND_ENERGY|COSMETICS|LOGISTICS_SERVICES|CREATIVE"],
 "jurisdiction": "чьё правило меняется, если меняется",
 "destination_market": "рынок назначения товара, не место публикации новости",
 "origin_countries": ["страны происхождения товара"],
 "reason": "почему это важно для нужного направления, 1-2 предложения",
 "companies": ["отрасли или названия компаний, которых это может касаться"],
 "hs_codes": ["коды, прямо помеченные в тексте"],
 "evidence": ["короткая цитата из текста"],
 "event_date": "YYYY-MM-DD или пусто",
 "effective_from": "YYYY-MM-DD или пусто",
 "uncertainties": ["чего в тексте не хватает"],
 "needs_deep_analysis": true/false}""" + UNTRUSTED_INPUT_RULES

# Типы событий, для которых товарное слово не обязательно: деловой контакт
# и запрос покупателя ценны сами по себе.
PRODUCT_FREE_EVENTS = (EVENT_BUSINESS_CONTACT, EVENT_BUYER_REQUEST)


@dataclass
class ScoutResult:
    signal: Optional[Signal] = None
    dropped: bool = False
    drop_reason: str = ""
    deferred: bool = False          # модель недоступна — вернуть в очередь
    usage: dict[str, int] = field(default_factory=dict)


@dataclass
class PrefilterResult:
    """Что дешёвый предфильтр понял о материале до вызова модели."""
    passed: bool = False
    reason: str = ""
    categories: list[tuple[str, int]] = field(default_factory=list)
    sectors: list[tuple[str, int]] = field(default_factory=list)
    events: list[tuple[str, int]] = field(default_factory=list)
    jurisdictions: list[str] = field(default_factory=list)
    third_countries: list[str] = field(default_factory=list)


class Scout:
    def __init__(self, llm: Any, settings: Any):
        self.llm = llm
        self.settings = settings
        self.min_score = int(getattr(settings, "scout_min_score", 2))
        self.allow_heuristic = bool(getattr(settings, "scout_allow_heuristic", False))

    # -- предфильтр --------------------------------------------------------
    def prefilter(self, item: RawItem) -> PrefilterResult:
        """
        Дешёвый отсев до модели.

        Отсутствие отраслевого слова больше не является самостоятельной
        причиной отказа: деловая миссия российской компании на Филиппинах
        проходит дальше и без товарного словаря. Именно на этом правиле
        терялись новости о выходе компаний на местный рынок.
        """
        text = f"{item.title}\n{item.raw_text}"
        if len(text.strip()) < 30:
            return PrefilterResult(reason="слишком короткий материал")
        if taxonomy.looks_like_noise(text):
            return PrefilterResult(reason="явный шум по стоп-словам")

        categories = taxonomy.guess_categories(text)
        sectors = taxonomy.guess_sectors(text)
        events = taxonomy.guess_event_types(text)
        jurisdictions = taxonomy.detect_jurisdictions(text)
        third = taxonomy.detect_third_countries(text)
        found = PrefilterResult(categories=categories, sectors=sectors, events=events,
                                jurisdictions=jurisdictions, third_countries=third)

        if item.source_type == "tender":
            meta = item.meta or {}
            if meta.get("status") in ("cancelled", "awarded"):
                found.reason = f"закупка {meta.get('status')}"
                return found
            if meta.get("deadline_status") == "closed":
                found.reason = "дедлайн закупки уже прошёл"
                return found
            if int(meta.get("tender_match_score") or 0) < self.min_score:
                found.reason = ("тендерный модуль оценил релевантность как "
                                f"{meta.get('tender_match_score', 0)}/5")
                return found
            found.passed = True
            if not found.events:
                found.events = [(EVENT_BUYER_REQUEST, 1)]
            return found

        ours = [code for code in jurisdictions
                if code in (taxonomy.JURISDICTION_PH, taxonomy.JURISDICTION_RU,
                            taxonomy.JURISDICTION_EAEU)]

        # Третий рынок без связи с нашими сторонами торговли не берём даже
        # при совпавшем товаре.
        if third and not ours:
            found.reason = ("событие третьего рынка ("
                            + ", ".join(third[:3])
                            + ") без связи с Филиппинами, РФ или маршрутом между ними")
            return found

        deal_event = any(name in PRODUCT_FREE_EVENTS for name, _ in events)
        if deal_event and ours:
            found.passed = True
            return found

        if not sectors and not categories:
            if ours and events:
                # Тема не опознана словарём, но есть наша юрисдикция
                # и деловое событие — отдаём решение модели.
                found.passed = True
                return found
            found.reason = "нет ни отраслевого слова, ни делового события в нашей юрисдикции"
            return found

        found.passed = True
        return found

    # -- основной вход -----------------------------------------------------
    def evaluate(self, item: RawItem) -> ScoutResult:
        found = self.prefilter(item)
        if not found.passed:
            return ScoutResult(dropped=True, drop_reason=found.reason)

        try:
            data = self._ask_llm(item, found)
        except LLMUnavailable as exc:
            if self.allow_heuristic:
                LOG.warning("Scout: модель недоступна (%s), используется эвристика", exc)
                data = self._heuristic(item, found)
            else:
                LOG.warning("Scout: модель недоступна (%s), материал остаётся в очереди", exc)
                return ScoutResult(deferred=True, drop_reason=str(exc))
        except Exception as exc:  # noqa: BLE001 - неверный ответ модели не роняет конвейер
            LOG.warning("Scout: не удалось разобрать ответ модели: %s", exc)
            return ScoutResult(deferred=True, drop_reason=f"некорректный ответ модели: {exc}")

        score = as_int(data.get("score"), 0, low=0, high=5)
        relevant = as_bool(data.get("relevant"), score >= self.min_score)
        if not relevant or score < self.min_score:
            return ScoutResult(
                dropped=True,
                drop_reason=f"оценка {score} ниже порога {self.min_score}: "
                            f"{truncate(str(data.get('reason', '')), 160)}",
            )

        text = f"{item.title}\n{item.raw_text}"
        direction = self._direction(data, found)
        event_type = taxonomy.valid_event_type(as_str(data.get("event_type"), 40))

        # Третий рынок мог всплыть уже в ответе модели.
        if (direction == DIRECTION_UNKNOWN and found.third_countries
                and not found.jurisdictions):
            return ScoutResult(
                dropped=True,
                drop_reason="третий рынок без связи с нужным направлением торговли")

        # Модель не может объявить изменение правила, если в тексте нет
        # предмета изменения. Проверяем детерминированно.
        from ..alerts import detect_rule_change
        change = detect_rule_change(text)
        if event_type == EVENT_RULE_CHANGE and change is None:
            event_type = EVENT_OTHER
            score = min(score, 3)

        reason = as_str(data.get("reason"), 800)
        if looks_like_injection(text):
            reason = ("ВНИМАНИЕ: в тексте источника найдены признаки попытки "
                      "внедрить инструкцию — проверять вручную. ") + reason

        sectors = taxonomy.valid_sectors(as_str_list(data.get("sectors"), max_items=4,
                                                     item_limit=40))
        if not sectors:
            sectors = [name for name, _ in found.sectors[:2]]

        # Цитаты-доказательства: берём только то, что действительно есть
        # в материале. Придуманную цитату в выпуск пускать нельзя.
        quotes = [value for value in as_str_list(data.get("evidence"), max_items=5,
                                                 item_limit=300)
                  if evidence.normalize(value)[:40]
                  and evidence.normalize(value)[:40] in evidence.normalize(text)]
        if change is not None and change.fragment not in quotes:
            quotes.append(change.fragment)

        signal = Signal(
            raw_item_id=int(item.id or 0),
            category=taxonomy.valid_category(as_str(data.get("category"), 40, "OTHER")),
            relevance_score=score,
            reason=reason,
            companies_matched=as_str_list(data.get("companies"), max_items=12, item_limit=200),
            # Только коды, явно помеченные в самом материале.
            hs_codes=[hit.code for hit in evidence.declared_hs_codes(text)][:8],
            geography=as_str(data.get("destination_market"), 120)
            or as_str(data.get("jurisdiction"), 120),
            trade_direction=direction,
            event_type=event_type,
            sectors=sectors,
            jurisdiction=as_str(data.get("jurisdiction"), 120),
            destination_market=as_str(data.get("destination_market"), 120),
            origin_countries=as_str_list(data.get("origin_countries"), max_items=6,
                                         item_limit=60),
            evidence_fragments=quotes[:5],
            event_date=as_str(data.get("event_date"), 40),
            effective_from=as_str(data.get("effective_from"), 40),
            deadline=str((item.meta or {}).get("closing_date") or ""),
            first_seen_at=item.published_at or item.fetched_at,
            uncertainties=as_str_list(data.get("uncertainties"), max_items=5, item_limit=200),
            # Scout читает пересказ, а не первичный документ.
            verification_status=(VERIFY_NEEDS_CHECK if event_type == EVENT_RULE_CHANGE
                                 else VERIFY_SOURCE_CLAIM),
            needs_deep_analysis=as_bool(data.get("needs_deep_analysis"), score >= 3),
            status=SIGNAL_NEW,
        )
        return ScoutResult(signal=signal)

    # -- направление -------------------------------------------------------
    def _direction(self, data: dict[str, Any], found: PrefilterResult) -> str:
        """Направление из ответа модели, иначе — из найденных юрисдикций."""
        direction = taxonomy.valid_direction(as_str(data.get("trade_direction"), 20))
        if direction != DIRECTION_UNKNOWN:
            return direction
        ph = taxonomy.JURISDICTION_PH in found.jurisdictions
        ru = (taxonomy.JURISDICTION_RU in found.jurisdictions
              or taxonomy.JURISDICTION_EAEU in found.jurisdictions)
        if ph and ru:
            return DIRECTION_BOTH
        if ph:
            return DIRECTION_RU_TO_PH
        if ru:
            return DIRECTION_PH_TO_RU
        return DIRECTION_UNKNOWN

    # -- вызов модели ------------------------------------------------------
    def _ask_llm(self, item: RawItem, found: PrefilterResult) -> dict[str, Any]:
        sector_hint = ", ".join(name for name, _ in found.sectors) or "не определена"
        event_hint = ", ".join(name for name, _ in found.events) or "не определён"
        jurisdiction_hint = ", ".join(found.jurisdictions) or "не определена"
        third_hint = ", ".join(found.third_countries) or "нет"
        tender_note = ""
        if item.source_type == "tender":
            meta = item.meta or {}
            tender_note = (
                f"\nЭто тендерное объявление. Ведомство: {meta.get('agency', '')}. "
                f"Дедлайн: {meta.get('closing_date', 'не указан')}. "
                f"Статус: {meta.get('status', '')}. "
                f"Оценка тендерного модуля: {meta.get('tender_match_score', '')}/5."
            )
        # Метаданные — доверенная часть, текст материала — нет.
        user = (
            "ЗАДАЧА: оценить материал ниже по своим правилам.\n"
            f"Источник: {item.source} ({item.source_type})\n"
            f"URL: {item.source_url}\n"
            f"Дата: {item.published_at or 'неизвестна'}\n"
            f"Подсказка предфильтра — отрасль: {sector_hint}\n"
            f"Подсказка предфильтра — тип события: {event_hint}\n"
            f"Найденные юрисдикции: {jurisdiction_hint}\n"
            f"Третьи страны в тексте: {third_hint}{tender_note}\n\n"
            + wrap_untrusted(
                f"Заголовок: {item.title}\n\nТекст:\n{item.raw_text}",
                source=item.source, doc_id=str(item.id or item.external_id or ""),
                url=item.source_url,
                max_chars=int(getattr(self.llm, "max_input_chars", 24000) // 2),
            )
        )
        data, _ = self.llm.complete_json(
            SYSTEM_PROMPT, user,
            model=getattr(self.llm, "model_fast", None) or None,
            max_tokens=800,
        )
        if not isinstance(data, dict):
            raise ValueError("ответ модели не является объектом")
        return data

    # -- запасная эвристика (только по явному разрешению) -------------------
    def _heuristic(self, item: RawItem, found: PrefilterResult) -> dict[str, Any]:
        top_category = found.categories[0][0] if found.categories else "OTHER"
        hits = found.categories[0][1] if found.categories else 0
        score = 2 if hits >= 1 else 1
        if hits >= 3:
            score = 3
        if item.source_type == "tender":
            score = max(score, int((item.meta or {}).get("tender_match_score") or 0))
        return {
            "relevant": score >= self.min_score,
            "category": top_category,
            "sectors": [name for name, _ in found.sectors[:2]],
            "event_type": found.events[0][0] if found.events else EVENT_OTHER,
            "trade_direction": DIRECTION_UNKNOWN,
            "score": score,
            "reason": "эвристическая оценка без модели: совпадение отраслевых ключевых слов",
            "companies": [],
            "hs_codes": [],
            "evidence": [],
            "jurisdiction": ", ".join(found.jurisdictions),
            "needs_deep_analysis": False,
        }
