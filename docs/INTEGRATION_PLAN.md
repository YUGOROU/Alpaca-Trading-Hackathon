# Integration plan — wiring the runtime to the DSH brain (Option A)

> Status: **plan only, nothing implemented.** This is the agreed direction for connecting
> our deterministic engine to Yugo's DSH harness. See `docs/HARNESS_INTEGRATION.md` for the
> current division of labor.

## The gap

Two islands exist today:

- **DSH candidate → gate flow** (`agent/`): `build_decision_context` produces N pre-vetted
  candidates; the model picks one; `agent/gate.py` re-validates and records a paper dry-run.
  Input currently comes from fixed fixtures (`agent/scenarios.py`).
- **Our scheduled runtime** (`harness/` + `scripts/run_agent.py`, on GitHub Actions):
  single-plan `strategy_api` → `default_decider` stub → dry-run executor → `runtime/ledger.py`.

They aren't connected. "DSH is the brain" is decided but not wired.

## The seam: Option A — DSH drives, our Python is the engine

DSH is the application loop; the model calls `agent/cli.py`. Our `harness/run_cycle` stops
being the live driver. Chosen because it honors "DSH owns the brain + MCP," reuses the gate
DSH already has, and avoids maintaining two decide loops.

**Key enabler:** `feed.observe(source, state)` already returns `(Portfolio, MarketData)` in
`risk_engine` types — exactly what `agent/candidates.build_decision_context(portfolio, market,
scenario_id)` consumes. So the live bridge is small; no engine changes.

## File-by-file task list (ordered)

### Phase 1 — Live data into the candidate flow (the one real new wire)
1. **`agent/candidates.py`** — no signature change; confirm `build_decision_context` is
   agnostic to the source of `portfolio`/`market` (it is). Optionally generalize the
   `scenario_id` label to cover live vs fixture provenance.
2. **`feed/` (reuse `observe`)** — nothing new to build; import `observe` from the agent side.
3. **New `agent/live_context.py`** — one function: `observe(AlpacaDataSource, StateStore) →
   build_decision_context(...)`. Replaces `scenarios.get_scenario` for the live path.

### Phase 2 — Expose live mode to DSH (JSON contract unchanged)
4. **`agent/cli.py`** — add `context --live` (keep `--scenario` for tests). Live branch calls
   `agent/live_context.py`. Output JSON shape is identical, so **`agent/dsh/index.js` needs
   zero changes**.
5. **`agent/scenarios.py`** — unchanged; remains the offline/replay fixture source.

### Phase 3 — Live market data into OBSERVE
Two paths to the same data — **pick one** to avoid double-maintaining:

- **Path A — Alpaca Data API (REST).** We already have `feed/alpaca.py`
  (`AlpacaDataSource`, via `alpaca-py`). Task is to confirm/extend it to supply every live
  field the hedge math needs, not build it fresh.
- **Path B — DSH snapshot.** `get_alpaca_readonly_snapshot` already returns
  `spy_daily_bars`, `spy_latest_trade`, `spy_option_chain`, `account`, `positions`.

6. **New `agent/snapshot_adapter.py`** (Path B) — map the snapshot output → `Portfolio` /
   `MarketData`, reusing the builders in `feed/core.py` (`build_portfolio`, `build_market`).
   Lets OBSERVE run off DSH's verified MCP read instead of REST.
7. **`agent/dsh/index.js`** — *decision point:* auto-chain the snapshot into context
   server-side, or leave the model to call `get_alpaca_readonly_snapshot` then
   `get_decision_context`. Not mandatory.

**Live-data field checklist** (whichever path) — the hedge engine needs:
- SPY **daily closes** → EWMA realized vol + 50-day MA regime signal
- **latest trade / price** → mark the book, drawdown-from-peak
- **option chain + IV** → expected-move strike distance, IV-rank (is protection cheap)
- **account + positions** → equity, cash, current holdings/betas, hedge contracts held

Data API (market data) is a **different product** from the Trading API (orders/account) —
see Phase 7 for the Trading-vs-Broker note; the same distinction applies to which data
endpoints we hit.

### Phase 4 — Retire `run_cycle`'s live role
8. **`.github/workflows/agent.yml`** — repoint to run the DSH bundle, or drop it if DSH is
   scheduled elsewhere. *Depends on the "independent heartbeat?" question for Yugo.*
9. **`scripts/run_agent.py`, `harness/`, `runtime/strategy_api.py`** — demote to dev/offline
   only (or move to `_archive/`). No longer the live driver; `validate_plan` becomes an
   offline helper.

### Phase 5 — One ledger
10. **`agent/ledger.py`** — becomes the single ledger (idempotent, gate-native). Writer
    unchanged.
11. **`runtime/ledger.py`** — retire to `_archive/`; its role moves to `agent/ledger.py`.
12. **`runtime/grade.py`** — repoint `grade_ledger(ledger, price_lookup, ...)` to read
    `agent/ledger.py`'s JSONL. `grade_entry` needs a small field-name adapter (agent ledger
    stores `{decision, gate, orders}`; grade expects the old cycle shape). **The one real
    logic change.**

### Phase 6 — Grading hook
13. **`agent/cli.py`** (or a thin `scripts/grade.py`) — after `submit`, or on a schedule, run
    `grade_ledger` over `agent/ledger.py` so expired trades self-grade for the demo.

### Phase 7 — Skills (SKILL.md → DSH)
Give the model Alpaca domain know-how as **SKILL.md reference docs** (from
`github.com/alpacahq/alpaca-skills`). These are docs, not code.

14. **DSH bundle (Yugo's side)** — DSH **loads SKILL.md natively**, so skills are dropped
    into the DSH bundle, *not* into our Python. (Our old `runtime/skills.py` loader was
    archived precisely because DSH owns this.)
15. **Pick the right skill set** — the alpaca-skills repo is mostly **Broker API** skills.
    ⚠️ Broker API is a *different product*: trading **on behalf of other people's accounts**
    (KYC, funding, journals). Placing our own calls/puts on a **paper account** is the
    **Trading API** — which is what DSH's `alpaca-readonly` snapshot already reads. So:
    - **Confirm which product the hackathon runs on** (Trading API paper vs Broker sandbox)
      *before* loading skills.
    - If Trading API (likely): load `alpaca-trading-backtest` + the Trading-API market-data
      skill; skip most Broker-API skills.
    - Cross-cutting skills worth loading either way: `reconciliation-idempotency`,
      `rate-limits-resilience`, `money-precision`.
16. **Scope to our two tools** — skills should reference only what the model can actually do
    (`get_decision_context`, `submit_decision`, read-only snapshot). Order/cancel/account
    tools stay hidden, so don't load skills that imply the model places raw orders.

## Net effort

- ~3 new small files: `agent/live_context.py`, `agent/snapshot_adapter.py`, maybe
  `scripts/grade.py`.
- 1 real logic change: the grade field-name adapter (Phase 5, #12).
- Everything else is wiring or retiring (moves to `_archive/`, never hard-deleted).
- **Untouched:** `risk_engine/` and `agent/dsh/*.js`.

## Decisions to settle before starting

- **(a) Scheduling** — keep an independent GitHub Actions heartbeat, or let DSH own
  scheduling entirely (Phase 4).
- **(b) OBSERVE source** — DSH read-only snapshot adapter, or the existing Alpaca Data
  API REST feed (Phase 3).
- **(c) Alpaca product** — Trading API (paper account, our own trades) vs Broker API
  (on-behalf-of accounts). Drives both the data endpoints (Phase 3) and which SKILL.md
  set to load (Phase 7). Almost certainly Trading API — confirm.
