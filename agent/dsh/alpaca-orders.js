import { Client } from '@modelcontextprotocol/sdk/client/index.js'
import { StdioClientTransport } from '@modelcontextprotocol/sdk/client/stdio.js'
import { ALPACA_MCP_VERSION, FASTMCP_VERSION, decodeMcpResult } from './alpaca-readonly.js'

// Order-capable connection to the SAME official server, still paper-enforced. Kept
// separate from the read-only wrapper so the write surface is opt-in and auditable.
const ORDER_TOOLSET = 'account,trading,options-data'
const PLACE_TOOL = 'place_option_order'
// This response is consumed only by the deterministic executor, never exposed
// to the model.  A 250-contract chain can exceed the model-tool 100KB limit.
const ORDER_CHAIN_MAX_RESULT_CHARS = 500_000

// Structures we can resolve to concrete listed contracts and place. Single-leg
// (protective_put buy, covered_call sell) go as a plain option order; the iron_condor
// goes as a 4-leg mleg order. Anything else is skipped rather than mis-traded.
const PLACEABLE_STRUCTURES = new Set([
  'protective_put', 'covered_call', 'iron_condor', 'bull_put_spread', 'bear_call_spread',
])

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
    args: [
      '--with', `fastmcp==${FASTMCP_VERSION}`,
      `--from=alpaca-mcp-server==${ALPACA_MCP_VERSION}`, 'alpaca-mcp-server',
    ],
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

function trackedProtectivePutSymbols(records) {
  const symbols = new Set()
  for (const record of Array.isArray(records) ? records : []) {
    const symbol = String(record?.contract ?? '')
    if (
      record?.event === 'protective_put_opened'
      && record?.strategy === 'protective_put'
      && record?.leg_role === 'hedge_long_put'
      && record?.underlying === 'SPY'
      && /^SPY\d{6}P\d{8}$/.test(symbol)
      && Number.isInteger(record?.quantity_opened) && record.quantity_opened > 0
      && typeof record?.broker_order_id === 'string' && record.broker_order_id
    ) symbols.add(symbol)
  }
  return symbols
}

// Resolve an abstract hedge reduction only from the intersection of fresh,
// positive broker positions and the append-only autonomous hedge provenance.
// One gate order must remain one broker order. When the gate asks to reduce
// across several contracts, this selects one deterministic contract and caps
// this cycle's close to its held quantity rather than fanning out orders.
export function resolveHeldTrackedProtectivePut(positions, trackedContracts, order, now = new Date()) {
  const requested = Number(order.contracts)
  if (!Number.isInteger(requested) || requested <= 0) {
    throw new Error('close order requires a positive whole contract quantity')
  }
  const tracked = trackedProtectivePutSymbols(trackedContracts)
  let best = null
  for (const row of positionRows(positions)) {
    const symbol = String(row?.symbol ?? row?.asset_symbol ?? '')
    const qty = Number(row?.qty ?? row?.quantity ?? 0)
    if (!Number.isInteger(qty) || qty <= 0 || !tracked.has(symbol)) continue
    let expiry
    try { expiry = parseOccExpiry(symbol) } catch { continue }
    const days = Math.round((expiry - now) / 86_400_000)
    if (days <= 0) continue
    const strike = Number(symbol.slice(-8)) / 1000
    if (!Number.isFinite(strike)) continue
    const score = [Math.abs(days - Number(order.expiry_days)), Math.abs(strike - Number(order.strike)), symbol]
    if (best === null || score[0] < best.score[0] || (score[0] === best.score[0] && (score[1] < best.score[1] || (score[1] === best.score[1] && score[2] < best.score[2])))) {
      best = { symbol, strike, expiry, score, contracts: Math.min(requested, qty) }
    }
  }
  if (best === null) throw new Error('no positive held provenance-recorded SPY protective put can satisfy the close order')
  return best
}

// Build an OCC symbol: ROOT + YYMMDD + C/P + strike*1000 (8 digits). e.g. SPY240920P00520000
export function occSymbol(root, expiry, right, strike) {
  const yy = pad(expiry.getUTCFullYear() % 100, 2)
  const mm = pad(expiry.getUTCMonth() + 1, 2)
  const dd = pad(expiry.getUTCDate(), 2)
  const strk = pad(Math.round(strike * 1000), 8)
  return `${root.toUpperCase()}${yy}${mm}${dd}${right.toUpperCase()}${strk}`
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
  if (order.structure === 'bull_put_spread') {
    return [
      { right: 'P', strike: order.short_strike, action: 'sell' },
      { right: 'P', strike: order.long_strike, action: 'buy' },
    ]
  }
  if (order.structure === 'bear_call_spread') {
    return [
      { right: 'C', strike: order.short_strike, action: 'sell' },
      { right: 'C', strike: order.long_strike, action: 'buy' },
    ]
  }
  throw new Error(`unsupported structure: ${order.structure}`)
}

