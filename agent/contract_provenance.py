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


def record_protective_put_open(
    ledger: str | Path, *, decision_id: str, contract: str, quantity: int, broker_order_id: str
) -> dict:
    if len(contract) < 15 or contract[:-15] != "SPY" or contract[-9] != "P":
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
