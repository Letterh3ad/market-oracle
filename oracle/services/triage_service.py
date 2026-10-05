"""The research funnel's first two stages (research-lab 05, spec 5). Stage 1, triage: Open Source
Asset Pricing's long-short returns for the method's mapped signal, in large caps (ME above the NYSE
20th percentile) after 2000, with the original sample and the publication split; no surviving
large-cap effect stops the method (status fail). Stage 2, power: the literature's effect against the
smallest one the testbed sees; one it cannot see stops as "could not see" (status not-testable).
Each stage run writes a row and moves the method's index status. OSAP is fetched in the compute
window only (a resumable job) or imported from files already on disk."""

from __future__ import annotations

import csv
import io
import zipfile
from collections.abc import Callable, Iterable, Iterator
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from statistics import median

import httpx

from oracle.analysts.lab import bonferroni_bar
from oracle.analysts.signal_test import power as formula_power
from oracle.analysts.triage import (
    SIGMA_CS, STRATEGY_Z, Span, detectable_excess, detectable_ic, ic_from_spread, publication_split, span,
)
from oracle.services.compute_window import WINDOW_TEXT, local_now, next_open, window_open
from oracle.services.context import AppContext
from oracle.services.errors import InvalidQuery
from oracle.services.lab_registry import hypotheses
from oracle.services.literature_service import open_index
from oracle.services.signal_test_service import signal_tests
from oracle.store.db import to_utc_iso
from oracle.store.lab import LabTestRepo
from oracle.store.literature import LiteratureRepo, MethodRow
from oracle.store.triage import ImportRow, OsapSignal, StageRow, TriageRepo, VariantCount

__all__ = ["FILES", "RELEASE", "TRIAGE_T", "OsapFile", "OsapStatus", "StageEntry", "TableRow", "check_window", "fetch_osap",
           "import_osap", "map_method", "observed_windows", "osap_folder", "osap_status", "run_power", "run_triage",
           "stage_table"]

RELEASE = "2025.10"
"""The OSAP data release the pinned files belong to (openassetpricing.com, version 2.0.0)."""


@dataclass(frozen=True)
class OsapFile:
    what: str
    """doc, op or large."""
    name: str
    drive_id: str


# The release's Google Drive file ids, read from its public folder listing on 2026-10-04. A newer
# release has new ids: pin them here with RELEASE.
FILES = (OsapFile("doc", "SignalDoc.csv", "1rdi0jTPSA6xtn6TpQAMT5WyEczQ1gK59"),
         OsapFile("op", "PredictorPortsFull.csv", "1g7w-yQ6Cg2qbMEkER9Q3vgns4JszXQo6"),
         OsapFile("large", "PredictorAltPorts_LiqScreen_ME_gt_NYSE20pct.zip", "1Q4YatQ3soRU_V7VeACwUn2bnCnmhDUI2"))
DOWNLOAD = "https://drive.usercontent.google.com/download?id={}&export=download&confirm=t"
CHUNK = 1 << 20

TRIAGE_FROM = frozenset({"go", "triage", "power"})
"""A method the user marked go, sent back to the literature (dig deeper), or triaged before."""
TRIAGE_T = 1.5
"""Large caps after 2000 must keep a positive long-short mean at this t (spec 12's "promising" t)."""
MIN_MONTHS = 36
LARGE_FROM = "2000-01"
CONFIRM_START = date(2020, 1, 1)
DEFAULT_QUANTILE = 0.2
STAGE_STATUS = {("triage", "pass"): "power", ("triage", "stop"): "fail",
                ("power", "pass"): "power", ("power", "stop"): "not-testable"}


@dataclass(frozen=True)
class OsapStatus:
    release: str
    signals: dict[str, int]
    """SignalDoc rows by kind (Predictor, Placebo, Drop)."""
    variants: tuple[VariantCount, ...]
    imports: tuple[ImportRow, ...]
    folder: str
    on_disk: tuple[str, ...]
    """Pinned files fully downloaded into the folder."""
    window_open: bool
    next_window: str
    """Local time the compute window next opens (now when open)."""
    window: str


@dataclass(frozen=True)
class StageEntry:
    stage: str
    outcome: str
    summary: str
    detail: dict
    status_from: str
    status_to: str
    by: str
    at: str


@dataclass(frozen=True)
class TableRow:
    ref: str
    name: str
    family: str
    status: str
    signal: str | None
    triage: StageEntry | None
    """The newest triage run."""
    power: StageEntry | None
    runs: int


