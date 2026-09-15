// 统一 fetch 封装：会话凭据经 HttpOnly Cookie 自动携带，前端不再把 JWT 存进 localStorage
// （避免 XSS 直接窃取 token）。401 时清空本地用户信息并跳登录。
const BASE = '/api/v1'
const USER_KEY = 'user'

export function readUser() {
  try {
    const raw = localStorage.getItem(USER_KEY)
    return raw ? JSON.parse(raw) : null
  } catch {
    return null
  }
}

export function saveUser(user) {
  localStorage.setItem(USER_KEY, JSON.stringify(user))
}

export function clearUser() {
  localStorage.removeItem(USER_KEY)
}

async function request(method, path, body) {
  const headers = { 'Content-Type': 'application/json' }
  let res
  try {
    res = await fetch(BASE + path, {
      method,
      headers,
      credentials: 'same-origin',
      body: body !== undefined ? JSON.stringify(body) : undefined,
    })
  } catch {
    throw new Error('网络异常，请检查后端服务是否启动')
  }

  if (res.status === 401) {
    clearUser()
    location.hash = '#/login'
    throw new Error('登录已过期，请重新登录')
  }

  if (!res.ok) {
    let msg = `请求失败（${res.status}）`
    try {
      const data = await res.json()
      const detail = data.detail
      if (typeof detail === 'string') msg = detail
      else if (Array.isArray(detail) && detail.length) msg = detail[0].msg || msg
    } catch {
      /* 非 JSON 错误体，用默认文案 */
    }
    throw new Error(msg)
  }

  return res.json()
}

/**
 * 校验当前会话：cookie 有效则返回并缓存 user，否则清空本地用户并返回 null。
 * 不走 request() 的 401 自动跳转，供路由守卫在跳转前静默探测。
 */
export async function probeAuth() {
  try {
    const res = await fetch(BASE + '/auth/me', { credentials: 'same-origin' })
    if (!res.ok) {
      clearUser()
      return null
    }
    const user = await res.json()
    saveUser(user)
    return user
  } catch {
    clearUser()
    return null
  }
}

export async function logout() {
  try {
    await fetch(BASE + '/auth/logout', { method: 'POST', credentials: 'same-origin' })
  } catch {
    /* 网络异常也不阻塞本地登出 */
  }
  clearUser()
}

export const api = {
  get: (path) => request('GET', path),
  post: (path, body) => request('POST', path, body),
  patch: (path, body) => request('PATCH', path, body),
  del: (path) => request('DELETE', path),
}
