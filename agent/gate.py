"""Fail-closed deterministic validation and exact dry-run order construction."""

from __future__ import annotations

from hashlib import sha256

from .contracts import AgentDecision, DecisionContext, GateResult
from .limits import MAX_DEFINED_RISK_FRACTION, MAX_HEDGE_COST_DRAG, MAX_REASON_LENGTH


def _order_id(context_id: str, candidate_id: str, suffix: str) -> str:
    raw = f"{context_id}:{candidate_id}:{suffix}"
    return "dry-" + sha256(raw.encode()).hexdigest()[:24]


def _orders(context: DecisionContext, candidate) -> tuple[dict, ...]:
    orders: list[dict] = []
    for index, leg in enumerate(candidate.plan.income.legs):
        order = {
            "client_order_id": _order_id(context.context_id, candidate.candidate_id, f"income-{index}"),
            "mode": "paper_dry_run",
            "intent": "sell_to_open",
            "structure": leg.kind,
            "symbol": leg.symbol,
            "contracts": leg.contracts,
            "expiry_days": leg.expiry_days,
            "short_strike": leg.short_strike,
            "long_strike": leg.long_strike,
            # Rechecked from current executable bid/ask immediately before a
            # paper order is submitted; never trust the theoretical premium.
            "max_total_loss": context.snapshot.equity * MAX_DEFINED_RISK_FRACTION,
        }
        # An iron condor is 4 legs: describe the CALL side too, or the placer can only
        # see the put spread and would submit an incomplete structure.
        if leg.kind == "iron_condor":
            order["call_short_strike"] = leg.call_short_strike
            order["call_long_strike"] = leg.call_long_strike
        orders.append(order)

    hedge = candidate.plan.hedge
    if hedge.contracts_delta != 0:
        orders.append({
            "client_order_id": _order_id(context.context_id, candidate.candidate_id, "hedge"),
            "mode": "paper_dry_run",
            "intent": "buy_to_open" if hedge.contracts_delta > 0 else "sell_to_close",
            "structure": "protective_put",
            "symbol": candidate.hedge_symbol,
            "contracts": abs(hedge.contracts_delta),
            "expiry_days": hedge.put_expiry_days,
            "strike": hedge.put_strike,
            # The proposal's Black-Scholes premium only admitted this hedge
            # under the cap. The executor must re-gate using the live ask.
            "max_total_cost": context.snapshot.equity * MAX_HEDGE_COST_DRAG,
        })
    return tuple(orders)


def validate_decision(context: DecisionContext, decision: AgentDecision) -> GateResult:
    reasons: list[str] = []
    candidate = next(
        (item for item in context.candidates if item.candidate_id == decision.candidate_id),
        None,
    )
    if decision.context_id != context.context_id:
        reasons.append("stale_or_unknown_context")
    if candidate is None:
        reasons.append("candidate_not_admissible")
    if not decision.reason.strip():
        reasons.append("reason_required")
    if len(decision.reason) > MAX_REASON_LENGTH:
        reasons.append("reason_too_long")

    if candidate is not None:
        equity = context.snapshot.equity
        if candidate.plan.income.total_max_loss > equity * MAX_DEFINED_RISK_FRACTION + 1e-9:
            reasons.append("defined_risk_limit_exceeded")
        if candidate.plan.hedge.hedge_cost_drag > MAX_HEDGE_COST_DRAG + 1e-9:
            reasons.append("hedge_cost_limit_exceeded")
        if not 0.0 <= candidate.plan.hedge.target_coverage <= 1.0:
            reasons.append("coverage_out_of_bounds")
        if context.snapshot.risk_score >= 40.0 and candidate.plan.income.legs:
            reasons.append("short_premium_disallowed_in_elevated_risk")

    if reasons or candidate is None:
        return GateResult(
            status="rejected",
            context_id=context.context_id,
            candidate_id=decision.candidate_id,
            reasons=tuple(reasons),
            orders=(),
        )
    return GateResult(
        status="approved_for_dry_run",
        context_id=context.context_id,
        candidate_id=decision.candidate_id,
        reasons=("candidate_and_limits_valid",),
        orders=_orders(context, candidate),
        human_approval_required=context.execution_mode == "human",
    )
