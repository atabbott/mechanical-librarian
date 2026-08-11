import { useInfiniteQuery, useMutation, useQuery } from '@tanstack/react-query'
import { Link, useNavigate, useRouterState } from '@tanstack/react-router'
import { useEffect, useRef, useState } from 'react'

import { AmbiguousMutationError, ApiError, displayValue } from '../api/http'
import type { CaseDetailView, DecisionCommand, DecisionOutcome, EvidenceView } from '../api/http'
import { useRouteReadyFocus } from '../focus'
import { detailQuery, detailRoute, useReviewContext } from '../router'

const friendly = (value: string) => value.replaceAll('_', ' ')
const readable = (value: string) => `${friendly(value).slice(0, 1).toUpperCase()}${friendly(value).slice(1)}`
const opaqueSource = /^(?:local|evidence|provider)_[0-9a-f]+$/iu
const reviewedSource = (value: string, index: number) => opaqueSource.test(value) ? `Evidence record ${String(index + 1)}` : readable(value)
const requestKey = () => typeof crypto.randomUUID === 'function' ? crypto.randomUUID() : `decision-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`
const recordedValue = (value: unknown, current: string) => value === null ? current : typeof value === 'object' && 'value' in value ? displayValue(value.value) : displayValue(value)
const actionable = (field: CaseDetailView['fields'][number]) => field.needsAttention && field.humanDecision === null && field.automaticDecision === null
// Keep the field queue stable: after a recorded decision, move forward in displayed
// order and wrap once; terminal cases remain on the authoritative selected field.
const nextUnresolved = (order: readonly string[], fields: CaseDetailView['fields'], destination: string) => {
  const index = order.indexOf(destination)
  const ordered = index < 0 ? order : [...order.slice(index + 1), ...order.slice(0, index)]
  return ordered.map((name) => fields.find((field) => field.destination === name)).find((field): field is CaseDetailView['fields'][number] => field !== undefined && actionable(field))
}

