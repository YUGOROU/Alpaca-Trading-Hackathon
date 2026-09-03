from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


class HeartbeatReadinessTests(unittest.TestCase):
    def test_tick_expires_after_three_intervals_in_every_phase(self) -> None:
        from deploy.heartbeat_server import _freshness

        for now in (
            datetime(2026, 9, 2, 12, 0, tzinfo=UTC),  # preopen
            datetime(2026, 9, 2, 15, 0, tzinfo=UTC),  # market
            datetime(2026, 9, 2, 22, 0, tzinfo=UTC),  # postclose
            datetime(2026, 9, 3, 6, 0, tzinfo=UTC),   # sleep
        ):
            fresh, age = _freshness({"last_successful_tick_at": (now - timedelta(minutes=14, seconds=59)).timestamp()}, now=now)
            self.assertTrue(fresh)
            self.assertGreater(age or 0, 0)
            stale, _ = _freshness({"last_successful_tick_at": (now - timedelta(minutes=15, seconds=1)).timestamp()}, now=now)
            self.assertFalse(stale)

    def test_future_tick_beyond_clock_skew_is_not_fresh(self) -> None:
        from deploy.heartbeat_server import _freshness

        now = datetime(2026, 9, 2, 15, 0, tzinfo=UTC)
        fresh, age = _freshness({"last_successful_tick_at": (now + timedelta(seconds=31)).timestamp()}, now=now)
        self.assertFalse(fresh)
        self.assertLess(age or 0, 0)
        allowed, age = _freshness({"last_successful_tick_at": (now + timedelta(seconds=30)).timestamp()}, now=now)
        self.assertTrue(allowed)
        self.assertEqual(age, 0.0)

    def test_malformed_or_missing_checkpoint_is_not_fresh(self) -> None:
        from deploy.heartbeat_server import _freshness, _heartbeat_state

        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "heartbeat.json"
            self.assertEqual(_heartbeat_state(path), {})
            path.write_text("not-json", encoding="utf-8")
            self.assertEqual(_heartbeat_state(path), {})
            path.write_text(json.dumps(["not", "a", "mapping"]), encoding="utf-8")
            self.assertEqual(_heartbeat_state(path), {})
        self.assertEqual(_freshness({}, now=datetime(2026, 9, 2, 15, 0, tzinfo=UTC)), (False, None))

    def test_first_tick_requires_current_process_generation(self) -> None:
        from deploy.heartbeat_server import _wait_for_first_tick

        class Process:
            returncode = None

            def poll(self): return None
            def terminate(self): self.returncode = -15
            def wait(self, timeout): return self.returncode

        started = datetime(2026, 9, 2, 15, 0, tzinfo=UTC)
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "heartbeat.json"
            path.write_text(json.dumps({"last_successful_tick_at": (started - timedelta(seconds=1)).timestamp()}), encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "first Python tick"):
                _wait_for_first_tick(Process(), started_at=started, path=path, timeout=0)
            path.write_text(json.dumps({"last_successful_tick_at": started.timestamp()}), encoding="utf-8")
            _wait_for_first_tick(Process(), started_at=started, path=path, timeout=0.01)

    def test_sleep_checkpoint_is_persisted(self) -> None:
        from agent.heartbeat import evaluate_tick

        with tempfile.TemporaryDirectory() as root, patch.dict("os.environ", {"AGENT_HEARTBEAT_PATH": str(Path(root) / "heartbeat.json")}, clear=False):
            evaluate_tick(now=datetime(2026, 9, 3, 6, 0, tzinfo=UTC))
            state = json.loads((Path(root) / "heartbeat.json").read_text(encoding="utf-8"))
        self.assertEqual(state["last_phase"], "sleep")
        self.assertIn("last_tick_at", state)
