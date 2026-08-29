# Deployment Model Evaluation

This protocol selects a reasoning model for the paper-only DSH heartbeat. It does not evaluate price prediction, generate orders, calculate position size, use live account data, or authorize deployment.

## Provider and candidates

Every candidate is called through Hugging Face Inference Providers with the Baseten route:

- `moonshotai/Kimi-K3:baseten`
- `deepseek-ai/DeepSeek-V4-Pro-0813:baseten`
- `deepseek-ai/DeepSeek-V4-Flash-0731:baseten`
- `zai-org/GLM-5.3:baseten`
- `zai-org/GLM-5.3-Flash:baseten`

## Protocol

Stage A runs each candidate once against eight fixed fixture contexts. It is a hard gate: no unsafe approval, malformed schema, or timeout is allowed. The runner also verifies stale-context and unknown-candidate rejection directly against the deterministic gate.

Only Stage-A survivors proceed to Stage B, where every fixture is repeated three times. The report scores Decision Quality (40%), Reliability (25%), Agent Fitness (20%), and Deployment Fitness (15%). P95 end-to-end latency must remain below 30 seconds.

The runner writes only sanitized records: model and fixture IDs, selected candidate ID, oracle class, gate/schema status, elapsed time, timeout/retry metadata, and aggregate scores. It discards prompts, model output, free-text reason, and decision ledgers.

## Run

```bash
HF_TOKEN="..." python3 scripts/run_deployment_model_evaluation.py \
  --output results/deployment-model-evaluation.json
```

Use the authenticated environment to supply `HF_TOKEN`; never place the token in source code, a result artifact, or a command log.

## Deployment decision

An eligible result is evidence for an explicit `HF_MODEL_ID` decision only. It does not automatically modify a Modal Secret or deploy the persistent Server. Those are separate, reviewed steps.
