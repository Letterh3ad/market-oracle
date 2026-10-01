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


FILED = "https://www.sec.gov/Archives/edgar/data/320193/000032019326000018"
INDEX = f"{FILED}/0000320193-26-000018-index.htm"


def index_rows(*rows: tuple[str, str, str]) -> str:
    """(description, href, type) per document, laid out like EDGAR's filing index."""
    cells = "".join(f'<tr><td scope="row">{n}</td><td scope="row">{d}</td><td scope="row"><a href="{h}">x</a></td>'
                    f'<td scope="row">{t}</td><td scope="row">100</td></tr>' for n, (d, h, t) in enumerate(rows, 1))
    return f'<table class="tableFile" summary="Document Format Files">{cells}</table>'


def test_the_filing_index_sits_beside_the_document_named_by_its_accession_number():
    assert edgar.index_url(f"{FILED}/aapl-20260730.htm") == INDEX


def test_the_filing_index_names_its_ex_99_1_document():
    found = edgar.find_exhibit((FIXTURES / "aapl-8k-index.htm").read_text(encoding="utf-8"), INDEX)
    assert found == edgar.Exhibit("EX-99.1", f"{FILED}/a8-kex991q3202606272026.htm", "EX-99.1")
    assert found.label == "EX-99.1"


def test_ex_99_1_wins_over_other_press_exhibits_and_without_it_the_first_ex_99_is_taken():
    both = index_rows(("Presentation", f"{FILED}/ex992.htm", "EX-99.2"),
                      ("Press release", f"{FILED}/ex991.htm", "EX-99.1"))
    assert edgar.find_exhibit(both, INDEX).url == f"{FILED}/ex991.htm"
    later = index_rows(("Main", f"/ix?doc={FILED.removeprefix('https://www.sec.gov')}/d.htm", "8-K"),
                       ("Press release", "ex99.htm", "EX-99"), ("Slides", "ex992.htm", "EX-99.2"))
    found = edgar.find_exhibit(later, INDEX)
    assert found.url == f"{FILED}/ex99.htm" and found.label == "EX-99 Press release"
    padded = index_rows(("Slides", "ex992.htm", "EX-99.2"), ("Press release", "ex9901.htm", "EX-99.01"))
    assert edgar.find_exhibit(padded, INDEX).url == f"{FILED}/ex9901.htm"


def test_index_cells_are_read_as_text_with_their_entities_decoded():
    found = edgar.find_exhibit(index_rows(("Q3 results &amp; outlook", "ex991.htm", "EX-99.1")), INDEX)
    assert found.label == "EX-99.1 Q3 results & outlook"


def test_no_press_exhibit_or_one_outside_the_filing_is_none():
    assert edgar.find_exhibit(index_rows(("Main", "d.htm", "8-K"), ("Deal", "ex10.htm", "EX-10.1")), INDEX) is None
    elsewhere = index_rows(("Press release", "https://evil.test/ex991.htm", "EX-99.1"),
                           ("Press release", "/Archives/edgar/data/320193/000032019399000001/ex991.htm", "EX-99.1"))
    assert edgar.find_exhibit(elsewhere, INDEX) is None
    assert edgar.find_exhibit("<html>not an index</html>", INDEX) is None


def test_results_or_a_press_release_point_at_an_exhibit_other_items_do_not():
    for item in ("2.02 Results of Operations", "7.01 Regulation FD Disclosure", "8.01 Other Events"):
        assert edgar.reads_exhibit(f"Cover\nItem {item}.\nSee Exhibit 99.1.\nItem 9.01 Exhibits")
    assert not edgar.reads_exhibit("Cover\nItem 5.02 Departure of Directors.\nItem 9.01 Exhibits")
    assert not edgar.reads_exhibit("No items at all")


def test_an_earnings_8k_reads_as_its_labelled_exhibit_then_its_other_items():
    exhibit = text_of("aapl-ex991.htm")
    kept = edgar.with_exhibit(edgar.Exhibit("EX-99.1", "u", "Press release"), exhibit, text_of("aapl-8k.htm"))
    assert kept.startswith("EX-99.1 Press release\nApple reports fourth quarter results\nRecord September quarter")
    assert "retire as Chief Operating Officer" in kept
    for left_out in ("A copy is furnished", "Inline XBRL", "emerging growth", "duly caused"):
        assert left_out not in kept
    assert len(edgar.with_exhibit(edgar.Exhibit("EX-99.1", "u", ""), exhibit * 100, "", cap=800)) <= 800


