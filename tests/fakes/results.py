"""A small literature index and scoreboard covering every way a method can stand in the results table
(research-lab 06): a candidate with a note, deaths at triage, power and confirm, one with no recorded
stage, a dropped one, one at build, one past power, and tests no method names."""

from oracle.services import results_service
from oracle.services.lab_service import Scoreboard, ScoreRow
from oracle.services.literature_service import SEED_KEY, open_index
from oracle.services.testbed_service import research_context
from oracle.store.literature import LiteratureRepo
from oracle.store.meta import MetaRepo
from oracle.store.triage import TriageRepo

AT = "2026-10-04T12:00:00+00:00"
HASH = "a" * 40


def _row(key, label, kind, horizon, measure, value, t, verdict, **kw) -> ScoreRow:
    return ScoreRow(key, label, kind, "2026-09-20", horizon, measure, value, t, kw.pop("bar", "t > 2.00"), verdict,
                    kw.pop("power", None), kw.pop("legacy", True), kw.pop("commit_hash", HASH),
                    kw.pop("run_at", "2026-10-01T09:00:00+00:00" if value is not None else None), kw.pop("note", ""),
                    **kw)


BOARD = Scoreboard((
    _row("momentum_12_1", "Momentum 12-1", "signal", 20, "IC", 0.0071, 0.45,
         "FAIL (could not see an IC this small: needed 0.0158)", power=0.0158, train=0.0123, turnover=0.31,
         break_even=0.0042),
    _row("forward:momentum_12_1", "Momentum 12-1 forward", "signal", 20, "", None, None, "not run"),
    _row("new_sig", "A new signal", "signal", 20, "", None, None, "not committed: no data run yet", bar="t >= 3.02",
         legacy=False, commit_hash=None),
    _row("event:1.01", "Results day", "event", 20, "mean CAR", 0.0041, 3.4, "PASS", train=0.0032),
    _row("event:1.01", "Results day", "event", 60, "mean CAR", 0.0050, 2.1, "PASS", train=0.0040),
    _row("trend_overlay", "Trend overlay", "strategy", None, "CAGR over buy and hold", -0.0331, None, "FAIL",
         bar="worst fall at most two thirds of buy and hold's", train=0.004, turnover=0.8, dsr=0.12),
), 20, 3.04)


def build(ctx, monkeypatch) -> None:
    """The index (unseeded) and the scoreboard the results table reads."""
    monkeypatch.setattr(results_service, "scoreboard", lambda c: BOARD)
    with research_context(ctx) as r:
        MetaRepo(r.conn).set(SEED_KEY, "test")
    with open_index(ctx) as r:
        lit, tri = LiteratureRepo(r.conn), TriageRepo(r.conn)
        good = lit.add_source("Returns to buying winners", "Jegadeesh and Titman", 1993, "replicated",
                              "https://doi.org/10.1111/j.1540-6261.1993.tb04702.x", None, "", AT, "seed")
        blog = lit.add_source("A blog on headlines", "", None, "practitioner", "https://example.com/post", None, "",
                              AT, "seed")
        wp = lit.add_source("Profitability", "Novy-Marx", 2013, "working-paper", None, None, "", AT, "seed")

        def method(ref, name, status, family="A. Cross-sectional stock anomalies", origin="archive", **kw):
            sources = kw.pop("sources", ())
            new = lit.add_method(ref, name, status, origin, AT, "seed", family=family, **kw)
            lit.link(new, sources)
            return new

        a = method("A-01", "LLM headline sentiment", "new", family="1. LLM and NLP on text", rating=3,
                   evidence="GPT scores predict next-day returns.", sources=(blog, good))
        lit.set_status(a, "go", "go", "user", AT, "worth a look")
        mom = method("T-13", "Residual momentum", "go", sources=(good,))
        tri.set_map(mom, "Mom12m", "user", AT)
        tri.record(mom, "triage", "stop", "OSAP Mom12m: large caps since 2000 +0.30%/month t 1.00", {"signal": "Mom12m"},
                   "go", "fail", "user", AT)
        gp = method("T-07", "Gross profitability", "go", sources=(wp,))
        tri.record(gp, "triage", "pass", "OSAP GP: survives", {}, "go", "power", "user", AT)
        tri.record(gp, "power", "stop", "could not see: expected IC 0.0050 at 20 d", {"expected_ic": 0.005}, "power",
                   "not-testable", "user", AT)
        ag = method("T-08", "Asset growth", "go")
        tri.record(ag, "triage", "pass", "OSAP AssetGrowth: survives", {}, "go", "power", "user", AT)
        tri.record(ag, "power", "pass", "expected IC 0.0300 at 20 d", {}, "power", "power", "user", AT)
        method("X-01", "Momentum 12-1 (h20)", "fail", family="Already tested (signal)", origin="tested",
               result="IC +0.008 t 0.50, could not see", test_keys=("momentum_12_1", "forward:momentum_12_1"))
        method("X-11", "4B LLM regime tilt", "fail", family="Already tested (strategy)", origin="tested", result="noise")
        method("A-02", "Earnings call tone", "dropped", family="1. LLM and NLP on text")
        method("M-001", "A new signal", "built", test_keys=("new_sig", "gone_key"))
