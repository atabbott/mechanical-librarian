import {
  invalidateBackgroundQueries,
  prefetchBackgroundQuery,
  runRequiredRead,
  useRequiredRead,
  queryWorkClass,
  useRequiredQuery as useQuery,
} from '../queryWork'
import { RecordingIdentityBasis } from '../components/RecordingIdentityBasis'
import { DescriptionAdvicePreparation } from '../components/DescriptionAdvice'
import { MediaHealth } from '../components/MediaHealth'
import { useInfiniteQuery, useMutation } from '@tanstack/react-query'
import { Link, createLink, useNavigate, useRouter, useRouterState } from '@tanstack/react-router'
import { Fragment, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import type { ComponentPropsWithRef, SyntheticEvent } from 'react'
import type { QueryExecuteOptions, QueryKey } from '@tanstack/react-query'

import { ApiError, CsrfRenewedError, displayValue, sessionRefreshedCopy } from '../api/http'
import type {
  DecisionCommand,
  LibraryLens,
  LibrarySearch,
  MediaKind,
  LibraryView,
  LibraryWorkView,
  ProviderOutcomeView,
  WorkSummaryState,
} from '../api/http'
import { choiceRadios, moveChoiceFocus } from '../choiceGroup'
import { drawerQuery, useAttentionRail } from '../attentionRail'
import { focusFieldHeading, useRouteReadyFocus } from '../focus'
import { destinationLabel, mediaKindLabels, sourceStatusLabels } from '../labels'
import { coalesceMilliseconds } from '../liveInvalidation'
import {
  detailQuery,
  libraryQuery,
  libraryRoute,
  librarySummaryQuery,
  PreparationNotice,
  ProviderThrottleNotice,
  usePreparation,
  useReviewContext,
  normalizeDetailSearch,
} from '../router'
import { WriteReadyNotice } from '../components/WriteReadyNotice'
import { Button } from '../components/Button'
import { StatusPill } from '../components/StatusPill'
import {
  actionable,
  choiceAvailable,
  requestKey,
  useReviewDecisionController,
} from '../reviewDecisionController'
import { emptyUnproposed, ownerChoiceCount, primaryField, railOffers } from '../reviewChoiceCounts'
import { advancePresentationSettled } from '../reviewAdvancePresentation'

import {
  authoredOwnerRecorded,
  completionOutcome,
  decisionValue,
  valueText,
  shownAdvice,
  effectSummary,
  commandSelected,
  choiceSelected,
  isLongDescription,
  decisionQuestion,
} from '../fieldDecision'
import type { ReviewField } from '../fieldDecision'
import { RecordedFieldObservations, MetadataChoice } from './FieldDecisionChoices'
import { LibraryCoverChoices } from './FieldDecisionCover'
import {
  AsinAutomaticStatement,
  ContextValue,
  DescriptionAutomaticStatement,
  NarratorsAutomaticStatement,
  ProviderConsensusAutomaticStatement,
  RecordedOutcome,
  SeriesAutomaticStatement,
  VerifiedManifestationIsbnAutomaticStatement,
} from './FieldDecisionOutcome'
import { OwnerControls } from './FieldDecisionOwner'
import { PreparationDetail, PreparationSummary } from './PreparationDetail'
import {
  librarySearchIdentity,
  usePreparationChange,
  usePreparationDismissal,
} from '../libraryPreparation'
import { WorkSources, WorkSummary } from './WorkSummary'
import { BookMark } from '../components/BookMark'
import { authorLine, reportEvidenceUrl } from '../libraryWork'
import {
  choiceContext,
  combinedAutomaticTags,
  comparisonNotices,
  fieldReason,
  noticeText,
  questionReason,
  selectedAutomaticLanguageForm,
} from '../fieldReasons'
const searchNavigationDelay = 150
const noWorks: readonly LibraryWorkView[] = []
// A pointer crossing several rows warms only the one it rests on.
const prefetchIntentDelay = 50
// Live Library invalidation batches the documented fifty-work burst for 120 ms.
// Keep each distinct subject from that bounded batch long enough to compare it
// with the next bounded page read, but never treat a frame as selection
// authority.
const maxCoalescedSuccessorCandidates = 50
const lensLabels = {
  all: 'All',
  needs_attention: 'Needs attention',
  no_attention_needed: 'No attention needed',
  not_matched: 'Not matched',
  preparation_failed: 'Could not finish',
} satisfies Record<LibraryLens, string>
// The masthead's Library link is the current page; the active lens is the
// current item within it, so it says `true` rather than a second `page` (#1530).
const LensLink = createLink((props: ComponentPropsWithRef<'a'>) => (
  <a {...props} aria-current={props['aria-current'] ? 'true' : undefined} />
))
// The compact layout, where the selected work replaces the list.
const compactWidth = () =>
  typeof window.matchMedia === 'function' && window.matchMedia('(max-width: 1024px)').matches
const narrowWidth = () =>
  typeof window.matchMedia === 'function' && window.matchMedia('(max-width: 760px)').matches
const workCount = (count: number) => `${String(count)} ${count === 1 ? 'work' : 'works'}`
// A search under a lens counts that lens against its whole-library total; the
// lens link names it visually and the status names it to screen readers (#1425).
const lensMatching = {
  all: ['all', undefined],
  needs_attention: ['attention', 'needing attention'],
  no_attention_needed: ['clear', 'needing no attention'],
  not_matched: ['notMatched', 'not matched'],
  preparation_failed: ['failed', 'that could not finish'],
} as const

// A recording's settled fill stays choosable without asking for review (#1249).
const optionalChoice = (field: ReviewField) =>
  field.classification === 'owner_choosable' && choiceAvailable(field)
// A field tab says where its field stands in words, never a glyph: an
// undecided optional field and a decided one both drew a check (#1290).
const fieldStatus = (field: ReviewField) =>
  actionable(field)
    ? (['needs', 'Needs you'] as const)
    : field.humanDecision || authoredOwnerRecorded(field) || field.manualHold
      ? (['decided', 'Decided'] as const)
      : field.classification === 'automatic_recorded' || field.automaticDecision
        ? (['automatic', 'Automatic'] as const)
        : optionalChoice(field)
          ? (['optional', 'Optional'] as const)
          : (['unchanged', 'No change'] as const)
// A field with nothing to decide is either unchanged, because no source
// offered a different value, or held: unavailable to this service, or waiting
// on a recovery. The server's reason is the only thing that tells them apart,
// so the two reasons for an unchanged field are named here (#1514).
const unchangedReasons = new Set([
  '',
  'There is nothing to decide for this field.',
  'There is no other value to choose from. You can enter your own.',
])
const contextKind = (field: ReviewField) =>
  field.manualHold || !unchangedReasons.has(field.classificationReason)
    ? /waiting for|pending a/u.test(field.classificationReason)
      ? ('waiting' as const)
      : ('unavailable' as const)
    : ('unchanged' as const)
const heldPhrase = (count: number, kind: 'waiting' | 'unavailable') =>
  `${String(count)} ${count === 1 ? 'field' : 'fields'} ${kind === 'waiting' ? 'waiting for a recovery' : 'unavailable'}`
const ownerOutcome = (outcome: DecisionCommand['outcome']) =>
  outcome === 'owner_entered' || outcome === 'withdraw'
// Match the persisted Library ordering; recovery eligibility is a separate contract.
const limitingProviderStatuses = new Set([
  'authentication',
  'authorization',
  'rate_limited',
  'unavailable',
  'incomplete',
  'malformed',
  'oversized',
  'candidate_limit_exceeded',
  'query_limit_exceeded',
])
// Why no provider matched a work, in the row's status slot (#1407).
const notMatchedReasons: Partial<Record<WorkSummaryState, string>> = {
  not_matched_refused: 'Found, but not this work',
  not_matched_unavailable_scheduled: 'Providers unavailable; will be checked again',
  not_matched_unavailable: 'Providers unavailable',
  not_matched_adapter_fault: 'Failed in Mechanical Librarian',
  not_matched_no_match: 'No provider matched',
}
// A failed preparation's group shares its lens's name, so a "needs attention"
// heading never sits outside the Needs attention lens (#1413).
const providerGroup = (work: LibraryWorkView) =>
  work.preparation
    ? 'Could not finish'
    : work.attention === 'not_matched'
      ? 'Not matched'
      : work.attentionCount === 0
        ? 'No attention needed'
        : work.preparationOutcomes.some((outcome) => limitingProviderStatuses.has(outcome.status))
          ? 'Provider evidence limited'
          : 'Provider evidence complete'
const providerSummary = (outcomes: readonly ProviderOutcomeView[]) => {
  const counts = new Map<string, number>()
  for (const outcome of outcomes) {
    const label = sourceStatusLabels[outcome.status]
    counts.set(label, (counts.get(label) ?? 0) + 1)
  }
  return [...counts]
    .map(([label, count]) => (count > 1 ? `${String(count)} sources: ${label}` : label))
    .join(' · ')
}

export default function LibraryPage({
  data,
  retained,
  pending,
  failed,
  retry,
  onRailRetaining,
}: {
  data: LibraryView | undefined
  /** `data` is the previous key's result, shown busy until the URL's own read settles. */
  retained: boolean
  pending: boolean
  failed: boolean
  retry: () => void
  /** Reports whether the attention rail is showing a retained queue. */
  onRailRetaining?: (retaining: boolean) => void
}) {
  const works = useMemo(() => data?.works ?? [], [data?.works])
  // Retained rows stay visible, but selection resolves only against the URL's own result.
  const currentWorks = retained ? noWorks : works
  const reading = pending || retained
  const search = libraryRoute.useSearch()
  const libraryNavigate = useNavigate({ from: '/library' })
  const router = useRouter()
  // Library stays painted while the next route's code loads, but it no longer
  // owns the URL, so a late selection or advance cannot pull the owner back.
  const navigate: typeof libraryNavigate = useCallback(
    (options) =>
      router.latestLocation.pathname === '/library' ? libraryNavigate(options) : Promise.resolve(),
    [libraryNavigate, router],
  )
  const { http, updates, queryClient } = useReviewContext()
  // Global figures read the library summary, which carries no q (#1284).
  const summary = useQuery(librarySummaryQuery(http))
  const [lensCount, lensWords] = lensMatching[search.lens]
  const lensTotal = summary.data?.counts[lensCount]
  const summaryAttention = summary.data?.counts.attention
  const summaryNotMatched = summary.data?.counts.notMatched
  const summaryFailed = summary.data?.counts.failed
  const preparationState = usePreparation()
  const presentation =
    preparationState.data?.presentation?.state === 'dismissed'
      ? null
      : (preparationState.data?.presentation ?? null)
  const preparationSelected = presentation !== null && search.preparation === presentation.run_id
  const animateChange = usePreparationChange(presentation, preparationSelected)
  const currentSearch = useRef(search)
  const pendingSearchQuery = useRef<{ q: string | null; attention?: boolean } | null>(null)
  const searchNavigation = useRef<Promise<void> | null>(null)
  const searchCommitTimer = useRef<number | null>(null)
  const searchGeneration = useRef(0)
  const observedSearchIdentity = useRef(librarySearchIdentity(search))
  const expectedSearchIdentity = useRef<string | null>(null)
  const searchInput = useRef<HTMLInputElement>(null)
  const lensRow = useRef<HTMLElement>(null)
  // The lenses scroll sideways in one row. An edge with more lenses beyond it
  // fades, so a row that happens to end between two lenses still shows it
  // continues. The current lens is brought into the row's view, clear of the
  // fade, without moving the page: on load, on a lens change, and when the row
  // or its lenses change size, as with enlarged text.
  useLayoutEffect(() => {
    const row = lensRow.current
    if (!row) return
    const mark = () => {
      const start = row.scrollLeft > 1
      const end = row.scrollLeft + row.clientWidth < row.scrollWidth - 1
      if (start || end) row.dataset.more = start && end ? 'both' : start ? 'start' : 'end'
      else delete row.dataset.more
    }
    // The first and last lenses scroll the row to its end, so no fade covers
    // them; any other lens is brought clear of both fades.
    const reveal = (lens: Element | null) => {
      if (lens instanceof HTMLElement) {
        let left = row.scrollLeft
        if (lens === row.firstElementChild) left = 0
        else if (lens === row.lastElementChild) left = row.scrollWidth - row.clientWidth
        else {
          const fade = parseFloat(getComputedStyle(row).scrollPaddingInlineStart) || 0
          const rowBox = row.getBoundingClientRect()
          const box = lens.getBoundingClientRect()
          // A lens too wide to clear both fades is centred, or starts at the
          // row's edge when even the row is narrower, so its label's start shows.
          if (box.width > rowBox.width - 2 * fade)
            left +=
              box.width > rowBox.width
                ? box.left - rowBox.left
                : box.left - rowBox.left - (rowBox.width - box.width) / 2
          else if (box.left < rowBox.left + fade) left -= rowBox.left + fade - box.left
          else if (box.right > rowBox.right - fade) left += box.right - (rowBox.right - fade)
        }
        if (Math.abs(left - row.scrollLeft) > 0.5) row.scrollLeft = left
      }
      mark()
    }
    const revealCurrent = () => reveal(row.querySelector('[aria-current]'))
    // Focus leaves a partly shown lens where it is, so Tab reveals it too.
    const revealFocused = (event: FocusEvent) => reveal(event.target as Element | null)
    revealCurrent()
    row.addEventListener('scroll', mark, { passive: true })
    row.addEventListener('focusin', revealFocused)
    const resized = typeof ResizeObserver === 'function' ? new ResizeObserver(revealCurrent) : null
    for (const node of [row, ...row.children]) resized?.observe(node)
    return () => {
      row.removeEventListener('scroll', mark)
      row.removeEventListener('focusin', revealFocused)
      resized?.disconnect()
    }
  }, [search.lens])
  const retryButton = useRef<HTMLButtonElement>(null)
  const completionRegion = useRef<HTMLElement>(null)
  const activeFieldButton = useRef<HTMLButtonElement>(null)
  const fieldRail = useRef<HTMLElement>(null)
  const advanceFocus = useRef(false)
  const advanceScroll = useRef<number | null>(null)
  const advancePresentation = useRef<{
    destination: string
    source: string
    sourceRevision: string
    target: string
    resolve: () => void
  } | null>(null)
  const releaseAdvancePresentation = useCallback(() => {
    const presentation = advancePresentation.current
    if (!presentation) return
    advancePresentation.current = null
    presentation.resolve()
  }, [])
  const centreFieldRail = useCallback((rail: HTMLElement, button: HTMLButtonElement) => {
    const reducedMotion =
      typeof window.matchMedia === 'function' &&
      window.matchMedia('(prefers-reduced-motion: reduce)').matches
    // This is a horizontal rail adjustment, not a request to reposition the
    // document. `scrollIntoView` can pull the selected work back to the top
    // when the owner records a middle field.
    const railBounds = rail.getBoundingClientRect()
    const buttonBounds = button.getBoundingClientRect()
    const left = Math.max(
      0,
      rail.scrollLeft +
        buttonBounds.left -
        railBounds.left -
        (rail.clientWidth - buttonBounds.width) / 2,
    )
    if (typeof rail.scrollTo === 'function')
      rail.scrollTo({ left, behavior: reducedMotion ? 'auto' : 'smooth' })
  }, [])
  const automaticTrigger = useRef<HTMLButtonElement>(null)
  const keepManualHandling = useRef<HTMLButtonElement>(null)
  const restoreAutomaticTrigger = useRef(false)
  const attentionRail = useRef<HTMLElement>(null)
  const attentionHeading = useRef<HTMLHeadingElement>(null)
  const attentionToggle = useRef<HTMLButtonElement>(null)
  const attentionRetry = useRef<HTMLButtonElement>(null)
  const revealWorkspace = useRef(false)
  const revealQueueChoice = useRef(false)
  const workSelectionGeneration = useRef(0)
  const selectedIdentityRef = useRef<{
    id: string
    title: string
    authors: readonly string[]
    library: string
    libraryDisplayName?: string
    mediaKind: MediaKind
  } | null>(null)
  const selectedTargetRef = useRef<{
    caseId: string
    connection: string
    library: string
    item: string
  } | null>(null)
  const selectedPreviewRef = useRef<string | undefined>(undefined)
  const selectedDetailRetry = useRef<string | null>(null)
  const selectedDetailRetryButton = useRef<HTMLButtonElement>(null)
  // The preview whose detail was last shown ready, until the preview changes.
  const selectedDetailShown = useRef<string | null>(null)
  const pendingSuccessorRef = useRef(new Set<string>())
  const lineageGenerationRef = useRef(0)
  const [reconciledSuccessorIdentity, setReconciledSuccessorIdentity] = useState<string | null>(
    null,
  )
  const [activeLineageGeneration, setActiveLineageGeneration] = useState<number | null>(null)
  const [lineageReconciliation, setLineageReconciliation] = useState<{
    predecessor: string
    selection: string
    generation: number
  } | null>(null)
  const [successorGeneration, setSuccessorGeneration] = useState(0)
  const [frozenFieldOrders, setFrozenFieldOrders] = useState<
    Readonly<Record<string, readonly string[]>>
  >({})
  const [shownEmptyFields, setShownEmptyFields] = useState<Readonly<Record<string, true>>>({})
  const [revealFocusDestination, setRevealFocusDestination] = useState<string | null>(null)
  const [complete, setComplete] = useState<{
    id: string
    fields: readonly ReviewField[]
    priorAttention: ReadonlySet<string>
    identity: {
      title: string
      authors: readonly string[]
      library: string
      libraryDisplayName?: string
      mediaKind: MediaKind
    }
  } | null>(null)
  const [completionInspection, setCompletionInspection] = useState<string | null>(null)
  const [automaticPanel, setAutomaticPanel] = useState<string | null>(null)
  const [ownerCommand, setOwnerCommand] = useState<string | null>(null)
  // What the owner is told on the case they were moved to when theirs was
  // superseded under them, and the case whose field heading takes focus (M4).
  const [ownerNotice, setOwnerNotice] = useState<{
    caseId: string
    destination: string
    text: string
  } | null>(null)
  const headingFocus = useRef<string | null>(null)
  // The case being left for its successor: while it is, its own failed read
  // and its absence from Library are the move, not something to report.
  const [movingFrom, setMovingFrom] = useState<string | null>(null)
  // The selected case once any path knows a successor is pending for it (a
  // lineage check, a Library update naming one, or a stale command): until
  // the move settles, its absence from Library is that move, not news. It is
  // set where the successor becomes known and cleared only where a move ends,
  // since a move can start before a lineage check's own update lands.
  const [successorPending, setSuccessorPending] = useState<string | null>(null)
  const ownerField = useRef<string | null>(null)
  // The case whose owner form was open, or whose owner control had focus,
  // when the Library said it changed; the refetch that follows can unmount it.
  const ownerEngaged = useRef<string | null>(null)
  const [automaticResults, setAutomaticResults] = useState<
    Readonly<Record<string, 'success' | 'error' | 'refreshed'>>
  >({})
  const staleCoverRecoveryButton = useRef<HTMLButtonElement>(null)
  const [staleCoverReview, setStaleCoverReview] = useState<{
    caseId: string
    title: string
    failed: boolean
    loading: boolean
  } | null>(null)
  const [openedWork, setOpenedWork] = useState<LibraryWorkView | null>(null)
  const [expandedChoiceState, setExpandedChoiceState] = useState<{
    work: string
    keys: ReadonlySet<string>
  }>({ work: '', keys: new Set() })
  // The URL owns the query. This holds only the exact characters typed since the
  // last URL move, so a trailing space survives the round trip; the moment the
  // URL says something this echo does not, the URL wins — which is what makes
  // Back, Forward and a deep link reseed the box instead of fighting it.
  const [typedQuery, setTypedQuery] = useState({ echo: search.q ?? '', text: search.q ?? '' })
  const [searchPending, setSearchPending] = useState(false)
  const urlQuery = search.q ?? ''
  const searchIdentity = librarySearchIdentity(search)
  const locationRevision = useRouterState({
    select: (state) =>
      state.location.state.__TSR_key ?? state.location.state.key ?? state.location.href,
  })
  const preparationDismissal = usePreparationDismissal({
    http,
    queryClient,
    router,
    navigate,
    adoptDismissal: preparationState.adoptDismissal,
    preparation: search.preparation,
    locationRevision,
    searchIdentity,
  })
  const { cleaningUp } = preparationDismissal
  const queryText = searchPending || typedQuery.echo === urlQuery ? typedQuery.text : urlQuery
  const selectedReviewCaseId = search.preview ?? ''
  useEffect(() => {
    if (selectedPreviewRef.current !== search.preview) {
      setActiveLineageGeneration(null)
      setLineageReconciliation(null)
      setSuccessorPending(null)
    }
    selectedPreviewRef.current = search.preview
    pendingSuccessorRef.current.clear()
  }, [search.preview])
  const invalidateSearch = useCallback(() => {
    ++searchGeneration.current
    pendingSearchQuery.current = null
    expectedSearchIdentity.current = null
    if (searchCommitTimer.current !== null) {
      window.clearTimeout(searchCommitTimer.current)
      searchCommitTimer.current = null
    }
  }, [])
  useEffect(() => {
    if (observedSearchIdentity.current === searchIdentity) return
    observedSearchIdentity.current = searchIdentity
    if (expectedSearchIdentity.current === searchIdentity) {
      expectedSearchIdentity.current = null
      return
    }
    invalidateSearch()
    setSearchPending(false)
  }, [invalidateSearch, searchIdentity])
  useEffect(() => {
    return () => {
      invalidateSearch()
    }
  }, [invalidateSearch])
  // A search or lens may temporarily hide the open work's card. The row the
  // owner opened is kept so the summary does not vanish mid-inspection and
  // send them back to the list; clearing the selection releases it.
  const selectedWork = search.work
    ? (currentWorks.find((work) => work.workId === search.work) ??
      (openedWork?.workId === search.work ? openedWork : undefined))
    : undefined
  const unresolvableWork =
    data !== undefined && !retained && Boolean(search.work) && selectedWork === undefined
  // URL-owned selection has to stay truthful. A ?work= that resolves to nothing
  // on the bounded page in hand, and was not opened in this session, asserts a
  // selection the view cannot render, so it is normalised out of the URL. The
  // entry is replaced rather than pushed, so Back still leaves for wherever the
  // owner arrived from instead of returning to the unresolvable link.
  useEffect(() => {
    if (!unresolvableWork) return
    void navigate({
      replace: true,
      search: (current) => {
        const next = { ...current }
        delete next.work
        return next
      },
    })
  }, [unresolvableWork, navigate])
  useEffect(() => {
    if (!search.preparation || preparationSelected || !preparationState.confirmed) return
    if (cleaningUp(search.preparation)) return
    void navigate({
      replace: true,
      search: (current) => {
        const next = { ...current }
        delete next.preparation
        return next
      },
    })
  }, [cleaningUp, navigate, preparationSelected, preparationState.confirmed, search.preparation])
  const hasSelection = preparationSelected || Boolean(search.preview) || selectedWork !== undefined
  const selectedLibraryWork = search.preview
    ? currentWorks.find((work) => work.reviewCaseId === search.preview)
    : undefined
  const detail = useQuery({
    ...detailQuery(http, selectedReviewCaseId),
    enabled: Boolean(search.preview) && !preparationSelected,
  })
  const activeLineageReconciliation =
    lineageReconciliation !== null &&
    lineageReconciliation.predecessor === search.preview &&
    lineageReconciliation.selection === search.preview &&
    activeLineageGeneration === lineageReconciliation.generation
  const lineageSuccessor = useQuery({
    queryKey: [
      'review-case-successor',
      lineageReconciliation?.predecessor ?? null,
      lineageReconciliation?.generation ?? null,
    ],
    queryFn: (queryContext) =>
      http.getCaseSuccessor(
        lineageReconciliation?.predecessor ?? '',
        queryContext.signal,
        queryWorkClass(queryContext),
      ),
    enabled: activeLineageReconciliation,
    retry: (failureCount) => activeLineageReconciliation && failureCount < 1,
    retryDelay: coalesceMilliseconds,
    retryOnMount: false,
    refetchInterval: false,
    refetchOnMount: false,
    refetchOnReconnect: false,
    refetchOnWindowFocus: false,
  })
  // Keep the queue this rail last showed while a newer search reads; a failed
  // read shows nothing, so it leaves nothing to revive.
  const showingAttention = useRef(false)
  const attentionQuery = useInfiniteQuery({
    queryKey: ['attention', 'workspace', search.q ?? null, search.attentionCursor ?? null],
    initialPageParam: search.attentionCursor,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
    queryFn: (queryContext) =>
      http.getAttention(
        {
          ...(search.q ? { q: search.q } : {}),
          ...(queryContext.pageParam ? { cursor: queryContext.pageParam } : {}),
        },
        queryContext.signal,
        queryWorkClass(queryContext),
      ),
    getNextPageParam: (page) => page.nextCursor ?? undefined,
    staleTime: 15_000,
    retry: false,
    placeholderData: (previous) => (showingAttention.current ? previous : undefined),
  })
  useEffect(() => {
    showingAttention.current = attentionQuery.data !== undefined
  })
  useLayoutEffect(
    () => onRailRetaining?.(attentionQuery.isPlaceholderData),
    [onRailRetaining, attentionQuery.isPlaceholderData],
  )
  const attention = useMemo(
    () => ({
      ...attentionQuery,
      refetch: () =>
        runRequiredRead(
          queryClient,
          ['attention', 'workspace', search.q ?? null, search.attentionCursor ?? null],
          () => attentionQuery.refetch(),
        ),
      fetchNextPage: () =>
        runRequiredRead(
          queryClient,
          ['attention', 'workspace', search.q ?? null, search.attentionCursor ?? null],
          () => attentionQuery.fetchNextPage(),
        ),
    }),
    [attentionQuery, queryClient, search.q, search.attentionCursor],
  )
  useRequiredRead(queryClient, [
    'attention',
    'workspace',
    search.q ?? null,
    search.attentionCursor ?? null,
  ])
  const attentionItems = useMemo(
    () =>
      attention.data?.pages.flatMap((page, pageIndex) => {
        const cursor = attention.data.pageParams[pageIndex]
        return page.items.map((item) => ({
          ...item,
          attentionCursor: typeof cursor === 'string' ? cursor : undefined,
        }))
      }) ?? [],
    [attention.data],
  )
  // One group per work, in first-appearance order; a work that continues onto
  // the next page joins its existing group.
  const attentionGroups = useMemo(() => {
    const groups = new Map<string, (typeof attentionItems)[number][]>()
    for (const item of attentionItems) {
      const group = groups.get(item.id)
      if (group) group.push(item)
      else groups.set(item.id, [item])
    }
    return [...groups.values()]
  }, [attentionItems])
  // Load more keeps its place: once the page lands, focus moves to the first row
  // it added, even when that row joins a work already in the rail (#1284). The
  // press ends at the query's next data or error update, whether or not a render
  // saw it fetching, so a refetch that cancels the page ends it too; a new search
  // drops it, and focus moves only for an added page the owner has not left.
  const attentionKey = `${search.q ?? ''} ${search.attentionCursor ?? ''}`
  const loadMore = useRef<{
    key: string
    pages: number
    updated: number
    errors: number
    from: number
  } | null>(null)
  useEffect(() => {
    const pending = loadMore.current
    if (!pending) return
    if (pending.key !== attentionKey || attentionQuery.errorUpdateCount > pending.errors) {
      loadMore.current = null
      return
    }
    const pages = attention.data?.pages.length ?? 0
    if (attentionQuery.dataUpdatedAt === pending.updated && pages <= pending.pages) return
    loadMore.current = null
    if (pages <= pending.pages) return
    const active = document.activeElement
    if (active && active !== document.body && !attentionRail.current?.contains(active)) return
    const first = attentionItems[pending.from]
    const row = first
      ? [
          ...(attentionRail.current?.querySelectorAll<HTMLElement>('[data-attention-row]') ?? []),
        ].find((node) => node.dataset.attentionRow === `${first.id}:${first.destination}`)
      : undefined
    ;(row ?? attentionRail.current)?.focus()
  }, [
    attention.data,
    attentionItems,
    attentionKey,
    attentionQuery.dataUpdatedAt,
    attentionQuery.errorUpdateCount,
  ])
  const selectedIdentity = useMemo(
    () =>
      search.preview
        ? (currentWorks.find((work) => work.reviewCaseId === search.preview) ??
          (attention.isPlaceholderData
            ? undefined
            : attentionItems.find((item) => item.id === search.preview)) ??
          // A readable case names its own work, so the selection survives a
          // Library result or Attention page that no longer lists it.
          (!detail.isError && detail.data?.id === search.preview
            ? {
                id: detail.data.id,
                title: detail.data.title,
                authors: detail.data.authors,
                library: detail.data.target.library,
                libraryDisplayName:
                  detail.data.target.libraryDisplayName ?? detail.data.target.library,
                mediaKind: detail.data.target.mediaKind,
              }
            : undefined))
        : undefined,
    [
      attentionItems,
      attention.isPlaceholderData,
      currentWorks,
      detail.data,
      detail.isError,
      search.preview,
    ],
  )
  useEffect(() => {
    if (selectedIdentity && search.preview)
      selectedIdentityRef.current = {
        id: search.preview,
        title: selectedIdentity.title,
        authors: selectedIdentity.authors,
        library: selectedIdentity.library,
        libraryDisplayName: selectedIdentity.libraryDisplayName ?? selectedIdentity.library,
        mediaKind: selectedIdentity.mediaKind,
      }
  }, [search.preview, selectedIdentity])
  useEffect(() => {
    if (search.preview && detail.data?.id === search.preview) {
      selectedTargetRef.current = { caseId: search.preview, ...detail.data.target }
      if (pendingSuccessorRef.current.size) {
        setSuccessorPending(search.preview)
        setSuccessorGeneration((generation) => generation + 1)
      }
    }
  }, [detail.data, search.preview])
  useLayoutEffect(() => {
    const reconciliation = lineageReconciliation
    if (
      !lineageSuccessor.isSuccess ||
      reconciliation === null ||
      reconciliation.selection !== search.preview ||
      selectedPreviewRef.current !== reconciliation.selection ||
      activeLineageGeneration !== reconciliation.generation
    )
      return
    // A successful null is authoritative; only the query's single bounded
    // retry treats transport failure as pending reconciliation.
    const named = lineageSuccessor.data
    const following =
      named !== null && pendingSuccessorRef.current.size < maxCoalescedSuccessorCandidates
    queueMicrotask(() => {
      setActiveLineageGeneration(null)
      setLineageReconciliation(null)
      if (following && selectedTargetRef.current?.caseId === reconciliation.selection)
        setSuccessorPending(reconciliation.selection)
    })
    if (following) {
      pendingSuccessorRef.current.add(named)
      setSuccessorGeneration((generation) => generation + 1)
    }
  }, [
    activeLineageGeneration,
    lineageReconciliation,
    lineageSuccessor.data,
    lineageSuccessor.isSuccess,
    search.preview,
  ])
  useLayoutEffect(() => {
    const reconciliation = lineageReconciliation
    if (
      !lineageSuccessor.isError ||
      lineageSuccessor.failureCount < 2 ||
      reconciliation === null ||
      reconciliation.selection !== search.preview ||
      activeLineageGeneration !== reconciliation.generation
    )
      return
    queueMicrotask(() => {
      setActiveLineageGeneration(null)
      setLineageReconciliation(null)
    })
  }, [
    activeLineageGeneration,
    lineageReconciliation,
    lineageSuccessor.failureCount,
    lineageSuccessor.isError,
    search.preview,
  ])
  useEffect(() => {
    if (!updates || !search.preview) return
    return updates.subscribe((update) => {
      if (update.topic !== 'library') return
      if (
        document.querySelector(
          '.owner-controls form, .owner-controls button:focus, .owner-controls input:focus',
        )
      )
        ownerEngaged.current = search.preview ?? null
      if (update.subject === null) {
        const predecessor = search.preview
        if (!predecessor) return
        const generation = ++lineageGenerationRef.current
        setActiveLineageGeneration(generation)
        setLineageReconciliation({ predecessor, selection: predecessor, generation })
        return
      }
      if (update.subject === search.preview) return
      if (
        pendingSuccessorRef.current.size < maxCoalescedSuccessorCandidates &&
        !pendingSuccessorRef.current.has(update.subject)
      ) {
        pendingSuccessorRef.current.add(update.subject)
        if (selectedTargetRef.current?.caseId === search.preview)
          setSuccessorPending(search.preview ?? null)
        // Query structural sharing can make the next bounded Library read
        // referentially identical after a no-match batch. Candidate arrival is
        // itself the reason to reconcile that read once more.
        setSuccessorGeneration((generation) => generation + 1)
      }
    })
  }, [http, search.preview, updates])
  useEffect(() => {
    const selectedTarget = selectedTargetRef.current
    const successorIds = [...pendingSuccessorRef.current]
    if (
      !data ||
      retained ||
      !selectedTarget ||
      !successorIds.length ||
      selectedTarget.caseId !== search.preview ||
      works.some((work) => work.reviewCaseId === search.preview)
    )
      return
    pendingSuccessorRef.current.clear()
    setMovingFrom(selectedTarget.caseId)
    void (async () => {
      try {
        const predecessor = await http.getCaseDetail(selectedTarget.caseId)
        if (
          predecessor.current ||
          predecessor.target.connection !== selectedTarget.connection ||
          predecessor.target.library !== selectedTarget.library ||
          predecessor.target.item !== selectedTarget.item
        ) {
          setMovingFrom(null)
          setSuccessorPending(null)
          return
        }
      } catch (error) {
        // The server refuses a superseded case's read as stale; that is the
        // case no longer being current. Each successor's target is checked.
        if (!(error instanceof ApiError && error.code === 'stale')) {
          // The check itself failed, so the case is read again while what the
          // owner has open stays; a failed read then drops it (#1365).
          void queryClient.refetchQueries({
            queryKey: detailQuery(http, selectedTarget.caseId).queryKey,
            exact: true,
          })
          setMovingFrom(null)
          setSuccessorPending(null)
          return
        }
      }
      for (const successorId of successorIds) {
        try {
          const successor = await http.getCaseDetail(successorId)
          if (
            !successor.current ||
            successor.target.connection !== selectedTarget.connection ||
            successor.target.library !== selectedTarget.library ||
            successor.target.item !== selectedTarget.item
          )
            continue
          setReconciledSuccessorIdentity(successor.id)
          const destination = ownerField.current
          if (destination && ownerEngaged.current === selectedTarget.caseId) {
            ownerEngaged.current = null
            setOwnerNotice({
              caseId: successor.id,
              destination,
              text: 'The work changed. Review it again.',
            })
            headingFocus.current = successor.id
          }
          await navigate({
            replace: true,
            search: (current) =>
              current.preview === selectedTarget.caseId
                ? { ...current, preview: successor.id }
                : current,
          })
          setMovingFrom(null)
          setSuccessorPending(null)
          return
        } catch {
          // A case can disappear again between the bounded page read and its
          // authoritative detail read. The remaining coalesced candidates may
          // still name its replacement.
        }
      }
      // A move that ends nowhere leaves a predecessor its own read refused, so
      // its retained case read is read again rather than shown as current.
      void queryClient.resetQueries({
        queryKey: detailQuery(http, selectedTarget.caseId).queryKey,
        exact: true,
      })
      setMovingFrom(null)
      setSuccessorPending(null)
    })()
  }, [data, retained, works, http, navigate, queryClient, search.preview, successorGeneration])
  const completionIdentity = complete?.id === selectedReviewCaseId ? complete.identity : undefined
  const [pendingIdentity, setPendingIdentity] = useState<{
    id: string
    identity: NonNullable<typeof selectedIdentity>
  } | null>(null)
  if (selectedIdentity && search.preview) {
    if (pendingIdentity?.id !== search.preview || pendingIdentity.identity !== selectedIdentity)
      setPendingIdentity({ id: search.preview, identity: selectedIdentity })
  } else if (pendingIdentity && (!reading || failed || pendingIdentity.id !== search.preview))
    setPendingIdentity(null)
  const displayedIdentity =
    selectedIdentity ??
    completionIdentity ??
    (reading && !failed && pendingIdentity !== null && pendingIdentity.id === search.preview
      ? pendingIdentity.identity
      : undefined)
  const visibleFields = detail.data?.fields.filter(primaryField) ?? []
  const currentActionableDestinations = visibleFields.map((field) => field.destination)
  const frozenDestinations =
    frozenFieldOrders[selectedReviewCaseId] ?? currentActionableDestinations
  const railFields = frozenDestinations
    .map((destination) => visibleFields.find((field) => field.destination === destination))
    .filter((field): field is ReviewField => field !== undefined)
  const urlSelected = visibleFields.find((field) => field.destination === search.field)
  const revealEmptyFields =
    shownEmptyFields[selectedReviewCaseId] === true ||
    (urlSelected !== undefined && emptyUnproposed(urlSelected))
  const omittedFields = revealEmptyFields ? [] : railFields.filter(emptyUnproposed)
  const renderedRailFields = revealEmptyFields
    ? railFields
    : railFields.filter((field) => !emptyUnproposed(field))
  const selected = urlSelected ?? renderedRailFields.find(actionable) ?? renderedRailFields[0]
  const attentionFields = railFields.filter(actionable)
  const selectedOwnerDestination = selected?.ownerControl ? selected.destination : null
  useEffect(() => {
    ownerField.current = selectedOwnerDestination
  }, [selectedOwnerDestination])
  const shownCaseId = detail.data?.id
  useEffect(() => {
    if (!shownCaseId || headingFocus.current !== shownCaseId) return
    headingFocus.current = null
    requestAnimationFrame(focusFieldHeading)
  }, [shownCaseId])
  // An owner write settles after its decision recorded. The case topic is not
  // published then, so while one is in flight each Activity frame re-reads the
  // open case, and its controls stop saying the write is in progress (O1).
  const ownerWriteInFlight =
    detail.data?.fields.some((field) => field.ownerControl?.withdraw === 'in_progress') === true
  useEffect(() => {
    if (!updates || !ownerWriteInFlight || !shownCaseId) return
    return updates.subscribe((update) => {
      if (update.topic !== 'activity') return
      void invalidateBackgroundQueries(queryClient, {
        queryKey: ['review-case', shownCaseId],
        exact: true,
      })
    })
  }, [ownerWriteInFlight, queryClient, shownCaseId, updates])
  const contextualFields =
    detail.data?.fields.filter((field) => field.inventoryRole === 'contextual') ?? []
  const contextualSummary = {
    unchanged: contextualFields.filter((field) => contextKind(field) === 'unchanged'),
    waiting: contextualFields.filter((field) => contextKind(field) === 'waiting').length,
    unavailable: contextualFields.filter((field) => contextKind(field) === 'unavailable').length,
  }
  const activeQueueIndex = selected
    ? attentionItems.findIndex(
        (item) => item.id === search.preview && item.destination === selected.destination,
      )
    : -1
  const activeQueueGroup = attentionGroups.findIndex((group) => group[0]?.id === search.preview)
  // The summary and this search's read are separate reads; clamp any skew.
  const outsideSearch = Math.max((summaryAttention ?? 0) - (data?.counts.attention ?? 0), 0)
  const nextWork =
    complete && !attention.isPlaceholderData
      ? (attentionItems.find(
          (item) =>
            item.id !== selectedReviewCaseId &&
            !complete.priorAttention.has(`${item.id}:${item.destination}`),
        ) ?? null)
      : null
  const nextWorkError = attention.isError
  const controller = useReviewDecisionController({
    reviewCaseId: selectedReviewCaseId,
    fields: detail.data?.fields ?? [],
    active: (destination) =>
      currentSearch.current.preview === search.preview &&
      search.preview !== undefined &&
      (currentSearch.current.field === undefined || currentSearch.current.field === destination),
    onAdvance: async (field) => {
      const advancing = field !== selected?.destination
      advanceFocus.current = advancing
      advanceScroll.current = advancing ? window.scrollY : null
      const presented = advancing
        ? new Promise<void>((resolve) => {
            advancePresentation.current = {
              destination: field,
              source: searchIdentity,
              sourceRevision: locationRevision,
              target: librarySearchIdentity({ ...search, field }),
              resolve,
            }
          })
        : null
      const navigation = { committed: false }
      try {
        await navigate({
          resetScroll: false,
          search: (current) => {
            if (current.preview !== search.preview) return current
            navigation.committed = true
            return { ...current, field }
          },
        })
      } catch {
        advanceFocus.current = false
        advanceScroll.current = null
        releaseAdvancePresentation()
        return
      }
      if (!navigation.committed) {
        advanceFocus.current = false
        advanceScroll.current = null
        releaseAdvancePresentation()
        return
      }
      // The controller may start background aggregate convergence only after
      // this exact next field has committed to the DOM.
      if (presented) await presented
    },
    onComplete: (fields) => {
      const retained = selectedIdentityRef.current
      if (retained?.id === selectedReviewCaseId) {
        setCompletionInspection(null)
        setComplete({
          id: selectedReviewCaseId,
          fields: fields.filter((field) => field.humanDecision !== null),
          priorAttention: new Set(
            // A retained rail is the previous search's queue, not a prefix of the next one.
            (activeQueueIndex < 0 || attention.isPlaceholderData
              ? []
              : attentionItems.slice(0, activeQueueIndex + 1)
            ).map((item) => `${item.id}:${item.destination}`),
          ),
          identity: retained,
        })
      }
    },
    onRecorded: (caseId, destination) => {
      if (destination !== 'cover') return
      setStaleCoverReview((current) => (current?.caseId === caseId ? null : current))
    },
    onStale: async (command) => {
      if (ownerOutcome(command.outcome)) {
        // The owner stays on the work: the refused command is said on the case
        // that replaced theirs, with focus on its field (M4).
        const predecessor = selectedReviewCaseId
        setMovingFrom(predecessor)
        setSuccessorPending(predecessor)
        const successor = await http.getCaseSuccessor(predecessor).catch(() => null)
        if (successor !== null) {
          setReconciledSuccessorIdentity(successor)
          setOwnerNotice({
            caseId: successor,
            destination: command.destination,
            text: 'Not recorded: the work changed. Review it again.',
          })
          headingFocus.current = successor
          await navigate({
            replace: true,
            search: (current) =>
              current.preview === predecessor
                ? { ...current, preview: successor, field: command.destination }
                : current,
          })
          setMovingFrom(null)
          setSuccessorPending(null)
          return
        }
        setMovingFrom(null)
        setSuccessorPending(null)
      }
      if (selected?.destination === 'cover') {
        setStaleCoverReview({
          caseId: selectedReviewCaseId,
          title: selectedIdentityRef.current?.title ?? 'Cover comparison',
          failed: false,
          loading: false,
        })
        return
      }
      await navigate({ search: { lens: 'needs_attention' } })
    },
  })
  const reviewCurrentCoverComparison = async () => {
    const stale = staleCoverReview
    if (!stale || stale.loading) return
    setStaleCoverReview({ ...stale, failed: false, loading: true })
    try {
      await controller.settle()
      const successor = await http.getCaseSuccessor(stale.caseId)
      const currentCaseId = successor ?? stale.caseId
      const currentDetail = await http.getCaseDetail(currentCaseId)
      const currentCover = currentDetail.fields.find(
        (field) => field.destination === 'cover' && field.coverConsideration !== null,
      )
      if (!currentDetail.current || currentCover === undefined) throw new Error('stale cover')
      if (successor !== null) setReconciledSuccessorIdentity(currentCaseId)
      await navigate({
        replace: true,
        search: (current) =>
          current.preview === stale.caseId
            ? { ...current, preview: currentCaseId, field: 'cover' }
            : current,
      })
      setStaleCoverReview(null)
    } catch {
      setStaleCoverReview({ ...stale, failed: true, loading: false })
    }
  }
  const staleCoverCaseId = staleCoverReview?.caseId
  const staleCoverIdentityAvailable = Boolean(displayedIdentity)
  useEffect(() => {
    if (!staleCoverCaseId) return
    const frame = requestAnimationFrame(() => {
      if (document.activeElement === document.body) staleCoverRecoveryButton.current?.focus()
    })
    return () => cancelAnimationFrame(frame)
  }, [staleCoverCaseId, staleCoverIdentityAvailable])
  const staleCoverNotice =
    staleCoverReview?.caseId === selectedReviewCaseId ? (
      <aside className="comparison-notice" role="alert">
        {!displayedIdentity || detail.isError ? <h2>{staleCoverReview.title}</h2> : null}
        <p>This cover comparison changed before your choice was recorded. No cover was changed.</p>
        {staleCoverReview.failed ? (
          <p>The current comparison could not be loaded. Your prior choice remains unrecorded.</p>
        ) : null}
        <button
          type="button"
          ref={staleCoverRecoveryButton}
          disabled={staleCoverReview.loading}
          onClick={() => void reviewCurrentCoverComparison()}
        >
          {staleCoverReview.loading ? 'Loading current comparison…' : 'Review current comparison'}
        </button>
      </aside>
    ) : null
  const selectedAutomaticKey = selected
    ? `${selectedReviewCaseId}:${selected.destination}:${selected.targetFingerprint}`
    : null
  const automaticPanelVisible = automaticPanel === selectedAutomaticKey && selected?.manualHold
  const automaticResult = selectedAutomaticKey
    ? (automaticResults[selectedAutomaticKey] ?? null)
    : null
  const automaticCompletedForSelected = automaticResult === 'success'
  const returnAutomatic = useMutation({
    mutationFn: ({ destination }: { destination: string; key: string }) =>
      http.returnToAutomatic(selectedReviewCaseId, destination),
    retry: false,
    onSuccess: (_value, request) => {
      setAutomaticResults((current) => ({ ...current, [request.key]: 'success' }))
    },
    onError: (error, request) =>
      setAutomaticResults((current) => ({
        ...current,
        [request.key]: error instanceof CsrfRenewedError ? 'refreshed' : 'error',
      })),
  })

  const routeIdentity = `${search.lens}:${search.q ?? ''}:${search.cursor ?? ''}:${search.attentionCursor ?? ''}:${search.preview ?? ''}:${search.work ?? ''}:${search.attention ?? ''}:${search.preparation ?? ''}`
  useRouteReadyFocus(routeIdentity, true)
  useEffect(() => {
    if (!preparationSelected) return
    const frame = requestAnimationFrame(() => {
      document
        .querySelector<HTMLElement>('.preparation-detail [data-route-focus]')
        ?.focus({ preventScroll: true })
    })
    return () => cancelAnimationFrame(frame)
  }, [preparationSelected, presentation?.run_id])
  const identityPending =
    !displayedIdentity &&
    (reading || attention.isPending || attention.isPlaceholderData || detail.isPending)
  const identityError = !displayedIdentity && (failed || attention.isError)
  // While a command records, the Library asks for the successor, the owner is
  // moved to it, or its identity is still being read, the absence is the move.
  const leaving =
    movingFrom === search.preview ||
    activeLineageReconciliation ||
    controller.recording ||
    (reconciledSuccessorIdentity === search.preview && detail.isPending)
  const selectedReadPending = identityPending || (Boolean(displayedIdentity) && detail.isPending)
  const selectedReadError = identityError || (Boolean(displayedIdentity) && detail.isError)
  const selectedDetailReady =
    !preparationSelected &&
    Boolean(search.preview) &&
    search.attention !== 'open' &&
    Boolean(displayedIdentity) &&
    !selectedReadPending &&
    !selectedReadError
  useEffect(() => {
    selectedDetailRetry.current = null
    selectedDetailShown.current = null
  }, [search.preview])
  useEffect(() => {
    const selectedDetailSettled = selectedDetailReady || selectedReadError
    if (!selectedDetailSettled) return
    const workspace = document.querySelector<HTMLElement>('#main-content .workspace-detail')
    const active = document.activeElement
    const restoreAfterRetry =
      selectedDetailRetry.current === search.preview && active === document.body
    if (workspace && active === workspace && selectedDetailReady)
      workspace.querySelector<HTMLElement>('[data-route-focus]')?.focus({ preventScroll: true })
    else if (restoreAfterRetry) {
      if (selectedReadError) selectedDetailRetryButton.current?.focus({ preventScroll: true })
      else
        workspace?.querySelector<HTMLElement>('[data-route-focus]')?.focus({ preventScroll: true })
    }
    selectedDetailRetry.current = null
    if (selectedDetailReady) selectedDetailShown.current = search.preview ?? null
  }, [selectedReadError, search.preview, selectedDetailReady])
  // A work chosen from the attention queue takes focus on its header without
  // scrolling. With enlarged text the header can sit below the fold, or under
  // the masthead, so once it holds focus it is brought fully into view; the
  // page's scroll padding keeps it clear of the masthead (#1487). Where Back to
  // works leads the work, the reveal starts there instead, so it stays in view
  // with the header beneath it, as much of it as the screen holds (#1530).
  useEffect(() => {
    if (!revealQueueChoice.current || !(selectedDetailReady || selectedReadError)) return
    revealQueueChoice.current = false
    let revealFrame = 0
    const focusFrame = requestAnimationFrame(() => {
      revealFrame = requestAnimationFrame(() => {
        const active = document.activeElement
        if (!(active instanceof HTMLElement && active.closest('#main-content .workspace-detail')))
          return
        const back = document.querySelector<HTMLElement>('.compact-workspace-back')
        if (!back?.getClientRects().length) {
          active.scrollIntoView({ block: 'nearest' })
          return
        }
        const masthead = document.querySelector<HTMLElement>('.masthead')?.getBoundingClientRect()
        const clear = Math.max(0, masthead?.bottom ?? 0)
        const top = back.getBoundingClientRect().top
        if (top < clear || active.getBoundingClientRect().bottom > window.innerHeight)
          window.scrollBy({ top: top - clear })
      })
    })
    return () => {
      cancelAnimationFrame(focusFrame)
      cancelAnimationFrame(revealFrame)
    }
  }, [search.preview, search.field, selectedDetailReady, selectedReadError])
  // A detail that was shown and then could not be read again, as after a
  // command whose follow-up reads fail, takes away the control that had focus;
  // its Try again takes focus rather than the page (#1288).
  useEffect(() => {
    if (!search.preview || !selectedReadError || leaving) return
    if (document.activeElement !== document.body) return
    if (selectedDetailShown.current !== search.preview) return
    selectedDetailRetryButton.current?.focus({ preventScroll: true })
  }, [leaving, search.preview, selectedReadError])
  useEffect(() => {
    currentSearch.current = search
  }, [search])
  useEffect(() => {
    if (controller.canRetry) retryButton.current?.focus()
  }, [controller.canRetry])
  useEffect(() => {
    if (complete?.id !== selectedReviewCaseId || completionInspection !== null) return
    completionRegion.current?.focus({ preventScroll: true })
    // The card replaces the last choice where it stood, so its heading can
    // start under the masthead; the page's scroll padding keeps it clear (#1490).
    completionRegion.current?.querySelector('h3')?.scrollIntoView({ block: 'nearest' })
  }, [complete, completionInspection, selectedReviewCaseId])
  useLayoutEffect(() => {
    const presentation = advancePresentation.current
    if (!presentation) return
    if (
      advancePresentationSettled(
        presentation,
        searchIdentity,
        locationRevision,
        selected?.destination,
      )
    )
      releaseAdvancePresentation()
  }, [locationRevision, releaseAdvancePresentation, searchIdentity, selected?.destination])
  useEffect(() => releaseAdvancePresentation, [releaseAdvancePresentation])
  useEffect(() => {
    const rail = fieldRail.current
    const active = activeFieldButton.current
    if (!selectedDetailReady || !rail || !active) return
    centreFieldRail(rail, active)
    if (!advanceFocus.current) return
    advanceFocus.current = false
    // The next field's choices take focus at their one Tab stop (#1290).
    const action =
      document.querySelector<HTMLButtonElement>('.decision-canvas [role="radio"][tabindex="0"]') ??
      document.querySelector<HTMLButtonElement>('.decision-canvas button:not(:disabled)')
    action?.focus({ preventScroll: true })
    if (!action) return
    const bounds = action.getBoundingClientRect()
    const masthead = document.querySelector<HTMLElement>('.masthead')
    const visibleTop = Math.max(0, masthead?.getBoundingClientRect().bottom ?? 0) + 8
    const reveal =
      bounds.top < visibleTop
        ? bounds.top - visibleTop
        : Math.max(0, bounds.bottom - window.innerHeight)
    if (reveal) window.scrollBy({ top: reveal })
  }, [centreFieldRail, selected?.destination, selectedDetailReady])
  // At compact widths the list gives way to the work, so an owner who chose one
  // far down the page is brought to the workspace's top, where Back to works
  // leads it, just below the sticky masthead (#1284). These selections skip the
  // router's reset to the top, and the work may still be loading, so the reveal
  // repeats until it lands or the work settles. Closing the rail over a selected
  // work reveals it the same way.
  useLayoutEffect(() => {
    if (!revealWorkspace.current || !(search.preview ?? search.work)) return
    const workspace = document.querySelector<HTMLElement>('.library-workspace')
    if (!workspace || !compactWidth()) {
      revealWorkspace.current = false
      return
    }
    const masthead = document.querySelector<HTMLElement>('.masthead')?.getBoundingClientRect()
    const top =
      window.scrollY + workspace.getBoundingClientRect().top - Math.max(0, masthead?.bottom ?? 0)
    window.scrollTo({ top })
    if (Math.abs(window.scrollY - top) <= 1 || selectedDetailReady || selectedReadError)
      revealWorkspace.current = false
  }, [search.attention, search.preview, search.work, selectedDetailReady, selectedReadError])
  useLayoutEffect(() => {
    if (advanceScroll.current === null) return
    const top = advanceScroll.current
    advanceScroll.current = null
    window.scrollTo({ top })
  }, [selected?.destination])
  useEffect(() => {
    if (automaticPanelVisible) keepManualHandling.current?.focus()
  }, [automaticPanelVisible, automaticResult])
  useEffect(() => {
    if (automaticPanel !== null || !restoreAutomaticTrigger.current) return
    restoreAutomaticTrigger.current = false
    automaticTrigger.current?.focus()
  }, [automaticPanel])
  const attentionFocus = useAttentionRail(search.attention, {
    rail: attentionRail,
    heading: attentionHeading,
    toggle: attentionToggle,
  })
  useEffect(() => {
    if (
      complete?.id === selectedReviewCaseId &&
      !nextWork &&
      attention.hasNextPage &&
      !attention.isFetchingNextPage &&
      !attention.isFetchNextPageError
    )
      void attention.fetchNextPage()
  }, [attention, complete, nextWork, selectedReviewCaseId])
  useEffect(() => {
    if (typeof window.matchMedia !== 'function') return
    const media = window.matchMedia('(min-width: 761px)')
    const selectWideDefault = () => {
      if (
        !media.matches ||
        searchPending ||
        currentSearch.current.preview ||
        currentSearch.current.work ||
        currentSearch.current.preparation
      )
        return
      const first =
        currentWorks.find((work) => work.reviewCaseId && work.attentionCount > 0) ??
        currentWorks.find((work) => work.reviewCaseId)
      const reviewCaseId = first?.reviewCaseId
      if (reviewCaseId)
        void navigate({
          replace: true,
          search: (current) =>
            current.preview || current.work || current.preparation
              ? current
              : { ...current, preview: reviewCaseId },
        })
    }
    selectWideDefault()
    media.addEventListener('change', selectWideDefault)
    return () => media.removeEventListener('change', selectWideDefault)
  }, [currentWorks, navigate, searchPending])

  const selectCase = (
    reviewCaseId: string,
    field?: string,
    attentionOrigin?: { cursor: string | undefined },
  ) => {
    workSelectionGeneration.current += 1
    if (reviewCaseId !== selectedReviewCaseId)
      setExpandedChoiceState({ work: reviewCaseId, keys: new Set() })
    if (complete?.id !== reviewCaseId) {
      setComplete(null)
      setCompletionInspection(null)
    }
    setOpenedWork(null)
    void navigate({
      resetScroll: !revealWorkspace.current && !(field && reviewCaseId === selectedReviewCaseId),
      search: (current) => {
        const next = { ...current, preview: reviewCaseId }
        delete next.work
        delete next.preparation
        if (attentionOrigin) {
          if (attentionOrigin.cursor) next.attentionCursor = attentionOrigin.cursor
          else delete next.attentionCursor
        }
        if (field) next.field = field
        else delete next.field
        return next
      },
    })
  }
  const select = async (work: LibraryWorkView) => {
    // A retained row may not be on the page the URL now names.
    if (retained) return
    revealWorkspace.current = compactWidth()
    if (work.reviewCaseId) {
      selectCase(work.reviewCaseId)
      return
    }
    setComplete(null)
    setCompletionInspection(null)
    const generation = ++workSelectionGeneration.current
    await navigate({
      resetScroll: !revealWorkspace.current,
      search: (current) => {
        const next = { ...current, work: work.workId }
        delete next.preview
        delete next.field
        delete next.preparation
        return next
      },
    })
    if (generation !== workSelectionGeneration.current) return
    setOpenedWork(work)
  }
  // Progressive filtering keeps the typed text local so each keystroke paints.
  // A short bounded delay avoids loading an obsolete clear before the next
  // typed value; the URL remains the owner after the committed replacement.
  const scheduleSearchNavigation = (delay = searchNavigationDelay) => {
    if (searchCommitTimer.current !== null) window.clearTimeout(searchCommitTimer.current)
    searchCommitTimer.current = window.setTimeout(() => {
      searchCommitTimer.current = null
      if (searchNavigation.current !== null || pendingSearchQuery.current === null) return
      const generation = searchGeneration.current
      const drain = async () => {
        try {
          while (generation === searchGeneration.current && pendingSearchQuery.current !== null) {
            const pending = pendingSearchQuery.current
            pendingSearchQuery.current = null
            await navigate({
              replace: pending.attention === undefined,
              resetScroll: !revealWorkspace.current,
              search: (current) => {
                if (generation !== searchGeneration.current) return current
                const next = { ...current }
                if (pending.q !== null) {
                  delete next.cursor
                  delete next.attentionCursor
                  if (pending.q) next.q = pending.q
                  else delete next.q
                }
                if (pending.attention === true) next.attention = 'open'
                else if (pending.attention === false) delete next.attention
                expectedSearchIdentity.current = librarySearchIdentity(next)
                return next
              },
            })
          }
        } catch {
          // A later owner edit may start a fresh drain; a failed read navigation
          // is never retried without that new intent.
        } finally {
          searchNavigation.current = null
          if (generation === searchGeneration.current) {
            expectedSearchIdentity.current = null
            if (pendingSearchQuery.current !== null) scheduleSearchNavigation()
            else setSearchPending(false)
          } else if (pendingSearchQuery.current !== null) {
            scheduleSearchNavigation()
          }
        }
      }
      searchNavigation.current = drain()
    }, delay)
  }
  const prefetchTimer = useRef<number | null>(null)
  useEffect(
    () => () => {
      if (prefetchTimer.current !== null) window.clearTimeout(prefetchTimer.current)
    },
    [],
  )
  const prefetchOnIntent = <TData, TKey extends QueryKey>(
    options: QueryExecuteOptions<TData, Error, TData, TData, TKey>,
  ) => {
    if (prefetchTimer.current !== null) window.clearTimeout(prefetchTimer.current)
    prefetchTimer.current = window.setTimeout(() => {
      prefetchTimer.current = null
      prefetchBackgroundQuery(queryClient, options)
    }, prefetchIntentDelay)
  }
  const pageIntent = (cursor: string | undefined) => {
    if (cursor && !retained) prefetchOnIntent(libraryQuery(http, { ...search, cursor }))
  }
  const workIntent = (work: LibraryWorkView) => {
    if (work.reviewCaseId && !retained) prefetchOnIntent(detailQuery(http, work.reviewCaseId))
  }
  const lensIntent = (lens: LibrarySearch['lens']) =>
    prefetchOnIntent(libraryQuery(http, { lens, ...(search.q ? { q: search.q } : {}) }))
  const runSearch = (value: string) => {
    const q = value.trim()
    setTypedQuery({ echo: q, text: value })
    setSearchPending(true)
    pendingSearchQuery.current = { ...pendingSearchQuery.current, q }
    scheduleSearchNavigation()
  }
  const closeAttention = () => {
    attentionFocus.returnFocus(Boolean(search.preview ?? search.work) && narrowWidth())
    changeAttention(false)
  }
  const changeAttention = (open: boolean) => {
    // Only a narrow rail covers the work; from 761px it overlays the page,
    // which stays where it was with the toggle in view.
    if (!open) {
      revealWorkspace.current = Boolean(search.preview ?? search.work) && narrowWidth()
      attentionFocus.cancelOpeningReveal()
    }
    pendingSearchQuery.current = {
      q: searchPending ? queryText.trim() : null,
      attention: open,
    }
    scheduleSearchNavigation(0)
  }
  const submitSearch = (event: SyntheticEvent<HTMLFormElement, SubmitEvent>) => {
    event.preventDefault()
    runSearch(queryText)
  }
  // A retained page's cursors belong to the key the URL just left.
  const movePage = (cursor: string | undefined) => {
    if (retained) return
    void navigate({
      search: (current) => {
        const next = { ...current }
        if (cursor) next.cursor = cursor
        else delete next.cursor
        return next
      },
    })
  }
  const choose = (
    field: ReviewField,
    outcome: DecisionCommand['outcome'],
    selectedValue: DecisionCommand['selectedValue'] = null,
    authoredDescription?: DecisionCommand['authoredDescription'],
  ) => {
    const idempotencyKey = requestKey()
    setOwnerCommand(null)
    setOwnerNotice(null)
    setFrozenFieldOrders((current) =>
      current[selectedReviewCaseId] === undefined
        ? { ...current, [selectedReviewCaseId]: currentActionableDestinations }
        : current,
    )
    controller.submit({
      destination: field.destination,
      outcome,
      proposalId: field.proposalId,
      selectedValue,
      targetFingerprint: field.targetFingerprint,
      idempotencyKey,
      ...(field.coverConsideration
        ? {
            considerationId: field.coverConsideration.id,
            coverConfirmation: {
              expected_selection_id: field.currentCoverSelectionId ?? null,
              confirm_unverified_match:
                outcome === 'accept' &&
                field.coverConsideration.candidates.some(
                  (candidate) =>
                    candidate.artifactId === selectedValue?.value &&
                    candidate.matchState === 'match_not_confirmed',
                ),
            },
          }
        : {}),
      ...(authoredDescription ? { authoredDescription } : {}),
    })
    return idempotencyKey
  }
  const closeAutomaticPanel = () => {
    const closingKey = automaticPanel
    restoreAutomaticTrigger.current = true
    setAutomaticPanel(null)
    if (closingKey && ['error', 'refreshed'].includes(automaticResults[closingKey] ?? ''))
      setAutomaticResults((current) => {
        return Object.fromEntries(Object.entries(current).filter(([key]) => key !== closingKey))
      })
  }
  const retryNextWork = () => {
    if (attention.isFetchNextPageError) void attention.fetchNextPage()
    else void attention.refetch()
  }
  const clearSelection = () => {
    workSelectionGeneration.current += 1
    revealWorkspace.current = false
    setComplete(null)
    setCompletionInspection(null)
    setOpenedWork(null)
    void navigate({
      search: (current) => {
        const next = { ...current }
        delete next.preview
        delete next.work
        delete next.field
        delete next.attention
        delete next.preparation
        return next
      },
    })
  }
  const selectFromQueue = (
    reviewCaseId: string,
    field: string,
    attentionCursor: string | undefined,
  ) => {
    if (attention.isPlaceholderData) return
    revealWorkspace.current = compactWidth()
    revealQueueChoice.current = true
    workSelectionGeneration.current += 1
    if (reviewCaseId !== selectedReviewCaseId)
      setExpandedChoiceState({ work: reviewCaseId, keys: new Set() })
    setComplete(null)
    setCompletionInspection(null)
    setOpenedWork(null)
    void navigate({
      resetScroll: !revealWorkspace.current,
      search: (current) => {
        const next = { ...current, preview: reviewCaseId, field }
        if (attentionCursor) next.attentionCursor = attentionCursor
        else delete next.attentionCursor
        delete next.work
        delete next.attention
        delete next.preparation
        return next
      },
    })
  }
  const expandedChoices = {
    has: (key: string) =>
      expandedChoiceState.work === selectedReviewCaseId && expandedChoiceState.keys.has(key),
  }
  const toggleChoiceEvidence = (key: string) =>
    setExpandedChoiceState((current) => {
      const next = current.work === selectedReviewCaseId ? new Set(current.keys) : new Set<string>()
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return { work: selectedReviewCaseId, keys: next }
    })

  const effect = selected ? effectSummary(selected) : undefined
  const selectedRecorded =
    selected !== undefined &&
    (selected.humanDecision !== null ||
      selected.automaticDecision !== null ||
      (selected.destination === 'cover' && Boolean(selected.currentCoverSelectionId)) ||
      Boolean(
        selected.descriptionAdvice &&
        (selected.descriptionAdvice.selected_advice?.current === true ||
          selected.descriptionAdvice.application_outcome !== null) &&
        selected.descriptionAdvice.selection,
      ))
  const selectedActionable = selected !== undefined && actionable(selected)
  const selectedRevisitable =
    selected !== undefined &&
    selectedRecorded &&
    selected.editable &&
    (selected.decisionActions.length > 0 ||
      Boolean(selected.ownerControl?.entry ?? selected.ownerControl?.subset))
  const selectedOwnerEntry = Boolean(
    selected?.ownerControl?.entry ?? selected?.ownerControl?.subset,
  )
  const ownerKey = selected ? `${selectedReviewCaseId}:${selected.destination}` : null
  const ownerStatusShown = ownerKey !== null && ownerCommand === ownerKey
  const selectedOptional =
    selected !== undefined && !selectedActionable && !selectedRecorded && optionalChoice(selected)
  const selectedAdvice = selected?.destination === 'description' ? shownAdvice(selected) : null
  const selectedChoiceMode =
    selectedActionable || selectedRevisitable || selectedOptional || Boolean(selectedAdvice)
  const selectedCoverPreparation =
    selected?.destination === 'cover' &&
    selected.coverConsideration?.current !== true &&
    selected.needsAttention
  const selectedCoverInspection =
    selected?.destination === 'cover' &&
    (selected.coverConsideration !== null || selectedCoverPreparation)
  const selectedProgress = selected ? controller.phase(selected.destination) : null
  // Attention membership comes from the authoritative exact case. A pending
  // or failed local command has not yet changed it.
  const remainingFields = attentionFields.length
  const railPosition =
    complete?.id === selectedReviewCaseId && completionInspection === null
      ? `${String(complete.fields.length)} recorded · work complete`
      : selected
        ? `${String(remainingFields)} remaining · field ${String(
            renderedRailFields.indexOf(selected) + 1,
          )} of ${String(renderedRailFields.length)}`
        : `${String(remainingFields)} remaining · no field selected`
  // The one-tap teaching line is for someone who has not yet seen what a tap
  // does. It says so on the first field only, and gives way to what is actually
  // happening once they have chosen.
  // An owner command says what it did in its own status, once (S4).
  const choiceProgress =
    selectedProgress && ownerOutcome(selectedProgress.command.outcome) ? null : selectedProgress
  const teachingLine =
    choiceProgress?.phase === 'recording'
      ? 'Recording choice…'
      : choiceProgress?.phase === 'confirmed' ||
          (!selectedProgress && selected && authoredOwnerRecorded(selected))
        ? 'Choice recorded.'
        : selectedActionable && frozenDestinations.indexOf(selected.destination) === 0
          ? 'One tap records and opens the next field'
          : ''
  const writesActive = detail.data?.writesConsented === true
  // The direct choices in reading order, so the radio group's one Tab stop and
  // each position come from a single list (#1290).
  type DirectChoice = Omit<Parameters<typeof MetadataChoice>[0], 'radio'>
  const currentChoice: DirectChoice | null = selected?.decisionActions.includes('keep_current')
    ? {
        context: choiceContext(selected, null),
        current: true,
        disabled: false,
        expanded: expandedChoices.has(`${selected.destination}:current`),
        label: 'Current value',
        longDescription: isLongDescription(selected.destination, selected.currentValue),
        onChoose: () => choose(selected, 'keep_current'),
        recording:
          commandSelected(
            selectedProgress?.command ?? null,
            selected.destination,
            'keep_current',
          ) && selectedProgress?.phase === 'recording',
        selected: choiceSelected(selected, selectedProgress, 'keep_current'),
        toggle: () => toggleChoiceEvidence(`${selected.destination}:current`),
        value: valueText(selected.destination, selected.currentValue),
      }
    : null
  const proposedChoices: (DirectChoice & { id: string })[] =
    selected && selected.decisionActions.includes('accept') && !selected.preservationOnly
      ? selected.proposedValues.map((value, index) => {
          const context = choiceContext(
            selected,
            selected.choiceContexts.filter((item) => item.selection === 'proposed')[index]
              ?.alternativeId ?? null,
          )
          const key = `${selected.destination}:${context?.alternativeId ?? String(index)}`
          return {
            id: key,
            context,
            current: false,
            disabled: false,
            // A proposed card is named by its value, never "Alternative N" (#1519).
            evidenceName: isLongDescription(selected.destination, displayValue(value.value))
              ? `Evidence for proposed description ${String(index + 1)}`
              : `Evidence for proposed value: ${valueText(selected.destination, displayValue(value.value))}`,
            expanded: expandedChoices.has(key),
            label: '',
            longDescription: isLongDescription(selected.destination, displayValue(value.value)),
            onChoose: () => choose(selected, 'accept', value),
            recording:
              commandSelected(
                selectedProgress?.command ?? null,
                selected.destination,
                'accept',
                value,
              ) && selectedProgress?.phase === 'recording',
            selected: choiceSelected(selected, selectedProgress, 'accept', value),
            toggle: () => toggleChoiceEvidence(key),
            value: valueText(selected.destination, displayValue(value.value)),
          }
        })
      : []
  const retainedAdviceChoice: (DirectChoice & { id: string })[] =
    selectedAdvice?.selected_advice &&
    selectedAdvice.selected_advice.candidate.candidate_id !== selectedAdvice.candidate?.candidate_id
      ? [
          {
            id: 'advice:retained',
            advice: selectedAdvice,
            retainedAdvice: true,
            context: undefined,
            current: false,
            disabled: true,
            expanded: false,
            label: 'Advised · Authored content · Recorded choice',
            longDescription: isLongDescription(
              'description',
              selectedAdvice.selected_advice.candidate.output.description,
            ),
            onChoose: () => undefined,
            recording: false,
            selected: Boolean(
              selectedAdvice.selected_advice.current || selectedAdvice.application_outcome,
            ),
            toggle: () => undefined,
            value: selectedAdvice.selected_advice.candidate.output.description,
          },
        ]
      : []
  const adviceCandidate = selectedAdvice?.candidate
  const adviceChoice: (DirectChoice & { id: string })[] =
    selected && selectedAdvice && adviceCandidate
      ? [
          {
            id: 'advice:candidate',
            advice: selectedAdvice,
            retainedAdvice: selectedAdvice.selection?.candidate_id === adviceCandidate.candidate_id,
            context: undefined,
            current: false,
            disabled:
              !selected.editable ||
              !selectedAdvice.current ||
              controller.recording ||
              selectedAdvice.state === 'requested' ||
              selectedAdvice.state === 'running',
            expanded: false,
            label: 'Advised · Authored content',
            longDescription: isLongDescription('description', adviceCandidate.output.description),
            onChoose: () => {
              if (!selectedAdvice.current || !selected.editable) return
              choose(
                selected,
                'accept',
                { kind: 'text', value: adviceCandidate.output.description },
                { candidateId: adviceCandidate.candidate_id, binding: selectedAdvice.binding },
              )
            },
            recording:
              selectedProgress?.command.authoredDescription?.candidateId ===
                adviceCandidate.candidate_id && selectedProgress.phase === 'recording',
            selected:
              selectedProgress && selectedProgress.phase !== 'failed'
                ? selectedProgress.command.authoredDescription?.candidateId ===
                  adviceCandidate.candidate_id
                : Boolean(
                    (selectedAdvice.selected_advice?.current === true ||
                      selectedAdvice.application_outcome) &&
                    selectedAdvice.selection?.candidate_id === adviceCandidate.candidate_id,
                  ),
            toggle: () => undefined,
            value: adviceCandidate.output.description,
          },
        ]
      : []
  const newChoices = [...proposedChoices, ...retainedAdviceChoice, ...adviceChoice]
  const directChoices = choiceRadios(
    [...(currentChoice ? [{ ...currentChoice, id: 'current' }] : []), ...newChoices],
    (choice) => ({ checked: choice.selected, disabled: choice.disabled }),
  )

  return (
    <div className="library-page variant-m-workspace">
      <h1 className="sr-only">Library</h1>
      {/* One toolbar in both states, so selecting a work never moves the workspace. */}
      <header
        className="library-header"
        aria-label="Library controls"
        tabIndex={-1}
        {...(!hasSelection ? { 'data-route-focus': 'true' } : {})}
      >
        <button
          ref={attentionToggle}
          className="attention-toggle"
          type="button"
          aria-expanded={search.attention === 'open'}
          onClick={() => (search.attention === 'open' ? closeAttention() : changeAttention(true))}
        >
          Attention
          {summaryAttention !== undefined || !summary.isError ? (
            <>
              {' '}
              <strong>{summaryAttention === undefined ? '…' : workCount(summaryAttention)}</strong>
            </>
          ) : null}
        </button>
        <WriteReadyNotice />
      </header>
      <PreparationNotice routeIdentity={routeIdentity} />
      <ProviderThrottleNotice />
      <div
        className={`library-workspace${hasSelection ? ' has-selection' : ''}${search.attention === 'open' ? ' attention-open' : ''}`}
      >
        {hasSelection && search.attention !== 'open' ? (
          <button className="compact-workspace-back" type="button" onClick={clearSelection}>
            Back to works
          </button>
        ) : null}
        <section className="library-works" aria-labelledby="library-works-title">
          {presentation ? (
            <PreparationSummary
              presentation={presentation}
              reconnecting={preparationState.reconnecting}
              recoveryRequired={preparationState.data?.recovery_required ?? false}
              selected={preparationSelected}
              onSelect={() =>
                void navigate({
                  search: (current) => {
                    const next = { ...current, preparation: presentation.run_id }
                    delete next.attention
                    return next
                  },
                })
              }
            />
          ) : null}
          <div className="library-works-heading">
            <h2 id="library-works-title">Works</h2>
            <span role="status" aria-live="polite" aria-busy={retained || undefined}>
              {data ? (
                search.q ? (
                  <>
                    {lensTotal === undefined
                      ? `Found ${workCount(data.counts[lensCount])}`
                      : `Found ${String(data.counts[lensCount])} of ${workCount(lensTotal)}`}
                    {lensWords === undefined ? null : (
                      <span className="sr-only">{` ${lensWords}`}</span>
                    )}
                  </>
                ) : (
                  workCount(data.counts.all) +
                  // The works outside both decision lenses, named beside the
                  // total in the masthead's words (#1407, #1413).
                  // One placeholder while the summary loads, so the line
                  // does not wrap and then jump when it arrives.
                  (summaryNotMatched === undefined || summaryFailed === undefined
                    ? summary.isError
                      ? ''
                      : ' · …'
                    : (
                        [
                          [summaryNotMatched, 'not matched'],
                          [summaryFailed, 'could not finish'],
                        ] as const
                      )
                        .map(([count, words]) =>
                          count === 0 ? '' : ` · ${String(count)} ${words}`,
                        )
                        .join(''))
                )
              ) : failed ? (
                'Count unavailable'
              ) : (
                'Loading count…'
              )}
            </span>
          </div>
          <nav className="lenses" aria-label="Library lenses" ref={lensRow}>
            {(
              [
                'all',
                'needs_attention',
                'no_attention_needed',
                'not_matched',
                'preparation_failed',
              ] as const
            ).map((lens) => (
              <LensLink
                key={lens}
                to="/library"
                search={{
                  lens,
                  ...(search.q ? { q: search.q } : {}),
                  ...(search.preview ? { preview: search.preview } : {}),
                  ...(search.work ? { work: search.work } : {}),
                  ...(search.field ? { field: search.field } : {}),
                  ...(search.preparation ? { preparation: search.preparation } : {}),
                }}
                preload="intent"
                onPointerEnter={() => lensIntent(lens)}
                onFocus={() => lensIntent(lens)}
                activeProps={{ 'aria-current': 'page' }}
              >
                {lensLabels[lens]}
              </LensLink>
            ))}
          </nav>
          <form className="search library-search" role="search" onSubmit={submitSearch}>
            <label className="sr-only" htmlFor="library-query">
              Search title, author, identifier, library, or format
            </label>
            <div>
              <input
                ref={searchInput}
                id="library-query"
                name="q"
                type="search"
                maxLength={128}
                value={queryText}
                placeholder="Search works…"
                onChange={(event) => runSearch(event.target.value)}
              />
              {queryText ? (
                <button
                  type="button"
                  aria-label="Clear search text"
                  onClick={() => {
                    runSearch('')
                    searchInput.current?.focus()
                  }}
                >
                  <span aria-hidden="true">×</span>
                </button>
              ) : null}
            </div>
          </form>
          <div className="library-work-list" aria-busy={retained || undefined}>
            {!data ? (
              <div className="empty-copy" aria-busy={!failed}>
                {failed ? (
                  <>
                    <p role="alert">Works are unavailable.</p>
                    <button type="button" onClick={retry}>
                      Try again
                    </button>
                  </>
                ) : (
                  <p role="status">Loading works…</p>
                )}
              </div>
            ) : works.length === 0 ? (
              <div className="empty-copy">
                <p>No exact-current works match this search and lens.</p>
                {search.q ? (
                  <button
                    type="button"
                    onClick={() => {
                      runSearch('')
                      searchInput.current?.focus()
                    }}
                  >
                    Clear search
                  </button>
                ) : null}
              </div>
            ) : (
              works.map((work, index) => {
                const selectedWorkRow =
                  !preparationSelected &&
                  (selectedWork
                    ? selectedWork.workId === work.workId
                    : work.reviewCaseId !== undefined && search.preview === work.reviewCaseId)
                const needsAttention = work.attentionCount > 0
                // A published report is not a failure. The row for a work
                // that stopped before producing one has nothing to open and
                // stays an article; a work whose evidence is addressable is a
                // successful entry the owner can read.
                const evidence = reportEvidenceUrl(work) !== null
                // Retained evidence is not by itself a clean outcome. A
                // completed preparation reports no terminal failure at all, so
                // its absence is what makes the recorded report the outcome; a
                // later real failure over the same retained report is still a
                // failure the owner has to see.
                const recorded = evidence && work.preparation === null
                // A terminal preparation failure outranks a retained report's
                // not-matched reason: it is the failure the owner must see (#1407).
                const status =
                  (work.summary && !work.preparation
                    ? notMatchedReasons[work.summary.state]
                    : undefined) ??
                  (recorded
                    ? 'Recorded evidence available'
                    : work.preparation
                      ? 'Preparation failed safely'
                      : needsAttention
                        ? work.attentionCount === 1
                          ? '1 field needs attention'
                          : `${String(work.attentionCount)} fields need attention`
                        : 'No attention needed')
                const terminalFailure = work.preparation !== null && !work.reviewCaseId && !evidence
                const group = providerGroup(work)
                const previousWork = works[index - 1]
                // Grouping follows the lens these rows were read for, not a retained URL's.
                const showGroup =
                  data.lens !== 'no_attention_needed' &&
                  data.lens !== 'not_matched' &&
                  data.lens !== 'preparation_failed' &&
                  (!previousWork || providerGroup(previousWork) !== group)
                const summary = providerSummary(work.preparationOutcomes)
                const summaryId = summary ? `provider-summary-${work.id}` : undefined
                const preparation = work.preparation
                const preparationId = preparation ? `preparation-${work.id}` : undefined
                const row = (
                  <>
                    <BookMark title={work.title} cover={work.cover} />
                    <span className="library-work-copy">
                      <strong className="library-work-title">{work.title}</strong>
                      <span className="library-work-author">{authorLine(work)}</span>
                      <span className="library-work-origin">
                        {work.libraryDisplayName ?? work.library} ·{' '}
                        {mediaKindLabels[work.mediaKind]}
                      </span>
                      {summary ? (
                        <span id={summaryId} className="library-provider-summary">
                          {summary}
                        </span>
                      ) : null}
                      <span className="library-work-status">
                        <StatusPill
                          tone={
                            !recorded && (needsAttention || work.preparation) ? 'notice' : 'accent'
                          }
                        >
                          {status}
                        </StatusPill>
                        {preparation ? (
                          <span id={preparationId} className="terminal-preparation-copy">
                            {preparation.stage}: {preparation.reason} {preparation.recovery}{' '}
                            Reference: <code>{preparation.diagnosticReference}</code>
                          </span>
                        ) : null}
                      </span>
                    </span>
                  </>
                )
                return (
                  <Fragment key={work.id}>
                    {showGroup ? (
                      <div className="library-provider-group">
                        <h3>{group}</h3>
                        {group === 'Provider evidence complete' ||
                        group === 'Provider evidence limited' ? (
                          <p>
                            {group === 'Provider evidence complete'
                              ? 'Lookups completed or did not apply; a match is not guaranteed.'
                              : 'Some lookups did not supply usable evidence. Recoverable lookups can change this group when new evidence arrives.'}
                          </p>
                        ) : null}
                      </div>
                    ) : null}
                    {terminalFailure ? (
                      <article
                        key={work.id}
                        className="library-work-row is-terminal"
                        aria-label="Terminal preparation failure"
                      >
                        {row}
                        {preparation?.mediaHealth.length ? (
                          <MediaHealth observations={preparation.mediaHealth} />
                        ) : null}
                      </article>
                    ) : (
                      <>
                        <button
                          key={work.id}
                          type="button"
                          className={`library-work-row${selectedWorkRow ? ' active' : ''}`}
                          aria-current={selectedWorkRow ? 'true' : undefined}
                          aria-disabled={retained || undefined}
                          aria-label={`${work.title}. ${authorLine(work)}. ${status}`}
                          aria-describedby={
                            [preparationId, summaryId].filter(Boolean).join(' ') || undefined
                          }
                          onClick={() => void select(work)}
                          onPointerEnter={() => workIntent(work)}
                          onFocus={() => workIntent(work)}
                        >
                          {row}
                        </button>
                        {/* A work whose latest preparation could not read a file
                            still opens what it retained, so the row is a control
                            and the section has to sit beside it rather than
                            inside it: a section nested in a button is not an
                            openable row, and the observation is the one thing
                            the failed attempt proved. */}
                        {preparation?.mediaHealth.length ? (
                          <div className="library-work-row-aside">
                            <MediaHealth observations={preparation.mediaHealth} />
                          </div>
                        ) : null}
                      </>
                    )}
                  </Fragment>
                )
              })
            )}
          </div>
          {data && (data.previousCursor || data.nextCursor) ? (
            // Only the moves that exist are offered; with one page there is no pager.
            <div className="library-pager" aria-busy={retained || undefined}>
              {data.previousCursor ? (
                <button
                  type="button"
                  className="library-pager-previous"
                  aria-disabled={retained || undefined}
                  onClick={() => movePage(data.previousCursor)}
                  onPointerEnter={() => pageIntent(data.previousCursor)}
                  onFocus={() => pageIntent(data.previousCursor)}
                >
                  ← <span className="library-pager-word">Previous</span>
                </button>
              ) : null}
              <span>Page {String(data.page)}</span>
              {data.nextCursor ? (
                <button
                  type="button"
                  className="library-pager-next"
                  aria-disabled={retained || undefined}
                  onClick={() => movePage(data.nextCursor)}
                  onPointerEnter={() => pageIntent(data.nextCursor)}
                  onFocus={() => pageIntent(data.nextCursor)}
                >
                  <span className="library-pager-word">Next</span> →
                </button>
              ) : null}
            </div>
          ) : null}
        </section>
        <section
          className="workspace-detail"
          // Preparation states its own progress in one status line; the whole
          // detail would otherwise be read again on every poll (#1288).
          aria-live={preparationSelected ? undefined : 'polite'}
          tabIndex={-1}
          {...(!selectedWork &&
          search.preview &&
          search.attention !== 'open' &&
          (!displayedIdentity || selectedReadPending || selectedReadError)
            ? { 'data-route-focus': 'true' }
            : {})}
        >
          {preparationSelected ? (
            <PreparationDetail
              presentation={presentation}
              reconnecting={preparationState.reconnecting}
              recoveryRequired={preparationState.data?.recovery_required ?? false}
              animateChange={animateChange}
              dismissing={preparationDismissal.dismissing}
              uncertain={preparationDismissal.uncertain(presentation.run_id)}
              onDismiss={() => preparationDismissal.dismiss(presentation.run_id)}
            />
          ) : selectedWork ? (
            <WorkSummary work={selectedWork} />
          ) : !search.preview ? (
            <div className="library-selection">
              <h2>Choose a work</h2>
              <p>
                Its current values and field position stay here while you move through attention.
              </p>
            </div>
          ) : !displayedIdentity && staleCoverNotice ? (
            staleCoverNotice
          ) : !displayedIdentity ? (
            identityPending || leaving || successorPending === search.preview ? (
              <p role="status">Loading selected work…</p>
            ) : identityError ? (
              <p role="alert">
                Selected work could not be loaded.{' '}
                <button
                  ref={selectedDetailRetryButton}
                  onClick={() => {
                    selectedDetailRetry.current = search.preview ?? null
                    retry()
                    void attention.refetch()
                    // The case read names the work too, so it is read again (#1365).
                    if (detail.isError) void detail.refetch()
                  }}
                >
                  Try again
                </button>
              </p>
            ) : (
              <div className="library-selection">
                <h2
                  tabIndex={-1}
                  // Reached when the case under an open owner form could not
                  // be followed: focus lands here, never on the page body.
                  ref={(heading) => {
                    if (heading && document.activeElement === document.body)
                      heading.focus({ preventScroll: true })
                  }}
                >
                  Selected work
                </h2>
                <p>Readable work identity is not available on this Library page.</p>
                <p role="status">This work is not available in the current Library result.</p>
              </div>
            )
          ) : detail.isPending || (detail.isError && leaving) ? (
            <p role="status">Loading selected work…</p>
          ) : detail.isError && staleCoverNotice ? (
            staleCoverNotice
          ) : detail.isError ? (
            <p role="alert">
              {controller.messageFor(search.field ?? selected?.destination ?? '') ??
                'Selected work could not be loaded.'}{' '}
              <button
                ref={selectedDetailRetryButton}
                onClick={() => {
                  selectedDetailRetry.current = search.preview ?? null
                  void detail.refetch()
                }}
              >
                Try again
              </button>{' '}
            </p>
          ) : !detail.data.current ? (
            <div className="library-selection">
              <p>Case superseded</p>
              <h2>{displayedIdentity.title}</h2>
              {staleCoverNotice ?? (
                <p role="alert">
                  This review case is no longer current. Reopen its current replacement from
                  Library.
                </p>
              )}
            </div>
          ) : (
            <>
              <header
                className="selected-work-header"
                tabIndex={-1}
                {...(search.preview && search.attention !== 'open'
                  ? { 'data-route-focus': 'true' }
                  : {})}
              >
                <BookMark
                  title={displayedIdentity.title}
                  cover={
                    currentWorks.find((work) => work.reviewCaseId === selectedReviewCaseId)?.cover
                  }
                />
                <div>
                  <h2>{displayedIdentity.title}</h2>
                  <p>
                    {displayedIdentity.authors.length
                      ? displayedIdentity.authors.join(', ')
                      : 'Author details unavailable'}{' '}
                    · {displayedIdentity.libraryDisplayName ?? displayedIdentity.library} ·{' '}
                    {mediaKindLabels[displayedIdentity.mediaKind]}
                  </p>
                </div>
                <dl className="selected-work-status">
                  <div>
                    <dt>Needs review</dt>
                    <dd>{String(attentionFields.length)}</dd>
                  </div>
                  <div>
                    <dt>Resolved automatically</dt>
                    <dd>
                      {String(
                        detail.data.fields.filter(
                          (field) =>
                            field.humanDecision === null &&
                            field.automaticDecision !== null &&
                            !field.manualHold,
                        ).length,
                      )}
                    </dd>
                  </div>
                </dl>
                <nav className="selected-work-diagnostics" aria-label="Read-only work diagnostics">
                  <Link
                    params={{ reviewCaseId: detail.data.id }}
                    search={normalizeDetailSearch({
                      ...search,
                      subview: 'evidence',
                      field: selected?.destination,
                    })}
                    to="/review/$reviewCaseId"
                  >
                    Evidence
                  </Link>
                  <Link
                    params={{ reviewCaseId: detail.data.id }}
                    search={normalizeDetailSearch({
                      ...search,
                      subview: 'history',
                      field: selected?.destination,
                    })}
                    to="/review/$reviewCaseId"
                  >
                    History
                  </Link>
                </nav>
              </header>
              {detail.data.comparisonNotices.length ? (
                <aside className="comparison-notice" aria-label="Provider comparison condition">
                  {detail.data.comparisonNotices.map((notice) => (
                    <p key={notice}>{notice}</p>
                  ))}
                </aside>
              ) : null}
              <section className="field-map" aria-label={`Fields in ${displayedIdentity.title}`}>
                <header>
                  <strong>{railPosition}</strong>
                </header>
                <nav ref={fieldRail} className="field-filmstrip" aria-label="Review fields">
                  {renderedRailFields.map((field) => (
                    <button
                      key={field.destination}
                      ref={
                        revealFocusDestination === field.destination
                          ? (button) => {
                              if (button) {
                                setRevealFocusDestination(null)
                                button.focus()
                                const rail = fieldRail.current
                                if (rail) centreFieldRail(rail, button)
                              }
                            }
                          : selected?.destination === field.destination
                            ? activeFieldButton
                            : undefined
                      }
                      aria-current={selected?.destination === field.destination}
                      className={!actionable(field) ? 'is-recorded' : undefined}
                      onClick={() => {
                        if (complete?.id === selectedReviewCaseId)
                          setCompletionInspection(field.destination)
                        selectCase(selectedReviewCaseId, field.destination)
                      }}
                    >
                      <strong>{destinationLabel(field.destination)}</strong>{' '}
                      <span className={`field-status is-${fieldStatus(field)[0]}`}>
                        {fieldStatus(field)[1]}
                      </span>
                    </button>
                  ))}
                  {omittedFields.length ? (
                    <button
                      type="button"
                      className="field-more"
                      onClick={() => {
                        setRevealFocusDestination(omittedFields[0]?.destination ?? null)
                        setShownEmptyFields((current) => ({
                          ...current,
                          [selectedReviewCaseId]: true,
                        }))
                      }}
                    >
                      {`Show ${String(omittedFields.length)} more field${omittedFields.length === 1 ? '' : 's'}`}
                    </button>
                  ) : null}
                </nav>
              </section>
              {staleCoverNotice}
              {complete?.id === selectedReviewCaseId && completionInspection === null ? (
                <article
                  className="review-complete"
                  aria-live="polite"
                  tabIndex={-1}
                  ref={completionRegion}
                >
                  <span className="review-complete-mark">✓</span>
                  <div>
                    <h3>Review complete</h3>
                    <span>
                      {String(complete.fields.length)} recorded field outcome
                      {complete.fields.length === 1 ? '' : 's'}
                      {writesActive ? ' — each began applying the moment you decided it.' : '.'}
                    </span>
                  </div>
                  <ol>
                    {complete.fields.map((field) => (
                      <li key={field.destination}>
                        <button
                          type="button"
                          aria-label={
                            field.destination === 'description' && decisionValue(field).length > 240
                              ? `Revisit description: ${completionOutcome(field)}`
                              : undefined
                          }
                          onClick={() => {
                            setCompletionInspection(field.destination)
                            selectCase(selectedReviewCaseId, field.destination)
                          }}
                        >
                          <small>
                            {destinationLabel(field.destination)} · {completionOutcome(field)}
                          </small>
                          <strong>{decisionValue(field)}</strong>
                        </button>
                      </li>
                    ))}
                  </ol>
                  {writesActive ? <p>Every write is logged in Activity.</p> : null}
                  <footer>
                    {nextWork ? (
                      <Button
                        variant="primary"
                        type="button"
                        onClick={() => {
                          setComplete(null)
                          setCompletionInspection(null)
                          selectCase(nextWork.id, nextWork.destination, {
                            cursor: nextWork.attentionCursor,
                          })
                        }}
                      >
                        Review next work →
                      </Button>
                    ) : attention.isPending ||
                      attention.isPlaceholderData ||
                      attention.isFetchingNextPage ? (
                      <span>Looking for the next visible work…</span>
                    ) : nextWorkError ? (
                      <>
                        <span>The next attention work could not be loaded.</span>
                        <Button variant="primary" type="button" onClick={retryNextWork}>
                          Try finding next work again
                        </Button>
                      </>
                    ) : !search.q ? (
                      <span>All visible review work is complete.</span>
                    ) : (
                      <span>
                        All review work matching this search is complete.
                        {summaryAttention === undefined || !data
                          ? null
                          : outsideSearch > 0
                            ? ` ${String(outsideSearch)} more ${outsideSearch === 1 ? 'work needs' : 'works need'} a decision outside this search.`
                            : ' No other work needs a decision.'}
                      </span>
                    )}
                  </footer>
                </article>
              ) : selected ? (
                <article
                  key={selected.destination}
                  className={`decision-canvas${controller.recording ? ' is-recording' : ''}${selectedChoiceMode ? '' : ' is-context-only'}`}
                >
                  {complete?.id === selectedReviewCaseId ? (
                    <button
                      type="button"
                      className="decision-back"
                      onClick={() => setCompletionInspection(null)}
                    >
                      ← Back to completion
                    </button>
                  ) : null}
                  <header>
                    <div>
                      <h3>
                        {selectedActionable
                          ? decisionQuestion(selected.destination)
                          : destinationLabel(selected.destination)}
                      </h3>
                      {selectedActionable ? (
                        // One line under the question: this field's own reason (#1519).
                        <p
                          className={
                            selected.automaticWithheldReason
                              ? 'automatic-withheld-reason'
                              : undefined
                          }
                        >
                          {questionReason(selected, detail.data.target.mediaKind)}
                        </p>
                      ) : selectedRevisitable ? (
                        <p>
                          {selected.humanDecision === null && selected.manualHold
                            ? 'Your earlier decision still holds this field.'
                            : 'The recorded outcome is marked below.'}{' '}
                          You can select another eligible value.
                        </p>
                      ) : selectedOptional ? (
                        <p>{fieldReason(selected)}</p>
                      ) : null}
                      {selected.automaticWithheldReason && !selectedActionable ? (
                        <p className="automatic-withheld-reason">
                          {selected.automaticWithheldReason}
                        </p>
                      ) : null}
                    </div>
                    {selectedActionable ? (
                      <dl>
                        <div>
                          <dt>Queue position</dt>
                          <dd>
                            {activeQueueGroup >= 0
                              ? `Work ${String(activeQueueGroup + 1)} of ${String(attentionGroups.length)}${attention.hasNextPage || search.attentionCursor ? ' loaded' : ''}`
                              : 'Selected work'}
                          </dd>
                        </div>
                        <div>
                          <dt>Choices</dt>
                          <dd>{String(ownerChoiceCount(selected))}</dd>
                        </div>
                      </dl>
                    ) : null}
                  </header>
                  {(selectedChoiceMode || selectedCoverInspection) && !controller.reloadRequired ? (
                    selected.destination === 'cover' ? (
                      <LibraryCoverChoices
                        reviewCaseId={selectedReviewCaseId}
                        mediaKind={detail.data.target.mediaKind}
                        field={selected}
                        disabled={!selectedChoiceMode && !selectedCoverPreparation}
                        expandedEvidence={expandedChoices}
                        progress={selectedProgress}
                        choose={(outcome, value) => choose(selected, outcome, value)}
                        revealEvidence={toggleChoiceEvidence}
                      />
                    ) : (
                      <section
                        className="direct-choice protected-choice"
                        // The radio group inside is named by the question.
                        aria-label={
                          selectedActionable
                            ? undefined
                            : selectedOptional
                              ? `${destinationLabel(selected.destination)} choices`
                              : `${destinationLabel(selected.destination)} recorded choices`
                        }
                      >
                        {selectedRevisitable ? (
                          <>
                            {combinedAutomaticTags(selected, detail.data.target.mediaKind) ||
                            selectedAutomaticLanguageForm(selected) ? (
                              <RecordedOutcome
                                field={selected}
                                mediaKind={detail.data.target.mediaKind}
                                expanded={expandedChoices.has(`${selected.destination}:recorded`)}
                                toggle={() =>
                                  toggleChoiceEvidence(`${selected.destination}:recorded`)
                                }
                              />
                            ) : null}
                            <ProviderConsensusAutomaticStatement field={selected} />
                            <DescriptionAutomaticStatement field={selected} />
                            <SeriesAutomaticStatement field={selected} />
                            <NarratorsAutomaticStatement field={selected} />
                            <AsinAutomaticStatement field={selected} />
                            <VerifiedManifestationIsbnAutomaticStatement field={selected} />
                          </>
                        ) : null}
                        <div
                          className={`direct-choice-grid${selected.preservationOnly ? ' preservation-only' : ''}`}
                          role="radiogroup"
                          aria-label={decisionQuestion(selected.destination)}
                          onKeyDown={moveChoiceFocus}
                        >
                          {directChoices
                            .filter((choice) => choice.current)
                            .map(({ id, ...choice }) => (
                              <MetadataChoice key={id} {...choice} />
                            ))}
                          {(selected.decisionActions.includes('accept') &&
                            !selected.preservationOnly) ||
                          selectedAdvice?.candidate ||
                          selectedAdvice?.selected_advice ? (
                            <div
                              className={`proposed-choice-list${selected.proposedValues.length + (selectedAdvice?.candidate ? 1 : 0) === 1 ? ' has-one-choice' : ''}`}
                            >
                              <header>
                                <strong>Choose a new value</strong>
                                <small
                                  aria-live="polite"
                                  className={teachingLine ? 'is-active' : ''}
                                >
                                  {teachingLine}
                                </small>
                              </header>
                              {directChoices
                                .filter((choice) => !choice.current)
                                .map(({ id, ...choice }) => (
                                  <MetadataChoice key={id} {...choice} />
                                ))}
                            </div>
                          ) : null}
                        </div>
                        {selectedAdvice ? (
                          <DescriptionAdvicePreparation
                            advice={selectedAdvice}
                            reviewCaseId={selectedReviewCaseId}
                            disabled={controller.recording || controller.reloadRequired}
                          />
                        ) : null}
                      </section>
                    )
                  ) : selectedRecorded ? (
                    <RecordedOutcome
                      field={selected}
                      mediaKind={detail.data.target.mediaKind}
                      expanded={expandedChoices.has(`${selected.destination}:recorded`)}
                      toggle={() => toggleChoiceEvidence(`${selected.destination}:recorded`)}
                    />
                  ) : (
                    <>
                      {selectedActionable || !selectedOwnerEntry ? (
                        <p
                          className={selectedActionable ? 'decision-recorded' : 'decision-context'}
                        >
                          {selectedActionable
                            ? `${fieldReason(selected)}${selected.humanDecision || selected.automaticDecision ? ` The recorded value remains ${decisionValue(selected)}.` : ''}`
                            : 'There’s no other value to choose from.'}
                        </p>
                      ) : null}
                      {!selectedActionable ? (
                        <ContextValue
                          field={selected}
                          expanded={expandedChoices.has(`${selected.destination}:context`)}
                          toggle={() => toggleChoiceEvidence(`${selected.destination}:context`)}
                        />
                      ) : null}
                    </>
                  )}
                  {selected.ownerControl && !controller.reloadRequired ? (
                    <OwnerControls
                      key={ownerKey}
                      field={selected}
                      disabled={controller.recording || controller.reloadRequired}
                      progress={selectedProgress}
                      status={
                        ownerNotice?.caseId === selectedReviewCaseId &&
                        ownerNotice.destination === selected.destination
                          ? ownerNotice.text
                          : ownerStatusShown
                            ? controller.statusFor(selected.destination)
                            : ''
                      }
                      failed={controller.decision.isError}
                      submit={(outcome, value) => {
                        const key = choose(selected, outcome, value)
                        setOwnerCommand(ownerKey)
                        return key
                      }}
                    />
                  ) : null}
                  {selected.manualHold ? (
                    <section
                      className="automatic-handling-panel"
                      aria-label="Automatic handling"
                      onKeyDown={(event) => {
                        if (event.key === 'Escape' && !returnAutomatic.isPending) {
                          event.preventDefault()
                          closeAutomaticPanel()
                        }
                      }}
                    >
                      {automaticPanelVisible ? (
                        <>
                          <h4>Automatic handling is paused for this field</h4>
                          <p>
                            The recorded value remains. Fresh checks are requested first; automatic
                            policy may manage later qualified changes.
                          </p>
                          {automaticResult === 'success' ? (
                            <p role="status">
                              Fresh checks requested. Manual handling remains until they complete.
                            </p>
                          ) : automaticResult === 'error' ? (
                            <p role="alert">
                              Fresh checks could not be requested. Manual handling is unchanged.
                            </p>
                          ) : automaticResult === 'refreshed' ? (
                            <p role="alert">{sessionRefreshedCopy}</p>
                          ) : null}
                          <div className="automatic-handling-actions">
                            <button
                              ref={keepManualHandling}
                              type="button"
                              disabled={returnAutomatic.isPending}
                              onClick={closeAutomaticPanel}
                            >
                              Keep manual handling
                            </button>
                            <button
                              type="button"
                              disabled={returnAutomatic.isPending || automaticCompletedForSelected}
                              onClick={() => {
                                if (!selectedAutomaticKey) return
                                returnAutomatic.mutate({
                                  destination: selected.destination,
                                  key: selectedAutomaticKey,
                                })
                              }}
                            >
                              {returnAutomatic.isPending
                                ? 'Requesting fresh checks…'
                                : 'Resume after fresh checks'}
                            </button>
                          </div>
                        </>
                      ) : (
                        <>
                          {selected.humanDecision === null ? (
                            <p>
                              An earlier owner decision is pausing automatic handling until you
                              choose Resume automatic handling.
                            </p>
                          ) : null}
                          <button
                            ref={automaticTrigger}
                            type="button"
                            aria-disabled={automaticCompletedForSelected}
                            onClick={
                              automaticCompletedForSelected
                                ? undefined
                                : () => setAutomaticPanel(selectedAutomaticKey)
                            }
                          >
                            {automaticCompletedForSelected
                              ? 'Fresh checks requested'
                              : 'Resume automatic handling'}
                          </button>
                        </>
                      )}
                    </section>
                  ) : null}
                  {comparisonNotices(selected).length ? (
                    <aside className="comparison-notice" aria-label="Comparison condition">
                      {comparisonNotices(selected).map((notice) => (
                        <p key={notice}>
                          {noticeText(selected, notice, detail.data.target.mediaKind)}
                        </p>
                      ))}
                    </aside>
                  ) : null}
                  <RecordedFieldObservations
                    key={selectedReviewCaseId}
                    reviewCaseId={selectedReviewCaseId}
                    field={selected}
                  />
                  {controller.canRetry && selectedProgress?.phase === 'failed' ? (
                    <button
                      ref={retryButton}
                      className="decision-retry"
                      type="button"
                      onClick={controller.retry}
                    >
                      Try the same decision again
                    </button>
                  ) : null}
                  {selectedChoiceMode &&
                  !ownerStatusShown &&
                  (controller.statusFor(selected.destination) ||
                    controller.messageFor(selected.destination)) ? (
                    <p
                      role={
                        controller.decision.isError || controller.reloadRequired
                          ? 'alert'
                          : 'status'
                      }
                    >
                      {controller.statusFor(selected.destination) ||
                        controller.messageFor(selected.destination)}
                    </p>
                  ) : null}
                  {selectedChoiceMode && effect ? (
                    <aside className="field-apply-context" aria-label="External update status">
                      <span className="activity-state-mark" aria-hidden="true"></span>
                      <span>
                        <small>External library</small>
                        <strong>{effect.state}</strong>
                        <em>{effect.detail}</em>
                      </span>
                    </aside>
                  ) : null}
                  {controller.reloadRequired ? (
                    <button
                      type="button"
                      disabled={detail.isFetching}
                      onClick={() => void controller.settle()}
                    >
                      {detail.isFetching ? 'Reloading current case…' : 'Reload current case'}
                    </button>
                  ) : null}
                </article>
              ) : (
                <p>This work has no fields requiring a decision.</p>
              )}
              {detail.data.recordingIdentityBasis || detail.data.audiobookReviewScope === 'work' ? (
                <section aria-label="Whole-work context">
                  <h3>Whole-work context</h3>
                  <RecordingIdentityBasis
                    value={detail.data.recordingIdentityBasis}
                    scope={detail.data.audiobookReviewScope}
                    offered={detail.data.fields
                      .filter(railOffers)
                      .map((field) => field.destination)}
                  />
                </section>
              ) : null}
              {contextualFields.length ? (
                <details className="automatic-resolution-details">
                  <summary>
                    <span>
                      {contextualSummary.unchanged.length ? (
                        <span>
                          No change:{' '}
                          {contextualSummary.unchanged
                            .map((field) => destinationLabel(field.destination))
                            .join(', ')}
                        </span>
                      ) : null}
                      {contextualSummary.waiting ? (
                        <span>{heldPhrase(contextualSummary.waiting, 'waiting')}</span>
                      ) : null}
                      {contextualSummary.unavailable ? (
                        <span>{heldPhrase(contextualSummary.unavailable, 'unavailable')}</span>
                      ) : null}
                    </span>
                    <small>Show why</small>
                  </summary>
                  <div className="automatic-resolution-list">
                    {contextualFields.map((field) => {
                      const kind = contextKind(field)
                      return (
                        <div
                          className={`automatic-resolution-row is-${kind}`}
                          key={field.destination}
                        >
                          <strong>
                            {destinationLabel(field.destination)}
                            {kind === 'unchanged' ? null : (
                              <StatusPill tone="notice">
                                {kind === 'waiting' ? 'Waiting' : 'Unavailable'}
                              </StatusPill>
                            )}
                          </strong>
                          <small>{decisionValue(field)}</small>
                          <small>
                            {kind === 'unchanged'
                              ? 'No source offered a different value.'
                              : fieldReason(field)}
                          </small>
                        </div>
                      )
                    })}
                  </div>
                </details>
              ) : null}
              <WorkSources
                comparison={selectedChoiceMode}
                outcomes={selectedLibraryWork?.preparationOutcomes ?? []}
              />
            </>
          )}
        </section>
        {search.attention === 'open' ? (
          <aside
            className="attention-rail"
            aria-label="Attention queue"
            aria-busy={attention.isPlaceholderData || undefined}
            tabIndex={-1}
            ref={attentionRail}
            onKeyDown={(event) => {
              if (event.key === 'Escape') {
                event.preventDefault()
                closeAttention()
                return
              }
              if (event.key !== 'Tab' || typeof window.matchMedia !== 'function') return
              if (!window.matchMedia(drawerQuery).matches) return
              const stops = [
                ...event.currentTarget.querySelectorAll<HTMLElement>('button, a[href], input'),
              ].filter((stop) => !stop.matches(':disabled') && stop.getClientRects().length)
              const edge = event.shiftKey ? stops[0] : stops.at(-1)
              const active = document.activeElement
              if (active === edge || (event.shiftKey && !stops.some((stop) => stop === active))) {
                event.preventDefault()
                ;(event.shiftKey ? stops.at(-1) : stops[0])?.focus()
              }
            }}
          >
            <header>
              <div>
                <h2 ref={attentionHeading} tabIndex={-1}>
                  Attention
                </h2>
                <p>
                  {search.q && !data
                    ? failed
                      ? 'Count unavailable'
                      : 'Counting works…'
                    : summaryAttention === undefined
                      ? !summary.isError
                        ? 'Counting works…'
                        : search.q
                          ? `Matching: ${workCount(data?.counts.attention ?? 0)} needing a decision`
                          : 'Count unavailable'
                      : !search.q
                        ? `${workCount(summaryAttention)} ${summaryAttention === 1 ? 'needs' : 'need'} a decision`
                        : `Matching: ${String(data?.counts.attention ?? 0)} of ${workCount(summaryAttention)} needing a decision`}
                </p>
              </div>
              <button onClick={closeAttention}>Close</button>
            </header>
            {attention.data === undefined ? (
              <p role={attention.isError ? 'alert' : 'status'}>
                {attention.isError ? (
                  <>
                    Attention could not be loaded.{' '}
                    <button
                      ref={attentionRetry}
                      type="button"
                      onClick={() =>
                        void attention.refetch().then((result) => {
                          // The retry removes this button while it reads, so
                          // the rail's heading takes focus back on success
                          // (#1399) and Try again does when it fails again.
                          const active = document.activeElement
                          if (
                            active &&
                            active !== document.body &&
                            !attentionRail.current?.contains(active)
                          )
                            return
                          if (result.isSuccess)
                            attentionHeading.current?.focus({ preventScroll: true })
                          else
                            requestAnimationFrame(() =>
                              attentionRetry.current?.focus({ preventScroll: true }),
                            )
                        })
                      }
                    >
                      Try again
                    </button>
                  </>
                ) : (
                  'Loading attention…'
                )}
              </p>
            ) : (
              <>
                {attentionGroups.map((group, index) => {
                  const work = group[0]
                  if (!work) return null
                  return (
                    <div key={work.id} className="attention-work-group">
                      <div className="attention-work-identity">
                        <span className="attention-position">{String(index + 1)}</span>
                        <span>
                          <strong>{work.title}</strong>
                          <small>
                            {work.authors.length
                              ? work.authors.join(', ')
                              : 'Author details unavailable'}
                          </small>
                          <small>
                            {work.libraryDisplayName ?? work.library} ·{' '}
                            {mediaKindLabels[work.mediaKind]}
                          </small>
                        </span>
                        <span className="attention-work-count">
                          {`${String(work.workAttentionCount)} ${work.workAttentionCount === 1 ? 'field' : 'fields'}`}
                        </span>
                      </div>
                      <ul className="attention-field-rows">
                        {group.map((item) => {
                          const line = `${destinationLabel(item.destination)} · ${item.classificationReason ?? 'Needs your decision'}`
                          return (
                            <li key={item.destination}>
                              <button
                                className="attention-queue-item"
                                // The same field in two works would otherwise share a name in a
                                // screen reader's list of buttons; the work is shown above (#1361).
                                aria-label={`${line}, ${work.title}`}
                                aria-current={
                                  item.id === search.preview &&
                                  item.destination === selected?.destination
                                }
                                aria-disabled={attention.isPlaceholderData || undefined}
                                data-attention-row={`${item.id}:${item.destination}`}
                                onClick={() =>
                                  selectFromQueue(item.id, item.destination, item.attentionCursor)
                                }
                              >
                                <span className="attention-queue-copy">
                                  <b>{line}</b>
                                </span>
                              </button>
                            </li>
                          )
                        })}
                      </ul>
                    </div>
                  )
                })}
                {attention.hasNextPage ? (
                  <button
                    className="attention-load-more"
                    type="button"
                    aria-disabled={attention.isFetchingNextPage || undefined}
                    onClick={() => {
                      if (loadMore.current || attention.isFetchingNextPage) return
                      loadMore.current = {
                        key: attentionKey,
                        pages: attention.data.pages.length,
                        updated: attentionQuery.dataUpdatedAt,
                        errors: attentionQuery.errorUpdateCount,
                        from: attentionItems.length,
                      }
                      void attention.fetchNextPage()
                    }}
                  >
                    {attention.isFetchingNextPage ? 'Loading more…' : 'Load more attention'}
                  </button>
                ) : null}
                {attention.isFetchNextPageError ? (
                  <p role="alert">
                    More attention could not be loaded.{' '}
                    <button type="button" onClick={() => void attention.fetchNextPage()}>
                      Try again
                    </button>
                  </p>
                ) : null}
              </>
            )}
          </aside>
        ) : null}
      </div>
    </div>
  )
}
