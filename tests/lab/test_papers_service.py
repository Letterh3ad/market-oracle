from dataclasses import replace
from datetime import datetime, timezone

import httpx
import pytest

from oracle.config import Config, ResearchConfig
from oracle.services import literature_service as lab
from oracle.services import papers_service as papers
from oracle.services.context import open_context
from oracle.services.errors import InvalidQuery
from tests.fakes.clock import FakeClock
from tests.fakes.open_access import FakeFiles, FakeSemanticScholar, FakeUnpaywall, recorded
from tests.fakes.pdf import make_pdf, paper
from tests.fakes.web import FakeWeb

NOW = datetime(2026, 10, 4, 9, 0, tzinfo=timezone.utc)
EMAIL = "lab-tests@example.org"
GKX = "10.1093/rfs/hhaa009"
GKX_PDF = "https://academic.oup.com/rfs/article-pdf/33/5/2223/33209812/hhaa009.pdf"
JT = "10.1111/j.1540-6261.1993.tb04702.x"
JT_TITLE = "Returns to Buying Winners and Selling Losers: Implications for Stock Market Efficiency"
PONE = "10.1371/journal.pone.0285244"
PONE_PDF = "https://journals.plos.org/plosone/article/file?id=10.1371/journal.pone.0285244&type=printable"
PONE_TITLE = ("Construction and application of computerized risk assessment model for supply chain finance under "
              "technology empowerment")


def web(files: dict | None = None) -> FakeWeb:
    return FakeWeb(FakeUnpaywall({GKX: recorded("unpaywall-open.json"), JT: recorded("unpaywall-closed.json")}),
                   FakeSemanticScholar({f"DOI:{JT}": recorded("s2-closed.json"), f"DOI:{PONE}": recorded("s2-open.json")}),
                   FakeFiles(files or {}))


def context(tmp_path, fake: FakeWeb, email: str | None = EMAIL, inbox: str | None = None):
    research = ResearchConfig(open_access_email=email, **({"inbox": inbox} if inbox else {}))
    config = Config(db_path=tmp_path / "live.db", research_db_path=tmp_path / "research.db", research=research)
    return open_context(config, tmp_path / "oracle.toml", FakeClock(NOW), fake.transport)


def record_jt_and_pone(ctx):
    lab.record(ctx, f"https://doi.org/{JT}", title=JT_TITLE, authors="Jegadeesh, Titman", year=1993,
               quality="peer-reviewed")
    lab.record(ctx, f"doi:{PONE}", title=PONE_TITLE, quality="peer-reviewed")


async def test_an_open_copy_from_unpaywall_is_read_and_kept(tmp_path):
    fake = web({GKX_PDF: paper("Empirical Asset Pricing via Machine Learning", GKX)})
    with context(tmp_path, fake) as ctx:
        papers.want(ctx, f"doi:{GKX}", note="asked by the user")
        got = await papers.fetch(ctx, f"https://doi.org/{GKX}")
        assert got.copy is not None and (got.copy.via, got.copy.url, got.copy.licence) == (
            "unpaywall", GKX_PDF, "cc-by-nc-nd")
        assert got.pages == 2 and got.chars > 1000 and not got.wanted
        assert got.source.title == "Empirical Asset Pricing via Machine Learning"
        held = papers.full_text(ctx, f"doi:{GKX}")
        assert held.via == "unpaywall" and "Line 59 of the body text" in papers.text(ctx, f"doi:{GKX}").text
        assert papers.wanted(ctx) == []
        again = await papers.fetch(ctx, GKX)
        assert again.copy is None and not again.wanted and again.note.startswith("already full-text (from unpaywall")
    (asked,) = fake.requests_to("api.unpaywall.org")
    assert asked.url.params["email"] == EMAIL and asked.url.path == f"/v2/{GKX}"
    assert fake.requests_to("api.semanticscholar.org") == []


