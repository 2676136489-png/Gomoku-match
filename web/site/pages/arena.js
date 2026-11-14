/**
 * 算法竞技场：五种算法两两对打，SSE 逐手直播。
 *
 * 记分板只报可数的事实：手数、平均耗时、谁赢——棋力来自搜索还是
 * 评估，跑两盘就看得出来。
 */

const { api, el, $, GomokuBoard } = window.Gomoku

const EMPTY = () => new Array(225).fill(0)

const PRESETS = [
  { label: '贪心 vs 搜索', left: 'greedy', right: 'minimax' },
  { label: '神经 vs 贪心', left: 'neural', right: 'greedy' },
  { label: '神经 vs 搜索', left: 'neural', right: 'minimax' },
  { label: '随机 vs 全部', left: 'random', right: 'greedy' },
]

const state = {
  left: 'greedy',
  right: 'minimax',
  difficulty: 3,
  rule: 'renju',
  running: false,
  error: '',
  board: EMPTY(),
  moves: [],
  info: null,
  result: null,
  tally: {},
}

let config = null
let board = null
let node = {}
let abortRef = null

function meta(key) {
  const algorithms = (config && config.algorithms) || []
  return algorithms.find((a) => a.key === key)
}

function picker(title, value, onChange) {
  const algorithms = (config && config.algorithms) || []
  return el('div', { class: 'picker', dataset: { side: title } },
    el('div', { class: 'picker-title' }, title),
    el('div', { class: 'picker-list' },
      algorithms.map((a) =>
        el('button', {
          class: `picker-item ${value === a.key ? 'is-active' : ''}`,
          type: 'button',
          dataset: { algorithm: a.key },
          onclick: () => onChange(a.key),
        }, el('b', {}, a.label), el('span', {}, `${a.tag} · ${a.speed}`)))))
}

function stats(color) {
  // 开局那一手是服务端随机放的，不算任何一方的决策
  const own = state.moves.filter((m) => m.color === color && !m.opening)
  if (!own.length) return { count: 0, avg: 0, max: 0 }
  const total = own.reduce((s, m) => s + m.ms, 0)
  return { count: own.length, avg: Math.round(total / own.length), max: Math.max(...own.map((m) => m.ms)) }
}

function sideName(color) {
  return state.info ? (color === 1 ? state.info.left.label : state.info.right.label) : ''
}

// 角标文案：对局中棋盘不再被盖住，观战信息缩进左上角的小药丸里
function hudText(n) {
  return n ? `引擎对弈中 · ${n} 手` : '引擎对弈中'
}

function render() {
  const main = document.getElementById('main')
  main.innerHTML = ''
  node = {}

  const errorSlot = el('div', { class: 'banner banner-error', role: 'alert', hidden: !state.error })
  if (state.error) errorSlot.textContent = state.error

  const presets = el('div', { class: 'arena-presets' },
    PRESETS.map((p) =>
      el('button', { class: 'chip', type: 'button', onclick: () => { state.left = p.left; state.right = p.right; render() } }, p.label)))

  const diffChips = el('div', { class: 'chips chips-4' },
    ((config && config.difficulties) || []).map((l) =>
      el('button', { class: `chip ${state.difficulty === l.level ? 'is-active' : ''}`, type: 'button', dataset: { level: l.level },
        onclick: () => { state.difficulty = l.level; render() } }, l.label)))

  const ruleChips = el('div', { class: 'chips' },
    ((config && config.rules) || []).map((r) =>
      el('button', { class: `chip ${state.rule === r.key ? 'is-active' : ''}`, type: 'button', dataset: { rule: r.key },
        onclick: () => { state.rule = r.rule ?? r.key; render() } }, r.label)))

  const runBtn = el('button', { class: 'btn btn-primary', type: 'button', onclick: run, disabled: state.running },
    state.running ? '对局进行中…' : '开始对局')
  node.runBtn = runBtn

  const tallySlot = el('div', { class: 'arena-tally' })
  renderTally(tallySlot)

  // 记分板
  node.scoreLeft = el('div', { class: 'score' })
  node.scoreRight = el('div', { class: 'score' })
  renderScores()

  node.boardHolder = el('div')
  board = new GomokuBoard(node.boardHolder, {})
  board.update({ board: state.board, interactive: false })

  node.boardOverlay = el('div', { class: 'board-overlay', hidden: !state.running },
    el('span', { class: 'spinner' }), el('p', {}, hudText(state.moves.length)))

  node.resultSlot = el('div', {})
  renderResult()

  node.logSlot = el('div', {})
  renderLog()

  main.append(
    el('div', { class: 'arena' },
      el('section', { class: 'page-head' },
        el('div', {}, el('h2', {}, '算法竞技场')),
        presets),
      errorSlot,
      el('div', { class: 'arena-grid' },
        el('section', { class: 'panel arena-setup' },
          picker('黑棋（先行）', state.left, (key) => { state.left = key; render() }),
          picker('白棋（后行）', state.right, (key) => { state.right = key; render() }),
          el('div', { class: 'arena-options' },
            el('label', { class: 'field' }, el('span', {}, '搜索强度'), diffChips),
            el('label', { class: 'field' }, el('span', {}, '规则'), ruleChips)),
          el('div', { class: 'arena-buttons' },
            runBtn,
            state.running ? el('button', { class: 'btn', type: 'button', onclick: stop } , '中止') : null),
          tallySlot),
        el('section', { class: 'arena-play' },
          el('div', { class: 'scoreboard' }, node.scoreLeft, el('span', { class: 'score-vs' }, 'VS'), node.scoreRight),
          el('div', { class: 'board-frame' }, node.boardHolder, node.boardOverlay),
          node.resultSlot,
          el('div', { class: 'panel arena-log' },
            el('div', { class: 'panel-head' }, el('h2', {}, '落子记录'), el('span', { class: 'panel-note' }, '最近 12 手')),
            node.logSlot)))))
}

