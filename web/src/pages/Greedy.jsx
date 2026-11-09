import { useEffect, useMemo, useRef, useState } from 'react'
import CorpusCarousel from '../components/CorpusCarousel'
import { api } from '../lib/api'

const MAX_TEXT = 64 * 1024

const fmtValue = (v) => {
  if (v === null || v === undefined) return '—'
  if (v >= 1e8) return `${(v / 1e8).toFixed(1)} 亿`
  if (v >= 1e6) return `${(v / 1e6).toFixed(1)} 百万`
  if (v >= 1e4) return `${(v / 1e4).toFixed(1)} 万`
  if (v >= 10) return String(Math.round(v))
  if (v < 0.5) return '≈0'
  return v.toFixed(1)
}

// 分值跨了七个数量级，线性长度看不出差别，取对数。
// 0 也留 2% 的宽度：一行里如果只剩一根条，读的人会以为另一根压根没画。
const barWidth = (v) => {
  const pct = v ? (Math.log10(v + 1) / Math.log10(1e8 + 1)) * 100 : 0
  return Math.max(2, Math.min(100, pct))
}

const pct = (v) => `${Math.round((v ?? 0) * 100)}%`

export default function Greedy() {
  const [status, setStatus] = useState(null)
  const [items, setItems] = useState([])
  const [summary, setSummary] = useState(null)
  const [activeId, setActiveId] = useState(null)
  const [error, setError] = useState('')

  const [training, setTraining] = useState(false)
  const [stage, setStage] = useState('')
  const [live, setLive] = useState([])
  const [result, setResult] = useState(null)

  const [uploading, setUploading] = useState(false)
  const [pasteOpen, setPasteOpen] = useState(false)
  const [pasteName, setPasteName] = useState('')
  const [pasteText, setPasteText] = useState('')
  const [dragging, setDragging] = useState(false)

  const fileRef = useRef(null)
  const abortRef = useRef(null)

  useEffect(() => {
    Promise.allSettled([api.greedyStatus(), api.greedyCorpus(225)]).then(([st, corpus]) => {
      if (st.status === 'fulfilled') setStatus(st.value)
      if (corpus.status === 'fulfilled') {
        setItems(corpus.value.items ?? [])
        setSummary(corpus.value.summary ?? null)
        // 第一次进来默认选中第一盘；之后由用户在列表或轮播里选
        if (corpus.value.items?.length) setActiveId(corpus.value.items[0].id)
      }
      if (st.status === 'rejected') setError(st.reason?.message ?? '状态读取失败')
    })
    return () => abortRef.current?.abort()
  }, [])

  const model = status?.model
  const params = status?.params

  const train = async () => {
    if (training) return
    setTraining(true)
    setError('')
    setLive([])
    setResult(null)
    setStage('准备训练')
    const controller = new AbortController()
    abortRef.current = controller
    try {
      await api.trainGreedy((ev) => {
        if (ev.type === 'stage') setStage(ev.label)
        else if (ev.type === 'epoch') setLive((prev) => [...prev, ev])
        else if (ev.type === 'done') {
          setResult(ev.result)
          setStatus((prev) => (prev ? { ...prev, model: ev.model } : prev))
          setStage('训练完成')
        } else if (ev.type === 'error') {
          setError(ev.message)
          setStage('训练失败')
        }
      }, controller.signal)
    } catch (e) {
      if (e.name !== 'AbortError') setError(e.message)
    } finally {
      setTraining(false)
      abortRef.current = null
    }
  }

  const stop = () => {
    abortRef.current?.abort()
    abortRef.current = null
    setTraining(false)
    setStage('已中止')
  }

  const reset = async () => {
    setError('')
    try {
      const res = await api.resetGreedy()
      setStatus((prev) => (prev ? { ...prev, model: res.model } : prev))
      setResult(null)
      setLive([])
      setStage('')
    } catch (e) {
      setError(e.message)
    }
  }

  const upload = async (name, text) => {
    if (!text.trim()) return
    setUploading(true)
    setError('')
    try {
      const res = await api.uploadCorpus(name || '上传棋谱', text)
      setItems((prev) => [res.item, ...prev])
      setSummary(res.summary)
      setActiveId(res.item.id)
      setPasteText('')
      setPasteName('')
      setPasteOpen(false)
    } catch (e) {
      setError(e.message)
    } finally {
      setUploading(false)
    }
  }

  const onFile = async (file) => {
    if (!file) return
    if (file.size > MAX_TEXT) {
      setError(`文件太大：最多 ${MAX_TEXT / 1024} KB`)
      return
    }
    const text = await file.text()
    upload(file.name.replace(/\.[^.]+$/, ''), text)
  }

  const remove = async (item) => {
    setError('')
    try {
      const res = await api.deleteCorpus(item.id)
      setItems((prev) => prev.filter((i) => i.id !== item.id))
      setSummary(res.summary)
      if (activeId === item.id) setActiveId(null)
    } catch (e) {
      setError(e.message)
    }
  }

  const curve = useMemo(() => {
    const points = live.length ? live : model?.curve ?? []
    if (points.length < 2) return null
    const w = 260
    const h = 64
    const max = Math.max(...points.map((p) => p.loss))
    const min = Math.min(...points.map((p) => p.loss))
    const span = Math.max(0.0001, max - min)
    const xy = points.map((p, i) => {
      const x = (i / (points.length - 1)) * w
      const y = h - ((p.loss - min) / span) * (h - 8) - 4
      return `${x.toFixed(1)},${y.toFixed(1)}`
    })
    return { w, h, path: `M${xy.join(' L')}`, points }
  }, [live, model])

  const evalBefore = result?.eval_before ?? model?.eval_before
  const evalAfter = result?.eval ?? model?.eval
  const trained = model?.trained

  return (
    <div className="greedy">
      <header className="page-head">
        <div>
          <h2>贪心学习</h2>
        </div>
        <div className="greedy-head-badges">
          <span className={`badge ${trained ? 'badge-on' : 'badge-off'}`}>
            {trained ? '已训练' : '未训练'}
          </span>
          <span className="badge">{model?.parameters ?? 12} 个参数</span>
          <span className="badge">{summary?.total ?? 0} 盘棋谱</span>
        </div>
      </header>

      {error && (
        <div className="banner banner-error" role="alert">
          {error}
        </div>
      )}

      <div className="greedy-grid">
        <section className="panel greedy-gallery">
          <div className="panel-head">
            <h2>棋谱展台</h2>
          </div>
          <CorpusCarousel items={items} activeId={activeId} onActive={setActiveId} />
        </section>

        <aside className="greedy-side">
          <section className="panel greedy-train">
            <div className="panel-head">
              <h2>训练台</h2>
              <span className="panel-note">
                {params ? `学习率 0.04 · ${params.epochs} 轮` : ''}
              </span>
            </div>

            <div className="greedy-train-actions">
              <button type="button" className="btn btn-primary" onClick={train} disabled={training}>
                {training ? '训练中…' : trained ? '重新学习' : '开始学习'}
              </button>
              {training && (
                <button type="button" className="btn" onClick={stop}>
                  中止
                </button>
              )}
              {trained && !training && (
                <button type="button" className="btn" onClick={reset}>
                  还原手写
                </button>
              )}
            </div>

            <p className={`greedy-stage ${training ? 'is-live' : ''}`}>
              {training && <span className="spinner" />}
              {stage || (trained ? '已加载上次训练结果' : '尚未训练，当前等同手写分值表')}
            </p>

            {training && live.length > 0 && (
              <div className="greedy-progress">
                <span style={{ width: `${(live.length / (params?.epochs ?? 12)) * 100}%` }} />
              </div>
            )}

            {curve && (
              <div className="greedy-curve">
                <svg viewBox={`0 0 ${curve.w} ${curve.h}`} width="100%" height="64" role="img">
                  <path d={curve.path} fill="none" stroke="var(--seal)" strokeWidth="1.6" />
                </svg>
                <div className="greedy-curve-foot">
                  <span>交叉熵</span>
                  <span>
                    {curve.points[0].loss.toFixed(3)} → {curve.points[curve.points.length - 1].loss.toFixed(3)}
                  </span>
                </div>
              </div>
            )}

            {result && (
              <p className="greedy-done">
                用时 {result.seconds}s · {result.games} 盘 / {result.samples} 个局面 · 复现率{' '}
                {pct(result.eval_before?.top1)} → <b>{pct(result.eval?.top1)}</b>
              </p>
            )}
          </section>

          <section className="panel greedy-metrics">
            <div className="panel-head">
              <h2>学习效果</h2>
            </div>
            <div className="metric-grid">
              <div className="metric">
                <span>实着命中率</span>
                <b>{evalAfter ? pct(evalAfter.top1) : '—'}</b>
                <em>{evalBefore ? `训练前 ${pct(evalBefore.top1)}` : '还没训练'}</em>
              </div>
              <div className="metric">
                <span>前三命中</span>
                <b>{evalAfter ? pct(evalAfter.top3) : '—'}</b>
                <em>{evalBefore ? `训练前 ${pct(evalBefore.top3)}` : '取候选前 3'}</em>
              </div>
              <div className="metric">
                <span>平均排名</span>
                <b>{evalAfter ? evalAfter.rank.toFixed(2) : '—'}</b>
                <em>{evalBefore ? `训练前 ${evalBefore.rank.toFixed(2)}` : '越小越准'}</em>
              </div>
              <div className="metric">
                <span>防守系数 α</span>
                <b>{model?.alpha?.toFixed(2) ?? '0.90'}</b>
                <em>手写方案是 0.90</em>
              </div>
            </div>
          </section>
        </aside>
      </div>

      <section className="panel greedy-weights">
        <div className="panel-head">
          <h2>棋形分值表</h2>
          <span className="panel-note">灰＝手写　朱＝学到（取对数画长度）</span>
        </div>
        <div className="weight-legend">
          <span>
            <i className="swatch swatch-hand" />
            手写
          </span>
          <span>
            <i className="swatch swatch-learn" />
            学到
          </span>
        </div>
        <div className="weight-table">
          {/* 两列数字长得一样，没有表头就得回去看图例才分得清谁是谁 */}
          <div className="weight-row is-head">
            <span />
            <span />
            <span className="weight-num">手写</span>
            <span className="weight-num">学到</span>
          </div>
          {(model?.table ?? []).map((row) => (
            <div className="weight-row" key={row.rank}>
              <span className="weight-label">{row.label}</span>
              <div className="weight-bars">
                <span className="weight-bar is-hand" style={{ width: `${barWidth(row.hand)}%` }} />
                <span className="weight-bar is-learn" style={{ width: `${barWidth(row.learned)}%` }} />
              </div>
              <span className="weight-num">{fmtValue(row.hand)}</span>
              <span className={`weight-num is-learn ${row.delta > 0.05 ? 'is-up' : row.delta < -0.05 ? 'is-down' : ''}`}>
                {fmtValue(row.learned)}
              </span>
            </div>
          ))}
        </div>
      </section>

      <section className="panel greedy-corpus">
        <div className="panel-head">
          <h2>棋谱库</h2>
          <span className="panel-note">
            {summary ? `内置 ${summary.builtin} · 上传 ${summary.upload} · 共 ${summary.plies} 手` : ''}
          </span>
        </div>

        <div className="corpus-layout">
          <ul className="corpus-list">
            {items.map((item) => (
              <li key={item.id} className={item.id === activeId ? 'is-active' : ''}>
                <button type="button" className="corpus-main" onClick={() => setActiveId(item.id)}>
                  <span className={`corpus-tag is-${item.source}`}>
                    {item.source === 'builtin' ? '内置' : '上传'}
                  </span>
                  <span className="corpus-name">{item.name}</span>
                  <span className="corpus-meta">{item.plies} 手</span>
                  <span className="corpus-meta corpus-note">{item.note}</span>
                </button>
                {item.source === 'upload' && (
                  <button type="button" className="corpus-del" onClick={() => remove(item)} title="删除">
                    ×
                  </button>
                )}
              </li>
            ))}
            {items.length === 0 && <li className="empty-note">还没有棋谱。</li>}
          </ul>

          <div className="corpus-upload">
            <div
              className={`upload-zone ${dragging ? 'is-drag' : ''}`}
              onDragOver={(e) => {
                e.preventDefault()
                setDragging(true)
              }}
              onDragLeave={() => setDragging(false)}
              onDrop={(e) => {
                e.preventDefault()
                setDragging(false)
                onFile(e.dataTransfer.files?.[0])
              }}
            >
              <b>拖入棋谱文件</b>
              <span>.sgf / .txt / .json，最大 {MAX_TEXT / 1024} KB</span>
              <button type="button" className="btn" onClick={() => fileRef.current?.click()} disabled={uploading}>
                {uploading ? '解析中…' : '选择文件'}
              </button>
              <input
                ref={fileRef}
                type="file"
                className="visually-hidden"
                accept=".sgf,.txt,.json"
                onChange={(e) => {
                  onFile(e.target.files?.[0])
                  e.target.value = ''
                }}
              />
              <button type="button" className="linkish" onClick={() => setPasteOpen((v) => !v)}>
                {pasteOpen ? '收起' : '或者直接粘贴文本'}
              </button>
            </div>

            {pasteOpen && (
              <div className="upload-paste">
                <input
                  className="input"
                  placeholder="给这盘棋起个名字"
                  value={pasteName}
                  maxLength={40}
                  onChange={(e) => setPasteName(e.target.value)}
                />
                <textarea
                  className="textarea"
                  rows={5}
                  placeholder={'(;GM[1]SZ[15];B[hh];W[hi]…)\n或者：H8 H9 I9 G9 …\n或者：[7,7] [8,8] …'}
                  value={pasteText}
                  onChange={(e) => setPasteText(e.target.value)}
                />
                <button
                  type="button"
                  className="btn btn-primary"
                  disabled={uploading || !pasteText.trim()}
                  onClick={() => upload(pasteName, pasteText)}
                >
                  解析并入库
                </button>
              </div>
            )}

          </div>
        </div>
      </section>
    </div>
  )
}
