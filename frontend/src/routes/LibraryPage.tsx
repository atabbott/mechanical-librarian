import { Link, useNavigate } from '@tanstack/react-router'
import type { SyntheticEvent } from 'react'

import type { LibraryView, LibraryWorkView } from '../api/http'
import { useRouteReadyFocus } from '../focus'
import { libraryRoute, ProgressiveCasePanel } from '../router'

const friendly = (value: string) => value.replaceAll('_', ' ').replace(/^./, (letter) => letter.toUpperCase())
const authorLine = (work: LibraryWorkView) => work.authors.length ? work.authors.join(', ') : 'Author details unavailable'
const initials = (title: string) => title.split(/\s+/u).filter(Boolean).slice(0, 2).map((word) => word[0]?.toUpperCase()).join('') || 'ML'

export default function LibraryPage({ data }: { data: LibraryView }) {
  const search = libraryRoute.useSearch()
  const navigate = useNavigate({ from: '/library' })
  const selectedWork = search.preview ? data.works.find((work) => work.id === search.preview) : undefined
  useRouteReadyFocus(`${search.lens}:${search.q ?? ''}:${search.cursor ?? ''}:${search.preview ?? ''}`)

  const submit = (event: SyntheticEvent<HTMLFormElement, SubmitEvent>) => {
    event.preventDefault()
    const submitted = new FormData(event.currentTarget).get('q')
    const query = typeof submitted === 'string' ? submitted : ''
    void navigate({
      search: (current) => {
        const next = { ...current }
        if (query !== (current.q ?? '')) {
          delete next.cursor
          delete next.preview
        }
        if (query) next.q = query
        else delete next.q
        return next
      },
    })
  }

  return (
    <div className="library-page">
      <header className="library-header" tabIndex={-1} data-route-focus>
        <div>
          <p className="eyebrow">Your library</p>
          <h1>Every work, with exceptions first</h1>
          <p>Browse every current work. Human exceptions stay prominent without making automatic or resolved work look unfinished.</p>
        </div>
        <dl className="library-metrics" aria-label="Library review status">
          <div><dt>Works</dt><dd>{data.counts.all}</dd></div>
          <div><dt>Need attention</dt><dd>{data.counts.attention}</dd></div>
          <div><dt>No attention needed</dt><dd>{data.counts.clear}</dd></div>
        </dl>
      </header>

      <div className={`library-workspace${search.preview ? ' has-selection' : ''}`}>
        <section className="library-works" aria-labelledby="library-works-title">
          <div className="library-works-heading"><div><p className="eyebrow">Library</p><h2 id="library-works-title">Works</h2></div><span>{data.counts.all} total</span></div>
          <nav className="lenses" aria-label="Library lenses">
            {(['all', 'needs_attention', 'no_attention_needed'] as const).map((lens) => (
              <Link key={lens} to="/library" search={{ lens, ...(search.q ? { q: search.q } : {}) }} preload="intent" activeProps={{ 'aria-current': 'page' }}>
                {friendly(lens === 'no_attention_needed' ? 'No attention needed' : lens)}
              </Link>
            ))}
          </nav>
          <form className="search library-search" onSubmit={submit}>
            <label className="sr-only" htmlFor="library-query">Search title, author, library, or media</label>
            <div><input key={search.q} id="library-query" name="q" type="search" maxLength={128} defaultValue={search.q ?? ''} placeholder="Search title, author, library…"/><button>Search</button></div>
          </form>
          <p className="library-order">Attention first · bounded current results</p>
          <div className="library-work-list">
            {data.works.length === 0 ? <p className="empty-copy">No exact-current works match this search and lens.</p> : null}
            {data.works.map((work) => {
              const selected = search.preview === work.id
              const needsAttention = work.attentionCount > 0
              return <article key={work.id} className={`library-work-row${selected ? ' active' : ''}`}>
                <span className="library-book-mark" aria-hidden="true">{initials(work.title)}</span>
                <div className="library-work-copy">
                  <h3>{work.reviewCaseId ? <Link to="/review/$reviewCaseId" params={{ reviewCaseId: work.reviewCaseId }} search={{ subview: 'review' }} preload="intent">{work.title}</Link> : <span className="library-title-text">{work.title}</span>}</h3>
                  <p>{authorLine(work)}</p>
                  <p>{work.library} · {friendly(work.mediaKind)}</p>
                  <div className="library-work-status">
                    <span className={needsAttention ? 'needs-attention' : 'handled'}>{needsAttention ? `${String(work.attentionCount)} ${work.attentionCount === 1 ? 'field needs' : 'fields need'} attention` : 'No attention needed'}</span>
                  </div>
                </div>
                {work.reviewCaseId ? <button className="preview-button" type="button" aria-pressed={selected} onClick={() => void navigate({ search: (current) => ({ ...current, preview: work.id }) })}>Preview</button> : null}
              </article>
            })}
          </div>
          <div className="library-pager">
            <span>{data.nextCursor ? 'More current works are available' : 'End of the bounded current result'}</span>
            {data.nextCursor ? <Link to="/library" search={{ ...search, cursor: data.nextCursor }} preload="intent">Next page →</Link> : null}
          </div>
        </section>

        {search.preview ? <div className="selected-work-surface"><button className="compact-back" type="button" onClick={() => void navigate({ search: (current) => { const next = { ...current }; delete next.preview; return next } })}>← Library</button><ProgressiveCasePanel id={search.preview} {...(selectedWork ? { identity: selectedWork } : {})} /></div> : (
          <aside className="library-selection"><p className="eyebrow">Selected work</p><h2>Choose a work to preview its exact-current fields.</h2><p>Open any title directly, or preview it here without leaving the complete library.</p></aside>
        )}
      </div>
    </div>
  )
}
