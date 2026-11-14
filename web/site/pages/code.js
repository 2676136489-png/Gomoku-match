/**
 * 代码工坊：写自己的 choose_move，校验后和内置引擎对弈。
 *
 * 校验是同步一次请求；对弈是 SSE 逐手推事件。支持上传 .py/.cpp、
 * 拖拽导入、历史版本模板。
 */

const { api, el, $, GomokuBoard } = window.Gomoku

const EMPTY = () => new Array(225).fill(0)
const MAX_BYTES = 512 * 1024

const LANGS = [
  { key: 'python', label: 'Python' },
  { key: 'cpp', label: 'C++' },
]

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

/* ------------------------------------------------------------ 语法高亮
   编辑器是「透明 textarea 压在高亮层上」的老方案：两层同一套字体和内边距，
   textarea 的文字颜色透明、只留光标，滚动时把 scrollTop/Left 同步给下层。
   不引第三方库——这里只需要 Python/C++ 两种语言，手写扫描器更省，也不会
   因为一个正则失配把整页打挂。 */

const escapeHtml = (s) => s.replace(/[&<>]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c]))

const PY_KW = new Set(['def', 'class', 'return', 'if', 'elif', 'else', 'for', 'while', 'break', 'continue', 'import', 'from', 'as', 'with', 'try', 'except', 'finally', 'raise', 'yield', 'lambda', 'global', 'nonlocal', 'assert', 'del', 'pass', 'in', 'is', 'not', 'and', 'or', 'async', 'await'])
const PY_CONST = new Set(['True', 'False', 'None'])
const PY_BUILTIN = new Set(['len', 'range', 'max', 'min', 'abs', 'sum', 'enumerate', 'zip', 'print', 'sorted', 'list', 'dict', 'set', 'tuple', 'int', 'float', 'str', 'bool', 'any', 'all', 'map', 'filter', 'isinstance', 'type', 'round', 'super', 'self', 'cls'])

const CPP_KW = new Set(['if', 'else', 'for', 'while', 'do', 'switch', 'case', 'default', 'break', 'continue', 'return', 'class', 'struct', 'public', 'private', 'protected', 'namespace', 'using', 'template', 'typename', 'try', 'catch', 'throw', 'new', 'delete', 'this', 'virtual', 'override', 'operator', 'sizeof', 'static', 'const', 'constexpr', 'inline', 'explicit', 'enum', 'typedef', 'auto', 'nullptr', 'true', 'false'])
const CPP_TYPE = new Set(['void', 'int', 'char', 'bool', 'float', 'double', 'long', 'short', 'unsigned', 'signed', 'size_t', 'uint8_t', 'uint32_t', 'int8_t', 'int32_t', 'vector', 'string', 'array', 'pair'])
const CPP_BUILTIN = new Set(['printf', 'cout', 'cin', 'endl', 'push_back', 'emplace_back', 'size', 'begin', 'end', 'clear', 'empty', 'resize', 'fill', 'swap', 'stoi', 'to_string', 'getline', 'sqrt', 'abs', 'min', 'max'])

function escapeChunk(cls, text) {
  return cls ? `<span class="${cls}">${escapeHtml(text)}</span>` : escapeHtml(text)
}

