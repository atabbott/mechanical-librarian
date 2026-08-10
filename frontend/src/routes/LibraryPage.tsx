import { Link, useNavigate } from '@tanstack/react-router'

import type { SyntheticEvent } from 'react'

import type { LibraryView } from '../api/http'
import { libraryRoute, ProgressiveCasePanel } from '../router'

const friendly = (value: string) => value.replaceAll('_', ' ')

export default function LibraryPage({ data }: { data: LibraryView }) {
  const search = libraryRoute.useSearch()
  const navigate = useNavigate({ from: '/library' })

  const submit = (event: SyntheticEvent<HTMLFormElement, SubmitEvent>) => {
    event.preventDefault()
    const submitted = new FormData(event.currentTarget).get('q')
    const query = typeof submitted === 'string' ? submitted : ''
    void navigate({
      search: (current) => {
        const next = { ...current }
        delete next.cursor
        if (query) next.q = query
        else delete next.q
        return next
      },
    })
  }

  return (
    <>
      <header className="page-heading">
        <div>
          <p className="eyebrow">Authenticated review</p>
          <h1>Review library</h1>
          <p>Attention-first, exact-current review cases.</p>
        </div>
        <dl className="counts">
          <div><dt>Attention</dt><dd>{data.counts.attention}</dd></div>
          <div><dt>All cases</dt><dd>{data.counts.all}</dd></div>
          <div><dt>No attention</dt><dd>{data.counts.clear}</dd></div>
        </dl>
      </header>
      <form className="search" onSubmit={submit}>
        <label htmlFor="library-query">Search title, library, or item</label>
        <div><input key={search.q} id="library-query" name="q" type="search" maxLength={128} defaultValue={search.q ?? ''} /><button>Search</button></div>
      </form>
      <nav className="lenses" aria-label="Library lenses">
        {(['needs_attention', 'all', 'no_attention_needed'] as const).map((lens) => (
          <Link key={lens} to="/library" search={{ lens, ...(search.q ? { q: search.q } : {}) }} preload="intent" activeProps={{ 'aria-current': 'page' }}>
            {friendly(lens)}
          </Link>
        ))}
      </nav>
      <div className="library-layout">
        <section aria-label="Review cases" className="work-list">
          {data.works.length === 0 ? <p>No exact-current cases match this view.</p> : null}
          {data.works.map((work) => (
            <article key={work.id} className="work-row">
              <div>
                <h2><Link to="/review/$reviewCaseId" params={{ reviewCaseId: work.id }} preload="intent">{work.title}</Link></h2>
                <p>{work.subtitle}</p>
              </div>
              <span>{friendly(work.attention)}</span>
              <button type="button" onClick={() => void navigate({ search: (current) => ({ ...current, preview: work.id }) })}>Preview</button>
            </article>
          ))}
          {data.nextCursor ? (
            <Link to="/library" search={{ ...search, cursor: data.nextCursor }} preload="intent">Next page</Link>
          ) : <p>End of the bounded current result.</p>}
        </section>
        {search.preview ? <ProgressiveCasePanel id={search.preview} /> : (
          <aside><p className="eyebrow">Current review cases</p><h2>Choose a work to preview its exact-current fields.</h2></aside>
        )}
      </div>
    </>
  )
}
