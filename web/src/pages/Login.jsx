import { useState } from 'react'
import { api } from '../lib/api'

const HIGHLIGHTS = [
  ['国际连珠规则', '三三、四四、长连三种禁手，白棋无禁手的完整实现'],
  ['引擎 + Agent 分层', '搜索给候选，模型在候选里按风格取舍'],
  ['代码实验室', '写自己的 AI，跟内置引擎逐手对弈，全程可回放'],
  ['算法竞技场', '随机 / 贪心 / 极小化极大 / 神经网络 互相对打'],
]

export default function Login({ onAuth }) {
  const [mode, setMode] = useState('login')
  const [name, setName] = useState('')
  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const submit = async (event) => {
    event.preventDefault()
    if (busy) return
    setError('')
    if (mode === 'register' && password !== confirm) {
      setError('两次输入的密码不一致')
      return
    }
    setBusy(true)
    try {
      const res = mode === 'login' ? await api.login(name, password) : await api.register(name, password)
      onAuth(res)
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  const switchMode = (next) => {
    setMode(next)
    setError('')
    setConfirm('')
  }

  return (
    <div className="auth">
      <section className="auth-intro">
        <div className="auth-brand">
          <span className="brand-mark" />
          <div>
            <h1>Gomoku Match</h1>
            <p>15×15 五子棋 · 可解释的 AI 对弈平台</p>
          </div>
        </div>

        <ul className="auth-points">
          {HIGHLIGHTS.map(([title, detail]) => (
            <li key={title}>
              <b>{title}</b>
              <span>{detail}</span>
            </li>
          ))}
        </ul>

        <p className="auth-footnote">
          账户只保存在这台服务器的数据文件里，密码用 PBKDF2 派生后存储，明文不落盘。
        </p>
      </section>

      <section className="auth-card">
        <div className="auth-tabs">
          <button
            type="button"
            className={`auth-tab ${mode === 'login' ? 'is-active' : ''}`}
            onClick={() => switchMode('login')}
          >
            登录
          </button>
          <button
            type="button"
            className={`auth-tab ${mode === 'register' ? 'is-active' : ''}`}
            onClick={() => switchMode('register')}
          >
            注册
          </button>
        </div>

        <form className="auth-form" onSubmit={submit}>
          <label className="field">
            <span>用户名</span>
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="2–20 位，中英文 / 数字 / 下划线"
              autoComplete="username"
              autoFocus
            />
          </label>

          <label className="field">
            <span>密码</span>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="至少 6 位"
              autoComplete={mode === 'login' ? 'current-password' : 'new-password'}
            />
          </label>

          {mode === 'register' && (
            <label className="field">
              <span>确认密码</span>
              <input
                type="password"
                value={confirm}
                onChange={(e) => setConfirm(e.target.value)}
                placeholder="再输一次"
                autoComplete="new-password"
              />
            </label>
          )}

          {error && <div className="banner banner-error auth-error">{error}</div>}

          <button type="submit" className="btn btn-primary auth-submit" disabled={busy}>
            {busy ? '处理中…' : mode === 'login' ? '进入棋院' : '创建账户'}
          </button>
        </form>

        <p className="auth-switch">
          {mode === 'login' ? '还没有账户？' : '已经有账户？'}
          <button type="button" onClick={() => switchMode(mode === 'login' ? 'register' : 'login')}>
            {mode === 'login' ? '注册一个' : '直接登录'}
          </button>
        </p>
      </section>
    </div>
  )
}
