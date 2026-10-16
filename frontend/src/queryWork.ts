import {
  focusManager,
  hashKey,
  CancelledError,
  useQuery,
  useQueryClient,
} from '@tanstack/react-query'
import type {
  DefaultError,
  DefinedInitialDataOptions,
  DefinedUseQueryResult,
  InvalidateOptions,
  InvalidateQueryFilters,
  Query,
  QueryClient,
  QueryExecuteOptions,
  QueryFunctionContext,
  QueryKey,
  UseQueryOptions,
  UseQueryResult,
} from '@tanstack/react-query'
import { useEffect, useEffectEvent, useRef } from 'react'
import type { ReadWorkClass } from './api/http'

type OwnerFetchMeta = NonNullable<Query['state']['fetchMeta']> & {
  ownerWorkClass: ReadWorkClass
}
const backgroundFetch: OwnerFetchMeta = { ownerWorkClass: 'background' }
const foregroundFetch: OwnerFetchMeta = { ownerWorkClass: 'foreground' }
const isBackground = (meta: Query['state']['fetchMeta']) =>
  meta !== null && 'ownerWorkClass' in meta && meta.ownerWorkClass === 'background'

/** Read the purpose of this actual fetch, including an offline-resumed fetch. */
export function queryWorkClass(
  context: Pick<QueryFunctionContext, 'client' | 'queryKey'>,
): ReadWorkClass {
  const query = context.client.getQueryCache().find({ queryKey: context.queryKey, exact: true })
  return query !== undefined && isBackground(query.state.fetchMeta) ? 'background' : 'foreground'
}

export const backgroundCoalesceMilliseconds = 120
export interface BackgroundWorkOptions {
  schedule?: (run: () => void, delay: number) => number
  cancel?: (handle: number) => void
  visible?: () => boolean
  observeVisibility?: (listener: () => void) => () => void
}
interface BackgroundOwner {
  references: number
  invalidate: (query: Query, throwOnError: boolean) => Promise<unknown>
  hasDebt: (query: Query) => boolean
  markStale: (query: Query) => void
  hold: (query: Query) => (succeeded: boolean) => void
  drain: () => void
  stop: () => void
}
const backgroundOwners = new WeakMap<QueryClient, BackgroundOwner>()

