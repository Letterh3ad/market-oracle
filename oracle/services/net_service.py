from __future__ import annotations

from oracle.net.gate import SourceGate, SourceStatus
from oracle.services.context import AppContext


def source_statuses(ctx: AppContext) -> list[SourceStatus]:
    gate = SourceGate(ctx.conn, ctx.clock, ctx.config.sources)
    return [gate.status(name) for name in sorted(ctx.config.sources)]
