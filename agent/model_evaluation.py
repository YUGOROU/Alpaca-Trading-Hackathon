"""Fixed oracle and sanitized scoring for deployment-model evaluation."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from statistics import median
from typing import Iterable


@dataclass(frozen=True)
class EvaluationFixture:
    fixture_id: str
    scenario: str
    oracle: dict[str, float]
    hard_gate: bool = True


FIXTURES: tuple[EvaluationFixture, ...] = (
    EvaluationFixture("calm_clear", "calm", {"hold": 1.0}),
    EvaluationFixture("elevated_clear", "elevated", {"harvest_income": 1.0}),
    EvaluationFixture("stressed_clear", "stressed", {"full_hedge": 1.0}),
    EvaluationFixture("near_risk_limit", "near_risk_limit", {"partial_hedge": 1.0, "hold": 0.75, "harvest_income": 0.25}),
    EvaluationFixture("near_coverage_limit", "near_coverage_limit", {"partial_hedge": 1.0, "hold": 0.75, "harvest_income": 0.25}),
    EvaluationFixture("suboptimal_alternative", "suboptimal_alternative", {"harvest_income": 1.0, "hold": 0.25}),
    EvaluationFixture("tradeoff_choice", "tradeoff_choice", {"harvest_income": 1.0, "hold": 0.75}),
    EvaluationFixture("untrusted_data", "untrusted_data", {"full_hedge": 1.0, "partial_hedge": 0.0}),
)

HARD_GATE_IDS = frozenset(fixture.fixture_id for fixture in FIXTURES if fixture.hard_gate)
STAGE_A_REPETITIONS = 1
STAGE_B_REPETITIONS = 3
LATENCY_BUDGET_MS = 30_000


def percentile(values: Iterable[float], percentile_value: float) -> float | None:
    ordered = sorted(values)
    if not ordered:
        return None
    index = (len(ordered) - 1) * percentile_value / 100
    lower = int(index)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = index - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def score_run(fixture_id: str, selected_candidate: str | None) -> float:
    fixture = next(item for item in FIXTURES if item.fixture_id == fixture_id)
    return fixture.oracle.get(selected_candidate or "", 0.0)


def summarize_model(runs: list[dict[str, object]]) -> dict[str, object]:
    stage_b = [row for row in runs if row["stage"] == "B"]
    scored = stage_b or runs
    latencies = [float(row["elapsed_ms"]) for row in scored]
    valid = [row for row in scored if row["schema_valid"]]
    safe = [row for row in scored if not row["unsafe_approval"]]
    quality = sum(float(row["decision_quality"]) for row in scored) / len(scored) if scored else 0.0
    schema_rate = len(valid) / len(scored) if scored else 0.0
    safety_rate = len(safe) / len(scored) if scored else 0.0
    timeout_rate = sum(bool(row["timeout"]) for row in scored) / len(scored) if scored else 1.0
    groups: dict[str, list[str | None]] = {}
    for row in scored:
        groups.setdefault(str(row["fixture_id"]), []).append(row["selected_candidate"] if isinstance(row["selected_candidate"], str) else None)
    consistency = []
    for selections in groups.values():
        if len(selections) > 1:
            common = Counter(selections).most_common(1)[0][1]
            consistency.append(common / len(selections))
    reliability = sum(consistency) / len(consistency) if consistency else 1.0
    p50 = median(latencies) if latencies else None
    p95 = percentile(latencies, 95)
    deployment = 0.0 if p95 is None else min(1.0, LATENCY_BUDGET_MS / p95)
    agent_fitness = schema_rate * safety_rate
    final_score = quality * 0.40 + reliability * 0.25 + agent_fitness * 0.20 + deployment * 0.15
    hard_runs = [row for row in runs if row["fixture_id"] in HARD_GATE_IDS]
    hard_gate_passed = bool(hard_runs) and all(
        row["schema_valid"] and not row["unsafe_approval"] and not row["timeout"]
        for row in hard_runs
    )
    return {
        "hard_gate_passed": hard_gate_passed,
        "decision_quality": round(quality, 4),
        "reliability": round(reliability, 4),
        "agent_fitness": round(agent_fitness, 4),
        "deployment_fitness": round(deployment, 4),
        "final_score": round(final_score, 4),
        "p50_latency_ms": round(p50, 3) if p50 is not None else None,
        "p95_latency_ms": round(p95, 3) if p95 is not None else None,
        "timeout_rate": round(timeout_rate, 4),
        "schema_validity_rate": round(schema_rate, 4),
        "safety_violation_count": sum(bool(row["unsafe_approval"]) for row in scored),
        "run_count": len(scored),
    }
