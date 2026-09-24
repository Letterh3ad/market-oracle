from datetime import date

import pytest

from oracle.research.conditions import FACTS
from oracle.research.trace import Claim, Evidence, Report, Step, Verdict
from oracle.research.verdict import Material, add_verdict, budget, check, fit
from tests.fakes.llm import ScriptedModel

DAY = date(2026, 9, 21)
STATS = "[a9] 2026-09-21 stored data: AAPL last close 105.00 USD. Change over 1 day: +5.0%. Baseline lean: buy 64%."
REPORT = Report(DAY, (Claim("Beat estimates", (1,), "news_analyst"),), Claim("Momentum", (1,), "bull"), None,
                (Claim("China demand", (2,), "risk"),), "Next quarter misses")
GOOD = {"verdict": "buy", "confidence": 0.7, "reason": "Beat and momentum", "cites": ["a1"],
        "invalidated_if": "Guidance cut"}


def ev(i: int) -> Evidence:
    return Evidence(i, f"https://x.test/{i}", f"Title {i}", "X", None, False)


def material(uncited: int = 0, notes: str = "Beat [a1].", steps: tuple[Step, ...] = ()) -> Material:
    evidence = (ev(1), ev(2), *(ev(10 + n) for n in range(uncited)))
    return Material(STATS, 9, notes, REPORT, evidence, steps,
                    {e.id: f"[a{e.id}] undated X: {e.title}" for e in evidence})


def test_everything_fits_in_a_roomy_budget_and_all_of_it_is_citable():
    steps = (Step(1, "look", "news_search", "Apple", "[a1] undated X: Title 1\n[a2] undated X: Title 2"),
             Step(2, "done", "final", "Beat [a1].", None))
    f = fit(material(uncited=2, steps=steps), 100_000)
    assert f.text.startswith(f"Statistics:\n{STATS}")
    for part in ("Beat [a1].", "- news_analyst: Beat estimates [a1]", "- bull case: Momentum [a1]",
                 "- risk: China demand [a2]", "- the bull case is wrong if: Next quarter misses",
                 "[a11] undated X: Title 11", "Step 1, news_search(Apple):"):
        assert part in f.text
    assert "Step 2" not in f.text
    assert f.shown == {1, 2, 9, 10, 11} and f.unread == ()


def test_the_statistics_are_always_there_even_with_no_room_at_all():
    f = fit(material(uncited=3), 0)
    assert STATS in f.text and f.shown == {9}
    assert f.unread == ("the researcher's notes", "4 role outputs", "2 cited items", "3 uncited items")


def test_a_tight_budget_leaves_out_uncited_evidence_first_and_records_it():
    m = material(uncited=7)
    full = fit(m, 100_000).text
    assert fit(m, len(full)).text == full
    assert fit(m, len(full) - 1).unread == ("1 uncited item",)
    f = fit(m, len(full.split("\n\nOther evidence shown:")[0]))
    assert f.unread == ("7 uncited items",) and "- risk: China demand" in f.text and "[a1] undated" in f.text


def test_observations_behind_cited_evidence_come_before_the_rest():
    cited = Step(1, "", "news_search", "Apple", "[a1] undated X: Title 1 " + "x" * 200)
    other = Step(2, "", "news_search", "Pears", "[a10] undated X: Title 10 " + "y" * 200)
    m = material(uncited=1, steps=(other, cited))
    f = fit(m, len(fit(m, 100_000).text) - 260)
    assert "Title 1 xxx" in f.text and "yyy" not in f.text
    assert f.unread == ("1 step observation", "1 uncited item")


def test_evidence_with_facts_is_left_out_after_headlines_and_step_observations():
    from dataclasses import replace

    facts = "\n  - Revenue rose 8% [a{0}]"
    base = material(uncited=3, steps=(Step(1, "", "news_search", "Pears", "[a12] undated X: Title 12"),))
    lines = {**base.evidence_lines, 2: base.evidence_lines[2] + facts.format(2),
             11: base.evidence_lines[11] + facts.format(11)}
    m = replace(base, evidence_lines=lines, read=frozenset({2, 11}))
    full = fit(m, 100_000).text
    assert full.index("[a2] undated") < full.index("[a1] undated")  # cited: the read one first
    assert full.index("Other evidence read:\n[a11] undated X: Title 11\n  - Revenue rose 8% [a11]") < full.index(
        "Other evidence shown:")
    f = fit(m, len(full.split("\n\nOther evidence shown:")[0]))
    assert "Revenue rose 8% [a11]" in f.text and f.unread == ("1 step observation", "2 uncited items")


def test_the_budget_is_three_quarters_of_the_models_context_less_the_reply_and_the_prompt():
    # Numbers and URLs run nearer 3 characters a token than 4.
    assert budget(8192, "s" * 100) == (8192 - 1024) * 3 - 100
    assert budget(1000, "s") == 0
    assert budget(8192, "s" * 100, reply_tokens=4096) == (8192 - 4096) * 3 - 100


def test_ollama_and_claude_keep_the_budgets_default_reply_room_and_a_bigger_one_shrinks_every_prompt():
    from oracle.research.budget import REPLY_TOKENS, reply_room
    from oracle.research.claude import ClaudeChat
    from oracle.research.notes import note_room
    from oracle.research.ollama import OllamaChat
    from oracle.research.roles import digest_room

    assert reply_room(OllamaChat("m", "http://x", 1.0, 8192)) == reply_room(ClaudeChat("m", 1.0)) == REPLY_TOKENS == 1024
    assert reply_room(object()) == REPLY_TOKENS, "a scripted model takes the default"
    assert digest_room(16384, 4096) == digest_room(16384) - 3072 * 3
    assert note_room(5000, "s", "h", 4096) == note_room(5000, "s", "h") - 3072 * 3


