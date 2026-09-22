from functools import partial

import pytest

from oracle.cli import main as cli_main
from oracle.services.context import open_context
from tests.fakes.financedatabase import BASIC, FakeFinanceDatabase


@pytest.fixture
def run(tmp_path, capsys, monkeypatch):
    """`oracle --config ... <argv>` against a fake FinanceDatabase, fast-paced."""
    cfg = tmp_path / "oracle.toml"
    cfg.write_text("[sources.financedatabase]\nrate_per_second = 1000\n", encoding="utf-8")
    fdb = FakeFinanceDatabase(BASIC)
    monkeypatch.setattr(cli_main, "open_context", partial(open_context, transport=fdb.transport))

    def _run(*argv):
        code = cli_main.main(["--config", str(cfg), *argv])
        out, err = capsys.readouterr()
        return code, out, err

    _run.fdb = fdb
    return _run


def test_status_before_a_refresh_says_how_to_load(run):
    code, out, _ = run("universe", "status")
    assert code == 0
    assert "oracle universe refresh" in out


def test_refresh_then_status_shows_counts_and_commit(run):
    code, out, _ = run("universe", "refresh")
    assert code == 0
    assert "7 listings" in out and "4 companies" in out
    _, out, _ = run("universe", "status")
    assert "abc123" in out
    assert "1 delisted" in out


def test_a_failed_refresh_is_an_error_without_a_traceback(run):
    run.fdb.files["equities.bz2"] = 404
    code, _, err = run("universe", "refresh")
    assert code == 1
    assert "404" in err
    assert "Traceback" not in err


def test_search_prints_one_row_per_company(run):
    run("universe", "refresh")
    code, out, _ = run("universe", "search", "apple")
    assert code == 0
    rows = [line for line in out.splitlines()[1:] if line.strip()]
    assert len(rows) == 1
    assert rows[0].split()[0] == "AAPL"
    assert "+2 listings" in rows[0]


def test_search_flags_reach_the_service(run):
    run("universe", "refresh")
    _, out, _ = run("universe", "search", "apple", "--all-listings", "--limit", "2")
    assert len([line for line in out.splitlines()[1:] if line.strip()]) == 2
    _, out, _ = run("universe", "search", "oldco", "--include-delisted")
    assert "OLD.F" in out and "delisted" in out


def test_search_with_no_hits_says_so(run):
    run("universe", "refresh")
    code, out, _ = run("universe", "search", "zzzz")
    assert code == 0
    assert "no matches" in out


def test_search_before_a_refresh_is_an_error_saying_how_to_load(run):
    code, _, err = run("universe", "search", "apple")
    assert code == 1
    assert "oracle universe refresh" in err


def _symbols(out):
    return [line.split()[0] for line in out.splitlines()[1:] if line.strip()]


def test_search_without_a_query_filters_and_sorts(run):
    run("universe", "refresh")
    _, out, _ = run("universe", "search", "--country", "japan", "--country", "United States")
    assert _symbols(out) == ["AAPL", "7203.T"]
    _, out, _ = run("universe", "search", "--sort", "name", "--desc", "--limit", "1")
    assert _symbols(out) == ["7203.T"]
    _, out, _ = run("universe", "search", "--market-cap", "mega", "--exchange", "GER")
    assert _symbols(out) == ["APC.DE"]


def test_a_typo_in_a_filter_is_an_error_with_a_suggestion(run):
    run("universe", "refresh")
    code, _, err = run("universe", "search", "--country", "Japna")
    assert code == 1
    assert "Japan" in err


def test_facets_list_values_with_company_counts(run):
    run("universe", "refresh")
    code, out, _ = run("universe", "facets", "country")
    assert code == 0
    counts = dict(line.rsplit(None, 1) for line in out.splitlines()[1:])
    assert {k.strip(): v for k, v in counts.items()} == {
        "United States": "1", "Japan": "1", "Germany": "1"
    }


def test_exchange_facets_show_the_market_name(run):
    run("universe", "refresh")
    _, out, _ = run("universe", "facets", "exchange")
    ger = next(line for line in out.splitlines() if line.startswith("GER"))
    assert ger.split() == ["GER", "1", "XETRA"]
