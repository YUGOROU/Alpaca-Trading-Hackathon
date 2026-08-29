"""Fixed, seed-free market contexts for local and replay validation."""

from __future__ import annotations

from risk_engine import MarketData, Portfolio, Position


def _book(spy: float, qqq: float, peak: float, cash: float = 40_000.0) -> Portfolio:
    return Portfolio(
        positions=[
            Position("SPY", shares=300, price=spy, beta=1.0),
            Position("QQQ", shares=200, price=qqq, beta=1.1),
        ],
        cash=cash,
        peak_equity=peak,
    )


SCENARIOS = {
    "calm": (
        _book(560.0, 480.0, 304_000.0),
        MarketData(
            index_symbol="SPY",
            index_price=560.0,
            index_ma50=545.0,
            index_returns=[0.003, -0.002, 0.004, 0.001, -0.003, 0.002, 0.0, 0.003, -0.001, 0.002],
            index_iv=0.12,
            iv_year_low=0.10,
            iv_year_high=0.40,
        ),
    ),
    "elevated": (
        _book(555.0, 475.0, 301_500.0),
        MarketData(
            index_symbol="SPY",
            index_price=555.0,
            index_ma50=545.0,
            index_returns=[0.006, -0.005, 0.008, -0.004, 0.007, -0.006, 0.005, -0.007, 0.006, -0.005],
            index_iv=0.24,
            iv_year_low=0.10,
            iv_year_high=0.40,
        ),
    ),
    "stressed": (
        _book(520.0, 440.0, 310_000.0),
        MarketData(
            index_symbol="SPY",
            index_price=520.0,
            index_ma50=550.0,
            index_returns=[-0.005, 0.002, -0.020, -0.025, -0.018, -0.030, 0.010, -0.022, -0.015, -0.028],
            index_iv=0.30,
            iv_year_low=0.10,
            iv_year_high=0.40,
        ),
    ),
}


def get_scenario(name: str):
    try:
        return SCENARIOS[name]
    except KeyError as error:
        raise ValueError(f"unknown scenario: {name}") from error
