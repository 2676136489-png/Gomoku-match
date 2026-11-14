/**
 * 复盘分析：无 ?id= 是棋谱库，带 ?id= 进回放器。
 *
 * 回放器：逐手推进 + 播放 + 胜率曲线 + 每手解说。方向键翻手，空格播放。
 */

const { api, el, $, $$, fmtTime, GomokuBoard, spinner } = window.Gomoku

const RESULT_TEXT = { win: '胜', loss: '负', draw: '和', live: '局' }
const MODE_TEXT = { agent: 'AI 对弈', learn: '学习', engine: '纯引擎' }

/* -------------------------------------------------------------- 棋谱库 */

async function renderLibrary() {
  const main = document.getElementById('main')
  main.innerHTML = ''
  const head = el('section', { class: 'page-head' },
    el('div', {}, el('h2', {}, '复盘分析')),
    el('span', { class: 'pill pill-quiet' }, '…'))
  const body = el('div', {})
  main.append(head, body)

  try {
    const res = await api.history(40)
    const rows = res.rows || []
    head.querySelector('.pill').textContent = `${rows.length} 局`
    if (!rows.length) {
      body.append(el('p', { class: 'empty-note' }, '暂无对局记录'))
      return
    }
    const table = el('table', { class: 'replay-table' },
      el('thead', {}, el('tr', {},
        el('th', {}, '结果'), el('th', {}, '对手'), el('th', {}, '模式'), el('th', {}, '手数'),
        el('th', {}, '终局'), el('th', {}, '时间'), el('th', {}, ''))),
      el('tbody', {},
        rows.map((row) =>
          el('tr', { dataset: { id: row.id } },
            el('td', {}, el('span', { class: `result result-${row.result}` }, RESULT_TEXT[row.result] || row.result)),
            el('td', { class: 'replay-opponent' }, row.opponent),
            el('td', { class: 'dim' }, MODE_TEXT[row.mode] || row.mode),
            el('td', { class: 'mono' }, String(row.moves)),
            el('td', { class: 'dim replay-reason' }, row.reason || '—'),
            el('td', { class: 'dim' }, fmtTime(row.at)),
            el('td', {}, el('button', {
              class: 'link-btn', type: 'button',
              onclick: () => (window.location.href = `replay.html?id=${encodeURIComponent(row.id)}`),
            }, '回放'))))))
    body.append(table)
  } catch (e) {
    body.append(el('div', { class: 'banner banner-error', role: 'alert' }, e.message))
  }
}

/* -------------------------------------------------------------- 回放器 */

const VERDICT = {
  win: { label: '胜着', tone: 'win' },
  good: { label: '好手', tone: 'good' },
  neutral: { label: '过渡', tone: 'neutral' },
  bad: { label: '失着', tone: 'bad' },
  ban: { label: '禁手', tone: 'ban' },
}
const KIND_LABEL = {
  shape: '棋形', defence: '防守', miss: '漏着', save: '防守', winrate: '胜率', alternative: '备选',
}
const SHAPES = ['活二', '眠三', '活三', '冲四', '活四', '连五']
const CONFIDENCE_LABEL = { certain: '确定', high: '高', medium: '中', low: '低' }
const SOURCE_LABEL = { tactical: '战术', model: '模型' }

class ReplayPlayer {
  constructor(main, gameId) {
    this.main = main
    this.gameId = gameId
    this.game = null
    this.index = 0
    this.playing = false
    this.tab = 'moves'
    this.anTab = 'rate'
    this.insight = null
    this.explain = null
    this.timer = null
    this.build()
  }

