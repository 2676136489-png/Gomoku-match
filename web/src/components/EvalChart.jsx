const W = 300
const H = 90
const COMPRESS = 6000

function compress(value) {
  // 评估分动辄上百万，直接画会被一两手杀棋拉成直线。
  // tanh 压一下，均势附近放大、胜势饱和，读起来才像人看的曲线。
  const t = Math.tanh(value / COMPRESS)
  return 0.5 - 0.44 * t
}

export default function EvalChart({ moves, humanColor }) {
  if (!moves || moves.length === 0) {
    return (
      <div className="chart-empty">
        <span>局势曲线</span>
        <p>落子后显示双方的形势起伏</p>
      </div>
    )
  }

  const sign = humanColor === 2 ? -1 : 1
  const points = moves.map((m, i) => {
    const x = moves.length === 1 ? W / 2 : (i / (moves.length - 1)) * W
    const y = compress((m.eval || 0) * sign) * H
    return [x, y]
  })

  const line = points.map(([x, y], i) => `${i === 0 ? 'M' : 'L'}${x.toFixed(1)},${y.toFixed(1)}`).join(' ')
  const area = `${line} L${W},${H} L0,${H} Z`
  const last = points[points.length - 1]
  const ahead = (moves[moves.length - 1].eval || 0) * sign > 0
  const tone = ahead ? 'ahead' : 'behind'

  return (
    <div className={`chart chart-${tone}`}>
      <div className="chart-head">
        <span>局势曲线</span>
        <span className={`chart-verdict ${tone}`}>{ahead ? '你占优' : '对手占优'}</span>
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" className="chart-svg">
        <defs>
          <linearGradient id="evalFill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="currentColor" stopOpacity="0.34" />
            <stop offset="100%" stopColor="currentColor" stopOpacity="0" />
          </linearGradient>
        </defs>
        <line x1="0" y1={H / 2} x2={W} y2={H / 2} className="chart-axis" />
        <path d={area} fill="url(#evalFill)" stroke="none" />
        <path d={line} className="chart-line" fill="none" />
        {last && <circle cx={last[0]} cy={last[1]} r="3" className="chart-dot" />}
      </svg>
      <div className="chart-foot">
        <span>第 1 手</span>
        <span>第 {moves.length} 手</span>
      </div>
    </div>
  )
}
