import assert from 'node:assert/strict'
import test from 'node:test'

import { decisionId } from '../decision-bridge.js'

test('decision IDs are stable for retries of one decision contract', () => {
  assert.equal(decisionId('context-1', 'hold'), decisionId('context-1', 'hold'))
  assert.notEqual(decisionId('context-1', 'hold'), decisionId('context-1', 'hedge'))
  assert.notEqual(decisionId('context-1', 'hold'), decisionId('context-2', 'hold'))
})
