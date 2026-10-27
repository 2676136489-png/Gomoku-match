import { useEffect, useRef, useState } from 'react'

const SIZE = 15
const PAD_RATIO = 0.055

// 棋盘只用木、墨、朱砂三样东西：木是底，墨画线，朱砂做所有的功能标记
// （最后一手、禁手、待落子、胜利连线），网络热力借一点花青。
// 之前那套霓虹青紫全删掉了——发光效果一上，纸和墨就白做了。
const COLORS = {
  woodTop: '#eed8b0',
  woodMid: '#e4ca9c',
  woodLow: '#d9bd8a',
  grain: 'rgba(122, 88, 44, 0.07)',
  grainDeep: 'rgba(112, 78, 36, 0.14)',
  grid: 'rgba(46, 32, 18, 0.42)',
  gridStrong: 'rgba(46, 32, 18, 0.72)',
  star: 'rgba(46, 32, 18, 0.78)',
  seal: '#a63628',
  indigo: '51, 80, 95',
  hint: 'rgba(166, 54, 40, 0.55)',
  ban: 'rgba(166, 54, 40, 0.88)',
  banSoft: 'rgba(166, 54, 40, 0.22)',
  focus: 'rgba(166, 54, 40, 0.95)',
}

function drawWood(ctx, size) {
  const bg = ctx.createLinearGradient(0, 0, size * 0.4, size)
  bg.addColorStop(0, COLORS.woodTop)
  bg.addColorStop(0.55, COLORS.woodMid)
  bg.addColorStop(1, COLORS.woodLow)
  ctx.fillStyle = bg
  ctx.fillRect(0, 0, size, size)

  // 木纹用正弦摆动的横线，振幅/相位都由下标算出来，
  // 不引随机数——否则每次重绘（悬停、落子）纹理都会跳一下。
  // 分两趟：密而淡的是导管纹，疏而深的是年轮线，只有一层的话
  // 要么看不见、要么像护墙板。
  const lines = 54
  for (let i = 0; i < lines; i += 1) {
    const baseY = ((i + 0.5) / lines) * size
    const ring = i % 11 === 0
    const amp = ring ? 2.6 : 1.1 + (i % 4) * 0.5
    const phase = i * 1.73
    ctx.beginPath()
    for (let x = 0; x <= size; x += size / 36) {
      const y = baseY + Math.sin((x / size) * Math.PI * 2.4 + phase) * amp
      if (x === 0) ctx.moveTo(x, y)
      else ctx.lineTo(x, y)
    }
    ctx.strokeStyle = ring ? COLORS.grainDeep : COLORS.grain
    ctx.lineWidth = ring ? 1.4 : 0.9
    ctx.stroke()
  }

  // 四周压一点暗，让棋子从木面上浮起来，光靠投影不够
  const vignette = ctx.createRadialGradient(size / 2, size / 2, size * 0.26, size / 2, size / 2, size * 0.8)
  vignette.addColorStop(0, 'rgba(96, 66, 30, 0)')
  vignette.addColorStop(1, 'rgba(96, 66, 30, 0.09)')
  ctx.fillStyle = vignette
  ctx.fillRect(0, 0, size, size)
}

function drawStone(ctx, cx, cy, r, color) {
  const gradient = ctx.createRadialGradient(cx - r * 0.34, cy - r * 0.38, r * 0.12, cx, cy, r * 1.02)

  // 黑子是暖黑（蛤碁石的墨色偏褐，不是蓝黑），白子是宣纸白偏米
  if (color === 1) {
    gradient.addColorStop(0, '#4b463c')
    gradient.addColorStop(0.42, '#1c1913')
    gradient.addColorStop(1, '#060504')
  } else {
    gradient.addColorStop(0, '#fffdf7')
    gradient.addColorStop(0.52, '#f7f1e3')
    gradient.addColorStop(1, '#ddd2ba')
  }

  ctx.beginPath()
  ctx.arc(cx, cy, r, 0, Math.PI * 2)
  ctx.fillStyle = gradient
  ctx.shadowColor = color === 1 ? 'rgba(42, 30, 16, 0.5)' : 'rgba(74, 56, 30, 0.34)'
  ctx.shadowBlur = r * 0.55
  ctx.shadowOffsetY = r * 0.18
  ctx.fill()
  ctx.shadowColor = 'transparent'
  ctx.shadowBlur = 0
  ctx.shadowOffsetY = 0

  ctx.beginPath()
  ctx.arc(cx, cy, r * 0.93, 0, Math.PI * 2)
  ctx.strokeStyle = color === 1 ? 'rgba(150, 138, 118, 0.2)' : 'rgba(132, 110, 78, 0.3)'
  ctx.lineWidth = Math.max(0.8, r * 0.055)
  ctx.stroke()
}

