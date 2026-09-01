"""Safe, public projection of the paper-agent decision ledger.

This module deliberately exposes aggregate operational evidence only.  It does
not return broker identifiers, order detail, account values, positions, model
prompts, decision reasons, approval identities, or raw ledger records.
"""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from typing import Any

try:  # ``heartbeat_server.py`` runs as a script inside /app/deploy.
    from approval_server import rows
except ModuleNotFoundError:  # Unit tests import this module from the repository root.
    from deploy.approval_server import rows


_CANDIDATE_LABELS = {
    "en": {"hold": "Continue monitoring", "harvest_income": "Evaluate income overlay", "partial_hedge": "Evaluate partial hedge", "full_hedge": "Evaluate protective hedge"},
    "ja": {"hold": "監視を継続", "harvest_income": "インカム・オーバーレイを評価", "partial_hedge": "部分ヘッジを評価", "full_hedge": "保護ヘッジを評価"},
}
_STATE_LABELS = {
    "en": {"proposed": "Gate passed — awaiting action", "rejected": "Blocked by gate", "approved": "Approved by human", "authorized": "Authorized by policy", "submission_requested": "Pre-submit revalidation passed", "accepted": "Paper order accepted", "partially_filled": "Paper order partially filled", "filled": "Paper order filled", "canceled": "Paper order canceled", "expired": "Paper order expired", "submission_failed": "Submission stopped", "submission_unknown": "Submission being reconciled"},
    "ja": {"proposed": "gate 通過・待機中", "rejected": "gate が遮断", "approved": "人の承認済み", "authorized": "自律ポリシーで認可", "submission_requested": "送信前の再検証を通過", "accepted": "paper 注文を受付", "partially_filled": "paper 注文を一部約定", "filled": "paper 注文を約定", "canceled": "paper 注文を取消", "expired": "paper 注文を失効", "submission_failed": "送信を停止", "submission_unknown": "送信状態を照合中"},
}
_SERVICE_LABELS = {
    "en": {"live": "live paper input", "unavailable": "input unavailable"},
    "ja": {"live": "ライブ paper 入力", "unavailable": "入力を利用できません"},
}


def _timestamp(value: object) -> str | None:
    return value if isinstance(value, str) and len(value) <= 64 else None


def _latest_decisions(ledger_rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Return a minimal per-decision state, ignoring malformed ledger entries."""
    decisions: dict[str, dict[str, Any]] = {}
    for row in ledger_rows:
        if not isinstance(row, dict):
            continue
        decision_id = row.get("decision_id")
        if not isinstance(decision_id, str) or not decision_id:
            continue
        if row.get("event", "proposal") == "proposal":
            decision = row.get("decision")
            gate = row.get("gate")
            execution = row.get("execution")
            if not isinstance(decision, dict) or not isinstance(gate, dict) or not isinstance(execution, dict):
                continue
            candidate_id = decision.get("candidate_id")
            gate_status = gate.get("status")
            state = execution.get("state")
            if candidate_id not in _CANDIDATE_LABELS["en"] or gate_status not in {"approved_for_dry_run", "rejected"} or state not in _STATE_LABELS["en"]:
                continue
            decisions[decision_id] = {
                "timestamp": _timestamp(row.get("timestamp")),
                "candidate": candidate_id,
                "gate": gate_status,
                "state": state,
                "mode": execution.get("mode") if execution.get("mode") in {"human", "autonomous-paper"} else "human",
            }
            continue
        current = decisions.get(decision_id)
        execution = row.get("execution")
        if current is None or not isinstance(execution, dict):
            continue
        state = execution.get("state")
        if state in _STATE_LABELS["en"]:
            current["state"] = state
            current["timestamp"] = _timestamp(row.get("timestamp")) or current["timestamp"]
    return decisions


def _activity(decisions: dict[str, dict[str, Any]], language: str) -> list[dict[str, str | None]]:
    latest = sorted(decisions.values(), key=lambda item: item["timestamp"] or "", reverse=True)[:8]
    return [
        {
            "timestamp": item["timestamp"],
            "decision": _CANDIDATE_LABELS[language][item["candidate"]],
            "state": _STATE_LABELS[language][item["state"]],
        }
        for item in latest
    ]


def snapshot(*, heartbeat_running: bool, input_ready: bool, ledger_rows: list[dict[str, Any]] | None = None, language: str = "en") -> dict[str, Any]:
    """Create the public monitor response from trusted, fixed fields only."""
    language = language if language in {"en", "ja"} else "en"
    decisions = _latest_decisions(ledger_rows if ledger_rows is not None else rows())
    values = list(decisions.values())
    states = Counter(item["state"] for item in values)
    approved = sum(item["gate"] == "approved_for_dry_run" for item in values)
    blocked = sum(item["gate"] == "rejected" for item in values)
    latest = _activity(decisions, language)
    latest_item = latest[0] if latest else None
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "language": language,
        "service": {
            "heartbeat": "running" if heartbeat_running else "stopped",
            "market_data": _SERVICE_LABELS[language]["live" if input_ready else "unavailable"],
        },
        "performance": {
            "decisions_recorded": len(values),
            "gate_passed": approved,
            "gate_blocked": blocked,
            "autonomous_submissions": sum(
                item["mode"] == "autonomous-paper" and item["state"] in {"accepted", "partially_filled", "filled"}
                for item in values
            ),
            "policy_stops": states["rejected"] + states["submission_failed"],
        },
        "latest": latest_item,
        "activity": latest,
    }
