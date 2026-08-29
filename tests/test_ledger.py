"""Tests for the persistent trade ledger."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from feed import MockDataSource, StateStore
from harness import run_cycle
from runtime.ledger import TradeLedger


def _state():
    tmp = Path(tempfile.mkdtemp(prefix="led_ss_")) / "s.json"
    return run_cycle(MockDataSource(), StateStore(tmp))


class TestLedger(unittest.TestCase):
    def _ledger(self):
        return TradeLedger(Path(tempfile.mkdtemp(prefix="led_")) / "ledger.jsonl")

    def test_record_and_read(self):
        led = self._ledger()
        e = led.record_cycle(_state(), mode="MOCK", ts="2026-08-24T10:00:00")
        self.assertEqual(e["id"], 1)
        self.assertEqual(e["mode"], "MOCK")
        self.assertGreater(e["credit"], 0)          # harvest cycle collected premium
        self.assertGreater(len(e["orders"]), 0)
        rows = led.entries()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["ts"], "2026-08-24T10:00:00")

    def test_ids_increment_and_persist(self):
        path = Path(tempfile.mkdtemp(prefix="led_")) / "l.jsonl"
        TradeLedger(path).record_cycle(_state(), ts="2026-08-24T10:00:00")
        TradeLedger(path).record_cycle(_state(), ts="2026-08-24T10:05:00")  # new instance
        rows = TradeLedger(path).entries(newest_first=False)  # reload from disk
        self.assertEqual([r["id"] for r in rows], [1, 2])

    def test_newest_first_and_limit(self):
        led = self._ledger()
        for i in range(3):
            led.record_cycle(_state(), ts=f"2026-08-24T10:0{i}:00")
        rows = led.entries(limit=2)  # newest-first
        self.assertEqual([r["id"] for r in rows], [3, 2])

    def test_summary(self):
        led = self._ledger()
        led.record_cycle(_state(), ts="2026-08-24T10:00:00")
        led.record_cycle(_state(), ts="2026-08-24T10:05:00")
        s = led.summary()
        self.assertEqual(s["cycles"], 2)
        self.assertGreater(s["total_credit"], 0)
        self.assertGreater(s["orders_placed"], 0)
        self.assertIn("HARVEST", s["postures"])

    def test_empty_summary(self):
        s = self._ledger().summary()
        self.assertEqual(s["cycles"], 0)
        self.assertEqual(s["total_credit"], 0.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
