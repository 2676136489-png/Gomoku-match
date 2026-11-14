/**
 * 新手教程：章节目录 + 正文 + SVG 演示盘。
 *
 * 内容全部来自 /api/tutorial，前端只管翻页与排版。
 */

const { api, el, $, miniBoard, spinner } = window.Gomoku

let data = null
let active = ''

async function load() {
  const root = document.getElementById('main')
  root.append(spinner('加载中…'))
  try {
    data = await api.tutorial()
    active = data.chapters[0] ? data.chapters[0].id : ''
    root.innerHTML = ''
    render()
  } catch (e) {
    root.innerHTML = ''
    root.append(el('div', { class: 'banner banner-error', role: 'alert' }, e.message))
  }
}

function render() {
  const root = document.getElementById('main')
  root.innerHTML = ''
  if (!data || !data.chapters.length) return

  const chapter = data.chapters.find((c) => c.id === active) || data.chapters[0]
  const index = data.chapters.findIndex((c) => c.id === chapter.id)

  const nav = el(
    'aside',
    { class: 'tut-nav' },
    el(
      'div',
      { class: 'tut-nav-inner' },
      data.chapters.map((c, i) =>
        el(
          'button',
          {
            class: `tut-nav-item ${c.id === chapter.id ? 'is-active' : ''}`,
            type: 'button',
            onclick: () => {
              active = c.id
              render()
            },
          },
          el('span', { class: 'tut-nav-index' }, String(i + 1).padStart(2, '0')),
          el('span', {}, el('b', {}, c.title)),
        ),
      ),
    ),
  )

  const body = el(
    'article',
    { class: 'tut-body' },
    el(
      'header',
      { class: 'tut-head' },
      el('span', { class: 'tut-kicker' }, `第 ${index + 1} 章 / 共 ${data.chapters.length} 章`),
      el('h2', {}, chapter.title),
    ),
    chapter.sections.map((section, i) =>
      el(
        'section',
        { class: 'tut-section', dataset: { section: String(i) } },
        el('h3', {}, el('span', { class: 'tut-num' }, String(i + 1)), section.title),
        el('div', { class: 'tut-text' }, (section.body || []).map((para) => el('p', {}, para))),
        section.demo
          ? el(
              'figure',
              { class: 'tut-demo' },
              miniBoard({ stones: section.demo.stones || [], marks: section.demo.marks || [], size: 300 }),
            )
          : null,
      ),
    ),
    el(
      'footer',
      { class: 'tut-foot' },
      el('button', {
        class: 'btn',
        type: 'button',
        disabled: index === 0,
        onclick: () => {
          active = data.chapters[index - 1].id
          render()
          window.scrollTo({ top: 0 })
        },
      }, '上一章'),
      el('button', {
        class: 'btn btn-primary',
        type: 'button',
        disabled: index === data.chapters.length - 1,
        onclick: () => {
          active = data.chapters[index + 1].id
          render()
          window.scrollTo({ top: 0 })
        },
      }, '下一章'),
    ),
  )

  root.append(el('div', { class: 'tutorial' }, nav, body))
}

;(async () => {
  window.Gomoku.landscapeHint()
  await window.Gomoku.bootPage('tutorial')
  load()
})()
