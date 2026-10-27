import { useEffect, useRef, useState } from 'react'
import Board from '../components/Board'
import { api } from '../lib/api'

const EMPTY = () => new Array(225).fill(0)

const PRESETS = [
  { label: '贪心 vs 搜索', left: 'greedy', right: 'minimax' },
  { label: '神经 vs 贪心', left: 'neural', right: 'greedy' },
  { label: '神经 vs 搜索', left: 'neural', right: 'minimax' },
  { label: '随机 vs 全部', left: 'random', right: 'greedy' },
]

function Picker({ title, value, onChange, algorithms }) {
  return (
    <div className="picker">
      <div className="picker-title">{title}</div>
      <div className="picker-list">
        {algorithms.map((a) => (
          <button
            key={a.key}
            type="button"
            className={`picker-item ${value === a.key ? 'is-active' : ''}`}
            onClick={() => onChange(a.key)}
          >
            <b>{a.label}</b>
            <span>{a.tag} · {a.speed}</span>
          </button>
        ))}
      </div>
    </div>
  )
}

export default function Arena({ config }) {
  const [left, setLeft] = useState('greedy')
  const [right, setRight] = useState('minimax')
  const [difficulty, setDifficulty] = useState(3)
  const [rule, setRule] = useState('renju')

  const [running, setRunning] = useState(false)
  const [error, setError] = useState('')
  const [board, setBoard] = useState(EMPTY)
  const [moves, setMoves] = useState([])
  const [info, setInfo] = useState(null)
  const [result, setResult] = useState(null)
  const [tally, setTally] = useState({})
  const abortRef = useRef(null)

  useEffect(() => () => abortRef.current?.abort(), [])

  const algorithms = config?.algorithms ?? []
  const meta = (key) => algorithms.find((a) => a.key === key)

  const run = async () => {
    if (running) return
    setRunning(true)
    setError('')
    setResult(null)
    setMoves([])
    setInfo(null)
    setBoard(EMPTY())

    const controller = new AbortController()
    abortRef.current = controller
    try {
      await api.arenaMatch(
        {
          left: { kind: 'algorithm', algorithm: left, difficulty },
          right: { kind: 'algorithm', algorithm: right, difficulty },
          rule,
          difficulty,
        },
        (ev) => {
          if (ev.type === 'start') setInfo(ev)
          else if (ev.type === 'move') {
            setMoves((prev) => [...prev, ev])
            setBoard((prev) => {
              const next = prev.slice()
              next[ev.y * 15 + ev.x] = ev.color
              return next
            })
          } else if (ev.type === 'end') {
            setResult(ev)
            setRunning(false)
            if (ev.winner) {
              const winnerKey = ev.winner === 1 ? left : right
              setTally((prev) => ({ ...prev, [winnerKey]: (prev[winnerKey] ?? 0) + 1 }))
            }
          } else if (ev.type === 'error') {
            setError(`${ev.message}${ev.detail ? `：${ev.detail}` : ''}`)
            setRunning(false)
          }
        },
        controller.signal,
      )
    } catch (e) {
      if (e.name !== 'AbortError') setError(e.message)
    } finally {
      setRunning(false)
      abortRef.current = null
    }
  }

  const stop = () => {
    abortRef.current?.abort()
    abortRef.current = null
    setRunning(false)
  }

  // 开局那一手是服务端随机放的，不算任何一方的决策，也不该摊进平均耗时
  const stats = (color) => {
    const own = moves.filter((m) => m.color === color && !m.opening)
    if (!own.length) return { count: 0, avg: 0, max: 0 }
    const total = own.reduce((s, m) => s + m.ms, 0)
    return {
      count: own.length,
      avg: Math.round(total / own.length),
      max: Math.max(...own.map((m) => m.ms)),
    }
  }

  const lastMove = moves.length ? { x: moves[moves.length - 1].x, y: moves[moves.length - 1].y } : null
  const leftStats = stats(1)
  const rightStats = stats(2)
  const sideName = (color) => (info ? (color === 1 ? info.left.label : info.right.label) : '')

  return (
    <div className="arena">
      <header className="page-head">
        <div>
          <h2>算法竞技场</h2>
          <p>
            四路算法互相对打。同一副规则、同一套禁手判定，唯一的变量是「用什么办法挑点」。
            想看清搜索值多少棋力，就看贪心对搜索的比分。
          </p>
        </div>
        <div className="arena-presets">
          {PRESETS.map((p) => (
            <button
              key={p.label}
              type="button"
              className="chip"
              onClick={() => {
                setLeft(p.left)
                setRight(p.right)
              }}
            >
              {p.label}
            </button>
          ))}
        </div>
      </header>

      {error && <div className="banner banner-error">{error}</div>}

      <div className="arena-grid">
        <section className="panel arena-setup">
          <Picker title="黑棋（先行）" value={left} onChange={setLeft} algorithms={algorithms} />
          <Picker title="白棋（后行）" value={right} onChange={setRight} algorithms={algorithms} />

          <div className="arena-options">
            <label className="field">
              <span>搜索强度</span>
              <div className="chips chips-4">
                {(config?.difficulties ?? []).map((l) => (
                  <button
                    key={l.level}
                    type="button"
                    className={`chip ${difficulty === l.level ? 'is-active' : ''}`}
                    onClick={() => setDifficulty(l.level)}
                  >
                    {l.label}
                  </button>
                ))}
              </div>
            </label>
            <label className="field">
              <span>规则</span>
              <div className="chips">
                {(config?.rules ?? []).map((r) => (
                  <button
                    key={r.key}
                    type="button"
                    className={`chip ${rule === r.key ? 'is-active' : ''}`}
                    onClick={() => setRule(r.key)}
                  >
                    {r.label}
                  </button>
                ))}
              </div>
            </label>
          </div>

          <div className="arena-buttons">
            <button type="button" className="btn btn-primary" onClick={run} disabled={running}>
              {running ? '对局进行中…' : '开始对局'}
            </button>
            {running && (
              <button type="button" className="btn" onClick={stop}>
                中止
              </button>
            )}
          </div>

          <p className="arena-hint">
            双方均为确定性算法，相同开局必然得到相同棋局。因此第一手由服务端随机落子（
            <b>越靠近中心概率越高</b>），第二手起交由算法处理，每次开局互不相同。
          </p>

          {Object.keys(tally).length > 0 && (
            <div className="arena-tally">
              <span className="coach-label">本次会话战绩</span>
              <ul>
                {Object.entries(tally).map(([key, wins]) => (
                  <li key={key}>
                    <b>{meta(key)?.label ?? key}</b>
                    <span>{wins} 胜</span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {meta('neural')?.ready === false && (
            <p className="arena-warn">神经网络权重尚未训练，选择该算法会直接报错。请先执行 tools/train_net.py。</p>
          )}
        </section>

        <section className="arena-play">
          <div className="scoreboard">
            <div className={`score ${result?.winner === 1 ? 'is-winner' : ''}`}>
              <span className="dot dot-black" />
              <b>{info?.left?.label ?? meta(left)?.label ?? left}</b>
              <em>
                {leftStats.count} 手 · 均 {leftStats.avg}ms
              </em>
            </div>
            <span className="score-vs">VS</span>
            <div className={`score ${result?.winner === 2 ? 'is-winner' : ''}`}>
              <span className="dot dot-white" />
              <b>{info?.right?.label ?? meta(right)?.label ?? right}</b>
              <em>
                {rightStats.count} 手 · 均 {rightStats.avg}ms
              </em>
            </div>
          </div>

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
            {running && (
              <div className="board-overlay">
                <span className="spinner" />
                <p>{moves.length} 手</p>
              </div>
            )}
          </div>

          {result && (
            <div className={`lab-result ${result.winner ? 'is-done' : ''}`}>
              <b>{result.winner ? `${sideName(result.winner)} 获胜` : '和棋'}</b>
              <span>
                {result.reason} · 共 {result.moves ?? moves.length} 手
              </span>
            </div>
          )}

          <div className="panel arena-log">
            <div className="panel-head">
              <h2>落子记录</h2>
              <span className="panel-note">最近 12 手</span>
            </div>
            {moves.length === 0 ? (
              <p className="empty-note">暂无对局记录。</p>
            ) : (
              <ul className="arena-moves">
                {moves.slice(-12).map((m) => (
                  <li key={m.n}>
                    <span className="lab-move-n">{m.n}</span>
                    <span className={`dot dot-${m.color === 1 ? 'black' : 'white'}`} />
                    <span className="lab-move-xy">
                      {m.x},{m.y}
                    </span>
                    <span className="lab-move-ms">{m.opening ? '开局' : `${m.ms}ms`}</span>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </section>
      </div>
    </div>
  )
}
