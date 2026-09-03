"""HTTP liveness wrapper for the DSH heartbeat running inside a Modal Server."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import threading
import time
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

try:  # This file is both a script in /app/deploy and an import in tests.
    from approval_server import ApprovalApiMixin, rows
    from public_monitor import snapshot
except ModuleNotFoundError:
    from deploy.approval_server import ApprovalApiMixin, rows
    from deploy.public_monitor import snapshot

MONITOR_PAGE = Path(__file__).with_name("monitor_ui.html")
HEARTBEAT_PATH = Path("/data/state/heartbeat.json")
DEFAULT_FIRST_TICK_TIMEOUT_SECONDS = 90
HEARTBEAT_INTERVAL_SECONDS = 5 * 60
MAX_FUTURE_TICK_SKEW_SECONDS = 30


def _heartbeat_state(path: Path = HEARTBEAT_PATH) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    return value if isinstance(value, dict) else {}


def _freshness(state: dict[str, object], *, now: datetime | None = None) -> tuple[bool, float | None]:
    """Return tick freshness and age without exposing persisted state over HTTP."""
    now = now or datetime.now(UTC)
    value = state.get("last_successful_tick_at")
    try:
        age = now.timestamp() - float(value)
    except (TypeError, ValueError):
        return False, None
    # The Python scheduler runs every five minutes in every session phase.
    # Daily pre/post reconciliation is business work, not a liveness allowance.
    if age < -MAX_FUTURE_TICK_SKEW_SECONDS:
        return False, age
    return age <= HEARTBEAT_INTERVAL_SECONDS * 3, max(0.0, age)


def _wait_for_first_tick(
    process: subprocess.Popen[bytes], *, started_at: datetime, path: Path = HEARTBEAT_PATH,
    timeout: float = DEFAULT_FIRST_TICK_TIMEOUT_SECONDS,
) -> None:
    if started_at.tzinfo is None:
        raise ValueError("started_at must be timezone-aware")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"heartbeat exited before its first tick (exit {process.returncode})")
        tick = _heartbeat_state(path).get("last_successful_tick_at")
        try:
            is_current_generation = float(tick) >= started_at.timestamp()
        except (TypeError, ValueError):
            is_current_generation = False
        if is_current_generation:
            return
        time.sleep(0.25)
    if process.poll() is None:
        process.terminate()
        process.wait(timeout=30)
    raise RuntimeError("heartbeat did not record its first Python tick before readiness deadline")


def heartbeat_process() -> subprocess.Popen[bytes]:
    env = os.environ.copy()
    env.update(
        {
            "DSH_HOME": "/data/dsh",
            "DSH_PERMISSION_MODE": "read-only",
            "DSH_TELEMETRY_MODE": "DISABLED",
            "AGENT_STATE_PATH": "/data/state/state.json",
            "AGENT_CONTEXT_PATH": "/data/state/contexts.jsonl",
            "AGENT_HEARTBEAT_PATH": "/data/state/heartbeat.json",
            "AGENT_HEARTBEAT_RUN_KIND": "service",
        }
    )
    Path("/data/state").mkdir(parents=True, exist_ok=True)
    command = [
        "flock", "-n", "/data/heartbeat.lock", "/app/deploy/startup.sh",
        "--live", "--heartbeat", "--interval", env["HEARTBEAT_INTERVAL_MS"],
        "--ledger", "/data/state/decisions.jsonl", env["HEARTBEAT_INSTRUCTION"],
    ]
    return subprocess.Popen(command, cwd="/app", env=env)


def serve(process: subprocess.Popen[bytes], *, started_at: datetime | None = None) -> None:
    # A Modal Server must begin accepting HTTP connections promptly.  The first
    # live Python tick can legitimately take longer than Modal's server-start
    # window during a market-hours LLM turn, so bind first and expose only 503
    # responses until the current process generation has recorded that tick.
    # This preserves fail-closed readiness without turning a slow first turn
    # into an endless container-replacement loop.
    startup_ready = threading.Event()
    startup_error: list[BaseException] = []

    class Handler(ApprovalApiMixin, BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 -- HTTP method spelling
            request = urlparse(self.path)
            path = request.path
            process_alive = process.poll() is None
            input_ready = bool(os.getenv("ALPACA_API_KEY") and os.getenv("ALPACA_SECRET_KEY"))
            tick_fresh, tick_age_seconds = _freshness(_heartbeat_state())
            running = process_alive and startup_ready.is_set() and tick_fresh
            if path in {"/", "/ja"}:
                language = "ja" if path == "/ja" else "en"
                payload = MONITOR_PAGE.read_text(encoding="utf-8").replace('lang="en"', f'lang="{language}"', 1).encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
                return
            if path == "/api/monitor":
                language = parse_qs(request.query).get("lang", ["en"])[0]
                self._json(200 if running else 503, snapshot(
                    heartbeat_running=running,
                    input_ready=input_ready,
                    ledger_rows=rows(),
                    language=language,
                ))
                return
            if path not in {"/healthz", "/statusz"}:
                if self.handle_approval_get():
                    return
                self.send_error(404)
                return
            if path == "/statusz":
                ledger_rows = rows()
                last_event = ledger_rows[-1].get("event") if ledger_rows else None
                state = _heartbeat_state()
                body = {
                    "ok": running and input_ready,
                    "heartbeat_running": running,
                    "process_alive": process_alive,
                    "startup_ready": startup_ready.is_set(),
                    "tick_fresh": tick_fresh,
                    "tick_age_seconds": tick_age_seconds,
                    "input_ready": input_ready,
                    "input_source": "alpaca_rest" if os.getenv("ALPACA_API_KEY") and os.getenv("ALPACA_SECRET_KEY") else "unavailable",
                    "approval_auth_configured": bool(os.getenv("HUMAN_APPROVAL_TOKEN")),
                    "last_ledger_event": last_event,
                    "last_successful_tick_at": state.get("last_successful_tick_at"),
                    "last_llm_attempt_at": state.get("last_llm_attempt_at"),
                    "last_llm_success_at": state.get("last_llm_success_at"),
                    "last_llm_failure_at": state.get("last_llm_failure_at"),
                    "consecutive_tick_failures": state.get("consecutive_tick_failures", 0),
                    "consecutive_llm_failures": state.get("consecutive_llm_failures", 0),
                    "last_run_kind": state.get("last_run_kind"),
                    "deploy_git_sha": os.getenv("DEPLOY_GIT_SHA", "unknown"),
                    "deploy_tree_state": os.getenv("DEPLOY_TREE_STATE", "unknown"),
                }
            else:
                body = {"ok": running, "heartbeat_running": running, "process_alive": process_alive, "tick_fresh": tick_fresh}
            payload = json.dumps(body).encode()
            self.send_response(200 if body["ok"] else 503)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_POST(self) -> None:  # noqa: N802 -- HTTP method spelling
            if self.handle_approval_post():
                return
            self.send_error(404)

        def log_message(self, _format: str, *_args: object) -> None:
            return

    server = ThreadingHTTPServer(("0.0.0.0", 8080), Handler)

    def await_initial_tick() -> None:
        try:
            _wait_for_first_tick(process, started_at=started_at or datetime.now(UTC))
            startup_ready.set()
        except BaseException as error:
            startup_error.append(error)
            server.shutdown()

    initial_tick_thread = threading.Thread(target=await_initial_tick, daemon=True)
    initial_tick_thread.start()
    try:
        server.serve_forever()
    finally:
        if process.poll() is None:
            process.send_signal(signal.SIGTERM)
            process.wait(timeout=30)
        server.server_close()
    if startup_error:
        raise startup_error[0]


if __name__ == "__main__":
    started_at = datetime.now(UTC)
    serve(heartbeat_process(), started_at=started_at)
