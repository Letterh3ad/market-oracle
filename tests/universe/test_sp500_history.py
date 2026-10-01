from datetime import date

import pytest

from oracle.universe.indexes import Unreadable
from oracle.universe.sp500_history import parse_history, parse_sectors


def test_rows_are_sorted_with_tickers_in_yahoos_spelling():
    text = 'date,tickers\n2011-03-07,"CCC,BRK.B,AAA"\n2009-12-01,"AAA,BF.B, BBB"\n'
    assert parse_history(text) == [(date(2009, 12, 1), ("AAA", "BBB", "BF-B")),
                                   (date(2011, 3, 7), ("AAA", "BRK-B", "CCC"))]


@pytest.mark.parametrize("text", ["", "date,tickers\n", "day,names\n2010-01-01,A\n", "date,tickers\nsoon,A\n"])
def test_an_unreadable_history_says_so(text):
    with pytest.raises(Unreadable):
        parse_history(text)


def test_sectors_come_from_the_table_with_symbol_and_gics_sector_columns():
    html = ("<table class='wikitable'><tr><th>Date</th><th>Added</th></tr><tr><td>x</td><td>y</td></tr></table>"
            "<table class='wikitable'><tr><th>Symbol</th><th>GICS Sector[1]</th></tr>"
            "<tr><td>BRK.B</td><td>Financials</td></tr><tr><td>XX</td><td>Not a sector</td></tr></table>")
    assert parse_sectors(html) == {"BRK-B": "Financials"}
    with pytest.raises(Unreadable):
        parse_sectors("<p>moved</p>")
