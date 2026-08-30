"""JSON-only bridge used by the DSH bundle.

Two provenance modes, same JSON contract:
  * `--scenario <name>` — fixed fixtures (offline/replay/tests). Deterministic, so
    `submit` safely rebuilds the same context.
  * `--live`            — a real `feed.observe(...)` snapshot. Because live data moves
    between the `context` and `submit` calls, `context` persists its inputs and `submit`
    rebuilds from them (see agent/live_context.py) so the gate's context_id check holds.
"""

from __future__ import annotations

import argparse
import json

from dataclasses import replace

from .candidates import build_decision_context
from .contracts import AgentDecision, GateResult, validate_execution_mode
from .gate import validate_decision
from .ledger import proposal_context_id, proposal_orders, record_dry_run, record_human_approval, record_submission_requested
from .live_context import _live_source_and_state, build_live_context, build_mock_context, rebuild_live_context, rebuild_observed_context
from .revalidation import revalidate_live_context
from .scenarios import get_scenario


def _add_mode(parser: argparse.ArgumentParser) -> None:
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--scenario", help="fixed fixture name (offline/replay)")
    group.add_argument("--mock", action="store_true", help="explicit mock observation (never live)")
    group.add_argument("--live", action="store_true", help="observe live Alpaca data")


def _scenario_context(scenario: str):
    fixture = get_scenario(scenario)
    context = build_decision_context(
        fixture.portfolio,
        fixture.market,
        scenario_id=scenario,
        current_contracts=fixture.current_contracts,
        income_open=fixture.income_open,
    )
    if not fixture.injected_data_note:
        return context
    candidates = tuple(
        replace(candidate, thesis=f"{candidate.thesis} {fixture.injected_data_note}")
        for candidate in context.candidates
    )
    return replace(context, candidates=candidates)


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

    context_parser = sub.add_parser("context")
    _add_mode(context_parser)

    submit_parser = sub.add_parser("submit")
    _add_mode(submit_parser)
    submit_parser.add_argument("--context-id", required=True)
    submit_parser.add_argument("--candidate-id", required=True)
    submit_parser.add_argument("--reason", required=True)
    submit_parser.add_argument("--decision-id", required=True)
    submit_parser.add_argument("--ledger", required=True)
    submit_parser.add_argument("--execution-mode", choices=("human", "autonomous-paper"), default="human")

    approve_parser = sub.add_parser("approve")
    approve_parser.add_argument("--ledger", required=True)
    approve_parser.add_argument("--decision-id", required=True)
    approve_parser.add_argument("--approved-by", required=True)

    prepare_parser = sub.add_parser("prepare-submission")
    prepare_parser.add_argument("--ledger", required=True)
    prepare_parser.add_argument("--decision-id", required=True)

    args = parser.parse_args()
    if args.command == "approve":
        row = record_human_approval(args.ledger, args.decision_id, approved_by=args.approved_by)
        print(json.dumps(row, sort_keys=True))
        return 0
    if args.command == "prepare-submission":
        source, _state = _live_source_and_state()
        # The canonical proposal supplies the only admissible context/order set.
        context_id = proposal_context_id(args.ledger, args.decision_id)
        check = revalidate_live_context(context_id, source)
        if not check["ok"]:
            print(json.dumps(check, sort_keys=True))
            return 2
        event = record_submission_requested(args.ledger, args.decision_id, revalidation=check)
        print(json.dumps({"event": event, "orders": proposal_orders(args.ledger, args.decision_id)}, sort_keys=True))
        return 0

    scenario_id = "live" if args.live else "mock" if args.mock else args.scenario

    if args.command == "context":
        context = build_live_context() if args.live else build_mock_context() if args.mock else _scenario_context(args.scenario)
        print(json.dumps(context.to_model_dict(), sort_keys=True))
        return 0

    # submit: rebuild the exact context the model chose from, then validate.
    if args.live:
        context = rebuild_live_context(args.context_id)
    elif args.mock:
        context = rebuild_observed_context(args.context_id, expected_source="mock")
    else:
        context = _scenario_context(args.scenario)

    decision = AgentDecision(args.context_id, args.candidate_id, args.reason)
    if context is None:
        # Unknown, expired, or non-live persisted contexts must never trigger a
        # replacement observation during submission.  Record the rejection so it
        # remains auditable without turning a stale decision into a fresh one.
        result = GateResult("rejected", args.context_id, args.candidate_id, ("stale_or_unknown_context",), ())
    else:
        result = validate_decision(context, decision)
    row = record_dry_run(
        args.ledger, args.decision_id, scenario_id, decision, result,
        execution_mode=validate_execution_mode(args.execution_mode),
    )
    print(json.dumps(row, sort_keys=True))
    return 0 if result.approved else 2


if __name__ == "__main__":
    raise SystemExit(main())
