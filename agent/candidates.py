"""Generate several safe, deterministic choices instead of one final posture."""

from __future__ import annotations

from dataclasses import asdict, replace
from hashlib import sha256
import json
from math import floor

from risk_engine import IncomePlan, MarketData, Portfolio, StrategyPlan, assess, plan_hedge, plan_income
from risk_engine.payoffs import stress_pnl

from .contracts import CandidateTradeoffs, DecisionCandidate, DecisionContext
from .limits import MAX_HEDGE_COST_DRAG


def _empty_income() -> IncomePlan:
    return IncomePlan(
        legs=[],
        total_credit=0.0,
        total_max_loss=0.0,
        capital_reserved=0.0,
        net_theta_per_day=0.0,
        aggressiveness=0.0,
        annualized_yield=0.0,
    )


def _strategy(posture: str, income: IncomePlan, hedge) -> StrategyPlan:
    hedge_theta = hedge.theta_per_day * hedge.contracts_target
    return StrategyPlan(
        posture=posture,
        income=income,
        hedge=hedge,
        net_theta_per_day=income.net_theta_per_day + hedge_theta,
        net_cost_today=hedge.total_cost - income.total_credit,
    )


def _candidate(
    candidate_id: str,
    action: str,
    label: str,
    thesis: str,
    plan: StrategyPlan,
    snapshot,
    market: MarketData,
) -> DecisionCandidate:
    stress = stress_pnl(
        snapshot.beta_weighted_delta,
        -0.05,
        market.index_price,
        hedge_contracts=plan.hedge.contracts_target,
        put_delta=plan.hedge.put_delta,
    )
    return DecisionCandidate(
        candidate_id=candidate_id,
        action=action,
        label=label,
        thesis=thesis,
        tradeoffs=CandidateTradeoffs(
            target_coverage=plan.hedge.target_coverage,
            cashflow_today=-plan.net_cost_today,
            daily_theta=plan.net_theta_per_day,
            capital_reserved=plan.income.capital_reserved,
            defined_risk=plan.income.total_max_loss,
            linear_hedge_adjusted_pnl_5pct=stress.net_pnl,
        ),
        hedge_symbol=market.index_symbol,
        plan=plan,
    )


