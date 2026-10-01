"""The pre-registered predictive test of Laya's daily features (spec decision 6, DECISIONS
2026-09-30): walk-forward 6-month segments from 2022-09-21, each main-list company's features
against its next 5 and 20 trading-day return over its industry, Spearman rank IC across companies
per day, a combined score whose feature signs and weights are fitted on trailing data only, and
the fixed bar. Nothing here is tuned on results.

Point in time: features are built from backfilled, dated articles through the shared aggregator
(`daily_features(history=True)`), a feature day used only by later decision days; a map link
counts for an article only when first known by that article's day (the sensitivity run takes
every link as known today, and says so); a return starts at the open of the decision day."""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta

from oracle.analysts.predictive import (
    MIN_DAYS, PEER_SLACK_DAYS, SegmentIC, combine, fit_weights, known_moment, lookback, mean, newey_west_t, own_return,
    span_return, spearman, trailing_return, usable_day, verdict,
)
from oracle.broker.walk_forward import segments
from oracle.market.bars import Bar
from oracle.services.context import AppContext
from oracle.services.errors import InvalidQuery
from oracle.services.history_service import Coverage, company_history, coverage, history_companies
from oracle.services.industry_research_service import KIND_RELATION, relation_for_kinds
from oracle.services.industry_service import Related
from oracle.services.laya_features import daily_features
from oracle.services.laya_history_service import MapSubject, map_subjects
from oracle.services.laya_service import MODEL_TAG, ScoredArticle
from oracle.services.watch_service import DEFAULT_LIST
from oracle.store.bars import BarRepo
from oracle.store.db import to_utc_iso
from oracle.store.industry_news import IndustryNewsRepo
from oracle.store.laya_tests import LayaTestRepo

__all__ = ["CAVEAT", "FIT_DAYS", "HORIZONS", "MAIN", "SENSITIVITY", "TEST_START", "FeatureIC", "HorizonResult",
           "LayaTest", "MAIN_RELATIONS", "Obs", "Panel", "SegmentRow", "build_panel", "horizon_result",
           "keep_laya_test", "latest_verdict", "laya_tests", "observations", "relation_for", "run_laya_test",
           "trailing_fit"]

TEST_START = date(2022, 9, 21)
HORIZONS = ((5, 7), (20, 28))
"""(trading days ahead, feature lookback in calendar days)."""
FIT_DAYS = 730
MAIN, SENSITIVITY = "main", "sensitivity"
MAIN_RELATIONS = ("own", "competitor", "industry")
SENSITIVITY_RELATIONS = ("own", "competitor", "supplier", "customer", "industry")
MAIN_LINK_SOURCES = ("user", "discovery", "chat")
"""Links the main run counts; 03's `filing` and `laya` links wait for the sensitivity run."""
INDUSTRY_SOURCES = ("industry", "industry_group")
BASELINE = "own_attention"
"""log(1 + own articles in the window): news attention alone, Laya's reading left out."""
MOMENTUM = "own_momentum"
"""The listing's industry-excess return over the feature window, closes before t: the price a headline may echo."""
BASELINES = (BASELINE, MOMENTUM)
MIN_INDUSTRY = 3
COVERAGE_SHARE = 0.5
CAVEAT = ("Survivorship: today's main list and industry maps taken back in time. Laya's probabilities are "
          "uncalibrated; returns are in each listing's own currency.")
RUN_LABELS = {MAIN: "main (links first known by the article's day; 03's links left out)",
              SENSITIVITY: "sensitivity: links as known today, 03's supplier and customer links included"}


