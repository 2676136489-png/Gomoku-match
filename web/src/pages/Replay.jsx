import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import Board from '../components/Board'
import { api } from '../lib/api'
import { navigate } from '../lib/router'

const RESULT_TEXT = { win: '胜', loss: '负', draw: '和', live: '进行中' }
const MODE_TEXT = { agent: 'AI 对弈', learn: '学习', online: '在线', pvp: '双人', engine: '纯引擎' }

function stamp(seconds) {
  if (!seconds) return ''
  const d = new Date(seconds * 1000)
  const pad = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

export default function Replay({ gameId }) {
  if (gameId) return <Player gameId={gameId} />
  return <Library />
}

/* ------------------------------------------------------------------ 棋谱库 */

function Library() {
  const [rows, setRows] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    api
      .history(40)
      .then((res) => setRows(res.rows ?? []))
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false))
  }, [])

  return (
    <div className="replay-list">
      <section className="page-head">
        <div>
          <h2>棋谱</h2>
          <p>所有对局逐手入库，可逐手回放。人机、在线与双人对局均在此列。</p>
        </div>
        <span className="pill pill-quiet">{rows.length} 局</span>
      </section>

      {error && <p className="banner banner-error">{error}</p>}
      {loading ? (
        <p className="empty-note">正在读取…</p>
      ) : rows.length === 0 ? (
        <p className="empty-note">暂无对局记录，完成一局后显示。</p>
      ) : (
        <table className="replay-table">
          <thead>
            <tr>
              <th>结果</th>
              <th>对手</th>
              <th>模式</th>
              <th>手数</th>
              <th>分数</th>
              <th>时间</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.id}>
                <td>
                  <span className={`result result-${row.result}`}>
                    {RESULT_TEXT[row.result] ?? row.result}
                  </span>
                </td>
                <td className="replay-opponent">{row.opponent}</td>
                <td className="dim">{MODE_TEXT[row.mode] ?? row.mode}</td>
                <td className="mono">{row.moves}</td>
                <td className={row.rating_delta > 0 ? 'up' : row.rating_delta < 0 ? 'down' : 'dim'}>
                  {row.rating_delta == null
                    ? '—'
                    : `${row.rating_delta > 0 ? '+' : ''}${row.rating_delta}`}
                </td>
                <td className="dim">{stamp(row.at)}</td>
                <td>
                  <button
                    type="button"
                    className="link-btn"
                    onClick={() => navigate(`/replay/${row.id}`)}
                  >
                    回放
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}

/* ------------------------------------------------------------------ 回放器 */

function Player({ gameId }) {
  const [game, setGame] = useState(null)
  const [error, setError] = useState('')
  const [index, setIndex] = useState(0)
  const [playing, setPlaying] = useState(false)
  const timer = useRef(null)

  useEffect(() => {
    api
      .replay(gameId)
      .then((res) => {
        setGame(res.game)
        setIndex(res.game.moves.length - 1 < 0 ? -1 : 0)
      })
      .catch((e) => setError(e.message))
  }, [gameId])

  const moves = game?.moves ?? []

  const board = useMemo(() => {
    const cells = Array.from({ length: 225 }, () => 0)
    for (let i = 0; i <= index && i < moves.length; i += 1) {
      const move = moves[i]
      cells[move.y * 15 + move.x] = move.color
    }
    return cells
  }, [moves, index])

  const step = useCallback(
    (delta) => {
      setIndex((cur) => Math.max(-1, Math.min(moves.length - 1, cur + delta)))
    },
    [moves.length],
  )

  useEffect(() => {
    const onKey = (e) => {
      if (e.key === 'ArrowLeft') step(-1)
      if (e.key === 'ArrowRight') step(1)
      if (e.key === ' ') {
        e.preventDefault()
        setPlaying((p) => !p)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [step])

  useEffect(() => {
    if (!playing) {
      clearInterval(timer.current)
      return undefined
    }
    timer.current = setInterval(() => {
      setIndex((cur) => {
        if (cur >= moves.length - 1) {
          setPlaying(false)
          return cur
        }
        return cur + 1
      })
    }, 420)
    return () => clearInterval(timer.current)
  }, [playing, moves.length])

  if (error) {
    return (
      <div className="replay-player">
        <p className="banner banner-error">{error}</p>
        <button type="button" className="btn" onClick={() => navigate('/replay')}>
          回到棋谱库
        </button>
      </div>
    )
  }

  if (!game) return <p className="empty-note">正在读取棋谱…</p>

  const current = index >= 0 ? moves[index] : null

  return (
    <div className="replay-player">
      <section className="page-head">
        <div>
          <h2>回放</h2>
          <p>
            {MODE_TEXT[game.mode] ?? game.mode} · {game.rule_label} · {moves.length} 手 ·{' '}
            {game.status === 'finished' ? game.result_reason || '已结束' : game.status}
          </p>
        </div>
        <div className="actions">
          <button type="button" className="btn" onClick={() => navigate('/replay')}>
            棋谱库
          </button>
        </div>
      </section>

      <div className="replay-grid">
        <div className="replay-board">
          <div className="board-frame">
            <Board
              board={board}
              lastMove={current ? { x: current.x, y: current.y } : null}
              winLine={game.win_line ?? []}
              humanColor={1}
              interactive={false}
              onPlace={() => {}}
            />
          </div>

          <div className="replay-controls">
            <button type="button" className="btn btn-small" onClick={() => setIndex(-1)}>
              ⏮
            </button>
            <button type="button" className="btn btn-small" onClick={() => step(-1)}>
              ◀
            </button>
            <button
              type="button"
              className="btn btn-primary btn-small"
              onClick={() => setPlaying((p) => !p)}
            >
              {playing ? '暂停' : '播放'}
            </button>
            <button type="button" className="btn btn-small" onClick={() => step(1)}>
              ▶
            </button>
            <button
              type="button"
              className="btn btn-small"
              onClick={() => setIndex(moves.length - 1)}
            >
              ⏭
            </button>
            <span className="replay-count">
              {index + 1} / {moves.length}
            </span>
          </div>

          <input
            className="replay-scrub"
            type="range"
            min={-1}
            max={moves.length - 1}
            value={index}
            onChange={(e) => setIndex(Number(e.target.value))}
          />
        </div>

        <aside className="replay-side">
          <div className="panel">
            <div className="panel-head">
              <h2>棋谱</h2>
              <span className="panel-note">← → 翻手，空格播放</span>
            </div>
            <div className="game-feed">
              {moves.map((move, i) => (
                <button
                  key={i}
                  type="button"
                  className={`game-row as-button ${i === index ? 'is-active' : ''}`}
                  onClick={() => setIndex(i)}
                >
                  <span className="feed-idx">{i + 1}</span>
                  <span className={`dot dot-${move.color === 1 ? 'black' : 'white'}`} />
                  <span className="feed-pos">
                    {String.fromCharCode(65 + move.x)}
                    {15 - move.y}
                  </span>
                  <span className="feed-ms">{move.comment || ''}</span>
                </button>
              ))}
              {moves.length === 0 && <p className="empty-note">本局没有落子记录。</p>}
            </div>
          </div>
        </aside>
      </div>
    </div>
  )
}
