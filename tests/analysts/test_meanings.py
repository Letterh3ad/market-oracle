import pytest

from oracle.analysts.meanings import figure_meaning


@pytest.mark.parametrize(("analyst", "name"), [
    ("trend", "close"), ("trend", "sma50"), ("trend", "sma200"), ("trend", "momentum_12_1"),
    ("rsi", "rsi"), ("macd", "macd"), ("macd", "histogram"), ("macd", "days_since_cross"),
    ("volatility", "volatility"), ("volatility", "usual"), ("volatility", "ratio"),
    ("technicals", "trend"), ("technicals", "rsi"), ("technicals", "macd"), ("technicals", "volatility_ratio"),
    ("risk", "size"), ("risk", "size_share"), ("risk", "volatility"), ("risk", "max_drawdown"), ("risk", "corr_KO"),
    ("value", "pe_percentile"), ("value", "dividend_yield_percentile"), ("value", "peers"),
    ("quality", "roe_percentile"), ("growth", "revenue_growth_percentile"),
])
def test_every_figure_an_analyst_reports_has_a_plain_meaning(analyst, name):
    assert len(figure_meaning(analyst, name)) > 20


def test_the_same_name_means_what_its_analyst_means_by_it():
    assert figure_meaning("rsi", "rsi") != figure_meaning("technicals", "rsi")
    assert "KO" in figure_meaning("risk", "corr_KO")
    assert "P/E" in figure_meaning("value", "pe_percentile")


def test_an_unknown_figure_still_gets_an_honest_answer():
    assert figure_meaning("trend", "new_thing") == "No explanation written for this figure yet."
