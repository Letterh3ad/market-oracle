from datetime import date

from oracle.market.dividends import Dividend
from oracle.store.db import connect
from oracle.store.dividends import DividendRepo, PaperDividend, PaperDividendRepo
from oracle.store.migrate import migrate


def repo(tmp_path):
    conn = connect(tmp_path / "o.db")
    migrate(conn)
    return conn


def test_dividends_are_kept_per_ex_date_and_a_refetch_keeps_a_known_pay_date(tmp_path):
    conn = repo(tmp_path)
    r = DividendRepo(conn)
    r.upsert("ACWI", [Dividend(date(2026, 6, 15), 1.01, "USD")], "yahoo", "t1")
    r.set_pay_dates("ACWI", {date(2026, 6, 15): date(2026, 6, 18)})
    r.upsert("ACWI", [Dividend(date(2026, 6, 15), 1.0097, "USD"), Dividend(date(2025, 12, 16), 1.235, "USD")],
             "yahoo", "t2")
    got = r.history("ACWI")
    assert [(x.ex_date, x.amount, x.pay_date) for x in got] == [
        (date(2025, 12, 16), 1.235, None), (date(2026, 6, 15), 1.0097, date(2026, 6, 18))]
    assert r.history("ACWI", date(2026, 1, 1), date(2026, 12, 31))[0].ex_date == date(2026, 6, 15)


def test_the_asked_span_grows_only_when_windows_touch(tmp_path):
    r = DividendRepo(repo(tmp_path))
    assert r.span("ACWI") is None
    r.cover("ACWI", date(2021, 1, 4), date(2026, 9, 1))
    assert r.cover("ACWI", date(2026, 8, 25), date(2026, 10, 2))
    assert r.span("ACWI") == (date(2021, 1, 4), date(2026, 10, 2))
    assert not r.cover("ACWI", date(2026, 11, 1), date(2026, 11, 5)), "a gap is not covered"
    assert r.span("ACWI") == (date(2021, 1, 4), date(2026, 10, 2))
    r.cover("ACWI", date(2020, 1, 1), date(2026, 11, 5), replace=True)
    assert r.span("ACWI") == (date(2020, 1, 1), date(2026, 11, 5))


def test_a_ledger_credit_is_recorded_once(tmp_path):
    r = PaperDividendRepo(repo(tmp_path))
    credit = PaperDividend("index", "index", "ACWI", date(2026, 6, 15), date(2026, 6, 18), True, 10, 1.01, "USD",
                           1.25, 8.08)
    assert r.record(credit)
    assert not r.record(credit)
    assert r.credits("index") == [credit]
    assert r.credits("index", "other") == []
    assert r.credited("index") == {("index", "ACWI", date(2026, 6, 15))}


def test_a_fetch_replaces_its_window_but_keeps_its_first_day(tmp_path):
    r = DividendRepo(repo(tmp_path))
    r.upsert("X", [Dividend(date(2026, 1, 5), 1.0, "USD"), Dividend(date(2026, 2, 5), 1.0, "USD"),
                   Dividend(date(2026, 3, 5), 1.0, "USD")], "yahoo", "t1")
    r.replace("X", [Dividend(date(2026, 3, 6), 1.0, "USD")], date(2026, 1, 5), date(2026, 3, 31), "yahoo", "t2")
    assert [d.ex_date for d in r.history("X")] == [date(2026, 1, 5), date(2026, 3, 6)]
