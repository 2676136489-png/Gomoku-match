/**
 * 页面底部的山水。
 *
 * 三层山、两道云气、一片苔点、几只飞鸟，全是算出来的 SVG，没有一张图片。
 *
 * 造山不用手描贝塞尔——手描的曲线永远圆润，画出来是心电图不是山。也试过
 * 一维中点位移（midpoint displacement），取绝对值做了 ridged noise，出来还是
 * 一排圆包：分形地形天生左右对称、峰谷同圆，而国画的山是「一峰独高、侧峰
 * 拱卫、坡脚拉长」。
 *
 * 最后还是老老实实按山来堆：每层摆一组山峰，每座用 h·(1-t)^k 造型。
 * k 小于 1，两坡在峰顶收成一个尖角，山脚反而陡——这正是山脊的样子，
 * 也顺手把主峰、次峰的高度差交给了峰表去控制。
 *
 * 云气两处：一道压在远山腰上把山脚吃掉，一道贴在山脚，让山「从雾里长出来」。
 * 近山脊线再撒一层苔点，那是国画里点出来的，不是长出来的。
 *
 * 最后一道滤镜：折线太干净，用 feTurbulence 生成噪声，拿 feDisplacementMap
 * 去推挤像素，边缘才有毛笔拖过的毛边。
 */

/** 定种子的线性同余，保证每次刷新山形一样 */
function rng(seed) {
  let s = seed >>> 0
  return () => {
    s = (s * 1664525 + 1013904223) >>> 0
    return s / 4294967296
  }
}

/**
 * 一座山。
 * cx 峰位，h 峰高，wl / wr 左右坡宽（不对称才有「侧峰」的势），
 * k 收尖的狠劲：小于 1 尖，等于 1 是等腰三角，大于 1 就成了馒头。
 */
function peak(x, cx, h, wl, wr, k) {
  const t = x < cx ? (cx - x) / wl : (x - cx) / wr
  if (t >= 1) return 0
  return h * Math.pow(1 - t, k)
}

/**
 * 一层山：把峰表叠起来，再叠一道低频起伏，免得平地起峰太生硬。
 * 返回路径和脊线采样点——苔点要用脊线。
 */
function makeRange({ base, peaks, seed, foot = 160 }) {
  const rand = rng(seed)
  const ph1 = rand() * 6.283
  const ph2 = rand() * 6.283
  const crest = []
  for (let x = -60; x <= 1500; x += 3) {
    let h = 0
    for (const [cx, ph, wl, wr, k] of peaks) h += peak(x, cx, ph, wl, wr, k ?? 0.72)
    const und = Math.sin(x * 0.0105 + ph1) * 4.2 + Math.sin(x * 0.028 + ph2) * 2.1
    // 平地那一段不动，起伏只长在山身上
    const lift = Math.min(1, h / 22)
    crest.push({ x, y: base - h + und * (0.25 + 0.75 * lift), h })
  }

  const d = [
    `M${crest[0].x},${base + foot}`,
    ...crest.map((p) => `L${p.x.toFixed(1)},${p.y.toFixed(1)}`),
    `L${crest[crest.length - 1].x},${base + foot}Z`,
  ].join(' ')

  return { d, crest }
}

// 远山：最高、最淡。中间一柱主峰，两侧矮峰拱着，最左一路沉进雾里
const FAR = makeRange({
  base: 318,
  seed: 20261024,
  peaks: [
    [24, 84, 210, 300, 0.9],
    [250, 166, 150, 240, 0.56],
    [430, 70, 260, 170, 0.94],
    [600, 206, 220, 150, 0.48],
    [790, 78, 170, 250, 0.88],
    [960, 154, 240, 180, 0.55],
    [1160, 62, 200, 240, 0.94],
    [1330, 182, 230, 210, 0.52],
    [1460, 74, 160, 200, 0.88],
  ],
})

// 中景：矮一头，肩宽，给远山当基座
const MID = makeRange({
  base: 402,
  seed: 77123,
  peaks: [
    [110, 104, 280, 340, 0.84],
    [380, 66, 220, 300, 0.95],
    [600, 156, 190, 260, 0.62],
    [830, 80, 260, 180, 0.9],
    [1030, 132, 210, 250, 0.66],
    [1250, 62, 240, 280, 0.95],
    [1400, 112, 200, 220, 0.76],
  ],
})

// 近山：只剩画面下沿一道坡，宽而钝，压住整幅画
const NEAR = makeRange({
  base: 448,
  seed: 991827,
  peaks: [
    [60, 58, 420, 520, 1.05],
    [430, 36, 380, 460, 1.15],
    [820, 72, 460, 400, 0.98],
    [1200, 48, 420, 380, 1.08],
  ],
})

/** 沿近山脊线点苔：位置抖动、大小不一，笔尖蘸墨点的那个劲儿 */
function mossDots() {
  const rand = rng(4711)
  const dots = []
  for (const p of NEAR.crest) {
    if (p.h < 26 || p.x < -20 || p.x > 1460) continue
    if (rand() > 0.16) continue
    const r = 1.5 + rand() * 2.6
    dots.push({ x: p.x + (rand() - 0.5) * 6, y: p.y + (rand() - 0.5) * 5, r, o: 0.3 + rand() * 0.34 })
  }
  return dots
}

const MOSS = mossDots()

