import json
from pathlib import Path

from oracle.market.yahoo import YahooProfile, parse_profile

FIXTURES = Path(__file__).parent.parent / "fixtures" / "yahoo"


def captured(symbol: str) -> dict:
    """quoteSummary price and assetProfile modules, captured 2026-09-24."""
    body = json.loads((FIXTURES / f"{symbol}.json").read_text(encoding="utf-8"))
    return body["quoteSummary"]["result"][0]


def test_a_korean_home_line_is_read_whole():
    p = parse_profile("373220.KS", captured("373220.KS"))
    assert p == YahooProfile(
        symbol="373220.KS", name="LG Energy Solution, Ltd.", quote_type="EQUITY", exchange="KSC", currency="KRW",
        market_cap=82250997694464.0, country="South Korea", sector="Industrials",
        industry="Electrical Equipment & Parts", website="https://www.lgensol.com", summary=p.summary,
    )
    assert p.summary.startswith("LG Energy Solution")


def test_a_pence_line_keeps_its_quoted_currency():
    p = parse_profile("BT-A.L", captured("BT-A.L"))
    assert (p.name, p.exchange, p.currency, p.country) == ("BT Group plc", "LSE", "GBp", "United Kingdom")


def test_a_fund_says_so_and_has_no_profile():
    p = parse_profile("SPY", captured("SPY"))
    assert (p.quote_type, p.exchange, p.sector, p.country) == ("ETF", "PCX", None, None)


def test_missing_modules_leave_fields_empty():
    p = parse_profile("X", {"price": {"shortName": "Xco"}})
    assert (p.name, p.quote_type, p.market_cap, p.country) == ("Xco", None, None, None)