async def test_no_legal_copy_puts_the_paper_on_the_wanted_list_and_never_fetches_a_closed_one(tmp_path):
    fake = web({"http://www.business.unr.edu/faculty/liuc/files/BADM742/Jegadeesh_Titman_1993.pdf": paper(JT_TITLE)})
    with context(tmp_path, fake) as ctx:
        record_jt_and_pone(ctx)
        got = await papers.fetch(ctx, JT)
        assert got.copy is None and got.wanted
        assert "Unpaywall: closed" in got.note and "Semantic Scholar: no open copy" in got.note
        (w,) = papers.wanted(ctx)
        assert w.source.title == JT_TITLE and w.reason == got.note and w.by == "user" and w.at.startswith("2026-10-04")
        assert papers.full_text(ctx, JT) is None
    assert fake.requests_to("www.business.unr.edu") == []


async def test_semantic_scholar_is_asked_when_unpaywall_has_nothing(tmp_path):
    fake = web({PONE_PDF: paper(PONE_TITLE, PONE)})
    with context(tmp_path, fake) as ctx:
        record_jt_and_pone(ctx)
        got = await papers.fetch(ctx, PONE, by="autopilot")
        assert got.copy.via == "semantic-scholar" and got.copy.licence == "CCBY"
        assert papers.full_text(ctx, PONE).by == "autopilot"
        assert "Unpaywall: not known" in got.note


async def test_a_copy_that_is_not_a_pdf_is_passed_over(tmp_path):
    fake = web({GKX_PDF: 403})
    with context(tmp_path, fake) as ctx:
        got = await papers.fetch(ctx, GKX)
        assert got.copy is None and got.wanted and "403" in got.note


async def test_without_an_email_unpaywall_is_skipped_and_the_note_says_where_to_set_it(tmp_path):
    fake = web({PONE_PDF: paper(PONE_TITLE, PONE)})
    with context(tmp_path, fake, email=None) as ctx:
        record_jt_and_pone(ctx)
        got = await papers.fetch(ctx, PONE)
        assert got.copy.via == "semantic-scholar" and "open_access_email" in got.note
    assert fake.requests_to("api.unpaywall.org") == []


async def test_a_source_without_a_doi_or_one_not_recorded_is_refused(tmp_path):
    with context(tmp_path, web()) as ctx:
        with pytest.raises(InvalidQuery, match="not a recorded source"):
            await papers.fetch(ctx, "10.9999/unknown")
        with pytest.raises(InvalidQuery, match="no DOI"):
            await papers.fetch(ctx, "https://ivey.uwo.ca/media/3775549/pontiff.pdf")
        with pytest.raises(InvalidQuery, match="not a recorded source"):
            papers.want(ctx, "an untitled thought")


def test_want_and_unwant_by_hand_keep_the_note(tmp_path):
    with context(tmp_path, web()) as ctx:
        record_jt_and_pone(ctx)
        w = papers.want(ctx, JT, note="library has it")
        assert w.reason == "added by hand" and w.note == "library has it"
        source_id = w.source.id
        assert [x.source.id for x in papers.wanted(ctx)] == [source_id]
        assert papers.want(ctx, f"#{source_id}").note == "library has it"
        papers.unwant(ctx, str(source_id))
        assert papers.wanted(ctx) == []
        with pytest.raises(InvalidQuery, match="not on the list"):
            papers.unwant(ctx, JT)


def drop(folder, name: str, data: bytes):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_bytes(data)


def test_the_inbox_matches_a_dropped_pdf_by_doi_and_by_title(tmp_path):
    with context(tmp_path, web()) as ctx:
        record_jt_and_pone(ctx)
        papers.want(ctx, JT)
        inbox = papers.inbox_folder(ctx)
        assert inbox == tmp_path / ".scratch" / "research-lab" / "inbox"
        drop(inbox, "download (3).pdf", paper("Returns to Buying Winners and Selling Losers", JT))
        drop(inbox, "scan.pdf", paper("Construction and Application of Computerised Risk Assessment Model for "
                                      "Supply Chain Finance under Technology Empowerment"))
        drop(inbox, "unrelated.pdf", paper("A Theory of Cheese Futures"))
        drop(inbox, "broken.pdf", b"%PDF-1.4 truncated")
        drop(inbox, "notes.txt", b"not a paper")
        scan = papers.scan_inbox(ctx)
        by_file = {r.file: r for r in scan.results}
        assert set(by_file) == {"download (3).pdf", "scan.pdf", "unrelated.pdf", "broken.pdf"}
        jt = by_file["download (3).pdf"]
        assert jt.source.title == JT_TITLE and jt.matched == f"id doi:{JT} (title 1.00)" and jt.pages == 2
        pone = by_file["scan.pdf"]
        assert pone.source.title == PONE_TITLE and pone.matched.startswith("title ")
        assert by_file["unrelated.pdf"].source is None and "no recorded source" in by_file["unrelated.pdf"].note
        assert by_file["broken.pdf"].source is None and "cannot be read" in by_file["broken.pdf"].note
        assert papers.full_text(ctx, JT).via == "inbox" and papers.full_text(ctx, JT).file.endswith("download (3).pdf")
        assert papers.wanted(ctx) == []
        assert sorted(p.name for p in inbox.iterdir() if p.is_file()) == ["broken.pdf", "notes.txt", "unrelated.pdf"]
        assert len(list((inbox / "done").iterdir())) == 2
        assert papers.scan_inbox(ctx).results[0].file in ("broken.pdf", "unrelated.pdf")


