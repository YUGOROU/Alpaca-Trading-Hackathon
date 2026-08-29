"""Fixture-only Modal entrypoint for selecting an HF Router model.

This app intentionally excludes the persistent Server definition.  That keeps
the model-selection gate runnable while the ``huggingface`` Secret contains
only ``HF_TOKEN``; ``HF_MODEL_ID`` is required only after a candidate passes.
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

import modal


REPO_ROOT = Path(__file__).resolve().parents[1]
MODEL_SECRET_NAME = "huggingface"
RESULTS_DICT_NAME = "liquidity-leak-dsh-model-eval-results"

image = (
    modal.Image.from_registry("node:22-bookworm", add_python="3.12")
    .add_local_dir(
        REPO_ROOT,
        "/app",
        copy=True,
        ignore=[
            ".git", ".dsh", ".agent", ".hermes", ".venv", "venv",
            "node_modules", "agent/dsh/node_modules", "state", "__pycache__",
            "*.pyc", ".env", ".env.*",
        ],
    )
    .run_commands(
        "npm install --global pnpm@10",
        "cd /app/agent/dsh && npm ci",
        "python -m pip install --no-cache-dir -r /app/requirements.txt",
    )
)

app = modal.App("liquidity-leak-dsh-model-eval")
hf_token_secret = modal.Secret.from_name(MODEL_SECRET_NAME, required_keys=["HF_TOKEN"])
evaluation_results = modal.Dict.from_name(RESULTS_DICT_NAME, create_if_missing=True)


@app.function(
    image=image,
    cpu=1.0,
    memory=2048,
    timeout=1800,
    secrets=[hf_token_secret],
)
def evaluate_model(model_id: str, result_key: str, qualification_only: bool = False) -> dict[str, object]:
    """Run sanitized protocol qualification or fixture evaluation without Alpaca access."""
    if not model_id.strip():
        raise ValueError("model_id must not be empty")
    if not result_key.strip():
        raise ValueError("result_key must not be empty")

    with tempfile.TemporaryDirectory(prefix="liquidity-leak-model-eval-") as root:
        result_path = Path(root) / "result.json"
        command = [
            "python", "/app/scripts/run_deployment_model_evaluation.py",
            "--model", model_id,
            "--output", str(result_path),
        ]
        if qualification_only:
            command.append("--qualification-only")
        completed = subprocess.run(
            command,
            cwd="/app",
            capture_output=True,
            text=True,
            timeout=1750,
            check=False,
        )
        if completed.returncode != 0 or not result_path.exists():
            raise RuntimeError("sanitized deployment-model evaluation did not complete")
        import json

        payload = json.loads(result_path.read_text(encoding="utf-8"))
        summary = payload["models"][0]
        evaluation_results.put(result_key, summary)
        return summary
