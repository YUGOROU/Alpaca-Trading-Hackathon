import { Client } from '@modelcontextprotocol/sdk/client/index.js'
import { StdioClientTransport } from '@modelcontextprotocol/sdk/client/stdio.js'
import { ALPACA_MCP_VERSION, decodeMcpResult } from './alpaca-readonly.js'

// Order-capable connection to the SAME official server, still paper-enforced. Kept
// separate from the read-only wrapper so the write surface is opt-in and auditable.
const ORDER_TOOLSET = 'account,trading,options-data'
const PLACE_TOOL = 'place_option_order'

// Structures we can resolve to concrete listed contracts and place. Single-leg
// (protective_put buy, covered_call sell) go as a plain option order; the iron_condor
// goes as a 4-leg mleg order. Anything else is skipped rather than mis-traded.
const PLACEABLE_STRUCTURES = new Set(['protective_put', 'covered_call', 'iron_condor'])

function orderChildEnv(env) {
  const key = env.ALPACA_API_KEY
  const secret = env.ALPACA_SECRET_KEY
  if (!key || !secret) throw new Error('Alpaca paper credentials are not configured for order placement')
  return Object.fromEntries(Object.entries({
    PATH: env.PATH,
    HOME: env.HOME,
    LANG: env.LANG,
    UV_CACHE_DIR: env.UV_CACHE_DIR,
    UV_NO_PROGRESS: '1',
    ALPACA_API_KEY: key,
    ALPACA_SECRET_KEY: secret,
    ALPACA_PAPER_TRADE: 'true', // hard paper enforcement — never live from this path
    ALPACA_TOOLSETS: ORDER_TOOLSET,
  }).filter(([, value]) => value !== undefined))
}

export async function connectAlpacaOrders(env = process.env) {
  const transport = new StdioClientTransport({
    command: 'uvx',
    args: [`--from=alpaca-mcp-server==${ALPACA_MCP_VERSION}`, 'alpaca-mcp-server'],
    env: orderChildEnv(env),
    stderr: 'pipe',
  })
  const client = new Client({ name: 'alpaca-portfolio-orders', version: '0.1.0' })
  await client.connect(transport)
  return { client, close: () => client.close() }
}

function pad(n, width) {
  return String(n).padStart(width, '0')
}

// Build an OCC symbol: ROOT + YYMMDD + C/P + strike*1000 (8 digits). e.g. SPY240920P00520000
export function occSymbol(root, expiry, right, strike) {
  const yy = pad(expiry.getUTCFullYear() % 100, 2)
  const mm = pad(expiry.getUTCMonth() + 1, 2)
  const dd = pad(expiry.getUTCDate(), 2)
  const strk = pad(Math.round(strike * 1000), 8)
  return `${root.toUpperCase()}${yy}${mm}${dd}${right.toUpperCase()}${strk}`
}

function parseOccExpiry(occ) {
  const ymd = occ.slice(-15, -9)
  return new Date(Date.UTC(2000 + Number(ymd.slice(0, 2)), Number(ymd.slice(2, 4)) - 1, Number(ymd.slice(4, 6))))
}

function positionRows(value) {
  if (Array.isArray(value)) return value
  if (Array.isArray(value?.positions)) return value.positions
  if (Array.isArray(value?.data)) return value.data
  return []
}

