import {
  invalidateBackgroundQueries,
  prefetchBackgroundQuery,
  useRequiredQuery as useQuery,
} from '../queryWork'
import { FirstReviewGuide } from './FirstReviewGuide'
import { RecordingIdentityBasis } from '../components/RecordingIdentityBasis'
import { StaleBuildRead } from '../components/StaleBuildRead'
import { useNavigate, useRouter, useRouterState } from '@tanstack/react-router'
import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
} from 'react'
import type { QueryExecuteOptions, QueryKey } from '@tanstack/react-query'

import { BuildMismatchError } from '../api/http'
import type {
  DecisionCommand,
  LibrarySearch,
  MediaKind,
  LibraryView,
  LibraryWorkView,
} from '../api/http'
import { useAttentionRail } from '../attentionRail'
import { focusFieldHeading, useRouteReadyFocus } from '../focus'
import {
  detailQuery,
  libraryQuery,
  libraryRoute,
  librarySummaryQuery,
  PreparationNotice,
  ProviderThrottleNotice,
  usePreparation,
  useReviewContext,
} from '../router'
import { WriteReadyNotice } from '../components/WriteReadyNotice'
import { actionable, requestKey, useReviewDecisionController } from '../reviewDecisionController'
import { emptyUnproposed, primaryField, railOffers } from '../reviewChoiceCounts'
import { advancePresentationSettled } from '../reviewAdvancePresentation'

import type { ReviewField } from '../fieldDecision'
import { LibraryViewSwitch } from '../components/LibraryViewSwitch'
import { CaseCheckingLine } from '../components/CaseCheckingLine'
import { ReviewCompletion } from './ReviewCompletion'
import { ReviewKeys } from './ReviewKeys'
import { useStaleCoverReview } from './StaleCoverReview'
import { useCoverPrepareFailure } from '../coverChoices'
import { FieldDecisionCanvas } from './FieldDecisionCanvas'
import { fieldDecisionMode, ownerOutcome } from '../fieldDecisionMode'
import { PreparationDetail } from './PreparationDetail'
import {
  librarySearchIdentity,
  usePreparationChange,
  usePreparationDismissal,
} from '../libraryPreparation'
import { WorkSources, WorkSummary } from './WorkSummary'
import { AttentionRail } from './AttentionRail'
import { type ShowLibraryListing, useLibraryListing } from '../libraryVisit'
import { LibraryWorks } from './LibraryWorks'
import { useAutomaticHandling } from '../automaticHandling'
import { RESTART_SENTENCE, workCount } from '../libraryWork'
import { useAttentionQueue } from '../attentionQueue'
import { useCaseSuccessor } from '../caseSuccessor'
import { useLibrarySearchNavigation } from '../librarySearchNavigation'
import { ContextualFields } from './ContextualFields'
import { FieldFilmstrip } from './FieldFilmstrip'
import { SelectedWorkHeader } from './SelectedWorkHeader'
const noWorks: readonly LibraryWorkView[] = []
// A pointer crossing several rows warms only the one it rests on.
const prefetchIntentDelay = 50
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

