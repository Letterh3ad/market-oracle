import json
from pathlib import Path

import pytest

from oracle.lab.papers import (
    NotAPdf, api_ids, ids_in, is_shadow, pdf_text, s2_copies, title_candidates, unpaywall_copies,
)
from tests.fakes.pdf import make_pdf, paper

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "open_access"


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_unpaywall_open_record_gives_its_pdf_copies_best_first():
    copies = unpaywall_copies(fixture("unpaywall-open.json"))
    assert [c.url for c in copies] == ["https://academic.oup.com/rfs/article-pdf/33/5/2223/33209812/hhaa009.pdf"]
    c = copies[0]
    assert (c.via, c.licence, c.version) == ("unpaywall", "cc-by-nc-nd", "publishedVersion")


def test_unpaywall_closed_record_gives_nothing():
    assert unpaywall_copies(fixture("unpaywall-closed.json")) == []


def test_semantic_scholar_gold_copy_is_taken_and_a_closed_one_refused():
    gold = s2_copies(fixture("s2-open.json"))
    assert [(c.url, c.via, c.licence) for c in gold] == [
        ("https://journals.plos.org/plosone/article/file?id=10.1371/journal.pone.0285244&type=printable",
         "semantic-scholar", "CCBY")]
    # Semantic Scholar calls this paper open access yet marks the copy CLOSED: a course page's upload.
    closed = fixture("s2-closed.json")
    assert closed["isOpenAccess"] is True and s2_copies(closed) == []
    assert s2_copies(fixture("s2-missing.json")) == []


@pytest.mark.parametrize("url", ["https://sci-hub.se/10.1/x", "http://libgen.rs/book.pdf", "https://annas-archive.org/md5/1",
                                 "https://z-lib.io/x", "https://www.library.lol/main/1", "https://z-library.sk/book/1",
                                 "https://Z-Library.se./x", "http://gen.lib.rus.ec/scimag/x", "https://zlibrary.to/x",
                                 "https://sci-hub.ru/x", "https://libgen.li/x"])
def test_shadow_libraries_are_refused_even_when_an_api_names_them(url):
    assert is_shadow(url)
    body = {"oa_locations": [{"url_for_pdf": url, "license": None, "version": "publishedVersion"}]}
    assert unpaywall_copies(body) == []


def test_ordinary_hosts_are_not_shadow():
    assert not is_shadow("https://journals.plos.org/x.pdf") and not is_shadow("https://arxiv.org/pdf/2502.21206")


@pytest.mark.parametrize("key, unpaywall, s2", [
    ("doi:10.1093/rfs/hhaa009", "10.1093/rfs/hhaa009", "DOI:10.1093/rfs/hhaa009"),
    ("ssrn:2155489", "10.2139/ssrn.2155489", "DOI:10.2139/ssrn.2155489"),
    ("arxiv:2502.21206", "10.48550/arxiv.2502.21206", "ARXIV:2502.21206"),
    ("nber:w20592", "10.3386/w20592", "DOI:10.3386/w20592"),
    ("url:example.org/paper", None, None),
])
def test_api_ids_per_key_kind(key, unpaywall, s2):
    assert api_ids(key) == (unpaywall, s2)


def test_pdf_text_reads_pages_and_metadata_title():
    t = pdf_text(make_pdf([["Momentum Everywhere", "body"], ["page two"]], title="Momentum Everywhere"))
    assert t.title == "Momentum Everywhere" and len(t.pages) == 2 and "page two" in t.pages[1]
    assert "Momentum Everywhere" in t.text


def test_not_a_pdf_is_refused():
    with pytest.raises(NotAPdf):
        pdf_text(b"<html>Sign in to read</html>")


def test_ids_in_finds_dois_arxiv_and_ssrn_on_the_first_pages():
    text = ("Journal of Finance. https://doi.org/10.1111/J.1540-6261.1993.TB04702.X.\n"
            "Also arXiv:2502.21206v2 and https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2155489\n"
            "doi: 10.1093/rfs/hhaa009,")
    assert ids_in(text) == ["doi:10.1111/j.1540-6261.1993.tb04702.x", "arxiv:2502.21206", "ssrn:2155489",
                            "doi:10.1093/rfs/hhaa009"]


def test_title_candidates_come_from_metadata_name_and_first_lines():
    t = pdf_text(paper("Returns to Buying Winners", body_lines=4))
    found = title_candidates(t, "jegadeesh titman 1993.pdf")
    assert "Returns to Buying Winners" in found and "jegadeesh titman 1993" in found
    assert "Returns to Buying Winners A. Author and B. Author" in found


def test_a_doi_in_brackets_loses_the_closing_bracket_but_a_doi_with_its_own_keeps_it():
    assert ids_in("as shown (doi:10.2307/2672906).") == ["doi:10.2307/2672906"]
    assert ids_in("[https://doi.org/10.2307/2672906]") == ["doi:10.2307/2672906"]
    assert ids_in("see 10.1016/S0304-405X(97)00004-X.") == ["doi:10.1016/s0304-405x(97)00004-x"]


def test_an_aes_encrypted_pdf_names_the_missing_package(monkeypatch):
    import pypdf
    from pypdf.errors import DependencyError

    def aes(*args, **kwargs):
        raise DependencyError("cryptography>=3.1 is required for AES algorithm")

    monkeypatch.setattr(pypdf.PageObject, "extract_text", aes)
    with pytest.raises(NotAPdf, match="encrypted.*cryptography"):
        pdf_text(make_pdf([["x"]]))
    monkeypatch.setattr(pypdf, "PdfReader", aes)
    with pytest.raises(NotAPdf, match="encrypted.*cryptography"):
        pdf_text(make_pdf([["x"]]))
