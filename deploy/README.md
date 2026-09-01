# Modal DSH heartbeat

`modal-heartbeat.yml` is the only deployment path. It uses the repository's
`MODAL_TOKEN_ID` and `MODAL_TOKEN_SECRET` GitHub Secrets; neither is copied into
the image or repository.

Before enabling the heartbeat, create one Modal Secret named `huggingface` with
only `HF_TOKEN`. `HF_MODEL_ID` is configuration, not a credential, and is injected
into the server container as a non-secret environment variable (see below).
The workflow relays repository Secrets `ALPACA_API_KEY` and `ALPACA_SECRET_KEY`
to the Modal Secret named `alpaca-paper`; only this paper credential pair is mounted.
It also requires a separate, high-entropy repository Secret `HUMAN_APPROVAL_TOKEN`,
which the workflow relays to `human-approval-auth`. This token protects the proposal
UI/API and is never logged or returned by the service. Without it, deployment fails
before an unauthenticated approval surface can start.

The credentials are **not** mounted in fixture-only evaluation, so that path cannot contact an Alpaca
account or submit an order.

Run **Actions → Deploy Modal DSH heartbeat** manually only after the model route
has passed the repository's replay evaluation and the evaluated model id
(`zai-org/GLM-5.3:baseten` by default) is injected as `HF_MODEL_ID` in the
server environment. The validated model id can be overridden at deploy time by
setting `HF_MODEL_ID` in the calling environment. It deploys a one-CPU Modal Server
with `min_containers=1`; its server process owns the heartbeat continuously and
exposes public liveness at `/healthz`, operational state at `/statusz`, and a
public read-only monitor at `/` (English by default) and `/ja` (Japanese). The monitor is a sanitized aggregate of the
decision ledger: it highlights decision cycles, gate outcomes, policy stops,
autonomous overlay submissions, and a fixed-label activity log. It never
returns positions, account values, order or broker identifiers, approval
identities, model input, or decision reasons. The authenticated Human Approval
UI is at `/approval`; its APIs still require `HUMAN_APPROVAL_TOKEN`. The `recreate` deploy strategy
stops the prior deployment before the new server starts. `/data/heartbeat.lock`
on the `liquidity-leak-dsh-state` Volume is a second safeguard against duplicate
owners.

The persisted DSH profile, sessions, contexts, and ledger survive container
replacement on the Volume. The deployed heartbeat is armed only for one `SPY`
options-overlay order per eligible live cycle: a SPY protective put, covered call, or
iron condor, or a covered call on AAPL, MSFT, NVDA, or DELL when at least 100 shares are held.
It cannot place an equity/core-book order, a multi-order batch, or
close option structures autonomously. Contract-level ledger provenance is required
before a future autonomous close/roll slice can distinguish protective puts from
income-spread legs. Before every write it records autonomous-policy provenance,
revalidates the fresh live snapshot, and keeps the broker client order ID for
reconciliation. It resolves fresh executable bid/ask quotes, re-gates the hedge-cost or defined-
risk cap, and submits only a bounded limit/net-credit limit order; missing quotes or a breached
cap fail closed. Autonomous income is a single overlay: an eligible named covered call, otherwise
a SPY iron condor. The authenticated UI remains available for human-mode proposals.

After deployment, run the non-mutating paper-read probe before approving any
proposal:

```bash
modal run deploy/modal_app.py::paper_readiness
```

It returns booleans only: account, positions, and an SPY quote must all be
readable. It does not return credentials, balances, prices, or submit an order.

## Model selection (no fine-tuning)

Run the credentialed but Alpaca-free fixed-scenario gate before changing the
server default:

```bash
modal run deploy/modal_app.py::evaluate_model --model-id '<HF model id>'
```

It evaluates calm, elevated, and stressed fixture scenarios. A candidate is
eligible only when every run exits cleanly and creates one
`approved_for_dry_run` ledger entry. This PR does not fine-tune a model, run RL,
or run GEPA; it creates the repeatable baseline those experiments would need.
