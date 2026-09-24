"""SEC filings: which to read, and the parts of each that bear on the price."""

from datetime import date
from pathlib import Path

from oracle.research import edgar
from oracle.research.edgar import Filing, key_text, worth_reading
from oracle.research.web import page_text

FIXTURES = Path(__file__).parent.parent / "fixtures" / "edgar"


def text_of(name: str) -> str:
    return page_text((FIXTURES / name).read_text(encoding="utf-8"))[1]


def test_a_10q_keeps_the_mdna_body_not_its_table_of_contents_line_and_the_risk_factor_changes():
    kept = key_text("10-Q", text_of("aapl-10q.htm"))
    assert kept.startswith("Item 2. Management’s Discussion and Analysis")
    assert "Total net sales increased 8%" in kept and "tariff costs of about $1.5 billion" in kept
    assert "New tariffs announced in April 2026" in kept
    assert kept.index("Total net sales") < kept.index("New tariffs")
    for left_out in ("94,930", "no material changes to the Company’s market risk", "Epic Games", "88 million shares",
                     "SIGNATURES", "TABLE OF CONTENTS"):
        assert left_out not in kept


def test_a_10k_with_uppercase_headings_split_over_two_lines_is_trimmed_too():
    kept = key_text("10-K", text_of("aapl-10k.htm"))
    assert "Greater China net sales decreased 8%" in kept and "outsourcing partners" in kept
    assert "designs, manufactures" not in kept and "Interest rate" not in kept and "None." not in kept


def test_an_8k_keeps_its_item_text_without_the_exhibit_list_or_signature():
    kept = key_text("8-K", text_of("aapl-8k.htm"))
    assert kept.startswith("Item 2.02 Results of Operations")
    assert "fourth fiscal quarter" in kept and "retire as Chief Operating Officer" in kept
    for left_out in ("emerging growth", "Inline XBRL", "Press release issued", "duly caused"):
        assert left_out not in kept


def test_a_filing_with_no_headings_found_is_cut_to_the_cap_not_dropped():
    text = "Some annual report with no item headings. " * 1000
    assert key_text("10-K", text, cap=500) == text[:500]
    assert key_text("DEF 14A", text, cap=500) == text[:500]


def test_sections_share_the_cap_and_a_short_one_leaves_its_room_to_the_long_one():
    mdna = "Item 2. Management's Discussion and Analysis\n" + "Sales rose. " * 500
    risks = "Item 1A. Risk Factors\nNo material changes."
    kept = key_text("10-Q", f"{mdna}\nItem 3. Market Risk\nNone.\n{risks}\nItem 2. Unregistered Sales\nx", cap=1000)
    assert len(kept) <= 1000 and kept.endswith("No material changes.")
    assert kept.startswith("Item 2. Management's") and len(kept) > 900


def filing(form: str, filed: str, n: int = 0) -> Filing:
    return Filing(form, date.fromisoformat(filed), f"https://www.sec.gov/Archives/edgar/data/1/{n}/d.htm", form)


def test_worth_reading_is_the_latest_periodic_report_then_8ks_from_the_last_30_days():
    filings = [filing("8-K", "2026-09-20", 1), filing("4", "2026-09-19", 2), filing("8-K", "2026-09-01", 3),
               filing("10-Q/A", "2026-08-20", 4), filing("8-K", "2026-08-15", 5), filing("10-Q", "2026-08-01", 6),
               filing("10-K", "2025-10-30", 7)]
    assert [f.url[-7] for f in worth_reading(filings, date(2026, 9, 21))] == ["6", "1", "3"]
    assert [f.url[-7] for f in worth_reading(filings, date(2026, 8, 20))] == ["6", "5"]
    assert [f.url[-7] for f in worth_reading(filings, date(2026, 7, 1))] == ["7"]


def test_a_filing_title_does_not_repeat_a_description_that_is_its_form():
    assert filing("8-K", "2026-09-20").title == "8-K"
    assert Filing("10-Q", date(2026, 8, 1), "u", "Quarterly report").title == "10-Q Quarterly report"
    assert Filing("8-K", date(2026, 8, 1), "u", "").title == "8-K"
    assert edgar.Filing("8-K", date(2026, 8, 1), "u", "8-k").title == "8-K"


def test_the_hidden_inline_xbrl_header_is_not_read_as_text():
    assert text_of("aapl-8k.htm").startswith("UNITED STATES SECURITIES AND EXCHANGE COMMISSION")