const backgroundOwner = (
  client: QueryClient,
  options: BackgroundWorkOptions = {},
): BackgroundOwner => {
  const retained = backgroundOwners.get(client)
  if (retained) return retained
  const cache = client.getQueryCache()
  const reads = new Set(cache.getAll().filter((query) => query.state.fetchStatus !== 'idle'))
  const owed = new Set<Query>()
  const ready = new Set<Query>()
  // Marked stale while a read was under way, owing no read of its own.
  const stale = new Set<Query>()
  const held = new Map<Query, { count: number; debt: boolean }>()
  const schedule = options.schedule ?? ((run, delay) => window.setTimeout(run, delay))
  const cancel = options.cancel ?? ((handle) => window.clearTimeout(handle))
  const visible =
    options.visible ??
    (() => typeof document === 'undefined' || document.visibilityState !== 'hidden')
  let timer: number | null = null
  let stopped = false
  const same = (query: Query) => cache.get(query.queryHash) === query
  const eligible = (query: Query) =>
    same(query) && query.isActive() && !query.isDisabled() && !query.isStatic()
  const hasDebt = (query: Query) =>
    owed.has(query) || ready.has(query) || stale.has(query) || held.get(query)?.debt === true
  const queue = () => {
    if (!stopped && visible() && ready.size > 0)
      timer ??= schedule(drain, backgroundCoalesceMilliseconds)
  }
  const invalidate = (query: Query, throwOnError = false): Promise<unknown> => {
    if (stopped || !same(query)) return Promise.resolve()
    stale.delete(query)
    query.invalidate()
    if (!eligible(query)) {
      owed.delete(query)
      ready.delete(query)
      return Promise.resolve()
    }
    const hold = held.get(query)
    if (hold) {
      hold.debt = true
      return Promise.resolve()
    }
    if (!visible()) {
      ready.add(query)
      return Promise.resolve()
    }
    ready.delete(query)
    if (query.state.fetchStatus !== 'idle') owed.add(query)
    let result = query.fetch(undefined, { cancelRefetch: false, meta: backgroundFetch })
    if (!throwOnError) result = result.catch(() => undefined)
    return query.state.fetchStatus === 'paused' ? Promise.resolve() : result
  }
  function drain() {
    timer = null
    if (stopped || !visible()) return
    for (const query of [...ready]) void invalidate(query)
  }
  const unsubscribe = cache.subscribe((event) => {
    const query =
      event.type === 'removed'
        ? [...reads, ...owed, ...ready, ...stale, ...held.keys()].find(
            (retained) => retained === event.query,
          )
        : cache.get(event.query.queryHash)
    if (!query || query !== event.query) return
    if (event.type === 'removed') {
      reads.delete(query)
      owed.delete(query)
      ready.delete(query)
      stale.delete(query)
      held.delete(query)
      return
    }
    if (
      (event.type === 'observerRemoved' || event.type === 'observerOptionsUpdated') &&
      !eligible(query)
    ) {
      if (held.has(query)) return
      if (owed.delete(query) || ready.delete(query) || stale.has(query)) {
        ready.delete(query)
        stale.delete(query)
        query.invalidate()
      }
      return
    }
    if (stopped || event.type !== 'updated') return
    if (event.action.type === 'fetch') {
      reads.add(query)
      owed.delete(query)
      ready.delete(query)
      stale.delete(query)
      return
    }
    if (event.action.type === 'error') {
      reads.delete(query)
      const marked = stale.delete(query)
      const debt = owed.delete(query) || ready.has(query) || marked
      ready.delete(query)
      if (debt) query.invalidate()
      return
    }
    if (event.action.type !== 'success') return
    if (event.action.manual === true) {
      if (hasDebt(query)) query.invalidate()
      return
    }
    reads.delete(query)
    if (stale.delete(query)) query.invalidate()
    if (owed.delete(query)) {
      ready.add(query)
      query.invalidate()
      queue()
    }
  })
  const observeVisibility =
    options.observeVisibility ??
    ((listener: () => void) => {
      if (typeof document === 'undefined') return () => undefined
      document.addEventListener('visibilitychange', listener)
      return () => document.removeEventListener('visibilitychange', listener)
    })
  // Let a stream's foreground resync enter its coalescer first; both then consume
  // the same ready obligation instead of starting a second visibility refresh.
  const unobserve = observeVisibility(() => {
    if (visible()) queueMicrotask(queue)
  })
  const owner: BackgroundOwner = {
    references: 0,
    invalidate,
    hasDebt,
    markStale(query) {
      if (!stopped && same(query) && query.state.fetchStatus !== 'idle' && !owed.has(query))
        stale.add(query)
    },
    drain,
    hold(query) {
      let state = held.get(query)
      if (!state) {
        state = { count: 0, debt: owed.delete(query) || ready.has(query) }
        ready.delete(query)
        held.set(query, state)
      }
      state.count += 1
      let released = false
      return (succeeded) => {
        if (released) return
        released = true
        if (--state.count !== 0) return
        held.delete(query)
        if (!state.debt || !same(query)) return
        query.invalidate()
        if (succeeded && !stopped) {
          ready.add(query)
          queue()
        }
      }
    },
    stop() {
      stopped = true
      for (const query of new Set([...reads, ...owed, ...ready, ...stale])) {
        if (!same(query)) continue
        void query.cancel({ silent: true })
        query.invalidate()
      }
      reads.clear()
      owed.clear()
      ready.clear()
      stale.clear()
      held.clear()
      unsubscribe()
      unobserve()
      if (timer !== null) cancel(timer)
      backgroundOwners.delete(client)
    },
  }
  backgroundOwners.set(client, owner)
  return owner
}

export const mountBackgroundWork = (
  client: QueryClient,
  options: BackgroundWorkOptions = {},
): (() => void) => {
  const owner = backgroundOwner(client, options)
  owner.references += 1
  return () => {
    if (--owner.references === 0) owner.stop()
  }
}
export const hasBackgroundDebt = (client: QueryClient, query: Query): boolean =>
  backgroundOwners.get(client)?.hasDebt(query) ?? false
