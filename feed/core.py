"""The OBSERVE step: turn a data source into the engine's inputs (README §4, §9).

`risk_engine` is pure math and never talks to a broker. This package is the I/O seam
that feeds it: a `DataSource` protocol (so the source can be Alpaca REST, an MCP-backed
feed, or a mock), pure builders that assemble `Portfolio` + `MarketData`, and a small
on-disk `StateStore` that carries the two things a single snapshot can't give us —

  * the **peak-equity high-water mark** (drawdown needs history), and
  * a **rolling IV history** for IV Rank, because Alpaca's option IV is snapshot-only
    with no history (README §8). We record today's IV each cycle and build the 1-yr
    range from what we've stored, seeding with sane defaults until enough accrues.

`observe(source, state)` runs the whole step and returns `(Portfolio, MarketData)`
ready to hand to `assess()` / `plan_strategy()`.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Protocol

from risk_engine import MarketData, Portfolio, Position
from risk_engine import metrics


# ---------------------------------------------------------------------------
# The data source contract — implement this to plug in any feed.
# ---------------------------------------------------------------------------


class DataSource(Protocol):
    """Everything the OBSERVE step needs, abstracted from where it comes from."""

    def account(self) -> tuple[float, float]:
        """(equity, cash) for the account, in dollars."""

    def positions(self) -> list[tuple[str, float, float]]:
        """Open holdings as (symbol, shares, price)."""

    def daily_closes(self, symbol: str, lookback: int) -> list[float]:
        """Daily closes, oldest -> newest, at least `lookback` long if available."""

    def latest_price(self, symbol: str) -> float:
        """Most recent trade/quote price for `symbol`."""

    def atm_iv(self, symbol: str, dte: int) -> float:
        """At-the-money implied vol (annualized decimal) near `dte` days to expiry."""


# ---------------------------------------------------------------------------
# Persistent state — the history a single snapshot can't provide.
# ---------------------------------------------------------------------------

DEFAULT_IV_LOW = 0.10
DEFAULT_IV_HIGH = 0.40
IV_MIN_SAMPLES = 20   # below this, use the default range rather than a thin observed one
IV_MAX_KEEP = 400     # ~a trading year and a half of daily IV points


@dataclass
class IVRange:
    low: float
    high: float
    samples: int
    seeded: bool  # True while we're still on the default range (not enough history)


class StateStore:
    """Tiny JSON store for the peak-equity mark and the rolling IV history."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._data = self._load()

    def _load(self) -> dict:
        if self.path.exists():
            try:
                return json.loads(self.path.read_text())
            except (json.JSONDecodeError, OSError):
                pass
        return {"peak_equity": None, "iv_history": {}}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._data, indent=2, sort_keys=True))

    def update_peak(self, equity: float) -> float:
        """Raise the high-water mark if equity is a new high; return the current peak."""
        peak = self._data.get("peak_equity")
        peak = equity if peak is None else max(peak, equity)
        self._data["peak_equity"] = peak
        return peak

    def record_iv(self, iv: float, day: str | None = None) -> None:
        """Store today's observed IV (one point per calendar day), trimming old points."""
        day = day or date.today().isoformat()
        hist = self._data.setdefault("iv_history", {})
        hist[day] = iv
        if len(hist) > IV_MAX_KEEP:
            for old in sorted(hist)[: len(hist) - IV_MAX_KEEP]:
                del hist[old]

    def iv_range(
        self,
        default_low: float = DEFAULT_IV_LOW,
        default_high: float = DEFAULT_IV_HIGH,
        min_samples: int = IV_MIN_SAMPLES,
    ) -> IVRange:
        """The 1-yr IV low/high for IV Rank, from stored history or defaults."""
        vals = list(self._data.get("iv_history", {}).values())
        if len(vals) < min_samples:
            return IVRange(default_low, default_high, len(vals), seeded=True)
        return IVRange(min(vals), max(vals), len(vals), seeded=False)


# ---------------------------------------------------------------------------
# Pure builders — raw data -> engine types (unit-testable, no I/O).
# ---------------------------------------------------------------------------


