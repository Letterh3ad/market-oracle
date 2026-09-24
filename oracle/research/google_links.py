"""Google News redirect stubs (news.google.com/rss/articles/<id>) decoded to the article's
address. An older id is a base64 protobuf carrying the address itself; a newer one ("AU_yqL"
inside) is opaque, and only Google's own decoder, fed a signature from the stub's page, gives
the address back."""

from __future__ import annotations

import base64
import binascii
import json
import re
from urllib.parse import urlsplit

STUB_PAGE = "https://news.google.com/rss/articles/{}"
DECODER = "https://news.google.com/_/DotsSplashUi/data/batchexecute"
_ID = re.compile(r"[A-Za-z0-9_-]{8,2000}")
# Field 1 = 19, then field 4 (length-delimited): the address follows its length.
_OLD_PREFIX = b"\x08\x13\x22"
_TS = re.compile(r'data-n-a-ts="(\d+)"')
_SG = re.compile(r'data-n-a-sg="([A-Za-z0-9_-]+)"')


def stub_id(url: str) -> str | None:
    """The id of a Google News redirect stub, or None for any other address."""
    parts = urlsplit(url)
    if parts.hostname != "news.google.com":
        return None
    head, _, tail = parts.path.rpartition("/")
    return tail if head in ("/rss/articles", "/articles") and _ID.fullmatch(tail) else None


def decode_offline(stub: str) -> str | None:
    """The address an older id carries, or None when the id needs Google's decoder."""
    try:
        raw = base64.urlsafe_b64decode(stub + "=" * (-len(stub) % 4))
    except (binascii.Error, ValueError):
        return None
    if not raw.startswith(_OLD_PREFIX):
        return None
    length, at = _varint(raw, len(_OLD_PREFIX))
    body = raw[at:at + length] if length is not None else b""
    if len(body) != length:
        return None
    try:
        url = body.decode("ascii")
    except UnicodeDecodeError:
        return None
    return url if url.startswith(("http://", "https://")) else None


def _varint(raw: bytes, at: int) -> tuple[int | None, int]:
    value = shift = 0
    while at < len(raw) and shift < 35:
        byte = raw[at]
        at += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, at
        shift += 7
    return None, at


def signature(page: str) -> tuple[int, str] | None:
    """The timestamp and signature Google's decoder wants, from a stub's page."""
    ts, sg = _TS.search(page), _SG.search(page)
    return (int(ts.group(1)), sg.group(1)) if ts and sg else None


def decoder_form(stub: str, ts: int, sg: str) -> dict[str, str]:
    """The form body for Google's decoder (its "garturlreq" call, as the News web app sends it)."""
    request = ["garturlreq", [["X", "X", ["X", "X"], None, None, 1, 1, "US:en", None, 1, None, None, None, None,
                               None, 0, 1], "X", "X", 1, [1, 1, 1], 1, 1, None, 0, 0, None, 0], stub, ts, sg]
    return {"f.req": json.dumps([[["Fbv4je", json.dumps(request), None, "generic"]]])}


def _envelopes(answer: str) -> list:
    # Google prefixes JSON answers with a line that stops them running as a script.
    _, _, rest = answer.partition("\n")
    try:
        body = json.loads(rest)
    except ValueError:
        return []
    return [e for e in body if isinstance(e, list) and e] if isinstance(body, list) else []


def refused(answer: str) -> bool:
    """Whether the decoder answered with an error envelope (a stale signature, throttling)."""
    return any(e[0] == "er" for e in _envelopes(answer))


def decoded_url(answer: str) -> str | None:
    """The article's address from the decoder's answer, or None if it gave none."""
    try:
        [inner] = [e[2] for e in _envelopes(answer) if e[:2] == ["wrb.fr", "Fbv4je"]]
        body = json.loads(inner)
    except (ValueError, TypeError, IndexError):
        return None
    ok = isinstance(body, list) and len(body) > 1 and body[0] == "garturlres" and isinstance(body[1], str)
    return body[1] if ok and body[1].startswith(("http://", "https://")) else None
