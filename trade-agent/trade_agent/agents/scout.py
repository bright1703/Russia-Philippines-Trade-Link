"""
Scout — первичный фильтр.

Единственная задача: понять, имеет ли материал отношение к российскому
экспорту на Филиппины. Глубокий анализ Scout не делает.

Порядок работы:
  1. дешёвый предфильтр по ключевым словам — явный шум отбрасывается
     без обращения к модели;
  2. вызов LLM для оставшегося;
  3. при недоступности модели материал НЕ теряется: он остаётся
     в очереди raw_items и будет обработан позже.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from ..llm import LLMUnavailable, extract_json
from ..models import RawItem, Signal, SIGNAL_NEW
from ..utils import truncate
from . import taxonomy
from .prompting import (
    UNTRUSTED_INPUT_RULES, as_bool, as_int, as_str, as_str_list,
    looks_like_injection, wrap_untrusted,
)

LOG = logging.getLogger("trade_agent.scout")

SYSTEM_PROMPT = """Ты — Scout торгового агента, который ищет возможности для
российских компаний (прежде всего из Приморского края) на рынке Филиппин.

Твоя единственная задача — быстрый отбор. Ты НЕ проводишь исследование.
Ты решаешь, относится ли материал к российскому экспорту на Филиппины
или к условиям такого экспорта.

Оценивай по шкале 0-5:
5 — прямая возможность или прямое регуляторное изменение для российского поставщика
4 — высокая вероятность связи, нужен анализ
3 — потенциально связано
2 — слабая связь
1 — почти не связано
0 — не связано

Категории (выбери одну): MEAT, FOOD, AGRICULTURE, FERTILIZER, GRAIN, AUTO,
EQUIPMENT, PACKAGING, ENERGY, LOGISTICS, REGULATION, TENDER, IMPORT, OTHER.

Правила:
- Не выдумывай факты и не додумывай то, чего нет в тексте.
- HS-коды указывай только как предположение на уровне товарной группы.
- Если данных мало, ставь низкую оценку и напиши это в reason.

Ответ — только JSON без пояснений:
{"relevant": true/false, "category": "...", "score": 0-5,
 "reason": "почему это важно, 1-2 предложения",
 "companies": ["названия или отрасли компаний, которых это может касаться"],
 "hs_codes": ["предполагаемые коды"],
 "geography": "страна/регион",
 "needs_deep_analysis": true/false}""" + UNTRUSTED_INPUT_RULES


@dataclass
class ScoutResult:
    signal: Optional[Signal] = None
    dropped: bool = False
    drop_reason: str = ""
    deferred: bool = False          # модель недоступна — вернуть в очередь
    usage: dict[str, int] = field(default_factory=dict)


class Scout:
    def __init__(self, llm: Any, settings: Any):
        self.llm = llm
        self.settings = settings
        self.min_score = int(getattr(settings, "scout_min_score", 2))
        self.allow_heuristic = bool(getattr(settings, "scout_allow_heuristic", False))

    # -- предфильтр --------------------------------------------------------
    def prefilter(self, item: RawItem) -> tuple[bool, str, list[tuple[str, int]]]:
        """Возвращает (пропустить_дальше, причина_отказа, найденные_категории)."""
        text = f"{item.title}\n{item.raw_text}"
        if len(text.strip()) < 30:
            return False, "слишком короткий материал", []
        if taxonomy.looks_like_noise(text):
            return False, "явный шум по стоп-словам", []
        categories = taxonomy.guess_categories(text)
        if item.source_type == "tender":
            meta = item.meta or {}
            if meta.get("status") in ("cancelled", "awarded"):
                return False, f"закупка {meta.get('status')}", []
            if meta.get("deadline_status") == "closed":
                return False, "дедлайн закупки уже прошёл", []
            if int(meta.get("tender_match_score") or 0) < self.min_score:
                return False, ("тендерный модуль оценил релевантность как "
                               f"{meta.get('tender_match_score', 0)}/5"), []
            return True, "", categories or [("TENDER", 1)]
        if not categories:
            return False, "нет ни одного отраслевого ключевого слова", []
        return True, "", categories

    # -- основной вход -----------------------------------------------------
    def evaluate(self, item: RawItem) -> ScoutResult:
        passed, reason, categories = self.prefilter(item)
        if not passed:
            return ScoutResult(dropped=True, drop_reason=reason)

        try:
            data = self._ask_llm(item, categories)
        except LLMUnavailable as exc:
            if self.allow_heuristic:
                LOG.warning("Scout: модель недоступна (%s), используется эвристика", exc)
                data = self._heuristic(item, categories)
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

        reason = as_str(data.get("reason"), 800)
        if looks_like_injection(f"{item.title}\n{item.raw_text}"):
            reason = ("ВНИМАНИЕ: в тексте источника найдены признаки попытки "
                      "внедрить инструкцию — проверять вручную. ") + reason

        signal = Signal(
            raw_item_id=int(item.id or 0),
            category=taxonomy.valid_category(as_str(data.get("category"), 40, "OTHER")),
            relevance_score=score,
            reason=reason,
            companies_matched=as_str_list(data.get("companies"), max_items=12, item_limit=200),
            hs_codes=[c for c in as_str_list(data.get("hs_codes"), max_items=12, item_limit=20)
                      if any(ch.isdigit() for ch in c)],
            geography=as_str(data.get("geography"), 120),
            needs_deep_analysis=as_bool(data.get("needs_deep_analysis"), score >= 3),
            status=SIGNAL_NEW,
        )
        return ScoutResult(signal=signal)

    # -- вызов модели ------------------------------------------------------
    def _ask_llm(self, item: RawItem, categories: list[tuple[str, int]]) -> dict[str, Any]:
        hint = ", ".join(c for c, _ in categories) or "нет"
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
            f"Подсказка предфильтра по категориям: {hint}{tender_note}\n\n"
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
            max_tokens=600,
        )
        if not isinstance(data, dict):
            raise ValueError("ответ модели не является объектом")
        return data

    # -- запасная эвристика (только по явному разрешению) -------------------
    def _heuristic(self, item: RawItem, categories: list[tuple[str, int]]) -> dict[str, Any]:
        top_category = categories[0][0] if categories else "OTHER"
        hits = categories[0][1] if categories else 0
        score = 2 if hits >= 1 else 1
        if hits >= 3:
            score = 3
        if item.source_type == "tender":
            score = max(score, int((item.meta or {}).get("tender_match_score") or 0))
        return {
            "relevant": score >= self.min_score,
            "category": top_category,
            "score": score,
            "reason": "эвристическая оценка без модели: совпадение отраслевых ключевых слов",
            "companies": [],
            "hs_codes": taxonomy.hs_hints([top_category])[:4],
            "geography": "Philippines",
            "needs_deep_analysis": False,
        }

    @staticmethod
    def _as_int(value: Any, default: int) -> int:
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return default
