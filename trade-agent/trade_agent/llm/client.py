"""
Провайдеро-независимый клиент LLM.

Агенты работают только с LLMClient и ничего не знают о конкретном API.
Чтобы добавить OpenAI или другую модель, достаточно написать новый
провайдер с методом complete() и зарегистрировать его в PROVIDERS.

Ключи читаются только из окружения (.env) и никогда не логируются.
"""

from __future__ import annotations

import json
import inspect
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Optional, Protocol

import requests

LOG = logging.getLogger("trade_agent.llm")


class LLMError(RuntimeError):
    """Ошибка вызова модели."""


class LLMUnavailable(LLMError):
    """
    Модель недоступна (нет ключа, сеть, лимит, 5xx).
    Вызывающий код обязан оставить материал в очереди, а не терять его.
    """


@dataclass
class LLMResponse:
    text: str = ""
    model: str = ""
    provider: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    raw: dict[str, Any] = field(default_factory=dict)


class LLMProvider(Protocol):
    name: str

    def complete(self, system: str, user: str, model: str, max_tokens: int,
                 temperature: float, timeout: float,
                 thinking: Optional[bool] = None) -> LLMResponse: ...


class AnthropicProvider:
    """Первый провайдер: Anthropic Messages API."""

    name = "anthropic"

    def __init__(self, api_key: str, base_url: str = "https://api.anthropic.com",
                 version: str = "2023-06-01"):
        if not api_key:
            raise LLMUnavailable("ANTHROPIC_API_KEY не задан")
        self._api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.version = version
        self.session = requests.Session()

    def complete(self, system: str, user: str, model: str, max_tokens: int,
                 temperature: float, timeout: float,
                 thinking: Optional[bool] = None) -> LLMResponse:
        payload = {
            "model": model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }
        if thinking is not None:
            payload["thinking"] = {"type": "enabled" if thinking else "disabled"}
        headers = {
            "x-api-key": self._api_key,
            "anthropic-version": self.version,
            "content-type": "application/json",
        }
        try:
            response = self.session.post(
                f"{self.base_url}/v1/messages", json=payload, headers=headers, timeout=timeout
            )
        except requests.RequestException as exc:
            raise LLMUnavailable(f"сеть недоступна: {exc}") from exc

        if response.status_code in (429, 500, 502, 503, 504, 529):
            raise LLMUnavailable(f"провайдер вернул {response.status_code}")
        if response.status_code == 401:
            raise LLMUnavailable("ключ API отклонён (401)")
        if response.status_code >= 400:
            raise LLMError(f"ошибка запроса {response.status_code}: {response.text[:200]}")

        try:
            data = response.json()
        except ValueError:
            raise LLMError("провайдер вернул невалидный JSON") from None
        if not isinstance(data, dict):
            raise LLMError("провайдер вернул ответ не в виде объекта")

        parts = data.get("content")
        if not isinstance(parts, list):
            raise LLMError("в ответе провайдера отсутствует корректное content")
        text = "".join(p.get("text", "") for p in parts
                        if isinstance(p, dict) and isinstance(p.get("text"), str))
        usage = data.get("usage") or {}
        if not isinstance(usage, dict):
            usage = {}
        return LLMResponse(
            text=text,
            model=data.get("model", model),
            provider=self.name,
            input_tokens=int(usage.get("input_tokens") or 0),
            output_tokens=int(usage.get("output_tokens") or 0),
            raw=data,
        )


