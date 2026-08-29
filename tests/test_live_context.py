"""Live candidate context: builds from a data source and survives context->submit.

Uses the offline MockDataSource (no creds, no network). The key assertion is that a
`context` then `submit` round-trip validates — i.e. persisting/rebuilding the inputs
defeats the live stale-context_id problem the fixtures never hit.
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from agent.contracts import AgentDecision, DecisionContext
from agent.gate import validate_decision


class LiveContextTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name)
        os.environ["AGENT_STATE_PATH"] = str(base / "state.json")
        os.environ["AGENT_CONTEXT_PATH"] = str(base / "contexts.jsonl")
        # Ensure the mock source path (no Alpaca creds present).
        self._saved_creds = {k: os.environ.pop(k, None) for k in ("ALPACA_API_KEY", "ALPACA_SECRET_KEY")}

    def tearDown(self) -> None:
        for k in ("AGENT_STATE_PATH", "AGENT_CONTEXT_PATH"):
            os.environ.pop(k, None)
        for k, v in self._saved_creds.items():
            if v is not None:
                os.environ[k] = v
        self._tmp.cleanup()

    def test_builds_valid_context(self) -> None:
        from feed import MockDataSource, StateStore
        from agent.live_context import build_live_context

        state = StateStore(os.environ["AGENT_STATE_PATH"])
        ctx = build_live_context(source=MockDataSource(), state=state)
        self.assertIsInstance(ctx, DecisionContext)
        self.assertEqual(ctx.scenario_id, "live")
        self.assertTrue(ctx.candidates)

    def test_context_then_submit_round_trip_validates(self) -> None:
        from feed import MockDataSource, StateStore
        from agent.live_context import build_live_context, rebuild_live_context

        state = StateStore(os.environ["AGENT_STATE_PATH"])
        built = build_live_context(source=MockDataSource(), state=state)

        # submit rebuilds from persisted inputs — must be the SAME context.
        rebuilt = rebuild_live_context(built.context_id)
        self.assertIsNotNone(rebuilt)
        self.assertEqual(rebuilt.context_id, built.context_id)

        decision = AgentDecision(built.context_id, built.candidates[0].candidate_id, "test reason")
        result = validate_decision(rebuilt, decision)
        self.assertTrue(result.approved, msg=str(result.reasons))

    def test_unknown_context_id_returns_none(self) -> None:
        from agent.live_context import rebuild_live_context

        self.assertIsNone(rebuild_live_context("does-not-exist"))

    def test_option_positions_excluded_and_income_detected(self) -> None:
        from feed.core import assemble_portfolio, is_option_symbol
        from agent.live_context import has_open_income

        raw = [("SPY", 100, 560.0), ("SPY240920P00520000", -2, 4.0), ("AAPL", 50, 300.0)]
        pf = assemble_portfolio(raw, cash=1000.0, peak_equity=1000.0)
        self.assertEqual([p.symbol for p in pf.positions], ["SPY", "AAPL"])  # option leg dropped
        self.assertTrue(is_option_symbol("SPY240920P00520000"))
        self.assertFalse(is_option_symbol("SPY"))
        self.assertTrue(has_open_income(raw))                          # short option leg present
        self.assertFalse(has_open_income([("SPY", 100, 560.0)]))       # equity only

    def test_count_hedge_contracts(self) -> None:
        from agent.live_context import count_hedge_contracts

        positions = [
            ("SPY", 300, 560.0),              # equity, not a hedge
            ("SPY240920P00520000", 3, 4.10),  # long SPY put -> counts
            ("SPY240920P00500000", 2, 2.05),  # another long SPY put -> counts
            ("SPY240920C00600000", 5, 1.20),  # call, ignored
            ("SPY240920P00540000", -1, 6.0),  # short put, ignored
            ("QQQ240920P00450000", 4, 3.0),   # wrong underlying, ignored
        ]
        self.assertEqual(count_hedge_contracts(positions, "SPY"), 5)
        self.assertEqual(count_hedge_contracts([("SPY", 300, 560.0)], "SPY"), 0)


if __name__ == "__main__":
    unittest.main()
