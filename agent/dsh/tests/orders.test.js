import assert from 'node:assert/strict'
import test from 'node:test'

import { occSymbol, resolveHedgeContract, buildPlaceArgs, placeGateOrders } from '../alpaca-orders.js'

const NOW = new Date(Date.UTC(2024, 8, 6)) // 2024-09-06

test('occSymbol builds a valid OCC option symbol', () => {
  assert.equal(occSymbol('SPY', new Date(Date.UTC(2024, 8, 20)), 'P', 520), 'SPY240920P00520000')
})

test('resolveHedgeContract picks nearest expiry then nearest strike', () => {
  const chain = {
    SPY240913P00520000: {},
    SPY240920P00520000: {}, // 14 days out — the target
    SPY240920P00500000: {},
    SPY240920C00520000: {}, // a call, must be ignored
  }
  const order = { symbol: 'SPY', strike: 519, expiry_days: 14, contracts: 3, intent: 'buy_to_open', structure: 'protective_put' }
  const resolved = resolveHedgeContract(order, chain, NOW)
  assert.equal(resolved.symbol, 'SPY240920P00520000')
})

test('resolveHedgeContract throws when nothing resolves', () => {
  assert.throws(() => resolveHedgeContract(
    { symbol: 'SPY', strike: 500, expiry_days: 14 }, {}, NOW,
  ), /no listed P resolves/)
})

test('buildPlaceArgs maps intent to side and carries contract count', () => {
  const args = buildPlaceArgs({ symbol: 'SPY240920P00520000' }, { intent: 'buy_to_open', contracts: 3 })
  assert.equal(args.side, 'buy')
  assert.equal(args.qty, '3')
  assert.equal(args.type, 'market')
  assert.equal(args.symbol, 'SPY240920P00520000')
})

test('buildPlaceArgs omits client_order_id when absent', () => {
  const args = buildPlaceArgs({ symbol: 'SPY240920P00520000' }, { intent: 'sell_to_close', contracts: 2 })
  assert.equal(args.side, 'sell')
  assert.equal(args.qty, '2')
  assert.equal(args.position_intent, 'sell_to_close')
  assert.equal(args.client_order_id, undefined)
})

test('placeGateOrders places the single-leg hedge and the 4-leg iron condor', async () => {
  const calls = []
  const client = { async callTool(req) { calls.push(req); return { structuredContent: { ok: true } } } }
  // far-future expiries so they resolve against the real clock; both rights present
  const chain = {
    SPY991220P00520000: {}, SPY991220P00510000: {},
    SPY991220C00580000: {}, SPY991220C00590000: {},
  }
  const io = { stderr: { write() {} } }
  const results = await placeGateOrders(client, [
    { structure: 'protective_put', symbol: 'SPY', strike: 520, expiry_days: 5, contracts: 2, intent: 'buy_to_open', client_order_id: 'hedge-1' },
    {
      structure: 'iron_condor', symbol: 'SPY', contracts: 1, expiry_days: 5, intent: 'sell_to_open', client_order_id: 'condor-1',
      short_strike: 520, long_strike: 510, call_short_strike: 580, call_long_strike: 590,
    },
  ], chain, io)
  assert.equal(results[0].status, 'placed')
  assert.equal(results[1].status, 'placed')
  assert.equal(calls.length, 2)
  // hedge = single-leg, with its client_order_id threaded through for broker idempotency
  assert.equal(calls[0].arguments.side, 'buy')
  assert.equal(calls[0].arguments.order_class, undefined)
  assert.equal(calls[0].arguments.client_order_id, 'hedge-1')
  assert.equal(calls[1].arguments.client_order_id, 'condor-1')
  // condor = 4-leg mleg (2 sells, 2 buys)
  assert.equal(calls[1].arguments.order_class, 'mleg')
  assert.equal(calls[1].arguments.qty, '1')
  assert.equal(calls[1].arguments.legs.length, 4)
  assert.equal(calls[1].arguments.legs.filter((l) => l.position_intent === 'sell_to_open').length, 2)
  assert.equal(calls[1].arguments.legs.filter((l) => l.position_intent === 'buy_to_open').length, 2)
  assert.ok(calls[1].arguments.legs.every((l) => l.ratio_qty === '1' && (l.side === 'buy' || l.side === 'sell')))
})

test('placeGateOrders fails closed when a condor leg cannot resolve', async () => {
  const calls = []
  const client = { async callTool(req) { calls.push(req); return { structuredContent: { ok: true } } } }
  const chain = { SPY991220P00520000: {}, SPY991220P00510000: {} } // no calls in the chain
  const io = { stderr: { write() {} } }
  const results = await placeGateOrders(client, [{
    structure: 'iron_condor', symbol: 'SPY', contracts: 1, expiry_days: 5, intent: 'sell_to_open',
    short_strike: 520, long_strike: 510, call_short_strike: 580, call_long_strike: 590,
  }], chain, io)
  assert.equal(results[0].status, 'failed')
  assert.equal(calls.length, 0) // nothing sent — never a partial condor
})