function highlightCode(src, lang) {
  const py = lang !== 'cpp'
  const kw = py ? PY_KW : CPP_KW
  const types = py ? PY_CONST : CPP_TYPE
  let out = ''
  let i = 0
  const n = src.length
  while (i < n) {
    const c = src[i]
    const nl = src.indexOf('\n', i)

    // 注释
    if (py && c === '#') {
      const end = nl < 0 ? n : nl
      out += escapeChunk('tk-com', src.slice(i, end))
      i = end
      continue
    }
    if (!py && c === '/' && src[i + 1] === '/') {
      const end = nl < 0 ? n : nl
      out += escapeChunk('tk-com', src.slice(i, end))
      i = end
      continue
    }
    if (!py && c === '/' && src[i + 1] === '*') {
      let end = src.indexOf('*/', i + 2)
      end = end < 0 ? n : end + 2
      out += escapeChunk('tk-com', src.slice(i, end))
      i = end
      continue
    }
    if (!py && c === '#') {
      const end = nl < 0 ? n : nl
      out += escapeChunk('tk-pre', src.slice(i, end))
      i = end
      continue
    }

    // 字符串（三引号 / 单双引号）
    if (c === '"' || c === "'") {
      const triple = src.slice(i, i + 3)
      if (triple === c + c + c) {
        let end = src.indexOf(triple, i + 3)
        end = end < 0 ? n : end + 3
        out += escapeChunk('tk-str', src.slice(i, end))
        i = end
        continue
      }
      let j = i + 1
      while (j < n && src[j] !== c && src[j] !== '\n') {
        if (src[j] === '\\') j += 1
        j += 1
      }
      const end = Math.min(j + 1, n)
      out += escapeChunk('tk-str', src.slice(i, end))
      i = end
      continue
    }

    // 数字
    if (c >= '0' && c <= '9') {
      let j = i
      while (j < n && /[0-9a-fA-FxXoObB_.]/.test(src[j])) j += 1
      out += escapeChunk('tk-num', src.slice(i, j))
      i = j
      continue
    }

    // 标识符 / 关键字
    if (/[A-Za-z_]/.test(c)) {
      let j = i
      while (j < n && /\w/.test(src[j])) j += 1
      const word = src.slice(i, j)
      if (kw.has(word)) out += escapeChunk('tk-kw', word)
      else if (types.has(word)) out += escapeChunk('tk-type', word)
      else if ((py ? PY_BUILTIN : CPP_BUILTIN).has(word)) out += escapeChunk('tk-fn', word)
      else if (src[j] === '(') out += escapeChunk('tk-call', word)
      else out += escapeHtml(word)
      i = j
      continue
    }

    out += escapeHtml(c)
    i += 1
  }
  return out
}


const state = {
  samples: null,
  lang: 'python',
  code: '',
  showArchive: false,
  fileName: '',
  fileSize: 0,
  opponent: 'minimax',
  difficulty: 3,
  codeColor: 1,
  rule: 'renju',
  timeout: 2,
  avoidBans: true,
  verify: null,
  checking: false,
  running: false,
  error: '',
  board: EMPTY(),
  moves: [],
  startInfo: null,
  result: null,
}

let config = null
let board = null
let node = {}
let abortRef = null

/* ------------------------------------------------------------- 渲染 */

