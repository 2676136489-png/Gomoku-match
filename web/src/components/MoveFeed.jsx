const SOURCE_LABEL = {
  llm: '模型决策',
  persona: '策略人格',
  'persona-fallback': '引擎兜底',
  fallback: '兜底',
}

export default function MoveFeed({ moves, agent, onPick, activeIndex }) {
  const total = moves?.length ?? 0
  const feed = [...(moves ?? [])].reverse()

  return (
    <section className="panel panel-feed">
      <div className="panel-head">
        <h2>棋谱</h2>
        {agent && (
          <span className="panel-note">
            {SOURCE_LABEL[agent.source] ?? agent.source} · {agent.think_ms}ms · 深度 {agent.depth}
          </span>
        )}
      </div>

      {agent?.candidates?.length > 0 && (
        <div className="thinking">
          <div className="thinking-head">候选评估</div>
          <ul>
            {agent.candidates.slice(0, 5).map((c) => (
              <li key={`${c.x}-${c.y}`} className={c.x === agent.x && c.y === agent.y ? 'is-picked' : ''}>
                <span className="coord">
                  {c.x},{c.y}
                </span>
                <span className="tag tag-attack">攻 {c.attack}</span>
                <span className="tag tag-defence">防 {c.defence}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {feed.length === 0 ? (
        <p className="feed-empty">还没有落子。</p>
      ) : (
        <ol className="feed">
          {feed.map((m, i) => {
            const index = total - 1 - i
            const active = activeIndex === index
            return (
              <li key={`${m.x}-${m.y}-${index}`}>
                <button
                  type="button"
                  className={`feed-row ${active ? 'is-active' : ''}`}
                  onClick={() => onPick?.(index)}
                  title="看这一手为什么这么走"
                >
                  <span className={`dot ${m.color === 1 ? 'dot-black' : 'dot-white'}`} />
                  <div className="feed-body">
                    <div className="feed-top">
                      <b>{index + 1}</b>
                      <span className="coord">
                        {m.x}, {m.y}
                      </span>
                      <span>{m.color === 1 ? '黑' : '白'}</span>
                      {m.ban && <span className="tag tag-ban">禁手</span>}
                      {m.think_ms > 0 && <span className="feed-meta">{m.think_ms}ms</span>}
                    </div>
                    {m.comment && <p>{m.comment}</p>}
                  </div>
                </button>
              </li>
            )
          })}
        </ol>
      )}
    </section>
  )
}
