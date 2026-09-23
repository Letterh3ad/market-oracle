"""A web searcher answering scripted results, or failing as told."""

from __future__ import annotations

from oracle.research.web import WebResult, WebSearchFailed


class FakeSearcher:
    def __init__(self) -> None:
        self.results: dict[str, list[WebResult]] = {}
        self.fail: WebSearchFailed | None = None
        self.queries: list[str] = []

    async def search(self, query: str, max_results: int) -> list[WebResult]:
        self.queries.append(query)
        if self.fail:
            raise self.fail
        return self.results.get(query, [])[:max_results]


class FakePages:
    """Any host listed in `pages`' URLs answers with the page's body and content type."""

    def __init__(self) -> None:
        self.pages: dict[str, tuple[str, str]] = {}
        """url -> (content type, body)"""

    @property
    def hosts(self) -> set[str]:
        from urllib.parse import urlsplit

        return {urlsplit(u).hostname for u in self.pages}

    def handle(self, request):
        import httpx

        found = self.pages.get(str(request.url))
        if found is None:
            return httpx.Response(404)
        kind, body = found
        return httpx.Response(200, text=body, headers={"Content-Type": kind})
