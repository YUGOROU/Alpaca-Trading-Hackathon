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
    .option('--live', 'observe the live Alpaca paper account instead of a fixture', false)
    .option('--heartbeat', 'run continuously on an interval instead of one shot', false)
    .option('--interval <ms>', 'heartbeat interval in milliseconds', String(1_800_000))
    .option('--place', 'autonomously place paper orders for approved decisions', false)
    .option('--ledger <path>', 'dry-run JSONL ledger', '.agent/decisions.jsonl')
    .argument('[instruction...]', 'decision objective')
}

export function apply(ctx) {
  const program = command()
  program.action((instructionParts, options) => {
    const instruction = instructionParts.join(' ').trim()
    if (instruction === '') program.error('error: an instruction is required')
    if (!options.live && !options.scenario) {
      program.error('error: provide --scenario <name> or --live')
    }
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
      live: Boolean(options.live),
      heartbeat: Boolean(options.heartbeat),
      intervalMs,
      placeOrders: Boolean(options.place),
      ledgerPath: resolve(options.ledger),
      instruction,
    })
  })
  parseCmdline(ctx, program)
}
