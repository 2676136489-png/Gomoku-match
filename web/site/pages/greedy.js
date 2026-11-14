/**
 * 贪心学习：喂棋谱、训一张 12 参数的估值表。
 *
 * 左侧是棋谱展台（每盘棋从头摆一遍，摆完停两秒换下一盘），
 * 右侧是训练台（SSE 逐轮直播），底部是分值表与棋谱库。
 */

const { api, el, $, $$, GomokuBoard, spinner } = window.Gomoku

const MAX_TEXT = 64 * 1024

const fmtValue = (v) => {
  if (v === null || v === undefined) return '—'
  if (v >= 1e8) return `${(v / 1e8).toFixed(1)} 亿`
  if (v >= 1e6) return `${(v / 1e6).toFixed(1)} 百万`
  if (v >= 1e4) return `${(v / 1e4).toFixed(1)} 万`
  if (v >= 10) return String(Math.round(v))
  if (v < 0.5) return '≈0'
  return v.toFixed(1)
}

// 分值跨了七个数量级，取对数画长度；0 也留 2%，不然像没画
const barWidth = (v) => {
  const pct = v ? (Math.log10(v + 1) / Math.log10(1e8 + 1)) * 100 : 0
  return Math.max(2, Math.min(100, pct))
}
const pct = (v) => `${Math.round((v ?? 0) * 100)}%`

const SPEED = 220 // 每手动画间隔 ms
const HOLD = 2400 // 一盘放完停多久再换下一盘

const state = {
  status: null,
  items: [],
  summary: null,
  activeId: null,
  error: '',
  training: false,
  stage: '',
  live: [],
  result: null,
  uploading: false,
  pasteOpen: false,
  pasteName: '',
  pasteText: '',
}

let board = null
let carouselIndex = 0
// 轮播只留一条定时链。之前每调一次 startCarousel 就多一条，点几次棋谱、
// 上传一次、悬停几次之后几条链同时在跑：手数跳得比设定快，选中行还会被
// 另一条链的「换下一盘」当场顶掉（上传完刚选中就跳到别的盘上）。
let carouselTimer = 0
let ply = 0
let playing = true
let hover = false
let abortRef = null
let node = {}

/* ------------------------------------------------------------- 展台轮播 */

function carouselItem() {
  if (!state.items.length) return null
  const at = state.activeId ? state.items.findIndex((i) => i.id === state.activeId) : -1
  carouselIndex = at >= 0 ? at : Math.min(carouselIndex, state.items.length - 1)
  return state.items[carouselIndex]
}

function carouselTick() {
  if (!playing || hover || !node.carouselBoard) return
  const item = carouselItem()
  if (!item) return
  const total = (item.moves || []).length
  if (ply < total) {
    ply += 1
    renderCarousel()
    carouselTimer = setTimeout(carouselTick, SPEED)
  } else {
    carouselTimer = setTimeout(() => {
      if (!playing || hover || !state.items.length) return
      carouselIndex = (carouselIndex + 1) % state.items.length
      state.activeId = state.items[carouselIndex].id
      ply = 0
      renderCarousel()
      renderCorpusList()
      carouselTimer = setTimeout(carouselTick, SPEED)
    }, HOLD)
  }
}

function startCarousel() {
  clearTimeout(carouselTimer)
  carouselTimer = setTimeout(carouselTick, SPEED)
}

