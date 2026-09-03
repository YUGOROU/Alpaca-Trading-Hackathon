import { resolve } from 'node:path'
import { dirname } from 'node:path'
import { fileURLToPath } from 'node:url'
import { Command } from 'commander'
import { parseCmdline } from '@deepseek-ai/dsh-cmdline'

export const name = 'portfolio-startup'
export const inject = ['cmdlineArgs']

const repositoryRoot = resolve(dirname(fileURLToPath(import.meta.url)), '../..')

function command() {
  return new Command()
    .name('dsh --profile portfolio-agent')
    .description('Run a bounded regime-adaptive portfolio decision, once or on a heartbeat.')
    .helpOption('-h, --help', 'show this help')
    .option('--scenario <name>', 'fixed evaluation or replay scenario')
    .option('--mock', 'observe the explicit local mock source (never live)', false)
    .option('--live', 'observe the live Alpaca paper account instead of a fixture', false)
    .option('--heartbeat', 'run continuously on an interval instead of one shot', false)
    .option('--interval <ms>', 'heartbeat observation interval in milliseconds', String(300_000))
    .option('--place', 'deprecated; autonomous placement is controlled by PAPER_EXECUTION_MODE', false)
    .option('--ledger <path>', 'canonical proposal JSONL ledger', '.agent/decisions.jsonl')
    .argument('[instruction...]', 'decision objective')
}

export function apply(ctx) {
  const program = command()
  program.action((instructionParts, options) => {
    const instruction = instructionParts.join(' ').trim()
    if (instruction === '') program.error('error: an instruction is required')
    if (Number(Boolean(options.live)) + Number(Boolean(options.mock)) + Number(Boolean(options.scenario)) !== 1) {
      program.error('error: provide exactly one of --scenario <name>, --mock, or --live')
    }
    if (options.place) program.error('error: --place is deprecated; use PAPER_EXECUTION_MODE=autonomous-options-overlay')
    if (options.scenario && ![
      'calm', 'elevated', 'stressed', 'near_risk_limit', 'near_coverage_limit',
      'suboptimal_alternative', 'tradeoff_choice', 'untrusted_data',
    ].includes(options.scenario)) {
      program.error('error: unknown fixed evaluation scenario')
    }
    const intervalMs = Number(options.interval)
    if (!Number.isFinite(intervalMs) || intervalMs <= 0) {
      program.error('error: --interval must be a positive number of milliseconds')
    }
    ctx.provide('portfolioStartup', {
      repositoryRoot,
      scenario: options.scenario ?? null,
      mock: Boolean(options.mock),
      live: Boolean(options.live),
      heartbeat: Boolean(options.heartbeat),
      intervalMs,
      executionMode: process.env.PAPER_EXECUTION_MODE === 'autonomous-options-overlay' && options.live && options.heartbeat
        ? 'autonomous-paper'
        : 'human',
      ledgerPath: resolve(options.ledger),
      instruction,
    })
  })
  parseCmdline(ctx, program)
}
