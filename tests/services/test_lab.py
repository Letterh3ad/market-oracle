"""The test registry: back-filled legacy tests with their own bars, new registrations with the
Bonferroni bar frozen at registration, the commit gate on data runs, and the scoreboard."""

import subprocess
from dataclasses import replace
from datetime import date

import pytest

from oracle.analysts.lab import bonferroni_bar
from oracle.services.activity_service import JobRunner, submit_signal_test
from oracle.services.errors import InvalidQuery, NotRegistered, UnknownLabTest
from oracle.services.event_study_service import run_event_study
from oracle.services.lab_registry import (
    Template, commit_test, drop_test, hypotheses, register_test, registration, registrations,
)
from oracle.services.lab_service import near_check_text, scoreboard
from oracle.services.laya_test_service import (
    DEFAULT_LIST, MODEL_TAG, TEST_START, HorizonResult, LayaTest, keep_laya_test,
)
from oracle.services.signal_test_service import keep_signal_test, run_signal_test
from oracle.services.testbed_service import research_context
from oracle.services.trend_service import run_and_keep_trend
from oracle.store.lab import LabTestRepo
from tests.fakes.lab import registered
from tests.services.test_signal_tests import REGISTRY, ctx, seed  # noqa: F401

LEGACY_N = 19
"""5 single-horizon signals, insider_buys (2), 4 event types, Laya (2), the trend overlay, 5 trackers."""


def template(key="future", kind="signal", horizons=(5,), **kw) -> Template:
    return Template(key, "a test", kind, horizons, "the rule", "the testbed", "positive", "IC 0.02", "a paper",
                    "power 0.01 at 20 d", "none", "confirm", **kw)


def test_the_legacy_tests_are_back_filled_committed_with_the_bars_they_were_judged_by(ctx):
    regs = {r.key: r for r in registrations(ctx)}
    assert set(regs) == {"momentum_12_1", "reversal_1m", "sue", "gross_profitability", "earnings_yield", "insider_buys",
                         "event:2.02", "event:1.01", "event:2.01", "event:insider_cluster", "laya", "trend_overlay",
                         "forward:momentum_12_1", "forward:reversal_1m", "forward:sue", "forward:earnings_yield",
                         "forward:1.01"}
    assert all(r.legacy and r.commit_hash and len(r.commit_hash) == 40 for r in regs.values())
    assert {r.bar for k, r in regs.items() if k != "trend_overlay"} == {2.0}
    assert not any(r.bar_inclusive for r in regs.values())
    assert regs["trend_overlay"].bar is None and "two thirds" in regs["trend_overlay"].bar_rule
    assert regs["insider_buys"].horizons == (20, 60) and regs["laya"].horizons == (5, 20)
    assert regs["forward:1.01"].expected_sign == "negative" and "2027-10-01" in regs["forward:1.01"].note
    assert hypotheses(regs.values()) == LEGACY_N


def test_a_registration_freezes_the_bonferroni_bar_over_every_horizon_its_own_included(ctx):
    one = register_test(ctx, template("a", horizons=(5,)), today="2026-10-04")
    two = register_test(ctx, template("b", horizons=(20, 60)), today="2026-10-04")
    strat = register_test(ctx, template("c", kind="strategy", horizons=()), today="2026-10-04")
    assert one.bar == round(bonferroni_bar(LEGACY_N + 1), 4) == 3.0233
    assert two.bar == round(bonferroni_bar(LEGACY_N + 3), 4)
    assert two.bar > one.bar and one.bar_inclusive and not one.legacy and one.commit_hash is None
    assert "N = 22" in two.bar_rule and strat.bar is None and "deflated Sharpe" in strat.bar_rule
    assert registration(ctx, "a").bar == one.bar, "frozen: later registrations never move it"
    assert hypotheses(registrations(ctx)) == LEGACY_N + 4


