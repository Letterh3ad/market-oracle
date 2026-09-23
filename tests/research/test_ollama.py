import pytest

from oracle.research.llm import ModelError
from oracle.research.ollama import OllamaChat
from tests.fakes.ollama import FakeOllama
from tests.fakes.web import FakeWeb

MODEL = "qwen3:4b-instruct-2507-q8_0"
SCHEMA = {"type": "object"}


def chat(ollama, model=MODEL):
    return OllamaChat(model, "http://localhost:11434", 5.0, 8192, transport=FakeWeb(ollama).transport)


async def test_a_reply_is_constrained_to_the_schema_and_parsed():
    ollama = FakeOllama({"thought": "t", "action": "final", "input": "x"})
    reply = await chat(ollama).reply([{"role": "user", "content": "hi"}], SCHEMA)
    assert reply == {"thought": "t", "action": "final", "input": "x"}
    [body] = ollama.bodies
    assert body["format"] == SCHEMA and body["stream"] is False
    # A reply is capped: a small model can loop inside a JSON array until the timeout.
    assert body["options"] == {"temperature": 0, "num_ctx": 8192, "num_predict": 1024}
    assert body["messages"] == [{"role": "user", "content": "hi"}]


async def test_text_that_is_not_json_is_handed_back_for_the_loop_to_reject():
    assert await chat(FakeOllama("sorry, I cannot")).reply([], SCHEMA) == "sorry, I cannot"


async def test_ollama_not_running_says_how_to_start_it():
    ollama = FakeOllama()
    ollama.down = True
    with pytest.raises(ModelError, match="not reachable at http://localhost:11434.*ollama serve"):
        await chat(ollama).reply([], SCHEMA)


async def test_a_model_not_pulled_says_how_to_get_it():
    with pytest.raises(ModelError, match="ollama pull other:1b"):
        await chat(FakeOllama(), model="other:1b").reply([], SCHEMA)


async def test_a_reply_too_slow_is_called_a_timeout_not_unreachable():
    import httpx

    class Slow(FakeOllama):
        def handle(self, request):
            raise httpx.ReadTimeout("timed out", request=request)

    with pytest.raises(ModelError, match="took longer than 5s") as caught:
        await chat(Slow()).reply([], SCHEMA)
    assert "ollama serve" not in str(caught.value)
