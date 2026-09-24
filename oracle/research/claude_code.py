"""Claude through the user's own logged-in `claude -p` CLI (their subscription), one process
per reply. The whole conversation and the schema go in on stdin as one prompt; the reply is
the `result` field of `--output-format json`. The CLI has its own login: nothing about the
session or its credentials is read or stored here. Flags as listed by `claude --help` of
CLI 2.1.281; `--bare` is left out on purpose, since it forces API-key auth."""

from __future__ import annotations

import asyncio
import contextlib
import functools
import json
import os
import re
import signal
import subprocess
import tempfile
import threading
from collections.abc import Sequence
from pathlib import Path

from oracle.config import CLAUDE_CODE_MODEL
from oracle.research.llm import ModelError

BIN_ENV = "ORACLE_CLAUDE_BIN"
"""The CLI's path, when `claude` is not on the PATH the app runs with."""

# The nested session only answers: no tools, nothing that could prompt, no MCP servers, no
# skills, and no settings (so no user hooks, plugins or permission rules: the only source
# named is the project's, and it runs in an empty folder). No transcript is kept either.
FLAGS = ("--tools", "", "--permission-prompts", "none", "--permission-mode", "dontAsk", "--strict-mcp-config",
         "--setting-sources", "project", "--no-session-persistence", "--disable-slash-commands")
GRACE_S = 5.0
"""How long a killed CLI gets to let go of its pipes."""
LOGGED_OUT = re.compile(r"/login|not logged in|invalid api key|oauth token|authenticat", re.IGNORECASE)
FENCE = re.compile(r"^```(?:json)?\s*\n(.*)\n```$", re.DOTALL)


def _inherited(name: str) -> bool:
    # API credentials would make the CLI bill the API, not the subscription; a parent Claude
    # Code session's variables would make it act as that session's child.
    upper = name.upper()
    return not (upper == "CLAUDECODE" or upper.startswith(("CLAUDE_CODE_", "ANTHROPIC_")))


def find_cli(name: str) -> str | None:
    """`name` as a path, else looked up in PATH's absolute folders only: never the working
    folder, as Windows' own lookup would. On Windows only PATHEXT kinds, so npm's sh launcher
    beside claude.cmd is passed over."""
    if os.path.dirname(name):
        return name if Path(name).is_file() else None
    exts = [""]
    if os.name == "nt":
        exts = [e for e in (os.environ.get("PATHEXT") or ".COM;.EXE;.BAT;.CMD").lower().split(os.pathsep) if e]
        if Path(name).suffix.lower() in exts:
            exts = [""]
    for folder in os.environ.get("PATH", "").split(os.pathsep):
        if not os.path.isabs(folder):
            continue
        for ext in exts:
            candidate = Path(folder) / (name + ext)
            if candidate.is_file() and (os.name == "nt" or os.access(candidate, os.X_OK)):
                return str(candidate)
    return None


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
        if not CLAUDE_CODE_MODEL.fullmatch(self.model):
            raise ModelError(f"claude_code: {self.model!r} is not a model name the claude CLI takes, "
                             "like sonnet, opus or claude-opus-5")
        # The schema stays in the prompt rather than --json-schema: through claude.cmd, cmd.exe
        # reparses every argument (a schema's quotes, <, > and & included) under an 8191
        # character line limit.
        argv = [*self._argv0(), "-p", "--output-format", "json", "--model", self.model, *FLAGS]
        code, out, err = await self._run(argv, flatten(messages, schema).encode("utf-8", "replace"))
        return self._parse(code, out, err)

    def _argv0(self) -> tuple[str, ...]:
        if self._command:
            return self._command
        name = os.environ.get(BIN_ENV) or "claude"
        found = find_cli(name)
        if found is None:
            raise ModelError(f"the claude CLI was not found ({name}): install Claude Code and log in, "
                             f"or set {BIN_ENV} to its path")
        return (found,)

    async def _run(self, argv: list[str], stdin: bytes) -> tuple[int, str, str]:
        env = {k: v for k, v in os.environ.items() if _inherited(k)}
        # An empty folder: no project instructions, settings or files for the session to pick up.
        with tempfile.TemporaryDirectory(prefix="oracle-claude-", ignore_cleanup_errors=True) as cwd:
            try:
                proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                        cwd=cwd, env=env, **_detached())
            except OSError as exc:
                raise ModelError(f"the claude CLI was not found or would not start ({argv[0]}: {exc}): "
                                 f"install Claude Code, or set {BIN_ENV} to its path") from exc
            tree = _Tree(proc)
            try:
                out, err = await asyncio.to_thread(proc.communicate, stdin, self._timeout)
            except subprocess.TimeoutExpired:
                tree.kill()
                await _reap(proc)
                raise ModelError(f"claude_code {self.model} took longer than {self._timeout:.0f}s to answer; "
                                 "raise its timeout_s in Settings > Models") from None
            except BaseException:
                # Cancelled: the thread still waits on the process. Kill without blocking the
                # loop, then give it a moment so the folder can go.
                tree.kill()
                with contextlib.suppress(BaseException):
                    await asyncio.shield(asyncio.to_thread(_wait, proc))
                raise
            finally:
                tree.close()
        return proc.returncode, out.decode("utf-8", "replace"), err.decode("utf-8", "replace")

    def _parse(self, code: int, out: str, err: str) -> object:
        envelope = _envelope(out)
        result = envelope.get("result") if envelope else None
        if code != 0 or (envelope and envelope.get("is_error")):
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


