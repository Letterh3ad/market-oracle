"""The claude_code backend against a fake `claude` executable: never the real CLI."""

import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from oracle.research import claude_code
from oracle.research.claude_code import BIN_ENV, ClaudeCodeChat, flatten
from oracle.research.llm import ModelError
from tests.fakes.claude_cli import shim

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


async def _pid(path: Path, within: float = 15.0) -> int:
    deadline = time.monotonic() + within
    while not (path.is_file() and path.read_text().strip()):
        if time.monotonic() >= deadline:
            raise AssertionError(f"the fake never wrote {path.name}")
        await asyncio.sleep(0.1)
    return int(path.read_text())


def _hanging(tmp_path, monkeypatch) -> Path:
    """The fake behind a .cmd shim found through BIN_ENV, as the app starts it; returns
    where its grandchild's pid will be."""
    monkeypatch.setenv(BIN_ENV, str(shim(tmp_path, "hang")))
    monkeypatch.setenv("FAKE_CLAUDE_PID", str(tmp_path / "pid"))
    return tmp_path / "pid"


async def test_a_reply_is_the_json_in_the_result_with_the_prompt_on_stdin_and_no_tools(tmp_path, monkeypatch):
    record = tmp_path / "record.json"
    monkeypatch.setenv("FAKE_CLAUDE_RECORD", str(record))
    messages = [{"role": "system", "content": "Be brief."}, {"role": "user", "content": "Go."},
                {"role": "assistant", "content": '{"action": "search"}'}, {"role": "user", "content": "Observation: x"}]
    assert await chat("good").reply(messages, SCHEMA) == {"ok": True}
    seen = json.loads(record.read_text(encoding="utf-8"))
    flags = seen["flags"]
    assert flags[:5] == ["-p", "--output-format", "json", "--model", "sonnet"]
    pairs = list(zip(flags, flags[1:]))
    for pair in [("--tools", ""), ("--permission-prompts", "none"), ("--permission-mode", "dontAsk"),
                 ("--setting-sources", "project")]:
        assert pair in pairs
    assert {"--strict-mcp-config", "--no-session-persistence", "--disable-slash-commands"} <= set(flags)
    assert not {"--disallowedTools", "--bare", "--json-schema"} & set(flags)
    assert seen["prompt"] == flatten(messages, SCHEMA)


async def test_the_session_gets_no_api_credentials_or_parent_claude_code_state(tmp_path, monkeypatch):
    record = tmp_path / "record.json"
    monkeypatch.setenv("FAKE_CLAUDE_RECORD", str(record))
    stripped = ("CLAUDECODE", "CLAUDE_CODE_SESSION_ID", "ANTHROPIC_BASE_URL", "ANTHROPIC_API_KEY")
    for name in stripped:
        monkeypatch.setenv(name, "x")
    monkeypatch.setenv("ORACLE_KEEP_ME", "1")
    await chat("good").reply(PROBE, SCHEMA)
    env = set(json.loads(record.read_text(encoding="utf-8"))["env"])
    assert not env & set(stripped) and "ORACLE_KEEP_ME" in env


async def test_the_subscription_token_and_git_bash_path_pass_through(tmp_path, monkeypatch):
    record = tmp_path / "record.json"
    monkeypatch.setenv("FAKE_CLAUDE_RECORD", str(record))
    kept = ("CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_CODE_GIT_BASH_PATH")
    for name in kept:
        monkeypatch.setenv(name, "x")
    await chat("good").reply(PROBE, SCHEMA)
    assert set(kept) <= set(json.loads(record.read_text(encoding="utf-8"))["env"])


def test_a_relative_cli_path_is_made_absolute(tmp_path, monkeypatch):
    (tmp_path / "tools").mkdir()
    found = shim(tmp_path / "tools", "good")
    monkeypatch.chdir(tmp_path)
    relative = os.path.join("tools", found.name)
    assert claude_code.find_cli(relative) == str(found)


@pytest.mark.skipif(os.name != "nt", reason="the job object and taskkill are Windows only")
async def test_killing_a_live_cli_also_walks_its_tree_for_children_started_before_the_job(monkeypatch):
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    tree = claude_code._Tree(proc)
    started = []
    real = subprocess.Popen
    monkeypatch.setattr(claude_code.subprocess, "Popen", lambda argv, **kw: started.append(argv) or real(argv, **kw))
    try:
        tree.kill()
        assert started == [["taskkill", "/F", "/T", "/PID", str(proc.pid)]]
        proc.wait(10)
    finally:
        tree.close()
        proc.kill()


