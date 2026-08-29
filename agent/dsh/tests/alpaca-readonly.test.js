import assert from 'node:assert/strict'
import test from 'node:test'

import {
  ALPACA_MCP_VERSION,
  READONLY_TOOLS,
  assertReadonlyTool,
  connectAlpacaMcp,
  decodeMcpResult,
  fetchAlpacaReadonlySnapshot,
  sanitizeExternalData,
  withAlpacaReadonlyTool,
} from '../alpaca-readonly.js'

class FakeClient {
  constructor() {
    this.calls = []
  }

  async listTools() {
    return {
      tools: [
        ...READONLY_TOOLS.map(name => ({ name })),
        { name: 'place_option_order' },
        { name: 'close_all_positions' },
      ],
    }
  }

  async callTool({ name, arguments: args }) {
    this.calls.push({ name, args })
    return { structuredContent: { tool: name, args } }
  }
}

test('decodes structured and JSON-text MCP results', () => {
  assert.deepEqual(decodeMcpResult({ structuredContent: { ok: true } }), { ok: true })
  assert.deepEqual(
    decodeMcpResult({ content: [{ type: 'text', text: '{"ok":true}' }] }),
    { ok: true },
  )
})

test('connection fails closed before spawn when paper credentials are absent', async () => {
  await assert.rejects(
    connectAlpacaMcp({ PATH: process.env.PATH, HOME: process.env.HOME }),
    /paper credentials are not configured/,
  )
})

test('redacts account identifiers before model/session exposure', () => {
  assert.deepEqual(sanitizeExternalData({
    id: 'private-id',
    account_number: 'private-account',
    equity: '100000',
    nested: { account_id: 'private-nested', symbol: 'SPY' },
  }), {
    equity: '100000',
    nested: { symbol: 'SPY' },
  })
})

test('official MCP wrapper calls only the five read-only tools', async () => {
  const client = new FakeClient()
  const snapshot = await fetchAlpacaReadonlySnapshot(client)
  assert.equal(snapshot.source, `alpaca-mcp-server@${ALPACA_MCP_VERSION}`)
  assert.equal(snapshot.mode, 'paper_readonly')
  assert.deepEqual(client.calls.map(call => call.name), READONLY_TOOLS)
  assert.ok(client.calls.every(call => !call.name.includes('place')))
  assert.ok(client.calls.every(call => !call.name.includes('close')))
  assert.equal(client.calls.find(call => call.name === 'get_stock_bars').args.feed, 'iex')
  assert.equal(client.calls.find(call => call.name === 'get_option_chain').args.feed, 'indicative')
})

test('transparent tool boundary rejects order names before any MCP call', () => {
  assert.throws(() => assertReadonlyTool('place_option_order'), /refusing non-read-only MCP tool/)
})
