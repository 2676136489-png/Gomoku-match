import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import AnalysisPanel from '../components/AnalysisPanel'
import Board from '../components/Board'
import CoachPanel from '../components/CoachPanel'
import Controls from '../components/Controls'
import ExplainCard from '../components/ExplainCard'
import MoveFeed from '../components/MoveFeed'
import RulesCard from '../components/RulesCard'
import WinrateChart from '../components/WinrateChart'
import { api } from '../lib/api'

const DEFAULTS = {
  difficulty: 3,
  persona: 'balanced',
  human_color: 1,
  use_llm: true,
  rule: 'renju',
  strict_ban: false,
  step: false,
}

export default function Play({ mode, config, onUserChange }) {
  const [settings, setSettings] = useState({ ...DEFAULTS, mode })
  const [game, setGame] = useState(null)
  const [agent, setAgent] = useState(null)
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
  const settled = useRef('')
  const insightSeq = useRef(0)

  const resetAnalysis = () => {
    setInsight(null)
    setExplain(null)
    setPendingPreview(null)
    setFocus(null)
  }

  const startGame = useCallback(
    async (override) => {
      const payload = override ?? settings
      setBusy(true)
      setError('')
      setReview('')
      setHintPoint(null)
      setCoach(null)
      setTactics(null)
      setHeat(null)
      resetAnalysis()
      settled.current = ''
      try {
        const res = await api.newGame(payload)
        setGame(res.game)
        setAgent(res.agent)
        if (res.explain) {
          setExplain(res.explain)
          setFocus({ x: res.explain.x, y: res.explain.y })
        }
      } catch (e) {
        setError(e.message)
      } finally {
        setBusy(false)
      }
    },
    [settings],
  )

  useEffect(() => {
    startGame({ ...DEFAULTS, mode })
    // 只在挂载时开一局；之后由「开始新对局」驱动
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

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
      (game.mode === 'pvp' || game.turn === game.human_color),
  )
  const canAct = humanTurn && !busy

  const status = useMemo(() => {
    if (!game) return { tone: 'idle', text: '准备中' }
    if (game.status === 'finished') {
      if (!game.winner) return { tone: 'idle', text: '和棋' }
      if (game.mode === 'pvp') return { tone: 'done', text: game.winner === 1 ? '黑棋胜' : '白棋胜' }
      if (game.winner === game.human_color) return { tone: 'win', text: '你赢了' }
      return { tone: 'lose', text: game.ban_loss ? '禁手判负' : 'Agent 获胜' }
    }
    if (busy) return { tone: 'thinking', text: 'Agent 思考中' }
    if (game.pending) return { tone: 'wait', text: '等你看完再落子' }
    return humanTurn ? { tone: 'live', text: '轮到你落子' } : { tone: 'wait', text: '等待对手' }
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
      if (!res.ready) setError('网络权重未加载')
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

  return (
    <div className="play">
      <div className="play-bar">
        <div className="play-meta">
          <span className={`pill pill-${status.tone}`}>{status.text}</span>
          {game && (
            <>
              <span className="pill pill-muted">{game.rule_label}</span>
              <span className="pill pill-muted">
                {game.mode === 'pvp' ? '双人对下' : `${game.difficulty_label} · ${game.persona_label}`}
              </span>
              {game.step && <span className="pill pill-accent">讲解模式</span>}
              <span className="pill pill-muted">第 {game.moves.length} 手</span>
            </>
          )}
        </div>
        <span className="pill pill-muted">{config?.llm?.enabled ? config.llm.model : '内置策略人格'}</span>
      </div>

      {error && (
        <div className="banner banner-error" role="alert">
          {error}
        </div>
      )}

      {review && (
        <div className="banner banner-review">
          <b>复盘</b>
          <p>{review}</p>
          <button type="button" className="banner-close" onClick={() => setReview('')}>
            收起
          </button>
        </div>
      )}

      <main className="layout">
        <section className="board-column">
          <div className="board-frame">
            <Board
              board={board}
              lastMove={game?.last_move}
              winLine={game?.win_line}
              humanColor={game?.human_color ?? 1}
              interactive={canAct}
              onPlace={play}
              hintPoint={hintPoint}
              banned={game?.banned}
              heat={heat}
              predict={predictPoints}
              ghost={game?.pending ? { x: game.pending.x, y: game.pending.y, color: game.pending.color } : null}
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
              {game?.status === 'finished'
                ? '对局结束'
                : game?.pending
                  ? '它已经算好了，等你确认'
                  : game?.mode === 'pvp'
                    ? '黑白轮换，点击棋盘落子'
                    : '点击交叉点落子'}
            </span>
            {game?.status === 'finished' && game.result_reason ? (
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
              disabled={!game || game.status !== 'playing'}
            >
              {predictOn ? '收起落点预测' : '落点预测'}
            </button>
            <button
              type="button"
              className={`heat-toggle ${heat ? 'is-on' : ''}`}
              data-tool="heat"
              onClick={toggleHeat}
              disabled={heatBusy || !game}
            >
              {heatBusy ? '扫描中…' : heat ? '收起网络视角' : '网络视角'}
            </button>

            {predictOn && predictTop.length > 0 && (
              <span className="heat-legend" data-legend="predict">
                {insight?.prediction?.side === game?.human_color ? '你大概会落在：' : '它最可能落在：'}
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

          <WinrateChart series={series} humanColor={game?.human_color ?? 1} onPick={explainPly} />

          <div className="analysis-grid">
            <AnalysisPanel
              insight={insight}
              humanColor={game?.human_color ?? 1}
              onPickPoint={(pt) => {
                setFocus({ x: pt.x, y: pt.y })
                setPredictOn(true)
              }}
              onPickPly={explainPly}
            />
            <ExplainCard
              explain={explainData}
              pending={Boolean(game?.pending)}
              busy={busy}
              onResume={resume}
              onClose={explainData ? () => setExplain(null) : undefined}
            />
          </div>

          <RulesCard game={game} />
        </section>

        <aside className="side-column">
          {learning && <CoachPanel coach={coach} tactics={tactics} agent={agent} />}
          <Controls
            config={config}
            settings={settings}
            onChange={setSettings}
            onNew={() => startGame()}
            onUndo={undo}
            onHint={askHint}
            onReview={askReview}
            busy={busy}
            canAct={canAct}
          />
          <MoveFeed moves={game?.moves} agent={agent} onPick={explainPly} activeIndex={explain?.index} />
        </aside>
      </main>
    </div>
  )
}
