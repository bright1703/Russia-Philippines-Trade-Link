"""Безопасное поведение при поврежденном ответе Anthropic."""

import pytest

from trade_agent.llm import AnthropicProvider, LLMError


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
