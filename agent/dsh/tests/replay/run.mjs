import assert from 'node:assert/strict'
import { execFileSync, spawnSync } from 'node:child_process'
import { mkdtemp, readFile, writeFile } from 'node:fs/promises'
import { join, resolve } from 'node:path'
import { tmpdir } from 'node:os'

const dshRoot = resolve(import.meta.dirname, '../..')
const repositoryRoot = resolve(dshRoot, '../..')
const dsh = join(dshRoot, 'node_modules', '.bin', 'dsh')
const generatedOverride = join(dshRoot, 'tests', 'replay', '.generated.override.json')
const tempRoot = await mkdtemp(join(tmpdir(), 'alpaca-dsh-replay-'))
const dshHome = join(tempRoot, '.dsh')
const ledger = join(tempRoot, 'decisions.jsonl')

function run(args, cwd = dshRoot) {
  const result = spawnSync(dsh, args, {
    cwd,
    env: { ...process.env, DSH_HOME: dshHome },
    encoding: 'utf8',
  })
  assert.equal(result.status, 0, `${result.stdout}\n${result.stderr}`)
}

run(['plugin', '--profile', 'portfolio-agent', 'add', dshRoot])
run(['plugin', '--profile', 'portfolio-agent', 'add', '@deepseek-ai/dsh-llm-replay@0.1.1-rc.2'])

const selections = {
  calm: 'hold',
  elevated: 'harvest_income',
  stressed: 'full_hedge',
}

for (const [scenario, candidateId] of Object.entries(selections)) {
  const context = JSON.parse(execFileSync(
    'python3', ['-m', 'agent.cli', 'context', '--scenario', scenario],
    { cwd: repositoryRoot, encoding: 'utf8' },
  ))
  const contextId = context.context_id
  assert.ok(context.candidates.some(candidate => candidate.candidate_id === candidateId))
  const override = [
    {
      kind: 'chunks',
      chunks: [
        { type: 'block-start', index: 0, blockType: 'tool-call' },
        { type: 'tool-call-delta', index: 0, id: `${scenario}-get`, name: 'get_decision_context', argumentsDelta: '{}' },
        { type: 'block-end', index: 0, block: { type: 'tool-call', id: `${scenario}-get`, name: 'get_decision_context', arguments: '{}' } },
        { type: 'finish', reason: { kind: 'tool-calls' } },
      ],
    },
    {
      kind: 'chunks',
      chunks: [
        { type: 'block-start', index: 0, blockType: 'tool-call' },
        { type: 'tool-call-delta', index: 0, id: `${scenario}-submit`, name: 'submit_decision', argumentsDelta: JSON.stringify({ context_id: contextId, candidate_id: candidateId, reason: `Replay selection for ${scenario}.` }) },
        { type: 'block-end', index: 0, block: { type: 'tool-call', id: `${scenario}-submit`, name: 'submit_decision', arguments: JSON.stringify({ context_id: contextId, candidate_id: candidateId, reason: `Replay selection for ${scenario}.` }) } },
        { type: 'finish', reason: { kind: 'tool-calls' } },
      ],
    },
  ]
  await writeFile(generatedOverride, JSON.stringify(override, null, 2))
  run([
    '--profile', 'portfolio-agent',
    '--patch', './tests/replay/cordis.patch.yml',
    '--scenario', scenario,
    '--ledger', ledger,
    `Evaluate the ${scenario} context and select one admissible candidate.`,
  ])
}

const rows = (await readFile(ledger, 'utf8')).trim().split('\n').map(JSON.parse)
assert.equal(rows.length, 3)
assert.deepEqual(rows.map(row => row.scenario_id), ['calm', 'elevated', 'stressed'])
assert.ok(rows.every(row => row.gate.status === 'approved_for_dry_run'))
assert.ok(rows.every(row => row.gate.human_approval_required === true))
console.log(`DSH replay E2E passed for 3 regimes; artifacts: ${tempRoot}`)
