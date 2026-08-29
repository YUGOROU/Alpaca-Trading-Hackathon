"""Top-level engine: portfolio + market -> RiskSnapshot -> HedgePlan (README §7).

`assess` measures risk and sets a target coverage. `plan_hedge` turns that target into
a concrete protective-put order (strike, contracts, cost). Neither touches a broker or
an LLM — they're the deterministic core the rest of the agent is built on.
"""
from __future__ import annotations

from dataclasses import dataclass

from . import blackscholes as bs
from . import metrics, scoring
from .income import IncomePlan, plan_income
from .types import HedgePlan, MarketData, Portfolio, RiskSnapshot


def assess(portfolio: Portfolio, market: MarketData) -> RiskSnapshot:
    """Compute the full risk snapshot for one cycle."""
    equity = portfolio.equity
    peak = portfolio.peak_equity if portfolio.peak_equity is not None else equity

    daily_vol = metrics.ewma_daily_vol(market.index_returns)
    annual_vol = metrics.annualize_vol(daily_vol)
    drawdown = metrics.drawdown_from_peak(equity, peak)
    regime = metrics.regime_signal(market.index_price, market.index_ma50)
    var_95 = metrics.parametric_var(equity, daily_vol, metrics.Z_95)
    var_99 = metrics.parametric_var(equity, daily_vol, metrics.Z_99)
    bwd = metrics.beta_weighted_delta(portfolio.positions)
    ivr = metrics.iv_rank(market.index_iv, market.iv_year_low, market.iv_year_high)
    exp_move = metrics.expected_move(market.index_price, market.index_iv, 30)

    var_pct = var_95 / equity if equity else 0.0
    score = scoring.risk_score(drawdown, annual_vol, regime, var_pct)
    coverage = scoring.target_coverage(score)

    return RiskSnapshot(
        equity=equity,
        beta_weighted_delta=bwd,
        daily_vol=daily_vol,
        annual_vol=annual_vol,
        drawdown=drawdown,
        regime_signal=regime,
        var_95=var_95,
        var_99=var_99,
        iv_rank=ivr,
        expected_move_30d=exp_move,
        risk_score=score,
        target_coverage=coverage,
    )


def plan_hedge(
    portfolio: Portfolio,
    market: MarketData,
    snapshot: RiskSnapshot,
    current_contracts: int = 0,
    expiry_days: int = 7,
    target_put_delta: float = 0.35,
    strike_pct_otm: float | None = None,
) -> HedgePlan:
    """Size a protective-put hedge to reach the snapshot's target coverage.

    Tuned for a short window: buys a *responsive* ~`target_put_delta` put (not a deep-OTM
    tail lottery that barely moves in 5 days) at a short `expiry_days` (less time-value
    drag, still alive across the window). Pass `strike_pct_otm` (e.g. 0.05) to fix a
    %-OTM strike instead (§7.3, §7.6).
    """
    S = market.index_price
    T = expiry_days / 365.0
    sigma = market.index_iv
    r = market.risk_free_rate

    if strike_pct_otm is not None:
        strike = round(S * (1.0 - strike_pct_otm))
    else:
        strike = round(bs.strike_for_put_delta(S, target_put_delta, T, r, sigma))

    p_delta = bs.put_delta(S, strike, T, r, sigma)
    premium_share = bs.put_price(S, strike, T, r, sigma)
    theta_day_contract = bs.put_theta_per_day(S, strike, T, r, sigma) * 100.0

    # Portfolio exposure expressed in index-equivalent shares, then in contracts
    # needed to fully neutralize it (§7.3): contracts = delta_shares / (|put delta|*100).
    port_delta_shares = snapshot.beta_weighted_delta / S
    full_contracts = (
        port_delta_shares / (abs(p_delta) * 100.0) if p_delta else 0.0
    )
    contracts_target = round(snapshot.target_coverage * full_contracts)
    contracts_delta = contracts_target - current_contracts

    if contracts_delta > 0:
        action = "increase"
    elif contracts_delta < 0:
        action = "reduce"
    else:
        action = "hold"

    premium_contract = premium_share * 100.0
    total_cost = contracts_target * premium_contract
    drag = total_cost / portfolio.equity if portfolio.equity else 0.0
    current_cov = current_contracts / full_contracts if full_contracts else 0.0

    return HedgePlan(
        action=action,
        target_coverage=snapshot.target_coverage,
        current_coverage=metrics.clip(current_cov, 0.0, 1.0),
        put_strike=float(strike),
        put_expiry_days=expiry_days,
        put_delta=p_delta,
        contracts_target=contracts_target,
        contracts_delta=contracts_delta,
        premium_per_contract=premium_contract,
        total_cost=total_cost,
        hedge_cost_drag=drag,
        theta_per_day=theta_day_contract,
        full_hedge_contracts=full_contracts,
    )


