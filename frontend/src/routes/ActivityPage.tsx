import { invalidateForegroundQueries } from '../queryWork'
import { queryWorkClass, useRequiredQuery as useQuery } from '../queryWork'
import { useMutation } from '@tanstack/react-query'
import { useNavigate, useRouter, useRouterState } from '@tanstack/react-router'
import { useEffect, useRef, useState } from 'react'
import { AmbiguousMutationError, ApiError, BuildMismatchError, displayValue } from '../api/http'
import type {
  ActivityCategory,
  ActivityLibraryView,
  ActivitySearch,
  ActivityTransitionView,
  ActivityView,
  ProviderRecoveryDispatchReason,
} from '../api/http'
import { activityOutcome } from '../activityState'
import { Dropdown } from '../components/Dropdown'
import { useRouteReadyFocus } from '../focus'
import { fieldLabel } from '../labels'
import { destinationReason, readWriteConsent } from '../writeConsent'
import {
  activityRoute,
  PreparationNotice,
  useLiveStream,
  usePreparation,
  useReviewContext,
} from '../router'
// The activity response is validated into `ActivityDisplayView` at the
// transport boundary, so this route reads it as the typed view it already is
// rather than re-deriving `unknown` shapes a second time (#636).
const capabilityLabel = (state: string) =>
  ({
    allowed: 'Ready for consent',
    partially_allowed: 'Some destinations are ready',
    connection_unavailable: 'Connection needs checking',
    insufficient_permission: 'Permission is unavailable',
    field_unavailable: 'A field is unavailable',
    not_ready: 'Not ready yet',
  })[state] ?? 'Not ready yet'
const stateLabel = (state: string, advice?: 'description' | 'cover') =>
  (advice === 'description'
    ? {
        completed: 'Description advice completed',
        succeeded: 'Description advice completed',
        failed: 'Description advice failed safely',
      }[state]
    : advice === 'cover'
      ? {
          completed: 'Cover advice completed',
          unavailable: 'Cover advice unavailable',
          failed: 'Cover advice failed safely',
          selected: 'Owner cover choice recorded',
          retry: 'Manual cover retry',
        }[state]
      : undefined) ??
  {
    accept: 'Accepted',
    keep_current: 'Kept current',
    reject: 'Rejected',
    completed: 'Completed',
    unavailable: 'Unavailable',
    failed: 'Failed safely',
    no_change_required: 'No change required',
    applying: 'Applying',
    claimed: 'Preparing',
    planned: 'Planned',
    reconciling: 'Checking',
    retryable: 'Waiting safely',
    verified: 'Verified',
    stale: 'Stale',
    superseded: 'Superseded',
    abandoned: 'Stopped safely',
  }[state] ??
  'Recorded'
// What the owner calls a record, on the row and on the control that filters
// for it, so a chip and the rows it selects never use different words. The
// filter axis is still the durable event type the ledger is indexed by; only
// the language is the owner's.
const categoryLabel: Record<ActivityCategory, string> = {
  owner: 'Owner choice',
  automatic: 'Automatic choice',
  policy: 'Policy change',
  recovery: 'Recovery',
}
const recoveryDispatchLabel: Record<ProviderRecoveryDispatchReason, string> = {
  missing_corroboration: 'needs independent evidence',
  zero_provider_evidence: 'no provider evidence yet',
  settleable_disagreement: 'evidence disagreement',
  blanket_scan: 'general recovery scan',
}
const typeOptions = [
  { id: 'all', label: 'All' },
  { id: 'review', label: 'Owner choices' },
  { id: 'metadata', label: 'Automatic choices' },
  { id: 'policy', label: 'Policy changes' },
  { id: 'safety', label: 'Recovery' },
] as const
type ActivitySearchUpdate = Partial<Omit<ActivitySearch, 'q'>> & { q?: string | undefined }
const locationIdentity = (location: { href: string; state: { __TSR_key?: string } }) =>
  `${location.href}\u0000${location.state.__TSR_key ?? ''}`
