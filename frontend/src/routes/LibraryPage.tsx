import { useInfiniteQuery, useQuery } from '@tanstack/react-query'
import { Link, useNavigate } from '@tanstack/react-router'
import { useEffect, useRef, useState } from 'react'
import type { SyntheticEvent } from 'react'

import { displayValue } from '../api/http'
import type { CaseDetailView, DecisionCommand, EvidenceView, LibraryView, LibraryWorkView } from '../api/http'
import { useRouteReadyFocus } from '../focus'
import { detailQuery, libraryRoute, useReviewContext } from '../router'
import { actionable, useReviewDecisionController } from '../reviewDecisionController'

type ReviewField = CaseDetailView['fields'][number]
type EvidenceItem = EvidenceView['items'][number]
interface EvidenceRow { basis: string; observed: string; source: string }

const friendly = (value: string) => value.replaceAll('_', ' ').replace(/^./, (letter) => letter.toUpperCase())
const authorLine = (work: LibraryWorkView) => work.authors.length ? work.authors.join(', ') : 'Author details unavailable'
const initials = (title: string) => title.split(/\s+/u).filter(Boolean).slice(0, 2).map((word) => word[0]?.toUpperCase()).join('') || 'ML'
const requestKey = () => typeof crypto.randomUUID === 'function' ? crypto.randomUUID() : `decision-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`
const opaqueSource = /^(?:local|evidence|provider)_[0-9a-f]+$/iu
const bounded = (value: string, fallback: string) => opaqueSource.test(value) ? fallback : value.length > 160 ? `${value.slice(0, 157)}…` : value
const decisionValue = (field: ReviewField) => {
  const selected = field.humanDecision?.selected_value ?? field.automaticDecision?.selected_value
  return selected === null || selected === undefined ? field.currentValue : displayValue(selected.value)
}
const fieldState = (field: ReviewField) => field.humanDecision
  ? 'Owner decision recorded'
  : field.automaticDecision
    ? 'Resolved automatically'
    : actionable(field)
      ? 'Needs review'
      : 'No eligible change'
const fieldReason = (field: ReviewField) => bounded(field.notices[0] ?? (
  field.humanDecision
    ? `${friendly(field.humanDecision.outcome)} recorded for this field.`
    : field.automaticDecision
      ? `${friendly(field.automaticDecision.outcome)} selected by deterministic policy.`
      : actionable(field)
        ? `${friendly(field.disposition)} requires an owner comparison of the retained values.`
        : `${friendly(field.disposition)} disposition; no human action is available.`
), 'Retained reviewer notice')
const evidenceRows = (field: ReviewField, evidence: readonly EvidenceItem[]): readonly EvidenceRow[] => {
  const detailed = evidence
    .filter((item) => item.kind === field.destination)
    .map((item, index) => ({
      source: bounded(item.provider ?? item.source, `Evidence source ${String(index + 1)}`),
      observed: bounded(item.fact, 'Retained observation'),
      basis: `${friendly(item.source)} · ${friendly(item.kind)}`,
    }))
  const provenance = field.provenance.map((item, index) => {
    const [label, ...rest] = item.split(':')
    return {
      source: bounded(label?.trim() ?? item, `Reviewed source ${String(index + 1)}`),
      observed: bounded(rest.join(':').trim() || 'Supports the field comparison', 'Retained observation'),
      basis: 'Reviewer provenance',
    }
  })
  return [...detailed, ...provenance]
    .filter((row, index, rows) => rows.findIndex((candidate) => candidate.source === row.source && candidate.observed === row.observed) === index)
    .slice(0, 6)
}
const effectSummary = (field: ReviewField) => field.effectCapability === 'allowed'
  ? { state: 'Eligible for guarded planning', detail: 'This decision queues nothing. Backup, currentness, validation and read-back checks remain separate gates.' }
  : field.effectCapability === 'unavailable'
    ? { state: 'External update unavailable', detail: 'The decision can still be recorded; no external update capability is configured.' }
    : { state: 'Guarded from external update', detail: `${friendly(field.effectCapability)} currently prevents an external update.` }