def is_option_symbol(symbol: str) -> bool:
    """True for an OCC option symbol (ROOT + YYMMDD + C/P + strike*1000, 8 digits).

    Alpaca's positions list mixes equities and the option legs we hold; the risk engine's
    book is equities only, so option symbols must be excluded from `assemble_portfolio`
    (they'd otherwise be counted as stock with wrong magnitude and beta 1.0).
    """
    s = str(symbol).strip()
    if len(s) < 15 or s[-9] not in ("C", "P"):
        return False
    return s[-8:].isdigit() and s[-15:-9].isdigit()


def moving_average(closes: list[float], window: int) -> float:
    """Simple moving average of the last `window` closes (uses all if fewer exist)."""
    if not closes:
        return 0.0
    w = min(window, len(closes))
    return sum(closes[-w:]) / w


def compute_beta(
    asset_closes: list[float], index_closes: list[float], downside: bool = True
) -> float:
    """Beta of an asset to the index from aligned daily closes (§7.2).

    Uses *downside* beta by default — the exposure a hedger actually cares about —
    falling back to full-sample beta, and to 1.0 when there isn't enough data.
    """
    a = metrics.simple_returns(asset_closes)
    m = metrics.simple_returns(index_closes)
    n = min(len(a), len(m))
    if n < 2:
        return 1.0
    a, m = a[-n:], m[-n:]
    b = metrics.downside_beta(a, m) if downside else metrics.ols_beta(a, m)
    return b if b > 0.0 else 1.0


def assemble_portfolio(
    raw_positions: list[tuple[str, float, float]],
    cash: float,
    peak_equity: float,
    betas: dict[str, float] | None = None,
) -> Portfolio:
    """Build a `Portfolio` from raw holdings, cash, the peak mark, and per-name betas."""
    betas = betas or {}
    positions = [
        Position(sym, shares=shares, price=price, beta=betas.get(sym, 1.0))
        for sym, shares, price in raw_positions
        if shares != 0.0 and price > 0.0 and not is_option_symbol(sym)
    ]
    return Portfolio(positions=positions, cash=cash, peak_equity=peak_equity)


def assemble_market_data(
    index_symbol: str,
    closes: list[float],
    latest_price: float,
    atm_iv: float,
    iv: IVRange,
    *,
    risk_free_rate: float = 0.04,
    returns_lookback: int = 60,
    ma_window: int = 50,
) -> MarketData:
    """Build `MarketData` for the hedge index from its closes + option IV."""
    tail = closes[-(returns_lookback + 1):] if len(closes) > returns_lookback + 1 else closes
    return MarketData(
        index_symbol=index_symbol,
        index_price=latest_price,
        index_ma50=moving_average(closes, ma_window),
        index_returns=metrics.simple_returns(tail),
        index_iv=atm_iv,
        iv_year_low=iv.low,
        iv_year_high=iv.high,
        risk_free_rate=risk_free_rate,
    )


# ---------------------------------------------------------------------------
# The OBSERVE step.
# ---------------------------------------------------------------------------


def observe(
    source: DataSource,
    state: StateStore,
    *,
    index_symbol: str = "SPY",
    risk_free_rate: float = 0.04,
    dte: int = 30,
    lookback: int = 60,
    compute_betas: bool = True,
    persist: bool = True,
) -> tuple[Portfolio, MarketData]:
    """Read one live snapshot and assemble the engine's inputs (README §4 step 1).

    Advances the peak-equity mark and the IV history in `state`. Set `persist=False`
    to observe without writing state (e.g. dry runs / tests).
    """
    equity, cash = source.account()
    raw_positions = source.positions()
    peak = state.update_peak(equity)

    need = max(lookback, 50) + 1
    index_closes = source.daily_closes(index_symbol, need)
    index_latest = source.latest_price(index_symbol)

    atm_iv = source.atm_iv(index_symbol, dte)
    state.record_iv(atm_iv)
    iv_range = state.iv_range()

    betas: dict[str, float] = {}
    if compute_betas:
        for sym, _shares, _price in raw_positions:
            if sym == index_symbol:
                betas[sym] = 1.0
                continue
            try:
                betas[sym] = compute_beta(source.daily_closes(sym, need), index_closes)
            except Exception:
                betas[sym] = 1.0  # config/1.0 fallback; never block OBSERVE on one name

    portfolio = assemble_portfolio(raw_positions, cash, peak, betas)
    market = assemble_market_data(
        index_symbol, index_closes, index_latest, atm_iv, iv_range,
        risk_free_rate=risk_free_rate, returns_lookback=lookback,
    )

    if persist:
        state.save()
    return portfolio, market