def relation_for(own: set[int], about: dict[int, list[Related]], sensitivity: bool) -> Callable[[ScoredArticle], str | None]:
    """The feature a scored pair counts in for its target, as known on the article's day (main),
    or as the map knows it today (sensitivity); None leaves it out."""
    def rel(s: ScoredArticle) -> str | None:
        if s.article_id in own:
            return "own"
        related = about.get(s.article_id)
        if not related:
            return None
        if sensitivity:
            return relation_for_kinds([k for r in related for k in r.kinds])
        end = (known_moment(s.known_at).date() + timedelta(days=1)).isoformat()
        found = set()
        for r in related:
            for x in r.relations:
                if x.first_known is None:
                    found.add("competitor")
                elif x.source in MAIN_LINK_SOURCES and x.first_known < end and KIND_RELATION.get(x.kind) in (
                        "competitor", "industry"):
                    found.add(KIND_RELATION[x.kind])
        return next((r for r in ("competitor", "industry") if r in found), None)
    return rel


@dataclass
class Panel:
    """What the test reads, gathered once: per main-list company its daily features, its bars
    and its industry peers' bars."""
    mains: list[str]
    daily: dict[str, dict[date, dict[str, tuple[int, float, float]]]]
    bars: dict[str, list[Bar]]
    peers: dict[str, list[str]]
    articles: dict[str, int] = field(default_factory=dict)
    """Relation -> scored pairs counted."""


def build_panel(ctx: AppContext, watchlist: str = DEFAULT_LIST, *, sensitivity: bool = False, start: date = TEST_START,
                end: date | None = None, model: str = MODEL_TAG, research: bool = False,
                say: Callable[[str], None] = lambda _: None) -> Panel:
    """With `research`, each company's articles are its backfill plus what the daily industry
    research found for it (Laya's live score, ticket 08); the test reads the backfill alone."""
    news = IndustryNewsRepo(ctx.conn)

    def ids(symbol: str) -> list[int]:
        own = company_history(ctx, symbol)
        return list(dict.fromkeys(own + news.recent(symbol, ""))) if research else own

    companies = history_companies(ctx, watchlist)
    mains = [c.symbol for c in companies if c.main]
    end = end or ctx.clock.utcnow().date()
    first = start - timedelta(days=max(lb for _, lb in HORIZONS) + 1)
    panel = Panel(mains, {}, {}, {})
    repo = BarRepo(ctx.conn)
    for n, m in enumerate(mains, 1):
        say(f"features {m} ({n}/{len(mains)})")
        subjects: list[MapSubject] = map_subjects(ctx, m, companies)
        about: dict[int, list[Related]] = {}
        for s in subjects:
            for i in ids(s.symbol):
                about.setdefault(i, []).append(s.related)
        rows = daily_features(ctx, m, first, end, model=model, history=True,
                              relation_for=relation_for(set(ids(m)), about, sensitivity),
                              day_for=lambda s, m=m: usable_day(m, known_moment(s.known_at)))
        days: dict[date, dict[str, tuple[int, float, float]]] = {}
        for r in rows:
            days.setdefault(r.day, {})[r.relation] = (r.articles, r.score, r.relevance)
            panel.articles[r.relation] = panel.articles.get(r.relation, 0) + r.articles
        panel.daily[m] = days
        panel.peers[m] = [s.symbol for s in subjects
                          if any(x.first_known is None and x.source in INDUSTRY_SOURCES for x in s.related.relations)]
    for symbol in {*mains, *(p for ps in panel.peers.values() for p in ps)}:
        panel.bars[symbol] = repo.since(symbol, first)
    return panel


@dataclass(frozen=True)
class SegmentRow:
    start: date
    end: date
    ic: float | None
    t: float | None
    days: int
    counted: bool
    why: str | None
    weights: dict[str, float]


@dataclass(frozen=True)
class FeatureIC:
    name: str
    ic: float | None
    """Mean daily IC over the counted segments' days, the feature as it is (no fitted sign)."""
    t: float | None
    days: int
    positive: int
    """Counted segments where its mean IC is above 0."""
    in_score: bool
    """In the combined score; else a secondary figure."""


