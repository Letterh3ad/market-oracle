import pytest

from oracle.research.budget import budget
from oracle.research.notes import MAX_FACT, MAX_NOTE_TEXT, Note, read_note, take_note
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
