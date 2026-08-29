"""Tests for the historical backtest (README §8)."""
from __future__ import annotations

import json
import unittest

from backtest import run_backtest, synthetic_series
from backtest.engine import _income_leg_expiry_pnl, _max_drawdown


class _Leg:  # minimal stand-in for an IncomeLeg
    def __init__(self, **kw):
        self.__dict__.update(kw)


class TestBacktest(unittest.TestCase):
    def test_runs_and_beats_on_drawdown(self):
        r = run_backtest(synthetic_series(), start_equity=100_000)
        self.assertGreater(r.cycles, 5)
        self.assertGreater(r.income_collected, 0)
        self.assertGreater(r.hedged_cycles, 0)  # hedge must step in during the crash
        s, u = r.strategy_metrics(), r.unhedged_metrics()
        # the whole point: the hedge controls drawdown vs buy-and-hold
        self.assertLess(s["max_drawdown"], u["max_drawdown"])
        self.assertEqual(len(r.strategy_curve), len(r.unhedged_curve))

    def test_result_json_serializable(self):
        r = run_backtest(synthetic_series())
        json.dumps(r.to_dict())

    def test_condor_expiry_pnl(self):
        leg = _Leg(kind="iron_condor", contracts=1, credit=500.0,
                   short_strike=520.0, long_strike=505.0,
                   call_short_strike=540.0, call_long_strike=555.0)
        # inside the short strikes at expiry -> keep the full credit
        self.assertAlmostEqual(_income_leg_expiry_pnl(leg, 530.0), 500.0)
        # blown through the put side -> capped loss = width*100 - credit
        self.assertAlmostEqual(_income_leg_expiry_pnl(leg, 500.0), 500.0 - 15 * 100)

    def test_covered_call_expiry_pnl(self):
        leg = _Leg(kind="covered_call", contracts=1, credit=300.0, short_strike=560.0)
        self.assertAlmostEqual(_income_leg_expiry_pnl(leg, 550.0), 300.0)          # kept
        self.assertAlmostEqual(_income_leg_expiry_pnl(leg, 570.0), 300.0 - 10 * 100)  # capped

    def test_max_drawdown(self):
        self.assertAlmostEqual(_max_drawdown([100, 120, 60, 90]), 0.5)  # 120 -> 60


if __name__ == "__main__":
    unittest.main(verbosity=2)