@dataclass(frozen=True)
class HorizonResult:
    horizon: int
    lookback: int
    ic: float | None
    t: float | None
    days: int
    observations: int
    segments: tuple[SegmentRow, ...]
    features: tuple[FeatureIC, ...]
    verdict: str
    passed: bool
    positive: int
    counted: int
    baseline_ic: float | None = None
    """The news-attention baseline's IC over the same counted days, its sign fitted per segment on the
    same purged trailing window as the combined score."""
    baseline_t: float | None = None
    beats_baseline: bool | None = None
    """Laya only counts as useful when its combined IC beats the baseline's (the bar is separate)."""
    baseline_raw_ic: float | None = None
    """The baseline as it is (more attention ranked higher), over the counted days."""
    momentum_ic: float | None = None
    """The trailing industry-excess momentum baseline, fitted and scored like the attention one."""
    momentum_t: float | None = None
    beats_momentum: bool | None = None
    """Laya only counts as useful when its combined IC beats both baselines (the bar is separate)."""
    momentum_raw_ic: float | None = None


@dataclass(frozen=True)
class LayaTest:
    run: str
    label: str
    model: str
    start: date
    end: date
    companies: int
    articles: dict[str, int]
    horizons: tuple[HorizonResult, ...]
    verdict: str
    passed: bool
    run_at: str
    t_method: str = "Newey-West (Bartlett) with h lags over the daily ICs (overlapping windows)"
    caveat: str = CAVEAT
    id: int | None = None


def _features(relations: Sequence[str]) -> tuple[list[str], list[str]]:
    """(in the combined score, secondary): per-article means and own relevance, then the sums."""
    return [f"{r}_mean" for r in relations], [f"{r}_sum" for r in relations] + ["own_relevance", *BASELINES]


def _quarter_span(q: str) -> tuple[date, date]:
    y, n = int(q[:4]), int(q[-1])
    first = date(y, 3 * n - 2, 1)
    last = date(y + (n == 4), 1 if n == 4 else 3 * n + 1, 1) - timedelta(days=1)
    return first, last


def _coverage_why(cov: Coverage | None, start: date, end: date) -> str | None:
    if cov is None:
        return None
    quarters = [q for q in cov.quarters if (s := _quarter_span(q))[0] <= end and s[1] >= start]
    rows = [r for r in cov.rows if r.main]
    total = len(rows) * len(quarters)
    if not total:
        return "no coverage"
    bad = sum(1 for r in rows for q in quarters if q in r.thin or q in r.noisy)
    good = 1 - bad / total
    return None if good >= COVERAGE_SHARE else f"thin coverage: {good:.0%} of company quarters usable"


Obs = dict[date, list[tuple[dict[str, float], float, date]]]
"""Decision day -> per company (features, industry-excess return, exit day)."""


def observations(panel: Panel, h: int, lb: int, first: date, last: date, relations: Sequence[str],
                 names: Sequence[str]) -> tuple[Obs, dict[str, dict[date, float]]]:
    """Every decision day from `first` to `last` with each main company's features and its
    `h`-day return over its industry, and each of `names`' daily IC. Reads only `panel`."""
    dates = {s: [b.date for b in bars] for s, bars in panel.bars.items()}
    cache: dict[tuple[str, date, date, bool], float | None] = {}

    def ret(symbol: str, a: date, b: date, trailing: bool = False) -> float | None:
        key = (symbol, a, b, trailing)
        if key not in cache:
            how = trailing_return if trailing else span_return
            cache[key] = how(panel.bars.get(symbol, []), dates.get(symbol, []), a, b)
        return cache[key]

    def excess(m: str, a: date, b: date, trailing: bool = False) -> float | None:
        """`m`'s return minus its industry's (3 or more peers), else the other mains' (3 or more)."""
        own = ret(m, a, b, trailing)
        if own is None:
            return None
        bench = [x for p in panel.peers.get(m, []) if (x := ret(p, a, b, trailing)) is not None]
        if len(bench) < MIN_INDUSTRY:
            bench = [x for o in panel.mains if o != m and (x := ret(o, a, b, trailing)) is not None]
            if len(bench) < MIN_INDUSTRY:
                return None
        return own - sum(bench) / len(bench)

    days = sorted({d for m in panel.mains for d in dates.get(m, []) if first <= d <= last})
    obs: Obs = {}
    for t in days:
        today = []
        for m in panel.mains:
            own = own_return(panel.bars.get(m, []), dates.get(m, []), t, h)
            if own is None:
                continue
            exit_day = own[1]
            if (y := excess(m, t, exit_day)) is None:
                continue
            x = lookback(panel.daily.get(m, {}), t, lb, relations)
            if MOMENTUM in names:
                x[MOMENTUM] = excess(m, t - timedelta(days=lb), t, trailing=True) or 0.0
            today.append((x, y, exit_day))
        if today:
            obs[t] = today
    ics: dict[str, dict[date, float]] = {f: {} for f in names}
    for t, rows in obs.items():
        ys = [y for _, y, _ in rows]
        for f in names:
            if (ic := spearman([x[f] for x, _, _ in rows], ys)) is not None:
                ics[f][t] = ic
    return obs, ics