export const drainBackgroundWork = (client: QueryClient): void =>
  backgroundOwners.get(client)?.drain()
export const holdBackgroundQuery = (
  client: QueryClient,
  query: Query,
): ((succeeded: boolean) => void) => backgroundOwner(client).hold(query)

/**
 * Mark every match stale and coalesce a concrete refresh obligation, or with
 * `refetchType: 'none'` only keep each match stale across a read under way.
 */
export function invalidateBackgroundQueries(
  client: QueryClient,
  filters: InvalidateQueryFilters = {},
  options: InvalidateOptions = {},
): Promise<void> {
  void client.invalidateQueries({ ...filters, refetchType: 'none' })
  if (filters.refetchType === 'none') {
    // Stale now and still stale when a read already under way lands; the
    // caller owns the read that follows.
    for (const query of client.getQueryCache().findAll(filters))
      backgroundOwners.get(client)?.markStale(query)
    return Promise.resolve()
  }
  const owner = backgroundOwner(client)
  const queries = client
    .getQueryCache()
    .findAll({ ...filters, type: filters.refetchType ?? filters.type ?? 'active' })
  return Promise.all(
    queries.map((query) => owner.invalidate(query, options.throwOnError === true)),
  ).then(() => undefined)
}

/**
 * Warm a read the owner is about to make as background work, deduplicated by
 * the cache. A required read of the same key later promotes or reuses it. A key
 * something already reads is not warmed: its observers, refreshes and holds own it.
 */
export function prefetchBackgroundQuery<TQueryFnData, TError, TData, TQueryKey extends QueryKey>(
  client: QueryClient,
  options: QueryExecuteOptions<TQueryFnData, TError, TData, TData, TQueryKey>,
): void {
  const defaulted = client.defaultQueryOptions(options)
  const query = client.getQueryCache().build(client, defaulted)
  if (query.getObserversCount() > 0) return
  const staleTime =
    typeof defaulted.staleTime === 'function' ? defaulted.staleTime(query) : defaulted.staleTime
  if (query.state.fetchStatus !== 'idle' || !query.isStaleByTime(staleTime)) return
  void query
    .fetch(defaulted, { cancelRefetch: false, meta: backgroundFetch })
    .catch(() => undefined)
}

/** Explicit result reads and retries promote a pending background request. */
export async function runRequiredRead<T>(
  client: QueryClient,
  key: QueryKey,
  read: () => Promise<T>,
): Promise<T> {
  const query = client.getQueryCache().find({ queryKey: key, exact: true })
  if (query && query.state.fetchStatus !== 'idle' && isBackground(query.state.fetchMeta))
    await query.cancel({ revert: true, silent: true })
  return read()
}

export async function invalidateForegroundQueries(
  client: QueryClient,
  filters: InvalidateQueryFilters,
): Promise<void> {
  if (filters.refetchType === 'none') return client.invalidateQueries(filters)
  const queries = client
    .getQueryCache()
    .findAll({ ...filters, type: filters.refetchType ?? filters.type ?? 'active' })
  await Promise.all(
    queries.map(async (query) => {
      if (query.state.fetchStatus !== 'idle' && isBackground(query.state.fetchMeta))
        await query.cancel({ revert: true, silent: true })
    }),
  )
  await client.invalidateQueries(filters)
}

/** A required read may replace only the exact background fetch it encountered. */
export async function promoteRequiredRead(client: QueryClient, key: QueryKey): Promise<void> {
  const cache = client.getQueryCache()
  const query = cache.find({ queryKey: key, exact: true })
  if (!query || query.state.fetchStatus === 'idle' || !isBackground(query.state.fetchMeta)) return
  await query.cancel({ revert: true, silent: true })
  if (cache.get(query.queryHash) !== query || !query.isActive()) return
  await query
    .fetch(undefined, { cancelRefetch: false, meta: foregroundFetch })
    .catch(() => undefined)
}