export default function LibraryPage({ data }: { data: LibraryView }) {
  const search = libraryRoute.useSearch()
  const navigate = useNavigate({ from: '/library' })
  const { http } = useReviewContext()
  const currentSearch = useRef(search)
  const queuedSuccessor = useRef<{ id: string; destination: string } | undefined>(undefined)
  const retryButton = useRef<HTMLButtonElement>(null)
  const [frozenFieldOrders, setFrozenFieldOrders] = useState<Readonly<Record<string, readonly string[]>>>({})
  const selectedReviewCaseId = search.preview ?? ''
  const detail = useQuery({ ...detailQuery(http, selectedReviewCaseId), enabled: Boolean(search.preview) })
  const evidence = useInfiniteQuery({
    queryKey: ['review-case', selectedReviewCaseId, 'evidence'],
    initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam, signal }) => http.getEvidence(selectedReviewCaseId, pageParam, signal),
    getNextPageParam: (page: EvidenceView) => page.next_cursor ?? undefined,
    staleTime: 30_000,
    retry: false,
    enabled: Boolean(search.preview && detail.data?.current),
  })
  const attention = useInfiniteQuery({
    queryKey: ['attention', 'workspace'],
    initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam, signal }) => http.getAttention(pageParam ? { cursor: pageParam } : {}, signal),
    getNextPageParam: (page) => page.nextCursor ?? undefined,
    staleTime: 15_000,
    retry: false,
  })
  const attentionItems = attention.data?.pages.flatMap((page) => page.items) ?? []
  const evidenceItems = evidence.data?.pages.flatMap((page) => page.items) ?? []
  const selectedIdentity = search.preview
    ? data.works.find((work) => work.reviewCaseId === search.preview) ?? attentionItems.find((item) => item.id === search.preview)
    : undefined
  const selected = detail.data?.fields.find((field) => field.destination === search.field)
    ?? detail.data?.fields.find(actionable)
    ?? detail.data?.fields[0]
  const currentActionableDestinations = detail.data?.fields.filter(actionable).map((field) => field.destination) ?? []
  const frozenDestinations = frozenFieldOrders[selectedReviewCaseId] ?? currentActionableDestinations
  const actionableFields = frozenDestinations
    .map((destination) => detail.data?.fields.find((field) => field.destination === destination))
    .filter((field): field is ReviewField => field !== undefined && actionable(field))
  const contextualFields = detail.data?.fields.filter((field) => !actionable(field)) ?? []
  const activeQueueIndex = selected
    ? attentionItems.findIndex((item) => item.id === search.preview && item.destination === selected.destination)
    : -1
  const fullReviewSearch = { subview: 'review' as const, ...(selected ? { field: selected.destination } : {}) }
  const controller = useReviewDecisionController({
    reviewCaseId: selectedReviewCaseId,
    fields: detail.data?.fields ?? [],
    active: (command) => currentSearch.current.preview === search.preview
      && search.preview !== undefined
      && (currentSearch.current.field === undefined || currentSearch.current.field === command.destination),
    onAdvance: async (field) => { await navigate({ search: (current) => current.preview === search.preview ? { ...current, field } : current }) },
    onComplete: async () => {
      const current = currentSearch.current
      const next = queuedSuccessor.current
      if (next && current.preview === search.preview && currentSearch.current.preview === search.preview) {
        await navigate({ search: (state) => state.preview === search.preview ? { ...state, preview: next.id, field: next.destination } : state })
      }
    },
    onStale: async () => { await navigate({ search: { lens: 'needs_attention' } }) },
  })

  useRouteReadyFocus(`${search.lens}:${search.q ?? ''}:${search.cursor ?? ''}:${search.preview ?? ''}:${search.field ?? ''}:${search.attention ?? ''}`)
  useEffect(() => { currentSearch.current = search }, [search])
  useEffect(() => { if (controller.canRetry) retryButton.current?.focus() }, [controller.canRetry])
  useEffect(() => {
    if (typeof window.matchMedia !== 'function') return
    const media = window.matchMedia('(min-width: 701px)')
    const selectWideDefault = () => {
      if (!media.matches || currentSearch.current.preview) return
      const first = data.works.find((work) => work.reviewCaseId && work.attentionCount > 0)
        ?? data.works.find((work) => work.reviewCaseId)
      const reviewCaseId = first?.reviewCaseId
      if (reviewCaseId) void navigate({ replace: true, search: (current) => current.preview ? current : { ...current, preview: reviewCaseId } })
    }
    selectWideDefault()
    media.addEventListener('change', selectWideDefault)
    return () => media.removeEventListener('change', selectWideDefault)
  }, [data.works, navigate])

  const select = (reviewCaseId: string, field?: string) => void navigate({ search: (current) => {
    const next = { ...current, preview: reviewCaseId }
    if (field) next.field = field
    else delete next.field
    return next
  } })
  const submitSearch = (event: SyntheticEvent<HTMLFormElement, SubmitEvent>) => {
    event.preventDefault()
    const value = new FormData(event.currentTarget).get('q')
    const q = typeof value === 'string' ? value : ''
    void navigate({ search: (current) => {
      const next = { ...current }
      delete next.cursor
      delete next.preview
      delete next.field
      if (q) next.q = q
      else delete next.q
      return next
    } })
  }
  const choose = (field: ReviewField, outcome: DecisionCommand['outcome'], selectedValue: DecisionCommand['selectedValue'] = null) => {
    setFrozenFieldOrders((current) => current[selectedReviewCaseId] === undefined
      ? { ...current, [selectedReviewCaseId]: currentActionableDestinations }
      : current)
    const afterCurrent = activeQueueIndex < 0 ? [] : attentionItems.slice(activeQueueIndex + 1)
    const successor = afterCurrent.find((item) => item.id !== search.preview)
    queuedSuccessor.current = successor ? { id: successor.id, destination: successor.destination } : undefined
    controller.submit({ destination: field.destination, outcome, proposalId: field.proposalId, selectedValue, targetFingerprint: field.targetFingerprint, idempotencyKey: requestKey() })
  }
  const moveQueue = (offset: -1 | 1) => {
    const item = attentionItems[activeQueueIndex + offset]
    if (item) select(item.id, item.destination)
  }
  const clearSelection = () => void navigate({ search: (current) => {
    const next = { ...current }
    delete next.preview
    delete next.field
    delete next.attention
    return next
  } })
  const selectFromQueue = (reviewCaseId: string, field: string) => void navigate({ search: (current) => {
    const next = { ...current, preview: reviewCaseId, field }
    delete next.attention
    return next
  } })

  const rows = selected ? evidenceRows(selected, evidenceItems) : []
  const sourceCount = new Set(rows.map((row) => row.source)).size
  const sourceSupport = evidence.isPending
    ? 'supporting sources loading'
    : sourceCount
      ? `${String(sourceCount)} named source${sourceCount === 1 ? '' : 's'} below`
      : 'no retained source records below'
  const effect = selected ? effectSummary(selected) : undefined
  const sourceLedgerRows = rows.length ? <div className="source-ledger-table" role="table" aria-label="Evidence ledger"><div className="source-ledger-row source-ledger-head" role="row"><span role="columnheader">Source</span><span role="columnheader">Observed value</span><span role="columnheader">Basis</span></div>{rows.map((row, index) => <div className="source-ledger-row" role="row" key={`${row.source}:${row.observed}:${String(index)}`}><span role="cell"><small className="source-cell-label">Source</small><strong>{row.source}</strong></span><span role="cell"><small className="source-cell-label">Observed value</small>{row.observed}</span><span role="cell"><small className="source-cell-label">Basis</small>{row.basis}</span></div>)}</div> : null

  return <div className="library-page variant-m-workspace">
    <header className="library-header" tabIndex={-1} data-route-focus>
      <div><h1>Every work, with exceptions first</h1><p>Library keeps the current collection and human attention in one workspace.</p></div>
      <button className="attention-toggle" type="button" aria-expanded={search.attention === 'open'} onClick={() => void navigate({ search: (current) => {
        const next = { ...current }
        if (next.attention === 'open') delete next.attention
        else next.attention = 'open'
        return next
      } })}>Attention <strong>{data.counts.attention}</strong></button>
    </header>
    <div className={`library-workspace${search.preview ? ' has-selection' : ''}${search.attention === 'open' ? ' attention-open' : ''}`}>
      <section className="library-works" aria-labelledby="library-works-title">
        <div className="library-works-heading"><h2 id="library-works-title">Works</h2><span>{data.counts.all} total</span></div>
        <nav className="lenses" aria-label="Library lenses">{(['all', 'needs_attention', 'no_attention_needed'] as const).map((lens) => <Link key={lens} to="/library" search={{ lens, ...(search.q ? { q: search.q } : {}) }} preload="intent" activeProps={{ 'aria-current': 'page' }}>{friendly(lens === 'no_attention_needed' ? 'No attention needed' : lens)}</Link>)}</nav>
        <form className="search library-search" onSubmit={submitSearch}><label className="sr-only" htmlFor="library-query">Search title, author, library, or media</label><div><input key={search.q} id="library-query" name="q" type="search" maxLength={128} defaultValue={search.q ?? ''} placeholder="Search title, author, library…"/><button>Search</button></div></form>
        <p className="library-order">Attention first · bounded current results</p>
        <div className="library-work-list">{data.works.length === 0 ? <p className="empty-copy">No exact-current works match this search and lens.</p> : data.works.map((work) => {
          const selectedWorkRow = search.preview === work.reviewCaseId
          const needsAttention = work.attentionCount > 0
          return <article key={work.id} className={`library-work-row${selectedWorkRow ? ' active' : ''}`}>
            <span className="library-book-mark" aria-hidden="true">{initials(work.title)}</span>
            <div className="library-work-copy"><h3>{work.reviewCaseId ? <button className="work-select" type="button" aria-pressed={selectedWorkRow} onClick={() => select(work.reviewCaseId ?? '')}>{work.title}</button> : work.title}</h3><p>{authorLine(work)}</p><p>{work.library} · {friendly(work.mediaKind)}</p><div className="library-work-status"><span className={needsAttention ? 'needs-attention' : 'handled'}>{needsAttention ? `${String(work.attentionCount)} field${work.attentionCount === 1 ? '' : 's'} need attention` : 'No attention needed'}</span></div></div>
          </article>
        })}</div>
        <div className="library-pager"><span>{data.nextCursor ? 'More current works are available' : 'End of the bounded current result'}</span>{data.nextCursor ? <Link to="/library" search={{ ...search, cursor: data.nextCursor }}>Next page →</Link> : null}</div>
      </section>
      <section className="workspace-detail" aria-live="polite">
        {!search.preview ? <div className="library-selection"><h2>Choose a work</h2><p>Its current values, field position, and bounded reviewer context stay here while you move through attention.</p></div>
          : !selectedIdentity ? <div className="library-selection"><p>Progressive detail</p><h2>Selected work</h2><p>Readable work identity is not available on this Library page.</p><Link to="/review/$reviewCaseId" params={{ reviewCaseId: search.preview }} search={fullReviewSearch}>Open full review</Link></div>
            : detail.isPending ? <p role="status">Loading selected work…</p>
              : detail.isError ? <p role="alert">Selected work could not be loaded. <button onClick={() => void detail.refetch()}>Try again</button> <Link className="full-review-link" to="/review/$reviewCaseId" params={{ reviewCaseId: search.preview }} search={fullReviewSearch}>Open full review</Link></p>
                : !detail.data.current ? <div className="library-selection"><p>Case superseded</p><h2>{selectedIdentity.title}</h2><p role="alert">This review case is no longer current. Reopen its current replacement from Library.</p></div>
                  : <>
                    <div className="compact-workspace-controls"><button type="button" onClick={clearSelection}>Back to works</button><button type="button" onClick={() => void navigate({ search: (current) => ({ ...current, attention: 'open' }) })}>Attention queue</button></div>
                    <header className="selected-work-header"><span className="library-book-mark" aria-hidden="true">{initials(selectedIdentity.title)}</span><div><p>Progressive detail</p><h2>{selectedIdentity.title}</h2><p>{selectedIdentity.authors.length ? selectedIdentity.authors.join(', ') : 'Author details unavailable'} · {selectedIdentity.library} · {friendly(selectedIdentity.mediaKind).toLowerCase()}</p></div><dl className="selected-work-status"><div><dt>Needs review</dt><dd>{String(actionableFields.length)}</dd></div><div><dt>Resolved automatically</dt><dd>{String(detail.data.fields.filter((field) => field.automaticDecision !== null).length)}</dd></div></dl></header>
                    <div className="workspace-utilities"><Link className="full-review-link" to="/review/$reviewCaseId" params={{ reviewCaseId: search.preview }} search={fullReviewSearch}>Open full review</Link><Link className="full-review-link" to="/review/$reviewCaseId" params={{ reviewCaseId: search.preview }} search={{ subview: 'evidence', ...(selected ? { field: selected.destination } : {}) }}>Open all evidence</Link></div>
                    <nav className="field-filmstrip" aria-label="Fields needing attention">{actionableFields.map((field) => {
                      const position = frozenDestinations.indexOf(field.destination) + 1
                      const total = frozenDestinations.length
                      return <button key={field.destination} aria-current={selected?.destination === field.destination} onClick={() => select(selectedReviewCaseId, field.destination)}><strong>{friendly(field.destination)}</strong><small>{selected?.destination === field.destination ? `${String(position)} of ${String(total)} · active` : `${String(position)} of ${String(total)} · needs review`}</small><span>Current: {field.currentValue}</span></button>
                    })}</nav>
                    {contextualFields.length ? <details className="automatic-resolution-details"><summary><span>{String(contextualFields.length)} automatic or recorded field{contextualFields.length === 1 ? '' : 's'}</span><small>Inspect context</small></summary><div className="automatic-resolution-list">{contextualFields.map((field) => <div className="automatic-resolution-row" key={field.destination}><span className="automatic-state-mark" aria-hidden="true">✓</span><div><strong>{friendly(field.destination)}</strong><small>{fieldState(field)} · {decisionValue(field)}</small><small>{fieldReason(field)}</small></div></div>)}</div></details> : null}
                    {selected ? <article className="decision-canvas">
                      <header><div><p>Human attention</p><h2>{friendly(selected.destination)}</h2><p>{fieldReason(selected)}</p></div><dl><div><dt>Queue position</dt><dd>{activeQueueIndex >= 0 ? `${String(activeQueueIndex + 1)} of ${String(attentionItems.length)}` : 'Selected work'}</dd></div><div><dt>Choices</dt><dd>{actionable(selected) ? String(selected.proposedValues.length + 1) : '0'}</dd></div></dl></header>
                      <div className="queue-movement" aria-label="Move through attention"><button type="button" disabled={activeQueueIndex <= 0 || controller.isPending} onClick={() => moveQueue(-1)}>← Previous</button><button type="button" disabled={activeQueueIndex < 0 || activeQueueIndex >= attentionItems.length - 1 || controller.isPending} onClick={() => moveQueue(1)}>Next →</button></div>
                      {actionable(selected) && !controller.reloadRequired ? <section className="direct-choice" aria-label={`Choose a ${friendly(selected.destination)} value`}><p className="choice-intro">Select the exact value that should remain bound to this field. Evidence informs the choice but never makes it for you.</p><div className="direct-choice-grid"><button aria-label={`Choose current value: ${selected.currentValue}`} className="value-choice current-value" disabled={controller.isPending} onClick={() => choose(selected, 'keep_current')}><small>Current value</small><strong>{selected.currentValue}</strong><span>Current catalogue value · compare {sourceSupport}</span></button>{selected.proposedValues.map((value, index) => <button aria-label={`Choose proposed value ${String(index + 1)}: ${displayValue(value.value)}`} className="value-choice proposed-value" key={`${value.kind}:${String(index)}`} disabled={controller.isPending} onClick={() => choose(selected, 'accept', value)}><small>Proposed value {String(index + 1)}</small><strong>{displayValue(value.value)}</strong><span>Eligible proposal · compare the same retained evidence</span></button>)}</div></section> : <p className="decision-recorded">{fieldState(selected)}. The recorded value remains {decisionValue(selected)}; this field has no human control.</p>}
                      {controller.canRetry && controller.pending?.destination === selected.destination ? <button ref={retryButton} className="decision-retry" type="button" onClick={controller.retry}>Try the same decision again</button> : null}
                      {effect ? <aside className="field-apply-context" aria-label="External update status"><span className="activity-state-mark" aria-hidden="true"></span><span><small>External library</small><strong>{effect.state}</strong><em>{effect.detail}</em></span></aside> : null}
                      <details className="source-ledger" open><summary><span>Source ledger</span><small>{String(rows.length)} record{rows.length === 1 ? '' : 's'} · {String(sourceCount)} named source{sourceCount === 1 ? '' : 's'}</small></summary>{evidence.isPending ? <p role="status">Loading supporting evidence…</p> : <>{evidence.isError ? <p role="alert">Supplemental evidence could not be loaded. Retained case provenance remains below. <button type="button" onClick={() => void evidence.refetch()}>Try again</button></p> : null}{sourceLedgerRows ?? (evidence.isError ? null : <p>No bounded source record is available for this field.</p>)}</>}</details>
                      {controller.status || controller.message ? <p role={controller.decision.isError || controller.reloadRequired ? 'alert' : 'status'}>{controller.status || controller.message}</p> : null}
                    </article> : <p>This work has no fields requiring a decision.</p>}
                  </>}
      </section>
      {search.attention === 'open' ? <aside className="attention-rail" aria-label="Attention queue"><header><div><h2>Attention</h2><p>{String(attentionItems.length)} visible fields</p></div><button onClick={() => void navigate({ search: (current) => { const next = { ...current }; delete next.attention; return next } })}>Close</button></header>{attention.data === undefined ? <p role={attention.isError ? 'alert' : 'status'}>{attention.isError ? <>Attention could not be loaded. <button type="button" onClick={() => void attention.refetch()}>Try again</button></> : 'Loading attention…'}</p> : <>{attentionItems.map((item, index) => <button key={`${item.id}:${item.destination}`} className="attention-queue-item" aria-current={item.id === search.preview && item.destination === selected?.destination} onClick={() => selectFromQueue(item.id, item.destination)}><span className="attention-position">{String(index + 1)}</span><span className="attention-queue-copy"><strong>{item.title}</strong><small>{item.authors.length ? item.authors.join(', ') : 'Author details unavailable'}</small><small>{item.library} · {friendly(item.mediaKind)}</small><b>{friendly(item.destination)} · {item.notices[0] ? bounded(item.notices[0], 'Reviewer notice') : 'Needs your decision'}</b></span><span className="attention-work-count"><strong>{String(item.workAttentionCount)}</strong><small>for work</small></span></button>)}{attention.hasNextPage ? <button className="attention-load-more" type="button" disabled={attention.isFetchingNextPage} onClick={() => void attention.fetchNextPage()}>{attention.isFetchingNextPage ? 'Loading more…' : 'Load more attention'}</button> : null}{attention.isFetchNextPageError ? <p role="alert">More attention could not be loaded. <button type="button" onClick={() => void attention.fetchNextPage()}>Try again</button></p> : null}</>}</aside> : null}
    </div>
  </div>
}