  build() {
    this.main.innerHTML = ''
    this.meta = el('p', {})
    this.head = el('section', { class: 'page-head' },
      el('div', {}, el('h2', {}, '复盘'), this.meta),
      el('div', { class: 'actions' },
        el('button', { class: 'btn', type: 'button', onclick: () => (window.location.href = 'replay.html') }, '棋谱库')))
    this.boardHolder = el('div')
    this.controls = el('div', { class: 'replay-controls' })
    this.scrub = el('input', { class: 'replay-scrub', type: 'range', min: -1, value: -1 })
    this.scrub.addEventListener('input', () => {
      this.stopPlay()
      this.setIndex(Number(this.scrub.value))
    })
    this.count = el('span', { class: 'replay-count' })

    this.tabs = el('div', { class: 'info-tabs', role: 'tablist' },
      ['moves', 'analysis'].map((key) =>
        el('button', {
          class: 'info-tab', type: 'button', role: 'tab', dataset: { tab: key },
          onclick: () => { this.tab = key; this.renderTabs() },
        }, key === 'moves' ? '棋谱' : '分析')))

    this.movesPanel = el('div', { dataset: { panel: 'moves' } })
    this.analysisPanel = el('div', { class: 'panel-stack', dataset: { panel: 'analysis' }, hidden: true })

    this.main.append(
      el('div', { class: 'replay-player' }, this.head,
        el('div', { class: 'replay-grid' },
          el('div', { class: 'replay-board' },
            el('div', { class: 'board-frame' }, this.boardHolder),
            el('div', { class: 'replay-bar' }, this.controls, this.scrub)),
          el('aside', { class: 'replay-side' }, this.tabs, this.movesPanel, this.analysisPanel))))

    this.board = new GomokuBoard(this.boardHolder, {})
    this.renderTabs()
    this.bindKeys()
  }

  bindKeys() {
    window.addEventListener('keydown', (e) => {
      if (e.target && /INPUT|TEXTAREA/.test(e.target.tagName)) return
      if (e.key === 'ArrowLeft') this.step(-1)
      if (e.key === 'ArrowRight') this.step(1)
      if (e.key === ' ') {
        e.preventDefault()
        this.togglePlay()
      }
    })
  }

  get moves() {
    return (this.game && this.game.moves) || []
  }

  setIndex(next) {
    this.index = Math.max(-1, Math.min(this.moves.length - 1, next))
    this.render()
    this.fetchExplain()
  }

  step(delta) {
    this.stopPlay()
    this.setIndex(this.index + delta)
  }

  togglePlay() {
    this.playing = !this.playing
    this.renderControls()
    if (this.playing) this.schedulePlay()
    else this.fetchExplain()
  }

  stopPlay() {
    if (!this.playing) return
    this.playing = false
    clearTimeout(this.timer)
    this.renderControls()
  }

  schedulePlay() {
    clearTimeout(this.timer)
    this.timer = setTimeout(() => {
      if (!this.playing) return
      if (this.index >= this.moves.length - 1) {
        this.playing = false
        this.renderControls()
        this.fetchExplain()
        return
      }
      this.setIndex(this.index + 1)
      this.schedulePlay()
    }, 420)
  }

  fetchExplain() {
    if (!this.game || this.playing || this.index < 0 || !this.moves.length) return
    api.explain(this.gameId, this.index)
      .then((res) => {
        if (!this.playing) {
          this.explain = res.explain
          if (this.tab === 'analysis') this.renderAnalysis()
        }
      })
      .catch(() => {
        this.explain = null
      })
  }

  async load() {
    this.main.innerHTML = ''
    this.main.append(spinner('加载中…'))
    try {
      const res = await api.replay(this.gameId)
      this.game = res.game
      this.main.innerHTML = ''
      this.build()
      this.index = 0
      const g = this.game
      const meta = `${MODE_TEXT[g.mode] || g.mode} · ${g.rule_label} · ${(g.moves || []).length} 手 · ${g.status === 'finished' ? g.result_reason || '已结束' : '进行中'}`
      this.meta.textContent = meta
      this.render()
      api.insight(this.gameId).then((data) => {
        this.insight = data
        if (this.tab === 'analysis') this.renderAnalysis()
      }).catch(() => {})
    } catch (e) {
      this.main.innerHTML = ''
      this.main.append(
        el('div', { class: 'banner banner-error', role: 'alert' }, e.message),
        el('button', { class: 'btn', type: 'button', onclick: () => (window.location.href = 'replay.html') }, '回到棋谱库'),
      )
    }
  }

