/**
 * 对弈页：无 ?game= 参数时是开局准备页，带参数进对局。
 *
 * 开局准备只做一件事：把一局棋的参数定下来。对局中不出现任何设置项，
 * 改设置要重开一局。
 */

const { api, el, $, $$, GomokuBoard, spinner } = window.Gomoku

const FALLBACK_MODES = [
  { key: 'agent', label: 'AI 对弈', brief: '三档共用一套搜索算法；本档由大模型在候选落点中择一，模型未启用时算法自选。' },
  { key: 'learn', label: '学习模式', brief: '对手决策与 AI 对弈相同，每手附一条按棋规生成的教练点评；点评不调用模型。' },
  { key: 'engine', label: '纯引擎', brief: '仅由搜索算法决策，全程不调用模型、无点评；作为对照组。' },
]

const FALLBACK_RULES = [
  { key: 'renju', label: '国际连珠', brief: '黑棋禁手：长连、四四、三三' },
  { key: 'freestyle', label: '自由规则', brief: '无禁手，五子及以上即胜' },
]

const MODE_ORDER = ['agent', 'learn', 'engine']

/* ============================================================ 开局准备 */

function renderSetup(config) {
  const main = document.getElementById('main')
  main.innerHTML = ''

  const modes = (config && config.modes && config.modes.length ? config.modes : FALLBACK_MODES)
    .slice()
    .sort((a, b) => {
      const ia = MODE_ORDER.indexOf(a.key)
      const ib = MODE_ORDER.indexOf(b.key)
      return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib)
    })
  const rules = (config && config.rules && config.rules.length ? config.rules : FALLBACK_RULES)
  const levels = (config && config.difficulties) || []
  const personas = (config && config.personas) || []
  const llm = (config && config.llm) || {}
  const llmReady = Boolean(llm.enabled)

  const form = {
    mode: new URLSearchParams(window.location.search).get('mode') || 'agent',
    difficulty: 3,
    persona: 'balanced',
    human_color: 1,
    use_llm: true,
    rule: 'renju',
    strict_ban: false,
    step: false,
  }

  let busy = false
  const summaryLabel = el('span', { class: 'setup-summary-text' })
  const errorSlot = el('div', { class: 'banner banner-error', role: 'alert', hidden: true })
  const startBtn = el('button', { class: 'btn btn-primary btn-lg', type: 'button' }, '开始对局')

  const modeCards = el(
    'div',
    { class: 'setup-cards' },
    modes.map((m) =>
      el(
        'button',
        {
          class: 'setup-card',
          type: 'button',
          dataset: { mode: m.key },
          onclick: () => {
            form.mode = m.key
            sync()
          },
        },
        el('span', { class: 'setup-card-title' }, m.label),
        el('span', { class: 'setup-card-desc' }, m.brief),
      ),
    ),
  )

  const levelChips = el(
    'div',
    { class: 'chips chips-4' },
    levels.map((l) =>
      el(
        'button',
        {
          class: 'chip',
          type: 'button',
          dataset: { level: l.level },
          onclick: () => {
            form.difficulty = l.level
            sync()
          },
        },
        l.label,
        el('em', {}, `深度 ${l.depth}`),
      ),
    ),
  )

  const personaField = el('div', { class: 'setup-field' }, el('span', { class: 'setup-label' }, '风格'))
  const personaChips = el(
    'div',
    { class: 'chips' },
    personas.map((p) =>
      el(
        'button',
        {
          class: 'chip',
          type: 'button',
          dataset: { persona: p.key },
          title: p.brief,
          onclick: () => {
            form.persona = p.key
            sync()
          },
        },
        p.label,
      ),
    ),
  )
  personaField.append(personaChips)

  const ruleChips = el(
    'div',
    { class: 'chips' },
    rules.map((r) =>
      el(
        'button',
        {
          class: 'chip',
          type: 'button',
          dataset: { rule: r.key },
          title: r.brief,
          onclick: () => {
            form.rule = r.key
            sync()
          },
        },
        r.label,
      ),
    ),
  )

  const strictToggle = el('input', { type: 'checkbox', dataset: { setting: 'strict_ban' } })
  strictToggle.addEventListener('change', () => {
    form.strict_ban = strictToggle.checked
    sync()
  })

  const colorChips = el(
    'div',
    { class: 'chips' },
    [
      { key: 1, label: '执黑先行' },
      { key: 2, label: '执白后行' },
    ].map((c) =>
      el('button', {
        class: 'chip',
        type: 'button',
        dataset: { color: c.key },
        onclick: () => {
          form.human_color = c.key
          sync()
        },
      }, c.label),
    ),
  )

  const llmToggle = el('input', { type: 'checkbox', dataset: { setting: 'use_llm' } })
  llmToggle.addEventListener('change', () => {
    form.use_llm = llmToggle.checked
  })
  const stepToggle = el('input', { type: 'checkbox', dataset: { setting: 'step' } })
  stepToggle.addEventListener('change', () => {
    form.step = stepToggle.checked
    sync()
  })

  startBtn.addEventListener('click', async () => {
    if (busy) return
    busy = true
    errorSlot.hidden = true
    startBtn.disabled = true
    startBtn.textContent = '正在开局…'
    try {
      const res = await api.newGame(form)
      window.location.href = `play.html?game=${encodeURIComponent(res.game.id)}`
      return
    } catch (e) {
      errorSlot.textContent = e.message
      errorSlot.hidden = false
      busy = false
      startBtn.disabled = false
      startBtn.textContent = '开始对局'
    }
  })

  function sync() {
    $$('.setup-card', modeCards).forEach((btn) => {
      btn.classList.toggle('is-active', btn.dataset.mode === form.mode)
    })
    $$('.chip[data-level]', levelChips).forEach((btn) => {
      btn.classList.toggle('is-active', Number(btn.dataset.level) === form.difficulty)
    })
    $$('.chip[data-persona]', personaChips).forEach((btn) => {
      btn.classList.toggle('is-active', btn.dataset.persona === form.persona)
    })
    $$('.chip[data-rule]', ruleChips).forEach((btn) => {
      btn.classList.toggle('is-active', btn.dataset.rule === form.rule)
    })
    $$('.chip[data-color]', colorChips).forEach((btn) => {
      btn.classList.toggle('is-active', Number(btn.dataset.color) === form.human_color)
    })
    strictToggle.checked = Boolean(form.strict_ban)
    stepToggle.checked = Boolean(form.step)
    llmToggle.checked = Boolean(form.use_llm) && llmReady

    // 规则是连珠才显示禁手判负开关；风格只在非纯引擎模式出现
    strictToggle.closest('.setup-field').hidden = form.rule !== 'renju'
    personaField.hidden = form.mode === 'engine' || personas.length === 0
    llmToggle.disabled = !llmReady

    const modeInfo = modes.find((m) => m.key === form.mode)
    const levelInfo = levels.find((l) => l.level === form.difficulty)
    const personaInfo = personas.find((p) => p.key === form.persona)
    summaryLabel.textContent = [
      modeInfo && modeInfo.label,
      levelInfo && levelInfo.label,
      form.mode !== 'engine' && personaInfo && personaInfo.label,
      form.human_color === 1 ? '执黑' : '执白',
      (rules.find((r) => r.key === form.rule) || {}).label,
    ]
      .filter(Boolean)
      .join(' · ')
  }

  main.append(
    el(
      'div',
      { class: 'setup' },
      el('header', { class: 'setup-head' }, el('h2', {}, '开始一局')),
      el(
        'section',
        { class: 'setup-step', dataset: { step: 'opponent' } },
        el('div', { class: 'setup-step-head' }, el('b', {}, '1'), el('div', {}, el('h3', {}, '选择对手'))),
        modeCards,
      ),
      el(
        'section',
        { class: 'setup-step', dataset: { step: 'strength' } },
        el('div', { class: 'setup-step-head' }, el('b', {}, '2'), el('div', {}, el('h3', {}, '强度与风格'))),
        el('div', { class: 'setup-field' }, el('span', { class: 'setup-label' }, '强度'), levelChips),
        personaField,
      ),
      el(
        'section',
        { class: 'setup-step', dataset: { step: 'rules' } },
        el('div', { class: 'setup-step-head' }, el('b', {}, '3'), el('div', {}, el('h3', {}, '规则与执子'))),
        el('div', { class: 'setup-field' }, el('span', { class: 'setup-label' }, '规则'), ruleChips),
        el('div', { class: 'setup-field', hidden: false },
          el('label', { class: 'toggle' },
            strictToggle,
            el('span', {}, '禁手判负', el('em', {}, '正式赛制：黑棋落禁手点立即判负；默认拦下并说明原因，棋局继续')),
          ),
        ),
        el('div', { class: 'setup-field' }, el('span', { class: 'setup-label' }, '执子'), colorChips),
      ),
      el(
        'details',
        { class: 'setup-more' },
        el('summary', {}, '高级选项（模型决策、逐步确认）'),
        el(
          'div',
          { class: 'setup-more-body' },
          el('label', { class: `toggle ${llmReady ? '' : 'is-disabled'}` },
            llmToggle,
            el('span', {}, '用模型做决策',
              el('em', {}, llmReady ? `候选点交给 ${llm.model} 取舍；不勾选则用内置人格权重` : '未配置模型，使用内置策略人格')),
          ),
          el('label', { class: 'toggle' },
            stepToggle,
            el('span', {}, '逐步确认',
              el('em', {}, 'Agent 计算完成后暂停，点「确认」再落子，便于逐步看懂它为什么这么走')),
          ),
        ),
      ),
      errorSlot,
      el(
        'footer',
        { class: 'setup-foot' },
        el('p', { class: 'setup-summary' }, el('span', { class: 'setup-summary-label' }, '当前'), summaryLabel),
        startBtn,
      ),
    ),
  )

  sync()
}

