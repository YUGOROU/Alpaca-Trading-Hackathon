# DSH local harness

This bundle connects the existing deterministic risk engine to a single DSH agent.
The model can access exactly three tools:

- `get_decision_context`: retrieves the risk snapshot and the admissible candidates.
- `submit_decision`: selects one candidate, validates it through the deterministic
  gate, and records a canonical paper-order proposal.
- `get_alpaca_readonly_snapshot`: reads the paper account, positions, and SPY market
  data through the official Alpaca MCP server. Order, cancellation, and account
  mutation tools are not exposed.

The model cannot invent symbols, orders, quantities, or risk values. A successful
`submit_decision` result still has `human_approval_required: true`. By default it is
**never sent to Alpaca** — it is recorded as a paper dry run only.

## Model-native tool calling

When `HF_MODEL_ID` names an approved Baseten candidate, the bounded decision path
uses a non-streaming model-native adapter. It retains the canonical two-tool
contract, forces `get_decision_context` followed by `submit_decision`, and maps
only three protocol families: `deepseek-v4`, `kimi-k3`, and `glm-5.3`. The harness,
not the model, generates the submission idempotency key. It records only sanitized
protocol metadata during qualification; reasoning content and raw model responses
are never written to the ledger.

The bundle discovers its focused DSH-native skills from `.agents/skills/`:
`paper-risk-gate`, `alpaca-mcp-observe`, and `decision-idempotency`. They document
the model-visible decision contract and the direct official MCP observation surface;
they never grant an order or account-mutation capability.

**Execution boundary.** `approved_for_dry_run` creates only a proposal; it is not
broker submission permission. The current bundle supports Human Approval records in
the canonical Python ledger but cannot submit orders. `--place` fails closed while the
autonomous-paper lifecycle, fresh revalidation, and reconciliation are implemented.

## Setup

Use Node.js 22 or later. DSH is a developer preview, so the `package-lock.json`
pins the `0.1.1-rc.2` dependency line.

```bash
cd agent/dsh
npm ci
DSH_HOME="$PWD/../../.dsh" ./node_modules/.bin/dsh \
  plugin --profile portfolio-agent add "$PWD"
```

## Keyless verification

The verification suite runs the calm, elevated, and stressed scenarios through
the official replay adapter without using an HF token, Alpaca credentials, or a
Modal secret.

```bash
cd agent/dsh
npm test
npm run test:alpaca-schema
npm run test:profile
cd ../..
python3 -m unittest discover -s tests -v
```

`test:profile` creates a temporary `DSH_HOME` and dry-run ledger. It does not
connect to a real account, remote model, or paper-order endpoint.
`test:alpaca-schema` starts the official server with non-secret placeholders and
calls only `tools/list`; it does not invoke an Alpaca API tool.

## Heartbeat runtime (market-monitoring loop)

`agent/dsh/heartbeat.js` is a DSH-native long-lived loop that wakes on an interval
during US market hours and records one bounded proposal per tick. It replaces the
retired GitHub Actions cron as the scheduling owner:

```bash
cd agent/dsh
DSH_HOME="$PWD/../../.dsh" ./node_modules/.bin/dsh \
  plugin --profile portfolio-agent add "$PWD"
dsh --profile portfolio-agent --live --heartbeat --interval 1800000 \
  "protect the book; step the hedge in only if risk justifies it"
```

- `--live` observes the real paper account (instead of a fixture scenario).
- `--mock` selects the explicit local mock source; it is never a `--live` fallback.
- `--heartbeat` loops; omit it for a one-shot run.
- `--interval <ms>` sets the cadence (default 30 min, matching the retired cron).
- `--place` is rejected until autonomous-paper is explicitly armed in a later release.

## Alpaca paper read-only boundary

`get_alpaca_readonly_snapshot` starts `alpaca-mcp-server==2.2.1` as a stdio child
process and calls only this internal allowlist:

- `get_account_info`
- `get_all_positions`
- `get_stock_bars`
- `get_stock_latest_trade`
- `get_option_chain`

Even though the official server also provides order, close-position, cancellation,
exercise, and account-configuration tools, those tools are never registered with
the DSH model.

Credentials are passed only from the DSH process environment to the MCP child via
`ALPACA_API_KEY` and `ALPACA_SECRET_KEY`. They are not written to configuration,
sessions, or the ledger. The connection always sets `ALPACA_PAPER_TRADE=true`; the
free-data feeds are `iex` for stocks and `indicative` for options.

Account IDs, account numbers, user IDs, and related identifiers are removed
recursively before a result reaches the model or session. External strings, arrays,
and the total result size are also bounded.

If credentials are absent, the tool fails closed before connecting to the API.
When credentials become available, run only this read-only tool first and never put
paper-account identifiers or credentials in logs or commits.
