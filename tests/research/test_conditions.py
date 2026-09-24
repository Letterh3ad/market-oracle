import pytest

from oracle.research.conditions import EMPTY, FACTS, REPEATS, condition_problem

# The 2026-08-24 replays' verdicts (oracle.db runs 68, 70, 71): each restated evidence where
# the condition belongs.
REPLAYS = {
    "MSFT": ("Microsoft's strong cloud revenue growth (27% YoY), Azure growth (41% YoY), and $678 billion commercial "
             "remaining performance obligation demonstrate robust demand and long-term momentum, supported by AI "
             "innovation and major strategic deals, all of which are cited across multiple sources as key drivers "
             "of stock performance and investor confidence.",
             "The company's cloud revenue grew 27% year-over-year to $214.4 billion in fiscal year 2026, with Azure "
             "and other cloud services up 41%, and a commercial remaining performance obligation of $678 billion "
             "reflecting strong long-term demand. This is backed by robust AI innovation, including in-house AI "
             "models and security platforms, and major strategic partnerships like the $9.7B U.S. military "
             "contract and $278M Macquarie deal, all of which support sustained growth and market momentum. "
             "Despite short-term legal noise, these are not material to the company's core performance or "
             "trajectory."),
    "AZN.L": ("AstraZeneca's stock is supported by strong Q2 earnings beats, robust sales from cancer drugs "
              "(especially Enhertu and breast cancer therapies in EU and India), and multiple regulatory approvals, "
              "all of which are cited across multiple sources and align with positive technical momentum and "
              "investor sentiment. These fundamentals are consistent and not driven by speculative merger rumors, "
              "which have been widely debunked and dismissed by analysts and market participants.",
              "AstraZeneca's Q2 2026 earnings beat and robust sales from cancer drugs\u2014particularly Enhertu and "
              "breast cancer therapies in EU and India\u2014demonstrate resilient profitability and market demand, "
              "supported by multiple regulatory approvals and positive investor sentiment. Hedge funds are "
              "bullish, and the stock has climbed on this performance, indicating strong confidence in the "
              "company's self-sustained growth through innovation and strategic partnerships, not M&A "
              "speculation."),
    "7203.T": ("Toyota's strong profitability in Q2 2026, with earnings exceeding estimates by 43.77%, is driven by "
               "a weak yen and hybrid/EV sales, supported by multiple sources, and is reinforced by strategic "
               "initiatives like a \u00a51 trillion share buyback, new vehicle launches, and leadership changes in "
               "supply chain, indicating long-term operational resilience and innovation.",
               "Toyota's Q2 2026 earnings exceed estimates by 43.77% due to a weak yen and strong hybrid/EV sales, "
               "as cited in a3168, a3170, a3183, a3193, and a452 \u2014 this claim is directly contradicted by "
               "evidence showing that the profit uplift is attributable to currency effects, not operational or "
               "margin improvements, and that vehicle sales volume declined, as noted in a3194 and a3191."),
}


@pytest.mark.parametrize("symbol", REPLAYS)
def test_a_condition_that_restates_the_evidence_is_rejected(symbol):
    reason, condition = REPLAYS[symbol]
    assert condition_problem(condition, reason) == FACTS


@pytest.mark.parametrize("condition", [
    "Guidance cut", "Guidance is cut", "a guidance cut", "Next quarter misses", "margin below 20%",
    "loss of the contract", "an FDA rejection", "The FDA rejects the drug", "the deal is not approved",
    "Q3 revenue under $60B", "The stock closes below $400", "a dividend cut", "Azure growth slows next quarter",
    "Toyota loses its lead in hybrids", "The yen strengthens past 140 per dollar", "a recall of the RAV4",
    "Revenue falls short of guidance", "the CEO resigns", "Cloud revenue growth stalls or the full-year profit "
    "outlook is cut further, indicating that AI-driven revenue is not materializing as expected.",
    "A significant regulatory penalty or enforcement action is issued against JPMorgan Chase in Europe within the "
    "next 30 days that directly impacts its core banking operations.",
    "If the share price drops 10% from here", "Enhertu approval in China is delayed",
    "P&G reports a 10% or greater increase in core EPS for fiscal 2027",
    "Tesla's Cybercab is approved for sale in the U.S. and regulatory investigations are resolved",
    "Guidance is raised", "Margins improve", "an analyst upgrade", "The stock rebounds to $20",
    "Q3 revenue of $58B", "A stock price of $380", "Enhertu sales flat in Q3", "iPhone unit sales flat year over year",
    "Market share stays flat", "China demand disappoints", "A new competitor product launch",
    "Regulatory approval takes longer than expected", "Margins remain unchanged", "Growth is slower than 5%",
    "Deliveries come in at 400,000", "Headcount of 70 thousand", "A P/E of 40x", "Net debt at EUR 3 billion",
    "a missed earnings date", "a missed dividend payment", "Margins reach a new high", "Growth stalling", "Margins worsen",
])
def test_a_genuine_condition_passes_in_any_phrasing(condition):
    assert condition_problem(condition, "Beat and momentum [a1], revenue of $50B.") is None


def test_a_condition_that_copies_a_sentence_of_the_reason_is_rejected():
    reason = "Azure demand will keep rising as AI spending grows [a1]. Margins stay above 40%."
    for condition in ("Azure demand will keep rising as AI spending grows.",
                      "Unless something changes: margins stay above 40%!"):
        assert condition_problem(condition, reason) == REPEATS
    assert condition_problem("Margins fall below 40%", reason) is None


@pytest.mark.parametrize("condition", [
    "Revenue of $50B", "Revenue grew to $58B", "Sales declined 4%", "The stock rose 12%",
])
def test_a_figure_is_a_threshold_only_when_new_and_not_told_in_the_past_tense(condition):
    assert condition_problem(condition, "Beat and momentum [a1], revenue of $50B.") == FACTS


def test_declines_is_a_condition_and_declined_a_fact():
    assert condition_problem("Vehicle sales volume declines", "") is None
    assert condition_problem("Vehicle sales volume declined", "") == FACTS


@pytest.mark.parametrize("condition, reason", [(None, "x"), ("", "x"), ("  ", "x"), ("Guidance cut", None)])
def test_missing_input_is_a_clean_answer(condition, reason):
    assert condition_problem(condition, reason) == (EMPTY if not (condition or "").strip() else None)