def test_an_inbox_pdf_naming_its_id_only_in_the_file_name_matches(tmp_path):
    with context(tmp_path, web()) as ctx:
        record_jt_and_pone(ctx)
        drop(papers.inbox_folder(ctx), "10.1111_j.1540-6261.1993.tb04702.x.pdf", paper("Untitled scan"))
        drop(papers.inbox_folder(ctx), "Returns to buying winners and selling losers.pdf", make_pdf([["x"]]))
        (a, b) = sorted(papers.scan_inbox(ctx).results, key=lambda r: r.file)
        assert a.source.title == JT_TITLE and a.matched == f"id doi:{JT}"
        assert b.source.title == JT_TITLE and b.matched.startswith("title ")


def test_the_inbox_folder_is_a_setting_relative_to_the_config_file(tmp_path):
    with context(tmp_path, web(), inbox="papers/drop") as ctx:
        assert papers.inbox_folder(ctx) == tmp_path / "papers" / "drop"
        ctx.config = replace(ctx.config, research=ResearchConfig(inbox=str(tmp_path / "abs")))
        assert papers.inbox_folder(ctx) == tmp_path / "abs"
        assert papers.scan_inbox(ctx).results == [] and (tmp_path / "abs").is_dir()


OWN = "10.9999/own.1"
OWN_TITLE = "Momentum in Imaginary Markets"
REPO = "https://repo.example.org/a.pdf"


class Down:
    """A host that answers one status to everything."""

    def __init__(self, host: str, status: int) -> None:
        self.hosts = (host,)
        self.status = status

    def handle(self, request):
        return httpx.Response(self.status, headers={"retry-after": "1"})


def open_at(url: str, files: dict) -> FakeWeb:
    return FakeWeb(FakeUnpaywall({OWN: {"oa_status": "green", "best_oa_location": {
        "url_for_pdf": url, "license": "cc-by", "version": "acceptedVersion"}, "oa_locations": []}}),
        FakeSemanticScholar({}), FakeFiles(files))


async def fetch_own(tmp_path, fake: FakeWeb):
    with context(tmp_path, fake) as ctx:
        lab.record(ctx, f"doi:{OWN}", title=OWN_TITLE, quality="peer-reviewed")
        return await papers.fetch(ctx, OWN), papers.full_text(ctx, OWN)


@pytest.mark.parametrize("target, why", [("https://sci-hub.se/x.pdf", "a shadow library"),
                                         ("https://z-library.sk/x.pdf", "a shadow library"),
                                         ("http://127.0.0.1:8740/api/x.pdf", "local"),
                                         ("ftp://repo.example.org/x.pdf", "http")])
async def test_a_copy_that_redirects_somewhere_refused_is_never_requested_there(tmp_path, target, why):
    fake = open_at(REPO, {REPO: target, target: paper(OWN_TITLE, OWN)})
    got, held = await fetch_own(tmp_path, fake)
    assert got.copy is None and got.wanted and held is None
    assert f"redirected to {target}" in got.note and why in got.note
    assert [str(r.url) for r in fake.requests if r.url.host not in ("api.unpaywall.org", "api.semanticscholar.org")
            ] == [REPO]


