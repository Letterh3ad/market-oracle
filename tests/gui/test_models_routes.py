from dataclasses import replace

import httpx

from oracle.research.claude_code import BIN_ENV
from tests.fakes.claude_cli import shim
from tests.fakes.ollama import FakeOllama

BIG = {"provider": "ollama", "model": "qwen3:8b", "context": 8192}


async def test_profiles_are_listed_default_first_and_added_edited_and_deleted(ctx, client, tmp_path):
    listing = (await client.get("/api/models")).json()
    assert [p["name"] for p in listing["profiles"]] == ["default"]
    assert list(listing["providers"]) == ["ollama", "claude_api", "openai_compatible"]
    assert listing["providers"]["ollama"] == {"base_url": "http://localhost:11434", "base_url_required": False,
                                              "context": 16384, "timeout_s": 180.0, "keyed": False, "url": True}
    assert listing["profiles"][0]["set"] == {"base_url": None, "context": None, "timeout_s": None}
    added = await client.post("/api/models", json={"name": "big", **BIG})
    assert added.status_code == 200 and [p["name"] for p in added.json()["profiles"]] == ["default", "big"]
    edited = (await client.put("/api/models/big", json={**BIG, "context": 4096})).json()
    assert edited["profiles"][1]["context"] == 4096 and ctx.config.models["big"].context == 4096
    assert "[models.big]" in (tmp_path / "oracle.toml").read_text(encoding="utf-8")
    deleted = (await client.delete("/api/models/big")).json()
    assert [p["name"] for p in deleted["profiles"]] == ["default"]


async def test_bad_changes_are_refused_with_a_clear_message(client):
    await client.post("/api/models", json={"name": "big", **BIG})
    again = await client.post("/api/models", json={"name": "big", **BIG})
    assert again.status_code == 400 and "a profile named big already exists" in again.json()["error"]
    bad = await client.put("/api/models/big", json={**BIG, "provider": "gpt"})
    assert bad.status_code == 400 and "models.big.provider must be one of" in bad.json()["error"]
    assert (await client.delete("/api/models/default")).status_code == 400
    assert (await client.put("/api/models/nope", json=BIG)).status_code == 404


async def test_a_key_is_saved_but_never_sent_back(client):
    response = await client.post("/api/models", json={"name": "deep", "provider": "claude_api",
                                                      "model": "claude-opus-5", "key": "sk-secret"})
    assert response.status_code == 200 and "sk-secret" not in response.text
    assert response.json()["profiles"][1]["key_state"] == "set"


async def test_test_reports_ok_and_the_reply_time_or_the_error(ctx, client):
    ctx.transport = httpx.MockTransport(FakeOllama({"ok": True}).handle)
    ok = (await client.post("/api/models/default/test")).json()
    assert ok["ok"] is True and ok["seconds"] >= 0 and ok["error"] is None
    await client.post("/api/models", json={"name": "big", **BIG})
    failed = (await client.post("/api/models/big/test")).json()
    assert failed["ok"] is False and "ollama pull qwen3:8b" in failed["error"]
    assert (await client.post("/api/models/nope/test")).status_code == 404


async def test_a_test_while_a_job_holds_the_model_is_409_with_why(ctx, client):
    from oracle.jobs import JobLock

    JobLock(ctx.conn, ctx.clock).acquire("discovery_chat", 600)
    busy = await client.post("/api/models/default/test")
    assert busy.status_code == 409 and busy.json()["error"] == "discovery chat is using the model: test once it finishes"


async def test_claude_code_is_listed_saved_and_tested_once_the_file_allows_it(ctx, client, tmp_path, monkeypatch):
    sub = {"name": "sub", "provider": "claude_code", "model": "sonnet"}
    refused = await client.post("/api/models", json=sub)
    assert refused.status_code == 400 and "allow_claude_code" in refused.json()["error"]
    (tmp_path / "oracle.toml").write_text("[models]\nallow_claude_code = true\n", encoding="utf-8")
    ctx.config = replace(ctx.config, allow_claude_code=True)
    listing = (await client.post("/api/models", json=sub)).json()
    assert listing["providers"]["claude_code"]["url"] is False and listing["profiles"][1]["provider"] == "claude_code"
    monkeypatch.setenv(BIN_ENV, str(shim(tmp_path, "good")))
    tested = (await client.post("/api/models/sub/test")).json()
    assert tested["ok"] is True, tested
