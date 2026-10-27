import { useLayoutEffect, useRef } from 'react'

/**
 * 带行号的代码输入框。
 *
 * 没有引 CodeMirror / Monaco：这个场景只需要等宽字体、行号、Tab 缩进和滚动同步，
 * 一个 textarea 加一层 div 就够。为这点功能塞几百 KB 的编辑器进来不值当。
 */
export default function CodeEditor({ value, onChange, readOnly = false, rows = 22 }) {
  const areaRef = useRef(null)
  const gutterRef = useRef(null)
  const lines = value.split('\n').length

  useLayoutEffect(() => {
    if (gutterRef.current && areaRef.current) {
      gutterRef.current.scrollTop = areaRef.current.scrollTop
    }
  }, [value])

  const syncScroll = () => {
    if (gutterRef.current && areaRef.current) {
      gutterRef.current.scrollTop = areaRef.current.scrollTop
    }
  }

  const handleKeyDown = (event) => {
    if (event.key !== 'Tab') return
    event.preventDefault()
    const el = event.currentTarget
    const { selectionStart, selectionEnd } = el
    const next = `${value.slice(0, selectionStart)}    ${value.slice(selectionEnd)}`
    onChange(next)
    // 光标要落在刚插入的缩进之后，等 React 回写 value 再设
    requestAnimationFrame(() => {
      el.selectionStart = el.selectionEnd = selectionStart + 4
    })
  }

  return (
    <div className={`editor ${readOnly ? 'is-readonly' : ''}`}>
      <div className="editor-gutter" ref={gutterRef} aria-hidden="true">
        {Array.from({ length: lines }, (_, i) => (
          <span key={i}>{i + 1}</span>
        ))}
      </div>
      <textarea
        ref={areaRef}
        className="editor-area"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={handleKeyDown}
        onScroll={syncScroll}
        spellCheck={false}
        readOnly={readOnly}
        rows={rows}
        wrap="off"
        autoCapitalize="off"
        autoCorrect="off"
      />
    </div>
  )
}