function render() {
  const main = document.getElementById('main')
  main.innerHTML = ''

  const caps = (config && config.capabilities) || {}
  const cppReady = (caps.cpp && caps.cpp.available) || false
  const algorithms = (config && config.algorithms) || []

  node = {}

  const errorSlot = el('div', { class: 'banner banner-error', role: 'alert', hidden: !state.error })
  if (state.error) errorSlot.textContent = state.error

  // 工具条
  const langSeg = el('div', { class: 'seg-group seg-tight' },
    LANGS.map((l) =>
      el('button', {
        class: `seg ${state.lang === l.key ? 'is-active' : ''}`,
        type: 'button',
        disabled: l.key === 'cpp' && !cppReady,
        title: l.key === 'cpp' && !cppReady ? (caps.cpp && caps.cpp.detail) || '' : '',
        onclick: () => switchLang(l.key),
      }, l.label)))
  node.langSeg = langSeg

  const fileName = el('span', { class: 'code-file' },
    state.fileName ? state.fileName : '内置模板',
    state.fileName ? el('em', {}, sizeText(state.fileSize)) : null)
  node.fileName = fileName

  const lines = state.code ? state.code.split('\n').length : 0
  node.lines = el('span', { class: 'code-lines' }, `${lines} 行`)
  node.flag = el('span', { className: `code-flag ${(state.verify && state.verify.ok) ? 'is-ok' : ''}` },
    state.verify && state.verify.ok ? '已校验' : '未校验')

  // 透明 textarea 压在高亮层上：文字颜色交给 pre，textarea 只出光标和选区
  const textarea = el('textarea', { rows: 24, spellcheck: 'false', wrap: 'off' })
  textarea.value = state.code
  const hl = el('pre', { class: 'code-hl', 'aria-hidden': 'true' })
  const syncEditor = () => {
    hl.innerHTML = highlightCode(textarea.value, state.lang) + '\n'
    hl.scrollTop = textarea.scrollTop
    hl.scrollLeft = textarea.scrollLeft
  }
  textarea.addEventListener('input', () => {
    state.code = textarea.value
    node.lines.textContent = `${state.code ? state.code.split('\n').length : 0} 行`
    syncEditor()
  })
  textarea.addEventListener('scroll', () => {
    hl.scrollTop = textarea.scrollTop
    hl.scrollLeft = textarea.scrollLeft
  })
  node.textarea = textarea
  syncEditor()

  const drop = el('div', { class: 'code-drop' })
  node.drop = drop
  const editor = el('div', { class: 'code-editor' }, hl, textarea)
  drop.append(editor)
  drop.addEventListener('dragover', (e) => {
    e.preventDefault()
    drop.classList.add('is-over')
  })
  drop.addEventListener('dragleave', () => drop.classList.remove('is-over'))
  drop.addEventListener('drop', (e) => {
    e.preventDefault()
    drop.classList.remove('is-over')
    importFile(e.dataTransfer.files && e.dataTransfer.files[0])
  })

  node.archiveSlot = el('div', {})
  renderArchive()

  // 校验/对弈按钮
  const verifyBtn = el('button', { class: 'btn', type: 'button', disabled: state.checking || state.running, onclick: doVerify },
    state.checking ? '校验中…' : '校验代码')
  const runBtn = el('button', { class: 'btn btn-primary', type: 'button', onclick: run, disabled: state.running || (state.lang === 'cpp' && !cppReady) },
    state.running ? '对弈中…' : '开始对弈')
  node.verifyBtn = verifyBtn
  node.runBtn = runBtn
  const stopBtn = state.running
    ? el('button', { class: 'btn', type: 'button', onclick: stopRun }, '中止')
    : null

  node.verifySlot = el('div', {})
  renderVerify()

  const fileInput = el('input', { class: 'visually-hidden', type: 'file', accept: '.py,.pyw,.cpp,.cc,.cxx,.h,.hpp' })
  fileInput.addEventListener('change', () => {
    importFile(fileInput.files && fileInput.files[0])
    fileInput.value = ''
  })

  // 对局设置
  const opponentChips = el('div', { class: 'chips' },
    algorithms.map((a) =>
      el('button', {
        class: `chip ${state.opponent === a.key ? 'is-active' : ''}`,
        type: 'button',
        title: a.brief,
        dataset: { opponent: a.key },
        onclick: () => {
          state.opponent = a.key
          render()
        },
      }, a.label)))
  node.opponentChips = opponentChips

  const diffChips = el('div', { class: 'chips chips-4' },
    ((config && config.difficulties) || []).map((l) =>
      el('button', {
        class: `chip ${state.difficulty === l.level ? 'is-active' : ''}`,
        type: 'button',
        dataset: { level: l.level },
        onclick: () => {
          state.difficulty = l.level
          render()
        },
      }, l.label)))

  const colorChips = el('div', { class: 'chips' },
    [{ key: 1, label: '黑棋（先行）' }, { key: 2, label: '白棋（后行）' }].map((c) =>
      el('button', {
        class: `chip ${state.codeColor === c.key ? 'is-active' : ''}`,
        type: 'button',
        dataset: { color: c.key },
        onclick: () => {
          state.codeColor = c.key
          render()
        },
      }, c.label)))

  const ruleChips = el('div', { class: 'chips' },
    ((config && config.rules) || []).map((r) =>
      el('button', {
        class: `chip ${state.rule === r.key ? 'is-active' : ''}`,
        type: 'button',
        title: r.brief,
        dataset: { rule: r.key },
        onclick: () => {
          state.rule = r.key
          render()
        },
      }, r.label)))

  const timeoutLabel = el('span', {}, `每手超时 ${state.timeout}s`)
  const timeoutRange = el('input', { type: 'range', min: '0.5', max: '5', step: '0.5', value: String(state.timeout) })
  timeoutRange.addEventListener('input', () => {
    state.timeout = Number(timeoutRange.value)
    timeoutLabel.textContent = `每手超时 ${state.timeout}s`
  })

  const avoidToggle = el('input', { type: 'checkbox' })
  avoidToggle.checked = state.avoidBans
  avoidToggle.addEventListener('change', () => {
    state.avoidBans = avoidToggle.checked
  })

  const settingsPanel = el('section', { class: 'panel' },
    el('div', { class: 'panel-head' }, el('h2', {}, '对局设置')),
    el('div', { class: 'lab-settings' },
      el('label', { class: 'field' }, el('span', {}, '对手'), opponentChips),
      state.opponent === 'minimax' ? el('label', { class: 'field' }, el('span', {}, '对手强度'), diffChips) : null,
      el('label', { class: 'field' }, el('span', {}, '代码执子'), colorChips),
      el('label', { class: 'field' }, el('span', {}, '规则'), ruleChips),
      el('label', { class: 'field' }, timeoutLabel, timeoutRange),
      el('label', { class: 'toggle' }, avoidToggle,
        el('span', {}, '自动避开禁手', el('em', {}, '禁手点自动替换')))))

  // 对局区
  node.boardHolder = el('div')
  board = new GomokuBoard(node.boardHolder, {})
  board.update({ board: state.board, interactive: false, humanColor: state.codeColor })
  node.boardOverlay = el('div', { class: 'board-overlay', hidden: !state.running },
    el('span', { class: 'spinner' }), el('p', {}, state.running ? (state.moves.length ? `对弈中 · ${state.moves.length} 手` : '对弈中…') : '准备中'))
  node.statusSlot = el('div', { class: 'lab-status' })
  node.resultSlot = el('div', {})
  node.feedSlot = el('div', {})

  renderStatus()
  renderResult()
  renderFeed()

  const runSection = state.startInfo || state.running
    ? el('section', { class: 'code-run' },
        el('div', { class: 'panel code-run-board' },
          el('div', { class: 'panel-head' }, el('h2', {}, '对局'),
            el('span', { class: 'panel-note' }, state.startInfo ? `黑 ${state.startInfo.left.label} · 白 ${state.startInfo.right.label}` : '准备中')),
          el('div', { class: 'board-frame' }, node.boardHolder, node.boardOverlay),
          node.statusSlot, node.resultSlot),
        el('div', { class: 'panel lab-feed' },
          el('div', { class: 'panel-head' }, el('h2', {}, '对局过程')), node.feedSlot))
    : null

  main.append(
    el('div', { class: 'code' },
      el('section', { class: 'page-head' },
        el('div', {}, el('h2', {}, '代码工坊')),
        el('div', { class: 'lab-caps' },
          el('span', { className: `pill ${caps.python && caps.python.available ? 'pill-live' : 'pill-lose'}` },
            `Python ${caps.python && caps.python.available ? caps.python.detail : '不可用'}`),
          el('span', { className: `pill ${cppReady ? 'pill-live' : 'pill-muted'}`, title: (caps.cpp && caps.cpp.detail) || '' },
            `C++ ${cppReady ? '就绪' : '无编译器'}`))),
      errorSlot,
      el('div', { class: 'code-grid' },
        el('section', { class: 'panel code-main' },
          el('div', { class: 'code-toolbar' },
            langSeg,
            el('div', { class: 'code-meta' }, fileName, node.lines, node.flag),
            el('div', { class: 'code-tools' },
              el('button', { class: 'link-btn', type: 'button', onclick: () => fileInput.click() }, '上传文件'),
              el('button', { class: 'link-btn', type: 'button', onclick: () => loadSample(state.lang) }, '载入模板'),
              el('button', { class: 'link-btn', type: 'button', onclick: () => { state.showArchive = !state.showArchive; renderArchive() } },
                state.showArchive ? '收起存档' : '历史版本'))),
          drop,
          node.archiveSlot,
          el('div', { class: 'code-actions' }, verifyBtn, runBtn, stopBtn),
          node.verifySlot),
        el('aside', { class: 'code-rail' },
          el('section', { class: 'panel code-upload' },
            el('div', { class: 'panel-head' }, el('h2', {}, '导入代码'), el('span', { class: 'panel-note' }, '.py / .cpp')),
            fileInput,
            el('button', { class: 'btn code-upload-btn', type: 'button', onclick: () => fileInput.click() }, '选择文件…')),
          settingsPanel)),
      runSection),
  )
}

