# Modal DSH heartbeat

`modal-heartbeat.yml` is the only deployment path. It uses the repository's
`MODAL_TOKEN_ID` and `MODAL_TOKEN_SECRET` GitHub Secrets; neither is copied into
the image or repository.

Before enabling the heartbeat, create one Modal Secret named `huggingface` with
`HF_TOKEN` and the evaluated `HF_MODEL_ID`. It is intentionally the only
application secret at this stage: Alpaca credentials are **not** mounted, so
this deployment cannot contact an Alpaca account or submit an order.

Run **Actions → Deploy Modal DSH heartbeat** manually only after the model route
has passed the repository's replay evaluation and its id is set as
`HF_MODEL_ID`. It deploys a one-CPU Modal Server
with `min_containers=1`; its server process owns the heartbeat continuously and
exposes an authenticated `/healthz` endpoint. The `recreate` deploy strategy
stops the prior deployment before the new server starts. `/data/heartbeat.lock`
on the `liquidity-leak-dsh-state` Volume is a second safeguard against duplicate
owners.

The persisted DSH profile, sessions, contexts, and ledger survive container
replacement on the Volume. Deployment starts the LLM heartbeat, but it cannot
contact an Alpaca account because no Alpaca credential secret is mounted.

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
