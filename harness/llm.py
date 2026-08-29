"""The DECIDE step — approve the risk-validated plan (README §4).

Real judgment (approve / reduce / skip based on soft context) now lives in the **DSH
harness**, which owns the model brain and can approve all trades. This in-house node is
therefore just the deterministic stub: the engine has already chosen and risk-validated
the plan, so `default_decider` approves it unchanged and the cycle runs end-to-end.

The retired in-house LLM decider (`make_llm_decider`, backed by the langchain model
client) is preserved under `_archive/harness/llm.original.py` if it's ever needed again.

Returns the decision shape the rest of the loop expects:
    {approved, posture, income_legs, hedge, reasoning}
"""
from __future__ import annotations


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
