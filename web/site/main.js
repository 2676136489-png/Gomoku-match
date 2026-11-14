/**
 * 落地页脚本：登录门禁 + 移动端菜单 + 统计数字滚动。
 *
 * 页面本身没有别的运行时依赖——入场动画全部由 CSS 驱动，JS 只负责
 * 三件 CSS 做不到的事：登录门禁、菜单的开合状态机、计数动画的数值插值。
 */

/* ------------------------------------------------------------ 登录门禁 */

// 首页只对已登录的访客开放：没登录（或 token 失效）一律送登录页，
// 登录成功后带 next=index.html 回来。
;(async () => {
  const kick = () => {
    location.replace('login.html?next=' + encodeURIComponent('index.html'))
  }
  const token = localStorage.getItem('gomoku.token') || ''
  if (!token) {
    kick()
    return
  }
  try {
    const res = await fetch('/api/auth/me', {
      headers: { Authorization: 'Bearer ' + token },
    })
    if (!res.ok) throw new Error(String(res.status))
  } catch {
    // token 失效就清干净再走，cookie 里那份也要一起清
    localStorage.removeItem('gomoku.token')
    document.cookie = 'gomoku_token=; path=/; max-age=0; SameSite=Lax'
    kick()
  }
})()

// 顶栏与移动菜单里的「退出」：清掉 token 再回登录页
for (const id of ['nav-exit', 'menu-exit']) {
  const exit = document.getElementById(id)
  if (!exit) continue
  exit.addEventListener('click', (e) => {
    e.preventDefault()
    localStorage.removeItem('gomoku.token')
    document.cookie = 'gomoku_token=; path=/; max-age=0; SameSite=Lax'
    window.location.href = 'login.html'
  })
}

/* ------------------------------------------------------------ 移动菜单 */

const burger = document.querySelector('.burger')
const overlay = document.getElementById('mobile-overlay')
const menu = document.getElementById('mobile-menu')

function setMenu(open) {
  if (!burger || !overlay || !menu) return
  document.body.classList.toggle('menu-open', open)
  burger.setAttribute('aria-expanded', String(open))
  burger.setAttribute('aria-label', open ? 'Close menu' : 'Open menu')
  overlay.hidden = !open
  menu.hidden = !open
}

if (burger && overlay && menu) {
  burger.addEventListener('click', () => {
    setMenu(!document.body.classList.contains('menu-open'))
  })
  overlay.addEventListener('click', () => setMenu(false))
  menu.addEventListener('click', (e) => {
    if (e.target.closest('a')) setMenu(false)
  })
  window.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') setMenu(false)
  })
  window.addEventListener('resize', () => {
    if (window.innerWidth > 720) setMenu(false)
  })
}

/* ---------------------------------------------------------- 计数动画 */

// easeOutCubic：起步快、收尾缓，计数读起来像「结算」而不是匀速爬
const easeOutCubic = (t) => 1 - Math.pow(1 - t, 3)

function formatValue(value, decimals, suffix) {
  return value.toFixed(decimals) + suffix
}

function runCounter(el, index) {
  const target = parseFloat(el.dataset.target || '0')
  const decimals = parseInt(el.dataset.decimals || '0', 10)
  const suffix = el.dataset.suffix || ''
  const duration = 1500 + index * 80
  const startOffset = 480 + index * 90
  const started = performance.now()

  const tick = (now) => {
    const t = Math.min(1, (now - started) / duration)
    const value = target * easeOutCubic(t)
    el.textContent = formatValue(value, decimals, suffix)
    if (t < 1) requestAnimationFrame(tick)
    else el.textContent = formatValue(target, decimals, suffix)
  }

  setTimeout(() => requestAnimationFrame(tick), startOffset)
}

const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches
const values = Array.from(document.querySelectorAll('.stat-value'))

if (reduceMotion || !('IntersectionObserver' in window)) {
  // 降级：直接停在终值（HTML 里本来就写了终值，这里再兜一次）
  values.forEach((el) => {
    el.textContent = formatValue(
      parseFloat(el.dataset.target || '0'),
      parseInt(el.dataset.decimals || '0', 10),
      el.dataset.suffix || '',
    )
  })
} else {
  // HTML 里预写了终值（无 JS 时不至于全是 0）；JS 接管后先归零再滚
  values.forEach((el) => {
    el.textContent = formatValue(0, parseInt(el.dataset.decimals || '0', 10), el.dataset.suffix || '')
  })

  const observer = new IntersectionObserver(
    (entries) => {
      entries.forEach((entry) => {
        if (!entry.isIntersecting) return
        const el = entry.target
        observer.unobserve(el)
        runCounter(el, values.indexOf(el))
      })
    },
    { threshold: 0.25 },
  )

  values.forEach((el) => observer.observe(el))
}