def _float(x: str | None) -> float | None:
    x = (x or "").strip()
    if not x or x.upper() == "NA":
        return None
    try:
        return float(x)
    except ValueError:
        return None


def _int(x: str | None) -> int | None:
    f = _float(x)
    return None if f is None else int(f)


def _signals(text: Iterable[str], release: str) -> list[OsapSignal]:
    out = []
    for r in csv.DictReader(text):
        name = (r.get("Acronym") or "").strip()
        if name:
            out.append(OsapSignal(name, (r.get("Cat.Signal") or "").strip(), (r.get("Authors") or "").strip(),
                                  _int(r.get("Year")), (r.get("Journal") or "").strip(),
                                  (r.get("LongDescription") or "").strip(), _int(r.get("SampleStartYear")),
                                  _int(r.get("SampleEndYear")), _float(r.get("Sign")), _float(r.get("Return")),
                                  _float(r.get("T-Stat")), (r.get("Stock Weight") or "").strip(),
                                  _float(r.get("LS Quantile")), release))
    if not out:
        raise InvalidQuery("SignalDoc.csv has no Acronym rows: not OSAP's signal documentation")
    return out


def _long_short(text: Iterable[str]) -> Iterator[tuple[str, str, float]]:
    """(signal, YYYY-MM, percent) of every LS row with a return."""
    reader = csv.DictReader(text)
    if not {"signalname", "port", "date", "ret"} <= set(reader.fieldnames or ()):
        raise InvalidQuery("a portfolio file needs signalname, port, date and ret columns")
    for r in reader:
        if r["port"].strip() == "LS" and (ret := _float(r["ret"])) is not None:
            yield r["signalname"].strip(), r["date"].strip()[:7], ret


def _open_text(path: Path):
    if path.suffix.lower() != ".zip":
        return path.open(encoding="utf-8-sig", newline="")
    z = zipfile.ZipFile(path)
    member = next((n for n in z.namelist() if n.lower().endswith(".csv")), None)
    if member is None:
        raise InvalidQuery(f"{path.name} holds no CSV")
    return io.TextIOWrapper(z.open(member), encoding="utf-8-sig", newline="")


def _find(folder: Path, f: OsapFile) -> Path | None:
    for name in (f.name, f.name.removesuffix(".zip") + ".csv"):
        if (folder / name).is_file():
            return folder / name
    return None


def import_osap(ctx: AppContext, folder: Path | str, release: str = RELEASE) -> list[ImportRow]:
    """Read the pinned files found in `folder` (SignalDoc.csv, PredictorPortsFull.csv and the large-cap
    screen, zipped or not) into the research database; each replaces what it held before."""
    folder = Path(folder)
    found = {f.what: p for f in FILES if (p := _find(folder, f))}
    if not found:
        raise InvalidQuery(f"no OSAP file in {folder}: expected {', '.join(f.name for f in FILES)}")
    at = to_utc_iso(ctx.clock.utcnow())
    done = []
    with open_index(ctx) as r:
        repo = TriageRepo(r.conn)
        if "doc" in found:
            with _open_text(found["doc"]) as text:
                n = repo.replace_signals(_signals(text, release))
            done.append(("doc", found["doc"].name, n))
        elif not repo.signal_count():
            raise InvalidQuery("import SignalDoc.csv first: the returns need its sample and publication years")
        for what in ("op", "large"):
            if what in found:
                with _open_text(found[what]) as text:
                    n = repo.replace_returns(what, _long_short(text))
                done.append((what, found[what].name, n))
        for what, name, n in done:
            repo.add_import(what, release, name, n, at)
        return [ImportRow(what, release, name, n, at) for what, name, n in done]


def osap_folder(ctx: AppContext) -> Path:
    return ctx.config.research_db_path.parent / "osap" / RELEASE


def osap_status(ctx: AppContext) -> OsapStatus:
    folder = osap_folder(ctx)
    local = local_now(ctx)
    with open_index(ctx) as r:
        repo = TriageRepo(r.conn)
        return OsapStatus(RELEASE, repo.signal_count(), tuple(repo.counts()), tuple(repo.imports()), str(folder),
                          tuple(f.name for f in FILES if (folder / f.name).is_file()), window_open(local),
                          f"{next_open(local):%a %Y-%m-%d %H:%M}", WINDOW_TEXT)


def check_window(ctx: AppContext) -> None:
    local = local_now(ctx)
    if not window_open(local):
        raise InvalidQuery(f"bulk downloads run only in {WINDOW_TEXT}: it opens {next_open(local):%a %H:%M}")


