from datetime import date

from oracle.research.freshness import Known, due_for_research

TODAY = date(2026, 9, 23)


def due(*known, max_age=7, move=0.05):
    return [(d.symbol, d.why) for d in due_for_research(list(known), TODAY, max_age, move)]


def test_a_fresh_report_with_a_small_move_is_not_due():
    assert due(Known("A", date(2026, 9, 20), 100.0, 104.9)) == []


def test_no_report_old_report_and_a_big_move_are_due_most_stale_first():
    assert due(
        Known("MOVED", date(2026, 9, 22), 100.0, 94.0),
        Known("OLD", date(2026, 9, 10), 100.0, 100.0),
        Known("NEW", None, None, 100.0),
        Known("OLDER", date(2026, 9, 1), None, None),
    ) == [
        ("NEW", "no report yet"),
        ("OLDER", "report from 2026-09-01, 22 days old"),
        ("OLD", "report from 2026-09-10, 13 days old"),
        ("MOVED", "price moved -6.0% since the report"),
    ]


def test_age_is_due_only_past_the_limit_and_the_move_only_past_the_threshold():
    assert due(Known("EDGE", date(2026, 9, 16), 100.0, 105.0)) == []
    assert due(Known("EDGE", date(2026, 9, 15), 100.0, 100.0), max_age=8) == []
    assert due(Known("UP", date(2026, 9, 22), 100.0, 105.1)) == [("UP", "price moved +5.1% since the report")]


def test_a_missing_price_is_never_a_move():
    assert due(Known("A", date(2026, 9, 22), None, 50.0), Known("B", date(2026, 9, 22), 100.0, None)) == []


def test_equally_stale_listings_keep_their_given_order_and_a_bigger_move_goes_first():
    assert [s for s, _ in due(Known("B", None, None, None), Known("A", None, None, None))] == ["B", "A"]
    assert [s for s, _ in due(Known("SMALL", date(2026, 9, 22), 100.0, 107.0),
                              Known("BIG", date(2026, 9, 22), 100.0, 80.0))] == ["BIG", "SMALL"]
