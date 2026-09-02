"""Безопасное поведение при поврежденном ответе Anthropic."""

import pytest

from trade_agent.config import load_settings
from trade_agent.llm import AnthropicProvider, DeepSeekProvider, LLMClient, LLMError, MockProvider


class _Response:
    status_code = 200
    text = "ответ"

    def __init__(self, payload):
        self.payload = payload

    def json(self):
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


class _Session:
    def __init__(self, response):
        self.response = response
        self.kwargs = None

    def post(self, *args, **kwargs):
        self.kwargs = kwargs
        return self.response


@pytest.mark.parametrize("payload", [ValueError("битый JSON"), [], {"content": {}}])
def test_anthropic_malformed_success_response_is_llm_error(payload):
    provider = AnthropicProvider("test-key")
    provider.session = _Session(_Response(payload))
    with pytest.raises(LLMError):
        provider.complete("system", "user", "model", 100, 0.0, 1)


def test_anthropic_request_omits_deprecated_temperature():
    provider = AnthropicProvider("test-key")
    session = _Session(_Response({"content": [], "usage": {}}))
    provider.session = session
    provider.complete("system", "user", "model", 100, 0.0, 1)
    assert "temperature" not in session.kwargs["json"]


def test_deepseek_uses_anthropic_endpoint_and_explicit_thinking_mode():
    provider = DeepSeekProvider("test-key")
    session = _Session(_Response({"content": [], "usage": {}}))
    provider.session = session

    provider.complete("system", "user", "deepseek-v4-flash", 100, 0.0, 1,
                      thinking=False)

    assert session.kwargs["json"]["thinking"] == {"type": "disabled"}
    assert session.kwargs["headers"]["x-api-key"] == "test-key"
    assert provider.base_url == "https://api.deepseek.com/anthropic"


def test_llm_client_selects_thinking_mode_by_model():
    provider = MockProvider(responses=["{}", "{}"])
    client = LLMClient(provider, model_fast="fast", model_deep="deep",
                       thinking_fast=False, thinking_deep=True)

    client.complete("system", "user", model="fast")
    client.complete("system", "user", model="deep")

    assert [call["thinking"] for call in provider.calls] == [False, True]


def test_deepseek_environment_selects_provider_defaults(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    settings = load_settings()

    assert settings.llm.provider == "deepseek"
    assert settings.llm.api_key == "test-key"
    assert settings.llm.base_url == "https://api.deepseek.com/anthropic"
    assert settings.llm.model_fast == "deepseek-v4-flash"
    assert settings.llm.model_deep == "deepseek-v4-pro"
    assert settings.llm.thinking_fast is False
    assert settings.llm.thinking_deep is True