function renderCarousel() {
  const item = carouselItem()
  const holder = node.carouselBoard
  if (!holder) return
  holder.innerHTML = ''
  if (!item) {
    holder.append(el('p', { class: 'empty-note' }, '棋谱库是空的，先上传一盘。'))
    return
  }
  const total = (item.moves || []).length
  const shown = Math.min(ply, total)

  const cells = new Array(225).fill(0)
  if (item.moves) {
    item.moves.slice(0, shown).forEach(([x, y], i) => {
      cells[y * 15 + x] = i % 2 === 0 ? 1 : 2
    })
  }
  const lastMove = shown > 0 && item.moves ? { x: item.moves[shown - 1][0], y: item.moves[shown - 1][1] } : null

  const frame = el('div', { class: 'board-frame' })
  holder.append(frame)
  board = new GomokuBoard(frame, {})
  board.update({ board: cells, lastMove, winLine: [], humanColor: 1, interactive: false })

  const progress = el('div', { class: 'carousel-progress' },
    el('span', { style: `width:${total ? (shown / total) * 100 : 0}%` }))

  const side = item.winner === 1 ? '黑胜' : item.winner === 2 ? '白胜' : '未分胜负'
  const noteTail = (item.note || '').includes('·')
    ? (item.note || '').split('·').slice(1).join('·').trim()
    : item.note

  const info = el(
    'div',
    { class: 'carousel-info' },
    el('div', { class: 'carousel-head' },
      el('span', { className: `carousel-badge is-${item.source}` }, item.source === 'builtin' ? '内置' : '上传'),
      el('h3', {}, item.name)),
    el('p', { class: 'carousel-note' }, noteTail || '—'),
    el('dl', { class: 'carousel-facts' },
      el('div', {}, el('dt', {}, '手数'), el('dd', {}, String(shown), el('em', {}, ` / ${total}`))),
      el('div', {}, el('dt', {}, '结果'), el('dd', {}, side)),
      el('div', {}, el('dt', {}, '来源'), el('dd', {}, item.author || '匿名'))),
    el('div', { class: 'carousel-controls' },
      el('button', { class: 'carousel-btn', type: 'button', title: '上一盘', onclick: () => goto(carouselIndex - 1) }, '‹'),
      el('button', { class: 'carousel-btn', type: 'button', title: playing ? '暂停' : '继续', onclick: () => { playing = !playing; renderCarousel(); if (playing) startCarousel() } }, playing ? '❙❙' : '▶'),
      el('button', { class: 'carousel-btn', type: 'button', title: '下一盘', onclick: () => goto(carouselIndex + 1) }, '›'),
      el('span', { class: 'carousel-count' }, `${carouselIndex + 1} / ${state.items.length}`)),
    el('div', { class: 'carousel-dots' },
      state.items.map((it, i) =>
        el('button', { class: `carousel-dot ${i === carouselIndex ? 'is-active' : ''}`, type: 'button', title: it.name, onclick: () => goto(i) }))),
  )

  node.carouselBoard.append(progress)
  node.carouselInfo.innerHTML = ''
  node.carouselInfo.append(info)
}

function goto(next) {
  if (!state.items.length) return
  carouselIndex = (next + state.items.length) % state.items.length
  state.activeId = state.items[carouselIndex].id
  ply = 0
  renderCarousel()
  renderCorpusList()
  startCarousel()
}

/* ------------------------------------------------------------- 渲染 */