@pytest.mark.parametrize("bad, why", [
    (dict(key="Bad Key"), "key"),
    (dict(key="momentum_12_1"), "registered already"),
    (dict(kind="vibes"), "kind"),
    (dict(horizons=(5, 20, 60)), "1 to 2"),
    (dict(horizons=()), "1 to 2"),
    (dict(kind="strategy", horizons=(5,)), "no horizons"),
    (dict(expected_sign="up"), "expected sign"),
    (dict(expected_sign="negative"), "IC above 0"),
    (dict(rule=" "), "needs rule"),
    (dict(rule=5), "are text"),
    (dict(horizons=(True,)), "whole trading days"),
    (dict(parent="momentum_12_1"), "different because"),
    (dict(parent="nope", different_because="x"), "not a registered test"),
])
def test_an_incomplete_or_clashing_template_is_refused(ctx, bad, why):
    t = replace(template(), **bad)
    with pytest.raises(InvalidQuery, match=why):
        register_test(ctx, t)
    assert hypotheses(registrations(ctx)) == LEGACY_N


def test_a_strategy_registration_records_the_variants_tried_for_its_deflated_sharpe(ctx):
    s = register_test(ctx, template("s", kind="strategy", horizons=(), variants=6), today="2026-10-04")
    assert s.variants == 6 and "6 variants tried" in s.bar_rule
    assert registration(ctx, "s").variants == 6 and hypotheses(registrations(ctx)) == LEGACY_N + 1, "still one in N"
    assert registration(ctx, "trend_overlay").variants == 1 and register_test(ctx, template("t")).variants == 1


@pytest.mark.parametrize("variants, why", [(0, "1 or more"), (True, "1 or more"), (2.5, "1 or more")])
def test_variants_are_a_whole_number_of_tries(ctx, variants, why):
    with pytest.raises(InvalidQuery, match=why):
        register_test(ctx, template("s", kind="strategy", horizons=(), variants=variants))


def test_only_a_strategy_counts_variants(ctx):
    with pytest.raises(InvalidQuery, match="strategy"):
        register_test(ctx, template("x", variants=3))


async def test_a_run_without_a_committed_registration_is_refused(ctx):
    seed(ctx)
    with research_context(ctx) as r:
        LabTestRepo(r.conn).delete("future")
    with pytest.raises(NotRegistered, match="not pre-registered"):
        await run_signal_test(ctx, "future", registry=REGISTRY)
    register_test(ctx, template("future"))
    with pytest.raises(NotRegistered, match="not committed"):
        await run_signal_test(ctx, "future", registry=REGISTRY, train_only=True)
    with research_context(ctx) as r:
        LabTestRepo(r.conn).set_commit("future", "a" * 40)
    t = await run_signal_test(ctx, "future", registry=REGISTRY)
    assert t.bar == registration(ctx, "future").bar and t.bar_inclusive and t.verdict == "PASS"


async def test_the_harness_judges_by_the_registered_bar(ctx):
    seed(ctx)
    with research_context(ctx) as r:
        LabTestRepo(r.conn).delete("future")
    reg = register_test(ctx, template("future"))
    with research_context(ctx) as r:
        repo = LabTestRepo(r.conn)
        repo.delete("future")
        repo.add(replace(reg, bar=1e6, commit_hash="a" * 40))
    t = await run_signal_test(ctx, "future", registry=REGISTRY)
    assert t.verdict == "FAIL" and t.confirm[0].t > 2, "a t that cleared the old bar fails a higher one"
    assert t.confirm[0].power > 1000, "power is the IC that would reach the registered bar"


async def test_every_test_kind_is_refused_before_its_data_run_and_before_its_job_is_queued(ctx):
    with research_context(ctx) as r:
        repo = LabTestRepo(r.conn)
        for key in ("reversal_1m", "event:2.01", "trend_overlay"):
            repo.set_commit(key, None)
    with pytest.raises(NotRegistered, match="event:2.01"):
        await run_event_study(ctx)
    with pytest.raises(NotRegistered, match="trend_overlay"):
        await run_and_keep_trend(ctx)
    with pytest.raises(NotRegistered, match="reversal_1m"):
        submit_signal_test(ctx, JobRunner(ctx), "reversal_1m")


