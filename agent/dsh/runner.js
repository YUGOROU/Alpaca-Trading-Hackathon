import { randomUUID } from 'node:crypto'
import Schema from '@deepseek-ai/schemastery'
import { installModelSelection } from '@deepseek-ai/dsh-agent'
import { createUserMessage } from '@deepseek-ai/dsh-llm'
import { SessionId } from '@deepseek-ai/dsh-session'

export const name = 'portfolio-runner'
export const inject = ['agentDefaultModel', 'agents', 'sessions']

export const Config = Schema.object({
  scenario: Schema.string(),
  instruction: Schema.string().required(),
  heartbeat: Schema.boolean().default(false), // when true, portfolio-heartbeat owns the loop
})

function outcome(events, firstSeq) {
  let reason
  for (const event of events) {
    if (event.seq >= firstSeq && event.type === 'turn/end') reason = event.data.reason
  }
  return reason
}

async function run(ctx, config, io) {
  await ctx.get('loader')?.await()
  const selection = ctx.agentDefaultModel.currentSelection()
  const { agent } = await ctx.agents.create({
    sessionId: SessionId(`portfolio-${config.scenario}-${randomUUID()}`),
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
  const reason = outcome(agent.session.events, firstSeq)
  if (reason?.kind === 'error') {
    io.stderr.write(`dsh: ${reason.error.code}: ${reason.error.message}\n`)
  }
  io.exit(reason?.kind === 'completed' ? 0 : 1)
}

export function apply(ctx, config) {
  if (config.heartbeat) return // heartbeat mode: the loop plugin drives cycles instead
  const exit = ctx.get('appExit')
  if (exit === undefined) throw new Error('portfolio-runner requires the dsh application launcher')
  const io = { stderr: process.stderr, exit }
  void run(ctx, config, io).catch((error) => {
    io.stderr.write(`dsh: ${error instanceof Error ? error.message : String(error)}\n`)
    io.exit(1)
  })
}
