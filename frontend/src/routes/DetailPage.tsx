import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useNavigate, useRouterState } from '@tanstack/react-router'
import { useEffect, useRef, useState } from 'react'

import { displayValue } from '../api/http'
import type { CaseDetailView, DecisionCommand, DecisionOutcome, EvidenceView, HistoryView } from '../api/http'
import { useRouteReadyFocus } from '../focus'
import { detailRoute, useReviewContext } from '../router'
import { useReviewDecisionController } from '../reviewDecisionController'
import { automaticChoiceSelected } from '../reviewAutomaticSelection'

const friendly = (value: string) => value.replaceAll('_', ' ')
const readable = (value: string) => `${friendly(value).slice(0, 1).toUpperCase()}${friendly(value).slice(1)}`
const opaqueSource = /^(?:local|evidence|provider)_[0-9a-f]+$/iu
const reviewedSource = (value: string, index: number) => opaqueSource.test(value) ? `Evidence record ${String(index + 1)}` : readable(value)
const requestKey = () => typeof crypto.randomUUID === 'function' ? crypto.randomUUID() : `decision-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`
const recordedValue = (value: unknown, current: string) => value === null ? current : typeof value === 'object' && 'value' in value ? displayValue(value.value) : displayValue(value)
const automaticSelection = (field: CaseDetailView['fields'][number], outcome: DecisionOutcome, value: DecisionCommand['selectedValue'] = null) => automaticChoiceSelected(field.automaticDecision, outcome, value)

function HistoryItem({ item, index }: { item: HistoryView['items'][number]; index: number }) {
  if (!("consideration" in item)) return <p key={index}>{friendly(item.kind)} · {'destination' in item ? item.destination : ''}</p>
  const policy = item.consideration.policy
  const recommendation = policy?.recommended_candidate_reference ?? null
  return <article className="history-cover" key={index}>
    <h3>Cover {friendly(item.kind)}</h3>
    <p>Consideration {item.consideration_id}. Observer status: {friendly(item.consideration.reviewer_status)}. {item.consideration.current ? 'Current.' : 'Historical; it grants no current authority.'}</p>
    <p>Owner outcome: {item.decision ? friendly(item.decision.outcome) : 'No owner outcome on this event'}{item.decision?.selected_value ? ` · ${displayValue(item.decision.selected_value.value)}` : ''}.</p>
    <p>Policy {item.consideration.policy_reference}: {policy ? friendly(policy.status) : 'observation unavailable'}; recommendation: {recommendation ?? 'none'}.</p>
    {policy?.reasons.length ? <p>Policy reasons: {policy.reasons.map(readable).join(' · ')}</p> : null}
    <ul aria-label="Historical cover candidates">{item.consideration.candidates.map((candidate) => <li key={candidate.reference}><strong>{candidate.reference}</strong> · {candidate.incumbent ? 'incumbent' : 'alternative'} · identity {friendly(candidate.identity.outcome)} · {candidate.observation ? `composition ${friendly(candidate.observation.composition)}, medium ${friendly(candidate.observation.medium)}, promotional text ${friendly(candidate.observation.promotional_text)}, tie-in branding ${friendly(candidate.observation.tie_in_branding)}` : 'visual observation unavailable'}</li>)}</ul>
  </article>
}

