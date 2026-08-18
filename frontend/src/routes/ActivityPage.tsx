import { useMutation, useQuery } from '@tanstack/react-query'
import { useNavigate, useRouter, useRouterState } from '@tanstack/react-router'
import { useEffect, useRef, useState } from 'react'

import { AmbiguousMutationError, ApiError, BuildMismatchError } from '../api/http'
import type { ActivityEventType, ActivitySearch, ActivityView } from '../api/http'
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

const records = (value: unknown): readonly Record<string, unknown>[] =>
  Array.isArray(value)
    ? value.filter(
        (item): item is Record<string, unknown> =>
          typeof item === 'object' && item !== null && !Array.isArray(item),
      )
    : []
const text = (value: unknown, fallback = '') => (typeof value === 'string' ? value : fallback)
const count = (value: unknown) =>
  typeof value === 'number' && Number.isSafeInteger(value) && value >= 0 ? value : 0
const capabilityDestinations = (capability: Record<string, unknown> | undefined) =>
  records(capability?.destinations).map((destination) => ({
    destination: text(destination.destination),
    state: text(destination.state, 'not_ready'),
    consented: destination.consented === true,
    prepared: count(destination.prepared),
  }))
const capabilityLabel = (state: string) =>
  ({
    allowed: 'Ready for consent',
    partially_allowed: 'Some destinations are ready',
    connection_unavailable: 'Connection needs checking',
    insufficient_permission: 'Permission is unavailable',
    field_unavailable: 'A field is unavailable',
    not_ready: 'Not ready yet',
  })[state] ?? 'Not ready yet'
const stateLabel = (state: string) =>
  ({
    accept: 'Accepted',
    keep_current: 'Kept current',
    reject: 'Rejected',
    completed: 'Cover advice completed',
    unavailable: 'Cover advice unavailable',
    failed: 'Cover advice failed safely',
    selected: 'Owner cover choice recorded',
    retry: 'Manual cover retry',
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
  })[state] ?? 'Recorded'
