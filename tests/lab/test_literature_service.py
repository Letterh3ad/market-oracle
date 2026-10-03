from collections import Counter
from datetime import datetime, timezone

import pytest

from oracle.config import Config
from oracle.services import literature_service as lab
from oracle.services.context import open_context
from oracle.services.errors import InvalidQuery, UnknownMethod
from tests.fakes.clock import FakeClock

NOW = datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc)
MCLEAN = "https://ivey.uwo.ca/media/3775549/pontiff.pdf"


@pytest.fixture
def ctx(tmp_path):
    config = Config(db_path=tmp_path / "live.db", research_db_path=tmp_path / "research.db")
    with open_context(config, None, FakeClock(NOW)) as c:
        yield c


def test_the_seed_holds_the_archive_the_tested_methods_and_ai_track_once(ctx):
    every = lab.methods(ctx)
    assert Counter(m.origin for m in every) == {"archive": 185, "tested": 14, "ai-track": 6}
    lab.methods(ctx)
    assert len(lab.methods(ctx)) == 205
    archive = [m for m in every if m.origin == "archive"]
    # The archive's own summary: "150 of 185 untested. Ratings: 7+ = 14, 5-6 = 21, 3-4 = 51, 1-2 = 99."
    assert Counter(m.archive_status for m in archive)["Untested"] == 150
    bands = Counter("7+" if m.rating >= 7 else "5-6" if m.rating >= 5 else "3-4" if m.rating >= 3 else "1-2"
                    for m in archive)
    assert bands == {"7+": 14, "5-6": 21, "3-4": 51, "1-2": 99}
    assert {m.ref for m in every if m.origin == "ai-track"} == {f"AT-0{n}" for n in range(4, 10)}
    assert all(m.status_by == "seed" for m in every)


def test_five_entries_match_the_archive_text(ctx):
    t15 = lab.method(ctx, "T-15")
    assert (t15.name, t15.rating, t15.archive_status, t15.status) == ("52-week-high proximity", 3, "Untested", "new")
    assert t15.definition == "stocks near their 52-week high keep rising."
    assert t15.evidence == "George-Hwang 2004: 0.45%/month, no long-run reversal."
    assert t15.family == "A. Cross-sectional stock anomalies"
    assert [s.keys for s in t15.sources] == [("url:academicnewsletter.sufe.edu.cn/info/361244",)]

    a24 = lab.method(ctx, "a-24")
    assert a24.rating == 5 and a24.name == "Chronologically consistent LLMs (ChronoBERT / ChronoGPT)"
    assert a24.definition == ("Use models trained only on text up to each year (vintages 1999-2024) so pre-2025 "
                              "history can be tested without look-ahead.")
    assert {k for s in a24.sources for k in s.keys} == {"arxiv:2502.21206", "url:huggingface.co/manelalab"}

    t09 = lab.method(ctx, "T-09")
    assert "Ikenberry-Lakonishok-Vermaelen 1995 (12% 4-year abnormal return, mostly value buybacks)" in t09.evidence
    assert "doi:10.1016/0304-405x(95)00826-z" in {k for s in t09.sources for k in s.keys}

    a01 = lab.method(ctx, "A-01")
    assert (a01.archive_status, a01.status, a01.rating) == ("Tried-FAIL", "fail", 3)

    t73 = lab.method(ctx, "T-73")
    assert t73.rating == 7 and t73.definition.startswith("up to £4k/yr, 25% government bonus, under 40 to open")


def test_tested_methods_carry_their_results_and_registry_names(ctx):
    x01 = lab.method(ctx, "X-01")
    assert (x01.name, x01.result, x01.status, x01.test_key) == (
        "Momentum 12-1 (h20)", "IC +0.008 t 0.50, could not see", "fail", "momentum_12_1")
    assert lab.method(ctx, "X-13").status == "built" and lab.method(ctx, "X-14").status == "dropped"
    t62 = lab.method(ctx, "T-62")
    assert (t62.status, t62.test_key) == ("fail", "trend_overlay")
    assert lab.method(ctx, "AT-07").see_also == ("A-08",)


@pytest.mark.parametrize("variant", [
    "http://ivey.uwo.ca/media/3775549/pontiff.pdf",
    "https://www.ivey.uwo.ca/media/3775549/pontiff.pdf/",
    "https://ivey.uwo.ca/media/3775549/pontiff.pdf?utm_source=newsletter&fbclid=x",
])
def test_url_variants_resolve_to_the_one_seeded_source(ctx, variant):
    seeded = lab.lookup(ctx, MCLEAN).sources[0].source
    found = lab.lookup(ctx, variant)
    assert found.key == "url:ivey.uwo.ca/media/3775549/pontiff.pdf"
    assert [h.source.id for h in found.sources] == [seeded.id]
    again = lab.record(ctx, variant, note="read section 4")
    assert not again.created and again.source.id == seeded.id and again.source.note == "read section 4"
    assert len({s.id for s in lab.sources(ctx)}) == len(lab.sources(ctx))


