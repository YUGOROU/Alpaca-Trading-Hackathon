"""Autonomous candidate execution-shape regression tests."""

from __future__ import annotations

import unittest

from agent import AgentDecision, build_decision_context, validate_decision
from agent.scenarios import get_scenario


class AutonomousCandidateTests(unittest.TestCase):
    def test_autonomous_omits_a_zero_delta_partial_hedge_but_human_keeps_it(self):
        portfolio, market = get_scenario("stressed")
        initial = build_decision_context(
            portfolio, market, scenario_id="stressed", execution_mode="autonomous-paper",
        )
        partial = next(item for item in initial.candidates if item.candidate_id == "partial_hedge")

        autonomous = build_decision_context(
            portfolio, market, scenario_id="stressed", execution_mode="autonomous-paper",
            current_contracts=partial.plan.hedge.contracts_target,
        )
        self.assertNotIn("partial_hedge", [item.candidate_id for item in autonomous.candidates])

        human_initial = build_decision_context(portfolio, market, scenario_id="stressed")
        human_target = next(
            item for item in human_initial.candidates if item.candidate_id == "partial_hedge"
        ).plan.hedge.contracts_target
        human = build_decision_context(
            portfolio, market, scenario_id="stressed", current_contracts=human_target,
        )
        human_partial = next(item for item in human.candidates if item.candidate_id == "partial_hedge")
        self.assertEqual(human_partial.plan.hedge.contracts_delta, 0)

    def test_autonomous_income_candidate_preserves_existing_hedge_as_one_order(self):
        portfolio, market = get_scenario("calm")
        autonomous = build_decision_context(
            portfolio, market, scenario_id="calm", execution_mode="autonomous-paper",
            current_contracts=2,
        )
        income = next(item for item in autonomous.candidates if item.candidate_id == "harvest_income")
        self.assertEqual(income.plan.hedge.contracts_delta, 0)

        result = validate_decision(
            autonomous,
            AgentDecision(autonomous.context_id, income.candidate_id, "One bounded income order."),
        )
        self.assertTrue(result.approved)
        self.assertEqual(len(result.orders), 1)
        self.assertNotEqual(result.orders[0]["intent"], "sell_to_close")

        # This is the executor-facing shape invariant for the complete model
        # choice set, not merely the preferred income candidate.
        for candidate in autonomous.candidates:
            shaped = validate_decision(
                autonomous,
                AgentDecision(autonomous.context_id, candidate.candidate_id, "Shape check."),
            )
            self.assertLessEqual(len(shaped.orders), 1, candidate.candidate_id)


if __name__ == "__main__":
    unittest.main()
