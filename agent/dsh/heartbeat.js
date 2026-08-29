import { randomUUID } from 'node:crypto'
import Schema from '@deepseek-ai/schemastery'
import { installModelSelection } from '@deepseek-ai/dsh-agent'
import { createUserMessage } from '@deepseek-ai/dsh-llm'
import { SessionId } from '@deepseek-ai/dsh-session'
import { runModelNativeDecision } from './model-native-adapter.js'

export const name = 'portfolio-heartbeat'
export const inject = ['agentDefaultModel', 'agents', 'sessions']

export const Config = Schema.object({
  repositoryRoot: Schema.string().required(),
  scenario: Schema.string(),
  live: Schema.boolean().default(false),
  ledgerPath: Schema.string().required(),
  pythonExecutable: Schema.string().default('python3'),
  placeOrders: Schema.boolean().default(false),
  heartbeat: Schema.boolean().default(false), // only loop when heartbeat mode is selected
  instruction: Schema.string().required(),
  intervalMs: Schema.number().default(1_800_000), // 30 min — matches the retired cron cadence
  marketHoursOnly: Schema.boolean().default(true),
  maxCycles: Schema.number().default(0), // 0 = run until stopped
})

const RTH_START_UTC = 13 // ~09:30 ET, widened to cover DST like the old cron (13-21 UTC)
const RTH_END_UTC = 21

export function isMarketOpen(now = new Date()) {
  const day = now.getUTCDay() // 0 Sun .. 6 Sat
  if (day === 0 || day === 6) return false
  const hour = now.getUTCHours()
  return hour >= RTH_START_UTC && hour < RTH_END_UTC
}

function cancellableSleep(ms) {
  let cancel
  const promise = new Promise((res) => {
    const t = setTimeout(res, ms)
    cancel = () => { clearTimeout(t); res() }
  })
  return { promise, cancel }
}

function turnOutcome(events, firstSeq) {
  let reason
  for (const event of events) {
    if (event.seq >= firstSeq && event.type === 'turn/end') reason = event.data.reason
  }
  return reason
}

async function runOneCycle(ctx, config) {
  if (process.env.HF_MODEL_ID) {
    const result = await runModelNativeDecision(config)
    return result.status === 'completed' ? { kind: 'completed' } : {
      kind: 'error', error: { code: result.failure, message: 'model-native tool calling failed' },
    }
  }
  const selection = ctx.agentDefaultModel.currentSelection()
  const { agent } = await ctx.agents.create({
    sessionId: SessionId(`portfolio-heartbeat-${randomUUID()}`),
    meta: { cwd: process.cwd() },
    agentOptions: { provider: selection.provider, model: selection.model },
    setup: (agentCtx) => {
      installModelSelection(agentCtx, { current: selection, assembled: undefined })
    },
  })
  await agent.whenIdle()
  const firstSeq = agent.session.seq
  agent.followup(createUserMessage({
    content: [{ type: 'text', text: config.instruction }],
    source: { kind: 'user' },
  }))
  await agent.whenIdle()
  await ctx.sessions.flush(agent.session)
  return turnOutcome(agent.session.events, firstSeq)
}

async function loop(ctx, config, io, isStopped) {
  await ctx.get('loader')?.await()
  let cycles = 0
  while (!isStopped()) {
    if (!config.marketHoursOnly || isMarketOpen()) {
      try {
        const reason = await runOneCycle(ctx, config)
        cycles += 1
        if (reason?.kind === 'error') {
          io.stderr.write(`heartbeat: cycle ${cycles} error: ${reason.error.code}: ${reason.error.message}\n`)
        } else {
          io.stderr.write(`heartbeat: cycle ${cycles} ${reason?.kind ?? 'done'}\n`)
        }
      } catch (error) {
        io.stderr.write(`heartbeat: cycle failed: ${error instanceof Error ? error.message : String(error)}\n`)
      }
      if (config.maxCycles > 0 && cycles >= config.maxCycles) break
    } else {
      io.stderr.write('heartbeat: market closed, skipping tick\n')
    }
    if (isStopped()) break
    const sleep = cancellableSleep(config.intervalMs)
    io._pending = sleep.cancel
    await sleep.promise
    io._pending = undefined
  }
  io.stderr.write(`heartbeat: stopped after ${cycles} cycles\n`)
}

export function apply(ctx, config) {
  if (!config.heartbeat) return // one-shot mode: portfolio-runner drives the single cycle
  let stopped = false
  const io = { stderr: process.stderr, _pending: undefined }
  const stop = () => {
    stopped = true
    if (io._pending) io._pending()
  }
  ctx.on?.('dispose', stop)
  process.once('SIGINT', stop)
  process.once('SIGTERM', stop)
  void loop(ctx, config, io, () => stopped).catch((error) => {
    io.stderr.write(`heartbeat: ${error instanceof Error ? error.message : String(error)}\n`)
  })
}
