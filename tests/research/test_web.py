"""Where a page's host resolves decides whether the reader may open it."""

import socket

import pytest

from oracle.research import web
from oracle.research.web import system_addresses, unsafe_address

PRIVATE = "private and local network addresses cannot be read"


@pytest.fixture
def dns(monkeypatch):
    names = {"localtest.me": ["::1"], "mixed.test": ["93.184.216.34", "10.0.0.5"],
             "public.test": ["93.184.216.34"], "mapped.test": ["::ffff:127.0.0.1"], "nowhere.test": []}
    asked = []

    def resolve(host):
        asked.append(host)
        return names[host]

    monkeypatch.setattr(web, "resolve", resolve)
    return asked


@pytest.mark.parametrize("url, why", [
    ("http://localtest.me/x", PRIVATE), ("https://mixed.test/x", PRIVATE), ("http://mapped.test/", PRIVATE),
    ("https://public.test/x", None), ("https://nowhere.test/x", None),
])
async def test_a_host_resolving_into_this_machine_or_the_lan_cannot_be_read(dns, url, why):
    assert await unsafe_address(url) == why


async def test_an_address_literal_is_not_looked_up(dns):
    assert await unsafe_address("http://93.184.216.34/x") is None and dns == []


def test_the_system_resolver_lists_every_address_and_none_for_a_name_it_cannot_find(monkeypatch):
    def getaddrinfo(host, port, *args, **kwargs):
        if host == "gone.test":
            raise socket.gaierror("not found")
        if host == "xn--":
            raise UnicodeError("bad label")
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0)),
                (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("::1", 0, 0, 0))]

    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
    assert system_addresses("two.test") == ["93.184.216.34", "::1"]
    assert system_addresses("gone.test") == [] and system_addresses("xn--") == []
