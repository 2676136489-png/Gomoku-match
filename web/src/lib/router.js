import { useEffect, useState } from 'react'

/**
 * 极简 hash 路由。
 *
 * 这个应用只有六个页面，引 react-router 会让首屏多几十 KB 和一层 Provider，
 * 换来的是用不上的嵌套路由、loader、data API。hash 路由还有一个实际好处：
 * 静态托管不需要任何 rewrite 规则，刷新页面不会 404。
 */

function parse(hash) {
  const raw = (hash || '').replace(/^#/, '')
  const [path, query] = raw.split('?')
  const segments = path.split('/').filter(Boolean)
  const params = new URLSearchParams(query || '')
  return { path: '/' + segments.join('/'), segments, params }
}

export function useRoute() {
  const [route, setRoute] = useState(() => parse(window.location.hash))

  useEffect(() => {
    const onChange = () => setRoute(parse(window.location.hash))
    window.addEventListener('hashchange', onChange)
    return () => window.removeEventListener('hashchange', onChange)
  }, [])

  return route
}

export function navigate(path) {
  const next = `#${path.startsWith('/') ? path : `/${path}`}`
  if (window.location.hash === next) return
  window.location.hash = next
}

export function replace(path) {
  const next = `#${path.startsWith('/') ? path : `/${path}`}`
  window.location.replace(next)
}
