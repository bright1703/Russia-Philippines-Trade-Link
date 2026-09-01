"""
Офлайн-заглушка LLM для проверки конвейера без API-ключа.

ВАЖНО: ответы синтетические и не являются анализом. Заглушка нужна
только чтобы прогнать конвейер целиком (тесты, dry-run, приёмка).
В продакшене используется реальный провайдер.
"""

from __future__ import annotations

import json
from typing import Any

from ..agents import taxonomy
from .client import LLMClient, MockProvider


def _scout_answer(user: str) -> str:
    categories = taxonomy.guess_categories(user)
    category = categories[0][0] if categories else "OTHER"
    hits = categories[0][1] if categories else 0
    score = min(5, 2 + hits)
    return json.dumps({
        "relevant": score >= 2,
        "category": category,
        "score": score,
        "reason": "офлайн-заглушка: совпали отраслевые ключевые слова",
        "companies": [],
        "hs_codes": taxonomy.hs_hints([category])[:3],
        "geography": "Philippines",
        "needs_deep_analysis": score >= 3,
    }, ensure_ascii=False)


def _analyst_answer(user: str) -> str:
    url = ""
    for line in user.split("\n"):
        if line.startswith("URL: ") and line[5:].strip() not in ("", "нет"):
            url = line[5:].strip()
            break
    return json.dumps({
        "company": "нет прямого совпадения",
        "summary": "Офлайн-заглушка: разбор не выполнялся, модель не вызывалась.",
        "opportunity": "Требуется запуск с реальным API-ключом.",
        "risks": ["вывод не проверен моделью"],
        "regulation": "не определено",
        "market_data": "нет данных в источнике",
        "what_to_verify": ["перезапустить обработку с настроенным ANTHROPIC_API_KEY"],
        "suggested_actions": ["настроить .env"],
        "next_step": "настроить доступ к модели",
        "confidence": 0.1,
        "sources": [url] if url else [],
    }, ensure_ascii=False)


def _reviewer_answer(_: str) -> str:
    return json.dumps({"verdict": "PASS", "problems": [], "corrected_fields": {},
                       "confidence": 0.1}, ensure_ascii=False)


def offline_handler(system: str, user: str) -> str:
    if "Scout" in system:
        return _scout_answer(user)
    if "рецензент" in system:
        return _reviewer_answer(user)
    return _analyst_answer(user)


def build_offline_client(settings: Any) -> LLMClient:
    return LLMClient(
        MockProvider(handler=offline_handler),
        max_input_chars=settings.llm.max_input_chars,
        max_output_tokens=settings.llm.max_output_tokens,
        model_fast="offline-mock",
        model_deep="offline-mock",
    )
