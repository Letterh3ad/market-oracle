"""The live regime line accounts: each step builds one regime book from stored bars (the traded
listings and every watched or held company, for the mood) and trades its leans under the strategy's
rules, logging each decision with what its lean was made of and saving the day's reading for
the Ledgers card. `regime` trades the rules weights; `regime-ai` the same book with the daily
model tilt (`regime_tilt`), so the two records measure what the model adds (frozen since the
core-sat line: it no longer steps); `regime-v2` the book with no hold padding and a rotation out
of stale holdings (`broker.regime.v2_settings`); `core-sat` the regime book as the satellite of an
equal-weight core (`core_sat_service`), each sleeve in its own cash, Laya's daily score in the
satellite only at the weight its kept verdict gives; `laya` the book weighing Laya's score alone
(`laya_line`), for evidence whatever the verdict."""

from __future__ import annotations

import dataclasses
from collections.abc import Collection, Mapping
from dataclasses import asdict, dataclass
from datetime import date, timedelta

from oracle.broker.orders import Action
from oracle.broker.regime import (
    LABEL_SCORES, ROTATION, SIGN_INFO, DatedScores, Explained, RegimeBook, RegimeInputs, RegimeState, Tilt, regime_book,
    rotation_from, settings_from, v2_settings,
)
from oracle.broker.rules import Decision, Lean
from oracle.research.llm import ChatModel
from oracle.services.context import AppContext
from oracle.services.core_sat_service import (
    core_card, core_listings, core_step, satellite_inputs, satellite_settings, satellite_traded, sleeves,
)
from oracle.services.laya_line import (
    METHOD, LayaInput, laya_input, laya_live, laya_only, laya_verdict, verdict_note, with_laya,
)
from oracle.services.laya_test_service import TEST_START
from oracle.services.models_service import slot_profile
from oracle.services.paper_service import (
    CORE_SAT, GONE, LAYA, LINE_NAMES, REGIME, REGIME_AI, REGIME_V2, account_on, discovered_off, frozen, step_account, traded_by,
    verdict_history,
)
from oracle.services.prices_service import ConvertedBar
from oracle.services.regime_lines import CORE_SAT_SOURCE, HISTORY_DAYS, market_history, regime_inputs
from oracle.services.regime_tilt import DailyTilt
from oracle.services.watch_service import discovered_only
from oracle.store.db import to_utc_iso
from oracle.store.paper import DecisionRepo, LoggedDecision, PaperRepo
from oracle.store.regime import RegimeStateRepo, StoredTilt, TiltRepo
from oracle.store.universe import UniverseRepo

__all__ = ["CORE_SAT_OFF", "LAYA_OFF", "NO_LAYA_HELD", "laya_card", "laya_traded", "FROZEN_NOTE", "OFF", "off_note", "REGIME", "REGIME_AI", "REGIME_AI_OFF", "REGIME_OFF", "REGIME_V2", "REGIME_V2_OFF", "LineStep", "RegimeCard", "RegimeRun",
           "build_book", "lag_warning", "regime_card", "regime_step"]

REGIME_OFF = ("the regime account is off: nothing was decided. Turn it on on the Ledgers tab or in Settings "
              "([regime_account] enabled)")
REGIME_AI_OFF = ("the regime-ai account is off: nothing was decided. Turn it on on the Ledgers tab or in Settings "
                 "([regime_ai_account] enabled)")
FROZEN_NOTE = ("the {} account is frozen: it no longer steps or records equity, and its history and holdings stay "
               "as they were (its model tilt measured as noise, not an edge)")
REGIME_V2_OFF = ("the regime-v2 account is off: nothing was decided. Turn it on on the Ledgers tab or in Settings "
                 "([regime_v2_account] enabled)")
CORE_SAT_OFF = ("the core-sat account is off: nothing was decided. Turn it on on the Ledgers tab or in Settings "
                "([core_sat_account] enabled)")
LAYA_OFF = ("the laya account is off: nothing was decided. Turn it on on the Ledgers tab or in Settings "
            "([laya_account] enabled)")