def _git(root, *args):
    return subprocess.run(["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@t", *args],
                          capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "checkout"
    root.mkdir()
    _git(root, "init", "-q")
    (root / "DECISIONS.md").write_text("## 2026-10-04 - Pre-register `future`\n", encoding="utf-8")
    _git(root, "add", "DECISIONS.md")
    _git(root, "commit", "-q", "-m", "docs: pre-register future")
    return root


def _decide(root, line, message="docs: pre-register"):
    path = root / "DECISIONS.md"
    path.write_text(path.read_text(encoding="utf-8") + line + "\n", encoding="utf-8")
    _git(root, "commit", "-q", "-am", message)
    return _git(root, "rev-parse", "HEAD")


def test_a_commit_is_recorded_only_when_its_decisions_name_the_test(ctx, repo, tmp_path):
    register_test(ctx, template("future"))
    register_test(ctx, template("other"))
    head = _git(repo, "rev-parse", "HEAD")
    with pytest.raises(InvalidQuery, match="does not add `other`"):
        commit_test(ctx, "other", head[:7], root=repo)
    with pytest.raises(InvalidQuery, match="no commit"):
        commit_test(ctx, "future", "deadbee", root=repo)
    with pytest.raises(InvalidQuery, match="not a commit hash"):
        commit_test(ctx, "future", "HEAD", root=repo)
    with pytest.raises(UnknownLabTest):
        commit_test(ctx, "nope", head, root=repo)
    assert commit_test(ctx, "future", head[:7].upper(), root=repo).commit_hash == head, "stored in full"
    with pytest.raises(InvalidQuery, match="committed already"):
        commit_test(ctx, "future", head, root=repo)


def test_the_commit_must_add_the_entry_after_the_registration_on_this_checkouts_history(ctx, repo):
    register_test(ctx, template("future"), today="2026-01-01")
    register_test(ctx, template("later"), today="2099-01-01")
    register_test(ctx, template("side"), today="2026-01-01")
    mention = _decide(repo, "## Unrelated, mentions `future` and `later` again")
    with pytest.raises(InvalidQuery, match="does not add `future`"):
        commit_test(ctx, "future", mention, root=repo)
    with pytest.raises(InvalidQuery, match="before `later` was registered"):
        commit_test(ctx, "later", mention, root=repo)
    _git(repo, "checkout", "-q", "-b", "elsewhere")
    side = _decide(repo, "## Pre-register `side`")
    _git(repo, "checkout", "-q", "-")
    with pytest.raises(InvalidQuery, match="not in this checkout's history"):
        commit_test(ctx, "side", side, root=repo)
    assert registration(ctx, "side").commit_hash is None


@pytest.mark.parametrize("fault, why", [
    (FileNotFoundError("git"), "git could not run"),
    (subprocess.TimeoutExpired("git", 10), "git timed out"),
])
def test_a_commit_is_refused_when_git_cannot_check_it(ctx, repo, monkeypatch, fault, why):
    register_test(ctx, template("future"))
    head = _git(repo, "rev-parse", "HEAD")

    def broken(*a, **k):
        raise fault
    monkeypatch.setattr("oracle.system.version.subprocess.run", broken)
    with pytest.raises(InvalidQuery, match=why):
        commit_test(ctx, "future", head, root=repo)
    assert registration(ctx, "future").commit_hash is None


def test_a_commit_is_refused_without_a_checkout_git_can_read(ctx, tmp_path):
    register_test(ctx, template("future"))
    plain = tmp_path / "no-git"
    plain.mkdir()
    with pytest.raises(InvalidQuery, match="not a git checkout"):
        commit_test(ctx, "future", "abcdef1", root=plain)
    assert registration(ctx, "future").commit_hash is None


def test_only_an_uncommitted_registration_can_be_dropped(ctx):
    register_test(ctx, template("draft"))
    drop_test(ctx, "draft")
    assert "draft" not in {r.key for r in registrations(ctx)}
    with pytest.raises(InvalidQuery, match="counts toward N"):
        drop_test(ctx, "momentum_12_1")


async def test_the_scoreboard_shows_old_verdicts_as_kept_with_their_own_bar_and_new_ones_with_theirs(ctx):
    seed(ctx)
    old = keep_signal_test(ctx, await run_signal_test(ctx, "reversal_1m"))
    draft = register_test(ctx, template("draft"))
    board = scoreboard(ctx)
    rows = {(r.key, r.horizon): r for r in board.rows}
    rev = rows[("reversal_1m", 5)]
    assert (rev.value, rev.t, rev.verdict, rev.power) == (old.confirm[0].ic, old.confirm[0].t,
                                                          old.confirm[0].verdict_text, old.confirm[0].observed_power)
    assert rev.bar == "t > 2.00" and rev.legacy and rev.measure == "IC" and rev.run_at == old.run_at
    assert rows[("momentum_12_1", 20)].verdict == "not run"
    assert rows[("draft", 5)].verdict.startswith("not committed") and rows[("draft", 5)].bar == f"t >= {draft.bar:.2f}"
    assert rows[("trend_overlay", None)].bar.startswith("worst fall")
    assert {h for k, h in rows if k == "insider_buys"} == {20, 60}
    n = LEGACY_N + len(REGISTRY) + 1  # seed registers the fake signals
    assert board.n == n and board.next_bar == bonferroni_bar(n + 1)


def test_a_negative_expected_sign_is_kept_for_an_event_test(ctx):
    assert register_test(ctx, replace(template("ev", kind="event"), expected_sign="negative")).expected_sign == "negative"


def test_a_registration_reads_n_and_inserts_in_one_transaction(ctx, monkeypatch):
    seen, read = [], LabTestRepo.all

    def watched(self):
        seen.append(self._conn.in_transaction)
        return read(self)
    monkeypatch.setattr(LabTestRepo, "all", watched)
    register_test(ctx, template("a"))
    assert seen == [True], "a registration between the read and the insert would freeze the same N"


async def test_a_signal_test_runs_exactly_its_registered_horizons(ctx):
    seed(ctx)
    with research_context(ctx) as r:
        for key in ("future", "null"):
            LabTestRepo(r.conn).delete(key)
    registered(ctx, "future", horizons=(5, 20))
    registered(ctx, "null", horizons=(20,))
    wider = await run_signal_test(ctx, "future", registry=REGISTRY)
    assert wider.horizons == (5, 20) and [w.horizon for w in wider.confirm] == [5, 20]
    other = await run_signal_test(ctx, "null", registry=REGISTRY)
    assert other.horizons == (20,) and [w.horizon for w in other.confirm] == [20], "the code's 5 is not registered"


def _laya(ic, watchlist=DEFAULT_LIST, start=TEST_START):
    hs = tuple(HorizonResult(h, 0, ic, 1.0, 10, 10, (), (), "FAIL", False, 0, 1) for h in (5, 20))
    return LayaTest("main", "Laya", MODEL_TAG, start, date(2026, 9, 1), 51, {}, hs, "FAIL", False,
                    "2026-10-01T00:00:00+00:00", watchlist=watchlist)


def test_the_scoreboard_reads_layas_pre_registered_run_never_a_look(ctx):
    keep_laya_test(ctx, _laya(0.01))
    keep_laya_test(ctx, _laya(0.5, watchlist="another list"))
    keep_laya_test(ctx, _laya(0.6, start=date(2024, 1, 1)))
    rows = {(r.key, r.horizon): r for r in scoreboard(ctx).rows}
    assert rows[("laya", 5)].value == rows[("laya", 20)].value == 0.01


def test_a_registration_near_a_failed_method_needs_a_different_because(ctx, monkeypatch):
    from tests.fakes.embed import FakeEmbed

    monkeypatch.setattr("oracle.services.meaning_service.embedder", lambda ctx: FakeEmbed())
    near = replace(template("over_200d", kind="strategy", horizons=()), rule="own the market over its 200-day average")
    with pytest.raises(InvalidQuery, match=r"T-62 .*FAILed.*different because"):
        register_test(ctx, near)
    with pytest.raises(UnknownLabTest):
        registration(ctx, "over_200d")
    reg = register_test(ctx, replace(near, different_because="weekly checks, not month-end"))
    assert reg.near_fail.startswith("T-62 Trend overlay (10-month MA) (meaning ")
    assert registration(ctx, "over_200d").near_fail == reg.near_fail
    assert reg.near_check == "meaning" and near_check_text(registration(ctx, "over_200d")).startswith("ran on meaning")
    assert register_test(ctx, template("plain")).near_fail is None


def test_a_registration_records_that_its_near_fail_check_ran_on_keywords_only(ctx):
    reg = register_test(ctx, template("plain"))
    kept = registration(ctx, "plain")
    assert reg.near_fail is None and kept.near_check == "keyword" and "not reachable" in kept.near_check_note
    assert near_check_text(kept).startswith("ran on keywords only")
    assert near_check_text(registration(ctx, "momentum_12_1")) is None
