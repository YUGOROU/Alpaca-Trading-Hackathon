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

## Verified Local Contract

- DSH tests: 19 passed.
- Fixture replay E2E: 3 regimes passed.
- Python evaluation tests: 5 passed.
- Static checks: Python compilation and `git diff --check` passed.

## Remote Fixture Results

Stage A contains one run across eight fixed fixture contexts. It is a hard
gate: every row must be schema-valid, timely, and free of unsafe approval.
Only Stage-A survivors run Stage B, which repeats each fixture three times.
The P50 and P95 values are end-to-end milliseconds.

| Candidate | Stage-A hard gate | Stage-B runs | Decision quality | Schema validity | P50 / P95 ms | Observation |
| --- | --- | ---: | ---: | ---: | --- | --- |
| `moonshotai/Kimi-K3:baseten` | Failed | 0 | 0.7500 | 0.8750 | 12035 / 29588 | One unsafe calm approval and one per-case timeout. |
| `deepseek-ai/DeepSeek-V4-Pro-0813:baseten` | Failed | 0 | 0.7500 | 0.8750 | 5176 / 5626 | One unsafe calm approval and one schema-invalid submission. |
| `deepseek-ai/DeepSeek-V4-Flash-0731:baseten` | Failed | 0 | 0.2812 | 0.7500 | 6741 / 7398 | One unsafe calm approval and two schema-invalid submissions. |
| `zai-org/GLM-5.3:baseten` | Passed | 24 | 0.8125 | 1.0000 | 6826 / 8688 | All Stage-A and Stage-B rows were schema-valid, safe, and timely. |
| `zai-org/GLM-5.3-Flash:baseten` | Passed | 24 | 0.4688 | 0.5833 | 4315 / 5173 | Stage B included unsafe selections and HTTP 429 provider-rate-limit responses. |

The current evidence identifies `zai-org/GLM-5.3:baseten` as the sole clean
candidate in this fixture run. This is evaluation evidence only: it does not
set `HF_MODEL_ID`, alter a Modal Secret, or deploy the persistent heartbeat.

## Failure Taxonomy

The adapter preserves the required protocol failures (`no_tool_call`,
`wrong_tool_name`, `missing_tool_call_id`, `arguments_parse_error`,
`schema_invalid`, `missing_argument`, `get_context_ok_submit_missing`,
`tool_dispatch_error`, and `provider_timeout`). Authentication failures and
HTTP 429 rate limiting are separately recorded as `provider_auth_error` and
`provider_rate_limited`, so transient provider conditions are not described as
model decision-quality failures.

## Remaining Qualification Boundary

The current qualification protocol uses 10 Direct Provider forced-tool probes
and 20 DSH decision-loop probes for each selected candidate. The fixture
evaluations above validate the full DSH decision path, but do not replace that
dedicated transport protocol. Do not treat the table as a deployment
authorization until the qualification result has been recorded.
