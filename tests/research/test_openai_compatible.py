import json

import httpx
import pytest

from oracle.research.llm import ModelError
from oracle.research.openai_compatible import MAX_REASONING_TOKENS, MAX_REPLY_TOKENS, OpenAICompatibleChat, chat_url
from tests.fakes.openai import FakeOpenAI

SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}}}
ASK = [{"role": "system", "content": "You answer in JSON."}, {"role": "user", "content": "hi"}]


def chat(server, base="http://llm.test/v1", **kw):
    return OpenAICompatibleChat("local-8b", base, 5.0, transport=httpx.MockTransport(server.handle), **kw)


async def test_a_reply_is_constrained_to_the_closed_schema_and_parsed():
    server = FakeOpenAI({"ok": True})
    assert await chat(server).reply(ASK, SCHEMA) == {"ok": True}
    [body] = server.bodies
    assert body["model"] == "local-8b" and body["messages"] == ASK and body["temperature"] == 0
    assert body["max_tokens"] == MAX_REPLY_TOKENS
    assert body["response_format"] == {"type": "json_schema", "json_schema": {
        "name": "reply", "strict": True, "schema": {**SCHEMA, "additionalProperties": False, "required": ["ok"]}}}
    assert "authorization" not in server.requests[0].headers


@pytest.mark.parametrize("base, url", [
    ("http://llm.test/v1", "http://llm.test/v1/chat/completions"),
    ("http://llm.test/v1/", "http://llm.test/v1/chat/completions"),
    ("http://llm.test:1234", "http://llm.test:1234/v1/chat/completions"),
    ("https://openrouter.ai/api/v1", "https://openrouter.ai/api/v1/chat/completions"),
    ("http://llm.test/v1/chat/completions", "http://llm.test/v1/chat/completions"),
])
def test_the_endpoint_is_the_api_root_plus_chat_completions(base, url):
    assert chat_url(base) == url


async def test_a_server_refusing_json_schema_falls_back_to_json_object_once_per_instance():
    server = FakeOpenAI({"ok": True}, {"ok": False}, schema_format=False)
    model = chat(server)
    assert await model.reply(ASK, SCHEMA) == {"ok": True}
    assert await model.reply(ASK, SCHEMA) == {"ok": False}
    formats = [b["response_format"]["type"] for b in server.bodies]
    assert formats == ["json_schema", "json_object", "json_object"], "the refusal is remembered"
    system = server.bodies[1]["messages"][0]
    assert system["role"] == "system" and system["content"].startswith("You answer in JSON.")
    assert json.dumps(SCHEMA) in system["content"] and len(server.bodies[1]["messages"]) == 2


async def test_with_no_system_message_the_schema_goes_in_a_new_one():
    server = FakeOpenAI({"ok": True}, schema_format=False)
    await chat(server).reply([{"role": "user", "content": "hi"}], SCHEMA)
    assert [m["role"] for m in server.bodies[1]["messages"]] == ["system", "user"]


async def test_a_server_refusing_both_formats_gets_a_clear_error_and_is_asked_with_the_schema_again():
    class Neither(FakeOpenAI):
        def handle(self, request):
            self.bodies.append(json.loads(request.content))
            return httpx.Response(400, json={"error": {"message": "bad response_format"}})

    server = Neither()
    model = chat(server)
    with pytest.raises(ModelError, match="refused both a json_schema and a json_object reply.*bad response_format"):
        await model.reply(ASK, SCHEMA)
    with pytest.raises(ModelError):
        await model.reply(ASK, SCHEMA)
    assert [b["response_format"]["type"] for b in server.bodies] == ["json_schema", "json_object"] * 2


async def test_a_model_that_takes_no_temperature_is_asked_without_one_from_then_on():
    server = FakeOpenAI({"ok": True}, {"ok": True}, temperature=False)
    model = chat(server)
    await model.reply(ASK, SCHEMA)
    await model.reply(ASK, SCHEMA)
    assert ["temperature" in b for b in server.bodies] == [True, False, False]
    assert all(b["response_format"]["type"] == "json_schema" for b in server.bodies)


