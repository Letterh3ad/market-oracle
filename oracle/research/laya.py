"""Laya (convaiinnovations/laya-multilingual): a small encoder that answers typed questions about
a text in one forward pass. It reads one headline (and snippet) for one company and says how
likely the news is about that company, which way it points, and what kind of event it is. Its
probabilities are uncalibrated: no temperatures are fitted here."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from typing import Protocol

CHECKPOINT = "convaiinnovations/laya-multilingual"
REVISION = "e4e9ddf21a7b1903b7acffd8814ad4307bf63a67"
"""laya's reviewed commit for the checkpoint (`laya.PINNED_REVISIONS`), so a tag names fixed weights."""
MODEL_TAG = f"laya-multilingual@{REVISION[:7]}"
"""Stored with every score: a new checkpoint scores every article again under its own tag."""

EVENT_TYPES: dict[str, str] = {
    "earnings": "quarterly or annual results, revenue or profit figures",
    "guidance": "an outlook or forecast the company gives or changes",
    "launch": "a new product, service or market launch",
    "recall_safety": "a product recall, safety problem or accident",
    "legal_regulatory": "a lawsuit, fine, investigation, regulation or government ruling",
    "mna": "a merger, acquisition, sale of a business, stake or investment",
    "management": "a change of chief executive, board or other leaders",
    "analyst_rating": "an analyst upgrade, downgrade or price target",
    "supply_production": "production, supply chain, factories, shortages or deliveries",
    "other": "anything else",
}
DIRECTIONS = ("up", "down", "none")


def questions(company: str) -> dict:
    """The three questions, naming the target: this wording scored best of five tried on the
    sanity set (tests/fixtures/laya), so that set's accuracy is optimistic."""
    return {
        "relevant": {"type": "noul", "instructions": f"Is this headline about {company}, or does it directly "
                     f"affect {company}'s business?"},
        "direction": {"type": "choice", "instructions": f"Is this headline good or bad news for {company}'s share "
                      "price?", "criteria": {"up": f"good news for {company}", "down": f"bad news for {company}",
                                             "none": "neither, or unclear"}},
        "event": {"type": "choice", "instructions": "What kind of event is this headline about?",
                  "criteria": EVENT_TYPES},
    }


GPU_FREE_MB = 3000
"""Free GPU memory `auto` needs before it loads there. Measured 2026-09-30 on the RTX 5060: loading
took about 2.5 GB and scoring 110 headlines 3.2 GB. Ollama's 4B research model holds 5.1 GB of the
8 GB, so with it loaded `auto` scores on the CPU instead of pushing either out to shared memory."""


@dataclass(frozen=True)
class LayaInput:
    company: str
    """The target, as "Name (SYMBOL)"."""
    headline: str
    snippet: str | None = None


@dataclass(frozen=True)
class LayaScore:
    relevant: float
    """P(the news is about the company), uncalibrated."""
    direction: str
    direction_probs: dict[str, float]
    event: str
    event_prob: float


class Scorer(Protocol):
    tag: str
    device: str

    def score(self, items: list[LayaInput]) -> list[LayaScore]:
        """One score per item, in order."""


def state(item: LayaInput) -> str:
    return f"Company: {item.company}\nHeadline: {item.headline}" + (f"\nSnippet: {item.snippet}" if item.snippet else "")


def parse(result: dict) -> LayaScore:
    a = result["answers"]
    direction, event = a["direction"], a["event"]
    return LayaScore(float(a["relevant"]["noul"]), direction["choice"],
                     {k: float(v) for k, v in direction["probabilities"].items()}, event["choice"],
                     float(event["probabilities"][event["choice"]]))


def gpu_free_mb() -> float | None:
    """The card's free memory as the driver reports it for every process (nvidia-smi); torch's
    own figure on Windows leaves out what Ollama holds. None when it cannot be read."""
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=memory.used,memory.total", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=10, check=True).stdout
        return smi_free_mb(out)
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def smi_free_mb(out: str) -> float:
    used, total = (float(x) for x in out.splitlines()[0].split(","))
    return total - used


class LayaUnavailable(RuntimeError):
    """The package or its model cannot be loaded here."""


def pick_device(choice: str, cuda: bool, free_mb: float | None) -> tuple[str, str]:
    """The device to load on and why. `auto` takes the GPU only with room beside what else holds
    it; `gpu` asked for and absent falls back to the CPU, saying so."""
    if choice == "cpu":
        return "cpu", "CPU as set"
    if not cuda:
        return "cpu", "no CUDA GPU; on the CPU"
    if choice == "gpu":
        return "cuda", "GPU as set"
    if free_mb is not None and free_mb < GPU_FREE_MB:
        return "cpu", f"GPU has {free_mb:.0f} MB free (needs {GPU_FREE_MB}); on the CPU"
    return "cuda", f"GPU, {free_mb:.0f} MB free" if free_mb is not None else "GPU"


class LayaModel:
    """The real model. Loading takes seconds and holds memory, so a caller keeps one for a batch
    of work and `close`s it after, giving the GPU back to Ollama."""

    tag = MODEL_TAG

    def __init__(self, choice: str = "auto", batch_size: int = 16) -> None:
        os.environ.setdefault("USE_TF", "0")
        # A broken Windows torch raises OSError (a DLL that will not load), not ImportError.
        try:
            import laya
            import torch
        except Exception as exc:
            raise LayaUnavailable(f"laya or torch will not load ({type(exc).__name__}: {exc}); "
                                  "install or repair them: pip install -e .[laya]") from exc
        self._torch = torch
        try:
            cuda = torch.cuda.is_available()
            free = gpu_free_mb() if cuda else None
            device, self.why = pick_device(choice, cuda, free)
            self._agent = laya.load(CHECKPOINT, device=device, revision=REVISION)
        except Exception as exc:
            raise LayaUnavailable(f"could not load {CHECKPOINT}: {exc}") from exc
        # The agent falls back to the CPU by itself when the GPU runs out of memory at load.
        self.device = str(self._agent.device.type)
        self._batch = batch_size

    def score(self, items: list[LayaInput]) -> list[LayaScore]:
        """The questions name the company, so each company's items share a forward pass."""
        out: dict[int, LayaScore] = {}
        for company in dict.fromkeys(i.company for i in items):
            at = [n for n, i in enumerate(items) if i.company == company]
            results = self._agent.predict_batch([state(items[n]) for n in at], questions(company),
                                                batch_size=self._batch)
            out.update({n: parse(r) for n, r in zip(at, results)})
        return [out[n] for n in range(len(items))]

    def close(self) -> None:
        self._agent = None
        if self.device == "cuda":
            self._torch.cuda.empty_cache()
