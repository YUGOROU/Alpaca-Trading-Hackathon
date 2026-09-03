import { randomUUID } from 'node:crypto'
import Schema from '@deepseek-ai/schemastery'
import { installModelSelection } from '@deepseek-ai/dsh-agent'
import { createUserMessage } from '@deepseek-ai/dsh-llm'
import { SessionId } from '@deepseek-ai/dsh-session'
import { runModelNativeDecision } from './model-native-adapter.js'
import { monitorHeartbeat, recordHeartbeatEvent } from './decision-bridge.js'

export const name = 'portfolio-heartbeat'
export const inject = ['agentDefaultModel', 'agents', 'sessions']

export const Config = Schema.object({
  repositoryRoot: Schema.string().required(),
  scenario: Schema.string(),
  mock: Schema.boolean().default(false),
  live: Schema.boolean().default(false),
  ledgerPath: Schema.string().required(),
  pythonExecutable: Schema.string().default('python3'),
  heartbeat: Schema.boolean().default(false), // only loop when heartbeat mode is selected
  instruction: Schema.string().required(),
  intervalMs: Schema.number().default(300_000),
  maxCycles: Schema.number().default(0), // 0 = run until stopped
  executionMode: Schema.string().default('human'),
})

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
    try {
      const tick = await monitorHeartbeat(config)
      cycles += 1
      await recordHeartbeatEvent(config, { kind: 'tick_success' })
      if (!tick.llm_due) {
        io.stderr.write(`heartbeat: ${JSON.stringify({ event: 'tick', cycle: cycles, phase: tick.phase, llm_due: false, reasons: tick.reasons || [], option_market_observation: tick.option_market_observation || {}, outcome: 'success' })}\n`)
      } else {
        await recordHeartbeatEvent(config, { kind: 'llm_attempt' })
        const reason = await runOneCycle(ctx, { ...config, preparedContext: tick.context })
        if (reason?.kind === 'error') {
          await recordHeartbeatEvent(config, { kind: 'llm_failure', failureCode: reason.error.code })
          io.stderr.write(`heartbeat: ${JSON.stringify({ event: 'tick', cycle: cycles, phase: tick.phase, llm_due: true, reasons: tick.reasons || [], option_market_observation: tick.option_market_observation || {}, outcome: 'llm_failure', failure_code: reason.error.code })}\n`)
        } else {
          await recordHeartbeatEvent(config, { kind: 'llm_success' })
          io.stderr.write(`heartbeat: ${JSON.stringify({ event: 'tick', cycle: cycles, phase: tick.phase, llm_due: true, reasons: tick.reasons || [], option_market_observation: tick.option_market_observation || {}, outcome: 'llm_success' })}\n`)
        }
      }
      if (config.maxCycles > 0 && cycles >= config.maxCycles) break
    } catch (error) {
      const failureCode = error instanceof Error ? error.message.slice(0, 80) : 'unknown_tick_failure'
      try { await recordHeartbeatEvent(config, { kind: 'tick_failure', failureCode }) } catch { /* preserve loop even if telemetry storage is unavailable */ }
      io.stderr.write(`heartbeat: ${JSON.stringify({ event: 'tick', cycle: cycles, outcome: 'tick_failure', failure_code: failureCode })}\n`)
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