// Shapes verified against alpaca-mcp-server 2.2.1's place_option_order schema
// (qty/ratio_qty are strings; single-leg uses side; multi-leg uses per-leg side +
// position_intent). Autonomous orders are bounded limit orders; human review
// keeps the historical market-order behaviour.
export function buildPlaceArgs(resolved, order, executable = null) {
  return {
    symbol: resolved.symbol,
    side: sideFor(order.intent),
    qty: String(order.contracts),
    type: executable ? 'limit' : 'market',
    time_in_force: 'day',
    position_intent: order.intent,
    ...(executable ? { limit_price: executable.limitPrice } : {}),
    // Broker-level idempotency: a retry with the same id is rejected, not duplicated.
    ...(order.client_order_id ? { client_order_id: order.client_order_id } : {}),
  }
}

export function buildMlegArgs(order, resolvedLegs, executable = null) {
  return {
    order_class: 'mleg',
    qty: String(order.contracts),
    type: executable ? 'limit' : 'market',
    time_in_force: 'day',
    ...(executable ? { limit_price: executable.limitPrice } : {}),
    ...(order.client_order_id ? { client_order_id: order.client_order_id } : {}),
    legs: resolvedLegs.map((leg) => ({
      symbol: leg.symbol,
      ratio_qty: '1',
      side: leg.action, // "buy" | "sell"
      position_intent: leg.action === 'sell' ? 'sell_to_open' : 'buy_to_open',
    })),
  }
}

function quoteValue(snapshot, names) {
  for (const container of [snapshot, snapshot?.latest_quote, snapshot?.latestQuote, snapshot?.quote]) {
    if (!container || typeof container !== 'object') continue
    for (const name of names) {
      const value = Number(container[name])
      if (Number.isFinite(value) && value > 0) return value
    }
  }
  return null
}

function quoteFor(resolved, side) {
  const names = side === 'buy'
    ? ['ask_price', 'askPrice', 'ap']
    : ['bid_price', 'bidPrice', 'bp']
  const price = quoteValue(resolved.snapshot, names)
  if (price === null) throw new Error(`missing executable ${side === 'buy' ? 'ask' : 'bid'} for ${resolved.symbol}`)
  return price
}

function priceString(value) {
  if (!Number.isFinite(value)) throw new Error('non-finite executable option price')
  return value.toFixed(2)
}

// Return the worst immediately executable price: ask for bought legs, bid for
// sold legs.  The same price is placed as a limit, so a later fill cannot be
// worse than the value that passed the deterministic cap.
export function executableOrderPrice(order, resolvedLegs) {
  const legPrices = resolvedLegs.map((leg) => ({ ...leg, price: quoteFor(leg, leg.action) }))
  if (legPrices.length === 1) {
    const debit = legPrices[0].action === 'buy' ? legPrices[0].price : -legPrices[0].price
    const totalCost = Math.max(0, debit) * 100 * order.contracts
    if (order.structure === 'protective_put') {
      const cap = Number(order.max_total_cost)
      if (!Number.isFinite(cap) || cap < 0 || totalCost > cap + 1e-9) {
        throw new Error('executable protective-put cost exceeds deterministic cap')
      }
    }
    return { limitPrice: priceString(legPrices[0].price), netDebit: debit }
  }

  // Alpaca's mleg API takes a signed net price: positive debit, negative credit.
  const netDebit = legPrices.reduce(
    (total, leg) => total + (leg.action === 'buy' ? leg.price : -leg.price), 0,
  )
  if (['iron_condor', 'bull_put_spread', 'bear_call_spread'].includes(order.structure)) {
    const putShort = legPrices.find((leg) => leg.right === 'P' && leg.action === 'sell')
    const putLong = legPrices.find((leg) => leg.right === 'P' && leg.action === 'buy')
    const callShort = legPrices.find((leg) => leg.right === 'C' && leg.action === 'sell')
    const callLong = legPrices.find((leg) => leg.right === 'C' && leg.action === 'buy')
    // Contract resolution can snap the model's target to a listed strike. Risk
    // must use those actual strikes, never the pre-resolution target values.
    const width = order.structure === 'bull_put_spread'
      ? Number(putShort?.strike) - Number(putLong?.strike)
      : order.structure === 'bear_call_spread'
        ? Number(callLong?.strike) - Number(callShort?.strike)
        : Math.max(
          Number(putShort?.strike) - Number(putLong?.strike),
          Number(callLong?.strike) - Number(callShort?.strike),
        )
    const totalLoss = (width + netDebit) * 100 * order.contracts
    const cap = Number(order.max_total_loss)
    if (!Number.isFinite(width) || width <= 0 || !Number.isFinite(cap) || cap < 0 || totalLoss < 0 || totalLoss > cap + 1e-9) {
      throw new Error(`executable ${order.structure} loss exceeds deterministic cap`)
    }
  }
  return { limitPrice: priceString(netDebit), netDebit }
}