async def test_a_copy_at_a_private_address_is_refused_before_any_request(tmp_path):
    private = "http://192.168.1.10/a.pdf"
    fake = open_at(private, {private: paper(OWN_TITLE, OWN)})
    got, held = await fetch_own(tmp_path, fake)
    assert got.copy is None and held is None and f"{private}: refused" in got.note
    assert fake.requests_to("192.168.1.10") == []


async def test_a_doi_org_copy_is_followed_to_the_publisher_and_the_final_url_kept(tmp_path):
    doi_link, final = f"https://doi.org/{OWN}", "https://publisher.example.com/pdf/own.pdf"
    fake = open_at(doi_link, {doi_link: "https://publisher.example.com/landing",
                              "https://publisher.example.com/landing": "/pdf/own.pdf", final: paper(OWN_TITLE, OWN)})
    got, held = await fetch_own(tmp_path, fake)
    assert got.copy.url == final and held.url == final
    assert f"read {final} (redirected from {doi_link})" in got.note


async def test_a_redirect_loop_and_an_oversized_copy_are_given_up(tmp_path, monkeypatch):
    loop = {f"https://repo.example.org/{n}.pdf": f"https://repo.example.org/{n + 1}.pdf" for n in range(10)}
    got, _ = await fetch_own(tmp_path / "loop", open_at("https://repo.example.org/0.pdf", loop))
    assert got.copy is None and "redirected more than 5 times" in got.note
    monkeypatch.setattr(papers, "PDF_BYTES", 2000)
    got, held = await fetch_own(tmp_path / "big", open_at(REPO, {REPO: paper(OWN_TITLE, OWN)}))
    assert got.copy is None and held is None and f"{REPO}: over" in got.note


@pytest.mark.parametrize("status", [503, 429])
async def test_a_lookup_that_did_not_answer_does_not_put_the_paper_on_the_wanted_list(tmp_path, status):
    fake = FakeWeb(Down("api.unpaywall.org", status), Down("api.semanticscholar.org", status))
    got, _ = await fetch_own(tmp_path, fake)
    assert got.copy is None and not got.wanted
    assert "try again later" in got.note
    with context(tmp_path, fake) as ctx:
        assert papers.wanted(ctx) == []


async def test_a_landing_page_only_is_said_plainly(tmp_path):
    fake = FakeWeb(FakeUnpaywall({OWN: {"oa_status": "green", "best_oa_location": {
        "url": "https://repo.example.org/record/1", "url_for_pdf": None}, "oa_locations": []}}), FakeSemanticScholar({}))
    got, _ = await fetch_own(tmp_path, fake)
    assert "Unpaywall: green (its open copy is a landing page only)" in got.note and got.wanted


async def test_a_source_with_its_full_text_cannot_be_wanted(tmp_path):
    fake = web({GKX_PDF: paper("Empirical Asset Pricing via Machine Learning", GKX)})
    with context(tmp_path, fake) as ctx:
        await papers.fetch(ctx, GKX)
        with pytest.raises(InvalidQuery, match="already has its full text"):
            papers.want(ctx, GKX)


def test_a_paper_citing_a_recorded_doi_is_not_taken_for_it(tmp_path):
    with context(tmp_path, web()) as ctx:
        record_jt_and_pone(ctx)
        inbox = papers.inbox_folder(ctx)
        drop(inbox, "jt.pdf", paper(JT_TITLE, JT))
        (kept,) = papers.scan_inbox(ctx).results
        assert kept.source.title == JT_TITLE
        before = papers.text(ctx, JT).text
        drop(inbox, "new_bond_paper.pdf", make_pdf([["Credit Spreads and Sovereign Defaults in Emerging Markets",
                                                      "C. Author", "Abstract", "We study bonds."],
                                                     ["As in Jegadeesh and Titman (1993), doi:" + JT + "."]]))
        (cited,) = papers.scan_inbox(ctx).results
        assert cited.source is None and f"#{kept.source.id}" in cited.note and "rename it to its DOI" in cited.note
        assert papers.text(ctx, JT).text == before and (inbox / "new_bond_paper.pdf").exists()


def test_an_id_in_the_text_is_taken_when_the_title_roughly_agrees(tmp_path):
    with context(tmp_path, web()) as ctx:
        record_jt_and_pone(ctx)
        drop(papers.inbox_folder(ctx), "scan.pdf", paper("Retums to Buyirg Wimners and Seling Losrs", JT))
        (r,) = papers.scan_inbox(ctx).results
        assert r.source.title == JT_TITLE and r.matched.startswith(f"id doi:{JT} (title 0.")