def test_the_flattened_prompt_keeps_every_turn_in_order_and_the_schema():
    messages = [{"role": "system", "content": "Rules A."}, {"role": "system", "content": "Rules B."},
                {"role": "user", "content": "First."}, {"role": "assistant", "content": "{}"},
                {"role": "user", "content": "Second."}]
    prompt = flatten(messages, SCHEMA)
    order = [prompt.index(s) for s in ("Rules A.", "Rules B.", "First.", "{}", "Second.", json.dumps(SCHEMA))]
    assert order == sorted(order)
    assert prompt.count('<message role="user">') == 2 and prompt.count('<message role="assistant">') == 1


async def test_content_that_is_not_plain_text_still_goes_in(tmp_path, monkeypatch):
    record = tmp_path / "record.json"
    monkeypatch.setenv("FAKE_CLAUDE_RECORD", str(record))
    messages = [{"role": "system", "content": {"rule": 1}}, {"role": "user", "content": "bad \ud800 surrogate"}]
    assert await chat("good").reply(messages, SCHEMA) == {"ok": True}
    prompt = json.loads(record.read_text(encoding="utf-8"))["prompt"]
    assert "{'rule': 1}" in prompt and "bad ? surrogate" in prompt


async def test_a_fenced_answer_is_unwrapped_and_a_plain_text_answer_comes_back_as_text():
    assert await chat("fenced").reply(PROBE, SCHEMA) == {"ok": True}
    assert await chat("text").reply(PROBE, SCHEMA) == "I would rather not."


async def test_a_line_printed_before_the_json_is_skipped():
    assert await chat("noisy").reply(PROBE, SCHEMA) == {"ok": True}


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


def test_the_cli_is_found_on_path_only_never_in_the_working_folder(tmp_path, monkeypatch):
    here, bin_dir = tmp_path / "here", tmp_path / "bin"
    here.mkdir()
    bin_dir.mkdir()
    shim(here, "good")
    monkeypatch.chdir(here)
    monkeypatch.delenv(BIN_ENV, raising=False)
    monkeypatch.setenv("PATH", f"{os.pathsep}.{os.pathsep}{bin_dir}")
    assert claude_code.find_cli("claude") is None
    found = shim(bin_dir, "good")
    if os.name == "nt":
        (bin_dir / "claude").write_text("#!/bin/sh\n", encoding="utf-8")  # npm's sh launcher beside the .cmd
    assert Path(claude_code.find_cli("claude")) == found


async def test_a_model_name_the_cli_could_misread_is_refused_before_running():
    with pytest.raises(ModelError, match="model name"):
        await chat("good", model="sonnet & calc").reply(PROBE, SCHEMA)


async def test_a_timeout_kills_the_whole_process_tree(tmp_path, monkeypatch):
    pid_file = _hanging(tmp_path, monkeypatch)
    start = time.monotonic()
    with pytest.raises(ModelError, match="longer than 5s"):
        await ClaudeCodeChat("sonnet", 5.0).reply(PROBE, SCHEMA)
    assert time.monotonic() - start < 20
    assert _gone(await _pid(pid_file, within=0))


async def test_a_tree_that_survives_the_kill_still_ends_the_reply_after_a_grace(tmp_path, monkeypatch):
    pid_file = _hanging(tmp_path, monkeypatch)
    monkeypatch.setattr(claude_code._Tree, "kill", lambda self: None)
    start = time.monotonic()
    with pytest.raises(ModelError, match="longer than 5s"):
        await ClaudeCodeChat("sonnet", 5.0).reply(PROBE, SCHEMA)
    assert time.monotonic() - start < 5 + claude_code.GRACE_S + 8
    pid = await _pid(pid_file, within=0)
    if os.name == "nt":
        assert _gone(pid), "closing the job ends whatever outlived the kill"
    else:
        os.kill(pid, 9)


async def test_cancelling_the_run_kills_the_whole_process_tree(tmp_path, monkeypatch):
    pid_file = _hanging(tmp_path, monkeypatch)
    task = asyncio.create_task(ClaudeCodeChat("sonnet", 60.0).reply(PROBE, SCHEMA))
    pid = await _pid(pid_file)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert _gone(pid)
