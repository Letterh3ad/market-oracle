"""Triage and power (research-lab 05): OSAP imported from files, methods mapped to signals, each
stage run writing a row and moving the method's status, and the fetch held to the compute window."""

from datetime import datetime, timezone
from types import SimpleNamespace

import httpx
import pytest

from oracle.config import Config
from oracle.services import literature_service as lab
from oracle.services import triage_service as triage
from oracle.services.context import open_context
from oracle.services.errors import InvalidQuery
from tests.fakes.clock import FakeClock
from tests.fakes.osap import write_release

NOW = datetime(2026, 10, 4, 12, 0, tzinfo=timezone.utc)
REAL_OBSERVED_WINDOWS = triage.observed_windows
KEPT = [(20, 0.0079 / 0.50, 480.0, 1680), (20, 0.0086 / 0.87, 480.0, 1680), (5, 0.0036 / 0.53, 480.0, 1680)]
"""momentum_12_1, earnings_yield and reversal_1m's confirm windows as next-signals recorded them."""


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    monkeypatch.setattr(triage, "observed_windows", lambda c: KEPT)
    config = Config(db_path=tmp_path / "live.db", research_db_path=tmp_path / "research.db")
    with open_context(config, None, FakeClock(NOW)) as c:
        yield c


@pytest.fixture
def imported(ctx, tmp_path):
    triage.import_osap(ctx, write_release(tmp_path / "osap"))
    return ctx


def go(ctx, ref, signal):
    triage.map_method(ctx, ref, signal)
    lab.mark(ctx, ref, "go", tests=False)


def test_the_import_reads_the_published_figures_of_three_known_anomalies(imported):
    """SignalDoc's Return (%/month) and T-Stat are the original papers': Jegadeesh and Titman (1993,
    JF 48(1)) 12-month momentum 1.31 t 3.74 over 1964-1989; Novy-Marx (2013, JFE 108(1)) gross
    profitability, value-weighted quintiles, 0.31 t 2.49 over 1963-2010; Cooper, Gulen and Schill (2008,
    JF 63(4)) asset growth 1.73 t 8.45 over 1968-2003."""
    with triage.open_index(imported) as r:
        repo = triage.TriageRepo(r.conn)
        got = {s: repo.signal(s) for s in ("Mom12m", "GP", "AssetGrowth")}
    assert {s: (x.paper_return, x.paper_t, x.sample_start, x.sample_end, x.year) for s, x in got.items()} == {
        "Mom12m": (1.31, 3.74, 1964, 1989, 1993), "GP": (0.31, 2.49, 1963, 2010, 2013),
        "AssetGrowth": (1.73, 8.45, 1968, 2003, 2008)}
    assert got["GP"].weight == "VW" and got["GP"].quantile == 0.2 and got["Mom12m"].sign == 1.0
    status = triage.osap_status(imported)
    assert status.signals == {"Predictor": 4, "Placebo": 1}
    counts = {v.variant: v for v in status.variants}
    assert counts["op"].signals == 3 and counts["large"].signals == 3 and counts["large"].last == "2024-12"
    assert counts["op"].rows == 12 * ((2024 - 1964 + 1) + (2024 - 1963 + 1) + (2024 - 1968 + 1))
    assert [i.what for i in status.imports] == ["large", "op", "doc"]


def test_the_original_sample_reproduces_each_papers_mean_and_t(imported):
    """Checks the mechanics, not the published numbers: the series are shaped to equal them, so this
    proves only that the parse and split carry them through. The real release is checked after the
    download, in the compute window."""
    for ref, signal, mean, t in (("T-13", "Mom12m", 1.31, 3.74), ("T-07", "GP", 0.31, 2.49),
                                 ("T-08", "AssetGrowth", 1.73, 8.45)):
        go(imported, ref, signal)
        done = triage.run_triage(imported, ref)
        inside = done.detail["original"]["spans"][0]
        assert inside["label"] == "in sample"
        assert inside["mean"] == pytest.approx(mean) and inside["t"] == pytest.approx(t)


def test_a_method_without_a_large_cap_effect_since_2000_stops_at_triage(imported):
    go(imported, "T-13", "Mom12m")
    done = triage.run_triage(imported, "T-13")
    assert (done.outcome, done.status_from, done.status_to) == ("stop", "go", "fail")
    assert "large caps since 2000 +0.30%/month t 1.00 over 300 months" in done.summary
    assert lab.method(imported, "T-13", tests=False).status == "fail"
    split = done.detail["original"]
    assert [s["label"] for s in split["spans"]][2] == "after publication" and split["decay"] == pytest.approx(0.5)
    with pytest.raises(InvalidQuery, match="fail: triage takes a method marked go"):
        triage.run_triage(imported, "T-13")


