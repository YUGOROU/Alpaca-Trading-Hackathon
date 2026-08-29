"""The EXECUTE step — place the approved orders (README §6).

The decision -> order translation lives in `orders.py`; here we just submit each
`OrderIntent` through a `Broker`. Options:

  * `default_executor`  — DRY RUN: lists the exact orders (with legs) it would place.
  * `BrokerExecutor(broker)` — submits each intent through any `Broker`
    (`DryRunBroker`, a test fake, or a live Alpaca broker — the DSH harness owns live
    MCP order placement now; the retired in-house `McpBroker` is in _archive/).

Swap the stub for `BrokerExecutor(<live broker>)` when you're ready to trade — nothing
upstream changes.
"""
from __future__ import annotations

from .orders import Broker, OrderIntent, plan_to_orders


def default_executor(decision: dict, context: dict) -> dict:
    """Dry-run: translate the decision into the concrete orders it would submit."""
    intents = plan_to_orders(
        decision.get("income_legs", []),
        decision.get("hedge", {}),
        context.get("index_symbol", "SPY"),
    )
    return {
        "dry_run": True,
        "orders": [i.to_dict() for i in intents],
        "note": "no live broker wired - dry-run only",
    }


class DryRunBroker:
    """A Broker that places nothing and just echoes the intent (safe default)."""

    dry_run = True

    def submit(self, intent: OrderIntent) -> dict:
        return {"status": "dry_run", "order": intent.to_dict()}


class BrokerExecutor:
    """An executor that submits each order intent through a `Broker`."""

    def __init__(self, broker: Broker):
        self.broker = broker

    def __call__(self, decision: dict, context: dict) -> dict:
        intents = plan_to_orders(
            decision.get("income_legs", []),
            decision.get("hedge", {}),
            context.get("index_symbol", "SPY"),
        )
        results = [self.broker.submit(i) for i in intents]
        return {
            "dry_run": getattr(self.broker, "dry_run", False),
            "orders": [i.to_dict() for i in intents],
            "results": results,
        }