@dataclass(frozen=True)
class StrategyPlan:
    """The unified per-cycle decision: income overlay + hedge overlay + net carry.

    This is what the LLM/agent presents and the executor acts on — the income legs
    generate P&L, the hedge caps the tail, and `net_theta_per_day` / `net_cost_today`
    show whether the book is being *paid* to hold its posture (positive carry) or is
    paying up for protection (a stressed cycle).
    """

    posture: str                 # short human summary of the cycle's stance
    income: IncomePlan
    hedge: HedgePlan
    net_theta_per_day: float     # income decay collected − hedge decay paid ($/day)
    net_cost_today: float        # hedge premium spent − income credit collected ($)

    def as_lines(self) -> list[str]:
        carry = "positive carry" if self.net_theta_per_day >= 0 else "negative carry"
        return [
            f"POSTURE           = {self.posture}",
            f"net_theta/day     = {self.net_theta_per_day:+,.0f}/day  ({carry})",
            f"net_cost_today    = ${self.net_cost_today:+,.0f}   "
            f"(credit ${self.income.total_credit:,.0f} - hedge ${self.hedge.total_cost:,.0f})",
        ]

    def to_dict(self) -> dict:
        """JSON-serializable view of the whole per-cycle decision (the DECIDE input)."""
        return {
            "posture": self.posture,
            "net_theta_per_day": round(self.net_theta_per_day, 2),
            "net_cost_today": round(self.net_cost_today, 2),
            "income": self.income.to_dict(),
            "hedge": self.hedge.to_dict(),
        }


def _posture(snapshot: RiskSnapshot, income: IncomePlan, hedge: HedgePlan) -> str:
    """One-line stance for the cycle, from which overlay is doing the work."""
    harvesting = income.total_credit > 0.0
    hedging = hedge.contracts_target > 0
    if hedging and harvesting:
        return "HARVEST + HEDGE (rich IV, rising risk)"
    if hedging:
        return "DEFEND (risk-off - hedge stepped in, income stood down)"
    if harvesting:
        return "HARVEST (rich IV, calm - sell premium, minimal hedge)"
    return "SIT (cheap IV, calm - nothing to sell, nothing to fear)"


def plan_strategy(
    portfolio: Portfolio,
    market: MarketData,
    snapshot: RiskSnapshot,
    current_contracts: int = 0,
    income_dte: int = 5,
    hedge_dte: int = 7,
) -> StrategyPlan:
    """Combine the income overlay and the hedge overlay into one decision (§3, §7).

    Income sells weekly (`income_dte`) to harvest fast time decay; the hedge uses a
    longer-dated put (`hedge_dte`) so protection stays alive across the window.
    """
    income = plan_income(portfolio, market, snapshot, expiry_days=income_dte)
    hedge = plan_hedge(
        portfolio, market, snapshot,
        current_contracts=current_contracts, expiry_days=hedge_dte,
    )
    hedge_theta_total = hedge.theta_per_day * hedge.contracts_target  # negative (cost)
    net_theta = income.net_theta_per_day + hedge_theta_total
    net_cost = hedge.total_cost - income.total_credit
    return StrategyPlan(
        posture=_posture(snapshot, income, hedge),
        income=income,
        hedge=hedge,
        net_theta_per_day=net_theta,
        net_cost_today=net_cost,
    )
