from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from oracle.services.context import AppContext
from oracle.store.meta import MetaRepo
from oracle.store.migrate import current_version


@dataclass(frozen=True)
class StatusReport:
    schema_version: int
    db_path: Path
    config_source: Path | None
    created_at: str
    base_currency: str


def status(ctx: AppContext) -> StatusReport:
    return StatusReport(
        schema_version=current_version(ctx.conn),
        db_path=ctx.config.db_path,
        config_source=ctx.config_source,
        created_at=MetaRepo(ctx.conn).get("created_at") or "",
        base_currency=ctx.config.base_currency,
    )
