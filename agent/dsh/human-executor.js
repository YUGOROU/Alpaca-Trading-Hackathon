#!/usr/bin/env node
/**
 * The only order-writing bridge exposed to the Human Approval server and the
 * separately armed autonomous-options heartbeat policy.
 *
 * A request is fail-closed unless the Python ledger records an approved proposal
 * and a fresh Alpaca REST revalidation succeeds immediately before this process
 * opens the paper-only Alpaca MCP transport.  The initial operational slice
 * deliberately accepts exactly one gate order, so a manual validation cannot
 * accidentally submit a portfolio-sized batch.
 */
import { execFileSync } from 'node:child_process'
import { connectAlpacaOrders, fetchOptionChain, placeGateOrders } from './alpaca-orders.js'

const AUTONOMOUS_OPTIONS_STRUCTURES = new Set(['protective_put', 'covered_call', 'iron_condor'])

function usage() {
  process.stderr.write('usage: human-executor.js --ledger PATH --decision-id ID [--execution-mode human|autonomous-paper]\n')
  process.exit(64)
}

function arg(name) {
  const index = process.argv.indexOf(name)
  if (index < 0 || !process.argv[index + 1]) usage()
  return process.argv[index + 1]
}

function optionalArg(name, fallback) {
  const index = process.argv.indexOf(name)
  return index < 0 ? fallback : process.argv[index + 1] || usage()
}

function assertAutonomousOptionsOverlay(orders) {
  if (!Array.isArray(orders) || orders.length !== 1) {
    throw new Error('autonomous options execution requires exactly one gate order')
  }
  const [order] = orders
  if (!AUTONOMOUS_OPTIONS_STRUCTURES.has(order?.structure)) {
    throw new Error('autonomous execution permits only known options-overlay structures')
  }
  if (order.symbol !== 'SPY') {
    throw new Error('autonomous options execution permits only SPY overlays')
  }
  if (!['buy_to_open', 'sell_to_open'].includes(order.intent)) {
    throw new Error('autonomous options execution never closes or trades an equity position')
  }
}

function python(args) {
  const output = execFileSync('python3', ['-m', 'agent.cli', ...args], {
    cwd: '/app', env: process.env, encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'],
  })
  return JSON.parse(output)
}

function orderId(value) {
  if (value === null || value === undefined) return null
  if (Array.isArray(value)) {
    for (const item of value) {
      const found = orderId(item)
      if (found) return found
    }
    return null
  }
  if (typeof value !== 'object') return null
  for (const key of ['alpaca_order_id', 'order_id', 'id']) {
    const candidate = value[key]
    if (typeof candidate === 'string' && candidate.trim()) return candidate
  }
  for (const child of Object.values(value)) {
    const found = orderId(child)
    if (found) return found
  }
  return null
}

async function main() {
  const ledger = arg('--ledger')
  const decisionId = arg('--decision-id')
  const executionMode = optionalArg('--execution-mode', 'human')
  if (!['human', 'autonomous-paper'].includes(executionMode)) usage()
  let connection = null
  let prepared = null
  try {
    if (executionMode === 'autonomous-paper') {
      python(['authorize-autonomous', '--ledger', ledger, '--decision-id', decisionId])
    }
    prepared = python([
      'prepare-submission', '--ledger', ledger, '--decision-id', decisionId,
      '--require-exactly-one-order',
      ...(executionMode === 'autonomous-paper' ? ['--autonomous-options-overlay'] : []),
    ])
    if (executionMode === 'autonomous-paper') assertAutonomousOptionsOverlay(prepared.orders)
    connection = await connectAlpacaOrders(process.env)
    const puts = await fetchOptionChain(connection.client, 'put')
    const calls = await fetchOptionChain(connection.client, 'call')
    const results = await placeGateOrders(connection.client, prepared.orders, { ...puts, ...calls })
    const placed = results.filter(result => result.status === 'placed')
    if (placed.length !== 1 || results.length !== 1) {
      throw new Error('MCP did not place exactly one approved order')
    }
    const alpacaOrderId = orderId(placed[0].result)
    if (!alpacaOrderId) {
      const clientOrderId = prepared.orders[0].client_order_id
      python([
        'record-submission-unknown', '--ledger', ledger, '--decision-id', decisionId,
        '--client-order-ids-json', JSON.stringify([clientOrderId]),
        '--reason', 'MCP placement response omitted Alpaca order id',
      ])
      throw new Error('MCP response omitted an Alpaca order id; reconcile by client order id before retrying')
    }
    const brokerEvent = python([
      'record-broker-update', '--ledger', ledger, '--decision-id', decisionId,
      '--state', 'accepted', '--broker-orders-json', JSON.stringify([{ alpaca_order_id: alpacaOrderId }]),
    ])
    process.stdout.write(JSON.stringify({ decision_id: decisionId, broker_event: brokerEvent }) + '\n')
  } catch (error) {
    // The CLI validates order cardinality before writing submission_requested.
    // Once preparation succeeds, even an MCP connection failure is recorded.
    if (prepared !== null) {
      try {
      python([
        'record-submission-failure', '--ledger', ledger, '--decision-id', decisionId,
        '--reason', error instanceof Error ? error.message : String(error),
      ])
      } catch { /* retain the original failure if a secondary audit write fails */ }
    }
    throw error
  } finally {
    if (connection !== null) await connection.close()
  }
}

main().catch(error => {
  process.stderr.write(`human executor failed: ${error instanceof Error ? error.message : String(error)}\n`)
  process.exit(1)
})
