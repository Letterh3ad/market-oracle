"""Unpaywall and Semantic Scholar answering from recorded responses, and hosts serving PDFs."""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import unquote

import httpx

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "open_access"


def recorded(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class FakeUnpaywall:
    hosts = ("api.unpaywall.org",)

    def __init__(self, records: dict[str, dict]) -> None:
        self.records = {k.lower(): v for k, v in records.items()}

    def handle(self, request: httpx.Request) -> httpx.Response:
        doi = unquote(request.url.path.removeprefix("/v2/")).lower()
        if not request.url.params.get("email"):
            return httpx.Response(422, json={"error": True, "message": "email required"})
        found = self.records.get(doi)
        return httpx.Response(404, text="<html>Not Found</html>") if found is None else httpx.Response(200, json=found)


class FakeSemanticScholar:
    hosts = ("api.semanticscholar.org",)

    def __init__(self, records: dict[str, dict]) -> None:
        self.records = {k.lower(): v for k, v in records.items()}

    def handle(self, request: httpx.Request) -> httpx.Response:
        ident = unquote(request.url.path.removeprefix("/graph/v1/paper/")).lower()
        found = self.records.get(ident)
        if found is None:
            return httpx.Response(404, json={"error": f"Paper with id {ident} not found"})
        return httpx.Response(200, json=found)


class FakeFiles:
    """Any URL to bytes, by full URL; hosts are taken from the URLs given."""

    def __init__(self, files: dict[str, bytes | int]) -> None:
        self.files = files
        self.hosts = tuple({httpx.URL(u).host for u in files})

    def handle(self, request: httpx.Request) -> httpx.Response:
        body = self.files.get(str(request.url))
        if body is None:
            return httpx.Response(404)
        if isinstance(body, int):
            return httpx.Response(body, text="<html>Sign in</html>")
        return httpx.Response(200, content=body, headers={"content-type": "application/pdf"})