/* ================================================================ 对局 */

const TABS = [
  { key: 'note', label: '解说' },
  { key: 'analysis', label: '分析' },
  { key: 'moves', label: '棋谱' },
]

const VERDICT = {
  win: { label: '胜着', tone: 'win' },
  good: { label: '好手', tone: 'good' },
  neutral: { label: '过渡', tone: 'neutral' },
  bad: { label: '失着', tone: 'bad' },
  ban: { label: '禁手', tone: 'ban' },
}

const KIND_LABEL = {
  shape: '棋形',
  defence: '兼顾防守',
  miss: '漏着',
  save: '救急',
  winrate: '胜率',
  alternative: '备选',
}

const GRADE_LABEL = { win: '胜势', good: '好手', warn: '漏防', neutral: '布局' }
const SHAPES = ['活二', '眠三', '活三', '冲四', '活四', '连五']
const CONFIDENCE_LABEL = { certain: '确定', high: '高', medium: '中', low: '低' }
const SOURCE_LABEL = { tactical: '战术判定', model: '标定模型' }

class GameView {
  constructor(main, gameId, refreshUser) {
    this.main = main
    this.gameId = gameId
    this.refreshUser = refreshUser
    this.game = null
    this.agent = null
    this.busy = false
    this.review = ''
    this.hintPoint = null
    this.coach = null
    this.tactics = null
    this.heat = null
    this.heatBusy = false
    this.insight = null
    this.explain = null
    this.pendingPreview = null
    this.explainBusy = false
    this.focus = null
    this.predictOn = true
    this.tab = 'note'
    this.anTab = 'rate'
    this.settled = ''
    this.insightSeq = 0

    this.build()
  }