async def fetch_osap(ctx: AppContext, say: Callable[[str], None] = lambda _: None,
                     stop: Callable[[], bool] = lambda: False) -> list[ImportRow]:
    """Download the pinned release files (about 100 MB) and import them. Refused outside the compute
    window; a file already downloaded is kept, and a part file is resumed where it stopped."""
    check_window(ctx)
    folder = osap_folder(ctx)
    folder.mkdir(parents=True, exist_ok=True)
    async with httpx.AsyncClient(transport=ctx.transport, follow_redirects=True, timeout=60) as client:
        for f in FILES:
            if (folder / f.name).is_file():
                say(f"{f.name}: already downloaded")
                continue
            await _download(client, f, folder, say, stop)
    say("importing")
    return import_osap(ctx, folder)


def _complete(part: Path, content_range: str | None) -> bool:
    """A 416 means the part starts past the end: finished only if its size is the file's total
    ("bytes */TOTAL"); a stale or oversized part must not become the file."""
    total = (content_range or "").rpartition("/")[2]
    return total.isdigit() and part.stat().st_size == int(total)


async def _download(client: httpx.AsyncClient, f: OsapFile, folder: Path, say, stop) -> None:
    part = folder / (f.name + ".part")
    have = part.stat().st_size if part.exists() else 0
    headers = {"Range": f"bytes={have}-"} if have else {}
    async with client.stream("GET", DOWNLOAD.format(f.drive_id), headers=headers) as resp:
        if resp.status_code == 416:
            if _complete(part, resp.headers.get("content-range")):
                part.replace(folder / f.name)
            else:
                part.unlink()
                await _download(client, f, folder, say, stop)
            return
        resp.raise_for_status()
        if "text/html" in resp.headers.get("content-type", ""):
            raise InvalidQuery(f"Google Drive sent a page instead of {f.name}: the release may have moved; pin its "
                               "new file ids (triage_service.FILES)")
        resumed = resp.status_code == 206
        with part.open("ab" if resumed else "wb") as out:
            got = have if resumed else 0
            async for chunk in resp.aiter_bytes(CHUNK):
                if stop():
                    raise InvalidQuery(f"stopped: {f.name} keeps {got} bytes and resumes next time")
                out.write(chunk)
                got += len(chunk)
                say(f"{f.name}: {got / 1e6:.0f} MB")
    part.replace(folder / f.name)


def _by(by: str) -> str:
    by = by.strip()
    if not by or len(by) > 40:
        raise InvalidQuery("by names who did it (1 to 40 characters), e.g. user or autopilot")
    return by


def _method(repo: LiteratureRepo, ref: str) -> MethodRow:
    m = repo.method(ref.strip())
    if m is None:
        raise InvalidQuery(f"no method {ref.strip()!r} in the literature index")
    return m


def map_method(ctx: AppContext, ref: str, signal: str | None, by: str = "user") -> str | None:
    """Tie a method to its OSAP signal (by acronym, as SignalDoc spells it); None unties it."""
    by = _by(by)
    with open_index(ctx) as r:
        m = _method(LiteratureRepo(r.conn), ref)
        repo = TriageRepo(r.conn)
        name = None
        if signal is not None and signal.strip():
            found = repo.signal(signal.strip())
            if found is None:
                raise InvalidQuery(f"{signal.strip()!r} is not in OSAP's signal documentation"
                                   + ("" if repo.signal_count() else " (not imported yet)"))
            name = found.signal
        repo.set_map(m.id, name, by, to_utc_iso(ctx.clock.utcnow()))
        return name


def _span_text(s: Span) -> str:
    if s.mean is None:
        return "no months"
    t = "" if s.t is None else f" t {s.t:.2f}"
    return f"{s.mean:+.2f}%/month{t} over {s.n} months"


def run_triage(ctx: AppContext, ref: str, by: str = "user", by_hand: str | None = None,
               note: str = "") -> StageEntry:
    """Stage 1. With `by_hand` (pass or stop) and a note, the literature's verdict for a method OSAP
    does not cover (timing, strategies, events); else the mapped signal's OSAP numbers judge it."""
    by = _by(by)
    note = note.strip()
    with open_index(ctx) as r:
        lit, repo = LiteratureRepo(r.conn), TriageRepo(r.conn)
        m = _method(lit, ref)
        if m.status not in TRIAGE_FROM:
            raise InvalidQuery(f"{m.ref} is {m.status}: triage takes a method marked go (or sent back with dig "
                               "deeper)")
        if by_hand is not None:
            if by_hand not in ("pass", "stop"):
                raise InvalidQuery(f"by hand is pass or stop, got {by_hand!r}")
            if not note:
                raise InvalidQuery("a triage by hand needs a note: the published evidence it rests on")
            outcome, summary, detail = by_hand, f"by hand: {note}", {"by_hand": True, "note": note}
        else:
            outcome, summary, detail = _osap_triage(repo, m)
            if note:
                detail["note"] = note
        return _record(ctx, repo, m, "triage", outcome, summary, detail, by)


