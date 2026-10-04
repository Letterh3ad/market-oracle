"""The local MCP server (research-lab 04): JSON-RPC over stdio, four tools over the literature services."""

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from oracle.config import Config
from oracle.mcp.server import Server
from oracle.services.context import open_context
from tests.fakes.clock import FakeClock
from tests.fakes.embed import FakeEmbed

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 10, 4, 1, 0, tzinfo=timezone.utc)


@pytest.fixture
def server(tmp_path):
    config = Config(db_path=tmp_path / "live.db", research_db_path=tmp_path / "research.db")
    with open_context(config, None, FakeClock(NOW)) as ctx:
        yield Server(ctx)


def call(server, name, **arguments):
    reply = server.handle({"jsonrpc": "2.0", "id": 9, "method": "tools/call",
                           "params": {"name": name, "arguments": arguments}})
    assert reply["id"] == 9
    result = reply["result"]
    return result.get("isError", False), json.loads(result["content"][0]["text"]) if not result.get("isError") \
        else result["content"][0]["text"]


def test_initialize_names_the_server_and_its_tools_capability(server):
    reply = server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                           "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                                      "clientInfo": {"name": "t", "version": "0"}}})
    assert reply["result"]["protocolVersion"] == "2025-03-26"
    assert reply["result"]["capabilities"] == {"tools": {"listChanged": False}}
    assert reply["result"]["serverInfo"]["name"] == "market-oracle-lab"
    later = server.handle({"jsonrpc": "2.0", "id": 2, "method": "initialize", "params": {"protocolVersion": "1999-01-01"}})
    assert later["result"]["protocolVersion"] == "2025-06-18"
    assert server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    assert server.handle({"jsonrpc": "2.0", "id": 3, "method": "ping"})["result"] == {}


def test_tools_list_gives_each_tool_an_input_schema(server):
    tools = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})["result"]["tools"]
    assert [t["name"] for t in tools] == ["lookup", "similar", "record", "search_log"]
    assert all(t["inputSchema"]["type"] == "object" and t["description"] for t in tools)
    assert tools[1]["inputSchema"]["required"] == ["text"]


def test_lookup_and_similar_answer_from_the_services(server, monkeypatch):
    bad, found = call(server, "lookup", query="t-15")
    assert not bad and found["methods"][0]["ref"] == "T-15"
    bad, alike = call(server, "similar", text="buy stocks near their yearly high")
    assert not bad and alike["mode"] == "keyword" and len(alike["hits"]) == 5
    monkeypatch.setattr("oracle.services.meaning_service.embedder", lambda ctx: FakeEmbed())
    bad, alike = call(server, "similar", text="buy stocks near their yearly high")
    assert alike["mode"] == "meaning" and alike["hits"][0]["ref"] == "T-15"


def test_record_and_search_log_are_the_callers_by_default_claude(server):
    bad, rec = call(server, "record", id="arXiv:2601.99999", title="Fresh paper", new_method="Fresh idea",
                    definition="Buy X.")
    assert not bad and rec["source"]["added_by"] == "claude" and rec["method"]["ref"] == "M-001"
    bad, logged = call(server, "search_log", query="decay", where="SSRN", found=["arXiv:2601.99999"], by="autopilot")
    assert not bad and logged["by"] == "autopilot" and logged["found"][0]["title"] == "Fresh paper"


def test_a_refused_call_or_bad_arguments_come_back_as_a_tool_error(server):
    bad, message = call(server, "lookup", query=" ")
    assert bad and "type a title" in message
    bad, message = call(server, "lookup", nonsense=1)
    assert bad and "query" in message
    reply = server.handle({"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "drop_db"}})
    assert reply["error"]["code"] == -32602


def test_an_unknown_method_and_a_malformed_line_are_json_rpc_errors(server):
    assert server.handle({"jsonrpc": "2.0", "id": 5, "method": "resources/list"})["error"]["code"] == -32601
    assert server.handle_line(b"{not json")["error"]["code"] == -32700
    assert server.handle({"jsonrpc": "2.0", "method": "notifications/cancelled"}) is None


def test_the_server_answers_a_real_session_over_stdio(tmp_path):
    cfg = tmp_path / "oracle.toml"
    # A port nothing listens on: the session must work with Ollama down, and never reach a real one.
    cfg.write_text('db_path = "o.db"\nresearch_db_path = "r.db"\n[llm]\nbase_url = "http://127.0.0.1:9"\n',
                   encoding="utf-8")
    lines = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "smoke", "version": "1"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "lookup", "arguments": {"query": "T-15"}}},
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
         "params": {"name": "similar", "arguments": {"text": "buy stocks near their yearly high"}}},
        {"jsonrpc": "2.0", "id": 5, "method": "tools/call",
         "params": {"name": "record", "arguments": {"title": "Smoke paper", "new_method": "Smoke rule"}}},
        {"jsonrpc": "2.0", "id": 6, "method": "tools/call",
         "params": {"name": "search_log", "arguments": {"query": "smoke", "where": "nowhere"}}},
    ]
    env = {**os.environ, "PYTHONPATH": str(ROOT), "PYTHONIOENCODING": "utf-8"}
    out = subprocess.run([sys.executable, "-m", "oracle.mcp", "--config", str(cfg)], cwd=tmp_path, env=env,
                         input="".join(json.dumps(m) + "\n" for m in lines).encode("utf-8"), capture_output=True,
                         timeout=120, check=True)
    replies = [json.loads(line) for line in out.stdout.decode("utf-8").splitlines()]
    assert [r["id"] for r in replies] == [1, 2, 3, 4, 5, 6]
    assert len(replies[1]["result"]["tools"]) == 4
    assert all(not r["result"].get("isError") for r in replies[2:]), replies
    similar = json.loads(replies[3]["result"]["content"][0]["text"])
    assert similar["mode"] == "keyword" and "127.0.0.1:9" in similar["note"]
    assert json.loads(replies[4]["result"]["content"][0]["text"])["method"]["ref"] == "M-001"


