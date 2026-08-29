import assert from 'node:assert/strict'
import test from 'node:test'
import { execFileSync } from 'node:child_process'
import { resolve } from 'node:path'

import { resolvePython } from '../index.js'

const repositoryRoot = resolve(import.meta.dirname, '../../..')
const python = resolvePython('python3') // 'python' on Windows, 'python3' elsewhere

test('python bridge exposes the expected fixed candidate sets', () => {
  const expected = {
    calm: ['hold', 'harvest_income'],           // VRP gate: calm-but-rich harvests
    elevated: ['hold', 'harvest_income'],
    stressed: ['partial_hedge', 'full_hedge'],  // 5-DTE protection stays under the cost cap
  }
  for (const [scenario, ids] of Object.entries(expected)) {
    const stdout = execFileSync(python, ['-m', 'agent.cli', 'context', '--scenario', scenario], {
      cwd: repositoryRoot,
      encoding: 'utf8',
    })
    const value = JSON.parse(stdout)
    assert.deepEqual(value.candidates.map(candidate => candidate.candidate_id), ids)
  }
})
