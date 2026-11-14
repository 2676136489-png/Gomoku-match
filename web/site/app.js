/**
 * Gomoku Match — 内页共享运行时（原生 JS，无框架）。
 *
 * 四块内容：
 *   1. API 客户端：token 三通道（Authorization 头 / cookie / 查询串），
 *      云端网关误判时自动 base64 重发，SSE 用 fetch 流手写分帧。
 *   2. 页面骨架：顶栏 + 移动菜单，登录守卫（401 跳 login.html）。
 *   3. 棋盘：15×15 canvas，暗色主题，落点预测 / 网络热力 / 禁手 /
 *      待落子 / 焦点 / 胜利连线全部在这一个组件里。
 *   4. 通用工具：DOM 构建器、时间格式化、提示条。
 *
 * 每个内页引这一个文件再加自己的页面脚本，页面脚本用 window.Gomoku。
 */

/* ============================================================ API 客户端 */

const TOKEN_KEY = 'gomoku.token'

let token = ''
try {
  token = localStorage.getItem(TOKEN_KEY) || ''
} catch {
  // 隐私模式读不了 localStorage：降级成内存态，能玩，关页面就掉登录
  token = ''
}

function syncCookie(value) {
  try {
    if (value) {
      document.cookie = `gomoku_token=${encodeURIComponent(value)}; path=/; max-age=2592000; SameSite=Lax`
    } else {
      document.cookie = 'gomoku_token=; path=/; max-age=0; SameSite=Lax'
    }
  } catch {
    /* cookie 不可用就只走请求头 */
  }
}

syncCookie(token)

function setToken(next) {
  token = next || ''
  try {
    if (next) localStorage.setItem(TOKEN_KEY, next)
    else localStorage.removeItem(TOKEN_KEY)
  } catch {
    /* 存不进去就只留内存态 */
  }
  syncCookie(token)
}

/**
 * 把 token 拼进查询串。云端的分享网关只放行它认识的请求头，
 * Authorization 会被转发时丢掉；凭据挂在查询串上的请求都穿得过去。
 */
function withToken(path, extra) {
  const params = []
  // cookie 能稳定穿过分享网关时，不再把 token 拼进查询串。URL 会进浏览器历史
  // 和代理日志，能少一份就少一份；只有 cookie 缺失时才回退到 query。
  const hasCookie = document.cookie
    .split(';')
    .some((part) => part.trim().startsWith('gomoku_token='))
  if (token && !hasCookie) params.push(`token=${encodeURIComponent(token)}`)
  if (extra) params.push(extra)
  if (!params.length) return path
  const sep = path.includes('?') ? '&' : '?'
  return `${path}${sep}${params.join('&')}`
}

async function readError(res) {
  const type = res.headers.get('content-type') || ''
  let message = `请求失败 (${res.status})`
  if (!type.includes('json')) {
    // 云端网关会用自己的 HTML 页顶掉应用的响应
    const html = await res.text().catch(() => '')
    if (html.includes('WAF拦截页面')) return '请求被安全策略拦截，请稍后重试'
    if (html.includes('链接不完整')) return '分享链接无效，请使用完整链接'
    return message
  }
  try {
    const body = await res.json()
    if (body && body.detail) {
      message = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail)
    }
  } catch {
    /* 不是 JSON 就保留默认文案 */
  }
  return message
}

/** 请求体整体 base64：代码工坊的布尔表达式源码会被 WAF 误判，兜一条软通道 */
function encodeBody(text) {
  const bytes = new TextEncoder().encode(text)
  let binary = ''
  for (let i = 0; i < bytes.length; i += 0x8000) {
    binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000))
  }
  return btoa(binary)
}

function gatewayBlocked(res) {
  if (res.status !== 403) return false
  return !(res.headers.get('content-type') || '').includes('json')
}

async function postJSON(path, text, { signal } = {}) {
  const headers = { 'Content-Type': 'application/json' }
  if (token) headers.Authorization = `Bearer ${token}`

  const plain = await fetch(withToken(path), { method: 'POST', headers, body: text, signal })
  if (!gatewayBlocked(plain) || typeof text !== 'string') return plain

  const encoded = await fetch(withToken(path, '_enc=b64'), {
    method: 'POST',
    headers,
    body: encodeBody(text),
    signal,
  })
  return encoded
}

