"""A small Open Source Asset Pricing release on disk. SignalDoc.csv is a verbatim extract of the
2025.10 release (tests/fixtures/osap). The portfolio returns are synthetic, in OSAP's file layout:
each original-paper series is shaped to its paper's published mean and t over the paper's sample, and
the large-cap series to a chosen mean and t since 2000."""

from __future__ import annotations

import math
import shutil
import zipfile
from pathlib import Path

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "osap" / "SignalDoc.csv"
HEADER = "signalname,port,date,ret,signallag,Nlong,Nshort\n"

PAPERS = {"Mom12m": (1964, 1989, 1.31, 3.74), "GP": (1963, 2010, 0.31, 2.49), "AssetGrowth": (1968, 2003, 1.73, 8.45)}
"""Sample years, mean %/month and t as published (SignalDoc's Return and T-Stat)."""
LARGE = {"Mom12m": (0.30, 1.0), "GP": (0.35, 2.0), "AssetGrowth": (-0.10, -0.5)}
"""Large caps since 2000: mean %/month and t."""


def months(first: int, last: int) -> list[str]:
    return [f"{y}-{m:02d}" for y in range(first, last + 1) for m in range(1, 13)]


def shaped(ms: list[str], mean: float, t: float) -> dict[str, float]:
    """Alternating around `mean` so the sample t is exactly `t` (an even count of months)."""
    n = len(ms)
    sd = abs(mean) * math.sqrt(n) / abs(t)
    d = sd * math.sqrt((n - 1) / n)
    return {m: mean + (d if k % 2 else -d) for k, m in enumerate(ms)}


def series(signal: str, variant: str) -> dict[str, float]:
    first, last, mean, t = PAPERS[signal]
    inside = shaped(months(first, last), mean, t)
    if variant == "op":
        return {**inside, **shaped(months(last + 1, 2024), mean / 2, 2.0)}
    mean, t = LARGE[signal]
    return {**shaped(months(1990, 1999), 0.5, 2.0), **shaped(months(2000, 2024), mean, t)}


def csv_text(variant: str) -> str:
    lines = [HEADER]
    for signal in PAPERS:
        for month, ret in sorted(series(signal, variant).items()):
            lines.append(f"{signal},LS,{month}-28,{ret!r},1,50,50\n")
            lines.append(f"{signal},01,{month}-28,{ret / 2!r},1,50,\n")
        lines.append(f"{signal},LS,1950-01-31,NA,1,0,0\n")
    return "".join(lines)


def write_release(folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    shutil.copy(FIXTURE, folder / "SignalDoc.csv")
    (folder / "PredictorPortsFull.csv").write_text(csv_text("op"), encoding="utf-8")
    with zipfile.ZipFile(folder / "PredictorAltPorts_LiqScreen_ME_gt_NYSE20pct.zip", "w") as z:
        z.writestr("PredictorAltPorts_LiqScreen_ME_gt_NYSE20pct.csv", csv_text("large"))
    return folder