def _osap_triage(repo: TriageRepo, m: MethodRow) -> tuple[str, str, dict]:
    name = repo.mapped().get(m.id)
    if name is None:
        raise InvalidQuery(f"{m.ref} has no OSAP signal: map it (oracle lab map), or triage it by hand with a note")
    sig = repo.signal(name)
    op, large = repo.returns(name, "op"), repo.returns(name, "large")
    if sig is None or not large:
        raise InvalidQuery(f"OSAP {name} has no large-cap returns imported: fetch OSAP in {WINDOW_TEXT}, or import "
                           "its files")
    post = span("large caps since 2000", large, LARGE_FROM, None)
    detail: dict = {"signal": name, "release": sig.release, "large_post_2000": asdict(post),
                    "paper": {"authors": sig.authors, "year": sig.year, "journal": sig.journal,
                              "sample": [sig.sample_start, sig.sample_end], "return": sig.paper_return,
                              "t": sig.paper_t, "weight": sig.weight, "quantile": sig.quantile}}
    if sig.sample_start and sig.sample_end and sig.year:
        if op:
            split = publication_split(op, sig.sample_start, sig.sample_end, sig.year)
            detail["original"] = {"spans": [asdict(s) for s in split.spans], "decay": split.decay}
        large_split = publication_split(large, sig.sample_start, sig.sample_end, sig.year)
        detail["large"] = {"spans": [asdict(s) for s in large_split.spans], "decay": large_split.decay}
    survives = post.n >= MIN_MONTHS and post.mean is not None and post.mean > 0 and (post.t or 0) >= TRIAGE_T
    why = ("survives" if survives else
           f"fewer than {MIN_MONTHS} months" if post.n < MIN_MONTHS else
           f"no large-cap effect at t {TRIAGE_T:g}")
    return ("pass" if survives else "stop",
            f"OSAP {name}: large caps since 2000 {_span_text(post)} ({why})", detail)


def _record(ctx: AppContext, repo: TriageRepo, m: MethodRow, stage: str, outcome: str, summary: str, detail: dict,
            by: str) -> StageEntry:
    status_to = STAGE_STATUS[(stage, outcome)]
    at = to_utc_iso(ctx.clock.utcnow())
    repo.record(m.id, stage, outcome, summary, detail, m.status, status_to, by, at)
    return StageEntry(stage, outcome, summary, detail, m.status, status_to, by, at)


def observed_windows(ctx: AppContext) -> list[tuple[int, float, float | None, int]]:
    """Every kept signal test's confirm windows: (horizon, IC standard error, names, IC days). The error
    is the stored observed power over the bar that test froze, not rebuilt from the rounded IC and t."""
    return [(w.horizon, w.observed_power / t.bar, w.names, w.days)
            for t in signal_tests(ctx).values() for w in t.confirm if w.observed_power]


def run_power(ctx: AppContext, ref: str, kind: str = "signal", horizon: int = 20, expected: float | None = None,
              tracking_error: float | None = None, sigma: float = SIGMA_CS, source: str = "",
              by: str = "user") -> StageEntry:
    """Stage 2, for a method past triage. A signal: the expected IC (given, or from OSAP's large-cap
    spread since 2000) against the smallest IC the testbed's kept confirm windows see at the bar a
    test registered now needs. A strategy: the expected yearly excess over the index (given, or the
    large-cap spread a year) against z tracking errors over the root of the confirm window's years."""
    by = _by(by)
    if kind not in ("signal", "strategy"):
        raise InvalidQuery(f"power is for a signal or a strategy, got {kind!r}")
    if isinstance(horizon, bool) or not isinstance(horizon, int) or horizon < 1:
        raise InvalidQuery("the horizon is whole trading days, 1 or more")
    if sigma <= 0:
        raise InvalidQuery("the cross-sectional spread (sigma) is above 0")
    observed = observed_windows(ctx) if kind == "signal" else []
    today = ctx.clock.utcnow().date()
    with open_index(ctx) as r:
        lit, repo = LiteratureRepo(r.conn), TriageRepo(r.conn)
        m = _method(lit, ref)
        triaged = [s for s in repo.stages(m.id) if s.stage == "triage"]
        if m.status != "power" or not triaged or triaged[-1].outcome != "pass":
            raise InvalidQuery(f"{m.ref} is {m.status}: power takes a method that passed triage")
        post = triaged[-1].detail.get("large_post_2000")
        quantile = (triaged[-1].detail.get("paper") or {}).get("quantile") or DEFAULT_QUANTILE
        bar = bonferroni_bar(hypotheses(LabTestRepo(r.conn).all()) + 1)
        detail: dict = {"kind": kind, "source": source.strip()}
        if kind == "signal":
            outcome, summary = _signal_power(detail, observed, horizon, expected, post, quantile, sigma, bar)
        else:
            outcome, summary = _strategy_power(detail, expected, tracking_error, post, today)
        return _record(ctx, repo, m, "power", outcome, summary, detail, by)


