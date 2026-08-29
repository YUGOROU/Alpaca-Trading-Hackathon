"""Risk caps — the deterministic bouncer between the engine and the executor (§7.10).

`validate_plan` is the last gate before any order is placed. It enforces hard limits the
LLM/agent can NOT override: a daily-loss halt, a total option-risk cap, a per-underlying
cap, and a hedge-premium flag. Offending income is *scaled down* (never silently
dropped), so the agent only ever chooses from a pre-validated, safe set. Pure and
deterministic — no broker, no model.
"""
from __future__ import annotations

from dataclasses import dataclass, replace

from .engine import StrategyPlan, _posture
from .income import IncomeLeg, IncomePlan

# --- The caps (fractions of account equity) ---
DAILY_LOSS_HALT = 0.05            # down >=5% on the day -> stop opening new premium
MAX_TOTAL_OPTION_RISK_FRAC = 0.30  # all income defined-risk + hedge premium <= 30%
MAX_PER_UNDERLYING_RISK_FRAC = 0.15  # defined risk on any one symbol <= 15%
MAX_HEDGE_PREMIUM_FRAC = 0.05      # hedge premium spent per cycle <= 5% (flag if over)


@dataclass(frozen=True)
class PlanValidation:
    """Outcome of the risk-cap gate: whether it passed clean, what tripped, and the
    (possibly scaled-down) plan that is safe to execute."""

    ok: bool                 # True if nothing tripped
    violations: list[str]    # human-readable list of what was clamped/flagged
    plan: StrategyPlan       # the safe plan to act on

    def to_dict(self) -> dict:
        return {"ok": self.ok, "violations": self.violations, "plan": self.plan.to_dict()}


def _scale_leg(leg: IncomeLeg, factor: float) -> IncomeLeg | None:
    """Scale a leg's size by `factor` (0..1). Every $ field scales linearly with
    contracts; returns None if it rounds down to nothing."""
    new_contracts = int(leg.contracts * factor)
    if new_contracts <= 0:
        return None
    ratio = new_contracts / leg.contracts
    return replace(
        leg,
        contracts=new_contracts,
        credit=leg.credit * ratio,
        max_loss=leg.max_loss * ratio,
        capital_reserved=leg.capital_reserved * ratio,
        theta_per_day=leg.theta_per_day * ratio,
    )


def _rebuild_income(src: IncomePlan, legs: list[IncomeLeg]) -> IncomePlan:
    """Recompute an IncomePlan's totals from a (clamped) list of legs."""
    total_credit = sum(l.credit for l in legs)
    expiry = legs[0].expiry_days if legs else 7
    equity_est = (
        (src.total_credit / src.annualized_yield) * (365.0 / expiry)
        if src.annualized_yield else 0.0
    )
    ann_yield = (total_credit / equity_est) * (365.0 / expiry) if equity_est else 0.0
    return IncomePlan(
        legs=legs,
        total_credit=total_credit,
        total_max_loss=sum(l.max_loss for l in legs),
        capital_reserved=sum(l.capital_reserved for l in legs),
        net_theta_per_day=sum(l.theta_per_day for l in legs),
        aggressiveness=src.aggressiveness,
        annualized_yield=ann_yield,
    )


def _rebuild_strategy(src: StrategyPlan, income: IncomePlan) -> StrategyPlan:
    """Reassemble a StrategyPlan around a clamped income overlay (hedge unchanged)."""
    hedge = src.hedge
    hedge_theta_total = hedge.theta_per_day * hedge.contracts_target
    return StrategyPlan(
        posture=_posture(None, income, hedge),  # _posture reads only income/hedge
        income=income,
        hedge=hedge,
        net_theta_per_day=income.net_theta_per_day + hedge_theta_total,
        net_cost_today=hedge.total_cost - income.total_credit,
    )


def validate_plan(
    plan: StrategyPlan, equity: float, day_pnl_pct: float = 0.0
) -> PlanValidation:
    """Enforce the §7.10 caps on a proposed StrategyPlan.

    `day_pnl_pct` is today's realized P&L as a fraction of equity (negative = a loss);
    at/under -DAILY_LOSS_HALT it halts all new premium selling. Returns the safe,
    possibly scaled-down plan plus a list of what tripped.
    """
    violations: list[str] = []
    legs = list(plan.income.legs)

    # 1) Daily-loss halt — stop opening new premium (keep the hedge on).
    if day_pnl_pct <= -DAILY_LOSS_HALT:
        if legs:
            violations.append(
                f"daily loss {day_pnl_pct*100:.1f}% <= -{DAILY_LOSS_HALT*100:.0f}%: "
                "halted new premium selling"
            )
        legs = []

    # 2) Per-underlying defined-risk cap — scale down offending symbols.
    if equity > 0 and legs:
        per_cap = MAX_PER_UNDERLYING_RISK_FRAC * equity
        by_sym: dict[str, float] = {}
        for l in legs:
            by_sym[l.symbol] = by_sym.get(l.symbol, 0.0) + l.max_loss
        scaled: list[IncomeLeg] = []
        for l in legs:
            sym_risk = by_sym[l.symbol]
            if sym_risk > per_cap > 0:
                f = per_cap / sym_risk
                new = _scale_leg(l, f)
                if new is not None:
                    scaled.append(new)
                if f < 1.0 and f"{l.symbol} risk" not in " ".join(violations):
                    violations.append(
                        f"{l.symbol} defined risk over {MAX_PER_UNDERLYING_RISK_FRAC*100:.0f}%"
                        " of equity: scaled down"
                    )
            else:
                scaled.append(l)
        legs = scaled

    # 3) Total option-risk cap (income defined risk + hedge premium).
    if equity > 0 and legs:
        income_risk = sum(l.max_loss for l in legs)
        room = MAX_TOTAL_OPTION_RISK_FRAC * equity - plan.hedge.total_cost
        if income_risk > room and income_risk > 0:
            f = max(0.0, room / income_risk)
            legs = [nl for l in legs if (nl := _scale_leg(l, f)) is not None]
            violations.append(
                f"total option risk over {MAX_TOTAL_OPTION_RISK_FRAC*100:.0f}% of equity:"
                " income scaled down"
            )

    # 4) Hedge premium — flag only (never reduce protection automatically).
    if equity > 0 and plan.hedge.total_cost > MAX_HEDGE_PREMIUM_FRAC * equity:
        violations.append(
            f"hedge premium {plan.hedge.total_cost/equity*100:.1f}% of equity over "
            f"{MAX_HEDGE_PREMIUM_FRAC*100:.0f}% cap (flagged, not reduced)"
        )

    new_income = _rebuild_income(plan.income, legs)
    safe_plan = _rebuild_strategy(plan, new_income)
    return PlanValidation(ok=not violations, violations=violations, plan=safe_plan)
