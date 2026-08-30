# Model-Native Tool Adapter Evaluation — 2026-08-29

## Scope

This report records the fixture-only validation of the model-native adapter.
Every request used the Hugging Face Router with the Baseten provider route.
No Alpaca credentials, account data, or order placement capability was present.
Only sanitized model, fixture, protocol, and aggregate metrics were retained.

The adapter uses three shared protocol families:

- `deepseek-v4` for both DeepSeek V4 candidates, including DSML compatibility.
- `kimi-k3`, retaining `reasoning_content` only in the in-memory second-turn history.
- `glm-5.3` for both GLM candidates, including native XML compatibility.

Each decision uses forced non-streaming calls in the fixed order
`get_decision_context` then `submit_decision`. The harness, rather than a
model, generates the decision identifier.

## Evidence basis

The reproducible evidence committed in this PR is the 30-call protocol
qualification per candidate (10 Direct Provider forced-tool probes + 20 DSH
decision-loop probes), recorded in:

- `results/qualification-glm-5.3-2026-08-29.json`
- `results/qualification-dsv4-pro-2026-08-29.json`
- `results/qualification-dsv4-flash-2026-08-29.json`

These are the transport-level records that back the candidate decision. An
earlier aggregate report of a decision-quality score (0.8125) is **not**
commit-ready: its per-run trace was not retained, and an independent
model-native Stage-A rerun of the eight fixtures reached `schema_validity = 1.0`
but does not reproduce that specific aggregate (see Qualification section).

## Verified Local Contract

- DSH tests: 20 passed (includes model-native adapter unit tests).
- Fixture replay E2E: 3 regimes passed.
- Python evaluation tests: 5 passed; full suite 74 passed.
- Static checks: Python compilation and `git diff --check` passed.

## Remote Fixture Results

The hard, reproducible evidence for candidate selection is the 30-call
transport qualification (10 Direct + 20 DSH per candidate), recorded in the
committed `results/qualification-*.json` files:

| Candidate | Qualification | Direct (10) | DSH transport (20) | Direct / DSH schema-invalid | timeout |
| --- | --- | ---: | ---: | ---: | ---: |
| `zai-org/GLM-5.3:baseten` | **Passed** | 10 / 10 | 20 / 20 | 0 / 0 | 0 |
| `deepseek-ai/DeepSeek-V4-Pro-0813:baseten` | Failed | 0 / 10 | 18 / 20 | 10 / 2 | 0 |
| `deepseek-ai/DeepSeek-V4-Flash-0731:baseten` | Failed | 9 / 10 | 16 / 20 | 1 / 4 | 0 |

Interpretation: `zai-org/GLM-5.3:baseten` is the only candidate whose transport
layer is stable across all 30 calls (every direct probe and DSH decision loop
was schema-valid and timely). Both DeepSeek candidates fail qualification on
transport grounds — the model-native DSML normalization is not stable, emitting
a malformed schema for the empty-argument `get_decision_context` call at a
non-deterministic rate. This is a model-output instability, not a parse or
timeout artifact.

**Transport qualification is not a decision-quality pass.** An independent
model-native Stage-A rerun of the eight fixtures (kept local, not a committed
deployment record) reached `schema_validity = 1.0` but failed the decision
quality hard gate on two unambiguous fixtures (`calm_clear` and
`elevated_clear`), so Stage-B did not run in that trace. Decision quality,
including the earlier unretained 0.8125 aggregate, is **deliberately deferred**
by the owner (see Decision-quality deferral) and is not claimed by this PR.
This document and the committed `results/qualification-*` JSONs therefore do
**not** record an unsafe-approval-free decision-quality pass.

## Failure Taxonomy

The adapter preserves the required protocol failures (`no_tool_call`,
`wrong_tool_name`, `missing_tool_call_id`, `arguments_parse_error`,
`schema_invalid`, `missing_argument`, `get_context_ok_submit_missing`,
`tool_dispatch_error`, and `provider_timeout`). Authentication failures and
HTTP 429 rate limiting are separately recorded as `provider_auth_error` and
`provider_rate_limited`, so transient provider conditions are not described as
model decision-quality failures.

## Remaining Qualification Boundary

This PR qualifies `zai-org/GLM-5.3:baseten` on **transport** grounds only (30/30
probes clean). It does not claim a decision-quality or safety pass: the
independent Stage-A rerun showed the decision-quality hard gate is not
reproducibly passed, and the earlier 0.8125 aggregate is not committed because
its per-run trace was not retained. A deployment built on this PR should be
treated as transport-qualified but decision-quality-open.

## Decision-quality deferral

The decision-quality gate (oracle agreement, unsafe-approval freedom, and the
earlier unreproduced 0.8125 aggregate) is deliberately **deferred** by the
owner and is out of scope for this PR. This PR therefore records the transport
qualification only. A follow-up must either reproduce decision-quality evidence
or formally adopt a lower decision-quality bar before the heartbeat is relied
upon for autonomous behavior.
