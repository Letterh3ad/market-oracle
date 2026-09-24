"""The claude_code backend against a fake `claude` executable: never the real CLI."""

import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from oracle.research.claude_code import BIN_ENV, ClaudeCodeChat, flatten
from oracle.research.llm import ModelError

FAKE = str(Path(__file__).parents[1] / "fakes" / "claude_cli.py")
SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}
PROBE = [{"role": "system", "content": "You answer in JSON."}, {"role": "user", "content": "Answer."}]


def chat(mode, timeout_s=30.0, model="sonnet"):
    return ClaudeCodeChat(model, timeout_s, command=(sys.executable, FAKE, mode))


def _alive(pid: int) -> bool:
    if os.name == "nt":
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True).stdout
        return str(pid) in out
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _gone(pid: int, within: float = 5.0) -> bool:
    deadline = time.monotonic() + within
    while time.monotonic() < deadline:
        if not _alive(pid):
            return True
        time.sleep(0.1)
    return False


async def test_a_reply_is_the_json_in_the_result_with_the_prompt_on_stdin_and_no_tools(tmp_path, monkeypatch):
    record = tmp_path / "record.json"
    monkeypatch.setenv("FAKE_CLAUDE_RECORD", str(record))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-would-bill-the-api")
    messages = [{"role": "system", "content": "Be brief."}, {"role": "user", "content": "Go."},
                {"role": "assistant", "content": '{"action": "search"}'}, {"role": "user", "content": "Observation: x"}]
    assert await chat("good").reply(messages, SCHEMA) == {"ok": True}
    seen = json.loads(record.read_text(encoding="utf-8"))
    flags = seen["flags"]
    assert flags[:5] == ["-p", "--output-format", "json", "--model", "sonnet"]
    assert "--disallowedTools" in flags and "--strict-mcp-config" in flags
    assert seen["prompt"] == flatten(messages, SCHEMA)
    assert "ANTHROPIC_API_KEY" not in seen["env"]


def test_the_flattened_prompt_keeps_every_turn_in_order_and_the_schema():
    messages = [{"role": "system", "content": "Rules A."}, {"role": "system", "content": "Rules B."},
                {"role": "user", "content": "First."}, {"role": "assistant", "content": "{}"},
                {"role": "user", "content": "Second."}]
    prompt = flatten(messages, SCHEMA)
    order = [prompt.index(s) for s in ("Rules A.", "Rules B.", "First.", "{}", "Second.", json.dumps(SCHEMA))]
    assert order == sorted(order)
    assert prompt.count('<message role="user">') == 2 and prompt.count('<message role="assistant">') == 1


async def test_a_fenced_answer_is_unwrapped_and_a_plain_text_answer_comes_back_as_text():
    assert await chat("fenced").reply(PROBE, SCHEMA) == {"ok": True}
    assert await chat("text").reply(PROBE, SCHEMA) == "I would rather not."


async def test_output_that_is_not_the_cli_json_is_a_model_error():
    with pytest.raises(ModelError, match="not the JSON"):
        await chat("garbage").reply(PROBE, SCHEMA)


async def test_a_non_zero_exit_is_a_model_error_with_the_cli_message():
    with pytest.raises(ModelError, match="exited with 2.*unknown option"):
        await chat("fail").reply(PROBE, SCHEMA)


async def test_a_logged_out_session_says_to_log_in():
    with pytest.raises(ModelError, match="not logged in.*run `claude`"):
        await chat("logged_out").reply(PROBE, SCHEMA)


async def test_a_missing_cli_is_a_model_error_naming_the_setting(monkeypatch):
    monkeypatch.setenv(BIN_ENV, "no-such-claude-anywhere")
    with pytest.raises(ModelError, match=f"not found.*{BIN_ENV}"):
        await ClaudeCodeChat("sonnet", 30.0).reply(PROBE, SCHEMA)
    with pytest.raises(ModelError, match="not found"):
        await ClaudeCodeChat("sonnet", 30.0, command=("C:/nowhere/claude.exe",)).reply(PROBE, SCHEMA)


async def test_a_model_name_the_cli_could_misread_is_refused_before_running():
    with pytest.raises(ModelError, match="model name"):
        await chat("good", model="sonnet & calc").reply(PROBE, SCHEMA)


async def test_a_timeout_kills_the_whole_process_tree(tmp_path, monkeypatch):
    pid_file = tmp_path / "pid"
    monkeypatch.setenv("FAKE_CLAUDE_PID", str(pid_file))
    start = time.monotonic()
    with pytest.raises(ModelError, match="longer than 2s"):
        await chat("hang", timeout_s=2.0).reply(PROBE, SCHEMA)
    assert time.monotonic() - start < 15
    assert _gone(int(pid_file.read_text()))


async def test_cancelling_the_run_kills_the_whole_process_tree(tmp_path, monkeypatch):
    pid_file = tmp_path / "pid"
    monkeypatch.setenv("FAKE_CLAUDE_PID", str(pid_file))
    task = asyncio.create_task(chat("hang").reply(PROBE, SCHEMA))
    for _ in range(100):
        if pid_file.is_file() and pid_file.read_text():
            break
        await asyncio.sleep(0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert _gone(int(pid_file.read_text()))
