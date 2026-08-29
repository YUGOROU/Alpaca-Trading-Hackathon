"""Define and size the Track-3 base book (README §9 step 1, §10).

The core portfolio is fixed **upfront** — the agent protects it, it never picks it
(Yugo's correction). This module is the single source of truth:

  * DEFAULT_BOOK   — target *weights* (not share counts) + betas, defined once.
  * build_portfolio — pure sizing: weights + equity + live prices -> `Portfolio`.
  * current_weights / rebalance_orders — drift check + share deltas to bring the
    book back to target (the "rebalance" arm of the loop).

Weights (not hardcoded shares) are the source of truth so the same definition sizes
correctly at any account value or price. Placing the actual buy/rebalance orders at
Alpaca is the executor's job; the *arithmetic* is deterministic and lives here so it
runs and tests offline.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import floor

from .types import Portfolio, Position


@dataclass(frozen=True)
class BookEntry:
    """One target holding: what fraction of equity to hold, and its beta to SPY."""

    symbol: str
    target_weight: float  # fraction of total equity (0..1)
    beta: float = 1.0


# Default core book — liquid, optionable US names (tight quotes -> clean paper fills,
# §7.13). Diversified across market / size / sector so no single position dominates the
# P&L, yet every holding stays highly correlated to SPY, so the SPY put hedge actually
# covers the book (low basis risk) instead of leaving concentrated single-name gaps.
# ETFs carry ~two-thirds of the weight (hundreds of underlying names) spread across
# seven sectors (market, tech, small-cap, industrials, healthcare, financials, energy)
# so no one sector dominates; the three mega-cap singles are a small "we hold real
# stocks" sleeve, with the high-beta name (NVDA) kept to a controlled ~3% momentum tilt
# so its volatility can't drive short-window variance. Equity weights sum to 0.80; the
# remaining 0.20 is the cash sleeve (dry powder for premium + margin, and a smaller net-
# long footprint keeps a quiet week's directional noise down). Betas are config
# estimates, refined later from live returns (feed.compute_beta).
DEFAULT_BOOK: list[BookEntry] = [
    BookEntry("SPY", 0.24, 1.00),   # broad-market core (500 names)
    BookEntry("QQQ", 0.11, 1.10),   # tech / growth tilt (100 names)
    BookEntry("IWM", 0.10, 1.15),   # small-cap breadth (2000 names)
    BookEntry("DIA", 0.09, 0.95),   # large-cap value / industrials (30 names)
    BookEntry("XLV", 0.08, 0.80),   # healthcare sector — defensive diversifier
    BookEntry("XLF", 0.07, 1.10),   # financials sector
    BookEntry("XLE", 0.04, 0.90),   # energy sector — low SPY correlation, real diversifier
    BookEntry("AAPL", 0.02, 1.15),
    BookEntry("MSFT", 0.02, 1.10),
    BookEntry("NVDA", 0.03, 1.60),  # controlled momentum sleeve (high beta, kept small)
]


def target_cash_weight(book: list[BookEntry] = DEFAULT_BOOK) -> float:
    """The leftover weight held as cash (1 - sum of equity weights)."""
    return max(0.0, 1.0 - sum(e.target_weight for e in book))


def build_portfolio(
    equity: float,
    prices: dict[str, float],
    book: list[BookEntry] = DEFAULT_BOOK,
    *,
    fractional: bool = False,
    peak_equity: float | None = None,
) -> Portfolio:
    """Size the target-weight book into concrete shares at the given prices.

    equity     : total account value to deploy ($).
    prices     : {symbol: price} live/last prices.
    fractional : True allows fractional shares (Alpaca supports it for equities);
                 False rounds each name down to whole shares, leftover falls to cash.
    Symbols missing from `prices` (or non-positive) are skipped — their weight stays
    in cash rather than silently mis-sizing.
    """
    positions: list[Position] = []
    for e in book:
        px = prices.get(e.symbol)
        if not px or px <= 0.0:
            continue
        dollars = equity * e.target_weight
        shares = dollars / px if fractional else float(floor(dollars / px))
        if shares <= 0.0:
            continue
        positions.append(Position(e.symbol, shares=shares, price=px, beta=e.beta))
    invested = sum(p.market_value for p in positions)
    cash = equity - invested
    return Portfolio(
        positions=positions,
        cash=cash,
        peak_equity=peak_equity if peak_equity is not None else equity,
    )


def current_weights(portfolio: Portfolio) -> dict[str, float]:
    """Each position's share of current equity (for drift / rebalance checks)."""
    eq = portfolio.equity
    if eq <= 0.0:
        return {}
    return {p.symbol: p.market_value / eq for p in portfolio.positions}


def rebalance_orders(
    portfolio: Portfolio,
    prices: dict[str, float],
    book: list[BookEntry] = DEFAULT_BOOK,
    *,
    band: float = 0.05,
    fractional: bool = False,
) -> dict[str, float]:
    """Share deltas to bring each name back to its target weight (+buy / -sell).

    Only names that have drifted more than `band` (default 5 percentage points of
    equity) off target are traded — a no-trade band that avoids churning on noise.
    This is the "rebalance" arm of the loop; the engine's hedge logic is separate.
    """
    eq = portfolio.equity
    held = {p.symbol: p for p in portfolio.positions}
    orders: dict[str, float] = {}
    for e in book:
        px = prices.get(e.symbol)
        if not px or px <= 0.0:
            continue
        cur_shares = held[e.symbol].shares if e.symbol in held else 0.0
        cur_weight = (cur_shares * px) / eq if eq > 0.0 else 0.0
        if abs(cur_weight - e.target_weight) < band:
            continue  # inside the no-trade band
        target_dollars = eq * e.target_weight
        target_shares = target_dollars / px if fractional else float(floor(target_dollars / px))
        delta = target_shares - cur_shares
        if delta != 0.0:
            orders[e.symbol] = delta
    return orders
