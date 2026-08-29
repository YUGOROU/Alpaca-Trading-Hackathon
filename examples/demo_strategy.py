"""Demo + smoke test for the HYBRID strategy: income core + hedge overlay.

Runs one base book through THREE market regimes and prints the unified strategy plan
(income legs + hedge + net carry) for each. Shows the whole thesis in one screen:

    CALM      premium rich vs realized -> HARVEST  (positive VRP, calm — sell premium)
    ELEVATED  richer IV, still risk-on   -> HARVEST  (bigger VRP — the P&L engine scales up)
    STRESSED  risk-off + drawdown        -> DEFEND   (income stands down, hedge steps in)

    Income now fires on the variance risk premium (implied richer than realized), not an
    IV-Rank floor — so it harvests whenever premium is genuinely overpriced, and stands
    down only when the regime turns risk-off.

    python examples/demo_strategy.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # repo root on sys.path

from risk_engine import (
    MarketData,
    Portfolio,
    Position,
    assess,
    plan_strategy,
)


def base_book(spy_price: float, qqq_price: float, peak: float, cash: float) -> Portfolio:
    # Deliberately larger than the hedge demo's book so covered-call capacity exists.
    return Portfolio(
        positions=[
            Position("SPY", shares=300, price=spy_price, beta=1.0),
            Position("QQQ", shares=200, price=qqq_price, beta=1.1),
        ],
        cash=cash,
        peak_equity=peak,
    )


# Quiet market, IV near its 1-yr low -> nothing worth selling, nothing to hedge.
CALM = MarketData(
    index_symbol="SPY",
    index_price=560.0,
    index_ma50=545.0,  # above the MA -> risk-on
    index_returns=[0.003, -0.002, 0.004, 0.001, -0.003, 0.002, 0.0, 0.003, -0.001, 0.002],
    index_iv=0.12,
    iv_year_low=0.10,
    iv_year_high=0.40,
)

# Rich IV but the market is still calm and rising -> the premium-harvesting sweet spot.
ELEVATED = MarketData(
    index_symbol="SPY",
    index_price=555.0,
    index_ma50=545.0,  # still above the MA -> risk-on
    index_returns=[0.006, -0.005, 0.008, -0.004, 0.007, -0.006, 0.005, -0.007, 0.006, -0.005],
    index_iv=0.24,     # ~47 IV Rank, and well above realized -> positive VRP
    iv_year_low=0.10,
    iv_year_high=0.40,
)

# Dear IV, below the MA, real drawdown -> stop selling premium, buy protection.
STRESSED = MarketData(
    index_symbol="SPY",
    index_price=520.0,
    index_ma50=550.0,  # ~5.5% below the MA -> risk-off
    index_returns=[-0.005, 0.002, -0.020, -0.025, -0.018, -0.030, 0.010, -0.022, -0.015, -0.028],
    index_iv=0.30,
    iv_year_low=0.10,
    iv_year_high=0.40,
)


def show(title: str, portfolio: Portfolio, market: MarketData):
    snap = assess(portfolio, market)
    plan = plan_strategy(portfolio, market, snapshot=snap, current_contracts=0)
    print(f"\n{'='*66}\n {title}\n{'='*66}")
    print("RISK SNAPSHOT")
    for line in snap.as_lines():
        print("  " + line)
    print("INCOME OVERLAY")
    for line in plan.income.as_lines():
        print("  " + line)
    print("HEDGE OVERLAY")
    for line in plan.hedge.as_lines():
        print("  " + line)
    print("STRATEGY")
    for line in plan.as_lines():
        print("  " + line)
    return snap, plan


def main() -> int:
    _, calm = show(
        "CALM  (premium rich vs realized, quiet) -> HARVEST",
        base_book(spy_price=560, qqq_price=480, peak=304_000, cash=40_000),
        CALM,
    )
    _, elevated = show(
        "ELEVATED  (richer IV, still risk-on) -> HARVEST",
        base_book(spy_price=555, qqq_price=475, peak=301_500, cash=40_000),
        ELEVATED,
    )
    _, stressed = show(
        "STRESSED  (dear IV, risk-off, drawdown) -> DEFEND",
        base_book(spy_price=520, qqq_price=440, peak=310_000, cash=40_000),
        STRESSED,
    )

    # --- invariants: the adaptive hybrid behavior we claim ---
    # CALM: premium rich vs realized (positive VRP) -> harvest; calm -> no hedge.
    assert calm.income.legs, "calm-but-rich premium should harvest (positive VRP)"
    assert calm.income.total_credit > 0, "harvest cycle should collect premium"
    assert calm.hedge.contracts_target == 0, "calm should hold no hedge"
    assert "HARVEST" in calm.posture, "calm-rich posture should HARVEST"

    # ELEVATED: richer IV + calm -> harvest more premium, positive carry, minimal hedge.
    assert elevated.income.legs, "elevated IV should generate income legs"
    assert elevated.income.total_credit > 0, "harvest cycle should collect premium"
    assert elevated.income.net_theta_per_day > 0, "harvesting should earn positive theta"
    assert elevated.net_theta_per_day > 0, "elevated cycle should be positive carry"
    assert any(l.kind == "covered_call" for l in elevated.income.legs), "expect covered calls"
    assert "HARVEST" in elevated.posture, "elevated posture should HARVEST"

    # STRESSED: dear IV but risk-off -> income stands down, hedge steps in.
    assert stressed.hedge.contracts_target > 0, "stress should buy protection"
    assert stressed.income.total_credit < elevated.income.total_credit, \
        "risk-off should harvest less than the calm-rich cycle"
    assert stressed.hedge.contracts_target > elevated.hedge.contracts_target, \
        "stress should hedge more than the harvest cycle"
    assert "DEFEND" in stressed.posture or "HEDGE" in stressed.posture, \
        "stress posture should defend/hedge"

    # covered-call capacity is never oversold vs shares held
    for leg in elevated.income.legs:
        if leg.kind == "covered_call":
            assert leg.contracts <= 3, "SPY covered calls <= floor(300/100)"

    print(f"\n{'='*66}")
    print(" ALL STRATEGY INVARIANTS PASSED  [OK]")
    print(f" calm: {calm.posture}")
    print(f" elevated: {elevated.posture}  |  credit ${elevated.income.total_credit:,.0f}"
          f"  net_theta +${elevated.net_theta_per_day:,.0f}/day")
    print(f" stressed: {stressed.posture}  |  hedge {stressed.hedge.contracts_target} contracts"
          f"  net_cost ${stressed.net_cost_today:+,.0f}")
    print(f"{'='*66}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
