from datetime import date

from oracle.research.roles import write_report
from oracle.research.trace import Claim, Drop, Report, RoleCheck
from tests.fakes.llm import ScriptedModel

DAY = date(2026, 9, 21)
SHOWN = {1, 2, 3}
ROLES = ["fundamental_analyst", "technical_analyst", "news_analyst", "bull", "bear", "risk"]


async def report(**roles) -> Report:
    model = ScriptedModel()
    model.roles = roles
    return await write_report(model, "Evidence:\n[a1] ...", SHOWN, DAY)


def checks(r: Report) -> dict[str, RoleCheck]:
    return {c.role: c for c in r.checks}


async def test_every_role_is_checked_and_a_clean_run_rejects_nothing():
    r = await report(news_analyst={"findings": [{"text": "Beat", "cites": ["a1"]}]},
                     bull={"case": "Momentum", "cites": ["a1"]})
    assert [c.role for c in r.checks] == ROLES
    assert all(c.usable and c.dropped == {} for c in r.checks)
    assert r.rejections is None


async def test_garbled_json_and_missing_fields_make_a_role_unusable():
    r = await report(fundamental_analyst="sorry, I cannot", technical_analyst={"claims": []},
                     news_analyst={"findings": "Beat [a1]"}, bull={"cites": ["a1"]}, risk=["China"])
    c = checks(r)
    assert [role for role in ROLES if not c[role].usable] == [
        "fundamental_analyst", "technical_analyst", "news_analyst", "bull", "risk"]
    assert c["bear"].usable and r.bull is None and r.risks == () and r.invalidated_if is None


async def test_each_dropped_claim_is_counted_by_its_reason():
    r = await report(
        fundamental_analyst={"findings": [
            {"text": "Rich valuation", "cites": ["a3"]},
            {"text": "Margins double", "cites": []},
            {"text": "Sales doubled", "cites": ["a99"]},
        ]},
        news_analyst={"findings": ["just a string", {"text": "  ", "cites": ["a1"]}, {"text": "Beat", "cites": "a1"}]},
        bull={"case": "To the moon", "cites": ["a42"]},
    )
    assert r.findings == (Claim("Rich valuation", (3,), "fundamental_analyst"),)
    c = checks(r)
    assert c["fundamental_analyst"].dropped == {Drop.NO_CITATION: 1, Drop.UNKNOWN_CITATION: 1}
    assert c["news_analyst"].dropped == {Drop.WRONG_SHAPE: 2, Drop.EMPTY_TEXT: 1}
    assert c["bull"].dropped == {Drop.UNKNOWN_CITATION: 1}
    assert all(x.usable for x in r.checks)


async def test_a_side_with_no_case_to_make_drops_nothing():
    r = await report(bull={"case": "", "cites": []})
    assert r.bull is None and checks(r)["bull"] == RoleCheck("bull", True, {})


async def test_the_rejections_read_as_one_line():
    r = await report(
        fundamental_analyst="garbled", risk={"invalidated_if": "x"},
        news_analyst={"findings": [{"text": "A", "cites": ["a9"]}, {"text": "B", "cites": ["a8"]},
                                   {"text": "C", "cites": []}]},
    )
    assert r.rejections == "2 of 6 roles gave unusable answers; 3 claims dropped (2 citing nothing shown, 1 with no citation)"


async def test_one_of_each_reads_in_the_singular():
    r = await report(risk="garbled", bull={"case": "Up", "cites": []})
    assert r.rejections == "1 of 6 roles gave an unusable answer; 1 claim dropped (1 with no citation)"


async def test_nothing_usable_is_visible_on_the_report():
    r = await report(**{role: "garbled" for role in ROLES})
    assert not any(c.usable for c in r.checks) and r.usable is False
    assert (await report()).usable is True
