import pytest

from oracle.research.laya import (
    EVENT_TYPES, GPU_FREE_MB, LayaInput, LayaScore, parse, pick_device, questions, smi_free_mb, state,
)


def test_the_ten_event_types_are_the_specs_fixed_set():
    assert list(EVENT_TYPES) == ["earnings", "guidance", "launch", "recall_safety", "legal_regulatory", "mna",
                                 "management", "analyst_rating", "supply_production", "other"]
    q = questions("Apple Inc. (AAPL)")
    assert q["event"]["criteria"] is EVENT_TYPES and list(q["direction"]["criteria"]) == ["up", "down", "none"]
    assert (q["relevant"]["type"], q["direction"]["type"]) == ("noul", "choice")
    assert "Apple Inc. (AAPL)" in q["relevant"]["instructions"]


def test_the_model_reads_the_company_headline_and_snippet_only():
    assert state(LayaInput("Apple Inc. (AAPL)", "Apple beats")) == "Company: Apple Inc. (AAPL)\nHeadline: Apple beats"
    assert state(LayaInput("Apple Inc. (AAPL)", "Apple beats", "Revenue rose 5%")).endswith("\nSnippet: Revenue rose 5%")


def test_an_answer_becomes_a_score():
    result = {"answers": {
        "relevant": {"type": "noul", "noul": 0.83},
        "direction": {"type": "choice", "choice": "down", "probabilities": {"up": 0.1, "down": 0.7, "none": 0.2}},
        "event": {"type": "choice", "choice": "mna", "probabilities": {"mna": 0.6, "other": 0.4}},
    }}
    assert parse(result) == LayaScore(0.83, "down", {"up": 0.1, "down": 0.7, "none": 0.2}, "mna", 0.6)


@pytest.mark.parametrize("choice, cuda, free, device", [
    ("cpu", True, 8000, "cpu"),
    ("auto", False, None, "cpu"),
    ("gpu", False, None, "cpu"),
    ("gpu", True, 100, "cuda"),
    ("auto", True, GPU_FREE_MB - 1, "cpu"),
    ("auto", True, GPU_FREE_MB, "cuda"),
])
def test_auto_takes_the_gpu_only_with_room_beside_ollama(choice, cuda, free, device):
    picked, why = pick_device(choice, cuda, free)
    assert picked == device and why


def test_free_memory_is_the_whole_cards_as_nvidia_smi_reports_it():
    assert smi_free_mb("5920, 8151\n") == 2231
