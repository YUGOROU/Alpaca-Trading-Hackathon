import assert from 'node:assert/strict'
import test from 'node:test'

import { modelProfile, normalizeToolCall, runDirectToolProbe, runModelNativeDecision } from '../model-native-adapter.js'

test('approved candidates resolve to exactly three native adapter families', () => {
  assert.equal(modelProfile('deepseek-ai/DeepSeek-V4-Pro-0813:baseten').family, 'deepseek-v4')
  assert.equal(modelProfile('deepseek-ai/DeepSeek-V4-Flash-0731:baseten').family, 'deepseek-v4')
  assert.equal(modelProfile('moonshotai/Kimi-K3:baseten').family, 'kimi-k3')
  assert.equal(modelProfile('zai-org/GLM-5.3:baseten').family, 'glm-5.3')
  assert.equal(modelProfile('zai-org/GLM-5.3-Flash:baseten').family, 'glm-5.3')
  assert.throws(() => modelProfile('other/model:baseten'), /no approved profile/)
})

test('GLM XML compatibility is normalized without prompt examples', () => {
  const result = normalizeToolCall(modelProfile('zai-org/GLM-5.3:baseten'), {
    content: '<tool_call>{"id":"glm-1","name":"submit_decision","arguments":{"context_id":"ctx","candidate_id":"hold","reason":"bounded"}}</tool_call>',
  }, 'submit_decision')
  assert.equal(result.ok, true)
  assert.deepEqual(result.call.arguments, { context_id: 'ctx', candidate_id: 'hold', reason: 'bounded' })
})

test('DeepSeek DSML compatibility is converted to the canonical call', () => {
  const result = normalizeToolCall(modelProfile('deepseek-ai/DeepSeek-V4-Pro-0813:baseten'), {
    content: '<｜tool▁call▁begin｜>function<｜tool▁sep｜>get_decision_context\n{}<｜tool▁call▁end｜>',
  }, 'get_decision_context')
  assert.equal(result.ok, true)
  assert.equal(result.call.name, 'get_decision_context')
})

test('provider authentication failures are not reported as timeouts', async () => {
  const result = await runModelNativeDecision({ instruction: 'Select one.' }, {
    token: 'test-token', modelId: 'moonshotai/Kimi-K3:baseten',
    fetchImpl: async () => ({ ok: false, status: 401 }),
  })
  assert.equal(result.failure, 'provider_auth_error')
  assert.equal(result.metadata.http_status, 401)
})

test('provider rate limits are kept distinct from timeouts', async () => {
  const result = await runModelNativeDecision({ instruction: 'Select one.' }, {
    token: 'test-token', modelId: 'moonshotai/Kimi-K3:baseten',
    fetchImpl: async () => ({ ok: false, status: 429 }),
  })
  assert.equal(result.failure, 'provider_rate_limited')
  assert.equal(result.metadata.http_status, 429)
})

test('provider request is aborted at the configured deadline', async () => {
  let signal
  const result = await runDirectToolProbe({
    token: 'test-token', modelId: 'moonshotai/Kimi-K3:baseten', providerTimeoutMs: 1,
    fetchImpl: async (_url, options) => {
      signal = options.signal
      return new Promise((_resolve, reject) => signal.addEventListener('abort', () => reject(new Error('aborted'))))
    },
  })
  assert.equal(signal.aborted, true)
  assert.equal(result.status, 'failed')
  assert.equal(result.failure, 'provider_timeout')
})

test('provider deadline remains active while reading the response body', async () => {
  let signal
  const result = await runDirectToolProbe({
    token: 'test-token', modelId: 'moonshotai/Kimi-K3:baseten', providerTimeoutMs: 1,
    fetchImpl: async (_url, options) => {
      signal = options.signal
      return {
        ok: true,
        json: async () => new Promise((_resolve, reject) => signal.addEventListener('abort', () => reject(new Error('aborted')))),
      }
    },
  })
  assert.equal(signal.aborted, true)
  assert.equal(result.failure, 'provider_timeout')
})

test('direct qualification probe records only forced-tool metadata', async () => {
  const result = await runDirectToolProbe({
    token: 'test-token', modelId: 'moonshotai/Kimi-K3:baseten',
    fetchImpl: async () => ({ ok: true, json: async () => ({ choices: [{
      finish_reason: 'tool_calls',
      message: { tool_calls: [{ id: 'probe-1', function: { name: 'get_decision_context', arguments: '{}' } }] },
    }] }) }),
  })
  assert.equal(result.status, 'completed')
  assert.equal(result.protocol[0].tool_name, 'get_decision_context')
  assert.equal(result.protocol[0].schema_valid, true)
})