def test_doi_variants_resolve_to_one_source_and_a_second_id_joins_it(ctx):
    made = lab.record(ctx, "https://doi.org/10.1234/ABC.5", title="A New Paper", authors="Doe", year=2024,
                      quality="peer-reviewed")
    assert made.created and made.source.keys == ("doi:10.1234/abc.5",) and made.source.url == "https://doi.org/10.1234/ABC.5"
    for variant in ("doi:10.1234/abc.5", "http://dx.doi.org/10.1234/abc.5", "10.1234/ABC.5"):
        assert lab.lookup(ctx, variant).sources[0].source.id == made.source.id
    joined = lab.record(ctx, "SSRN 999999", title="a new paper")
    assert not joined.created and joined.source.keys == ("doi:10.1234/abc.5", "ssrn:999999")
    assert lab.lookup(ctx, "https://papers.ssrn.com/sol3/papers.cfm?abstract_id=999999").sources[0].source.id == made.source.id


def test_a_mangled_title_finds_the_known_paper_and_its_methods(ctx):
    found = lab.lookup(ctx, "does academic reserch destory stock return predictibility")
    assert found.key is None
    top = found.sources[0]
    assert top.source.url == MCLEAN and top.score > 0.85
    assert set(top.source.methods) >= {"T-06", "T-60"}
    assert len(found.methods) == 5 and found.methods[0].ref in top.source.methods
    assert found.methods[0].matched.startswith("source: ")
    assert all(h.status and 0 <= h.score <= 1 for h in found.methods)


def test_a_name_lookup_ranks_the_method_first_with_score_and_status(ctx):
    found = lab.lookup(ctx, "52 week high")
    assert found.methods[0].ref == "T-15" and found.methods[0].status == "new" and found.methods[0].score > 0.7
    assert [h.score for h in found.methods] == sorted((h.score for h in found.methods), reverse=True)
    assert lab.lookup(ctx, "t-62").methods[0].ref == "T-62"


def test_an_unknown_id_says_so(ctx):
    found = lab.lookup(ctx, "arXiv:2999.00001")
    assert found.key == "arxiv:2999.00001" and found.sources == [] and found.methods == []
    assert "not in the index" in found.note
    with pytest.raises(InvalidQuery):
        lab.lookup(ctx, "   ")


@pytest.mark.parametrize("mark,status", [("go", "go"), ("drop", "dropped"), ("dig", "triage")])
def test_a_mark_changes_status_and_records_who_and_when(ctx, mark, status):
    done = lab.mark(ctx, "T-15", mark, by="autopilot", note="cheap to test")
    assert (done.status, done.status_by, done.status_at) == (status, "autopilot", "2026-10-03T12:00:00+00:00")
    assert [(m.mark, m.status_from, m.status_to, m.by, m.at, m.note) for m in done.marks] == [
        (mark, "new", status, "autopilot", "2026-10-03T12:00:00+00:00", "cheap to test")]
    assert lab.method(ctx, "T-15").status == status
    assert [m.ref for m in lab.methods(ctx, status) if m.origin == "archive"][:1] == ["T-15"]


def test_marks_keep_their_history_newest_first(ctx):
    lab.mark(ctx, "T-09", "dig")
    done = lab.mark(ctx, "T-09", "go")
    assert [(m.mark, m.status_from, m.status_to, m.by) for m in done.marks] == [
        ("go", "triage", "go", "user"), ("dig", "new", "triage", "user")]


def test_a_tested_method_cannot_be_marked(ctx):
    with pytest.raises(InvalidQuery, match="FAIL is never re-tested"):
        lab.mark(ctx, "X-01", "go")
    with pytest.raises(InvalidQuery, match="being tested"):
        lab.mark(ctx, "X-13", "drop")
    with pytest.raises(InvalidQuery, match="go, drop or dig"):
        lab.mark(ctx, "T-15", "maybe")
    with pytest.raises(UnknownMethod):
        lab.mark(ctx, "T-999", "go")
    assert lab.method(ctx, "X-01").status == "fail"


