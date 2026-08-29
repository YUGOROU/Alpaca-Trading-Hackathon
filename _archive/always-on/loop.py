"""Always-on scheduler — run a cycle every LOOP_INTERVAL_SECONDS (README §6).

The alternative to GitHub Actions cron: one long-running process (e.g. a Docker
container) that ticks on its own. Deps are installed once (in the image), state + ledger
persist on a mounted volume, so there's no per-run reinstall and no git commit-back.

    LOOP_INTERVAL_SECONDS=1800 python loop.py
"""
from __future__ import annotations

import os
import time

from run_agent import build_context, run_once


def main() -> int:
    interval = int(os.getenv("LOOP_INTERVAL_SECONDS", "1800"))  # default 30 min
    source, state, ledger, mode, decider = build_context()
    print(f"[{mode}] agent loop started — one cycle every {interval}s", flush=True)
    while True:
        try:
            run_once(source, state, ledger, mode, decider)
        except Exception as e:  # never let one bad cycle kill the loop
            print(f"cycle error: {type(e).__name__}: {e}", flush=True)
        time.sleep(interval)


if __name__ == "__main__":
    raise SystemExit(main())
