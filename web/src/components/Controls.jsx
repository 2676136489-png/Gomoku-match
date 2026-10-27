const MODES = [
  { key: 'agent', label: 'AI 对弈', hint: '搜索引擎出候选，Agent 做取舍' },
  { key: 'learn', label: '学习', hint: '每手给出教练点评' },
  { key: 'engine', label: '纯引擎', hint: '关掉 Agent，只看算力' },
  { key: 'pvp', label: '双人', hint: '同一块屏幕轮流落子' },
]

const COLORS = [
  { key: 1, label: '执黑先行' },
  { key: 2, label: '执白后行' },
]

const FALLBACK_RULES = [
  { key: 'renju', label: '国际连珠', brief: '黑棋禁手：长连、四四、三三' },
  { key: 'freestyle', label: '自由规则', brief: '无禁手，五子及以上即胜' },
]

export default function Controls({ config, settings, onChange, onNew, onUndo, onHint, onReview, busy, canAct }) {
  const personas = config?.personas ?? []
  const levels = config?.difficulties ?? []
  const ruleModes = config?.rules ?? FALLBACK_RULES
  const llmReady = config?.llm?.enabled ?? false

  const set = (patch) => onChange({ ...settings, ...patch })

  return (
    <section className="panel">
      <div className="panel-head">
        <h2>对局设置</h2>
        <span className="panel-note">改设置后需开新局</span>
      </div>

      <div className="seg-group">
        {MODES.map((m) => (
          <button
            key={m.key}
            type="button"
            className={`seg ${settings.mode === m.key ? 'is-active' : ''}`}
            onClick={() => set({ mode: m.key })}
            title={m.hint}
          >
            {m.label}
          </button>
        ))}
      </div>

      <label className="field">
        <span>规则</span>
        <div className="chips">
          {ruleModes.map((r) => (
            <button
              key={r.key}
              type="button"
              className={`chip ${settings.rule === r.key ? 'is-active' : ''}`}
              onClick={() => set({ rule: r.key })}
              title={r.brief}
            >
              {r.label}
            </button>
          ))}
        </div>
      </label>

      {settings.rule === 'renju' && (
        <label className="toggle">
          <input
            type="checkbox"
            checked={Boolean(settings.strict_ban)}
            onChange={(e) => set({ strict_ban: e.target.checked })}
          />
          <span>
            禁手判负
            <em>
              {settings.strict_ban
                ? '正式赛制：黑棋落禁手点立即判负'
                : '默认拦下并说明原因，棋局继续'}
            </em>
          </span>
        </label>
      )}

      {settings.mode !== 'pvp' && (
        <>
          <label className="field">
            <span>强度</span>
            <div className="chips chips-4">
              {levels.map((l) => (
                <button
                  key={l.level}
                  type="button"
                  className={`chip ${settings.difficulty === l.level ? 'is-active' : ''}`}
                  onClick={() => set({ difficulty: l.level })}
                >
                  {l.label}
                  <em>深度 {l.depth}</em>
                </button>
              ))}
            </div>
          </label>

          <label className="field">
            <span>风格</span>
            <div className="chips">
              {personas.map((p) => (
                <button
                  key={p.key}
                  type="button"
                  className={`chip ${settings.persona === p.key ? 'is-active' : ''}`}
                  onClick={() => set({ persona: p.key })}
                  title={p.brief}
                >
                  {p.label}
                </button>
              ))}
            </div>
          </label>
        </>
      )}

      <label className="field">
        <span>你执</span>
        <div className="chips">
          {COLORS.map((c) => (
            <button
              key={c.key}
              type="button"
              className={`chip ${settings.human_color === c.key ? 'is-active' : ''}`}
              data-color={c.key}
              onClick={() => set({ human_color: c.key })}
            >
              {c.label}
            </button>
          ))}
        </div>
      </label>

      {(settings.mode === 'agent' || settings.mode === 'learn') && (
        <label className={`toggle ${llmReady ? '' : 'is-disabled'}`}>
          <input
            type="checkbox"
            checked={settings.use_llm && llmReady}
            disabled={!llmReady}
            onChange={(e) => set({ use_llm: e.target.checked })}
          />
          <span>
            用模型做决策
            <em>
              {llmReady
                ? `候选点交给 ${config.llm.model} 取舍`
                : '未配置 API Key，使用内置策略人格'}
            </em>
          </span>
        </label>
      )}

      <label className={`toggle ${settings.mode === 'pvp' ? 'is-disabled' : ''}`}>
        <input
          type="checkbox"
          data-setting="step"
          checked={Boolean(settings.step) && settings.mode !== 'pvp'}
          disabled={settings.mode === 'pvp'}
          onChange={(e) => set({ step: e.target.checked })}
        />
        <span>
          讲解模式
          <em>
            {settings.step && settings.mode !== 'pvp'
              ? 'Agent 计算完成后暂停，点击「确认」再落子'
              : '关闭后 Agent 计算完成即落子，不再逐步确认'}
          </em>
        </span>
      </label>

      <div className="actions">
        <button type="button" className="btn btn-primary" onClick={onNew} disabled={busy}>
          开始新对局
        </button>
        <button type="button" className="btn" onClick={onUndo} disabled={busy || !canAct}>
          悔棋
        </button>
        <button type="button" className="btn" onClick={onHint} disabled={busy || !canAct}>
          提示
        </button>
        <button type="button" className="btn" onClick={onReview} disabled={busy}>
          复盘
        </button>
      </div>
    </section>
  )
}
