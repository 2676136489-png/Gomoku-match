const RENJU_POINTS = [
  ['长连禁手', '黑棋连成六子及以上判负'],
  ['四四禁手', '一手同时形成两个「四」'],
  ['三三禁手', '一手同时形成两个「活三」，假活三不算'],
  ['五连优先', '成五同时撞上四四算赢；撞上长连算负'],
  ['白棋无禁手', '白棋长连也判胜'],
]

const FREE_POINTS = [
  ['无禁手', '黑白都可以随便连，没有限制点'],
  ['五子及以上即胜', '长连同样算赢'],
]

export default function RulesCard({ game }) {
  const renju = (game?.rule ?? 'renju') === 'renju'
  const points = renju ? RENJU_POINTS : FREE_POINTS
  const banned = game?.banned ?? []

  return (
    <section className="panel rules-card">
      <div className="panel-head">
        <h2>规则</h2>
        <span className="panel-note">{renju ? '国际连珠 Renju' : '自由规则 Freestyle'}</span>
      </div>

      <ul className="rule-list">
        {points.map(([title, desc]) => (
          <li key={title}>
            <b>{title}</b>
            <span>{desc}</span>
          </li>
        ))}
      </ul>

      {renju && game?.status === 'playing' && (
        <p className="rule-note">
          {game.turn !== 1
            ? '禁手点仅在黑棋回合计算并在棋盘上标注，白棋回合不标注。'
            : banned.length > 0
              ? `当前有 ${banned.length} 个禁手点，棋盘上标了红叉。`
              : '当前无禁手点。'}
          {game?.strict_ban ? ' 本局为赛制模式：落禁手点直接判负。' : ' 本局禁手点会被拦截，不判负。'}
        </p>
      )}
    </section>
  )
}