// Fetch a fresh P or C chain for the gate-approved underlying through the order client.
// Placement resolves its own contracts rather than the read-only snapshot's puts-only view.
export async function fetchOptionChain(client, type, underlyingSymbol = 'SPY', {
  expiryDays = 0,
  targetStrikes = [],
  now = new Date(),
} = {}) {
  const strikes = targetStrikes.map(Number).filter(Number.isFinite)
  const expiryFloor = new Date(now.getTime() + Math.max(0, expiryDays) * 86_400_000)
  const raw = await client.callTool({
    name: 'get_option_chain',
    arguments: {
      underlying_symbol: underlyingSymbol,
      feed: 'indicative',
      type,
      // Calendar-day targets can fall on weekends or exchange holidays. The
      // resolver therefore chooses the nearest listed expiry on or after it.
      ...(expiryDays > 0 ? { expiration_date_gte: expiryFloor.toISOString().slice(0, 10) } : {}),
      ...(strikes.length ? {
        strike_price_gte: Math.min(...strikes) - 10,
        strike_price_lte: Math.max(...strikes) + 10,
      } : {}),
      limit: 250,
    },
  })
  // The Alpaca API's OptionChain body is `{ snapshots: { OCC: snapshot } }`.
  // alpaca-mcp-server 2.2.1 wraps that body in its trust-boundary envelope as
  // `{ _alpaca_mcp_security, data: { snapshots: ... } }`. Contract resolution
  // must operate on the OCC-keyed map, never either envelope. Walking an
  // envelope made every valid chain look like non-OCC keys and therefore fail
  // closed as "no listed contract".
  const decoded = decodeMcpResult(raw, { maxResultChars: ORDER_CHAIN_MAX_RESULT_CHARS }) || {}
  const snapshots = decoded?.data?.snapshots ?? decoded?.snapshots
  return snapshots && typeof snapshots === 'object' ? snapshots : {}
}

export async function fetchOptionPositions(client) {
  return decodeMcpResult(await client.callTool({ name: 'get_all_positions', arguments: {} })) || []
}

export async function placeGateOrders(
  client, gateOrders, optionChain, io = { stderr: process.stderr }, { executablePrices = false, trackedContracts = [] } = {},
) {
  const results = []
  for (const order of gateOrders) {
    if (!PLACEABLE_STRUCTURES.has(order.structure)) {
      results.push({ order, status: 'skipped', reason: `structure ${order.structure} not auto-placeable` })
      io.stderr.write(`orders: skipped ${order.structure}\n`)
      continue
    }
    try {
      // Resolve every leg first — a partially-resolved structure is never sent (fail-closed).
      const closeContract = order.intent === 'sell_to_close'
        ? resolveHeldTrackedProtectivePut(await fetchOptionPositions(client), trackedContracts, order)
        : null
      const executionOrder = closeContract ? { ...order, contracts: closeContract.contracts } : order
      const resolved = closeContract
        ? [{ ...closeContract, right: 'P', action: 'sell', snapshot: optionChain[closeContract.symbol] }]
        : legSpecs(order).map((leg) => {
          const contract = resolveContract(optionChain, leg.right, leg.strike, order.expiry_days)
          return { ...contract, right: leg.right, action: leg.action, snapshot: optionChain[contract.symbol] }
        })
      const executable = executablePrices ? executableOrderPrice(executionOrder, resolved) : null
      const args = resolved.length === 1
        ? buildPlaceArgs(resolved[0], executionOrder, executable)
        : buildMlegArgs(executionOrder, resolved, executable)
      const raw = await client.callTool({ name: PLACE_TOOL, arguments: args })
      const symbols = resolved.map((r) => r.symbol)
      results.push({ order: executionOrder, status: 'placed', contracts: symbols, result: decodeMcpResult(raw) })
      io.stderr.write(`orders: placed ${order.structure} x${executionOrder.contracts} [${symbols.join(', ')}]\n`)
    } catch (error) {
      results.push({ order, status: 'failed', reason: error instanceof Error ? error.message : String(error) })
      io.stderr.write(`orders: FAILED to place ${order.structure}: ${error}\n`)
    }
  }
  return results
}