@pytest.mark.parametrize("reply, why", [
    ("garbled", "not in the shape asked for"),
    ({**GOOD, "cites": "a1"}, "not in the shape asked for"),
    ({**GOOD, "verdict": "strong buy"}, "'strong buy' is not buy, hold or sell"),
    ({**GOOD, "confidence": 1.5}, "confidence 1.5 is not a number from 0 to 1"),
    ({**GOOD, "confidence": "0.7"}, "confidence '0.7' is not a number from 0 to 1"),
    ({**GOOD, "reason": "", "cites": []}, "it gave no reason"),
    ({**GOOD, "cites": []}, "its reason cites no evidence"),
    ({**GOOD, "cites": ["a42"]}, "its reason cites nothing it was shown"),
    ({**GOOD, "invalidated_if": " "}, "it gave no condition that would prove it wrong"),
])
def test_an_invalid_verdict_is_rejected_with_its_reason(reply, why):
    assert check(reply, {1, 9}) == (None, why)


def test_a_valid_verdict_keeps_only_the_citations_it_was_shown():
    assert check({**GOOD, "verdict": " Hold ", "confidence": 1, "cites": ["a1", "a42"]}, {1}) == (
        Verdict("hold", 1.0, "Beat and momentum", (1,), "Guidance cut"), None)


async def test_the_verdict_role_reads_the_fitted_material_and_its_verdict_lands_on_the_report():
    model = ScriptedModel()
    model.roles = {"verdict": {**GOOD, "cites": ["a9"]}}
    r = await add_verdict(model, REPORT, material(uncited=1), 8192)
    assert r.verdict == Verdict("buy", 0.7, "Beat and momentum", (9,), "Guidance cut")
    assert r.verdict_rejected is None and r.unread == () and r.did_not_read is None and 9 in r.cites
    (messages, schema), = model.calls
    assert messages[0]["content"].startswith("Role: verdict.") and STATS in messages[1]["content"]
    assert schema["properties"]["verdict"]["enum"] == ["buy", "hold", "sell"]


async def test_a_rejected_verdict_says_why_and_what_went_unread():
    model = ScriptedModel()
    model.roles = {"verdict": {**GOOD, "cites": ["a1"]}}
    r = await add_verdict(model, REPORT, material(uncited=40), 1100)
    assert r.verdict is None and r.verdict_rejected == "its reason cites nothing it was shown"
    assert r.no_verdict == "the verdict was rejected: its reason cites nothing it was shown"
    assert r.did_not_read == "did not read: the researcher's notes, 4 role outputs, 2 cited items, 40 uncited items"


async def test_a_backend_that_cannot_write_the_verdict_leaves_the_report_with_why():
    from oracle.research.llm import ModelError

    model = ScriptedModel()
    model.roles = {"verdict": ModelError("ollama timed out")}
    r = await add_verdict(model, REPORT, material(), 8192)
    assert r.verdict is None and r.verdict_rejected == "the model could not write a verdict: ollama timed out"
    assert r.findings == REPORT.findings


def test_an_older_report_has_no_verdict_and_says_so():
    assert REPORT.verdict is None and REPORT.no_verdict == "written before reports had verdicts"


BAD = {**GOOD, "invalidated_if": "Earnings beat estimates by 12%."}


def _answers(*replies):
    queue = list(replies)
    return lambda messages: queue.pop(0)



async def test_a_bad_condition_is_asked_again_once_with_why_and_a_good_second_answer_is_kept():
    model = ScriptedModel()
    model.roles = {"verdict": _answers(BAD, GOOD)}
    r = await add_verdict(model, REPORT, material(), 8192)
    assert r.verdict == Verdict("buy", 0.7, "Beat and momentum", (1,), "Guidance cut") and r.verdict_rejected is None
    first, second = (m for m, _ in model.calls)
    assert second[0] == first[0] and second[1]["content"].startswith(first[1]["content"])
    assert FACTS in second[1]["content"] and "Earnings beat estimates by 12%." in second[1]["content"]


async def test_a_second_bad_condition_rejects_the_verdict_as_before():
    model = ScriptedModel()
    model.roles = {"verdict": BAD}
    r = await add_verdict(model, REPORT, material(), 8192)
    assert r.verdict is None and r.verdict_rejected == FACTS and len(model.calls) == 2


async def test_other_rejections_are_not_asked_again():
    model = ScriptedModel()
    model.roles = {"verdict": {**GOOD, "confidence": 7}}
    r = await add_verdict(model, REPORT, material(), 8192)
    assert r.verdict_rejected == "confidence 7 is not a number from 0 to 1" and len(model.calls) == 1


async def test_the_second_ask_still_fits_the_budget_and_records_what_it_left_out():
    from math import ceil

    from oracle.research.roles import prompt
    from oracle.research.verdict import ROLE, TASK, WHO

    model = ScriptedModel()
    model.roles = {"verdict": _answers({**BAD, "invalidated_if": "Earnings beat estimates. " * 100}, GOOD)}
    m = material(uncited=40)
    system = prompt(ROLE, WHO, TASK)
    context = ceil((len(fit(m, 100_000).text) + len(system)) / 3) + 1024  # the first ask just fits
    room = budget(context, system)
    r = await add_verdict(model, REPORT, m, context)
    first, second = (msgs[1]["content"] for msgs, _ in model.calls)
    assert len(first) <= room and len(second) <= room
    assert r.verdict is not None and r.unread and fit(m, room).unread == ()