function render() {
  const main = document.getElementById('main')
  main.innerHTML = ''

  const model = state.status && state.status.model
  const params = state.status && state.status.params
  const trained = model && model.trained

  const badges = el('div', { class: 'greedy-head-badges' },
    el('span', { className: `badge ${trained ? 'badge-on' : 'badge-off'}` }, trained ? '已训练' : '未训练'),
    el('span', { class: 'badge' }, `${(model && model.parameters) ?? 12} 个参数`),
    el('span', { class: 'badge' }, `${(state.summary && state.summary.total) ?? 0} 盘棋谱`))

  const errorSlot = el('div', { class: 'banner banner-error', role: 'alert', hidden: !state.error })
  if (state.error) errorSlot.textContent = state.error

  node.carouselBoard = el('div', { class: 'carousel-board' })
  node.carouselInfo = el('div', { class: 'carousel-info' })
  node.carouselBoard.addEventListener('mouseenter', () => { hover = true })
  // 暂停是「这一拍不再续链」，所以移开必须重新起链，否则悬停一次就再也不动了
  node.carouselBoard.addEventListener('mouseleave', () => { hover = false; startCarousel() })

  // 训练台
  const trainBtn = el('button', { class: 'btn btn-primary', type: 'button', onclick: train, disabled: state.training },
    state.training ? '训练中…' : trained ? '重新学习' : '开始学习')
  const stageNode = el('p', { className: `greedy-stage ${state.training ? 'is-live' : ''}` },
    state.training ? el('span', { class: 'spinner' }) : null,
    state.stage || (trained ? '已加载上次训练结果' : '尚未训练，当前等同手写分值表'))
  node.trainBtn = trainBtn
  node.stageNode = stageNode

  node.curveSlot = el('div', {})
  node.doneSlot = el('div', {})

  const trainPanel = el('section', { class: 'panel greedy-train' },
    el('div', { class: 'panel-head' }, el('h2', {}, '训练台'),
      el('span', { class: 'panel-note' }, params ? `学习率 0.04 · ${params.epochs} 轮` : '')),
    el('div', { class: 'greedy-train-actions' },
      trainBtn,
      state.training ? el('button', { class: 'btn', type: 'button', onclick: stopTrain }, '中止') : null,
      trained && !state.training ? el('button', { class: 'btn', type: 'button', onclick: resetModel }, '还原手写') : null),
    stageNode,
    node.curveSlot,
    node.doneSlot)

  // 学习效果
  const evalAfter = (state.result && state.result.eval) || (model && model.eval)
  const evalBefore = (state.result && state.result.eval_before) || (model && model.eval_before)
  const metrics = el('section', { class: 'panel greedy-metrics' },
    el('div', { class: 'panel-head' }, el('h2', {}, '学习效果')),
    el('div', { class: 'metric-grid' },
      el('div', { class: 'metric' }, el('span', {}, '实着命中率'),
        el('b', {}, evalAfter ? pct(evalAfter.top1) : '—'),
        el('em', {}, evalBefore ? `训练前 ${pct(evalBefore.top1)}` : '还没训练')),
      el('div', { class: 'metric' }, el('span', {}, '前三命中'),
        el('b', {}, evalAfter ? pct(evalAfter.top3) : '—'),
        el('em', {}, evalBefore ? `训练前 ${pct(evalBefore.top3)}` : '取候选前 3')),
      el('div', { class: 'metric' }, el('span', {}, '平均排名'),
        el('b', {}, evalAfter ? evalAfter.rank.toFixed(2) : '—'),
        el('em', {}, evalBefore ? `训练前 ${evalBefore.rank.toFixed(2)}` : '越小越准')),
      el('div', { class: 'metric' }, el('span', {}, '防守系数 α'),
        el('b', {}, (model && model.alpha ? model.alpha.toFixed(2) : '0.90')),
        el('em', {}, '手写方案是 0.90'))))

  // 分值表
  const weightPanel = el('section', { class: 'panel greedy-weights' },
    el('div', { class: 'panel-head' }, el('h2', {}, '棋形分值表'), el('span', { class: 'panel-note' }, '灰＝手写　朱＝学到（取对数画长度）')),
    el('div', { class: 'weight-legend' },
      el('span', {}, el('i', { class: 'swatch swatch-hand' }), '手写'),
      el('span', {}, el('i', { class: 'swatch swatch-learn' }), '学到')),
    el('div', { class: 'weight-table' },
      el('div', { class: 'weight-row is-head' }, el('span', {}), el('span', {}), el('span', { class: 'weight-num' }, '手写'), el('span', { class: 'weight-num' }, '学到')),
      ((model && model.table) || []).map((row) =>
        el('div', { class: 'weight-row', dataset: { rank: row.rank } },
          el('span', { class: 'weight-label' }, row.label),
          el('div', { class: 'weight-bars' },
            el('span', { class: 'weight-bar is-hand', style: `width:${barWidth(row.hand)}%` }),
            el('span', { class: 'weight-bar is-learn', style: `width:${barWidth(row.learned)}%` })),
          el('span', { class: 'weight-num' }, fmtValue(row.hand)),
          el('span', { className: `weight-num is-learn ${row.delta > 0.05 ? 'is-up' : row.delta < -0.05 ? 'is-down' : ''}` }, fmtValue(row.learned))))))

  // 棋谱库
  node.corpusList = el('ul', { class: 'corpus-list' })
  node.uploadZone = el('div', { class: 'upload-zone' })
  node.pasteSlot = el('div', {})

  const fileInput = el('input', { class: 'visually-hidden', type: 'file', accept: '.sgf,.txt,.json' })
  fileInput.addEventListener('change', () => {
    onFile(fileInput.files && fileInput.files[0])
    fileInput.value = ''
  })
  node.fileInput = fileInput

  renderUploadZone()

  const corpusPanel = el('section', { class: 'panel greedy-corpus' },
    el('div', { class: 'panel-head' }, el('h2', {}, '棋谱库'),
      el('span', { class: 'panel-note' },
        state.summary ? `内置 ${state.summary.builtin} · 上传 ${state.summary.upload} · 共 ${state.summary.plies} 手` : '')),
    el('div', { class: 'corpus-layout' }, node.corpusList,
      el('div', { class: 'corpus-upload' }, node.uploadZone, fileInput, node.pasteSlot)))

  main.append(
    el('div', { class: 'greedy' },
      el('section', { class: 'page-head' }, el('div', {}, el('h2', {}, '贪心学习')), badges),
      errorSlot,
      el('div', { class: 'greedy-grid' },
        el('section', { class: 'panel greedy-gallery' },
          el('div', { class: 'panel-head' }, el('h2', {}, '棋谱展台')),
          el('div', { class: 'carousel' }, node.carouselBoard, node.carouselInfo)),
        el('aside', { class: 'greedy-side' }, trainPanel, metrics)),
      weightPanel,
      corpusPanel),
  )

  // 曲线和训练结论都挂在 state 上：整页重建后要把它们填回来，
  // 否则训练跑完的结论行刚写上就被这里的空 doneSlot 抹掉
  renderCarousel()
  renderCorpusList()
  renderTrainingState()
  renderCurve()
}

