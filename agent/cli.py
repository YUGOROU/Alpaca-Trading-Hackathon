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
from pathlib import Path

from dataclasses import replace

from .candidates import AUTONOMOUS_OPTION_UNDERLYINGS, build_decision_context
from .contracts import AgentDecision, GateResult, validate_execution_mode
from .gate import validate_decision
from .ledger import (
    proposal_context_id,
    proposal_orders,
    record_broker_update,
    record_autonomous_authorization,
    record_dry_run,
    record_human_approval,
    record_human_rejection,
    record_submission_failure,
    record_submission_unknown,
    record_submission_requested,
)
from .contract_provenance import recorded_protective_put_contracts
from .live_context import _live_source_and_state, build_live_context, build_mock_context, rebuild_live_context, rebuild_observed_context
from .revalidation import revalidate_live_context
from .scenarios import get_scenario

AUTONOMOUS_OPTIONS_SYMBOLS = frozenset(AUTONOMOUS_OPTION_UNDERLYINGS)

def _add_mode(parser: argparse.ArgumentParser) -> None:
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--scenario", help="fixed fixture name (offline/replay)")
    group.add_argument("--mock", action="store_true", help="explicit mock observation (never live)")
    group.add_argument("--live", action="store_true", help="observe live Alpaca data")


def _scenario_context(scenario: str, *, execution_mode: str = "human"):
    fixture = get_scenario(scenario)
    context = build_decision_context(
        fixture.portfolio,
        fixture.market,
        scenario_id=scenario,
        current_contracts=fixture.current_contracts,
        income_open=fixture.income_open,
        execution_mode=execution_mode,
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
    context_parser.add_argument("--execution-mode", choices=("human", "autonomous-paper"), default="human")

    monitor_parser = sub.add_parser("monitor")
    _add_mode(monitor_parser)
    monitor_parser.add_argument("--force-market", action="store_true", help=argparse.SUPPRESS)

    heartbeat_event_parser = sub.add_parser("heartbeat-event")
    heartbeat_event_parser.add_argument("--kind", required=True, choices=("tick_success", "tick_failure", "llm_attempt", "llm_success", "llm_failure"))
    heartbeat_event_parser.add_argument("--failure-code")

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

    autonomous_parser = sub.add_parser("authorize-autonomous")
    autonomous_parser.add_argument("--ledger", required=True)
    autonomous_parser.add_argument("--decision-id", required=True)

    reject_parser = sub.add_parser("reject")
    reject_parser.add_argument("--ledger", required=True)
    reject_parser.add_argument("--decision-id", required=True)
    reject_parser.add_argument("--rejected-by", required=True)

    prepare_parser = sub.add_parser("prepare-submission")
    prepare_parser.add_argument("--ledger", required=True)
    prepare_parser.add_argument("--decision-id", required=True)
    prepare_parser.add_argument("--require-exactly-one-order", action="store_true")
    prepare_parser.add_argument("--autonomous-options-overlay", action="store_true")

    broker_parser = sub.add_parser("record-broker-update")
    broker_parser.add_argument("--ledger", required=True)
    broker_parser.add_argument("--decision-id", required=True)
    broker_parser.add_argument("--state", required=True)
    broker_parser.add_argument("--broker-orders-json", required=True)

    failure_parser = sub.add_parser("record-submission-failure")
    failure_parser.add_argument("--ledger", required=True)
    failure_parser.add_argument("--decision-id", required=True)
    failure_parser.add_argument("--reason", required=True)

    unknown_parser = sub.add_parser("record-submission-unknown")
    unknown_parser.add_argument("--ledger", required=True)
    unknown_parser.add_argument("--decision-id", required=True)
    unknown_parser.add_argument("--client-order-ids-json", required=True)
    unknown_parser.add_argument("--reason", required=True)

    reconcile_parser = sub.add_parser("reconcile")
    reconcile_parser.add_argument("--ledger", required=True)
    reconcile_parser.add_argument("--decision-id", required=True)

    provenance_parser = sub.add_parser("record-protective-put-open")
    provenance_parser.add_argument("--ledger", required=True)
    provenance_parser.add_argument("--decision-id", required=True)
    provenance_parser.add_argument("--contract", required=True)
    provenance_parser.add_argument("--quantity", required=True, type=int)
    provenance_parser.add_argument("--broker-order-id", required=True)

    recorded_contracts_parser = sub.add_parser("recorded-protective-put-contracts")
    recorded_contracts_parser.add_argument("--ledger", required=True)

    args = parser.parse_args()
    if args.command == "monitor":
        if not args.live:
            raise ValueError("heartbeat monitor requires --live")
        from .heartbeat import evaluate_tick
        print(json.dumps(evaluate_tick(force_market=args.force_market), sort_keys=True))
        return 0
    if args.command == "heartbeat-event":
        from .heartbeat import record_event
        print(json.dumps(record_event(args.kind, failure_code=args.failure_code), sort_keys=True))
        return 0
    if args.command == "approve":
        row = record_human_approval(args.ledger, args.decision_id, approved_by=args.approved_by)
        print(json.dumps(row, sort_keys=True))
        return 0
    if args.command == "authorize-autonomous":
        row = record_autonomous_authorization(args.ledger, args.decision_id)
        print(json.dumps(row, sort_keys=True))
        return 0
    if args.command == "recorded-protective-put-contracts":
        print(json.dumps({"contracts": recorded_protective_put_contracts(args.ledger)}, sort_keys=True))
        return 0
    if args.command == "reject":
        row = record_human_rejection(args.ledger, args.decision_id, rejected_by=args.rejected_by)
        print(json.dumps(row, sort_keys=True))
        return 0
    if args.command == "prepare-submission":
        orders = proposal_orders(args.ledger, args.decision_id)
        # Validate the executor's bounded shape before reserving a submission
        # transition. A hold/no-order or multi-order proposal remains approvable
        # and reviewable instead of becoming an unrecoverable submission request.
        if args.require_exactly_one_order:
            if len(orders) != 1:
                raise ValueError("the initial human executor requires exactly one gate order")
            if orders[0].get("intent") == "sell_to_close" and not args.autonomous_options_overlay:
                raise ValueError("sell_to_close requires a recorded held OCC contract and is not yet executable")
        if args.autonomous_options_overlay:
            if len(orders) != 1:
                raise ValueError("autonomous options execution requires exactly one gate order")
            order = orders[0]
            if order.get("structure") not in {
                "protective_put", "covered_call", "iron_condor", "bull_put_spread", "bear_call_spread",
            }:
                raise ValueError("autonomous execution permits only known options-overlay structures")
            if order.get("symbol") not in AUTONOMOUS_OPTIONS_SYMBOLS:
                raise ValueError("autonomous options execution permits only approved overlays")
            if order.get("intent") not in {"buy_to_open", "sell_to_open", "sell_to_close"}:
                raise ValueError("autonomous options execution permits only bounded opening or protective-put close orders")
            if order.get("intent") == "sell_to_close":
                if order.get("structure") != "protective_put" or order.get("symbol") != "SPY":
                    raise ValueError("autonomous close orders are limited to recorded SPY protective puts")
                if not recorded_protective_put_contracts(args.ledger):
                    raise ValueError("autonomous close requires a recorded SPY protective-put contract")
        source, _state = _live_source_and_state()
        # The canonical proposal supplies the only admissible context/order set.
        context_id = proposal_context_id(args.ledger, args.decision_id)
        check = revalidate_live_context(
            context_id, source, require_no_open_orders=args.autonomous_options_overlay,
        )
        if not check["ok"]:
            print(json.dumps(check, sort_keys=True))
            return 2
        event = record_submission_requested(args.ledger, args.decision_id, revalidation=check)
        print(json.dumps({"event": event, "orders": orders}, sort_keys=True))
        return 0
    if args.command == "record-broker-update":
        orders = json.loads(args.broker_orders_json)
        if not isinstance(orders, list):
            raise ValueError("broker_orders_json must encode a list")
        row = record_broker_update(
            args.ledger, args.decision_id, state=args.state, broker_orders=orders
        )
        print(json.dumps(row, sort_keys=True))
        return 0
    if args.command == "record-submission-failure":
        row = record_submission_failure(args.ledger, args.decision_id, reason=args.reason)
        print(json.dumps(row, sort_keys=True))
        return 0
    if args.command == "record-submission-unknown":
        client_order_ids = json.loads(args.client_order_ids_json)
        if not isinstance(client_order_ids, list):
            raise ValueError("client_order_ids_json must encode a list")
        row = record_submission_unknown(
            args.ledger, args.decision_id, client_order_ids=client_order_ids, reason=args.reason
        )
        print(json.dumps(row, sort_keys=True))
        return 0
    if args.command == "reconcile":
        rows = [json.loads(line) for line in Path(args.ledger).read_text().splitlines() if line.strip()]
        broker = next(
            (row for row in reversed(rows) if row.get("decision_id") == args.decision_id and row.get("broker_orders")),
            None,
        )
        source, _state = _live_source_and_state()
        if broker is not None:
            observed = [source.order_status(order["alpaca_order_id"]) for order in broker["broker_orders"]]
        else:
            unknown = next(
                (row for row in reversed(rows) if row.get("decision_id") == args.decision_id and row.get("event") == "submission_unknown"),
                None,
            )
            if unknown is None:
                raise ValueError("no broker order ids or uncertain submission keys recorded for decision")
            observed = [source.order_status_by_client_order_id(value) for value in unknown["submission"]["client_order_ids"]]
        states = {order["state"] for order in observed}
        if len(states) != 1 or next(iter(states)) not in {"accepted", "partially_filled", "filled", "rejected", "canceled", "expired"}:
            raise ValueError(f"unreconcilable broker statuses: {sorted(states)}")
        row = record_broker_update(
            args.ledger, args.decision_id, state=next(iter(states)), broker_orders=observed
        )
        print(json.dumps(row, sort_keys=True))
        return 0
    if args.command == "record-protective-put-open":
        from .contract_provenance import record_protective_put_open
        row = record_protective_put_open(
            args.ledger, decision_id=args.decision_id, contract=args.contract,
            quantity=args.quantity, broker_order_id=args.broker_order_id,
        )
        print(json.dumps(row, sort_keys=True))
        return 0

    scenario_id = "live" if args.live else "mock" if args.mock else args.scenario

    if args.command == "context":
        if args.live:
            context = build_live_context(execution_mode=args.execution_mode)
        elif args.mock:
            context = build_mock_context(execution_mode=args.execution_mode)
        else:
            fixture = get_scenario(args.scenario)
            context = build_decision_context(
                fixture.portfolio, fixture.market, scenario_id=args.scenario,
                current_contracts=fixture.current_contracts, income_open=fixture.income_open,
                execution_mode=args.execution_mode,
            )
        print(json.dumps(context.to_model_dict(), sort_keys=True))
        return 0

    # submit: rebuild the exact context the model chose from, then validate.
    if args.live:
        context = rebuild_live_context(args.context_id)
    elif args.mock:
        context = rebuild_observed_context(args.context_id, expected_source="mock")
    else:
        context = _scenario_context(args.scenario, execution_mode=args.execution_mode)

    decision = AgentDecision(args.context_id, args.candidate_id, args.reason)
    if context is None:
        # Unknown, expired, or non-live persisted contexts must never trigger a
        # replacement observation during submission.  Record the rejection so it
        # remains auditable without turning a stale decision into a fresh one.
        result = GateResult("rejected", args.context_id, args.candidate_id, ("stale_or_unknown_context",), ())
        execution_mode = validate_execution_mode(args.execution_mode)
    elif context.execution_mode != args.execution_mode:
        # The mode is part of the context identity and its approval contract.
        # Never let a submit-time flag relabel a human-reviewed context as an
        # autonomous proposal (or vice versa).
        result = GateResult("rejected", args.context_id, args.candidate_id, ("execution_mode_mismatch",), ())
        execution_mode = context.execution_mode
    else:
        result = validate_decision(context, decision)
        execution_mode = context.execution_mode
    row = record_dry_run(
        args.ledger, args.decision_id, scenario_id, decision, result,
        execution_mode=execution_mode,
    )
    print(json.dumps(row, sort_keys=True))
    return 0 if result.approved else 2


if __name__ == "__main__":
    raise SystemExit(main())
