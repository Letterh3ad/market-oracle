"""The Claude backend against a fake of the SDK's async client: no network in the suite."""

from types import SimpleNamespace

import anthropic
import httpx2
import pytest

from oracle.research.claude import ClaudeChat
from oracle.research.llm import ModelError

SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["news_search", "final"]},
        "findings": {"type": "array", "maxItems": 3,
                     "items": {"type": "object", "properties": {"text": {"type": "string"}}}},
    },
    "required": ["action"],
}


class FakeClient:
    """Stands in for AsyncAnthropic: records each request, answers or raises as told."""

    def __init__(self, *answers) -> None:
        self.answers = list(answers)
        self.requests: list[dict] = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    async def _create(self, **request):
        self.requests.append(request)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def message(*blocks, stop_reason="end_turn"):
    return SimpleNamespace(content=list(blocks), stop_reason=stop_reason)


def text(value):
    return SimpleNamespace(type="text", text=value)


def status_error(cls, code):
    response = httpx2.Response(code, request=httpx2.Request("POST", "https://api.anthropic.com/v1/messages"))
    return cls("refused", response=response, body=None)


async def test_a_reply_is_json_constrained_to_the_schema_with_refusal_fallbacks_on():
    client = FakeClient(message(SimpleNamespace(type="thinking", thinking=""), text('{"action": "final"}')))
    chat = ClaudeChat("claude-opus-5", 60.0, client)
    reply = await chat.reply([{"role": "system", "content": "Be brief."}, {"role": "user", "content": "Go."},
                              {"role": "assistant", "content": "{}"}, {"role": "user", "content": "Again."}], SCHEMA)
    assert reply == {"action": "final"}
    [request] = client.requests
    assert request["model"] == "claude-opus-5" and request["system"] == "Be brief."
    assert [m["role"] for m in request["messages"]] == ["user", "assistant", "user"]
    assert request["fallbacks"] == "default" and request["betas"] == ["server-side-fallback-2026-07-01"]
    sent = request["output_config"]["format"]
    assert sent["type"] == "json_schema"
    # Structured outputs need closed objects, and take no array-length limits.
    assert sent["schema"]["additionalProperties"] is False
    findings = sent["schema"]["properties"]["findings"]
    assert "maxItems" not in findings and findings["items"]["additionalProperties"] is False
    assert findings["items"]["required"] == ["text"]
    assert SCHEMA["properties"]["findings"]["maxItems"] == 3  # the caller's schema is left alone


async def test_a_refusal_is_handed_back_as_no_answer_for_the_loop_to_reject():
    client = FakeClient(message(stop_reason="refusal"))
    assert not isinstance(await ClaudeChat("claude-opus-5", 60.0, client).reply([], SCHEMA), dict)


@pytest.mark.parametrize("error, says", [
    (status_error(anthropic.AuthenticationError, 401), "ANTHROPIC_API_KEY"),
    (status_error(anthropic.RateLimitError, 429), "rate limiting"),
    (status_error(anthropic.InternalServerError, 529), "HTTP 529"),
    (anthropic.AnthropicError("Could not resolve authentication method."), "ANTHROPIC_API_KEY"),
])
async def test_api_failures_are_plain_model_errors(error, says):
    with pytest.raises(ModelError, match=says):
        await ClaudeChat("claude-opus-5", 60.0, FakeClient(error)).reply([], SCHEMA)


async def test_an_answer_from_a_fallback_model_is_remembered():
    served = message(text('{"action": "final"}'))
    served.model = "claude-opus-5"
    served.usage = SimpleNamespace(iterations=[SimpleNamespace(type="message", model="claude-opus-5"),
                                               SimpleNamespace(type="fallback_message", model="claude-opus-4-8")])
    chat = ClaudeChat("claude-opus-5", 60.0, FakeClient(served))
    await chat.reply([], SCHEMA)
    assert chat.fell_back_to == {"claude-opus-4-8"}


async def test_a_served_id_that_differs_without_a_fallback_is_not_one():
    # An alias can come back as a longer dated id; only the API's own fallback record counts.
    served = message(text('{"action": "final"}'))
    served.model = "claude-opus-5-20260801"
    served.usage = SimpleNamespace(iterations=None)
    chat = ClaudeChat("claude-opus-5", 60.0, FakeClient(served))
    await chat.reply([], SCHEMA)
    assert chat.fell_back_to == set()


async def test_a_refused_key_names_where_the_profile_took_it_from():
    chat = ClaudeChat("claude-opus-5", 60.0, FakeClient(status_error(anthropic.AuthenticationError, 401)),
                      key_source="check MY_CLAUDE (key_env of model profile deep)")
    with pytest.raises(ModelError, match=r"refused the credentials: check MY_CLAUDE \(key_env of model profile deep\)"):
        await chat.reply([], SCHEMA)


async def test_a_profiles_key_and_endpoint_reach_the_client(monkeypatch):
    made = []

    class Recording(FakeClient):
        def __init__(self, **kwargs):
            super().__init__(message(text('{"action": "final"}')))
            made.append(kwargs)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return None

    monkeypatch.setattr(anthropic, "AsyncAnthropic", Recording)
    await ClaudeChat("claude-opus-5", 60.0, api_key="sk-x", base_url="https://proxy.test").reply([], SCHEMA)
    await ClaudeChat("claude-opus-5", 60.0).reply([], SCHEMA)
    assert made == [{"timeout": 60.0, "api_key": "sk-x", "base_url": "https://proxy.test"},
                    {"timeout": 60.0, "api_key": None, "base_url": None}]


async def test_a_reply_too_slow_is_called_a_timeout():
    error = anthropic.APITimeoutError(request=httpx2.Request("POST", "https://api.anthropic.com/v1/messages"))
    with pytest.raises(ModelError, match="took longer than 60s"):
        await ClaudeChat("claude-opus-5", 60.0, FakeClient(error)).reply([], SCHEMA)