function renderCorpusList() {
  const list = node.corpusList
  if (!list) return
  list.innerHTML = ''
  state.items.forEach((item) => {
    const li = el('li', { className: item.id === state.activeId ? 'is-active' : '' })
    li.append(
      el('button', { class: 'corpus-main', type: 'button', onclick: () => { state.activeId = item.id; ply = 0; carouselIndex = state.items.findIndex((i) => i.id === item.id); renderCarousel(); renderCorpusList(); startCarousel() } },
        el('span', { className: `corpus-tag is-${item.source}` }, item.source === 'builtin' ? '内置' : '上传'),
        el('span', { class: 'corpus-name' }, item.name),
        el('span', { class: 'corpus-meta' }, `${item.plies} 手`),
        el('span', { class: 'corpus-meta corpus-note' }, item.note || '')),
    )
    if (item.source === 'upload') {
      li.append(el('button', { class: 'corpus-del', type: 'button', title: '删除', onclick: () => remove(item) }, '×'))
    }
    list.append(li)
  })
  if (!state.items.length) list.append(el('li', { class: 'empty-note' }, '还没有棋谱。'))
}

function renderUploadZone() {
  const zone = node.uploadZone
  zone.innerHTML = ''
  zone.append(
    el('b', {}, '拖入棋谱文件'),
    el('span', {}, `.sgf / .txt / .json，最大 ${MAX_TEXT / 1024} KB`),
    el('button', { class: 'btn', type: 'button', disabled: state.uploading, onclick: () => node.fileInput.click() },
      state.uploading ? '解析中…' : '选择文件'),
    el('button', { class: 'linkish', type: 'button', onclick: () => { state.pasteOpen = !state.pasteOpen; renderPaste() } },
      state.pasteOpen ? '收起' : '或者直接粘贴文本'),
  )
  zone.addEventListener('dragover', (e) => {
    e.preventDefault()
    zone.classList.add('is-drag')
  })
  zone.addEventListener('dragleave', () => zone.classList.remove('is-drag'))
  zone.addEventListener('drop', (e) => {
    e.preventDefault()
    zone.classList.remove('is-drag')
    onFile(e.dataTransfer.files && e.dataTransfer.files[0])
  })
  renderPaste()
}

