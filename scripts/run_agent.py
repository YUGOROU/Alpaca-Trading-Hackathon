"""One agent cycle — the entry point for cron / one-shot runs (README §6).

Uses live Alpaca data when ALPACA_API_KEY/ALPACA_SECRET_KEY are set, else MockDataSource.
Persists engine state + the trade ledger between runs. Execution is still the dry-run
stub, so this is SAFE to schedule now (observes + logs the plan, places no real orders).

    python scripts/run_agent.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # repo root on sys.path


def _load_env() -> None:
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except Exception:
        pass  # dotenv optional; containers/CI pass env directly


def make_decider():
    """The DECIDE-node decider: approve the risk-validated plan unchanged.

    Real judgment (approve / reduce / skip) now lives in the DSH harness, so this
    in-house node is just the deterministic stub that lets a cycle run end-to-end.
    """
    from harness import default_decider

    return default_decider


def build_context():
    """Construct (source, state, ledger, mode, decider) once — reused across cycles."""
    _load_env()
    from feed import StateStore
    from runtime.ledger import TradeLedger

    have_creds = bool(os.getenv("ALPACA_API_KEY") and os.getenv("ALPACA_SECRET_KEY"))
    if have_creds:
        from feed import AlpacaDataSource

        source, mode = AlpacaDataSource(), "LIVE"
    else:
        from feed import MockDataSource

        source, mode = MockDataSource(), "MOCK (no ALPACA_* creds)"

    state = StateStore(os.getenv("AGENT_STATE_PATH", "state/state.json"))
    ledger = TradeLedger(os.getenv("AGENT_LEDGER_PATH", "state/ledger.jsonl"))
    return source, state, ledger, mode, make_decider()


def run_once(source, state, ledger, mode: str, decider=None) -> dict:
    """Run one observe->decide->execute->log cycle and record it to the ledger."""
    from harness import default_decider, run_cycle

    day_pnl = float(os.getenv("DAY_PNL_PCT", "0") or 0.0)
    result = run_cycle(source, state, day_pnl_pct=day_pnl, decider=decider or default_decider)
    ledger.record_cycle(result, mode="LIVE" if mode.startswith("LIVE") else "MOCK")

    # Self-grade any past cycles whose options have now expired (README §8).
    try:
        from runtime.grade import grade_ledger

        grade_ledger(ledger, price_lookup=source.latest_price)
    except Exception as e:
        print(f"grade skipped: {type(e).__name__}: {e}", flush=True)

    print(f"[{mode}] {result['log']}", flush=True)
    return result


def main() -> int:
    run_once(*build_context())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
