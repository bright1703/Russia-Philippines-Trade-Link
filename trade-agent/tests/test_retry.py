"""Повторные попытки и поведение при недоступности LLM."""
import pytest

from trade_agent.llm import LLMClient, LLMError, LLMUnavailable, MockProvider, extract_json
from trade_agent.utils import RetryError, retry_call


def test_retry_call_succeeds_after_failures():
    attempts = {"n": 0}

    def flaky():
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise ValueError("временный сбой")
        return "ok"

    counter = {}
    assert retry_call(flaky, retries=3, backoff=0, label="t",
                      sleep=lambda s: None, counter=counter) == "ok"
    assert attempts["n"] == 3 and counter["retries"] == 2


def test_retry_call_gives_up_and_counts_attempts():
    with pytest.raises(RetryError) as exc:
        retry_call(lambda: 1 / 0, retries=2, backoff=0, label="t", sleep=lambda s: None)
    assert exc.value.attempts == 2


class _AlwaysDown:
    name = "down"

    def __init__(self):
        self.calls = 0

    def complete(self, *args, **kwargs):
        self.calls += 1
        raise LLMUnavailable("503")


def test_llm_client_retries_then_raises_unavailable():
    provider = _AlwaysDown()
    client = LLMClient(provider, retries=3, backoff=0)
    with pytest.raises(LLMUnavailable):
        client.complete("s", "u", sleep=lambda s: None)
    assert provider.calls == 3
    assert client.usage()["retries"] == 2


def test_llm_client_enforces_call_budget():
    client = LLMClient(MockProvider(["{}", "{}"]), max_calls_per_run=1)
    client.complete("s", "u")
    with pytest.raises(LLMUnavailable):
        client.complete("s", "u")


def test_llm_client_truncates_long_input():
    provider = MockProvider(["{}"])
    client = LLMClient(provider, max_input_chars=100)
    client.complete("s", "x" * 5000)
    assert len(provider.calls[0]["user"]) < 300


def test_llm_client_without_provider_is_unavailable():
    with pytest.raises(LLMUnavailable):
        LLMClient(None).complete("s", "u")


def test_extract_json_handles_fences_and_noise():
    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json('Вот ответ: {"a": 2} — всё') == {"a": 2}
    with pytest.raises(LLMError):
        extract_json("совсем не json")
