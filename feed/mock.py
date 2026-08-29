"""An offline `DataSource` with canned data — lets the whole OBSERVE step and the
engine run and be tested with no Alpaca account, no network, and no market hours.

The names move as a real book would: each asset's daily return is `beta * market
return + a little idiosyncratic noise`, so `compute_beta` recovers ~the intended beta
and the market is a calm, gently rising tape with moderately rich IV — a full
observe -> assess -> plan_strategy round-trip lands on a sensible harvest-ish result.
"""
from __future__ import annotations

# A calm market tape with both up and down days (down days are what downside-beta needs).
_MARKET_RETURNS = [
    0.004, -0.006, 0.008, -0.010, 0.003, -0.004, 0.006, -0.007, 0.005, -0.003
] * 7  # 70 daily returns

# Per-name true beta to the market + a tiny deterministic idiosyncratic wobble.
_BETAS = {"SPY": 1.00, "QQQ": 1.10, "AAPL": 1.20}
_STARTS = {"SPY": 540.0, "QQQ": 470.0, "AAPL": 220.0}


def _closes_from_returns(start: float, rets: list[float]) -> list[float]:
    closes = [start]
    for r in rets:
        closes.append(round(closes[-1] * (1.0 + r), 2))
    return closes


def _asset_returns(beta: float) -> list[float]:
    # market-driven return + small alternating idiosyncratic term (keeps beta ~ true beta)
    return [beta * r + (0.0004 if i % 3 else -0.0003) for i, r in enumerate(_MARKET_RETURNS)]


class MockDataSource:
    """Canned account + market data implementing the `DataSource` protocol."""

    def __init__(self, *, cash: float = 40_000.0, atm_iv: float = 0.22):
        self._cash = cash
        self._atm_iv = atm_iv
        self._closes = {
            sym: _closes_from_returns(_STARTS[sym], _asset_returns(b))
            for sym, b in _BETAS.items()
        }
        self._shares = {"SPY": 300.0, "QQQ": 200.0, "AAPL": 100.0}

    def account(self) -> tuple[float, float]:
        equity = self._cash + sum(
            self._shares[s] * self._closes[s][-1] for s in self._shares
        )
        return equity, self._cash

    def positions(self) -> list[tuple[str, float, float]]:
        return [(s, self._shares[s], self._closes[s][-1]) for s in self._shares]

    def daily_closes(self, symbol: str, lookback: int) -> list[float]:
        return self._closes.get(symbol, [])[-lookback:]

    def latest_price(self, symbol: str) -> float:
        return self._closes[symbol][-1]

    def atm_iv(self, symbol: str, dte: int) -> float:
        return self._atm_iv
