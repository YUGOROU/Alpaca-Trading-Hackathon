"""The DECIDE step — the one place a model makes a judgment call (README §4, §5).

Two deciders:
  * `default_decider`   — STUB: approve the risk-validated plan unchanged (no model).
  * `make_llm_decider()` — the real LLM decider, but SAFETY-BOUNDED: the engine has
    already chosen and risk-validated the plan, so the model may only **approve /
    reduce / skip** based on soft context (a catalyst before expiry, regime nuance).
    It can never invent trades or upsize past what was validated, and the tail hedge
    is always kept. On any model error it falls back (default: approve) so the loop
    keeps running.

Both return the same decision shape, so nothing downstream changes:
    {approved, posture, income_legs, hedge, reasoning}
"""
from __future__ import annotations

import json


def default_decider(context: dict) -> dict:
    """Approve the risk-validated plan as-is (deterministic stub, no model)."""
    plan = context["plan"]
    return {
        "approved": True,
        "posture": plan["posture"],
        "income_legs": plan["income"]["legs"],
        "hedge": plan["hedge"],
        "reasoning": "stub decider: approve the risk-validated plan unchanged",
    }


_SYSTEM = (
    "You are the DECIDE step of an autonomous options INCOME agent on Alpaca paper "
    "trading. A deterministic engine has ALREADY chosen and risk-validated a plan of "
    "defined-risk option trades (weekly premium selling + a tail hedge). Your ONLY job "
    "is a judgment the numbers can't see: approve it, reduce its size, or skip this "
    "cycle, based on soft context (a major catalyst before expiry, regime nuance, "
    "anything unusual).\n"
    "RULES:\n"
    "- You may NOT invent new trades, strikes, or sizes. Only: approve / reduce / skip.\n"
    "- Prefer approve unless there is a clear reason for caution.\n"
    "- The tail hedge is always kept; you govern only the income (premium-selling) side.\n"
    "- Reply with ONLY a JSON object: "
    '{"action":"approve|reduce|skip","size_factor":0.0-1.0,"reasoning":"one sentence"}. '
    "size_factor is the fraction of the proposed size to keep (used for 'reduce')."
)


def _prompt_context(context: dict) -> dict:
    """A trimmed, token-light view of the cycle for the model to reason over."""
    plan = context["plan"]
    inc = plan["income"]
    return {
        "posture": plan["posture"],
        "snapshot": context["snapshot"],
        "income": {
            "aggressiveness": inc["aggressiveness"],
            "total_credit": inc["total_credit"],
            "net_theta_per_day": inc["net_theta_per_day"],
            "total_max_loss": inc["total_max_loss"],
            "legs": [{"kind": l["kind"], "symbol": l["symbol"],
                      "contracts": l["contracts"]} for l in inc["legs"]],
        },
        "hedge": {"action": plan["hedge"]["action"],
                  "contracts_target": plan["hedge"]["contracts_target"]},
        "validation": context["validation"],
    }


def _parse_decision(raw: str) -> dict:
    """Pull the JSON decision object out of the model's reply (tolerant of fences)."""
    s = raw.strip()
    start, end = s.find("{"), s.rfind("}")
    if start == -1 or end == -1:
        raise ValueError(f"no JSON object in model reply: {raw!r}")
    obj = json.loads(s[start : end + 1])
    action = str(obj.get("action", "approve")).lower()
    if action not in ("approve", "reduce", "skip"):
        action = "approve"
    try:
        factor = float(obj.get("size_factor", 1.0))
    except (TypeError, ValueError):
        factor = 1.0
    return {"action": action, "size_factor": max(0.0, min(1.0, factor)),
            "reasoning": str(obj.get("reasoning", ""))[:300]}


def _scale_leg_dict(leg: dict, factor: float) -> dict | None:
    """Scale a leg dict's size by `factor` (0..1); None if it rounds to nothing."""
    new_n = int(round(leg["contracts"] * factor))
    if new_n <= 0:
        return None
    ratio = new_n / leg["contracts"]
    return {**leg, "contracts": new_n,
            "credit": leg["credit"] * ratio, "max_loss": leg["max_loss"] * ratio,
            "capital_reserved": leg["capital_reserved"] * ratio,
            "theta_per_day": leg["theta_per_day"] * ratio}


def _decision_from(plan: dict, action: str, factor: float, reasoning: str) -> dict:
    legs = plan["income"]["legs"]
    if action == "skip":
        legs, approved = [], False
    elif action == "reduce":
        legs = [nl for l in legs if (nl := _scale_leg_dict(l, factor)) is not None]
        approved = True
    else:  # approve
        approved = True
    return {
        "approved": approved,
        "posture": plan["posture"],
        "income_legs": legs,
        "hedge": plan["hedge"],  # tail hedge is never the LLM's to drop
        "reasoning": reasoning or f"LLM: {action}",
    }


def make_llm_decider(
    completion_fn=None, *, role: str = "THESIS", fallback: str = "approve", skills: str = ""
):
    """Build a decider backed by an LLM.

    `completion_fn(system, user) -> str` lets you inject any model (or a fake in tests);
    when omitted it uses `agent.llm.complete` with the `role`'s configured provider.
    `skills` is Alpaca SKILL.md reference text (see skills.py) injected into the prompt so
    the model has domain know-how. On any error it falls back to `fallback` (approve).
    """
    system = _SYSTEM
    if skills:
        system += "\n\nREFERENCE — Alpaca skills (domain know-how you may use):\n" + skills

    def decider(context: dict) -> dict:
        plan = context["plan"]
        try:
            fn = completion_fn or _real_completion(role)
            user = "This cycle's context:\n" + json.dumps(_prompt_context(context)) + "\nDecide."
            parsed = _parse_decision(fn(system, user))
        except Exception as e:  # model/key/parse failure -> keep the loop alive, safely
            return _decision_from(
                plan, fallback, 1.0,
                f"LLM unavailable ({type(e).__name__}); fell back to '{fallback}'",
            )
        return _decision_from(plan, parsed["action"], parsed["size_factor"], parsed["reasoning"])

    return decider


def _real_completion(role: str):
    def _fn(system: str, user: str) -> str:
        from model.llm import complete

        return complete(system, user, role=role)
    return _fn
