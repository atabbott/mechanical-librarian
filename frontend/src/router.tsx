import { invalidateForegroundQueries } from './queryWork'
import { invalidateBackgroundQueries } from './queryWork'
import { queryWorkClass, useRequiredQuery as useQuery, useRetainedQuery } from './queryWork'
import { QueryClient, queryOptions, useMutation } from '@tanstack/react-query'
import {
  Link,
  Navigate,
  Outlet,
  createLink,
  createRootRouteWithContext,
  createRoute,
  createRouter,
  useRouter,
  useRouterState,
} from '@tanstack/react-router'
import {
  Suspense,
  createContext,
  useCallback,
  useContext,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from 'react'

import type { ComponentPropsWithRef } from 'react'
import type { createMemoryHistory } from '@tanstack/react-router'
import settingsVocabulary from '../../src/mechanical_librarian/interfaces/web/settings_vocabulary.json'
import { ApiError, BuildMismatchError, SessionExpiredError, refusalCopy } from './api/http'
import type {
  ActivitySearch,
  ScanState,
  AutomaticSafetyView,
  DetailSearch,
  LibraryLens,
  LibrarySearch,
  PolicySearch,
  PolicyStateView,
  PreparationView,
  ProviderThrottleEntry,
  ReviewHttp,
  SettingsView,
  ShadowPostureView,
} from './api/http'
import { ChunkRecoveryError, recoverableLazy, reloadCurrentApplication } from './chunkRecovery'
import { CountPhrases } from './components/CountPhrases'
import { LiveStreamNotice } from './components/LiveStreamNotice'
import {
  preparationInvalidationDebtKey,
  useLiveInvalidation,
  useLiveStreamState,
} from './liveInvalidation'
import type { LiveUpdateStream } from './liveUpdates'
import { formatWhen } from './dates'
import { preparationOutcomes, scanStateLabels } from './labels'
import { useLibraryName } from './libraryNames'
import { handoffNeedsLibraryNames } from './writeConsent'

const LibraryPage = recoverableLazy(() => import('./routes/LibraryPage'))
const DetailPage = recoverableLazy(() => import('./routes/DetailPage'))
const PolicyPage = recoverableLazy(() => import('./routes/PolicyPage'))
const ActivityPage = recoverableLazy(() => import('./routes/ActivityPage'))
const SettingsPage = recoverableLazy(() => import('./routes/SettingsPage'))

export interface RouterContext {
  http: ReviewHttp
  queryClient: QueryClient
  preparation: ReturnType<typeof preparationQuery>
  /**
   * The tab's one live-update stream, absent where a composition has none.
   *
   * Absent is a supported posture rather than a broken one: every surface falls
   * back to the polling cadence it had before the stream existed, and says it
   * is doing so. That is also exactly the degraded posture, so the fallback is
   * exercised by the ordinary case rather than only by a failure (#641).
   */
  updates?: LiveUpdateStream
}
const bounded = (value: unknown) =>
  typeof value === 'string' && value.length > 0 && value.length <= 128 ? value : undefined
const libraryLens = (value: unknown): LibraryLens =>
  value === 'needs_attention' || value === 'no_attention_needed' || value === 'not_matched'
    ? value
    : 'all'
export const normalizeLibrarySearch = (search: Record<string, unknown>): LibrarySearch => {
  const lens = libraryLens(search.lens)
  const q = bounded(search.q)
  const cursor = bounded(search.cursor)
  const attentionCursor = bounded(search.attentionCursor)
  const preview = bounded(search.preview)
  // A work with no current review case has no case identity to preview, so its
  // selection is owned by its own key rather than by borrowing ``preview``.
  const work = bounded(search.work)
  const field = bounded(search.field)
  const preparation = bounded(search.preparation)
  return {
    lens,
    ...(q ? { q } : {}),
    ...(cursor ? { cursor } : {}),
    ...(attentionCursor ? { attentionCursor } : {}),
    ...(preview ? { preview } : {}),
    ...(preview ? {} : work ? { work } : {}),
    ...(field ? { field } : {}),
    ...(preparation ? { preparation } : {}),
    ...(search.attention === 'open' ? { attention: 'open' } : {}),
  }
}
export const normalizeDetailSearch = (search: Record<string, unknown>): DetailSearch => {
  const field = bounded(search.field)
  const lens = libraryLens(search.lens)
  const subview = search.subview === 'history' ? 'history' : 'evidence'
  const q = bounded(search.q)
  const cursor = bounded(search.cursor)
  const attentionCursor = bounded(search.attentionCursor)
  const preparation = bounded(search.preparation)
  return {
    ...(field ? { field } : {}),
    lens,
    subview,
    ...(q ? { q } : {}),
    ...(cursor ? { cursor } : {}),
    ...(attentionCursor ? { attentionCursor } : {}),
    ...(preparation ? { preparation } : {}),
    ...(search.attention === 'open' ? { attention: 'open' } : {}),
  }
}
export const normalizePolicySearch = (search: Record<string, unknown>): PolicySearch => ({
  section:
    search.section === 'publication' ||
    search.section === 'curation' ||
    search.section === 'unsupported'
      ? search.section
      : 'identity',
})
export const normalizeActivitySearch = (search: Record<string, unknown>): ActivitySearch => {
  const eventType = ['review', 'policy', 'metadata', 'safety', 'unknown'].includes(
    String(search.eventType),
  )
    ? (String(search.eventType) as ActivitySearch['eventType'])
    : 'all'
  const rangeDays = [7, 90].includes(Number(search.rangeDays))
    ? (Number(search.rangeDays) as 7 | 90)
    : 30
  const pageSize = [50, 100].includes(Number(search.pageSize))
    ? (Number(search.pageSize) as 50 | 100)
    : 25
  const requestedPage = Number(search.page)
  const page =
    Number.isSafeInteger(requestedPage) && requestedPage >= 1 && requestedPage <= 1_000_000
      ? requestedPage
      : 1
  const q = bounded(search.q)
  // The router rewrites the address to this result on entry, so an unknown
  // filter is kept as a flag rather than lost; the flag itself is kept so a
  // second pass agrees with the first.
  const unrecognised =
    search.filter === 'unrecognised' ||
    (search.eventType !== undefined && eventType === 'all' && search.eventType !== 'all')
  return {
    eventType,
    rangeDays,
    pageSize,
    page,
    ...(q ? { q } : {}),
    ...(unrecognised ? { filter: 'unrecognised' as const } : {}),
  }
}

export type SettingsGroup =
  'connection' | 'libraries' | 'providers' | 'reviewer' | 'background' | 'access' | 'appearance'
export interface SettingsSearch {
  group: SettingsGroup
}
// `automation` and `server` were the earlier names for Background work, so
// links saved before the sections were regrouped still open a useful page.
export const normalizeSettingsSearch = (search: Record<string, unknown>): SettingsSearch => ({
  group:
    search.group === 'libraries' ||
    search.group === 'providers' ||
    search.group === 'reviewer' ||
    search.group === 'background' ||
    search.group === 'access' ||
    search.group === 'appearance'
      ? search.group
      : search.group === 'automation' || search.group === 'server'
        ? 'background'
        : 'connection',
})

export const libraryQuery = (http: ReviewHttp, search: LibrarySearch) =>
  queryOptions({
    queryKey: ['library', search.lens, search.q ?? null, search.cursor ?? null] as const,
    queryFn: (queryContext) =>
      http.getLibrary(search, queryContext.signal, queryWorkClass(queryContext)),
    staleTime: 15_000,
    retry: false,
  })
// Library-wide counts. The server counts independently of lens, cursor and
// limit, so the default landing read is the summary: it shares that request,
// and with no q no search can reach a global figure (#1284).
export const librarySummaryQuery = (http: ReviewHttp) => libraryQuery(http, { lens: 'all' })
// The narrow consent read behind Library's completion handoff (#1236). Library
// waits for it to settle, so the handoff arrives with the works rather than
// pushing them down after they are read (#1291).
export const handoffConsentQuery = (http: ReviewHttp) =>
  queryOptions({
    queryKey: ['activity', 'library-handoff'] as const,
    queryFn: (queryContext) =>
      http.getActivityConsent(queryContext.signal, queryWorkClass(queryContext)),
    staleTime: 4_000,
    retry: false,
  })
export const detailQuery = (http: ReviewHttp, id: string) =>
  queryOptions({
    queryKey: ['review-case', id] as const,
    queryFn: (queryContext) =>
      http.getCaseDetail(id, queryContext.signal, queryWorkClass(queryContext)),
    staleTime: 15_000,
    retry: false,
  })
const policyStateQuery = (http: ReviewHttp) =>
  queryOptions({
    queryKey: ['policy', 'state'] as const,
    queryFn: (queryContext) =>
      http.getPolicyState(queryContext.signal, queryWorkClass(queryContext)),
    staleTime: 15_000,
    retry: false,
  })
const shadowQuery = (http: ReviewHttp) =>
  queryOptions({
    queryKey: ['policy', 'shadow'] as const,
    queryFn: (queryContext) =>
      http.getShadowPosture(queryContext.signal, queryWorkClass(queryContext)),
    staleTime: 15_000,
    retry: false,
  })
const safetyQuery = (http: ReviewHttp) =>
  queryOptions({
    queryKey: ['policy', 'safety'] as const,
    queryFn: (queryContext) =>
      http.getAutomaticSafety(queryContext.signal, queryWorkClass(queryContext)),
    staleTime: 15_000,
    retry: false,
  })
const activityQuery = (http: ReviewHttp, search: ActivitySearch) =>
  queryOptions({
    queryKey: [
      'activity',
      search.q ?? null,
      search.eventType,
      search.rangeDays,
      search.page,
      search.pageSize,
    ] as const,
    queryFn: (queryContext) =>
      http.getActivity(search, queryContext.signal, queryWorkClass(queryContext)),
    staleTime: 4_000,
    retry: false,
  })
export const preparationQuery = (
  http: ReviewHttp,
  fallbackPolling: boolean | (() => boolean) = false,
) =>
  queryOptions({
    queryKey: ['review-preparation'] as const,
    queryFn: (queryContext) =>
      http.getPreparation(queryContext.signal, queryWorkClass(queryContext)),
    staleTime: 1_000,
    retry: false,
    structuralSharing: false,
    // The shell's always-mounted observer owns this singleton snapshot. Lazy
    // route consumers subscribe to that same query and must not turn a slow
    // route/chunk mount into a second startup read once the snapshot is stale.
    refetchOnMount: false,
    // The cadence survives only as the degraded fallback. While the stream is
    // up the snapshot is refetched when preparation actually advances, which is
    // both sooner than 1.5 seconds and, for an idle library, never.
    refetchInterval: (result) => {
      if (!(typeof fallbackPolling === 'function' ? fallbackPolling() : fallbackPolling))
        return false
      const value = result.state.data
      return value?.active || value?.actionable || value?.paused ? 1_500 : 15_000
    },
  })

const routeTitle = (path: string) =>
  path.startsWith('/review/')
    ? 'Review case'
    : path === '/library'
      ? 'Library'
      : path === '/policy'
        ? 'Policy'
        : path === '/activity'
          ? 'Activity'
          : path === '/settings'
            ? 'Settings'
            : 'Page unavailable'

export const useLiveStream = () => {
  const { updates } = rootRoute.useRouteContext()
  return useLiveStreamState(updates)
}

export const usePreparation = () => {
  const { http, preparation, queryClient } = rootRoute.useRouteContext()
  const streamState = useLiveStream()
  const live = streamState === 'live'
  const previousSnapshot = useRef<string | null>(null)
  const debtQuery = useQuery({
    queryKey: preparationInvalidationDebtKey,
    queryFn: (): Promise<boolean> => Promise.resolve(false),
    enabled: false,
    initialData: false,
  })
  const query = useQuery(preparation)
  const reconnecting = debtQuery.data
  // TanStack Query retains the last successful snapshot while a refetch is in
  // flight or the stream is degraded.  Frames never write this cache, so this
  // is the last server-confirmed state rather than a client-side catch-up.
  const data = query.data
  const snapshot = data ? JSON.stringify(data) : null
  useEffect(() => {
    if (snapshot === null) return
    if (previousSnapshot.current === null) {
      previousSnapshot.current = snapshot
      return
    }
    if (previousSnapshot.current === snapshot) return
    previousSnapshot.current = snapshot
    // Only while polling. A snapshot that changed used to be the only sign that
    // a work might have been published, so every poll that saw a difference
    // rebuilt the Library and the attention rail. With a stream, the cutover
    // announces itself and names the work, so this cascade would be a second,
    // coarser copy of an invalidation that already arrived (#641).
    if (live) return
    void Promise.all([
      invalidateBackgroundQueries(queryClient, { queryKey: ['library'] }),
      invalidateBackgroundQueries(queryClient, { queryKey: ['attention'] }),
    ])
  }, [data?.presentation?.state, live, queryClient, snapshot])
  const adoptDismissal = useCallback(
    (runId: string, value: PreparationView) => {
      const dismissed = value.presentation
      if (dismissed !== null) return false
      const current = queryClient.getQueryData<PreparationView>([
        'review-preparation',
      ])?.presentation
      if (current !== undefined && current !== null && current.run_id !== runId) return false
      queryClient.setQueryData(['review-preparation'], value)
      const preparationDebt = queryClient.getQueryData(preparationInvalidationDebtKey) === true
      void Promise.all([
        invalidateForegroundQueries(queryClient, {
          queryKey: ['review-preparation'],
          exact: true,
          refetchType: preparationDebt ? 'none' : 'active',
        }),
        invalidateBackgroundQueries(queryClient, { queryKey: ['library'] }),
        invalidateBackgroundQueries(queryClient, { queryKey: ['attention'] }),
      ])
      return true
    },
    [queryClient],
  )
  return {
    data,
    confirmed: query.data !== undefined,
    queryClient,
    http,
    live,
    reconnecting,
    adoptDismissal,
  }
}

const connectionCopy = (code: 'tls' | 'authentication' | 'authorization' | 'unavailable') =>
  ({
    tls: 'The library connection needs its secure connection settings checked by its owner.',
    authentication: 'The library connection needs its sign-in settings checked by its owner.',
    authorization: 'The library connection needs its access permission checked by its owner.',
    unavailable: 'The library connection is unavailable and needs an owner check.',
  })[code]

export const ConnectionNotice = ({
  data,
  works,
  checking,
  onCheck,
  recovered,
  onDismissRecovery,
  onControlFocusChange,
  error,
}: {
  data: PreparationView | undefined
  /** Works in Library, from the library summary; undefined until it resolves. */
  works: number | undefined
  checking: boolean
  onCheck: (connection: string) => void
  recovered: NonNullable<PreparationView['connections']>[number] | undefined
  onDismissRecovery: () => void
  onControlFocusChange: (focused: boolean) => void
  error: Error | null
}) => {
  const condition = data?.connections?.find((item) => item.state !== 'recovered') ?? recovered
  if (!condition) return null
  const paused = condition.state === 'recovered' ? 0 : condition.affected
  const title =
    condition.state === 'checking'
      ? 'Checking library connection'
      : condition.state === 'recovered'
        ? 'Library connection recovered'
        : 'Library connection needs attention'
  const copy = data?.recovery_required
    ? 'Preparation stopped. Restart the Mechanical Librarian service to resume. Completed reviews remain available.'
    : condition.state === 'checking'
      ? 'Preparation remains safely paused while the configured connection is checked.'
      : condition.state === 'recovered'
        ? 'Preparation resumed from its retained checkpoint. Completed reviews remain available.'
        : `${connectionCopy(condition.code)} ${String(paused)} preparation ${paused === 1 ? 'work is' : 'works are'} safely paused. Completed reviews remain available.`
  const canCheck = condition.state === 'paused' && !checking && !data?.recovery_required
  const action = condition.state === 'paused' ? 'Check connection again' : 'Checking connection…'
  return (
    <section
      className={`connection-notice is-${condition.state}`}
      aria-live="polite"
      aria-label="Shared library connection status"
    >
      <div>
        <strong>{title}</strong>
        <span>{copy}</span>
      </div>
      <dl>
        <div>
          <dt>In Library</dt>
          <dd>{works === undefined ? '…' : String(works)}</dd>
        </div>
        <div>
          <dt>Safely paused</dt>
          <dd>{String(paused)}</dd>
        </div>
      </dl>
      <button
        type="button"
        disabled={data?.recovery_required && condition.state !== 'recovered'}
        aria-disabled={condition.state === 'recovered' ? undefined : !canCheck}
        aria-busy={checking || undefined}
        onFocus={() => onControlFocusChange(true)}
        onBlur={() => onControlFocusChange(false)}
        onClick={() => {
          if (condition.state === 'recovered') onDismissRecovery()
          else if (canCheck) onCheck(condition.connection)
        }}
      >
        {condition.state === 'recovered' ? 'Dismiss' : action}
      </button>
      {error ? (
        <p role="alert">
          {refusalCopy(
            error,
            'Connection checking could not be completed. Preparation remains safely paused.',
          )}
        </p>
      ) : null}
    </section>
  )
}

export const PreparationNotice = ({ routeIdentity }: { routeIdentity: string }) => {
  const { data, queryClient, http } = usePreparation()
  const connectionControlFocused = useRef(false)
  const [recovered, setRecovered] = useState<
    NonNullable<PreparationView['connections']>[number] | undefined
  >()
  const currentRecovery = data?.connections?.find(
    (condition) => condition.connection === recovered?.connection,
  )
  const acknowledgedRecovery =
    recovered &&
    (!currentRecovery ||
      (currentRecovery.state === 'recovered' &&
        currentRecovery.updated_at === recovered.updated_at))
      ? recovered
      : undefined
  const activeConnection = data?.connections?.some((condition) => condition.state !== 'recovered')
  // Only a shown notice reads the summary, so Activity issues no extra read.
  const summary = useQuery({
    ...librarySummaryQuery(http),
    enabled: activeConnection === true || acknowledgedRecovery !== undefined,
  })
  useLayoutEffect(() => {
    if (activeConnection || acknowledgedRecovery || !connectionControlFocused.current) return
    connectionControlFocused.current = false
    document.querySelector<HTMLElement>('[data-route-focus]')?.focus()
  }, [acknowledgedRecovery, activeConnection])
  useEffect(() => {
    if (!recovered) return
    if (!acknowledgedRecovery) {
      queueMicrotask(() => setRecovered(undefined))
      return
    }
    const timer = window.setTimeout(() => setRecovered(undefined), 8_000)
    return () => window.clearTimeout(timer)
  }, [acknowledgedRecovery, recovered])
  const action = useMutation({
    mutationFn: (path: string) => http.preparationAction(path),
    retry: false,
    onMutate: (path) => {
      setRecovered(undefined)
      const match = /^([^/]+)\/check-connection$/.exec(path)
      if (!match) return
      const encodedConnection = match[1]
      if (!encodedConnection) return
      const connection = decodeURIComponent(encodedConnection)
      const previous = queryClient.getQueryData<PreparationView>(['review-preparation'])
      queryClient.setQueryData<PreparationView>(['review-preparation'], (current) => {
        if (!current || !current.connections) return current
        return {
          ...current,
          connections: current.connections.map((condition) =>
            condition.connection === connection
              ? { ...condition, state: 'checking' as const }
              : condition,
          ),
        }
      })
      return { previous }
    },
    onSuccess: (result, path) => {
      if (!('schema_version' in result) || !result.connections) return
      const match = /^([^/]+)\/check-connection$/.exec(path)
      const encodedConnection = match?.[1]
      if (!encodedConnection) return
      const connection = decodeURIComponent(encodedConnection)
      queryClient.setQueryData(['review-preparation'], result)
      setRecovered(
        result.connections.find(
          (condition) => condition.connection === connection && condition.state === 'recovered',
        ),
      )
    },
    onError: (_error, _path, context) => {
      if (context?.previous) queryClient.setQueryData(['review-preparation'], context.previous)
    },
    onSettled: async () => {
      await invalidateForegroundQueries(queryClient, { queryKey: ['review-preparation'] })
      await Promise.all([
        invalidateBackgroundQueries(queryClient, { queryKey: ['library'] }),
        invalidateBackgroundQueries(queryClient, { queryKey: ['attention'] }),
      ])
    },
  })
  return (
    <ConnectionNotice
      data={data}
      works={summary.data?.counts.all}
      checking={action.isPending}
      error={action.error}
      recovered={acknowledgedRecovery}
      onDismissRecovery={() => setRecovered(undefined)}
      onControlFocusChange={(focused) => {
        connectionControlFocused.current = focused
      }}
      onCheck={(connection) => {
        const main = document.getElementById('main-content')
        if (main) main.dataset.routeFocusIdentity = `${window.location.pathname}:${routeIdentity}`
        action.mutate(`${encodeURIComponent(connection)}/check-connection`)
      }}
    />
  )
}

// One sentence per recovery reason, in the order an entry names the first.
const throttleReasonCopy: readonly (readonly [ProviderThrottleEntry['reasons'][number], string])[] =
  [
    ['quota_exhausted', 'Its request quota is used up.'],
    ['burst_throttled', 'It is limiting requests.'],
    ['rate_limited', 'It is limiting requests.'],
    ['authentication', 'It refused the configured API key.'],
    ['authorization', 'It refused access for the configured API key.'],
    ['configuration', 'Its settings need checking.'],
    ['transient', 'It did not answer.'],
  ]
const providerLabels: Readonly<Record<string, string>> = settingsVocabulary.provider_labels
const throttleWhen = (entry: ProviderThrottleEntry, now: Date) =>
  entry.state === 'checking'
    ? 'Retrying now.'
    : entry.state === 'dormant'
      ? 'Waits for Retry now after the source is fixed.'
      : entry.state === 'exhausted'
        ? 'No automatic retry remains.'
        : entry.next_retry_at === null || entry.next_retry_at * 1000 <= now.getTime()
          ? 'Retry due now.'
          : `Next automatic retry ${formatWhen(entry.next_retry_at, now)}.`
const fileManagedRetry =
  'Settings are managed by /data/config.json: raise provider_recovery_generation by one there and restart the service.'
const retryRefusal = (error: unknown) =>
  error instanceof ApiError && error.code === 'provider_recovery_stale'
    ? 'Retry now was already pressed elsewhere. The notice now shows the current state.'
    : error instanceof ApiError && error.code === 'settings_managed_by_file'
      ? fileManagedRetry
      : error instanceof ApiError && error.code === 'settings_stale'
        ? 'Settings changed elsewhere. Check the notice, then press Retry now again.'
        : refusalCopy(
            error,
            'Retry now could not be confirmed. Check the notice before pressing it again.',
          )

/**
 * Library-level provider throttling: one entry per library and source, and one
 * Retry now that raises the recovery generation for all of them (#1282).
 *
 * The container is always mounted but is a named region only while it holds
 * entries. Its two live regions sit beside Retry now and are mounted whenever
 * the server composes the projection, so an outcome is announced into a region
 * that already exists. Empty, none of them takes any space.
 */
export const ProviderThrottleNotice = () => {
  const { data, queryClient, http } = usePreparation()
  const container = useRef<HTMLElement>(null)
  const controlFocused = useRef(false)
  const pending = useRef(false)
  const [status, setStatus] = useState('')
  const [refusal, setRefusal] = useState('')
  const throttling = data?.provider_throttling ?? null
  const entries = throttling?.entries ?? []
  const names = useLibraryName(http, entries.length > 0)
  const retry = useMutation({
    mutationFn: (expected: number) => http.retryProviderRecovery(expected),
    retry: false,
    onMutate: () => {
      setStatus('')
      setRefusal('')
    },
    onSuccess: (value) => {
      // A Retry now moves the settings revision; an open draft rebases over it.
      queryClient.setQueryData<SettingsView>(['settings'], value)
      setStatus('Retry now accepted. Every deferred lookup will retry now.')
    },
    onError: (error) => setRefusal(retryRefusal(error)),
    onSettled: async () => {
      pending.current = false
      await invalidateForegroundQueries(queryClient, { queryKey: ['review-preparation'] })
    },
  })
  // A focused control that leaves (the notice emptied, or the command was
  // replaced) hands focus to the route's focus target, else to main, never
  // to the document body.
  useLayoutEffect(() => {
    if (!controlFocused.current) return
    const active = document.activeElement
    if (active && active !== document.body) {
      if (!container.current?.contains(active)) controlFocused.current = false
      return
    }
    controlFocused.current = false
    const main = document.getElementById('main-content')
    ;(main?.querySelector<HTMLElement>('[data-route-focus]') ?? main)?.focus({
      preventScroll: true,
    })
  })
  const [clock, setClock] = useState(0)
  const now = new Date()
  // One timer to the earliest wait still ahead re-renders it as due.
  const nextDue = Math.min(
    ...entries.flatMap((entry) =>
      entry.state === 'checking' ||
      entry.state === 'dormant' ||
      entry.state === 'exhausted' ||
      entry.next_retry_at === null ||
      entry.next_retry_at * 1000 <= now.getTime()
        ? []
        : [entry.next_retry_at * 1000],
    ),
  )
  useEffect(() => {
    if (!Number.isFinite(nextDue)) return
    const timer = setTimeout(
      () => setClock((tick) => tick + 1),
      Math.min(nextDue - Date.now(), 2_147_483_647),
    )
    return () => clearTimeout(timer)
  }, [nextDue, clock])
  const busy = retry.isPending
  return (
    <section
      ref={container}
      className={`provider-throttle-notice${entries.length || status || refusal ? '' : ' is-empty'}`}
      aria-label={entries.length ? 'Provider lookups waiting to retry' : undefined}
      onFocus={() => {
        controlFocused.current = true
      }}
      onBlur={(event) => {
        // A window blur keeps the element focused; only a real move clears it.
        if (event.relatedTarget !== null || document.hasFocus()) controlFocused.current = false
      }}
    >
      {entries.length ? (
        <ul>
          {entries.map((entry) => {
            const reason = throttleReasonCopy.find(([code]) => entry.reasons.includes(code))
            return (
              <li key={`${entry.library}\u0000${entry.adapter}`}>
                <span>
                  <strong>
                    {providerLabels[entry.adapter]} · {names.name(entry.library)}:
                  </strong>{' '}
                  {reason ? `${reason[1]} ` : ''}
                  {entry.deferred.toLocaleString()} deferred{' '}
                  {entry.deferred === 1 ? 'lookup' : 'lookups'}. {throttleWhen(entry, now)}
                </span>
                {entry.remedy ? (
                  <>
                    {' '}
                    <Link to="/settings" search={{ group: 'providers' }}>
                      {/* The ring hugs this text, not the padded target. */}
                      <span>
                        {entry.remedy === 'add_api_key'
                          ? 'Add a Google Books API key'
                          : 'Check the source\u2019s API key'}
                      </span>
                    </Link>
                  </>
                ) : null}
              </li>
            )
          })}
        </ul>
      ) : null}
      {/* Mounted whenever the projection exists, so before any outcome. */}
      {throttling ? (
        <div className="provider-throttle-retry">
          {entries.length > 0 && throttling.retry === 'available' ? (
            <div className="provider-throttle-command">
              <button
                type="button"
                aria-disabled={busy || undefined}
                aria-busy={busy || undefined}
                onClick={() => {
                  if (pending.current) return
                  pending.current = true
                  retry.mutate(throttling.generation)
                }}
              >
                {busy ? 'Retrying…' : 'Retry now'}
              </button>
              <small>
                Retry now retries every deferred lookup in every library; a provider still limiting
                requests may defer them again.
              </small>
            </div>
          ) : entries.length > 0 && throttling.retry === 'managed_by_file' ? (
            <p className="provider-throttle-file">{fileManagedRetry}</p>
          ) : null}
          <p className="provider-throttle-outcome provider-throttle-status" role="status">
            {status}
          </p>
          <p className="provider-throttle-outcome provider-throttle-refusal" role="alert">
            {refusal}
          </p>
        </div>
      ) : null}
    </section>
  )
}

/**
 * The ten buckets a work moves through, in the order an owner reads them.
 *
 * The stage names the store uses are engineering words; these are what the
 * owner is actually waiting for. Naming every bucket -- including the empty
 * ones -- keeps the pipeline in one stable order as counts move through it,
 * which is what makes a live update read as progress rather than as a redraw
 * (#641).
 */
const stageLabels: Readonly<Record<string, string>> = {
  queued: 'Not started',
  extracting: 'Reading your files',
  providers: 'Asking providers',
  proposals: 'Comparing sources',
  admission: 'Finding safe automatic choices',
  projection: 'Preparing the review',
  ready: 'Ready to browse',
  recorded: 'Recorded, nothing to decide',
  not_matched: 'Not matched',
  failed: 'Could not finish',
}

export const PreparationActivity = () => {
  const { data, queryClient, http } = usePreparation()
  const names = useLibraryName(http, Boolean(data?.libraries.length))
  const [open, setOpen] = useState(false)
  const trigger = useRef<HTMLButtonElement>(null)
  const popover = useRef<HTMLElement>(null)
  const pending = useRef(false)
  const action = useMutation({
    mutationFn: (path: string) => http.preparationAction(path),
    retry: false,
    onSettled: async () => {
      pending.current = false
      await invalidateForegroundQueries(queryClient, { queryKey: ['review-preparation'] })
      await Promise.all([
        invalidateBackgroundQueries(queryClient, { queryKey: ['library'] }),
        invalidateBackgroundQueries(queryClient, { queryKey: ['attention'] }),
      ])
    },
  })
  // Idle is a state the owner can act on: Scan now and Pause stay reachable.
  const unavailable = !data || data.libraries.length === 0
  const runScans =
    data?.presentation && data.presentation.state !== 'dismissed'
      ? data.presentation.now?.scan_ids
      : undefined
  // Each command is aria-disabled rather than disabled, so focus stays on it;
  // the guard drops a press while one is pending or the command is refused.
  const command = (path: string, refused: boolean) => () => {
    if (refused || pending.current) return
    pending.current = true
    action.mutate(path)
  }
  useEffect(() => {
    if (!open || !unavailable) return
    requestAnimationFrame(() => {
      document.getElementById('main-content')?.focus({ preventScroll: true })
      setOpen(false)
    })
  }, [open, unavailable])
  if (unavailable && !open) return null
  const close = () => {
    trigger.current?.focus()
    setOpen(false)
  }
  return (
    <div
      className="preparation-activity"
      // Escape closes from the trigger or from anywhere inside the popover,
      // and ``close`` already returns focus to the trigger. Tabbing off either
      // end closes it where it stands -- the Dropdown precedent -- so the
      // popover never stays open behind a focus ring that has left it; the
      // browser keeps the tab order it was going to follow. Nothing is bound
      // while the popover is shut, so both keys stay available to the page.
      onKeyDown={(event) => {
        if (!open) return
        if (event.key === 'Escape') {
          event.preventDefault()
          event.stopPropagation()
          close()
          return
        }
        if (event.key !== 'Tab') return
        const focusable = popover.current?.querySelectorAll<HTMLElement>(
          'button:not([disabled]),a[href]',
        )
        if (!focusable?.length) return
        const edge = event.shiftKey ? focusable[0] : focusable[focusable.length - 1]
        if (event.target === edge) setOpen(false)
      }}
    >
      <button
        ref={trigger}
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
      >
        {data?.recovery_required
          ? 'Preparation stopped'
          : data?.paused
            ? 'Preparation paused'
            : data?.actionable
              ? 'Preparation needs attention'
              : data?.active
                ? 'Preparing libraries'
                : 'Preparation idle'}
      </button>
      {open && data ? (
        <section ref={popover} className="preparation-popover" aria-label="Background activity">
          <h2>{data.recovery_required ? 'Preparation stopped' : 'Background activity'}</h2>
          {data.recovery_required ? (
            <p role="status">
              Restart the Mechanical Librarian service to resume preparation. Refreshing this page
              or scanning again cannot recover it. Prepared books remain available.
            </p>
          ) : null}
          {data.libraries.map((item) => (
            <article key={`${item.connection}:${item.library}`}>
              <strong>{names.name(item.library)}</strong>
              <span>
                {data.recovery_required
                  ? 'Last saved progress'
                  : // The progress mapper refuses a state outside the closed set.
                    scanStateLabels[item.state as ScanState]}
                {/*
                 * The run line sums only the libraries its run enrolled, so a
                 * library outside it is the one way the two totals differ.
                 */}
                {runScans && item.scan_id && !runScans.includes(item.scan_id)
                  ? ' · not in this run'
                  : ''}
              </span>
              <span>
                {/*
                 * A finished scan's buckets below already state its split in
                 * these words, so only the checked count stands here.
                 */}
                {`${String(item.checked)} of ${String(item.total)} checked`}
                {!item.state.startsWith('completed') && (
                  <>
                    {' · '}
                    <CountPhrases
                      phrases={preparationOutcomes({
                        ready: item.ready,
                        recorded: item.recorded,
                        not_matched: item.not_matched,
                        failed: item.blocked,
                      })}
                    />
                  </>
                )}
              </span>
              <dl className="preparation-stages">
                {/*
                 * A live scan keeps every bucket, including the empty ones, so
                 * the pipeline holds one stable order as counts move through
                 * it (#641). A finished scan is not moving, so the buckets
                 * nothing reached are a wall of zeros and are left out.
                 */}
                {(item.stages ?? [])
                  .filter((stage) => !item.state.startsWith('completed') || stage.count > 0)
                  .map((stage) => (
                    <div key={stage.stage}>
                      <dt>{stageLabels[stage.stage] ?? stage.stage}</dt>
                      <dd>{stage.count}</dd>
                    </div>
                  ))}
              </dl>
              <button
                type="button"
                aria-disabled={
                  data.recovery_required ||
                  action.isPending ||
                  data.paused ||
                  item.state === 'queued' ||
                  item.state === 'discovering'
                }
                aria-busy={action.isPending || undefined}
                onClick={command(
                  `${encodeURIComponent(item.connection)}/${encodeURIComponent(item.library)}/scan`,
                  data.recovery_required ||
                    data.paused ||
                    item.state === 'queued' ||
                    item.state === 'discovering',
                )}
              >
                Scan now
              </button>
              {item.state === 'completed_with_failures' ? (
                <button
                  type="button"
                  aria-disabled={data.recovery_required || action.isPending || data.paused}
                  aria-busy={action.isPending || undefined}
                  onClick={command(
                    `${encodeURIComponent(item.connection)}/${encodeURIComponent(item.library)}/retry`,
                    data.recovery_required || data.paused,
                  )}
                >
                  Retry
                </button>
              ) : null}
              {/*
               * Both controls call the same operation -- a new library cycle --
               * and the recovery schedule they do not control is documented in
               * `docs/operations/provider-recovery.md`. This states what they
               * do; it decides nothing.
               */}
              {!data.recovery_required ? (
                <small className="activity-backup-note">
                  Scan now — and Retry after a run with failures — start a new preparation cycle for
                  this library; books that could not finish are prepared again; provider lookups
                  that hit limits recover automatically on the documented schedule; a failure before
                  a report has no automatic retry.
                </small>
              ) : null}
            </article>
          ))}
          <button
            type="button"
            aria-disabled={data.recovery_required || action.isPending}
            aria-busy={action.isPending || undefined}
            onClick={command(data.paused ? 'resume' : 'pause', data.recovery_required)}
          >
            {data.paused ? 'Resume' : 'Pause'}
          </button>
          {!data.recovery_required ? (
            <small className="activity-backup-note">
              Pause stops new library scans. Provider lookups that are already deferred still retry
              on their schedule.
            </small>
          ) : null}
          <button type="button" onClick={close}>
            Close
          </button>
          {action.isError ? (
            <p role="alert">
              {refusalCopy(action.error, 'Preparation control failed safely. Try again.')}
            </p>
          ) : null}
        </section>
      ) : null}
    </div>
  )
}

// One rule with setup, sign-in and public/theme.js, which applies it before first
// paint: the owner's saved choice, otherwise the system, followed live until a
// choice is saved (#1013).
const savedTheme = () => /(?:^|; )ml_theme=(light|dark)(?:;|$)/.exec(document.cookie)?.[1]
const systemMedia = () => {
  const query = '(prefers-color-scheme: dark)'
  const media = typeof window.matchMedia === 'function' ? window.matchMedia(query) : null
  return media?.media === query ? media : null
}
const ThemeControl = () => {
  const [theme, setTheme] = useState(
    () => savedTheme() ?? (systemMedia()?.matches ? 'dark' : 'light'),
  )
  useEffect(() => {
    const media = systemMedia()
    if (savedTheme() || typeof media?.addEventListener !== 'function') return
    const follow = (event: MediaQueryListEvent) => {
      if (!savedTheme()) setTheme(event.matches ? 'dark' : 'light')
    }
    media.addEventListener('change', follow)
    return () => media.removeEventListener('change', follow)
  }, [])
  useEffect(() => {
    document.documentElement.dataset.theme = theme
    document.documentElement.style.colorScheme = theme
  }, [theme])
  const toggle = () => {
    const next = theme === 'dark' ? 'light' : 'dark'
    document.cookie = `ml_theme=${next}; Path=/; Max-Age=31536000; SameSite=Strict${location.protocol === 'https:' ? '; Secure' : ''}`
    setTheme(next)
  }
  return (
    <button
      className="theme-toggle"
      type="button"
      aria-pressed={theme === 'dark'}
      aria-label={theme === 'dark' ? 'Use light mode' : 'Use dark mode'}
      onClick={toggle}
    >
      <span aria-hidden="true">{theme === 'dark' ? '☀' : '◐'}</span>
      <span>{theme === 'dark' ? 'Light' : 'Dark'}</span>
    </button>
  )
}

// A route showing a retained read is not yet loaded, so the shell holds its announcement.
const RetainingContext = createContext<(retaining: boolean) => void>(() => undefined)
const useAnnounceRetaining = (retaining: boolean) => {
  const setRetaining = useContext(RetainingContext)
  useLayoutEffect(() => {
    setRetaining(retaining)
    return () => setRetaining(false)
  }, [setRetaining, retaining])
}
// The brand leads to Library too, but the Library nav link is the one current
// page; TanStack marks every active link current, so the brand drops it (#1488).
const BrandLink = createLink((props: ComponentPropsWithRef<'a'>) => (
  <a {...props} aria-current={undefined} />
))
const Shell = () => {
  const masthead = useRef<HTMLElement>(null)
  const main = useRef<HTMLElement>(null)
  const previousPath = useRef('')
  const popNavigation = useRef(false)
  const navigation = useRouterState({
    select: (state) => ({ href: state.location.href, status: state.status }),
  })
  // A review is part of Library, as is the attention rail, so Library stays the
  // current destination while a review is open.
  const libraryCurrent = useRouterState({
    select: (state) => /^\/library(?:\/|$)|^\/review\//.test(state.location.pathname),
  })
  const { http, queryClient, updates } = rootRoute.useRouteContext()
  useLiveInvalidation(updates, queryClient)
  const streamState = useLiveStreamState(updates)
  const appearance = useQuery({
    queryKey: ['appearance'] as const,
    queryFn: (queryContext) =>
      http.getAppearance(queryContext.signal, queryWorkClass(queryContext)),
    staleTime: 15_000,
    retry: false,
  })
  const path = new URL(navigation.href, window.location.origin).pathname
  const title = routeTitle(path)
  const [mismatch, setMismatch] = useState(false)
  const [retaining, setRetaining] = useState(false)
  const [shellError, setShellError] = useState('')
  useEffect(() => {
    const listener = () => setMismatch(true)
    addEventListener('ml:build-mismatch', listener)
    return () => removeEventListener('ml:build-mismatch', listener)
  }, [])
  useEffect(() => {
    const preference = appearance.data?.reduced_motion
    if (preference === 'always_reduced')
      document.documentElement.dataset.reducedMotion = 'always-reduced'
    else delete document.documentElement.dataset.reducedMotion
  }, [appearance.data?.reduced_motion])
  useLayoutEffect(() => {
    const mastheadElement = masthead.current
    if (!mastheadElement) return
    const root = document.documentElement
    // The masthead sticks only while it takes at most a quarter of the
    // viewport. Taller -- a phone, a short window, enlarged text -- it scrolls
    // away with the page, so it never covers what the owner reads or focuses.
    const updateMastheadClearance = () => {
      const height = mastheadElement.getBoundingClientRect().height
      root.style.setProperty('--masthead-clearance', `${(height + 8).toString()}px`)
      if (height > window.innerHeight / 4) root.dataset.masthead = 'static'
      else delete root.dataset.masthead
    }
    updateMastheadClearance()
    addEventListener('resize', updateMastheadClearance)
    const observer = 'ResizeObserver' in window ? new ResizeObserver(updateMastheadClearance) : null
    observer?.observe(mastheadElement)
    return () => {
      observer?.disconnect()
      removeEventListener('resize', updateMastheadClearance)
      root.style.removeProperty('--masthead-clearance')
      delete root.dataset.masthead
    }
  }, [])
  useEffect(() => {
    const listener = () => {
      popNavigation.current = true
    }
    addEventListener('popstate', listener)
    return () => removeEventListener('popstate', listener)
  }, [])
  useEffect(() => {
    if (navigation.status !== 'idle') return
    document.title = `${title} · Mechanical Librarian`
    const changed = previousPath.current !== path
    previousPath.current = path
    const held = document.activeElement
    const focusAlreadyInsideRoute = held !== null && main.current?.contains(held) === true
    const frame = requestAnimationFrame(() => {
      if (changed && !focusAlreadyInsideRoute && document.activeElement === held) {
        const target =
          main.current?.querySelector<HTMLElement>('[data-route-focus]') ?? main.current
        target?.focus({ preventScroll: true })
      }
      if (changed && !popNavigation.current) window.scrollTo({ top: 0 })
      popNavigation.current = false
      main.current?.setAttribute('data-restored-path', path)
      dispatchEvent(new Event('ml:route-restored'))
    })
    return () => cancelAnimationFrame(frame)
  }, [navigation.status, path, title])
  const signOut = async () => {
    setShellError('')
    try {
      await http.logout()
      window.location.assign('/login')
    } catch (error) {
      if (!(error instanceof BuildMismatchError))
        setShellError(refusalCopy(error, 'Sign out failed. Try again.'))
    }
  }
  return (
    <div className="app-shell">
      <a className="skip-link" href="#main-content">
        Skip to content
      </a>
      <header ref={masthead} className="masthead">
        <BrandLink
          to="/library"
          search={{ lens: 'all' }}
          className="brand"
          aria-label="Mechanical Librarian"
        >
          <span className="brand-mark" aria-hidden="true">
            ML
          </span>
          <span>Mechanical Librarian</span>
        </BrandLink>
        <nav aria-label="Primary workspace">
          <Link
            to="/library"
            search={{ lens: 'all' }}
            activeOptions={{ includeSearch: false }}
            aria-current={libraryCurrent ? 'page' : undefined}
          >
            Library
          </Link>
          <Link
            to="/policy"
            search={{ section: 'identity' }}
            activeProps={{ 'aria-current': 'page' }}
          >
            Policy
          </Link>
          <Link
            to="/activity"
            search={{ eventType: 'all', rangeDays: 30, page: 1, pageSize: 25 }}
            activeProps={{ 'aria-current': 'page' }}
          >
            Activity
          </Link>
          <Link
            to="/settings"
            search={{ group: 'connection' }}
            activeOptions={{ includeSearch: false }}
            activeProps={{ 'aria-current': 'page' }}
          >
            Settings
          </Link>
        </nav>
        {/* Status has a slot of its own, so a notice arriving moves nothing else. */}
        <div className="shell-status">
          <LiveStreamNotice state={streamState} />
          <PreparationActivity />
        </div>
        <div className="shell-actions">
          <ThemeControl />
          <button className="sign-out" type="button" onClick={() => void signOut()}>
            Sign out
          </button>
        </div>
      </header>
      <p className="navigation-status" role="status" aria-live="polite">
        {navigation.status === 'idle' && !retaining ? `${title} loaded` : `Loading ${title}`}
      </p>
      {shellError ? (
        <p className="shell-error" role="alert">
          {shellError}
        </p>
      ) : null}
      {mismatch ? (
        <section className="build-mismatch" role="alert">
          <strong>This page is from a different application build.</strong>
          <span>Writes are blocked. Reload once to continue safely.</span>
          <button onClick={() => window.location.reload()}>Reload application</button>
        </section>
      ) : null}
      <main id="main-content" ref={main} tabIndex={-1} aria-busy={navigation.status !== 'idle'}>
        <Suspense fallback={<RoutePending title={title} />}>
          <RetainingContext.Provider value={setRetaining}>
            <Outlet />
          </RetainingContext.Provider>
        </Suspense>
      </main>
    </div>
  )
}

export const RoutePending = ({ title = 'workspace' }: { title?: string }) => (
  <section aria-busy="true" className="route-state">
    <p className="eyebrow">Private workspace</p>
    <h1>Loading {title.toLowerCase()}…</h1>
  </section>
)
const RouteFailure = ({
  error,
  reset,
  title = 'This workspace',
}: {
  error: unknown
  reset: () => void
  title?: string
}) => {
  const router = useRouter()
  return error instanceof ChunkRecoveryError && error.exhausted ? (
    <section className="route-state">
      <p className="eyebrow">Application update unavailable</p>
      <h1>The current workspace assets could not be loaded.</h1>
      <p role="alert">{error.message}</p>
      <button onClick={() => reloadCurrentApplication()}>Reload the current application</button>
    </section>
  ) : (
    <section className="route-state">
      <p className="eyebrow">Review unavailable</p>
      <h1>{title} could not be loaded.</h1>
      <p role="alert">{error instanceof Error ? error.message : String(error)}</p>
      <button onClick={() => void router.invalidate().then(reset)}>Try again</button>
    </section>
  )
}
const DirectEntryFallback = () => (
  <section className="route-state">
    <p className="eyebrow">Page unavailable</p>
    <h1>This review route does not exist.</h1>
    <Link to="/library" search={{ lens: 'all' }}>
      Open review library
    </Link>
  </section>
)

const rootRoute = createRootRouteWithContext<RouterContext>()({
  component: Shell,
  pendingComponent: RoutePending,
  errorComponent: RouteFailure,
  notFoundComponent: DirectEntryFallback,
})
const indexRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/',
  component: () => <Navigate to="/library" search={{ lens: 'all' }} replace />,
})
const LibraryRouteComponent = () => {
  const { http } = rootRoute.useRouteContext()
  const search = libraryRoute.useSearch()
  const query = useRetainedQuery(libraryQuery(http, search))
  const handoff = useQuery(handoffConsentQuery(http))
  const names = useLibraryName(http, handoffNeedsLibraryNames(handoff.data))
  const [railRetaining, setRailRetaining] = useState(false)
  useAnnounceRetaining(query.isPlaceholderData || railRetaining)
  // Library waits for the handoff's read to settle once, either way, and for
  // the library names the notice states, so the notice above the works is
  // there when they are and never moves them. A read that failed has settled
  // even while a later mount retries it, and a failed names read falls through.
  // Once open, Library never returns to pending: a later read only updates.
  const [opened, setOpened] = useState(false)
  const settling = (!handoff.dataUpdatedAt && !handoff.errorUpdatedAt) || !names.ready
  if (!opened && !settling) setOpened(true)
  if (!opened && settling) return <RoutePending title="Library" />
  return (
    <LibraryPage
      data={query.data}
      retained={query.isPlaceholderData}
      onRailRetaining={setRailRetaining}
      pending={query.isPending}
      failed={query.isError}
      retry={() => void query.refetch()}
    />
  )
}
const DiagnosticRouteComponent = () => {
  const { reviewCaseId } = detailRoute.useParams()
  const { http } = rootRoute.useRouteContext()
  const search = normalizeDetailSearch(detailRoute.useSearch())
  const detail = useRetainedQuery(detailQuery(http, reviewCaseId))
  useAnnounceRetaining(detail.isPlaceholderData)
  const backSearch = normalizeLibrarySearch({ ...search, preview: reviewCaseId })
  const problem =
    detail.error instanceof ApiError && detail.error.code === 'not_found'
      ? 'No retained diagnostic was found for this work.'
      : detail.error instanceof ApiError && detail.error.code === 'stale'
        ? 'This diagnostic is no longer current.'
        : 'The retained diagnostic could not be read safely.'
  return (
    <div className="diagnostic-route" aria-busy={detail.isPlaceholderData || undefined}>
      <Link className="diagnostic-back-to-work" to="/library" search={backSearch}>
        <span aria-hidden="true">←</span> Back to work
      </Link>
      {detail.isPending ? (
        <section aria-busy="true" className="diagnostic-state" data-route-focus tabIndex={-1}>
          <h1>Loading work diagnostics…</h1>
          <p>The retained review record is being opened.</p>
        </section>
      ) : detail.isError ? (
        <section className="diagnostic-state diagnostic-failure" data-route-focus tabIndex={-1}>
          <h1>Work diagnostics are unavailable.</h1>
          <p role="alert">{problem}</p>
        </section>
      ) : (
        <Suspense
          fallback={
            <section aria-busy="true" className="diagnostic-state" data-route-focus tabIndex={-1}>
              <h1>Loading work diagnostics…</h1>
              <p>The retained review record is being opened.</p>
            </section>
          }
        >
          <DetailPage data={detail.data} />
        </Suspense>
      )}
    </div>
  )
}
export const libraryRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/library',
  validateSearch: normalizeLibrarySearch,
  // Each route component preloads its page chunk on intent through the lazy wrapper.
  component: Object.assign(LibraryRouteComponent, { preload: () => LibraryPage.preload?.() }),
})
export const detailRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/review/$reviewCaseId',
  validateSearch: normalizeDetailSearch,
  component: Object.assign(DiagnosticRouteComponent, { preload: () => DetailPage.preload?.() }),
})
export interface PolicyCritical {
  state: PolicyStateView
  shadow: ShadowPostureView
  safety: AutomaticSafetyView
}
const PolicyRouteComponent = () => {
  const { http } = rootRoute.useRouteContext()
  const state = useQuery(policyStateQuery(http))
  const shadow = useQuery(shadowQuery(http))
  const safety = useQuery(safetyQuery(http))
  const error = state.error ?? shadow.error ?? safety.error
  if (error instanceof SessionExpiredError) return null
  if (error)
    return (
      <RouteFailure
        error={error}
        reset={() => {
          void state.refetch()
          void shadow.refetch()
          void safety.refetch()
        }}
      />
    )
  if (!state.data || !shadow.data || !safety.data) return <RoutePending title="Policy" />
  return <PolicyPage data={{ state: state.data, shadow: shadow.data, safety: safety.data }} />
}
export const policyRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/policy',
  validateSearch: normalizePolicySearch,
  component: Object.assign(PolicyRouteComponent, { preload: () => PolicyPage.preload?.() }),
})
const ActivityRouteComponent = () => {
  const { http } = rootRoute.useRouteContext()
  const search = activityRoute.useSearch()
  const query = useQuery(activityQuery(http, search))
  if (!query.data) {
    if (query.isError)
      return <RouteFailure error={query.error} reset={() => undefined} title="Activity" />
    return <RoutePending title="Activity" />
  }
  return <ActivityPage initial={query.data} />
}
export const activityRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/activity',
  validateSearch: normalizeActivitySearch,
  loaderDeps: ({ search }) => search,
  preload: false,
  loader: ({ context, deps, abortController, cause }) =>
    cause === 'enter'
      ? undefined
      : context.queryClient.query({
          ...activityQuery(context.http, deps),
          queryFn: (queryContext) =>
            context.http.getActivity(deps, abortController.signal, queryWorkClass(queryContext)),
          staleTime: 'static',
        }),
  pendingMs: Infinity,
  pendingComponent: () => <RoutePending title="Activity" />,
  errorComponent: (props) => <RouteFailure {...props} title="Activity" />,
  component: Object.assign(ActivityRouteComponent, { preload: () => ActivityPage.preload?.() }),
})
export const settingsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/settings',
  validateSearch: normalizeSettingsSearch,
  component: SettingsPage,
})
const routeTree = rootRoute.addChildren([
  indexRoute,
  libraryRoute,
  detailRoute,
  policyRoute,
  activityRoute,
  settingsRoute,
])

export const useReviewContext = () => rootRoute.useRouteContext()

export const createApplicationRouter = (
  http: ReviewHttp,
  queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false, refetchOnWindowFocus: false, refetchOnReconnect: false },
      mutations: { retry: false },
    },
  }),
  history?: ReturnType<typeof createMemoryHistory>,
  updates?: LiveUpdateStream,
) => {
  const preparation = preparationQuery(
    http,
    () =>
      updates === undefined ||
      (updates.hasBeenLive !== true && updates.state !== 'live') ||
      (updates.state === 'live' &&
        queryClient.getQueryData(preparationInvalidationDebtKey) === true),
  )
  return createRouter({
    routeTree,
    context: { http, queryClient, preparation, ...(updates ? { updates } : {}) },
    ...(history ? { history } : {}),
    defaultPreload: 'intent',
    defaultPreloadStaleTime: 15_000,
    defaultPendingMs: 140,
    defaultPendingMinMs: 220,
    scrollRestoration: true,
  })
}
export type ApplicationRouter = ReturnType<typeof createApplicationRouter>
declare module '@tanstack/react-router' {
  interface Register {
    router: ApplicationRouter
  }
}