def test_record_adds_a_method_from_a_source_and_shows_the_nearest(ctx):
    made = lab.record(ctx, "https://example.com/blog/52wk?utm_source=x", title="Buying stocks near yearly highs",
                      quality="practitioner", new_method="Yearly-high breakout", family="momentum",
                      definition="Buy stocks within 2% of their 52-week high.", aliases=["52 week breakout"],
                      by="autopilot")
    assert made.method.ref == "M-001" and made.method.status == "new" and made.method.origin == "added"
    assert made.method.aliases == ("52 week breakout",) and made.method.status_by == "autopilot"
    assert made.source.methods == ("M-001",) and made.source.added_by == "autopilot"
    assert "T-15" in [h.ref for h in made.nearest] and "M-001" not in [h.ref for h in made.nearest]
    assert lab.record(ctx, title="Another", new_method="Second one").method.ref == "M-002"
    assert lab.lookup(ctx, "52 week breakout").methods[0].ref == "M-001"


def test_record_links_a_source_to_existing_methods(ctx):
    made = lab.record(ctx, "arXiv:2601.99999", title="Fresh momentum paper", methods=["T-15", "x-01"], read_on="2026-10-02")
    assert made.source.methods == ("T-15", "X-01") and made.source.read_on == "2026-10-02"
    assert made.source.url == "https://arxiv.org/abs/2601.99999" and made.source.quality == "abstract-only"
    assert "Fresh momentum paper" in [s.title for s in lab.method(ctx, "T-15").sources]


@pytest.mark.parametrize("kwargs,match", [
    ({"ident": "not an id"}, "not a DOI"),
    ({"ident": "doi:10.5555/zz"}, "needs a title"),
    ({"title": "x", "quality": "great"}, "quality"),
    ({"title": "x", "year": 1500}, "year"),
    ({"title": "x", "read_on": "yesterday"}, "date"),
    ({"title": "x", "methods": ["T-999"]}, "T-999"),
    ({"methods": ["T-15"]}, "source"),
    ({}, "nothing to record"),
    ({"title": "   "}, "nothing to record"),
    ({"ident": "https://example.com/new-paper", "title": " "}, "needs a title"),
    ({"title": "x", "new_method": "52-week-high proximity"}, "already in the index as T-15"),
    ({"title": "x", "by": ""}, "by"),
])
def test_record_refuses_bad_input_and_writes_nothing(ctx, kwargs, match):
    before = len(lab.sources(ctx)), len(lab.methods(ctx))
    ident = kwargs.pop("ident", None)
    with pytest.raises(InvalidQuery, match=match):
        lab.record(ctx, ident, **kwargs)
    assert (len(lab.sources(ctx)), len(lab.methods(ctx))) == before


def test_a_search_is_logged_with_what_it_found(ctx):
    paper = lab.record(ctx, "https://doi.org/10.1234/found", title="Found paper").source
    first = lab.log_search(ctx, "post-publication decay", "SSRN", found=["doi:10.1234/FOUND", MCLEAN], by="autopilot")
    assert (first.query, first.place, first.by, first.at) == ("post-publication decay", "SSRN", "autopilot",
                                                               "2026-10-03T12:00:00+00:00")
    assert sorted(s.id for s in first.found) == sorted([paper.id, lab.lookup(ctx, MCLEAN).sources[0].source.id])
    second = lab.log_search(ctx, "52 week high UK", "Google Scholar", note="nothing new")
    assert [s.id for s in lab.searches(ctx)] == [second.id, first.id] and second.found == ()
    with pytest.raises(InvalidQuery, match="record it first"):
        lab.log_search(ctx, "x", "y", found=["https://unknown.example/p"])
    with pytest.raises(InvalidQuery, match="where"):
        lab.log_search(ctx, "x", " ")
    assert len(lab.searches(ctx)) == 2


def test_method_list_filters_by_status_and_refuses_an_unknown_one(ctx):
    assert {m.ref for m in lab.methods(ctx, "built")} == {"X-13"}
    with pytest.raises(InvalidQuery, match="status"):
        lab.methods(ctx, "maybe")
    with pytest.raises(UnknownMethod):
        lab.method(ctx, "Z-1")


FAMA_FRENCH = "https://doi.org/10.1111/j.1540-6261.1992.tb04398.x"


