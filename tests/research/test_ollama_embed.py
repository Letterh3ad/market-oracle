import json

import httpx
import pytest

from oracle.research.llm import ModelError
from oracle.research.ollama import OllamaEmbed


def embedder(handler, model="nomic-embed-text"):
    return OllamaEmbed(model, "http://localhost:11434/", 5.0, transport=httpx.MockTransport(handler))


def test_texts_go_in_batches_with_nomics_task_prefixes():
    bodies = []

    def handler(request):
        body = json.loads(request.content)
        bodies.append((request.url.path, body))
        return httpx.Response(200, json={"embeddings": [[float(len(t)), 1.0] for t in body["input"]]})

    e = embedder(handler)
    e.batch = 2
    vectors = e.embed(["ab", "abc", "abcd"])
    assert vectors == [[19.0, 1.0], [20.0, 1.0], [21.0, 1.0]]
    assert [p for p, _ in bodies] == ["/api/embed", "/api/embed"]
    assert bodies[0][1]["input"] == ["search_document: ab", "search_document: abc"]
    assert bodies[0][1]["model"] == "nomic-embed-text" and bodies[0][1]["truncate"] is True
    e.embed(["q"], query=True)
    assert bodies[-1][1]["input"] == ["search_query: q"]


def test_another_model_gets_the_text_as_is():
    seen = []

    def handler(request):
        seen.append(json.loads(request.content)["input"])
        return httpx.Response(200, json={"embeddings": [[1.0]]})

    embedder(handler, model="mxbai-embed-large").embed(["plain"], query=True)
    assert seen == [["plain"]]


def test_ollama_down_or_the_model_missing_is_a_model_error_saying_what_to_do():
    def down(request):
        raise httpx.ConnectError("refused")

    with pytest.raises(ModelError, match="not reachable.*ollama serve"):
        embedder(down).embed(["x"])
    with pytest.raises(ModelError, match="ollama pull nomic-embed-text"):
        embedder(lambda r: httpx.Response(404, json={"error": "model not found"})).embed(["x"])


@pytest.mark.parametrize("reply", [{"embeddings": []}, {"nope": 1}, {"embeddings": [["a"]]}])
def test_a_reply_without_one_vector_per_text_is_a_model_error(reply):
    with pytest.raises(ModelError, match="no usable embeddings"):
        embedder(lambda r: httpx.Response(200, json=reply)).embed(["x"])


def test_localhost_is_asked_over_ipv4():
    hosts = []

    def handler(request):
        hosts.append(request.url.host)
        return httpx.Response(200, json={"embeddings": [[1.0]]})

    embedder(handler).embed(["x"])
    OllamaEmbed("m", "http://gpu-box:11434", 5.0, transport=httpx.MockTransport(handler)).embed(["x"])
    assert hosts == ["127.0.0.1", "gpu-box"]
