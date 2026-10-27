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

export default function ExplainCard({ explain, pending = false, busy, onResume, onClose }) {
  if (!explain) {
    return (
      <section className="panel panel-explain is-empty">
        <div className="panel-head">
          <h2>本手解说</h2>
          <span className="panel-note">点击棋谱中任意一手</span>
        </div>
        <p className="feed-empty">
          每落一手都会自动解说。棋谱里点某一手，也能看那一手当时为什么那么走。
        </p>
      </section>
    )
  }

  const v = VERDICT[explain.verdict] ?? VERDICT.neutral
  const side = explain.color === 1 ? '黑' : '白'
  const before = explain.winrate.before * 100
  const after = explain.winrate.after * 100
  const delta = explain.winrate.delta * 100

  return (
    <section className={`panel panel-explain tone-${v.tone}`}>
      <div className="panel-head">
        <h2>{pending ? '预计算落子' : `第 ${(explain.index ?? 0) + 1} 手`}</h2>
        <span className="panel-note">
          {side}棋 {explain.x},{explain.y}
          {explain.think_ms > 0 ? ` · ${explain.think_ms}ms` : ''}
        </span>
      </div>

      <div className="ex-head">
        <span className={`ex-verdict tone-${v.tone}`}>{v.label}</span>
        <b>{explain.headline}</b>
        {onClose && (
          <button type="button" className="ex-close" onClick={onClose} aria-label="收起解说">
            ×
          </button>
        )}
      </div>

      <p className="ex-narrative">{explain.narrative}</p>

      <ul className="ex-facts">
        {explain.facts.map((f) => (
          <li key={`${f.kind}-${f.text}`} className={`kind-${f.kind}`}>
            <span className="ex-kind">{KIND_LABEL[f.kind] ?? f.kind}</span>
            <span>{f.text}</span>
          </li>
        ))}
      </ul>

      <div className="ex-rate">
        <div className="ex-rate-head">
          <span>本手前后</span>
          <b className={delta >= 0 ? 'up' : 'down'}>
            {delta >= 0 ? '+' : ''}
            {delta.toFixed(1)} 点
          </b>
        </div>
        <div className="ex-rate-track">
          <i className="ex-mark before" style={{ left: `${Math.min(99, Math.max(1, before))}%` }} />
          <i className="ex-mark after" style={{ left: `${Math.min(99, Math.max(1, after))}%` }} />
        </div>
        <div className="ex-rate-foot">
          <span>{before.toFixed(1)}%</span>
          <span>{after.toFixed(1)}%</span>
        </div>
      </div>

      {explain.alternatives?.length > 0 && (
        <div className="ex-alts">
          <div className="ex-alts-head">当时的其他候选</div>
          <ul>
            {explain.alternatives.map((a) => (
              <li key={`${a.x}-${a.y}`}>
                <span className="coord">
                  {a.x},{a.y}
                </span>
                <span>{a.why}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {pending && (
        <button type="button" className="btn btn-primary ex-resume" onClick={onResume} disabled={busy}>
          {busy ? '落子中…' : '确认，按此落子'}
        </button>
      )}
    </section>
  )
}
