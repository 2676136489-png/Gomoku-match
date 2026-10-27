import { useCallback, useEffect, useRef, useState } from 'react'
import Board from '../components/Board'
import CodeEditor from '../components/CodeEditor'
import { api } from '../lib/api'

const EMPTY = () => new Array(225).fill(0)
const MAX_BYTES = 512 * 1024

const LANGS = [
  { key: 'python', label: 'Python' },
  { key: 'cpp', label: 'C++' },
]

// 只认这两种语言的扩展名。少猜一点：把 .java 当 Python 传上去，
// 报的是一堆语法错，用户根本不知道是文件类型不对。
const EXT_LANG = {
  py: 'python',
  pyw: 'python',
  cpp: 'cpp',
  cc: 'cpp',
  cxx: 'cpp',
  'c++': 'cpp',
  h: 'cpp',
  hpp: 'cpp',
}

const sizeText = (n) => (n < 1024 ? `${n} B` : `${(n / 1024).toFixed(1)} KB`)

export default function Code({ config }) {
  const [samples, setSamples] = useState(null)
  const [lang, setLang] = useState('python')
  const [code, setCode] = useState('')
  const [showArchive, setShowArchive] = useState(false)
  const [fileName, setFileName] = useState('')
  const [fileSize, setFileSize] = useState(0)
  const [dragging, setDragging] = useState(false)

  const [opponent, setOpponent] = useState('minimax')
  const [difficulty, setDifficulty] = useState(3)
  const [codeColor, setCodeColor] = useState(1)
  const [rule, setRule] = useState('renju')
  const [timeout, setTimeoutMs] = useState(2)
  const [avoidBans, setAvoidBans] = useState(true)

  const [verify, setVerify] = useState(null)
  const [checking, setChecking] = useState(false)
  const [running, setRunning] = useState(false)
  const [error, setError] = useState('')

  const [board, setBoard] = useState(EMPTY)
  const [moves, setMoves] = useState([])
  const [startInfo, setStartInfo] = useState(null)
  const [result, setResult] = useState(null)
  const abortRef = useRef(null)
  const fileRef = useRef(null)

  useEffect(() => {
    api
      .labSamples()
      .then((res) => {
        setSamples(res)
        setCode(res[lang]?.code ?? '')
      })
      .catch((e) => setError(e.message))
    // 只加载一次；切语言时从 samples 里取
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const algorithms = config?.algorithms ?? []
  const caps = config?.capabilities ?? {}
  const cppReady = caps.cpp?.available ?? false

  const switchLang = (next) => {
    if (next === lang) return
    if (next === 'cpp' && !cppReady) {
      setError(caps.cpp?.detail || 'C++ 通道不可用')
      return
    }
    setLang(next)
    setVerify(null)
    setFileName('')
    setFileSize(0)
    if (samples?.[next]) setCode(samples[next].code)
  }

  const loadSample = (key) => {
    if (!samples?.[key]) return
    setCode(samples[key].code)
    setVerify(null)
    setFileName('')
    setFileSize(0)
    setError('')
  }

  /** 导入一个本地文件：按扩展名判语言，内容直接进编辑器，不落盘 */
  const importFile = useCallback(
    async (file) => {
      if (!file) return
      setVerify(null)
      setError('')
      if (file.size > MAX_BYTES) {
        setError(`${file.name} 有 ${sizeText(file.size)}，超过 ${sizeText(MAX_BYTES)} 上限`)
        return
      }
      const ext = (file.name.split('.').pop() || '').toLowerCase()
      const guess = EXT_LANG[ext]
      if (!guess) {
        setError(`认不出 .${ext} 是什么语言，只接 .py 和 .cpp（含 .cc/.cxx/.hpp）`)
        return
      }
      let text = ''
      try {
        text = await file.text()
      } catch {
        setError(`读不了 ${file.name}，可能没有读取权限`)
        return
      }
      if (!text.trim()) {
        setError(`${file.name} 是空的`)
        return
      }
      if (guess === 'cpp' && !cppReady) {
        setError(`已导入 ${file.name}，但这台机器没有可用的 C++ 编译器，暂时跑不了`)
      }
      setLang(guess)
      setCode(text)
      setFileName(file.name)
      setFileSize(file.size)
    },
    [cppReady],
  )

  const onDrop = (event) => {
    event.preventDefault()
    setDragging(false)
    importFile(event.dataTransfer?.files?.[0])
  }

  const payload = useCallback(
    () => ({ language: lang, source: code, timeout }),
    [lang, code, timeout],
  )

  const doVerify = async () => {
    if (checking || running) return
    setChecking(true)
    setError('')
    setVerify(null)
    try {
      setVerify(await api.labVerify(payload()))
    } catch (e) {
      setError(e.message)
    } finally {
      setChecking(false)
    }
  }

  const stop = () => {
    abortRef.current?.abort()
    abortRef.current = null
    setRunning(false)
  }

  const run = async () => {
    if (running) return
    if (lang === 'cpp' && !cppReady) {
      setError('没有可用的 C++ 编译器，先换成 Python')
      return
    }
    setRunning(true)
    setError('')
    setVerify(null)
    setResult(null)
    setMoves([])
    setStartInfo(null)
    setBoard(EMPTY())

    const controller = new AbortController()
    abortRef.current = controller
    try {
      await api.labMatch(
        {
          language: lang,
          source: code,
          opponent,
          difficulty,
          code_color: codeColor,
          rule,
          timeout,
          avoid_bans: avoidBans,
        },
        (ev) => {
          if (ev.type === 'start') setStartInfo(ev)
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

  const lastMove = moves.length ? { x: moves[moves.length - 1].x, y: moves[moves.length - 1].y } : null
  const banFixes = moves.filter((m) => m.ban_fixed).length
  const sideName = (color) => (startInfo ? (color === 1 ? startInfo.left.label : startInfo.right.label) : '')
  const canRun = !running && !(lang === 'cpp' && !cppReady)
  const lines = code ? code.split('\n').length : 0

  return (
    <div className="code">
      <header className="page-head">
        <div>
          <h2>代码工坊</h2>
          <p>
            写一个 <code>choose_move</code>，或者直接把手上的 .py / .cpp 拖进来。代码跑在独立子进程里，
            每手有超时，越权调用在静态校验阶段就会被拦下。
          </p>
        </div>
        <div className="lab-caps">
          <span className={`pill ${caps.python?.available ? 'pill-live' : 'pill-lose'}`}>
            Python {caps.python?.available ? caps.python.detail : '不可用'}
          </span>
          <span className={`pill ${cppReady ? 'pill-live' : 'pill-muted'}`} title={caps.cpp?.detail}>
            C++ {cppReady ? '就绪' : '无编译器'}
          </span>
        </div>
      </header>

      {error && <div className="banner banner-error">{error}</div>}

      <div className="code-grid">
        <section className="panel code-main">
          <div className="code-toolbar">
            <div className="seg-group seg-tight">
              {LANGS.map((l) => (
                <button
                  key={l.key}
                  type="button"
                  className={`seg ${lang === l.key ? 'is-active' : ''}`}
                  onClick={() => switchLang(l.key)}
                  disabled={l.key === 'cpp' && !cppReady}
                  title={l.key === 'cpp' && !cppReady ? caps.cpp?.detail : ''}
                >
                  {l.label}
                </button>
              ))}
            </div>

            <div className="code-meta">
              {fileName ? (
                <span className="code-file" title={fileName}>
                  {fileName}
                  <em>{sizeText(fileSize)}</em>
                </span>
              ) : (
                <span className="code-file code-file-builtin">内置模板</span>
              )}
              <span className="code-lines">{lines} 行</span>
              {verify?.ok ? <span className="code-flag is-ok">已校验</span> : <span className="code-flag">未校验</span>}
            </div>

            <div className="code-tools">
              <button type="button" className="link-btn" onClick={() => fileRef.current?.click()}>
                上传文件
              </button>
              <button type="button" className="link-btn" onClick={() => loadSample(lang)}>
                载入模板
              </button>
              <button type="button" className="link-btn" onClick={() => setShowArchive((v) => !v)}>
                {showArchive ? '收起存档' : '看历史版本'}
              </button>
            </div>
          </div>

          <div
            className={`code-drop ${dragging ? 'is-over' : ''}`}
            onDragOver={(e) => {
              e.preventDefault()
              setDragging(true)
            }}
            onDragLeave={() => setDragging(false)}
            onDrop={onDrop}
          >
            <CodeEditor value={code} onChange={setCode} rows={24} />
            {dragging && (
              <div className="code-drop-hint">
                <span>松手就导入</span>
              </div>
            )}
          </div>

          {showArchive && samples?.archive && (
            <div className="lab-archive">
              <div className="lab-archive-head">
                <b>{samples.archive.title}</b>
                <span>{samples.archive.note}</span>
              </div>
              <CodeEditor value={samples.archive.code} onChange={() => {}} readOnly rows={14} />
            </div>
          )}

          <div className="code-actions">
            <button type="button" className="btn" onClick={doVerify} disabled={checking || running}>
              {checking ? '校验中…' : '校验代码'}
            </button>
            <button type="button" className="btn btn-primary" onClick={run} disabled={!canRun}>
              {running ? '对弈中…' : '开始对弈'}
            </button>
            {running && (
              <button type="button" className="btn" onClick={stop}>
                中止
              </button>
            )}
            <span className="code-actions-note">校验只空盘试走一手，不等同于对局结果</span>
          </div>

          {verify && (
            <div className={`lab-verify ${verify.ok ? 'is-ok' : 'is-bad'}`}>
              {verify.ok ? (
                <>
                  <b>校验通过</b>
                  <span>
                    空盘试走一手 → ({verify.move?.x},{verify.move?.y})，耗时 {verify.elapsed_ms} ms
                  </span>
                </>
              ) : (
                <>
                  <b>校验失败</b>
                  <span>{verify.error}</span>
                  {verify.detail && <pre>{verify.detail}</pre>}
                </>
              )}
            </div>
          )}
        </section>

        <aside className="code-rail">
          <section className="panel code-upload">
            <div className="panel-head">
              <h2>导入代码</h2>
              <span className="panel-note">.py / .cpp</span>
            </div>
            <p className="code-upload-note">
              拖文件到左边编辑器，或者从这里选。文件只读进浏览器，跟手写的是同一条路。
            </p>
            <input
              ref={fileRef}
              type="file"
              className="visually-hidden"
              accept=".py,.pyw,.cpp,.cc,.cxx,.h,.hpp"
              onChange={(e) => {
                importFile(e.target.files?.[0])
                e.target.value = ''
              }}
            />
            <button type="button" className="btn code-upload-btn" onClick={() => fileRef.current?.click()}>
              选择文件…
            </button>
            <p className="code-upload-hint">最大 {sizeText(MAX_BYTES)}；扩展名决定语言，跟着换页签</p>
          </section>

          <section className="panel">
            <div className="panel-head">
              <h2>对局设置</h2>
            </div>
            <div className="lab-settings">
              <label className="field">
                <span>对手</span>
                <div className="chips">
                  {algorithms.map((a) => (
                    <button
                      key={a.key}
                      type="button"
                      className={`chip ${opponent === a.key ? 'is-active' : ''}`}
                      onClick={() => setOpponent(a.key)}
                      title={a.brief}
                    >
                      {a.label}
                    </button>
                  ))}
                </div>
              </label>

              {opponent === 'minimax' && (
                <label className="field">
                  <span>对手强度</span>
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
              )}

              <label className="field">
                <span>你的代码执</span>
                <div className="chips">
                  {[
                    { key: 1, label: '黑棋（先行）' },
                    { key: 2, label: '白棋（后行）' },
                  ].map((c) => (
                    <button
                      key={c.key}
                      type="button"
                      className={`chip ${codeColor === c.key ? 'is-active' : ''}`}
                      onClick={() => setCodeColor(c.key)}
                    >
                      {c.label}
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
                      title={r.brief}
                    >
                      {r.label}
                    </button>
                  ))}
                </div>
              </label>

              <label className="field">
                <span>每手超时 {timeout}s</span>
                <input
                  type="range"
                  min="0.5"
                  max="5"
                  step="0.5"
                  value={timeout}
                  onChange={(e) => setTimeoutMs(Number(e.target.value))}
                />
              </label>

              <label className="toggle">
                <input type="checkbox" checked={avoidBans} onChange={(e) => setAvoidBans(e.target.checked)} />
                <span>
                  自动避开禁手
                  <em>你返回禁手点时改走贪心推荐的合法点，并在过程里标注</em>
                </span>
              </label>
            </div>
          </section>
        </aside>
      </div>

      {(startInfo || running) && (
        <section className="code-run">
          <div className="panel code-run-board">
            <div className="panel-head">
              <h2>对局</h2>
              <span className="panel-note">
                {startInfo ? `黑 ${startInfo.left.label} · 白 ${startInfo.right.label}` : '准备中'}
              </span>
            </div>
            <div className="board-frame">
              <Board
                board={board}
                lastMove={lastMove}
                winLine={[]}
                humanColor={codeColor}
                interactive={false}
                onPlace={() => {}}
                hintPoint={null}
                banned={[]}
              />
              {running && (
                <div className="board-overlay">
                  <span className="spinner" />
                  <p>对弈中…</p>
                </div>
              )}
            </div>
            <div className="lab-status">
              {startInfo ? (
                <>
                  <span className="pill pill-muted">黑 {startInfo.left.label}</span>
                  <span className="pill pill-muted">白 {startInfo.right.label}</span>
                  <span className="pill pill-muted">{moves.length} 手</span>
                  {banFixes > 0 && <span className="pill pill-ban">{banFixes} 手被改判禁手</span>}
                </>
              ) : (
                <span className="pill pill-idle">尚未开局</span>
              )}
            </div>
            {result && (
              <div className={`lab-result ${result.winner ? 'is-done' : ''}`}>
                <b>{result.winner ? `${sideName(result.winner)} 获胜` : '和棋'}</b>
                <span>{result.reason}</span>
              </div>
            )}
          </div>

          <div className="panel lab-feed">
            <div className="panel-head">
              <h2>对局过程</h2>
              <span className="panel-note">每手的落点、耗时与备注</span>
            </div>
            {moves.length === 0 ? (
              <p className="empty-note">开始对弈后，这里会逐手冒出来。</p>
            ) : (
              <ol className="lab-moves">
                {moves.map((m) => (
                  <li key={m.n} className={m.ban_fixed ? 'is-ban' : ''}>
                    <span className="lab-move-n">{m.n}</span>
                    <span className={`dot dot-${m.color === 1 ? 'black' : 'white'}`} />
                    <span className="lab-move-xy">
                      {m.x},{m.y}
                    </span>
                    <span className="lab-move-by">{m.player}</span>
                    <span className="lab-move-tail">
                      {m.note && (
                        <span className="lab-move-note" title={m.note}>
                          {m.note}
                        </span>
                      )}
                      <span className="lab-move-ms">{m.opening ? '—' : `${m.ms}ms`}</span>
                    </span>
                  </li>
                ))}
              </ol>
            )}
          </div>
        </section>
      )}
    </div>
  )
}