export function useRequiredRead(client: QueryClient, key: QueryKey, enabled = true): void {
  const queryHash = hashKey(key)
  useEffect(() => {
    const query = client.getQueryCache().get(queryHash)
    if (enabled && query) void promoteRequiredRead(client, query.queryKey)
  }, [client, queryHash, enabled])
}

/**
 * A required read that keeps the result this view last showed while a newer
 * key reads. Unlike keepPreviousData it never revives a result that a failed
 * read already cleared from the screen.
 */
export function useRetainedQuery<TQueryFnData, TError, TQueryKey extends QueryKey>(
  options: UseQueryOptions<TQueryFnData, TError, TQueryFnData, TQueryKey>,
): UseQueryResult<TQueryFnData, TError> {
  const showing = useRef(false)
  const result = useRequiredQuery({
    ...options,
    placeholderData: (previous) => (showing.current ? previous : undefined),
  })
  useEffect(() => {
    showing.current = result.data !== undefined
  })
  return result
}

export function useRequiredQuery<
  TQueryFnData = unknown,
  TError = DefaultError,
  TData = TQueryFnData,
  TQueryKey extends QueryKey = QueryKey,
>(
  options: DefinedInitialDataOptions<TQueryFnData, TError, TData, TQueryKey>,
  client?: QueryClient,
): DefinedUseQueryResult<TData, TError>
export function useRequiredQuery<
  TQueryFnData = unknown,
  TError = DefaultError,
  TData = TQueryFnData,
  TQueryKey extends QueryKey = QueryKey,
>(
  options: UseQueryOptions<TQueryFnData, TError, TData, TQueryKey>,
  client?: QueryClient,
): UseQueryResult<TData, TError>
export function useRequiredQuery<TQueryFnData, TError, TData, TQueryKey extends QueryKey>(
  options: UseQueryOptions<TQueryFnData, TError, TData, TQueryKey>,
  suppliedClient?: QueryClient,
): UseQueryResult<TData, TError> {
  const client = useQueryClient(suppliedClient)
  const result = useQuery(
    { ...options, refetchInterval: false, refetchOnWindowFocus: false, refetchOnReconnect: false },
    client,
  )
  const queryHash = hashKey(options.queryKey)
  const interval = options.refetchInterval
  const startPolling = useEffectEvent(() => {
    if (!result.isEnabled || !interval) return
    const query = client.getQueryCache().build(client, options)
    let delay: number | false = false
    let timer: number | undefined
    let completedAt = 0
    const sample = () => {
      const scheduledDelay = delay
      updateInterval(false)
      if (delay !== scheduledDelay) return
      if (
        delay !== false &&
        focusManager.isFocused() &&
        query.state.fetchStatus === 'idle' &&
        Date.now() - completedAt >= delay
      )
        void invalidateBackgroundQueries(client, { queryKey: query.queryKey, exact: true })
    }
    const updateInterval = (completed: boolean) => {
      const candidate = typeof interval === 'function' ? interval(query) : interval
      const next = candidate && candidate > 0 && Number.isFinite(candidate) ? candidate : false
      if (completed) completedAt = Date.now()
      if (next === delay && !completed) return
      window.clearInterval(timer)
      delay = next
      timer = delay === false ? undefined : window.setInterval(sample, delay)
    }
    updateInterval(false)
    const unsubscribe = client.getQueryCache().subscribe((event) => {
      if (event.query !== query) return
      if (event.type === 'observerOptionsUpdated') updateInterval(false)
      if (event.type !== 'updated') return
      updateInterval(
        (event.action.type === 'success' && event.action.manual !== true) ||
          (event.action.type === 'error' && !(event.action.error instanceof CancelledError)),
      )
    })
    return () => {
      window.clearInterval(timer)
      unsubscribe()
    }
  })
  useEffect(() => startPolling(), [client, queryHash, interval, result.isEnabled])
  useRequiredRead(client, options.queryKey, result.isEnabled)
  return {
    ...result,
    refetch: (refetchOptions) =>
      runRequiredRead(client, options.queryKey, () => result.refetch(refetchOptions)),
  }
}
