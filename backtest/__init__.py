"""Historical backtest — the proof-of-edge for the pitch (README §8)."""
from __future__ import annotations

from .data import synthetic_series
from .engine import BacktestResult, run_backtest

__all__ = ["run_backtest", "BacktestResult", "synthetic_series"]
