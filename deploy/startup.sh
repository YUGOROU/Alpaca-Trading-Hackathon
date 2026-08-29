#!/usr/bin/env bash
set -euo pipefail

: "${DSH_HOME:?DSH_HOME must be set}"
: "${HF_TOKEN:?HF_TOKEN must be supplied by the Modal Secret}"
: "${HF_MODEL_ID:?HF_MODEL_ID must be set by the Modal heartbeat}"

app_root="${APP_ROOT:-/app}"
profile_dir="$DSH_HOME/profiles/portfolio-agent"
profile_patch="$profile_dir/cordis.patch.yml"
dsh_bin="$app_root/agent/dsh/node_modules/.bin/dsh"

mkdir -p "$DSH_HOME"
if [[ ! -f "$profile_dir/package.json" ]]; then
  "$dsh_bin" plugin --profile portfolio-agent add "$app_root/agent/dsh"
fi

# The model route is configured at runtime.  Only its id is retained in the
# profile; HF_TOKEN remains an environment variable supplied by Modal.
cat > "$profile_patch" <<'YAML'
- id: agent-default-model
  config:
    provider: huggingface-router
    model: !!js process.env.HF_MODEL_ID

- id: llm-pi-ai
  config:
    providers:
      huggingface-router:
        displayName: Hugging Face Inference Providers
        apiKeyEnv: HF_TOKEN
        api: openai-completions
        baseURL: https://router.huggingface.co/v1
        models:
          - id: !!js process.env.HF_MODEL_ID
            name: !!js process.env.HF_MODEL_ID
            contextWindow: 65536
            maxTokens: 4096
YAML

exec "$dsh_bin" --profile portfolio-agent "$@"
