"""Historical price series for the backtest.

`synthetic_series` builds a deterministic tape with three regimes — a calm uptrend, a
sharp crash, then a recovery — so the backtest visibly exercises all three postures
(harvest / defend / harvest). Swap in real Alpaca daily closes via
`feed.AlpacaDataSource.daily_closes(...)` for a live-data backtest.
"""
from __future__ import annotations


def _closes_from_returns(start: float, rets: list[float]) -> list[float]:
    closes = [start]
    for r in rets:
        closes.append(round(closes[-1] * (1.0 + r), 2))
    return closes


def synthetic_series(start: float = 500.0) -> list[float]:
    """A calm uptrend -> crash -> recovery tape (~150 daily closes)."""
    calm = [0.0025 + (0.004 if i % 2 else -0.004) for i in range(80)]     # grind up, low vol
    crash = [-0.028 + (0.012 if i % 2 else -0.012) for i in range(16)]    # sharp, high vol
    recover = [0.006 + (0.006 if i % 2 else -0.006) for i in range(45)]   # bounce back
    return _closes_from_returns(start, calm + crash + recover)
