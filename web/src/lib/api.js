const TOKEN_KEY = 'gomoku.token'

let token = ''
try {
  token = localStorage.getItem(TOKEN_KEY) || ''
} catch {
  // 隐私模式下 localStorage 会抛异常，降级成内存态：能玩，关掉页面就要重登
  token = ''
}

let unauthorizedHandler = null

export function getToken() {
  return token
}

export function setToken(next) {
  token = next || ''
  try {
    if (next) localStorage.setItem(TOKEN_KEY, next)
    else localStorage.removeItem(TOKEN_KEY)
  } catch {
    /* 存不进去就只留内存态 */
  }
}

export function onUnauthorized(handler) {
  unauthorizedHandler = handler
}

async function readError(res) {
  let message = `请求失败 (${res.status})`
  try {
    const body = await res.json()
    if (body?.detail) message = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail)
  } catch {
    /* 不是 JSON 就保留默认文案 */
  }
  return message
}

async function request(path, options = {}) {
  const headers = { ...(options.headers || {}) }
  if (token) headers.Authorization = `Bearer ${token}`
  const res = await fetch(path, { ...options, headers })
  if (res.status === 401) {
    setToken('')
    unauthorizedHandler?.()
  }
  if (!res.ok) throw new Error(await readError(res))
  return res.json()
}

const jsonPost = (path, payload) =>
  request(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: payload === undefined ? undefined : JSON.stringify(payload),
  })

/**
 * 用 fetch 读 SSE。
 *
 * EventSource 只支持 GET，而实验室要把整段源码放进请求体，URL 装不下。
 * 所以自己按 `data: {...}\n\n` 切帧——协议本身简单到不值得为它引库。
 */
async function stream(path, payload, onEvent, signal) {
  const res = await fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
    body: JSON.stringify(payload),
    signal,
  })
  if (!res.ok) throw new Error(await readError(res))
  if (!res.body) throw new Error('浏览器不支持流式响应')

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    let cut = buffer.indexOf('\n\n')
    while (cut >= 0) {
      const frame = buffer.slice(0, cut)
      buffer = buffer.slice(cut + 2)
      for (const line of frame.split('\n')) {
        if (!line.startsWith('data: ')) continue
        try {
          onEvent(JSON.parse(line.slice(6)))
        } catch {
          /* 坏帧直接跳过，不要让一帧拖崩整局 */
        }
      }
      cut = buffer.indexOf('\n\n')
    }
  }
}

export const api = {
  // 账户
  register: (name, password) => jsonPost('/api/auth/register', { name, password }),
  login: (name, password) => jsonPost('/api/auth/login', { name, password }),
  logout: () => jsonPost('/api/auth/logout'),
  me: () => request('/api/auth/me'),
  changePassword: (oldPassword, newPassword) =>
    jsonPost('/api/auth/password', { old: oldPassword, new: newPassword }),
  leaderboard: () => request('/api/leaderboard'),

  // 元信息
  config: () => request('/api/config'),
  health: () => request('/api/health'),
  tutorial: () => request('/api/tutorial'),
  algorithms: () => request('/api/algorithms'),

  // 对局
  newGame: (settings) => jsonPost('/api/games', settings),
  move: (id, x, y) => jsonPost(`/api/games/${id}/move`, { x, y }),
  hint: (id) => jsonPost(`/api/games/${id}/hint`),
  undo: (id) => jsonPost(`/api/games/${id}/undo`),
  review: (id) => jsonPost(`/api/games/${id}/review`),
  tactics: (id) => request(`/api/games/${id}/tactics`),

  // 分析与讲解
  insight: (id) => request(`/api/games/${id}/insight`),
  explain: (id, index) => jsonPost(`/api/games/${id}/explain`, index === undefined ? {} : { index }),
  pending: (id) => request(`/api/games/${id}/pending`),
  resume: (id) => jsonPost(`/api/games/${id}/resume`),

  // 实验室
  labSamples: () => request('/api/lab/samples'),
  labVerify: (payload) => jsonPost('/api/lab/verify', payload),
  labMatch: (payload, onEvent, signal) => stream('/api/lab/match', payload, onEvent, signal),

  // 竞技场
  arenaMatch: (payload, onEvent, signal) => stream('/api/arena/match', payload, onEvent, signal),

  // 落点网络的可视化扫描
  neuralScan: (payload) => jsonPost('/api/neural/scan', payload),

  // 在线对战
  rooms: () => request('/api/rooms'),
  createRoom: (payload) => jsonPost('/api/rooms', payload),
  peekRoom: (code) => request(`/api/rooms/${encodeURIComponent(code)}`),
  joinRoom: (code) => jsonPost(`/api/rooms/${encodeURIComponent(code)}/join`),
  closeRoom: (id) => request(`/api/rooms/${id}`, { method: 'DELETE' }),
  onlineStats: () => request('/api/online/stats'),

  // 棋谱与主页
  history: (limit = 20, offset = 0) => request(`/api/history?limit=${limit}&offset=${offset}`),
  profile: (name) => request(`/api/profile/${encodeURIComponent(name)}`),
  replay: (id) => request(`/api/games/${id}`),
}
