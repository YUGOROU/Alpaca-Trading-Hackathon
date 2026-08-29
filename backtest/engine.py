"""Replay the strategy through a historical price tape (README §8).

Alpaca has no option-IV history, so we backtest the *signals + decisions* on historical
price/vol and *approximate the option payoffs* at expiry (Black-Scholes at open for the
credit/cost via the engine; intrinsic value at expiry for the P&L). Each weekly cycle:
open the risk-validated income + hedge, hold to expiry, realize P&L, roll. We compare the
strategy against plain buy-and-hold on risk-adjusted metrics (max drawdown, vol, Sharpe)
— where a hedged/income agent earns its keep even when raw return is similar.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from risk_engine import MarketData, Portfolio, Position, assess, plan_strategy, validate_plan
from risk_engine import metrics


# --- expiry payoffs (intrinsic value; premiums already collected/paid at open) ---

def _income_leg_expiry_pnl(leg, S_T: float) -> float:
    n = leg.contracts
    if n <= 0:
        return 0.0
    if leg.kind == "covered_call":
        # keep the credit; give up stock gains above the call strike (overlay on the book)
        return leg.credit - max(0.0, S_T - leg.short_strike) * 100.0 * n
    if leg.kind == "iron_condor":
        put_loss = max(0.0, leg.short_strike - S_T) - max(0.0, leg.long_strike - S_T)
        call_loss = max(0.0, S_T - leg.call_short_strike) - max(0.0, S_T - leg.call_long_strike)
        return leg.credit - (put_loss + call_loss) * 100.0 * n
    return leg.credit  # single/other structures: approximate by the credit


def _hedge_expiry_pnl(hedge, S_T: float) -> float:
    n = hedge.contracts_target
    if n <= 0:
        return 0.0
    payoff = max(0.0, hedge.put_strike - S_T) * 100.0 * n
    return payoff - hedge.total_cost


# --- metrics on an equity curve ---

def _max_drawdown(curve: list[float]) -> float:
    peak, mdd = curve[0], 0.0
    for v in curve:
        peak = max(peak, v)
        mdd = max(mdd, (peak - v) / peak if peak else 0.0)
    return mdd


def _sharpe_and_vol(curve: list[float], periods_per_year: float) -> tuple[float, float]:
    rets = metrics.simple_returns(curve)
    if len(rets) < 2:
        return 0.0, 0.0
    mean = metrics.mean(rets)
    sd = math.sqrt(metrics.sample_variance(rets))
    vol = sd * math.sqrt(periods_per_year)
    sharpe = (mean / sd) * math.sqrt(periods_per_year) if sd else 0.0
    return sharpe, vol


@dataclass
class BacktestResult:
    strategy_curve: list[float]
    unhedged_curve: list[float]
    cycles: int
    income_collected: float
    hedge_spent: float
    hedged_cycles: int
    periods_per_year: float
    records: list[dict] = field(default_factory=list)

    def _metrics(self, curve: list[float]) -> dict:
        sharpe, vol = _sharpe_and_vol(curve, self.periods_per_year)
        return {
            "total_return": curve[-1] / curve[0] - 1.0,
            "max_drawdown": _max_drawdown(curve),
            "annual_vol": vol,
            "sharpe": sharpe,
            "final_equity": curve[-1],
        }

    def strategy_metrics(self) -> dict:
        return self._metrics(self.strategy_curve)

    def unhedged_metrics(self) -> dict:
        return self._metrics(self.unhedged_curve)

    def as_lines(self) -> list[str]:
        s, u = self.strategy_metrics(), self.unhedged_metrics()
        return [
            f"cycles            = {self.cycles}  ({self.hedged_cycles} hedged)",
            f"income_collected  = ${self.income_collected:,.0f}   hedge_spent ${self.hedge_spent:,.0f}",
            "                     STRATEGY      BUY&HOLD",
            f"total_return      = {s['total_return']*100:+7.1f}%     {u['total_return']*100:+7.1f}%",
            f"max_drawdown      = {s['max_drawdown']*100:7.1f}%     {u['max_drawdown']*100:7.1f}%",
            f"annual_vol        = {s['annual_vol']*100:7.1f}%     {u['annual_vol']*100:7.1f}%",
            f"sharpe            = {s['sharpe']:7.2f}      {u['sharpe']:7.2f}",
            f"final_equity      = ${s['final_equity']:,.0f}   ${u['final_equity']:,.0f}",
        ]

    def to_dict(self) -> dict:
        return {
            "cycles": self.cycles,
            "hedged_cycles": self.hedged_cycles,
            "income_collected": round(self.income_collected, 2),
            "hedge_spent": round(self.hedge_spent, 2),
            "strategy": {k: round(v, 4) for k, v in self.strategy_metrics().items()},
            "unhedged": {k: round(v, 4) for k, v in self.unhedged_metrics().items()},
            "strategy_curve": [round(v, 2) for v in self.strategy_curve],
            "unhedged_curve": [round(v, 2) for v in self.unhedged_curve],
        }


def run_backtest(
    closes: list[float],
    *,
    start_equity: float = 100_000.0,
    cycle_days: int = 5,
    lookback: int = 30,
    iv_premium: float = 1.20,
    invested_fraction: float = 0.85,
) -> BacktestResult:
    """Walk the tape in weekly cycles; return both equity curves + metrics.

    `iv_premium` turns realized vol into an implied-vol estimate (implied usually runs
    above realized — that gap is the premium we harvest). `invested_fraction` is how much
    of equity is in the book (rest is the cash sleeve).
    """
    strat_eq = start_equity
    unhedged_eq = start_equity
    strat_curve = [strat_eq]
    unhedged_curve = [unhedged_eq]
    strat_peak = start_equity
    records: list[dict] = []
    income_total = hedge_total = 0.0
    hedged_cycles = 0

    iv_hist: list[float] = []
    start_i = max(50, lookback)
    i = start_i
    while i + cycle_days < len(closes):
        S0 = closes[i]
        window = closes[i - lookback : i + 1]
        rets = metrics.simple_returns(window)
        realized = metrics.annualize_vol(metrics.ewma_daily_vol(rets))
        iv_est = max(0.05, realized * iv_premium)
        iv_hist.append(iv_est)
        if len(iv_hist) >= 5:
            iv_lo, iv_hi = min(iv_hist), max(iv_hist)
        else:
            iv_lo, iv_hi = iv_est * 0.7, iv_est * 1.3
        ma50 = sum(closes[i - 49 : i + 1]) / 50.0

        invested = invested_fraction * strat_eq
        portfolio = Portfolio(
            positions=[Position("SPY", shares=invested / S0, price=S0, beta=1.0)],
            cash=strat_eq - invested,
            peak_equity=strat_peak,
        )
        market = MarketData(
            index_symbol="SPY", index_price=S0, index_ma50=ma50,
            index_returns=rets, index_iv=iv_est, iv_year_low=iv_lo, iv_year_high=iv_hi,
        )

        snap = assess(portfolio, market)
        plan = validate_plan(
            plan_strategy(portfolio, market, snap, income_dte=cycle_days, hedge_dte=cycle_days * 2),
            strat_eq,
        ).plan

        S_T = closes[i + cycle_days]
        book_return = S_T / S0 - 1.0

        overlay = sum(_income_leg_expiry_pnl(l, S_T) for l in plan.income.legs)
        overlay += _hedge_expiry_pnl(plan.hedge, S_T)
        income_total += plan.income.total_credit
        hedge_total += plan.hedge.total_cost
        if plan.hedge.contracts_target > 0:
            hedged_cycles += 1

        # strategy holds the book AND the option overlay; buy&hold holds only the book
        strat_eq += invested * book_return + overlay
        unhedged_eq += (invested_fraction * unhedged_eq) * book_return
        strat_peak = max(strat_peak, strat_eq)

        strat_curve.append(strat_eq)
        unhedged_curve.append(unhedged_eq)
        records.append({
            "i": i, "S0": S0, "S_T": S_T, "posture": plan.posture,
            "risk_score": round(snap.risk_score, 1),
            "overlay_pnl": round(overlay, 0), "strat_eq": round(strat_eq, 0),
        })
        i += cycle_days

    return BacktestResult(
        strategy_curve=strat_curve,
        unhedged_curve=unhedged_curve,
        cycles=len(records),
        income_collected=income_total,
        hedge_spent=hedge_total,
        hedged_cycles=hedged_cycles,
        periods_per_year=252.0 / cycle_days,
        records=records,
    )
