"""Each model's training cutoff by model tag (spec decision 7, DECISIONS 2026-10-01): a model-read
signal counts only on decision days after it. A published date where there is one; else, or when
sources differ, the later date (a later cutoff throws away more days, the conservative side)."""

from __future__ import annotations

from datetime import date

from oracle.research.laya import MODEL_TAG
from oracle.research.reader import reader_tag

__all__ = ["CUTOFFS", "DEFAULT_CUTOFF", "training_cutoff"]

DEFAULT_CUTOFF = date(2025, 5, 31)
"""For a model with no published cutoff."""
CUTOFFS = {
    # mmBERT's pretraining web data ends April 2024 (FineWeb2), but neither mmBERT nor Laya's
    # decision head publishes a cutoff or dates its training data.
    MODEL_TAG: DEFAULT_CUTOFF,
    # Alibaba publishes no cutoff for Qwen2.5; third parties say 2023-10, end of 2023 or 2024-06-30.
    reader_tag("qwen2.5:32b"): DEFAULT_CUTOFF,
}


def training_cutoff(model: str) -> date:
    return CUTOFFS.get(model, DEFAULT_CUTOFF)
