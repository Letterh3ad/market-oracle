"""Form 4 trades from SEC's data sets, typed filings from the submissions, and the pure rules of
the insider signal and the typed-event study."""

import io
import zipfile
from datetime import date

from oracle.research import insider
from oracle.research.insider import Filing, Trade

D = date.fromisoformat
SUB = "ACCESSION_NUMBER\tFILING_DATE\tPERIOD_OF_REPORT\tDOCUMENT_TYPE\tISSUERCIK\tISSUERTRADINGSYMBOL"
OWN = "ACCESSION_NUMBER\tRPTOWNERCIK\tRPTOWNERNAME\tRPTOWNER_RELATIONSHIP\tRPTOWNER_TITLE"
TRN = ("ACCESSION_NUMBER\tNONDERIV_TRANS_SK\tTRANS_DATE\tTRANS_CODE\tTRANS_SHARES\tTRANS_PRICEPERSHARE\t"
       "TRANS_ACQUIRED_DISP_CD")


def dataset(sub, own, trn) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, head, rows in (("SUBMISSION.tsv", SUB, sub), ("REPORTINGOWNER.tsv", OWN, own),
                                 ("NONDERIV_TRANS.tsv", TRN, trn)):
            z.writestr(name, "\n".join([head, *("\t".join(r) for r in rows)]) + "\n")
    return buf.getvalue()


def test_parse_dataset_keeps_open_market_trades_of_original_form4s_for_known_issuers():
    data = dataset(
        [("a1", "31-MAR-2010", "30-MAR-2010", "4", "0000000001", "AAA"),
         ("a2", "31-MAR-2010", "30-MAR-2010", "4/A", "0000000001", "AAA"),
         ("a3", "01-APR-2010", "30-MAR-2010", "4", "0000000099", "ZZZ")],
        [("a1", "0000000500", "Doe Jane", "Director,Officer", "CEO"),
         ("a1", "0000000501", "Doe Trust", "Other", ""),
         ("a2", "0000000500", "Doe Jane", "Director", ""), ("a3", "0000000502", "X", "Officer", "")],
        [("a1", "1", "30-MAR-2010", "P", "100.0", "8.5", "A"),
         ("a1", "2", "30-MAR-2010", "S", "50.0", "9.0", "D"),
         ("a1", "3", "30-MAR-2010", "M", "50.0", "1.0", "A"),
         ("a1", "4", "30-MAR-2010", "P", "10.0", "", "A"),
         ("a1", "5", "30-MAR-2010", "P", "10.0", "2.0", "D"),
         ("a2", "1", "30-MAR-2010", "P", "100.0", "8.5", "A"), ("a3", "1", "30-MAR-2010", "P", "1.0", "1.0", "A")])
    trades = insider.parse_dataset(data, {1})
    assert [(t.accession, t.code, t.shares, t.price, t.owner_cik, t.role) for t in trades] == [
        ("a1", "P", 100.0, 8.5, 500, "Director,Officer"), ("a1", "S", 50.0, 9.0, 500, "Director,Officer")]
    assert trades[0].value == 850.0 and trades[0].insider and trades[0].filed == D("2010-03-31")


def test_dataset_links_from_the_page():
    page = ('<a href="/files/datastandardsinnovation/data/insider-transactions-data-sets/2026q2_form345.zip">'
            '<a href="/files/structureddata/data/insider-transactions-data-sets/2010Q1_form345.zip">')
    assert insider.dataset_links(page) == {
        "2026q2": "https://www.sec.gov/files/datastandardsinnovation/data/insider-transactions-data-sets/2026q2_form345.zip",
        "2010q1": "https://www.sec.gov/files/structureddata/data/insider-transactions-data-sets/2010Q1_form345.zip"}
    assert [insider.quarter_end(q) for q in ("2010q1", "2010q2", "2010q4")] == [D("2010-03-31"), D("2010-06-30"),
                                                                              D("2010-12-31")]


def test_parse_filings_times_in_new_york_and_skips_amendments():
    cols = {"form": ["4", "8-K", "8-K/A", "10-Q"], "accessionNumber": ["a", "b", "c", "d"],
            "filingDate": ["2020-01-03", "2020-01-03", "2020-01-03", "2020-01-03"],
            "items": ["", "2.02, 9.01", "2.02", ""],
            "acceptanceDateTime": ["2020-01-03T01:30:00.000Z", "2020-01-03T13:00:00.000Z", "", ""]}
    got = insider.parse_filings(cols, 7)
    assert got == [Filing("a", 7, "4", "", D("2020-01-03"), "2020-01-02T20:30"),
                   Filing("b", 7, "8-K", "2.02,9.01", D("2020-01-03"), "2020-01-03T08:00")]
    assert insider.known_day(D("2020-01-03"), "2020-01-02T20:30") == D("2020-01-02")
    assert insider.known_day(D("2020-01-03"), None) == D("2020-01-03")
    assert insider.item_events(got) == {"2.02": [D("2020-01-03")], "1.01": [], "2.01": []}


def trade(a, owner, filed, code="P", role="Officer"):
    return Trade(a, 1, owner, "x", role, code, D(filed), 1.0, 1.0, D(filed))


def test_insider_score_counts_distinct_insider_buyers_known_in_the_90_days_before():
    b = insider.buys([trade("a", 1, "2020-01-10"), trade("b", 1, "2020-02-01"), trade("c", 2, "2020-03-01"),
                      trade("d", 3, "2020-03-01", code="S"), trade("e", 4, "2020-03-01", role="TenPercentOwner")],
                     {"c": "2020-02-28T19:00"})
    assert b == ([D("2020-01-10"), D("2020-02-01"), D("2020-02-28")], [1, 1, 2])
    assert insider.insider_score(b, D("2020-02-28")) == 1, "known that day: from the next session only"
    assert insider.insider_score(b, D("2020-02-29")) == 2
    assert insider.insider_score(b, D("2020-04-09")) == 2, "2020-01-10 is exactly 90 days before"
    assert insider.insider_score(b, D("2020-04-10")) == 2
    assert insider.insider_score(b, D("2020-05-02")) == 1


def test_clusters_need_three_insiders_in_30_days_and_fire_at_most_once_a_month():
    days = ["2020-01-01", "2020-01-10", "2020-01-30", "2020-02-05", "2020-02-20", "2020-03-01", "2020-03-10"]
    b = insider.buys([trade(str(n), n % 3, d) for n, d in enumerate(days)], {})
    # Day 30 (01-30) has owners 0,1,2 within 01-01..01-30: fires. Next possible from 03-01.
    assert insider.clusters(b) == [D("2020-01-30"), D("2020-03-01")]
    assert insider.clusters(insider.buys([trade("a", 1, "2020-01-01"), trade("b", 2, "2020-01-15"),
                                          trade("c", 3, "2020-01-31")], {})) == [], "01-01 is 31 days back"


def test_quarter_of_a_day():
    assert [insider.quarter_of(D(d)) for d in ("2010-01-01", "2010-03-31", "2010-04-01", "2010-12-31")] == [
        "2010q1", "2010q1", "2010q2", "2010q4"]
    assert insider.quarter_start("2010q2") == D("2010-04-01")
