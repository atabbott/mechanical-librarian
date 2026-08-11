import { useInfiniteQuery, useQuery } from '@tanstack/react-query'
import { Link, useNavigate } from '@tanstack/react-router'
import { useEffect, useRef } from 'react'
import type { SyntheticEvent } from 'react'

import { displayValue } from '../api/http'
import type { CaseDetailView, DecisionCommand, LibraryView, LibraryWorkView } from '../api/http'
import { useRouteReadyFocus } from '../focus'
import { detailQuery, libraryRoute, useReviewContext } from '../router'
import { actionable, useReviewDecisionController } from '../reviewDecisionController'

const friendly = (value: string) => value.replaceAll('_', ' ').replace(/^./, (letter) => letter.toUpperCase())
const authorLine = (work: LibraryWorkView) => work.authors.length ? work.authors.join(', ') : 'Author details unavailable'
const initials = (title: string) => title.split(/\s+/u).filter(Boolean).slice(0, 2).map((word) => word[0]?.toUpperCase()).join('') || 'ML'
const requestKey = () => typeof crypto.randomUUID === 'function' ? crypto.randomUUID() : `decision-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`
const evidenceSummary = (value: string) => /^(?:local|evidence|provider)_[0-9a-f]+$/iu.test(value) ? 'Corroborating provider record' : /<[^>]+>/u.test(value) ? 'Conflicting retained observation' : value.length > 96 ? `${value.slice(0, 93)}…` : value