async function request(path, options = {}) {
  const headers = { ...(options.headers || {}) }
  if (token) headers.Authorization = `Bearer ${token}`
  const res = await fetch(withToken(path), { ...options, headers })
  if (res.status === 401) {
    setToken('')
    const err = new Error(await readError(res))
    err.unauthorized = true
    throw err
  }
  if (!res.ok) throw new Error(await readError(res))
  return res.json()
}

async function jsonPost(path, payload) {
  const text = payload === undefined ? undefined : JSON.stringify(payload)
  const res = await postJSON(path, text)
  if (res.status === 401) {
    setToken('')
    const err = new Error(await readError(res))
    err.unauthorized = true
    throw err
  }
  if (!res.ok) throw new Error(await readError(res))
  return res.json()
}

/** 用 fetch 读 SSE：按 `data: {...}\n\n` 手动分帧， EventSource 只支持 GET */
async function stream(path, payload, onEvent, signal) {
  const res = await postJSON(path, JSON.stringify(payload), { signal })
  if (res.status === 401) {
    setToken('')
    const err = new Error(await readError(res))
    err.unauthorized = true
    throw err
  }
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
          /* 坏帧直接跳过 */
        }
      }
      cut = buffer.indexOf('\n\n')
    }
  }
}

const api = {
  register: (name, password) => jsonPost('/api/auth/register', { name, password }),
  login: (name, password) => jsonPost('/api/auth/login', { name, password }),
  logout: () => jsonPost('/api/auth/logout'),
  me: () => request('/api/auth/me'),

  config: () => request('/api/config'),
  tutorial: () => request('/api/tutorial'),
  algorithms: () => request('/api/algorithms'),

  newGame: (settings) => jsonPost('/api/games', settings),
  move: (id, x, y) => jsonPost(`/api/games/${id}/move`, { x, y }),
  hint: (id) => jsonPost(`/api/games/${id}/hint`),
  undo: (id) => jsonPost(`/api/games/${id}/undo`),
  review: (id) => jsonPost(`/api/games/${id}/review`),
  tactics: (id) => request(`/api/games/${id}/tactics`),

  insight: (id) => request(`/api/games/${id}/insight`),
  explain: (id, index) => jsonPost(`/api/games/${id}/explain`, index === undefined ? {} : { index }),
  pending: (id) => request(`/api/games/${id}/pending`),
  resume: (id) => jsonPost(`/api/games/${id}/resume`),

  labSamples: () => request('/api/lab/samples'),
  labVerify: (payload) => jsonPost('/api/lab/verify', payload),
  labMatch: (payload, onEvent, signal) => stream('/api/lab/match', payload, onEvent, signal),

  arenaMatch: (payload, onEvent, signal) => stream('/api/arena/match', payload, onEvent, signal),

  greedyStatus: () => request('/api/greedy/status'),
  greedyCorpus: (preview = 48) => request(`/api/greedy/corpus?preview=${preview}`),
  greedyCorpusItem: (id) => request(`/api/greedy/corpus/${encodeURIComponent(id)}`),
  uploadCorpus: (name, text) => jsonPost('/api/greedy/corpus', { name, text }),
  deleteCorpus: (id) => request(`/api/greedy/corpus/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  trainGreedy: (onEvent, signal) => stream('/api/greedy/train', {}, onEvent, signal),
  resetGreedy: () => jsonPost('/api/greedy/reset'),

  neuralScan: (payload) => jsonPost('/api/neural/scan', payload),

  history: (limit = 20, offset = 0) => request(`/api/history?limit=${limit}&offset=${offset}`),
  replay: (id) => request(`/api/games/${id}`),
}

/* ============================================================ DOM 工具 */

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag)
  for (const [key, value] of Object.entries(attrs)) {
    if (value == null || value === false) continue
    if (key === 'class' || key === 'className') node.className = value
    else if (key === 'dataset') Object.assign(node.dataset, value)
    else if (key.startsWith('on') && typeof value === 'function') {
      node.addEventListener(key.slice(2).toLowerCase(), value)
    } else if (value === true) node.setAttribute(key, '')
    else node.setAttribute(key, String(value))
  }
  for (const child of children.flat(Infinity)) {
    if (child == null || child === false) continue
    node.append(child.nodeType ? child : document.createTextNode(String(child)))
  }
  return node
}

const $ = (sel, root = document) => root.querySelector(sel)
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel))

function fmtTime(seconds) {
  if (!seconds) return ''
  const d = new Date(seconds * 1000)
  const pad = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

function fmtShort(seconds) {
  if (!seconds) return ''
  const d = new Date(seconds * 1000)
  const pad = (n) => String(n).padStart(2, '0')
  return `${d.getMonth() + 1}/${d.getDate()} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

const coordName = (x, y) => `${String.fromCharCode(65 + x)}${15 - y}`

function spinner(label) {
  return el('div', { class: 'boot' }, el('span', { class: 'spinner' }), el('p', {}, label || '加载中…'))
}

/* ============================================================ 棋盘组件 */

const SIZE = 15
const PAD_RATIO = 0.055

// 经典棋盘：榧木底、墨线、黑白子。棋盘必须是亮的暖木色——深色底上黑子
// 直接糊进背景，只有木色才能让黑白两边都立得住。功能标记跟着底走：
// 网格/星位/预测/焦点用墨褐，禁手与连珠线用朱红（唯一允许的强调色）。
const BC = {
  boardTop: '#e9c07c',
  boardMid: '#dcac5f',
  boardLow: '#c89442',
  rim: 'rgba(96, 58, 16, 0.32)',
  grid: 'rgba(74, 44, 12, 0.55)',
  gridStrong: 'rgba(56, 32, 8, 0.8)',
  star: 'rgba(56, 32, 8, 0.82)',
  hint: 'rgba(62, 36, 8, 0.55)',
  ban: 'rgba(176, 46, 28, 0.9)',
  banSoft: 'rgba(176, 46, 28, 0.34)',
  focus: 'rgba(44, 26, 4, 0.88)',
  win: '#c2382a',
}

class GomokuBoard {
  /**
   * @param {HTMLElement} container
   * @param {{ onPlace?: (x:number, y:number) => void, maxSize?: number }} opts
   */
  constructor(container, opts = {}) {
    this.opts = opts
    this.size = 560
    this.hover = null
    this.state = {
      board: new Array(225).fill(0),
      lastMove: null,
      winLine: [],
      humanColor: 1,
      interactive: false,
      hintPoint: null,
      banned: [],
      heat: null,
      predict: [],
      ghost: null,
      focus: null,
    }

    this.wrap = el('div', { class: 'board-wrap' })
    this.canvas = el('canvas', { class: 'board-canvas' })
    this.wrap.append(this.canvas)
    container.append(this.wrap)

    this.canvas.addEventListener('mousemove', (e) => {
      this.hover = this.state.interactive ? this.toCell(e) : null
      if (this.state.interactive) this.draw()
    })
    this.canvas.addEventListener('mouseleave', () => {
      if (this.hover) {
        this.hover = null
        this.draw()
      }
    })
    this.canvas.addEventListener('click', (e) => {
      if (!this.state.interactive || !this.opts.onPlace) return
      const cell = this.toCell(e)
      if (cell) this.opts.onPlace(cell.x, cell.y)
    })

    const observer = new ResizeObserver(() => {
      // 回调里直接改 canvas 尺寸会再触发一轮布局，
      // 浏览器会报「loop completed with undelivered notifications」；
      // 挪进 rAF 并在真正变化时才重绘，循环就断了。
      if (this._raf) return
      this._raf = requestAnimationFrame(() => {
        this._raf = null
        const width = this.wrap.clientWidth
        if (width > 0) {
          const next = Math.min(width, this.opts.maxSize || 700)
          if (Math.abs(next - this.size) > 1) {
            this.size = next
            this.draw()
          }
        }
      })
    })
    observer.observe(this.wrap)
  }

  update(patch) {
    Object.assign(this.state, patch)
    this.draw()
  }

  geometry() {
    const size = this.size
    const pad = size * PAD_RATIO
    const step = (size - pad * 2) / (SIZE - 1)
    return { size, pad, step, at: (i) => pad + i * step }
  }

  toCell(event) {
    const rect = this.canvas.getBoundingClientRect()
    const { pad, step } = this.geometry()
    const x = Math.round((event.clientX - rect.left - pad) / step)
    const y = Math.round((event.clientY - rect.top - pad) / step)
    if (x < 0 || y < 0 || x >= SIZE || y >= SIZE) return null
    const dx = event.clientX - rect.left - (pad + x * step)
    const dy = event.clientY - rect.top - (pad + y * step)
    if (Math.hypot(dx, dy) > step * 0.55) return null
    return { x, y }
  }

  drawStone(ctx, cx, cy, r, color) {
    const g = ctx.createRadialGradient(cx - r * 0.34, cy - r * 0.38, r * 0.12, cx, cy, r * 1.02)
    if (color === 1) {
      // 黑子留一小片高光，木色反上来一点，不至于糊成一块圆
      g.addColorStop(0, '#5e5e66')
      g.addColorStop(0.42, '#1a1a1e')
      g.addColorStop(1, '#000000')
    } else {
      g.addColorStop(0, '#ffffff')
      g.addColorStop(0.52, '#f4f1ea')
      g.addColorStop(1, '#d9d3c6')
    }
    ctx.beginPath()
    ctx.arc(cx, cy, r, 0, Math.PI * 2)
    ctx.fillStyle = g
    // 阴影带一点木头的暖调，纯黑投影压在暖底上会发脏
    ctx.shadowColor = 'rgba(72, 40, 8, 0.45)'
    ctx.shadowBlur = r * 0.5
    ctx.shadowOffsetY = r * 0.14
    ctx.fill()
    ctx.shadowColor = 'transparent'
    ctx.shadowBlur = 0
    ctx.shadowOffsetY = 0

    ctx.beginPath()
    ctx.arc(cx, cy, r * 0.93, 0, Math.PI * 2)
    // 白子必须有一圈深描边，否则浅木色底上边界就化了
    ctx.strokeStyle = color === 1 ? 'rgba(0,0,0,0.45)' : 'rgba(96, 66, 26, 0.42)'
    ctx.lineWidth = Math.max(0.8, r * 0.05)
    ctx.stroke()
  }

  draw() {
    // 尺寸以容器为准，每次重画前先校正一次。初始值与 RO 回调都只是估计：
    // RO 的修正要走 rAF，页面在后台被节流时画布会停在旧尺寸上好一阵
    // （414 宽的走查量到过 560 的画布横在 348 的框上）。draw 由 update
    // 和轮播定时器驱动，不依赖渲染帧，这里同步校正最可靠。
    const live = Math.min(this.wrap.clientWidth || this.size, this.opts.maxSize || 700)
    if (live > 0 && Math.abs(live - this.size) > 1) this.size = live
    const canvas = this.canvas
    const dpr = window.devicePixelRatio || 1
    const { size, pad, step, at } = this.geometry()
    canvas.width = size * dpr
    canvas.height = size * dpr
    canvas.style.width = `${size}px`
    canvas.style.height = `${size}px`

    const ctx = canvas.getContext('2d')
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
    ctx.clearRect(0, 0, size, size)

    // 底板：榧木色的对角渐变 + 四周压暗，边上比中心深一档，有张真棋盘的厚度
    const bg = ctx.createLinearGradient(0, 0, size * 0.55, size)
    bg.addColorStop(0, BC.boardTop)
    bg.addColorStop(0.55, BC.boardMid)
    bg.addColorStop(1, BC.boardLow)
    ctx.fillStyle = bg
    ctx.fillRect(0, 0, size, size)

    const vignette = ctx.createRadialGradient(size / 2, size / 2, size * 0.28, size / 2, size / 2, size * 0.82)
    vignette.addColorStop(0, 'rgba(0,0,0,0)')
    vignette.addColorStop(1, 'rgba(104, 60, 12, 0.26)')
    ctx.fillStyle = vignette
    ctx.fillRect(0, 0, size, size)

    ctx.strokeStyle = BC.rim
    ctx.lineWidth = 1
    ctx.strokeRect(0.5, 0.5, size - 1, size - 1)

    const s = this.state
    const board = s.board

    // 网格
    for (let i = 0; i < SIZE; i += 1) {
      const p = at(i)
      const strong = i === 0 || i === SIZE - 1
      ctx.beginPath()
      ctx.strokeStyle = strong ? BC.gridStrong : BC.grid
      ctx.lineWidth = strong ? 1.5 : 1
      ctx.moveTo(at(0), p)
      ctx.lineTo(at(SIZE - 1), p)
      ctx.moveTo(p, at(0))
      ctx.lineTo(p, at(SIZE - 1))
      ctx.stroke()
    }

    // 星位
    for (const [x, y] of [
      [3, 3],
      [11, 3],
      [3, 11],
      [11, 11],
      [7, 7],
    ]) {
      ctx.beginPath()
      ctx.arc(at(x), at(y), Math.max(2, step * 0.08), 0, Math.PI * 2)
      ctx.fillStyle = BC.star
      ctx.fill()
    }

    // 提示点
    if (s.hintPoint) {
      ctx.beginPath()
      ctx.arc(at(s.hintPoint.x), at(s.hintPoint.y), step * 0.44, 0, Math.PI * 2)
      ctx.strokeStyle = BC.hint
      ctx.lineWidth = 2
      ctx.setLineDash([5, 4])
      ctx.stroke()
      ctx.setLineDash([])
    }

    // 禁手点：只在轮到自己时画
    if (s.interactive && s.banned && s.banned.length > 0) {
      for (const point of s.banned) {
        const cx = at(point.x)
        const cy = at(point.y)
        const arm = step * 0.26
        ctx.beginPath()
        ctx.moveTo(cx - arm, cy - arm)
        ctx.lineTo(cx + arm, cy + arm)
        ctx.moveTo(cx + arm, cy - arm)
        ctx.lineTo(cx - arm, cy + arm)
        ctx.strokeStyle = BC.ban
        ctx.lineWidth = Math.max(1.6, step * 0.07)
        ctx.lineCap = 'round'
        ctx.stroke()

        ctx.beginPath()
        ctx.arc(cx, cy, step * 0.36, 0, Math.PI * 2)
        ctx.strokeStyle = BC.banSoft
        ctx.lineWidth = 1
        ctx.stroke()
      }
    }

    // 网络热力：画在棋子下面。木底上只有偏红的暖色能透出来，
    // 低透明度的褐色会直接消失
    if (s.heat && s.heat.length > 0) {
      for (const p of s.heat) {
        if (board[p.y * SIZE + p.x] !== 0) continue
        const a = Math.max(0, Math.min(1, p.score))
        if (a < 0.03) continue
        ctx.beginPath()
        ctx.arc(at(p.x), at(p.y), step * 0.46 * (0.42 + a * 0.58), 0, Math.PI * 2)
        ctx.fillStyle = `rgba(190, 76, 22, ${(0.05 + a * 0.34).toFixed(3)})`
        ctx.fill()
      }
    }

    // 落点预测：墨线圆圈，半径与透明度都按份额缩放
    if (!s.ghost && s.predict && s.predict.length > 0) {
      const top = Math.max(...s.predict.map((p) => p.share)) || 1
      for (const p of s.predict) {
        if (board[p.y * SIZE + p.x] !== 0) continue
        const weight = Math.max(0, Math.min(1, p.share / top))
        if (weight < 0.05) continue
        const cx = at(p.x)
        const cy = at(p.y)
        // 圈要明显小于棋子：跟棋子一样大就会被读成「空着的棋子位」
        const r = step * (0.13 + weight * 0.17)

        ctx.beginPath()
        ctx.arc(cx, cy, r, 0, Math.PI * 2)
        ctx.fillStyle = `rgba(64, 38, 6, ${(0.02 + weight * 0.05).toFixed(3)})`
        ctx.fill()
        ctx.beginPath()
        ctx.arc(cx, cy, r, 0, Math.PI * 2)
        ctx.strokeStyle = `rgba(52, 30, 4, ${(0.26 + weight * 0.4).toFixed(3)})`
        ctx.lineWidth = Math.max(1, step * (0.02 + weight * 0.018))
        ctx.stroke()
      }
    }

    // 棋子
    const radius = step * 0.42
    for (let y = 0; y < SIZE; y += 1) {
      for (let x = 0; x < SIZE; x += 1) {
        const v = board[y * SIZE + x]
        if (v) this.drawStone(ctx, at(x), at(y), radius, v)
      }
    }

    // 待落子（逐步确认模式）
    if (s.ghost) {
      ctx.globalAlpha = 0.45
      this.drawStone(ctx, at(s.ghost.x), at(s.ghost.y), radius, s.ghost.color)
      ctx.globalAlpha = 1
      ctx.beginPath()
      ctx.arc(at(s.ghost.x), at(s.ghost.y), radius * 1.12, 0, Math.PI * 2)
      ctx.strokeStyle = BC.focus
      ctx.lineWidth = Math.max(1.6, step * 0.05)
      ctx.setLineDash([4, 4])
      ctx.stroke()
      ctx.setLineDash([])
    }

    // 解说焦点
    if (s.focus) {
      ctx.beginPath()
      ctx.arc(at(s.focus.x), at(s.focus.y), radius * 1.24, 0, Math.PI * 2)
      ctx.strokeStyle = BC.focus
      ctx.lineWidth = Math.max(1.6, step * 0.055)
      ctx.stroke()
    }

    // 落子预览
    if (s.interactive && this.hover && board[this.hover.y * SIZE + this.hover.x] === 0) {
      ctx.globalAlpha = 0.35
      this.drawStone(ctx, at(this.hover.x), at(this.hover.y), radius, s.humanColor)
      ctx.globalAlpha = 1
    }

    // 最后一手：白点。黑子上白点、白子上黑点，保证任何底色都看得清
    if (s.lastMove) {
      const cx = at(s.lastMove.x)
      const cy = at(s.lastMove.y)
      const v = board[s.lastMove.y * SIZE + s.lastMove.x]
      const dot = v === 1 ? '#ffffff' : '#111114'
      const ring = v === 1 ? 'rgba(255,255,255,0.55)' : 'rgba(255,255,255,0.7)'
      ctx.beginPath()
      ctx.arc(cx, cy, radius * 0.3, 0, Math.PI * 2)
      ctx.fillStyle = dot
      ctx.fill()
      ctx.beginPath()
      ctx.arc(cx, cy, radius * 0.3, 0, Math.PI * 2)
      ctx.strokeStyle = ring
      ctx.lineWidth = 1
      ctx.stroke()
    }

    // 胜利连线：朱红 + 轻微辉光——木底上唯一压得住黑白子的颜色
    if (s.winLine && s.winLine.length >= 2) {
      const first = s.winLine[0]
      const last = s.winLine[s.winLine.length - 1]
      ctx.beginPath()
      ctx.moveTo(at(first[0]), at(first[1]))
      ctx.lineTo(at(last[0]), at(last[1]))
      ctx.strokeStyle = BC.win
      ctx.lineWidth = Math.max(3, step * 0.1)
      ctx.lineCap = 'round'
      ctx.globalAlpha = 0.9
      ctx.shadowColor = 'rgba(194, 56, 42, 0.6)'
      ctx.shadowBlur = step * 0.3
      ctx.stroke()
      ctx.shadowColor = 'transparent'
      ctx.shadowBlur = 0
      ctx.globalAlpha = 1
    }
  }
}

/* ======================================================== SVG 小棋盘 */

const MARGIN = 10
const SPAN = 280
const STEP_M = SPAN / 14
const STARS = [
  [3, 3],
  [11, 3],
  [7, 7],
  [3, 11],
  [11, 11],
]
const mpx = (v) => MARGIN + v * STEP_M

const SVG_NS = 'http://www.w3.org/2000/svg'
function svgEl(tag, attrs = {}) {
  const node = document.createElementNS(SVG_NS, tag)
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null) continue
    node.setAttribute(k, String(v))
  }
  return node
}