class MockProvider:
    """
    Провайдер для тестов и офлайн-прогонов.

    Возвращает заранее заданные ответы. Реальных сетевых вызовов не делает,
    поэтому unit-тесты никогда не обращаются к внешнему API.
    """

    name = "mock"

    def __init__(self, responses: Optional[list[str]] = None,
                 handler: Optional[Any] = None):
        self.responses = list(responses or [])
        self.handler = handler
        self.calls: list[dict[str, Any]] = []

    def complete(self, system: str, user: str, model: str, max_tokens: int,
                 temperature: float, timeout: float,
                 thinking: Optional[bool] = None) -> LLMResponse:
        self.calls.append({"system": system, "user": user, "model": model,
                           "thinking": thinking})
        if self.handler is not None:
            text = self.handler(system, user)
        elif self.responses:
            text = self.responses.pop(0)
        else:
            text = "{}"
        return LLMResponse(text=text, model=model, provider=self.name,
                           input_tokens=len(user) // 4, output_tokens=len(text) // 4)


class DeepSeekProvider(AnthropicProvider):
    """DeepSeek через официальный Anthropic-совместимый Messages API."""

    name = "deepseek"

    def __init__(self, api_key: str,
                 base_url: str = "https://api.deepseek.com/anthropic"):
        super().__init__(api_key, base_url=base_url)


PROVIDERS: dict[str, Any] = {
    "anthropic": AnthropicProvider,
    "deepseek": DeepSeekProvider,
    "mock": MockProvider,
}


_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def extract_json(text: str) -> dict[str, Any]:
    """
    Достаёт JSON-объект из ответа модели.
    Терпит обрамление в ``` и текст вокруг. Бросает LLMError, если не вышло.
    """
    if not text:
        raise LLMError("пустой ответ модели")
    candidate = text.strip()
    fenced = _FENCE_RE.search(candidate)
    if fenced:
        candidate = fenced.group(1).strip()
    try:
        parsed = json.loads(candidate)
        if isinstance(parsed, dict):
            return parsed
    except ValueError:
        pass
    start = candidate.find("{")
    end = candidate.rfind("}")
    if start != -1 and end > start:
        try:
            parsed = json.loads(candidate[start: end + 1])
            if isinstance(parsed, dict):
                return parsed
        except ValueError:
            pass
    raise LLMError("ответ модели не содержит корректного JSON")


class LLMClient:
    """
    Обёртка над провайдером: лимит размера запроса, таймауты, повторы,
    бюджет вызовов на запуск и учёт токенов.
    """

    def __init__(self, provider: Any, *, max_input_chars: int = 24000,
                 max_output_tokens: int = 2000, timeout: float = 90.0,
                 retries: int = 3, backoff: float = 4.0,
                 max_calls_per_run: int = 200, log_usage: bool = True,
                 model_fast: str = "", model_deep: str = "",
                 thinking_fast: Optional[bool] = None,
                 thinking_deep: Optional[bool] = None):
        self.provider = provider
        self.max_input_chars = max_input_chars
        self.max_output_tokens = max_output_tokens
        self.timeout = timeout
        self.retries = max(1, retries)
        self.backoff = backoff
        self.max_calls_per_run = max_calls_per_run
        self.log_usage = log_usage
        self.model_fast = model_fast
        self.model_deep = model_deep
        self.thinking_fast = thinking_fast
        self.thinking_deep = thinking_deep
        self.calls = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.retries_used = 0

    @property
    def available(self) -> bool:
        return self.provider is not None

    def usage(self) -> dict[str, int]:
        return {
            "calls": self.calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "retries": self.retries_used,
        }

    def complete(self, system: str, user: str, *, model: Optional[str] = None,
                 max_tokens: Optional[int] = None, temperature: float = 0.0,
                 thinking: Optional[bool] = None,
                 sleep: Any = time.sleep) -> LLMResponse:
        if self.provider is None:
            raise LLMUnavailable("LLM-провайдер не настроен")
        if self.calls >= self.max_calls_per_run:
            raise LLMUnavailable(
                f"исчерпан лимит вызовов на запуск ({self.max_calls_per_run})"
            )
        prompt = user if len(user) <= self.max_input_chars else (
            user[: self.max_input_chars] + "\n\n[...текст обрезан по лимиту запроса...]"
        )
        chosen = model or self.model_deep or self.model_fast
        if thinking is None:
            if chosen == self.model_fast:
                thinking = self.thinking_fast
            elif chosen == self.model_deep:
                thinking = self.thinking_deep
        last: Optional[BaseException] = None

        for attempt in range(1, self.retries + 1):
            try:
                provider_args = (
                    system, prompt, chosen, max_tokens or self.max_output_tokens,
                    temperature, self.timeout,
                )
                # Сохраняем совместимость с тестовыми и внешними провайдерами,
                # которые реализовали старый интерфейс без thinking.
                try:
                    provider_params = inspect.signature(self.provider.complete).parameters
                    supports_thinking = (
                        "thinking" in provider_params or
                        any(p.kind == inspect.Parameter.VAR_KEYWORD
                            for p in provider_params.values())
                    )
                except (TypeError, ValueError):
                    supports_thinking = True
                if thinking is not None and supports_thinking:
                    response = self.provider.complete(*provider_args, thinking=thinking)
                else:
                    response = self.provider.complete(*provider_args)
                self.calls += 1
                self.input_tokens += response.input_tokens
                self.output_tokens += response.output_tokens
                if self.log_usage:
                    LOG.info("LLM %s/%s: in=%d out=%d", response.provider, response.model,
                             response.input_tokens, response.output_tokens)
                return response
            except LLMUnavailable as exc:
                last = exc
                if attempt < self.retries:
                    self.retries_used += 1
                    sleep(self.backoff * attempt)
                    continue
                break
            except LLMError as exc:
                last = exc
                break
        raise LLMUnavailable(f"модель недоступна после {self.retries} попыток: {last}")

    def complete_json(self, system: str, user: str, **kwargs: Any) -> tuple[dict[str, Any], LLMResponse]:
        response = self.complete(system, user, **kwargs)
        return extract_json(response.text), response


def build_client(settings: Any, provider: Optional[Any] = None) -> LLMClient:
    """
    Создаёт клиент по настройкам. Если провайдер не настроен (нет ключа),
    возвращается клиент с provider=None: агенты увидят LLMUnavailable
    и оставят материал в очереди.
    """
    llm = settings.llm
    if provider is None:
        factory = PROVIDERS.get(llm.provider)
        if factory is None:
            LOG.error("неизвестный LLM-провайдер: %s", llm.provider)
        elif llm.provider in ("anthropic", "deepseek"):
            try:
                provider = factory(llm.api_key, llm.base_url)
            except LLMUnavailable as exc:
                LOG.warning("LLM недоступен: %s", exc)
                provider = None
        else:
            provider = factory()
    return LLMClient(
        provider,
        max_input_chars=llm.max_input_chars,
        max_output_tokens=llm.max_output_tokens,
        timeout=llm.timeout,
        retries=llm.retries,
        backoff=llm.retry_backoff,
        max_calls_per_run=llm.max_calls_per_run,
        log_usage=llm.log_usage,
        model_fast=llm.model_fast,
        model_deep=llm.model_deep,
        thinking_fast=llm.thinking_fast,
        thinking_deep=llm.thinking_deep,
    )
