from __future__ import annotations

import json
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent import AgentDecision, build_decision_context, validate_decision
from agent.ledger import (
    execution_state,
    record_broker_update,
    record_autonomous_authorization,
    record_dry_run,
    record_human_approval,
    record_human_rejection,
    record_submission_unknown,
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

    def test_fixture_autonomous_context_rebuilds_with_the_same_id(self):
        from agent.cli import _scenario_context
        autonomous = _scenario_context("stressed", execution_mode="autonomous-paper")
        self.assertEqual(
            autonomous.context_id,
            _scenario_context("stressed", execution_mode="autonomous-paper").context_id,
        )
        self.assertNotEqual(autonomous.context_id, _scenario_context("stressed").context_id)

    def test_submit_cannot_relabel_a_human_context_as_autonomous(self):
        from agent.cli import main
        import sys

        human = context("stressed")
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "decisions.jsonl"
            saved = sys.argv
            output = StringIO()
            try:
                sys.argv = [
                    "agent.cli", "submit", "--mock", "--context-id", human.context_id,
                    "--candidate-id", "full_hedge", "--reason", "Attempt mode switch.",
                    "--decision-id", "mode-mismatch", "--ledger", str(ledger),
                    "--execution-mode", "autonomous-paper",
                ]
                with patch("agent.cli.rebuild_observed_context", return_value=human), redirect_stdout(output):
                    self.assertEqual(main(), 2)
            finally:
                sys.argv = saved
            row = json.loads(output.getvalue())
            self.assertEqual(row["execution"]["mode"], "human")
            self.assertEqual(row["execution"]["state"], "rejected")
            self.assertIn("execution_mode_mismatch", row["gate"]["reasons"])

    def test_autonomous_income_candidate_is_one_spy_condor_order(self):
        portfolio, market = get_scenario("calm")
        autonomous = build_decision_context(
            portfolio, market, scenario_id="calm", execution_mode="autonomous-paper",
        )
        income = next(candidate for candidate in autonomous.candidates if candidate.candidate_id == "harvest_income")
        self.assertEqual(len(income.plan.income.legs), 1)
        self.assertEqual(income.plan.income.legs[0].kind, "iron_condor")
        self.assertEqual(income.plan.income.legs[0].symbol, "SPY")
        gate = validate_decision(
            autonomous,
            AgentDecision(autonomous.context_id, "harvest_income", "Single bounded SPY overlay."),
        )
        self.assertTrue(gate.approved)
        self.assertEqual(len(gate.orders), 1)
        self.assertEqual(gate.orders[0]["structure"], "iron_condor")

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

    def test_legacy_eventless_proposal_remains_idempotent(self):
        ctx = context("elevated")
        decision = AgentDecision(ctx.context_id, "hold", "Remain in the current posture.")
        result = validate_decision(ctx, decision)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "decisions.jsonl"
            row = record_dry_run(path, "decision-1", "elevated", decision, result)
            row.pop("event")
            path.write_text(json.dumps(row) + "\n")
            self.assertEqual(record_dry_run(path, "decision-1", "elevated", decision, result), row)

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

    def test_human_rejection_is_terminal(self):
        ctx = context("elevated")
        decision = AgentDecision(ctx.context_id, "hold", "Remain in the current posture.")
        result = validate_decision(ctx, decision)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "decisions.jsonl"
            record_dry_run(path, "decision-1", "elevated", decision, result)
            rejection = record_human_rejection(path, "decision-1", rejected_by="operator-1")
            self.assertEqual(rejection["execution"]["state"], "rejected")
            with self.assertRaisesRegex(ValueError, "different approval|proposed"):
                record_human_approval(path, "decision-1", approved_by="operator-1")

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

    def test_autonomous_submission_keeps_distinct_policy_provenance(self):
        ctx = context("stressed")
        decision = AgentDecision(ctx.context_id, "full_hedge", "Protect the fixed core book.")
        result = validate_decision(ctx, decision)
        self.assertTrue(result.approved)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "decisions.jsonl"
            record_dry_run(path, "auto-1", "stressed", decision, result, execution_mode="autonomous-paper")
            authorization = record_autonomous_authorization(path, "auto-1")
            self.assertEqual(authorization["execution"]["approval_source"], "autonomous_policy")
            requested = record_submission_requested(path, "auto-1", revalidation={
                "ok": True, "context_id": ctx.context_id, "equity": 100.0, "open_order_ids": [],
            })
            self.assertEqual(requested["execution"], {
                "mode": "autonomous-paper", "state": "submission_requested", "approval_source": "autonomous_policy",
            })
            accepted = record_broker_update(
                path, "auto-1", state="accepted", broker_orders=[{"alpaca_order_id": "broker-1"}],
            )
            self.assertEqual(accepted["execution"]["mode"], "autonomous-paper")

    def test_autonomous_options_policy_rejects_close_orders_before_submission(self):
        from agent.cli import main

        ctx = context("stressed")
        decision = AgentDecision(ctx.context_id, "full_hedge", "Protect the fixed core book.")
        result = validate_decision(ctx, decision)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "decisions.jsonl"
            record_dry_run(path, "auto-close", "stressed", decision, result, execution_mode="autonomous-paper")
            record_autonomous_authorization(path, "auto-close")
            row = json.loads(path.read_text().splitlines()[0])
            row["gate"]["orders"][0]["intent"] = "sell_to_close"
            path.write_text(json.dumps(row) + "\n")
            import sys
            saved = sys.argv
            try:
                sys.argv = ["agent.cli", "prepare-submission", "--ledger", str(path), "--decision-id", "auto-close", "--autonomous-options-overlay"]
                with self.assertRaisesRegex(ValueError, "bounded opening orders"):
                    main()
            finally:
                sys.argv = saved
            self.assertEqual(len(path.read_text().splitlines()), 1)

    def test_uncertain_submission_can_be_reconciled_by_a_later_broker_update(self):
        ctx = context("elevated")
        decision = AgentDecision(ctx.context_id, "hold", "Remain in the current posture.")
        result = validate_decision(ctx, decision)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "decisions.jsonl"
            record_dry_run(path, "decision-1", "elevated", decision, result)
            record_human_approval(path, "decision-1", approved_by="operator-1")
            record_submission_requested(path, "decision-1", revalidation={
                "ok": True, "context_id": ctx.context_id, "equity": 100.0, "open_order_ids": [],
            })
            unknown = record_submission_unknown(
                path, "decision-1", client_order_ids=["dry-client-1"], reason="MCP response missing id",
            )
            self.assertEqual(unknown["execution"]["state"], "submission_unknown")
            accepted = record_broker_update(
                path, "decision-1", state="accepted", broker_orders=[{"alpaca_order_id": "broker-1"}],
            )
            self.assertEqual(accepted["execution"]["state"], "accepted")

    def test_resting_alpaca_statuses_normalize_to_accepted(self):
        from feed.alpaca import AlpacaDataSource

        class Order:
            id = "broker-1"
            status = "pending_new"

        self.assertEqual(
            AlpacaDataSource._normalized_order_status(Order()),
            {"alpaca_order_id": "broker-1", "state": "accepted"},
        )


if __name__ == "__main__":
    unittest.main()