/** 教程用静态小棋盘：SVG 节点可检查、可自动化验证 */
function miniBoard({ stones = [], marks = [], size = 300, label = '教学棋盘' }) {
  const svg = svgEl('svg', {
    class: 'mini-board',
    viewBox: '0 0 300 300',
    width: size,
    height: size,
    role: 'img',
    'aria-label': label,
  })
  svg.append(svgEl('rect', { x: 0, y: 0, width: 300, height: 300, rx: 10, class: 'mini-bg' }))

  for (let i = 0; i < 15; i += 1) {
    svg.append(
      svgEl('line', { x1: mpx(0), y1: mpx(i), x2: mpx(14), y2: mpx(i), class: 'mini-grid' }),
      svgEl('line', { x1: mpx(i), y1: mpx(0), x2: mpx(i), y2: mpx(14), class: 'mini-grid' }),
    )
  }
  for (const [x, y] of STARS) {
    svg.append(svgEl('circle', { cx: mpx(x), cy: mpx(y), r: 2, class: 'mini-star' }))
  }
  for (const [x, y, color] of stones) {
    svg.append(
      svgEl('circle', {
        cx: mpx(x),
        cy: mpx(y),
        r: 8.2,
        class: color === 1 ? 'mini-stone mini-black' : 'mini-stone mini-white',
      }),
    )
  }
  for (const m of marks || []) {
    const cx = mpx(m.x)
    const cy = mpx(m.y)
    if (m.kind === 'ban') {
      const g = svgEl('g', { class: 'mini-ban' })
      g.append(svgEl('circle', { cx, cy, r: 8.6 }))
      g.append(svgEl('line', { x1: cx - 5, y1: cy - 5, x2: cx + 5, y2: cy + 5 }))
      g.append(svgEl('line', { x1: cx + 5, y1: cy - 5, x2: cx - 5, y2: cy + 5 }))
      svg.append(g)
    } else if (m.kind === 'star') {
      svg.append(svgEl('circle', { cx, cy, r: 2.4, class: 'mini-star' }))
    } else {
      svg.append(svgEl('circle', { cx, cy, r: 8.6, class: 'mini-hint' }))
    }
  }
  return svg
}

