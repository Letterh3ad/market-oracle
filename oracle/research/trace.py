"""What a research run did, step by step, and what it concluded."""

from __future__ import annotations

from collections import Counter
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
    """What the tool returned; for the final answer, None unless the step limit forced it."""


@dataclass(frozen=True)
class Evidence:
    """An article a run was shown; `cited` if its findings refer to it as [a<id>]."""

    id: int
    url: str
    title: str
    outlet: str | None
    published: datetime | None
    cited: bool
    body: str | None = None
    """Only for evidence Market Oracle wrote itself: the stored data a run read."""
    query: str | None = None
    """The search that first found it."""


@dataclass(frozen=True)
class Claim:
    text: str
    cites: tuple[int, ...]
    """Ids of the evidence it rests on; a claim without any is never kept."""
    role: str


class Drop(StrEnum):
    """Why a claim was left out, in the order the checks run."""

    WRONG_SHAPE = "wrong shape"
    EMPTY_TEXT = "empty text"
    NO_CITATION = "no citation"
    UNKNOWN_CITATION = "unknown citation"
    """It cites ids, but none of them were shown to the run."""


# Most telling first, for the summary line.
_DROPPED = {Drop.UNKNOWN_CITATION: "citing nothing shown", Drop.NO_CITATION: "with no citation",
            Drop.EMPTY_TEXT: "with no text", Drop.WRONG_SHAPE: "in the wrong shape"}


@dataclass(frozen=True)
class RoleCheck:
    """What the checks made of one role's answer."""

    role: str
    usable: bool
    """The reply had every field asked for, of the right type; an unusable role adds nothing."""
    dropped: dict[Drop, int]


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
    checks: tuple[RoleCheck, ...] = ()
    """Empty for a report saved before answers were checked."""

    @property
    def usable(self) -> bool:
        return not self.checks or any(c.usable for c in self.checks)

    @property
    def rejections(self) -> str | None:
        """What the checks rejected, as one line for the GUI and CLI; None when nothing was."""
        parts = []
        if bad := sum(not c.usable for c in self.checks):
            parts.append(f"{bad} of {len(self.checks)} roles gave "
                         + ("an unusable answer" if bad == 1 else "unusable answers"))
        dropped = Counter()
        for c in self.checks:
            dropped.update(c.dropped)
        if total := dropped.total():
            reasons = sorted(dropped.items(), key=lambda kv: (-kv[1], list(_DROPPED).index(kv[0])))
            parts.append(f"{total} claim{'' if total == 1 else 's'} dropped ("
                         + ", ".join(f"{n} {_DROPPED[r]}" for r, n in reasons) + ")")
        return "; ".join(parts) or None

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
    fell_back_to: tuple[str, ...] = ()
    """Models that answered after `model` refused."""

    @property
    def model_label(self) -> str:
        fell_back = f" ({', '.join(self.fell_back_to)} answered when it refused)" if self.fell_back_to else ""
        return f"{self.backend} {self.model}{fell_back}"
