"""The hand-labelled sanity set: 110 real headlines from the live DB (2026-09-30), each labelled
for its company by the builder of ticket 01. The live test measures Laya against it; the number
is reported (DECISIONS.md), never a gate."""
import json
from pathlib import Path

import pytest

from oracle.research.laya import DIRECTIONS, EVENT_TYPES, LayaInput, LayaModel

SANITY = json.loads((Path(__file__).parents[1] / "fixtures" / "laya" / "sanity.json").read_text(encoding="utf-8"))


def accuracy(rows: list[dict], scores) -> dict[str, float]:
    """Relevance over every row at 0.5, plus its rank AUC (the probabilities are uncalibrated);
    direction and event over the rows labelled relevant."""
    pos = [s.relevant for s, r in zip(scores, rows) if r["relevant"]]
    neg = [s.relevant for s, r in zip(scores, rows) if not r["relevant"]]
    on = [(s, r) for s, r in zip(scores, rows) if r["relevant"]]
    return {
        "relevance": sum((s.relevant >= 0.5) == r["relevant"] for s, r in zip(scores, rows)) / len(rows),
        "relevance_auc": sum((a > b) + 0.5 * (a == b) for a in pos for b in neg) / (len(pos) * len(neg)),
        "direction": sum(s.direction == r["direction"] for s, r in on) / len(on),
        "event": sum(s.event == r["event"] for s, r in on) / len(on),
    }


def test_the_sanity_set_is_about_a_hundred_labelled_real_headlines():
    assert 100 <= len(SANITY) <= 120
    for row in SANITY:
        assert row["headline"] and row["symbol"] and row["company"] and row["published_at"]
        assert isinstance(row["relevant"], bool) and row["direction"] in DIRECTIONS and row["event"] in EVENT_TYPES
    assert 0.2 < sum(r["relevant"] for r in SANITY) / len(SANITY) < 0.9, "both kinds are there"


@pytest.mark.laya
def test_laya_on_the_sanity_set(capsys):
    """Opt in with -m laya: loads the real checkpoint (downloaded once, about 1.3 GB)."""
    model = LayaModel("auto")
    try:
        scores = model.score([LayaInput(f"{r['company']} ({r['symbol']})", r["headline"]) for r in SANITY])
    finally:
        model.close()
    assert len(scores) == len(SANITY)
    assert all(0 <= s.relevant <= 1 and s.direction in DIRECTIONS and s.event in EVENT_TYPES for s in scores)
    with capsys.disabled():
        print(f"\nLaya {model.tag} on {model.device}: {accuracy(SANITY, scores)}")
