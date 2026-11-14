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
import { StaleBuildRead } from '../components/StaleBuildRead'
import { useInfiniteQuery } from '@tanstack/react-query'
import { Link, useNavigate, useRouter, useRouterState } from '@tanstack/react-router'
import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
} from 'react'
import type { SyntheticEvent } from 'react'
import type { QueryExecuteOptions, QueryKey } from '@tanstack/react-query'

import { ApiError, BuildMismatchError, displayValue } from '../api/http'
import type {
  DecisionCommand,
  LibrarySearch,
  MediaKind,
  LibraryView,
  LibraryWorkView,
} from '../api/http'
import { choiceRadios, moveChoiceFocus } from '../choiceGroup'
import { useAttentionRail } from '../attentionRail'
import { focusFieldHeading, useRouteReadyFocus } from '../focus'
import { destinationLabel, mediaKindLabels } from '../labels'
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
  decisionValue,
  valueText,
  shownAdvice,
  effectSummary,
  commandSelected,
  choiceSelected,
  automaticallySelected,
  isLongDescription,
  decisionQuestion,
} from '../fieldDecision'
import type { ReviewField } from '../fieldDecision'
import { RecordedFieldObservations, MetadataChoice } from './FieldDecisionChoices'
import { LibraryCoverChoices } from './FieldDecisionCover'
import { LibraryViewSwitch } from '../components/LibraryViewSwitch'
import { ReviewCompletion } from './ReviewCompletion'
import { useStaleCoverReview } from './StaleCoverReview'
import { useCoverPrepareFailure } from '../coverChoices'
import {
  AsinAutomaticStatement,
  SettledFieldContext,
  DescriptionAutomaticStatement,
  NarratorsAutomaticStatement,
  ProviderConsensusAutomaticStatement,
  RecordedOutcome,
  SeriesAutomaticStatement,
  VerifiedManifestationIsbnAutomaticStatement,
} from './FieldDecisionOutcome'
import { OwnerControls } from './FieldDecisionOwner'
import { PreparationDetail } from './PreparationDetail'
import {
  librarySearchIdentity,
  usePreparationChange,
  usePreparationDismissal,
} from '../libraryPreparation'
import { WorkSources, WorkSummary } from './WorkSummary'
import { AttentionRail } from './AttentionRail'
import { LibraryWorks } from './LibraryWorks'
import { AutomaticHandlingPanel } from './AutomaticHandling'
import { useAutomaticHandling } from '../automaticHandling'
import { BookMark } from '../components/BookMark'
import { RESTART_SENTENCE, workCount } from '../libraryWork'
import {
  choiceContext,
  combinedAutomaticTags,
  comparisonNotices,
  contextKind,
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
// The compact layout, where the selected work replaces the list.
const compactQuery = '(max-width: 1024px)'
const compactWidth = () =>
  typeof window.matchMedia === 'function' && window.matchMedia(compactQuery).matches
// Below this the open Attention rail is a drawer over the workspace or the
// whole screen, never a column beside the works.
const railCoversQuery = '(max-width: 1199px)'
// Whether `query` matches now, following the viewport as it changes.
const useMediaMatch = (query: string) =>
  useSyncExternalStore(
    (change) => {
      if (typeof window.matchMedia !== 'function') return () => undefined
      const media = window.matchMedia(query)
      media.addEventListener('change', change)
      return () => {
        media.removeEventListener('change', change)
      }
    },
    () => typeof window.matchMedia === 'function' && window.matchMedia(query).matches,
  )
const narrowWidth = () =>
  typeof window.matchMedia === 'function' && window.matchMedia('(max-width: 760px)').matches

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
const heldPhrase = (count: number, kind: 'waiting' | 'unavailable') =>
  `${String(count)} ${count === 1 ? 'field' : 'fields'} ${kind === 'waiting' ? 'waiting' : 'unavailable'}`
const ownerOutcome = (outcome: DecisionCommand['outcome']) =>
  outcome === 'owner_entered' || outcome === 'withdraw'

export default function LibraryPage({
  data,
  retained,
  pending,
  failed,
  error,
  retry,
  onRailRetaining,
}: {
  data: LibraryView | undefined
  /** `data` is the previous key's result, shown busy until the URL's own read settles. */
  retained: boolean
  pending: boolean
  failed: boolean
  /** The works read's failure, which a build mismatch makes unrecoverable by another try. */
  error: unknown
  /** Reads the works again; settles when the read does. */
  retry: () => Promise<unknown>
  /** Reports whether the attention rail is showing a retained queue. */
  onRailRetaining?: (retaining: boolean) => void
}) {
  const works = useMemo(() => data?.works ?? [], [data?.works])
  // The restart sentence is said once a screen: on the Works failure when that
  // shows, otherwise on whichever other read failed (#1765). The compact layout
  // hides the list behind a selected work, and below 1200px the Attention rail
  // covers the workspace, so each says it there.
  const worksSaysRestart = !data && failed && !(error instanceof BuildMismatchError)
  const compactLayout = useMediaMatch(compactQuery)
  const railCoversWorks = useMediaMatch(railCoversQuery)
  const restartSentence = worksSaysRestart && !compactLayout ? '' : ` ${RESTART_SENTENCE}`
  const railRestartSentence = worksSaysRestart && !railCoversWorks ? '' : ` ${RESTART_SENTENCE}`
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
  const summaryAttention = summary.data?.counts.attention
  const preparationState = usePreparation()
  const noLibraryConnected = preparationState.data?.libraries.length === 0
  const presentation =
    preparationState.data?.presentation?.state === 'dismissed'
      ? null
      : (preparationState.data?.presentation ?? null)
  const preparationSelected = presentation !== null && search.preparation === presentation.run_id
  const animateChange = usePreparationChange(
    presentation,
    preparationState.data?.recovery_required ?? false,
    preparationSelected,
  )
  const currentSearch = useRef(search)
  const pendingSearchQuery = useRef<{ q: string | null; attention?: boolean } | null>(null)
  const searchNavigation = useRef<Promise<void> | null>(null)
  const searchCommitTimer = useRef<number | null>(null)
  const searchGeneration = useRef(0)
  const observedSearchIdentity = useRef(librarySearchIdentity(search))
  const expectedSearchIdentity = useRef<string | null>(null)
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
  const attentionRail = useRef<HTMLElement>(null)
  const attentionHeading = useRef<HTMLHeadingElement>(null)
  const attentionToggle = useRef<HTMLButtonElement>(null)
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
  const coverPrepareFailure = useCoverPrepareFailure(selectedReviewCaseId)
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
      staleCover.clear(caseId)
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
        staleCover.open(
          selectedReviewCaseId,
          selectedIdentityRef.current?.title ?? 'Cover comparison',
        )
        return
      }
      await navigate({ search: { lens: 'needs_attention' } })
    },
  })
  const staleCover = useStaleCoverReview({
    http,
    settle: () => controller.settle(),
    navigate,
    selectedReviewCaseId,
    setReconciledSuccessorIdentity,
    identityShown: Boolean(displayedIdentity),
    detailFailed: detail.isError,
  })
  const staleCoverNotice = staleCover.notice
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
  const identityStale =
    identityError &&
    (error instanceof BuildMismatchError ||
      attention.error instanceof BuildMismatchError ||
      detail.error instanceof BuildMismatchError)
  // While a command records, the Library asks for the successor, the owner is
  // moved to it, or its identity is still being read, the absence is the move.
  const leaving =
    movingFrom === search.preview ||
    activeLineageReconciliation ||
    controller.recording ||
    (reconciledSuccessorIdentity === search.preview && detail.isPending)
  // Exactly when the workspace shows "Loading selected work…": a retry settles
  // only once this clears, since until then its Try again or Reload is not there
  // to take focus, even when every read has already failed (#1742).
  const selectedReadLoading = !displayedIdentity
    ? !staleCoverNotice && (identityPending || leaving || successorPending === search.preview)
    : detail.isPending || (detail.isError && leaving)
  const selectedReadError = identityError || (Boolean(displayedIdentity) && detail.isError)
  // A works retry that meets a newer build can leave the identity in error
  // throughout, so the switch to the reload is what settles the retry.
  const selectedReadStale = identityStale || detail.error instanceof BuildMismatchError
  const selectedDetailReady =
    !preparationSelected &&
    Boolean(search.preview) &&
    search.attention !== 'open' &&
    Boolean(displayedIdentity) &&
    !selectedReadLoading &&
    !selectedReadError
  useEffect(() => {
    selectedDetailRetry.current = null
    selectedDetailShown.current = null
  }, [search.preview])
  // A read with no data goes back to pending when retried, so the loading line
  // replaces its Try again: the workspace, which stays, holds focus until the
  // settled read takes it. A read that still holds data keeps Try again
  // mounted, and focus stays on it.
  const holdSelectedRetry = (replaced: boolean) => {
    selectedDetailRetry.current = search.preview ?? null
    if (replaced)
      document
        .querySelector<HTMLElement>('#main-content .workspace-detail')
        ?.focus({ preventScroll: true })
  }
  useEffect(() => {
    // An error is settled only once it renders: while another read is still
    // in flight the work shows as loading, and its Try again or Reload does
    // not exist yet to take focus.
    const selectedDetailSettled = selectedDetailReady || (selectedReadError && !selectedReadLoading)
    if (!selectedDetailSettled) return
    const workspace = document.querySelector<HTMLElement>('#main-content .workspace-detail')
    const active = document.activeElement
    // A selected-work retry holds focus on the workspace while it reads.
    const restoreAfterRetry =
      selectedDetailRetry.current === search.preview &&
      (active === document.body || active === workspace)
    if (workspace && active === workspace && selectedDetailReady)
      workspace.querySelector<HTMLElement>('[data-route-focus]')?.focus({ preventScroll: true })
    else if (restoreAfterRetry) {
      if (selectedReadError) selectedDetailRetryButton.current?.focus({ preventScroll: true })
      else
        workspace?.querySelector<HTMLElement>('[data-route-focus]')?.focus({ preventScroll: true })
    }
    selectedDetailRetry.current = null
    if (selectedDetailReady) selectedDetailShown.current = search.preview ?? null
    // A retried read that fails again with its Try again still mounted
    // changes none of the states above: its new error is what settles it.
  }, [
    selectedReadError,
    selectedReadLoading,
    selectedReadStale,
    search.preview,
    selectedDetailReady,
    detail.errorUpdateCount,
    attention.errorUpdateCount,
  ])
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
  const automaticHandling = useAutomaticHandling(selected, selectedReviewCaseId)
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
    // A kept cover asks for nothing but stays choosable (#1310).
    (selected.needsAttention || selected.classification === 'owner_choosable')
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
        automatic: automaticallySelected(selected, selectedProgress),
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
            automatic: automaticallySelected(selected, selectedProgress),
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
            automatic: selectedAdvice.selection?.origin === 'automatic_policy',
            retainedAdvice: true,
            context: undefined,
            current: false,
            disabled: true,
            expanded: false,
            label: 'Description adviser · Earlier choice',
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
            automatic:
              (selectedProgress === null || selectedProgress.phase === 'failed') &&
              selectedAdvice.selection?.origin === 'automatic_policy',
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
            label: 'Description adviser',
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
      <LibraryViewSwitch view="works" />
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
        <LibraryWorks
          search={search}
          data={data}
          works={works}
          retained={retained}
          failed={failed}
          error={error}
          retry={retry}
          summaryCounts={summary.data?.counts}
          summaryUnavailable={summary.isError}
          presentation={presentation}
          reconnecting={preparationState.reconnecting}
          recoveryRequired={preparationState.data?.recovery_required ?? false}
          preparationSelected={preparationSelected}
          onSelectPreparation={(runId) =>
            void navigate({
              search: (current) => {
                const next = { ...current, preparation: runId }
                delete next.attention
                return next
              },
            })
          }
          noLibraryConnected={noLibraryConnected}
          selectedWork={selectedWork}
          queryText={queryText}
          runSearch={runSearch}
          submitSearch={submitSearch}
          select={select}
          movePage={movePage}
          lensIntent={lensIntent}
          workIntent={workIntent}
          pageIntent={pageIntent}
        />
        <section
          className="workspace-detail"
          // Preparation states its own progress in one status line; the whole
          // detail would otherwise be read again on every poll (#1288).
          aria-live={preparationSelected ? undefined : 'polite'}
          tabIndex={-1}
          {...(!selectedWork &&
          search.preview &&
          search.attention !== 'open' &&
          (!displayedIdentity || selectedReadLoading || selectedReadError)
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
            </div>
          ) : !displayedIdentity && staleCoverNotice ? (
            staleCoverNotice
          ) : !displayedIdentity ? (
            selectedReadLoading ? (
              <p role="status">Loading selected work…</p>
            ) : identityStale ? (
              <StaleBuildRead
                lead="Selected work was not loaded."
                ref={selectedDetailRetryButton}
              />
            ) : identityError ? (
              <p role="alert">
                {`Selected work could not be loaded.${restartSentence}`}{' '}
                <button
                  ref={selectedDetailRetryButton}
                  onClick={() => {
                    holdSelectedRetry(
                      data === undefined ||
                        attention.data === undefined ||
                        (detail.isError && detail.data === undefined),
                    )
                    void retry()
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
                <p role="status">This work is not in the current list of works.</p>
              </div>
            )
          ) : // `detail.isPending` is part of `selectedReadLoading` here; naming it
          // narrows the read for the branches below.
          selectedReadLoading || detail.isPending ? (
            <p role="status">Loading selected work…</p>
          ) : detail.isError && staleCoverNotice ? (
            staleCoverNotice
          ) : detail.error instanceof BuildMismatchError ? (
            <StaleBuildRead lead="Selected work was not loaded." ref={selectedDetailRetryButton} />
          ) : detail.isError ? (
            <p role="alert">
              {controller.messageFor(search.field ?? selected?.destination ?? '') ??
                `Selected work could not be loaded.${restartSentence}`}{' '}
              <button
                ref={selectedDetailRetryButton}
                onClick={() => {
                  holdSelectedRetry(detail.data === undefined)
                  void detail.refetch()
                }}
              >
                Try again
              </button>{' '}
            </p>
          ) : !detail.data.current ? (
            <div className="library-selection">
              <h2>{displayedIdentity.title}</h2>
              {staleCoverNotice ?? (
                <p role="alert">
                  This review case is out of date. Choose the work again in Works to open the
                  current one.
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
                    <dt>Needs you</dt>
                    <dd>{String(attentionFields.length)}</dd>
                  </div>
                  <div>
                    <dt>Automatic</dt>
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
                <nav className="selected-work-diagnostics" aria-label="Evidence and history">
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
                <ReviewCompletion
                  restartSentence={restartSentence}
                  fields={complete.fields}
                  writesActive={writesActive}
                  completionRegion={completionRegion}
                  attention={attention}
                  hasNextWork={Boolean(nextWork)}
                  searching={Boolean(search.q)}
                  outsideSearch={
                    summaryAttention === undefined || !data ? undefined : outsideSearch
                  }
                  inspect={(destination) => {
                    setCompletionInspection(destination)
                    selectCase(selectedReviewCaseId, destination)
                  }}
                  reviewNextWork={() => {
                    if (!nextWork) return
                    setComplete(null)
                    setCompletionInspection(null)
                    selectCase(nextWork.id, nextWork.destination, {
                      cursor: nextWork.attentionCursor,
                    })
                  }}
                />
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
                      {/* Takes focus when a retry below it leaves nothing in its place. */}
                      <h3 className="decision-question" tabIndex={-1}>
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
                          You can choose another value.
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
                        prepareFailure={coverPrepareFailure}
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
                    <SettledFieldContext
                      field={selected}
                      actionable={selectedActionable}
                      ownerEntry={selectedOwnerEntry}
                      expanded={expandedChoices.has(`${selected.destination}:context`)}
                      toggle={() => toggleChoiceEvidence(`${selected.destination}:context`)}
                    />
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
                    <AutomaticHandlingPanel handling={automaticHandling} selected={selected} />
                  ) : null}
                  {comparisonNotices(selected).length ? (
                    <aside className="comparison-notice" aria-label="Comparison condition">
                      {comparisonNotices(selected).map((notice) => (
                        <p key={notice}>
                          {`${noticeText(selected, notice, detail.data.target.mediaKind) ?? ''}.`}
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
                  {controller.reloadRequired ? (
                    <button
                      type="button"
                      disabled={detail.isFetching}
                      onClick={() => void controller.settle()}
                    >
                      {detail.isFetching ? 'Reloading current case…' : 'Reload current case'}
                    </button>
                  ) : null}
                  {selectedChoiceMode && effect ? (
                    <aside className="field-apply-context" aria-label="Writing to the library">
                      <span className="activity-state-mark" aria-hidden="true"></span>
                      <span>
                        <small>Writing to the library</small>
                        <strong>{effect.state}</strong>
                        {effect.detail ? <em>{effect.detail}</em> : null}
                      </span>
                    </aside>
                  ) : null}
                </article>
              ) : (
                <p>This work has no fields requiring a decision.</p>
              )}
              {detail.data.recordingIdentityBasis ||
              detail.data.recordingAdmissionBasis ||
              detail.data.audiobookReviewScope === 'work' ? (
                <section aria-label="Recording match">
                  <h3>Recording match</h3>
                  <RecordingIdentityBasis
                    value={detail.data.recordingIdentityBasis}
                    admission={detail.data.recordingAdmissionBasis}
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
        <AttentionRail
          search={search}
          countText={
            search.q && !data
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
                  : `Matching: ${String(data?.counts.attention ?? 0)} of ${workCount(summaryAttention)} needing a decision`
          }
          attention={attention}
          attentionItems={attentionItems}
          attentionGroups={attentionGroups}
          attentionRail={attentionRail}
          attentionHeading={attentionHeading}
          selected={selected}
          closeAttention={closeAttention}
          selectFromQueue={selectFromQueue}
          restartSentence={railRestartSentence}
        />
      </div>
    </div>
  )
}