@pytest.mark.parametrize("variant", [
    "https://doi.org/10.1111/j.1540-6261.1992.tb04398.x/",
    "http://dx.doi.org/10.1111/j.1540-6261.1992.tb04398.x/",
    "https://onlinelibrary.wiley.com/doi/abs/10.1111/j.1540-6261.1992.tb04398.x/",
    "https://onlinelibrary.wiley.com/doi/full/10.1111/j.1540-6261.1992.tb04398.x",
    "https://onlinelibrary.wiley.com/doi/pdf/10.1111/j.1540-6261.1992.tb04398.x",
])
def test_doi_link_variants_resolve_to_the_seeded_fama_french(ctx, variant):
    seeded = lab.lookup(ctx, FAMA_FRENCH).sources[0].source
    assert [h.source.id for h in lab.lookup(ctx, variant).sources] == [seeded.id]
    again = lab.record(ctx, variant, note="checked")
    assert not again.created and again.source.id == seeded.id
    assert len(lab.sources(ctx)) == 170


@pytest.mark.parametrize("title,ref", [
    ("Seasonality in the cross-section of stock returns", "T-17"),
    ("Market underreaction to open market share repurchases", "T-09"),
    ("Price reactions to dividend initiations and omissions: overreaction or drift?", "T-21"),
    ("High stock returns before holidays", "T-30"),
    ("All the news that's fit to reprint: do investors react to stale information?", "A-18"),
])
def test_a_real_paper_title_finds_its_method_first(ctx, title, ref):
    found = lab.lookup(ctx, title)
    assert found.methods[0].ref == ref and found.methods[0].score > 0.8
    assert ref in found.sources[0].source.methods


@pytest.mark.parametrize("query,ref", [
    ("Heston Sadka 2008", "T-17"),
    ("Ikenberry, Lakonishok, Vermaelen 1995", "T-09"),
    ("return seasonality", "T-17"),
])
def test_authors_and_year_or_the_method_name_find_the_method(ctx, query, ref):
    assert lab.lookup(ctx, query).methods[0].ref == ref


def test_authors_and_year_pick_the_paper_among_an_authors_others(ctx):
    found = lab.lookup(ctx, "Fama French 1992")
    assert found.sources[0].source.url == FAMA_FRENCH and found.sources[0].score == 1.0


def test_seeded_titles_are_paper_titles_not_link_text(ctx):
    titles = {s.title for s in lab.sources(ctx)}
    assert not titles & {"Seasonality", "buybacks", "withdrawal", "working paper", "2022 update",
                         "international evidence (JFQA)", "HAC covariance"}
    assert "Seasonality in the Cross-Section of Stock Returns" in titles


def test_a_blank_title_never_blanks_a_known_source(ctx):
    for blank in ("", "   "):
        kept = lab.record(ctx, MCLEAN, title=blank, note="re-read")
        assert kept.source.title == "Does Academic Research Destroy Stock Return Predictability?"
        assert kept.source.note == "re-read"


def test_the_archive_note_is_kept(ctx):
    t10 = lab.method(ctx, "T-10")
    assert t10.archive_note == ("Tried-other (low-vol scorer inside the 51-name regime blend; regime ~+50% vs "
                                "b&h +76.6%)")
    assert lab.method(ctx, "T-13").archive_note == "Untested (raw 12-1 Tried-FAIL t 0.50)"


def test_seeding_twice_is_a_no_op(ctx):
    from oracle.services.testbed_service import research_context
    lab.methods(ctx)
    with research_context(ctx) as r:
        lab._seed(r.conn, "2026-10-03T12:00:00+00:00")
    assert len(lab.methods(ctx)) == 205 and len(lab.sources(ctx)) == 170


@pytest.mark.parametrize("doi,ref,wrong", [
    ("10.1111/j.1540-6261.1990.tb03731.x", "T-30", "10.1111/j.1540-6261.1990.tb05110.x"),
    ("10.1111/j.1540-6261.1995.tb04796.x", "T-21", "10.1111/j.1540-6261.1995.tb05168.x"),
    ("10.1016/0304-405X(93)90009-Z", "T-22", "10.1016/0304-405X(93)90024-4"),
])
def test_corrected_seed_dois_resolve_to_their_method(ctx, doi, ref, wrong):
    found = lab.lookup(ctx, f"https://doi.org/{doi}")
    assert found.note == "exact id match" and ref in [h.ref for h in found.methods]
    assert lab.lookup(ctx, f"https://doi.org/{wrong}").sources == []


def test_the_nber_page_title_finds_the_congress_paper(ctx):
    found = lab.lookup(ctx, "Relief Rally: Senators As Feckless As the Rest of Us at Stock Picking")
    assert found.methods[0].ref == "A-91" and found.methods[0].score > 0.9
    source = found.sources[0].source
    assert source.title.startswith("Do Senators and House Members Beat the Stock Market?")
    assert source.also_titled == "Relief Rally: Senators As Feckless As the Rest of Us at Stock Picking"