async def test_the_key_goes_as_a_bearer_token_and_a_refusal_never_echoes_it():
    server = FakeOpenAI({"ok": True}, key="sk-right")
    assert await chat(server, api_key="sk-right").reply(ASK, SCHEMA) == {"ok": True}
    with pytest.raises(ModelError, match="refused the key: check MY_KEY") as caught:
        await chat(server, api_key="sk-wrong", key_source="check MY_KEY").reply(ASK, SCHEMA)
    assert "sk-" not in str(caught.value)


async def test_a_server_wanting_a_key_when_none_is_given_says_so():
    with pytest.raises(ModelError, match="wants a key.*Settings > Models"):
        await chat(FakeOpenAI(key="sk-right")).reply(ASK, SCHEMA)


async def test_a_reply_too_slow_is_a_timeout():
    class Slow(FakeOpenAI):
        def handle(self, request):
            raise httpx.ReadTimeout("timed out", request=request)

    with pytest.raises(ModelError, match="took longer than 5s"):
        await chat(Slow()).reply(ASK, SCHEMA)


async def test_a_server_not_running_is_unreachable_at_its_url():
    server = FakeOpenAI()
    server.down = True
    with pytest.raises(ModelError, match="not reachable at http://llm.test/v1"):
        await chat(server).reply(ASK, SCHEMA)


async def test_a_wrong_path_says_to_check_base_url():
    with pytest.raises(ModelError, match="HTTP 404.*base_url"):
        await chat(FakeOpenAI(), base="http://llm.test/api").reply(ASK, SCHEMA)


async def test_a_body_that_is_not_json_is_no_usable_answer():
    class Html(FakeOpenAI):
        def handle(self, request):
            return httpx.Response(200, text="<html>proxy login</html>")

    with pytest.raises(ModelError, match="no usable answer"):
        await chat(Html()).reply(ASK, SCHEMA)


async def test_content_that_is_not_json_is_handed_back_for_the_loop_to_reject():
    assert await chat(FakeOpenAI("sorry, I cannot")).reply(ASK, SCHEMA) == "sorry, I cannot"


@pytest.mark.parametrize("message", [
    {"content": '<think>hmm {"ok": false}</think>\n{"ok": true}'},
    {"content": '```json\n{"ok": true}\n```'},
    {"content": None, "reasoning_content": '{"ok": true}'},
    {"content": "", "reasoning": 'so the answer: {"ok": true}'},
    {"content": [{"type": "text", "text": '{"ok": true}'}]},
])
async def test_reasoning_models_answers_are_found_where_they_put_them(message):
    assert await chat(FakeOpenAI({"message": message})).reply(ASK, SCHEMA) == {"ok": True}


@pytest.mark.parametrize("choice", [
    {"message": None},
    {"message": {"content": {"ok": True}}},
    {"message": {"content": 7}},
    {"message": {"content": [{"type": "text", "text": None}]}},
    {"message": {"content": None, "reasoning_content": {"ok": True}}},
    {"message": {"content": None, "reasoning": ["x"]}},
    "not a choice",
])
async def test_a_malformed_reply_is_a_model_error_not_a_crash(choice):
    def handle(request):
        return httpx.Response(200, json={"choices": [choice]})

    model = OpenAICompatibleChat("m", "http://llm.test/v1", 5.0, transport=httpx.MockTransport(handle))
    with pytest.raises(ModelError, match="no usable answer"):
        await model.reply(ASK, SCHEMA)