/* ============================================================ 页面骨架 */

const NAV_ITEMS = [
  { key: 'play', href: 'play.html', label: '对弈' },
  { key: 'replay', href: 'replay.html', label: '复盘' },
  { key: 'tutorial', href: 'tutorial.html', label: '教程' },
  { key: 'greedy', href: 'greedy.html', label: '贪心学习' },
  { key: 'code', href: 'code.html', label: '代码' },
  { key: 'arena', href: 'arena.html', label: '竞技场' },
]

function buildChrome(user, active) {
  const mount = document.getElementById('chrome')
  if (!mount) return

  const stats = (user && user.stats) || {}
  const games = stats.games ?? 0
  const winRate = Math.round((stats.win_rate ?? 0) * 100)

  const nav = el(
    'nav',
    { class: 'topnav', 'aria-label': '主导航' },
    NAV_ITEMS.map((item) =>
      el('a', {
        class: `topnav-item ${item.key === active ? 'is-active' : ''}`,
        href: item.href,
        dataset: { nav: item.key },
      }, item.label),
    ),
  )

  const meta = el(
    'div',
    { class: 'topbar-meta' },
    el('span', { class: 'pill pill-record' }, `${games} 局 · 胜率 ${winRate}%`),
    el('span', { class: 'pill pill-user' }, (user && user.name) || ''),
    el('button', { class: 'pill pill-quiet', type: 'button', onclick: doLogout }, '退出'),
  )

  const burger = el(
    'button',
    { class: 'burger', type: 'button', 'aria-label': '菜单', 'aria-expanded': 'false', 'aria-controls': 'mobile-menu' },
    el('span', { class: 'burger-bar' }),
    el('span', { class: 'burger-bar' }),
    el('span', { class: 'burger-bar' }),
  )

  const header = el(
    'header',
    { class: 'topbar' },
    el(
      'div',
      { class: 'topbar-left' },
      el(
        'a',
        { class: 'brand', href: 'index.html' },
        el('span', { class: 'brand-logo' }, el('img', { src: 'assets/logo.svg', alt: '', width: '40', height: '40' })),
        el('span', { class: 'brand-text' }, el('b', {}, 'Gomoku Match')),
      ),
      nav,
    ),
    meta,
    burger,
  )

  const overlay = el('div', { class: 'mobile-overlay', id: 'mobile-overlay', hidden: true })
  const menu = el(
    'nav',
    { class: 'mobile-menu', id: 'mobile-menu', 'aria-label': '移动端导航', hidden: true },
    NAV_ITEMS.map((item, i) =>
      el('a', {
        class: `menu-link ${item.key === active ? 'is-active' : ''}`,
        href: item.href,
        style: `--i:${i}`,
      }, item.label),
    ),
    el('a', { class: 'menu-signin', style: `--i:${NAV_ITEMS.length}`, href: 'login.html', onclick: doLogout }, '退出'),
  )

  mount.append(header, overlay, menu)

  const setMenu = (open) => {
    document.body.classList.toggle('menu-open', open)
    burger.setAttribute('aria-expanded', String(open))
    overlay.hidden = !open
    menu.hidden = !open
  }
  burger.addEventListener('click', () => setMenu(!document.body.classList.contains('menu-open')))
  overlay.addEventListener('click', () => setMenu(false))
  menu.addEventListener('click', (e) => {
    if (e.target.closest('a')) setMenu(false)
  })
  window.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') setMenu(false)
  })
  window.addEventListener('resize', () => {
    if (window.innerWidth > 860) setMenu(false)
  })
}