export default function LibraryPage({
  data,
  retained,
  pending,
  failed,
  error,
  retry,
  onRailRetaining,
  showListing,
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
  /** Gives the Library route's visit what a rendered New lens listed (#1351). */
  showListing: ShowLibraryListing
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
  useLibraryListing(showListing, data)
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
  const selectedDetailRetry = useRef<string | null>(null)
  const selectedDetailRetryButton = useRef<HTMLButtonElement>(null)
  // The preview whose detail was last shown ready, until the preview changes.
  const selectedDetailShown = useRef<string | null>(null)
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
  const noticeMovedOwner = useCallback((caseId: string, destination: string, text: string) => {
    setOwnerNotice({ caseId, destination, text })
    headingFocus.current = caseId
  }, [])
  const ownerField = useRef<string | null>(null)
  const [openedWork, setOpenedWork] = useState<LibraryWorkView | null>(null)
  const [expandedChoiceState, setExpandedChoiceState] = useState<{
    work: string
    keys: ReadonlySet<string>
  }>({ work: '', keys: new Set() })
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
  const { queryText, searchPending, runSearch, changeAttentionSearch, submitSearch } =
    useLibrarySearchNavigation({ urlQuery, searchIdentity, navigate, revealWorkspace })
  const selectedReviewCaseId = search.preview ?? ''
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
  const { attention, attentionItems, attentionGroups } = useAttentionQueue(
    http,
    queryClient,
    search.q,
    search.attentionCursor,
    onRailRetaining,
  )
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
  const {
    activeLineageReconciliation,
    reconciledSuccessorIdentity,
    setReconciledSuccessorIdentity,
    movingFrom,
    setMovingFrom,
    successorPending,
    setSuccessorPending,
  } = useCaseSuccessor({
    preview: search.preview,
    http,
    updates,
    queryClient,
    navigate,
    data,
    retained,
    works,
    detailData: detail.data,
    ownerField,
    noticeMovedOwner,
  })
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
  const activeQueueIndex = selected
    ? attentionItems.findIndex(
        (item) => item.id === search.preview && item.destination === selected.destination,
      )
    : -1
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
    checking: detail.data?.checking ?? false,
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
    changeAttentionSearch(open)
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
      offerDigest: field.offerDigest,
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

  const selectedMode = fieldDecisionMode(selected)
  const writesActive = detail.data?.writesConsented === true

  return (
    <div className="library-page variant-m-workspace">
      {/* One toolbar in both states, so selecting a work never moves the workspace. */}
      <header
        className="library-header"
        aria-label="Library controls"
        tabIndex={-1}
        {...(!hasSelection ? { 'data-route-focus': 'true' } : {})}
      >
        <div className="library-title">
          <h1>Library</h1>
          <LibraryViewSwitch view="works" />
        </div>
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
        <ReviewKeys
          ready={
            !(preparationSelected || search.attention === 'open') &&
            !(controller.recording || controller.checking || controller.reloadRequired) &&
            (!search.preview || selectedDetailReady || (selectedReadError && !selectedReadLoading))
          }
        />
        <WriteReadyNotice />
      </header>
      <PreparationNotice routeIdentity={routeIdentity} />
      <ProviderThrottleNotice />
      <FirstReviewGuide
        first={attention.isPlaceholderData ? undefined : attentionItems[0]}
        onOpen={selectFromQueue}
      />
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
              <SelectedWorkHeader
                search={search}
                identity={displayedIdentity}
                cover={
                  currentWorks.find((work) => work.reviewCaseId === selectedReviewCaseId)?.cover
                }
                reviewCaseId={detail.data.id}
                fields={detail.data.fields}
                needsYou={attentionFields.length}
                selectedDestination={selected?.destination}
              />
              {detail.data.comparisonNotices.length ? (
                <aside className="comparison-notice" aria-label="Provider comparison condition">
                  {detail.data.comparisonNotices.map((notice) => (
                    <p key={notice}>{notice}</p>
                  ))}
                </aside>
              ) : null}
              <FieldFilmstrip
                title={displayedIdentity.title}
                fields={renderedRailFields}
                omittedCount={omittedFields.length}
                selectedDestination={selected?.destination}
                fieldRail={fieldRail}
                activeFieldButton={activeFieldButton}
                revealFocusDestination={revealFocusDestination}
                clearRevealFocus={() => setRevealFocusDestination(null)}
                centreFieldRail={centreFieldRail}
                onSelectField={(destination) => {
                  if (complete?.id === selectedReviewCaseId) setCompletionInspection(destination)
                  selectCase(selectedReviewCaseId, destination)
                }}
                onShowMore={() => {
                  setRevealFocusDestination(omittedFields[0]?.destination ?? null)
                  setShownEmptyFields((current) => ({
                    ...current,
                    [selectedReviewCaseId]: true,
                  }))
                }}
              />
              <CaseCheckingLine
                checking={detail.data.checking}
                givenUp={detail.data.checkGivenUp}
                http={http}
              />
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
                <FieldDecisionCanvas
                  key={selected.destination}
                  field={selected}
                  mode={selectedMode}
                  reviewCaseId={selectedReviewCaseId}
                  mediaKind={detail.data.target.mediaKind}
                  controller={controller}
                  inspectingCompletion={complete?.id === selectedReviewCaseId}
                  backToCompletion={() => setCompletionInspection(null)}
                  expandedChoices={expandedChoices}
                  toggleChoiceEvidence={toggleChoiceEvidence}
                  choose={choose}
                  coverPrepareFailure={coverPrepareFailure}
                  ownerCommand={ownerCommand}
                  setOwnerCommand={setOwnerCommand}
                  ownerNotice={ownerNotice}
                  automaticHandling={automaticHandling}
                  retryButton={retryButton}
                  reloading={detail.isFetching}
                />
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
              {contextualFields.length ? <ContextualFields fields={contextualFields} /> : null}
              <WorkSources
                comparison={selectedMode.choiceMode}
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
                  : `Matching: ${(data?.counts.attention ?? 0).toLocaleString('en-US')} of ${workCount(summaryAttention)} needing a decision`
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
