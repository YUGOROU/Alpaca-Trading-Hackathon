"""Append-only local dry-run decision log with decision-id idempotency."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .contracts import AgentDecision, GateResult, validate_execution_mode


def _read_rows(target: Path) -> list[dict[str, Any]]:
    if not target.exists():
        return []
    return [
        json.loads(line)
        for line in target.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _append_row(target: Path, row: dict[str, Any]) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def record_dry_run(
    path: str | Path,
    decision_id: str,
    scenario_id: str,
    decision: AgentDecision,
    result: GateResult,
    *,
    execution_mode: str = "human",
) -> dict[str, Any]:
    """Persist the canonical proposal event.

    Gate approval means only that a paper-order *proposal* is safe to record.
    The resulting state is never broker-submittable without a later approval
    transition.
    """
    execution_mode = validate_execution_mode(execution_mode)
    if not decision_id.strip():
        raise ValueError("decision_id is required")
    target = Path(path)
    existing = _read_rows(target)
    if existing:
        prior = next((row for row in existing if row.get("event") == "proposal" and row["decision_id"] == decision_id), None)
        if prior is not None:
            same_decision = prior["decision"] == {
                "context_id": decision.context_id,
                "candidate_id": decision.candidate_id,
                "reason": decision.reason,
            }
            if same_decision and prior["gate"] == result.to_dict() and prior.get("execution", {}).get("mode") == execution_mode:
                return prior
            raise ValueError("decision_id already exists with different content")

    row = {
        "schema_version": 2,
        "timestamp": datetime.now(UTC).isoformat(),
        "event": "proposal",
        "decision_id": decision_id,
        "scenario_id": scenario_id,
        "decision": {
            "context_id": decision.context_id,
            "candidate_id": decision.candidate_id,
            "reason": decision.reason,
        },
        "gate": result.to_dict(),
        "execution": {
            "mode": execution_mode,
            "state": "proposed" if result.approved else "rejected",
            "approval_source": None,
        },
    }
    _append_row(target, row)
    return row


def record_human_approval(
    path: str | Path, decision_id: str, *, approved_by: str
) -> dict[str, Any]:
    """Append the only approval transition currently supported by the runtime.

    This never submits an order.  It creates a durable approval record that a
    later paper-only executor can consume after it has revalidated the context.
    """
    if not decision_id.strip():
        raise ValueError("decision_id is required")
    if not approved_by.strip():
        raise ValueError("approved_by is required")
    target = Path(path)
    rows = _read_rows(target)
    proposal = next((row for row in rows if row.get("event") == "proposal" and row.get("decision_id") == decision_id), None)
    if proposal is None:
        raise ValueError("unknown decision_id")
    execution = proposal.get("execution", {})
    if proposal.get("gate", {}).get("status") != "approved_for_dry_run":
        raise ValueError("only approved proposals may receive human approval")
    if execution.get("mode") != "human":
        raise ValueError("human approval is only valid in human execution mode")

    prior = next((row for row in rows if row.get("event") == "approval" and row.get("decision_id") == decision_id), None)
    if prior is not None:
        if prior.get("execution", {}).get("approved_by") == approved_by:
            return prior
        raise ValueError("decision_id already has a different approval")

    row = {
        "schema_version": 2,
        "timestamp": datetime.now(UTC).isoformat(),
        "event": "approval",
        "decision_id": decision_id,
        "context_id": proposal["decision"]["context_id"],
        "execution": {
            "mode": "human",
            "state": "approved",
            "approval_source": "human",
            "approved_by": approved_by,
        },
    }
    _append_row(target, row)
    return row


def execution_state(path: str | Path, decision_id: str) -> dict[str, Any] | None:
    """Return the latest canonical execution state for one decision."""
    rows = [row for row in _read_rows(Path(path)) if row.get("decision_id") == decision_id]
    if not rows:
        return None
    return rows[-1].get("execution")


def _proposal(rows: list[dict[str, Any]], decision_id: str) -> dict[str, Any]:
    proposal = next((row for row in rows if row.get("event") == "proposal" and row.get("decision_id") == decision_id), None)
    if proposal is None:
        raise ValueError("unknown decision_id")
    return proposal


def proposal_orders(path: str | Path, decision_id: str) -> list[dict[str, Any]]:
    """Return immutable gate orders only for the canonical decision record."""
    return list(_proposal(_read_rows(Path(path)), decision_id)["gate"]["orders"])


def proposal_context_id(path: str | Path, decision_id: str) -> str:
    return str(_proposal(_read_rows(Path(path)), decision_id)["decision"]["context_id"])


def record_submission_requested(
    path: str | Path, decision_id: str, *, revalidation: dict[str, Any]
) -> dict[str, Any]:
    """Record the last fail-closed checkpoint before a future broker call."""
    target = Path(path)
    rows = _read_rows(target)
    proposal = _proposal(rows, decision_id)
    latest = execution_state(target, decision_id)
    if latest is None or latest.get("state") != "approved":
        raise ValueError("submission requires an approved decision")
    if revalidation.get("ok") is not True:
        raise ValueError("submission requires successful revalidation")
    if revalidation.get("context_id") != proposal["decision"]["context_id"]:
        raise ValueError("revalidation context does not match decision")
    row = {
        "schema_version": 2,
        "timestamp": datetime.now(UTC).isoformat(),
        "event": "submission_requested",
        "decision_id": decision_id,
        "context_id": proposal["decision"]["context_id"],
        "revalidation": {
            "ok": True,
            "equity": revalidation.get("equity"),
            "open_order_ids": revalidation.get("open_order_ids"),
        },
        "execution": {
            "mode": "human",
            "state": "submission_requested",
            "approval_source": "human",
        },
    }
    _append_row(target, row)
    return row


BROKER_STATES = frozenset({"accepted", "partially_filled", "filled", "rejected", "canceled", "expired", "submission_failed"})


def record_broker_update(
    path: str | Path, decision_id: str, *, state: str, broker_orders: list[dict[str, Any]]
) -> dict[str, Any]:
    """Append normalized broker evidence after a submission attempt or poll."""
    if state not in BROKER_STATES:
        raise ValueError(f"unsupported broker state: {state}")
    if not broker_orders:
        raise ValueError("broker_orders is required")
    target = Path(path)
    rows = _read_rows(target)
    proposal = _proposal(rows, decision_id)
    latest = execution_state(target, decision_id)
    if latest is None or latest.get("state") not in {"submission_requested", "accepted", "partially_filled"}:
        raise ValueError("broker update requires a submission request")
    normalized = []
    for order in broker_orders:
        order_id = order.get("alpaca_order_id")
        if not isinstance(order_id, str) or not order_id:
            raise ValueError("every broker order requires alpaca_order_id")
        normalized.append({"alpaca_order_id": order_id, "state": state})
    row = {
        "schema_version": 2,
        "timestamp": datetime.now(UTC).isoformat(),
        "event": "broker_update",
        "decision_id": decision_id,
        "context_id": proposal["decision"]["context_id"],
        "broker_orders": normalized,
        "execution": {
            "mode": "human",
            "state": state,
            "approval_source": "human",
        },
    }
    _append_row(target, row)
    return row
