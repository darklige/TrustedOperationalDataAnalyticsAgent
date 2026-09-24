import pytest

from trust_agent.chat_provider import ChatCompletionsProvider
from trust_agent.config import make_provider
from trust_agent.provider import OpenAIResponsesProvider


def test_provider_factory_selects_chat_gateway(monkeypatch):
    monkeypatch.setenv("TRUST_AGENT_PROVIDER", "chat_completions")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.com/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("TRUST_AGENT_MAX_OUTPUT_TOKENS", "2048")
    monkeypatch.setenv("TRUST_AGENT_CHAT_EXTRA_BODY", '{"enable_thinking":false}')
    provider = make_provider("test-model")
    assert isinstance(provider, ChatCompletionsProvider)
    assert provider.max_output_tokens == 2048
    assert provider.extra_body == {"enable_thinking": False}


def test_provider_factory_defaults_to_responses(monkeypatch):
    monkeypatch.delenv("TRUST_AGENT_PROVIDER", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    assert isinstance(make_provider("test-model"), OpenAIResponsesProvider)


def test_provider_factory_rejects_unknown(monkeypatch):
    monkeypatch.setenv("TRUST_AGENT_PROVIDER", "not-a-provider")
    with pytest.raises(ValueError, match="unknown TRUST_AGENT_PROVIDER"):
        make_provider("test-model")
