"""Translate an approved plan into concrete option ORDER INTENTS (README §4 EXECUTE).

This is the deterministic bridge between "what the engine decided" and "what the broker
places." Each income leg / hedge becomes an `OrderIntent` with its exact option legs
(right, strike, buy/sell). A `Broker` then submits each intent — a `DryRunBroker`, a
live broker (the DSH harness places orders via MCP), or a test fake — without this
translation layer changing.

Strikes + `expiry_days` are as the engine sized them; the broker resolves `expiry_days`
to the nearest listed expiration and the strikes to real listed contracts.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class OptionLeg:
    right: str      # "P" or "C"
    strike: float
    action: str     # "buy" or "sell"

    def to_dict(self) -> dict:
        return {"right": self.right, "strike": self.strike, "action": self.action}


@dataclass(frozen=True)
class OrderIntent:
    """One order the executor will place (single- or multi-leg)."""

    structure: str        # "covered_call" | "iron_condor" | "protective_put" | ...
    symbol: str
    contracts: int
    expiry_days: int
    net_side: str         # "credit" (we collect) | "debit" (we pay)
    legs: tuple[OptionLeg, ...]
    note: str = ""

    def to_dict(self) -> dict:
        return {
            "structure": self.structure,
            "symbol": self.symbol,
            "contracts": self.contracts,
            "expiry_days": self.expiry_days,
            "net_side": self.net_side,
            "legs": [l.to_dict() for l in self.legs],
            "note": self.note,
        }


class Broker(Protocol):
    """Anything that can place an OrderIntent (dry-run, MCP, or a test fake)."""

    def submit(self, intent: OrderIntent) -> dict: ...


def _income_leg_to_intent(leg: dict) -> OrderIntent | None:
    kind = leg["kind"]
    n = leg["contracts"]
    exp = leg["expiry_days"]
    sym = leg["symbol"]
    if n <= 0:
        return None

    if kind == "covered_call":
        legs = (OptionLeg("C", leg["short_strike"], "sell"),)
    elif kind == "iron_condor":
        legs = (
            OptionLeg("P", leg["short_strike"], "sell"),      # short put
            OptionLeg("P", leg["long_strike"], "buy"),        # long put (protection)
            OptionLeg("C", leg["call_short_strike"], "sell"),  # short call
            OptionLeg("C", leg["call_long_strike"], "buy"),   # long call (protection)
        )
    elif kind == "cash_secured_put":
        legs = (OptionLeg("P", leg["short_strike"], "sell"),)
    elif kind == "bull_put_spread":
        legs = (
            OptionLeg("P", leg["short_strike"], "sell"),
            OptionLeg("P", leg["long_strike"], "buy"),
        )
    elif kind == "bear_call_spread":
        legs = (
            OptionLeg("C", leg["short_strike"], "sell"),
            OptionLeg("C", leg["long_strike"], "buy"),
        )
    else:
        return None  # unknown structure -> skip rather than mis-trade

    return OrderIntent(
        structure=kind, symbol=sym, contracts=n, expiry_days=exp,
        net_side="credit", legs=legs, note=leg.get("note", ""),
    )


def _hedge_to_intent(hedge: dict, index_symbol: str) -> OrderIntent | None:
    delta = hedge.get("contracts_delta", 0)
    if hedge.get("action") == "hold" or not delta:
        return None
    action = "buy" if delta > 0 else "sell"   # buy to add protection, sell to reduce
    return OrderIntent(
        structure="protective_put",
        symbol=index_symbol,
        contracts=abs(delta),
        expiry_days=hedge.get("put_expiry_days", 14),
        net_side="debit",
        legs=(OptionLeg("P", hedge["put_strike"], action),),
        note="tail hedge",
    )


def plan_to_orders(
    income_legs: list[dict], hedge: dict, index_symbol: str = "SPY"
) -> list[OrderIntent]:
    """Turn the approved income legs + hedge into concrete order intents."""
    intents: list[OrderIntent] = []
    for leg in income_legs:
        intent = _income_leg_to_intent(leg)
        if intent is not None:
            intents.append(intent)
    hedge_intent = _hedge_to_intent(hedge or {}, index_symbol)
    if hedge_intent is not None:
        intents.append(hedge_intent)
    return intents