  build() {
    this.main.innerHTML = ''
    this.errorSlot = el('div', { class: 'banner banner-error', role: 'alert', hidden: true })

    this.metaPills = el('div', { class: 'play-meta' })
    this.btnUndo = el('button', { class: 'btn btn-small', type: 'button', onclick: () => this.undo() }, '悔棋')
    this.btnHint = el('button', { class: 'btn btn-small', type: 'button', onclick: () => this.askHint() }, '提示')
    this.btnReview = el('button', { class: 'btn btn-small', type: 'button', onclick: () => this.askReview() }, '复盘')
    this.btnNew = el('button', {
      class: 'btn btn-small btn-primary',
      type: 'button',
      onclick: () => (window.location.href = 'play.html'),
    }, '新对局')

    this.boardHolder = el('div')
    this.overlay = el('div', { class: 'board-overlay', hidden: true }, el('span', { class: 'spinner' }), el('p', {}, '搜索中…'))
    this.captionMain = el('span', {})
    this.captionSide = el('span', {})
    this.btnPredict = el('button', {
      class: 'heat-toggle is-on',
      type: 'button',
      dataset: { tool: 'predict' },
      onclick: () => this.togglePredict(),
    }, '收起落点预测')
    this.btnHeat = el('button', {
      class: 'heat-toggle',
      type: 'button',
      dataset: { tool: 'heat' },
      onclick: () => this.toggleHeat(),
    }, '网络视角')
    this.legendPredict = el('span', { class: 'heat-legend', dataset: { legend: 'predict' }, hidden: true })
    this.legendHeat = el('span', { class: 'heat-legend', dataset: { legend: 'heat' }, hidden: true })

    this.tabs = el(
      'div',
      { class: 'info-tabs', role: 'tablist' },
      TABS.map((t) =>
        el('button', {
          class: 'info-tab',
          type: 'button',
          role: 'tab',
          dataset: { tab: t.key },
          onclick: () => {
            this.tab = t.key
            this.renderTabs()
          },
        }, t.label),
      ),
    )

    this.notePanel = el('div', { dataset: { panel: 'note' } })
    this.reviewSlot = el('div', { hidden: true })
    this.coachSlot = el('div', {})
    this.explainSlot = el('div', {})

    this.chartSlot = el('div', { class: 'chart-block' })
    this.analysisSlot = el('div', {})

    this.feedSlot = el('div', {})

    this.main.append(
      el(
        'div',
        { class: 'play' },
        el(
          'div',
          { class: 'play-bar' },
          this.metaPills,
          el('div', { class: 'play-actions' }, this.btnUndo, this.btnHint, this.btnReview, this.btnNew),
        ),
        this.errorSlot,
        el(
          'div',
          { class: 'layout' },
          el(
            'section',
            { class: 'board-column' },
            el('div', { class: 'board-frame' }, this.boardHolder, this.overlay),
            el('div', { class: 'board-caption' }, this.captionMain, this.captionSide),
            el('div', { class: 'board-tools' }, this.btnPredict, this.btnHeat, this.legendPredict, this.legendHeat),
          ),
          el(
            'aside',
            { class: 'info-column' },
            this.tabs,
            el(
              'div',
              { class: 'info-body' },
              this.notePanel,
              el('div', { dataset: { panel: 'analysis' }, class: 'panel-stack', hidden: true }, this.chartSlot, this.analysisSlot),
              el('div', { dataset: { panel: 'moves' }, hidden: true }, this.feedSlot),
            ),
          ),
        ),
      ),
    )

    this.notePanel.append(this.reviewSlot, this.coachSlot, this.explainSlot)
    this.board = new GomokuBoard(this.boardHolder, { onPlace: (x, y) => this.play(x, y) })
    this.renderTabs()
  }

  renderTabs() {
    $$('.info-tab', this.tabs).forEach((btn) => {
      const on = btn.dataset.tab === this.tab
      btn.classList.toggle('is-active', on)
      btn.setAttribute('aria-selected', String(on))
    })
    const panels = { note: this.notePanel, analysis: this.notePanel.nextElementSibling, moves: this.notePanel.nextElementSibling.nextElementSibling }
    Object.entries(panels).forEach(([key, node]) => {
      node.hidden = key !== this.tab
    })
    if (this.tab === 'analysis') this.renderAnalysis()
    if (this.tab === 'moves') this.renderFeed()
  }

  statusInfo() {
    const g = this.game
    if (!g) return { tone: 'idle', text: '准备中' }
    if (g.status === 'finished') {
      if (!g.winner) return { tone: 'idle', text: '和棋' }
      if (g.winner === g.human_color) return { tone: 'win', text: '你赢了' }
      return { tone: 'lose', text: g.ban_loss ? '禁手判负' : 'Agent 获胜' }
    }
    if (this.busy) return { tone: 'thinking', text: 'Agent 思考中' }
    if (g.pending) return { tone: 'wait', text: '等待确认落子' }
    return this.humanTurn ? { tone: 'live', text: '轮到你落子' } : { tone: 'wait', text: '等待对手落子' }
  }

  get humanTurn() {
    const g = this.game
    return Boolean(g && g.status === 'playing' && !g.pending && g.turn === g.human_color)
  }

  get canAct() {
    return this.humanTurn && !this.busy
  }

