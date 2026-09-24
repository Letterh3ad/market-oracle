from datetime import date

import pytest

from oracle.research.cutoff import answered, cutoff_from, month_scores, normalize, reply_text
from oracle.research.cutoff_questions import CLAIMED_CUTOFF, EVENTS, VERSION


@pytest.mark.parametrize("reply, expect, ok", [
    ("The Philadelphia Eagles won.", ("eagles",), True),
    ("The Kansas City Chiefs won.", ("eagles",), False),
    ("4.00% to 4.25%", ("4-4.25",), True),
    ("between 4 and 4.25 percent", ("4-4.25",), True),
    ("4.25%\u20134.50%", ("4-4.25",), False),
    ("4.25-4.5%", ("4.25-4.5",), True),
    ("4 to 4-1/4 percent", ("4-4.25",), True),
    ("3-1/2 to 3\u20113/4 percent", ("3.5-3.75",), True),
    ("3\u00bd to 3\u00be%", ("3.5-3.75",), True),
    ("Pope Leo XIV", ("leo",), True),
    ("Leopold", ("leo",), False),
    ("Nicu\u0219or Dan", ("nicusor dan",), True),
    ("Spain and Portugal", ("spain", "portugal"), True),
    ("Spain", ("spain", "portugal"), False),
    ("Stargate", ("stargate|star gate",), True),
    ("unknown", ("stargate",), False),
    ("", ("stargate",), False),
])
def test_a_reply_is_right_when_every_expected_keyword_or_one_of_its_alternatives_is_there(reply, expect, ok):
    assert answered(reply, expect) is ok


def test_numbers_and_ranges_are_written_one_way():
    assert normalize("4.50% \u2013 4.75 per cent") == "4.5-4.75"
    assert normalize("  The  EAGLES ") == "the eagles"


def test_the_reply_is_the_answer_field_or_the_text_the_model_gave():
    assert reply_text({"answer": "Eagles"}) == "Eagles"
    assert reply_text("Eagles") == "Eagles"
    assert reply_text({"other": 1}) == "{'other': 1}"


def test_months_are_scored_in_order_and_known_at_half_right():
    marks = [(date(2025, 2, 3), True), (date(2025, 1, 5), True), (date(2025, 1, 9), False),
             (date(2025, 2, 10), False), (date(2025, 2, 11), False)]
    scores = month_scores(marks)
    assert [(s.month, s.right, s.total, s.known) for s in scores] == [
        ("2025-01", 1, 2, True), ("2025-02", 1, 3, False)]


def scores_known(*known: tuple[str, bool]):
    return month_scores([(date.fromisoformat(f"{m}-10"), k) for m, k in known])


def test_the_cutoff_is_the_end_of_the_month_after_the_last_known_one():
    scores = scores_known(("2025-04", True), ("2025-05", True), ("2025-06", True), ("2025-07", False),
                          ("2025-08", False))
    assert cutoff_from(scores, date(2025, 5, 20)) == date(2025, 7, 31)


def test_a_late_lucky_month_moves_the_cutoff_later_never_earlier():
    scores = scores_known(("2025-05", True), ("2025-06", False), ("2025-07", False), ("2025-11", True),
                          ("2025-12", False))
    assert cutoff_from(scores, date(2025, 5, 20)) == date(2025, 12, 31)


def test_the_cutoff_is_never_before_the_claimed_one():
    assert cutoff_from(scores_known(("2025-01", True), ("2025-02", False)), date(2025, 5, 20)) == date(2025, 5, 20)
    assert cutoff_from(scores_known(("2025-01", False)), date(2025, 5, 20)) == date(2025, 5, 20)


def test_a_december_margin_runs_into_the_next_year():
    assert cutoff_from(scores_known(("2025-11", True)), date(2025, 5, 20)) == date(2025, 12, 31)
    assert cutoff_from(scores_known(("2025-12", True)), date(2025, 5, 20)) == date(2026, 1, 31)


def months(first: date, last: date) -> list[str]:
    out, y, m = [], first.year, first.month
    while (y, m) <= (last.year, last.month):
        out.append(f"{y:04d}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def test_the_question_set_covers_four_months_before_the_claim_to_september_2026():
    assert VERSION
    by_month: dict[str, int] = {}
    for e in EVENTS:
        by_month[e.month] = by_month.get(e.month, 0) + 1
    assert list(by_month) == months(date(2025, 1, 1), date(2026, 9, 1)), "one run of months, in order"
    assert all(2 <= n <= 3 for n in by_month.values()), by_month
    assert CLAIMED_CUTOFF == date(2025, 5, 20)


def test_every_question_is_sourced_unique_and_its_own_answer_passes():
    assert len({e.id for e in EVENTS}) == len(EVENTS)
    for e in EVENTS:
        assert e.source.startswith("https://"), e.id
        assert e.day.isoformat() not in e.answer, e.id
        assert all(normalize(k) for k in e.expect), e.id
        assert answered(e.answer, e.expect), e.id
        assert not answered("unknown", e.expect) and not answered("I do not know.", e.expect), e.id
