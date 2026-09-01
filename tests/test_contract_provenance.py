from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agent.contract_provenance import provenance_path, record_protective_put_open, recorded_protective_puts


class ContractProvenanceTests(unittest.TestCase):
    def test_records_only_exact_protective_put_opens_idempotently(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "decisions.jsonl"
            row = record_protective_put_open(
                ledger, decision_id="d-1", contract="SPY260918P00500000", quantity=2, broker_order_id="broker-1"
            )
            self.assertEqual(row["leg_role"], "hedge_long_put")
            self.assertEqual(record_protective_put_open(
                ledger, decision_id="d-1", contract="SPY260918P00500000", quantity=2, broker_order_id="broker-1"
            ), row)
            self.assertEqual(recorded_protective_puts(ledger), [row])
            self.assertTrue(provenance_path(ledger).exists())

    def test_rejects_non_spy_or_invalid_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "decisions.jsonl"
            with self.assertRaisesRegex(ValueError, "SPY OCC put"):
                record_protective_put_open(ledger, decision_id="d", contract="SPYG260918P00500000", quantity=1, broker_order_id="b")
            with self.assertRaisesRegex(ValueError, "positive"):
                record_protective_put_open(ledger, decision_id="d", contract="SPY260918P00500000", quantity=0, broker_order_id="b")
