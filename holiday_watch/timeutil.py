from __future__ import annotations

from datetime import datetime, timezone


def iso(t: datetime) -> str:
    return t.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse(text: str) -> datetime:
    """An ISO time; one without a zone is taken as UTC (the app stores UTC)."""
    t = datetime.fromisoformat(text)
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)
