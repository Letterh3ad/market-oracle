import pytest

from oracle.research.budget import budget
from oracle.research.notes import (
    MAX_FACT, MAX_LISTED, MAX_NOTE_TEXT, Note, is_list_line, listed, read_note, take_note,
)
from tests.fakes.llm import ScriptedModel

HEADING = "2026-09-20 Reuters: Apple beats estimates"


async def test_one_call_per_article_with_a_short_prompt_and_a_bounded_schema():
    model = ScriptedModel()
    model.roles["note_taker"] = {"about_company": True, "facts": ["Revenue rose 8% to $95bn", "Guidance raised"]}
    note = await take_note(model, "Apple Inc. (AAPL)", HEADING, "Apple said revenue rose 8%.", 8192)
    assert note == Note(True, ("Revenue rose 8% to $95bn", "Guidance raised"))
    [(messages, schema)] = model.calls
    system, user = messages
    assert system["content"].startswith("Role: note_taker.") and len(system["content"]) < 700
    assert "Apple Inc. (AAPL)" in system["content"]
    assert user["content"].startswith(HEADING) and user["content"].endswith("Apple said revenue rose 8%.")
    assert schema["properties"]["facts"]["maxItems"] == 3
    assert schema["properties"]["about_company"]["type"] == "boolean"
    assert set(schema["required"]) == {"about_company", "facts"}


def test_the_text_is_cut_to_fit_the_context_and_never_past_the_cap():
    from oracle.research.notes import note_room, system_prompt

    system = system_prompt("Apple Inc. (AAPL)")
    assert note_room(16384, system, HEADING) == MAX_NOTE_TEXT
    assert note_room(4096, system, HEADING) == budget(4096, system) - len(HEADING) - 2 < MAX_NOTE_TEXT
    assert note_room(512, system, HEADING) == 0


async def test_a_long_article_is_cut_and_marked_so():
    model = ScriptedModel()
    await take_note(model, "Apple", HEADING, "x" * 50_000, 4096)
    user = model.calls[0][0][1]["content"]
    assert user.endswith(" [cut]") and len(user) <= budget(4096, model.calls[0][0][0]["content"]) + len(" [cut]")


@pytest.mark.parametrize("reply", ["garbled", {"facts": ["a"]}, {"about_company": "yes", "facts": []},
                                   {"about_company": True, "facts": "one fact"}])
def test_a_garbled_reply_gives_no_note(reply):
    assert read_note(reply) is None


def test_facts_are_trimmed_cut_deduplicated_and_at_most_three():
    note = read_note({"about_company": True, "facts": [" A ", "", 7, "A", "B", "C" * 500, "D"]})
    assert note.facts == ("A", "B", "C" * (MAX_FACT - 1) + "…")


def test_an_article_not_about_the_company_keeps_no_facts():
    assert read_note({"about_company": False, "facts": ["Pears are up"]}) == Note(False, ())


def test_facts_are_asked_for_in_english_whatever_the_articles_language():
    from oracle.research.notes import system_prompt

    assert "in English" in system_prompt("Sony Group (6758.T)")


def test_a_fact_cannot_carry_a_citation_the_page_wrote():
    note = read_note({"about_company": True, "facts": ["Revenue rose [a12] 8% (a3, a4)", "[a9]"]})
    assert note == Note(True, ("Revenue rose 8%",))


LIST = ("Top 10 chipmakers in Europe\nASML makes lithography machines.\nNXP Semiconductors, Eindhoven.\n"
        "STMicroelectronics N.V. makes power chips.\nMelaxis builds sensors.\nAixtron builds deposition tools.")
FOUR = ("ASML", "NXP Semiconductors", "STMicroelectronics", "Melaxis")


