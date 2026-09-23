"""JSON shapes more than one route returns, so every tab reads them the same way."""

from __future__ import annotations

from oracle.services.paper_service import Order


def lean(lean, stale: bool) -> dict | None:
    return None if lean is None else {
        "label": lean.label, "probability": lean.probability, "source": lean.source,
        "as_of": lean.as_of, "stale": stale,
    }


def next_step(has_price: bool, lean, stale: bool) -> str | None:
    """What a listing needs before its view is complete, as the button that does it."""
    if not has_price:
        return "update prices"
    if lean is None or stale:
        return "analyse"
    return None


def order_outcome(order: Order, status: str, note: str | None) -> dict:
    return {"symbol": order.symbol, "account": order.account, "side": order.side, "status": status, "note": note}
