"""Small structured contracts shared by the math bridge, DSH, and risk gate."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from risk_engine import RiskSnapshot, StrategyPlan


@dataclass(frozen=True)
class CandidateTradeoffs:
    """Compact facts the model may compare without doing arithmetic itself."""

    target_coverage: float
    cashflow_today: float
    daily_theta: float
    capital_reserved: float
    defined_risk: float
    linear_hedge_adjusted_pnl_5pct: float

    def to_dict(self) -> dict[str, float]:
        return {
            "target_coverage": self.target_coverage,
            "cashflow_today": self.cashflow_today,
            "daily_theta": self.daily_theta,
            "capital_reserved": self.capital_reserved,
            "defined_risk": self.defined_risk,
            "linear_hedge_adjusted_pnl_5pct": self.linear_hedge_adjusted_pnl_5pct,
        }


@dataclass(frozen=True)
class DecisionCandidate:
    """One deterministic, bounded portfolio action available to the agent."""

    candidate_id: str
    action: str
    label: str
    thesis: str
    tradeoffs: CandidateTradeoffs
    hedge_symbol: str
    plan: StrategyPlan

    def to_model_dict(self) -> dict[str, Any]:
        """Exclude exact orders and contract counts from the model-visible view."""
        return {
            "candidate_id": self.candidate_id,
            "action": self.action,
            "label": self.label,
            "thesis": self.thesis,
            "tradeoffs": self.tradeoffs.to_dict(),
        }


@dataclass(frozen=True)
class DecisionContext:
    """Authoritative snapshot plus the complete set of admissible candidates."""

    context_id: str
    scenario_id: str
    snapshot: RiskSnapshot
    candidates: tuple[DecisionCandidate, ...]
    input_provenance: dict[str, Any] | None = None
    execution_mode: str = "human"

    def to_model_dict(self) -> dict[str, Any]:
        snapshot = self.snapshot
        result = {
            "context_id": self.context_id,
            "scenario_id": self.scenario_id,
            "risk": {
                "equity": snapshot.equity,
                "beta_weighted_exposure": snapshot.beta_weighted_delta,
                "annual_vol": snapshot.annual_vol,
                "drawdown": snapshot.drawdown,
                "regime_signal": snapshot.regime_signal,
                "var_95_1d": snapshot.var_95,
                "iv_rank": snapshot.iv_rank,
                "risk_score": snapshot.risk_score,
            },
            "candidates": [candidate.to_model_dict() for candidate in self.candidates],
            "decision_contract": {
                "choose_exactly_one_candidate_id": True,
                "exact_order_sizing_owned_by": "deterministic_gate",
                "human_approval_required_before_submission": self.execution_mode == "human",
                "execution_mode": self.execution_mode,
            },
        }
        if self.input_provenance is not None:
            result["input_provenance"] = self.input_provenance
        return result


@dataclass(frozen=True)
class AgentDecision:
    context_id: str
    candidate_id: str
    reason: str


@dataclass(frozen=True)
class GateResult:
    status: str
    context_id: str
    candidate_id: str
    reasons: tuple[str, ...]
    orders: tuple[dict[str, Any], ...]
    human_approval_required: bool = True

    @property
    def approved(self) -> bool:
        return self.status == "approved_for_dry_run"

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "context_id": self.context_id,
            "candidate_id": self.candidate_id,
            "reasons": list(self.reasons),
            "orders": list(self.orders),
            "human_approval_required": self.human_approval_required,
        }


EXECUTION_MODES = frozenset({"human", "autonomous-paper"})


def validate_execution_mode(mode: str) -> str:
    """Return a supported execution mode or fail closed.

    ``approved_for_dry_run`` is deliberately independent from this setting: it
    authorizes a proposal record, never a broker submission.
    """
    if mode not in EXECUTION_MODES:
        raise ValueError(f"unknown execution mode: {mode}")
    return mode