def test_a_surviving_method_moves_to_power_and_a_small_ic_stops_as_could_not_see(imported):
    go(imported, "T-07", "GP")
    passed = triage.run_triage(imported, "T-07")
    assert (passed.outcome, passed.status_to) == ("pass", "power")
    seen = triage.run_power(imported, "T-07", horizon=20)
    d = seen.detail
    assert d["expected_ic"] == pytest.approx(triage.ic_from_spread(0.35, 0.2, 20), rel=1e-9)
    bar = triage.bonferroni_bar(20)
    assert d["bar"] == pytest.approx(bar) and d["same_horizon"] == 2
    assert d["detectable_ic"] == pytest.approx(bar * (0.0079 / 0.50 + 0.0086 / 0.87) / 2)
    assert d["formula_ic"] == pytest.approx(triage.formula_power(480, 1680, 20, bar))
    assert (seen.outcome, seen.status_to) == ("stop", "not-testable") and seen.summary.startswith("could not see")
    assert lab.method(imported, "T-07", tests=False).status == "not-testable"


def test_a_large_enough_expected_ic_passes_power_and_stays_ready_to_build(imported):
    go(imported, "T-07", "GP")
    triage.run_triage(imported, "T-07")
    done = triage.run_power(imported, "T-07", expected=0.08, source="a paper's IC")
    assert (done.outcome, done.status_to) == ("pass", "power") and done.detail["expected_from"] == "given"
    rows = {r.ref: r for r in triage.stage_table(imported)}
    assert rows["T-07"].triage.outcome == "pass" and rows["T-07"].power.outcome == "pass"
    assert rows["T-07"].signal == "GP" and rows["T-07"].runs == 2


def test_a_strategy_needs_its_tracking_error_and_is_judged_on_excess(imported):
    lab.mark(imported, "T-29", "go", tests=False)
    triage.run_triage(imported, "T-29", by_hand="pass", note="Lakonishok and Smidt 1988: turn of the month")
    with pytest.raises(InvalidQuery, match="tracking error"):
        triage.run_power(imported, "T-29", kind="strategy", expected=0.01)
    done = triage.run_power(imported, "T-29", kind="strategy", expected=0.01, tracking_error=0.05)
    assert done.outcome == "stop" and "could not see" in done.summary
    assert done.detail["detectable_excess"] == pytest.approx(
        triage.detectable_excess(0.05, done.detail["years"]), rel=1e-3)


@pytest.mark.parametrize("call, why", [
    (lambda c: triage.run_triage(c, "T-13"), "marked go"),
    (lambda c: (lab.mark(c, "T-13", "go", tests=False), triage.run_triage(c, "T-13")), "no OSAP signal"),
    (lambda c: (lab.mark(c, "T-13", "go", tests=False), triage.run_triage(c, "T-13", by_hand="pass")), "needs a note"),
    (lambda c: triage.map_method(c, "T-13", "NotASignal"), "not in OSAP"),
    (lambda c: triage.map_method(c, "Z-99", "Mom12m"), "no method"),
    (lambda c: triage.run_power(c, "T-13"), "passed triage"),
])
def test_out_of_order_or_incomplete_stage_runs_are_refused(imported, call, why):
    with pytest.raises(InvalidQuery, match=why):
        call(imported)


def test_triage_needs_the_large_cap_returns(ctx, tmp_path):
    folder = write_release(tmp_path / "osap")
    (folder / "PredictorAltPorts_LiqScreen_ME_gt_NYSE20pct.zip").unlink()
    triage.import_osap(ctx, folder)
    go(ctx, "T-13", "Mom12m")
    with pytest.raises(InvalidQuery, match="no large-cap returns"):
        triage.run_triage(ctx, "T-13")


