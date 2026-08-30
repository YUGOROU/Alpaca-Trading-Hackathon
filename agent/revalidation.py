"""Fail-closed checks immediately before any future broker submission."""

from __future__ import annotations

from typing import Any

from .live_context import load_context_inputs


DEFAULT_MAX_EQUITY_CHANGE_FRACTION = 0.01


def _positions(value: list[tuple[str, float, float]]) -> list[tuple[str, float]]:
    return sorted((str(symbol), float(quantity)) for symbol, quantity, _price in value if quantity)


def _open_order_ids(source: Any) -> list[str] | None:
    method = getattr(source, "open_order_ids", None)
    if method is None:
        return None
    return sorted(str(order_id) for order_id in method())


def revalidate_live_context(
    context_id: str,
    source: Any,
    *,
    max_equity_change_fraction: float = DEFAULT_MAX_EQUITY_CHANGE_FRACTION,
) -> dict[str, Any]:
    """Compare the stored live snapshot to current broker state.

    The comparison intentionally rejects on any unavailable field.  A later
    executor may act only after this function reports ``ok: true`` and records
    the result in the canonical ledger.
    """
    if not 0 <= max_equity_change_fraction <= 1:
        raise ValueError("max_equity_change_fraction must be between 0 and 1")
    row = load_context_inputs(context_id)
    if row is None:
        return {"ok": False, "reasons": ["unknown_context"]}
    if row.get("input_provenance", {}).get("source") != "alpaca_rest":
        return {"ok": False, "reasons": ["non_live_context"]}
    baseline = row.get("execution_snapshot")
    if not isinstance(baseline, dict):
        return {"ok": False, "reasons": ["missing_execution_snapshot"]}

    try:
        market_open = getattr(source, "is_market_open")()
        equity, _cash = source.account()
        positions = _positions(source.positions())
        open_order_ids = _open_order_ids(source)
    except Exception:
        return {"ok": False, "reasons": ["revalidation_source_error"]}

    reasons: list[str] = []
    if market_open is not True:
        reasons.append("market_not_open")
    baseline_equity = float(baseline.get("equity", 0))
    if baseline_equity <= 0:
        reasons.append("invalid_baseline_equity")
    elif abs(float(equity) - baseline_equity) / baseline_equity > max_equity_change_fraction:
        reasons.append("equity_changed")
    baseline_positions = sorted(
        (str(symbol), float(quantity)) for symbol, quantity in baseline.get("positions", [])
    )
    if positions != baseline_positions:
        reasons.append("positions_changed")
    if open_order_ids is None or open_order_ids != baseline.get("open_order_ids"):
        reasons.append("open_orders_changed")
    return {
        "ok": not reasons,
        "reasons": reasons,
        "context_id": context_id,
        "equity": float(equity),
        "open_order_ids": open_order_ids,
    }
