"""Generate several safe, deterministic choices instead of one final posture."""

from __future__ import annotations

from dataclasses import asdict, replace
from hashlib import sha256
import json
from math import floor

from risk_engine import IncomePlan, MarketData, Portfolio, StrategyPlan, assess, plan_hedge, plan_income
from risk_engine.income import IncomeLeg
from risk_engine.payoffs import stress_pnl

from .contracts import CandidateTradeoffs, DecisionCandidate, DecisionContext
from .limits import MAX_DEFINED_RISK_FRACTION, MAX_HEDGE_COST_DRAG

# This is deliberately a fixed, reviewable universe -- never a model supplied
# symbol.  All names are US large-cap stocks or broad/sector ETFs with listed
# options.  Adding a name is a code review/deploy decision, not a prompt change.
AUTONOMOUS_OPTION_UNDERLYINGS = (
    "SPY", "QQQ", "IWM", "DIA", "XLK", "XLF", "XLV", "SMH",
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "AMD", "TSLA",
)
AUTONOMOUS_COVERED_CALL_SYMBOLS = AUTONOMOUS_OPTION_UNDERLYINGS
# The deployed paper overlay can be intentionally more responsive than the
# human-review profile, but its total protection is still capped at three quarters of the
# beta-weighted book.  This is a position target, not a daily order quota: the
# planner subtracts existing verified protective-put contracts before emitting
# another single-leg order.
AUTONOMOUS_AGGRESSIVE_HEDGE_COVERAGE = 0.75

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


def _preserved_hedge(
    portfolio: Portfolio,
    market: MarketData,
    snapshot,
    *,
    current_contracts: int,
    expiry_days: int,
):
    """Keep verified protective puts out of an income-opening candidate.

    An autonomous candidate is limited to one opening order.  Planning income
    against a zero-coverage snapshot would otherwise turn existing protective
    puts into an implicit ``sell_to_close`` order, producing a two-order
    candidate (and, before close execution is armed, a rejected one).  The
    existing hedge is instead represented as held inventory with no adjustment.
    """
    hold_snapshot = replace(snapshot, target_coverage=0.0)
    hedge = plan_hedge(
        portfolio, market, hold_snapshot, current_contracts=current_contracts,
        expiry_days=expiry_days,
    )
    if current_contracts <= 0:
        return hedge
    return replace(
        hedge,
        action="hold",
        target_coverage=hedge.current_coverage,
        contracts_target=current_contracts,
        contracts_delta=0,
    )


def _one_leg_income(leg: IncomeLeg, *, equity: float, aggressiveness: float) -> IncomePlan:
    """Return one executable option structure, never a portfolio-sized batch."""
    annualized_yield = (
        (leg.credit / equity) * (365.0 / leg.expiry_days)
        if equity and leg.expiry_days else 0.0
    )
    return IncomePlan(
        legs=[leg], total_credit=leg.credit, total_max_loss=leg.max_loss,
        capital_reserved=leg.capital_reserved, net_theta_per_day=leg.theta_per_day,
        aggressiveness=aggressiveness, annualized_yield=annualized_yield,
    )


def _scaled_leg(leg: IncomeLeg, contracts: int) -> IncomeLeg:
    """Scale a pre-admissible leg without changing its strikes or risk geometry."""
    if contracts <= 0:
        raise ValueError("contracts must be positive")
    ratio = contracts / leg.contracts
    return replace(
        leg,
        contracts=contracts,
        credit=leg.credit * ratio,
        max_loss=leg.max_loss * ratio,
        capital_reserved=leg.capital_reserved * ratio,
        theta_per_day=leg.theta_per_day * ratio,
    )


def _directional_spreads(condor: IncomeLeg, *, equity: float) -> tuple[IncomeLeg, IncomeLeg]:
    """Split a defined-risk condor into conservative bullish/bearish alternatives.

    Risk is deliberately budgeted at the full spread width (ignoring received
    credit), so the candidate cannot rely on theoretical credit to pass the cap.
    """
    if condor.kind != "iron_condor" or condor.long_strike is None or condor.call_short_strike is None or condor.call_long_strike is None:
        raise ValueError("directional spreads require a complete iron condor")
    candidates = []
    for kind, short, long in (
        ("bull_put_spread", condor.short_strike, condor.long_strike),
        ("bear_call_spread", condor.call_short_strike, condor.call_long_strike),
    ):
        width = abs(float(short) - float(long))
        max_contracts = int(floor((equity * MAX_DEFINED_RISK_FRACTION) / (width * 100.0)))
        contracts = min(condor.contracts, max_contracts)
        if contracts <= 0:
            continue
        ratio = contracts / condor.contracts
        candidates.append(IncomeLeg(
            kind=kind, symbol=condor.symbol, short_strike=float(short), long_strike=float(long),
            expiry_days=condor.expiry_days, short_delta=condor.short_delta, contracts=contracts,
            credit=max(0.0, condor.credit * 0.5 * ratio),
            max_loss=width * 100.0 * contracts,
            capital_reserved=width * 100.0 * contracts,
            theta_per_day=condor.theta_per_day * 0.5 * ratio,
            note="defined-risk directional credit spread; risk budget ignores theoretical credit",
        ))
    return tuple(candidates)


