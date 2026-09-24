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
    facts: tuple[str, ...] = ()
    """What the run's model read in it that bears on the share price."""
    read: str | None = None
    """With no facts, what reading it gave: why it was not read or noted, NOT_ABOUT or NOTHING.
    None when it was never read."""
    via: str | None = None
    """The linked company whose planned search found it, in words."""
    site: str | None = None
    """The outlet's own domain, where the feed named it beside a redirect link."""


NOT_ABOUT = "not about the company"
NOTHING = "nothing in it bears on the share price"


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


VERDICTS = ("buy", "hold", "sell")


@dataclass(frozen=True)
class Verdict:
    """The research's call on the listing, written last from everything the run found."""

    label: str
    """One of VERDICTS."""
    confidence: float
    """0 to 1."""
    reason: str
    cites: tuple[int, ...]
    invalidated_if: str


UNVALIDATED = "unvalidated: AI research has not yet beaten the technicals-only baseline in paper trades"
UNVALIDATED_TAG = "(unvalidated)"
"""The short form, where a line has no room for the whole badge."""


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
    verdict: Verdict | None = None
    verdict_rejected: str | None = None
    """Why there is no verdict; None with no verdict means the report predates verdicts."""
    unread: tuple[str, ...] = ()
    """What the verdict role's context budget left out, e.g. "7 uncited items"."""

    @property
    def no_verdict(self) -> str | None:
        """Why the report has no verdict, as shown; None when it has one."""
        if self.verdict:
            return None
        return f"the verdict was rejected: {self.verdict_rejected}" if self.verdict_rejected             else "written before reports had verdicts"

    @property
    def did_not_read(self) -> str | None:
        return f"did not read: {', '.join(self.unread)}" if self.unread else None

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
        claims = [*self.findings, *self.risks, *(c for c in (self.bull, self.bear, self.verdict) if c)]
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
    """Models that answered after a stage's model refused, over the whole run."""
    stages: tuple[StageModel, ...] = ()
    """What each stage ran on, in stage order; empty for runs from before stages were kept.
    `backend` and `model` are the research stage's."""

    @property
    def mixed(self) -> bool:
        """Its stages ran on more than one model."""
        return len({(s.backend, s.model) for s in self.stages}) > 1

    @property
    def model_label(self) -> str:
        if not self.mixed:
            return f"{self.backend} {self.model}{_fell_back(self.fell_back_to)}"
        groups: dict[tuple[str, str], list[StageModel]] = {}
        for s in self.stages:
            groups.setdefault((s.backend, s.model), []).append(s)
        return "; ".join(
            f"{backend} {model} ({', '.join(s.stage for s in group)})"
            f"{_fell_back(tuple(sorted({m for s in group for m in s.fell_back_to})))}"
            for (backend, model), group in groups.items())


@dataclass(frozen=True)
class StageModel:
    stage: str
    """The slot it ran on: queries, research, notes, roles or verdict."""
    profile: str
    backend: str
    model: str
    fell_back_to: tuple[str, ...] = ()


def _fell_back(models: tuple[str, ...]) -> str:
    return f" ({', '.join(models)} answered when it refused)" if models else ""