def test_a_press_item_that_says_more_than_a_pointer_is_kept_after_the_exhibit():
    event = "An investor day was held; " * 40
    text = f"Cover\nItem 8.01 Other Events.\n{event}\nItem 7.01 Regulation FD.\nSee Exhibit 99.1.\nItem 9.01 Exhibits"
    kept = edgar.with_exhibit(edgar.Exhibit("EX-99.1", "u", ""), "Press release text.", text)
    assert kept.startswith("EX-99.1\nPress release text.\n\nItem 8.01 Other Events.\nAn investor day")
    assert "See Exhibit 99.1" not in kept


def test_a_results_item_that_only_points_at_the_exhibit_under_the_usual_legend_is_dropped():
    item = ("Item 2.02 Results of Operations and Financial Condition.\n"
            "On July 30, 2026, Apple Inc. issued a press release regarding its financial results for its third fiscal "
            "quarter ended June 27, 2026. A copy of the press release is furnished as Exhibit 99.1 to this Current "
            "Report on Form 8-K.\n"
            "The information contained in this Item 2.02 and in the accompanying Exhibit 99.1 shall not be deemed "
            "filed for purposes of Section 18 of the Securities Exchange Act of 1934, as amended, nor shall it be "
            "deemed incorporated by reference in any filing under the Securities Act of 1933, as amended, or the "
            "Exchange Act, except as shall be expressly set forth by specific reference in such a filing.")
    assert len(item) >= edgar.MIN_SECTIONS
    kept = edgar.with_exhibit(edgar.Exhibit("EX-99.1", "u", ""), "Press release text.", f"Cover\n{item}\nItem 9.01 X")
    assert kept == "EX-99.1\nPress release text."


CITI = (FIXTURES / "citi-10q.txt").read_text(encoding="utf-8")


def test_a_report_without_item_headings_is_read_from_its_mdna_not_its_cover_or_contents():
    kept = key_text("10-Q", CITI)
    assert kept.startswith("MANAGEMENT’S DISCUSSION AND ANALYSIS OF FINANCIAL CONDITION\nAND RESULTS OF OPERATIONS\n"
                           "EXECUTIVE SUMMARY")
    assert "Citigroup’s average loans were $785 billion" in kept
    assert "Indicate by check mark" not in kept and "Citi’s Multiyear Transformation\n9" not in kept


def test_a_report_without_item_or_mdna_headings_skips_its_cover_and_contents():
    words = ("MANAGEMENT’S DISCUSSION", "AND RESULTS OF OPERATIONS", "EXECUTIVE SUMMARY", "RESULTS OF OPERATIONS")
    plain = "\n".join(line for line in CITI.split("\n") if not line.upper().startswith(words))
    for form in ("10-K", "8-K", "DEF 14A"):
        kept = key_text(form, plain)
        assert kept.startswith("This Quarterly Report on Form 10-Q should be read"), form
        assert "check mark" not in kept and "Balance Sheet Overview\n12" not in kept


def _recent(*rows):
    names = ("form", "filingDate", "items", "acceptanceDateTime")
    return {"filings": {"recent": {n: [r[i] for r in rows] for i, n in enumerate(names)}}}


def test_earnings_releases_are_8ks_with_item_2_02_timed_against_the_open():
    body = _recent(
        ("8-K", "2026-07-30", "2.02,9.01", "2026-07-30T16:31:02.000Z"),
        ("8-K", "2026-06-02", "5.07", "2026-06-02T16:00:00.000Z"),
        ("8-K/A", "2026-05-02", "2.02", "2026-05-02T08:00:00.000Z"),
        ("10-Q", "2026-05-01", "", "2026-05-01T06:00:00.000Z"),
        ("8-K", "2026-05-01", "2.02,7.01,9.01", "2026-05-01T12:45:00.000Z"),
        ("8-K", "2026-01-29", "2.02", ""),
    )
    assert edgar.parse_earnings(body) == [
        edgar.Release(date(2026, 7, 30), False),
        edgar.Release(date(2026, 5, 1), True),
        edgar.Release(date(2026, 1, 29), None),
    ]