def _autonomous_income_candidates(
    *, portfolio: Portfolio, snapshot, market: MarketData,
    income_markets: dict[str, MarketData] | None,
    current_contracts: int, expiry_days: int,
) -> list[tuple[str, str, str, StrategyPlan]]:
    """Generate bounded one-order choices across the reviewed option universe.

    The model chooses an already-sized candidate (including conservative/standard
    size variants); it never sends a symbol, strike, direction, or quantity to the
    broker.  Each candidate remains subject to the final defined-risk gate and
    fresh executable-price checks.
    """
    markets = {market.index_symbol: market}
    markets.update(income_markets or {})
    choices: list[tuple[str, str, str, StrategyPlan]] = []
    seen: set[tuple[str, str, int]] = set()

    # Covered calls are permitted only where the live portfolio already supplies
    # the 100-share coverage; plan_income computes that capacity.
    covered = plan_income(portfolio, market, snapshot, expiry_days=expiry_days)
    source_legs = [
        leg for leg in covered.legs
        if leg.kind == "covered_call" and leg.symbol in AUTONOMOUS_COVERED_CALL_SYMBOLS
    ]
    preferred_covered = source_legs[0] if source_legs else None

    # A defined-risk condor may be proposed for every whitelist member whose
    # current bars/spot/IV were successfully observed.
    empty_portfolio = Portfolio(positions=[], cash=portfolio.equity, peak_equity=portfolio.equity)
    for symbol in AUTONOMOUS_OPTION_UNDERLYINGS:
        option_market = markets.get(symbol)
        if option_market is None:
            continue
        income = plan_income(empty_portfolio, option_market, snapshot, expiry_days=expiry_days)
        source_legs.extend(
            leg for leg in income.legs
            if leg.kind == "iron_condor" and leg.symbol == symbol
        )
        for condor in [leg for leg in income.legs if leg.kind == "iron_condor" and leg.symbol == symbol]:
            source_legs.extend(_directional_spreads(condor, equity=portfolio.equity))

    for leg in source_legs:
        # Give the agent a real sizing choice while retaining a bounded,
        # deterministic order.  Duplicated one-contract variants are omitted.
        for fraction, label in ((1.0, "standard"), (0.5, "conservative")):
            contracts = max(1, int(floor(leg.contracts * fraction)))
            key = (leg.kind, leg.symbol, contracts)
            if key in seen:
                continue
            seen.add(key)
            sized = _scaled_leg(leg, contracts)
            income = _one_leg_income(sized, equity=portfolio.equity, aggressiveness=covered.aggressiveness)
            hedge = _preserved_hedge(
                portfolio, market, snapshot,
                current_contracts=current_contracts, expiry_days=expiry_days,
            )
            plan = _strategy("HARVEST", income, hedge)
            candidate_id = "harvest_income" if label == "standard" and (
                leg == preferred_covered or (
                    preferred_covered is None
                    and leg.kind == "iron_condor"
                    and leg.symbol == market.index_symbol
                )
            ) else (
                f"harvest_{leg.symbol.lower()}_{leg.kind}_{label}"
            )
            choices.append((
                candidate_id,
                f"Open {label} {leg.kind.replace('_', ' ')} on {leg.symbol}",
                f"Use the {label} risk-budget allocation for a defined one-order {leg.kind} on {leg.symbol}.",
                plan,
            ))
    return choices


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
    selection = None
    if len(plan.income.legs) == 1:
        leg = plan.income.legs[0]
        selection = {
            "underlying": leg.symbol,
            "strategy": leg.kind,
            "direction": {
                "iron_condor": "neutral",
                "covered_call": "short_call_covered",
                "bull_put_spread": "bullish",
                "bear_call_spread": "bearish",
            }.get(leg.kind, "bounded"),
            "contracts": leg.contracts,
        }
    elif plan.hedge.contracts_delta > 0:
        selection = {
            "underlying": market.index_symbol,
            "strategy": "protective_put",
            "direction": "bearish_protection",
            "contracts": plan.hedge.contracts_delta,
        }
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
        selection=selection,
    )


def _context_id(
    scenario_id: str, snapshot, candidate_ids: list[str], execution_mode: str,
    income_markets: dict[str, MarketData] | None = None,
) -> str:
    payload = json.dumps(
        {
            "scenario_id": scenario_id,
            "snapshot": asdict(snapshot),
            "candidate_ids": candidate_ids,
            "execution_mode": execution_mode,
            "income_markets": {
                symbol: asdict(option_market)
                for symbol, option_market in sorted((income_markets or {}).items())
            },
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
    income_markets: dict[str, MarketData] | None = None,
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
        if execution_mode == "autonomous-paper":
            for candidate_id, label, thesis, income_plan in _autonomous_income_candidates(
                portfolio=portfolio, snapshot=snapshot, market=market,
                income_markets=income_markets, current_contracts=current_contracts,
                expiry_days=expiry_days,
            ):
                candidates.append(_candidate(
                    candidate_id,
                    "add_income_overlay",
                    label,
                    thesis,
                    income_plan,
                    snapshot,
                    market,
                ))
        elif income.legs:
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
        partial_coverage = (
            AUTONOMOUS_AGGRESSIVE_HEDGE_COVERAGE
            if execution_mode == "autonomous-paper"
            else min(0.50, max(0.25, snapshot.target_coverage))
        )
        partial_snapshot = replace(snapshot, target_coverage=partial_coverage)
        partial_hedge = plan_hedge(
            portfolio,
            market,
            partial_snapshot,
            current_contracts=current_contracts,
            expiry_days=expiry_days,
        )
        partial_plan = _strategy("PARTIAL DEFEND", _empty_income(), partial_hedge)
        # Do not ask the autonomous model to select an add-hedge candidate
        # which cannot place an order.  Human review retains its full planning
        # view, including an already-satisfied target.
        if execution_mode == "human" or partial_hedge.contracts_delta != 0:
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
        if (
            full_hedge.contracts_target > partial_hedge.contracts_target
            and (execution_mode == "human" or full_hedge.contracts_delta != 0)
        ):
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
    context_id = _context_id(
        scenario_id, snapshot, ids, execution_mode, income_markets=income_markets,
    )
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
