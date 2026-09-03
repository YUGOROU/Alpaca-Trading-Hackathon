from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
import os
from pathlib import Path
import tempfile
import unittest


class HeartbeatScheduleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.saved = {key: os.environ.get(key) for key in (
            "AGENT_STATE_PATH", "AGENT_CONTEXT_PATH", "AGENT_HEARTBEAT_PATH",
        )}
        os.environ["AGENT_STATE_PATH"] = str(root / "state.json")
        os.environ["AGENT_CONTEXT_PATH"] = str(root / "contexts.jsonl")
        os.environ["AGENT_HEARTBEAT_PATH"] = str(root / "heartbeat.json")

    def tearDown(self) -> None:
        for key, value in self.saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self.temp.cleanup()

    def test_market_tick_uses_event_then_fifteen_minute_backstop(self) -> None:
        from agent.heartbeat import evaluate_tick
        from feed import MockDataSource, StateStore

        source = MockDataSource()
        state = StateStore(os.environ["AGENT_STATE_PATH"])
        now = datetime(2026, 9, 2, 15, 0, tzinfo=UTC)  # 11:00 EDT
        first = evaluate_tick(now=now, source=source, state=state)
        self.assertEqual(first["phase"], "market")
        self.assertTrue(first["llm_due"])
        self.assertIn("initial_snapshot", first["reasons"])
        self.assertIsNotNone(first["context"])

        quiet = evaluate_tick(now=now + timedelta(minutes=5), source=source, state=state)
        self.assertFalse(quiet["llm_due"])
        self.assertIsNone(quiet["context"])

        backstop = evaluate_tick(now=now + timedelta(minutes=16), source=source, state=state)
        self.assertTrue(backstop["llm_due"])
        self.assertIn("fifteen_minute_backstop", backstop["reasons"])

    def test_preopen_postclose_once_and_overnight_sleep(self) -> None:
        from agent.heartbeat import evaluate_tick
        from feed import MockDataSource, StateStore

        source = MockDataSource()
        state = StateStore(os.environ["AGENT_STATE_PATH"])
        preopen = evaluate_tick(now=datetime(2026, 9, 2, 12, 30, tzinfo=UTC), source=source, state=state)
        self.assertEqual(preopen["phase"], "preopen")
        self.assertFalse(preopen["llm_due"])
        self.assertIn("preopen_daily_reconciliation", preopen["reasons"])

        preopen_again = evaluate_tick(now=datetime(2026, 9, 2, 13, 0, tzinfo=UTC), source=source, state=state)
        self.assertEqual(preopen_again["reasons"], ["preopen_already_reconciled"])

        postclose = evaluate_tick(now=datetime(2026, 9, 2, 21, 0, tzinfo=UTC), source=source, state=state)
        self.assertEqual(postclose["phase"], "postclose")
        self.assertFalse(postclose["llm_due"])
        self.assertIn("postclose_daily_reconciliation", postclose["reasons"])

        sleep = evaluate_tick(now=datetime(2026, 9, 3, 6, 0, tzinfo=UTC), source=source, state=state)
        self.assertEqual(sleep, {"phase": "sleep", "llm_due": False, "reasons": ["scheduled_sleep"]})

    def test_force_market_makes_a_smoke_context_during_sleep(self) -> None:
        from agent.heartbeat import evaluate_tick
        from feed import MockDataSource, StateStore

        result = evaluate_tick(
            now=datetime(2026, 9, 3, 6, 0, tzinfo=UTC),
            source=MockDataSource(), state=StateStore(os.environ["AGENT_STATE_PATH"]),
            force_market=True,
        )
        self.assertEqual(result["phase"], "market")
        self.assertTrue(result["llm_due"])
        self.assertIsNotNone(result["context"])

    def test_events_separate_llm_attempt_success_and_failure(self) -> None:
        from agent.heartbeat import record_event

        first = record_event("llm_attempt", now=datetime(2026, 9, 2, 15, 0, tzinfo=UTC))
        self.assertIn("last_llm_attempt_at", first)
        failed = record_event("llm_failure", failure_code="provider_timeout", now=datetime(2026, 9, 2, 15, 1, tzinfo=UTC))
        self.assertEqual(failed["last_failure_code"], "provider_timeout")
        self.assertEqual(failed["consecutive_llm_failures"], 1)
        succeeded = record_event("llm_success", now=datetime(2026, 9, 2, 15, 2, tzinfo=UTC))
        self.assertEqual(succeeded["consecutive_llm_failures"], 0)
        self.assertIn("last_llm_success_at", succeeded)

    def test_tick_keeps_existing_runtime_telemetry(self) -> None:
        from agent.heartbeat import evaluate_tick, record_event
        from feed import MockDataSource, StateStore

        now = datetime(2026, 9, 2, 15, 0, tzinfo=UTC)
        record_event("llm_attempt", now=now)
        record_event("llm_failure", failure_code="provider_timeout", now=now)
        evaluate_tick(now=now + timedelta(minutes=1), source=MockDataSource(), state=StateStore(os.environ["AGENT_STATE_PATH"]))
        state = json.loads(Path(os.environ["AGENT_HEARTBEAT_PATH"]).read_text(encoding="utf-8"))
        self.assertIn("last_llm_attempt_at", state)
        self.assertIn("last_llm_failure_at", state)
        self.assertEqual(state["consecutive_llm_failures"], 1)

    def test_market_tick_records_option_observation_availability(self) -> None:
        from agent.heartbeat import evaluate_tick
        from feed import MockDataSource, StateStore

        result = evaluate_tick(
            now=datetime(2026, 9, 2, 15, 0, tzinfo=UTC), source=MockDataSource(),
            state=StateStore(os.environ["AGENT_STATE_PATH"]),
        )
        observation = result["option_market_observation"]
        self.assertIn("SPY", observation["available_symbols"])
        self.assertTrue(observation["unavailable_symbols"])
        stored = json.loads(Path(os.environ["AGENT_HEARTBEAT_PATH"]).read_text(encoding="utf-8"))
        self.assertEqual(stored["observation"]["option_market_observation"], observation)