def trailing_fit(obs: Obs, ics: dict[str, dict[date, float]], start: date, names: Sequence[str]) -> dict[str, float]:
    """`names`' weights for a segment starting on `start`: fitted on the FIT_DAYS before it, only
    on decision days whose every exit (plus the peers' slack) came before it."""
    train = [t for t in obs if start - timedelta(days=FIT_DAYS) <= t < start
             and max(x[2] for x in obs[t]) + timedelta(days=PEER_SLACK_DAYS) < start]
    return fit_weights({f: [ics[f][t] for t in train if t in ics[f]] for f in names})


def horizon_result(panel: Panel, h: int, lb: int, spans: Sequence[tuple[date, date]], relations: Sequence[str],
                   cov: Coverage | None) -> HorizonResult:
    """The test at one horizon over `spans`, reading only `panel`."""
    in_score, secondary = _features(relations)
    names = in_score + secondary
    obs, ics = observations(panel, h, lb, spans[0][0] - timedelta(days=FIT_DAYS), spans[-1][1], relations, names)
    out_segments, counted_ics = [], []
    counted_base: dict[str, list[tuple[date, float]]] = {b: [] for b in BASELINES}
    for s, e in spans:
        w = trailing_fit(obs, ics, s, in_score)
        signs = {b: trailing_fit(obs, ics, s, [b])[b] for b in BASELINES}
        seg = []
        if any(w.values()):
            for t in sorted(x for x in obs if s <= x <= e):
                rows = obs[t]
                score = combine({f: [x[f] for x, _, _ in rows] for f in in_score}, w)
                if (ic := spearman(score, [y for _, y, _ in rows])) is not None:
                    seg.append((t, ic))
        why = ("no trailing fit" if not any(w.values()) else _coverage_why(cov, s, e)
               or (f"{len(seg)} IC days (needs {MIN_DAYS})" if len(seg) < MIN_DAYS else None))
        vals = [ic for _, ic in seg]
        out_segments.append(SegmentRow(s, e, _r(mean(vals)), _r(newey_west_t(vals, h)), len(vals), why is None, why,
                                       {f: round(v, 4) for f, v in w.items()}))
        if why is None:
            counted_ics += seg
            for b in BASELINES:
                counted_base[b] += [(t, signs[b] * ics[b].get(t, 0.0)) for t, _ in seg]
    counted = [x for x in out_segments if x.counted]
    features = []
    for f in names:
        vals, positive = [], 0
        for x in counted:
            seg = [ic for t, ic in sorted(ics[f].items()) if x.start <= t <= x.end]
            vals += seg
            positive += bool(seg) and sum(seg) > 0
        features.append(FeatureIC(f, _r(mean(vals)), _r(newey_west_t(vals, h)), len(vals), positive, f in in_score))
    vals = [ic for _, ic in sorted(counted_ics)]
    ic, t = mean(vals), newey_west_t(vals, h)
    v = verdict(ic, t, [SegmentIC(x.start, x.end, x.ic, x.t, x.days, x.counted, x.why) for x in out_segments])
    fitted = {}
    for b in BASELINES:
        series = [x for _, x in sorted(counted_base[b])]
        b_ic = mean(series)
        fitted[b] = (_r(b_ic), _r(newey_west_t(series, h)), None if ic is None or b_ic is None else ic > b_ic,
                     next(f.ic for f in features if f.name == b))
    return HorizonResult(h, lb, _r(ic), _r(t), len(vals), sum(len(x) for x in obs.values()), tuple(out_segments),
                         tuple(features), v.label, v.passed, v.positive, v.counted, *fitted[BASELINE], *fitted[MOMENTUM])


