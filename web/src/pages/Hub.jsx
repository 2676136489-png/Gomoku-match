import { useEffect, useState } from 'react'
import { api } from '../lib/api'

const MODULES = [
  {
    path: '/online',
    index: '01',
    title: '在线对战',
    tag: '实时 · 计分',
    desc: '与真人实时对局。服务端权威计时，断线可续，胜负均计入 Elo 等级分。',
  },
  {
    path: '/play',
    index: '02',
    title: 'AI 对弈',
    tag: '完整对局',
    desc: '自主选择执黑或执白，与引擎完成整局。支持切换风格、调整强度、悔棋、提示与复盘。',
  },
  {
    path: '/learn',
    index: '03',
    title: '学习模式',
    tag: '教练点评',
    desc: '同样的对局，逐手标注棋形，说明该手形成什么、对手的活三是否已被拦截。',
  },
  {
    path: '/tutorial',
    index: '04',
    title: '教程',
    tag: '从零开始',
    desc: '规则与胜负、棋形分级、三种禁手，以及引擎如何选点。每节配一副可交互棋盘。',
  },
  {
    path: '/code',
    index: '05',
    title: '代码工坊',
    tag: '自定义策略',
    desc: '编写自己的 choose_move，或导入已有的 .py / .cpp 文件。落点、耗时与禁手判定全程可查。',
  },
  {
    path: '/arena',
    index: '06',
    title: '算法竞技场',
    tag: '对照实验',
    desc: '随机、贪心、极小化极大、神经网络两两对打，从数据上区分棋力来自搜索还是评估。',
  },
  {
    path: '/replay',
    index: '07',
    title: '棋谱',
    tag: '逐手回放',
    desc: '每局均入库。可逐手翻看、拖动进度条，查看当时的落点与耗时。',
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

      <section className="hub-grid">
        {MODULES.map((m) => (
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

      <section className="hub-lower">
        <div className="panel">
          <div className="panel-head">
            <h2>最近对局</h2>
            <span className="panel-note">点击查看回放</span>
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
            <h2>天梯</h2>
            <span className="panel-note">按等级分排序 · 仅统计计分对局</span>
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
