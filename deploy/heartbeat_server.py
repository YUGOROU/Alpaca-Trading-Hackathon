"""HTTP liveness wrapper for the DSH heartbeat running inside a Modal Server."""

from __future__ import annotations

import json
import os
import signal
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def heartbeat_process() -> subprocess.Popen[bytes]:
    env = os.environ.copy()
    env.update(
        {
            "DSH_HOME": "/data/dsh",
            "DSH_PERMISSION_MODE": "read-only",
            "DSH_TELEMETRY_MODE": "DISABLED",
            "AGENT_STATE_PATH": "/data/state/state.json",
            "AGENT_CONTEXT_PATH": "/data/state/contexts.jsonl",
        }
    )
    Path("/data/state").mkdir(parents=True, exist_ok=True)
    command = [
        "flock", "-n", "/data/heartbeat.lock", "/app/deploy/startup.sh",
        "--live", "--heartbeat", "--interval", env["HEARTBEAT_INTERVAL_MS"],
        "--ledger", "/data/state/decisions.jsonl", env["HEARTBEAT_INSTRUCTION"],
    ]
    return subprocess.Popen(command, cwd="/app", env=env)


def serve(process: subprocess.Popen[bytes]) -> None:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 -- HTTP method spelling
            if self.path != "/healthz":
                self.send_error(404)
                return
            running = process.poll() is None
            payload = json.dumps({"ok": running, "heartbeat_running": running}).encode()
            self.send_response(200 if running else 503)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, _format: str, *_args: object) -> None:
            return

    server = ThreadingHTTPServer(("0.0.0.0", 8080), Handler)
    try:
        server.serve_forever()
    finally:
        if process.poll() is None:
            process.send_signal(signal.SIGTERM)
            process.wait(timeout=30)


if __name__ == "__main__":
    serve(heartbeat_process())