export default function LibraryPage({ data }: { data: LibraryView }) {
  const search = libraryRoute.useSearch()
  const navigate = useNavigate({ from: '/library' })
  const { http } = useReviewContext()
  const currentSearch = useRef(search)
  const queuedSuccessor = useRef<{ id: string; destination: string } | undefined>(undefined)
  const selectedReviewCaseId = search.preview ?? ''
  const detail = useQuery({ ...detailQuery(http, search.preview ?? ''), enabled: Boolean(search.preview) })
  const attention = useInfiniteQuery({ queryKey: ['attention', 'workspace'], initialPageParam: undefined as string | undefined, queryFn: ({ pageParam, signal }) => http.getAttention(pageParam ? { cursor: pageParam } : {}, signal), getNextPageParam: (page) => page.nextCursor ?? undefined, staleTime: 15_000, retry: false })
  const attentionItems = attention.data?.pages.flatMap((page) => page.items) ?? []
  const selectedIdentity = search.preview ? data.works.find((work) => work.reviewCaseId === search.preview) ?? attentionItems.find((item) => item.id === search.preview) : undefined
  const selected = detail.data?.fields.find((field) => field.destination === search.field) ?? detail.data?.fields.find(actionable) ?? detail.data?.fields[0]
  const fullReviewSearch = { subview: 'review' as const, ...(selected ? { field: selected.destination } : {}) }
  const controller = useReviewDecisionController({ reviewCaseId: search.preview ?? '', fields: detail.data?.fields ?? [], active: (command) => currentSearch.current.preview === search.preview && search.preview !== undefined && (currentSearch.current.field === undefined || currentSearch.current.field === command.destination), onAdvance: async (field) => { await navigate({ search: (current) => current.preview === search.preview ? { ...current, field } : current }) }, onComplete: async () => {
    const current = currentSearch.current
    const next = queuedSuccessor.current
    if (next && current.preview === search.preview && currentSearch.current.preview === search.preview) await navigate({ search: (state) => state.preview === search.preview ? { ...state, preview: next.id, field: next.destination } : state })
  }, onStale: async () => { await navigate({ search: { lens: 'needs_attention' } }) } })
  useRouteReadyFocus(`${search.lens}:${search.q ?? ''}:${search.cursor ?? ''}:${search.preview ?? ''}:${search.field ?? ''}:${search.attention ?? ''}`)
  useEffect(() => { currentSearch.current = search }, [search])
  useEffect(() => {
    if (typeof window.matchMedia !== 'function') return
    const media = window.matchMedia('(min-width: 1024px)')
    const selectWideDefault = () => {
      if (!media.matches || currentSearch.current.preview) return
      const first = data.works.find((work) => work.reviewCaseId && work.attentionCount > 0) ?? data.works.find((work) => work.reviewCaseId)
      const reviewCaseId = first?.reviewCaseId
      if (reviewCaseId) void navigate({ replace: true, search: (current) => current.preview ? current : { ...current, preview: reviewCaseId } })
    }
    selectWideDefault()
    media.addEventListener('change', selectWideDefault)
    return () => media.removeEventListener('change', selectWideDefault)
  }, [data.works, navigate])

  const select = (reviewCaseId: string, field?: string) => void navigate({ search: (current) => { const next = { ...current, preview: reviewCaseId }; if (field) next.field = field; else delete next.field; return next } })
  const submit = (event: SyntheticEvent<HTMLFormElement, SubmitEvent>) => {
    event.preventDefault()
    const value = new FormData(event.currentTarget).get('q')
    const q = typeof value === 'string' ? value : ''
    void navigate({ search: (current) => { const next = { ...current }; delete next.cursor; delete next.preview; delete next.field; if (q) next.q = q; else delete next.q; return next } })
  }
  const choose = (field: CaseDetailView['fields'][number], outcome: DecisionCommand['outcome'], selectedValue: DecisionCommand['selectedValue'] = null) => {
    const queue = attentionItems
    const currentIndex = queue.findIndex((item) => item.id === search.preview && item.destination === field.destination)
    const afterCurrent = currentIndex < 0 ? [] : queue.slice(currentIndex + 1)
    const successor = afterCurrent.find((item) => item.id !== search.preview)
    queuedSuccessor.current = successor ? { id: successor.id, destination: successor.destination } : undefined
    controller.submit({ destination: field.destination, outcome, proposalId: field.proposalId, selectedValue, targetFingerprint: field.targetFingerprint, idempotencyKey: requestKey() })
  }
  const clearSelection = () => void navigate({ search: (current) => { const next = { ...current }; delete next.preview; delete next.field; delete next.attention; return next } })
  const selectFromQueue = (reviewCaseId: string, field: string) => void navigate({ search: (current) => { const next = { ...current, preview: reviewCaseId, field }; delete next.attention; return next } })

  return <div className="library-page variant-m-workspace">
    <header className="library-header" tabIndex={-1} data-route-focus>
      <div><h1>Every work, with exceptions first</h1><p>Library keeps the current collection and human attention in one workspace.</p></div>
      <button className="attention-toggle" type="button" aria-expanded={search.attention === 'open'} onClick={() => void navigate({ search: (current) => { const next = { ...current }; if (next.attention === 'open') delete next.attention; else next.attention = 'open'; return next } })}>Attention <strong>{data.counts.attention}</strong></button>
    </header>
    <div className={`library-workspace${search.preview ? ' has-selection' : ''}${search.attention === 'open' ? ' attention-open' : ''}`}>
      <section className="library-works" aria-labelledby="library-works-title">
        <div className="library-works-heading"><div><h2 id="library-works-title">Works</h2></div><span>{data.counts.all} total</span></div>
        <nav className="lenses" aria-label="Library lenses">{(['all', 'needs_attention', 'no_attention_needed'] as const).map((lens) => <Link key={lens} to="/library" search={{ lens, ...(search.q ? { q: search.q } : {}) }} preload="intent" activeProps={{ 'aria-current': 'page' }}>{friendly(lens === 'no_attention_needed' ? 'No attention needed' : lens)}</Link>)}</nav>
        <form className="search library-search" onSubmit={submit}><label className="sr-only" htmlFor="library-query">Search title, author, library, or media</label><div><input key={search.q} id="library-query" name="q" type="search" maxLength={128} defaultValue={search.q ?? ''} placeholder="Search title, author, library…"/><button>Search</button></div></form>
        <p className="library-order">Attention first · bounded current results</p>
        <div className="library-work-list">{data.works.length === 0 ? <p className="empty-copy">No exact-current works match this search and lens.</p> : data.works.map((work) => {
          const selectedWorkRow = search.preview === work.reviewCaseId; const needsAttention = work.attentionCount > 0; const reviewCaseId = work.reviewCaseId
          return <article key={work.id} className={`library-work-row${selectedWorkRow ? ' active' : ''}`}><span className="library-book-mark" aria-hidden="true">{initials(work.title)}</span><div className="library-work-copy"><h3>{reviewCaseId ? <button className="work-select" type="button" aria-pressed={selectedWorkRow} onClick={() => select(reviewCaseId)}>{work.title}</button> : work.title}</h3><p>{authorLine(work)}</p><p>{work.library} · {friendly(work.mediaKind)}</p><div className="library-work-status"><span className={needsAttention ? 'needs-attention' : 'handled'}>{needsAttention ? `${String(work.attentionCount)} field${work.attentionCount === 1 ? '' : 's'} need attention` : 'No attention needed'}</span></div></div></article>
        })}</div>
        <div className="library-pager"><span>{data.nextCursor ? 'More current works are available' : 'End of the bounded current result'}</span>{data.nextCursor ? <Link to="/library" search={{ ...search, cursor: data.nextCursor }}>Next page →</Link> : null}</div>
      </section>
      <section className="workspace-detail" aria-live="polite">
        {!search.preview ? <div className="library-selection"><h2>Choose a work</h2><p>Its current values, field position, and bounded reviewer context stay here while you move through attention.</p></div> : !selectedIdentity ? <div className="library-selection"><p>Progressive detail</p><h2>Selected work</h2><p>Readable work identity is not available on this Library page.</p><Link to="/review/$reviewCaseId" params={{ reviewCaseId: search.preview }} search={fullReviewSearch}>Open full review</Link></div> : detail.isPending ? <p>Loading preview…</p> : detail.isError ? <p role="alert">Selected work could not be loaded. <button onClick={() => void detail.refetch()}>Try again</button> <Link className="full-review-link" to="/review/$reviewCaseId" params={{ reviewCaseId: search.preview }} search={fullReviewSearch}>Open full review</Link></p> : !detail.data.current ? <div className="library-selection"><p>Case superseded</p><h2>{selectedIdentity.title}</h2><p role="alert">This review case is no longer current. Reopen its current replacement from Library.</p></div> : <>
          <div className="compact-workspace-controls"><button type="button" onClick={clearSelection}>Back to works</button><button type="button" onClick={() => void navigate({ search: (current) => ({ ...current, attention: 'open' }) })}>Attention queue</button></div>
          <header className="selected-work-header"><span className="library-book-mark" aria-hidden="true">{initials(selectedIdentity.title)}</span><div><p>Progressive detail</p><h2>{selectedIdentity.title}</h2><p>{selectedIdentity.authors.length ? selectedIdentity.authors.join(', ') : 'Author details unavailable'} · {selectedIdentity.library} · {selectedIdentity.mediaKind.replaceAll('_', ' ')}</p></div></header>
          <Link className="full-review-link" to="/review/$reviewCaseId" params={{ reviewCaseId: search.preview }} search={fullReviewSearch}>Open full review</Link>
          <p className="automatic-summary">{detail.data.fields.filter((field) => field.automaticDecision !== null).length} resolved automatically · {detail.data.fields.filter(actionable).length} need your decision</p>
          <nav className="field-filmstrip" aria-label="Fields needing attention">{detail.data.fields.filter((field) => actionable(field)).map((field, index) => <button key={field.destination} aria-current={selected?.destination === field.destination} onClick={() => select(selectedReviewCaseId, field.destination)}><strong>{friendly(field.destination)}</strong><small>{selected?.destination === field.destination ? `Field ${String(index + 1)} active` : 'Needs review'}</small></button>)}</nav>
          {selected ? <article className="decision-canvas"><header><div><p>Human attention</p><h2>{friendly(selected.destination)}</h2><p>{selected.notices[0] ?? 'Choose the value that should remain bound to this field.'}</p></div><dl><div><dt>Disposition</dt><dd>{friendly(selected.disposition)}</dd></div><div><dt>Choices</dt><dd>{selected.proposedValues.length + 1}</dd></div></dl></header>{actionable(selected) && !controller.reloadRequired ? <div className="direct-choice-grid"><button className="value-choice current-value" disabled={controller.isPending} onClick={() => choose(selected, 'keep_current')}><small>Current value</small><strong>{selected.currentValue}</strong><span>Retain this exact value</span></button>{selected.proposedValues.map((value, index) => <button className="value-choice proposed-value" key={index} disabled={controller.isPending} onClick={() => choose(selected, 'accept', value)}><small>Specific proposed value</small><strong>{displayValue(value.value)}</strong><span>Use this exact value</span></button>)}</div> : <p className="decision-recorded">This field is resolved and remains inspectable; it has no human control.</p>}<footer>{selected.provenance.slice(0, 3).map((source, index) => <span key={`${source}:${String(index)}`}>{evidenceSummary(source)}</span>)}</footer>{controller.status || controller.message ? <p role={controller.decision.isError || controller.reloadRequired ? 'alert' : 'status'}>{controller.status || controller.message}</p> : null}</article> : <p>This work has no fields requiring a decision.</p>}</>}
      </section>
      {search.attention === 'open' ? <aside className="attention-rail" aria-label="Attention queue"><header><h2>Attention</h2><button onClick={() => void navigate({ search: (current) => { const next = { ...current }; delete next.attention; return next } })}>Close</button></header>{attention.data === undefined ? <p role={attention.isError ? 'alert' : 'status'}>{attention.isError ? <>Attention could not be loaded. <button type="button" onClick={() => void attention.refetch()}>Try again</button></> : 'Loading attention…'}</p> : <>{attentionItems.map((item) => <button key={`${item.id}:${item.destination}`} className="attention-queue-item" onClick={() => selectFromQueue(item.id, item.destination)}><strong>{item.title}</strong><span>{friendly(item.destination)} · {item.notices[0] ?? 'Needs your decision'}</span></button>)}{attention.hasNextPage ? <button className="attention-load-more" type="button" disabled={attention.isFetchingNextPage} onClick={() => void attention.fetchNextPage()}>{attention.isFetchingNextPage ? 'Loading more…' : 'Load more attention'}</button> : null}{attention.isFetchNextPageError ? <p role="alert">More attention could not be loaded. <button type="button" onClick={() => void attention.fetchNextPage()}>Try again</button></p> : null}</>}</aside> : null}
    </div>
  </div>
}
