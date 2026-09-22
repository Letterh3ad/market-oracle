from __future__ import annotations

from oracle.config import KEY_NAMES
from oracle.services.context import AppContext


def missing_key(ctx: AppContext, source: str) -> bool:
    """A paid source is skipped until its key is set; key names match source names."""
    return source in KEY_NAMES and ctx.config.keys.get(source) is None