export default function DetailPage({ data }: { data: CaseDetailView }) {
  const { http, queryClient } = useReviewContext()
  const search = detailRoute.useSearch()
  const { reviewCaseId } = detailRoute.useParams()
  const location = useRouterState({ select: (state) => state.location })
  const navigate = useNavigate({ from: '/review/$reviewCaseId' })
  const [status, setStatus] = useState('')
  const [pending, setPending] = useState<DecisionCommand | null>(null)
  const [reloadRequired, setReloadRequired] = useState(false)
  const canonicalized = useRef('')
  const submittedFieldOrder = useRef<readonly string[]>([])
  const currentSearch = useRef(search)
  const currentLocation = useRef(location)
  const mounted = useRef(true)
  const evidence = useInfiniteQuery({ queryKey: ['review-case', reviewCaseId, 'evidence'], initialPageParam: undefined as string | undefined, queryFn: ({ pageParam, signal }) => http.getEvidence(reviewCaseId, pageParam, signal), getNextPageParam: (page: EvidenceView) => page.next_cursor ?? undefined, staleTime: 30_000, retry: false })
  const history = useQuery({ queryKey: ['review-case', reviewCaseId, 'history'], queryFn: ({ signal }) => http.getHistory(reviewCaseId, signal), staleTime: 30_000, retry: false })
  const settle = async () => {
    await Promise.all([queryClient.invalidateQueries({ queryKey: ['library'] }), queryClient.invalidateQueries({ queryKey: ['attention'] })])
    try {
      return await queryClient.fetchQuery({ ...detailQuery(http, reviewCaseId), staleTime: 0 })
    } catch {
      return undefined
    }
  }
  const decision = useMutation({ mutationFn: (command: DecisionCommand) => http.recordDecision(reviewCaseId, command), retry: false, onSuccess: async (_, command) => {
    const refreshed = await settle()
    setPending(null)
    if (!refreshed) {
      setReloadRequired(true)
      setStatus('Decision recorded, but the current case could not be refreshed. Reload before reviewing another value.')
      return
    }
    const next = nextUnresolved(submittedFieldOrder.current, refreshed.fields, command.destination)
    const stillReviewingSubmittedField = mounted.current && currentLocation.current.pathname === `/review/${reviewCaseId}` && currentSearch.current.subview === 'review' && currentSearch.current.field === command.destination
    setStatus(stillReviewingSubmittedField && next ? `Decision recorded. Continuing with ${friendly(next.destination)}.` : 'Decision recorded from the authoritative case.')
    if (stillReviewingSubmittedField && next) await navigate({ search: { ...currentSearch.current, field: next.destination, subview: 'review' } })
  }, onError: async (error, command) => {
    const refreshed = await settle()
    const refreshedField = refreshed?.fields.find((field) => field.destination === command.destination)
    if (refreshedField && !actionable(refreshedField)) setPending(null)
    if (error instanceof ApiError && error.code === 'stale') {
      setStatus('This case was superseded. Reopen its current replacement from Library.')
      await navigate({ to: '/library', search: { lens: 'needs_attention' } })
      return
    }
    if (!refreshed) {
      setReloadRequired(true)
      setStatus(error instanceof AmbiguousMutationError
        ? 'The decision outcome is uncertain and the current case could not be refreshed. Reload to reconcile it before reviewing another value.'
        : 'The current decision state could not be reconciled because the case could not be refreshed. Reload before reviewing another value.')
      return
    }
    if (error instanceof ApiError && error.code === 'already_decided') {
      setPending(null)
      setStatus('The authoritative decision was reloaded.')
      return
    }
    setStatus(error instanceof AmbiguousMutationError ? 'The response was lost. The current case was reloaded before any retry.' : 'The decision was not recorded. Your choice is unchanged.')
  } })
  const returnAutomatic = useMutation({ mutationFn: (destination: string) => http.returnToAutomatic(reviewCaseId, destination), retry: false, onSuccess: async () => { setStatus('Automatic policy resumes after fresh checks.'); await settle() }, onError: async () => { setStatus('Manual hold is unchanged. Try again.'); await settle() } })
  const selected = data.fields.some((field) => field.destination === search.field) ? search.field : data.fields[0]?.destination
  useRouteReadyFocus(`${search.subview}:${selected ?? ''}`)
  useEffect(() => { currentSearch.current = search }, [search])
  useEffect(() => { currentLocation.current = location }, [location])
  useEffect(() => () => { mounted.current = false }, [])
  useEffect(() => { const identity = `${reviewCaseId}:${selected ?? ''}:${search.subview}`; if (canonicalized.current === identity) return; canonicalized.current = identity; void navigate({ replace: true, search: { subview: search.subview, ...(selected ? { field: selected } : {}) } }) }, [navigate, reviewCaseId, search.subview, selected])
  const pages = evidence.data?.pages.flatMap((page) => page.items) ?? []
  const submit = (field: CaseDetailView['fields'][number], outcome: DecisionOutcome, selectedValue: DecisionCommand['selectedValue'] = null, reuse = false) => {
    const command: DecisionCommand = reuse && pending ? pending : { destination: field.destination, outcome, proposalId: field.proposalId, selectedValue, targetFingerprint: field.targetFingerprint, idempotencyKey: requestKey() }
    if (!reuse) submittedFieldOrder.current = data.fields.map((item) => item.destination)
    setPending(command); setStatus(`Recording ${friendly(outcome)}…`); decision.mutate(command)
  }
  if (!data.current) return <section className="route-state" data-route-focus tabIndex={-1}><h1>Case superseded</h1><p role="alert">Failed or superseded cases are never treated as current.</p><Link to="/library" search={{ lens: 'needs_attention' }}>Return to library</Link></section>
  return <>
    <header className="page-heading"><div><p className="eyebrow">Exact-current review</p><h1>Current review case</h1><p><strong>{data.title}</strong> · {data.authors.length ? data.authors.join(', ') : 'Author details unavailable'} · {data.subtitle}</p></div><span className="badge">Current</span></header>
    <nav className="subviews" aria-label="Case view"><button aria-current={search.subview === 'review'} onClick={() => void navigate({ search: { ...search, subview: 'review' } })}>Review</button><button aria-current={search.subview === 'evidence'} onClick={() => void navigate({ search: { ...search, subview: 'evidence' } })}>Evidence</button><button aria-current={search.subview === 'history'} onClick={() => void navigate({ search: { ...search, subview: 'history' } })}>History</button></nav>
    {search.subview === 'review' ? <div className="detail-layout">
      <nav className="field-navigation" aria-label="Review fields">{data.fields.map((field) => <button key={field.destination} aria-current={selected === field.destination} onClick={() => void navigate({ search: { ...search, field: field.destination } })}>{friendly(field.destination)}</button>)}</nav>
      <section className="field-list" aria-label="Review fields">{data.fields.map((field) => {
        const hasDecision = field.humanDecision !== null || field.automaticDecision !== null
        const choicesVisible = actionable(field) && !reloadRequired
        const availableChoiceCount = choicesVisible ? field.proposedValues.length + 1 : 0
        return <article id={`field-${field.destination}`} key={field.destination} hidden={selected !== field.destination} tabIndex={-1} data-route-focus={selected === field.destination ? '' : undefined}>
          <div className="detail-field-heading"><div><p className="eyebrow">{choicesVisible ? 'Your decision' : 'Field status'}</p><h2>{friendly(field.destination)}</h2></div><div className="reviewer-facts" aria-label="Reviewer facts"><span><small>Deterministic disposition</small><strong>{readable(field.disposition)}</strong></span><span><small>Available choices</small><strong>{String(availableChoiceCount)}</strong></span></div></div>
          {choicesVisible ? <section className="direct-choice" aria-label={`Choose a ${friendly(field.destination)} value`}><p className="choice-intro">Choose the value that should remain bound to this field. Your selection records this field only.</p><div className="direct-choice-grid"><button aria-label={`Choose current value: ${field.currentValue}`} className="value-choice current-value" disabled={decision.isPending} onClick={() => submit(field, 'keep_current')}><small>Current value</small><strong>{field.currentValue}</strong><span>Retain this exact value</span></button>{field.proposedValues.map((value, index) => <button aria-label={`Choose proposed value ${String(index + 1)}: ${displayValue(value.value)}`} className="value-choice proposed-value" key={`${value.kind}:${String(index)}`} disabled={decision.isPending} onClick={() => submit(field, 'accept', value)}><small>Proposed value {field.proposedValues.length > 1 ? String(index + 1) : ''}</small><strong>{displayValue(value.value)}</strong><span>Use this exact value</span></button>)}</div></section> : <div className="comparison"><div><small>Current value</small><p>{field.currentValue}</p></div><div><small>Decision state</small><p>{hasDecision ? 'A decision is already recorded.' : field.proposedValues.length ? `${String(field.proposedValues.length)} eligible proposed value${field.proposedValues.length === 1 ? '' : 's'}.` : 'No eligible proposed values.'}</p></div></div>}
          {field.notices.length || field.provenance.length ? <section className="field-notices" aria-label="Reviewer notes">{field.notices.map((notice) => <p key={notice}>{readable(notice)}</p>)}{field.provenance.map((source, index) => <p key={source}><small>Reviewed source {String(index + 1)}</small> {reviewedSource(source, index)}</p>)}</section> : null}
          {field.humanDecision ? <p className="decision-recorded">Human decision: {friendly(field.humanDecision.outcome)} by {field.humanDecision.actor}. Recorded value: {recordedValue(field.humanDecision.selected_value, field.currentValue)}.</p> : field.automaticDecision ? <p className="decision-recorded">Handled automatically: {friendly(field.automaticDecision.outcome)} · policy origin.</p> : null}
          {field.manualHold ? <button disabled={returnAutomatic.isPending} onClick={() => returnAutomatic.mutate(field.destination)}>Return to automatic handling</button> : null}
          {decision.isError && choicesVisible && pending?.destination === field.destination ? <button onClick={() => submit(field, pending.outcome, pending.selectedValue, true)}>Try again</button> : null}
        </article>
      })}</section>
    </div> : null}
    {search.subview === 'evidence' ? <section className="progressive-panel" data-route-focus tabIndex={-1}><h2>Evidence and source ledger</h2>{evidence.isPending ? <p role="status">Loading evidence…</p> : evidence.isError ? <><p role="alert">Evidence could not be loaded.</p><button onClick={() => void evidence.refetch()}>Try again</button></> : <>{pages.map((item) => <article className="evidence-item" key={item.reference}><h3>{friendly(item.kind)}</h3><pre>{item.fact}</pre></article>)}{evidence.hasNextPage ? <button disabled={evidence.isFetchingNextPage} onClick={() => void evidence.fetchNextPage()}>Load more evidence</button> : <p>All referenced evidence is shown.</p>}</>}</section> : null}
    {search.subview === 'history' ? <section className="progressive-panel" data-route-focus tabIndex={-1}><h2>Current-case history</h2>{history.isPending ? <p role="status">Loading history…</p> : history.isError ? <><p role="alert">History could not be loaded.</p><button onClick={() => void history.refetch()}>Try again</button></> : history.data.items.map((item, index) => <p key={index}>{friendly(item.kind)} · {'destination' in item ? item.destination : ''}</p>)}</section> : null}
    <p role={decision.isError || returnAutomatic.isError ? 'alert' : 'status'}>{status}</p>
  </>
}
