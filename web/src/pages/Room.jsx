import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import Board from '../components/Board'
import { navigate } from '../lib/router'
import { openSocket } from '../lib/ws'

const EMPTY = Array.from({ length: 225 }, () => 0)

function fmtClock(seconds) {
  if (seconds == null) return '—'
  const s = Math.max(0, seconds)
  const m = Math.floor(s / 60)
  const rest = Math.floor(s % 60)
  if (m >= 10) return `${m}:${String(rest).padStart(2, '0')}`
  if (s < 20) return `${s.toFixed(1)}s`
  return `${m}:${String(rest).padStart(2, '0')}`
}

export default function Room({ gameId, user }) {
  const [game, setGame] = useState(null)
  const [seat, setSeat] = useState(null)
  const [status, setStatus] = useState('connecting')
  const [error, setError] = useState('')
  const [result, setResult] = useState(null)
  const [chat, setChat] = useState([])
  const [draft, setDraft] = useState('')
  const sockRef = useRef(null)
  const feedRef = useRef(null)
  const syncRef = useRef({ seat: null, at: 0, left: 0, turn: 0 })
  const [tick, setTick] = useState(0)

  useEffect(() => {
    const sock = openSocket(`/ws/game/${gameId}`, {
      onEvent: (msg) => {
        if (msg.type === 'hello') {
          setGame(msg.game)
          setSeat(msg.you)
          setStatus('online')
          setError('')
        }
        if (msg.type === 'state' || msg.type === 'finished') {
          if (msg.game) setGame(msg.game)
          if (msg.type === 'finished') {
            setResult({ winner: msg.winner, reason: msg.reason, rating: msg.rating })
          }
        }
        if (msg.type === 'chat') {
          setChat((prev) => [...prev.slice(-40), msg])
        }
        if (msg.type === 'error') {
          setError(msg.message)
          setTimeout(() => setError(''), 3200)
        }
      },
      onStatus: (state) => {
        if (state === 'closed') setStatus((prev) => (prev === 'online' ? 'reconnecting' : prev))
        if (state === 'open') setStatus('online')
      },
    })
    sockRef.current = sock
    return () => sock.close()
  }, [gameId])

  // 本地倒计时。服务端只在每手落子时给一次剩余秒数，中间不动——这样一局棋
  // 不需要每秒一条心跳消息，网络抖动也不会让钟跳。
  useEffect(() => {
    const timer = setInterval(() => setTick((n) => n + 1), 250)
    return () => clearInterval(timer)
  }, [])

  useEffect(() => {
    if (!game?.clock) return
    const clock = game.clock
    syncRef.current = {
      seat: clock.turn,
      at: Date.now(),
      left: clock.turn === 1 ? clock.black : clock.white,
      turn: clock.turn,
    }
  }, [game])

  useEffect(() => {
    if (feedRef.current) feedRef.current.scrollTop = feedRef.current.scrollHeight
  }, [game?.moves?.length])

  const remain = useCallback(
    (color) => {
      if (!game?.clock) return null
      const base = color === 1 ? game.clock.black : game.clock.white
      if (game.status !== 'playing') return base
      const sync = syncRef.current
      if (sync.seat !== color) return base
      return Math.max(0, sync.left - (Date.now() - sync.at) / 1000)
    },
    [game, tick],
  )

  const board = game?.board ?? EMPTY
  const myTurn = game && seat && game.status === 'playing' && game.turn === seat
  const last = game?.moves?.length ? game.moves[game.moves.length - 1] : null

  const place = (x, y) => {
    if (!myTurn) return
    if (board[y * 15 + x] !== 0) return
    sockRef.current?.send({ type: 'move', x, y })
  }

  const resign = () => {
    if (!game || game.status !== 'playing') return
    if (!window.confirm('认输之后这一局就结束了，分数按负场结算。确定？')) return
    sockRef.current?.send({ type: 'resign' })
  }

  const sendChat = (event) => {
    event.preventDefault()
    const text = draft.trim()
    if (!text) return
    sockRef.current?.send({ type: 'chat', text })
    setDraft('')
  }

  const players = game?.players ?? {}
  const outcome = useMemo(() => {
    if (!result) return null
    if (result.winner == null) return { title: '和棋', tone: 'draw' }
    return result.winner === seat
      ? { title: '你赢了', tone: 'win' }
      : { title: '你输了', tone: 'loss' }
  }, [result, seat])

  const delta = result?.rating?.rated
    ? seat === 1
      ? result.rating.black?.delta
      : result.rating.white?.delta
    : null

  return (
    <div className="room">
      <section className="page-head">
        <div>
          <h2>对局</h2>
          <p>
            房间 <span className="mono">{gameId}</span>
            {game?.rule_label ? ` · ${game.rule_label}` : ''}
            {status === 'reconnecting' ? ' · 重连中…' : ''}
          </p>
        </div>
        <div className="actions">
          <button type="button" className="btn" onClick={() => navigate('/online')}>
            回到大厅
          </button>
          <button
            type="button"
            className="btn btn-danger"
            onClick={resign}
            disabled={!game || game.status !== 'playing'}
          >
            认输
          </button>
        </div>
      </section>

      {error && <p className="banner banner-error">{error}</p>}
      {status === 'reconnecting' && (
        <p className="banner">连接断了，正在重连。重连成功会自动拿到最新局面。</p>
      )}

      <div className="room-grid">
        <div className="room-board">
          <div className="room-players">
            {[
              { color: 1, label: '黑', info: players.black },
              { color: 2, label: '白', info: players.white },
            ].map((side) => {
              const active = game?.turn === side.color && game?.status === 'playing'
              const isMe = seat === side.color
              const left = remain(side.color)
              const low = left != null && left < 30
              return (
                <div
                  key={side.color}
                  className={`room-player ${active ? 'is-active' : ''} ${isMe ? 'is-me' : ''}`}
                >
                  <span className={`stone stone-${side.color === 1 ? 'black' : 'white'}`} />
                  <div className="room-player-main">
                    <b>{side.info?.name ?? '等待中'}</b>
                    <span className="dim">
                      {side.info?.rating ? `${Math.round(side.info.rating)} 分` : '—'}
                      {isMe ? ' · 你' : ''}
                      {side.info && side.info.online === false ? ' · 离线' : ''}
                    </span>
                  </div>
                  <span className={`room-clock ${active ? 'is-active' : ''} ${low ? 'is-low' : ''}`}>
                    {fmtClock(left)}
                  </span>
                </div>
              )
            })}
          </div>

          <div className="board-frame">
            <Board
              board={board}
              lastMove={last ? { x: last.x, y: last.y } : null}
              winLine={game?.win_line ?? []}
              humanColor={seat ?? 1}
              interactive={Boolean(myTurn)}
              onPlace={place}
              banned={myTurn ? game?.banned ?? [] : []}
            />
          </div>

          <p className="board-caption">
            {game?.status === 'finished'
              ? game.result_reason || '对局结束'
              : myTurn
                ? '轮到你落子'
                : '等对手落子'}
          </p>
        </div>

        <aside className="room-side">
          {result && outcome && (
            <div className={`result-card result-${outcome.tone}`}>
              <h3>{outcome.title}</h3>
              <p>{result.reason || (result.winner == null ? '棋盘已满' : '五连')}</p>
              {delta != null && (
                <p className="result-delta">
                  天梯 {delta > 0 ? '+' : ''}
                  {delta} 分
                </p>
              )}
              {!result.rating?.rated && <p className="dim">本局不计分</p>}
              <button type="button" className="btn btn-primary" onClick={() => navigate('/online')}>
                再来一局
              </button>
            </div>
          )}

          <div className="panel">
            <div className="panel-head">
              <h2>手数</h2>
              <span className="panel-note">{game?.moves?.length ?? 0} 手</span>
            </div>
            <div className="game-feed" ref={feedRef}>
              {(game?.moves ?? []).map((move, i) => (
                <div key={i} className="game-row">
                  <span className="feed-idx">{i + 1}</span>
                  <span className={`dot dot-${move.color === 1 ? 'black' : 'white'}`} />
                  <span className="feed-pos">
                    {String.fromCharCode(65 + move.x)}
                    {15 - move.y}
                  </span>
                  <span className="feed-ms">{move.think_ms ? `${(move.think_ms / 1000).toFixed(1)}s` : ''}</span>
                </div>
              ))}
              {!game?.moves?.length && <p className="empty-note">还没落子。</p>}
            </div>
          </div>

          <div className="panel">
            <div className="panel-head">
              <h2>对话</h2>
              <span className="panel-note">每秒一条，200 字以内</span>
            </div>
            <div className="chat-log">
              {chat.map((msg, i) => (
                <p key={i} className={msg.from === user?.name ? 'is-me' : ''}>
                  <b>{msg.from}</b>
                  {msg.text}
                </p>
              ))}
              {chat.length === 0 && <p className="empty-note">还没有人说话。</p>}
            </div>
            <form className="chat-form" onSubmit={sendChat}>
              <input
                value={draft}
                onChange={(e) => setDraft(e.target.value.slice(0, 200))}
                placeholder="说点什么"
              />
              <button type="submit" className="btn btn-small">
                发送
              </button>
            </form>
          </div>
        </aside>
      </div>
    </div>
  )
}
