/**
 * 教程里用的小棋盘。
 *
 * 用 SVG 不用 canvas：教程棋盘是静态的，不需要每帧重绘，而且 SVG 节点能被
 * 检查、能被文本选中——canvas 画什么在 DOM 里查不到，调试和自动化验证都吃亏。
 */

const MARGIN = 10
const SPAN = 280
const STEP = SPAN / 14
const STARS = [
  [3, 3],
  [11, 3],
  [7, 7],
  [3, 11],
  [11, 11],
]

const px = (v) => MARGIN + v * STEP

export default function MiniBoard({ stones = [], marks = [], size = 320 }) {
  return (
    <svg className="mini-board" viewBox="0 0 300 300" width={size} height={size} role="img">
      <rect x="0" y="0" width="300" height="300" rx="10" className="mini-bg" />

      {Array.from({ length: 15 }, (_, i) => (
        <g key={`grid-${i}`} className="mini-grid">
          <line x1={px(0)} y1={px(i)} x2={px(14)} y2={px(i)} />
          <line x1={px(i)} y1={px(0)} x2={px(i)} y2={px(14)} />
        </g>
      ))}

      {STARS.map(([x, y]) => (
        <circle key={`star-${x}-${y}`} cx={px(x)} cy={px(y)} r="2" className="mini-star" />
      ))}

      {stones.map(([x, y, color]) => (
        <circle
          key={`stone-${x}-${y}`}
          cx={px(x)}
          cy={px(y)}
          r="8.2"
          className={color === 1 ? 'mini-stone mini-black' : 'mini-stone mini-white'}
        />
      ))}

      {marks.map((m) => {
        const cx = px(m.x)
        const cy = px(m.y)
        if (m.kind === 'ban') {
          return (
            <g key={`mark-${m.x}-${m.y}`} className="mini-ban">
              <circle cx={cx} cy={cy} r="8.6" />
              <line x1={cx - 5} y1={cy - 5} x2={cx + 5} y2={cy + 5} />
              <line x1={cx + 5} y1={cy - 5} x2={cx - 5} y2={cy + 5} />
            </g>
          )
        }
        if (m.kind === 'star') {
          return <circle key={`mark-${m.x}-${m.y}`} cx={cx} cy={cy} r="2.4" className="mini-star" />
        }
        return (
          <circle key={`mark-${m.x}-${m.y}`} cx={cx} cy={cy} r="8.6" className="mini-hint" />
        )
      })}
    </svg>
  )
}
