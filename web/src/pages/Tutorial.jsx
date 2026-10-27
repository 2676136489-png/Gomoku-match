import { useEffect, useState } from 'react'
import MiniBoard from '../components/MiniBoard'
import { api } from '../lib/api'

export default function Tutorial() {
  const [data, setData] = useState(null)
  const [active, setActive] = useState('')
  const [error, setError] = useState('')

  useEffect(() => {
    api
      .tutorial()
      .then((res) => {
        setData(res)
        setActive(res.chapters[0]?.id ?? '')
      })
      .catch((e) => setError(e.message))
  }, [])

  if (error) return <div className="banner banner-error">{error}</div>
  if (!data) {
    return (
      <div className="boot">
        <span className="spinner" />
        <p>加载教程…</p>
      </div>
    )
  }

  const chapter = data.chapters.find((c) => c.id === active) ?? data.chapters[0]
  const index = data.chapters.findIndex((c) => c.id === chapter.id)

  return (
    <div className="tutorial">
      <aside className="tut-nav">
        {data.chapters.map((c, i) => (
          <button
            key={c.id}
            type="button"
            className={`tut-nav-item ${c.id === chapter.id ? 'is-active' : ''}`}
            onClick={() => setActive(c.id)}
          >
            <span className="tut-nav-index">{String(i + 1).padStart(2, '0')}</span>
            <span>
              <b>{c.title}</b>
              <em>{c.summary}</em>
            </span>
          </button>
        ))}
      </aside>

      <article className="tut-body">
        <header className="tut-head">
          <span className="tut-kicker">
            第 {index + 1} 章 / 共 {data.chapters.length} 章
          </span>
          <h2>{chapter.title}</h2>
          <p>{chapter.summary}</p>
        </header>

        {chapter.sections.map((section, i) => (
          <section key={`${chapter.id}-${i}`} className="tut-section">
            <h3>
              <span className="tut-num">{i + 1}</span>
              {section.title}
            </h3>
            <div className="tut-text">
              {section.body.map((para, j) => (
                <p key={j}>{para}</p>
              ))}
            </div>
            {section.demo && (
              <figure className="tut-demo">
                <MiniBoard stones={section.demo.stones} marks={section.demo.marks} size={300} />
                <figcaption>{section.demo.caption}</figcaption>
              </figure>
            )}
          </section>
        ))}

        <footer className="tut-foot">
          <button
            type="button"
            className="btn"
            disabled={index === 0}
            onClick={() => setActive(data.chapters[index - 1].id)}
          >
            上一章
          </button>
          <button
            type="button"
            className="btn btn-primary"
            disabled={index === data.chapters.length - 1}
            onClick={() => setActive(data.chapters[index + 1].id)}
          >
            下一章
          </button>
        </footer>
      </article>
    </div>
  )
}