def _signal_power(detail: dict, observed, h: int, expected: float | None, post: dict | None, quantile: float,
                  sigma: float, bar: float) -> tuple[str, str]:
    if expected is None:
        if not post or post.get("mean") is None:
            raise InvalidQuery("no expected IC: give one from the literature (a by-hand triage has no OSAP spread)")
        expected = ic_from_spread(post["mean"], quantile, h, sigma)
        detail["expected_from"] = (f"OSAP large-cap spread {post['mean']:+.2f}%/month, {quantile:g} tails, "
                                   f"cross-sectional spread {sigma:.0%} a month")
    else:
        detail["expected_from"] = "given"
    seen = detectable_ic([(oh, se) for oh, se, _, _ in observed], h, bar)
    if seen is None:
        raise InvalidQuery("no kept signal test to read the testbed's power from: run one first")
    sized = [(n, d) for oh, _, n, d in observed if oh == h and n and d] or \
        [(n, d) for _, _, n, d in observed if n and d]
    formula = formula_power(median(n for n, _ in sized), int(median(d for _, d in sized)), h, bar) if sized else None
    detail.update(horizon=h, expected_ic=expected, detectable_ic=seen.value, bar=bar, windows=seen.used,
                  same_horizon=seen.same_horizon, formula_ic=formula)
    if expected >= seen.value:
        return "pass", f"expected IC {expected:.4f} at {h} d; the testbed sees {seen.value:.4f} at t {bar:.2f}"
    return "stop", (f"could not see: expected IC {expected:.4f} at {h} d, the testbed sees {seen.value:.4f} at "
                    f"t {bar:.2f}")


def _strategy_power(detail: dict, expected: float | None, te: float | None, post: dict | None,
                    today: date) -> tuple[str, str]:
    if te is None or te <= 0:
        raise InvalidQuery("a strategy's power needs its tracking error against the index (a share a year, e.g. "
                           "0.05)")
    if expected is None:
        if not post or post.get("mean") is None:
            raise InvalidQuery("no expected excess: give one from the literature")
        expected = post["mean"] * 12 / 100
        detail["expected_from"] = "OSAP large-cap long-short spread a year (a long-only rule keeps less)"
    else:
        detail["expected_from"] = "given"
    years = (today - CONFIRM_START).days / 365.25
    seen = detectable_excess(te, years)
    detail.update(expected_excess=expected, detectable_excess=seen, tracking_error=te, years=round(years, 2),
                  z=STRATEGY_Z)
    if expected >= seen:
        return "pass", f"expected excess {expected:.2%} a year; the confirm window sees {seen:.2%}"
    return "stop", f"could not see: expected excess {expected:.2%} a year, the confirm window sees {seen:.2%}"


def _entry(s: StageRow) -> StageEntry:
    return StageEntry(s.stage, s.outcome, s.summary, s.detail, s.status_from, s.status_to, s.by, s.at)


def stage_table(ctx: AppContext) -> list[TableRow]:
    """Every method with an OSAP signal or a stage run: its newest triage and power runs."""
    with open_index(ctx) as r:
        repo = TriageRepo(r.conn)
        mapped, runs = repo.mapped(), repo.stages()
        by_method: dict[int, list[StageRow]] = {}
        for s in runs:
            by_method.setdefault(s.method_id, []).append(s)
        out = []
        for m in LiteratureRepo(r.conn).methods():
            if m.id not in mapped and m.id not in by_method:
                continue
            mine = by_method.get(m.id, [])
            last = {s.stage: s for s in mine}
            out.append(TableRow(m.ref, m.name, m.family, m.status, mapped.get(m.id),
                                _entry(last["triage"]) if "triage" in last else None,
                                _entry(last["power"]) if "power" in last else None, len(mine)))
        return out
