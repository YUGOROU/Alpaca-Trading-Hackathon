"""Thin agent layer over the deterministic portfolio/risk engine."""

from .candidates import build_decision_context
from .contracts import AgentDecision, DecisionCandidate, DecisionContext, GateResult
from .gate import validate_decision

__all__ = [
    "AgentDecision",
    "DecisionCandidate",
    "DecisionContext",
    "GateResult",
    "build_decision_context",
    "validate_decision",
]
