import { QueryClient, queryOptions, useMutation, useQuery } from '@tanstack/react-query'
import {
  Link,
  Navigate,
  Outlet,
  createRootRouteWithContext,
  createRoute,
  createRouter,
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
import { useLiveInvalidation, useLiveStreamState } from './liveInvalidation'
import type { LiveUpdateStream } from './liveUpdates'

const LibraryPage = recoverableLazy(() => import('./routes/LibraryPage'))
const AttentionPage = recoverableLazy(() => import('./routes/AttentionPage'))
const DetailPage = recoverableLazy(() => import('./routes/DetailPage'))
const PolicyPage = recoverableLazy(() => import('./routes/PolicyPage'))
const ActivityPage = recoverableLazy(() => import('./routes/ActivityPage'))
const SettingsPage = recoverableLazy(() => import('./routes/SettingsPage'))

export interface RouterContext {
  http: ReviewHttp
  queryClient: QueryClient
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
  return { ...(field ? { field } : {}), lens, subview }
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
  'connection' | 'providers' | 'libraries' | 'automation' | 'reviewer' | 'server' | 'appearance'
export interface SettingsSearch {
  group: SettingsGroup
}
export const normalizeSettingsSearch = (search: Record<string, unknown>): SettingsSearch => ({
  group:
    search.group === 'providers' ||
    search.group === 'libraries' ||
    search.group === 'automation' ||
    search.group === 'reviewer' ||
    search.group === 'server' ||
    search.group === 'appearance'
      ? search.group
      : 'connection',
})

export const libraryQuery = (http: ReviewHttp, search: LibrarySearch) =>
  queryOptions({
    queryKey: ['library', search.lens, search.q ?? null, search.cursor ?? null] as const,
    queryFn: ({ signal }) => http.getLibrary(search, signal),
    staleTime: 15_000,
    retry: false,
  })
export const attentionQuery = (http: ReviewHttp, search: AttentionSearch) =>
  queryOptions({
    queryKey: ['attention', search.q ?? null, search.cursor ?? null] as const,
    queryFn: ({ signal }) => http.getAttention(search, signal),
    staleTime: 15_000,
    retry: false,
  })
export const detailQuery = (http: ReviewHttp, id: string) =>
  queryOptions({
    queryKey: ['review-case', id] as const,
    queryFn: ({ signal }) => http.getCaseDetail(id, signal),
    staleTime: 15_000,
    retry: false,
  })
const policyStateQuery = (http: ReviewHttp) =>
  queryOptions({
    queryKey: ['policy', 'state'] as const,
    queryFn: ({ signal }) => http.getPolicyState(signal),
    staleTime: 15_000,
    retry: false,
  })
const shadowQuery = (http: ReviewHttp) =>
  queryOptions({
    queryKey: ['policy', 'shadow'] as const,
    queryFn: ({ signal }) => http.getShadowPosture(signal),
    staleTime: 15_000,
    retry: false,
  })
const safetyQuery = (http: ReviewHttp) =>
  queryOptions({
    queryKey: ['policy', 'safety'] as const,
    queryFn: ({ signal }) => http.getAutomaticSafety(signal),
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
    queryFn: ({ signal }) => http.getActivity(search, signal),
    staleTime: 4_000,
    retry: false,
  })
export const preparationQuery = (
  http: ReviewHttp,
  fallbackPolling = false,
  onConfirmed?: (value: PreparationView) => void,
) =>
  queryOptions({
    queryKey: ['review-preparation'] as const,
    queryFn: async ({ signal }) => {
      const value = await http.getPreparation(signal)
      onConfirmed?.(value)
      return value
    },
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
      if (!fallbackPolling) return false
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

interface PreparationReconnect {
  epoch: number
  state: 'clear' | 'debt' | 'refreshing' | 'retry'
  transport: 'unknown' | 'live' | 'degraded'
  frozen?: PreparationView
}
const preparationReconnectKey = ['review-preparation-reconnect'] as const

export const usePreparation = () => {
  const { http, queryClient, updates } = rootRoute.useRouteContext()
  const streamState = useLiveStream()
  const live = streamState === 'live'
  const previousSnapshot = useRef<string | null>(null)
  const reconnectQuery = useQuery({
    queryKey: preparationReconnectKey,
    queryFn: (): Promise<PreparationReconnect> =>
      Promise.resolve({ epoch: 0, state: 'clear', transport: 'unknown' }),
    enabled: false,
    structuralSharing: false,
    initialData: {
      epoch: 0,
      state: 'clear',
      transport: 'unknown',
    } satisfies PreparationReconnect,
  })
  const recordConfirmed = useCallback(
    (value: PreparationView) => {
      queryClient.setQueryData<PreparationReconnect>(preparationReconnectKey, (current) => {
        if (current?.state !== 'clear') return current
        const frozen = current.frozen?.presentation
        const incoming = value.presentation
        if (
          frozen?.state === 'dismissed' &&
          incoming?.run_id === frozen.run_id &&
          incoming.updated_at <= frozen.updated_at
        )
          return current
        return { ...current, frozen: value }
      })
    },
    [queryClient],
  )
  const query = useQuery(
    preparationQuery(http, !live && updates?.hasBeenLive !== true, recordConfirmed),
  )
  const refetchPreparation = query.refetch
  useEffect(() => {
    if (streamState !== 'degraded' || updates?.hasBeenLive !== true) return
    void queryClient.cancelQueries(
      { queryKey: ['review-preparation'], exact: true },
      { silent: true },
    )
    queryClient.setQueryData<PreparationReconnect>(preparationReconnectKey, (current) =>
      current?.transport === 'degraded'
        ? current
        : {
            epoch: (current?.epoch ?? 0) + 1,
            state: 'debt',
            transport: 'degraded',
            ...(current?.frozen === undefined ? {} : { frozen: current.frozen }),
          },
    )
  }, [queryClient, streamState, updates?.hasBeenLive])
  useEffect(() => {
    if (streamState !== 'live') return
    queryClient.setQueryData<PreparationReconnect>(preparationReconnectKey, (current) =>
      current?.transport === 'live'
        ? current
        : { ...(current ?? { epoch: 0, state: 'clear' }), transport: 'live' },
    )
  }, [queryClient, streamState])
  useEffect(() => {
    const epoch = reconnectQuery.data.epoch
    if (
      streamState !== 'live' ||
      reconnectQuery.data.transport !== 'live' ||
      reconnectQuery.data.state !== 'debt'
    )
      return
    const claim = { won: false }
    queryClient.setQueryData<PreparationReconnect>(preparationReconnectKey, (current) => {
      if (current?.epoch !== epoch || current.transport !== 'live' || current.state !== 'debt')
        return current
      claim.won = true
      return { ...current, state: 'refreshing' }
    })
    if (!claim.won) return
    void (async () => {
      let successful: boolean
      let confirmed: PreparationView | undefined
      try {
        await queryClient.cancelQueries(
          { queryKey: ['review-preparation'], exact: true },
          { silent: true },
        )
        const result = await refetchPreparation({ cancelRefetch: true })
        successful = result.isSuccess
        if (result.isSuccess) confirmed = result.data
      } catch {
        successful = false
      }
      if (successful) {
        const adoption = { accepted: false }
        queryClient.setQueryData<PreparationReconnect>(preparationReconnectKey, (current) => {
          if (
            current?.epoch !== epoch ||
            current.transport !== 'live' ||
            current.state !== 'refreshing'
          )
            return current
          adoption.accepted = true
          return {
            epoch,
            state: 'clear',
            transport: 'live',
            ...(confirmed === undefined ? {} : { frozen: confirmed }),
          }
        })
        if (!adoption.accepted) {
          const frozen =
            queryClient.getQueryData<PreparationReconnect>(preparationReconnectKey)?.frozen
          if (frozen?.presentation?.state === 'dismissed')
            queryClient.setQueryData(['review-preparation'], frozen)
        }
        return
      }
      queryClient.setQueryData<PreparationReconnect>(preparationReconnectKey, (current) =>
        current?.epoch === epoch && current.transport === 'live' && current.state === 'refreshing'
          ? { ...current, state: 'retry' }
          : current,
      )
      window.setTimeout(() => {
        queryClient.setQueryData<PreparationReconnect>(preparationReconnectKey, (current) => {
          if (current === undefined) return current
          if (current.epoch !== epoch || current.transport !== 'live' || current.state !== 'retry')
            return current
          return { ...current, state: 'debt' }
        })
      }, 1_500)
    })()
  }, [queryClient, reconnectQuery.data, refetchPreparation, streamState])
  const reconnecting = reconnectQuery.data.state !== 'clear'
  // TanStack Query retains the last successful snapshot while a refetch is in
  // flight or the stream is degraded.  Frames never write this cache, so this
  // is the last server-confirmed state rather than a client-side catch-up.
  const data = reconnecting ? reconnectQuery.data.frozen : query.data
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
      queryClient.invalidateQueries({ queryKey: ['library'] }),
      queryClient.invalidateQueries({ queryKey: ['attention'] }),
    ])
  }, [live, queryClient, snapshot])
  const adoptDismissal = useCallback(
    async (runId: string, value: PreparationView) => {
      const dismissed = value.presentation
      if (dismissed?.run_id !== runId || dismissed.state !== 'dismissed') return false
      const responseIsStale = () =>
        [
          queryClient.getQueryData<PreparationView>(['review-preparation']),
          queryClient.getQueryData<PreparationReconnect>(preparationReconnectKey)?.frozen,
        ].some((candidate) => {
          const current = candidate?.presentation
          return (
            current !== undefined &&
            current !== null &&
            (current.run_id !== runId || current.updated_at > dismissed.updated_at)
          )
        })
      if (responseIsStale()) return false
      let dismissalEpoch: number | undefined
      queryClient.setQueryData<PreparationReconnect>(preparationReconnectKey, (current) => {
        if (current === undefined || current.state === 'clear') return current
        dismissalEpoch = current.epoch + 1
        return {
          ...current,
          epoch: dismissalEpoch,
          state: current.transport === 'live' ? 'retry' : current.state,
        }
      })
      await queryClient.cancelQueries(
        { queryKey: ['review-preparation'], exact: true },
        { silent: true },
      )
      const reconnect = queryClient.getQueryData<PreparationReconnect>(preparationReconnectKey)
      const ownedEpoch = dismissalEpoch
      if ((ownedEpoch !== undefined && reconnect?.epoch !== ownedEpoch) || responseIsStale())
        return false
      queryClient.setQueryData(['review-preparation'], value)
      queryClient.setQueryData<PreparationReconnect>(preparationReconnectKey, (current) =>
        current === undefined
          ? current
          : {
              ...current,
              state: ownedEpoch === undefined ? current.state : 'clear',
              frozen: value,
            },
      )
      return true
    },
    [queryClient],
  )
  return { data, queryClient, http, live, reconnecting, adoptDismissal }
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
  const copy =
    condition.state === 'checking'
      ? 'Preparation remains safely paused while the configured connection is checked.'
      : condition.state === 'recovered'
        ? 'Preparation resumed from its retained checkpoint. Completed reviews remain available.'
        : `${connectionCopy(condition.code)} ${String(paused)} preparation ${paused === 1 ? 'work is' : 'works are'} safely paused. Completed reviews remain available.`
  const canCheck = condition.state === 'paused' && !checking
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
      await queryClient.invalidateQueries({ queryKey: ['review-preparation'] })
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['library'] }),
        queryClient.invalidateQueries({ queryKey: ['attention'] }),
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
 * The eight buckets a work moves through, in the order an owner reads them.
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
  failed: 'Could not finish',
}