function renderArchive() {
  const slot = node.archiveSlot
  if (!slot) return
  slot.innerHTML = ''
  if (!state.showArchive || !state.samples || !state.samples.archive) return
  const archive = state.samples.archive
  const textarea = el('textarea', { rows: 14, readOnly: true, wrap: 'off' })
  textarea.value = archive.code
  const hl = el('pre', { class: 'code-hl', 'aria-hidden': 'true' })
  hl.innerHTML = highlightCode(archive.code, state.lang) + '\n'
  const sync = () => {
    hl.scrollTop = textarea.scrollTop
    hl.scrollLeft = textarea.scrollLeft
  }
  textarea.addEventListener('scroll', sync)
  slot.append(
    el('div', { class: 'lab-archive' },
      el('div', { class: 'lab-archive-head' }, el('b', {}, archive.title), el('span', {}, archive.note || '')),
      el('div', { class: 'code-editor' }, hl, textarea)),
  )
}

function renderVerify() {
  const slot = node.verifySlot
  if (!slot) return
  slot.innerHTML = ''
  if (!state.verify) return
  const v = state.verify
  if (v.ok) {
    slot.append(el('div', { class: 'lab-verify is-ok' },
      el('b', {}, '校验通过'),
      el('span', {}, `试走 (${v.move.x},${v.move.y}) · ${v.elapsed_ms}ms`)))
  } else {
    slot.append(el('div', { class: 'lab-verify is-bad' },
      el('b', {}, '校验失败'),
      el('span', {}, v.error || ''),
      v.detail ? el('pre', {}, v.detail) : null))
  }
}

