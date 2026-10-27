import { useCallback, useEffect, useState } from 'react'
import { api, onUnauthorized, setToken } from './lib/api'
import { navigate, useRoute } from './lib/router'
import Arena from './pages/Arena'
import Code from './pages/Code'
import ErrorBoundary from './components/ErrorBoundary'
import Hub from './pages/Hub'
import Landscape from './components/Landscape'
import Login from './pages/Login'
import Online from './pages/Online'
import Play from './pages/Play'
import Replay from './pages/Replay'
import Room from './pages/Room'
import Tutorial from './pages/Tutorial'

const NAV = [
  { path: '/hub', label: '大厅' },
  { path: '/online', label: '在线' },
  { path: '/play', label: '对弈' },
  { path: '/learn', label: '学习' },
  { path: '/tutorial', label: '教程' },
  { path: '/code', label: '代码' },
  { path: '/arena', label: '竞技场' },
  { path: '/replay', label: '棋谱' },
]

export default function App() {
  const [user, setUser] = useState(null)
  const [config, setConfig] = useState(null)
  const [booting, setBooting] = useState(true)
  const route = useRoute()

  useEffect(() => {
    onUnauthorized(() => setUser(null))
    Promise.allSettled([api.me(), api.config()]).then(([me, cfg]) => {
      if (me.status === 'fulfilled') setUser(me.value.user)
      if (cfg.status === 'fulfilled') setConfig(cfg.value)
      setBooting(false)
    })
  }, [])

  const refreshUser = useCallback(async () => {
    try {
      const res = await api.me()
      setUser(res.user)
    } catch {
      /* 拉不到就保持原样，不要因为一个统计接口把用户踢下线 */
    }
  }, [])

  const handleAuth = useCallback((payload) => {
    setToken(payload.token)
    setUser(payload.user)
    navigate('/hub')
  }, [])

  const logout = useCallback(async () => {
    try {
      await api.logout()
    } catch {
      /* 服务端删不掉 token 也要让本地登出 */
    }
    setToken('')
    setUser(null)
    navigate('/login')
  }, [])

  if (booting) {
    return (
      <>
        <Landscape />
        <div className="boot">
          <span className="spinner" />
          <p>正在连接服务端…</p>
        </div>
      </>
    )
  }

  if (!user) {
    return (
      <>
        <Landscape />
        <Login onAuth={handleAuth} />
      </>
    )
  }

  // 带参数的页面自己解析第二段：/room/abc123、/replay/abc123。
  const segments = route.segments ?? []
  const root = `/${segments[0] ?? ''}`
  const param = segments[1]
  const path = root === '/' ? '/hub' : root

  const render = () => {
    if (path === '/hub') return <Hub user={user} config={config} onNavigate={navigate} />
    if (path === '/online') return <Online user={user} />
    if (path === '/play') return <Play key="play" mode="agent" config={config} onUserChange={refreshUser} />
    if (path === '/learn') return <Play key="learn" mode="learn" config={config} onUserChange={refreshUser} />
    if (path === '/tutorial') return <Tutorial />
    if (path === '/code') return <Code config={config} />
    if (path === '/arena') return <Arena config={config} />
    if (path === '/replay') return <Replay key={param ?? 'list'} gameId={param} />
    if (path === '/room')
      return <Room key={param ?? 'room'} gameId={param} user={user} />
    return (
      <div className="notfound">
        <h2>页面不存在</h2>
        <p>路径 {route.path} 未在导航中注册。</p>
        <button type="button" className="btn btn-primary" onClick={() => navigate('/hub')}>
          回到大厅
        </button>
      </div>
    )
  }

  return (
    <>
      <Landscape />
      <div className="app">
        <header className="topbar">
          <div className="brand">
            <span className="brand-mark" />
            <div>
              <h1>Gomoku Match</h1>
              <p>国际连珠 · 棋形评估 + 搜索 + 学习型落点网络</p>
            </div>
          </div>

          <nav className="nav">
            {NAV.map((item) => (
              <button
                key={item.path}
                type="button"
                className={`nav-item ${path === item.path ? 'is-active' : ''}`}
                onClick={() => navigate(item.path)}
              >
                {item.label}
              </button>
            ))}
          </nav>

          <div className="topbar-meta">
            <button
              type="button"
              className="pill pill-rating"
              onClick={() => navigate('/online')}
              title={user.stats?.tier?.label}
            >
              {user.stats?.tier?.label ?? '一级'} · {Math.round(user.stats?.rating ?? 1200)}
            </button>
            <span className="pill pill-user">{user.name}</span>
            <button type="button" className="pill pill-quiet" onClick={logout}>
              退出
            </button>
          </div>
        </header>

        <ErrorBoundary key={route.path}>{render()}</ErrorBoundary>

        <footer className="foot">
          <span>
            引擎与 Agent 同源：先算候选，再在候选里做决策。
            <a href="/api/docs" target="_blank" rel="noreferrer">
              API
            </a>
            <a href="/api/health" target="_blank" rel="noreferrer">
              服务状态
            </a>
          </span>
        </footer>
      </div>
    </>
  )
}