export const PreparationActivity = () => {
  const { data, queryClient, http } = usePreparation()
  const [open, setOpen] = useState(false)
  const trigger = useRef<HTMLButtonElement>(null)
  const action = useMutation({
    mutationFn: (path: string) => http.preparationAction(path),
    retry: false,
    onSettled: async () => {
      await queryClient.invalidateQueries({ queryKey: ['review-preparation'] })
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['library'] }),
        queryClient.invalidateQueries({ queryKey: ['attention'] }),
      ])
    },
  })
  const unavailable = !data || (!data.active && !data.actionable && !data.paused)
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
    <div className="preparation-activity">
      <button
        ref={trigger}
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
      >
        {data?.paused
          ? 'Preparation paused'
          : data?.actionable
            ? 'Preparation needs attention'
            : 'Preparing libraries'}
      </button>
      {open && data ? (
        <section className="preparation-popover" aria-label="Background activity">
          <h2>Background activity</h2>
          {data.libraries.map((item) => (
            <article key={`${item.connection}:${item.library}`}>
              <strong>{item.library}</strong>
              <span>
                {item.state} · {item.checked}/{item.total}
              </span>
              <dl className="preparation-stages">
                {(item.stages ?? []).map((stage) => (
                  <div key={stage.stage}>
                    <dt>{stageLabels[stage.stage] ?? stage.stage}</dt>
                    <dd>{stage.count}</dd>
                  </div>
                ))}
              </dl>
              <button
                disabled={
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
                  disabled={action.isPending || data.paused}
                  onClick={() =>
                    action.mutate(
                      `${encodeURIComponent(item.connection)}/${encodeURIComponent(item.library)}/retry`,
                    )
                  }
                >
                  Retry
                </button>
              ) : null}
            </article>
          ))}
          <button
            disabled={action.isPending}
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
    document.cookie = `ml_theme=${next}; Path=/; Max-Age=31536000; SameSite=Strict; Secure`
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
    queryFn: ({ signal }) => http.getAppearance(signal),
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
const RouteFailure = ({ error, reset }: { error: Error; reset: () => void }) =>
  error instanceof ChunkRecoveryError && error.exhausted ? (
    <section className="route-state">
      <p className="eyebrow">Application update unavailable</p>
      <h1>The current workspace assets could not be loaded.</h1>
      <p role="alert">{error.message}</p>
      <button onClick={() => reloadCurrentApplication()}>Reload the current application</button>
    </section>
  ) : (
    <section className="route-state">
      <p className="eyebrow">Review unavailable</p>
      <h1>This workspace could not be loaded.</h1>
      <p role="alert">{error.message}</p>
      <button onClick={reset}>Try again</button>
    </section>
  )
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
  const initial = libraryRoute.useLoaderData()
  const { http } = rootRoute.useRouteContext()
  const search = libraryRoute.useSearch()
  const query = useQuery({ ...libraryQuery(http, search), initialData: initial })
  return <LibraryPage data={query.data} />
}
const AttentionRouteComponent = () => {
  const initial = attentionRoute.useLoaderData()
  const { http } = rootRoute.useRouteContext()
  const search = attentionRoute.useSearch()
  const query = useQuery({ ...attentionQuery(http, search), initialData: initial })
  return <AttentionPage data={query.data} />
}
const DiagnosticRouteComponent = () => {
  const { reviewCaseId } = detailRoute.useParams()
  const { http } = rootRoute.useRouteContext()
  const search = detailRoute.useSearch()
  const detail = useQuery(detailQuery(http, reviewCaseId))
  const backSearch = {
    lens: search.lens,
    preview: reviewCaseId,
    ...(search.field ? { field: search.field } : {}),
  }
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
  loaderDeps: ({ search }) => search,
  loader: ({ context, deps, abortController }) =>
    context.queryClient.ensureQueryData({
      ...libraryQuery(context.http, deps),
      queryFn: () => context.http.getLibrary(deps, abortController.signal),
    }),
  component: LibraryRouteComponent,
})
export const attentionRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/attention',
  validateSearch: normalizeAttentionSearch,
  loaderDeps: ({ search }) => search,
  loader: ({ context, deps, abortController }) =>
    context.queryClient.ensureQueryData({
      ...attentionQuery(context.http, deps),
      queryFn: () => context.http.getAttention(deps, abortController.signal),
    }),
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
  const initial = policyRoute.useLoaderData()
  return initial === null ? null : <LoadedPolicyRoute initial={initial} />
}
const LoadedPolicyRoute = ({ initial }: { initial: PolicyCritical }) => {
  const { http } = rootRoute.useRouteContext()
  const state = useQuery({ ...policyStateQuery(http), initialData: initial.state })
  const shadow = useQuery({ ...shadowQuery(http), initialData: initial.shadow })
  const safety = useQuery({ ...safetyQuery(http), initialData: initial.safety })
  return <PolicyPage data={{ state: state.data, shadow: shadow.data, safety: safety.data }} />
}
export const policyRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/policy',
  validateSearch: normalizePolicySearch,
  loader: async ({ context, abortController }) => {
    const [state, shadow, safety] = await Promise.allSettled([
      context.queryClient.ensureQueryData({
        ...policyStateQuery(context.http),
        queryFn: () => context.http.getPolicyState(abortController.signal),
      }),
      context.queryClient.ensureQueryData({
        ...shadowQuery(context.http),
        queryFn: () => context.http.getShadowPosture(abortController.signal),
      }),
      context.queryClient.ensureQueryData({
        ...safetyQuery(context.http),
        queryFn: () => context.http.getAutomaticSafety(abortController.signal),
      }),
    ])
    for (const result of [state, shadow, safety]) {
      if (result.status === 'fulfilled') continue
      // The client has scheduled document navigation. Keeping this loader
      // fulfilled avoids an unhandled concurrent loader rejection during it.
      if (result.reason instanceof SessionExpiredError) return null
      throw result.reason
    }
    if (state.status !== 'fulfilled') throw state.reason
    if (shadow.status !== 'fulfilled') throw shadow.reason
    if (safety.status !== 'fulfilled') throw safety.reason
    return { state: state.value, shadow: shadow.value, safety: safety.value }
  },
  component: PolicyRouteComponent,
})
export const activityRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/activity',
  validateSearch: normalizeActivitySearch,
  loaderDeps: ({ search }) => search,
  loader: ({ context, deps, abortController }) =>
    context.queryClient.ensureQueryData({
      ...activityQuery(context.http, deps),
      queryFn: () => context.http.getActivity(deps, abortController.signal),
    }),
  pendingMs: Infinity,
  component: () => <ActivityPage initial={activityRoute.useLoaderData()} />,
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
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  }),
  history?: ReturnType<typeof createMemoryHistory>,
  updates?: LiveUpdateStream,
) =>
  createRouter({
    routeTree,
    context: { http, queryClient, ...(updates ? { updates } : {}) },
    ...(history ? { history } : {}),
    defaultPreload: 'intent',
    defaultPreloadStaleTime: 15_000,
    defaultPendingMs: 140,
    defaultPendingMinMs: 220,
    scrollRestoration: true,
  })
export type ApplicationRouter = ReturnType<typeof createApplicationRouter>
declare module '@tanstack/react-router' {
  interface Register {
    router: ApplicationRouter
  }
}