  showError(msg) {
    this.errorSlot.textContent = msg
    this.errorSlot.hidden = !msg
  }

  /* ---------------------------------------------------------- 渲染 */

  renderAll() {
    const g = this.game
    if (!g) return
    window.__gomoku = g

    const status = this.statusInfo()
    const oppoLabel = `${g.difficulty_label} · ${g.persona_label}`
    this.metaPills.innerHTML = ''
    const pills = [
      el('span', { class: `pill pill-${status.tone}` }, status.text),
      el('span', { class: 'pill pill-muted' }, g.rule_label || ''),
      el('span', { class: 'pill pill-muted' }, oppoLabel),
    ]
    if (g.step) pills.push(el('span', { class: 'pill pill-accent' }, '逐步确认'))
    pills.push(el('span', { class: 'pill pill-muted' }, `第 ${(g.moves || []).length} 手`))
    this.metaPills.append(...pills)

    this.btnUndo.disabled = this.busy || g.status !== 'playing' || !(g.moves || []).length
    this.btnHint.disabled = !this.canAct
    this.btnReview.disabled = this.busy

    this.overlay.hidden = !this.busy
    this.overlay.querySelector('p').textContent = this.busy ? '搜索中…' : ''

    const board = g.board || new Array(225).fill(0)
    const predictPoints = this.predictOn ? (this.insight && this.insight.prediction && this.insight.prediction.points) || [] : []
    this.board.update({
      board,
      lastMove: g.last_move,
      winLine: g.win_line || [],
      humanColor: g.human_color ?? 1,
      interactive: this.canAct,
      hintPoint: this.hintPoint,
      banned: g.banned,
      heat: this.heat,
      predict: predictPoints,
      ghost: g.pending ? { x: g.pending.x, y: g.pending.y, color: g.pending.color } : null,
      focus: this.focus,
    })

    this.captionMain.textContent = g.status === 'finished' ? '对局结束' : g.pending ? '已计算完成，等待确认' : '点击交叉点落子'
    this.captionSide.innerHTML = ''
    if (g.status === 'finished' && g.result_reason) {
      this.captionSide.append(el('span', { class: `result-note ${g.ban_loss ? 'is-ban' : ''}` }, g.result_reason))
    } else if (this.hintPoint) {
      this.captionSide.append(el('span', { class: 'hint-note' }, `建议 ${this.hintPoint.x},${this.hintPoint.y}`))
    }

    this.btnPredict.textContent = this.predictOn ? '收起落点预测' : '落点预测'
    this.btnPredict.disabled = g.status !== 'playing'
    this.btnHeat.textContent = this.heatBusy ? '扫描中…' : this.heat ? '收起网络视角' : '网络视角'
    this.btnHeat.classList.toggle('is-on', Boolean(this.heat))

    const predictTop = predictPoints.slice(0, 5)
    this.legendPredict.hidden = !(this.predictOn && predictTop.length > 0)
    if (!this.legendPredict.hidden) {
      this.legendPredict.innerHTML = ''
      this.legendPredict.append(
        document.createTextNode(
          (this.insight.prediction.side === g.human_color ? '你的可能落点：' : '对手可能落点：'),
        ),
        ...predictTop.map((p) =>
          el('b', { title: p.why }, `${p.x},${p.y}`, el('i', {}, String(Math.round(p.share * 100)))),
        ),
      )
    }

    const heatTop = (this.heat || []).slice(0, 5)
    this.legendHeat.hidden = !(this.heat && heatTop.length > 0)
    if (!this.legendHeat.hidden) {
      this.legendHeat.innerHTML = ''
      this.legendHeat.append(
        document.createTextNode('网络最看好的五个点：'),
        ...heatTop.map((p) => el('b', {}, `${p.x},${p.y}`, el('i', {}, String(Math.round(p.score * 100))))),
      )
    }

    // 页签内容
    this.renderNote()
    if (this.tab === 'analysis') this.renderAnalysis()
    if (this.tab === 'moves') this.renderFeed()
    window.__gomokuInsight = this.insight
  }

  renderNote() {
    const g = this.game
    this.reviewSlot.hidden = !this.review
    this.reviewSlot.innerHTML = ''
    if (this.review) {
      this.reviewSlot.append(
        el(
          'div',
          { class: 'banner banner-review' },
          el('b', {}, '复盘'),
          el('p', {}, this.review),
          el('button', { class: 'banner-close', type: 'button', onclick: () => { this.review = ''; this.renderNote() } }, '收起'),
        ),
      )
    }

    this.coachSlot.innerHTML = ''
    if (g.mode === 'learn') this.coachSlot.append(this.buildCoach())

    this.explainSlot.innerHTML = ''
    this.explainSlot.append(this.buildExplain())
  }