async function doLogout() {
  try {
    await api.logout()
  } catch {
    /* 服务端删不掉 token 也要让本地登出 */
  }
  setToken('')
  window.location.href = 'login.html'
}

/**
 * 内页启动：拉身份与配置，装配顶栏。
 * 未登录（401）→ 跳 login.html 并带 next 参数回来。
 */
async function bootPage(active) {
  let me = null
  let config = null
  try {
    const [meRes, cfgRes] = await Promise.allSettled([api.me(), api.config()])
    if (meRes.status === 'fulfilled') me = meRes.value
    if (cfgRes.status === 'fulfilled') config = cfgRes.value
  } catch {
    /* 下面统一处理 */
  }

  if (!me || !me.user) {
    const next = encodeURIComponent(window.location.pathname.split('/').pop() + window.location.search)
    window.location.replace(`login.html?next=${next}`)
    return new Promise(() => {}) // 跳转中，挂住页面脚本
  }

  buildChrome(me.user, active)
  window.__gomokuUser = me.user
  return { user: me.user, config }
}

/* ------------------------------------------------- 横屏提示（移动端） */

function landscapeHint() {
  if (!window.matchMedia('(max-width: 900px)').matches) return
  const node = el(
    'div',
    { class: 'landscape-hint', 'aria-hidden': 'true' },
    el('b', {}, '请竖屏使用'),
  )
  const update = () => {
    const narrow = window.matchMedia('(max-width: 900px)').matches
    const flat = window.matchMedia('(orientation: landscape)').matches
    node.classList.toggle('is-visible', narrow && flat)
  }
  window.addEventListener('resize', update)
  update()
  document.body.append(node)
}

