import { useState } from 'react'

/**
 * 首次进入某个页面时的说明。
 *
 * 只在第一次显示，关掉后写 localStorage 不再出现。用 id 区分页面，
 * 加新引导时换个 id 就是新的一条，不会影响已经看过的用户。
 *
 * compact 用在纵向空间紧张的页面（对局页）：三步压成一行，
 * 仍然可读，但不抢棋盘的位置。
 */
export default function FirstRunTip({ id, title, steps, compact = false }) {
  const key = `gomoku.tip.${id}`
  const [open, setOpen] = useState(() => {
    try {
      return !localStorage.getItem(key)
    } catch {
      /* 隐私模式下读不到，按未看过处理 */
      return true
    }
  })

  if (!open) return null

  const dismiss = () => {
    try {
      localStorage.setItem(key, '1')
    } catch {
      /* 存不进去就只关掉本次 */
    }
    setOpen(false)
  }

  return (
    <section className={`tip-card ${compact ? 'is-compact' : ''}`} role="note" data-tip-id={id}>
      <div className="tip-head">
        <h2>{title}</h2>
        {compact && (
          <span className="tip-inline">
            {steps.map((step, i) => (
              <span key={step}>
                <b>{i + 1}</b>
                {step}
              </span>
            ))}
          </span>
        )}
        <button type="button" className="link-btn" onClick={dismiss}>
          知道了
        </button>
      </div>
      {!compact && (
        <ol className="tip-steps">
          {steps.map((step, i) => (
            <li key={step}>
              <b>{i + 1}</b>
              <span>{step}</span>
            </li>
          ))}
        </ol>
      )}
    </section>
  )
}
