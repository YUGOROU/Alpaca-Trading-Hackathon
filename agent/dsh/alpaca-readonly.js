import { Client } from '@modelcontextprotocol/sdk/client/index.js'
import { StdioClientTransport } from '@modelcontextprotocol/sdk/client/stdio.js'

export const ALPACA_MCP_VERSION = '2.2.1'

export const READONLY_TOOLS = Object.freeze([
  'get_account_info',
  'get_all_positions',
  'get_stock_bars',
  'get_stock_latest_trade',
  'get_option_chain',
])

const READONLY_TOOLSET = 'account,trading,stock-data,options-data'
const MAX_RESULT_CHARS = 100_000
const MAX_ARRAY_ITEMS = 200
const MAX_STRING_CHARS = 2_000
const SENSITIVE_FIELDS = new Set([
  'id',
  'account_id',
  'account_number',
  'user_id',
  'client_order_id',
])

function requiredCredentials(env) {
  const key = env.ALPACA_API_KEY
  const secret = env.ALPACA_SECRET_KEY
  if (!key || !secret) {
    throw new Error('Alpaca paper credentials are not configured in the DSH process environment')
  }
  return { key, secret }
}

function childEnvironment(env) {
  const { key, secret } = requiredCredentials(env)
  return Object.fromEntries(Object.entries({
    PATH: env.PATH,
    HOME: env.HOME,
    LANG: env.LANG,
    UV_CACHE_DIR: env.UV_CACHE_DIR,
    UV_NO_PROGRESS: '1',
    ALPACA_API_KEY: key,
    ALPACA_SECRET_KEY: secret,
    ALPACA_PAPER_TRADE: 'true',
    ALPACA_TOOLSETS: READONLY_TOOLSET,
  }).filter(([, value]) => value !== undefined))
}

export async function connectAlpacaMcp(env = process.env) {
  const transport = new StdioClientTransport({
    command: 'uvx',
    args: [`--from=alpaca-mcp-server==${ALPACA_MCP_VERSION}`, 'alpaca-mcp-server'],
    env: childEnvironment(env),
    stderr: 'pipe',
  })
  const client = new Client({ name: 'alpaca-portfolio-readonly', version: '0.1.0' })
  await client.connect(transport)
  return { client, close: () => client.close() }
}

export function decodeMcpResult(result) {
  if (result?.isError === true) throw new Error('Alpaca MCP returned an error result')
  if (result?.structuredContent !== undefined) {
    const encoded = JSON.stringify(result.structuredContent)
    if (encoded.length > MAX_RESULT_CHARS) {
      throw new Error(`Alpaca MCP result exceeded ${MAX_RESULT_CHARS} characters`)
    }
    return result.structuredContent
  }
  const text = (result?.content || [])
    .filter(block => block.type === 'text')
    .map(block => block.text)
    .join('\n')
  if (text.length > MAX_RESULT_CHARS) {
    throw new Error(`Alpaca MCP result exceeded ${MAX_RESULT_CHARS} characters`)
  }
  if (text === '') return null
  try {
    return JSON.parse(text)
  } catch {
    return { text }
  }
}

export function sanitizeExternalData(value) {
  if (typeof value === 'string') return value.slice(0, MAX_STRING_CHARS)
  if (Array.isArray(value)) return value.slice(0, MAX_ARRAY_ITEMS).map(sanitizeExternalData)
  if (value !== null && typeof value === 'object') {
    return Object.fromEntries(Object.entries(value)
      .filter(([key]) => !SENSITIVE_FIELDS.has(key.toLowerCase()))
      .map(([key, child]) => [key, sanitizeExternalData(child)]))
  }
  return value
}

async function checkedCall(client, available, name, args) {
  if (!READONLY_TOOLS.includes(name)) throw new Error(`refusing non-read-only MCP tool: ${name}`)
  if (!available.has(name)) throw new Error(`required Alpaca MCP tool is unavailable: ${name}`)
  return sanitizeExternalData(decodeMcpResult(
    await client.callTool({ name, arguments: args }),
  ))
}

export function assertReadonlyTool(name) {
  if (!READONLY_TOOLS.includes(name)) throw new Error(`refusing non-read-only MCP tool: ${name}`)
}

/**
 * Invoke one official Alpaca MCP read tool without exposing any mutation tool.
 * This is intentionally exported for the DSH profile's transparent MCP tools:
 * the model sees the official data operation, while this boundary still enforces
 * paper credentials, a fixed allowlist, bounded output, and identifier redaction.
 */
export async function withAlpacaReadonlyTool(name, args, env = process.env) {
  assertReadonlyTool(name)
  const connection = await connectAlpacaMcp(env)
  try {
    const listed = await connection.client.listTools()
    const available = new Set(listed.tools.map(tool => tool.name))
    return await checkedCall(connection.client, available, name, args)
  } finally {
    await connection.close()
  }
}

export async function fetchAlpacaReadonlySnapshot(client) {
  const listed = await client.listTools()
  const available = new Set(listed.tools.map(tool => tool.name))
  for (const name of READONLY_TOOLS) {
    if (!available.has(name)) throw new Error(`required Alpaca MCP tool is unavailable: ${name}`)
  }

  const account = await checkedCall(client, available, 'get_account_info', {})
  const positions = await checkedCall(client, available, 'get_all_positions', {})
  const spyDailyBars = await checkedCall(client, available, 'get_stock_bars', {
    symbols: 'SPY',
    timeframe: '1Day',
    days: 120,
    limit: 1000,
    adjustment: 'all',
    feed: 'iex',
    sort: 'asc',
  })
  const spyLatestTrade = await checkedCall(client, available, 'get_stock_latest_trade', {
    symbols: 'SPY',
    feed: 'iex',
  })
  const spyOptionChain = await checkedCall(client, available, 'get_option_chain', {
    underlying_symbol: 'SPY',
    feed: 'indicative',
    limit: 100,
    type: 'put',
  })
  return {
    source: `alpaca-mcp-server@${ALPACA_MCP_VERSION}`,
    mode: 'paper_readonly',
    fetched_at: new Date().toISOString(),
    account,
    positions,
    spy_daily_bars: spyDailyBars,
    spy_latest_trade: spyLatestTrade,
    spy_option_chain: spyOptionChain,
  }
}

export async function withAlpacaReadonlySnapshot(env = process.env) {
  const connection = await connectAlpacaMcp(env)
  try {
    return await fetchAlpacaReadonlySnapshot(connection.client)
  } finally {
    await connection.close()
  }
}
