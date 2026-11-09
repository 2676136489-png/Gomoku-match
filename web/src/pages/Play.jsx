import { useEffect, useMemo, useRef, useState } from 'react'
import AnalysisPanel from '../components/AnalysisPanel'
import Board from '../components/Board'
import CoachPanel from '../components/CoachPanel'
import ExplainCard from '../components/ExplainCard'
import FirstRunTip from '../components/FirstRunTip'
import { Term } from '../components/Glossary'
import MoveFeed from '../components/MoveFeed'
import WinrateChart from '../components/WinrateChart'
import { api } from '../lib/api'
import { navigate } from '../lib/router'

/**
 * 对局页。
 *
 * 一屏只做一件事：下棋。分析、棋谱、教练点评各自待在一个页签里，
 * 默认停在「解说」，因为那是落子之后最想立刻看到的东西。
 * 开局参数由 /play 准备页决定，这里不再提供任何设置项。
 */

const TABS = [
  { key: 'note', label: '解说' },
  { key: 'analysis', label: '分析' },
  { key: 'moves', label: '棋谱' },
]

export default function Play({ gameId, onUserChange }) {
  const [game, setGame] = useState(null)
  const [agent, setAgent] = useState(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [review, setReview] = useState('')
  const [hintPoint, setHintPoint] = useState(null)
  const [coach, setCoach] = useState(null)
  const [tactics, setTactics] = useState(null)
  const [heat, setHeat] = useState(null)
  const [heatBusy, setHeatBusy] = useState(false)
  const [insight, setInsight] = useState(null)
  const [explain, setExplain] = useState(null)
  const [pendingPreview, setPendingPreview] = useState(null)
  const [explainBusy, setExplainBusy] = useState(false)
  const [focus, setFocus] = useState(null)
  const [predictOn, setPredictOn] = useState(true)
  const [tab, setTab] = useState('note')
  const settled = useRef('')
  const insightSeq = useRef(0)

  // 进页面先按 id 把局面拉回来。刷新、从复盘跳回、分享链接都走这条路径。
  useEffect(() => {
    if (!gameId) {
      navigate('/play')
      return
    }
    let alive = true
    setLoading(true)
    api
      .replay(gameId)
      .then(({ game: g }) => {
        if (!alive) return
        setGame(g)
        if (g?.step && g.moves?.length && !g.pending) {
          api
            .explain(gameId)
            .then((res) => {
              if (!alive) return
              setExplain(res.explain)
              setFocus({ x: res.explain.x, y: res.explain.y })
            })
            .catch(() => {})
        }
      })
      .catch((e) => {
        if (alive) setError(e.message)
      })
      .finally(() => {
        if (alive) setLoading(false)
      })
    return () => {
      alive = false
    }
  }, [gameId])

  // 棋盘画在 canvas 上，DOM 里查不到棋子。挂一份快照出来，自动化验证不用靠截图猜。
  useEffect(() => {
    window.__gomoku = game
  }, [game])

  // 分析数据同样挂出来，端到端检查时能直接读到胜率和预测
  useEffect(() => {
    window.__gomokuInsight = insight
  }, [insight])

  // 一局结束就刷新战绩，不用等用户手动刷新页面
  useEffect(() => {
    if (!game || game.status !== 'finished') return
    if (settled.current === game.id) return
    settled.current = game.id
    onUserChange?.()
  }, [game, onUserChange])

  // 每落一手刷一次分析。用序号挡住乱序返回的旧请求——
  // 快棋连点两下时，后发先至的响应会把新局面的数据覆盖成旧的。
  const ply = game?.moves?.length ?? 0
  useEffect(() => {
    if (!game?.id) return
    const seq = insightSeq.current + 1
    insightSeq.current = seq
    api
      .insight(game.id)
      .then((data) => {
        if (insightSeq.current === seq) setInsight(data)
      })
      .catch(() => {
        /* 分析失败不该影响下棋，静默降级 */
      })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [game?.id, ply, game?.status])

  // 待确认的那一手单独取解说：它是「将要下」而不是「已经下」
  useEffect(() => {
    if (!game?.id || !game.pending) {
      setPendingPreview(null)
      return undefined
    }
    let alive = true
    api
      .pending(game.id)
      .then((res) => {
        if (alive) setPendingPreview(res.preview)
      })
      .catch(() => {
        if (alive) setPendingPreview(null)
      })
    return () => {
      alive = false
    }
  }, [game?.id, ply, game?.pending])

  const humanTurn = Boolean(
    game &&
      game.status === 'playing' &&
      !game.pending &&
      game.turn === game.human_color,
  )
  const canAct = humanTurn && !busy

  const status = useMemo(() => {
    if (!game) return { tone: 'idle', text: '准备中' }
    if (game.status === 'finished') {
      if (!game.winner) return { tone: 'idle', text: '和棋' }
      if (game.winner === game.human_color) return { tone: 'win', text: '你赢了' }
      return { tone: 'lose', text: game.ban_loss ? '禁手判负' : 'Agent 获胜' }
    }
    if (busy) return { tone: 'thinking', text: 'Agent 思考中' }
    if (game.pending) return { tone: 'wait', text: '等待确认落子' }
    return humanTurn ? { tone: 'live', text: '轮到你落子' } : { tone: 'wait', text: '等待对手落子' }
  }, [game, busy, humanTurn])

  const applyResponse = (res) => {
    setGame(res.game)
    if (res.agent) setAgent(res.agent)
    if ('coach' in res) setCoach(res.coach)
    if (res.tactics) setTactics(res.tactics)
    if (res.explain) {
      setExplain(res.explain)
      setFocus({ x: res.explain.x, y: res.explain.y })
    }
  }

  const play = async (x, y) => {
    if (!canAct) return
    setBusy(true)
    setError('')
    setHintPoint(null)
    try {
      applyResponse(await api.move(game.id, x, y))
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  const resume = async () => {
    if (!game?.pending || busy) return
    setBusy(true)
    setError('')
    try {
      const res = await api.resume(game.id)
      setGame(res.game)
      if (res.agent) setAgent(res.agent)
      if (res.explain) {
        setExplain(res.explain)
        setFocus({ x: res.explain.x, y: res.explain.y })
      }
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  const undo = async () => {
    if (!game || busy) return
    setBusy(true)
    setError('')
    setHintPoint(null)
    setCoach(null)
    setExplain(null)
    setFocus(null)
    settled.current = ''
    try {
      const res = await api.undo(game.id)
      setGame(res.game)
      setAgent(res.agent ?? null)
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  const askHint = async () => {
    if (!game || busy) return
    setBusy(true)
    setError('')
    try {
      const res = await api.hint(game.id)
      setHintPoint({ x: res.x, y: res.y })
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  const askReview = async () => {
    if (!game || busy) return
    setBusy(true)
    setError('')
    try {
      const res = await api.review(game.id)
      setReview(res.review)
      setTab('note')
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  // 点棋谱里任意一手，或者点曲线上的转折点，都走这里
  const explainPly = async (index) => {
    if (!game || explainBusy) return
    setExplainBusy(true)
    setError('')
    try {
      const res = await api.explain(game.id, index)
      setExplain(res.explain)
      setFocus({ x: res.explain.x, y: res.explain.y })
      setTab('note')
    } catch (e) {
      setError(e.message)
    } finally {
      setExplainBusy(false)
    }
  }

  // 网络视角：把 2881 个参数对一个局面的判断摊在棋盘上。
  // 单看胜率没人知道它到底在看什么，热力图能直接看出它对哪片区域有偏好。
  const toggleHeat = async () => {
    if (heat) {
      setHeat(null)
      return
    }
    if (!game || heatBusy) return
    setHeatBusy(true)
    setError('')
    try {
      const res = await api.neuralScan({
        board: game.board,
        color: game.status === 'finished' ? game.human_color : game.turn,
        rule: game.rule,
      })
      setHeat(res.ready ? res.points : [])
      if (!res.ready) setError('网络权重未加载，请稍后重试')
      setPredictOn(false)
    } catch (e) {
      setError(e.message)
    } finally {
      setHeatBusy(false)
    }
  }

  const togglePredict = () => {
    setPredictOn((v) => !v)
    if (!predictOn) setHeat(null)
  }

  const heatTop = heat?.slice(0, 5) ?? []
  const predictPoints = predictOn ? insight?.prediction?.points ?? [] : []
  const predictTop = predictPoints.slice(0, 5)

  const board = game?.board ?? new Array(225).fill(0)
  const learning = game?.mode === 'learn'
  const explainData = game?.pending ? pendingPreview : explain
  const series = insight?.series ?? []

  if (loading) {
    return <p className="empty-note">正在读取对局…</p>
  }

  if (!game) {
    return (
      <div className="notfound">
        <h2>对局不存在</h2>
        <p>{error || '这个对局 id 没有对应的记录，可能已经被清理。'}</p>
        <button type="button" className="btn btn-primary" onClick={() => navigate('/play')}>
          回到开局准备
        </button>
      </div>
    )
  }

  const oppoLabel = `${game.difficulty_label} · ${game.persona_label}`

  return (
    <div className="play">
      <div className="play-bar">
        <div className="play-meta">
          <span className={`pill pill-${status.tone}`}>{status.text}</span>
          <span className="pill pill-muted">
            {game.rule === 'renju' ? <Term k="renju">{game.rule_label}</Term> : game.rule_label}
          </span>
          <span className="pill pill-muted">{oppoLabel}</span>
          {game.step && <span className="pill pill-accent">逐步确认</span>}
          <span className="pill pill-muted">
            第 {game.moves.length} <Term k="ply">手</Term>
          </span>
        </div>
        <div className="play-actions">
          <button type="button" className="btn btn-small" onClick={undo} disabled={busy || !game.moves.length}>
            悔棋
          </button>
          <button type="button" className="btn btn-small" onClick={askHint} disabled={!canAct}>
            提示
          </button>
          <button type="button" className="btn btn-small" onClick={askReview} disabled={busy}>
            复盘
          </button>
          <button type="button" className="btn btn-small btn-primary" onClick={() => navigate('/play')}>
            新对局
          </button>
        </div>
      </div>

      {error && (
        <div className="banner banner-error" role="alert">
          {error}
        </div>
      )}

      <FirstRunTip
        id="game"
        compact
        title="这一页只有三件事"
        steps={['点交叉点落子', '右侧页签：解说 / 分析 / 棋谱', '顶部：悔棋 / 提示 / 复盘']}
      />

      <main className="layout">
        <section className="board-column">
          <div className="board-frame">
            <Board
              board={board}
              lastMove={game.last_move}
              winLine={game.win_line}
              humanColor={game.human_color ?? 1}
              interactive={canAct}
              onPlace={play}
              hintPoint={hintPoint}
              banned={game.banned}
              heat={heat}
              predict={predictPoints}
              ghost={game.pending ? { x: game.pending.x, y: game.pending.y, color: game.pending.color } : null}
              focus={focus}
            />
            {busy && (
              <div className="board-overlay">
                <span className="spinner" />
                <p>搜索中…</p>
              </div>
            )}
          </div>

          <div className="board-caption">
            <span>
              {game.status === 'finished'
                ? '对局结束'
                : game.pending
                  ? '已计算完成，等待确认'
                  : '点击交叉点落子'}
            </span>
            {game.status === 'finished' && game.result_reason ? (
              <span className={`result-note ${game.ban_loss ? 'is-ban' : ''}`}>{game.result_reason}</span>
            ) : hintPoint ? (
              <span className="hint-note">
                建议 {hintPoint.x},{hintPoint.y}
              </span>
            ) : null}
          </div>

          <div className="board-tools">
            <button
              type="button"
              className={`heat-toggle ${predictOn ? 'is-on' : ''}`}
              data-tool="predict"
              onClick={togglePredict}
              disabled={game.status !== 'playing'}
            >
              {predictOn ? '收起落点预测' : '落点预测'}
            </button>
            <button
              type="button"
              className={`heat-toggle ${heat ? 'is-on' : ''}`}
              data-tool="heat"
              onClick={toggleHeat}
              disabled={heatBusy}
            >
              {heatBusy ? '扫描中…' : heat ? '收起网络视角' : '网络视角'}
            </button>

            {predictOn && predictTop.length > 0 && (
              <span className="heat-legend" data-legend="predict">
                {insight?.prediction?.side === game.human_color ? '你的可能落点：' : '对手可能落点：'}
                {predictTop.map((p) => (
                  <b key={`${p.x}-${p.y}`} title={p.why}>
                    {p.x},{p.y}
                    <i>{Math.round(p.share * 100)}</i>
                  </b>
                ))}
              </span>
            )}
            {heat && heatTop.length > 0 && (
              <span className="heat-legend" data-legend="heat">
                网络最看好的五个点：
                {heatTop.map((p) => (
                  <b key={`${p.x}-${p.y}`}>
                    {p.x},{p.y}
                    <i>{Math.round(p.score * 100)}</i>
                  </b>
                ))}
              </span>
            )}
          </div>
        </section>

        <aside className="info-column">
          <div className="info-tabs" role="tablist">
            {TABS.map((t) => (
              <button
                key={t.key}
                type="button"
                role="tab"
                aria-selected={tab === t.key}
                className={`info-tab ${tab === t.key ? 'is-active' : ''}`}
                data-tab={t.key}
                onClick={() => setTab(t.key)}
              >
                {t.label}
              </button>
            ))}
          </div>

          <div className="info-body" data-panel={tab}>
            {tab === 'note' && (
              <>
                {review && (
                  <div className="banner banner-review">
                    <b>复盘</b>
                    <p>{review}</p>
                    <button type="button" className="banner-close" onClick={() => setReview('')}>
                      收起
                    </button>
                  </div>
                )}
                {learning && <CoachPanel coach={coach} tactics={tactics} agent={agent} />}
                <ExplainCard
                  explain={explainData}
                  pending={Boolean(game.pending)}
                  busy={busy}
                  onResume={resume}
                  onClose={explainData && !game.pending ? () => setExplain(null) : undefined}
                />
              </>
            )}

            {tab === 'analysis' && (
              <div className="panel-stack">
                <div className="chart-block">
                  <WinrateChart series={series} humanColor={game.human_color ?? 1} onPick={explainPly} />
                </div>
                <AnalysisPanel
                  insight={insight}
                  humanColor={game.human_color ?? 1}
                  onPickPoint={(pt) => {
                    setFocus({ x: pt.x, y: pt.y })
                    setPredictOn(true)
                  }}
                  onPickPly={explainPly}
                />
              </div>
            )}

            {tab === 'moves' && (
              <MoveFeed
                moves={game.moves}
                agent={agent}
                onPick={explainPly}
                activeIndex={explain?.index}
              />
            )}
          </div>
        </aside>
      </main>
    </div>
  )
}
