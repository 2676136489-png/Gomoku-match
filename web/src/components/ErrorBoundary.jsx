import { Component } from 'react'

/**
 * 全局错误边界。
 *
 * React 19 里未捕获的渲染异常会直接卸载整棵树，用户看到的是一片白屏——
 * 上一版就踩过这个坑：`Math.max(...arr) * 100` 括号放错位置，切个页签白屏，
 * 而接口测试全绿。错误边界至少能把「白屏」变成「一句话 + 一个重试按钮」。
 *
 * 只能是 class 组件：`componentDidCatch` / `getDerivedStateFromError`
 * 到现在都没有函数组件版本。
 */
export default class ErrorBoundary extends Component {
  constructor(props) {
    super(props)
    this.state = { error: null, info: '' }
  }

  static getDerivedStateFromError(error) {
    return { error }
  }

  componentDidCatch(error, info) {
    this.setState({ info: info?.componentStack || '' })
    // 生产环境这里应该上报到采集端。当前只留控制台，避免把堆栈塞进用户界面。
    console.error('[gomoku] 渲染异常', error, info)
  }

  reset = () => {
    this.setState({ error: null, info: '' })
  }

  render() {
    if (!this.state.error) return this.props.children

    return (
      <div className="crash">
        <h2>页面渲染出错</h2>
        <p className="crash-msg">{String(this.state.error?.message || this.state.error)}</p>
        <p className="crash-note">
          当前页面状态已失效，重新加载会重新挂载该页面。对局数据保存在服务端，不会丢失。
        </p>
        <div className="actions">
          <button type="button" className="btn btn-primary" onClick={this.reset}>
            重新加载
          </button>
          <button type="button" className="btn" onClick={() => window.location.reload()}>
            刷新页面
          </button>
        </div>
        {this.state.info && (
          <details className="crash-detail">
            <summary>组件调用栈</summary>
            <pre>{this.state.info}</pre>
          </details>
        )}
      </div>
    )
  }
}
