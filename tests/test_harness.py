"""Tests for the harness skeleton — the agent loop wiring (dependency-free runner).

Covers the node routing and a full cycle with the offline MockDataSource + stubs, so
the loop is verified without an LLM or Alpaca. The DECIDE node is the `default_decider`
stub (real judgment lives in the DSH harness now).
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from feed import MockDataSource, StateStore
from harness import (
    BrokerExecutor,
    default_decider,
    plan_to_orders,
    run_cycle,
)
from harness import nodes
from harness.orders import OrderIntent


class TestHarness(unittest.TestCase):
    def _state_store(self):
        return StateStore(Path(tempfile.mkdtemp(prefix="harn_")) / "s.json")

    def test_full_cycle_dry_run(self):
        state = run_cycle(MockDataSource(), self._state_store())
        for key in ("context", "decision", "log"):
            self.assertIn(key, state)
        self.assertTrue(state["decision"]["approved"])
        # MockDataSource is a harvest cycle -> should propose orders (dry run)
        self.assertIn("execution", state)
        self.assertTrue(state["execution"]["dry_run"])
        self.assertGreater(len(state["execution"]["orders"]), 0)
        self.assertIsInstance(state["log"], str)

    def test_route_skips_execute_when_nothing_to_do(self):
        # a SIT context: no income legs, hedge hold -> route to 'log'
        ctx = {"plan": {"income": {"legs": []}, "hedge": {"action": "hold"}}}
        decision = default_decider(
            {"plan": {"posture": "SIT", "income": {"legs": []},
                      "hedge": {"action": "hold"}}}
        )
        self.assertEqual(nodes.route_after_decide(decision, ctx), "log")

    def test_route_executes_when_income_present(self):
        ctx = {"plan": {"income": {"legs": [{"kind": "iron_condor"}]},
                        "hedge": {"action": "hold"}}}
        self.assertEqual(nodes.route_after_decide({"approved": True}, ctx), "execute")

    def test_daily_halt_flows_through_loop(self):
        # a big down day -> validate halts income -> loop still completes cleanly
        state = run_cycle(MockDataSource(), self._state_store(), day_pnl_pct=-0.07)
        self.assertFalse(state["context"]["plan"]["income"]["legs"])
        self.assertFalse(state["context"]["validation"]["ok"])
        self.assertIn("log", state)


class TestOrders(unittest.TestCase):
    def test_iron_condor_to_four_legs(self):
        leg = {
            "kind": "iron_condor", "symbol": "SPY", "contracts": 8, "expiry_days": 7,
            "short_strike": 523.0, "long_strike": 507.0,
            "call_short_strike": 540.0, "call_long_strike": 556.0, "note": "",
        }
        [intent] = plan_to_orders([leg], {"action": "hold"})
        self.assertEqual(intent.structure, "iron_condor")
        self.assertEqual(intent.net_side, "credit")
        self.assertEqual(len(intent.legs), 4)
        # sell the inner strikes, buy the protective wings
        kinds = {(l.right, l.action) for l in intent.legs}
        self.assertIn(("P", "sell"), kinds)
        self.assertIn(("P", "buy"), kinds)
        self.assertIn(("C", "sell"), kinds)
        self.assertIn(("C", "buy"), kinds)

    def test_covered_call_and_hedge(self):
        cc = {"kind": "covered_call", "symbol": "SPY", "contracts": 1, "expiry_days": 7,
              "short_strike": 566.0, "long_strike": None, "note": ""}
        hedge = {"action": "increase", "contracts_delta": 5, "put_strike": 510.0,
                 "put_expiry_days": 14}
        intents = plan_to_orders([cc], hedge, index_symbol="SPY")
        cc_i = next(i for i in intents if i.structure == "covered_call")
        self.assertEqual(cc_i.legs[0].right, "C")
        self.assertEqual(cc_i.legs[0].action, "sell")
        put_i = next(i for i in intents if i.structure == "protective_put")
        self.assertEqual(put_i.net_side, "debit")
        self.assertEqual(put_i.legs[0].action, "buy")   # +delta -> buy protection
        self.assertEqual(put_i.contracts, 5)

    def test_broker_executor_submits_each_intent(self):
        class FakeBroker:
            dry_run = False
            def __init__(self): self.seen = []
            def submit(self, intent: OrderIntent) -> dict:
                self.seen.append(intent.structure)
                return {"status": "ok", "structure": intent.structure}

        decision = default_decider({
            "plan": {
                "posture": "HARVEST",
                "income": {"legs": [
                    {"kind": "covered_call", "symbol": "SPY", "contracts": 1,
                     "expiry_days": 7, "short_strike": 566.0, "long_strike": None, "note": ""},
                ]},
                "hedge": {"action": "hold", "contracts_delta": 0},
            }
        })
        broker = FakeBroker()
        out = BrokerExecutor(broker)(decision, {"index_symbol": "SPY"})
        self.assertEqual(broker.seen, ["covered_call"])
        self.assertFalse(out["dry_run"])
        self.assertEqual(len(out["results"]), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