export default function Board({
  board,
  lastMove,
  winLine,
  humanColor,
  interactive,
  onPlace,
  hintPoint,
  banned,
  heat,
  predict,
  ghost,
  focus,
}) {
  const wrapRef = useRef(null)
  const canvasRef = useRef(null)
  const [size, setSize] = useState(560)
  const [hover, setHover] = useState(null)

  useEffect(() => {
    const el = wrapRef.current
    if (!el) return
    const observer = new ResizeObserver((entries) => {
      const width = entries[0].contentRect.width
      if (width > 0) setSize(Math.min(width, 700))
    })
    observer.observe(el)
    return () => observer.disconnect()
  }, [])

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const dpr = window.devicePixelRatio || 1
    canvas.width = size * dpr
    canvas.height = size * dpr
    canvas.style.width = `${size}px`
    canvas.style.height = `${size}px`

    const ctx = canvas.getContext('2d')
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
    ctx.clearRect(0, 0, size, size)

    const pad = size * PAD_RATIO
    const step = (size - pad * 2) / (SIZE - 1)
    const at = (i) => pad + i * step

    // 底板：木纹 + 四周压暗
    drawWood(ctx, size)

    // 画布与棋墩的交界留一道极淡的亮边，像木头上打了一层薄蜡
    ctx.strokeStyle = 'rgba(255, 250, 238, 0.3)'
    ctx.lineWidth = 1
    ctx.strokeRect(0.5, 0.5, size - 1, size - 1)

    // 网格：墨线。外框加重一点，其余是淡墨
    for (let i = 0; i < SIZE; i += 1) {
      const p = at(i)
      const strong = i === 0 || i === SIZE - 1
      ctx.beginPath()
      ctx.strokeStyle = strong ? COLORS.gridStrong : COLORS.grid
      ctx.lineWidth = strong ? 1.5 : 1
      ctx.moveTo(at(0), p)
      ctx.lineTo(at(SIZE - 1), p)
      ctx.moveTo(p, at(0))
      ctx.lineTo(p, at(SIZE - 1))
      ctx.stroke()
    }

    // 星位：实心墨点，不是发光的青点
    for (const [x, y] of [
      [3, 3],
      [11, 3],
      [3, 11],
      [11, 11],
      [7, 7],
    ]) {
      ctx.beginPath()
      ctx.arc(at(x), at(y), Math.max(2, step * 0.08), 0, Math.PI * 2)
      ctx.fillStyle = COLORS.star
      ctx.fill()
    }

    // 提示点
    if (hintPoint) {
      ctx.beginPath()
      ctx.arc(at(hintPoint.x), at(hintPoint.y), step * 0.44, 0, Math.PI * 2)
      ctx.strokeStyle = COLORS.hint
      ctx.lineWidth = 2
      ctx.setLineDash([5, 4])
      ctx.stroke()
      ctx.setLineDash([])
    }

    // 禁手点：连珠规则下黑棋不能落子的位置。只在轮到自己时画，
    // 平时画满一屏红叉反而看不清棋。
    if (interactive && banned && banned.length > 0) {
      for (const point of banned) {
        const cx = at(point.x)
        const cy = at(point.y)
        const arm = step * 0.26
        ctx.beginPath()
        ctx.moveTo(cx - arm, cy - arm)
        ctx.lineTo(cx + arm, cy + arm)
        ctx.moveTo(cx + arm, cy - arm)
        ctx.lineTo(cx - arm, cy + arm)
        ctx.strokeStyle = COLORS.ban
        ctx.lineWidth = Math.max(1.6, step * 0.07)
        ctx.lineCap = 'round'
        ctx.stroke()

        ctx.beginPath()
        ctx.arc(cx, cy, step * 0.36, 0, Math.PI * 2)
        ctx.strokeStyle = COLORS.banSoft
        ctx.lineWidth = 1
        ctx.stroke()
      }
    }

    // 网络热力：画在棋子下面。用半径 + 透明度同时表达置信度，
    // 只看颜色深浅的话，浅色木面上差别不够明显。颜色用花青，
    // 和朱砂的预测圈分开，两种标记同时开着也认得出来。
    if (heat && heat.length > 0) {
      for (const p of heat) {
        if (board[p.y * SIZE + p.x] !== 0) continue
        const a = Math.max(0, Math.min(1, p.score))
        if (a < 0.03) continue
        ctx.beginPath()
        ctx.arc(at(p.x), at(p.y), step * 0.46 * (0.42 + a * 0.58), 0, Math.PI * 2)
        ctx.fillStyle = `rgba(${COLORS.indigo}, ${(0.05 + a * 0.34).toFixed(3)})`
        ctx.fill()
      }
    }

    // 落点预测：圆圈的半径与透明度都按份额缩放。
    // 只用颜色不足以区分——同色系的两个斑块差异很小，半径更直观。
    // 份额接近时圆圈本就接近等大，此时用描边深浅区分主次，
    // 否则五六个等大的红圈铺在木面上，容易与手绘记号混淆。
    // 存在待确认的一手时不绘制：该点是唯一的操作焦点，其余预测均为干扰。
    if (!ghost && predict && predict.length > 0) {
      const top = Math.max(...predict.map((p) => p.share)) || 1
      for (const p of predict) {
        if (board[p.y * SIZE + p.x] !== 0) continue
        const weight = Math.max(0, Math.min(1, p.share / top))
        if (weight < 0.05) continue
        const cx = at(p.x)
        const cy = at(p.y)
        const r = step * (0.2 + weight * 0.28)

        ctx.beginPath()
        ctx.arc(cx, cy, r, 0, Math.PI * 2)
        ctx.fillStyle = `rgba(166, 54, 40, ${(0.03 + weight * 0.07).toFixed(3)})`
        ctx.fill()
        ctx.beginPath()
        ctx.arc(cx, cy, r, 0, Math.PI * 2)
        ctx.strokeStyle = `rgba(166, 54, 40, ${(0.3 + weight * 0.6).toFixed(3)})`
        ctx.lineWidth = Math.max(1.1, step * (0.03 + weight * 0.035))
        ctx.stroke()

        // 圈里不再写百分数：木色底上一圈红字既挤又像盖歪了的章，
        // 而且份额本来就由圈的大小表达，具体数字右侧「落点预测」里有。
      }
    }

    // 棋子
    const radius = step * 0.42
    for (let y = 0; y < SIZE; y += 1) {
      for (let x = 0; x < SIZE; x += 1) {
        const v = board[y * SIZE + x]
        if (v) drawStone(ctx, at(x), at(y), radius, v)
      }
    }

    // 待落子：讲解模式下 Agent 已计算完成但尚未落子的位置。
    // 用半透明实体棋子而非空心圈，因为该点确实会落子，只是等待确认。
    if (ghost) {
      ctx.globalAlpha = 0.45
      drawStone(ctx, at(ghost.x), at(ghost.y), radius, ghost.color)
      ctx.globalAlpha = 1
      ctx.beginPath()
      ctx.arc(at(ghost.x), at(ghost.y), radius * 1.12, 0, Math.PI * 2)
      ctx.strokeStyle = COLORS.seal
      ctx.lineWidth = Math.max(1.6, step * 0.05)
      ctx.setLineDash([4, 4])
      ctx.stroke()
      ctx.setLineDash([])
    }

    // 正在解说的那一手
    if (focus) {
      ctx.beginPath()
      ctx.arc(at(focus.x), at(focus.y), radius * 1.24, 0, Math.PI * 2)
      ctx.strokeStyle = COLORS.focus
      ctx.lineWidth = Math.max(1.6, step * 0.055)
      ctx.stroke()
    }

    // 落子预览
    if (interactive && hover && board[hover.y * SIZE + hover.x] === 0) {
      ctx.globalAlpha = 0.35
      drawStone(ctx, at(hover.x), at(hover.y), radius, humanColor)
      ctx.globalAlpha = 1
    }

    // 最后一手：朱砂点。之前是个带光晕的青色亮点，
    // 光晕一去掉反而更清楚——棋谱上本来就是这么标的。
    if (lastMove) {
      const cx = at(lastMove.x)
      const cy = at(lastMove.y)
      ctx.beginPath()
      ctx.arc(cx, cy, radius * 0.3, 0, Math.PI * 2)
      ctx.fillStyle = COLORS.seal
      ctx.fill()
      ctx.beginPath()
      ctx.arc(cx, cy, radius * 0.3, 0, Math.PI * 2)
      ctx.strokeStyle = 'rgba(255, 250, 238, 0.6)'
      ctx.lineWidth = 1
      ctx.stroke()
    }

    // 胜利连线：一道朱砂，不发光
    if (winLine && winLine.length >= 2) {
      const first = winLine[0]
      const last = winLine[winLine.length - 1]
      ctx.beginPath()
      ctx.moveTo(at(first[0]), at(first[1]))
      ctx.lineTo(at(last[0]), at(last[1]))
      ctx.strokeStyle = COLORS.seal
      ctx.lineWidth = Math.max(3, step * 0.1)
      ctx.lineCap = 'round'
      ctx.globalAlpha = 0.88
      ctx.stroke()
      ctx.globalAlpha = 1
    }
  }, [board, size, lastMove, winLine, hover, interactive, humanColor, hintPoint, banned, heat, predict, ghost, focus])

  const toCell = (event) => {
    const rect = canvasRef.current.getBoundingClientRect()
    const pad = size * PAD_RATIO
    const step = (size - pad * 2) / (SIZE - 1)
    const x = Math.round((event.clientX - rect.left - pad) / step)
    const y = Math.round((event.clientY - rect.top - pad) / step)
    if (x < 0 || y < 0 || x >= SIZE || y >= SIZE) return null
    // 点击离交叉点太远就不算
    const dx = event.clientX - rect.left - (pad + x * step)
    const dy = event.clientY - rect.top - (pad + y * step)
    if (Math.hypot(dx, dy) > step * 0.55) return null
    return { x, y }
  }

  return (
    <div className="board-wrap" ref={wrapRef}>
      <canvas
        ref={canvasRef}
        className={interactive ? 'board-canvas is-interactive' : 'board-canvas'}
        onMouseMove={(e) => setHover(interactive ? toCell(e) : null)}
        onMouseLeave={() => setHover(null)}
        onClick={(e) => {
          if (!interactive) return
          const cell = toCell(e)
          if (cell) onPlace(cell.x, cell.y)
        }}
      />
    </div>
  )
}
