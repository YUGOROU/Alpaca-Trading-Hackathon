"""Five-minute, deterministic heartbeat monitor for the paper agent.

This module never invokes an LLM or submits an order.  It observes the live
paper account, builds the admissible context, records a compact checkpoint, and
decides whether the Node runtime should spend an LLM turn.
"""
from __future__ import annotations

import json
import os
from datetime import UTC, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

from .live_context import _income_markets_from_dict, build_live_context, load_context_inputs

NEW_YORK = ZoneInfo("America/New_York")
RISK_SCORE_TRIGGER = 5.0
PRICE_CHANGE_TRIGGER = 0.01
IV_CHANGE_TRIGGER = 0.03
LLM_BACKSTOP_SECONDS = 15 * 60
EVENT_KINDS = frozenset({"tick_success", "tick_failure", "llm_attempt", "llm_success", "llm_failure"})


def _path() -> Path:
    return Path(os.getenv("AGENT_HEARTBEAT_PATH", "state/heartbeat.json"))


def _load() -> dict:
    try:
        return json.loads(_path().read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save(value: dict) -> None:
    target = _path()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")
    temporary.replace(target)


def record_event(kind: str, *, failure_code: str | None = None, now: datetime | None = None) -> dict:
    """Persist minimal scheduler telemetry; no context, provider payload, or order data."""
    if kind not in EVENT_KINDS:
        raise ValueError(f"unsupported heartbeat event: {kind}")
    now = now or datetime.now(UTC)
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    state = _load()
    timestamp = now.timestamp()
    state["last_run_kind"] = os.getenv("AGENT_HEARTBEAT_RUN_KIND", "service")
    if kind == "tick_success":
        state["last_successful_tick_at"] = timestamp
        state["consecutive_tick_failures"] = 0
    elif kind == "tick_failure":
        state["last_tick_failure_at"] = timestamp
        state["consecutive_tick_failures"] = int(state.get("consecutive_tick_failures", 0)) + 1
    elif kind == "llm_attempt":
        state["last_llm_attempt_at"] = timestamp
    elif kind == "llm_success":
        state["last_llm_success_at"] = timestamp
        state["consecutive_llm_failures"] = 0
    else:
        state["last_llm_failure_at"] = timestamp
        state["consecutive_llm_failures"] = int(state.get("consecutive_llm_failures", 0)) + 1
    if failure_code:
        state["last_failure_code"] = failure_code[:80]
    _save(state)
    return state


def session_phase(now: datetime) -> tuple[str, str | None]:
    """Return the trading-session phase and daily key, in New York time."""
    local = now.astimezone(NEW_YORK)
    if local.weekday() >= 5:
        return "sleep", None
    day = local.date().isoformat()
    value = local.timetz().replace(tzinfo=None)
    if time(20, 0) <= value or value < time(4, 0):
        return "sleep", None
    if value < time(9, 30):
        return "preopen", day
    if value < time(16, 0):
        return "market", day
    return "postclose", day


def _market_facts(context, income_markets: dict) -> dict[str, dict[str, float]]:
    facts = {}
    for symbol, market in income_markets.items():
        facts[symbol] = {"price": float(market.index_price), "iv": float(market.index_iv)}
    return facts


def _changes(previous: dict, *, risk: dict, candidates: list[str], markets: dict, broker: dict) -> list[str]:
    if not previous:
        return ["initial_snapshot"]
    reasons: list[str] = []
    old_risk = previous.get("risk", {})
    if abs(float(risk["risk_score"]) - float(old_risk.get("risk_score", risk["risk_score"]))) >= RISK_SCORE_TRIGGER:
        reasons.append("risk_score_changed")
    if candidates != previous.get("candidates"):
        reasons.append("candidate_set_changed")
    for symbol, value in markets.items():
        prior = previous.get("markets", {}).get(symbol)
        if not prior:
            reasons.append("underlying_available")
            continue
        old_price = float(prior.get("price", 0.0))
        if old_price > 0 and abs(value["price"] - old_price) / old_price >= PRICE_CHANGE_TRIGGER:
            reasons.append(f"price_changed:{symbol}")
        if abs(value["iv"] - float(prior.get("iv", value["iv"]))) >= IV_CHANGE_TRIGGER:
            reasons.append(f"iv_changed:{symbol}")
    if broker != previous.get("broker"):
        reasons.append("portfolio_or_order_changed")
    return reasons


def evaluate_tick(*, now: datetime | None = None, source=None, state=None, force_market: bool = False) -> dict:
    """Observe one tick and return a sanitized scheduling decision.

    Market ticks build a fresh context every five minutes.  Pre-open and
    post-close ticks do the same deterministic portfolio/risk reconciliation,
    but do not arm option execution outside regular trading hours.
    """
    now = now or datetime.now(UTC)
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    phase, day = session_phase(now)
    if force_market:
        phase = "market"
        day = now.astimezone(NEW_YORK).date().isoformat()
    if phase == "sleep":
        # A sleep tick is still evidence that the scheduler is alive.  Do not
        # require an LLM or live observation outside the scheduled session.
        previous = _load()
        previous.update({
            "last_tick_at": now.timestamp(),
            "last_successful_tick_at": now.timestamp(),
            "last_phase": phase,
            "last_run_kind": os.getenv("AGENT_HEARTBEAT_RUN_KIND", "service"),
        })
        _save(previous)
        return {"phase": "sleep", "llm_due": False, "reasons": ["scheduled_sleep"]}

    previous = _load()
    if phase in {"preopen", "postclose"} and previous.get(f"{phase}_done") == day:
        return {"phase": phase, "llm_due": False, "reasons": [f"{phase}_already_reconciled"]}
    context = build_live_context(source=source, state=state, execution_mode="autonomous-paper", now=now)
    stored = load_context_inputs(context.context_id) or {}
    income_markets = _income_markets_from_dict(stored.get("income_markets"))
    option_market_observation = stored.get("option_market_observation", {})
    markets = _market_facts(context, income_markets)
    risk = {
        "risk_score": float(context.snapshot.risk_score),
        "drawdown": float(context.snapshot.drawdown),
        "var_95": float(context.snapshot.var_95),
    }
    candidates = [candidate.candidate_id for candidate in context.candidates]
    broker = stored.get("execution_snapshot", {})
    reasons = _changes(previous.get("observation", {}), risk=risk, candidates=candidates, markets=markets, broker=broker)
    # Scheduling records when a model turn became due.  The Node runtime records
    # the actual attempt/success/failure separately, so a failed spawn cannot
    # masquerade as an attempted provider call.
    last_llm = previous.get("last_llm_scheduled_at")
    elapsed = (now.timestamp() - float(last_llm)) if last_llm is not None else None
    periodic = elapsed is None or elapsed >= LLM_BACKSTOP_SECONDS
    phase_once = previous.get(f"{phase}_done") != day
    # Options are not tradable pre/post market.  Those phases reconcile state
    # once daily in Python and deliberately do not create an executable proposal.
    llm_due = phase == "market" and bool(reasons or periodic)
    # Preserve runtime telemetry written by the Node loop. Observation updates
    # must not erase LLM/tick outcome evidence from the immediately prior cycle.
    state_row = dict(previous)
    state_row.update({
        "observation": {
            "risk": risk,
            "candidates": candidates,
            "markets": markets,
            "broker": broker,
            # Per-symbol availability is safe operational telemetry.  It has no
            # account data, prices, provider messages, credentials, or orders.
            "option_market_observation": option_market_observation,
        },
        "last_tick_at": now.timestamp(),
        "last_successful_tick_at": now.timestamp(),
        "last_phase": phase,
        "last_run_kind": os.getenv("AGENT_HEARTBEAT_RUN_KIND", "service"),
        f"{phase}_done": day,
        "last_llm_scheduled_at": now.timestamp() if llm_due else last_llm,
    })
    _save(state_row)
    return {
        "phase": phase,
        "llm_due": llm_due,
        "reasons": reasons + (["fifteen_minute_backstop"] if periodic and phase == "market" else []) + (
            [f"{phase}_daily_reconciliation"] if phase_once and phase != "market" else []
        ),
        "option_market_observation": option_market_observation,
        "context": context.to_model_dict() if llm_due else None,
    }
