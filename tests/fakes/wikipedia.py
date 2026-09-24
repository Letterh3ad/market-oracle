"""A fake Wikipedia page API serving captured or hand-written pages by title."""

from __future__ import annotations

from urllib.parse import unquote

import httpx

PREFIX = "/api/rest_v1/page/html/"


class FakeWikipedia:
    hosts = {"en.wikipedia.org", "zh.wikipedia.org"}

    def __init__(self) -> None:
        self.pages: dict[str, str | int] = {}
        """Title as the page's URL spells it -> HTML, or an HTTP status to answer with."""
        self.requests: list[httpx.Request] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        title = request.url.raw_path.decode().split("?")[0].removeprefix(PREFIX)
        page = self.pages.get(title, self.pages.get(unquote(title), 404))
        return httpx.Response(page) if isinstance(page, int) else httpx.Response(200, text=page)


def members_table(*rows: tuple[str, str], symbol: str = "Symbol", name: str = "Company") -> str:
    cells = "".join(f"<tr><td>{s}</td><td>{n}</td></tr>" for s, n in rows)
    return f"<table class='wikitable'><tr><th>{symbol}</th><th>{name}</th></tr>{cells}</table>"
