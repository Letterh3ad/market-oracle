"""The hand-labelled tie set: 100 candidate ties from the live DB (2026-09-30), 50 headlines and 50
10-K sentences (8 US main-list names), stratified half on ties the first keyword rule named
(`drawn_rule`) and half on ones it did not, each labelled by the builder of ticket 03 with what the
text shows the other company is. Live, only about a fifth of candidates are rule-named, so recall
here overstates the live one (DECISIONS.md gives both). Only 7 labelled ties are supplier or
customer. The rule's numbers are pinned here; Laya's are measured opt-in (-m laya) and reported in
DECISIONS.md, never a gate. Optimistic for the rule: its patterns were written, and fixed after
gate 03, looking at candidates from the same documents."""
import json
from pathlib import Path

import pytest

from oracle.research.edges import EDGE_CHOICES, rule_type
from oracle.research.laya import EdgeInput, EdgeType, LayaModel
from oracle.services.edges_service import LINKED, decide

TIES = json.loads((Path(__file__).parents[1] / "fixtures" / "laya" / "edges.json").read_text(encoding="utf-8"))


def scores(pred: list[str]) -> dict[str, float]:
    """Accuracy over every row; precision over the ties named (not none); recall over labelled ties."""
    gold = [r["label"] for r in TIES]
    named = [(p, g) for p, g in zip(pred, gold) if p != "none"]
    tied = [(p, g) for p, g in zip(pred, gold) if g != "none"]
    return {"accuracy": sum(p == g for p, g in zip(pred, gold)) / len(gold), "named": len(named),
            "precision": sum(p == g for p, g in named) / len(named) if named else 0.0,
            "recall": sum(p == g for p, g in tied) / len(tied)}


def rule_of(r: dict) -> str:
    return rule_type(r["text"], r["company_names"], r["other_names"])


def test_the_set_is_a_hundred_labelled_ties_from_headlines_and_filings():
    assert len(TIES) == 100 and {r["kind"] for r in TIES} == {"news", "filing"}
    assert all(r["label"] in EDGE_CHOICES and r["text"] for r in TIES)
    assert sum(r["label"] == "none" for r in TIES) == 56, "most candidates show no tie"


def test_the_keyword_rule_beats_always_none_and_is_right_on_over_half_the_ties_it_names():
    rule = scores([rule_of(r) for r in TIES])
    assert scores(["none"] * len(TIES))["accuracy"] == 0.56
    assert (rule["accuracy"], rule["named"], round(rule["precision"], 2)) == (0.67, 45, 0.58)
    assert sum(r["drawn_rule"] != "none" for r in TIES) == 50, "drawn half from ties the rule of the day named"


@pytest.mark.laya
def test_laya_on_the_tie_set(capsys):
    """Opt in with -m laya: loads the real checkpoint."""
    model = LayaModel("auto")
    try:
        typed = model.type_edges([EdgeInput(r["company"], r["other"], r["text"], r["kind"]) for r in TIES])
    finally:
        model.close()
    laya = scores([t.choice for t in typed])
    kept = [decide(rule_of(r), t, r["names"]) for r, t in zip(TIES, typed)]
    agreed = scores([relation if state == LINKED else "none" for relation, state in kept])
    with capsys.disabled():
        print(f"\nlaya {laya}\nlinked where rule and laya agree {agreed}")
    assert isinstance(typed[0], EdgeType)