OFF = {REGIME: REGIME_OFF, REGIME_AI: REGIME_AI_OFF, REGIME_V2: REGIME_V2_OFF, CORE_SAT: CORE_SAT_OFF, LAYA: LAYA_OFF}
DISCOVERED_OFF = discovered_off("the regime account")
DISCOVERED = {REGIME: DISCOVERED_OFF, REGIME_AI: discovered_off("the regime-ai account"),
              REGIME_V2: discovered_off("the regime-v2 account"), CORE_SAT: discovered_off("the core-sat account"),
              LAYA: discovered_off("the laya account")}
LAG_WEEKDAYS = 2
"""A step day more weekdays than this behind yesterday means a source has stopped: the step keeps
trading an old day's leans (gate 04 #3), so the card and the tick say so."""
NO_MOOD = "no market mood yet: under a year of stored market history, so the regime line leans nothing"
NO_SCORE = "no regime score: too little history for any price method"
NOT_CORE = "not one of the main list's companies: core-sat's satellite trades only those, as its backtest does"
NOT_MAIN = "not one of the main list's companies: the laya account trades only those Laya's score ranks"
NO_LAYA = ("no Laya score within 7 days: no news fit for its segment yet, or it is not its company's primary "
           "listing")
NO_LAYA_HELD = ("held with no Laya score within 7 days (left the main list, not its company's primary listing, or "
                "no news fit): sold, as Laya alone decides this account")
STALE_DAYS = 7
"""A listing whose last bar is this far behind the market's is not leaned on: its prices are stale."""
COMPLETE_SHARE = 0.8
"""A trailing stored day on which fewer of the traded listings have a bar than this share of the
recent busiest day is an exchange holiday or a failed fetch (today is never read, so no session in
it is still open): the step reads up to the day before it. Only the traded listings count: the
rest of the stored market (a replay study's, ones no longer watched) stops getting bars and would
make every day look partial."""
RECENT_DAYS = 10


@dataclass(frozen=True)
class RegimeRun:
    day: date | None
    """The market day read: the last complete stored session before today."""
    mood: str | None
    flags: tuple[str, ...]
    decisions: list[Decision]
    account: str = REGIME
    tilt: StoredTilt | None = None
    """regime-ai: the step day's tilt, or why there was none."""
    lag: str | None = None
    """Set when the step day is well behind yesterday."""


@dataclass(frozen=True)
class LineStep:
    """A regime line's step run as a job: None while the account is off."""
    account: str
    run: RegimeRun | None


@dataclass(frozen=True)
class RegimeCard:
    account: str
    enabled: bool
    note: str | None
    """Why the card has less to show: off, never stepped, or no mood on the last step."""
    day: date | None
    at: str | None
    """When the step that read `state` ran."""
    state: dict | None
    decisions: tuple[LoggedDecision, ...]
    lag: str | None = None
    tilt: dict | None = None
    """regime-ai: the card day's stored tilt (multipliers, the rules weights it was given,
    reason or error, model)."""
    model: str | None = None
    """regime-ai: `[regime_ai_account] model` as set ("" the research slot's)."""
    research_profile: str | None = None
    """regime-ai: the research slot's profile, which "" means."""
    rotation: dict | None = None
    """regime-v2: its rotation settings (min_hold, margin, most)."""
    rotated_last_step: int | None = None
    """regime-v2: rotation sales on its most recent decision day, whatever `decisions`' limit is."""
    frozen: bool = False
    """The account no longer steps (regime-ai); its card and history stay."""
    sleeves: dict | None = None
    """core-sat: each sleeve's cash, holdings and positions, and the core's next rebalance."""
    laya: dict | None = None
    """core-sat and laya: Laya's kept verdict and the weight it gives in core-sat's satellite."""


def off_note(account: str) -> str:
    """Why the account decided nothing: frozen, or switched off."""
    return FROZEN_NOTE.format(account) if frozen(account) else OFF[account]


