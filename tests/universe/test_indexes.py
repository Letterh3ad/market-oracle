from pathlib import Path

import pytest

from oracle.universe.indexes import BY_CODE, INDEXES, IndexSpec, Member, Unreadable, parse_members, yahoo_symbol

FIXTURES = Path(__file__).parent.parent / "fixtures" / "indexes"


def page(code: str) -> str:
    return (FIXTURES / f"{code}.html").read_text(encoding="utf-8")


# Captured 2026-09-24. A page that drifts from these fails its test, never the others.
COUNTS = {
    "sp500": 503, "sp400": 400, "sp600": 603, "tsx60": 60, "ftse100": 100, "ftse250": 250, "dax": 40, "mdax": 50,
    "cac40": 40, "aex": 25, "bel20": 20, "smi": 20, "ibex35": 35, "ftsemib": 40, "psi": 18, "omxs30": 30,
    "omxh25": 25, "omxc25": 25, "obx": 25, "nikkei225": 225, "kospi200": 200, "twse50": 50, "hsi": 85,
    "csi300": 300, "nifty50": 50, "niftynext50": 50, "asx200": 200, "nzx50": 50, "sti": 30, "klci": 30,
    "set50": 51, "lq45": 44,
}


def test_every_index_has_a_captured_page():
    assert set(COUNTS) == {spec.code for spec in INDEXES}


@pytest.mark.parametrize("code", sorted(COUNTS))
def test_each_captured_page_yields_its_members_as_yahoo_symbols(code):
    members = parse_members(BY_CODE[code], page(code))
    assert len(members) == COUNTS[code]
    assert all(m.name and m.symbol == m.symbol.upper() for m in members)


@pytest.mark.parametrize("code, symbol, name", [
    ("kospi200", "373220.KS", "LG Energy Solution"),
    ("kospi200", "028260.KS", "Samsung C&T"),
    ("kospi200", "066570.KS", "LG Electronics"),
    ("hsi", "0005.HK", "HSBC Holdings plc"),
    ("sp500", "BRK-B", "Berkshire Hathaway"),
    ("ftse100", "BT-A.L", "BT Group"),
    ("tsx60", "BIP-UN.TO", "Brookfield Infrastructure Partners"),
    ("omxc25", "NOVO-B.CO", "Novo Nordisk"),
    ("omxs30", "ADDT-B.ST", "Addtech B"),
    ("dax", "AIR.PA", "Airbus"),
    ("nikkei225", "7203.T", "Toyota Motor"),
    ("csi300", "300750.SZ", "CATL"),
    ("csi300", "600519.SS", "Kweichow Moutai"),
    ("twse50", "2330.TW", "台積電"),
    ("sti", "D05.SI", "DBS Group Holdings"),
    ("lq45", "BBCA.JK", "Bank Central Asia Tbk."),
    ("klci", "1155.KL", "Malayan Banking Berhad"),
])
def test_members_are_read_with_their_names(code, symbol, name):
    found = {m.symbol: m for m in parse_members(BY_CODE[code], page(code))}
    assert found[symbol].name.startswith(name)


def test_an_isin_column_is_kept():
    found = {m.symbol: m for m in parse_members(BY_CODE["ftsemib"], page("ftsemib"))}
    assert found["A2A.MI"].isin == "IT0001233417"


def test_a_page_without_a_member_table_is_unreadable():
    with pytest.raises(Unreadable, match="KOSPI 200"):
        parse_members(BY_CODE["kospi200"], "<table class='wikitable'><tr><th>Year</th><th>Close</th></tr></table>")


def test_a_table_inside_a_member_cell_is_not_read_as_members():
    html = ("<table class='wikitable'><tr><th>Ticker</th><th>Name</th></tr>"
            "<tr><td>SEHK: 5</td><td>HSBC<table class='wikitable'><tr><td>SEHK: 9</td><td>x</td></tr></table></td></tr>"
            "</table>")
    assert [m.symbol for m in parse_members(BY_CODE["hsi"], html)] == ["0005.HK"]


def test_a_member_listed_twice_is_kept_once():
    html = ("<table class='wikitable'><tr><th>Ticker</th><th>Company</th></tr>"
            "<tr><td>AAA</td><td>A</td></tr><tr><td>AAA</td><td>A again</td></tr></table>")
    assert parse_members(BY_CODE["sp500"], html) == [Member("AAA", "A")]


@pytest.mark.parametrize("cell, spec, symbol", [
    ("BP.", IndexSpec("x", "x", "x", ".L"), "BP.L"),
    ("SEHK: 700", IndexSpec("x", "x", "x", ".HK", pad=4), "0700.HK"),
    ("ADS.DE", IndexSpec("x", "x", "x", ""), "ADS.DE"),
    ("BF.B", IndexSpec("x", "x", "x", ""), "BF-B"),
    ("NESN[1]", IndexSpec("x", "x", "x", ".SW"), "NESN.SW"),
    ("", IndexSpec("x", "x", "x", ".L"), None),
    ("n/a", IndexSpec("x", "x", "x", ".L"), None),
])
def test_tickers_become_yahoo_symbols(cell, spec, symbol):
    assert yahoo_symbol(spec, cell) == symbol
