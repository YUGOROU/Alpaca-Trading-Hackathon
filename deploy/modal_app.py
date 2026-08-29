"""Modal entrypoint for the paper-only DSH portfolio heartbeat.

This app deliberately has no Alpaca credential secret.  It can build and run the
fixture/replay path, but a real Alpaca account cannot be contacted or traded from
this deployment until a separate, reviewed change introduces that capability.
"""

from __future__ import annotations

import os
import json
import subprocess
import tempfile
from pathlib import Path

import modal


APP_NAME = "liquidity-leak-dsh-heartbeat"
VOLUME_NAME = "liquidity-leak-dsh-state"
MODEL_SECRET_NAME = "huggingface"
REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INSTRUCTION = "Protect the paper portfolio using only admissible candidates."

image = (
    modal.Image.from_registry("node:22-bookworm", add_python="3.12")
    .apt_install("util-linux")  # provides flock for the single-heartbeat lease
    .add_local_dir(
        REPO_ROOT,
        "/app",
        copy=True,
        ignore=[
            ".git",
            ".dsh",
            ".agent",
            ".hermes",
            ".venv",
            "venv",
            "node_modules",
            "agent/dsh/node_modules",
            "state",
            "__pycache__",
            "*.pyc",
            ".env",
            ".env.*",
        ],
    )
    .run_commands(
        "cd /app/agent/dsh && npm ci",
        "python -m pip install --no-cache-dir -r /app/requirements.txt",
    )
)

app = modal.App(APP_NAME)
state_volume = modal.Volume.from_name(VOLUME_NAME, create_if_missing=True)
hf_token_secret = modal.Secret.from_name(MODEL_SECRET_NAME, required_keys=["HF_TOKEN"])
selected_model_secret = modal.Secret.from_name(
    MODEL_SECRET_NAME,
    required_keys=["HF_TOKEN", "HF_MODEL_ID"],
)


@app.server(
    port=8080,
    image=image,
    cpu=1.0,
    memory=2048,
    min_containers=1,
    max_containers=1,
    volumes={"/data": state_volume},
    secrets=[selected_model_secret],
)
class HeartbeatServer:
    """Always-on CPU container whose entrypoint owns the DSH heartbeat process."""

    @modal.enter()
    def start(self) -> None:
        # HF_TOKEN and the already-evaluated HF_MODEL_ID are injected by Modal;
        # Alpaca credentials are never mounted here.
        os.environ.setdefault("HEARTBEAT_INSTRUCTION", DEFAULT_INSTRUCTION)
        os.environ.setdefault("HEARTBEAT_INTERVAL_MS", "1800000")
        Path("/data/state").mkdir(parents=True, exist_ok=True)
        self.process = __import__("subprocess").Popen(
            ["python", "/app/deploy/heartbeat_server.py"],
            cwd="/app",
            env=os.environ.copy(),
        )

    @modal.exit()
    def stop(self) -> None:
        if self.process.poll() is None:
            self.process.terminate()
            self.process.wait(timeout=30)


@app.function(image=image, volumes={"/data": state_volume})
def inspect_state() -> dict[str, bool]:
    """Credential-free deployment smoke check; it never contacts Alpaca or an LLM."""
    return {
        "volume_mounted": Path("/data").is_dir(),
        "profile_initialized": Path("/data/dsh/profiles/portfolio-agent").is_dir(),
        "heartbeat_lock_present": Path("/data/heartbeat.lock").exists(),
    }


@app.function(image=image, cpu=1.0, memory=2048, timeout=900, secrets=[hf_token_secret])
def evaluate_model(model_id: str) -> dict[str, object]:
    """Run the fixed three-scenario DSH selection evaluation without Alpaca access.

    This is the model-selection gate. It deliberately runs fixture scenarios only;
    a candidate that errors, bypasses the gate, or cannot select an admissible
    candidate is not eligible to become the persistent heartbeat default.
    """
    if not model_id.strip():
        raise ValueError("model_id must not be empty")
    with tempfile.TemporaryDirectory(prefix="liquidity-leak-model-eval-") as root:
        ledger = Path(root) / "decisions.jsonl"
        env = os.environ.copy()
        env.update(
            {
                "DSH_HOME": str(Path(root) / "dsh"),
                "DSH_PERMISSION_MODE": "read-only",
                "DSH_TELEMETRY_MODE": "DISABLED",
                "HF_MODEL_ID": model_id,
            }
        )
        cases: list[dict[str, object]] = []
        for scenario in ("calm", "elevated", "stressed"):
            result = subprocess.run(
                [
                    "/app/deploy/startup.sh", "--scenario", scenario,
                    "--ledger", str(ledger),
                    "Select one admissible candidate and explain its trade-off.",
                ],
                cwd="/app",
                env=env,
                capture_output=True,
                text=True,
                timeout=240,
                check=False,
            )
            cases.append({"scenario": scenario, "exit_code": result.returncode})
        rows = [json.loads(line) for line in ledger.read_text().splitlines()] if ledger.exists() else []
        approved = [row for row in rows if row.get("gate", {}).get("status") == "approved_for_dry_run"]
        return {
            "model_id": model_id,
            "cases": cases,
            "approved_for_dry_run": len(approved),
            "eligible": len(cases) == 3 and all(case["exit_code"] == 0 for case in cases) and len(approved) == 3,
        }