const dateParts = (value: number | null) => {
  if (value === null) return { day: 'Date not recorded', time: '—' }
  const date = new Date(value * 1000)
  if (Number.isNaN(date.getTime())) return { day: 'Date not recorded', time: '—' }
  return {
    day: new Intl.DateTimeFormat(undefined, { dateStyle: 'medium' }).format(date),
    time: new Intl.DateTimeFormat(undefined, {
      hour: '2-digit',
      minute: '2-digit',
      hour12: false,
    }).format(date),
  }
}
function PageJump({
  page,
  pageCount,
  onCommit,
  onEdit,
}: {
  page: number
  pageCount: number
  onCommit: (page: number, focusKey: string) => void
  onEdit: () => void
}) {
  const [value, setValue] = useState(String(page))
  const cancelCommit = useRef(false)
  const commit = () => {
    if (cancelCommit.current) {
      cancelCommit.current = false
      setValue(String(page))
      return
    }
    const requested = Number.parseInt(value, 10)
    if (!Number.isFinite(requested)) {
      setValue(String(page))
      return
    }
    const clamped = Math.max(1, Math.min(pageCount, requested))
    setValue(String(clamped))
    if (clamped !== page) onCommit(clamped, 'page-jump')
  }
  return (
    <label className="cm-activity-page-jump">
      <span>Page</span>
      <input
        data-activity-focus="page-jump"
        aria-label="Activity page number"
        inputMode="numeric"
        type="number"
        min="1"
        max={pageCount}
        value={value}
        onChange={(event) => {
          cancelCommit.current = false
          onEdit()
          setValue(event.target.value)
        }}
        onBlur={commit}
        onKeyDown={(event) => {
          if (event.key === 'Enter') event.currentTarget.blur()
          if (event.key === 'Escape') {
            event.preventDefault()
            cancelCommit.current = true
            setValue(String(page))
          }
        }}
      />
      <span>of {pageCount}</span>
    </label>
  )
}
function ActivityTransitions({ eventId }: { eventId: string }) {
  const { http } = useReviewContext()
  const [extraPages, setExtraPages] = useState<ActivityTransitionView[][]>([])
  const [hasMore, setHasMore] = useState(false)
  const query = useQuery({
    queryKey: ['activity-transitions', eventId],
    queryFn: (queryContext) =>
      http.getActivityTransitions(
        eventId,
        undefined,
        queryContext.signal,
        queryWorkClass(queryContext),
      ),
    retry: false,
  })
  const more = useMutation({
    mutationFn: (afterSequence: number) => http.getActivityTransitions(eventId, afterSequence),
    onSuccess: (page) => {
      setExtraPages((current) => [...current, [...page.events]])
      setHasMore(page.has_more)
    },
  })
  if (query.isLoading) return <p>Loading recorded transitions…</p>
  if (query.isError)
    return (
      <p role="alert">
        Recorded transitions could not be loaded.{' '}
        <button type="button" onClick={() => void query.refetch()}>
          Try again
        </button>
      </p>
    )
  const events = [...(query.data?.events ?? []), ...extraPages.flat()]
  const moreAvailable = extraPages.length ? hasMore : Boolean(query.data?.has_more)
  if (!events.length) return null
  return (
    <ol className="cm-activity-transitions" aria-label="Recorded lifecycle transitions">
      {events.map((transition) => (
        <li key={transition.event_id}>
          <time>
            {dateParts(transition.occurred_at).day} · {dateParts(transition.occurred_at).time}
          </time>
          <span> · {stateLabel(transition.state)}</span>
        </li>
      ))}
      {moreAvailable ? (
        <li>
          <button
            type="button"
            onClick={() => more.mutate(events[events.length - 1]?.sequence ?? 1)}
            disabled={more.isPending}
          >
            {more.isPending ? 'Loading transitions…' : 'Load more transitions'}
          </button>
          {more.isError ? (
            <p role="alert">
              More recorded transitions could not be loaded.{' '}
              <button
                type="button"
                onClick={() => more.mutate(events[events.length - 1]?.sequence ?? 1)}
              >
                Try again
              </button>
            </p>
          ) : null}
        </li>
      ) : null}
    </ol>
  )
}
export default function ActivityPage({ initial }: { initial: ActivityView }) {
  const { http, queryClient, updates } = useReviewContext()
  const router = useRouter()
  const navigate = useNavigate({ from: activityRoute.fullPath })
  const search = activityRoute.useSearch()
  const resolvedLocation = useRouterState({ select: (state) => state.resolvedLocation })
  const [queryText, setQueryText] = useState(search.q ?? '')
  const queryCommit = useRef<number | null>(null)
  const queryDraftVersion = useRef(0)
  const canonicalQuery = useRef(search.q ?? '')
  const pendingLocalNavigations = useRef<{ q: string; draftVersion: number }[]>([])
  const localNavigations = useRef(new Map<string, { q: string; draftVersion: number }>())
  const externalTarget = useRef<string | null>(null)
  const requestedPage = useRef(search.page)
  const pageNavigationVersion = useRef(0)
  const focusVersion = useRef(0)
  const [focusRequest, setFocusRequest] = useState<{ key: string; version: number } | null>(null)
  useEffect(
    () => () => {
      if (queryCommit.current !== null) window.clearTimeout(queryCommit.current)
    },
    [],
  )
  useEffect(
    () =>
      router.history.subscribe(({ action, location }) => {
        if (action.type === 'PUSH' || action.type === 'REPLACE') {
          const q = new URL(location.href, window.location.origin).searchParams.get('q') ?? ''
          const pending = pendingLocalNavigations.current
          const index = pending.findIndex((intent) => intent.q === q)
          if (index !== -1) {
            const [intent] = pending.splice(index, 1)
            if (intent) localNavigations.current.set(locationIdentity(location), intent)
          }
        }
        if (action.type === 'BACK' || action.type === 'FORWARD' || action.type === 'GO') {
          externalTarget.current = locationIdentity(location)
          if (queryCommit.current !== null) {
            window.clearTimeout(queryCommit.current)
            queryCommit.current = null
          }
          pendingLocalNavigations.current.length = 0
          localNavigations.current.clear()
          ++queryDraftVersion.current
          ++pageNavigationVersion.current
          ++focusVersion.current
        }
      }),
    [router],
  )
  useEffect(() => {
    if (!resolvedLocation) return
    const identity = locationIdentity(resolvedLocation)
    const current = search.q ?? ''
    const external = externalTarget.current === identity
    if (external) {
      externalTarget.current = null
      if (queryCommit.current !== null) {
        window.clearTimeout(queryCommit.current)
        queryCommit.current = null
      }
      pendingLocalNavigations.current.length = 0
      localNavigations.current.clear()
      ++queryDraftVersion.current
      ++pageNavigationVersion.current
      ++focusVersion.current
      canonicalQuery.current = current
      setQueryText(current)
      requestedPage.current = search.page
      return
    }
    const local = localNavigations.current.get(identity)
    if (local) {
      localNavigations.current.delete(identity)
      canonicalQuery.current = local.q
      requestedPage.current = search.page
      return
    }
    if (canonicalQuery.current === current) {
      requestedPage.current = search.page
      return
    }
    externalTarget.current = null
    if (queryCommit.current !== null) {
      window.clearTimeout(queryCommit.current)
      queryCommit.current = null
    }
    pendingLocalNavigations.current.length = 0
    localNavigations.current.clear()
    ++queryDraftVersion.current
    ++pageNavigationVersion.current
    ++focusVersion.current
    canonicalQuery.current = current
    setQueryText(current)
    requestedPage.current = search.page
  }, [resolvedLocation, search.page, search.q])
  useEffect(() => {
    if (focusVersion.current !== focusRequest?.version) return
    let outer: number | null = null
    let inner: number | null = null
    let active = true
    outer = requestAnimationFrame(() => {
      outer = null
      inner = requestAnimationFrame(() => {
        inner = null
        if (active && focusVersion.current === focusRequest.version)
          document
            .querySelector<HTMLElement>(`[data-activity-focus="${focusRequest.key}"]`)
            ?.focus({ preventScroll: true })
      })
    })
    return () => {
      active = false
      if (outer !== null) cancelAnimationFrame(outer)
      if (inner !== null) cancelAnimationFrame(inner)
    }
  }, [focusRequest, search.eventType, search.page, search.pageSize, search.q, search.rangeDays])
  const routeIdentity = 'activity'
  useRouteReadyFocus(routeIdentity)
  const live = useLiveStream() === 'live'
  const query = useQuery({
    queryKey: [
      'activity',
      search.q ?? null,
      search.eventType,
      search.rangeDays,
      search.page,
      search.pageSize,
    ],
    queryFn: (queryContext) =>
      http.getActivity(search, queryContext.signal, queryWorkClass(queryContext)),
    initialData: initial,
    staleTime: 4_000,
    retry: false,
    // Five seconds was the only reason a write that started between polls ever
    // became visible. It survives as the fallback for a stream that is down;
    // while one is up, the write lane announces each transition itself (#641).
    refetchInterval: live ? false : 5_000,
  })
  const data = query.data
  const preparation = usePreparation().data
  const connectionConditions = preparation?.connections ?? []
  const connection = connectionConditions.find((item) => item.state !== 'recovered')
  const connectionState = JSON.stringify(connectionConditions)
  const ledger = data.ledger
  const [consentOpen, setConsentOpen] = useState(
    () => router.state.location.hash === 'activity-consent',
  )
  const consentTrigger = useRef<HTMLElement | null>(null)
  const nextPermissionRequest = useRef(1)
  const [permissionRequest, setPermissionRequest] = useState<number | null>(() =>
    router.state.location.hash === 'activity-consent' ? 1 : null,
  )
  const previousConnectionState = useRef(connectionState)
  useEffect(() => {
    if (previousConnectionState.current !== connectionState) setPermissionRequest(null)
    previousConnectionState.current = connectionState
  }, [connectionState])
  const permissions = useQuery({
    queryKey: ['activity-permissions', permissionRequest],
    queryFn: (context) =>
      http.getActivityPermissionReadiness(context.signal, queryWorkClass(context)),
    enabled: consentOpen && permissionRequest !== null,
    retry: false,
    staleTime: Infinity,
    gcTime: 0,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
  })
  const permission = permissionRequest === null ? undefined : permissions.data
  const discardPermissions = () => setPermissionRequest(null)
  useEffect(() => {
    const discard = () => setPermissionRequest(null)
    const unsubscribe = updates?.subscribe(discard)
    const unobserve = updates?.observeState(discard)
    const unwatch = queryClient.getQueryCache().subscribe((event) => {
      const queryKey: unknown = event.query.queryKey
      if (
        event.type === 'updated' &&
        Array.isArray(queryKey) &&
        queryKey[0] === 'activity' &&
        ['fetch', 'invalidate', 'success', 'error'].includes(event.action.type)
      )
        discard()
    })
    document.addEventListener('visibilitychange', discard)
    window.addEventListener('offline', discard)
    return () => {
      unsubscribe?.()
      unobserve?.()
      unwatch()
      document.removeEventListener('visibilitychange', discard)
      window.removeEventListener('offline', discard)
    }
  }, [queryClient, updates])
  const checkPermissions = () => setPermissionRequest(++nextPermissionRequest.current)
  const openConsentReview = () => {
    consentTrigger.current =
      document.activeElement instanceof HTMLElement ? document.activeElement : null
    setConsentOpen(true)
    checkPermissions()
  }
  useEffect(() => {
    if (consentOpen)
      document.getElementById('activity-consent')?.scrollIntoView({ block: 'nearest' })
  }, [consentOpen])
  const closeConsentReview = () => {
    setConsentOpen(false)
    discardPermissions()
    const trigger =
      consentTrigger.current ?? document.querySelector<HTMLElement>('[data-route-focus]')
    requestAnimationFrame(() => trigger?.focus({ preventScroll: true }))
    if (router.state.location.hash === 'activity-consent')
      void navigate({ search: (current) => current, hash: '', replace: true })
  }
  const configured = permission?.configured_libraries ?? []
  const enabled = data.enabled_libraries
  const capability = (library: string) =>
    permission?.capabilities.find((item) => item.library === library)
  const preparedCount = (library: ActivityLibraryView) => library.prepared?.total ?? 0
  const consentCandidates = configured.filter((item) =>
    (capability(item.library)?.destinations ?? []).some(
      (destination) => destination.state === 'allowed' && !destination.consented,
    ),
  )
  const selectable = consentCandidates.filter((item) => {
    const state = capability(item.library)?.state ?? 'not_ready'
    return (
      permission?.backup_ready === true &&
      !connection &&
      ['allowed', 'partially_allowed'].includes(state) &&
      preparedCount(item) > 0
    )
  })
  const selectableNames = new Set(selectable.map((item) => item.library))
  const [selection, setSelection] = useState(() => ({
    source: permission,
    names: new Set<string>(),
  }))
  if (selection.source !== permission) setSelection({ source: permission, names: new Set() })
  const selected = selection.source === permission ? selection.names : new Set<string>()
  const [status, setStatus] = useState('')
  const [expandedId, setExpandedId] = useState<string | null>(null)
  const mutation = useMutation({
    mutationFn: ({ path, body }: { path: string; body?: unknown }) =>
      http.activityCommand(path, body),
    retry: false,
    onSuccess: async () => {
      closeConsentReview()
      setSelection({ source: permission, names: new Set() })
      setStatus('Activity state refreshed from the authoritative server.')
      await invalidateForegroundQueries(queryClient, { queryKey: ['activity'] })
    },
    onError: async (error, command) => {
      discardPermissions()
      if (
        error instanceof AmbiguousMutationError ||
        error instanceof BuildMismatchError ||
        (error instanceof ApiError && error.status >= 500)
      ) {
        try {
          const refreshed = await query.refetch()
          const outcome = refreshed.isError ? 'unknown' : activityOutcome(refreshed.data, command)
          setStatus(
            outcome === 'applied'
              ? 'The requested activity change was applied, confirmed by the authoritative server.'
              : 'The activity outcome could not be confirmed. No command was resent.',
          )
        } catch {
          setStatus('The activity outcome could not be confirmed. No command was resent.')
        }
        return
      }
      setStatus(
        error instanceof ApiError &&
          command.path === 'activity/consent' &&
          error.code === 'capability'
          ? 'Write permissions changed during confirmation. No permission was granted. Review the refreshed included and excluded destinations, then confirm again.'
          : error instanceof ApiError &&
              command.path === 'activity/consent' &&
              error.code === 'backup'
            ? 'Backup readiness changed. No permission was granted. Restore backup readiness before confirming again.'
            : 'The requested activity change failed safely. Try again.',
      )
      await invalidateForegroundQueries(queryClient, { queryKey: ['activity'] })
    },
  })
  const commandStatusRef = useRef<HTMLParagraphElement>(null)
  useEffect(() => {
    if (!mutation.isError || !status) return
    commandStatusRef.current?.focus({ preventScroll: true })
    commandStatusRef.current?.scrollIntoView({ block: 'center' })
  }, [mutation.isError, status])
  const selectedGrants = selectable.flatMap((library) => {
    const name = library.library
    return selected.has(name)
      ? [
          {
            library: name,
            destinations: (capability(name)?.destinations ?? [])
              .filter((destination) => destination.state === 'allowed')
              .map((destination) => destination.destination),
          },
        ]
      : []
  })
  const toggle = (library: string) => {
    const next = new Set(selected)
    if (next.has(library)) next.delete(library)
    else next.add(library)
    setSelection({ source: permission, names: next })
  }
  const restoreFocus = (key: string, version: number) => {
    if (focusVersion.current !== version) return
    setFocusRequest({ key, version })
  }
  const changeSearch = (next: ActivitySearchUpdate, focusKey?: string) => {
    setExpandedId(null)
    ++pageNavigationVersion.current
    const resetsPage =
      Object.hasOwn(next, 'q') ||
      Object.hasOwn(next, 'eventType') ||
      Object.hasOwn(next, 'rangeDays') ||
      Object.hasOwn(next, 'pageSize')
    if (resetsPage) requestedPage.current = 1
    const base: ActivitySearch = {
      eventType: next.eventType ?? search.eventType,
      rangeDays: next.rangeDays ?? search.rangeDays,
      pageSize: next.pageSize ?? search.pageSize,
      page: resetsPage ? 1 : (next.page ?? 1),
      ...(!Object.hasOwn(next, 'q') && search.q ? { q: search.q } : {}),
      ...(next.q ? { q: next.q } : {}),
    }
    const key = focusKey ?? document.activeElement?.getAttribute('data-activity-focus') ?? undefined
    const version = ++focusVersion.current
    pendingLocalNavigations.current.push({
      q: base.q ?? '',
      draftVersion: queryDraftVersion.current,
    })
    void navigate({ search: base, replace: true }).then(() => {
      if (key) restoreFocus(key, version)
    })
  }
  const changeQuery = (value: string) => {
    ++focusVersion.current
    setQueryText(value)
    ++queryDraftVersion.current
    if (queryCommit.current !== null) window.clearTimeout(queryCommit.current)
    queryCommit.current = window.setTimeout(() => {
      queryCommit.current = null
      changeSearch({ q: value || undefined }, 'search')
    }, 150)
  }
  const clearFilters = () => {
    ++queryDraftVersion.current
    if (queryCommit.current !== null) {
      window.clearTimeout(queryCommit.current)
      queryCommit.current = null
    }
    setQueryText('')
    changeSearch({ q: undefined, eventType: 'all' }, 'clear-filters')
  }
  const goToPage = (target: number | ((page: number) => number), focusKey?: string) => {
    const requested = typeof target === 'function' ? target(requestedPage.current) : target
    const clamped = Math.max(1, Math.min(ledger.page_count, requested))
    requestedPage.current = clamped
    ++pageNavigationVersion.current
    setExpandedId(null)
    const key = focusKey ?? document.activeElement?.getAttribute('data-activity-focus') ?? undefined
    const restoredKey =
      (clamped === 1 && (key === 'first' || key === 'previous')) ||
      (clamped === ledger.page_count && (key === 'next' || key === 'last'))
        ? 'page-jump'
        : key
    const version = ++focusVersion.current
    const next = { ...search, page: clamped }
    pendingLocalNavigations.current.push({
      q: next.q ?? '',
      draftVersion: queryDraftVersion.current,
    })
    void navigate({ search: () => next }).then(() => {
      if (restoredKey) restoreFocus(restoredKey, version)
    })
  }
  const suspensions = data.suspensions
  const writes = ['claimed', 'applying', 'reconciling'].reduce(
    (total, state) => total + (data.counts[state] ?? 0),
    0,
  )
  const providerRecovery = data.provider_recovery ?? []
  const dormantRecovery = providerRecovery
    .filter((item) => item.state === 'dormant')
    .reduce((total, item) => total + item.affected_count, 0)
  // One condition remains focused for the banner/action, but recovery totals
  // must reflect every independently paused/checking connection.
  const connectionRecovery = connectionConditions.reduce(
    (total, item) => total + (item.state === 'recovered' ? 0 : item.affected),
    0,
  )
  const recoveryCount = suspensions.length + dormantRecovery + connectionRecovery
  const reasons = data.waiting
  const waitingEvent = data.current_waiting
  const needsConsent = data.configured_libraries.length > 0
  const enabling = mutation.isPending && mutation.variables.path === 'activity/consent'
  // Library states the same offer compactly, from this same reading, so the two
  // routes into the decision can never disagree about it (#639).
  const consent = readWriteConsent(permission ? { ...data, ...permission } : data, {
    enabling,
    // A connection that needs an owner check is the one condition that makes
    // enabling writes the wrong next step. Preparation still running is not:
    // permission given now covers the work preparation is still finding.
    connectionUnavailable: Boolean(connection),
  })
  const offer = consent.scopes
  const offeredChanges = consent.changes
  const consentState = consent.state
  const consentBlocked = consent.blocked
  const consentActionable = consent.actionable
  // One reading of "prepared work is waiting on the owner", shared by the
  // masthead pill, the status panel and the current waiting item. #639 asks
  // that no screen say no action is required while its own displayed prepared
  // work requires consent, and three cascades each deciding that separately is
  // how one of them came to say it directly above the count that refutes it.
  const permissionNeeded = consentState === 'off' && reasons.awaiting_permission > 0
  const hasAuthority = enabled.length > 0 || suspensions.length > 0 || needsConsent
  const showOperationalSummary =
    !permissionNeeded || Boolean(connection) || recoveryCount > 0 || data.paused
  const consentFeedback = consentOpen && mutation.variables?.path === 'activity/consent'
  const commandStatus = (
    <p
      className="activity-command-status"
      ref={commandStatusRef}
      tabIndex={-1}
      role={mutation.isError ? 'alert' : 'status'}
    >
      {status}
    </p>
  )
  return (
    <div
      className="cm-activity-route"
      onFocusCapture={() => {
        ++focusVersion.current
      }}
    >
      <header className="cm-page-heading" tabIndex={-1} data-route-focus>
        <div>
          <h1>Activity and recovery</h1>
          <p>
            See what needs attention now and inspect the durable record of reviews, policy changes,
            safeguards, and verified updates.
          </p>
        </div>
        <span className="cm-activity-calm">
          {connection?.state === 'checking'
            ? 'Connection checking'
            : connection?.state === 'paused'
              ? 'Connection paused'
              : recoveryCount
                ? 'Recovery available'
                : permissionNeeded
                  ? 'Action needed'
                  : consentState === 'enabling'
                    ? 'Enabling writes'
                    : consentState === 'enabled' || consentState === 'applying'
                      ? 'Writes enabled'
                      : data.paused
                        ? 'New work paused'
                        : 'No action required'}
        </span>
      </header>
      <section className="cm-activity-ledger" aria-labelledby="activity-ledger-title">
        <header className="cm-activity-ledger-heading">
          <div>
            <h2 id="activity-ledger-title">Activity ledger</h2>
            <p>
              One record for review decisions, policy changes, safeguards, writes, and verification.
            </p>
          </div>
          <label className="cm-activity-range">
            <span>Date range</span>
            <Dropdown
              compact
              focusKey="date-range"
              ariaLabel="Activity date range"
              value={String(search.rangeDays)}
              options={[
                { value: '7', label: 'Last 7 days' },
                { value: '30', label: 'Last 30 days' },
                { value: '90', label: 'Last 90 days' },
              ]}
              onChange={(value) =>
                changeSearch(
                  { rangeDays: Number(value) as ActivitySearch['rangeDays'] },
                  'date-range',
                )
              }
            />
          </label>
        </header>
        <div className="cm-activity-tools">
          <label className="cm-activity-search">
            <span>Search activity</span>
            <input
              data-activity-focus="search"
              aria-label="Search activity"
              type="search"
              value={queryText}
              placeholder="Work, outcome, or status"
              onChange={(event) => changeQuery(event.target.value)}
            />
          </label>
          <div className="cm-activity-filters" aria-label="Filter activity by type">
            {typeOptions.map((item) => (
              <button
                data-activity-focus={`type-${item.id}`}
                key={item.id}
                aria-pressed={search.eventType === item.id}
                onClick={() => changeSearch({ eventType: item.id }, `type-${item.id}`)}
              >
                {item.label}
              </button>
            ))}
          </div>
        </div>
        <div className="cm-activity-columns" aria-hidden="true">
          <span>When</span>
          <span>Type</span>
          <span>Work and field</span>
          <span>Change</span>
          <span>Result</span>
          <span />
        </div>
        <div className="cm-activity-events">
          {data.events.length ? (
            data.events.map((event, index) => {
              const changeId = event.change_id || event.event_id
              const expanded = expandedId === changeId
              const currentDate = dateParts(event.occurred_at)
              const previousDate = index
                ? dateParts(data.events[index - 1]?.occurred_at ?? null).day
                : null
              return (
                <div className="cm-activity-event-group" key={changeId}>
                  {currentDate.day !== previousDate ? <h3>{currentDate.day}</h3> : null}
                  <article className={expanded ? 'is-expanded' : ''}>
                    <time>{currentDate.time}</time>
                    <span className={`cm-activity-category is-${event.category}`}>
                      {categoryLabel[event.category]}
                    </span>
                    <div className="cm-activity-work">
                      <strong title={event.work_label}>{event.work_label}</strong>
                      <span>
                        {event.field_label} · {event.library}
                      </span>
                    </div>
                    {/*
                      The arrow in `change` is a glyph most screen readers drop,
                      so the announced row carries the same change as a whole
                      sentence. Both come from the one projection, so they
                      cannot come to disagree.
                    */}
                    <div className="cm-activity-change" aria-label={event.change_detail}>
                      <span aria-hidden="true">{event.change}</span>
                    </div>
                    <b>
                      {stateLabel(
                        event.state,
                        event.destination === 'description' && event.origin === 'adviser'
                          ? 'description'
                          : event.event_type !== 'policy' &&
                              event.destination === 'cover' &&
                              ['adviser', 'automatic_pipeline', 'owner_retry'].includes(
                                event.origin,
                              )
                            ? 'cover'
                            : undefined,
                      )}
                    </b>
                    <button
                      aria-expanded={expanded}
                      aria-controls={`activity-evidence-${event.event_id}`}
                      onClick={() => setExpandedId(expanded ? null : changeId)}
                    >
                      {expanded ? 'Hide details' : 'Details'}
                    </button>
                    {expanded ? (
                      <div
                        className={`cm-activity-evidence${
                          event.policy_changes.length ? ' has-policy-changes' : ''
                        }`}
                        id={`activity-evidence-${event.event_id}`}
                      >
                        {event.policy_changes.length ? (
                          <section
                            className="cm-activity-policy-changes"
                            aria-label={`${String(event.policy_changes.length)} exact policy changes`}
                          >
                            <header>
                              <strong>
                                {event.policy_changes.length} exact{' '}
                                {event.policy_changes.length === 1 ? 'change' : 'changes'}
                              </strong>
                              <span>Field and setting</span>
                              <span>From</span>
                              <span>To</span>
                            </header>
                            <ul>
                              {event.policy_changes.map((change) => (
                                <li key={`${change.field}-${change.setting}`}>
                                  <span>
                                    <strong>{change.field}</strong>
                                    <small>{change.setting}</small>
                                  </span>
                                  <b>{change.previous}</b>
                                  <b>{change.next}</b>
                                </li>
                              ))}
                            </ul>
                          </section>
                        ) : null}
                        <dl>
                          {event.evidence.map((item) => (
                            <div key={item.label}>
                              <dt>{item.label}</dt>
                              <dd>{item.value}</dd>
                            </div>
                          ))}
                        </dl>
                        {event.transition_id ? (
                          <ActivityTransitions key={changeId} eventId={event.transition_id} />
                        ) : null}
                      </div>
                    ) : null}
                  </article>
                </div>
              )
            })
          ) : (
            <div className="cm-activity-empty">
              <strong>No activity matches these filters</strong>
              <span>Try another term or activity type.</span>
              <button data-activity-focus="clear-filters" onClick={clearFilters}>
                Clear filters
              </button>
            </div>
          )}
        </div>
        <footer className="cm-activity-pagination">
          <div className="cm-activity-pagination-summary">
            <strong>
              {ledger.total
                ? `${String((ledger.page - 1) * ledger.page_size + 1)}–${String(Math.min(ledger.page * ledger.page_size, ledger.total))}`
                : '0'}
            </strong>
            <span>of {ledger.total} events</span>
            <label className="cm-activity-page-size">
              <span>Rows</span>
              <Dropdown
                compact
                focusKey="page-size"
                ariaLabel="Rows per activity page"
                value={String(search.pageSize)}
                options={[
                  { value: '25', label: '25 per page' },
                  { value: '50', label: '50 per page' },
                  { value: '100', label: '100 per page' },
                ]}
                onChange={(value) =>
                  changeSearch(
                    { pageSize: Number(value) as ActivitySearch['pageSize'] },
                    'page-size',
                  )
                }
              />
            </label>
          </div>
          <nav className="cm-activity-pagination-nav" aria-label="Activity pages">
            <button
              data-activity-focus="first"
              disabled={ledger.page === 1}
              onClick={() => goToPage(1, 'first')}
            >
              First
            </button>
            <button
              data-activity-focus="previous"
              disabled={ledger.page === 1}
              onClick={() => goToPage((page) => page - 1, 'previous')}
            >
              Previous
            </button>
            <PageJump
              key={`${String(ledger.page)}:${search.q ?? ''}:${search.eventType}:${String(search.rangeDays)}:${String(search.pageSize)}`}
              page={ledger.page}
              pageCount={ledger.page_count}
              onCommit={goToPage}
              onEdit={() => {
                ++focusVersion.current
              }}
            />
            <button
              data-activity-focus="next"
              disabled={ledger.page >= ledger.page_count}
              onClick={() => goToPage((page) => page + 1, 'next')}
            >
              Next
            </button>
            <button
              data-activity-focus="last"
              disabled={ledger.page >= ledger.page_count}
              onClick={() => goToPage(ledger.page_count, 'last')}
            >
              Last
            </button>
          </nav>
        </footer>
      </section>
      {consentActionable ? (
        <section
          className={`cm-write-consent is-${consentState}`}
          aria-labelledby="write-consent-title"
          aria-live="polite"
        >
          <div className="cm-write-consent-summary">
            <div>
              <h2 id="write-consent-title">
                {consentState === 'off'
                  ? permission?.backup_ready === true
                    ? `${String(offeredChanges)} prepared ${offeredChanges === 1 ? 'change is' : 'changes are'} ready. Writes are off.`
                    : 'Writes are off. Review permissions when you are ready.'
                  : consentState === 'enabling'
                    ? 'Enabling writes…'
                    : consentState === 'enabled'
                      ? 'Writes are enabled.'
                      : 'Applying prepared changes.'}
              </h2>
              <p>
                {consentState === 'off'
                  ? 'Open permission review to check backup readiness and the destinations available for writes.'
                  : consentState === 'enabling'
                    ? 'Confirming permission with the authoritative server. No change is sent twice.'
                    : consentState === 'enabled'
                      ? 'Permission is confirmed. Mechanical Librarian is starting the prepared changes.'
                      : `${String(reasons.applying)} ${reasons.applying === 1 ? 'change is' : 'changes are'} in progress; the remaining ${String(reasons.scheduled)} stay ready.`}
              </p>
            </div>
            <dl>
              <div>
                <dt>Awaiting permission</dt>
                <dd>{reasons.awaiting_permission}</dd>
              </div>
              <div>
                <dt>Ready to apply</dt>
                <dd>{reasons.scheduled}</dd>
              </div>
              <div>
                <dt>Applying now</dt>
                <dd>{reasons.applying}</dd>
              </div>
              <div>
                <dt>Retrying</dt>
                <dd>{reasons.retrying}</dd>
              </div>
              <div>
                <dt>Held for recovery</dt>
                <dd>{reasons.held_for_recovery}</dd>
              </div>
            </dl>
          </div>
          {!consentOpen
            ? offer.map((item) => (
                <div className="cm-write-consent-scope" key={item.library}>
                  <div>
                    <strong>{item.library}</strong>
                    <span>
                      {item.eligible
                        .map(
                          (entry) =>
                            `${String(entry.prepared)} ${fieldLabel(entry.destination).toLowerCase()} ${entry.prepared === 1 ? 'change' : 'changes'}`,
                        )
                        .join(' · ') || 'No eligible destination'}
                    </span>
                  </div>
                  <dl>
                    {[...item.eligible, ...item.excluded].map((entry) => (
                      <div key={entry.destination}>
                        <dt>{fieldLabel(entry.destination)}</dt>
                        <dd>
                          {entry.state === 'allowed'
                            ? consentState === 'off' || consentState === 'enabling'
                              ? 'Writes off'
                              : 'Writes enabled'
                            : `Excluded — ${destinationReason(entry.state)}`}
                        </dd>
                      </div>
                    ))}
                  </dl>
                </div>
              ))
            : null}
          <div className="cm-write-consent-action">
            <button
              className="cm-primary"
              type="button"
              disabled={consentState !== 'off' || consentOpen}
              aria-expanded={consentOpen}
              aria-controls="activity-consent"
              onClick={openConsentReview}
            >
              {consentState === 'off'
                ? 'Review write permissions'
                : consentState === 'enabling'
                  ? 'Enabling writes…'
                  : consentState === 'applying'
                    ? 'Applying changes…'
                    : 'Writes enabled'}
            </button>
            {consentBlocked ? <p className="cm-write-consent-reason">{consentBlocked}</p> : null}
          </div>
        </section>
      ) : null}
      {consentOpen ? (
        <section className="activity-consent-ledger" id="activity-consent">
          <div>
            <p className="eyebrow">Prepared, read-only work</p>
            <h2>Enable guarded writes</h2>
            <p>
              Each write is checked before it starts, verified afterwards, and recorded durably.
            </p>
          </div>
          <p role="status">
            {permissionRequest === null
              ? 'Permission readiness has not been checked, or the library changed. Check again before selecting destinations.'
              : permissions.isFetching
                ? 'Checking backup readiness and write permissions…'
                : permissions.isError
                  ? 'Permission readiness is unavailable. No permission was granted. Try again.'
                  : permission?.backup_ready === null
                    ? 'Readiness could not be confirmed for the current library state. Check again before enabling writes.'
                    : permission?.backup_ready === false
                      ? 'Backup readiness could not be confirmed. Writes cannot be enabled.'
                      : 'Backup readiness checked. Review the included and excluded destinations.'}
          </p>
          <button
            type="button"
            onClick={checkPermissions}
            disabled={permissions.isFetching && permissionRequest !== null}
          >
            {permissionRequest === null ? 'Check write permissions' : 'Retry permission check'}
          </button>
          {permission?.example ? (
            <p>
              Example: {fieldLabel(permission.example.destination)} for {permission.example.library}{' '}
              — {displayValue(permission.example.before)} → {displayValue(permission.example.after)}
            </p>
          ) : null}
          {configured.map((library) => {
            const name = library.library
            const state = capability(name)?.state ?? 'not_ready'
            const disabled = !selectableNames.has(name)
            return (
              <div className="activity-consent-library" key={name}>
                <label>
                  <input
                    type="checkbox"
                    disabled={disabled}
                    checked={selected.has(name)}
                    onChange={() => toggle(name)}
                  />
                  <strong>{name}</strong>
                  <small>
                    {preparedCount(library)} prepared ·{' '}
                    {permission?.backup_ready === true
                      ? capabilityLabel(state)
                      : 'Readiness unconfirmed'}
                  </small>
                </label>
                <ul aria-label={`${name} included and excluded destinations`}>
                  {capability(name)?.destinations.map((entry) => (
                    <li key={entry.destination}>
                      <span>{fieldLabel(entry.destination)}</span>
                      <small>
                        {entry.state === 'allowed'
                          ? `Included · ${String(entry.prepared)} prepared`
                          : `Excluded — ${destinationReason(entry.state)}`}
                      </small>
                    </li>
                  ))}
                </ul>
              </div>
            )
          })}
          <p>
            Only the included destinations shown above are enabled. Excluded destinations remain
            off. Backup and service capability are checked again on confirmation.
          </p>
          {consentFeedback ? commandStatus : null}
          <button type="button" onClick={closeConsentReview} disabled={mutation.isPending}>
            Cancel permission review
          </button>
          <button
            className="primary-action"
            disabled={
              mutation.isPending || permission?.backup_ready !== true || selectedGrants.length === 0
            }
            onClick={() =>
              mutation.mutate({
                path: 'activity/consent',
                body: { grants: selectedGrants },
              })
            }
          >
            Confirm write permissions
          </button>
        </section>
      ) : null}
      <section
        className="cm-activity-status"
        aria-labelledby={showOperationalSummary ? 'activity-status-title' : undefined}
      >
        {showOperationalSummary ? (
          <div className="cm-activity-status-copy">
            <h2 id="activity-status-title">
              {connection?.state === 'checking'
                ? 'Checking library connection'
                : connection?.state === 'paused'
                  ? 'Library connection is paused'
                  : recoveryCount
                    ? 'Recovery needs attention'
                    : data.paused
                      ? 'New work is paused'
                      : 'No intervention needed'}
            </h2>
            <p>
              {connection?.state === 'checking'
                ? 'Preparation remains safely paused while the authoritative connection check settles.'
                : connection?.state === 'paused'
                  ? `${String(connection.affected)} preparation ${connection.affected === 1 ? 'work remains' : 'works remain'} safely paused for an owner connection check.`
                  : dormantRecovery
                    ? `${String(dormantRecovery)} provider ${dormantRecovery === 1 ? 'scope needs' : 'scopes need'} owner correction before a future check can become eligible.`
                    : recoveryCount
                      ? 'Affected work remains held until its current state can be reconciled safely.'
                      : data.paused
                        ? 'No new work will be claimed. Work already claimed may finish its safe checkpoint; resume new work when you are ready for planned work to continue.'
                        : 'Guarded work continues automatically. Recovery controls appear only when your decision is required.'}
            </p>
          </div>
        ) : null}
        {!consentActionable ? (
          <dl>
            <div>
              <dt>Writes in progress</dt>
              <dd>{writes}</dd>
            </div>
            <div>
              <dt>Awaiting permission</dt>
              <dd>{reasons.awaiting_permission}</dd>
            </div>
            <div>
              <dt>Scheduled</dt>
              <dd>{reasons.scheduled}</dd>
            </div>
            <div>
              <dt>Retrying</dt>
              <dd>{reasons.retrying}</dd>
            </div>
            <div>
              <dt>Held for recovery</dt>
              <dd>{reasons.held_for_recovery}</dd>
            </div>
            <div>
              <dt>Recovery needed</dt>
              <dd>{recoveryCount}</dd>
            </div>
          </dl>
        ) : null}
        {waitingEvent && !permissionNeeded ? (
          <div className="cm-activity-waiting">
            <span className="cm-activity-pulse" aria-hidden="true" />
            <div>
              <strong>
                {waitingEvent.work_label} · {fieldLabel(waitingEvent.destination)} update
              </strong>
              <span>
                {data.paused
                  ? 'Resume new work when you are ready. No automatic check will start while new work is paused.'
                  : 'Next exact-current check runs automatically. No action is required.'}
              </span>
            </div>
          </div>
        ) : null}
        {providerRecovery.length ? (
          <section className="activity-recovery" aria-label="Provider recovery">
            <h3>Provider recovery</h3>
            {providerRecovery.map((item, index) => (
              <p key={`${item.state}:${String(index)}`}>
                <strong>
                  {item.state === 'waiting'
                    ? 'Waiting for a safe retry'
                    : item.state === 'checking'
                      ? 'Checking provider health'
                      : item.state === 'dormant'
                        ? 'Needs a configuration change'
                        : item.state === 'fanout'
                          ? 'Retrying affected work'
                          : item.state === 'exhausted'
                            ? 'No further retry scheduled'
                            : 'Recovered'}
                </strong>{' '}
                · {item.affected_count} affected{' '}
                {item.state === 'fanout'
                  ? item.affected_count === 1
                    ? 'work'
                    : 'works'
                  : item.affected_count === 1
                    ? 'scope'
                    : 'scopes'}
                {item.next_eligible_at === null
                  ? ''
                  : ` · next eligible ${dateParts(item.next_eligible_at).time}`}
                {item.dispatch_reasons?.length ? (
                  <span>
                    {' '}
                    · Priority:{' '}
                    {item.dispatch_reasons
                      .map((reason) => recoveryDispatchLabel[reason])
                      .join(', ')}
                  </span>
                ) : null}
              </p>
            ))}
          </section>
        ) : null}
        {hasAuthority ? (
          <details className="cm-activity-authority">
            <summary>
              <span>
                <strong>Authority and recovery</strong>
                <small>
                  {recoveryCount
                    ? `${String(recoveryCount)} recovery ${recoveryCount === 1 ? 'item' : 'items'} need review`
                    : data.paused
                      ? 'New work remains paused until you resume it'
                      : 'Write permissions and pause controls'}
                </small>
              </span>
            </summary>
            <div
              className="cm-activity-authority-actions"
              aria-label="Activity authority and recovery"
            >
              {enabled.length > 0 ? (
                <button
                  disabled={mutation.isPending}
                  onClick={() =>
                    mutation.mutate({ path: `field-effects/${data.paused ? 'resume' : 'pause'}` })
                  }
                >
                  {data.paused ? 'Resume new work' : 'Pause new work'}
                </button>
              ) : null}
              {enabled.map((library) => (
                <button
                  className="activity-revoke"
                  key={`${library.connection}:${library.library}`}
                  disabled={mutation.isPending}
                  onClick={() =>
                    mutation.mutate({
                      path: `activity/${encodeURIComponent(library.connection)}/${encodeURIComponent(library.library)}/revoke`,
                    })
                  }
                >
                  Remove write permission for {library.library}
                </button>
              ))}
              {needsConsent ? (
                <button
                  id="review-write-permissions"
                  onClick={() => (consentOpen ? closeConsentReview() : openConsentReview())}
                  aria-expanded={consentOpen}
                  aria-controls="activity-consent"
                >
                  Review write permissions
                </button>
              ) : null}
              {suspensions.length ? (
                <div className="cm-activity-recovery-facts" aria-label="Recovery facts">
                  {suspensions.map((item, index) => (
                    <p key={`${item.connection}:${item.destination ?? ''}:${String(index)}`}>
                      <strong>Recovery:</strong>{' '}
                      {fieldLabel(item.reason || 'A shared condition is being checked')}. No
                      accepted decision is retried blindly.
                    </p>
                  ))}
                </div>
              ) : null}
            </div>
          </details>
        ) : null}
      </section>
      <PreparationNotice routeIdentity={routeIdentity} />
      {!consentFeedback ? commandStatus : null}
    </div>
  )
}
