# Harness integration — coexistence of the two branches

This branch merges Yugo's DSH decision layer (PR #4) with our runtime so **both live on
one tree with no conflicts**. Our Python suite passes (63 tests); the DSH suite is separate.

## What changed to make them coexist

- **`agent/` namespace → Yugo's.** His `agent/` decision package (incl. `agent/dsh`) owns
  that dir. We don't touch it.
- **langchain removed.** The in-house langchain LLM client (`model/`) and MCP executor
  (`harness/mcp_executor.py`) are retired — the **DSH harness owns the model brain + MCP
  connection now**. Both moved to `_archive/` (nothing hard-deleted).
- **In-house LLM decider retired.** DSH approves all trades, so `harness/llm.py` keeps
  only the `default_decider` stub; `make_llm_decider` + `runtime/skills.py` are in
  `_archive/` (`harness/llm.original.py`).
- **Our webui removed.** DSH ships its own UI, so `webui/` + `scripts/run_webui.py` moved
  to `_archive/webui/`.
- **Harness: LangGraph dropped.** Our loop is a plain Python runner (`harness/run.py`);
  removed `harness/graph.py`, `harness/state.py`, the `use_langgraph` path, and the
  `langgraph` dependency — honoring his design non-goal.
- **`.gitignore`** unified to the union of both branches.

## One pick per category (canonical owner)

| Category | Canonical | Notes / redundant piece to retire later |
|---|---|---|
| Risk engine | `risk_engine/` (shared) | — |
| Decision **input** | **Yugo** — `agent/candidates.py` (N bounded candidates) | our single-plan `runtime/strategy_api.py` stays to build the measure-node context |
| Decision **safety** | **Yugo** — candidates hide exact sizes from the model | — |
| Gate / limits | **Yugo** — `agent/gate.py` + `agent/limits.py` | our `risk_engine/limits.py` + `validate_plan` stay until the loop uses the candidate flow |
| **Runtime** | **Ours** — `harness/` loop + `scripts/run_agent.py` on a GitHub Actions schedule (state committed back) | his single local slice |
| MCP **read** | **Yugo** — `agent/dsh` read-only snapshot (verified) | our `feed/AlpacaDataSource` REST kept as fallback |
| MCP **write** | **Yugo** — DSH harness | our langchain `McpBroker` retired to `_archive/` |
| Decider | **Yugo** — DSH harness (approves all trades) | our `default_decider` stub keeps the loop runnable; LLM decider in `_archive/` |
| Ledger | **Ours** — `runtime/ledger.py` (trade log + self-grading) | fold his `agent/ledger.py` decision-idempotency into it |
| UI | **Yugo** — DSH's own webui | our `webui/` retired to `_archive/` |
| Backtest / grading | **Ours** — `backtest/`, `runtime/grade.py` | — |

## One decision still open for you + Yugo

- **Default approval mode:** **human-in-the-loop** (Yugo's gate default) or
  **autonomous** (our loop default)?

_(Resolved: the **DSH harness** is the decider and approves all trades — our in-house LLM
decider is archived.)_

## Follow-up consolidation (not done here — would touch the loop, kept out to avoid breakage)

- Rewire `run_cycle` to drive **candidates → chosen id → `agent.gate`** instead of the
  single-plan `validate_plan`, then retire our `validate_plan`/duplicate limits.
- Merge `agent/ledger.py` idempotency into `runtime/ledger.py`; keep one ledger.
- Point OBSERVE at the DSH read-only MCP snapshot via the `DataSource` protocol.
