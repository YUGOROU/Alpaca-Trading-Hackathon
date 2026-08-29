import assert from 'node:assert/strict'

import { READONLY_TOOLS, connectAlpacaMcp } from '../alpaca-readonly.js'

// Non-secret placeholders are sufficient for local server construction and tools/list.
// This probe performs no Alpaca API tool calls.
const connection = await connectAlpacaMcp({
  PATH: process.env.PATH,
  HOME: process.env.HOME,
  LANG: process.env.LANG,
  UV_CACHE_DIR: process.env.UV_CACHE_DIR,
  ALPACA_API_KEY: 'schema-probe-not-a-key',
  ALPACA_SECRET_KEY: 'schema-probe-not-a-secret',
})
try {
  const listed = await connection.client.listTools()
  const names = new Set(listed.tools.map(tool => tool.name))
  for (const name of READONLY_TOOLS) assert.ok(names.has(name), `missing ${name}`)
  assert.ok(names.has('place_option_order'), 'expected official server mutation surface')
  assert.ok(names.has('close_all_positions'), 'expected official server mutation surface')
  console.log(`Alpaca MCP schema probe passed; wrapper allowlist: ${READONLY_TOOLS.join(', ')}`)
} finally {
  await connection.close()
}