/**
 * 皴笔：顺着山脊往下拉的一道道短线，用纸色挖出来。
 * 国画里山石靠皴法立骨，一条平涂的色块永远是色块，加了皴才有石头的意思。
 */
function cunStrokes(range, seed, density, minH) {
  const rand = rng(seed)
  const out = []
  for (const p of range.crest) {
    if (p.h < minH || p.x < -20 || p.x > 1460) continue
    if (rand() > density) continue
    out.push({
      x: p.x,
      y: p.y + 3,
      dx: (rand() - 0.5) * 26,
      len: 7 + rand() * 16,
      w: 0.7 + rand() * 1.0,
      o: 0.05 + rand() * 0.09,
    })
  }
  return out
}

const CUN_NEAR = cunStrokes(NEAR, 8123, 0.07, 18)
const CUN_MID = cunStrokes(MID, 2711, 0.035, 46)

// 「人」字雁阵：一笔画两翼，起笔重、收笔飘
const BIRDS = [
  { d: 'M0,0 q9,-8 18,0 q9,-8 18,0', x: 742, y: 196, s: 1 },
  { d: 'M0,0 q8,-7 16,0 q8,-7 16,0', x: 690, y: 168, s: 0.82 },
  { d: 'M0,0 q6,-6 12,0 q6,-6 12,0', x: 800, y: 154, s: 0.66 },
]

export default function Landscape() {
  return (
    <div className="landscape" aria-hidden="true">
      <svg viewBox="0 0 1440 460" preserveAspectRatio="xMidYMax slice">
        <defs>
          {/* 远山：位移最大 + 一层雾，轮廓化得最狠 */}
          <filter id="washFar" x="-12%" y="-30%" width="124%" height="170%">
            <feTurbulence type="fractalNoise" baseFrequency="0.005 0.012" numOctaves="4" seed="11" result="n" />
            <feDisplacementMap in="SourceGraphic" in2="n" scale="17" xChannelSelector="R" yChannelSelector="G" />
            <feGaussianBlur stdDeviation="1.2" />
          </filter>

          {/* 中景：位移适中，留一点湿边 */}
          <filter id="washMid" x="-12%" y="-30%" width="124%" height="170%">
            <feTurbulence type="fractalNoise" baseFrequency="0.008 0.02" numOctaves="4" seed="23" result="n" />
            <feDisplacementMap in="SourceGraphic" in2="n" scale="12" xChannelSelector="R" yChannelSelector="G" />
            <feGaussianBlur stdDeviation="0.6" />
          </filter>

          {/* 近山：位移最小，轮廓最实 */}
          <filter id="washNear" x="-12%" y="-30%" width="124%" height="170%">
            <feTurbulence type="fractalNoise" baseFrequency="0.012 0.028" numOctaves="3" seed="37" result="n" />
            <feDisplacementMap in="SourceGraphic" in2="n" scale="7" xChannelSelector="R" yChannelSelector="G" />
          </filter>

          <linearGradient id="mist" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0" className="mist-a" />
            <stop offset="0.42" className="mist-b" />
            <stop offset="0.62" className="mist-b" />
            <stop offset="1" className="mist-c" />
          </linearGradient>

          {/* 贴地的一层积雾，把近山的山脚化掉，别留一条硬底边 */}
          <linearGradient id="fog" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0" className="fog-a" />
            <stop offset="1" className="fog-b" />
          </linearGradient>
        </defs>

        <path className="ridge-far" d={FAR.d} filter="url(#washFar)" />
        {/* 云带横着切一刀，远山的山脚就断在雾里 */}
        <rect x="0" y="248" width="1440" height="64" fill="url(#mist)" />

        <path className="ridge-mid" d={MID.d} filter="url(#washMid)" />
        {CUN_MID.map((s, i) => (
          <path
            key={i}
            className="cun"
            d={`M${s.x.toFixed(1)},${s.y.toFixed(1)} q${(s.dx / 2).toFixed(1)},${(s.len * 0.6).toFixed(1)} ${s.dx.toFixed(1)},${s.len.toFixed(1)}`}
            strokeWidth={s.w.toFixed(1)}
            opacity={s.o.toFixed(2)}
          />
        ))}
        <rect x="0" y="348" width="1440" height="50" fill="url(#mist)" />

        <path className="ridge-near" d={NEAR.d} filter="url(#washNear)" />
        {CUN_NEAR.map((s, i) => (
          <path
            key={i}
            className="cun"
            d={`M${s.x.toFixed(1)},${s.y.toFixed(1)} q${(s.dx / 2).toFixed(1)},${(s.len * 0.6).toFixed(1)} ${s.dx.toFixed(1)},${s.len.toFixed(1)}`}
            strokeWidth={s.w.toFixed(1)}
            opacity={s.o.toFixed(2)}
          />
        ))}

        <rect x="0" y="418" width="1440" height="42" fill="url(#fog)" />

        {MOSS.map((m, i) => (
          <ellipse
            key={i}
            className="moss"
            cx={m.x.toFixed(1)}
            cy={m.y.toFixed(1)}
            rx={(m.r * 1.15).toFixed(1)}
            ry={m.r.toFixed(1)}
            opacity={m.o.toFixed(2)}
          />
        ))}

        {BIRDS.map((b) => (
          <g key={b.d} transform={`translate(${b.x} ${b.y}) scale(${b.s})`}>
            <path className="bird" d={b.d} />
          </g>
        ))}
      </svg>
    </div>
  )
}
