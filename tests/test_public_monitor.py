from __future__ import annotations

import unittest

from deploy.public_monitor import snapshot


class PublicMonitorTests(unittest.TestCase):
    def test_projection_is_aggregate_only_and_ignores_sensitive_ledger_fields(self) -> None:
        ledger = [
            {
                "event": "proposal", "decision_id": "safe-1", "timestamp": "2026-08-31T00:00:00+00:00",
                "decision": {"candidate_id": "full_hedge", "reason": "private model chain", "context_id": "private-context"},
                "gate": {"status": "approved_for_dry_run", "orders": [{"symbol": "SPY", "qty": 99, "client_order_id": "private-order"}]},
                "execution": {"mode": "autonomous-paper", "state": "proposed", "approved_by": "private-operator"},
            },
            {
                "event": "broker_update", "decision_id": "safe-1", "timestamp": "2026-08-31T00:01:00+00:00",
                "broker": {"id": "private-broker-id"}, "execution": {"mode": "autonomous-paper", "state": "accepted"},
            },
        ]
        result = snapshot(heartbeat_running=True, input_ready=True, ledger_rows=ledger)
        rendered = str(result)
        self.assertEqual(result["performance"], {"decisions_recorded": 1, "gate_passed": 1, "gate_blocked": 0, "autonomous_submissions": 1, "policy_stops": 0})
        self.assertEqual(result["latest"], {"timestamp": "2026-08-31T00:01:00+00:00", "decision": "保護ヘッジを評価", "state": "paper 注文を受付"})
        for secret in ("private model chain", "private-context", "private-order", "private-operator", "private-broker-id", "99"):
            self.assertNotIn(secret, rendered)

    def test_invalid_or_unknown_candidate_is_not_published(self) -> None:
        result = snapshot(heartbeat_running=False, input_ready=False, ledger_rows=[
            {"event": "proposal", "decision_id": "bad", "decision": {"candidate_id": "<script>"}, "gate": {"status": "approved_for_dry_run"}, "execution": {"state": "proposed"}},
        ])
        self.assertEqual(result["performance"]["decisions_recorded"], 0)
        self.assertIsNone(result["latest"])
