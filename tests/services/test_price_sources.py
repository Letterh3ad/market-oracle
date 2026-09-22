from datetime import datetime, timezone

import httpx
import pytest

from oracle.config import Config, Keys, PricesConfig, load_config
from oracle.services.context import open_context
from oracle.services.errors import PriceUnavailable
from oracle.services.prices_service import price_history, price_sources, update_listing
from oracle.services.universe_service import refresh_universe
from tests.fakes.clock import FakeClock
from tests.fakes.eodhd import FakeEodhd
from tests.fakes.financedatabase import FakeFinanceDatabase, equity
from tests.fakes.web import FakeWeb
from tests.fakes.yahoo import FakeYahoo, bar

NOW = datetime(2026, 9, 22, 12, 0, tzinfo=timezone.utc)
KEY = "sk-live-0123456789"


@pytest.fixture
def yahoo():
    y = FakeYahoo()
    y.add("AAPL", "USD", "America/New_York", [bar("2026-09-18", 232.0)])
    return y


@pytest.fixture
def eodhd():
    e = FakeEodhd(KEY)
    e.add("AAPL.US", [("2026-09-17", 231.0, 230.0, 500), ("2026-09-18", 232.0, 231.0, 600)])
    e.add("BP.LSE", [("2026-09-18", 542.3, 540.0, 700)])
    return e


@pytest.fixture
def web(yahoo, eodhd):
    universe = [equity("AAPL", "Apple Inc.", currency="USD"), equity("BP.L", "BP p.l.c.", "United Kingdom", "LSE", currency="GBP")]
    return FakeWeb(FakeFinanceDatabase(universe), yahoo, eodhd)


def context(tmp_path, web, **config):
    return open_context(Config(db_path=tmp_path / "o.db", **config), None, FakeClock(NOW), web.transport)


@pytest.fixture
async def keyed(tmp_path, web):
    with context(tmp_path, web, keys=Keys({"eodhd": KEY})) as c:
        await refresh_universe(c)
        yield c


@pytest.fixture
async def unkeyed(tmp_path, web):
    with context(tmp_path, web) as c:
        await refresh_universe(c)
        yield c


def test_without_a_key_eodhd_is_listed_but_disabled(unkeyed):
    rows = {s.name: s for s in price_sources(unkeyed)}
    assert (rows["yahoo"].position, rows["yahoo"].enabled) == (1, True)
    assert (rows["eodhd"].position, rows["eodhd"].enabled, rows["eodhd"].reason) == (2, False, "no key")


async def test_without_a_key_a_yahoo_failure_is_final(unkeyed, yahoo, web):
    yahoo.fail["AAPL"] = 500
    with pytest.raises(PriceUnavailable, match="yahoo"):
        await update_listing(unkeyed, "AAPL")
    assert web.requests_to("eodhd.com") == []


async def test_with_a_key_eodhd_fills_in_when_yahoo_fails(keyed, yahoo, web):
    yahoo.fail["AAPL"] = 500
    result = await update_listing(keyed, "AAPL")
    assert (result.added, result.source) == (2, "eodhd")
    last = price_history(keyed, "AAPL")[-1]
    assert (last.close, last.adj_close, last.volume, last.currency, last.source) == (232.0, 231.0, 600, "USD", "eodhd")
    [req] = web.requests_to("eodhd.com")
    assert req.url.path == "/api/eod/AAPL.US"


async def test_with_a_key_eodhd_fills_in_when_yahoo_has_no_data(keyed, yahoo):
    del yahoo.series["AAPL"]
    assert (await update_listing(keyed, "AAPL")).source == "eodhd"


async def test_the_configured_order_decides_who_goes_first(tmp_path, web):
    with context(tmp_path, web, keys=Keys({"eodhd": KEY}), prices=PricesConfig(("eodhd", "yahoo"))) as c:
        await refresh_universe(c)
        assert (await update_listing(c, "AAPL")).source == "eodhd"


async def test_london_prices_from_eodhd_are_pence_converted_to_pounds(keyed):
    result = await update_listing(keyed, "BP.L")
    [b] = price_history(keyed, "BP.L")
    assert (result.source, b.currency, b.close) == ("eodhd", "GBP", pytest.approx(5.423))


async def test_a_rejected_key_is_reported_without_the_key(keyed, yahoo, eodhd):
    yahoo.fail["AAPL"] = 500
    eodhd.key = "someone-else"
    with pytest.raises(PriceUnavailable, match="eodhd: key rejected") as err:
        await update_listing(keyed, "AAPL")
    assert KEY not in str(err.value)


async def test_a_transport_error_quoting_the_url_never_leaks_the_key(keyed, yahoo, eodhd):
    yahoo.fail["AAPL"] = 500
    eodhd.explode_with = httpx.ConnectError(f"cannot reach https://eodhd.com/api/eod/AAPL.US?api_token={KEY}")
    with pytest.raises(PriceUnavailable) as err:
        await update_listing(keyed, "AAPL")
    assert KEY not in str(err.value)


def test_keys_come_from_the_file_or_the_environment_and_never_print(tmp_path, monkeypatch):
    cfg = tmp_path / "oracle.toml"
    cfg.write_text('[keys]\neodhd = "from-file"\n', encoding="utf-8")
    monkeypatch.delenv("ORACLE_EODHD_KEY", raising=False)
    config, _ = load_config(cfg)
    assert config.keys.get("eodhd") == "from-file"
    monkeypatch.setenv("ORACLE_EODHD_KEY", "from-env")
    config, _ = load_config(cfg)
    assert config.keys.get("eodhd") == "from-env"
    assert "from-env" not in repr(config)


def test_an_unknown_key_name_is_refused(tmp_path):
    from oracle.config import ConfigError

    cfg = tmp_path / "oracle.toml"
    cfg.write_text('[keys]\neodhdd = "x"\n', encoding="utf-8")
    with pytest.raises(ConfigError, match="eodhdd"):
        load_config(cfg)