function renderPaste() {
  const slot = node.pasteSlot
  slot.innerHTML = ''
  if (!state.pasteOpen) return
  const nameInput = el('input', { class: 'input', placeholder: '给这盘棋起个名字', maxLength: 40, value: state.pasteName })
  nameInput.addEventListener('input', () => { state.pasteName = nameInput.value })
  const textArea = el('textarea', { class: 'textarea', rows: 5, placeholder: '(;GM[1]SZ[15];B[hh];W[hi]…)\n或者：H8 H9 I9 G9 …\n或者：[7,7] [8,8] …' })
  textArea.value = state.pasteText
  const submitBtn = el('button', { class: 'btn btn-primary', type: 'button', disabled: state.uploading || !state.pasteText.trim(), onclick: () => upload(state.pasteName, state.pasteText) }, '解析并入库')
  // 没有 React 的重渲染替我们把按钮点亮：文本一变就得自己更新 disabled，
  // 否则粘贴完按钮永远是灰的，粘贴入库这条路走不通
  textArea.addEventListener('input', () => {
    state.pasteText = textArea.value
    submitBtn.disabled = state.uploading || !state.pasteText.trim()
  })
  slot.append(
    el('div', { class: 'upload-paste' },
      nameInput, textArea, submitBtn),
  )
}

function renderCurve() {
  const slot = node.curveSlot
  if (!slot) return
  slot.innerHTML = ''
  const points = state.live.length ? state.live : (state.status && state.status.model && state.status.model.curve) || []
  if (points.length < 2) return

  const w = 260
  const h = 64
  const max = Math.max(...points.map((p) => p.loss))
  const min = Math.min(...points.map((p) => p.loss))
  const span = Math.max(0.0001, max - min)
  const xy = points.map((p, i) => {
    const x = (i / (points.length - 1)) * w
    const y = h - ((p.loss - min) / span) * (h - 8) - 4
    return `${x.toFixed(1)},${y.toFixed(1)}`
  })

  const svgNS = 'http://www.w3.org/2000/svg'
  const svg = document.createElementNS(svgNS, 'svg')
  svg.setAttribute('viewBox', `0 0 ${w} ${h}`)
  svg.setAttribute('width', '100%')
  svg.setAttribute('height', '64')
  svg.setAttribute('role', 'img')
  // 线下面垫一层同色渐变：损失往下掉这件事，眼睛先于数字看到
  const defs = document.createElementNS(svgNS, 'defs')
  const fade = document.createElementNS(svgNS, 'linearGradient')
  fade.setAttribute('id', 'loss-fade')
  fade.setAttribute('x1', '0')
  fade.setAttribute('y1', '0')
  fade.setAttribute('x2', '0')
  fade.setAttribute('y2', '1')
  for (const [off, color, op] of [[0, '#cf4529', 0.3], [1, '#cf4529', 0]]) {
    const stop = document.createElementNS(svgNS, 'stop')
    stop.setAttribute('offset', String(off))
    stop.setAttribute('stop-color', color)
    stop.setAttribute('stop-opacity', String(op))
    fade.append(stop)
  }
  defs.append(fade)
  svg.append(defs)
  const area = document.createElementNS(svgNS, 'path')
  area.setAttribute('d', `M0,${h} L${xy.join(' L')} L${w},${h} Z`)
  area.setAttribute('class', 'curve-area')
  svg.append(area)
  const path = document.createElementNS(svgNS, 'path')
  path.setAttribute('d', `M${xy.join(' L')}`)
  path.setAttribute('fill', 'none')
  path.setAttribute('stroke-width', '1.8')
  svg.append(path)

  slot.append(
    el('div', { class: 'greedy-curve' }, svg,
      el('div', { class: 'greedy-curve-foot' },
        el('span', {}, '交叉熵'),
        el('span', {}, `${points[0].loss.toFixed(3)} → ${points[points.length - 1].loss.toFixed(3)}`))),
  )
}

