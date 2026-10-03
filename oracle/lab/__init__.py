"""research-lab: the literature index's matching rules and its seed."""

from __future__ import annotations

import json
from importlib import resources


def load_seed() -> dict:
    """The research archive (185 rated methods), its 14 tested methods and ai-track 04-09, with the
    sources they cite. Generated once from `.scratch/next-ideas/research-archive.md` (2026-10-03)."""
    return json.loads(resources.files(__name__).joinpath("seed.json").read_text(encoding="utf-8"))