/* ------------------------------------------------------- 背景视频层 */

// 内页沿用首页那段极光视频。标记里不写死：八个页面各抄一遍 <video> 只会
// 越抄越走样，这里统一注入，样式全在 app.css 的 .bg 一节。
// login.html 自己写了一份（它比内页更亮，见 login.css），先查重再插。
function mountBackdrop() {
  if (document.querySelector('.bg')) return
  const video = el(
    'video',
    { class: 'bg-video', autoplay: true, loop: true, playsinline: true, 'aria-hidden': 'true' },
    el('source', {
      src: 'https://motionsites.org/assets/prompt-media/f1862a9c64d125907d30.mp4',
      type: 'video/mp4',
    }),
    // 远端拉不到（境内网络抖动）时退到本地副本，页面不至于黑成纯色
    el('source', { src: 'assets/bg.mp4', type: 'video/mp4' }),
  )
  // muted 必须在插入文档前设成属性，否则自动播放会被拦
  video.muted = true
  document.body.prepend(el('div', { class: 'bg', 'aria-hidden': 'true' }, video))
}

mountBackdrop()

/* ============================================================ 汇总导出 */

window.Gomoku = {
  api,
  setToken,
  el,
  $,
  $$,
  fmtTime,
  fmtShort,
  coordName,
  spinner,
  GomokuBoard,
  miniBoard,
  bootPage,
  landscapeHint,
}
