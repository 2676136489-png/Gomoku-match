import { useMemo, useState } from 'react'
import FirstRunTip from '../components/FirstRunTip'
import { Term } from '../components/Glossary'
import { api } from '../lib/api'
import { navigate } from '../lib/router'

/**
 * 开局准备页。
 *
 * 这里只做一件事：把一局棋的参数定下来。对局中不再出现任何设置项，
 * 免得「改难度」和「悔棋」混在一个面板里让人分不清哪个会真的生效。
 */

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

export default function PlaySetup({ config, initialMode = 'agent' }) {
  const modes = useMemo(() => {
    const list = config?.modes?.length ? config.modes : FALLBACK_MODES
    return [...list].sort((a, b) => {
      const ia = MODE_ORDER.indexOf(a.key)
      const ib = MODE_ORDER.indexOf(b.key)
      return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib)
    })
  }, [config])

  const rules = config?.rules?.length ? config.rules : FALLBACK_RULES
  const levels = config?.difficulties ?? []
  const personas = config?.personas ?? []
  const llmReady = config?.llm?.enabled ?? false

  const [form, setForm] = useState({
    mode: initialMode,
    difficulty: 3,
    persona: 'balanced',
    human_color: 1,
    use_llm: true,
    rule: 'renju',
    strict_ban: false,
    step: false,
  })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const set = (patch) => setForm((prev) => ({ ...prev, ...patch }))

  const modeInfo = modes.find((m) => m.key === form.mode)
  const levelInfo = levels.find((l) => l.level === form.difficulty)
  const personaInfo = personas.find((p) => p.key === form.persona)

  const summary = [
    modeInfo?.label,
    levelInfo?.label,
    form.mode !== 'engine' ? personaInfo?.label : null,
    form.human_color === 1 ? '执黑' : '执白',
    rules.find((r) => r.key === form.rule)?.label,
  ]
    .filter(Boolean)
    .join(' · ')

  const start = async () => {
    setBusy(true)
    setError('')
    try {
      const res = await api.newGame(form)
      navigate(`/play/${res.game.id}`)
    } catch (e) {
      setError(e.message)
      setBusy(false)
    }
  }

  return (
    <div className="setup">
      <header className="setup-head">
        <h2>开始一局</h2>
      </header>

      <FirstRunTip
        id="setup"
        title="第一次来，按这个顺序走"
        steps={[
          '在第 1 步选一个对手，三种用法的区别写在卡片里。',
          '第 2 步定强度和风格，强度对应搜索深度，风格对应进攻倾向。',
          '点右下角「开始对局」进棋盘。对局中只剩悔棋、提示、复盘、新对局四个按钮，改设置要重开一局。',
        ]}
      />

      <section className="setup-step">
        <div className="setup-step-head">
          <b>1</b>
          <div>
            <h3>选择对手</h3>
          </div>
        </div>
        <div className="setup-cards">
          {modes.map((m) => (
            <button
              key={m.key}
              type="button"
              className={`setup-card ${form.mode === m.key ? 'is-active' : ''}`}
              onClick={() => set({ mode: m.key })}
            >
              <span className="setup-card-title">{m.label}</span>
              <span className="setup-card-desc">{m.brief}</span>
            </button>
          ))}
        </div>
      </section>

      <section className="setup-step">
        <div className="setup-step-head">
          <b>2</b>
          <div>
            <h3>强度与风格</h3>
          </div>
        </div>

        <div className="setup-field">
          <span className="setup-label">强度</span>
          <div className="chips chips-4">
            {levels.map((l) => (
              <button
                key={l.level}
                type="button"
                className={`chip ${form.difficulty === l.level ? 'is-active' : ''}`}
                data-level={l.level}
                onClick={() => set({ difficulty: l.level })}
              >
                {l.label}
                <em>
                  <Term k="depth">深度</Term> {l.depth}
                </em>
              </button>
            ))}
          </div>
        </div>

        {form.mode !== 'engine' && personas.length > 0 && (
          <div className="setup-field">
            <span className="setup-label">风格</span>
            <div className="chips">
              {personas.map((p) => (
                <button
                  key={p.key}
                  type="button"
                  className={`chip ${form.persona === p.key ? 'is-active' : ''}`}
                  data-persona={p.key}
                  title={p.brief}
                  onClick={() => set({ persona: p.key })}
                >
                  {p.label}
                </button>
              ))}
            </div>
          </div>
        )}
      </section>

      <section className="setup-step">
        <div className="setup-step-head">
          <b>3</b>
          <div>
            <h3>规则与执子</h3>
          </div>
        </div>

        <div className="setup-field">
          <span className="setup-label">规则</span>
          <div className="chips">
            {rules.map((r) => (
              <button
                key={r.key}
                type="button"
                className={`chip ${form.rule === r.key ? 'is-active' : ''}`}
                data-rule={r.key}
                title={r.brief}
                onClick={() => set({ rule: r.key })}
              >
                {r.label}
              </button>
            ))}
          </div>
        </div>

        {form.rule === 'renju' && (
          <label className="toggle">
            <input
              type="checkbox"
              data-setting="strict_ban"
              checked={Boolean(form.strict_ban)}
              onChange={(e) => set({ strict_ban: e.target.checked })}
            />
            <span>
              禁手判负
              <em>
                {form.strict_ban ? '正式赛制：黑棋落禁手点立即判负' : '默认拦下并说明原因，棋局继续'}
              </em>
            </span>
          </label>
        )}

        <div className="setup-field">
          <span className="setup-label">执子</span>
          <div className="chips">
            <button
              type="button"
              data-color="1"
              className={`chip ${form.human_color === 1 ? 'is-active' : ''}`}
              onClick={() => set({ human_color: 1 })}
            >
              执黑先行
            </button>
            <button
              type="button"
              data-color="2"
              className={`chip ${form.human_color === 2 ? 'is-active' : ''}`}
              onClick={() => set({ human_color: 2 })}
            >
              执白后行
            </button>
          </div>
        </div>
      </section>

      <details className="setup-more">
        <summary>高级选项（模型决策、逐步确认）</summary>
        <div className="setup-more-body">
          <label className={`toggle ${llmReady ? '' : 'is-disabled'}`}>
            <input
              type="checkbox"
              data-setting="use_llm"
              checked={Boolean(form.use_llm) && llmReady}
              disabled={!llmReady}
              onChange={(e) => set({ use_llm: e.target.checked })}
            />
            <span>
              用模型做决策
              <em>
                {llmReady
                  ? `候选点交给 ${config.llm.model} 取舍；不勾选则用内置人格权重`
                  : '未配置模型，使用内置策略人格'}
              </em>
            </span>
          </label>

          <label className="toggle">
            <input
              type="checkbox"
              data-setting="step"
              checked={Boolean(form.step)}
              onChange={(e) => set({ step: e.target.checked })}
            />
            <span>
              逐步确认
              <em>
                {form.step
                  ? 'Agent 计算完成后暂停，点「确认」再落子，便于逐步看懂它为什么这么走'
                  : 'Agent 计算完成即落子，节奏更快'}
              </em>
            </span>
          </label>
        </div>
      </details>

      {error && (
        <div className="banner banner-error" role="alert">
          {error}
        </div>
      )}

      <footer className="setup-foot">
        <p className="setup-summary">
          <span className="setup-summary-label">当前</span>
          {summary}
        </p>
        <button type="button" className="btn btn-primary btn-lg" onClick={start} disabled={busy}>
          {busy ? '正在开局…' : '开始对局'}
        </button>
      </footer>
    </div>
  )
}
