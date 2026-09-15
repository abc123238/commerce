import { createRouter, createWebHashHistory } from 'vue-router'
import { probeAuth, readUser } from '../api/client'

// 用 hash 模式：刷新/直达任意路由都不需要服务端 SPA fallback，FastAPI 静态托管零配置
const routes = [
  { path: '/login', name: 'login', component: () => import('../views/Login.vue') },
  { path: '/', name: 'products', component: () => import('../views/Products.vue'), meta: { requiresAuth: true } },
  { path: '/cart', name: 'cart', component: () => import('../views/Cart.vue'), meta: { requiresAuth: true } },
  { path: '/orders', name: 'orders', component: () => import('../views/Orders.vue'), meta: { requiresAuth: true } },
  { path: '/chat', name: 'chat', component: () => import('../views/Chat.vue'), meta: { requiresAuth: true } },
  { path: '/analytics', name: 'analytics', component: () => import('../views/Analytics.vue'), meta: { requiresAuth: true } },
]

const router = createRouter({ history: createWebHashHistory(), routes })

// token 在 HttpOnly cookie 中，JS 不可读；守卫改为「本地有 user 则放行，否则静默探测 /auth/me」。
// 探测成功会校验 cookie 有效性并缓存 user；失败（含过期）跳登录。
router.beforeEach(async (to) => {
  const isAuthed = () => (readUser() ? Promise.resolve(true) : probeAuth().then((u) => !!u))

  if (to.meta.requiresAuth) {
    const authed = await isAuthed()
    return authed ? true : { name: 'login' }
  }
  if (to.name === 'login') {
    const authed = await isAuthed()
    return authed ? { name: 'products' } : true
  }
  return true
})

export default router
