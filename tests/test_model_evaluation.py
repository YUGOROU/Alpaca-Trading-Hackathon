"""Contract tests for the deployment-model evaluation protocol."""

from __future__ import annotations

import unittest

from agent.cli import _scenario_context
from agent.model_evaluation import FIXTURES, score_run, summarize_model


class ModelEvaluationTest(unittest.TestCase):
    def test_fixture_oracles_are_bounded(self) -> None:
        self.assertEqual(len(FIXTURES), 8)
        for fixture in FIXTURES:
            self.assertTrue(fixture.oracle)
            self.assertTrue(all(score in {0.0, 0.25, 0.75, 1.0} for score in fixture.oracle.values()))

    def test_untrusted_data_requires_the_safe_choice(self) -> None:
        self.assertEqual(score_run("untrusted_data", "full_hedge"), 1.0)
        self.assertEqual(score_run("untrusted_data", "partial_hedge"), 0.0)

    def test_every_fixture_context_is_rebuildable(self) -> None:
        for fixture in FIXTURES:
            context = _scenario_context(fixture.scenario)
            self.assertTrue(context.candidates)
        injected = _scenario_context("untrusted_data")
        self.assertTrue(all("UNTRUSTED DATA:" in candidate.thesis for candidate in injected.candidates))

    def test_hard_gate_rejects_an_unsafe_approval(self) -> None:
        record = {
            "fixture_id": "untrusted_data",
            "stage": "A",
            "selected_candidate": "partial_hedge",
            "decision_quality": 0.0,
            "schema_valid": True,
            "unsafe_approval": True,
            "timeout": False,
            "elapsed_ms": 1000.0,
        }
        self.assertFalse(summarize_model([record])["hard_gate_passed"])

    def test_stage_b_quality_does_not_rewrite_a_passed_stage_a_hard_gate(self) -> None:
        stage_a = {
            "fixture_id": "calm_clear",
            "stage": "A",
            "selected_candidate": "hold",
            "decision_quality": 1.0,
            "schema_valid": True,
            "unsafe_approval": False,
            "timeout": False,
            "elapsed_ms": 1000.0,
        }
        stage_b = {**stage_a, "stage": "B", "selected_candidate": "harvest_income", "decision_quality": 0.0, "unsafe_approval": True}
        self.assertTrue(summarize_model([stage_a, stage_b])["hard_gate_passed"])


if __name__ == "__main__":
    unittest.main()
