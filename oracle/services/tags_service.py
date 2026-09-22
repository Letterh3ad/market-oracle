from __future__ import annotations

from dataclasses import dataclass

from oracle.services.context import AppContext
from oracle.services.errors import InvalidTag
from oracle.services.universe_service import resolve_symbol
from oracle.store.db import to_utc_iso
from oracle.store.tags import TagRepo, TagSummary
from oracle.universe.tags import normalise_tag

__all__ = ["TagSummary", "Tagged", "add_tag", "list_tags", "remove_tag", "tags_of"]


@dataclass(frozen=True)
class Tagged:
    tag: str
    """As stored: lower case, whitespace collapsed."""
    symbol: str
    name: str


def add_tag(ctx: AppContext, symbol: str, tag: str) -> Tagged:
    """Tag the company `symbol` belongs to."""
    tag = _normalise(tag)
    listing = resolve_symbol(ctx, symbol)
    repo = TagRepo(ctx.conn)
    if tag not in repo.of_company(listing.company_key):
        repo.add(listing.symbol, tag, to_utc_iso(ctx.clock.utcnow()))
    return Tagged(tag, listing.symbol, listing.name)


def remove_tag(ctx: AppContext, symbol: str, tag: str) -> bool:
    """False when the company did not carry the tag. Works on orphaned symbols too."""
    return TagRepo(ctx.conn).remove(symbol, _normalise(tag))


def tags_of(ctx: AppContext, symbol: str) -> list[str]:
    return TagRepo(ctx.conn).of_company(resolve_symbol(ctx, symbol).company_key)


def list_tags(ctx: AppContext) -> list[TagSummary]:
    return TagRepo(ctx.conn).summaries()


def _normalise(tag: str) -> str:
    try:
        return normalise_tag(tag)
    except ValueError as exc:
        raise InvalidTag(str(exc)) from None
