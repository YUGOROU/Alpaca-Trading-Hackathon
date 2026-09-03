"""Append-only intent ledger for autonomously opened protective-put contracts.

This is deliberately separate from the decision lifecycle ledger.  It records
only exact OCC contracts that this executor opened as a protective hedge after
the broker accepted the order.  A future close/roll path must intersect this
inventory with the broker's current position; unrecorded and income-leg puts
are never eligible.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path


def provenance_path(ledger: str | Path) -> Path:
    target = Path(ledger)
    return target.with_name(f"{target.stem}.contracts.jsonl")


def is_spy_occ_put(contract: object) -> bool:
    """Return true only for the exact OCC shape we record for autonomous hedges."""
    return (
        isinstance(contract, str)
        and len(contract) == 18
        and contract.startswith("SPY")
        and contract[3:9].isdigit()
        and contract[9] == "P"
        and contract[10:].isdigit()
    )


def record_protective_put_open(
    ledger: str | Path, *, decision_id: str, contract: str, quantity: int, broker_order_id: str
) -> dict:
    if not is_spy_occ_put(contract):
        raise ValueError("contract must be an SPY OCC put")
    if not isinstance(quantity, int) or quantity <= 0:
        raise ValueError("quantity must be a positive whole number")
    if not broker_order_id:
        raise ValueError("broker_order_id is required")
    path = provenance_path(ledger)
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()] if path.exists() else []
    existing = next((row for row in rows if row.get("broker_order_id") == broker_order_id), None)
    if existing is not None:
        if existing.get("contract") == contract and existing.get("quantity_opened") == quantity:
            return existing
        raise ValueError("broker_order_id already has different contract provenance")
    row = {
        "schema_version": 1,
        "event": "protective_put_opened",
        "recorded_at": datetime.now(UTC).isoformat(),
        "decision_id": decision_id,
        "strategy": "protective_put",
        "leg_role": "hedge_long_put",
        "underlying": "SPY",
        "contract": contract,
        "quantity_opened": quantity,
        "broker_order_id": broker_order_id,
    }
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(row, sort_keys=True) + "\n")
    return row


def recorded_protective_puts(ledger: str | Path) -> list[dict]:
    path = provenance_path(ledger)
    if not path.exists():
        return []
    return [
        json.loads(line) for line in path.read_text().splitlines() if line.strip()
    ]


def recorded_protective_put_contracts(ledger: str | Path) -> list[dict]:
    """Return only valid autonomous SPY hedge-open records for close matching.

    This intentionally does not infer ownership from a similarly named broker
    position.  The executor must still intersect this list with a fresh,
    positive broker position immediately before placing a close.
    """
    return [
        row for row in recorded_protective_puts(ledger)
        if row.get("event") == "protective_put_opened"
        and row.get("strategy") == "protective_put"
        and row.get("leg_role") == "hedge_long_put"
        and row.get("underlying") == "SPY"
        and is_spy_occ_put(row.get("contract"))
        and isinstance(row.get("quantity_opened"), int)
        and row["quantity_opened"] > 0
        and isinstance(row.get("broker_order_id"), str)
        and bool(row["broker_order_id"])
    ]
