"""Вспомогательные функции для тестов (без сетевых вызовов)."""

import json
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

FIXTURES = Path(__file__).resolve().parent / "fixtures"

from trade_agent.llm import LLMClient, MockProvider   # noqa: E402


def mock_llm(*responses: str, handler=None) -> LLMClient:
    """Клиент с подставными ответами: сеть не используется."""
    return LLMClient(MockProvider(list(responses), handler=handler),
                     model_fast="mock-fast", model_deep="mock-deep")


def json_response(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False)
