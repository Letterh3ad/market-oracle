from datetime import date

import pytest

from oracle.research.news import google_news_params
from oracle.research.queries import (
    EDITION, LOCAL, LOCAL_NEWS, MAX_PLANNED, NEWS, WEB, Search, plan, seed_queries,
)


@pytest.mark.parametrize("country, hl, gl, ceid", [
    ("Japan", "ja", "JP", "JP:ja"), ("Germany", "de", "DE", "DE:de"), ("South Korea", "ko", "KR", "KR:ko"),
    ("China", "zh-CN", "CN", "CN:zh-Hans"), ("Brazil", "pt-BR", "BR", "BR:pt-419"),
    ("Finland", "fi-FI", "FI", "FI:fi"), ("Mexico", "es-419", "MX", "MX:es-419"),
])
def test_a_local_edition_asks_google_news_in_the_countrys_language(country, hl, gl, ceid):
    params = google_news_params("Sony 株価", edition=EDITION[country])
    assert params == {"q": "Sony 株価", "hl": hl, "gl": gl, "ceid": ceid}


def test_every_country_with_a_local_word_has_an_edition_and_english_markets_have_none():
    assert set(EDITION) == set(LOCAL)
    assert not {"United States", "United Kingdom", "Canada", "Australia", "Ireland"} & set(EDITION)
    assert google_news_params("Apple") == {"q": "Apple", "hl": "en-GB", "gl": "GB", "ceid": "GB:en"}


def test_a_replay_of_the_local_edition_still_searches_before_the_cutoff():
    params = google_news_params("Sony 株価", date(2026, 9, 18), EDITION["Japan"])
    assert params["q"] == "Sony 株価 before:2026-09-19" and params["hl"] == "ja"


def test_a_japanese_listing_seeds_a_local_edition_query_and_a_press_release_query():
    assert seed_queries("Sony Group Corporation", "Japan", web=True) == [
        Search(NEWS, "Sony shares"), Search(NEWS, "Sony earnings outlook"), Search(LOCAL_NEWS, "Sony 株価"),
        Search(WEB, "Sony press release investor relations"),
    ]


def test_a_us_listing_seeds_no_local_query_and_without_the_web_no_press_release_query():
    assert seed_queries("Apple Inc.", "United States", web=True) == [
        Search(NEWS, "Apple shares"), Search(NEWS, "Apple earnings outlook"),
        Search(WEB, "Apple press release investor relations"),
    ]
    assert [s.tool for s in seed_queries("Apple Inc.", "United States", web=False)] == [NEWS, NEWS]


def test_the_plan_keeps_every_seed_and_fills_up_to_the_cap_with_new_suggestions():
    seeds = seed_queries("Sony Group", "Japan", web=True)
    ideas = ["sony SHARES", "", "x" * 150, "PlayStation 6 launch", "Sony tariffs", "Sony pictures", "Sony chips",
             "Sony music"]
    planned = plan(seeds, ideas, "Japan")
    assert len(planned) == MAX_PLANNED == 8
    assert planned == [*seeds, *(Search(NEWS, q) for q in ideas[3:7])]
    us = plan(seed_queries("Apple Inc.", "United States", web=True), [f"Apple idea {n}" for n in range(9)], None)
    assert len(us) == MAX_PLANNED, "a US listing's 3 seeds take 5 suggestions"


def test_a_suggestion_in_the_local_script_goes_to_the_local_edition_where_there_is_one():
    assert plan([], ["ソニー 決算", "Sony results"], "Japan") == [Search(LOCAL_NEWS, "ソニー 決算"),
                                                                 Search(NEWS, "Sony results")]
    assert plan([], ["Nestlé Aktie"], None) == [Search(NEWS, "Nestlé Aktie")]
