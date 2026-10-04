"""Meaning search over the literature index (research-lab 04, spec 10): "find a method like this". Each
method's name and mechanical definition is embedded once by a small local Ollama model and stored in the
research database; a query is embedded and the 5 nearest methods by cosine come back with score and
status. When Ollama cannot answer, shared content words score instead, and the result says which was
used. A registration whose rule is near a FAILed method is flagged (`near_fail`), and research-lab 01's
registry asks it for a "different because"."""

from __future__ import annotations

import hashlib
import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from oracle.lab.matching import keyword_score
from oracle.research.llm import ModelError
from oracle.research.ollama import OllamaEmbed
from oracle.services.context import AppContext
from oracle.services.errors import InvalidQuery
from oracle.services.literature_service import NEAREST, open_index
from oracle.store.db import to_utc_iso
from oracle.store.embeddings import EmbeddingRepo
from oracle.store.literature import LiteratureRepo, MethodRow

__all__ = ["EMBED_MODEL", "NEAR", "Embedder", "NearCheck", "NearFail", "Similar", "SimilarHit", "embedder", "near_fail", "similar"]

EMBED_MODEL = "nomic-embed-text"
EMBED_TIMEOUT_S = 120.0
"""A cold load plus the whole index in batches; a query alone takes well under a second."""
NEAR = {"meaning": 0.75, "keyword": 0.6}
"""The score at which a FAILed method counts as a near-duplicate, by how it was scored."""


class Embedder(Protocol):
    model: str

    def embed(self, texts: list[str], query: bool = False) -> list[list[float]]: ...


def embedder(ctx: AppContext) -> Embedder:
    """Ollama at the settings' address (`[llm] base_url`), the local model the research engine uses."""
    return OllamaEmbed(EMBED_MODEL, ctx.config.llm.base_url, EMBED_TIMEOUT_S)


@dataclass(frozen=True)
class SimilarHit:
    ref: str
    name: str
    family: str
    status: str
    rating: int | None
    score: float


@dataclass(frozen=True)
class Similar:
    query: str
    mode: str
    """meaning (embeddings) or keyword (the fallback)."""
    note: str
    hits: list[SimilarHit]


@dataclass(frozen=True)
class NearFail:
    ref: str
    name: str
    score: float
    mode: str

    @property
    def label(self) -> str:
        return f"{self.ref} {self.name} ({self.mode} {self.score:.2f})"


@dataclass(frozen=True)
class NearCheck:
    mode: str
    """meaning or keyword: a keyword check is uncalibrated on rule text and misses most paraphrases."""
    note: str
    hit: NearFail | None


def method_text(m: MethodRow) -> str:
    """What a method means: its name, its rule and any other names."""
    also = f" Also called: {', '.join(m.aliases)}." if m.aliases else ""
    return f"{m.name}. {m.definition}{also}".strip()


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(x * x for x in b))
    if not na or not nb or len(a) != len(b):
        return 0.0
    return sum(x * y for x, y in zip(a, b)) / (na * nb)


def _vectors(ctx: AppContext, conn, rows: list[MethodRow], embed: Embedder) -> dict[int, list[float]]:
    """Every method's vector for this model; a new or edited method is embedded and stored first."""
    repo = EmbeddingRepo(conn)
    stored = repo.all(embed.model)
    texts = {m.id: method_text(m) for m in rows}
    stale = [m.id for m in rows if stored.get(m.id, ("",))[0] != _hash(texts[m.id])]
    if stale:
        fresh = embed.embed([texts[i] for i in stale])
        repo.put(embed.model, to_utc_iso(ctx.clock.utcnow()),
                 [(i, _hash(texts[i]), v) for i, v in zip(stale, fresh)])
        stored.update({i: (_hash(texts[i]), v) for i, v in zip(stale, fresh)})
    return {m.id: stored[m.id][1] for m in rows}


def _scored(ctx: AppContext, text: str, embed: Embedder | None,
            as_query: bool = True) -> tuple[str, str, list[tuple[MethodRow, float]]]:
    text = text.strip()
    if not text:
        raise InvalidQuery("describe the method to find, e.g. buy stocks near their yearly high")
    embed = embed or embedder(ctx)
    with open_index(ctx) as r:
        rows = LiteratureRepo(r.conn).methods()
        try:
            vectors = _vectors(ctx, r.conn, rows, embed)
            [q] = embed.embed([text], query=as_query)
        except ModelError as exc:
            return ("keyword", f"keyword match: meaning search is unavailable ({exc})",
                    [(m, keyword_score(text, method_text(m))) for m in rows])
    return ("meaning", f"meaning match ({embed.model} embeddings, cosine)",
            [(m, _cosine(q, vectors[m.id])) for m in rows])


def similar(ctx: AppContext, text: str, limit: int = NEAREST, embed: Embedder | None = None) -> Similar:
    """The `limit` methods nearest in meaning to `text`, or by shared words when Ollama is down."""
    mode, note, scored = _scored(ctx, text, embed)
    scored.sort(key=lambda p: -p[1])
    return Similar(text.strip(), mode, note, [SimilarHit(m.ref, m.name, m.family, m.status, m.rating, round(s, 3))
                                              for m, s in scored[:limit]])


def near_fail(ctx: AppContext, text: str, embed: Embedder | None = None) -> NearCheck:
    """How the near-FAIL check ran, and the FAILed method nearest to `text` when it scores at the
    near-duplicate threshold or above. The rule is embedded as a document like the methods: a query's
    cosines run lower (0.55 to 0.78 live)."""
    mode, note, scored = _scored(ctx, text, embed, as_query=False)
    fails = sorted(((m, s) for m, s in scored if m.status == "fail"), key=lambda p: -p[1])
    if not fails or fails[0][1] < NEAR[mode]:
        return NearCheck(mode, note, None)
    m, s = fails[0]
    return NearCheck(mode, note, NearFail(m.ref, m.name, round(s, 3), mode))
