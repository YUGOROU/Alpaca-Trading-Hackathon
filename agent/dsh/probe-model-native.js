import { runDirectToolProbe } from './model-native-adapter.js'

const modelId = process.argv[2]
if (!modelId) throw new Error('usage: node probe-model-native.js <model-id>')

const result = await runDirectToolProbe({ modelId })
process.stdout.write(`${JSON.stringify(result)}\n`)
process.exitCode = result.status === 'completed' ? 0 : 1
