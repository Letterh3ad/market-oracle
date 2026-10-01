import json

import httpx
import pytest

from oracle.research.laya import LayaInput
from oracle.research.reader import CODES, TIMEOUT_S, OllamaReader, ReaderError, decode, reader_tag, schema
from oracle.services import reader_service

ITEMS = [LayaInput("Toyota Motor (7203.T)", "Toyota recalls 1m cars"), LayaInput("Apple (AAPL)", "Apple beats"),
         LayaInput("Linde (LIN)", "van der Linde crowned champion")]


def transport(answers, seen=None, status=200):
    """Answers are replied to calls in turn."""
    it = iter(answers)

    def handle(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if seen is not None:
            seen.append(body)
        content = next(it)
        return httpx.Response(status, json={"message": {"content": content if isinstance(content, str)
                                                         else json.dumps(content)}})
    return httpx.MockTransport(handle)


def test_codes_decode_to_scores():
    assert decode("d3").direction == "down" and decode("d3").event == "recall_safety" and decode("d3").relevant == 1.0
    assert decode("u0").direction_probs == {"up": 1.0} and decode("u0").event == "earnings"
    x = decode("x")
    assert (x.relevant, x.direction, x.event) == (0.0, "none", "other")
    assert all(decode(c) for c in CODES)
    assert decode("q1") is None and decode("u") is None and decode(3) is None and decode("n10") is None


def test_one_call_a_batch_with_one_required_code_per_numbered_headline():
    seen = []
    reader = OllamaReader("m:1b", "http://x", transport=transport([{"codes": ["d3", "u0", "x"]}], seen))
    scores = reader.score(ITEMS)
    assert [s.direction for s in scores] == ["down", "up", "none"] and scores[2].relevant == 0.0
    body = seen[0]
    assert body["model"] == "m:1b" and body["options"]["temperature"] == 0 and body["think"] is False
    codes = body["format"]["properties"]["codes"]
    assert body["format"] == schema(3) and codes["minItems"] == codes["maxItems"] == 3 and codes["items"]["enum"] == CODES
    user = body["messages"][1]["content"].splitlines()
    assert user[0] == "1. Company: Toyota Motor (7203.T) | Headline: Toyota recalls 1m cars"
    assert reader.stats.calls == 1 and reader.stats.items == 3 and reader.stats.unparsed == 0
    assert reader.tag == reader_tag("m:1b") == "ollama:m:1b"


def test_unusable_codes_are_asked_once_more_alone_then_left_unscored():
    seen = []
    reader = OllamaReader("m", "http://x", transport=transport([{"codes": ["d3", "zz", "x"]}, {"codes": ["u0"]}], seen))
    assert [s and s.direction for s in reader.score(ITEMS)] == ["down", "up", "none"]
    assert seen[1]["messages"][1]["content"] == "1. Company: Apple (AAPL) | Headline: Apple beats"
    reader = OllamaReader("m", "http://x", transport=transport(["not json", "still not"]))
    assert reader.score(ITEMS[:1]) == [None] and reader.stats.unparsed == 1
    reader = OllamaReader("m", "http://x", transport=transport([{"codes": ["x"]}, {"codes": ["x", "x", "x"]}]))
    assert reader.score(ITEMS[:2]) == [None, None], "a count that does not match cannot be aligned"


def test_the_model_is_kept_between_calls_and_released_on_request_and_close():
    seen = []
    reader = OllamaReader("m", "http://x", transport=transport([{"codes": ["x"]}, {}, {}], seen))
    reader.score(ITEMS[:1])
    assert seen[0]["keep_alive"] == "30m"
    reader.release()
    reader.close()
    assert seen[1] == seen[2] == {"model": "m", "messages": [], "keep_alive": 0}


def test_localhost_is_asked_on_ipv4():
    seen = []

    def handle(request):
        seen.append(str(request.url))
        return httpx.Response(200, json={"message": {"content": '{"codes": ["x"]}'}})
    OllamaReader("m", "http://localhost:11434/", transport=httpx.MockTransport(handle)).score(ITEMS[:1])
    assert seen == ["http://127.0.0.1:11434/api/chat"]


def test_a_call_that_times_out_leaves_its_batch_unscored():
    def slow(request):
        raise httpx.ReadTimeout("slow")
    reader = OllamaReader("m", "http://x", transport=httpx.MockTransport(slow))
    assert reader.score(ITEMS) == [None, None, None] and reader.stats.unparsed == 3
    assert reader.stats.calls == 1, "no retry after a timeout: a Cancel answers within one timeout"
    assert TIMEOUT_S <= 300 and reader_service.LEASE_S >= 2 * TIMEOUT_S


def test_a_missing_model_or_ollama_is_an_error():
    with pytest.raises(ReaderError, match="ollama pull"):
        OllamaReader("m", "http://x", transport=transport([{}], status=404)).score(ITEMS)

    def down(request):
        raise httpx.ConnectError("refused")
    with pytest.raises(ReaderError, match="not reachable"):
        OllamaReader("m", "http://x", transport=httpx.MockTransport(down)).score(ITEMS)
