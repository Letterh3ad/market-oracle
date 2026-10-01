"""The live laya account: the regime book weighing Laya's daily score alone, on the main list's
companies, for evidence whatever its test said; and core-sat's satellite reading Laya only at
the weight its kept verdict gives."""

from dataclasses import replace
from datetime import date, timedelta

from oracle.broker.regime import RegimeInputs
from oracle.services.laya_line import LayaInput, LayaVerdict
from oracle.broker.orders import Side
from oracle.services.paper_service import ACCOUNTS, LINE_NAMES, TRADERS, account_view, decision_log, fill_pending
from oracle.services.regime_service import LAYA_OFF, NO_LAYA, NO_LAYA_HELD, NOT_MAIN, build_book, regime_card, regime_step
from oracle.services.core_sat_service import SAT_SOURCE, satellite_traded
from oracle.services.regime_service import laya_traded
from oracle.services.watch_service import watch
from oracle.store.paper import FillRow, PaperRepo
from tests.broker.test_backtest import rising
from tests.fakes.regime import use_stub
from tests.services.test_paper import clock, ctx, next_day, seed, yahoo  # noqa: F401
from tests.services.test_paper_discovered import ai_trades, found
from tests.services.test_regime_account import PARTS

ACCOUNT = "laya"
FAILED = LayaVerdict("FAIL", False, "laya", 7, "2026-10-01T00:00:00+00:00", "FAIL: weight 0")
USEFUL = LayaVerdict("PASS", True, "laya", 8, "2026-10-01T00:00:00+00:00", "passed and beat both baselines")


def asked(monkeypatch, verdict=FAILED, scored=None):
    """Laya's input as the step reads it, recording each read: ("verdict",) for the verdict alone,
    ("live", listings, first, day, now) for scores."""
    calls = []

    def verdict_only(ctx, listings, first, last, *, scores=True, **_):
        assert not scores
        calls.append(("verdict",))
        return LayaInput({}, verdict)

    def live(ctx, listings, first, day, now, **_):
        calls.append(("live", sorted(listings), first, day, now))
        return LayaInput(dict(scored or {}), verdict)

    monkeypatch.setattr("oracle.services.regime_service.laya_input", verdict_only)
    monkeypatch.setattr("oracle.services.regime_service.laya_live", live)
    monkeypatch.setattr("oracle.services.regime_service.laya_verdict", lambda ctx, model=None: verdict)
    monkeypatch.setattr("oracle.services.laya_line.laya_verdict", lambda ctx, model=None: verdict)
    return calls


def test_laya_is_listed_on_by_default_with_its_own_full_capital(ctx):
    assert ACCOUNT in ACCOUNTS and ACCOUNT in TRADERS and LINE_NAMES[ACCOUNT] == "Laya"
    assert ctx.config.laya_account.enabled is True
    assert account_view(ctx, ACCOUNT).cash == ctx.config.risk.account


def test_off_it_decides_nothing_and_says_so(ctx, monkeypatch):
    built = use_stub(monkeypatch, leans={"AAPL": ("buy", 0.9, {"laya": 0.6})})
    watch(ctx, "AAPL")
    ctx.config = replace(ctx.config, laya_account=replace(ctx.config.laya_account, enabled=False))
    assert regime_step(ctx, ACCOUNT) is None and built == [] and decision_log(ctx, ACCOUNT) == []
    card = regime_card(ctx, account=ACCOUNT)
    assert (card.enabled, card.note) == (False, LAYA_OFF) and "[laya_account]" in LAYA_OFF


def test_it_leans_on_the_main_lists_companies_with_laya_scores_over_the_books_history(ctx, monkeypatch):
    calls = asked(monkeypatch)
    built = use_stub(monkeypatch, leans={"AAPL": ("buy", 0.9, {"laya": 0.6})})
    seed(ctx, "AAPL", "hold", size_share=0.5)
    seed(ctx, "VOD.L", "hold", size_share=0.5)
    watch(ctx, "AAPL")
    ai_trades(ctx, True)
    found(ctx, "VOD.L")
    regime_step(ctx, ACCOUNT)
    now = ctx.clock.utcnow()
    [(kind, listings, first, day, at)] = calls
    assert (kind, listings, at) == ("live", ["AAPL"], now) and day < now.date()
    assert day == built[0].asked[0][1], "the step day's score is the one the book reads"
    assert first <= day - timedelta(days=1000), "scores over the book's history, so its health reads like the backtest's"
    assert built[0].account == ACCOUNT and built[0].laya.verdict == FAILED
    assert {o.symbol for o in account_view(ctx, ACCOUNT).pending} == {"AAPL"}
    reasons = {d.symbol: d.reason for d in decision_log(ctx, ACCOUNT)}
    assert reasons["VOD.L"] == NOT_MAIN


def test_a_company_without_a_laya_score_is_skipped_and_says_why(ctx, monkeypatch):
    asked(monkeypatch)
    use_stub(monkeypatch)
    watch(ctx, "AAPL")
    regime_step(ctx, ACCOUNT)
    assert [d.reason for d in decision_log(ctx, ACCOUNT)] == [NO_LAYA]


