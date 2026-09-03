#!/usr/bin/env bash
set -euo pipefail

: "${DSH_HOME:?DSH_HOME must be set}"
: "${HF_TOKEN:?HF_TOKEN must be supplied by the Modal Secret}"
: "${HF_MODEL_ID:?HF_MODEL_ID must be set by the Modal heartbeat}"

app_root="${APP_ROOT:-/app}"
profile_dir="$DSH_HOME/profiles/portfolio-agent"
profile_patch="$profile_dir/cordis.patch.yml"
dsh_bin="$app_root/agent/dsh/node_modules/.bin/dsh"

profile_is_complete() {
  local manifest="$profile_dir/package.json"
  [[ -f "$manifest" ]] || return 1
  node - "$manifest" <<'NODE'
const fs = require('node:fs')
const manifest = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'))
const dependencies = manifest.dependencies || {}
const bundles = manifest.dsh?.profile?.bundles || []
process.exit(
  typeof dependencies['alpaca-portfolio-dsh'] === 'string' &&
  bundles.includes('alpaca-portfolio-dsh') ? 0 : 1,
)
NODE
}

mkdir -p "$DSH_HOME"
if ! profile_is_complete; then
  # A Volume can retain a base-only profile when a prior initialization was
  # interrupted.  package.json existing is not evidence that this repository's
  # startup and heartbeat plugins were installed.
  printf '%s\n' "heartbeat-profile: repairing missing alpaca-portfolio-dsh bundle" >&2
  "$dsh_bin" plugin --profile portfolio-agent add "$app_root/agent/dsh"
fi
if ! profile_is_complete; then
  printf '%s\n' "heartbeat-profile: required alpaca-portfolio-dsh bundle is unavailable" >&2
  exit 70
fi
printf '%s\n' "heartbeat-profile: verified alpaca-portfolio-dsh bundle" >&2

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
