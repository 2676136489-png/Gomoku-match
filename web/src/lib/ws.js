import { getToken } from './api'

/**
 * WebSocket 薄封装。
 *
 * 浏览器的 WebSocket 不能自定义请求头，token 只能挂在 query 上。这一点没有
 * 绕过去的办法——用 Sec-WebSocket-Protocol 塞 token 也行，但服务端和中间代理
 * 都得配合，收益是「token 不出现在 URL 里」，代价是链路里多一层约定。
 *
 * 重连策略：指数退避，上限 8 秒。4401/4403/4404 是服务端主动拒绝
 * （未登录 / 不在局中 / 对局不存在），这类错误重连多少次都一样，直接放弃。
 */
export function openSocket(path, { onEvent, onStatus, autoReconnect = true } = {}) {
  let ws = null
  let closed = false
  let tries = 0
  let retryTimer = null
  let pingTimer = null

  const url = () => {
    const proto = window.location.protocol === 'https:' ? 'wss' : 'ws'
    return `${proto}://${window.location.host}${path}?token=${encodeURIComponent(getToken())}`
  }

  const stopPing = () => {
    if (pingTimer) clearInterval(pingTimer)
    pingTimer = null
  }

  const connect = () => {
    if (closed) return
    ws = new WebSocket(url())

    ws.onopen = () => {
      tries = 0
      onStatus?.('open')
      // 反向代理通常 60 秒断空闲连接，25 秒一次心跳留足余量
      stopPing()
      pingTimer = setInterval(() => send({ type: 'ping' }), 25000)
    }

    ws.onmessage = (event) => {
      try {
        onEvent(JSON.parse(event.data))
      } catch {
        /* 坏帧跳过，不要因为一条消息炸掉整局 */
      }
    }

    ws.onclose = (event) => {
      stopPing()
      onStatus?.('closed', event.code)
      if (closed || !autoReconnect) return
      if (event.code === 4401 || event.code === 4403 || event.code === 4404) return
      const delay = Math.min(8000, 400 * 2 ** tries)
      tries += 1
      retryTimer = setTimeout(connect, delay)
    }

    ws.onerror = () => {
      /* onclose 一定会跟着来，这里不重复处理 */
    }
  }

  const send = (payload) => {
    if (!ws || ws.readyState !== 1) return false
    ws.send(JSON.stringify(payload))
    return true
  }

  connect()

  return {
    send,
    get connected() {
      return ws?.readyState === 1
    },
    close() {
      closed = true
      stopPing()
      if (retryTimer) clearTimeout(retryTimer)
      try {
        ws?.close()
      } catch {
        /* 已经关了 */
      }
    },
  }
}
