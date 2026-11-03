import asyncio
from types import SimpleNamespace

import pytest

from llm.client import DEFAULT_GROQ_MODEL, GroqClient, LLMCallError, build_client_from_env


class _Completions:
    def __init__(self, result):
        self.result, self.kwargs = result, None

    async def create(self, **kwargs):
        self.kwargs = kwargs
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


def _stub(result):
    comps = _Completions(result)
    return SimpleNamespace(chat=SimpleNamespace(completions=comps)), comps


def _reply(text):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))])


def test_groq_sends_system_and_user_messages_in_json_mode():
    stub, comps = _stub(_reply('  {"a": 1}  '))
    out = asyncio.run(GroqClient(_client=stub).complete(system="SYS", user="USR"))
    assert out == '{"a": 1}'
    assert comps.kwargs["messages"] == [{"role": "system", "content": "SYS"}, {"role": "user", "content": "USR"}]
    assert comps.kwargs["response_format"] == {"type": "json_object"}
    assert comps.kwargs["temperature"] == 0.0
    assert comps.kwargs["model"] == DEFAULT_GROQ_MODEL


def test_groq_options_can_be_disabled():
    stub, comps = _stub(_reply("{}"))
    asyncio.run(GroqClient(_client=stub, json_mode=False, temperature=None, model="m").complete(system="s", user="u"))
    assert "response_format" not in comps.kwargs and "temperature" not in comps.kwargs
    assert comps.kwargs["model"] == "m"


@pytest.mark.parametrize("bad", [_reply(""), _reply(None), SimpleNamespace(choices=[])])
def test_groq_empty_reply_is_call_error(bad):
    stub, _ = _stub(bad)
    with pytest.raises(LLMCallError, match="empty"):
        asyncio.run(GroqClient(_client=stub).complete(system="s", user="u"))


def test_groq_sdk_errors_become_call_errors():
    stub, _ = _stub(RuntimeError("401 invalid api key"))
    with pytest.raises(LLMCallError, match="invalid api key"):
        asyncio.run(GroqClient(_client=stub).complete(system="s", user="u"))


def test_groq_timeout_becomes_call_error():
    stub, _ = _stub(asyncio.TimeoutError())
    with pytest.raises(LLMCallError, match="timed out"):
        asyncio.run(GroqClient(_client=stub).complete(system="s", user="u"))


def test_provider_switch(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "nonsense")
    with pytest.raises(ValueError, match="LLM_PROVIDER"):
        build_client_from_env()


def test_groq_json_mode_env_switch(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test")
    monkeypatch.setenv("LLM_JSON_MODE", "false")
    assert GroqClient.from_env()._json_mode is False
    monkeypatch.delenv("LLM_JSON_MODE")
    assert GroqClient.from_env()._json_mode is True


def test_groq_is_the_default_provider(monkeypatch):
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test")
    assert type(build_client_from_env()).__name__ == "GroqClient"


def test_reasoning_effort_sent_only_when_set():
    stub, comps = _stub(_reply("{}"))
    asyncio.run(GroqClient(_client=stub).complete(system="s", user="u"))
    assert "reasoning_effort" not in comps.kwargs
    stub, comps = _stub(_reply("{}"))
    asyncio.run(GroqClient(_client=stub, reasoning_effort="low").complete(system="s", user="u"))
    assert comps.kwargs["reasoning_effort"] == "low"