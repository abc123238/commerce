// 前端 SSE 解析逻辑 × 真实后端 的端到端验证（Node 环境）：
// 复用 frontend/src/api/sse.js 的 streamSse，连真实 Docker 后端，断言事件流完整。
// 说明：Node 里没有 localStorage / 相对路径 fetch，这里做最小 shim。
import { streamSse } from '../frontend/src/api/sse.js'

// 注意：sse.js 内部已把请求拼成 '/api/v1' + path，这里 BASE 只要协议+主机
const BASE = 'http://127.0.0.1:8000'
const EMAIL = process.env.VERIFY_EMAIL || 'verify@example.com'
const PASSWORD = process.env.VERIFY_PASSWORD || 'Verify123!@#'

// ---- shim 浏览器全局 ----
const realFetch = globalThis.fetch
globalThis.localStorage = {
  getItem: (k) => (k === 'access_token' ? globalThis.__token : null),
}

// 1. 登录拿真实 token
const loginRes = await realFetch(BASE + '/api/v1/auth/login', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ email: EMAIL, password: PASSWORD }),
})
if (!loginRes.ok) {
  console.error('登录失败', loginRes.status)
  process.exit(1)
}
globalThis.__token = (await loginRes.json()).access_token
console.log('token 长度:', globalThis.__token.length)

// 2. mock fetch：把前端相对路径 /api/v1 解析到真实后端
globalThis.fetch = (url, opts) => realFetch(BASE + url, opts)

// 3. 跑真实客服对话
const collected = { steps: [], answers: [], dones: [], errors: [] }
const cancel = streamSse(
  '/chat',
  { question: '我的订单到哪里了？', history: [] },
  {
    onStep: (s) => collected.steps.push(s),
    onAnswer: (a) => collected.answers.push(a),
    onDone: (d) => collected.dones.push(d),
    onError: (e) => collected.errors.push(e),
  },
)

await new Promise((r) => setTimeout(r, 20000))
cancel()

const ok =
  collected.steps.length >= 2 &&
  collected.answers.length === 1 &&
  collected.dones.length === 1 &&
  collected.errors.length === 0 &&
  !!collected.dones[0].trace_id

console.log('步骤数:', collected.steps.length, '->', collected.steps.map((s) => s.node).join(' > '))
console.log('answer:', JSON.stringify(collected.answers[0]?.content || '').slice(0, 80))
console.log('done.trace_id:', collected.dones[0]?.trace_id)
console.log('errors:', collected.errors.length)
console.log(ok ? 'PASS: SSE 解析 × 真实后端 兼容' : 'FAIL')
process.exit(ok ? 0 : 1)
