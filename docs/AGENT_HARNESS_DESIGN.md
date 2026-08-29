# Regime-Adaptive Portfolio Agent — Minimal DSH Design

Status: approved implementation baseline for the first local vertical slice.

## Product boundary

The existing deterministic engine remains the arithmetic and safety foundation. It
measures the portfolio and generates several bounded, fully calculated candidates.
The DSH agent interprets objectives and soft context, then selects exactly one
candidate. It never invents a security, structure, order size, or risk number.

```text
portfolio + market
  -> deterministic snapshot
  -> admissible candidates with comparable trade-offs
  -> one DSH main agent selects a candidate_id and explains why
  -> deterministic gate revalidates limits and sizes exact orders
  -> human approval
  -> Alpaca paper execution (later phase)
```

## First vertical slice

The first slice is deliberately local and keyless:

1. Use the fixed calm, elevated, and stressed scenarios.
2. Generate a compact `DecisionContext` and safe candidate set.
3. Let one DSH agent call `get_decision_context`, then `submit_decision`.
4. Reject stale contexts, invented candidates, missing reasons, and limit breaches.
5. Write approved results as idempotent `paper_dry_run` JSONL records.

If the risk-engine target would breach the hard hedge-cost budget, candidate generation
reduces it to the largest deterministically sized hedge inside the cap before exposing it
to the model. Rejected or unaffordable actions are not presented as admissible choices.

No Alpaca credentials, HF token, Modal secret, remote model, or real order is needed
for this slice.

The next read-only seam uses `alpaca-mcp-server==2.2.1` behind one narrow DSH tool.
Only account/positions, SPY bars/latest trade, and an indicative SPY put chain are
allowlisted. Alpaca order, close-position, cancellation, exercise, and account-config
tools are not registered with the model. Credentials remain in the DSH process and its
MCP child environment only; the connection is hard-wired to paper mode.

## Contracts

- `DecisionContext`: authoritative risk facts plus the complete admissible candidate set.
- `DecisionCandidate`: stable ID, action, thesis, and deterministic trade-offs. Exact
  contract quantities are intentionally hidden from the model-facing representation.
  The 5% scenario field is explicitly a linear hedge-adjusted book estimate; income
  option repricing is represented separately by its deterministic defined-risk bound.
- `AgentDecision`: context ID, one candidate ID, and a concise reason.
- `GateResult`: approval/rejection reasons and exact paper-dry-run orders. Every
  approved result still requires human approval before any future submission. **Opt-in
  exception:** when the operator starts the bundle with `--place`, the system (never
  the model) may auto-place an approved options overlay on the **paper** account — the
  single-leg hedge/covered call as plain option orders, the iron condor as a 4-leg
  `mleg` order, fail-closed per leg. Without `--place`, nothing is ever sent to Alpaca.

## Explicit non-goals

- LangGraph or a custom general-purpose agent loop
- subagents or fixed specialist topology
- live trading
- arbitrary model-authored orders or target coverage
- custom UI, distributed database, or message queue
- Modal deployment or paid provider calls before the local replay passes