async def test_a_reply_is_bounded_in_wall_clock_time_not_only_per_read():
    import asyncio

    class Trickle(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            await asyncio.sleep(10)

    model = OpenAICompatibleChat("m", "http://llm.test/v1", 0.05, transport=Trickle())
    with pytest.raises(ModelError, match="took longer than 0s"):
        await model.reply(ASK, SCHEMA)


async def test_a_reply_is_capped_and_a_model_that_wants_max_completion_tokens_gets_it_from_then_on():
    server = FakeOpenAI({"ok": True}, {"ok": True}, cap="max_completion_tokens")
    model = chat(server)
    await model.reply(ASK, SCHEMA)
    await model.reply(ASK, SCHEMA)
    caps = [{k: b[k] for k in ("max_tokens", "max_completion_tokens") if k in b} for b in server.bodies]
    assert caps == [{"max_tokens": MAX_REPLY_TOKENS}, {"max_completion_tokens": MAX_REASONING_TOKENS},
                    {"max_completion_tokens": MAX_REASONING_TOKENS}]
    assert all(b["response_format"]["type"] == "json_schema" for b in server.bodies)


async def test_a_server_taking_neither_cap_is_asked_without_one():
    server = FakeOpenAI({"ok": True}, cap=None)
    await chat(server).reply(ASK, SCHEMA)
    assert [("max_tokens" in b, "max_completion_tokens" in b) for b in server.bodies] == [
        (True, False), (False, True), (False, False)]


@pytest.mark.parametrize("error", [
    {"message": "Input should be a valid string", "input": {"temperature": 0}},
    {"message": "The prompt mentions temperature too often"},
])
async def test_temperature_is_kept_when_the_error_is_not_about_it(error):
    server = FakeOpenAI({"ok": True})
    replies = [httpx.Response(400, json={"error": error})]

    def handle(request):
        return replies.pop(0) if replies else server.handle(request)

    model = OpenAICompatibleChat("m", "http://llm.test/v1", 5.0, transport=httpx.MockTransport(handle))
    await model.reply(ASK, SCHEMA)
    assert server.bodies[0]["temperature"] == 0 and server.bodies[0]["response_format"]["type"] == "json_object"


async def test_temperature_named_as_the_param_is_dropped():
    server = FakeOpenAI({"ok": True})
    replies = [httpx.Response(400, json={"error": {"message": "bad value", "param": "temperature"}})]

    def handle(request):
        return replies.pop(0) if replies else server.handle(request)

    await OpenAICompatibleChat("m", "http://llm.test/v1", 5.0, transport=httpx.MockTransport(handle)).reply(ASK, SCHEMA)
    assert "temperature" not in server.bodies[0] and server.bodies[0]["response_format"]["type"] == "json_schema"


def test_a_query_on_base_url_stays_out_of_the_path():
    assert chat_url("http://llm.test/v1?x=1") == "http://llm.test/v1/chat/completions?x=1"


async def test_errors_show_base_url_without_credentials_or_query():
    server = FakeOpenAI()
    server.down = True
    with pytest.raises(ModelError) as caught:
        await chat(server, base="http://user:pw@llm.test/v1?api_key=secret").reply(ASK, SCHEMA)
    assert "at http://llm.test/v1 " in str(caught.value) and "secret" not in str(caught.value)
    assert "pw" not in str(caught.value)


async def test_only_a_closing_think_tag_drops_everything_before_it():
    reply = await chat(FakeOpenAI('I think {"ok": false} maybe</think>\n{"ok": true}')).reply(ASK, SCHEMA)
    assert reply == {"ok": True}


async def test_a_refused_key_shows_the_servers_reason():
    with pytest.raises(ModelError, match="refused the key: check MY_KEY.*Incorrect API key provided"):
        await chat(FakeOpenAI(key="sk-right"), api_key="sk-wrong", key_source="check MY_KEY").reply(ASK, SCHEMA)


async def test_a_refusal_is_no_answer():
    assert await chat(FakeOpenAI({"message": {"content": None, "refusal": "I can't"}})).reply(ASK, SCHEMA) is None


async def test_the_key_never_follows_a_redirect_to_another_host():
    seen = []

    def handle(request):
        seen.append(request)
        return httpx.Response(307, headers={"location": "http://elsewhere.test/v1/chat/completions"})

    model = OpenAICompatibleChat("m", "http://llm.test/v1", 5.0, api_key="sk-x", transport=httpx.MockTransport(handle))
    with pytest.raises(ModelError, match="HTTP 307"):
        await model.reply(ASK, SCHEMA)
    assert [r.url.host for r in seen] == ["llm.test"]
