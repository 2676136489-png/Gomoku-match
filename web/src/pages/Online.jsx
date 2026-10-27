import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api } from '../lib/api'
import { navigate } from '../lib/router'
import { openSocket } from '../lib/ws'

const CLOCKS = [
  { base: 180, inc: 2, label: '3 分钟' },
  { base: 300, inc: 5, label: '5 分钟' },
  { base: 600, inc: 10, label: '10 分钟' },
]

const RULES = [
  { key: 'renju', label: '国际连珠', note: '黑棋禁手' },
  { key: 'freestyle', label: '自由规则', note: '无禁手' },
]

export default function Online({ user }) {
  const [mode, setMode] = useState('queue') // queue | rooms
  const [rule, setRule] = useState('renju')
  const [clock, setClock] = useState(1)
  const [queueing, setQueueing] = useState(false)
  const [waitSeconds, setWaitSeconds] = useState(0)
  const [matched, setMatched] = useState(null)
  const [stats, setStats] = useState(null)
  const [rooms, setRooms] = useState([])
  const [code, setCode] = useState('')
  const [created, setCreated] = useState(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [copied, setCopied] = useState(false)
  const sockRef = useRef(null)
  const waitTimer = useRef(null)

  const conf = CLOCKS[clock]

  const refresh = useCallback(async () => {
    try {
      const [s, r] = await Promise.all([api.onlineStats(), api.rooms()])
      setStats(s)
      setRooms(r.rooms ?? [])
    } catch {
      /* 大厅数据拿不到不影响匹配，静默 */
    }
  }, [])

  useEffect(() => {
    refresh()
    const timer = setInterval(refresh, 12000)
    return () => clearInterval(timer)
  }, [refresh])

  useEffect(() => {
    const sock = openSocket('/ws/lobby', {
      onEvent: (msg) => {
        if (msg.type === 'ready') {
          setStats((prev) => ({ ...(prev ?? {}), online_users: prev?.online_users ?? 1 }))
        }
        if (msg.type === 'queued') {
          setQueueing(true)
          setWaitSeconds(0)
        }
        if (msg.type === 'cancelled') {
          setQueueing(false)
        }
        if (msg.type === 'matched') {
          setMatched(msg)
          setQueueing(false)
          // 稍等一下再跳：直接跳走的话，用户看不到「匹配成功」这一下
          setTimeout(() => navigate(`/room/${msg.game_id}`), 900)
        }
        if (msg.type === 'error') setError(msg.message)
      },
      onStatus: (status) => {
        if (status === 'closed') setQueueing(false)
      },
    })
    sockRef.current = sock
    return () => sock.close()
  }, [])

  useEffect(() => {
    if (!queueing) {
      clearInterval(waitTimer.current)
      return undefined
    }
    waitTimer.current = setInterval(() => setWaitSeconds((n) => n + 1), 1000)
    return () => clearInterval(waitTimer.current)
  }, [queueing])

  const startQueue = () => {
    setError('')
    setMatched(null)
    const sent = sockRef.current?.send({
      type: 'queue',
      rule,
      clock_base: conf.base,
      clock_inc: conf.inc,
    })
    if (!sent) {
      setError('实时通道还没连上，稍等一下再点')
      return
    }
    setQueueing(true)
    setWaitSeconds(0)
  }

  const cancelQueue = () => {
    sockRef.current?.send({ type: 'cancel' })
    setQueueing(false)
  }

  const createRoom = async () => {
    setBusy(true)
    setError('')
    try {
      const res = await api.createRoom({
        rule,
        clock_base: conf.base,
        clock_inc: conf.inc,
        rated: true,
      })
      setCreated(res.room)
      setMode('rooms')
      refresh()
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  const joinByCode = async (value) => {
    const target = (value ?? code).trim().toUpperCase()
    if (target.length !== 6) {
      setError('房间码是 6 位')
      return
    }
    setBusy(true)
    setError('')
    try {
      const res = await api.joinRoom(target)
      navigate(`/room/${res.game_id}`)
    } catch (e) {
      setError(e.message)
      setBusy(false)
    }
  }

  const copyCode = async () => {
    if (!created) return
    try {
      await navigator.clipboard.writeText(created.code)
      setCopied(true)
      setTimeout(() => setCopied(false), 1600)
    } catch {
      setCopied(false)
    }
  }

  const tier = useMemo(() => user?.stats?.tier ?? { label: '一级', progress: 0 }, [user])

  return (
    <div className="online">
      <section className="page-head">
        <div>
          <h2>在线对战</h2>
          <p>服务端权威计时，超时判负。对局计入天梯，胜负都改分。</p>
        </div>
        <div className="online-me">
          <span className="pill pill-accent">{tier.label}</span>
          <b>{Math.round(user?.stats?.rating ?? 1200)}</b>
          <span className="dim">分</span>
        </div>
      </section>

      <div className="online-tabs">
        <button
          type="button"
          className={`tab ${mode === 'queue' ? 'is-active' : ''}`}
          onClick={() => setMode('queue')}
        >
          快速匹配
        </button>
        <button
          type="button"
          className={`tab ${mode === 'rooms' ? 'is-active' : ''}`}
          onClick={() => setMode('rooms')}
        >
          房间
        </button>
        <span className="online-live">
          <span className="dot dot-live" />
          {stats?.online_users ?? 0} 人在线 · {stats?.queue ?? 0} 人等待 ·{' '}
          {stats?.live_games ?? 0} 局进行中
        </span>
      </div>

      {error && <p className="banner banner-error">{error}</p>}

      <section className="online-grid">
        <div className="panel">
          <div className="panel-head">
            <h2>规则与时限</h2>
            <span className="panel-note">匹配和开房共用这套设置</span>
          </div>

          <div className="field">
            <span className="field-label">规则</span>
            <div className="chips">
              {RULES.map((item) => (
                <button
                  key={item.key}
                  type="button"
                  className={`chip ${rule === item.key ? 'is-active' : ''}`}
                  onClick={() => setRule(item.key)}
                >
                  {item.label}
                  <em>{item.note}</em>
                </button>
              ))}
            </div>
          </div>

          <div className="field">
            <span className="field-label">每方用时</span>
            <div className="chips">
              {CLOCKS.map((item, i) => (
                <button
                  key={item.base}
                  type="button"
                  className={`chip ${clock === i ? 'is-active' : ''}`}
                  onClick={() => setClock(i)}
                >
                  {item.label}
                  <em>+{item.inc}s</em>
                </button>
              ))}
            </div>
          </div>

          {mode === 'queue' ? (
            <div className="queue-box">
              {queueing ? (
                <>
                  <div className="queue-wait">
                    <span className="spinner" />
                    <div>
                      <b>正在找对手</b>
                      <p>
                        已等 {waitSeconds} 秒，窗口按等待时间逐步放宽（±150 → ±800 分）。
                      </p>
                    </div>
                  </div>
                  <button type="button" className="btn" onClick={cancelQueue}>
                    取消匹配
                  </button>
                </>
              ) : (
                <>
                  <button type="button" className="btn btn-primary" onClick={startQueue} disabled={busy}>
                    开始匹配
                  </button>
                  <p className="hint-note">
                    黑白由服务端随机分配。连珠规则下黑棋受禁手限制，随机分座是为了不让先手优势变成固定收益。
                  </p>
                </>
              )}
              {matched && (
                <p className="queue-matched">
                  匹配成功：对手 {matched.players.white.name === user.name
                    ? matched.players.black.name
                    : matched.players.white.name}
                  ，正在进入棋盘…
                </p>
              )}
            </div>
          ) : (
            <div className="queue-box">
              <button type="button" className="btn btn-primary" onClick={createRoom} disabled={busy}>
                开一间房
              </button>
              <p className="hint-note">开房后把 6 位房间码发给对手，对方输入即可入座。</p>

              {created && (
                <div className="room-code">
                  <span className="room-code-label">房间码</span>
                  <b>{created.code}</b>
                  <button type="button" className="link-btn" onClick={copyCode}>
                    {copied ? '已复制' : '复制'}
                  </button>
                  <button
                    type="button"
                    className="link-btn"
                    onClick={() => {
                      api.closeRoom(created.id).catch(() => {})
                      setCreated(null)
                      refresh()
                    }}
                  >
                    撤销
                  </button>
                </div>
              )}

              <div className="room-join">
                <input
                  value={code}
                  onChange={(e) => setCode(e.target.value.toUpperCase().slice(0, 6))}
                  placeholder="输入 6 位房间码"
                  inputMode="latin"
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') joinByCode()
                  }}
                />
                <button type="button" className="btn" onClick={() => joinByCode()} disabled={busy}>
                  加入
                </button>
              </div>
            </div>
          )}
        </div>

        <div className="panel">
          <div className="panel-head">
            <h2>等待中的房间</h2>
            <span className="panel-note">{rooms.length} 间</span>
          </div>
          {rooms.length === 0 ? (
            <p className="empty-note">现在没有开放的房间。开一间，或者直接走快速匹配。</p>
          ) : (
            <ul className="room-list">
              {rooms.map((room) => (
                <li key={room.id}>
                  <span className="room-host">{room.host_name}</span>
                  <span className="room-meta">
                    {room.host_rating ? Math.round(room.host_rating) : '—'} 分
                  </span>
                  <span className="room-meta">
                    {Math.round(room.clock_base / 60)} 分钟 +{room.clock_inc}s
                  </span>
                  <span className="room-meta">{room.rule === 'renju' ? '连珠' : '自由'}</span>
                  <button
                    type="button"
                    className="btn btn-small"
                    disabled={busy || room.host_name === user?.name}
                    onClick={() => joinByCode(room.code)}
                  >
                    入座
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      </section>

      <section className="panel online-how">
        <div className="panel-head">
          <h2>这套对战是怎么跑的</h2>
        </div>
        <ol className="how-list">
          <li>
            <b>服务端权威</b>
            <span>落子请求走 WebSocket，轮次、禁手、胜负都在服务端判。客户端只负责发意图和画棋盘。</span>
          </li>
          <li>
            <b>计时不广播</b>
            <span>
              每手落完只下发一次剩余秒数，前端本地倒计时；到点判罚用服务端时钟，两者不互相依赖。
            </span>
          </li>
          <li>
            <b>断线可续</b>
            <span>
              连接断开后指数退避重连，重连即拉全量快照，不靠回放补消息。对方掉线时他的钟继续走。
            </span>
          </li>
          <li>
            <b>每手落库</b>
            <span>
              棋谱逐手写进 SQLite，进程重启后靠重放恢复局面；对局结束按 Elo 结算，涨跌对冲。
            </span>
          </li>
        </ol>
      </section>
    </div>
  )
}
