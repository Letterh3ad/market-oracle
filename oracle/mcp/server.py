"""A thin local MCP server (research-lab 04): newline-delimited JSON-RPC 2.0 over stdio, exposing the
literature index's lookup, meaning search, record and search log to a Claude Code session. A front end
like the CLI and the GUI: every tool is one service call."""

from __future__ import annotations

import json
import sys
import traceback
from collections.abc import Callable
from dataclasses import asdict, dataclass, is_dataclass
from typing import BinaryIO

from oracle.services import literature_service, meaning_service
from oracle.services.context import AppContext
from oracle.services.errors import ServiceError

NAME = "market-oracle-lab"
VERSION = "1"
PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")
BY = "claude"
"""Who a record or search log made through the server is by, unless the call names another (autopilot)."""
PARSE_ERROR, INVALID_REQUEST, NO_METHOD, INVALID_PARAMS, INTERNAL = -32700, -32600, -32601, -32602, -32603

_TEXT = {"type": "string"}
_LIST = {"type": "array", "items": {"type": "string"}}
_BY = {"type": "string", "description": "who does it: claude (default) or autopilot"}


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    properties: dict
    required: tuple[str, ...]
    call: Callable[..., object]

    def spec(self) -> dict:
        return {"name": self.name, "description": self.description,
                "inputSchema": {"type": "object", "properties": self.properties, "required": list(self.required),
                                "additionalProperties": False}}


TOOLS = (
    Tool("lookup", "Find a source in Market Oracle's literature index by exact id (DOI, SSRN, arXiv, NBER, URL) or a "
                   "paper or method by title, name or ref (fuzzy). Returns the 5 nearest methods with score and "
                   "funnel status.",
         {"query": _TEXT}, ("query",), lambda ctx, query: literature_service.lookup(ctx, query)),
    Tool("similar", "Find methods like an idea by meaning (local embeddings of each method's rule; shared words when "
                    "the local model is down, and the result says which). Returns the 5 nearest with score and "
                    "status. Check this before proposing a method: a near-duplicate of a FAIL is never re-tested "
                    "with tweaks.",
         {"text": {"type": "string", "description": "the idea in plain words"}}, ("text",),
         lambda ctx, text: meaning_service.similar(ctx, text)),
    Tool("record", "Record a source (new, or update one found by any of its ids or its exact title), link it to "
                   "methods it yields, and optionally add a new method; returns the 5 nearest methods.",
         {"id": {"type": "string", "description": "DOI, SSRN or arXiv id, NBER paper, or URL"}, "title": _TEXT,
          "authors": _TEXT, "year": {"type": "integer"},
          "quality": {"enum": list(literature_service.QUALITIES)}, "note": _TEXT,
          "read_on": {"type": "string", "description": "YYYY-MM-DD"},
          "methods": {**_LIST, "description": "refs of methods it yields"}, "new_method": _TEXT,
          "family": _TEXT, "definition": {"type": "string", "description": "the new method's mechanical rule"},
          "aliases": _LIST, "by": _BY}, (),
         lambda ctx, id=None, by=BY, **fields: literature_service.record(ctx, id, by=by, **fields)),
    Tool("search_log", "Log a literature search that was run: the query, where it ran, and the recorded sources it "
                       "found (by id or URL).",
         {"query": _TEXT, "where": _TEXT, "found": _LIST, "note": _TEXT, "by": _BY}, ("query", "where"),
         lambda ctx, query, where, found=(), note="", by=BY:
             literature_service.log_search(ctx, query, where, found, note, by)),
)
_BY_NAME = {t.name: t for t in TOOLS}


def _fits(schema: dict, value: object) -> bool:
    if "enum" in schema:
        return value in schema["enum"]
    if schema["type"] == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if schema["type"] == "array":
        return isinstance(value, list) and all(_fits(schema["items"], v) for v in value)
    return isinstance(value, str)