// A hedge reduction is abstract at the risk-gate layer. Resolve it only from
// a positive broker position at execution time; never synthesize an OCC symbol.
export function resolveHeldProtectivePuts(positions, order, now = new Date()) {
  const requested = Number(order.contracts)
  if (!Number.isInteger(requested) || requested <= 0) {
    throw new Error('close order requires a positive whole contract quantity')
  }
  const candidates = []
  for (const row of positionRows(positions)) {
    const symbol = String(row?.symbol ?? row?.asset_symbol ?? '')
    const qty = Number(row?.qty ?? row?.quantity ?? 0)
    if (!Number.isInteger(qty) || qty <= 0) continue
    // OCC has a fixed YYMMDD + right + 8-digit strike suffix. The root must be
    // exact: a SPYG put is not a SPY hedge merely because it shares a prefix.
    if (symbol.length < 15 || symbol.slice(0, -15) !== String(order.symbol).toUpperCase() || symbol.at(-9) !== 'P') continue
    let expiry
    try { expiry = parseOccExpiry(symbol) } catch { continue }
    const days = Math.round((expiry - now) / 86_400_000)
    if (days <= 0) continue
    const strike = Number(symbol.slice(-8)) / 1000
    const score = [Math.abs(days - Number(order.expiry_days)), Math.abs(strike - Number(order.strike))]
    candidates.push({ symbol, strike, expiry, qty, score })
  }
  candidates.sort((a, b) => a.score[0] - b.score[0] || a.score[1] - b.score[1] || a.symbol.localeCompare(b.symbol))
  let remaining = requested
  const allocations = []
  for (const candidate of candidates) {
    if (remaining === 0) break
    const contracts = Math.min(candidate.qty, remaining)
    allocations.push({ ...candidate, contracts })
    remaining -= contracts
  }
  if (remaining !== 0) throw new Error('positive held SPY protective puts cannot satisfy the close order')
  return allocations
}

// Backward-compatible convenience for callers that require one exact contract.
export function resolveHeldProtectivePut(positions, order, now = new Date()) {
  const allocations = resolveHeldProtectivePuts(positions, order, now)
  if (allocations.length !== 1) throw new Error('close order spans multiple held SPY protective puts')
  return allocations[0]
}

// Resolve one leg (right P/C + target strike + target DTE) to a real listed contract by
// scanning the option-chain snapshot for the nearest expiry, then the nearest strike.
// Returns { symbol, strike, expiry } or throws — we never place an unresolved contract.
export function resolveContract(chain, right, targetStrike, targetDays, now = new Date()) {
  let best = null
  for (const occ of Object.keys(chain || {})) {
    if (occ.length < 15) continue
    if (occ[occ.length - 9] !== right) continue
    let expiry
    try { expiry = parseOccExpiry(occ) } catch { continue }
    const days = Math.round((expiry - now) / 86_400_000)
    if (days <= 0) continue
    const strike = Number(occ.slice(-8)) / 1000
    const score = [Math.abs(days - targetDays), Math.abs(strike - targetStrike)]
    if (best === null || score[0] < best.score[0] || (score[0] === best.score[0] && score[1] < best.score[1])) {
      best = { symbol: occ, strike, expiry, score }
    }
  }
  if (best === null) throw new Error(`no listed ${right} resolves ~${targetStrike} @ ${targetDays}d`)
  return { symbol: best.symbol, strike: best.strike, expiry: best.expiry }
}

// Back-compat single-put helper (the hedge path + its test rely on this exact shape).
export function resolveHedgeContract(order, optionChain, now = new Date()) {
  return resolveContract(optionChain, 'P', order.strike, order.expiry_days, now)
}

function sideFor(intent) {
  if (intent === 'buy_to_open' || intent === 'buy_to_close') return 'buy'
  if (intent === 'sell_to_open' || intent === 'sell_to_close') return 'sell'
  throw new Error(`unknown order intent: ${intent}`)
}

// The abstract legs of each structure: {right, strike, action} — action is open-side
// (buy = long/debit leg, sell = short/credit leg). This is where "how we decide the
// options" is made concrete: the engine picked the deltas/strikes; here they become the
// exact P/C legs to send.
export function legSpecs(order) {
  if (order.structure === 'protective_put') {
    return [{ right: 'P', strike: order.strike, action: sideFor(order.intent) }]
  }
  if (order.structure === 'covered_call') {
    return [{ right: 'C', strike: order.short_strike, action: 'sell' }]
  }
  if (order.structure === 'iron_condor') {
    return [
      { right: 'P', strike: order.short_strike, action: 'sell' },      // short put  (credit)
      { right: 'P', strike: order.long_strike, action: 'buy' },        // long put   (protection)
      { right: 'C', strike: order.call_short_strike, action: 'sell' }, // short call (credit)
      { right: 'C', strike: order.call_long_strike, action: 'buy' },   // long call  (protection)
    ]
  }
  throw new Error(`unsupported structure: ${order.structure}`)
}