function CoverChoices({ reviewCaseId, field, disabled, submit }: { reviewCaseId: string; field: CaseDetailView['fields'][number]; disabled: boolean; submit: (outcome: DecisionOutcome, selectedValue?: DecisionCommand['selectedValue']) => void }) {
  const { http } = useReviewContext()
  const queryClient = useQueryClient()
  const consideration = field.coverConsideration
  const coverSelected = (outcome: DecisionOutcome, value: DecisionCommand['selectedValue'] = null) => automaticChoiceSelected(field.automaticDecision, outcome, value)
  const [failed, setFailed] = useState<ReadonlySet<string>>(new Set())
  const retry = useMutation({ mutationFn: () => http.retryCoverConsideration(reviewCaseId), retry: false, onSuccess: async () => { await queryClient.invalidateQueries({ queryKey: ['review-case', reviewCaseId] }) } })
  if (!consideration) return <section aria-label="Prepare cover comparison"><p role="status">No current cover consideration is available. The existing cover remains unchanged.</p><button disabled={disabled || retry.isPending} onClick={() => retry.mutate()}>Prepare cover comparison</button>{retry.isError ? <p role="alert">The comparison could not be prepared. The existing cover remains unchanged.</p> : null}</section>
  const reasons = consideration.policy && Array.isArray(consideration.policy.reasons) ? consideration.policy.reasons.filter((value): value is string => typeof value === 'string') : []
  const preferences = [consideration.policyPreferences.avoidPromotionalText ? 'Avoid promotional text' : null, consideration.policyPreferences.avoidTieInBranding ? 'Avoid tie-in branding' : null, consideration.policyPreferences.preferMinimalComposition ? 'Prefer minimal composition' : null].filter((value): value is string => value !== null)
  return <section className="cover-consideration" aria-label="Choose a cover">
    <div className="cover-advice"><p><strong>{consideration.reviewerStatus === 'completed' ? 'Advisory visual review complete' : 'Manual comparison available'}</strong></p><p>{consideration.reviewerStatus === 'completed' ? 'The recommendation is deterministic advice, not ground truth or write authority.' : 'Observation or recommendation is unavailable. You can still compare and choose identity-admitted covers manually.'}</p><p>Composition is the least reliable experimental field; its reference labels are reviewer judgements.</p><p><small>Policy preferences</small> {preferences.length ? preferences.join(' · ') : 'No optional visual preferences enabled'}</p>{reasons.map((reason) => <small key={reason}>{readable(reason)}</small>)}</div>
    {consideration.incumbent.state === 'unavailable' ? <article className="cover-card cover-unavailable"><div className="cover-image-frame"><p role="img" aria-label="Current cover unavailable">Current cover image unavailable</p></div><div><p className="eyebrow">Current cover</p><h3>Unavailable for comparison</h3><p>The current library cover could not be read safely. Keeping it remains a no-write choice.</p><button aria-pressed={coverSelected('keep_current')} className={coverSelected('keep_current') ? 'is-selected' : undefined} disabled={disabled} onClick={() => submit('keep_current')}>Keep current cover</button></div></article> : null}
    <div className="cover-grid">{consideration.candidates.map((candidate, index) => {
      const src = `/api/v1/review-cases/${encodeURIComponent(reviewCaseId)}/cover-considerations/${encodeURIComponent(consideration.id)}/images/${encodeURIComponent(candidate.reference)}`
      const observation = candidate.observation
      const alternativeNumber = consideration.candidates.slice(0, index + 1).filter((value) => !value.incumbent).length
      return <article className={`cover-card cover-${candidate.state}`} key={candidate.reference}>
        <div className="cover-image-frame">{failed.has(candidate.reference) ? <p role="img" aria-label={`${candidate.incumbent ? 'Current' : 'Alternative'} cover unavailable`}>Image unavailable</p> : <img src={src} alt={`${candidate.incumbent ? 'Current library' : `Alternative ${String(alternativeNumber)}`} cover`} onError={() => setFailed((values) => new Set(values).add(candidate.reference))} />}</div>
        <div><p className="eyebrow">{candidate.incumbent ? 'Current cover' : `Alternative ${String(alternativeNumber)}`}</p><h3>{readable(candidate.state)}</h3><p>{candidate.width} × {candidate.height} · {candidate.sourceKinds.map(readable).join(', ')}</p>{observation ? <p>{['composition', 'medium', 'promotional_text', 'tie_in_branding'].map((name) => { const value = observation[name]; return `${readable(name)}: ${readable(typeof value === 'string' ? value : 'unknown')}` }).join(' · ')}</p> : <p>Visual observation unavailable</p>}{candidate.incumbent ? <button aria-pressed={coverSelected('keep_current')} className={coverSelected('keep_current') ? 'is-selected' : undefined} disabled={disabled} onClick={() => submit('keep_current')}>Keep current cover</button> : <button aria-pressed={coverSelected('accept', { kind: 'cover', value: candidate.artifactId })} className={coverSelected('accept', { kind: 'cover', value: candidate.artifactId }) ? 'is-selected' : undefined} disabled={disabled || !candidate.selectable} onClick={() => submit('accept', { kind: 'cover', value: candidate.artifactId })}>{candidate.selectable ? 'Choose this cover' : 'Unavailable for selection'}</button>}</div>
      </article>
    })}</div>
    <button aria-pressed={coverSelected('reject')} className={coverSelected('reject') ? 'secondary-action is-selected' : 'secondary-action'} disabled={disabled} onClick={() => submit('reject')}>Take no action</button>
    {consideration.reviewerStatus === 'failed' ? <button className="secondary-action" disabled={disabled || retry.isPending} onClick={() => retry.mutate()}>Retry advisory review</button> : null}
    {retry.isError ? <p role="alert">The advisory retry failed safely. The existing cover and manual choices are unchanged.</p> : null}
    {consideration.omissions.length ? <p className="cover-omissions">{String(consideration.omissions.length)} unsafe or unavailable source{consideration.omissions.length === 1 ? '' : 's'} retained in history.</p> : null}
  </section>
}