def build_book(ctx: AppContext, history: Mapping[str, list[ConvertedBar]], inputs: RegimeInputs,
               tilt: Tilt | None = None, account: str = REGIME, laya: LayaInput | None = None) -> RegimeBook:
    """The regime book over `history`, with the `[regime]` settings and the strategy's limits;
    on regime-v2 also `[regime_v2]`'s, its leans made in its own name. core-sat's satellite reads
    `laya` at its verdict's weight; the laya account weighs it alone."""
    settings = settings_from(asdict(ctx.config.regime), max_positions=ctx.config.strategy.max_positions,
                             min_probability=ctx.config.strategy.min_probability)
    if account == REGIME_V2:
        return regime_book(history, inputs=inputs, settings=v2_settings(settings, asdict(ctx.config.regime_v2)),
                           tilt=tilt, source=REGIME_V2)
    if account == CORE_SAT:
        return regime_book(history, inputs=satellite_inputs(inputs, laya), settings=satellite_settings(settings, laya),
                           source=CORE_SAT_SOURCE)
    if account == LAYA:
        scores = laya.scores if laya else {}
        return regime_book(history, inputs=_with_scores(inputs, scores),
                           settings=laya_only(settings), source=LAYA)
    return regime_book(history, inputs=inputs, settings=settings, tilt=tilt)


def regime_step(ctx: AppContext, account: str = REGIME, model: ChatModel | None = None) -> RegimeRun | None:
    """The regime book's leans traded under the strategy's rules on `account` (a regime line);
    None, deciding nothing, while it is off. Reads only sessions before today (UTC): a bar is not
    known to be final until its exchange has closed, and bars carry no closing time, so today's
    are never read, whatever time the tick runs. On regime-ai the book is tilted by the day's
    model answer (`model` stands in for the configured one)."""
    if not account_on(ctx, account):
        return None
    today = ctx.clock.utcnow().date()
    last = today - timedelta(days=1)
    traded = traded_by(ctx, account)
    leaned = (satellite_traded(ctx) if account == CORE_SAT else laya_traded(ctx) if account == LAYA else traded)
    history, day = complete_sessions(
        market_history(ctx, last - timedelta(days=HISTORY_DAYS), last, {*traded, *leaned}), traded)
    tilt = DailyTilt(ctx, account, day, ctx.config.regime.tilt_cap, model) if account == REGIME_AI and day else None
    laya = _laya(ctx, account, leaned, day)
    book = build_book(ctx, history, regime_inputs(ctx, history, today, _verdicts(ctx, traded)), tilt, account, laya)
    state = book.state(day) if day else None
    sleeve, core = (sleeves(ctx)[1], core_step(ctx, day, history) if day else []) if account == CORE_SAT else (None, [])
    found, skips, why = _leans(ctx, book, state, day, leaned, history, NO_LAYA if account == LAYA else NO_SCORE)
    if account == LAYA and state is not None:
        found, skips = _sell_unscored(ctx, day, found, skips)
    outside = NOT_CORE if account == CORE_SAT else NOT_MAIN
    skips += [Decision(s, Action.SKIP, None, outside) for s in traded if s not in leaned]
    skips += [Decision(s, Action.SKIP, None, DISCOVERED[account]) for s in discovered_only(ctx) if s not in traded]
    decisions = core + step_account(ctx, account, found, skips, {}, why, book.cap, sleeve)
    if day is not None:
        reading = ({**_plain(state), "unscored": _unscored(state, why), "signs": _signs(state)} if state
                   else {"mood": None, "note": NO_MOOD})
        RegimeStateRepo(ctx.conn).save(account, day, reading, to_utc_iso(ctx.clock.utcnow()))
    return RegimeRun(day, state.mood if state else None, tuple(sorted(state.flags)) if state else (), decisions,
                     account, tilt.today if tilt else None, lag_warning(day, today) if day else None)


def _with_scores(inputs: RegimeInputs | None, scores: Mapping[str, DatedScores]) -> RegimeInputs:
    base = inputs or RegimeInputs()
    return dataclasses.replace(base, extra={**base.extra, METHOD: scores})


def laya_traded(ctx: AppContext) -> list[str]:
    """What the laya account leans on: the main list's companies (never a fund or Tesla), as Laya's
    score ranks them, plus what it holds, so a holding is never stranded (one with no score is sold)."""
    held = PaperRepo(ctx.conn).held_symbols(LAYA)
    return sorted(set(core_listings(ctx)) | set(held))


