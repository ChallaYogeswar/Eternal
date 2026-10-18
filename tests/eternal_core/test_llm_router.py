"""
Tests for the LLM provider abstraction and multi-provider router.
Uses the real OllamaProvider/AnthropicProvider/OpenAIProvider classes
against genuinely unreachable/unconfigured endpoints (proving the real
failure-handling code paths), plus the FakeSuccessProvider test double
(conftest.py) to prove the "a provider works" path that this sandbox
can't reach live.
"""
import pytest

from eternal_core.llm.ollama_provider import OllamaProvider
from eternal_core.llm.cloud_provider import AnthropicProvider, OpenAIProvider
from eternal_core.llm.router import LLMRouter, AllProvidersFailedError
from eternal_core.llm.base import LLMUnavailableError, LLMAuthError

from tests.conftest import FakeSuccessProvider, FakeFailProvider, FakeUnconfiguredProvider


def test_ollama_provider_raises_unavailable_on_connection_refused():
    provider = OllamaProvider(host="http://localhost:1", model="x", connect_timeout_seconds=1.0)
    assert provider.is_configured() is True
    with pytest.raises(LLMUnavailableError):
        provider.generate("sys", [{"role": "user", "content": "hi"}])


def test_ollama_health_check_returns_false_when_unreachable():
    provider = OllamaProvider(host="http://localhost:1", model="x", connect_timeout_seconds=1.0)
    assert provider.health_check() is False


def test_anthropic_provider_reports_not_configured_without_key():
    provider = AnthropicProvider(api_key="", model="claude-sonnet-5")
    assert provider.is_configured() is False
    with pytest.raises(LLMAuthError):
        provider.generate("sys", [{"role": "user", "content": "hi"}])


def test_openai_provider_reports_not_configured_without_key():
    provider = OpenAIProvider(api_key="", model="gpt-4o")
    assert provider.is_configured() is False
    with pytest.raises(LLMAuthError):
        provider.generate("sys", [{"role": "user", "content": "hi"}])


def test_router_falls_through_unconfigured_and_failing_to_success():
    unconfigured = FakeUnconfiguredProvider()
    failing = FakeFailProvider()
    success = FakeSuccessProvider(reply="hello from fake")

    router = LLMRouter([unconfigured, failing, success])
    routed = router.generate("sys", [{"role": "user", "content": "hi"}])

    assert routed.result.text == "hello from fake"
    assert routed.result.provider == "fake_success"
    assert unconfigured.call_count == 0  # never called — is_configured() gated it
    assert failing.call_count == 1
    assert success.call_count == 1

    # Attempt log should reflect exactly what happened, in order
    labels = [(a.provider, a.ok) for a in routed.attempts]
    assert labels == [("fake_unconfigured", False), ("fake_fail", False), ("fake_success", True)]


def test_router_stops_at_first_success_without_trying_later_providers():
    first = FakeSuccessProvider(name="first", reply="first wins")
    second = FakeSuccessProvider(name="second", reply="should not be reached")

    router = LLMRouter([first, second])
    routed = router.generate("sys", [{"role": "user", "content": "hi"}])

    assert routed.result.text == "first wins"
    assert first.call_count == 1
    assert second.call_count == 0


def test_router_raises_when_every_provider_fails():
    router = LLMRouter([FakeFailProvider("a"), FakeFailProvider("b")])
    with pytest.raises(AllProvidersFailedError) as exc_info:
        router.generate("sys", [{"role": "user", "content": "hi"}])
    assert len(exc_info.value.attempts) == 2
    assert all(not a.ok for a in exc_info.value.attempts)


def test_router_with_zero_providers_raises_immediately():
    router = LLMRouter([])
    with pytest.raises(AllProvidersFailedError) as exc_info:
        router.generate("sys", [{"role": "user", "content": "hi"}])
    assert exc_info.value.attempts == []