export default function DetailPage({ data }: { data: CaseDetailView }) {
  const { http } = useReviewContext()
  const search = detailRoute.useSearch()
  const { reviewCaseId } = detailRoute.useParams()
  const location = useRouterState({ select: (state) => state.location })
  const navigate = useNavigate({ from: '/review/$reviewCaseId' })
  const canonicalized = useRef('')
  const currentSearch = useRef(search)
  const currentLocation = useRef(location)
  const retryButton = useRef<HTMLButtonElement>(null)
  const evidence = useInfiniteQuery({ queryKey: ['review-case', reviewCaseId, 'evidence'], initialPageParam: undefined as string | undefined, queryFn: ({ pageParam, signal }) => http.getEvidence(reviewCaseId, pageParam, signal), getNextPageParam: (page: EvidenceView) => page.next_cursor ?? undefined, staleTime: 30_000, retry: false, enabled: search.subview === 'evidence' })
  const history = useQuery({ queryKey: ['review-case', reviewCaseId, 'history'], queryFn: ({ signal }) => http.getHistory(reviewCaseId, signal), staleTime: 30_000, retry: false })
  const controller = useReviewDecisionController({ reviewCaseId, fields: data.fields, active: (command) => currentLocation.current.pathname === `/review/${reviewCaseId}` && currentSearch.current.subview === 'review' && currentSearch.current.field === command.destination, onAdvance: async (field) => { await navigate({ search: { ...currentSearch.current, field, subview: 'review' } }) }, onStale: async () => { await navigate({ to: '/library', search: { lens: 'needs_attention' } }) } })
  const returnAutomatic = useMutation({ mutationFn: (destination: string) => http.returnToAutomatic(reviewCaseId, destination), retry: false, onSuccess: async () => { controller.announce('Automatic policy resumes after fresh checks.'); await controller.settle() }, onError: async () => { controller.announce('Manual hold is unchanged. Try again.'); await controller.settle() } })
  const selected = data.fields.some((field) => field.destination === search.field) ? search.field : data.fields[0]?.destination
  useRouteReadyFocus(`${search.subview}:${selected ?? ''}`)
  useEffect(() => { currentSearch.current = search }, [search])
  useEffect(() => { currentLocation.current = location }, [location])
  useEffect(() => { if (controller.canRetry) retryButton.current?.focus() }, [controller.canRetry])
  useEffect(() => { const identity = `${reviewCaseId}:${selected ?? ''}:${search.subview}`; if (canonicalized.current === identity) return; canonicalized.current = identity; void navigate({ replace: true, search: { subview: search.subview, ...(selected ? { field: selected } : {}) } }) }, [navigate, reviewCaseId, search.subview, selected])
  const pages = evidence.data?.pages.flatMap((page) => page.items) ?? []
  const submit = (field: CaseDetailView['fields'][number], outcome: DecisionOutcome, selectedValue: DecisionCommand['selectedValue'] = null) => controller.submit({ destination: field.destination, outcome, proposalId: field.proposalId, selectedValue, targetFingerprint: field.targetFingerprint, idempotencyKey: requestKey(), ...(field.coverConsideration ? { considerationId: field.coverConsideration.id } : {}) })
  if (!data.current) return <section className="route-state" data-route-focus tabIndex={-1}><h1>Case superseded</h1><p role="alert">Failed or superseded cases are never treated as current.</p><Link to="/library" search={{ lens: 'needs_attention' }}>Return to library</Link></section>
  return <>
    <header className="page-heading"><div><p className="eyebrow">Exact-current review</p><h1>Current review case</h1><p><strong>{data.title}</strong> · {data.authors.length ? data.authors.join(', ') : 'Author details unavailable'} · {data.subtitle}</p></div><span className="badge">Current</span></header>
    <nav className="subviews" aria-label="Case view"><button aria-current={search.subview === 'review'} onClick={() => void navigate({ search: { ...search, subview: 'review' } })}>Review</button><button aria-current={search.subview === 'evidence'} onClick={() => void navigate({ search: { ...search, subview: 'evidence' } })}>Evidence</button><button aria-current={search.subview === 'history'} onClick={() => void navigate({ search: { ...search, subview: 'history' } })}>History</button></nav>
    {search.subview === 'review' ? <div className="detail-layout">
      <nav className="field-navigation" aria-label="Review fields">{data.fields.map((field) => <button key={field.destination} aria-current={selected === field.destination} onClick={() => void navigate({ search: { ...search, field: field.destination } })}>{friendly(field.destination)}</button>)}</nav>
      <section className="field-list" aria-label="Review fields">{data.fields.map((field) => {
        const choicesVisible = field.editable && field.humanDecision === null && !controller.reloadRequired
        const availableChoiceCount = choicesVisible ? field.proposedValues.length + 1 : 0
        return <article id={`field-${field.destination}`} key={field.destination} hidden={selected !== field.destination} tabIndex={-1} data-route-focus={selected === field.destination ? '' : undefined}>
          <div className="detail-field-heading"><div><p className="eyebrow">{choicesVisible ? 'Your decision' : 'Field status'}</p><h2>{friendly(field.destination)}</h2></div><div className="reviewer-facts" aria-label="Reviewer facts"><span><small>Deterministic disposition</small><strong>{readable(field.disposition)}</strong></span><span><small>Available choices</small><strong>{String(availableChoiceCount)}</strong></span></div></div>
          {choicesVisible ? field.destination === 'cover' ? <CoverChoices reviewCaseId={reviewCaseId} field={field} disabled={controller.isPending} submit={(outcome, value) => submit(field, outcome, value)} /> : <section className="direct-choice" aria-label={`Choose a ${friendly(field.destination)} value`}><p className="choice-intro">{field.classificationReason}</p><div className={`direct-choice-grid${field.preservationOnly ? ' preservation-only' : ''}`}><button aria-label={`Choose current value: ${field.currentValue}`} aria-pressed={automaticSelection(field, 'keep_current')} className={`value-choice current-value${automaticSelection(field, 'keep_current') ? ' is-selected' : ''}`} disabled={controller.isPending} onClick={() => submit(field, 'keep_current')}><small>Current value</small><strong>{field.currentValue}</strong><span>Retain this exact value</span></button>{field.preservationOnly ? null : field.proposedValues.map((value, index) => <button aria-label={`Choose proposed value ${String(index + 1)}: ${displayValue(value.value)}`} aria-pressed={automaticSelection(field, 'accept', value)} className={`value-choice proposed-value${automaticSelection(field, 'accept', value) ? ' is-selected' : ''}`} key={`${value.kind}:${String(index)}`} disabled={controller.isPending} onClick={() => submit(field, 'accept', value)}><small>Proposed value {field.proposedValues.length > 1 ? String(index + 1) : ''}</small><strong>{displayValue(value.value)}</strong><span>Use this exact value</span></button>)}</div><button aria-pressed={automaticSelection(field, 'reject')} className={automaticSelection(field, 'reject') ? 'secondary-action is-selected' : 'secondary-action'} disabled={controller.isPending} onClick={() => submit(field, 'reject')}>Take no action</button></section> : <div className="comparison"><div><small>Current value</small><p>{field.currentValue}</p></div><div><small>Decision state</small><p>{field.classificationReason}</p></div></div>}
          {field.notices.length || field.provenance.length || field.corroborationSources.length ? <section className="field-notices" aria-label="Reviewer notes">{field.notices.map((notice) => <p key={notice}>{readable(notice)}</p>)}{field.corroborationSources.length ? <p><small>Corroborated creator sources</small> {field.corroborationSources.join(' · ')}</p> : null}{field.provenance.map((source, index) => <p key={source}><small>Reviewed source {String(index + 1)}</small> {reviewedSource(source, index)}</p>)}</section> : null}
          {field.humanDecision ? <p className="decision-recorded">Human decision: {friendly(field.humanDecision.outcome)} by {field.humanDecision.actor}. Recorded value: {recordedValue(field.humanDecision.selected_value, field.currentValue)}.</p> : field.automaticDecision ? <p className="decision-recorded">Handled automatically: {friendly(field.automaticDecision.outcome)} · policy origin.</p> : null}
          {field.manualHold ? <button disabled={returnAutomatic.isPending} onClick={() => returnAutomatic.mutate(field.destination)}>Return to automatic handling</button> : null}
          {controller.canRetry && choicesVisible && controller.pending?.destination === field.destination ? <button ref={retryButton} onClick={controller.retry}>Try again</button> : null}
        </article>
      })}</section>
    </div> : null}
    {search.subview === 'evidence' ? <section className="progressive-panel" data-route-focus tabIndex={-1}><h2>Evidence and source ledger</h2>{evidence.isPending ? <p role="status">Loading evidence…</p> : evidence.isError ? <><p role="alert">Evidence could not be loaded.</p><button onClick={() => void evidence.refetch()}>Try again</button></> : <>{pages.map((item) => <article className="evidence-item" key={item.reference}><h3>{friendly(item.kind)}</h3><pre>{item.fact}</pre></article>)}{evidence.hasNextPage ? <button disabled={evidence.isFetchingNextPage} onClick={() => void evidence.fetchNextPage()}>Load more evidence</button> : <p>All referenced evidence is shown.</p>}</>}</section> : null}
    {search.subview === 'history' ? <section className="progressive-panel" data-route-focus tabIndex={-1}><h2>Current-case history</h2>{history.isPending ? <p role="status">Loading history…</p> : history.isError ? <><p role="alert">History could not be loaded.</p><button onClick={() => void history.refetch()}>Try again</button></> : history.data.items.map((item, index) => <HistoryItem item={item} index={index} key={index} />)}</section> : null}
    <p role={controller.decision.isError || returnAutomatic.isError || controller.reloadRequired ? 'alert' : 'status'}>{controller.status || controller.message}</p>
  </>
}