  buildCoach() {
    const panel = el('section', { class: 'panel coach' },
      el('div', { class: 'panel-head' }, el('h2', {}, '教练'), el('span', { class: 'panel-note' }, '基于可验证的棋形事实判断')))

    if (this.coach) {
      const c = this.coach
      panel.append(
        el(
          'div',
          { class: `coach-card coach-${c.grade}` },
          el('div', { class: 'coach-head' },
            el('span', { class: `coach-grade coach-grade-${c.grade}` }, GRADE_LABEL[c.grade] || c.grade),
            el('b', {}, c.title)),
          el('p', {}, c.detail),
          el('div', { class: 'coach-facts' },
            el('span', {}, `本手形成：${(c.facts && c.facts.attack) || '-'}`),
            el('span', {}, `对手在此：${(c.facts && c.facts.defence) || '-'}`),
            el('span', {}, `对手威胁点：${(c.facts && c.facts.threats) || 0}`)),
        ),
      )
    } else {
      panel.append(el('p', { class: 'empty-note' }, '落子后显示点评。'))
    }

    if (this.tactics) {
      const t = this.tactics
      const list = (items) =>
        items && items.length
          ? el('ul', {}, items.map((it) => el('li', {}, `(${it.x},${it.y}) ${it.shape}`)))
          : el('p', { class: 'dim' }, '暂无')
      panel.append(
        el(
          'div',
          { class: 'coach-tactics' },
          el('div', {}, el('span', { class: 'coach-label' }, '我方活三及以上'), list(t.my_threats)),
          el('div', {}, el('span', { class: 'coach-label' }, '对手活三及以上'), list(t.opp_threats)),
        ),
      )
    }

    if (this.agent && this.agent.comment) {
      panel.append(
        el('div', { class: 'coach-agent' }, el('span', { class: 'coach-label' }, 'Agent 的说明'), el('p', {}, this.agent.comment)),
      )
    }
    return panel
  }

  buildExplain() {
    const g = this.game
    const data = g.pending ? this.pendingPreview : this.explain
    if (!data) {
      return el(
        'section',
        { class: 'panel panel-explain is-empty' },
        el('div', { class: 'panel-head' }, el('h2', {}, '本手解说'), el('span', { class: 'panel-note' }, '点击棋谱中任意一手')),
        el('p', { class: 'empty-note' }, '每落一手都会自动解说。棋谱里点某一手，也能看那一手当时为什么那么走。'),
      )
    }

    const v = VERDICT[data.verdict] || VERDICT.neutral
    const side = data.color === 1 ? '黑' : '白'
    const before = data.winrate.before * 100
    const after = data.winrate.after * 100
    const delta = data.winrate.delta * 100

    const card = el('section', { class: `panel panel-explain tone-${v.tone}` })
    card.append(
      el('div', { class: 'panel-head' },
        el('h2', {}, g.pending ? '预计算落子' : `第 ${(data.index ?? 0) + 1} 手`),
        el('span', { class: 'panel-note' }, `${side}棋 ${data.x},${data.y}${data.think_ms > 0 ? ` · ${data.think_ms}ms` : ''}`)),
      el('div', { class: 'ex-head' },
        el('span', { class: `ex-verdict tone-${v.tone}` }, v.label),
        el('b', {}, data.headline),
        !g.pending ? el('button', { class: 'ex-close', type: 'button', 'aria-label': '收起解说', onclick: () => { this.explain = null; this.renderNote() } }, '×') : null),
      el('p', { class: 'ex-narrative' }, data.narrative),
      el('ul', { class: 'ex-facts' },
        (data.facts || []).map((f) =>
          el('li', { className: `kind-${f.kind}` }, el('span', { class: 'ex-kind' }, KIND_LABEL[f.kind] || f.kind), el('span', {}, f.text)))),
      el(
        'div',
        { class: 'ex-rate' },
        el('div', { class: 'ex-rate-head' }, el('span', {}, '本手前后'),
          el('b', { class: delta >= 0 ? 'up' : 'down' }, `${delta >= 0 ? '+' : ''}${delta.toFixed(1)} 点`)),
        el('div', { class: 'ex-rate-track' },
          el('i', { class: 'ex-mark before', style: `left:${Math.min(99, Math.max(1, before))}%` }),
          el('i', { class: 'ex-mark after', style: `left:${Math.min(99, Math.max(1, after))}%` })),
        el('div', { class: 'ex-rate-foot' }, el('span', {}, `${before.toFixed(1)}%`), el('span', {}, `${after.toFixed(1)}%`)),
      ),
    )

    if (data.alternatives && data.alternatives.length > 0) {
      card.append(
        el(
          'div',
          { class: 'ex-alts' },
          el('div', { class: 'ex-alts-head' }, '当时的其他候选'),
          el('ul', {},
            data.alternatives.map((a) => el('li', {}, el('span', { class: 'coord' }, `${a.x},${a.y}`), el('span', {}, a.why)))),
        ),
      )
    }

    if (g.pending) {
      card.append(
        el('button', {
          class: 'btn btn-primary ex-resume',
          type: 'button',
          disabled: this.busy,
          onclick: () => this.resume(),
        }, this.busy ? '落子中…' : '确认，按此落子'),
      )
    }
    return card
  }

  /* ------------------------------------------------------ 分析页签 */

  renderAnalysis() {
    this.renderChart()
    this.renderInsightPanel()
  }

