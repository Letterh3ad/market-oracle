"""Claude through the user's own logged-in `claude -p` CLI (their subscription), one process
per reply. The whole conversation and the schema go in on stdin as one prompt; the reply is
the `result` field of `--output-format json`. The CLI has its own login: nothing about the
session or its credentials is read or stored here. Assumed flags, from the CLI reference:
`-p`, `--output-format json`, `--model`, `--strict-mcp-config`, `--disallowedTools`."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import shutil
import signal
import subprocess
import tempfile
from collections.abc import Sequence

from oracle.research.llm import ModelError

BIN_ENV = "ORACLE_CLAUDE_BIN"
"""The CLI's path, when `claude` is not on the PATH the app runs with."""

# The nested session only answers: it gets no tools to act with, and no MCP servers.
NO_TOOLS = ("Bash", "Edit", "MultiEdit", "Write", "Read", "Glob", "Grep", "LS", "WebFetch", "WebSearch",
            "NotebookEdit", "NotebookRead", "Task", "TodoWrite", "BashOutput", "KillShell", "SlashCommand")
# Either would make the CLI bill the API instead of the subscription the user picked this for.
API_ENV = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")
# On Windows the CLI is often claude.cmd, whose arguments cmd.exe reparses: only plain names pass.
MODEL_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:\[\]-]*")
LOGGED_OUT = re.compile(r"/login|not logged in|invalid api key|oauth token|authenticat", re.IGNORECASE)
FENCE = re.compile(r"^```(?:json)?\s*\n(.*)\n```$", re.DOTALL)


class ClaudeCodeChat:
    name = "claude_code"

    def __init__(self, model: str, timeout_s: float, command: Sequence[str] | None = None) -> None:
        """`command`: the argv that starts the CLI, before its flags; None finds `claude`."""
        self.model = model
        self._timeout = timeout_s
        self._command = tuple(command) if command else None
        self.fell_back_to: set[str] = set()
        """Always empty: the CLI reports no fallback."""

    async def reply(self, messages: list[dict], schema: dict) -> object:
        if not MODEL_NAME.fullmatch(self.model):
            raise ModelError(f"claude_code: {self.model!r} is not a model name the claude CLI takes, "
                             "like sonnet, opus or claude-opus-5")
        argv = [*self._argv0(), "-p", "--output-format", "json", "--model", self.model,
                "--strict-mcp-config", "--disallowedTools", *NO_TOOLS]
        code, out, err = await self._run(argv, flatten(messages, schema).encode("utf-8"))
        return self._parse(code, out, err)

    def _argv0(self) -> tuple[str, ...]:
        if self._command:
            return self._command
        name = os.environ.get(BIN_ENV) or "claude"
        found = shutil.which(name)
        if found is None:
            raise ModelError(f"the claude CLI was not found ({name}): install Claude Code and log in, "
                             f"or set {BIN_ENV} to its path")
        return (found,)

    async def _run(self, argv: list[str], stdin: bytes) -> tuple[int, str, str]:
        env = {k: v for k, v in os.environ.items() if k not in API_ENV}
        # An empty folder: no project instructions or files for the session to pick up.
        with tempfile.TemporaryDirectory(prefix="oracle-claude-", ignore_cleanup_errors=True) as cwd:
            try:
                proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                        cwd=cwd, env=env, **_detached())
            except OSError as exc:
                raise ModelError(f"the claude CLI was not found or would not start ({argv[0]}: {exc}): "
                                 f"install Claude Code, or set {BIN_ENV} to its path") from exc
            try:
                out, err = await asyncio.to_thread(proc.communicate, stdin, self._timeout)
            except subprocess.TimeoutExpired:
                _kill_tree(proc)
                await asyncio.to_thread(proc.communicate)
                raise ModelError(f"claude_code {self.model} took longer than {self._timeout:.0f}s to answer; "
                                 "raise its timeout_s in Settings > Models") from None
            except BaseException:
                # Cancelled: the thread still waits on the process, so end it and its children.
                _kill_tree(proc)
                raise
        return proc.returncode, out.decode("utf-8", "replace"), err.decode("utf-8", "replace")

    def _parse(self, code: int, out: str, err: str) -> object:
        try:
            envelope = json.loads(out)
        except ValueError:
            envelope = None
        result = envelope.get("result") if isinstance(envelope, dict) else None
        failed = code != 0 or (isinstance(envelope, dict) and envelope.get("is_error"))
        if failed:
            said = str(result or err or out).strip()
            if LOGGED_OUT.search(said):
                raise ModelError("the claude CLI is not logged in: run `claude` once and log in with /login, "
                                 "then test again")
            raise ModelError(f"the claude CLI exited with {code}: {said[:300]}")
        if not isinstance(result, str):
            raise ModelError(f"the claude CLI answered with something that is not the JSON of "
                             f"--output-format json: {out.strip()[:200]!r}")
        text = result.strip()
        fenced = FENCE.match(text)
        try:
            return json.loads(fenced.group(1) if fenced else text)
        except ValueError:
            return text


def flatten(messages: list[dict], schema: dict) -> str:
    """The conversation as one prompt: system instructions, then every other turn in order."""
    system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
    turns = "\n".join(f'<message role="{m["role"]}">\n{m["content"]}\n</message>'
                      for m in messages if m["role"] != "system")
    return (f"<instructions>\n{system}\n</instructions>\n\n"
            f"The conversation so far, oldest first:\n{turns}\n\n"
            "Reply to the last message as the assistant. Answer with one JSON object and nothing else "
            "(no prose, no code fence), matching this JSON Schema:\n"
            f"{json.dumps(schema)}\n")


def _detached() -> dict:
    if os.name == "nt":
        return {"creationflags": subprocess.CREATE_NO_WINDOW}
    return {"start_new_session": True}


def _kill_tree(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    if os.name == "nt":
        # claude.cmd runs node under cmd.exe: killing cmd.exe alone leaves node holding the pipes.
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True,
                       creationflags=subprocess.CREATE_NO_WINDOW)
    else:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(proc.pid, signal.SIGKILL)
