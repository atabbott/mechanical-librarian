import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useNavigate } from '@tanstack/react-router'
import { useEffect, useRef, useState } from 'react'
import type { SyntheticEvent } from 'react'

import { displayValue } from '../api/http'
import type { CaseDetailView, DecisionCommand, EvidenceView, LibraryView, LibraryWorkView } from '../api/http'
import { useRouteReadyFocus } from '../focus'
import { detailQuery, libraryRoute, useReviewContext } from '../router'
import { actionable, useReviewDecisionController } from '../reviewDecisionController'
import { automaticChoiceSelected } from '../reviewAutomaticSelection'

type ReviewField = CaseDetailView['fields'][number]
type EvidenceItem = EvidenceView['items'][number]
interface EvidenceRow { basis: string; observed: string; source: string }

const friendly = (value: string) => value.replaceAll('_', ' ').replace(/^./, (letter) => letter.toUpperCase())
const authorLine = (work: LibraryWorkView) => work.authors.length ? work.authors.join(', ') : 'Author details unavailable'
const providerOutcomeSummary = (work: LibraryWorkView) => work.preparation
  ? 'Preparation needs owner recovery'
  : work.preparationOutcomes.length
    ? `${String(work.preparationOutcomes.length)} provider outcome${work.preparationOutcomes.length === 1 ? '' : 's'} retained`
    : undefined
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
const completionOutcome = (field: ReviewField) => field.humanDecision?.outcome === 'reject'
  ? 'No action recorded'
  : field.humanDecision?.outcome === 'keep_current'
    ? 'Current value retained'
    : field.humanDecision?.outcome === 'accept'
      ? 'Proposed value selected'
      : 'Recorded outcome'
const sameSelectedValue = (left: DecisionCommand['selectedValue'], right: DecisionCommand['selectedValue']) => left == null || right == null ? left === right : left.kind === right.kind && JSON.stringify(left.value) === JSON.stringify(right.value)
const commandSelected = (command: DecisionCommand | null, destination: string, outcome: DecisionCommand['outcome'], selectedValue: DecisionCommand['selectedValue'] = null) => command?.destination === destination && command.outcome === outcome && sameSelectedValue(command.selectedValue, selectedValue)
const recordedSelected = (field: ReviewField, outcome: DecisionCommand['outcome'], selectedValue: DecisionCommand['selectedValue'] = null) => field.automaticDecision !== null && outcome !== 'reject' && automaticChoiceSelected(field.automaticDecision, outcome, selectedValue)
const choiceSelected = (field: ReviewField, pending: DecisionCommand | null, outcome: DecisionCommand['outcome'], selectedValue: DecisionCommand['selectedValue'] = null) => commandSelected(pending, field.destination, outcome, selectedValue) || recordedSelected(field, outcome, selectedValue)
const ownerChoiceCount = (field: ReviewField) => {
  const consideration = field.coverConsideration
  if (field.destination !== 'cover') return field.proposedValues.length + 1
  if (!consideration) return 0
  const candidates = consideration.candidates.filter((candidate) => candidate.selectable || candidate.incumbent)
  const unavailableIncumbent = consideration.incumbent.state === 'unavailable' && !candidates.some((candidate) => candidate.incumbent)
  return candidates.length + (unavailableIncumbent ? 1 : 0) + 1
}

