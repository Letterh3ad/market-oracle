"""How the literature index tells sources apart: one normalised key per id (DOI, SSRN, arXiv, NBER,
or a cleaned URL), and a fuzzy title score that survives typos, reordering and punctuation."""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher
from functools import lru_cache
from urllib.parse import parse_qsl, urlencode, urlsplit

_DOI = re.compile(r"\b(10\.\d{4,9}/[^\s?#]+)", re.I)
_SSRN_DOI = re.compile(r"^10\.2139/ssrn\.(\d+)$", re.I)
_ARXIV_DOI = re.compile(r"^10\.48550/arxiv\.(\d{4}\.\d{4,5})", re.I)
_ARXIV = re.compile(r"(?:arxiv\.org/(?:abs|pdf)/|\barxiv:?\s*)(\d{4}\.\d{4,5})", re.I)
_SSRN = re.compile(r"(?:ssrn\.com/.*?abstract(?:_id)?=|\bssrn:?\s*)(\d{4,})", re.I)
_NBER = re.compile(r"nber\.org/(?:papers|system/files/working_papers)/w?(\d{3,})", re.I)
_URL = re.compile(r"^(?:https?://|www\.)\S+$", re.I)
TRACKING = re.compile(r"^(utm_\w+|fbclid|gclid|dclid|msclkid|mc_cid|mc_eid|ref|ref_src|_hsenc|_hsmi|igshid|"
                      r"download|via|source)$", re.I)
STOP = frozenset("a an the of and in on for to with from by at is are do does vs via".split())


def source_key(text: str) -> str | None:
    """The one key every spelling of a source shares; None when `text` is not an id or a URL."""
    t = text.strip()
    if not t:
        return None
    for pattern, kind in ((_ARXIV, "arxiv"), (_SSRN, "ssrn"), (_NBER, "nber")):
        m = pattern.search(t)
        if m:
            return f"{kind}:{'w' if kind == 'nber' else ''}{m[1]}"
    m = _DOI.search(t)
    if m and (t.lower().startswith(("doi", "10.")) or "/doi" in t.lower() or "doi.org/" in t.lower()):
        doi = m[1].rstrip(".,;").lower()
        if (s := _SSRN_DOI.match(doi)) is not None:
            return f"ssrn:{s[1]}"
        if (a := _ARXIV_DOI.match(doi)) is not None:
            return f"arxiv:{a[1]}"
        return f"doi:{doi}"
    if _URL.match(t):
        return "url:" + _clean_url(t)
    return None


LINKS = {"doi": "https://doi.org/{}", "ssrn": "https://papers.ssrn.com/abstract={}",
         "arxiv": "https://arxiv.org/abs/{}", "nber": "https://www.nber.org/papers/{}", "url": "https://{}"}


def link_for(key: str) -> str:
    """A link that opens the source a key names."""
    kind, _, rest = key.partition(":")
    return LINKS[kind].format(rest)


def _clean_url(url: str) -> str:
    parts = urlsplit(url if "://" in url else f"https://{url}")
    host = parts.netloc.lower().removeprefix("www.")
    path = parts.path.rstrip("/")
    query = sorted((k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not TRACKING.match(k))
    return host + path + (f"?{urlencode(query)}" if query else "")


def normal_title(text: str) -> str:
    plain = "".join(c if c.isascii() else " " for c in unicodedata.normalize("NFKD", text)
                    if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^a-z0-9]+", " ", plain.lower()).split())


@lru_cache(maxsize=4096)
def _tokens(text: str) -> tuple[str, ...]:
    words = normal_title(text).split()
    return tuple(w for w in words if w not in STOP) or tuple(words)


@lru_cache(maxsize=65536)
def _ratio(a: str, b: str) -> float:
    m = SequenceMatcher(None, a, b)
    return m.ratio() if m.quick_ratio() > 0.5 else 0.0


def _best(word: str, others: tuple[str, ...]) -> float:
    if word in others:
        return 1.0
    best = 0.0
    for o in others:
        if 2 * min(len(word), len(o)) / (len(word) + len(o)) > best:  # the most a ratio can reach
            best = max(best, _ratio(word, o))
    return best


def title_score(query: str, title: str) -> float:
    """0 to 1: each word's closest counterpart, recall-weighted so a short query can match a longer
    title it is part of; or the whole strings' likeness, whichever is higher."""
    q, t = _tokens(query), _tokens(title)
    if not q or not t:
        return 0.0
    recall = sum(_best(w, t) for w in q) / len(q)
    precision = sum(_best(w, q) for w in t) / len(t)
    soft = 5 * recall * precision / (4 * precision + recall) if recall + precision else 0.0
    whole = SequenceMatcher(None, " ".join(q), " ".join(t)).ratio()
    return round(max(soft, whole), 3)
