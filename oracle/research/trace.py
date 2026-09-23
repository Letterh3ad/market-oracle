"""What a research run did, step by step, and what it concluded."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
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
class Claim:
    text: str
    cites: tuple[int, ...]
    """Ids of the evidence it rests on; a claim without any is never kept."""
    role: str


UNVALIDATED = "unvalidated: AI research has not yet beaten the technicals-only baseline in paper trades"


@dataclass(frozen=True)
class Report:
    """What the research roles concluded from a run's evidence. It informs; it never
    changes the traded lean (Laya weighs it from phase 8)."""

    day: date
    findings: tuple[Claim, ...]
    bull: Claim | None
    bear: Claim | None
    risks: tuple[Claim, ...]
    invalidated_if: str | None

    @property
    def cites(self) -> set[int]:
        claims = [*self.findings, *self.risks, *(c for c in (self.bull, self.bear) if c)]
        return {i for c in claims for i in c.cites}


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
    as_of: date | None = None
    """Set for a replay: the run saw only what existed by the end of that day."""
    queries: tuple[str, ...] = ()
    """The searches the run was told to start from."""
    report: Report | None = None
