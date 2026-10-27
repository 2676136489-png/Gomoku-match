import { useState } from 'react'

const TABS = [
  { key: 'rate', label: '胜率' },
  { key: 'predict', label: '预测' },
  { key: 'data', label: '数据' },
]

const SHAPES = ['活二', '眠三', '活三', '冲四', '活四', '连五']

const CONFIDENCE_LABEL = {
  certain: '确定',
  high: '高',
  medium: '中',
  low: '低',
}

const SOURCE_LABEL = {
  tactical: '战术判定',
  model: '标定模型',
}

function share(mine, theirs) {
  const total = mine + theirs
  if (total <= 0) return 0.5
  return mine / total
}

function twoWay(mine, theirs) {
  return { left: `${(share(mine, theirs) * 100).toFixed(1)}%` }
}

export default function AnalysisPanel({ insight, humanColor, onPickPoint, onPickPly }) {
  const [tab, setTab] = useState('rate')
  const win = insight?.winrate
  const pred = insight?.prediction
  const census = insight?.census
  const model = insight?.model

  const p = win ? win.human : 0.5
  const pct = (p * 100).toFixed(1)
  const ahead = p >= 0.5

  return (
    <section className="panel panel-analysis">
      <div className="panel-head">
        <h2>局面分析</h2>
        {insight && (
          <span className="panel-note">
            {insight.elapsed_ms}ms · 第 {insight.series?.length ?? 0} 手
          </span>
        )}
      </div>

      <div className="an-tabs">
        {TABS.map((t) => (
          <button
            key={t.key}
            type="button"
            className={`an-tab ${tab === t.key ? 'is-active' : ''}`}
            onClick={() => setTab(t.key)}
          >
            {t.label}
          </button>
        ))}
      </div>

      {!insight ? (
        <p className="feed-empty">等待局面数据…</p>
      ) : tab === 'rate' ? (
        <div className="an-body">
          <div className={`rate-hero ${ahead ? 'is-ahead' : 'is-behind'}`}>
            <b>{pct}<i>%</i></b>
            <span>
              你的胜率
              <em>
                {SOURCE_LABEL[win.source] ?? win.source} · 把握{CONFIDENCE_LABEL[win.confidence] ?? win.confidence}
              </em>
            </span>
          </div>

          <div className="rate-bar" role="img" aria-label={`你 ${pct}%，对手 ${(100 - p * 100).toFixed(1)}%`}>
            <span className="rate-mine" style={{ width: `${pct}%` }} />
            <span className="rate-theirs" />
          </div>
          <p className="rate-reason">{win.reason}</p>

          <ul className="facts">
            {win.facts.map((f) => (
              <li key={f.label}>
                <div className="fact-head">
                  <b>{f.mine.toLocaleString()}</b>
                  <span>{f.label}</span>
                  <b>{f.theirs.toLocaleString()}</b>
                </div>
                <div className="fact-track">
                  <span className="fact-mine" style={twoWay(f.mine, f.theirs)} />
                </div>
              </li>
            ))}
          </ul>

          <p className="an-foot">
            左右两侧是黑棋和白棋的量，中间那条线是两者相等的位置。
            {win.confidence_note ? ` ${win.confidence_note}。` : ''}
          </p>
        </div>
      ) : tab === 'predict' ? (
        <div className="an-body">
          <p className="pred-head">
            {insight.status === 'playing' ? (
              <>
                {pred?.side === 1 ? '黑棋' : '白棋'}下一步最可能落在这几个点。份额表示候选池内的相对倾向，
                不是统计概率，准确度见下方命中率。
              </>
            ) : (
              '对局已结束，没有待预测的一手。'
            )}
          </p>

          {pred?.points?.length ? (
            <ol className="pred-list">
              {pred.points.map((pt, i) => (
                <li key={`${pt.x}-${pt.y}`}>
                  <button
                    type="button"
                    className={`pred-row ${i === 0 ? 'is-top' : ''}`}
                    onClick={() => onPickPoint?.(pt)}
                    title={pt.why}
                  >
                    <span className="pred-rank">{i + 1}</span>
                    <span className="pred-coord">
                      {pt.x},{pt.y}
                    </span>
                    <span className="pred-bar">
                      <i style={{ width: `${Math.max(2, pt.share * 100)}%` }} />
                    </span>
                    <span className="pred-share">{(pt.share * 100).toFixed(1)}</span>
                  </button>
                  <p className="pred-why">{pt.why}</p>
                </li>
              ))}
            </ol>
          ) : (
            <p className="feed-empty">无可落子点。</p>
          )}

          {model?.predict?.top3 != null && (
            <p className="an-foot">
              在 {model.predict.eval_positions ?? 40} 个中盘局面上，以搜索实际选点为标准：
              首选命中 {(model.predict.top1 * 100).toFixed(0)}%，前三命中{' '}
              {(model.predict.top3 * 100).toFixed(0)}%。
              {model.predict.prior_weight > 0
                ? ` 叠加网络先验（权重 ${model.predict.prior_weight}）之后取表现最好的那一组参数。`
                : ' 叠加网络先验未在该指标上产生差异，权重取 0：排序仅使用棋形分，网络自身的判断在「网络视角」中单独查看。'}
            </p>
          )}
        </div>
      ) : (
        <div className="an-body">
          <div className="data-block">
            <div className="data-title">
              <span>棋形分布</span>
              <em>每个空点若被该色占据，能形成的最强棋形</em>
            </div>
            <div className="shape-rows">
              {SHAPES.map((name) => {
                const b = census.shapes.black[name] ?? 0
                const w = census.shapes.white[name] ?? 0
                return (
                  <div key={name} className="shape-row">
                    <span className="shape-name">{name}</span>
                    <div className="shape-bars">
                      <i className="sb-black" style={{ width: `${Math.min(100, b * 18)}%` }} />
                      <i className="sb-white" style={{ width: `${Math.min(100, w * 18)}%` }} />
                    </div>
                    <span className="shape-num">
                      {b} / {w}
                    </span>
                  </div>
                )
              })}
            </div>
            <div className="legend">
              <span><i className="lg ln-black" />黑</span>
              <span><i className="lg ln-white" />白</span>
            </div>
          </div>

          <div className="data-block">
            <div className="data-title">
              <span>可数项</span>
              <em>都能在棋盘上数出来</em>
            </div>
            <dl className="data-grid">
              <div>
                <dt>棋子</dt>
                <dd>
                  {census.stones.black} / {census.stones.white}
                </dd>
              </div>
              <div>
                <dt>棋形总量</dt>
                <dd>
                  {census.material.black.toLocaleString()} / {census.material.white.toLocaleString()}
                </dd>
              </div>
              <div>
                <dt>冲四点</dt>
                <dd>
                  {census.threats.black.fours + census.threats.black.open_fours} /{' '}
                  {census.threats.white.fours + census.threats.white.open_fours}
                </dd>
              </div>
              <div>
                <dt>活三点</dt>
                <dd>
                  {census.threats.black.threes} / {census.threats.white.threes}
                </dd>
              </div>
              <div>
                <dt>重心</dt>
                <dd>
                  {census.centroid.black ? census.centroid.black.map((v) => v.toFixed(1)).join(',') : '—'} /{' '}
                  {census.centroid.white ? census.centroid.white.map((v) => v.toFixed(1)).join(',') : '—'}
                </dd>
              </div>
              <div>
                <dt>盘面</dt>
                <dd>{(census.occupancy * 100).toFixed(1)}%</dd>
              </div>
            </dl>
          </div>

          {model?.buckets?.length ? (
            <div className="data-block">
              <div className="data-title">
                <span>模型可靠性</span>
                <em>
                  {model.games ? `${model.games} 局自对弈标定` : '手写兜底权重'} · AUC {model.auc ?? '—'}
                </em>
              </div>
              <div className="rel-table">
                <div className="rel-head">
                  <span>预测区间</span>
                  <span>样本</span>
                  <span>实际</span>
                </div>
                {model.buckets.map((b) => (
                  <div key={b.range} className="rel-row">
                    <span>{b.range}</span>
                    <span>{b.n}</span>
                    <span>
                      <i style={{ width: `${Math.min(100, b.actual * 100)}%` }} />
                      {(b.actual * 100).toFixed(1)}%
                    </span>
                  </div>
                ))}
              </div>
              <p className="an-foot">
                每档的实际胜率贴着预测走，才说明这个数字可信。偏差最大的一档相差{' '}
                {Math.max(...model.buckets.map((b) => Math.abs(b.actual - b.predicted) * 100)).toFixed(1)}{' '}
                个百分点。
              </p>
            </div>
          ) : null}
        </div>
      )}
    </section>
  )
}
