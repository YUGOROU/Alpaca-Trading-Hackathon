"""Append-only local dry-run decision log with decision-id idempotency."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .contracts import AgentDecision, GateResult


def record_dry_run(
    path: str | Path,
    decision_id: str,
    scenario_id: str,
    decision: AgentDecision,
    result: GateResult,
) -> dict[str, Any]:
    if not decision_id.strip():
        raise ValueError("decision_id is required")
    target = Path(path)
    existing: list[dict[str, Any]] = []
    if target.exists():
        existing = [
            json.loads(line)
            for line in target.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        prior = next((row for row in existing if row["decision_id"] == decision_id), None)
        if prior is not None:
            same_decision = prior["decision"] == {
                "context_id": decision.context_id,
                "candidate_id": decision.candidate_id,
                "reason": decision.reason,
            }
            if same_decision and prior["gate"] == result.to_dict():
                return prior
            raise ValueError("decision_id already exists with different content")

    row = {
        "decision_id": decision_id,
        "scenario_id": scenario_id,
        "decision": {
            "context_id": decision.context_id,
            "candidate_id": decision.candidate_id,
            "reason": decision.reason,
        },
        "gate": result.to_dict(),
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    return row