function LibraryCoverChoices({ reviewCaseId, field, disabled, pending, phase, choose }: { reviewCaseId: string; field: ReviewField; disabled: boolean; pending: DecisionCommand | null; phase: 'recording' | 'confirmed' | 'failed' | null; choose: (outcome: DecisionCommand['outcome'], selectedValue?: DecisionCommand['selectedValue']) => void }) {
  const { http } = useReviewContext()
  const queryClient = useQueryClient()
  const consideration = field.coverConsideration
  const [failed, setFailed] = useState<ReadonlySet<string>>(new Set())
  const prepare = useMutation({ mutationFn: () => http.retryCoverConsideration(reviewCaseId), retry: false, onSuccess: async () => { await queryClient.invalidateQueries({ queryKey: ['review-case', reviewCaseId] }) } })
  if (!consideration) return <section aria-label="Prepare cover comparison"><p role="status">No current cover comparison is available. The existing cover remains unchanged.</p><button type="button" disabled={disabled || prepare.isPending} onClick={() => prepare.mutate()}>Prepare cover comparison</button>{prepare.isError ? <p role="alert">The comparison could not be prepared. The existing cover remains unchanged.</p> : null}</section>
  const candidates = consideration.candidates.filter((candidate) => candidate.selectable || candidate.incumbent)
  const excluded = consideration.candidates.filter((candidate) => !candidate.selectable && !candidate.incumbent)
  const expanded = candidates.length > 3
  const incumbent = candidates.find((candidate) => candidate.incumbent)
  const alternatives = candidates.filter((candidate) => !candidate.incumbent)
  const recommended = typeof consideration.policy?.recommended_candidate_reference === 'string' ? consideration.policy.recommended_candidate_reference : null
  const acceptableReferences = Array.isArray(consideration.policy?.acceptable_candidate_references) && consideration.policy.acceptable_candidate_references.every((reference): reference is string => typeof reference === 'string') ? consideration.policy.acceptable_candidate_references : []
  const policyStatus = typeof consideration.policy?.status === 'string' ? consideration.policy.status : null
  const policyReasons = Array.isArray(consideration.policy?.reasons) ? consideration.policy.reasons.filter((reason): reason is string => typeof reason === 'string').slice(0, 2) : []
  const excludedReason = (candidate: typeof candidates[number]) => consideration.policyPreferences.avoidPromotionalText && candidate.observation?.promotional_text === 'present'
    ? 'Promotional text excluded by policy'
    : consideration.policyPreferences.avoidTieInBranding && candidate.observation?.tie_in_branding === 'present'
      ? 'Tie in branding excluded by policy'
      : typeof candidate.identity.outcome === 'string' && !['plausible_match', 'verified_work_match', 'verified_manifestation_match'].includes(candidate.identity.outcome)
    ? `${friendly(candidate.identity.outcome)}${Array.isArray(candidate.identity.reasons) ? ` · ${candidate.identity.reasons.filter((reason): reason is string => typeof reason === 'string').slice(0, 2).map(friendly).join(', ')}` : ''}`
    : candidate.observation === null
      ? 'Visual observation unavailable'
      : policyStatus !== null && !acceptableReferences.includes(candidate.reference)
        ? policyReasons.length ? policyReasons.map(friendly).join(', ') : friendly(policyStatus)
        : friendly(candidate.state)
  const card = (candidate: typeof candidates[number], index: number) => {
    const alternative = candidates.slice(0, index + 1).filter((item) => !item.incumbent).length
    const source = `/api/v1/review-cases/${encodeURIComponent(reviewCaseId)}/cover-considerations/${encodeURIComponent(consideration.id)}/images/${encodeURIComponent(candidate.reference)}`
    const outcome = candidate.incumbent ? 'keep_current' : 'accept'
    const value: DecisionCommand['selectedValue'] = candidate.incumbent ? null : { kind: 'cover', value: candidate.artifactId }
    const selected = choiceSelected(field, pending, outcome, value)
    return <button type="button" aria-pressed={selected} className={`library-cover-card${candidate.incumbent ? ' is-current' : ''}${candidate.reference === recommended ? ' is-recommended' : ''}${selected ? ' is-selected' : ''}`} key={candidate.reference} disabled={disabled} onClick={() => choose(outcome, value)}>
      <span className="library-cover-image">{failed.has(candidate.reference) ? <span role="img" aria-label="Cover unavailable">Image unavailable</span> : <img src={source} alt={`${candidate.incumbent ? 'Current library' : `Alternative ${String(alternative)}`} cover`} onError={() => setFailed((items) => new Set(items).add(candidate.reference))} />}</span>
      <span><small>{candidate.incumbent ? 'Current cover' : `Alternative ${String(alternative)}`}{candidate.reference === recommended ? ' · Recommended' : ''}</small><strong>{friendly(candidate.state)}</strong><em>{candidate.width} × {candidate.height} · {candidate.sourceKinds.map(friendly).join(', ')}</em>{selected && phase === 'recording' ? <b>Recording…</b> : null}</span>
    </button>
  }
  return <section className={`library-cover-comparison${expanded ? ' is-expanded' : ''}`} aria-label="Choose a cover">
    <header><strong>{consideration.reviewerStatus === 'completed' ? 'Advisory visual review complete' : 'Manual cover comparison'}</strong><small>{recommended ? 'One candidate is recommended as advice only.' : 'Recommendation is unavailable; comparison remains manual.'}</small></header>
    {expanded && incumbent ? <div className="library-cover-pinned"><aside><strong>Current reference</strong>{card(incumbent, candidates.indexOf(incumbent))}</aside><div className="library-cover-gallery"><header><strong>Eligible alternatives</strong><small>{String(alternatives.length)} available</small></header><div>{alternatives.map((candidate) => card(candidate, candidates.indexOf(candidate)))}</div></div></div> : <div className="library-cover-layout">{candidates.map(card)}</div>}
    {consideration.incumbent.state === 'unavailable' && !incumbent ? <button type="button" aria-pressed={choiceSelected(field, pending, 'keep_current')} className={`library-cover-card is-current${choiceSelected(field, pending, 'keep_current') ? ' is-selected' : ''}`} disabled={disabled} onClick={() => choose('keep_current')}><span className="library-cover-image" role="img" aria-label="Current cover unavailable">Image unavailable</span><span><small>Current cover</small><strong>Unavailable</strong><em>Keep the current bound cover without a preview.</em>{commandSelected(pending, field.destination, 'keep_current') && phase === 'recording' ? <b>Recording…</b> : null}</span></button> : null}
    {excluded.length || consideration.omissions.length ? <details className="library-cover-excluded"><summary>{String(excluded.length + consideration.omissions.length)} excluded or unavailable candidate{excluded.length + consideration.omissions.length === 1 ? '' : 's'} <small>Reasons retained</small></summary>{excluded.map((candidate) => <p key={candidate.reference}>{excludedReason(candidate)}</p>)}{consideration.omissions.map((omission, index) => <p key={String(index)}>{typeof omission.reason === 'string' ? friendly(omission.reason) : 'Unavailable source retained in history'}</p>)}</details> : null}
    <button className={`library-no-action${commandSelected(pending, field.destination, 'reject') ? ' is-selected' : ''}`} type="button" aria-pressed={commandSelected(pending, field.destination, 'reject')} disabled={disabled} onClick={() => choose('reject')}>Take no action{commandSelected(pending, field.destination, 'reject') && phase === 'recording' ? ' · Recording…' : ''}</button>
  </section>
}