// Shapes verified against alpaca-mcp-server 2.2.1's place_option_order schema
// (qty/ratio_qty are strings; single-leg uses side; multi-leg uses per-leg side +
// position_intent). Market order, day TIF, paper-enforced by the child env above.
export function buildPlaceArgs(resolved, order) {
  return {
    symbol: resolved.symbol,
    side: sideFor(order.intent),
    qty: String(order.contracts),
    type: 'market',
    time_in_force: 'day',
    position_intent: order.intent,
    // Broker-level idempotency: a retry with the same id is rejected, not duplicated.
    ...(order.client_order_id ? { client_order_id: order.client_order_id } : {}),
  }
}

export function buildMlegArgs(order, resolvedLegs) {
  return {
    order_class: 'mleg',
    qty: String(order.contracts),
    type: 'market',
    time_in_force: 'day',
    ...(order.client_order_id ? { client_order_id: order.client_order_id } : {}),
    legs: resolvedLegs.map((leg) => ({
      symbol: leg.symbol,
      ratio_qty: '1',
      side: leg.action, // "buy" | "sell"
      position_intent: leg.action === 'sell' ? 'sell_to_open' : 'buy_to_open',
    })),
  }
}

// Fetch a fresh P or C chain for SPY through the order client (has options-data). Placement
// resolves its own contracts (both rights) rather than the read-only snapshot's puts-only view.
export async function fetchOptionChain(client, type) {
  const raw = await client.callTool({
    name: 'get_option_chain',
    arguments: { underlying_symbol: 'SPY', feed: 'indicative', limit: 250, type },
  })
  return decodeMcpResult(raw) || {}
}

export async function fetchOptionPositions(client) {
  return decodeMcpResult(await client.callTool({ name: 'get_all_positions', arguments: {} })) || []
}

export async function placeGateOrders(client, gateOrders, optionChain, io = { stderr: process.stderr }) {
  const results = []
  for (const order of gateOrders) {
    if (!PLACEABLE_STRUCTURES.has(order.structure)) {
      results.push({ order, status: 'skipped', reason: `structure ${order.structure} not auto-placeable` })
      io.stderr.write(`orders: skipped ${order.structure}\n`)
      continue
    }
    try {
      // Resolve every leg first — a partially-resolved structure is never sent (fail-closed).
      const resolved = order.intent === 'sell_to_close'
        ? resolveHeldProtectivePuts(await fetchOptionPositions(client), order).map((leg) => ({ ...leg, action: 'sell' }))
        : legSpecs(order).map((leg) => ({
          ...resolveContract(optionChain, leg.right, leg.strike, order.expiry_days),
          action: leg.action,
        }))
      if (order.intent === 'sell_to_close') {
        for (const [index, leg] of resolved.entries()) {
          const closeOrder = { ...order, contracts: leg.contracts, client_order_id: `${order.client_order_id}-close-${index + 1}` }
          const raw = await client.callTool({ name: PLACE_TOOL, arguments: buildPlaceArgs(leg, closeOrder) })
          results.push({ order: closeOrder, status: 'placed', contracts: [leg.symbol], result: decodeMcpResult(raw) })
        }
        io.stderr.write(`orders: placed protective_put close x${order.contracts} [${resolved.map((r) => r.symbol).join(', ')}]\n`)
      } else {
        const args = resolved.length === 1
          ? buildPlaceArgs(resolved[0], order)
          : buildMlegArgs(order, resolved)
        const raw = await client.callTool({ name: PLACE_TOOL, arguments: args })
        const symbols = resolved.map((r) => r.symbol)
        results.push({ order, status: 'placed', contracts: symbols, result: decodeMcpResult(raw) })
        io.stderr.write(`orders: placed ${order.structure} x${order.contracts} [${symbols.join(', ')}]\n`)
      }
    } catch (error) {
      results.push({ order, status: 'failed', reason: error instanceof Error ? error.message : String(error) })
      io.stderr.write(`orders: FAILED to place ${order.structure}: ${error}\n`)
    }
  }
  return results
}
