#!/usr/bin/env node
/**
 * The only order-writing bridge exposed to the Human Approval server.
 *
 * A request is fail-closed unless the Python ledger records an approved proposal
 * and a fresh Alpaca REST revalidation succeeds immediately before this process
 * opens the paper-only Alpaca MCP transport.  The initial operational slice
 * deliberately accepts exactly one gate order, so a manual validation cannot
 * accidentally submit a portfolio-sized batch.
 */
import { execFileSync } from 'node:child_process'
import { connectAlpacaOrders, fetchOptionChain, placeGateOrders } from './alpaca-orders.js'

function usage() {
  process.stderr.write('usage: human-executor.js --ledger PATH --decision-id ID\n')
  process.exit(64)
}

function arg(name) {
  const index = process.argv.indexOf(name)
  if (index < 0 || !process.argv[index + 1]) usage()
  return process.argv[index + 1]
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
  const prepared = python(['prepare-submission', '--ledger', ledger, '--decision-id', decisionId])
  if (!Array.isArray(prepared.orders) || prepared.orders.length !== 1) {
    throw new Error('the initial human executor permits exactly one approved gate order')
  }

  const connection = await connectAlpacaOrders(process.env)
  try {
    const puts = await fetchOptionChain(connection.client, 'put')
    const calls = await fetchOptionChain(connection.client, 'call')
    const results = await placeGateOrders(connection.client, prepared.orders, { ...puts, ...calls })
    const placed = results.filter(result => result.status === 'placed')
    if (placed.length !== 1 || results.length !== 1) {
      throw new Error('MCP did not place exactly one approved order')
    }
    const alpacaOrderId = orderId(placed[0].result)
    if (!alpacaOrderId) {
      throw new Error('MCP response did not include an Alpaca order id; reconcile manually before retrying')
    }
    const brokerEvent = python([
      'record-broker-update', '--ledger', ledger, '--decision-id', decisionId,
      '--state', 'accepted', '--broker-orders-json', JSON.stringify([{ alpaca_order_id: alpacaOrderId }]),
    ])
    process.stdout.write(JSON.stringify({ decision_id: decisionId, broker_event: brokerEvent }) + '\n')
  } catch (error) {
    // A preflight rejection has not created submission_requested.  Do not append a
    // misleading broker state in that case; all post-request errors are retained
    // as a terminal submission_failed transition when possible.
    try {
      python([
        'record-submission-failure', '--ledger', ledger, '--decision-id', decisionId,
        '--reason', error instanceof Error ? error.message : String(error),
      ])
    } catch { /* preflight failures stay fail-closed without a broker assertion */ }
    throw error
  } finally {
    await connection.close()
  }
}

main().catch(error => {
  process.stderr.write(`human executor failed: ${error instanceof Error ? error.message : String(error)}\n`)
  process.exit(1)
})