def test_two_earnings_8ks_on_one_day_are_one_release_timed_by_the_first():
    body = _recent(("8-K", "2026-07-30", "2.02", "2026-07-30T16:31:02.000Z"),
                   ("8-K", "2026-07-30", "2.02,9.01", "2026-07-30T11:00:00.000Z"))
    assert edgar.parse_earnings(body) == [edgar.Release(date(2026, 7, 30), True)]


def test_a_filing_accepted_after_1730_is_released_on_its_acceptance_day():
    body = _recent(("8-K", "2026-07-31", "2.02", "2026-07-30T22:05:00.000Z"))
    assert edgar.parse_earnings(body) == [edgar.Release(date(2026, 7, 30), False)]


def test_acceptance_stamps_are_utc_and_read_in_new_york_time():
    # JPM's 8-K 0001628280-26-048078: the filing index says accepted 06:30:38 (New York); the JSON says 10:30:38Z.
    jpm = _recent(("8-K", "2026-07-14", "2.02,9.01", "2026-07-14T10:30:38.000Z"))
    assert edgar.parse_earnings(jpm) == [edgar.Release(date(2026, 7, 14), True)], "pre-market, not after the open"
    evening = _recent(("8-K", "2026-07-31", "2.02", "2026-07-31T00:10:00.000Z"))
    assert edgar.parse_earnings(evening) == [edgar.Release(date(2026, 7, 30), False)], "20:10 New York the day before"
    winter = _recent(("8-K", "2026-01-15", "2.02", "2026-01-15T14:29:00.000Z"))
    assert edgar.parse_earnings(winter) == [edgar.Release(date(2026, 1, 15), True)], "09:29 EST"


def test_a_submissions_body_without_items_has_no_releases():
    assert edgar.parse_earnings({"filings": {"recent": {"form": ["8-K"], "filingDate": ["2026-07-30"]}}}) == []
    assert edgar.parse_earnings([]) == []


def test_a_reports_cover_names_each_trading_symbol_it_tags():
    from tests.fakes.sec import cover

    assert edgar.trading_symbols(cover("META")) == ("META",)
    assert edgar.trading_symbols(cover("GOOGL", "GOOG")) == ("GOOGL", "GOOG")
    assert edgar.trading_symbols("<html>Document and Entity Information</html>") == ()


def test_reports_are_periodic_filings_oldest_first_named_by_their_document_when_so_named():
    columns = {"form": ["10-Q", "8-K", "10-K", "10-Q/A", "10-Q"],
               "filingDate": ["2022-07-28", "2022-06-01", "2022-02-03", "2022-01-01", "2021-10-26"],
               "accessionNumber": ["a5", "a4", "a3", "a2", "a1"],
               "primaryDocument": ["meta-20220630.htm", "x.htm", "fb-20211231.htm", "y.htm", "d371464d10q.htm"]}
    reports = edgar.parse_reports(columns)
    assert [(r.form, r.filed, r.document_ticker) for r in reports] == [
        ("10-Q", date(2021, 10, 26), None), ("10-K", date(2022, 2, 3), "FB"), ("10-Q", date(2022, 7, 28), "META")]
    assert edgar.Report("10-Q", date(2021, 1, 1), "a", "wltw-10q_20210930.htm").document_ticker == "WLTW"


def test_older_pages_between_two_days_and_the_filers_first_filing_day():
    body = {"filings": {"recent": {"filingDate": ["2026-01-02", "2024-07-01"]}, "files": [
        {"name": "p1.json", "filingFrom": "2017-05-17", "filingTo": "2024-06-15"},
        {"name": "p2.json", "filingFrom": "2005-05-06", "filingTo": "2017-05-15"}]}}
    assert edgar.pages_between(body, date(2018, 1, 1), date(2019, 1, 1)) == [f"{edgar.SUBMISSIONS}/p1.json"]
    assert edgar.first_filed(body) == date(2005, 5, 6)
    assert edgar.first_filed({"filings": {"recent": {"filingDate": ["2012-06-22"]}}}) == date(2012, 6, 22)
