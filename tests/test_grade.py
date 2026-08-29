"""Tests for the self-grading log."""
from __future__ import annotations

import tempfile
import unittest
from datetime import date
from pathlib import Path

from runtime.grade import grade_entry, grade_ledger
from runtime.ledger import TradeLedger


def _condor_entry(**over):
    e = {
        "id": 1, "ts": "2026-08-01T10:00:00", "index_symbol": "SPY",
        "credit": 5000.0, "hedge_cost": 0.0,
        "orders": [{"structure": "iron_condor", "symbol": "SPY", "contracts": 1,
                    "expiry_days": 7, "legs": [
                        {"right": "P", "strike": 520, "action": "sell"},
                        {"right": "P", "strike": 505, "action": "buy"},
                        {"right": "C", "strike": 540, "action": "sell"},
                        {"right": "C", "strike": 555, "action": "buy"}]}],
    }
    e.update(over)
    return e


class TestGradeEntry(unittest.TestCase):
    def test_condor_kept_full_credit(self):
        # expiry price inside the short strikes -> all legs worthless -> keep the credit
        g = grade_entry(_condor_entry(), lambda s: 530.0)
        self.assertAlmostEqual(g["realized_pnl"], 5000.0)
        self.assertIn("harvest", g["verdict"].lower())

    def test_condor_breached_loses(self):
        # blow through the put side (500 < long strike 505) -> capped loss
        g = grade_entry(_condor_entry(), lambda s: 500.0)
        # put spread max loss = (520-505)*100 = 1500 intrinsic; realized = 5000 - 1500
        self.assertAlmostEqual(g["realized_pnl"], 3500.0)

    def test_hedge_paid_off(self):
        e = {"id": 2, "ts": "2026-08-01T10:00:00", "index_symbol": "SPY",
             "credit": 0.0, "hedge_cost": 1000.0,
             "orders": [{"structure": "protective_put", "symbol": "SPY", "contracts": 5,
                         "expiry_days": 14, "legs": [{"right": "P", "strike": 520, "action": "buy"}]}]}
        g = grade_entry(e, lambda s: 480.0)  # 40 below strike -> 40*100*5 = 20000 payoff
        self.assertAlmostEqual(g["realized_pnl"], 20000.0 - 1000.0)
        self.assertIn("hedge paid off", g["verdict"])

    def test_hedge_expired_worthless_is_discipline(self):
        e = {"id": 3, "ts": "2026-08-01T10:00:00", "index_symbol": "SPY",
             "credit": 0.0, "hedge_cost": 1000.0,
             "orders": [{"structure": "protective_put", "symbol": "SPY", "contracts": 5,
                         "expiry_days": 14, "legs": [{"right": "P", "strike": 520, "action": "buy"}]}]}
        g = grade_entry(e, lambda s: 560.0)  # above strike -> worthless
        self.assertAlmostEqual(g["realized_pnl"], -1000.0)
        self.assertIn("discipline", g["verdict"])


class TestGradeLedger(unittest.TestCase):
    def _ledger(self):
        return TradeLedger(Path(tempfile.mkdtemp(prefix="grade_")) / "l.jsonl")

    def test_only_grades_expired(self):
        led = self._ledger()
        led.append(_condor_entry(ts="2026-08-01T10:00:00"))  # expires 2026-08-08
        # "now" before expiry -> nothing graded
        self.assertEqual(grade_ledger(led, lambda s: 530.0, now_date=date(2026, 8, 5)), 0)
        self.assertIsNone(led.entries()[0].get("grade"))
        # "now" after expiry -> graded once
        self.assertEqual(grade_ledger(led, lambda s: 530.0, now_date=date(2026, 8, 10)), 1)
        self.assertIsNotNone(led.entries()[0]["grade"])
        # idempotent: already graded, not re-graded
        self.assertEqual(grade_ledger(led, lambda s: 530.0, now_date=date(2026, 8, 10)), 0)

    def test_sit_cycle_grades_flat(self):
        led = self._ledger()
        led.append({"id": 1, "ts": "2026-08-01T10:00:00", "orders": [], "credit": 0.0})
        self.assertEqual(grade_ledger(led, lambda s: 1.0, now_date=date(2026, 8, 10)), 1)
        self.assertEqual(led.entries()[0]["grade"]["status"], "flat")

    def test_summary_includes_grades(self):
        led = self._ledger()
        led.append(_condor_entry(ts="2026-08-01T10:00:00"))
        grade_ledger(led, lambda s: 530.0, now_date=date(2026, 8, 10))
        s = led.summary()
        self.assertEqual(s["graded_cycles"], 1)
        self.assertGreater(s["realized_pnl"], 0)
        self.assertEqual(s["win_rate"], 1.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
