"""The live Alpaca `DataSource` (REST via alpaca-py).

Reads the account, positions, daily bars, latest price, and ATM option IV needed by
the OBSERVE step. `alpaca-py` is imported lazily so this module (and the rest of the
package) still imports with the SDK absent — only constructing `AlpacaDataSource`
requires it. Credentials come from the environment (`ALPACA_API_KEY` / `ALPACA_SECRET_KEY`,
see `.env.example`).

Boundary note: reading market/account data via REST is standard; the *agent's trades*
are what must go through Alpaca's MCP/CLI. Because everything here sits behind the
`DataSource` protocol, Yugo can drop in an MCP-backed source later without touching the
engine or the OBSERVE logic.
"""
from __future__ import annotations

import math
import os
from datetime import date, datetime, timedelta


def _parse_occ(occ: str) -> tuple[date, str, float]:
    """Parse an OCC option symbol -> (expiry, 'C'|'P', strike).

    Format: ROOT + YYMMDD + C/P + strike*1000 (8 digits). e.g. SPY240920P00450000.
    """
    strike = int(occ[-8:]) / 1000.0
    cp = occ[-9]
    ymd = occ[-15:-9]
    expiry = date(2000 + int(ymd[:2]), int(ymd[2:4]), int(ymd[4:6]))
    return expiry, cp, strike