def _context_id(scenario_id: str, snapshot, candidate_ids: list[str], execution_mode: str) -> str:
    payload = json.dumps(
        {
            "scenario_id": scenario_id,
            "snapshot": asdict(snapshot),
            "candidate_ids": candidate_ids,
            "execution_mode": execution_mode,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return sha256(payload.encode()).hexdigest()[:20]


def build_decision_context(
    portfolio: Portfolio,
    market: MarketData,
    *,
    scenario_id: str = "live",
    current_contracts: int = 0,
    income_open: bool = False,
    expiry_days: int = 4,
    input_provenance: dict | None = None,
    execution_mode: str = "human",
) -> DecisionContext:
    """Create only choices that pass the coarse deterministic risk envelope.

    Calm/elevated regimes expose hold and, when genuinely available, income.
    Medium/high-risk regimes expose partial/full protection. A high-risk context
    deliberately does not expose an unhedged hold or short-premium choice.

    `income_open` (an overlay already on from a prior cycle) suppresses the
    harvest_income choice, so a periodic loop never stacks a fresh condor every tick.
    """
    if execution_mode not in {"human", "autonomous-paper"}:
        raise ValueError("unknown execution mode")
    snapshot = assess(portfolio, market)
    candidates: list[DecisionCandidate] = []

    if snapshot.risk_score < 75.0:
        hold_snapshot = replace(snapshot, target_coverage=0.0)
        hold_hedge = plan_hedge(
            portfolio,
            market,
            hold_snapshot,
            current_contracts=current_contracts,
            expiry_days=expiry_days,
        )
        hold_plan = _strategy("HOLD", _empty_income(), hold_hedge)
        candidates.append(_candidate(
            "hold",
            "hold",
            "Hold current posture",
            "Keep optionality and avoid transaction or premium drag.",
            hold_plan,
            snapshot,
            market,
        ))

    if snapshot.risk_score < 40.0 and not income_open:
        income = plan_income(portfolio, market, snapshot, expiry_days=expiry_days)
        if income.legs:
            income_snapshot = replace(snapshot, target_coverage=0.0)
            income_hedge = plan_hedge(
                portfolio,
                market,
                income_snapshot,
                current_contracts=current_contracts,
                expiry_days=expiry_days,
            )
            income_plan = _strategy("HARVEST", income, income_hedge)
            candidates.append(_candidate(
                "harvest_income",
                "add_income_overlay",
                "Harvest defined-risk premium",
                "Collect rich premium while the deterministic regime gate remains risk-on.",
                income_plan,
                snapshot,
                market,
            ))

    if snapshot.risk_score >= 25.0:
        partial_coverage = min(0.50, max(0.25, snapshot.target_coverage))
        partial_snapshot = replace(snapshot, target_coverage=partial_coverage)
        partial_hedge = plan_hedge(
            portfolio,
            market,
            partial_snapshot,
            current_contracts=current_contracts,
            expiry_days=expiry_days,
        )
        partial_plan = _strategy("PARTIAL DEFEND", _empty_income(), partial_hedge)
        candidates.append(_candidate(
            "partial_hedge",
            "add_hedge",
            "Add partial protection",
            "Reduce tail exposure while limiting premium drag.",
            partial_plan,
            snapshot,
            market,
        ))

    if snapshot.risk_score >= 40.0:
        full_coverage = max(0.50, snapshot.target_coverage)
        full_snapshot = replace(snapshot, target_coverage=full_coverage)
        full_hedge = plan_hedge(
            portfolio,
            market,
            full_snapshot,
            current_contracts=current_contracts,
            expiry_days=expiry_days,
        )
        candidate_id = "full_hedge"
        label = "Defend at the risk-engine target"
        thesis = "Prioritize drawdown control under elevated deterministic risk."
        if full_hedge.hedge_cost_drag > MAX_HEDGE_COST_DRAG:
            max_contracts = floor(
                portfolio.equity * MAX_HEDGE_COST_DRAG / full_hedge.premium_per_contract
            )
            capped_coverage = min(
                full_coverage,
                max_contracts / full_hedge.full_hedge_contracts
                if full_hedge.full_hedge_contracts else 0.0,
            )
            capped_snapshot = replace(snapshot, target_coverage=capped_coverage)
            full_hedge = plan_hedge(
                portfolio,
                market,
                capped_snapshot,
                current_contracts=current_contracts,
                expiry_days=expiry_days,
            )
            candidate_id = "cost_capped_hedge"
            label = "Defend up to the hedge-cost cap"
            thesis = "Maximize protection without breaching the deterministic premium budget."
        full_plan = _strategy("DEFEND", _empty_income(), full_hedge)
        if full_hedge.contracts_target > partial_hedge.contracts_target:
            candidates.append(_candidate(
                candidate_id,
                "add_hedge",
                label,
                thesis,
                full_plan,
                snapshot,
                market,
            ))

    if not candidates:  # Defensive fallback for unusual future scoring changes.
        raise RuntimeError("candidate generation produced no admissible action")

    ids = [candidate.candidate_id for candidate in candidates]
    # A live context must bind the provenance timestamps as well as the risk
    # snapshot.  Rebuilding from the persisted inputs therefore retains the
    # same ID, while a newly observed snapshot gets a distinct decision ID.
    context_id = _context_id(scenario_id, snapshot, ids, execution_mode)
    if input_provenance is not None:
        provenance_bytes = json.dumps(input_provenance, sort_keys=True, separators=(",", ":"))
        context_id = sha256(f"{context_id}:{provenance_bytes}".encode()).hexdigest()[:20]
    return DecisionContext(
        context_id=context_id,
        scenario_id=scenario_id,
        snapshot=snapshot,
        candidates=tuple(candidates),
        input_provenance=input_provenance,
        execution_mode=execution_mode,
    )