function renderStatus() {
  const slot = node.statusSlot
  if (!slot) return
  slot.innerHTML = ''
  if (state.startInfo) {
    const banFixes = state.moves.filter((m) => m.ban_fixed).length
    const statusPills = [
      el('span', { class: 'pill pill-muted' }, `黑 ${state.startInfo.left.label}`),
      el('span', { class: 'pill pill-muted' }, `白 ${state.startInfo.right.label}`),
      el('span', { class: 'pill pill-muted' }, `${state.moves.length} 手`),
    ]
    if (banFixes > 0) statusPills.push(el('span', { class: 'pill pill-ban' }, `${banFixes} 手被改判禁手`))
    slot.append(...statusPills)
  } else {
    slot.append(el('span', { class: 'pill pill-idle' }, '尚未开局'))
  }
}

function renderResult() {
  const slot = node.resultSlot
  if (!slot) return
  slot.innerHTML = ''
  if (!state.result) return
  const r = state.result
  const sideName = (color) => (state.startInfo ? (color === 1 ? state.startInfo.left.label : state.startInfo.right.label) : '')
  slot.append(
    el('div', { className: `lab-result ${r.winner ? 'is-done' : ''}` },
      el('b', {}, r.winner ? `${sideName(r.winner)} 获胜` : '和棋'),
      el('span', {}, r.reason || '')),
  )
}

function renderFeed() {
  const slot = node.feedSlot
  if (!slot) return
  slot.innerHTML = ''
  if (!state.moves.length) {
    slot.append(el('p', { class: 'empty-note' }, '暂无记录'))
    return
  }
  slot.append(
    el('ol', { class: 'lab-moves' },
      state.moves.map((m) =>
        el('li', { className: m.ban_fixed ? 'is-ban' : '', dataset: { n: m.n } },
          el('span', { class: 'lab-move-n' }, String(m.n)),
          el('span', { className: `dot dot-${m.color === 1 ? 'black' : 'white'}` }),
          el('span', { class: 'lab-move-xy' }, `${m.x},${m.y}`),
          el('span', { class: 'lab-move-by' }, m.player),
          el('span', { class: 'lab-move-tail' },
            m.note ? el('span', { class: 'lab-move-note', title: m.note }, m.note) : null,
            el('span', { class: 'lab-move-ms' }, m.opening ? '—' : `${m.ms}ms`))))),
  )
}

/* ------------------------------------------------------------- 动作 */

function switchLang(next) {
  if (next === state.lang) return
  const caps = (config && config.capabilities) || {}
  const cppReady = (caps.cpp && caps.cpp.available) || false
  if (next === 'cpp' && !cppReady) {
    state.error = (caps.cpp && caps.cpp.detail) || 'C++ 编译器不可用'
    render()
    return
  }
  state.lang = next
  state.verify = null
  state.fileName = ''
  state.fileSize = 0
  if (state.samples && state.samples[next]) state.code = state.samples[next].code
  state.error = ''
  render()
}

