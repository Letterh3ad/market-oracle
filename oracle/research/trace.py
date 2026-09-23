"""What a research run did, step by step, and what it concluded."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class RunStatus(StrEnum):
    RUNNING = "running"
    DONE = "done"
    STEP_CAP = "step_cap"
    """Hit the step limit; the findings were asked for straight after."""
    FAILED = "failed"


FINAL = "final"


@dataclass(frozen=True)
class Step:
    n: int
    thought: str
    action: str
    """A tool's name, or FINAL."""
    input: str
    observation: str | None
    """What the tool returned; None for the final answer."""


@dataclass(frozen=True)
class Evidence:
    """An article a run was shown; `cited` if its findings refer to it as [a<id>]."""

    id: int
    url: str
    title: str
    outlet: str | None
    published: datetime | None
    cited: bool


@dataclass(frozen=True)
class ResearchRun:
    id: int
    symbol: str
    backend: str
    model: str
    started_at: str
    finished_at: str | None
    status: RunStatus
    findings: str | None
    error: str | None
    steps: tuple[Step, ...]
    evidence: tuple[Evidence, ...] = ()
    """Newest first."""
