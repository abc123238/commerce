// 前端 API 封装层（frontend/src/api/*.js） × 真实后端 的端到端验证（Node 环境）。
// 覆盖：登录 → 个人信息 → 商品列表 → 加购 → 下单 → 支付 → 订单列表，
// 验证前端字段映射与后端 schema 完全一致（页面不白屏的最后一道兜底）。
import {
  login,
  register,
  me,
} from '../frontend/src/api/auth.js'
import { listProducts } from '../frontend/src/api/products.js'
import { addToCart, getCart } from '../frontend/src/api/cart.js'
import { createOrder, listOrders, payOrder } from '../frontend/src/api/orders.js'
import { setToken, getToken } from '../frontend/src/api/client.js'

const BASE = 'http://127.0.0.1:8000'
const EMAIL = process.env.VERIFY_EMAIL || 'verify@example.com'
const PASSWORD = process.env.VERIFY_PASSWORD || 'Verify123!@#'

// ---- 浏览器全局 shim ----
const realFetch = globalThis.fetch
globalThis.localStorage = {
  getItem: (k) => (k === 'access_token' ? globalThis.__t : null),
  setItem: (k, v) => { if (k === 'access_token') globalThis.__t = v },
  removeItem: (k) => { if (k === 'access_token') globalThis.__t = null },
}
globalThis.location = { hash: '' }
// client.js 用相对路径 '/api/v1'，Node 里补上协议+主机
globalThis.fetch = (url, opts) => realFetch(BASE + url, opts)

let ok = true
function check(name, cond, extra = '') {
  console.log(`${cond ? 'PASS' : 'FAIL'}  ${name}${extra ? '  -> ' + extra : ''}`)
  if (!cond) ok = false
}

// 1. 注册（已存在则忽略）
try {
  await register(EMAIL, PASSWORD)
} catch (e) { /* 已注册会 409，忽略 */ }

// 2. 登录 + 个人信息
const auth = await login(EMAIL, PASSWORD)
check('登录返回 access_token', typeof auth.access_token === 'string' && auth.access_token.length > 0)
setToken(auth.access_token)
check('token 已写入 localStorage', getToken() === auth.access_token)

const user = await me()
check('me() 返回用户信息', !!user.id && !!user.email, `${user.role}@${user.email}`)

// 3. 商品列表
const { items, total } = await listProducts({ page: 1, size: 5 })
check('商品列表字段', Array.isArray(items) && typeof total === 'number' && items.length > 0, `共 ${total} 件`)
const pid = items[0].id
check('商品字段', !!items[0].name && items[0].price !== undefined, `商品#${pid} ${items[0].name}`)

// 4. 加购 + 查购物车
const cart = await addToCart(pid, 1)
check('加购返回购物车', Array.isArray(cart.items) && Number(cart.total) > 0, `合计 ¥${cart.total}`)
const cartAgain = await getCart()
check('购物车字段', cartAgain.items[0]?.product_id === pid)

// 5. 下单 + 支付 + 列表
const order = await createOrder()
check('下单返回 order_no', !!order.order_no, order.order_no)
const paid = await payOrder(order.order_no)
check('支付后状态为 paid', paid.status === 'paid', paid.status)
const orders = await listOrders()
check('订单列表含刚下的单', orders.items.some((o) => o.order_no === order.order_no), `共 ${orders.total} 单`)

console.log(ok ? '\n=== 前端 API 层 × 后端 全部通过 ===' : '\n=== 存在失败项 ===')
process.exit(ok ? 0 : 1)
