"""The agent harness — the loop that drives the engine (README §4, §6).

    from feed import AlpacaDataSource, StateStore
    from harness import run_cycle
    state = run_cycle(AlpacaDataSource(), StateStore("state.json"))  # one cycle
    print(state["log"])

A plain, dependency-free Python loop (`run_cycle`). The DECIDE node is `default_decider`
(llm.py) — a stub that approves the risk-validated plan; the DSH harness owns the real
model brain + MCP order placement. Swap `default_executor` (executor.py) for live order
placement; the node contracts stay the same.
"""
from __future__ import annotations

from .executor import BrokerExecutor, DryRunBroker, default_executor
from .llm import default_decider
from .orders import OptionLeg, OrderIntent, plan_to_orders
from .run import run_cycle

__all__ = [
    "run_cycle",
    "default_decider",
    "default_executor",
    "BrokerExecutor",
    "DryRunBroker",
    "OrderIntent",
    "OptionLeg",
    "plan_to_orders",
]
