"""One transport for every fake service, routed by host, as AppContext takes one."""

from __future__ import annotations

import httpx


class FakeWeb:
    def __init__(self, *services) -> None:
        self.services = list(services)
        self.requests: list[httpx.Request] = []
        self.transport = httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        for service in self.services:
            if request.url.host in service.hosts:
                return service.handle(request)
        return httpx.Response(404)

    def requests_to(self, host: str) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.host == host]
