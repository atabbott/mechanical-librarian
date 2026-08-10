import { useInfiniteQuery, useMutation, useQuery } from '@tanstack/react-query'
import { Link, useNavigate } from '@tanstack/react-router'
import { useEffect, useRef, useState } from 'react'

import { AmbiguousMutationError, ApiError, displayValue } from '../api/http'
import type { CaseDetailView, DecisionCommand, DecisionOutcome, EvidenceView } from '../api/http'
import { useRouteReadyFocus } from '../focus'
import { detailRoute, useReviewContext } from '../router'

const friendly = (value: string) => value.replaceAll('_', ' ')
const requestKey = () => typeof crypto.randomUUID === 'function' ? crypto.randomUUID() : `decision-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`

export default function DetailPage({ data }: { data: CaseDetailView }) {
  const { http, queryClient } = useReviewContext()
  const search = detailRoute.useSearch()
  const { reviewCaseId } = detailRoute.useParams()
  const navigate = useNavigate({ from: '/review/$reviewCaseId' })
  const [status, setStatus] = useState('')
  const [pending, setPending] = useState<DecisionCommand | null>(null)
  const [editingDestination, setEditingDestination] = useState<string | null>(null)
  const canonicalized = useRef('')
  const evidence = useInfiniteQuery({ queryKey: ['review-case', reviewCaseId, 'evidence'], initialPageParam: undefined as string | undefined, queryFn: ({ pageParam, signal }) => http.getEvidence(reviewCaseId, pageParam, signal), getNextPageParam: (page: EvidenceView) => page.next_cursor ?? undefined, staleTime: 30_000, retry: false })
  const history = useQuery({ queryKey: ['review-case', reviewCaseId, 'history'], queryFn: ({ signal }) => http.getHistory(reviewCaseId, signal), staleTime: 30_000, retry: false })
  const settle = async () => { await Promise.all([queryClient.invalidateQueries({ queryKey: ['review-case', reviewCaseId] }), queryClient.invalidateQueries({ queryKey: ['library'] }), queryClient.invalidateQueries({ queryKey: ['attention'] })]) }
  const decision = useMutation({ mutationFn: (command: DecisionCommand) => http.recordDecision(reviewCaseId, command), retry: false, onSuccess: async () => { setPending(null); setStatus('Decision recorded from the authoritative case.'); await settle() }, onError: async (error) => { await settle(); if (error instanceof ApiError && error.code === 'stale') { setStatus('This case was superseded. Reopen its current replacement from Library.'); await navigate({ to: '/library', search: { lens: 'needs_attention' } }); return } if (error instanceof ApiError && error.code === 'already_decided') { setPending(null); setStatus('The authoritative decision was reloaded.'); return } setStatus(error instanceof AmbiguousMutationError ? 'The response was lost. The current case was reloaded before any retry.' : 'The decision was not recorded. Your choice is unchanged.') } })
  const returnAutomatic = useMutation({ mutationFn: (destination: string) => http.returnToAutomatic(reviewCaseId, destination), retry: false, onSuccess: async () => { setStatus('Automatic policy resumes after fresh checks.'); await settle() }, onError: async () => { setStatus('Manual hold is unchanged. Try again.'); await settle() } })
  const selected = data.fields.some((field) => field.destination === search.field) ? search.field : data.fields[0]?.destination
  useRouteReadyFocus(`${search.subview}:${selected ?? ''}`)
  useEffect(() => { const identity = `${reviewCaseId}:${selected ?? ''}:${search.subview}`; if (canonicalized.current === identity) return; canonicalized.current = identity; void navigate({ replace: true, search: { subview: search.subview, ...(selected ? { field: selected } : {}) } }) }, [navigate, reviewCaseId, search.subview, selected])
  const pages = evidence.data?.pages.flatMap((page) => page.items) ?? []
  const submit = (field: CaseDetailView['fields'][number], outcome: DecisionOutcome, selectedValue: DecisionCommand['selectedValue'] = null, reuse = false) => {
    const command: DecisionCommand = reuse && pending ? pending : { destination: field.destination, outcome, proposalId: field.proposalId, selectedValue, targetFingerprint: field.targetFingerprint, idempotencyKey: requestKey() }
    setPending(command); setStatus(`Recording ${friendly(outcome)}…`); decision.mutate(command)
  }
  if (!data.current) return <section className="route-state" data-route-focus tabIndex={-1}><h1>Case superseded</h1><p role="alert">Failed or superseded cases are never treated as current.</p><Link to="/library" search={{ lens: 'needs_attention' }}>Return to library</Link></section>
  return <>
    <header className="page-heading"><div><p className="eyebrow">Exact-current review</p><h1>Current review case</h1><p><strong>{data.title}</strong> · {data.subtitle}</p></div><span className="badge">Current</span></header>
    <nav className="subviews" aria-label="Case view"><button aria-current={search.subview === 'review'} onClick={() => void navigate({ search: { ...search, subview: 'review' } })}>Review</button><button aria-current={search.subview === 'evidence'} onClick={() => void navigate({ search: { ...search, subview: 'evidence' } })}>Evidence</button><button aria-current={search.subview === 'history'} onClick={() => void navigate({ search: { ...search, subview: 'history' } })}>History</button></nav>
    {search.subview === 'review' ? <div className="detail-layout">
      <nav className="field-navigation" aria-label="Review fields">{data.fields.map((field) => <button key={field.destination} aria-current={selected === field.destination} onClick={() => void navigate({ search: { ...search, field: field.destination } })}>{friendly(field.destination)}</button>)}</nav>
      <section className="field-list" aria-label="Review fields">{data.fields.map((field) => {
        const hasDecision = field.humanDecision !== null || field.automaticDecision !== null
        const choicesVisible = field.needsAttention || editingDestination === field.destination
        return <article id={`field-${field.destination}`} key={field.destination} hidden={selected !== field.destination} tabIndex={-1} data-route-focus={selected === field.destination ? '' : undefined}>
          <h2>{friendly(field.destination)}</h2>
          <div className="comparison"><div><small>Current</small><p>{field.currentValue}</p></div><div><small>Proposed</small><p>{field.proposedValues.length ? field.proposedValues.map((value) => displayValue(value.value)).join(' · ') : 'No proposed value'}</p></div></div>
          {field.notices.map((notice) => <p key={notice}>{notice}</p>)}
          {field.humanDecision ? <p className="decision-recorded">Human decision: {friendly(field.humanDecision.outcome)} by {field.humanDecision.actor}.</p> : field.automaticDecision ? <p className="decision-recorded">Handled automatically: {friendly(field.automaticDecision.outcome)} · policy origin.</p> : null}
          {hasDecision && !choicesVisible ? <button className="inline-action" onClick={() => setEditingDestination(field.destination)}>{field.humanDecision ? 'Change human decision' : 'Choose human override'}</button> : null}
          {choicesVisible ? <div className="choices">{field.proposedValues.map((value, index) => <button key={`${value.kind}:${String(index)}`} disabled={decision.isPending} onClick={() => submit(field, 'accept', value)}>{field.proposedValues.length === 1 ? 'Accept proposed value' : `Accept proposed value ${String(index + 1)}: ${displayValue(value.value)}`}</button>)}<button disabled={decision.isPending} onClick={() => submit(field, 'keep_current')}>Keep current value</button><button disabled={decision.isPending} onClick={() => submit(field, 'reject')}>Reject proposed change</button></div> : null}
          {field.manualHold ? <button disabled={returnAutomatic.isPending} onClick={() => returnAutomatic.mutate(field.destination)}>Return to automatic handling</button> : null}
          {decision.isError && pending?.destination === field.destination ? <button onClick={() => submit(field, pending.outcome, pending.selectedValue, true)}>Try again</button> : null}
        </article>
      })}</section>
    </div> : null}
    {search.subview === 'evidence' ? <section className="progressive-panel" data-route-focus tabIndex={-1}><h2>Evidence and source ledger</h2>{evidence.isPending ? <p role="status">Loading evidence…</p> : evidence.isError ? <><p role="alert">Evidence could not be loaded.</p><button onClick={() => void evidence.refetch()}>Try again</button></> : <>{pages.map((item) => <article className="evidence-item" key={item.reference}><h3>{friendly(item.kind)}</h3><pre>{item.fact}</pre></article>)}{evidence.hasNextPage ? <button disabled={evidence.isFetchingNextPage} onClick={() => void evidence.fetchNextPage()}>Load more evidence</button> : <p>All referenced evidence is shown.</p>}</>}</section> : null}
    {search.subview === 'history' ? <section className="progressive-panel" data-route-focus tabIndex={-1}><h2>Current-case history</h2>{history.isPending ? <p role="status">Loading history…</p> : history.isError ? <><p role="alert">History could not be loaded.</p><button onClick={() => void history.refetch()}>Try again</button></> : history.data.items.map((item, index) => <p key={index}>{friendly(item.kind)} · {'destination' in item ? item.destination : ''}</p>)}</section> : null}
    <p role={decision.isError || returnAutomatic.isError ? 'alert' : 'status'}>{status}</p>
  </>
}
