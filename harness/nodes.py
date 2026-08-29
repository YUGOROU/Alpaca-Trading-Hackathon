"""The agent-loop nodes (README §4), written framework-agnostic.

Each node is a small pure-ish function that takes what it needs and returns a partial
state update. `run.py` runs them directly with no framework dependency. Keeping the
logic in these small functions means the runner stays simple and easy to test.
"""
from __future__ import annotations

from runtime.strategy_api import get_strategy_context

from .executor import default_executor
from .llm import default_decider


def measure(source, state_store, day_pnl_pct: float = 0.0, index_symbol: str = "SPY") -> dict:
    """OBSERVE + MEASURE: pull live data and produce the risk-capped JSON context."""
    ctx = get_strategy_context(
        source, state_store, day_pnl_pct=day_pnl_pct, index_symbol=index_symbol
    )
    return {"context": ctx}


def decide(context: dict, decider=default_decider) -> dict:
    """DECIDE: the model (or stub) reads the context and picks the posture."""
    return {"decision": decider(context)}


def route_after_decide(decision: dict, context: dict) -> str:
    """Skip execution when there's nothing to do (SIT cycle or a rejected plan)."""
    plan = context["plan"]
    has_income = bool(plan["income"]["legs"])
    hedge_acts = plan["hedge"]["action"] != "hold"
    if decision.get("approved") and (has_income or hedge_acts):
        return "execute"
    return "log"


def execute(decision: dict, context: dict, executor=default_executor) -> dict:
    """EXECUTE: place the approved orders (stub does a dry run)."""
    return {"execution": executor(decision, context)}


def log_cycle(context: dict, decision: dict | None, execution: dict | None) -> dict:
    """LOG: one-line, self-grading summary of the cycle."""
    plan = context["plan"]
    inc = plan["income"]
    parts = [
        plan["posture"],
        f"credit=${inc['total_credit']:,.0f}",
        f"theta=+${inc['net_theta_per_day']:,.0f}/day",
        f"hedge={plan['hedge']['action']}x{plan['hedge']['contracts_target']}",
        f"ok={context['validation']['ok']}",
    ]
    if context["validation"]["violations"]:
        parts.append("violations=" + "; ".join(context["validation"]["violations"]))
    if execution:
        mode = "DRY-RUN" if execution.get("dry_run") else "LIVE"
        parts.append(f"{mode} {len(execution.get('orders', []))} orders")
    else:
        parts.append("no trades")
    return {"log": " | ".join(parts)}
