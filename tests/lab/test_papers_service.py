from dataclasses import replace
from datetime import datetime, timezone

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
        assert held.via == "unpaywall" and "Line 59 of the body text" in papers.text(ctx, f"doi:{GKX}")
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
        drop(inbox, "download (3).pdf", paper("Some header line", JT))
        drop(inbox, "scan.pdf", paper("Construction and Application of Computerised Risk Assessment Model for "
                                      "Supply Chain Finance under Technology Empowerment"))
        drop(inbox, "unrelated.pdf", paper("A Theory of Cheese Futures"))
        drop(inbox, "broken.pdf", b"%PDF-1.4 truncated")
        drop(inbox, "notes.txt", b"not a paper")
        scan = papers.scan_inbox(ctx)
        by_file = {r.file: r for r in scan.results}
        assert set(by_file) == {"download (3).pdf", "scan.pdf", "unrelated.pdf", "broken.pdf"}
        jt = by_file["download (3).pdf"]
        assert jt.source.title == JT_TITLE and jt.matched == f"id doi:{JT}" and jt.pages == 2
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
