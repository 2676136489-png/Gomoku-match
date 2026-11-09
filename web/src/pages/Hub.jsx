import { useEffect, useState } from 'react'
import FirstRunTip from '../components/FirstRunTip'
import { Term } from '../components/Glossary'
import { api } from '../lib/api'

/**
 * 大厅。
 *
 * 入口分两档：四张主卡是需要坐下来做的事（对局、学习、复盘、训练），
 * 下面三行是偶尔才用一次的工具。一眼能分出主次，比七张等大的卡片好选。
 */
const PRIMARY = [
  {
    path: '/play',
    index: '01',
    seal: '弈',
    title: '人机对弈',
    tag: '与引擎',
    desc: '和引擎下。可选强度、风格与执子，对局中可悔棋、提示、复盘。',
  },
  {
    path: '/play?mode=learn',
    index: '02',
    seal: '学',
    title: '学习模式',
    tag: '教练点评',
    desc: '同样的对局，每一手都有评语：形成了什么棋形、对手的活三拦没拦住。',
  },
  {
    path: '/replay',
    index: '03',
    seal: '谱',
    title: '复盘分析',
    tag: '逐手回看',
    desc: '把下过的棋逐手翻出来看：胜率怎么变、关键手是什么、当时还有哪些选择。',
  },
  {
    path: '/greedy',
    index: '04',
    seal: '算',
    title: '贪心学习',
    tag: '喂棋谱',
    desc: '把棋谱喂给贪心，看那张手写的分值表被改成什么样。可上传自己的棋谱。',
  },
]

const SECONDARY = [
  {
    path: '/arena',
    title: '算法竞技场',
    desc: '随机、贪心、学习型贪心、极小化极大、神经网络两两对打，用数据区分棋力来自搜索还是评估。',
  },
  {
    path: '/code',
    title: '代码工坊',
    desc: '写自己的 choose_move 并跑起来，Python 与 C++ 两条通道，落点与耗时全程可查。',
  },
  {
    path: '/tutorial',
    title: '新手教程',
    desc: '从棋规、棋形讲到禁手与开局规则，每节配一副可交互棋盘。第一次接触连珠建议先看这里。',
  },
]

function formatTime(seconds) {
  if (!seconds) return ''
  const date = new Date(seconds * 1000)
  const pad = (n) => String(n).padStart(2, '0')
  return `${date.getMonth() + 1}/${date.getDate()} ${pad(date.getHours())}:${pad(date.getMinutes())}`
}

const RESULT_TEXT = { win: '胜', loss: '负', draw: '和', live: '局' }

export default function Hub({ user, onNavigate }) {
  const [greedy, setGreedy] = useState(null)
  const stats = user?.stats ?? {}
  const history = user?.history ?? []

  useEffect(() => {
    api
      .greedyStatus()
      .then(setGreedy)
      .catch(() => setGreedy(null))
  }, [])

  const model = greedy?.model
  const corpus = greedy?.corpus
  const evalStat = model?.eval

  return (
    <div className="hub">
      <section className="hub-hero">
        <div>
          <h2>欢迎回来，{user?.name}</h2>
          <p>
            {stats.games
              ? `累计 ${stats.games} 盘，胜率 ${Math.round((stats.win_rate ?? 0) * 100)}%。`
              : '还没有对局记录，完成首局后自动统计。'}
            {stats.streak > 1 ? ` 当前连胜 ${stats.streak} 场。` : ''}
          </p>
        </div>
        <div className="hub-stats">
          {[
            ['对局', stats.games ?? 0],
            ['胜', stats.wins ?? 0],
            ['负', stats.losses ?? 0],
            ['和', stats.draws ?? 0],
            ['胜率', `${Math.round((stats.win_rate ?? 0) * 100)}%`],
          ].map(([label, value]) => (
            <div key={label} className="stat">
              <b>{value}</b>
              <span>{label}</span>
            </div>
          ))}
        </div>
      </section>

      <FirstRunTip
        id="hub"
        title="第一次来，从这里开始"
        steps={[
          '想直接下棋就点「人机对弈」，不用等人。',
          '规则还没搞清楚就先看「新手教程」，六章带可交互棋盘。',
          '好奇算法怎么选点，去「贪心学习」喂一盘棋谱，看分值表怎么被改。',
        ]}
      />

      <section className="hub-grid">
        {PRIMARY.map((m) => (
          <button key={m.path} type="button" className="module-card" onClick={() => onNavigate(m.path)}>
            <span className="module-index">{m.index}</span>
            <div className="module-head">
              <h3>{m.title}</h3>
              <span className="module-tag">{m.tag}</span>
            </div>
            <p>{m.desc}</p>
            <span className="module-go">进入 →</span>
            <span className="module-seal" aria-hidden="true">{m.seal}</span>
          </button>
        ))}
      </section>

      <section className="hub-links">
        <h3 className="hub-links-title">工具</h3>
        {SECONDARY.map((m) => (
          <button key={m.path} type="button" className="hub-link" onClick={() => onNavigate(m.path)}>
            <span className="hub-link-name">{m.title}</span>
            <span className="hub-link-desc">{m.desc}</span>
            <span className="hub-link-go">→</span>
          </button>
        ))}
      </section>

      <section className="hub-lower">
        <div className="panel">
          <div className="panel-head">
            <h2>最近对局</h2>
            <span className="panel-note">点击进入复盘</span>
          </div>
          {history.length === 0 ? (
            <p className="empty-note">暂无已结束的对局。</p>
          ) : (
            <ul className="history">
              {history.slice(0, 8).map((item, i) => (
                <li
                  key={`${item.id ?? item.at}-${i}`}
                  className={item.id ? 'is-link' : ''}
                  onClick={() => item.id && onNavigate(`/replay/${item.id}`)}
                >
                  <span className={`result result-${item.result}`}>{RESULT_TEXT[item.result] ?? '?'}</span>
                  <span className="history-main">{item.opponent}</span>
                  <span className="history-meta">{item.moves} 手</span>
                  <span className="history-meta">{formatTime(item.at)}</span>
                </li>
              ))}
            </ul>
          )}
        </div>

        <div className="panel hub-model">
          <div className="panel-head">
            <h2>学习型贪心</h2>
            <span className="panel-note">{corpus ? `${corpus.total} 盘棋谱` : '加载中'}</span>
          </div>
          {!model ? (
            <p className="empty-note">模型状态读不到，稍后刷新。</p>
          ) : (
            <>
              <p className="hub-model-state">
                {model.trained
                  ? `已用 ${model.games ?? 0} 盘棋谱训练 · ${model.samples ?? 0} 个局面`
                  : '还没训练：当前等同手写分值表'}
              </p>
              <ul className="hub-model-metrics">
                <li>
                  <b>{model.trained ? `${Math.round((evalStat?.top1 ?? 0) * 100)}%` : '—'}</b>
                  <span>复现棋谱实着</span>
                </li>
                <li>
                  <b>{model.alpha?.toFixed(2) ?? '0.90'}</b>
                  <span>防守系数 α</span>
                </li>
                <li>
                  <b>{model.parameters ?? 12}</b>
                  <span>可训练参数</span>
                </li>
              </ul>
              <button type="button" className="btn btn-primary hub-model-go" onClick={() => onNavigate('/greedy')}>
                {model.trained ? '继续喂棋谱' : '开始训练'}
              </button>
            </>
          )}
        </div>
      </section>

    </div>
  )
}
