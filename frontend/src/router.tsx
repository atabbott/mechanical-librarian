import { invalidateForegroundQueries } from './queryWork'
import { invalidateBackgroundQueries } from './queryWork'
import { queryWorkClass, useRequiredQuery as useQuery } from './queryWork'
import { QueryClient, queryOptions, useMutation } from '@tanstack/react-query'
import {
  Link,
  Navigate,
  Outlet,
  createRootRouteWithContext,
  createRoute,
  createRouter,
  useRouter,
  useRouterState,
} from '@tanstack/react-router'
import { Suspense, useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'

import type { createMemoryHistory } from '@tanstack/react-router'
import { ApiError, BuildMismatchError, SessionExpiredError } from './api/http'
import type {
  ActivitySearch,
  AttentionSearch,
  AutomaticSafetyView,
  DetailSearch,
  LibrarySearch,
  PolicySearch,
  PolicyStateView,
  PreparationView,
  ReviewHttp,
  ShadowPostureView,
} from './api/http'
import { ChunkRecoveryError, recoverableLazy, reloadCurrentApplication } from './chunkRecovery'
import { LiveStreamNotice } from './components/LiveStreamNotice'
import {
  preparationInvalidationDebtKey,
  useLiveInvalidation,
  useLiveStreamState,
} from './liveInvalidation'
import type { LiveUpdateStream } from './liveUpdates'
import { fieldLabel } from './labels'

const LibraryPage = recoverableLazy(() => import('./routes/LibraryPage'))
const AttentionPage = recoverableLazy(() => import('./routes/AttentionPage'))
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
export const normalizeLibrarySearch = (search: Record<string, unknown>): LibrarySearch => {
  const lens =
    search.lens === 'needs_attention' || search.lens === 'no_attention_needed' ? search.lens : 'all'
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
export const normalizeAttentionSearch = (search: Record<string, unknown>): AttentionSearch => {
  const q = bounded(search.q)
  const cursor = bounded(search.cursor)
  return { ...(q ? { q } : {}), ...(cursor ? { cursor } : {}) }
}
export const normalizeDetailSearch = (search: Record<string, unknown>): DetailSearch => {
  const field = bounded(search.field)
  const lens =
    search.lens === 'needs_attention' || search.lens === 'no_attention_needed' ? search.lens : 'all'
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
  return { eventType, rangeDays, pageSize, page, ...(q ? { q } : {}) }
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
export const attentionQuery = (http: ReviewHttp, search: AttentionSearch) =>
  queryOptions({
    queryKey: ['attention', search.q ?? null, search.cursor ?? null] as const,
    queryFn: (queryContext) =>
      http.getAttention(search, queryContext.signal, queryWorkClass(queryContext)),
    staleTime: 15_000,
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
      : path === '/attention'
        ? 'Attention'
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
  checking,
  onCheck,
  recovered,
  onDismissRecovery,
  onControlFocusChange,
  error,
}: {
  data: PreparationView | undefined
  checking: boolean
  onCheck: (connection: string) => void
  recovered: NonNullable<PreparationView['connections']>[number] | undefined
  onDismissRecovery: () => void
  onControlFocusChange: (focused: boolean) => void
  error: boolean
}) => {
  const condition = data?.connections?.find((item) => item.state !== 'recovered') ?? recovered
  if (!condition) return null
  const ready = data?.libraries.reduce((total, item) => total + item.ready, 0) ?? 0
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
          <dt>Ready</dt>
          <dd>{String(ready)}</dd>
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
          Connection checking could not be completed. Preparation remains safely paused.
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
      checking={action.isPending}
      error={action.isError}
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

/**
 * The nine buckets a work moves through, in the order an owner reads them.
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
  failed: 'Could not finish',
}

/**
 * What a library's scan is doing, in the words the owner reads elsewhere.
 *
 * The store state was printed raw, so an owner read `completed_with_failures`
 * and had to translate it. Anything the store adds later still reads as a
 * label through ``fieldLabel`` rather than as an identifier.
 */
const scanStateLabels: Readonly<Record<string, string>> = {
  queued: 'Waiting to start',
  discovering: 'Finding your books',
  processing: 'Preparing your books',
  completed: 'Complete',
  completed_with_failures: 'Complete · some books could not finish',
}

export const PreparationActivity = () => {
  const { data, queryClient, http } = usePreparation()
  const [open, setOpen] = useState(false)
  const trigger = useRef<HTMLButtonElement>(null)
  const popover = useRef<HTMLElement>(null)
  const action = useMutation({
    mutationFn: (path: string) => http.preparationAction(path),
    retry: false,
    onSettled: async () => {
      await invalidateForegroundQueries(queryClient, { queryKey: ['review-preparation'] })
      await Promise.all([
        invalidateBackgroundQueries(queryClient, { queryKey: ['library'] }),
        invalidateBackgroundQueries(queryClient, { queryKey: ['attention'] }),
      ])
    },
  })
  const unavailable =
    !data || (!data.active && !data.actionable && !data.paused && !data.recovery_required)
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
              : 'Preparing libraries'}
      </button>
      {open && data ? (
        <section ref={popover} className="preparation-popover" aria-label="Background activity">
          <h2>{data.recovery_required ? 'Preparation stopped' : 'Background activity'}</h2>
          {data.recovery_required ? (
            <p role="status">
              Restart the Mechanical Librarian service to resume preparation. Refreshing this page
              or scanning again cannot recover it. Ready books remain available.
            </p>
          ) : null}
          {data.libraries.map((item) => (
            <article key={`${item.connection}:${item.library}`}>
              <strong>{item.library}</strong>
              <span>
                {data.recovery_required
                  ? 'Last saved progress'
                  : (scanStateLabels[item.state] ?? fieldLabel(item.state))}
              </span>
              <span>
                {String(item.checked)} of {String(item.total)} checked · {String(item.ready)} ready
                {item.blocked > 0 ? ` · ${String(item.blocked)} could not finish` : ''}
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
                disabled={
                  data.recovery_required ||
                  action.isPending ||
                  data.paused ||
                  item.state === 'queued' ||
                  item.state === 'discovering'
                }
                onClick={() =>
                  action.mutate(
                    `${encodeURIComponent(item.connection)}/${encodeURIComponent(item.library)}/scan`,
                  )
                }
              >
                Scan now
              </button>
              {item.state === 'completed_with_failures' ? (
                <button
                  disabled={data.recovery_required || action.isPending || data.paused}
                  onClick={() =>
                    action.mutate(
                      `${encodeURIComponent(item.connection)}/${encodeURIComponent(item.library)}/retry`,
                    )
                  }
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
            disabled={data.recovery_required || action.isPending}
            onClick={() => action.mutate(data.paused ? 'resume' : 'pause')}
          >
            {data.paused ? 'Resume' : 'Pause'}
          </button>
          <button onClick={close}>Close</button>
          {action.isError ? (
            <p role="alert">Preparation control failed safely. Try again.</p>
          ) : null}
        </section>
      ) : null}
    </div>
  )
}

const ThemeControl = () => {
  const cookie = /(?:^|; )ml_theme=(light|dark)(?:;|$)/.exec(document.cookie)?.[1]
  const [theme, setTheme] = useState(cookie === 'dark' ? 'dark' : 'light')
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

const Shell = () => {
  const shell = useRef<HTMLDivElement>(null)
  const masthead = useRef<HTMLElement>(null)
  const main = useRef<HTMLElement>(null)
  const previousPath = useRef('')
  const popNavigation = useRef(false)
  const navigation = useRouterState({
    select: (state) => ({ href: state.location.href, status: state.status }),
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
    const shellElement = shell.current
    const mastheadElement = masthead.current
    if (!shellElement || !mastheadElement) return
    const updateMastheadClearance = () => {
      const clearance = mastheadElement.getBoundingClientRect().height + 8
      shellElement.style.setProperty('--masthead-clearance', `${clearance.toString()}px`)
    }
    updateMastheadClearance()
    if (!('ResizeObserver' in window)) {
      return () => shellElement.style.removeProperty('--masthead-clearance')
    }
    const observer = new ResizeObserver(updateMastheadClearance)
    observer.observe(mastheadElement)
    return () => {
      observer.disconnect()
      shellElement.style.removeProperty('--masthead-clearance')
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
      if (!(error instanceof BuildMismatchError)) setShellError('Sign out failed. Try again.')
    }
  }
  return (
    <div ref={shell} className="app-shell">
      <a className="skip-link" href="#main-content">
        Skip to content
      </a>
      <header ref={masthead} className="masthead">
        <Link
          to="/library"
          search={{ lens: 'all' }}
          className="brand"
          aria-label="Mechanical Librarian home"
        >
          <span className="brand-mark" aria-hidden="true">
            ML
          </span>
          <span>Mechanical Librarian</span>
        </Link>
        <nav aria-label="Primary workspace">
          <Link
            to="/library"
            search={{ lens: 'all' }}
            activeOptions={{ includeSearch: false }}
            activeProps={{ 'aria-current': 'page' }}
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
        <div className="shell-actions">
          <LiveStreamNotice state={streamState} />
          <PreparationActivity />
          <ThemeControl />
          <button className="sign-out" type="button" onClick={() => void signOut()}>
            Sign out
          </button>
        </div>
      </header>
      <p className="navigation-status" role="status" aria-live="polite">
        {navigation.status === 'idle' ? `${title} loaded` : `Loading ${title}`}
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
          <Outlet />
        </Suspense>
      </main>
    </div>
  )
}

const RoutePending = ({ title = 'workspace' }: { title?: string }) => (
  <section aria-busy="true" className="route-state">
    <p className="eyebrow">Authenticated review</p>
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
  const query = useQuery(libraryQuery(http, search))
  return (
    <LibraryPage
      data={query.data}
      pending={query.isPending}
      failed={query.isError}
      retry={() => void query.refetch()}
    />
  )
}
const AttentionRouteComponent = () => {
  const { http } = rootRoute.useRouteContext()
  const search = attentionRoute.useSearch()
  const query = useQuery(attentionQuery(http, search))
  return (
    <AttentionPage data={query.data} failed={query.isError} retry={() => void query.refetch()} />
  )
}
const DiagnosticRouteComponent = () => {
  const { reviewCaseId } = detailRoute.useParams()
  const { http } = rootRoute.useRouteContext()
  const search = normalizeDetailSearch(detailRoute.useSearch())
  const detail = useQuery(detailQuery(http, reviewCaseId))
  const backSearch = normalizeLibrarySearch({ ...search, preview: reviewCaseId })
  const problem =
    detail.error instanceof ApiError && detail.error.code === 'not_found'
      ? 'No retained diagnostic was found for this work.'
      : detail.error instanceof ApiError && detail.error.code === 'stale'
        ? 'This diagnostic is no longer current.'
        : 'The retained diagnostic could not be read safely.'
  return (
    <div className="diagnostic-route">
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
  component: LibraryRouteComponent,
})
export const attentionRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/attention',
  validateSearch: normalizeAttentionSearch,
  component: AttentionRouteComponent,
})
export const detailRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/review/$reviewCaseId',
  validateSearch: normalizeDetailSearch,
  component: DiagnosticRouteComponent,
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
  component: PolicyRouteComponent,
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
  component: ActivityRouteComponent,
})
export const settingsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/settings',
  validateSearch: normalizeSettingsSearch,
  component: () => <SettingsPage />,
})
const routeTree = rootRoute.addChildren([
  indexRoute,
  libraryRoute,
  attentionRoute,
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