def _drive(served: dict[str, bytes], seen: list):
    ids = {f.drive_id: f.name for f in triage.FILES}

    def handler(request: httpx.Request) -> httpx.Response:
        name = ids[request.url.params["id"]]
        seen.append((name, request.headers.get("Range")))
        body = served[name]
        if rng := request.headers.get("Range"):
            start = int(rng.removeprefix("bytes=").rstrip("-"))
            if start >= len(body):
                return httpx.Response(416, headers={"content-range": f"bytes */{len(body)}"})
            return httpx.Response(206, content=body[start:], headers={"content-type": "application/octet-stream"})
        return httpx.Response(200, content=body, headers={"content-type": "application/octet-stream"})
    return httpx.MockTransport(handler)


async def test_the_fetch_is_refused_outside_the_compute_window(ctx, monkeypatch):
    monkeypatch.setattr(triage, "local_now", lambda c: datetime(2026, 10, 4, 8, 30))  # a Sunday after 08:00
    with pytest.raises(InvalidQuery, match="compute window.*opens Mon 01:00"):
        await triage.fetch_osap(ctx)


async def test_the_fetch_resumes_a_part_file_keeps_done_files_and_imports(ctx, tmp_path, monkeypatch):
    monkeypatch.setattr(triage, "local_now", lambda c: datetime(2026, 10, 5, 2, 0))  # Monday 02:00
    source = write_release(tmp_path / "source")
    served = {f.name: (source / f.name).read_bytes() for f in triage.FILES}
    folder = triage.osap_folder(ctx)
    folder.mkdir(parents=True)
    (folder / "SignalDoc.csv").write_bytes(served["SignalDoc.csv"])
    big = "PredictorPortsFull.csv"
    (folder / (big + ".part")).write_bytes(served[big][:1000])
    seen: list = []
    ctx.transport = _drive(served, seen)
    done = await triage.fetch_osap(ctx)
    assert seen == [(big, "bytes=1000-"), ("PredictorAltPorts_LiqScreen_ME_gt_NYSE20pct.zip", None)]
    assert (folder / big).read_bytes() == served[big] and not (folder / (big + ".part")).exists()
    assert [i.what for i in done] == ["doc", "op", "large"]
    assert set(triage.osap_status(ctx).on_disk) == {f.name for f in triage.FILES}


@pytest.mark.parametrize("extra, kept", [(b"", True), (b"stale", False)])
async def test_a_416_finishes_only_a_part_of_the_expected_size(ctx, tmp_path, monkeypatch, extra, kept):
    monkeypatch.setattr(triage, "local_now", lambda c: datetime(2026, 10, 5, 2, 0))
    source = write_release(tmp_path / "source")
    served = {f.name: (source / f.name).read_bytes() for f in triage.FILES}
    folder = triage.osap_folder(ctx)
    folder.mkdir(parents=True)
    (folder / "SignalDoc.csv").write_bytes(served["SignalDoc.csv"])
    (folder / "PredictorPortsFull.csv.part").write_bytes(served["PredictorPortsFull.csv"] + extra)
    seen: list = []
    ctx.transport = _drive(served, seen)
    await triage.fetch_osap(ctx)
    ranges = [r for n, r in seen if n == "PredictorPortsFull.csv"]
    assert ranges == (["bytes=%d-" % len(served["PredictorPortsFull.csv"])] if kept else
                      ["bytes=%d-" % (len(served["PredictorPortsFull.csv"]) + 5), None])
    assert (folder / "PredictorPortsFull.csv").read_bytes() == served["PredictorPortsFull.csv"]


async def test_a_drive_page_instead_of_the_file_is_refused(ctx, monkeypatch):
    monkeypatch.setattr(triage, "local_now", lambda c: datetime(2026, 10, 5, 2, 0))
    ctx.transport = httpx.MockTransport(lambda r: httpx.Response(200, text="<html>quota</html>",
                                                                 headers={"content-type": "text/html"}))
    with pytest.raises(InvalidQuery, match="sent a page"):
        await triage.fetch_osap(ctx)


def test_observed_windows_take_the_error_from_the_stored_observed_power_and_the_tests_bar(ctx, monkeypatch):
    from types import SimpleNamespace as NS
    window = lambda h, power: NS(horizon=h, observed_power=power, names=480.0, days=1680)
    tests = {"a": NS(bar=3.0, confirm=[window(20, 0.0111), window(5, None)]), "b": NS(bar=2.0, confirm=[window(5, 0.02)])}
    monkeypatch.setattr(triage, "signal_tests", lambda c: tests)
    assert REAL_OBSERVED_WINDOWS(ctx) == [(20, pytest.approx(0.0037), 480.0, 1680), (5, pytest.approx(0.01), 480.0, 1680)]
