import { useEffect, useState } from 'react'
import FirstRunTip from '../components/FirstRunTip'
import { Term } from '../components/Glossary'
import { api } from '../lib/api'

/**
 * 大厅。
 *
 * 入口分两档：四张主卡是需要坐下来做的事（对局、学习、复盘），
 * 下面三行是偶尔才用一次的工具。一眼能分出主次，比七张等大的卡片好选。
 */
const PRIMARY = [
  {
    path: '/online',
    index: '01',
    title: '在线对战',
    tag: '实时 · 计分',
    desc: '和真人下。服务端权威计时，断线可续，结果计入天梯。',
  },
  {
    path: '/play',
    index: '02',
    title: '人机对弈',
    tag: '与引擎',
    desc: '和引擎下。可选强度、风格与执子，对局中可悔棋、提示、复盘。',
  },
  {
    path: '/play?mode=learn',
    index: '03',
    title: '学习模式',
    tag: '教练点评',
    desc: '同样的对局，每一手都有评语：形成了什么棋形、对手的活三拦没拦住。',
  },
  {
    path: '/replay',
    index: '04',
    title: '复盘分析',
    tag: '逐手回看',
    desc: '把下过的棋逐手翻出来看：胜率怎么变、关键手是什么、当时还有哪些选择。',
  },
]

const SECONDARY = [
  {
    path: '/tutorial',
    title: '新手教程',
    desc: '从规则与胜负讲到三种禁手，每节配一副可交互棋盘。第一次接触连珠建议先看这里。',
  },
  {
    path: '/code',
    title: '代码工坊',
    desc: '写自己的 choose_move 并跑起来，Python 与 C++ 两条通道，落点与耗时全程可查。',
  },
  {
    path: '/arena',
    title: '算法竞技场',
    desc: '随机、贪心、极小化极大、神经网络两两对打，用数据区分棋力来自搜索还是评估。',
  },
]

function formatTime(seconds) {
  if (!seconds) return ''
  const date = new Date(seconds * 1000)
  const pad = (n) => String(n).padStart(2, '0')
  return `${date.getMonth() + 1}/${date.getDate()} ${pad(date.getHours())}:${pad(date.getMinutes())}`
}

const RESULT_TEXT = { win: '胜', loss: '负', draw: '和', live: '局' }

export default function Hub({ user, config, onNavigate }) {
  const [board, setBoard] = useState([])
  const stats = user?.stats ?? {}
  const history = user?.history ?? []
  const tier = stats.tier ?? { label: '一级', progress: 0, next: 1350 }

  useEffect(() => {
    api
      .leaderboard()
      .then((res) => setBoard(res.rows ?? []))
      .catch(() => setBoard([]))
  }, [])

  return (
    <div className="hub">
      <section className="hub-hero">
        <div>
          <h2>欢迎回来，{user?.name}</h2>
          <p>
            {stats.games
              ? `累计 ${stats.games} 盘，胜率 ${Math.round((stats.win_rate ?? 0) * 100)}%。`
              : '暂无对局记录，完成首局后自动统计。'}
            {stats.streak > 1 ? ` 当前连胜 ${stats.streak} 场。` : ''}
          </p>

          <div className="rank-bar">
            <span className="rank-label">{tier.label}</span>
            <div className="rank-track">
              <span style={{ width: `${Math.round((tier.progress ?? 0) * 100)}%` }} />
            </div>
            <span className="rank-next">
              {tier.next
                ? `距下一段 ${Math.max(0, Math.round(tier.next - (stats.rating ?? 1200)))} 分`
                : '已是最高段'}
            </span>
          </div>
        </div>
        <div className="hub-stats">
          {[
            ['段位分', Math.round(stats.rating ?? 1200)],
            ['对局', stats.games ?? 0],
            ['胜', stats.wins ?? 0],
            ['负', stats.losses ?? 0],
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
          '想直接下棋就点「人机对弈」或「在线对战」，前者不用等人。',
          '规则还没搞清楚就先看「新手教程」，四章带可交互棋盘。',
          '每局结束后去「复盘分析」，能看到每一步的胜率变化和原因。',
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

        <div className="panel">
          <div className="panel-head">
            <h2>
              <Term k="elo" />天梯
            </h2>
            <span className="panel-note">
              按等级分排序 · 仅<Term k="rated" />对局
            </span>
          </div>
          {board.length === 0 ? (
            <p className="empty-note">暂无上榜玩家，完成首局计分对局后显示。</p>
          ) : (
            <ul className="board-list">
              {board.map((row) => (
                <li key={row.name} className={row.name === user?.name ? 'is-me' : ''}>
                  <span className="rank">{row.rank}</span>
                  <span className="board-name">{row.name}</span>
                  <span className="board-tier">{row.tier}</span>
                  <span className="board-rate">{Math.round(row.rating)}</span>
                  <span className="history-meta">{row.rated_games} 盘</span>
                </li>
              ))}
            </ul>
          )}
        </div>
      </section>

      {config?.net?.ready && (
        <p className="hub-note">
          落点网络已就绪：{config.net.params} 个参数，{config.net.samples} 条自对弈样本训练，
          教师搜索深度 {config.net.teacher_depth}。
        </p>
      )}
    </div>
  )
}
