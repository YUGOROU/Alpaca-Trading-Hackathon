from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from agent import AgentDecision, build_decision_context, validate_decision
from agent.ledger import (
    execution_state,
    record_broker_update,
    record_dry_run,
    record_human_approval,
    record_submission_requested,
)
from agent.scenarios import get_scenario


def context(name: str):
    portfolio, market = get_scenario(name)
    return build_decision_context(portfolio, market, scenario_id=name)


class DecisionPipelineTests(unittest.TestCase):
    def test_fixed_regimes_expose_bounded_distinct_choices(self):
        # calm now harvests too: premium is rich vs realized (positive VRP), regime calm.
        self.assertEqual(
            [c.candidate_id for c in context("calm").candidates],
            ["hold", "harvest_income"],
        )
        self.assertEqual(
            [c.candidate_id for c in context("elevated").candidates],
            ["hold", "harvest_income"],
        )
        # short-DTE (5d) protection is cheap enough to stay under the cost cap -> full_hedge.
        self.assertEqual(
            [c.candidate_id for c in context("stressed").candidates],
            ["partial_hedge", "full_hedge"],
        )

    def test_income_open_suppresses_harvest(self):
        # an overlay already on -> no fresh harvest_income choice (a loop must not stack)
        portfolio, market = get_scenario("calm")
        ctx = build_decision_context(portfolio, market, scenario_id="calm", income_open=True)
        self.assertEqual([c.candidate_id for c in ctx.candidates], ["hold"])

    def test_model_view_has_tradeoffs_but_not_exact_order_sizes(self):
        visible = context("stressed").to_model_dict()
        encoded = json.dumps(visible)
        self.assertIn("linear_hedge_adjusted_pnl_5pct", encoded)
        self.assertNotIn("contracts_target", encoded)
        self.assertNotIn("orders", encoded)

    def test_gate_rejects_invented_candidate_and_stale_context(self):
        ctx = context("elevated")
        result = validate_decision(ctx, AgentDecision("wrong", "buy_random_stock", "Trust me"))
        self.assertFalse(result.approved)
        self.assertEqual(result.orders, ())
        self.assertIn("stale_or_unknown_context", result.reasons)
        self.assertIn("candidate_not_admissible", result.reasons)

    def test_gate_sizes_orders_only_after_candidate_selection(self):
        ctx = context("stressed")
        result = validate_decision(
            ctx,
            AgentDecision(ctx.context_id, "full_hedge", "Risk is high; prioritize drawdown control."),
        )
        self.assertTrue(result.approved)
        self.assertTrue(result.human_approval_required)
        self.assertEqual(len(result.orders), 1)
        self.assertEqual(result.orders[0]["mode"], "paper_dry_run")
        self.assertGreater(result.orders[0]["contracts"], 0)
        selected = next(c for c in ctx.candidates if c.candidate_id == "full_hedge")
        self.assertLessEqual(selected.plan.hedge.hedge_cost_drag, 0.05)

    def test_dry_run_ledger_is_idempotent_by_decision_id(self):
        ctx = context("elevated")
        decision = AgentDecision(
            ctx.context_id,
            "harvest_income",
            "IV is rich and the regime remains calm; keep the rationale UTF-8 safe: café.",
        )
        result = validate_decision(ctx, decision)
        self.assertTrue(result.approved)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "decisions.jsonl"
            first = record_dry_run(path, "decision-1", "elevated", decision, result)
            second = record_dry_run(path, "decision-1", "elevated", decision, result)
            self.assertEqual(first, second)
            ledger_text = path.read_text(encoding="utf-8")
            self.assertEqual(len(ledger_text.splitlines()), 1)
            self.assertIn("café", ledger_text)
            self.assertEqual(first["schema_version"], 2)
            self.assertEqual(first["execution"], {
                "mode": "human", "state": "proposed", "approval_source": None,
            })

            conflicting = AgentDecision(ctx.context_id, "hold", "Different decision.")
            conflicting_result = validate_decision(ctx, conflicting)
            with self.assertRaisesRegex(ValueError, "different content"):
                record_dry_run(path, "decision-1", "elevated", conflicting, conflicting_result)

    def test_ledger_rejects_unknown_execution_mode(self):
        ctx = context("elevated")
        decision = AgentDecision(ctx.context_id, "hold", "Remain in the current posture.")
        result = validate_decision(ctx, decision)
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "unknown execution mode"):
                record_dry_run(Path(tmp) / "decisions.jsonl", "decision-1", "elevated", decision, result,
                               execution_mode="surprise")

    def test_ledger_rejects_execution_mode_change_for_same_decision(self):
        ctx = context("elevated")
        decision = AgentDecision(ctx.context_id, "hold", "Remain in the current posture.")
        result = validate_decision(ctx, decision)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "decisions.jsonl"
            record_dry_run(path, "decision-1", "elevated", decision, result)
            with self.assertRaisesRegex(ValueError, "different content"):
                record_dry_run(path, "decision-1", "elevated", decision, result,
                               execution_mode="autonomous-paper")

    def test_human_approval_is_an_idempotent_lifecycle_transition(self):
        ctx = context("elevated")
        decision = AgentDecision(ctx.context_id, "hold", "Remain in the current posture.")
        result = validate_decision(ctx, decision)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "decisions.jsonl"
            record_dry_run(path, "decision-1", "elevated", decision, result)
            approval = record_human_approval(path, "decision-1", approved_by="operator-1")
            self.assertEqual(approval["event"], "approval")
            self.assertEqual(approval["execution"]["state"], "approved")
            self.assertEqual(record_human_approval(path, "decision-1", approved_by="operator-1"), approval)
            self.assertEqual(execution_state(path, "decision-1")["approval_source"], "human")
            with self.assertRaisesRegex(ValueError, "different approval"):
                record_human_approval(path, "decision-1", approved_by="operator-2")

    def test_human_approval_rejects_autonomous_or_rejected_proposals(self):
        ctx = context("elevated")
        decision = AgentDecision(ctx.context_id, "hold", "Remain in the current posture.")
        approved = validate_decision(ctx, decision)
        rejected = validate_decision(ctx, AgentDecision("wrong", "hold", "stale"))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "decisions.jsonl"
            record_dry_run(path, "autonomous", "elevated", decision, approved,
                           execution_mode="autonomous-paper")
            record_dry_run(path, "rejected", "elevated", decision, rejected)
            with self.assertRaisesRegex(ValueError, "only valid in human"):
                record_human_approval(path, "autonomous", approved_by="operator-1")
            with self.assertRaisesRegex(ValueError, "only approved proposals"):
                record_human_approval(path, "rejected", approved_by="operator-1")

    def test_submission_lifecycle_requires_approval_and_matching_revalidation(self):
        ctx = context("elevated")
        decision = AgentDecision(ctx.context_id, "hold", "Remain in the current posture.")
        result = validate_decision(ctx, decision)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "decisions.jsonl"
            record_dry_run(path, "decision-1", "elevated", decision, result)
            valid = {"ok": True, "context_id": ctx.context_id, "equity": 100.0, "open_order_ids": []}
            with self.assertRaisesRegex(ValueError, "approved decision"):
                record_submission_requested(path, "decision-1", revalidation=valid)
            record_human_approval(path, "decision-1", approved_by="operator-1")
            requested = record_submission_requested(path, "decision-1", revalidation=valid)
            self.assertEqual(requested["execution"]["state"], "submission_requested")
            accepted = record_broker_update(
                path, "decision-1", state="accepted", broker_orders=[{"alpaca_order_id": "broker-1"}],
            )
            self.assertEqual(accepted["broker_orders"][0]["alpaca_order_id"], "broker-1")
            self.assertEqual(execution_state(path, "decision-1")["state"], "accepted")


if __name__ == "__main__":
    unittest.main()