def _r(x: float | None) -> float | None:
    return None if x is None else round(x, 4)


def run_laya_test(ctx: AppContext, watchlist: str = DEFAULT_LIST, *, sensitivity: bool = False,
                  start: date = TEST_START, end: date | None = None, model: str = MODEL_TAG,
                  on_progress: Callable[[str], None] | None = None) -> LayaTest:
    """The test as pre-registered, over the stored scores of `model`; reads only."""
    say = on_progress or (lambda _: None)
    end = end or ctx.clock.utcnow().date()
    spans = segments(start, end)
    if not spans:
        raise InvalidQuery(f"{start.isoformat()} to {end.isoformat()} is shorter than half a segment")
    relations = SENSITIVITY_RELATIONS if sensitivity else MAIN_RELATIONS
    panel = build_panel(ctx, watchlist, sensitivity=sensitivity, start=start, end=end, model=model, say=say)
    say("coverage")
    cov = coverage(ctx, watchlist)
    results = []
    for h, lb in HORIZONS:
        say(f"{h}-day horizon")
        results.append(horizon_result(panel, h, lb, spans, relations, cov))
    labels = [r.verdict for r in results]
    overall = "PASS" if all(x == "PASS" for x in labels) else "FAIL" if "FAIL" in labels else "INCONCLUSIVE"
    run = SENSITIVITY if sensitivity else MAIN
    return LayaTest(run, RUN_LABELS[run], model, start, end, len(panel.mains), panel.articles, tuple(results),
                    overall, overall == "PASS", to_utc_iso(ctx.clock.utcnow()))


def keep_laya_test(ctx: AppContext, test: LayaTest) -> LayaTest:
    new_id = LayaTestRepo(ctx.conn).add(test.run, test.model, test.verdict, json.dumps(asdict(test), default=str),
                                        test.run_at)
    return _load(json.dumps(asdict(test), default=str), new_id)


def laya_tests(ctx: AppContext) -> list[LayaTest]:
    """The latest kept result of each run (main first)."""
    rows = LayaTestRepo(ctx.conn).latest()
    return sorted((_load(r.result, r.id) for r in rows), key=lambda t: t.run != MAIN)


def latest_verdict(ctx: AppContext, model: str = MODEL_TAG, run: str = MAIN) -> LayaTest | None:
    """The newest kept result of `run` for `model`, if any."""
    row = LayaTestRepo(ctx.conn).latest_for(run, model)
    return _load(row.result, row.id) if row else None


def _load(raw: str, test_id: int) -> LayaTest:
    d = json.loads(raw)
    horizons = tuple(HorizonResult(**{**h, "segments": tuple(SegmentRow(**{**s, "start": date.fromisoformat(s["start"]),
                                                                           "end": date.fromisoformat(s["end"])})
                                                              for s in h["segments"]),
                                      "features": tuple(FeatureIC(**f) for f in h["features"])})
                     for h in d["horizons"])
    return LayaTest(**{**d, "start": date.fromisoformat(d["start"]), "end": date.fromisoformat(d["end"]),
                       "horizons": horizons, "id": test_id})