  renderTabs() {
    $$('.info-tab', this.tabs).forEach((b) => {
      const on = b.dataset.tab === this.tab
      b.classList.toggle('is-active', on)
      b.setAttribute('aria-selected', String(on))
    })
    this.movesPanel.hidden = this.tab !== 'moves'
    this.analysisPanel.hidden = this.tab !== 'analysis'
    if (this.tab === 'analysis') this.renderAnalysis()
    else this.renderMoves()
  }

  currentBoard() {
    const cells = new Array(225).fill(0)
    for (let i = 0; i <= this.index && i < this.moves.length; i += 1) {
      const move = this.moves[i]
      cells[move.y * 15 + move.x] = move.color
    }
    return cells
  }

  render() {
    const current = this.index >= 0 ? this.moves[this.index] : null
    const g = this.game
    this.board.update({
      board: this.currentBoard(),
      lastMove: current ? { x: current.x, y: current.y } : null,
      winLine: (g && g.win_line) || [],
      humanColor: 1,
      interactive: false,
    })
    this.renderControls()
    if (this.tab === 'moves') this.renderMoves()
    else this.renderAnalysis()
  }

  renderControls() {
    const n = this.moves.length
    this.controls.innerHTML = ''
    this.controls.append(
      el('button', { class: 'btn btn-small', type: 'button', onclick: () => { this.stopPlay(); this.setIndex(-1) } }, '⏮'),
      el('button', { class: 'btn btn-small', type: 'button', onclick: () => this.step(-1) }, '◀'),
      el('button', { class: 'btn btn-primary btn-small', type: 'button', onclick: () => this.togglePlay() }, this.playing ? '暂停' : '播放'),
      el('button', { class: 'btn btn-small', type: 'button', onclick: () => this.step(1) }, '▶'),
      el('button', { class: 'btn btn-small', type: 'button', onclick: () => { this.stopPlay(); this.setIndex(n - 1) } }, '⏭'),
      this.count,
    )
    this.count.textContent = `${this.index + 1} / ${n}`
    this.scrub.max = String(n - 1)
    this.scrub.value = String(this.index)
  }

  renderMoves() {
    const moves = this.moves
    const panel = el('section', { class: 'panel' },
      el('div', { class: 'panel-head' }, el('h2', {}, '全部落子')))
    const feed = el('div', { class: 'game-feed' })
    moves.forEach((move, i) => {
      feed.append(
        el('button', {
          class: `game-row as-button ${i === this.index ? 'is-active' : ''}`,
          type: 'button',
          onclick: () => { this.stopPlay(); this.setIndex(i) },
        },
          el('span', { class: 'feed-idx' }, String(i + 1)),
          el('span', { className: `dot dot-${move.color === 1 ? 'black' : 'white'}` }),
          el('span', { class: 'feed-pos' }, `${String.fromCharCode(65 + move.x)}${15 - move.y}`),
          el('span', { class: 'feed-ms' }, move.comment || '')))
    })
    if (!moves.length) feed.append(el('p', { class: 'empty-note' }, '暂无落子'))
    panel.append(feed)
    this.movesPanel.innerHTML = ''
    this.movesPanel.append(panel)
  }

  /* 分析页签：胜率曲线 + 局面分析 + 当前手解说 */

  renderAnalysis() {
    const holder = this.analysisPanel
    holder.innerHTML = ''

    const series = (this.insight && this.insight.series) || []
    if (series.length) {
      const chart = el('div', { class: 'chart-block' })
      holder.append(chart)
      this.buildChart(chart, series)
    } else {
      holder.append(el('p', { class: 'empty-note' }, '暂无数据'))
    }

    holder.append(this.buildInsightPanel())
    if (this.index >= 0) holder.append(this.buildExplain())
  }