function renderTally(slot) {
  slot.innerHTML = ''
  const entries = Object.entries(state.tally)
  if (!entries.length) return
  slot.append(
    el('span', { class: 'coach-label' }, '本次会话战绩'),
    el('ul', {},
      entries.map(([key, wins]) =>
        el('li', {}, el('b', {}, (meta(key) && meta(key).label) || key), el('span', {}, `${wins} 胜`)))))
}

function renderScores() {
  const leftStats = stats(1)
  const rightStats = stats(2)
  node.scoreLeft.innerHTML = ''
  node.scoreRight.innerHTML = ''
  node.scoreLeft.className = `score ${state.result && state.result.winner === 1 ? 'is-winner' : ''}`
  node.scoreRight.className = `score ${state.result && state.result.winner === 2 ? 'is-winner' : ''}`
  node.scoreLeft.append(
    el('span', { class: 'dot dot-black' }),
    el('b', {}, (state.info && state.info.left.label) || (meta(state.left) && meta(state.left).label) || state.left),
    el('em', {}, `${leftStats.count} 手 · 均 ${leftStats.avg}ms`))
  node.scoreRight.append(
    el('span', { class: 'dot dot-white' }),
    el('b', {}, (state.info && state.info.right.label) || (meta(state.right) && meta(state.right).label) || state.right),
    el('em', {}, `${rightStats.count} 手 · 均 ${rightStats.avg}ms`))
}

function renderResult() {
  const slot = node.resultSlot
  slot.innerHTML = ''
  if (!state.result) return
  const r = state.result
  slot.append(
    el('div', { className: `lab-result ${r.winner ? 'is-done' : ''}` },
      el('b', {}, r.winner ? `${sideName(r.winner)} 获胜` : '和棋'),
      el('span', {}, `${r.reason} · 共 ${r.moves ?? state.moves.length} 手`)))
}

function renderLog() {
  const slot = node.logSlot
  slot.innerHTML = ''
  if (!state.moves.length) {
    slot.append(el('p', { class: 'empty-note' }, '暂无对局记录。'))
    return
  }
  slot.append(
    el('ul', { class: 'arena-moves' },
      state.moves.slice(-12).map((m) =>
        el('li', { dataset: { n: m.n } },
          el('span', { class: 'lab-move-n' }, String(m.n)),
          el('span', { className: `dot dot-${m.color === 1 ? 'black' : 'white'}` }),
          el('span', { class: 'lab-move-xy' }, `${m.x},${m.y}`),
          el('span', { class: 'lab-move-ms' }, m.opening ? '开局' : `${m.ms}ms`)))))
}

async function run() {
  if (state.running) return
  state.running = true
  state.error = ''
  state.result = null
  state.moves = []
  state.info = null
  state.board = EMPTY()
  render()

  const controller = new AbortController()
  abortRef = controller
  try {
    await api.arenaMatch(
      {
        left: { kind: 'algorithm', algorithm: state.left, difficulty: state.difficulty },
        right: { kind: 'algorithm', algorithm: state.right, difficulty: state.difficulty },
        rule: state.rule,
        difficulty: state.difficulty,
      },
      (ev) => {
        if (ev.type === 'start') {
          state.info = ev
          renderScores()
        } else if (ev.type === 'move') {
          state.moves.push(ev)
          state.board[ev.y * 15 + ev.x] = ev.color
          board.update({ board: state.board, lastMove: { x: ev.x, y: ev.y }, interactive: false })
          node.boardOverlay.querySelector('p').textContent = hudText(state.moves.length)
          renderScores()
          renderLog()
        } else if (ev.type === 'end') {
          state.result = ev
          state.running = false
          if (ev.winner) {
            const winnerKey = ev.winner === 1 ? state.left : state.right
            state.tally[winnerKey] = (state.tally[winnerKey] || 0) + 1
          }
          renderScores()
          renderResult()
          renderLog()
        } else if (ev.type === 'error') {
          state.error = `${ev.message}${ev.detail ? `：${ev.detail}` : ''}`
          state.running = false
          render()
        }
      },
      controller.signal,
    )
  } catch (e) {
    if (e.name !== 'AbortError') state.error = e.message
  } finally {
    state.running = false
    abortRef = null
    render()
  }
}

function stop() {
  if (abortRef) abortRef.abort()
  abortRef = null
  state.running = false
  render()
}

;(async () => {
  window.Gomoku.landscapeHint()
  const boot = await window.Gomoku.bootPage('arena')
  config = boot.config
  render()
})()
