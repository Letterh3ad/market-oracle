"""Plain-English meanings of the named figures behind each signal, for tooltips. Keyed by
analyst as well as name: "rsi" is a reading under the RSI analyst but a score under the
combined technicals."""

from __future__ import annotations

from oracle.analysts.factors import FACTORS

_MEANINGS = {
    ("trend", "close"): "The last closing price.",
    ("trend", "sma50"): "The average close over the last 50 trading days (about ten weeks).",
    ("trend", "sma200"): "The average close over the last 200 trading days (about ten months).",
    ("trend", "momentum_12_1"):
        "How much the price rose or fell over the past year, leaving out the latest month (0.07 is up 7%).",
    ("rsi", "rsi"):
        "Relative Strength Index, 0 to 100: how hard recent days pushed up against down. Above 70 is "
        "stretched high, below 30 stretched low.",
    ("macd", "macd"): "The 12-day price average minus the 26-day one: above zero means the short run is ahead.",
    ("macd", "histogram"): "MACD minus its own 9-day average: which way the momentum is turning.",
    ("macd", "days_since_cross"): "Trading days since MACD last crossed its own average.",
    ("volatility", "volatility"): "How much the price swings, as a yearly rate from the last month (0.25 is 25%).",
    ("volatility", "usual"): "The same swing measured over the whole last year, for comparison.",
    ("volatility", "ratio"): "Recent swings over usual ones: above 1 is choppier than normal.",
    ("technicals", "trend"): "The trend analyst's score, from -1 to +1.",
    ("technicals", "rsi"): "The RSI analyst's score, from -1 to +1.",
    ("technicals", "macd"): "The MACD analyst's score, from -1 to +1.",
    ("technicals", "volatility_ratio"): "Recent swings over usual ones; above 1 cuts the combined confidence.",
    ("risk", "size"): "Suggested position size in your base currency, smaller for bigger swings.",
    ("risk", "size_share"): "That size as a share of the paper account (0.067 is 6.7%).",
    ("risk", "volatility"): "Yearly price swing in your base currency, currency moves included.",
    ("risk", "max_drawdown"): "The worst fall from a high on record, in your base currency (-0.40 is 40% down).",
    ("value", "peers"): "How many peer companies the comparison used.",
}
for _factor in FACTORS:
    _MEANINGS[(_factor.name, "peers")] = _MEANINGS[("value", "peers")]
    for _m in _factor.metrics:
        better = "higher" if _m.higher_is_better else "lower"
        _MEANINGS[(_factor.name, f"{_m.name}_percentile")] = (
            f"Share of peers this company beats on {_m.label} ({better} is better): 0.75 beats three in four."
        )

UNKNOWN = "No explanation written for this figure yet."


def figure_meaning(analyst: str, name: str) -> str:
    if analyst == "risk" and name.startswith("corr_"):
        other = name.removeprefix("corr_")
        return (f"How closely this listing's daily moves follow {other}'s, from -1 to 1: near 1 means holding "
                "both adds little spreading of risk.")
    return _MEANINGS.get((analyst, name), UNKNOWN)
