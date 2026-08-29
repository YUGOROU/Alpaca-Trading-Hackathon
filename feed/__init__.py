"""The OBSERVE layer — feeds live/mock data into the pure `risk_engine` (README §4).

    from feed import observe, StateStore, AlpacaDataSource
    src = AlpacaDataSource()                 # reads ALPACA_* from the env
    state = StateStore("state.json")         # peak mark + rolling IV history
    portfolio, market = observe(src, state)  # -> hand to assess()/plan_strategy()

Use `MockDataSource` for offline runs and tests (no creds, no network).
"""
from __future__ import annotations

from .core import (
    DataSource,
    IVRange,
    StateStore,
    assemble_market_data,
    assemble_portfolio,
    compute_beta,
    is_option_symbol,
    moving_average,
    observe,
)
from .mock import MockDataSource

__all__ = [
    "DataSource",
    "IVRange",
    "StateStore",
    "observe",
    "assemble_portfolio",
    "assemble_market_data",
    "compute_beta",
    "is_option_symbol",
    "moving_average",
    "MockDataSource",
    "AlpacaDataSource",
]


def __getattr__(name: str):
    # Lazy — importing `feed` never imports alpaca-py; only touching AlpacaDataSource does.
    if name == "AlpacaDataSource":
        from .alpaca import AlpacaDataSource

        return AlpacaDataSource
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