async def test_a_note_for_a_question_may_keep_the_companies_a_list_page_names():
    model = ScriptedModel()
    model.roles["note_taker"] = {"about_company": True, "facts": ["Europe makes 10% of chips"],
                                 "companies": list(FOUR)}
    note = await take_note(model, 'companies that answer "European chipmakers?"', HEADING, LIST, 8192, lists=True)
    assert note == Note(True, ("Europe makes 10% of chips",
                               "Lists: ASML; NXP Semiconductors; STMicroelectronics; Melaxis"))
    assert listed(note.facts) == FOUR
    [(messages, schema)] = model.calls
    assert '"top 10"' in messages[0]["content"] and len(messages[0]["content"]) < 1000
    assert schema["properties"]["companies"]["maxItems"] == MAX_LISTED == 15
    assert "companies" in schema["required"] and schema["properties"]["facts"]["maxItems"] == 3


async def test_research_notes_never_ask_for_companies():
    model = ScriptedModel()
    await take_note(model, "Apple Inc. (AAPL)", HEADING, "Apple said revenue rose 8%.", 8192)
    [(messages, schema)] = model.calls
    assert "companies" not in schema["properties"] and '"top 10"' not in messages[0]["content"]
    note = read_note({"about_company": True, "facts": ["A"], "companies": ["Apple"]})
    assert note == Note(True, ("A",)), "names are kept only when asked for"


async def test_listed_names_are_checked_against_the_text_the_model_was_given():
    model = ScriptedModel()
    model.roles["note_taker"] = {"about_company": True, "facts": [],
                                 "companies": ["ASML", "Late Corp", "NXP", "Aixtron", "Melaxis"]}
    note = await take_note(model, "q", HEADING, LIST + "More. " * 3000 + "Late Corp.", 4096, lists=True)
    assert listed(note.facts) == ("ASML", "NXP", "Aixtron", "Melaxis"), "past the cut the model never saw it"


def test_a_listed_name_the_text_does_not_hold_is_dropped_and_the_rest_capped():
    reply = {"about_company": True, "facts": [], "companies": [
        "ASML", "Melexis", "Melaxis", "asml", " ", 7, "Intel", "STMicroelectronics", "Aixtron"]}
    assert listed(read_note(reply, LIST).facts) == ("ASML", "Melaxis", "STMicroelectronics", "Aixtron"), \
        "as the page spells it"
    many = {"about_company": True, "facts": [], "companies": [f"Firm {i}" for i in range(30)]}
    assert len(listed(read_note(many, " ".join(f"Firm {i}." for i in range(30))).facts)) == MAX_LISTED


def test_a_listed_name_cannot_carry_a_citation_the_model_wrote():
    reply = {"about_company": True, "facts": [], "companies": ["ASML [a3]", "NXP Semiconductors (a1)", *FOUR[2:]]}
    assert listed(read_note(reply, LIST).facts) == FOUR


def test_a_page_not_about_the_question_lists_nothing():
    reply = {"about_company": False, "facts": [], "companies": ["ASML"]}
    assert read_note(reply, LIST) == Note(False, ())


@pytest.mark.parametrize("names", [["Intel"], ["ASML", "Melaxis", "Intel", "NXP Semiconductors"]])
def test_a_story_naming_fewer_than_four_is_no_list(names):
    note = read_note({"about_company": True, "facts": ["A"], "companies": names}, LIST)
    assert note == Note(True, ("A",)) and listed(note.facts) == ()


def test_a_research_note_keeps_a_fact_that_reads_like_a_list_line():
    assert read_note({"about_company": True, "facts": ["Lists: 3 new chips"]}) == Note(True, ("Lists: 3 new chips",))
    note = read_note({"about_company": True, "facts": ["Lists: 3 new chips"], "companies": list(FOUR)}, LIST)
    assert note.facts == ("Lists: ASML; NXP Semiconductors; STMicroelectronics; Melaxis",), "one list line, ours"


def test_a_list_line_is_known_as_a_note_line_or_an_items_line():
    assert is_list_line("Lists: A; B") and is_list_line("  - Lists: A; B")
    assert not is_list_line("Listsy: A") and not is_list_line("Apple Lists: A")
