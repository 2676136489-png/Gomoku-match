/**
 * 登录 / 注册页。
 *
 * 已登录的会话直接送回首页；表单支持登录与注册两种模式，
 * 校验错误就地显示在表单里。视觉走 login.css 的落地页风格：
 * 视频背景上一张居中的玻璃卡，与 index.html 接得上。
 */

const { api, setToken, el, $ } = window.Gomoku

function nextTarget() {
  const params = new URLSearchParams(window.location.search)
  const next = params.get('next') || ''
  // 只放行站内相对路径，别的统统送回首页
  if (next && /^[\w./-]+\.html(\?.*)?$/.test(next) && !next.includes('//')) return next
  return 'index.html'
}

let mode = 'login'

function render() {
  const root = document.getElementById('auth')
  root.innerHTML = ''

  const errorSlot = el('div', { class: 'banner banner-error auth-error', role: 'alert', hidden: true })

  const nameInput = el('input', {
    class: 'input',
    value: '',
    placeholder: '2–20 位，中英文 / 数字 / 下划线',
    autocomplete: 'username',
  })
  const passInput = el('input', {
    class: 'input',
    type: 'password',
    placeholder: '至少 6 位',
    autocomplete: mode === 'login' ? 'current-password' : 'new-password',
  })
  const confirmInput = el('input', {
    class: 'input',
    type: 'password',
    placeholder: '再次输入密码',
    autocomplete: 'new-password',
  })

  let busy = false

  async function submit(event) {
    event.preventDefault()
    if (busy) return
    const name = nameInput.value.trim()
    const password = passInput.value
    if (mode === 'register' && password !== confirmInput.value) {
      errorSlot.textContent = '两次输入的密码不一致，请重新输入'
      errorSlot.hidden = false
      return
    }
    errorSlot.hidden = true
    busy = true
    submitBtn.disabled = true
    submitBtn.textContent = busy ? '处理中…' : ''
    try {
      const res = mode === 'login' ? await api.login(name, password) : await api.register(name, password)
      setToken(res.token)
      window.location.href = nextTarget()
    } catch (e) {
      errorSlot.textContent = e.message
      errorSlot.hidden = false
      busy = false
      submitBtn.disabled = false
      submitBtn.textContent = mode === 'login' ? '进入棋院' : '创建账户'
    }
  }

  const submitBtn = el(
    'button',
    { class: 'btn btn-primary auth-submit', type: 'submit' },
    mode === 'login' ? '进入棋院' : '创建账户',
  )

  const form = el(
    'form',
    { class: 'auth-form', onsubmit: submit },
    el('label', { class: 'field' }, el('span', {}, '用户名'), nameInput),
    el('label', { class: 'field' }, el('span', {}, '密码'), passInput),
    mode === 'register' ? el('label', { class: 'field' }, el('span', {}, '确认密码'), confirmInput) : null,
    errorSlot,
    submitBtn,
  )

  const switchMode = (next) => {
    mode = next
    render()
  }

  root.append(
    el(
      'section',
      { class: 'auth-panel' },
      el(
        'a',
        { class: 'auth-brand', href: 'index.html' },
        el('img', { src: 'assets/logo.webp', alt: '', width: '44', height: '44' }),
        el('span', { class: 'auth-wordmark' }, 'Gomoku Match'),
      ),
      el(
        'section',
        { class: 'auth-card' },
        el(
          'div',
          { class: 'auth-tabs' },
          el('button', { class: `auth-tab ${mode === 'login' ? 'is-active' : ''}`, type: 'button', onclick: () => switchMode('login') }, '登录'),
          el('button', { class: `auth-tab ${mode === 'register' ? 'is-active' : ''}`, type: 'button', onclick: () => switchMode('register') }, '注册'),
        ),
        form,
        el(
          'p',
          { class: 'auth-switch' },
          mode === 'login' ? '还没有账户？' : '已经有账户？',
          el('button', { type: 'button', onclick: () => switchMode(mode === 'login' ? 'register' : 'login') },
            mode === 'login' ? '立即注册' : '返回登录'),
        ),
      ),
      el('p', { class: 'auth-tagline' }, '十五路棋盘 · 每一手带解说 · 每一局能复盘'),
    ),
  )

  nameInput.focus()
}

// 已登录就不再看登录页
;(async () => {
  try {
    await api.me()
    window.location.replace(nextTarget())
    return
  } catch {
    render()
  }
})()
