import { createHash } from 'node:crypto'
import { promisify } from 'node:util'
import { execFile } from 'node:child_process'

const execFileAsync = promisify(execFile)

export function resolvePython(configured) {
  if (configured && configured !== 'python3') return configured
  return process.platform === 'win32' ? 'python' : 'python3'
}

function modeArgs(config) {
  if (config.live) return ['--live']
  if (config.mock) return ['--mock']
  if (!config.scenario) throw new Error('a fixed scenario is required when live mode is disabled')
  return ['--scenario', config.scenario]
}

async function bridge(config, args) {
  try {
    const { stdout } = await execFileAsync(resolvePython(config.pythonExecutable), ['-m', 'agent.cli', ...args], {
      cwd: config.repositoryRoot,
      encoding: 'utf8',
      maxBuffer: 1024 * 1024,
    })
    return JSON.parse(stdout)
  } catch (error) {
    if (error?.stdout) {
      try { return JSON.parse(error.stdout) } catch { /* report below */ }
    }
    const detail = error?.stderr?.trim() || error?.message || String(error)
    throw new Error(`deterministic bridge failed: ${detail}`)
  }
}

export function getDecisionContext(config) {
  return bridge(config, ['context', ...modeArgs(config), '--execution-mode', config.executionMode ?? 'human'])
}

// The model never receives the idempotency key.  It is a harness-owned value,
// so a malformed/native tool protocol cannot corrupt or omit it.
export function decisionId(contextId, candidateId) {
  return `dsh-${createHash('sha256').update(`${contextId}\u0000${candidateId}`).digest('hex').slice(0, 32)}`
}

export function submitDecision(config, { context_id, candidate_id, reason }) {
  return bridge(config, [
    'submit', ...modeArgs(config),
    '--context-id', context_id,
    '--candidate-id', candidate_id,
    '--reason', reason,
    '--decision-id', decisionId(context_id, candidate_id),
    '--ledger', config.ledgerPath,
    '--execution-mode', config.executionMode ?? 'human',
  ])
}

// This is not model-visible. The heartbeat can invoke it only after the
// deterministic gate created an autonomous-paper proposal.
export async function executeAutonomousOptionsOverlay(config, decisionId) {
  if (config.executionMode !== 'autonomous-paper') {
    throw new Error('autonomous options execution is not armed')
  }
  const { stdout } = await execFileAsync('node', [
    `${config.repositoryRoot}/agent/dsh/human-executor.js`,
    '--ledger', config.ledgerPath,
    '--decision-id', decisionId,
    '--execution-mode', 'autonomous-paper',
  ], { cwd: config.repositoryRoot, encoding: 'utf8', maxBuffer: 1024 * 1024 })
  return JSON.parse(stdout)
}

// Operator-only preflight.  It is intentionally not registered as a model tool:
// Python revalidates live broker state and writes submission_requested before any
// order-capable MCP client can be opened.
export function prepareHumanSubmission(config, decisionId) {
  return bridge(config, ['prepare-submission', '--ledger', config.ledgerPath, '--decision-id', decisionId])
}
