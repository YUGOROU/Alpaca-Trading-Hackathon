import { randomUUID } from 'node:crypto'
import { promisify } from 'node:util'
import { execFile } from 'node:child_process'
import { connectAlpacaOrders, placeGateOrders, fetchOptionChain } from './alpaca-orders.js'

const execFileAsync = promisify(execFile)

export function resolvePython(configured) {
  if (configured && configured !== 'python3') return configured
  return process.platform === 'win32' ? 'python' : 'python3'
}

function modeArgs(config) {
  if (config.live) return ['--live']
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
  return bridge(config, ['context', ...modeArgs(config)])
}

// The model never receives the idempotency key.  It is a harness-owned value,
// so a malformed/native tool protocol cannot corrupt or omit it.
export function submitDecision(config, { context_id, candidate_id, reason }) {
  return bridge(config, [
    'submit', ...modeArgs(config),
    '--context-id', context_id,
    '--candidate-id', candidate_id,
    '--reason', reason,
    '--decision-id', `dsh-${randomUUID()}`,
    '--ledger', config.ledgerPath,
  ])
}

export async function placeApprovedDecision(gate) {
  const connection = await connectAlpacaOrders()
  try {
    const [puts, calls] = await Promise.all([
      fetchOptionChain(connection.client, 'put'),
      fetchOptionChain(connection.client, 'call'),
    ])
    return await placeGateOrders(connection.client, gate.orders || [], { ...puts, ...calls })
  } finally {
    await connection.close()
  }
}
