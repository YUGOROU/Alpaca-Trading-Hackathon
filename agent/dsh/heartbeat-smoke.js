// One controlled end-to-end smoke cycle. It shares the live Python observation,
// GLM tool protocol, and deterministic gate with production, but explicitly
// suppresses executor dispatch so no broker order client is opened.
import { monitorHeartbeat } from './decision-bridge.js'
import { runModelNativeDecision } from './model-native-adapter.js'

const repositoryRoot = process.env.APP_ROOT || '/app'
const config = {
  repositoryRoot,
  live: true,
  heartbeat: true,
  executionMode: 'autonomous-paper',
  ledgerPath: process.env.SMOKE_LEDGER_PATH || '/data/state/decisions.jsonl',
  instruction: process.env.HEARTBEAT_INSTRUCTION || 'Select exactly one admissible paper-only candidate.',
  pythonExecutable: 'python3',
  forceMarket: true,
  allowExecution: false,
}

const tick = await monitorHeartbeat(config)
if (!tick.llm_due || !tick.context) throw new Error('forced Python heartbeat did not produce an LLM context')
const result = await runModelNativeDecision({ ...config, preparedContext: tick.context })
const gate = result.value?.gate || {}
process.stdout.write(JSON.stringify({
  python: { phase: tick.phase, reasons: tick.reasons, context_id: tick.context.context_id },
  llm: { status: result.status, failure: result.failure || null, protocol: result.protocol || [] },
  gate: {
    status: gate.status || null,
    reasons: gate.reasons || [],
    order_count: Array.isArray(gate.orders) ? gate.orders.length : 0,
    candidate_id: result.value?.decision?.candidate_id || null,
  },
  execution: 'suppressed_before_executor',
}) + '\n')