const categoryLabel: Record<ActivityEventType, string> = {
  review: 'Review',
  policy: 'Policy',
  metadata: 'Metadata',
  safety: 'Safety',
  unknown: 'Recorded',
}
const typeOptions = [
  { id: 'all', label: 'All' },
  { id: 'review', label: 'Reviews' },
  { id: 'policy', label: 'Policy' },
  { id: 'metadata', label: 'Metadata' },
  { id: 'safety', label: 'Safety' },
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

export default function ActivityPage({ initial }: { initial: ActivityView }) {
  const { http, queryClient } = useReviewContext()
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
    queryFn: ({ signal }) => http.getActivity(search, signal),
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
  const connection =
    connectionConditions.find((item) => item.state !== 'recovered') ?? connectionConditions[0]
  const ledger = data.ledger
  const configured = records(data.configured_libraries)
  const enabled = records(data.enabled_libraries)
  const capabilities = records(data.capabilities)
  const capability = (library: string) =>
    capabilities.find((item) => text(item.library) === library)
  const preparedCount = (library: Record<string, unknown>) =>
    typeof library.prepared === 'object' && library.prepared
      ? count((library.prepared as Record<string, unknown>).total)
      : 0
  const consentCandidates = configured.filter((item) =>
    capabilityDestinations(capability(text(item.library))).some(
      (destination) => destination.state === 'allowed' && !destination.consented,
    ),
  )
  const selectable = consentCandidates.filter((item) => {
    const state = text(capability(text(item.library))?.state, 'not_ready')
    return (
      data.backup_ready &&
      ['allowed', 'partially_allowed'].includes(state) &&
      preparedCount(item) > 0
    )
  })
  const selectableNames = new Set(selectable.map((item) => text(item.library)))
  const [selection, setSelection] = useState(() => ({ source: data, names: new Set<string>() }))
  if (selection.source !== data)
    setSelection({
      source: data,
      names: new Set([...selection.names].filter((name) => selectableNames.has(name))),
    })
  const selected =
    selection.source === data
      ? selection.names
      : new Set([...selection.names].filter((name) => selectableNames.has(name)))
  const [status, setStatus] = useState('')
  const [expandedId, setExpandedId] = useState<string | null>(null)
  const [consentOpen, setConsentOpen] = useState(false)
  const mutation = useMutation({
    mutationFn: ({ path, body }: { path: string; body?: unknown }) =>
      http.activityCommand(path, body),
    retry: false,
    onSuccess: async () => {
      setSelection({ source: data, names: new Set() })
      setStatus('Activity state refreshed from the authoritative server.')
      await queryClient.invalidateQueries({ queryKey: ['activity'] })
    },
    onError: async (error, command) => {
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
      setStatus('The requested activity change failed safely. Try again.')
      await queryClient.invalidateQueries({ queryKey: ['activity'] })
    },
  })
  const selectedGrants = selectable.flatMap((library) => {
    const name = text(library.library)
    return selected.has(name)
      ? [
          {
            library: name,
            destinations: capabilityDestinations(capability(name))
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
    setSelection({ source: data, names: next })
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
  const suspensions = records(data.suspensions)
  const writes = ['claimed', 'applying', 'reconciling'].reduce(
    (total, state) => total + count(data.counts[state]),
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
  const needsConsent = consentCandidates.length > 0 || (!enabled.length && configured.length > 0)
  const enabling = mutation.isPending && mutation.variables.path === 'activity/consent'
  // Library states the same offer compactly, from this same reading, so the two
  // routes into the decision can never disagree about it (#639).
  const consent = readWriteConsent(data, {
    enabling,
    // A connection that needs an owner check is the one condition that makes
    // enabling writes the wrong next step. Preparation still running is not:
    // permission given now covers the work preparation is still finding.
    connectionUnavailable: Boolean(connection),
  })
  const offer = consent.scopes
  const offeredGrants = consent.grants
  const offeredChanges = consent.changes
  const consentState = consent.state
  const consentBlocked = consent.blocked
  const consentActionable = consent.actionable

  const hasAuthority = enabled.length > 0 || suspensions.length > 0 || needsConsent

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
              : connection?.state === 'recovered'
                ? 'Connection recovered'
                : recoveryCount
                  ? 'Recovery available'
                  : consentState === 'off' && offeredGrants.length
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
      <PreparationNotice routeIdentity={routeIdentity} />
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
                  ? `${String(offeredChanges)} prepared ${offeredChanges === 1 ? 'change is' : 'changes are'} ready. Writes are off.`
                  : consentState === 'enabling'
                    ? 'Enabling writes…'
                    : consentState === 'enabled'
                      ? 'Writes are enabled.'
                      : 'Applying prepared changes.'}
              </h2>
              <p>
                {consentState === 'off'
                  ? `Enable writes for ${offer.map((item) => item.library).join(', ')} to apply these prepared changes.`
                  : consentState === 'enabling'
                    ? 'Confirming permission with the authoritative server. No change is sent twice.'
                    : consentState === 'enabled'
                      ? 'Permission is confirmed. Mechanical Librarian is starting the prepared changes.'
                      : `${String(reasons.applying)} ${reasons.applying === 1 ? 'change is' : 'changes are'} in progress; the remaining ${String(reasons.scheduled)} stay ready.`}
              </p>
            </div>
            <dl>
              <div>
                <dt>Ready for permission</dt>
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
          {offer.map((item) => (
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
          ))}
          <div className="cm-write-consent-action">
            <button
              className="cm-primary"
              type="button"
              disabled={consentState !== 'off' || consentBlocked !== null}
              onClick={() =>
                mutation.mutate({
                  path: 'activity/consent',
                  body: { grants: offeredGrants },
                })
              }
            >
              {consentState === 'off'
                ? 'Enable writes'
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
      <section className="cm-activity-status" aria-labelledby="activity-status-title">
        <div className="cm-activity-status-copy">
          <h2 id="activity-status-title">
            {connection?.state === 'checking'
              ? 'Checking library connection'
              : connection?.state === 'paused'
                ? 'Library connection is paused'
                : connection?.state === 'recovered'
                  ? 'Library connection recovered'
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
                : connection?.state === 'recovered'
                  ? 'Preparation is resuming from its retained checkpoint. No work needs recovery from this connection.'
                  : dormantRecovery
                    ? `${String(dormantRecovery)} provider ${dormantRecovery === 1 ? 'scope needs' : 'scopes need'} owner correction before a future check can become eligible.`
                    : recoveryCount
                      ? 'Affected work remains held until its current state can be reconciled safely.'
                      : data.paused
                        ? 'No new work will be claimed. Work already claimed may finish its safe checkpoint; resume new work when you are ready for planned work to continue.'
                        : 'Guarded work continues automatically. Recovery controls appear only when your decision is required.'}
          </p>
        </div>
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
        {waitingEvent ? (
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
                  key={`${text(library.connection)}:${text(library.library)}`}
                  disabled={mutation.isPending}
                  onClick={() =>
                    mutation.mutate({
                      path: `activity/${encodeURIComponent(text(library.connection))}/${encodeURIComponent(text(library.library))}/revoke`,
                    })
                  }
                >
                  Remove write permission for {text(library.library)}
                </button>
              ))}
              {needsConsent ? (
                <button
                  onClick={() => setConsentOpen((value) => !value)}
                  aria-expanded={consentOpen}
                  aria-controls="activity-consent"
                >
                  Review write permissions
                </button>
              ) : null}
              {suspensions.length ? (
                <div className="cm-activity-recovery-facts" aria-label="Recovery facts">
                  {suspensions.map((item, index) => (
                    <p key={`${text(item.connection)}:${text(item.destination)}:${String(index)}`}>
                      <strong>Recovery:</strong>{' '}
                      {fieldLabel(text(item.reason, 'A shared condition is being checked'))}. No
                      accepted decision is retried blindly.
                    </p>
                  ))}
                </div>
              ) : null}
              {consentOpen ? (
                <section className="activity-consent-ledger" id="activity-consent">
                  <div>
                    <p className="eyebrow">Prepared, read-only work</p>
                    <h2>Enable guarded writes</h2>
                    <p>
                      Each write is checked before it starts, verified afterwards, and recorded
                      durably.
                    </p>
                  </div>
                  {configured.map((library) => {
                    const name = text(library.library)
                    const state = text(capability(name)?.state, 'not_ready')
                    const disabled = !selectableNames.has(name)
                    return (
                      <label className="activity-consent-library" key={name}>
                        <input
                          type="checkbox"
                          disabled={disabled}
                          checked={selected.has(name)}
                          onChange={() => toggle(name)}
                        />
                        <strong>{name}</strong>
                        <small>
                          {preparedCount(library)} prepared · {capabilityLabel(state)}
                        </small>
                      </label>
                    )
                  })}
                  <button
                    className="primary-action"
                    disabled={
                      mutation.isPending || !data.backup_ready || selectedGrants.length === 0
                    }
                    onClick={() =>
                      mutation.mutate({
                        path: 'activity/consent',
                        body: { grants: selectedGrants },
                      })
                    }
                  >
                    Enable selected libraries
                  </button>
                </section>
              ) : null}
            </div>
          </details>
        ) : null}
      </section>
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
          <span>Outcome</span>
          <span>Status</span>
          <span />
        </div>
        <div className="cm-activity-events">
          {data.events.length ? (
            data.events.map((event, index) => {
              const expanded = expandedId === event.event_id
              const currentDate = dateParts(event.occurred_at)
              const previousDate = index
                ? dateParts(data.events[index - 1]?.occurred_at ?? null).day
                : null
              return (
                <div className="cm-activity-event-group" key={event.event_id}>
                  {currentDate.day !== previousDate ? <h3>{currentDate.day}</h3> : null}
                  <article className={expanded ? 'is-expanded' : ''}>
                    <time>{currentDate.time}</time>
                    <span className={`cm-activity-category is-${event.event_type}`}>
                      {categoryLabel[event.event_type]}
                    </span>
                    <div className="cm-activity-outcome">
                      <strong title={event.work_label}>{event.work_label}</strong>
                      <span>
                        {fieldLabel(event.destination)} · {event.library}
                      </span>
                    </div>
                    <b>{stateLabel(event.state)}</b>
                    <button
                      aria-expanded={expanded}
                      aria-controls={`activity-evidence-${event.event_id}`}
                      onClick={() => setExpandedId(expanded ? null : event.event_id)}
                    >
                      {expanded ? 'Close' : 'Inspect'}
                    </button>
                    {expanded ? (
                      <div
                        className="cm-activity-evidence"
                        id={`activity-evidence-${event.event_id}`}
                      >
                        <header>
                          <strong>Durable evidence</strong>
                          <span>Recorded with this outcome</span>
                        </header>
                        <dl>
                          {event.evidence.map((item) => (
                            <div key={item.label}>
                              <dt>{item.label}</dt>
                              <dd>{item.value}</dd>
                            </div>
                          ))}
                        </dl>
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
      <p className="activity-command-status" role={mutation.isError ? 'alert' : 'status'}>
        {status}
      </p>
    </div>
  )
}