def test_replacing_a_full_text_says_so(tmp_path):
    with context(tmp_path, web()) as ctx:
        record_jt_and_pone(ctx)
        inbox = papers.inbox_folder(ctx)
        drop(inbox, "a.pdf", paper(JT_TITLE, JT))
        papers.scan_inbox(ctx)
        drop(inbox, "10.1111_j.1540-6261.1993.tb04702.x.pdf", paper("Untitled scan"))
        (r,) = papers.scan_inbox(ctx).results
        assert "replaces its earlier full text (from inbox, 2026-10-04)" in r.note


def test_a_full_text_shows_on_its_source_and_can_be_read(tmp_path):
    with context(tmp_path, web()) as ctx:
        record_jt_and_pone(ctx)
        drop(papers.inbox_folder(ctx), "jt.pdf", paper(JT_TITLE, JT))
        papers.scan_inbox(ctx)
        by_title = {s.title: s for s in lab.sources(ctx)}
        jt, pone = by_title[JT_TITLE], by_title[PONE_TITLE]
        assert (jt.full_text_via, jt.full_text_at[:10]) == ("inbox", "2026-10-04")
        assert pone.full_text_via is None and pone.full_text_at is None
        (held,) = papers.texts(ctx)
        assert held.source.id == jt.id and held.full_text.via == "inbox" and held.full_text.pages == 2
        read = papers.text(ctx, f"#{jt.id}")
        assert read.source.id == jt.id and read.full_text.via == "inbox" and "Line 59 of the body text" in read.text
        with pytest.raises(InvalidQuery, match="has no full text"):
            papers.text(ctx, PONE)


async def test_a_downloaded_pdf_is_parsed_off_the_event_loop(tmp_path, monkeypatch):
    import threading

    threads = []
    real = papers.pdf_text

    def spy(data):
        threads.append(threading.current_thread())
        return real(data)

    monkeypatch.setattr(papers, "pdf_text", spy)
    fake = web({GKX_PDF: paper("Empirical Asset Pricing via Machine Learning", GKX)})
    with context(tmp_path, fake) as ctx:
        assert (await papers.fetch(ctx, GKX)).copy is not None
    assert threads and threads[0] is not threading.current_thread()


BAB = "10.1016/j.jfineco.2013.10.005"
BAB_TITLE = "Betting Against Beta"


def keep_bab_text(ctx):
    """The seeded index already records both BAB (#14) and its follow-up (#16, no DOI)."""
    lab.record(ctx, f"doi:{BAB}", title=BAB_TITLE, quality="peer-reviewed")
    inbox = papers.inbox_folder(ctx)
    drop(inbox, "10.1016_j.jfineco.2013.10.005.pdf", paper(BAB_TITLE, BAB))
    (kept,) = papers.scan_inbox(ctx).results
    return kept


def test_a_follow_up_paper_citing_a_recorded_doi_is_not_taken_for_the_cited_one(tmp_path):
    with context(tmp_path, web()) as ctx:
        kept = keep_bab_text(ctx)
        before = papers.text(ctx, BAB).text
        inbox = papers.inbox_folder(ctx)
        drop(inbox, "babab.pdf", make_pdf([["Betting Against Betting Against Beta", "Abstract"],
                                           ["We revisit Frazzini and Pedersen, doi:" + BAB + "."]]))
        (r,) = papers.scan_inbox(ctx).results
        assert r.source is None or r.source.id != kept.source.id
        assert papers.text(ctx, BAB).text == before


def test_a_page_text_id_alone_never_replaces_a_full_text(tmp_path):
    with context(tmp_path, web()) as ctx:
        keep_bab_text(ctx)
        before = papers.text(ctx, BAB).text
        inbox = papers.inbox_folder(ctx)
        drop(inbox, "other.pdf", make_pdf([["Betting Against Beta", "Abstract"], ["Cites doi:" + BAB + "."]]))
        (r,) = papers.scan_inbox(ctx).results
        assert papers.text(ctx, BAB).text == before and (inbox / "other.pdf").exists()