export default function LibraryPage({ data }: { data: LibraryView }) {
  const search = libraryRoute.useSearch()
  const navigate = useNavigate({ from: '/library' })
  const { http } = useReviewContext()
  const currentSearch = useRef(search)
  const retryButton = useRef<HTMLButtonElement>(null)
  const completionRegion = useRef<HTMLElement>(null)
  const confirmationRegion = useRef<HTMLElement>(null)
  const activeFieldButton = useRef<HTMLButtonElement>(null)
  const attentionRail = useRef<HTMLElement>(null)
  const selectedIdentityRef = useRef<{ id: string; title: string; authors: readonly string[]; library: string; mediaKind: string } | null>(null)
  const [frozenFieldOrders, setFrozenFieldOrders] = useState<Readonly<Record<string, readonly string[]>>>({})
  const [complete, setComplete] = useState<{ id: string; fields: readonly ReviewField[]; priorAttention: ReadonlySet<string>; identity: { title: string; authors: readonly string[]; library: string; mediaKind: string } } | null>(null)
  const [completionInspection, setCompletionInspection] = useState<string | null>(null)
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
  useEffect(() => { if (selectedIdentity && search.preview) selectedIdentityRef.current = { id: search.preview, title: selectedIdentity.title, authors: selectedIdentity.authors, library: selectedIdentity.library, mediaKind: selectedIdentity.mediaKind } }, [search.preview, selectedIdentity])
  const completionIdentity = complete?.id === selectedReviewCaseId ? complete.identity : undefined
  const displayedIdentity = selectedIdentity ?? completionIdentity
  const selected = detail.data?.fields.find((field) => field.destination === search.field)
    ?? detail.data?.fields.find(actionable)
    ?? detail.data?.fields[0]
  const currentActionableDestinations = detail.data?.fields.map((field) => field.destination) ?? []
  const frozenDestinations = frozenFieldOrders[selectedReviewCaseId] ?? currentActionableDestinations
  const railFields = frozenDestinations
    .map((destination) => detail.data?.fields.find((field) => field.destination === destination))
    .filter((field): field is ReviewField => field !== undefined)
  const actionableFields = railFields.filter(actionable)
  const contextualFields = detail.data?.fields.filter((field) => !actionable(field)) ?? []
  const activeQueueIndex = selected
    ? attentionItems.findIndex((item) => item.id === search.preview && item.destination === selected.destination)
    : -1
  const fullReviewSearch = { subview: 'review' as const, ...(selected ? { field: selected.destination } : {}) }
  const nextWork = complete ? attentionItems.find((item) => item.id !== selectedReviewCaseId && !complete.priorAttention.has(`${item.id}:${item.destination}`)) ?? null : null
  const nextWorkError = attention.isError
  const controller = useReviewDecisionController({
    reviewCaseId: selectedReviewCaseId,
    fields: detail.data?.fields ?? [],
    active: (command) => currentSearch.current.preview === search.preview
      && search.preview !== undefined
      && (currentSearch.current.field === undefined || currentSearch.current.field === command.destination),
    onAdvance: async (field) => { await navigate({ search: (current) => current.preview === search.preview ? { ...current, field } : current }) },
    onComplete: (fields) => { const retained = selectedIdentityRef.current; if (retained?.id === selectedReviewCaseId) { setCompletionInspection(null); setComplete({ id: selectedReviewCaseId, fields: fields.filter((field) => field.humanDecision !== null), priorAttention: new Set((activeQueueIndex < 0 ? [] : attentionItems.slice(0, activeQueueIndex + 1)).map((item) => `${item.id}:${item.destination}`)), identity: retained }) } },
    onStale: async () => { await navigate({ search: { lens: 'needs_attention' } }) },
  })

  useRouteReadyFocus(`${search.lens}:${search.q ?? ''}:${search.cursor ?? ''}:${search.preview ?? ''}:${search.field ?? ''}:${search.attention ?? ''}`)
  useEffect(() => { currentSearch.current = search }, [search])
  useEffect(() => { if (controller.canRetry) retryButton.current?.focus() }, [controller.canRetry])
  useEffect(() => { if (complete?.id === selectedReviewCaseId && completionInspection === null) completionRegion.current?.focus({ preventScroll: true }) }, [complete, completionInspection, selectedReviewCaseId])
  useEffect(() => { if (controller.phase === 'confirmed') confirmationRegion.current?.focus({ preventScroll: true }) }, [controller.phase])
  useEffect(() => {
    const reducedMotion = typeof window.matchMedia === 'function' && window.matchMedia('(prefers-reduced-motion: reduce)').matches
    activeFieldButton.current?.scrollIntoView({ block: 'nearest', inline: 'center', behavior: reducedMotion ? 'auto' : 'smooth' })
  }, [selected?.destination])
  useEffect(() => {
    if (search.attention !== 'open') return
    let focusFrame = 0
    const routeFrame = requestAnimationFrame(() => { focusFrame = requestAnimationFrame(() => attentionRail.current?.focus({ preventScroll: true })) })
    return () => { cancelAnimationFrame(routeFrame); cancelAnimationFrame(focusFrame) }
  }, [search.attention])
  useEffect(() => { if (complete?.id === selectedReviewCaseId && !nextWork && attention.hasNextPage && !attention.isFetchingNextPage && !attention.isFetchNextPageError) void attention.fetchNextPage() }, [attention, complete, nextWork, selectedReviewCaseId])
  useEffect(() => {
    if (typeof window.matchMedia !== 'function') return
    const media = window.matchMedia('(min-width: 761px)')
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

  const select = (reviewCaseId: string, field?: string) => { if (complete?.id !== reviewCaseId) { setComplete(null); setCompletionInspection(null) } void navigate({ search: (current) => {
    const next = { ...current, preview: reviewCaseId }
    if (field) next.field = field
    else delete next.field
    return next
  } }) }
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
    controller.submit({ destination: field.destination, outcome, proposalId: field.proposalId, selectedValue, targetFingerprint: field.targetFingerprint, idempotencyKey: requestKey(), ...(field.coverConsideration ? { considerationId: field.coverConsideration.id } : {}) })
  }
  const retryNextWork = () => { if (attention.isFetchNextPageError) void attention.fetchNextPage(); else void attention.refetch() }
  const clearSelection = () => { setComplete(null); setCompletionInspection(null); void navigate({ search: (current) => {
    const next = { ...current }
    delete next.preview
    delete next.field
    delete next.attention
    return next
  } }) }
  const selectFromQueue = (reviewCaseId: string, field: string) => { setComplete(null); setCompletionInspection(null); void navigate({ search: (current) => {
    const next = { ...current, preview: reviewCaseId, field }
    delete next.attention
    return next
  } }) }

  const rows = selected ? evidenceRows(selected, evidenceItems) : []
  const sourceCount = new Set(rows.map((row) => row.source)).size
  const sourceSupport = evidence.isPending
    ? 'supporting sources loading'
    : sourceCount
      ? `${String(sourceCount)} named source${sourceCount === 1 ? '' : 's'} below`
      : 'no retained source records below'
  const effect = selected ? effectSummary(selected) : undefined
  const sourceLedgerRows = rows.length ? <div className="source-ledger-table" role="table" aria-label="Evidence ledger"><div className="source-ledger-row source-ledger-head" role="row"><span role="columnheader">Source</span><span role="columnheader">Observed value</span><span role="columnheader">Basis</span></div>{rows.map((row, index) => <div className="source-ledger-row" role="row" key={`${row.source}:${row.observed}:${String(index)}`}><span role="cell"><small className="source-cell-label">Source</small><strong>{row.source}</strong></span><span role="cell"><small className="source-cell-label">Observed value</small>{row.observed}</span><span role="cell"><small className="source-cell-label">Basis</small>{row.basis}</span></div>)}</div> : null

  return <div className={`library-page variant-m-workspace${search.preview ? ' has-selected-work' : ''}`}>
    <header className="library-header" tabIndex={-1} {...(search.preview ? {} : { 'data-route-focus': 'true' })}>
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
          const providerSummary = providerOutcomeSummary(work)
          return <article key={work.id} className={`library-work-row${selectedWorkRow ? ' active' : ''}`}>
            <span className="library-book-mark" aria-hidden="true">{initials(work.title)}</span>
            <div className="library-work-copy"><h3>{work.reviewCaseId ? <button className="work-select" type="button" aria-pressed={selectedWorkRow} onClick={() => select(work.reviewCaseId ?? '')}>{work.title}</button> : work.title}</h3><p>{authorLine(work)}</p><p>{work.library} · {friendly(work.mediaKind)}</p><div className="library-work-status"><span className={needsAttention || work.preparation ? 'needs-attention' : 'handled'}>{work.preparation ? 'Preparation failed safely' : needsAttention ? `${String(work.attentionCount)} field${work.attentionCount === 1 ? '' : 's'} need attention` : 'No attention needed'}</span></div>{providerSummary ? <details className="provider-outcomes"><summary>{providerSummary}</summary>{work.preparation ? <p><strong>{friendly(work.preparation.status)} at {friendly(work.preparation.stage)}</strong> · {work.preparation.reason} {work.preparation.recovery}</p> : null}<ul>{work.preparationOutcomes.map((outcome, index) => <li key={`${outcome.provider}:${outcome.laneKind}:${outcome.status}:${String(index)}`}><strong>{outcome.provider}</strong> · {friendly(outcome.laneKind)} · {friendly(outcome.status)} · {String(outcome.providerRequests)} bounded provider request{outcome.providerRequests === 1 ? '' : 's'}. {outcome.reason}</li>)}</ul></details> : null}</div>
          </article>
        })}</div>
        <div className="library-pager"><span>{data.nextCursor ? 'More current works are available' : 'End of the bounded current result'}</span>{data.nextCursor ? <Link to="/library" search={{ ...search, cursor: data.nextCursor }}>Next page →</Link> : null}</div>
      </section>
      <section className="workspace-detail" aria-live="polite" tabIndex={-1} {...(search.preview && search.attention !== 'open' && (!displayedIdentity || detail.isPending || detail.isError) ? { 'data-route-focus': 'true' } : {})}>
        {!search.preview ? <div className="library-selection"><h2>Choose a work</h2><p>Its current values, field position, and bounded reviewer context stay here while you move through attention.</p></div>
          : !displayedIdentity ? <div className="library-selection"><p>Progressive detail</p><h2>Selected work</h2><p>Readable work identity is not available on this Library page.</p><Link to="/review/$reviewCaseId" params={{ reviewCaseId: search.preview }} search={fullReviewSearch}>Open full review</Link></div>
            : detail.isPending ? <p role="status">Loading selected work…</p>
              : detail.isError ? <p role="alert">Selected work could not be loaded. <button onClick={() => void detail.refetch()}>Try again</button> <Link className="full-review-link" to="/review/$reviewCaseId" params={{ reviewCaseId: search.preview }} search={fullReviewSearch}>Open full review</Link></p>
                : !detail.data.current ? <div className="library-selection"><p>Case superseded</p><h2>{displayedIdentity.title}</h2><p role="alert">This review case is no longer current. Reopen its current replacement from Library.</p></div>
                  : <>
                    <header className="selected-work-header" tabIndex={-1} {...(search.preview && search.attention !== 'open' ? { 'data-route-focus': 'true' } : {})}><span className="library-book-mark" aria-hidden="true">{initials(displayedIdentity.title)}</span><div><p>Progressive detail</p><h2>{displayedIdentity.title}</h2><p>{displayedIdentity.authors.length ? displayedIdentity.authors.join(', ') : 'Author details unavailable'} · {displayedIdentity.library} · {friendly(displayedIdentity.mediaKind).toLowerCase()}</p></div><dl className="selected-work-status"><div><dt>Needs review</dt><dd>{String(actionableFields.length)}</dd></div><div><dt>Resolved automatically</dt><dd>{String(detail.data.fields.filter((field) => field.automaticDecision !== null).length)}</dd></div></dl></header>
                    <nav className="field-filmstrip" aria-label="Review fields">{railFields.map((field) => {
                      const position = frozenDestinations.indexOf(field.destination) + 1
                      return <button key={field.destination} ref={selected?.destination === field.destination ? activeFieldButton : undefined} aria-current={selected?.destination === field.destination} className={!actionable(field) ? 'is-recorded' : undefined} onClick={() => { if (complete?.id === selectedReviewCaseId) setCompletionInspection(field.destination); select(selectedReviewCaseId, field.destination) }}><small>{String(position)}</small><strong>{friendly(field.destination)}</strong><span aria-label={actionable(field) ? 'Needs review' : field.humanDecision ? 'Decision recorded' : field.classification === 'automatic_recorded' ? 'Handled automatically; inspect or override' : fieldState(field)}>{actionable(field) ? '•' : '✓'}</span></button>
                    })}</nav>
                    {complete?.id === selectedReviewCaseId && completionInspection === null ? <article className="review-complete" aria-live="polite" tabIndex={-1} ref={completionRegion}><span className="review-complete-mark">✓</span><div><p>Review complete</p><h2>{displayedIdentity.title}</h2><span>{String(complete.fields.length)} recorded field outcome{complete.fields.length === 1 ? '' : 's'}. No library data changed.</span></div><ol>{complete.fields.map((field) => <li key={field.destination}><button type="button" onClick={() => { setCompletionInspection(field.destination); select(selectedReviewCaseId, field.destination) }}><small>{friendly(field.destination)} · {completionOutcome(field)}</small><strong>{decisionValue(field)}</strong></button></li>)}</ol><p>Preparation, authorization, apply, and read-back verification remain separate guarded stages.</p><footer>{nextWork ? <button type="button" onClick={() => { setComplete(null); setCompletionInspection(null); select(nextWork.id, nextWork.destination) }}>Review next work →</button> : attention.isPending || attention.isFetchingNextPage ? <span>Looking for the next visible work…</span> : nextWorkError ? <><span>The next attention work could not be loaded.</span><button type="button" onClick={retryNextWork}>Try finding next work again</button></> : <span>All visible review work is complete.</span>}</footer></article> : selected ? <article className={`decision-canvas${controller.isPending ? ' is-recording' : ''}`}>
                      {complete?.id === selectedReviewCaseId ? <button type="button" className="decision-back" onClick={() => setCompletionInspection(null)}>← Back to completion</button> : null}
                      <header><div><p>Human attention</p><h2>{friendly(selected.destination)}</h2><p>{fieldReason(selected)}</p></div><dl><div><dt>Queue position</dt><dd>{activeQueueIndex >= 0 ? `${String(activeQueueIndex + 1)} of ${String(attentionItems.length)}` : 'Selected work'}</dd></div><div><dt>Choices</dt><dd>{actionable(selected) ? String(ownerChoiceCount(selected)) : '0'}</dd></div></dl></header>
                      {controller.phase === 'confirmed' && controller.pending?.destination === selected.destination ? <section className="decision-confirmed" role="status" tabIndex={-1} ref={confirmationRegion}><span aria-hidden="true">✓</span><div><strong>Decision recorded</strong><p>The authoritative case confirms this exact field outcome.</p></div></section> : selected.editable && selected.humanDecision === null && !controller.reloadRequired ? selected.destination === 'cover' ? <LibraryCoverChoices reviewCaseId={selectedReviewCaseId} field={selected} disabled={controller.isPending} pending={controller.pending} phase={controller.phase} choose={(outcome, value) => choose(selected, outcome, value)} /> : <section className="direct-choice protected-choice" aria-label={`Choose a ${friendly(selected.destination)} value`}><p className="choice-intro">{selected.classificationReason}</p><div className={`direct-choice-grid${selected.preservationOnly ? ' preservation-only' : ''}`}><button aria-label={`Choose current value: ${selected.currentValue}`} aria-pressed={choiceSelected(selected, controller.pending, 'keep_current')} className={`value-choice current-value${choiceSelected(selected, controller.pending, 'keep_current') ? ' is-selected' : ''}`} disabled={controller.isPending} onClick={() => choose(selected, 'keep_current')}><small>1 · Current value</small><strong>{selected.currentValue}</strong><span>{commandSelected(controller.pending, selected.destination, 'keep_current') && controller.phase === 'recording' ? 'Recording…' : `Current catalogue value · compare ${sourceSupport}`}</span></button>{selected.preservationOnly ? null : <div className="proposed-choice-list">{selected.proposedValues.map((value, index) => <button aria-label={`Choose proposed value ${String(index + 1)}: ${displayValue(value.value)}`} aria-pressed={choiceSelected(selected, controller.pending, 'accept', value)} className={`value-choice proposed-value${choiceSelected(selected, controller.pending, 'accept', value) ? ' is-selected' : ''}`} key={`${value.kind}:${String(index)}`} disabled={controller.isPending} onClick={() => choose(selected, 'accept', value)}><small>{String(index + 2)} · Proposed value</small><strong>{displayValue(value.value)}</strong><span>{commandSelected(controller.pending, selected.destination, 'accept', value) && controller.phase === 'recording' ? 'Recording…' : 'Eligible proposal · compare the same retained evidence'}</span></button>)}</div>}</div></section> : <p className="decision-recorded">{selected.classificationReason}{selected.humanDecision || selected.automaticDecision ? ` The recorded value remains ${decisionValue(selected)}.` : ''}</p>}
                      {controller.canRetry && controller.pending?.destination === selected.destination ? <button ref={retryButton} className="decision-retry" type="button" onClick={controller.retry}>Try the same decision again</button> : null}
                      {effect ? <aside className="field-apply-context" aria-label="External update status"><span className="activity-state-mark" aria-hidden="true"></span><span><small>External library</small><strong>{effect.state}</strong><em>{effect.detail}</em></span></aside> : null}
                      {evidence.isError ? <p className="evidence-error" role="alert">Supplemental evidence could not be loaded. Retained case provenance remains in the closed ledger. <button type="button" onClick={() => void evidence.refetch()}>Try again</button></p> : null}<details className="source-ledger"><summary><span>Source ledger</span><small>{String(rows.length)} record{rows.length === 1 ? '' : 's'} · {String(sourceCount)} named source{sourceCount === 1 ? '' : 's'}</small></summary>{evidence.isPending ? <p role="status">Loading supporting evidence…</p> : <>{sourceLedgerRows ?? (evidence.isError ? null : <p>No bounded source record is available for this field.</p>)}</>}</details>
                      {controller.status || controller.message ? <p role={controller.decision.isError || controller.reloadRequired ? 'alert' : 'status'}>{controller.status || controller.message}</p> : null}
                    </article> : <p>This work has no fields requiring a decision.</p>}
                    {contextualFields.length ? <details className="automatic-resolution-details"><summary><span>{String(contextualFields.length)} automatic or recorded field{contextualFields.length === 1 ? '' : 's'}</span><small>Inspect context</small></summary><div className="automatic-resolution-list">{contextualFields.map((field) => <div className="automatic-resolution-row" key={field.destination}><span className="automatic-state-mark" aria-hidden="true">✓</span><div><strong>{friendly(field.destination)}</strong><small>{fieldState(field)} · {decisionValue(field)}</small><small>{fieldReason(field)}</small></div></div>)}</div></details> : null}
                    <div className="workspace-utilities"><Link className="full-review-link" to="/review/$reviewCaseId" params={{ reviewCaseId: search.preview }} search={fullReviewSearch}>Open full review</Link><Link className="full-review-link" to="/review/$reviewCaseId" params={{ reviewCaseId: search.preview }} search={{ subview: 'evidence', ...(selected ? { field: selected.destination } : {}) }}>Open all evidence</Link></div>
                    <div className="compact-workspace-controls"><button type="button" onClick={clearSelection}>Back to works</button><button type="button" onClick={() => void navigate({ search: (current) => ({ ...current, attention: 'open' }) })}>Attention queue</button></div>
                  </>}
      </section>
      {search.attention === 'open' ? <aside className="attention-rail" aria-label="Attention queue" tabIndex={-1} ref={attentionRail}><header><div><h2>Attention</h2><p>{String(attentionItems.length)} visible fields</p></div><button onClick={() => void navigate({ search: (current) => { const next = { ...current }; delete next.attention; return next } })}>Close</button></header>{attention.data === undefined ? <p role={attention.isError ? 'alert' : 'status'}>{attention.isError ? <>Attention could not be loaded. <button type="button" onClick={() => void attention.refetch()}>Try again</button></> : 'Loading attention…'}</p> : <>{attentionItems.map((item, index) => <button key={`${item.id}:${item.destination}`} className="attention-queue-item" aria-current={item.id === search.preview && item.destination === selected?.destination} onClick={() => selectFromQueue(item.id, item.destination)}><span className="attention-position">{String(index + 1)}</span><span className="attention-queue-copy"><strong>{item.title}</strong><small>{item.authors.length ? item.authors.join(', ') : 'Author details unavailable'}</small><small>{item.library} · {friendly(item.mediaKind)}</small><b>{friendly(item.destination)} · {item.notices[0] ? bounded(item.notices[0], 'Reviewer notice') : 'Needs your decision'}</b></span><span className="attention-work-count"><strong>{String(item.workAttentionCount)}</strong><small>for work</small></span></button>)}{attention.hasNextPage ? <button className="attention-load-more" type="button" disabled={attention.isFetchingNextPage} onClick={() => void attention.fetchNextPage()}>{attention.isFetchingNextPage ? 'Loading more…' : 'Load more attention'}</button> : null}{attention.isFetchNextPageError ? <p role="alert">More attention could not be loaded. <button type="button" onClick={() => void attention.fetchNextPage()}>Try again</button></p> : null}</>}</aside> : null}
    </div>
  </div>
}