def _envelope(out: str) -> dict | None:
    """The CLI's JSON, the whole output or else its last line (a notice may come first)."""
    lines = [line for line in out.splitlines() if line.strip()]
    for text in (out, lines[-1] if lines else ""):
        try:
            parsed = json.loads(text)
        except ValueError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def flatten(messages: list[dict], schema: dict) -> str:
    """The conversation as one prompt: system instructions, then every other turn in order."""
    system = "\n\n".join(str(m["content"]) for m in messages if m["role"] == "system")
    turns = "\n".join(f'<message role="{m["role"]}">\n{m["content"]}\n</message>'
                      for m in messages if m["role"] != "system")
    return (f"<instructions>\n{system}\n</instructions>\n\n"
            f"The conversation so far, oldest first:\n{turns}\n\n"
            "Reply to the last message as the assistant. Answer with one JSON object and nothing else "
            "(no prose, no code fence), matching this JSON Schema:\n"
            f"{json.dumps(schema)}\n")


async def _reap(proc: subprocess.Popen) -> None:
    try:
        await asyncio.to_thread(proc.communicate, None, GRACE_S)
    except subprocess.TimeoutExpired:
        # Something outlived the kill and holds the pipes; closing may wait on the reader
        # threads' locks, so it happens off this path.
        threading.Thread(target=_close_pipes, args=(proc,), daemon=True).start()


def _close_pipes(proc: subprocess.Popen) -> None:
    for pipe in (proc.stdin, proc.stdout, proc.stderr):
        with contextlib.suppress(OSError, ValueError):
            if pipe:
                pipe.close()


def _wait(proc: subprocess.Popen) -> None:
    with contextlib.suppress(subprocess.TimeoutExpired):
        proc.wait(GRACE_S)


def _detached() -> dict:
    if os.name == "nt":
        return {"creationflags": subprocess.CREATE_NO_WINDOW}
    return {"start_new_session": True}


class _Tree:
    """The CLI and everything it starts. On Windows a job object that kills them all when
    closed (claude.cmd runs node under cmd.exe, and killing cmd.exe alone leaves node holding
    the pipes); elsewhere the process group the CLI leads. Every call returns at once."""

    def __init__(self, proc: subprocess.Popen) -> None:
        self._proc = proc
        self._job = _job_for(proc.pid) if os.name == "nt" else None

    def kill(self) -> None:
        if os.name != "nt":
            with contextlib.suppress(OSError):
                os.killpg(self._proc.pid, signal.SIGKILL)
        elif not (self._job and _kernel32().TerminateJobObject(self._job, 1)) and self._proc.poll() is None:
            subprocess.Popen(["taskkill", "/F", "/T", "/PID", str(self._proc.pid)], stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)

    def close(self) -> None:
        if self._job:
            _kernel32().CloseHandle(self._job)
            self._job = None


@functools.cache
def _kernel32():
    import ctypes
    from ctypes import wintypes

    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.CreateJobObjectW.restype = wintypes.HANDLE
    k.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
    k.OpenProcess.restype = wintypes.HANDLE
    k.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    for name, args in (("SetInformationJobObject", (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD)),
                       ("AssignProcessToJobObject", (wintypes.HANDLE, wintypes.HANDLE)),
                       ("TerminateJobObject", (wintypes.HANDLE, wintypes.UINT)),
                       ("CloseHandle", (wintypes.HANDLE,))):
        getattr(k, name).restype = wintypes.BOOL
        getattr(k, name).argtypes = args
    return k


def _job_for(pid: int) -> int | None:
    """A kill-on-close job holding `pid`, or None (then kill falls back to taskkill /T). A
    child the CLI starts before this runs escapes it; cmd.exe has barely begun by then."""
    import ctypes
    from ctypes import wintypes

    class Basic(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD)]

    class Extended(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", Basic), ("IoInfo", ctypes.c_uint64 * 6),
                    ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

    kill_on_close, extended_info, set_quota_and_terminate = 0x2000, 9, 0x0100 | 0x0001
    k = _kernel32()
    job = k.CreateJobObjectW(None, None)
    if not job:
        return None
    info = Extended()
    info.BasicLimitInformation.LimitFlags = kill_on_close
    process = k.OpenProcess(set_quota_and_terminate, False, pid)
    ok = bool(process) and k.SetInformationJobObject(job, extended_info, ctypes.byref(info), ctypes.sizeof(info)) \
        and k.AssignProcessToJobObject(job, process)
    if process:
        k.CloseHandle(process)
    if not ok:
        k.CloseHandle(job)
        return None
    return job