function loadSample(key) {
  if (!state.samples || !state.samples[key]) return
  state.code = state.samples[key].code
  state.verify = null
  state.fileName = ''
  state.fileSize = 0
  state.error = ''
  render()
}

async function importFile(file) {
  if (!file) return
  state.verify = null
  state.error = ''
  if (file.size > MAX_BYTES) {
    state.error = `${file.name} 有 ${sizeText(file.size)}，超过 ${sizeText(MAX_BYTES)} 上限`
    render()
    return
  }
  const ext = (file.name.split('.').pop() || '').toLowerCase()
  const guess = EXT_LANG[ext]
  if (!guess) {
    state.error = '仅支持 .py / .cpp'
    render()
    return
  }
  let text = ''
  try {
    text = await file.text()
  } catch {
    state.error = `无法读取 ${file.name}`
    render()
    return
  }
  if (!text.trim()) {
    state.error = `${file.name} 是空的`
    render()
    return
  }
  const caps = (config && config.capabilities) || {}
  const cppReady = (caps.cpp && caps.cpp.available) || false
  state.lang = guess
  state.code = text
  state.fileName = file.name
  state.fileSize = file.size
  if (guess === 'cpp' && !cppReady) {
    state.error = `已导入 ${file.name}，C++ 编译器不可用`
  }
  render()
}

async function doVerify() {
  if (state.checking || state.running) return
  state.checking = true
  state.error = ''
  state.verify = null
  render()
  try {
    state.verify = await api.labVerify({ language: state.lang, source: state.code, timeout: state.timeout })
  } catch (e) {
    state.error = e.message
  } finally {
    state.checking = false
    render()
  }
}

function stopRun() {
  if (abortRef) abortRef.abort()
  abortRef = null
  state.running = false
  render()
}

async function run() {
  if (state.running) return
  const caps = (config && config.capabilities) || {}
  const cppReady = (caps.cpp && caps.cpp.available) || false
  if (state.lang === 'cpp' && !cppReady) {
    state.error = 'C++ 编译器不可用，请切换为 Python'
    render()
    return
  }
  state.running = true
  state.error = ''
  state.verify = null
  state.result = null
  state.moves = []
  state.startInfo = null
  state.board = EMPTY()
  render()

  const controller = new AbortController()
  abortRef = controller
  try {
    await api.labMatch(
      {
        language: state.lang,
        source: state.code,
        opponent: state.opponent,
        difficulty: state.difficulty,
        code_color: state.codeColor,
        rule: state.rule,
        timeout: state.timeout,
        avoid_bans: state.avoidBans,
      },
      (ev) => {
        if (ev.type === 'start') {
          state.startInfo = ev
          renderStatus()
        } else if (ev.type === 'move') {
          state.moves.push(ev)
          state.board[ev.y * 15 + ev.x] = ev.color
          board.update({
            board: state.board,
            lastMove: { x: ev.x, y: ev.y },
            interactive: false,
            humanColor: state.codeColor,
          })
          node.boardOverlay.querySelector('p').textContent = `对弈中 · ${state.moves.length} 手`
          renderStatus()
          renderFeed()
        } else if (ev.type === 'end') {
          state.result = ev
          state.running = false
          renderResult()
          render()
        } else if (ev.type === 'error') {
          state.error = `${ev.message}${ev.detail ? `：${ev.detail}` : ''}`
          state.running = false
          render()
        }
      },
      controller.signal,
    )
  } catch (e) {
    if (e.name !== 'AbortError') state.error = e.message
  } finally {
    state.running = false
    abortRef = null
    render()
  }
}

/* ------------------------------------------------------------- 入口 */

;(async () => {
  window.Gomoku.landscapeHint()
  const boot = await window.Gomoku.bootPage('code')
  config = boot.config
  try {
    state.samples = await api.labSamples()
    state.code = state.samples[state.lang] ? state.samples[state.lang].code : ''
  } catch (e) {
    state.error = e.message
  }
  render()
})()
