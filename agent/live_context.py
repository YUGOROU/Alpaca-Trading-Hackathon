"""Live OBSERVE -> candidate context: the bridge from real data to the DSH gate.

`scenarios.py` feeds the candidate flow fixed fixtures; this feeds it a *live*
`feed.observe(...)` snapshot instead, with no change to `build_decision_context`.

The one subtlety: DSH calls Python twice — once for `context`, once for `submit` — as
separate processes. The gate re-derives the context and checks the `context_id` matches
(agent/gate.py). Fixtures rebuild identically, but live market data moves between the two
calls, so a naive rebuild would hash to a different id and every live submit would be
rejected as `stale_or_unknown_context`. We therefore **persist the inputs** that produced
each live context, keyed by `context_id`, so `submit` rebuilds from the exact same inputs
the model saw (same inputs -> same id -> gate passes).

We store the inputs (plain floats) rather than the built `DecisionContext` (nested
StrategyPlan objects) because inputs round-trip trivially and rebuild identically.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

from risk_engine import MarketData, Portfolio, Position

from .candidates import build_decision_context
from .contracts import DecisionContext

LIVE_SCENARIO_ID = "live"
MOCK_SCENARIO_ID = "mock"
DEFAULT_STORE_KEEP = 200  # cap the inputs store; live contexts are short-lived
DEFAULT_CONTEXT_TTL_SECONDS = 300


def count_hedge_contracts(positions, index_symbol: str = "SPY") -> int:
    """Long protective-put contracts already held on `index_symbol` (for step-out sizing).

    Reads OCC option symbols from the raw `(symbol, shares, price)` holdings; a long put
    on the index root counts toward the hedge we hold. Equity-only books (the mock) have
    no option symbols and return 0. Malformed symbols are ignored, never fatal.
    """
    held = 0
    for symbol, shares, _price in positions:
        if shares is None or shares <= 0:
            continue  # only long puts protect; shorts/closed don't count
        occ = str(symbol).replace(" ", "")
        if len(occ) < 15 or not occ.upper().startswith(index_symbol.upper()):
            continue
        try:
            right = occ[-9].upper()
            int(occ[-8:])  # strike field must be numeric -> it's a real OCC symbol
        except (IndexError, ValueError):
            continue
        if right == "P":
            held += int(shares)
    return held


def has_open_income(positions) -> bool:
    """True if any short option leg is already held (an income overlay is on).

    Selling premium means short option legs (negative qty); a live condor/covered call
    leaves them on the book. A periodic loop must see this or it would sell a fresh
    overlay every tick and stack far past the risk caps in aggregate.
    """
    from feed import is_option_symbol

    return any(
        shares is not None and shares < 0 and is_option_symbol(symbol)
        for symbol, shares, _price in positions
    )


# ---------------------------------------------------------------------------
# Source selection — mirror scripts/run_agent.py so creds are read one way.
# ---------------------------------------------------------------------------


def _live_source_and_state():
    """Build only a real Alpaca source for ``--live``.

    A live request must never silently become a fixture-like mock cycle.  Tests
    that need a mock inject it directly or use the explicit ``--mock`` command.
    """
    from feed import StateStore

    have_creds = bool(os.getenv("ALPACA_API_KEY") and os.getenv("ALPACA_SECRET_KEY"))
    if not have_creds:
        raise RuntimeError(
            "--live requires ALPACA_API_KEY and ALPACA_SECRET_KEY; "
            "use --mock or --scenario for non-live execution"
        )
    from feed import AlpacaDataSource

    source = AlpacaDataSource()
    state = StateStore(os.getenv("AGENT_STATE_PATH", "state/state.json"))
    return source, state


# ---------------------------------------------------------------------------
# (De)serialize the engine inputs — floats only, rebuilds identically.
# ---------------------------------------------------------------------------


def _portfolio_to_dict(p: Portfolio) -> dict:
    return {
        "positions": [
            {"symbol": q.symbol, "shares": q.shares, "price": q.price, "beta": q.beta}
            for q in p.positions
        ],
        "cash": p.cash,
        "peak_equity": p.peak_equity,
    }


def _portfolio_from_dict(d: dict) -> Portfolio:
    return Portfolio(
        positions=[Position(**q) for q in d["positions"]],
        cash=d["cash"],
        peak_equity=d["peak_equity"],
    )


def _market_to_dict(m: MarketData) -> dict:
    return {
        "index_symbol": m.index_symbol,
        "index_price": m.index_price,
        "index_ma50": m.index_ma50,
        "index_returns": list(m.index_returns),
        "index_iv": m.index_iv,
        "iv_year_low": m.iv_year_low,
        "iv_year_high": m.iv_year_high,
        "risk_free_rate": m.risk_free_rate,
    }


def _market_from_dict(d: dict) -> MarketData:
    return MarketData(**d)


# ---------------------------------------------------------------------------
# The inputs store — one JSONL line per built live context, keyed by id.
# ---------------------------------------------------------------------------


def _store_path() -> Path:
    return Path(os.getenv("AGENT_CONTEXT_PATH", "state/contexts.jsonl"))


def save_context_inputs(
    context_id: str,
    portfolio: Portfolio,
    market: MarketData,
    *,
    expiry_days: int,
    current_contracts: int,
    income_open: bool = False,
    input_provenance: dict | None = None,
    execution_snapshot: dict | None = None,
    scenario_id: str = LIVE_SCENARIO_ID,
    keep: int = DEFAULT_STORE_KEEP,
) -> None:
    """Append the inputs that produced `context_id`; keep only the last `keep`."""
    path = _store_path()
    rows: list[dict] = []
    if path.exists():
        rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    rows = [r for r in rows if r.get("context_id") != context_id]
    rows.append({
        "context_id": context_id,
        "scenario_id": scenario_id,
        "portfolio": _portfolio_to_dict(portfolio),
        "market": _market_to_dict(market),
        "expiry_days": expiry_days,
        "current_contracts": current_contracts,
        "income_open": income_open,
        "input_provenance": input_provenance,
        "execution_snapshot": execution_snapshot,
    })
    rows = rows[-keep:]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as stream:
        for r in rows:
            stream.write(json.dumps(r, sort_keys=True) + "\n")


def load_context_inputs(context_id: str) -> dict | None:
    """Return the stored inputs for `context_id`, or None if unknown/expired."""
    path = _store_path()
    if not path.exists():
        return None
    for line in reversed(path.read_text().splitlines()):
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("context_id") == context_id:
            return row
    return None


# ---------------------------------------------------------------------------
# Build (context command) and rebuild (submit command).
# ---------------------------------------------------------------------------


def build_live_context(
    *,
    source=None,
    state=None,
    index_symbol: str = "SPY",
    expiry_days: int = 4,
    current_contracts: int | None = None,
    persist: bool = True,
    source_kind: str = "alpaca_rest",
    scenario_id: str = LIVE_SCENARIO_ID,
    now: datetime | None = None,
) -> DecisionContext:
    """OBSERVE live data and produce the candidate context, persisting its inputs.

    `current_contracts` is the hedge already held, so the plan proposes only the delta.
    When None (the default) it is counted from live option positions, so the hedge can
    correctly step *out* as well as in; pass an explicit int to override.
    """
    from feed import observe

    if source is None or state is None:
        source, state = _live_source_and_state()
        source_kind = "alpaca_rest"
    elif source_kind == "alpaca_rest":
        # Dependency injection is used by offline tests.  Do not label such a
        # context as a live Alpaca observation merely because it used the
        # default argument.
        from feed import MockDataSource
        if isinstance(source, MockDataSource):
            source_kind = "mock"
    now = now or datetime.now(UTC)
    ttl_seconds = int(os.getenv("AGENT_CONTEXT_TTL_SECONDS", str(DEFAULT_CONTEXT_TTL_SECONDS)))
    if ttl_seconds <= 0:
        raise RuntimeError("AGENT_CONTEXT_TTL_SECONDS must be positive")
    provenance = {
        "source": source_kind,
        "observed_at": now.isoformat(),
        "expires_at": (now + timedelta(seconds=ttl_seconds)).isoformat(),
    }
    live_positions = source.positions()
    if current_contracts is None:
        current_contracts = count_hedge_contracts(live_positions, index_symbol)
    income_open = has_open_income(live_positions)
    portfolio, market = observe(source, state, index_symbol=index_symbol)
    broker_equity, _broker_cash = source.account()
    open_order_ids = None
    if hasattr(source, "open_order_ids"):
        open_order_ids = sorted(str(order_id) for order_id in source.open_order_ids())
    execution_snapshot = {
        "equity": float(broker_equity),
        "positions": sorted((str(symbol), float(quantity)) for symbol, quantity, _price in live_positions if quantity),
        "open_order_ids": open_order_ids,
    }
    context = build_decision_context(
        portfolio,
        market,
        scenario_id=scenario_id,
        current_contracts=current_contracts,
        income_open=income_open,
        expiry_days=expiry_days,
        input_provenance=provenance,
    )
    if persist:
        save_context_inputs(
            context.context_id, portfolio, market,
            expiry_days=expiry_days, current_contracts=current_contracts,
            income_open=income_open,
            input_provenance=provenance,
            execution_snapshot=execution_snapshot,
            scenario_id=scenario_id,
        )
    return context


def rebuild_observed_context(
    context_id: str, *, expected_source: str, now: datetime | None = None
) -> DecisionContext | None:
    """Rebuild a fresh, provenance-matching observed context for submission."""
    row = load_context_inputs(context_id)
    if row is None:
        return None
    provenance = row.get("input_provenance")
    if not provenance or provenance.get("source") != expected_source:
        return None
    try:
        expires_at = datetime.fromisoformat(provenance["expires_at"])
    except (KeyError, TypeError, ValueError):
        return None
    now = now or datetime.now(UTC)
    if expires_at <= now:
        return None
    return build_decision_context(
        _portfolio_from_dict(row["portfolio"]),
        _market_from_dict(row["market"]),
        scenario_id=row.get("scenario_id", LIVE_SCENARIO_ID),
        current_contracts=row["current_contracts"],
        income_open=row.get("income_open", False),
        expiry_days=row["expiry_days"],
        input_provenance=provenance,
    )


def rebuild_live_context(context_id: str, *, now: datetime | None = None) -> DecisionContext | None:
    """Rebuild the exact fresh Alpaca context the model saw."""
    return rebuild_observed_context(context_id, expected_source="alpaca_rest", now=now)


def build_mock_context(*, state=None, now: datetime | None = None) -> DecisionContext:
    """Build an explicit mock context; never use it as a live fallback."""
    from feed import MockDataSource, StateStore

    if state is None:
        state = StateStore(os.getenv("AGENT_STATE_PATH", "state/state.json"))
    return build_live_context(
        source=MockDataSource(), state=state, source_kind="mock",
        scenario_id=MOCK_SCENARIO_ID, now=now,
    )