def _kind(schema: dict) -> str:
    if "enum" in schema:
        return f"one of {', '.join(schema['enum'])}"
    return {"integer": "a whole number", "array": "a list of strings"}.get(schema["type"], "a string")


def _plain(value: object) -> object:
    return asdict(value) if is_dataclass(value) else value


def _text(text: str, error: bool = False) -> dict:
    return {"content": [{"type": "text", "text": text}], **({"isError": True} if error else {})}


class Server:
    def __init__(self, ctx: AppContext) -> None:
        self.ctx = ctx

    def handle_line(self, line: bytes) -> dict | None:
        try:
            message = json.loads(line)
        except ValueError as exc:
            return _error(None, PARSE_ERROR, f"not JSON: {exc}")
        if not isinstance(message, dict):
            return _error(None, INVALID_REQUEST, "a request is a JSON object")
        try:
            return self.handle(message)
        except Exception as exc:
            # One bad call must not end the session; stdout is JSON-RPC only, so the trace goes to stderr.
            traceback.print_exc(file=sys.stderr)
            if "id" not in message:
                return None
            return _error(message["id"], INTERNAL, f"internal error: {type(exc).__name__}: {exc}")

    def handle(self, message: dict) -> dict | None:
        """The reply to one request; None for a notification (no id), which gets none."""
        rid, method, params = message.get("id"), message.get("method"), message.get("params")
        if "id" not in message:
            return None
        if not isinstance(method, str):
            return _error(rid, INVALID_REQUEST, "a request names its method")
        if params is None:
            params = {}
        if not isinstance(params, dict):
            return _error(rid, INVALID_PARAMS, "params is a JSON object")
        if method == "initialize":
            asked = params.get("protocolVersion")
            return _result(rid, {"protocolVersion": asked if asked in PROTOCOLS else PROTOCOLS[0],
                                 "capabilities": {"tools": {"listChanged": False}},
                                 "serverInfo": {"name": NAME, "version": VERSION}})
        if method == "ping":
            return _result(rid, {})
        if method == "tools/list":
            return _result(rid, {"tools": [t.spec() for t in TOOLS]})
        if method == "tools/call":
            name, arguments = params.get("name"), params.get("arguments")
            tool = _BY_NAME.get(name) if isinstance(name, str) else None
            if tool is None:
                return _error(rid, INVALID_PARAMS, f"no tool {name!r}; tools: {', '.join(_BY_NAME)}")
            if arguments is None:
                arguments = {}
            if not isinstance(arguments, dict):
                return _error(rid, INVALID_PARAMS, "arguments is a JSON object")
            return _result(rid, self._call(tool, arguments))
        return _error(rid, NO_METHOD, f"method {method!r} is not served")

    def _call(self, tool: Tool, arguments: dict) -> dict:
        unknown = set(arguments) - set(tool.properties)
        missing = [k for k in tool.required if k not in arguments]
        if unknown or missing:
            return _text(f"{tool.name} takes {', '.join(tool.properties)}"
                         + (f"; {', '.join(missing)} required" if missing else ""), error=True)
        if wrong := [k for k, v in arguments.items() if not _fits(tool.properties[k], v)]:
            return _text(f"{tool.name}: " + "; ".join(f"{k} is {_kind(tool.properties[k])}" for k in wrong), error=True)
        try:
            return _text(json.dumps(_plain(tool.call(self.ctx, **arguments)), ensure_ascii=False))
        except ServiceError as exc:
            return _text(str(exc), error=True)
        except (TypeError, ValueError) as exc:
            return _text(f"{tool.name}: bad arguments ({exc})", error=True)


def _result(rid: object, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": rid, "result": result}


def _error(rid: object, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}


def serve(ctx: AppContext, reader: BinaryIO, writer: BinaryIO) -> None:
    """Answer one request per line until the client closes stdin."""
    server = Server(ctx)
    for line in reader:
        if not line.strip():
            continue
        reply = server.handle_line(line)
        if reply is not None:
            writer.write(json.dumps(reply, ensure_ascii=False).encode("utf-8") + b"\n")
            writer.flush()
