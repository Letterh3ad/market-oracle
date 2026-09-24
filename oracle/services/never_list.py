"""Companies never watched or traded: the user's rule (Tesla) always, and `[discovery] never`
wherever discovery reaches."""

from __future__ import annotations

from oracle.services.context import AppContext
from oracle.services.errors import NeverListed
from oracle.store.universe import UniverseRepo
from oracle.universe.listings import normalise_name

__all__ = ["ALWAYS_NEVER", "is_never", "never_company", "never_names", "refuse_never"]

# The user's rule, kept whatever `[discovery] never` says.
ALWAYS_NEVER = ("Tesla",)


def never_names(ctx: AppContext, user: bool) -> tuple[str, ...]:
    """Normalised; with `user`, the `[discovery] never` names too."""
    names = (*ALWAYS_NEVER, *(ctx.config.discovery.never if user else ()))
    return tuple(n for n in (normalise_name(x) for x in names) if n)


def is_never(name: str, never: tuple[str, ...]) -> bool:
    """A never name as whole words of `name`."""
    text = f" {normalise_name(name)} "
    return any(f" {n} " in text for n in never)


def never_company(ctx: AppContext, symbol: str, user: bool) -> str | None:
    """The company's name when any of its listings is named on the never list; a never name
    can hide on a line other than the one asked about."""
    universe = UniverseRepo(ctx.conn)
    found = universe.resolve(symbol)
    if found is None:
        return None
    never = never_names(ctx, user)
    names = [found.name, *(x.name for x in universe.company_listings(found.company_key))]
    return found.name if any(is_never(n, never) for n in names) else None


def refuse_never(ctx: AppContext, symbol: str) -> None:
    """Raise for a company the user's rule keeps out of everything."""
    if company := never_company(ctx, symbol, user=False):
        raise NeverListed(f"{company} is on your never list: it is never watched or traded")