function renderTrainingState() {
  if (!node.stageNode) return
  node.stageNode.className = `greedy-stage ${state.training ? 'is-live' : ''}`
  node.stageNode.innerHTML = ''
  node.stageNode.append(
    ...(state.training ? [el('span', { class: 'spinner' })] : []),
    document.createTextNode(state.stage || (state.status && state.status.model && state.status.model.trained ? '已加载上次训练结果' : '尚未训练，当前等同手写分值表')),
  )
  if (node.trainBtn) node.trainBtn.textContent = state.training ? '训练中…' : (state.status && state.status.model && state.status.model.trained ? '重新学习' : '开始学习')
  node.doneSlot.innerHTML = ''
  if (state.result) {
    const r = state.result
    node.doneSlot.append(
      el('p', { class: 'greedy-done' },
        `用时 ${r.seconds}s · ${r.games} 盘 / ${r.samples} 个局面 · 复现率 ${pct(r.eval_before && r.eval_before.top1)} → `,
        el('b', {}, pct(r.eval && r.eval.top1))),
    )
  }
}

/* ------------------------------------------------------------- 动作 */

async function load() {
  const main = document.getElementById('main')
  main.innerHTML = ''
  main.append(spinner('读取贪心状态…'))
  const [st, corpus] = await Promise.allSettled([api.greedyStatus(), api.greedyCorpus(225)])
  if (st.status === 'fulfilled') state.status = st.value
  if (corpus.status === 'fulfilled') {
    state.items = (corpus.value.items) || []
    state.summary = corpus.value.summary || null
    if (state.items.length) state.activeId = state.items[0].id
  }
  if (st.status === 'rejected') state.error = (st.reason && st.reason.message) || '状态读取失败'
  main.innerHTML = ''
  render()
  startCarousel()
}

async function train() {
  if (state.training) return
  state.training = true
  state.error = ''
  state.live = []
  state.result = null
  state.stage = '准备训练'
  renderTrainingState()
  const controller = new AbortController()
  abortRef = controller
  try {
    await api.trainGreedy((ev) => {
      if (ev.type === 'stage') {
        state.stage = ev.label
        renderTrainingState()
      } else if (ev.type === 'epoch') {
        state.live.push(ev)
        renderCurve()
        renderTrainingState()
      } else if (ev.type === 'done') {
        state.result = ev.result
        if (state.status) state.status.model = ev.model
        state.stage = '训练完成'
        renderTrainingState()
        render()
      } else if (ev.type === 'error') {
        state.error = ev.message
        state.stage = '训练失败'
        renderTrainingState()
        render()
      }
    }, controller.signal)
  } catch (e) {
    if (e.name !== 'AbortError') state.error = e.message
  } finally {
    state.training = false
    abortRef = null
    renderTrainingState()
    render()
  }
}

function stopTrain() {
  if (abortRef) abortRef.abort()
  abortRef = null
  state.training = false
  state.stage = '已中止'
  renderTrainingState()
}

async function resetModel() {
  try {
    const res = await api.resetGreedy()
    if (state.status) state.status.model = res.model
    state.result = null
    state.live = []
    state.stage = ''
    render()
  } catch (e) {
    state.error = e.message
    render()
  }
}

async function upload(name, text) {
  if (!text.trim()) return
  state.uploading = true
  state.error = ''
  renderUploadZone()
  try {
    const res = await api.uploadCorpus(name || '上传棋谱', text)
    state.items.unshift(res.item)
    state.summary = res.summary
    state.activeId = res.item.id
    carouselIndex = 0
    state.pasteText = ''
    state.pasteName = ''
    state.pasteOpen = false
    ply = 0
    render()
    startCarousel()
  } catch (e) {
    state.error = e.message
    render()
  } finally {
    state.uploading = false
  }
}

async function onFile(file) {
  if (!file) return
  if (file.size > MAX_TEXT) {
    state.error = `文件太大：最多 ${MAX_TEXT / 1024} KB`
    render()
    return
  }
  const text = await file.text()
  upload(file.name.replace(/\.[^.]+$/, ''), text)
}

async function remove(item) {
  try {
    const res = await api.deleteCorpus(item.id)
    state.items = state.items.filter((i) => i.id !== item.id)
    state.summary = res.summary
    if (state.activeId === item.id) {
      state.activeId = state.items.length ? state.items[0].id : null
      carouselIndex = 0
      ply = 0
    }
    render()
    startCarousel()
  } catch (e) {
    state.error = e.message
    render()
  }
}

;(async () => {
  window.Gomoku.landscapeHint()
  await window.Gomoku.bootPage('greedy')
  load()
})()
