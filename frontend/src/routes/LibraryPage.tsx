import { useMutation, useQuery } from '@tanstack/react-query'
import { Link, useNavigate } from '@tanstack/react-router'
import type { SyntheticEvent } from 'react'

import { displayValue } from '../api/http'
import type { CaseDetailView, DecisionCommand, LibraryView, LibraryWorkView } from '../api/http'
import { useRouteReadyFocus } from '../focus'
import { attentionQuery, detailQuery, libraryRoute, useReviewContext } from '../router'

const friendly = (value: string) => value.replaceAll('_', ' ').replace(/^./, (letter) => letter.toUpperCase())
const authorLine = (work: LibraryWorkView) => work.authors.length ? work.authors.join(', ') : 'Author details unavailable'
const initials = (title: string) => title.split(/\s+/u).filter(Boolean).slice(0, 2).map((word) => word[0]?.toUpperCase()).join('') || 'ML'
const actionable = (field: CaseDetailView['fields'][number]) => field.needsAttention && field.humanDecision === null && field.automaticDecision === null
const requestKey = () => typeof crypto.randomUUID === 'function' ? crypto.randomUUID() : `decision-${Date.now().toString(36)}`

export default function LibraryPage({ data }: { data: LibraryView }) {
  const search = libraryRoute.useSearch()
  const navigate = useNavigate({ from: '/library' })
  const { http, queryClient } = useReviewContext()
  const listedSelectedWork = search.preview ? data.works.find((work) => work.id === search.preview) : undefined
  const detail = useQuery({ ...detailQuery(http, search.preview ?? ''), enabled: Boolean(search.preview) })
  const attention = useQuery({ ...attentionQuery(http, {}), enabled: search.attention === 'open' })
  const selected = detail.data?.fields.find((field) => field.destination === search.field) ?? detail.data?.fields.find(actionable) ?? detail.data?.fields[0]
  const decision = useMutation({ mutationFn: (command: DecisionCommand) => http.recordDecision(search.preview ?? '', command), retry: false, onSuccess: async () => {
    await Promise.all([queryClient.invalidateQueries({ queryKey: ['library'] }), queryClient.invalidateQueries({ queryKey: ['attention'] }), queryClient.invalidateQueries({ queryKey: ['review-case', search.preview] })])
  } })
  useRouteReadyFocus(`${search.lens}:${search.q ?? ''}:${search.cursor ?? ''}:${search.preview ?? ''}:${search.field ?? ''}:${search.attention ?? ''}`)

  const select = (id: string, field?: string) => void navigate({ search: (current) => ({ ...current, preview: id, ...(field ? { field } : {}) }) })
  const submit = (event: SyntheticEvent<HTMLFormElement, SubmitEvent>) => {
    event.preventDefault()
    const value = new FormData(event.currentTarget).get('q')
    const q = typeof value === 'string' ? value : ''
    void navigate({ search: (current) => { const next = { ...current }; delete next.cursor; delete next.preview; delete next.field; if (q) next.q = q; else delete next.q; return next } })
  }
  const choose = (field: CaseDetailView['fields'][number], outcome: DecisionCommand['outcome'], selectedValue: DecisionCommand['selectedValue'] = null) => decision.mutate({ destination: field.destination, outcome, proposalId: field.proposalId, selectedValue, targetFingerprint: field.targetFingerprint, idempotencyKey: requestKey() })

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
          const selectedWorkRow = search.preview === work.id; const needsAttention = work.attentionCount > 0
          return <article key={work.id} className={`library-work-row${selectedWorkRow ? ' active' : ''}`}><span className="library-book-mark" aria-hidden="true">{initials(work.title)}</span><div className="library-work-copy"><h3>{work.reviewCaseId ? <Link className="work-select" to="/review/$reviewCaseId" params={{ reviewCaseId: work.reviewCaseId }} search={{ subview: 'review' }}>{work.title}</Link> : work.title}</h3><p>{authorLine(work)}</p><p>{work.library} · {friendly(work.mediaKind)}</p><div className="library-work-status"><span className={needsAttention ? 'needs-attention' : 'handled'}>{needsAttention ? `${String(work.attentionCount)} field${work.attentionCount === 1 ? '' : 's'} need attention` : 'No attention needed'}</span></div></div>{work.reviewCaseId ? <button className="preview-button" type="button" aria-pressed={selectedWorkRow} onClick={() => select(work.id)}>Preview</button> : null}</article>
        })}</div>
        <div className="library-pager"><span>{data.nextCursor ? 'More current works are available' : 'End of the bounded current result'}</span>{data.nextCursor ? <Link to="/library" search={{ ...search, cursor: data.nextCursor }}>Next page →</Link> : null}</div>
      </section>
      <section className="workspace-detail" aria-live="polite">
        {!search.preview ? <div className="library-selection"><h2>Choose a work</h2><p>Its current values, field position, and bounded reviewer context stay here while you move through attention.</p></div> : !listedSelectedWork ? <div className="library-selection"><p>Progressive detail</p><h2>Selected work</h2><p>Readable work identity is not available on this Library page.</p><Link to="/review/$reviewCaseId" params={{ reviewCaseId: search.preview }} search={{ subview: 'review' }}>Open full review</Link></div> : detail.isPending ? <p>Loading preview…</p> : detail.isError ? <p role="alert">Selected work could not be loaded. <button onClick={() => void detail.refetch()}>Try again</button></p> : <>
          <header className="selected-work-header"><span className="library-book-mark" aria-hidden="true">{initials(listedSelectedWork.title)}</span><div><p>Progressive detail</p><h2>{listedSelectedWork.title}</h2><p>{authorLine(listedSelectedWork)} · {listedSelectedWork.library} · {listedSelectedWork.mediaKind.replaceAll('_', ' ')}</p></div></header>
          <p className="automatic-summary">{detail.data.fields.filter((field) => field.automaticDecision !== null).length} resolved automatically · {detail.data.fields.filter(actionable).length} need your decision</p>
          <nav className="field-filmstrip" aria-label="Fields needing attention">{detail.data.fields.filter((field) => actionable(field)).map((field, index) => <button key={field.destination} aria-current={selected?.destination === field.destination} onClick={() => select(search.preview ?? '', field.destination)}><strong>{friendly(field.destination)}</strong><small>{selected?.destination === field.destination ? `Field ${String(index + 1)} active` : 'Needs review'}</small></button>)}</nav>
          {selected ? <article className="decision-canvas"><header><div><p>Human attention</p><h2>{friendly(selected.destination)}</h2><p>{selected.notices[0] ?? 'Choose the value that should remain bound to this field.'}</p></div><dl><div><dt>Disposition</dt><dd>{friendly(selected.disposition)}</dd></div><div><dt>Choices</dt><dd>{selected.proposedValues.length + 1}</dd></div></dl></header>{actionable(selected) ? <div className="direct-choice-grid"><button className="value-choice current-value" disabled={decision.isPending} onClick={() => choose(selected, 'keep_current')}><small>Current value</small><strong>{selected.currentValue}</strong><span>Retain this exact value</span></button>{selected.proposedValues.map((value, index) => <button className="value-choice proposed-value" key={index} disabled={decision.isPending} onClick={() => choose(selected, 'accept', value)}><small>Specific proposed value</small><strong>{displayValue(value.value)}</strong><span>Use this exact value</span></button>)}</div> : <p className="decision-recorded">This field is resolved and remains inspectable; it has no human control.</p>}<footer>{selected.provenance.slice(0, 3).map((source, index) => <span key={source}>Reviewed evidence {String(index + 1)}: {friendly(source)}</span>)}</footer>{decision.isError ? <p role="alert">The choice was not recorded. Your current value is unchanged.</p> : null}</article> : <p>This work has no fields requiring a decision.</p>}</>}
      </section>
      {search.attention === 'open' ? <aside className="attention-rail" aria-label="Attention queue"><header><h2>Attention</h2><button onClick={() => void navigate({ search: (current) => { const next = { ...current }; delete next.attention; return next } })}>Close</button></header>{attention.isPending ? <p>Loading attention…</p> : attention.isError ? <p role="alert">Attention could not be loaded.</p> : attention.data.items.map((item) => <button key={`${item.id}:${item.destination}`} className="attention-queue-item" onClick={() => select(item.id, item.destination)}><strong>{item.title}</strong><span>{friendly(item.destination)} · {item.notices[0] ?? 'Needs your decision'}</span></button>)}</aside> : null}
    </div>
  </div>
}
