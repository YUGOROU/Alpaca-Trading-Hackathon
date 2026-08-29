"""One-command risk report — the whole engine's view of the book on one screen.

Runs OBSERVE -> MEASURE -> plan_strategy against live Alpaca data (when ALPACA_* creds
are set) or the offline mock, and prints:

  * the book (positions, weights, betas, SPY-equivalent exposure)
  * the risk snapshot (vol, drawdown, regime, VaR, IV rank, Risk Score, target coverage)
  * tail risk (parametric vs historical vs fat-tail VaR, and Expected Shortfall)
  * a stress table (hedged vs unhedged P&L under -3% / -5% / -10% shocks)
  * the strategy overlay (income + hedge + net carry + posture)

Read-only: it observes and prints, places nothing.

    python scripts/risk_report.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _load_env() -> None:
    try:
        from dotenv import load_dotenv

        load_dotenv(dotenv_path=Path(__file__).resolve().parent.parent / ".env")
    except Exception:
        pass


def _source_and_mode():
    if os.getenv("ALPACA_API_KEY") and os.getenv("ALPACA_SECRET_KEY"):
        from feed import AlpacaDataSource

        return AlpacaDataSource(), "LIVE (Alpaca paper)"
    from feed import MockDataSource

    return MockDataSource(), "MOCK (no ALPACA_* creds)"


def _rule(title: str) -> None:
    print(f"\n{'-' * 68}\n{title}\n{'-' * 68}")


def main() -> int:
    _load_env()
    from feed import StateStore, observe
    from risk_engine import assess, plan_strategy
    from risk_engine import metrics
    from risk_engine.book import current_weights
    from risk_engine.payoffs import stress_pnl

    source, mode = _source_and_mode()
    state = StateStore(os.getenv("AGENT_STATE_PATH", "state/state.json"))
    portfolio, market = observe(source, state, persist=False)

    snapshot = assess(portfolio, market)
    plan = plan_strategy(portfolio, market, snapshot)

    print(f"\n{'=' * 68}\n RISK REPORT  [{mode}]  index={market.index_symbol} @ ${market.index_price:,.2f}\n{'=' * 68}")

    # --- Book ---------------------------------------------------------------
    _rule("BOOK")
    weights = current_weights(portfolio)
    print(f"  {'sym':6s}{'shares':>9s}{'price':>10s}{'value':>12s}{'weight':>9s}{'beta':>7s}"
          f"{'$SPY-eq':>12s}")
    for p in sorted(portfolio.positions, key=lambda q: q.market_value, reverse=True):
        spy_eq = p.shares * p.price * p.beta
        print(f"  {p.symbol:6s}{p.shares:>9.0f}{p.price:>10.2f}{p.market_value:>12,.0f}"
              f"{weights.get(p.symbol, 0) * 100:>8.1f}%{p.beta:>7.2f}{spy_eq:>12,.0f}")
    print(f"  {'cash':6s}{'':>9s}{'':>10s}{portfolio.cash:>12,.0f}"
          f"{(portfolio.cash / portfolio.equity * 100 if portfolio.equity else 0):>8.1f}%")
    print(f"  equity = ${portfolio.equity:,.0f}   beta-weighted (SPY-equiv) exposure = "
          f"${snapshot.beta_weighted_delta:,.0f}")

    # --- Risk snapshot ------------------------------------------------------
    _rule("RISK SNAPSHOT")
    for line in snapshot.as_lines():
        print("  " + line)

    # --- Tail risk ----------------------------------------------------------
    _rule("TAIL RISK  (1-day loss, $)")
    eq = portfolio.equity
    rets = market.index_returns
    skew = metrics.skewness(rets)
    kurt = metrics.excess_kurtosis(rets)
    hist95 = metrics.historical_var(eq, rets, 0.05)
    cf99 = metrics.cornish_fisher_var(eq, snapshot.daily_vol, skew, kurt, metrics.Z_99)
    es95 = metrics.expected_shortfall(eq, rets, 0.05)
    print(f"  parametric 95% / 99%   = ${snapshot.var_95:,.0f} / ${snapshot.var_99:,.0f}")
    print(f"  historical 95%         = ${hist95:,.0f}   (empirical quantile of returns)")
    print(f"  fat-tail 99% (C-F)     = ${cf99:,.0f}   (skew {skew:+.2f}, excess-kurt {kurt:+.2f})")
    print(f"  expected shortfall 95% = ${es95:,.0f}   (avg loss beyond the 95% VaR)")

    # --- Stress test --------------------------------------------------------
    _rule("STRESS TEST  (index shock: unhedged vs at target hedge)")
    contracts = plan.hedge.contracts_target
    put_delta = plan.hedge.put_delta
    cov = snapshot.target_coverage
    print(f"  target coverage {cov * 100:.0f}%  ({contracts} put(s), delta {put_delta:+.2f})")
    print(f"  {'shock':>7s}{'unhedged':>14s}{'hedged':>14s}{'cushion':>12s}")
    for shock in (-0.03, -0.05, -0.10):
        s = stress_pnl(snapshot.beta_weighted_delta, shock, market.index_price,
                       hedge_contracts=contracts, put_delta=put_delta)
        cushion = s.net_pnl - s.unhedged_pnl
        print(f"  {shock * 100:>6.0f}%{s.unhedged_pnl:>14,.0f}{s.net_pnl:>14,.0f}{cushion:>+12,.0f}")

    # --- Strategy overlay ---------------------------------------------------
    _rule("STRATEGY OVERLAY")
    print(f"  POSTURE = {plan.posture}")
    if plan.income.legs:
        for line in plan.income.as_lines():
            print("  " + line)
    else:
        print("  income: none (premium not rich vs realized, or risk-off)")
    print("  " + " | ".join([
        f"hedge {plan.hedge.action} x{plan.hedge.contracts_target}",
        f"cost ${plan.hedge.total_cost:,.0f} (drag {plan.hedge.hedge_cost_drag * 100:.2f}%)",
    ]))
    for line in plan.as_lines():
        print("  " + line)
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