def _laya(ctx: AppContext, account: str, listings: list[str], day: date | None) -> LayaInput | None:
    """Laya's scores over the book's history window, the step day's being the score for each
    listing's fill (`laya_line.laya_live`), for the laya account always and for core-sat only when
    its verdict gives weight (otherwise just the verdict)."""
    if account not in (CORE_SAT, LAYA):
        return None
    if day is None or (account == CORE_SAT and not laya_verdict(ctx).useful):
        return laya_input(ctx, listings, TEST_START, TEST_START, scores=False)
    return laya_live(ctx, listings, max(TEST_START, day - timedelta(days=HISTORY_DAYS)), day, ctx.clock.utcnow())


def _sell_unscored(ctx: AppContext, day: date, found: dict[str, Lean],
                   skips: list[Decision]) -> tuple[dict[str, Lean], list[Decision]]:
    """The laya account's holdings without a Laya score lean sell: Laya alone decides it, so with no
    score there is nothing to hold them on (left the main list, not the primary listing, or no fit)."""
    held = set(PaperRepo(ctx.conn).held_symbols(LAYA))
    unscored = {d.symbol for d in skips if d.symbol in held and d.reason == NO_LAYA}
    sells = {s: Lean(s, day, "sell", 1.0, LAYA, note=NO_LAYA_HELD) for s in unscored}
    return {**found, **sells}, [d for d in skips if d.symbol not in unscored]


def lag_warning(day: date, today: date) -> str | None:
    """Why a step day far behind yesterday is a problem, when it is."""
    yesterday = today - timedelta(days=1)
    behind = sum(1 for k in range(1, (yesterday - day).days + 1) if (day + timedelta(days=k)).weekday() < 5)
    if behind <= LAG_WEEKDAYS:
        return None
    return (f"the step read {day.isoformat()}, {behind} weekdays behind yesterday: under "
            f"{COMPLETE_SHARE:.0%} of the traded listings have a bar on any later day, so it trades old leans. "
            "Update prices and check the price sources")


def _signs(state: RegimeState) -> dict[str, list[dict]]:
    """Each firing warning and good sign per method, with its reason and source, for the card."""
    out: dict[str, list[dict]] = {}
    for found, warning in ((state.warnings, True), (state.good, False)):
        for method, names in found.items():
            for name in names:
                sign = SIGN_INFO.get((method, name))
                out.setdefault(method, []).append({"name": name, "warning": warning,
                                                   "reason": sign.reason if sign else "",
                                                   "source": sign.source if sign else ""})
    return out


def _unscored(state: RegimeState, why: Mapping[str, dict]) -> list[str]:
    """Weighted methods no traded listing had a score from, so the card can say its weight went
    to the others (earnings drift, while no earnings dates are stored)."""
    if not why:
        return []
    scored = {m for x in why.values() for m in x["parts"]}
    return sorted(m for m, w in state.weights.items() if w > 0 and m not in scored)


def complete_sessions(history: Mapping[str, list[ConvertedBar]],
                      traded: Collection[str]) -> tuple[dict[str, list[ConvertedBar]], date | None]:
    """`history` without its trailing days partial among `traded`, and the last complete day
    (None without bars for any traded listing)."""
    counts: dict[date, int] = {}
    for symbol in traded:
        for c in history.get(symbol, ()):
            counts[c.bar.date] = counts.get(c.bar.date, 0) + 1
    days = sorted(counts)
    busiest = max((counts[d] for d in days[-RECENT_DAYS:]), default=0)
    while days and counts[days[-1]] < COMPLETE_SHARE * busiest:
        days.pop()
    if not days:
        return {}, None
    last = days[-1]
    kept = {s: [c for c in rows if c.bar.date <= last] for s, rows in history.items()}
    return {s: rows for s, rows in kept.items() if rows}, last