  buildChart(holder, series) {
    const W = 320
    const H = 96
    const PAD = 6
    const humanColor = (this.game && this.game.human_color) || 1
    const toHuman = (black) => (humanColor === 2 ? 1 - black : black)
    const pts = series.map((row, i) => {
      const p = toHuman(row.black)
      const x = series.length === 1 ? W / 2 : PAD + (i / (series.length - 1)) * (W - PAD * 2)
      const y = PAD + (1 - p) * (H - PAD * 2)
      return { x, y, p, row }
    })
    let swing = -1
    let swingDelta = 0
    for (let i = 1; i < pts.length; i += 1) {
      const d = Math.abs(pts[i].p - pts[i - 1].p)
      if (d > swingDelta) { swingDelta = d; swing = i }
    }
    const line = pts.map((p, i) => `${i === 0 ? 'M' : 'L'}${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(' ')
    const last = pts[pts.length - 1]
    const ahead = last.p >= 0.5
    const midY = PAD + 0.5 * (H - PAD * 2)

    const svgNS = 'http://www.w3.org/2000/svg'
    const svg = document.createElementNS(svgNS, 'svg')
    svg.setAttribute('viewBox', `0 0 ${W} ${H}`)
    svg.setAttribute('preserveAspectRatio', 'none')
    svg.setAttribute('class', 'chart-svg wr-svg')
    const mk = (tag, attrs) => {
      const n = document.createElementNS(svgNS, tag)
      for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, String(v))
      return n
    }
    svg.append(
      mk('rect', { x: 0, y: 0, width: W, height: midY, class: 'wr-zone top' }),
      mk('rect', { x: 0, y: midY, width: W, height: H - midY, class: 'wr-zone bottom' }),
      mk('line', { x1: 0, y1: midY, x2: W, y2: midY, class: 'chart-axis' }),
      mk('path', { d: line, class: 'chart-line', fill: 'none' }),
    )
    if (swing > 0) {
      svg.append(
        mk('line', { x1: pts[swing].x, y1: PAD, x2: pts[swing].x, y2: H - PAD, class: 'wr-swing-line' }),
        mk('circle', { cx: pts[swing].x, cy: pts[swing].y, r: 3.2, class: 'wr-swing-dot' }),
      )
    }
    svg.append(mk('circle', { cx: last.x, cy: last.y, r: 3, class: 'chart-dot' }))

    holder.append(
      el('div', { class: `chart wr-chart ${ahead ? 'chart-ahead' : 'chart-behind'}` },
        el('div', { class: 'chart-head' }, el('span', {}, '胜率曲线'),
          el('span', { class: `chart-verdict ${ahead ? 'ahead' : 'behind'}` }, `${(last.p * 100).toFixed(1)}%`)),
        svg,
        el('div', { class: 'chart-foot' },
          el('span', {}, `第 1 手 · ${(pts[0].p * 100).toFixed(0)}%`),
          swing > 0 ? el('span', { class: 'wr-swing-note' }, `转折 第 ${pts[swing].row.ply} 手 ${(swingDelta * 100).toFixed(0)} 点`) : null,
          el('span', {}, `第 ${series.length} 手`)),
        swing > 0
          ? el('button', { class: 'wr-jump', type: 'button', onclick: () => { this.stopPlay(); this.setIndex(swing); this.tab = 'analysis'; this.renderTabs() } }, `查看第 ${pts[swing].row.ply} 手`)
          : null))
  }

  buildInsightPanel() {
    const insight = this.insight
    const panel = el('section', { class: 'panel panel-analysis' },
      el('div', { class: 'panel-head' }, el('h2', {}, '局面分析'),
        insight ? el('span', { class: 'panel-note' }, `${insight.elapsed_ms}ms · 第 ${(insight.series || []).length} 手`) : null))

    const tabs = el('div', { class: 'an-tabs' })
    const body = el('div', { class: 'an-body' })
    const renderTab = () => {
      $$('.an-tab', tabs).forEach((b) => b.classList.toggle('is-active', b.dataset.an === this.anTab))
      body.innerHTML = ''
      if (!insight) {
        body.append(el('p', { class: 'empty-note' }, '暂无数据'))
        return
      }
      if (this.anTab === 'rate') this.buildRate(insight, body)
      else if (this.anTab === 'predict') this.buildPredict(insight, body)
      else this.buildData(insight, body)
    }
    ;[['rate', '胜率'], ['predict', '预测'], ['data', '数据']].forEach(([key, label]) =>
      tabs.append(el('button', { class: 'an-tab', type: 'button', dataset: { an: key }, onclick: () => { this.anTab = key; renderTab() } }, label)))
    panel.append(tabs, body)
    renderTab()
    return panel
  }

  buildRate(insight, body) {
    const win = insight.winrate
    if (!win) { body.append(el('p', { class: 'empty-note' }, '暂无数据')); return }
    const p = win.human ?? 0.5
    const pct = (p * 100).toFixed(1)
    body.append(
      el('div', { class: `rate-hero ${p >= 0.5 ? 'is-ahead' : 'is-behind'}` },
        el('b', {}, pct, el('i', { style: 'font-style:normal;font-size:0.6em' }, '%')),
        el('span', {}, '你的胜率', el('em', {}, `${SOURCE_LABEL[win.source] || win.source} · 把握${CONFIDENCE_LABEL[win.confidence] || win.confidence}`))),
      el('div', { class: 'rate-bar' },
        el('span', { class: 'rate-mine', style: `width:${pct}%` }), el('span', { class: 'rate-theirs' })),
      el('p', { class: 'rate-reason' }, win.reason),
      el('ul', { class: 'facts' },
        (win.facts || []).map((f) => {
          const total = (f.mine || 0) + (f.theirs || 0)
          const left = total > 0 ? (f.mine / total) * 100 : 50
          return el('li', {},
            el('div', { class: 'fact-head' }, el('b', {}, (f.mine || 0).toLocaleString()), el('span', {}, f.label), el('b', {}, (f.theirs || 0).toLocaleString())),
            el('div', { class: 'fact-track' }, el('span', { class: 'fact-mine', style: `width:${left.toFixed(1)}%` })))
        })),
    )
    if (win.confidence_note) body.append(el('p', { class: 'an-foot' }, win.confidence_note))
  }

  buildPredict(insight, body) {
    const pred = insight.prediction
    body.append(el('p', { class: 'pred-head' },
      insight.status === 'playing'
        ? `${pred && pred.side === 1 ? '黑棋' : '白棋'}下一手候选点`
        : '对局已结束'))
    const points = (pred && pred.points) || []
    if (points.length) {
      const list = el('ol', { class: 'pred-list' })
      points.forEach((pt, i) => {
        list.append(el('li', {},
          el('button', { class: `pred-row ${i === 0 ? 'is-top' : ''}`, type: 'button', title: pt.why,
            onclick: () => { this.stopPlay(); this.setIndex(this.index) } },
            el('span', { class: 'pred-rank' }, String(i + 1)),
            el('span', { class: 'pred-coord' }, `${pt.x},${pt.y}`),
            el('span', { class: 'pred-bar' }, el('i', { style: `width:${Math.max(2, pt.share * 100)}%` })),
            el('span', { class: 'pred-share' }, (pt.share * 100).toFixed(1))),
          el('p', { class: 'pred-why' }, pt.why)))
      })
      body.append(list)
    } else {
      body.append(el('p', { class: 'empty-note' }, '暂无候选点'))
    }
  }

  buildData(insight, body) {
    const census = insight.census
    const model = insight.model
    if (!census) { body.append(el('p', { class: 'empty-note' }, '暂无数据')); return }
    body.append(
      el('div', { class: 'data-block' },
        el('div', { class: 'data-title' }, el('span', {}, '棋形分布')),
        el('div', { class: 'shape-rows' },
          SHAPES.map((name) => {
            const b = (census.shapes.black[name] ?? 0)
            const w = (census.shapes.white[name] ?? 0)
            return el('div', { class: 'shape-row' },
              el('span', { class: 'shape-name' }, name),
              el('div', { class: 'shape-bars' },
                el('i', { class: 'sb-black', style: `width:${Math.min(100, b * 18)}%` }),
                el('i', { class: 'sb-white', style: `width:${Math.min(100, w * 18)}%` })),
              el('span', { class: 'shape-num' }, `${b} / ${w}`))
          }))),
      el('div', { class: 'data-block' },
        el('div', { class: 'data-title' }, el('span', {}, '可数项')),
        el('dl', { class: 'data-grid' },
          el('div', {}, el('dt', {}, '棋子'), el('dd', {}, `${census.stones.black} / ${census.stones.white}`)),
          el('div', {}, el('dt', {}, '棋形总量'), el('dd', {}, `${census.material.black.toLocaleString()} / ${census.material.white.toLocaleString()}`)),
          el('div', {}, el('dt', {}, '冲四点'), el('dd', {}, `${census.threats.black.fours + census.threats.black.open_fours} / ${census.threats.white.fours + census.threats.white.open_fours}`)),
          el('div', {}, el('dt', {}, '活三点'), el('dd', {}, `${census.threats.black.threes} / ${census.threats.white.threes}`)),
          el('div', {}, el('dt', {}, '盘面'), el('dd', {}, `${(census.occupancy * 100).toFixed(1)}%`)))),
    )
    if (model && model.buckets && model.buckets.length) {
      body.append(
        el('div', { class: 'data-block' },
          el('div', { class: 'data-title' }, el('span', {}, '模型可靠性'),
            el('em', {}, `${model.games ? `${model.games} 局标定` : '默认权重'} · AUC ${model.auc ?? '—'}`)),
          el('div', { class: 'rel-table' },
            el('div', { class: 'rel-head' }, el('span', {}, '预测区间'), el('span', {}, '样本'), el('span', {}, '实际')),
            model.buckets.map((b) =>
              el('div', { class: 'rel-row' }, el('span', {}, b.range), el('span', {}, String(b.n)),
                el('span', {}, el('i', { style: `width:${Math.min(100, b.actual * 100)}%` }), `${(b.actual * 100).toFixed(1)}%`))))))

    }
  }

  buildExplain() {
    const data = this.explain
    if (!data) {
      return el('section', { class: 'panel panel-explain is-empty' },
        el('div', { class: 'panel-head' }, el('h2', {}, '本手解说')),
        el('p', { class: 'empty-note' }, '暂无解说'))
    }
    const v = VERDICT[data.verdict] || VERDICT.neutral
    const before = data.winrate.before * 100
    const after = data.winrate.after * 100
    const delta = data.winrate.delta * 100
    const card = el('section', { class: `panel panel-explain tone-${v.tone}` })
    card.append(
      el('div', { class: 'panel-head' },
        el('h2', {}, `第 ${(data.index ?? 0) + 1} 手`),
        el('span', { class: 'panel-note' }, `${data.color === 1 ? '黑' : '白'}棋 ${data.x},${data.y}${data.think_ms > 0 ? ` · ${data.think_ms}ms` : ''}`)),
      el('div', { class: 'ex-head' }, el('span', { class: `ex-verdict tone-${v.tone}` }, v.label), el('b', {}, data.headline)),
      el('ul', { class: 'ex-facts' },
        (data.facts || []).map((f) => el('li', { className: `kind-${f.kind}` },
          el('span', { class: 'ex-kind' }, KIND_LABEL[f.kind] || f.kind), el('span', {}, f.text)))),
      el('div', { class: 'ex-rate' },
        el('div', { class: 'ex-rate-head' }, el('span', {}, '本手前后'),
          el('b', { class: delta >= 0 ? 'up' : 'down' }, `${delta >= 0 ? '+' : ''}${delta.toFixed(1)} 点`)),
        el('div', { class: 'ex-rate-track' },
          el('i', { class: 'ex-mark before', style: `left:${Math.min(99, Math.max(1, before))}%` }),
          el('i', { class: 'ex-mark after', style: `left:${Math.min(99, Math.max(1, after))}%` })),
        el('div', { class: 'ex-rate-foot' }, el('span', {}, `${before.toFixed(1)}%`), el('span', {}, `${after.toFixed(1)}%`))),
    )
    if (data.alternatives && data.alternatives.length) {
      card.append(el('div', { class: 'ex-alts' },
        el('div', { class: 'ex-alts-head' }, '其他候选'),
        el('ul', {}, data.alternatives.map((a) => el('li', {},
          el('span', { class: 'coord' }, `${a.x},${a.y}`), el('span', {}, a.why))))))
    }
    return card
  }
}

/* ============================================================== 入口 */

;(async () => {
  window.Gomoku.landscapeHint()
  await window.Gomoku.bootPage('replay')
  const id = new URLSearchParams(window.location.search).get('id')
  if (id) new ReplayPlayer(document.getElementById('main'), id).load()
  else renderLibrary()
})()
