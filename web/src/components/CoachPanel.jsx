const GRADE_LABEL = {
  win: '胜势',
  good: '好手',
  warn: '漏防',
  neutral: '布局',
}

/**
 * 教练面板。
 *
 * 只报可验证的事实，不给「这手棋 8.5 分」这种编出来的评分——
 * 在没有职业棋力的前提下，任何精细评分数值都是假精确。
 */
export default function CoachPanel({ coach, tactics, agent }) {
  return (
    <section className="panel coach">
      <div className="panel-head">
        <h2>教练</h2>
        <span className="panel-note">按可验证的棋形事实给判断</span>
      </div>

      {!coach && <p className="empty-note">落一手，这里会给出点评。</p>}

      {coach && (
        <div className={`coach-card coach-${coach.grade}`}>
          <div className="coach-head">
            <span className={`coach-grade coach-grade-${coach.grade}`}>
              {GRADE_LABEL[coach.grade] ?? coach.grade}
            </span>
            <b>{coach.title}</b>
          </div>
          <p>{coach.detail}</p>
          <div className="coach-facts">
            <span>你这一手：{coach.facts?.attack ?? '-'}</span>
            <span>对手占此处：{coach.facts?.defence ?? '-'}</span>
            <span>对手威胁点：{coach.facts?.threats ?? 0}</span>
          </div>
        </div>
      )}

      {tactics && (
        <div className="coach-tactics">
          <div>
            <span className="coach-label">你的活三以上</span>
            {tactics.my_threats?.length ? (
              <ul>
                {tactics.my_threats.map((t) => (
                  <li key={`m-${t.x}-${t.y}`}>
                    ({t.x},{t.y}) {t.shape}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="dim">还没有</p>
            )}
          </div>
          <div>
            <span className="coach-label">对手的活三以上</span>
            {tactics.opp_threats?.length ? (
              <ul>
                {tactics.opp_threats.map((t) => (
                  <li key={`o-${t.x}-${t.y}`} className="warn">
                    ({t.x},{t.y}) {t.shape}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="dim">还没有</p>
            )}
          </div>
        </div>
      )}

      {agent?.comment && (
        <div className="coach-agent">
          <span className="coach-label">Agent 的说明</span>
          <p>{agent.comment}</p>
        </div>
      )}
    </section>
  )
}