def _leans(ctx: AppContext, book: RegimeBook, state: RegimeState | None, day: date | None, traded: list[str],
           history: Mapping[str, list[ConvertedBar]],
           no_score: str = NO_SCORE) -> tuple[dict[str, Lean], list[Decision], dict[str, dict]]:
    universe = UniverseRepo(ctx.conn)
    found, skips, why = {}, [], {}
    for symbol in traded:
        rows = history.get(symbol)
        if universe.resolve(symbol) is None:
            skips.append(Decision(symbol, Action.SKIP, None, GONE))
        elif not rows:
            skips.append(Decision(symbol, Action.SKIP, None, "no prices stored: update its prices"))
        elif state is None:
            skips.append(Decision(symbol, Action.SKIP, None, NO_MOOD))
        elif (last := rows[-1].bar.date) < day - timedelta(days=STALE_DAYS):
            skips.append(Decision(symbol, Action.SKIP, None, f"no price since {last.isoformat()}: update its prices"))
        elif (explained := book.explain(symbol, day)) is None:
            skips.append(Decision(symbol, Action.SKIP, None, no_score))
        else:
            found[symbol] = explained.lean
            why[symbol] = _explanation(explained)
    return found, skips, why


def _explanation(x: Explained) -> dict:
    """The mood and every method's weighted part of the score, largest first."""
    ranked = sorted(x.parts.items(), key=lambda kv: (-abs(kv[1]), kv[0]))
    return {"mood": x.state.mood, "flags": sorted(x.state.flags), "label": x.lean.label,
            "probability": x.lean.probability, "score": x.score, "parts": dict(ranked)}


def _verdicts(ctx: AppContext, traded: list[str]) -> dict[str, DatedScores]:
    return {s: tuple((d, LABEL_SCORES[label]) for d, label in found)
            for s, found in verdict_history(ctx, traded).items()}


def _plain(value):
    """A regime reading as JSON: sets sorted, days as ISO text, mappings as objects."""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {f.name: _plain(getattr(value, f.name)) for f in dataclasses.fields(value)}
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (set, frozenset)):
        return sorted(_plain(v) for v in value)
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    return value


def regime_card(ctx: AppContext, limit: int = 20, account: str = REGIME) -> RegimeCard:
    """The last step's regime reading and the account's recent decisions with their explanations;
    on regime-ai also that day's tilt and the model that gives it."""
    enabled = account_on(ctx, account)
    stored = RegimeStateRepo(ctx.conn).latest(account)
    decisions = tuple(DecisionRepo(ctx.conn).recent(account, limit))
    if frozen(account) and stored is not None:
        note = f"{off_note(account)}. Its last reading, on {stored.day.isoformat()}, is below"
    elif not enabled:
        note = off_note(account)
    elif stored is None:
        note = f"no reading yet: it is read at each tick's {account} step, or press {LINE_NAMES[account]} step"
    else:
        note = stored.state.get("note")
    card = RegimeCard(account, enabled, note, stored.day if stored else None, stored.at if stored else None,
                      stored.state if stored else None, decisions,
                      lag_warning(stored.day, date.fromisoformat(stored.at[:10])) if stored and enabled else None,
                      frozen=frozen(account))
    if account == CORE_SAT:
        return dataclasses.replace(card, sleeves=core_card(ctx), laya=laya_card(ctx))
    if account == LAYA:
        return dataclasses.replace(card, laya=laya_card(ctx))
    if account == REGIME_V2:
        rotated = DecisionRepo(ctx.conn).count_on_last_day(account, ROTATION)
        return dataclasses.replace(card, rotation=asdict(rotation_from(asdict(ctx.config.regime_v2))),
                                   rotated_last_step=rotated)
    if account != REGIME_AI:
        return card
    tilt = TiltRepo(ctx.conn).get(account, stored.day) if stored else None
    return dataclasses.replace(card, tilt=_plain(tilt) if tilt else None, model=ctx.config.regime_ai_account.model,
                               research_profile=slot_profile(ctx.config, "research"))


def laya_card(ctx: AppContext) -> dict:
    """Laya's kept verdict and the weight it gives in core-sat's satellite, per mood."""
    settings = settings_from(asdict(ctx.config.regime), max_positions=ctx.config.strategy.max_positions,
                             min_probability=ctx.config.strategy.min_probability)
    v = laya_verdict(ctx)
    weights = {m: round(r[METHOD], 4) for m, r in with_laya(settings, v.useful).weights.items()}
    return {**asdict(v), "weights": weights, "note": verdict_note(v, settings)}
