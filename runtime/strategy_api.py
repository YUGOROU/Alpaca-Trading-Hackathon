"""The harness-facing API — the single call the agent loop makes each cycle.

`get_strategy_context()` runs the whole read-side + brain and returns ONE plain,
JSON-serializable dict:

    OBSERVE (feed) -> MEASURE (assess) -> plan_strategy -> validate_plan (risk caps)

The DSH agent calls this in its "measure" node, hands the JSON to its LLM
"decide" node, then executes the (already risk-checked) legs. Harness-agnostic: it only
needs a `DataSource` and a `StateStore`.

    from feed import AlpacaDataSource, StateStore
    from runtime.strategy_api import get_strategy_context
    ctx = get_strategy_context(AlpacaDataSource(), StateStore("state.json"))
    # ctx["plan"] -> what to trade;  ctx["validation"] -> what was capped
"""
from __future__ import annotations

import json

from feed import DataSource, StateStore, observe
from risk_engine import assess, plan_strategy, validate_plan


def get_strategy_context(
    source: DataSource,
    state: StateStore,
    *,
    index_symbol: str = "SPY",
    day_pnl_pct: float = 0.0,
    income_dte: int = 4,
    hedge_dte: int = 4,
    current_contracts: int = 0,
) -> dict:
    """Produce the full, risk-validated decision context for one cycle, as a dict.

    `day_pnl_pct` (today's P&L as a fraction of equity) feeds the daily-loss halt.
    `current_contracts` is the hedge already held, so the plan proposes the delta.
    """
    portfolio, market = observe(source, state, index_symbol=index_symbol)
    snapshot = assess(portfolio, market)
    plan = plan_strategy(
        portfolio, market, snapshot,
        current_contracts=current_contracts,
        income_dte=income_dte, hedge_dte=hedge_dte,
    )
    validation = validate_plan(plan, portfolio.equity, day_pnl_pct=day_pnl_pct)

    return {
        "index_symbol": index_symbol,
        "portfolio": {
            "equity": round(portfolio.equity, 2),
            "cash": round(portfolio.cash, 2),
            "peak_equity": round(portfolio.peak_equity, 2) if portfolio.peak_equity else None,
            "positions": [
                {"symbol": p.symbol, "shares": p.shares,
                 "price": round(p.price, 2), "beta": round(p.beta, 3)}
                for p in portfolio.positions
            ],
        },
        "market": {
            "index_price": round(market.index_price, 2),
            "index_ma50": round(market.index_ma50, 2),
            "index_iv": round(market.index_iv, 4),
            "iv_year_low": round(market.iv_year_low, 4),
            "iv_year_high": round(market.iv_year_high, 4),
        },
        "snapshot": snapshot.to_dict(),
        "plan": validation.plan.to_dict(),      # the SAFE, risk-capped plan to act on
        "validation": {"ok": validation.ok, "violations": validation.violations},
    }


def get_strategy_context_json(source: DataSource, state: StateStore, **kwargs) -> str:
    """Same as `get_strategy_context` but returns a JSON string."""
    return json.dumps(get_strategy_context(source, state, **kwargs), indent=2)
