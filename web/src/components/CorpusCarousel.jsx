import { useEffect, useMemo, useState } from 'react'
import Board from './Board'

const EMPTY = () => new Array(225).fill(0)
const SPEED = 220 // 每手动画间隔，毫秒
const HOLD = 2400 // 一盘放完停多久再翻下一盘

/**
 * 棋谱轮换展台。
 *
 * 不只是「换一张图」：每盘棋会自己从头摆一遍，摆完停两秒再换下一盘。
 * 静态截图看不出棋是怎么走到那个局面的，而「一手一手长出来」才看得出
 * 哪里有活三、哪里在堵四。
 *
 * 悬停暂停是必须的：用户想看某一手的细节时，轮播不能抢走棋盘。
 *
 * 当前显示哪一盘由父级的 `activeId` 决定，自己不存 index。早先这里是
 * 双向同步（父级改我也改、我改也通知父级），棋谱库多一盘的时候两边会
 * 各算各的——上传完列表已经选中新棋谱，展台却翻回上一盘，来回抖动。
 * 现在只有一条写路径：想换盘就调 onActive，父级改完再从 props 流回来。
 * `fallback` 只兜住父级不接管的情况（比如以后单独拿这个组件去展示）。
 */
export default function CorpusCarousel({ items = [], activeId, onActive }) {
  const [ply, setPly] = useState(0)
  const [playing, setPlaying] = useState(true)
  const [hover, setHover] = useState(false)
  const [fallback, setFallback] = useState(0)

  const count = items.length

  const index = useMemo(() => {
    const at = activeId ? items.findIndex((i) => i.id === activeId) : -1
    if (at >= 0) return at
    return Math.min(fallback, Math.max(0, count - 1))
  }, [activeId, items, fallback, count])

  const item = items[index]
  const itemId = item?.id
  const total = item?.moves?.length ?? 0

  // 换盘那一帧里 ply 还是上一盘的值（清零的 effect 要下一轮才跑），
  // 直接拿它去取 item.moves[ply - 1] 会取到 undefined，解构就抛
  // 「undefined is not iterable」，整页崩掉。所有读取都走这个夹紧后的值。
  const shown = Math.min(ply, total)

  // 换盘就把摆子进度清零，否则新棋谱会从上一盘的第十五手接着摆
  useEffect(() => {
    setPly(0)
  }, [itemId])

  const goto = (next) => {
    if (!count) return
    const target = (next + count) % count
    setFallback(target)
    onActive?.(items[target].id)
  }

  useEffect(() => {
    if (!count || !playing || hover) return undefined
    if (shown < total) {
      const timer = setTimeout(() => setPly(shown + 1), SPEED)
      return () => clearTimeout(timer)
    }
    const timer = setTimeout(() => goto(index + 1), HOLD)
    return () => clearTimeout(timer)
    // goto 每次渲染都是新函数，放进依赖会让计时器无限重建，这里只认 index
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [shown, total, playing, hover, count, index])

  const board = useMemo(() => {
    const next = EMPTY()
    if (!item) return next
    item.moves.slice(0, shown).forEach(([x, y], i) => {
      next[y * 15 + x] = i % 2 === 0 ? 1 : 2
    })
    return next
  }, [item, shown])

  const lastMove = useMemo(() => {
    if (!item || shown === 0) return null
    const [x, y] = item.moves[shown - 1]
    return { x, y }
  }, [item, shown])

  if (!item) {
    return <p className="empty-note">棋谱库是空的，先上传一盘。</p>
  }

  const side = item.winner === 1 ? '黑胜' : item.winner === 2 ? '白胜' : '未分胜负'
  // 备注在列表里是「白胜 · 白棋连五」这样一整句，但展台下面已经单列了「结果」，
  // 同一块里让「白胜」出现两遍很吵，这里只留后半句：怎么结束的。
  const noteTail = (item.note || '').includes('·')
    ? (item.note || '').split('·').slice(1).join('·').trim()
    : item.note

  return (
    <div
      className="carousel"
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
    >
      <div className="carousel-board">
        {/* 和其它页一样套一层 board-frame：深色木框是棋墩的边，
            少了它棋盘就是一块贴在纸上的画布，和整站质感对不上 */}
        <div className="board-frame">
          <Board
            board={board}
            lastMove={lastMove}
            winLine={[]}
            humanColor={1}
            interactive={false}
            onPlace={() => {}}
            hintPoint={null}
            banned={[]}
          />
        </div>
        <div className="carousel-progress">
          <span style={{ width: `${total ? (shown / total) * 100 : 0}%` }} />
        </div>
      </div>

      <div className="carousel-info">
        <div className="carousel-head">
          <span className={`carousel-badge is-${item.source}`}>
            {item.source === 'builtin' ? '内置' : '上传'}
          </span>
          <h3>{item.name}</h3>
        </div>

        <p className="carousel-note">{noteTail || '—'}</p>

        <dl className="carousel-facts">
          <div>
            <dt>手数</dt>
            <dd>
              {shown}
              <em>/ {total}</em>
            </dd>
          </div>
          <div>
            <dt>结果</dt>
            <dd>{side}</dd>
          </div>
          <div>
            <dt>来源</dt>
            <dd>{item.author || '匿名'}</dd>
          </div>
        </dl>

        <div className="carousel-controls">
          <button type="button" className="carousel-btn" onClick={() => goto(index - 1)} title="上一盘">
            ‹
          </button>
          <button
            type="button"
            className="carousel-btn"
            onClick={() => setPlaying((p) => !p)}
            title={playing ? '暂停' : '继续'}
          >
            {playing ? '❙❙' : '▶'}
          </button>
          <button type="button" className="carousel-btn" onClick={() => goto(index + 1)} title="下一盘">
            ›
          </button>
          <span className="carousel-count">
            {index + 1} / {count}
          </span>
        </div>

        <div className="carousel-dots">
          {items.map((it, i) => (
            <button
              key={it.id}
              type="button"
              className={`carousel-dot ${i === index ? 'is-active' : ''}`}
              onClick={() => goto(i)}
              title={it.name}
            />
          ))}
        </div>
      </div>
    </div>
  )
}
