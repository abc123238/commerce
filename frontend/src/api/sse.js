// POST + SSE 流式解析。
// 后端 /chat /analytics 是 POST 且流式返回 SSE（event: message，data: {json}），
// 浏览器 EventSource 只支持 GET，所以这里用 fetch + ReadableStream 逐行解析。
// 事件类型：step{node,detail} / answer{content,citations} / done{trace_id} / error{message}

// 超过该时长无任何数据帧即判定连接挂死：abort 并回调 onError，避免 UI 永久 loading。
const IDLE_TIMEOUT_MS = 90_000

/**
 * 消费一段 SSE 流。
 * @param {string} path         如 '/chat' 或 '/analytics'
 * @param {object} body         { question, history }
 * @param {object} handlers     { onStep, onAnswer, onDone, onError }
 * @returns {() => void}        取消函数（中止请求 + 清除定时器）
 */
export function streamSse(path, body, handlers) {
  const { onStep, onAnswer, onDone, onError } = handlers
  const controller = new AbortController()
  let receivedDone = false
  let idleTimer = null

  function clearIdle() {
    if (idleTimer) {
      clearTimeout(idleTimer)
      idleTimer = null
    }
  }

  function armIdle() {
    clearIdle()
    idleTimer = setTimeout(() => {
      controller.abort()
      onError?.('连接超时，请重试')
    }, IDLE_TIMEOUT_MS)
  }

  ;(async () => {
    let res
    try {
      res = await fetch('/api/v1' + path, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'same-origin', // 会话凭据经 HttpOnly cookie 自动携带，不再手动拼 token
        body: JSON.stringify(body),
        signal: controller.signal,
      })
    } catch (err) {
      if (err.name !== 'AbortError') onError?.('网络异常，请检查后端服务')
      return
    }

    if (!res.ok || !res.body) {
      let msg = `请求失败（${res.status}）`
      try {
        const data = await res.json()
        if (data.detail) msg = typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail)
      } catch {
        /* 默认文案 */
      }
      onError?.(msg)
      return
    }

    const reader = res.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''
    try {
      for (;;) {
        armIdle()
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n')
        buffer = lines.pop() // 最后一段可能不完整，留到下一个 chunk 再拼
        for (const line of lines) {
          const trimmed = line.trim()
          if (!trimmed.startsWith('data:')) continue
          let payload
          try {
            payload = JSON.parse(trimmed.slice(5).trim())
          } catch {
            continue
          }
          switch (payload.type) {
            case 'step':
              onStep?.(payload)
              break
            case 'answer':
              onAnswer?.(payload)
              break
            case 'done':
              receivedDone = true
              onDone?.(payload)
              break
            case 'error':
              receivedDone = true
              onError?.(payload.message)
              break
          }
        }
      }
      // 流正常关闭但从未收到 done/error：后端异常断开，通知前端复位 running 状态
      if (!receivedDone) onError?.('连接已中断，请重试')
    } catch (err) {
      if (err.name !== 'AbortError') onError?.(String(err?.message || err))
    } finally {
      clearIdle()
      reader.releaseLock()
    }
  })()

  return () => {
    clearIdle()
    controller.abort()
  }
}
