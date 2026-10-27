/**
 * 术语表与行内释义。
 *
 * 连珠、禁手、棋形这些词对下过棋的人是常识，对第一次打开的人是黑话。
 * 释义集中放在这里，正文用 <Term k="renju" /> 引用，悬停或键盘聚焦时展开，
 * 不占版面，也不需要读者跳去别的页面查。
 */

const TERMS = {
  renju: {
    label: '连珠',
    def: '五子棋的竞技规则。黑棋受三三、四四、长连三种禁手限制，白棋不受限。',
  },
  ban: {
    label: '禁手',
    def: '连珠规则下黑棋不能落子的点。默认拦下并说明原因；开启赛制模式后落子即判负。',
  },
  shape: {
    label: '棋形',
    def: '一颗子与周围棋子构成的形状。按威胁由低到高：活二、眠三、活三、冲四、活四、连五。',
  },
  winrate: {
    label: '胜率',
    def: '模型对当前局面下人类一方获胜概率的估计，取值 0%–100%。它来自评估函数，不等同于搜索结果。',
  },
  heat: {
    label: '网络视角',
    def: '只让落点价值网络打分、不做任何搜索的结果，用来观察它对棋盘各区域的偏好。',
  },
  elo: {
    label: '等级分',
    def: '按对手强弱结算的分数。赢强手加得多，输弱手扣得多，初始 1200。',
  },
  rated: {
    label: '计分对局',
    def: '结果写入天梯的对局。人机难度由玩家自选，因此只有真人之间的对局计分。',
  },
  depth: {
    label: '搜索深度',
    def: '引擎向前推演的层数。深度越大棋力越强，单步耗时也越长。',
  },
  ply: {
    label: '手数',
    def: '双方各落一子记为一手，从 1 开始计数。',
  },
}

export function Term({ k, children }) {
  const term = TERMS[k]
  if (!term) return children ?? null
  return (
    <span className="term" data-tip={term.def} title={term.def} tabIndex={0}>
      {children ?? term.label}
    </span>
  )
}

export default TERMS
