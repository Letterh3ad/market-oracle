from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class Dividend:
    ex_date: date
    amount: float
    """Cash per share in the major unit of `currency` (pence lines are stored in pounds)."""
    currency: str
    pay_date: date | None = None
    """When the cash is paid; None while no source has said."""
