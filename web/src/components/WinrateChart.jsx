const W = 320
const H = 96
const PAD = 6

/**
 * 逐手胜率曲线。
 *
 * 和旧的局势曲线（直接画静态分）不同，这条线的纵轴是概率，
 * 中间那条虚线为 50%，曲线在其上方即表示人类方占优，无需再对照分数量级。
 * 幅度最大的一次跳变会单独标出，作为该局的转折点。
 */
export default function WinrateChart({ series, humanColor = 1, onPick }) {
  if (!series || series.length === 0) {
    return (
      <div className="chart-empty">
        <span>胜率曲线</span>
        <p>落子后显示逐手胜率</p>
      </div>
    )
  }

  // 曲线统一按人类玩家视角绘制：执黑时直接取黑棋胜率，执白时取反
  const toHuman = (black) => (humanColor === 2 ? 1 - black : black)
  const pts = series.map((row, i) => {
    const p = toHuman(row.black)
    const x = series.length === 1 ? W / 2 : PAD + (i / (series.length - 1)) * (W - PAD * 2)
    const y = PAD + (1 - p) * (H - PAD * 2)
    return { x, y, p, row }
  })

  // 转折点：相邻两手之间胜率变化最大的一处
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

  return (
    <div className={`chart wr-chart ${ahead ? 'chart-ahead' : 'chart-behind'}`}>
      <div className="chart-head">
        <span>胜率曲线</span>
        <span className={`chart-verdict ${ahead ? 'ahead' : 'behind'}`}>
          {(last.p * 100).toFixed(1)}%
        </span>
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" className="chart-svg wr-svg">
        <rect x="0" y="0" width={W} height={midY} className="wr-zone top" />
        <rect x="0" y={midY} width={W} height={H - midY} className="wr-zone bottom" />
        <line x1="0" y1={midY} x2={W} y2={midY} className="chart-axis" />
        {/* 不填面积：胜率在 50% 附近时，曲线下方那一大片淡色会被读成一块灰板。
            一根笔线加一条 50% 虚线已经够看了。 */}
        <path d={line} className="chart-line" fill="none" />
        {swing > 0 && (
          <g className="wr-swing">
            <line x1={pts[swing].x} y1={PAD} x2={pts[swing].x} y2={H - PAD} className="wr-swing-line" />
            <circle cx={pts[swing].x} cy={pts[swing].y} r="3.2" className="wr-swing-dot" />
          </g>
        )}
        {last && <circle cx={last.x} cy={last.y} r="3" className="chart-dot" />}
      </svg>
      <div className="chart-foot">
        <span>第 1 手 · {(pts[0].p * 100).toFixed(0)}%</span>
        {swing > 0 ? (
          <span className="wr-swing-note">
            转折 第 {pts[swing].row.ply} 手 {(swingDelta * 100).toFixed(0)} 点
          </span>
        ) : null}
        <span>第 {series.length} 手</span>
      </div>
      {onPick && swing > 0 && (
        <button type="button" className="wr-jump" onClick={() => onPick(swing)}>
          看第 {pts[swing].row.ply} 手怎么走的
        </button>
      )}
    </div>
  )
}