async def test_a_holding_without_a_laya_score_is_sold_on_the_next_step_saying_why(ctx, yahoo, clock, monkeypatch):
    asked(monkeypatch)
    use_stub(monkeypatch, leans={"AAPL": ("strong buy", 0.95, {"laya": 0.9})})
    seed(ctx, "AAPL", "hold", size_share=0.5)
    watch(ctx, "AAPL")
    regime_step(ctx, ACCOUNT)
    await next_day(ctx, yahoo, clock)
    fill_pending(ctx)
    [held] = account_view(ctx, ACCOUNT).positions
    use_stub(monkeypatch)
    regime_step(ctx, ACCOUNT)
    [sell] = [o for o in account_view(ctx, ACCOUNT).pending if o.side == Side.SELL]
    assert sell.symbol == held.symbol == "AAPL" and sell.shares is None, "the whole holding"
    assert decision_log(ctx, ACCOUNT)[0].reason == NO_LAYA_HELD and "sold" in NO_LAYA_HELD


def test_its_card_and_core_sats_carry_the_verdict_and_weight_0_on_a_fail(ctx, monkeypatch):
    asked(monkeypatch)
    for account in (ACCOUNT, "core-sat"):
        laya = regime_card(ctx, account=account).laya
        assert (laya["verdict"], laya["useful"], laya["test_id"]) == ("FAIL", False, 7)
        assert set(laya["weights"].values()) == {0.0} and laya["note"].endswith("weight 0 in core-sat")
    assert regime_card(ctx, account="regime").laya is None


def test_core_sat_reads_laya_scores_only_when_its_verdict_gives_weight(ctx, monkeypatch):
    calls = asked(monkeypatch)
    built = use_stub(monkeypatch)
    watch(ctx, "AAPL")
    regime_step(ctx, "core-sat")
    assert calls == [("verdict",)] and not built[-1].laya.verdict.useful
    calls = asked(monkeypatch, USEFUL, {"AAPL": ((date(2026, 9, 20), 0.5),)})
    regime_step(ctx, "core-sat")
    assert [c[0] for c in calls] == ["live"] and built[-1].laya.scores == {"AAPL": ((date(2026, 9, 20), 0.5),)}
    card = regime_card(ctx, account="core-sat").laya
    assert card["useful"] and card["weights"]["calm_up"] == 0.125 and "weighs in core-sat's satellite" in card["note"]


BARS = {s: rising(400, s, daily=d) for s, d in (("UP", 0.003), ("SLOW", 0.001), ("MKT", 0.002))}


def test_the_real_laya_book_leans_by_laya_alone_and_not_at_all_without_a_score(ctx):
    day = BARS["UP"][-1].date
    scores = {"UP": ((day - timedelta(days=1), -0.8),), "SLOW": ((day - timedelta(days=1), 0.9),)}
    book = build_book(ctx, BARS, RegimeInputs(), account=ACCOUNT, laya=LayaInput(scores, FAILED))
    up, slow = book.explain("UP", day), book.explain("SLOW", day)
    assert up.parts == {"laya": -0.8} and up.lean.label in ("sell", "strong sell") and up.lean.source == ACCOUNT
    assert slow.parts == {"laya": 0.9} and slow.lean.label == "strong buy"
    assert book.explain("MKT", day) is None
    stale = build_book(ctx, BARS, RegimeInputs(), account=ACCOUNT,
                       laya=LayaInput({"UP": ((day - timedelta(days=9), 0.9),)}, FAILED))
    assert stale.explain("UP", day) is None, "a score older than 7 days is none"


def test_the_real_core_sat_book_is_the_regime_one_unless_laya_is_useful(ctx):
    day = BARS["UP"][-1].date
    scores = {s: tuple((b.date, -1.0 if s == "UP" else 1.0) for b in bars) for s, bars in BARS.items()}
    plain = build_book(ctx, BARS, RegimeInputs(), account="core-sat")
    failed = build_book(ctx, BARS, RegimeInputs(), account="core-sat", laya=LayaInput(scores, FAILED))
    useful = build_book(ctx, BARS, RegimeInputs(), account="core-sat", laya=LayaInput(scores, USEFUL))
    assert failed.explain("UP", day) == plain.explain("UP", day) and "laya" not in plain.explain("UP", day).parts
    assert useful.explain("UP", day).parts["laya"] < 0


def test_every_listing_laya_and_the_satellite_lean_on_has_prices_in_the_narrowed_market(ctx, monkeypatch):
    asked(monkeypatch)
    built = use_stub(monkeypatch, leans={})
    watch(ctx, "AAPL")
    paper = PaperRepo(ctx.conn)
    for account, source in ((ACCOUNT, ACCOUNT), ("core-sat", SAT_SOURCE)):
        order = paper.add_order(account, "VOD.L", Side.BUY, None, 1, "2026-09-21T14:00:00Z", date(2026, 9, 21), source)
        paper.record_fill(FillRow(order.id, account, "VOD.L", Side.BUY, date(2026, 9, 21), 1, 100.0, "GBP", 1.0,
                                  100.0, 0.0, 0.0, 0.0, 0.0))
    ctx.clock.advance(86400)  # the fixture's last session (2026-09-21) known final
    for account, leaned in ((ACCOUNT, laya_traded), ("core-sat", satellite_traded)):
        regime_step(ctx, account)
        assert set(leaned(ctx)) == {"AAPL", "VOD.L"} and set(leaned(ctx)) <= set(built[-1].history)