  renderChart() {
    const series = (this.insight && this.insight.series) || []
    const holder = this.chartSlot
    holder.innerHTML = ''
    if (!series.length) {
      holder.append(
        el('div', { class: 'chart-empty' }, el('span', {}, '胜率曲线'), el('p', {}, '落子后显示逐手胜率')),
      )
      return
    }

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
      if (d > swingDelta) {
        swingDelta = d
        swing = i
      }
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
      el(
        'div',
        { class: `chart wr-chart ${ahead ? 'chart-ahead' : 'chart-behind'}` },
        el('div', { class: 'chart-head' }, el('span', {}, '胜率曲线'),
          el('span', { class: `chart-verdict ${ahead ? 'ahead' : 'behind'}` }, `${(last.p * 100).toFixed(1)}%`)),
        svg,
        el('div', { class: 'chart-foot' },
          el('span', {}, `第 1 手 · ${(pts[0].p * 100).toFixed(0)}%`),
          swing > 0 ? el('span', { class: 'wr-swing-note' }, `转折 第 ${pts[swing].row.ply} 手 ${(swingDelta * 100).toFixed(0)} 点`) : null,
          el('span', {}, `第 ${series.length} 手`)),
        swing > 0
          ? el('button', { class: 'wr-jump', type: 'button', onclick: () => this.explainPly(swing) }, `看第 ${pts[swing].row.ply} 手怎么走的`)
          : null,
      ),
    )
  }

  renderInsightPanel() {
    const insight = this.insight
    const holder = this.analysisSlot
    holder.innerHTML = ''

    const panel = el('section', { class: 'panel panel-analysis' })
    panel.append(
      el('div', { class: 'panel-head' },
        el('h2', {}, '局面分析'),
        insight ? el('span', { class: 'panel-note' }, `${insight.elapsed_ms}ms · 第 ${(insight.series || []).length} 手`) : null),
    )

    const tabs = el('div', { class: 'an-tabs' })
    const TAB_KEYS = [
      { key: 'rate', label: '胜率' },
      { key: 'predict', label: '预测' },
      { key: 'data', label: '数据' },
    ]
    const body = el('div', { class: 'an-body' })
    const renderTab = () => {
      $$('.an-tab', tabs).forEach((b) => b.classList.toggle('is-active', b.dataset.an === this.anTab))
      body.innerHTML = ''
      if (!insight) {
        body.append(el('p', { class: 'empty-note' }, '等待局面数据…'))
        return
      }
      if (this.anTab === 'rate') this.buildRateTab(insight, body)
      else if (this.anTab === 'predict') this.buildPredictTab(insight, body)
      else this.buildDataTab(insight, body)
    }
    TAB_KEYS.forEach((t) =>
      tabs.append(el('button', { class: 'an-tab', type: 'button', dataset: { an: t.key }, onclick: () => { this.anTab = t.key; renderTab() } }, t.label)),
    )
    panel.append(tabs, body)
    holder.append(panel)
    renderTab()
  }

  buildRateTab(insight, body) {
    const win = insight.winrate
    if (!win) {
      body.append(el('p', { class: 'empty-note' }, '胜率数据还没有就绪。'))
      return
    }
    const p = win.human ?? 0.5
    const pct = (p * 100).toFixed(1)
    const ahead = p >= 0.5

    body.append(
      el('div', { class: `rate-hero ${ahead ? 'is-ahead' : 'is-behind'}` },
        el('b', {}, pct, el('i', { style: 'font-style:normal;font-size:0.6em' }, '%')),
        el('span', {}, '你的胜率',
          el('em', {}, `${SOURCE_LABEL[win.source] || win.source} · 把握${CONFIDENCE_LABEL[win.confidence] || win.confidence}`))),
      el('div', { class: 'rate-bar', role: 'img', 'aria-label': `你 ${pct}%` },
        el('span', { class: 'rate-mine', style: `width:${pct}%` }),
        el('span', { class: 'rate-theirs' })),
      el('p', { class: 'rate-reason' }, win.reason),
      el('ul', { class: 'facts' },
        (win.facts || []).map((f) => {
          const total = (f.mine || 0) + (f.theirs || 0)
          const left = total > 0 ? (f.mine / total) * 100 : 50
          return el('li', {},
            el('div', { class: 'fact-head' }, el('b', {}, (f.mine || 0).toLocaleString()), el('span', {}, f.label), el('b', {}, (f.theirs || 0).toLocaleString())),
            el('div', { class: 'fact-track' }, el('span', { class: 'fact-mine', style: `width:${left.toFixed(1)}%` })))
        })),
      el('p', { class: 'an-foot' },
        '左右两侧是黑棋和白棋的量，中间那条线是两者相等的位置。',
        win.confidence_note ? ` ${win.confidence_note}。` : ''),
    )
  }

  buildPredictTab(insight, body) {
    const pred = insight.prediction
    body.append(
      el('p', { class: 'pred-head' },
        insight.status === 'playing'
          ? `${pred && pred.side === 1 ? '黑棋' : '白棋'}下一步最可能落在这几个点。份额表示候选池内的相对倾向，不是统计概率，准确度见下方命中率。`
          : '对局已结束，没有待预测的一手。'),
    )

    const points = (pred && pred.points) || []
    if (points.length) {
      const list = el('ol', { class: 'pred-list' })
      points.forEach((pt, i) => {
        list.append(
          el('li', {},
            el('button', { class: `pred-row ${i === 0 ? 'is-top' : ''}`, type: 'button', title: pt.why,
              onclick: () => {
                this.focus = { x: pt.x, y: pt.y }
                this.predictOn = true
                this.renderAll()
              } },
              el('span', { class: 'pred-rank' }, String(i + 1)),
              el('span', { class: 'pred-coord' }, `${pt.x},${pt.y}`),
              el('span', { class: 'pred-bar' }, el('i', { style: `width:${Math.max(2, pt.share * 100)}%` })),
              el('span', { class: 'pred-share' }, (pt.share * 100).toFixed(1))),
            el('p', { class: 'pred-why' }, pt.why)),
        )
      })
      body.append(list)
    } else {
      body.append(el('p', { class: 'empty-note' }, '无可落子点。'))
    }

    const model = insight.model
    if (model && model.predict && model.predict.top3 != null) {
      body.append(
        el('p', { class: 'an-foot' },
          `在 ${model.predict.eval_positions ?? 40} 个中盘局面上，以搜索实际选点为标准：首选命中 ${(model.predict.top1 * 100).toFixed(0)}%，前三命中 ${(model.predict.top3 * 100).toFixed(0)}%。`,
          model.predict.prior_weight > 0
            ? ` 叠加网络先验（权重 ${model.predict.prior_weight}）之后取表现最好的那一组参数。`
            : ' 叠加网络先验未在该指标上产生差异，权重取 0：排序仅使用棋形分，网络自身的判断在「网络视角」中单独查看。'),
      )
    }
  }

  buildDataTab(insight, body) {
    const census = insight.census
    const model = insight.model
    if (!census) {
      body.append(el('p', { class: 'empty-note' }, '盘面数据还没有就绪。'))
      return
    }

    body.append(
      el('div', { class: 'data-block' },
        el('div', { class: 'data-title' }, el('span', {}, '棋形分布'), el('em', {}, '每个空点若被该色占据，能形成的最强棋形')),
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
          })),
        el('div', { class: 'legend' },
          el('span', {}, el('i', { class: 'lg ln-black' }), '黑'),
          el('span', {}, el('i', { class: 'lg ln-white' }), '白'))),
      el('div', { class: 'data-block' },
        el('div', { class: 'data-title' }, el('span', {}, '可数项'), el('em', {}, '都能在棋盘上数出来')),
        el('dl', { class: 'data-grid' },
          el('div', {}, el('dt', {}, '棋子'), el('dd', {}, `${census.stones.black} / ${census.stones.white}`)),
          el('div', {}, el('dt', {}, '棋形总量'), el('dd', {}, `${census.material.black.toLocaleString()} / ${census.material.white.toLocaleString()}`)),
          el('div', {}, el('dt', {}, '冲四点'), el('dd', {}, `${census.threats.black.fours + census.threats.black.open_fours} / ${census.threats.white.fours + census.threats.white.open_fours}`)),
          el('div', {}, el('dt', {}, '活三点'), el('dd', {}, `${census.threats.black.threes} / ${census.threats.white.threes}`)),
          el('div', {}, el('dt', {}, '重心'), el('dd', {}, `${census.centroid.black ? census.centroid.black.map((v) => v.toFixed(1)).join(',') : '—'} / ${census.centroid.white ? census.centroid.white.map((v) => v.toFixed(1)).join(',') : '—'}`)),
          el('div', {}, el('dt', {}, '盘面'), el('dd', {}, `${(census.occupancy * 100).toFixed(1)}%`)))),
    )

    if (model && model.buckets && model.buckets.length) {
      body.append(
        el('div', { class: 'data-block' },
          el('div', { class: 'data-title' }, el('span', {}, '模型可靠性'),
            el('em', {}, `${model.games ? `${model.games} 局自对弈标定` : '手写兜底权重'} · AUC ${model.auc ?? '—'}`)),
          el('div', { class: 'rel-table' },
            el('div', { class: 'rel-head' }, el('span', {}, '预测区间'), el('span', {}, '样本'), el('span', {}, '实际')),
            model.buckets.map((b) =>
              el('div', { class: 'rel-row' },
                el('span', {}, b.range),
                el('span', {}, String(b.n)),
                el('span', {}, el('i', { style: `width:${Math.min(100, b.actual * 100)}%` }), `${(b.actual * 100).toFixed(1)}%`)))),
          el('p', { class: 'an-foot' },
            '每档的实际胜率贴着预测走，才说明这个数字可信。偏差最大的一档相差',
            `${Math.max(...model.buckets.map((b) => Math.abs(b.actual - b.predicted) * 100)).toFixed(1)} 个百分点。`)),
      )
    }
  }

  /* ------------------------------------------------------ 棋谱页签 */

  renderFeed() {
    const g = this.game
    const holder = this.feedSlot
    holder.innerHTML = ''
    const panel = el('section', { class: 'panel' },
      el('div', { class: 'panel-head' }, el('h2', {}, '全部落子'), el('span', { class: 'panel-note' }, '点击任意一手看当时怎么想的')))
    const feed = el('div', { class: 'game-feed' })
    const moves = g.moves || []
    moves.forEach((move, i) => {
      feed.append(
        el('button', {
          class: `game-row as-button ${this.explain && this.explain.index === i ? 'is-active' : ''}`,
          type: 'button',
          onclick: () => this.explainPly(i),
        },
          el('span', { class: 'feed-idx' }, String(i + 1)),
          el('span', { className: `dot dot-${move.color === 1 ? 'black' : 'white'}` }),
          el('span', { class: 'feed-pos' }, `${String.fromCharCode(65 + move.x)}${15 - move.y}`),
          el('span', { class: 'feed-ms' }, move.comment || '')),
      )
    })
    if (!moves.length) feed.append(el('p', { class: 'empty-note' }, '本局还没有落子。'))
    panel.append(feed)
    holder.append(panel)
  }

  /* ---------------------------------------------------------- 数据流 */

  async load() {
    this.main.innerHTML = ''
    this.main.append(spinner('正在读取对局…'))
    try {
      const res = await api.replay(this.gameId)
      this.main.innerHTML = ''
      this.build()
      this.applyGame(res.game)
      if (res.game.step && res.game.moves && res.game.moves.length && !res.game.pending) {
        api.explain(this.gameId).then((r) => {
          this.explain = r.explain
          this.focus = { x: r.explain.x, y: r.explain.y }
          this.renderNote()
        }).catch(() => {})
      }
      this.fetchInsight()
    } catch (e) {
      this.main.innerHTML = ''
      this.main.append(
        el('div', { class: 'notfound' },
          el('h2', { style: 'font-size:18px;margin-bottom:8px' }, '对局不存在'),
          el('p', { class: 'dim', style: 'margin-bottom:14px' }, e.message || '这个对局 id 没有对应的记录，可能已经被清理。'),
          el('button', { class: 'btn btn-primary', type: 'button', onclick: () => (window.location.href = 'play.html') }, '回到开局准备')),
      )
    }
  }

  applyGame(g) {
    this.game = g
    if (g.status === 'finished' && this.settled !== g.id) {
      this.settled = g.id
      if (this.refreshUser) this.refreshUser()
    }
    const ply = (g.moves || []).length
    if (ply !== this.lastPly || g.status !== this.lastStatus) {
      this.lastPly = ply
      this.lastStatus = g.status
      this.fetchInsight()
    }
    if (g.pending) this.fetchPending()
    else this.pendingPreview = null
    this.renderAll()
  }

  fetchInsight() {
    if (!this.game || !this.game.id) return
    const seq = ++this.insightSeq
    api.insight(this.game.id)
      .then((data) => {
        if (seq === this.insightSeq) {
          this.insight = data
          if (this.tab === 'analysis') this.renderAnalysis()
          this.renderAll()
        }
      })
      .catch(() => {
        /* 分析失败不影响下棋 */
      })
  }

  fetchPending() {
    if (!this.game || !this.game.pending) return
    api.pending(this.game.id)
      .then((res) => {
        this.pendingPreview = res.preview
        this.renderNote()
      })
      .catch(() => {
        this.pendingPreview = null
      })
  }

  applyResponse(res) {
    this.applyGame(res.game)
    if (res.agent) this.agent = res.agent
    if ('coach' in res) this.coach = res.coach
    if (res.tactics) this.tactics = res.tactics
    if (res.explain) {
      this.explain = res.explain
      this.focus = { x: res.explain.x, y: res.explain.y }
    }
    this.renderAll()
  }

  /* ---------------------------------------------------------- 动作 */

  async play(x, y) {
    if (!this.canAct) return
    this.busy = true
    this.hintPoint = null
    this.showError('')
    this.renderAll()
    try {
      this.applyResponse(await api.move(this.game.id, x, y))
    } catch (e) {
      this.showError(e.message)
    } finally {
      this.busy = false
      this.renderAll()
    }
  }

  async resume() {
    if (!this.game || !this.game.pending || this.busy) return
    this.busy = true
    this.showError('')
    this.renderAll()
    try {
      const res = await api.resume(this.game.id)
      this.applyGame(res.game)
      if (res.agent) this.agent = res.agent
      if (res.explain) {
        this.explain = res.explain
        this.focus = { x: res.explain.x, y: res.explain.y }
      }
    } catch (e) {
      this.showError(e.message)
    } finally {
      this.busy = false
      this.renderAll()
    }
  }

  async undo() {
    if (!this.game || this.busy || this.game.status !== 'playing') return
    this.busy = true
    this.hintPoint = null
    this.coach = null
    this.explain = null
    this.focus = null
    this.settled = ''
    this.showError('')
    this.renderAll()
    try {
      const res = await api.undo(this.game.id)
      this.applyGame(res.game)
      this.agent = res.agent || null
    } catch (e) {
      this.showError(e.message)
    } finally {
      this.busy = false
      this.renderAll()
    }
  }

  async askHint() {
    if (!this.game || this.busy) return
    this.busy = true
    this.showError('')
    this.renderAll()
    try {
      const res = await api.hint(this.game.id)
      this.hintPoint = { x: res.x, y: res.y }
    } catch (e) {
      this.showError(e.message)
    } finally {
      this.busy = false
      this.renderAll()
    }
  }

  async askReview() {
    if (!this.game || this.busy) return
    this.busy = true
    this.showError('')
    this.renderAll()
    try {
      const res = await api.review(this.game.id)
      this.review = res.review
      this.tab = 'note'
      this.renderTabs()
    } catch (e) {
      this.showError(e.message)
    } finally {
      this.busy = false
      this.renderAll()
    }
  }

  async explainPly(index) {
    if (!this.game || this.explainBusy) return
    this.explainBusy = true
    this.showError('')
    try {
      const res = await api.explain(this.game.id, index)
      this.explain = res.explain
      this.focus = { x: res.explain.x, y: res.explain.y }
      this.tab = 'note'
      this.renderTabs()
      this.renderNote()
    } catch (e) {
      this.showError(e.message)
    } finally {
      this.explainBusy = false
    }
  }

  togglePredict() {
    this.predictOn = !this.predictOn
    if (!this.predictOn) this.heat = null
    this.renderAll()
  }

  async toggleHeat() {
    if (this.heat) {
      this.heat = null
      this.renderAll()
      return
    }
    if (!this.game || this.heatBusy) return
    this.heatBusy = true
    this.showError('')
    this.renderAll()
    try {
      const res = await api.neuralScan({
        board: this.game.board,
        color: this.game.status === 'finished' ? this.game.human_color : this.game.turn,
        rule: this.game.rule,
      })
      this.heat = res.ready ? res.points : []
      if (!res.ready) this.showError('网络权重未加载，请稍后重试')
      this.predictOn = false
    } catch (e) {
      this.showError(e.message)
    } finally {
      this.heatBusy = false
      this.renderAll()
    }
  }
}

/* ============================================================== 入口 */

;(async () => {
  window.Gomoku.landscapeHint()
  const { config } = await window.Gomoku.bootPage('play')

  const boot = window.Gomoku.$ // 占位避免未使用告警
  void boot

  // 工坊页面刷新用户信息（对局结束后战绩要跟着变）
  const refreshUser = async () => {
    try {
      const res = await window.Gomoku.api.me()
      if (res.user) window.__gomokuUser = res.user
    } catch {
      /* 统计刷新失败不拦截 */
    }
  }

  const gameId = new URLSearchParams(window.location.search).get('game')
  if (gameId) {
    new GameView(document.getElementById('main'), gameId, refreshUser).load()
  } else {
    renderSetup(config)
  }
})()
