import { Link, useNavigate } from '@tanstack/react-router'
import type { MouseEvent, SyntheticEvent } from 'react'

import type { AttentionItemView, AttentionView } from '../api/http'
import { useRouteReadyFocus } from '../focus'
import { fieldLabel, labelWords } from '../labels'
import { attentionRoute } from '../router'

const reasonLabel = (item: AttentionItemView) =>
  item.classificationReason ?? 'The current evidence requires a human choice'
const outcomePreview = (item: AttentionItemView) =>
  `Inspect the current value and available ${labelWords(item.destination)} choices${item.provenance.length ? ` · ${String(item.provenance.length)} evidence ${item.provenance.length === 1 ? 'record' : 'records'} available` : ''}.`
const authors = (item: AttentionItemView) =>
  item.authors.length ? item.authors.join(', ') : 'Author details unavailable'
const groupItems = (items: readonly AttentionItemView[]) => {
  const groups = new Map<string, AttentionItemView[]>()
  for (const item of items) groups.set(item.id, [...(groups.get(item.id) ?? []), item])
  return [...groups.values()]
}

export default function AttentionPage({
  data,
  retained,
  failed,
  retry,
}: {
  data: AttentionView | undefined
  /** `data` is the previous key's result, shown busy until the URL's own read settles. */
  retained: boolean
  failed: boolean
  retry: () => void
}) {
  const search = attentionRoute.useSearch()
  const navigate = useNavigate({ from: '/attention' })
  useRouteReadyFocus(`${search.q ?? ''}:${search.cursor ?? ''}`)
  const submit = (event: SyntheticEvent<HTMLFormElement>) => {
    event.preventDefault()
    const value = new FormData(event.currentTarget).get('q')
    const q = typeof value === 'string' ? value : ''
    void navigate({ search: q ? { q } : {} })
  }
  const groups = groupItems(data?.items ?? [])
  // Retained links stay focusable, but their cursor and items belong to the key the URL left.
  const holdRetained = (event: MouseEvent) => {
    if (retained) event.preventDefault()
  }
  return (
    <div className="attention-page">
      <header className="attention-header" tabIndex={-1} data-route-focus>
        <div>
          <p className="eyebrow">Human review</p>
          <h1>Needs your attention</h1>
          <p>Only unresolved fields where your choice can improve the result.</p>
        </div>
        <div className="attention-total">
          <strong>{data ? data.total : '…'}</strong>
          <span>
            {data
              ? `${data.total === 1 ? 'field' : 'fields'} across current works`
              : failed
                ? 'Count unavailable'
                : 'Loading count…'}
          </span>
        </div>
      </header>
      <form className="search attention-search" onSubmit={submit}>
        <label htmlFor="attention-query">Search title, author, library, or media</label>
        <div>
          <input
            key={search.q}
            id="attention-query"
            name="q"
            type="search"
            maxLength={128}
            defaultValue={search.q ?? ''}
          />
          <button>Search</button>
        </div>
      </form>
      <section className="attention-groups" aria-label="Fields needing attention">
        {!data ? (
          <div className="attention-empty" aria-busy={!failed}>
            {failed ? (
              <>
                <p role="alert">Attention is unavailable.</p>
                <button type="button" onClick={retry}>
                  Try again
                </button>
              </>
            ) : (
              <p role="status">Loading attention…</p>
            )}
          </div>
        ) : groups.length ? (
          groups.map((group) => {
            const work = group[0]
            if (!work) return null
            return (
              <article className="attention-work" key={work.id}>
                <header>
                  <div>
                    <p className="eyebrow">
                      {work.libraryDisplayName ?? work.library} · {fieldLabel(work.mediaKind)}
                    </p>
                    <h2>{work.title}</h2>
                    <p>{authors(work)}</p>
                  </div>
                  <span>
                    {work.workAttentionCount} {work.workAttentionCount === 1 ? 'field' : 'fields'}
                  </span>
                </header>
                <div className="attention-field-list">
                  {group.map((item) => (
                    <Link
                      className="attention-field-row"
                      key={`${item.id}:${item.destination}`}
                      aria-disabled={retained || undefined}
                      onClick={holdRetained}
                      to="/library"
                      search={{
                        lens: 'all',
                        preview: item.id,
                        field: item.destination,
                        ...(search.cursor ? { attentionCursor: search.cursor } : {}),
                      }}
                    >
                      <span className="attention-field-name">
                        <strong>{fieldLabel(item.destination)}</strong>
                        <small>Open exact field</small>
                      </span>
                      <span>
                        <small>Why it needs you</small>
                        <strong>{reasonLabel(item)}</strong>
                      </span>
                      <span>
                        <small>Decision preview</small>
                        <strong>{outcomePreview(item)}</strong>
                      </span>
                      <span aria-hidden="true">→</span>
                    </Link>
                  ))}
                </div>
              </article>
            )
          })
        ) : (
          <div className="attention-empty">
            <p className="eyebrow">All clear</p>
            <h2>Nothing needs your attention.</h2>
            <p>Automatic-only and resolved work remains available in the complete Library.</p>
            <Link to="/library" search={{ lens: 'all' }}>
              Browse Library
            </Link>
          </div>
        )}
        {data ? (
          <footer className="attention-pager">
            {data.nextCursor ? (
              <Link
                to="/attention"
                search={{ ...search, cursor: data.nextCursor }}
                aria-disabled={retained || undefined}
                onClick={holdRetained}
              >
                Next page →
              </Link>
            ) : (
              <span>End of the bounded current result</span>
            )}
          </footer>
        ) : null}
      </section>
    </div>
  )
}