def test_arguments_of_the_wrong_type_are_a_tool_error_never_a_crash(server):
    bad, message = call(server, "similar", text=5)
    assert bad and "text" in message and "string" in message
    bad, message = call(server, "search_log", query="q", where="SSRN", found="arXiv:2601.99999")
    assert bad and "found" in message and "list" in message
    bad, message = call(server, "search_log", query="q", where="SSRN", found=["ok", 3])
    assert bad and "found" in message
    bad, message = call(server, "record", title="T", year="2020")
    assert bad and "year" in message
    bad, message = call(server, "record", title="T", year=True)
    assert bad and "year" in message
    bad, message = call(server, "record", title="T", quality="superb")
    assert bad and "quality" in message


def test_params_or_arguments_that_are_not_objects_are_invalid_params(server):
    for params in (["x"], "x", 5):
        reply = server.handle({"jsonrpc": "2.0", "id": 6, "method": "tools/call", "params": params})
        assert reply["id"] == 6 and reply["error"]["code"] == -32602
    reply = server.handle({"jsonrpc": "2.0", "id": 7, "method": "initialize", "params": ["x"]})
    assert reply["error"]["code"] == -32602
    reply = server.handle({"jsonrpc": "2.0", "id": 8, "method": "tools/call",
                           "params": {"name": "similar", "arguments": ["x"]}})
    assert reply["error"]["code"] == -32602
    reply = server.handle({"jsonrpc": "2.0", "id": 9, "method": "tools/call", "params": {"name": ["similar"]}})
    assert reply["error"]["code"] == -32602


def test_a_request_without_a_method_is_an_invalid_request(server):
    assert server.handle({"jsonrpc": "2.0", "id": 5})["error"]["code"] == -32600
    assert server.handle({"jsonrpc": "2.0", "id": 5, "method": 7})["error"]["code"] == -32600


def test_an_unexpected_failure_is_an_internal_error_and_the_server_goes_on(server, monkeypatch):
    def boom(ctx, text):
        raise RuntimeError("disk on fire")
    monkeypatch.setattr("oracle.services.meaning_service.similar", boom)
    reply = server.handle_line(json.dumps({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                                           "params": {"name": "similar", "arguments": {"text": "x"}}}).encode())
    assert reply["id"] == 3 and reply["error"]["code"] == -32603 and "disk on fire" in reply["error"]["message"]
    assert server.handle_line(b'{"jsonrpc": "2.0", "id": 4, "method": "ping"}')["result"] == {}


def test_serve_survives_bad_lines_and_answers_the_next(server, monkeypatch):
    import io

    from oracle.mcp.server import serve

    def boom(ctx, text):
        raise RuntimeError("disk on fire")
    monkeypatch.setattr("oracle.services.meaning_service.similar", boom)
    lines = [{"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "similar", "arguments": {"text": 5}}},
             {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": ["x"]},
             {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "similar", "arguments": {"text": "x"}}},
             {"jsonrpc": "2.0", "method": "tools/call", "params": {"name": "similar", "arguments": {"text": "x"}}},
             {"jsonrpc": "2.0", "id": 4, "method": "ping"}]
    out = io.BytesIO()
    serve(server.ctx, io.BytesIO("".join(json.dumps(m) + "\n" for m in lines).encode()), out)
    replies = [json.loads(line) for line in out.getvalue().decode().splitlines()]
    assert [r["id"] for r in replies] == [1, 2, 3, 4]
    assert replies[0]["result"]["isError"] and replies[1]["error"]["code"] == -32602
    assert replies[2]["error"]["code"] == -32603 and replies[3]["result"] == {}