test('native adapter forces the two canonical calls and preserves Kimi reasoning history', async () => {
  const requests = []
  const fetchImpl = async (_url, options) => {
    const request = JSON.parse(options.body)
    requests.push(request)
    const isContext = request.tool_choice.function.name === 'get_decision_context'
    return {
      ok: true,
      json: async () => ({ choices: [{
        finish_reason: 'tool_calls',
        message: isContext
          ? { role: 'assistant', content: null, reasoning_content: 'not persisted', tool_calls: [{ id: 'context-1', type: 'function', function: { name: 'get_decision_context', arguments: '{}' } }] }
          : { role: 'assistant', content: null, tool_calls: [{ id: 'submit-1', type: 'function', function: { name: 'submit_decision', arguments: JSON.stringify({ context_id: 'ctx', candidate_id: 'hold', reason: 'bounded reason' }) } }] },
      }] }),
    }
  }
  const result = await runModelNativeDecision({
    repositoryRoot: process.cwd(), scenario: 'calm', live: false, ledgerPath: '/tmp/unused',
    instruction: 'Select one.', pythonExecutable: 'python3',
  }, {
    fetchImpl,
    token: 'test-token',
    modelId: 'moonshotai/Kimi-K3:baseten',
    getContext: async () => ({ context_id: 'ctx', candidates: [{ candidate_id: 'hold' }] }),
    submit: async (_config, args) => ({ gate: { status: 'approved_for_dry_run' }, decision: args }),
    now: (() => { let time = 0; return () => ++time })(),
  })
  assert.equal(result.status, 'completed')
  assert.equal(requests.length, 2)
  assert.equal(requests[0].stream, false)
  assert.equal(requests[0].tool_choice.function.name, 'get_decision_context')
  assert.equal(requests[1].tool_choice.function.name, 'submit_decision')
  assert.equal(requests[1].messages[2].reasoning_content, 'not persisted')
})

test('autonomous execution is invoked only for an approved non-empty overlay proposal', async () => {
  let executions = 0
  const fetchImpl = async (_url, options) => {
    const isContext = JSON.parse(options.body).tool_choice.function.name === 'get_decision_context'
    return { ok: true, json: async () => ({ choices: [{
      finish_reason: 'tool_calls',
      message: isContext
        ? { tool_calls: [{ id: 'context-1', function: { name: 'get_decision_context', arguments: '{}' } }] }
        : { tool_calls: [{ id: 'submit-1', function: { name: 'submit_decision', arguments: JSON.stringify({ context_id: 'ctx', candidate_id: 'full_hedge', reason: 'bounded' }) } }] },
    }] }) }
  }
  const result = await runModelNativeDecision({ instruction: 'Select one.', executionMode: 'autonomous-paper' }, {
    fetchImpl, token: 'test-token', modelId: 'zai-org/GLM-5.3:baseten',
    getContext: async () => ({ context_id: 'ctx', candidates: [{ candidate_id: 'full_hedge' }] }),
    submit: async () => ({ decision_id: 'dsh-1', gate: { status: 'approved_for_dry_run', orders: [{ structure: 'protective_put' }] } }),
    executeAutonomous: async () => {
      executions += 1
      return { broker_event: { execution: { state: 'accepted' } } }
    },
  })
  assert.equal(result.status, 'completed')
  assert.equal(executions, 1)
})

test('native GLM tool markup is rebuilt as a canonical assistant tool call', async () => {
  const requests = []
  const fetchImpl = async (_url, options) => {
    const request = JSON.parse(options.body)
    requests.push(request)
    const isContext = request.tool_choice.function.name === 'get_decision_context'
    return {
      ok: true,
      json: async () => ({ choices: [{
        finish_reason: 'tool_calls',
        message: isContext
          ? { role: 'assistant', content: '<tool_call>{"id":"ctx-1","name":"get_decision_context","arguments":{}}</tool_call>' }
          : { role: 'assistant', content: null, tool_calls: [{ id: 'submit-1', function: { name: 'submit_decision', arguments: JSON.stringify({ context_id: 'ctx', candidate_id: 'hold', reason: 'bounded reason' }) } }] },
      }] }),
    }
  }
  const result = await runModelNativeDecision({ instruction: 'Select one.' }, {
    fetchImpl, token: 'test-token', modelId: 'zai-org/GLM-5.3:baseten',
    getContext: async () => ({ context_id: 'ctx', candidates: [{ candidate_id: 'hold' }] }),
    submit: async () => ({ gate: { status: 'approved_for_dry_run' } }),
  })
  assert.equal(result.status, 'completed')
  assert.deepEqual(requests[1].messages[2].tool_calls, [{
    id: 'ctx-1', type: 'function', function: { name: 'get_decision_context', arguments: '{}' },
  }])
})
