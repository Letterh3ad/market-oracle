import pytest

from oracle.research.mentions import (
    MENTIONS_SCHEMA, QUESTION_SCHEMA, TOPIC_SCHEMA, Mention, mentions_prompt, parse_mentions, question_prompt,
    topic_prompt,
)

HEADLINES = [
    "Toyota and Panasonic expand battery venture",
    "Denso to supply Toyota with new chips",
    "Honda and Toyota race for hybrid buyers",
]


def test_the_prompt_numbers_the_headlines_and_names_the_seed():
    [system, user] = mentions_prompt("Toyota Motor Corporation", HEADLINES)
    assert "Toyota Motor Corporation" in system["content"]
    assert "[2] Denso to supply Toyota with new chips" in user["content"]


def test_the_schema_bounds_the_answer():
    companies = MENTIONS_SCHEMA["properties"]["companies"]
    assert companies["maxItems"] <= 10
    assert companies["items"]["properties"]["name"]["maxLength"] <= 100
    assert set(companies["items"]["properties"]["relation"]["enum"]) >= {"supplier", "customer", "competitor", "partner"}


def test_names_come_back_with_their_relation_and_the_headlines_they_came_from():
    reply = {"companies": [
        {"name": "Panasonic", "relation": "partner", "headlines": [1]},
        {"name": "Denso Corp", "relation": "supplier", "headlines": [2, 2]},
        {"name": "Honda", "relation": "competitor", "headlines": [3]},
    ]}
    assert parse_mentions(reply, HEADLINES, "Toyota Motor Corporation") == [
        Mention("Panasonic", "partner", (0,)), Mention("Denso Corp", "supplier", (1,)),
        Mention("Honda", "competitor", (2,)),
    ]


@pytest.mark.parametrize("reply", [
    None, "Panasonic, Denso", [], {"companies": "Panasonic"}, {"nope": []}, {"companies": [None, 3, "x"]},
])
def test_garbled_output_adds_nothing(reply):
    assert parse_mentions(reply, HEADLINES, "Toyota") == []


def test_a_name_no_cited_headline_holds_is_dropped_as_made_up():
    reply = {"companies": [
        {"name": "Nissan", "relation": "competitor", "headlines": [3]},
        {"name": "Honda", "relation": "competitor", "headlines": [9, 0, -1]},
        {"name": "Honda", "relation": "competitor", "headlines": []},
    ]}
    assert parse_mentions(reply, HEADLINES, "Toyota") == []


def test_the_seed_itself_duplicates_blank_names_and_odd_relations_are_handled():
    reply = {"companies": [
        {"name": "Toyota", "relation": "partner", "headlines": [1]},
        {"name": "  ", "relation": "partner", "headlines": [1]},
        {"name": "Panasonic", "relation": "sponsor", "headlines": [1]},
        {"name": "panasonic", "relation": "partner", "headlines": [1]},
        {"name": 7, "relation": "partner", "headlines": [1]},
    ]}
    assert parse_mentions(reply, HEADLINES, "Toyota Motor Corporation") == [Mention("Panasonic", "other", (0,))]


def test_a_topic_prompt_asks_what_companies_the_headlines_are_about_without_a_relation():
    [system, user] = topic_prompt("hybrid cars", HEADLINES)
    assert "hybrid cars" in system["content"] and "[3] Honda and Toyota race" in user["content"]
    assert "relation" not in TOPIC_SCHEMA["properties"]["companies"]["items"]["properties"]
    reply = {"companies": [{"name": "Honda", "headlines": [3]}, {"name": "Toyota", "headlines": [1, 3]}]}
    assert parse_mentions(reply, HEADLINES) == [Mention("Honda", "other", (2,)), Mention("Toyota", "other", (0, 2))]


def test_a_question_prompt_carries_the_question_and_numbers_the_items():
    [system, user] = question_prompt("which European companies gain from grid batteries?",
                                     ["2026-09-20 Wire: Siemens Energy wins battery deal"])
    assert "grid batteries" in system["content"] and "why" in system["content"]
    assert user["content"].startswith("News items:\n[1] 2026-09-20 Wire: Siemens Energy wins")
    assert QUESTION_SCHEMA["properties"]["companies"]["items"]["properties"]["why"]["maxLength"] <= 200


def test_a_why_line_comes_back_without_item_numbers_or_citations():
    reply = {"companies": [{"name": "Panasonic", "why": "Battery venture with Toyota [1] (a3)", "headlines": [1]}]}
    [m] = parse_mentions(reply, HEADLINES)
    assert m.why == "Battery venture with Toyota" and m.headlines == (0,)
