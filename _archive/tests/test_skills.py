"""Tests for the skills loader + injecting skills into the LLM decider."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from harness import make_llm_decider
from runtime.skills import discover_skills, load_skills


def _skills_dir():
    root = Path(tempfile.mkdtemp(prefix="skills_"))
    (root / "alpaca-broker-trading-orders").mkdir()
    (root / "alpaca-broker-trading-orders" / "SKILL.md").write_text(
        "---\nname: trading-orders\n---\nPlace option orders via the Alpaca CLI. "
        "Use marketable-limit orders.", encoding="utf-8")
    (root / "alpaca-broker-market-data").mkdir()
    (root / "alpaca-broker-market-data" / "SKILL.md").write_text(
        "Fetch quotes with the market-data endpoint.", encoding="utf-8")
    return root


class TestSkills(unittest.TestCase):
    def test_discover_and_load(self):
        root = _skills_dir()
        found = discover_skills(root)
        self.assertEqual(set(found), {"alpaca-broker-trading-orders", "alpaca-broker-market-data"})
        text = load_skills(root)
        self.assertIn("marketable-limit", text)          # body kept
        self.assertNotIn("name: trading-orders", text)    # frontmatter stripped

    def test_load_missing_dir_is_empty(self):
        self.assertEqual(load_skills("/no/such/skills/dir"), "")

    def test_names_filter(self):
        root = _skills_dir()
        text = load_skills(root, names=["alpaca-broker-market-data"])
        self.assertIn("market-data endpoint", text)
        self.assertNotIn("marketable-limit", text)

    def test_skills_injected_into_decider_prompt(self):
        seen = {}
        def fake(system, user):
            seen["system"] = system
            return '{"action":"approve","reasoning":"ok"}'
        ctx = {"plan": {"posture": "HARVEST", "income": {"legs": [], "aggressiveness": 0,
               "total_credit": 0, "net_theta_per_day": 0, "total_max_loss": 0},
               "hedge": {"action": "hold", "contracts_target": 0}},
               "snapshot": {}, "validation": {"ok": True, "violations": []}}
        decider = make_llm_decider(fake, skills="SELL premium via marketable-limit orders.")
        decider(ctx)
        self.assertIn("Alpaca skills", seen["system"])
        self.assertIn("marketable-limit", seen["system"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