class AlpacaDataSource:
    """Live account + market data from Alpaca, implementing the `DataSource` protocol."""

    def __init__(
        self,
        api_key: str | None = None,
        secret_key: str | None = None,
        *,
        paper: bool | None = None,
    ):
        try:
            from alpaca.data.historical import StockHistoricalDataClient
            from alpaca.data.historical.option import OptionHistoricalDataClient
            from alpaca.trading.client import TradingClient
        except ImportError as e:  # pragma: no cover - depends on optional dep
            raise ImportError(
                "alpaca-py is required for AlpacaDataSource. Install it: pip install alpaca-py"
            ) from e

        api_key = api_key or os.getenv("ALPACA_API_KEY")
        secret_key = secret_key or os.getenv("ALPACA_SECRET_KEY")
        if not api_key or not secret_key:
            raise RuntimeError(
                "Missing Alpaca credentials — set ALPACA_API_KEY and ALPACA_SECRET_KEY "
                "(see .env.example)."
            )
        if paper is None:
            paper = os.getenv("ALPACA_PAPER", "true").lower() != "false"
        self._paper = paper

        self._trading = TradingClient(api_key, secret_key, paper=paper)
        self._stocks = StockHistoricalDataClient(api_key, secret_key)
        self._options = OptionHistoricalDataClient(api_key, secret_key)

    # --- account -----------------------------------------------------------
    def account(self) -> tuple[float, float]:
        acct = self._trading.get_account()
        return float(acct.equity), float(acct.cash)

    def positions(self) -> list[tuple[str, float, float]]:
        out: list[tuple[str, float, float]] = []
        for p in self._trading.get_all_positions():
            price = float(p.current_price) if p.current_price is not None else 0.0
            out.append((p.symbol, float(p.qty), price))
        return out

    def open_order_ids(self) -> list[str]:
        """Return open paper-order IDs for submission-time reconciliation."""
        from alpaca.trading.enums import QueryOrderStatus
        from alpaca.trading.requests import GetOrdersRequest

        orders = self._trading.get_orders(GetOrdersRequest(status=QueryOrderStatus.OPEN))
        return [str(order.id) for order in orders]

    def is_market_open(self) -> bool:
        """Use the broker clock rather than a guessed UTC session window."""
        return bool(self._trading.get_clock().is_open)

    def order_status(self, order_id: str) -> dict[str, str]:
        """Return one broker order's canonical id/status for paper reconciliation."""
        order = self._trading.get_order_by_id(order_id)
        status = getattr(order.status, "value", order.status)
        return {"alpaca_order_id": str(order.id), "state": str(status).lower()}

    # --- order placement (one-off seeding) ---------------------------------
    def submit_market_order(self, symbol: str, qty: int | float) -> str:
        """Submit a day market buy order on the configured Alpaca account; returns the order id.

        The account mode (paper vs live) follows how this `AlpacaDataSource` was
        constructed — `ALPACA_PAPER` defaults to true (paper). This write path is
        **paper-only**: it raises if the source is live-constructed (fail-closed), so
        seeding can never place a live order.

        Kept on the concrete `AlpacaDataSource` (not the read-only `DataSource`
        protocol) because OBSERVE never places orders — this is for one-off seeding
        scripts (scripts/seed_book.py), not the agent loop.

        `qty` must be a positive whole number of shares/contracts. Float sizing math
        (e.g. seed_book's target - held) can land a hair off an integer (49.999999);
        we round to the nearest whole share and raise if it is genuinely fractional,
        so seeding never silently under-buys.

        Fail-closed: this write path refuses to run against a live-constructed source
        (`ALPACA_PAPER=false`). Seeding is paper-only by design (no live trading).
        """
        if not self._paper:
            raise RuntimeError(
                "submit_market_order is paper-only (fail-closed); refusing to place on a "
                "live-constructed AlpacaDataSource (set ALPACA_PAPER=true)."
            )
        if not isinstance(qty, (int, float)) or isinstance(qty, bool):
            raise TypeError(f"qty must be a number, got {type(qty).__name__}")
        whole = round(qty)
        if not math.isclose(qty, whole, rel_tol=0.0, abs_tol=1e-6):
            raise ValueError(f"qty must be a whole number of shares/contracts, got {qty}")
        if whole <= 0:
            raise ValueError(f"qty must be positive, got {qty}")

        from alpaca.trading.enums import OrderSide, TimeInForce
        from alpaca.trading.requests import MarketOrderRequest

        req = MarketOrderRequest(
            symbol=symbol, qty=whole, side=OrderSide.BUY, time_in_force=TimeInForce.DAY
        )
        order = self._trading.submit_order(req)
        return getattr(order, "id", "?")

    # --- prices / bars -----------------------------------------------------
    def daily_closes(self, symbol: str, lookback: int) -> list[float]:
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame

        # Over-fetch calendar days to cover weekends/holidays for `lookback` trading days.
        start = datetime.utcnow() - timedelta(days=int(lookback * 1.7) + 10)
        req = StockBarsRequest(
            symbol_or_symbols=symbol, timeframe=TimeFrame.Day, start=start
        )
        bars = self._stocks.get_stock_bars(req)
        rows = bars.data.get(symbol, []) if hasattr(bars, "data") else bars[symbol]
        return [float(b.close) for b in rows][-lookback:]

    def latest_price(self, symbol: str) -> float:
        from alpaca.data.requests import StockLatestTradeRequest

        try:
            req = StockLatestTradeRequest(symbol_or_symbols=symbol)
            trade = self._stocks.get_stock_latest_trade(req)
            return float(trade[symbol].price)
        except Exception:
            closes = self.daily_closes(symbol, 1)  # fall back to last close
            if not closes:
                raise
            return closes[-1]

    # --- option IV (the snapshot-only field, README §8) --------------------
    def atm_iv(self, symbol: str, dte: int) -> float:
        """ATM implied vol near `dte`, chosen from the live option chain snapshot."""
        from alpaca.data.requests import OptionChainRequest

        spot = self.latest_price(symbol)
        try:
            chain = self._options.get_option_chain(
                OptionChainRequest(underlying_symbol=symbol)
            )
        except Exception as e:  # pragma: no cover - needs options entitlement + market
            raise RuntimeError(
                f"Could not fetch {symbol} option chain — check options data entitlement "
                f"and market hours ({e})."
            ) from e

        today = date.today()
        best_iv: float | None = None
        best_score: tuple[float, float] | None = None
        for occ, snap in chain.items():
            iv = getattr(snap, "implied_volatility", None)
            if iv is None and getattr(snap, "greeks", None) is not None:
                iv = getattr(snap.greeks, "implied_volatility", None)
            if not iv or iv <= 0.0:
                continue
            try:
                expiry, _cp, strike = _parse_occ(occ)
            except (ValueError, IndexError):
                continue
            days = (expiry - today).days
            if days <= 0:
                continue
            score = (abs(days - dte), abs(strike - spot))  # nearest expiry, then ATM
            if best_score is None or score < best_score:
                best_score, best_iv = score, float(iv)

        if best_iv is None:
            raise RuntimeError(
                f"No usable IV in the {symbol} option chain snapshot (empty or no greeks)."
            )
        return best_iv
