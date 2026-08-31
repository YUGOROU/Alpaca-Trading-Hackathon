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
exposes public liveness at `/healthz`, operational state at `/statusz`, and an
authenticated Human Approval UI/API at `/`. The `recreate` deploy strategy
stops the prior deployment before the new server starts. `/data/heartbeat.lock`
on the `liquidity-leak-dsh-state` Volume is a second safeguard against duplicate
owners.

The persisted DSH profile, sessions, contexts, and ledger survive container
replacement on the Volume. Deployment starts the LLM heartbeat with paper-only
read access; a paper write remains unavailable until an authenticated operator
approves a proposal and its snapshot passes fresh revalidation. The initial UI
allows exactly one approved options order per submission, making the first manual
paper-order validation a bounded operation.

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
