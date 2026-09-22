import type { Query } from '@tanstack/react-query'
import { act, renderHook } from '@testing-library/react'
import { startLiveInvalidation } from '../src/liveInvalidation'
import { createLiveStream } from './fixtures'
import { mountBackgroundWork, hasBackgroundDebt, useRequiredQuery } from '../src/queryWork'
import { QueryClient, QueryObserver, onlineManager, focusManager } from '@tanstack/react-query'
import { expect, it, onTestFinished, vi } from 'vitest'
import {
  invalidateForegroundQueries,
  invalidateBackgroundQueries,
  promoteRequiredRead,
  queryWorkClass,
} from '../src/queryWork'

it('retains the actual paused fetch class and promotes only background work', async () => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  client.mount()
  const dispose = mountBackgroundWork(client)
  const reads: { role: string; signal: AbortSignal; resolve: (value: string) => void }[] = []
  const key = ['priority-read']
  const options = {
    queryKey: key,
    initialData: 'retained',
    staleTime: Infinity,
    queryFn: (context: {
      client: QueryClient
      queryKey: readonly unknown[]
      signal: AbortSignal
    }) =>
      new Promise<string>((resolve) =>
        reads.push({ role: queryWorkClass(context), signal: context.signal, resolve }),
      ),
  }
  const observer = new QueryObserver(client, options)
  const stop = observer.subscribe(() => undefined)
  onTestFinished(() => {
    onlineManager.setOnline(true)
    stop()
    dispose()
    client.unmount()
    client.clear()
  })
  onlineManager.setOnline(false)
  await invalidateBackgroundQueries(client, { queryKey: key })
  expect(client.getQueryState(key)?.fetchStatus).toBe('paused')
  expect(reads).toHaveLength(0)
  onlineManager.setOnline(true)
  await new Promise((resolve) => setTimeout(resolve, 0))
  expect(reads.map((read) => read.role)).toEqual(['background'])
  const promoted = promoteRequiredRead(client, key)
  await new Promise((resolve) => setTimeout(resolve, 0))
  expect(reads[0]?.signal.aborted).toBe(true)
  expect(reads.map((read) => read.role)).toEqual(['background', 'foreground'])
  const joined = invalidateBackgroundQueries(client, { queryKey: key })
  expect(reads).toHaveLength(2)
  expect(reads[1]?.signal.aborted).toBe(false)
  reads[1]?.resolve('foreground result')
  await promoted
  await joined
  reads[0]?.resolve('late obsolete result')
  await Promise.resolve()
  expect(client.getQueryData(key)).toBe('foreground result')
})

it.each([
  ['activity'],
  ['policy', 'transition'],
  ['reviewer-access', 'codex-device'],
  ['review-preparation'],
])('keeps one completion obligation for held background ticks on %j', async (...key) => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const timers = new Map<number, () => void>()
  let serial = 0
  const dispose = mountBackgroundWork(client, {
    schedule: (run) => {
      timers.set(++serial, run)
      return serial
    },
    cancel: (handle) => {
      timers.delete(handle)
    },
  })
  const reads: {
    resolve: (value: string) => void
    reject: (error: Error) => void
    signal: AbortSignal
  }[] = []
  const observer = new QueryObserver(client, {
    queryKey: key,
    initialData: 'initial',
    staleTime: Infinity,
    queryFn: ({ signal }) =>
      new Promise<string>((resolve, reject) => reads.push({ resolve, reject, signal })),
  })
  const stop = observer.subscribe(() => undefined)
  onTestFinished(() => {
    stop()
    dispose()
    client.clear()
  })
  const query = client.getQueryCache().find({ queryKey: key, exact: true })
  if (!query) throw new Error('query required')
  const first = invalidateBackgroundQueries(client, { queryKey: key })
  for (let i = 0; i < 10; i += 1) void invalidateBackgroundQueries(client, { queryKey: key })
  expect(reads).toHaveLength(1)
  expect(reads[0]?.signal.aborted).toBe(false)
  client.setQueryData(key, 'manual publication')
  expect(hasBackgroundDebt(client, query)).toBe(true)
  expect(query.state.isInvalidated).toBe(true)
  reads[0]?.resolve('before ticks')
  await first
  expect(timers.size).toBe(1)
  for (const run of timers.values()) run()
  timers.clear()
  expect(reads).toHaveLength(2)
  expect(hasBackgroundDebt(client, query)).toBe(false)
  void invalidateBackgroundQueries(client, { queryKey: key })
  reads[1]?.reject(new Error('unavailable'))
  await new Promise((resolve) => setTimeout(resolve, 0))
  expect(query.state.status).toBe('error')
  expect(query.state.isInvalidated).toBe(true)
  expect(hasBackgroundDebt(client, query)).toBe(false)
  expect(timers.size).toBe(0)
  const retry = invalidateBackgroundQueries(client, { queryKey: key })
  expect(reads).toHaveLength(3)
  reads[2]?.resolve('new event confirmed')
  await retry
  expect(query.state.data).toBe('new event confirmed')
})

it('retains hidden debt and never transfers it to a same-key replacement', async () => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  let visible = true
  let visibility: () => void = () => undefined
  const timers = new Map<number, () => void>()
  let serial = 0
  const dispose = mountBackgroundWork(client, {
    visible: () => visible,
    observeVisibility: (listener) => {
      visibility = listener
      return () => undefined
    },
    schedule: (run) => {
      timers.set(++serial, run)
      return serial
    },
    cancel: (handle) => {
      timers.delete(handle)
    },
  })
  let resolve!: (value: string) => void
  const key = ['activity', 'hidden']
  const observer = new QueryObserver(client, {
    queryKey: key,
    initialData: 'initial',
    staleTime: Infinity,
    queryFn: () =>
      new Promise<string>((settle) => {
        resolve = settle
      }),
  })
  const stop = observer.subscribe(() => undefined)
  onTestFinished(() => {
    stop()
    dispose()
    client.clear()
  })
  const query = client.getQueryCache().find({ queryKey: key, exact: true })
  if (!query) throw new Error('query required')
  const pending = invalidateBackgroundQueries(client, { queryKey: key })
  void invalidateBackgroundQueries(client, { queryKey: key })
  visible = false
  resolve('old success')
  await pending
  expect(hasBackgroundDebt(client, query)).toBe(true)
  expect(timers.size).toBe(0)
  stop()
  expect(query.state.isInvalidated).toBe(true)
  client.removeQueries({ queryKey: key, exact: true })
  client.setQueryData(key, 'replacement')
  visible = true
  visibility()
  await Promise.resolve()
  expect(timers.size).toBe(0)
  expect(client.getQueryData(key)).toBe('replacement')
})

it('keeps a foreground read across stream replacement and cancels it only at final owner teardown', async () => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  let signal!: AbortSignal
  const pending = client
    .query({
      queryKey: ['review-preparation'],
      queryFn: (context) => {
        signal = context.signal
        return new Promise<string>(() => undefined)
      },
    })
    .catch(() => undefined)
  const dispose = mountBackgroundWork(client)
  const stopFirst = startLiveInvalidation(createLiveStream(), client)
  stopFirst()
  expect(signal.aborted).toBe(false)
  const stopSecond = startLiveInvalidation(createLiveStream(), client)
  stopSecond()
  expect(signal.aborted).toBe(false)
  dispose()
  await pending
  expect(signal.aborted).toBe(true)
  expect(client.getQueryState(['review-preparation'])?.isInvalidated).toBe(true)
  client.clear()
})

it('samples four observers once, preserves cadence after errors, and ignores manual cache writes', async () => {
  vi.useFakeTimers()
  vi.setSystemTime(10_000)
  focusManager.setFocused(true)
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const dispose = mountBackgroundWork(client)
  const reads: { resolve: (value: string) => void; reject: (error: Error) => void }[] = []
  const options = {
    queryKey: ['sampled-status'],
    initialData: 'initial',
    staleTime: Infinity,
    queryFn: () => new Promise<string>((resolve, reject) => reads.push({ resolve, reject })),
    refetchInterval: 1_500,
  }
  const hooks = Array.from({ length: 4 }, () => renderHook(() => useRequiredQuery(options, client)))
  onTestFinished(() => {
    hooks.forEach((hook) => hook.unmount())
    dispose()
    client.clear()
    focusManager.setFocused(undefined)
    vi.useRealTimers()
  })
  await act(() => vi.advanceTimersByTimeAsync(1_500))
  expect(reads).toHaveLength(1)
  await act(() => vi.advanceTimersByTimeAsync(6_000))
  expect(reads).toHaveLength(1)
  const query = client.getQueryCache().find({ queryKey: options.queryKey })
  expect(query).toBeDefined()
  if (!query) throw new Error('missing sampled query')
  expect(hasBackgroundDebt(client, query)).toBe(false)
  await act(async () => {
    reads[0]?.resolve('native')
    await Promise.resolve()
  })
  await act(() => vi.advanceTimersByTimeAsync(1_000))
  await act(() => client.setQueryData(options.queryKey, 'manual'))
  await act(() => vi.advanceTimersByTimeAsync(500))
  expect(reads).toHaveLength(2)
  await act(async () => {
    reads[1]?.reject(new Error('temporary'))
    await Promise.resolve()
  })
  await act(() => vi.advanceTimersByTimeAsync(1_499))
  expect(reads).toHaveLength(2)
  await act(() => vi.advanceTimersByTimeAsync(1))
  expect(reads).toHaveLength(3)
  await act(async () => {
    reads[2]?.resolve('recovered')
    await Promise.resolve()
  })
  focusManager.setFocused(false)
  await act(() => vi.advanceTimersByTimeAsync(3_000))
  expect(reads).toHaveLength(3)
  focusManager.setFocused(true)
  await act(() => vi.advanceTimersByTimeAsync(1_500))
  expect(reads).toHaveLength(4)
  hooks.forEach((hook) => hook.unmount())
  await act(() => vi.advanceTimersByTimeAsync(6_000))
  expect(reads).toHaveLength(4)
})

it('lets the shortest observer cadence satisfy slower status samplers', async () => {
  vi.useFakeTimers()
  vi.setSystemTime(10_000)
  focusManager.setFocused(true)
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const dispose = mountBackgroundWork(client)
  const read = vi.fn(async () => 'status')
  const options = {
    queryKey: ['unequal-status'],
    queryFn: read,
    initialData: 'initial',
    staleTime: Infinity,
  }
  const fast = renderHook(() => useRequiredQuery({ ...options, refetchInterval: 1_000 }, client))
  const slow = renderHook(() => useRequiredQuery({ ...options, refetchInterval: 1_500 }, client))
  onTestFinished(() => {
    fast.unmount()
    slow.unmount()
    dispose()
    client.clear()
    focusManager.setFocused(undefined)
    vi.useRealTimers()
  })
  await act(() => vi.advanceTimersByTimeAsync(3_000))
  expect(read).toHaveBeenCalledTimes(3)
  fast.unmount()
  await act(() => vi.advanceTimersByTimeAsync(1_499))
  expect(read).toHaveBeenCalledTimes(3)
  await act(() => vi.advanceTimersByTimeAsync(1))
  expect(read).toHaveBeenCalledTimes(4)
})

it('reevaluates a dynamic sampling interval without manual writes postponing its cadence', async () => {
  vi.useFakeTimers()
  vi.setSystemTime(10_000)
  focusManager.setFocused(true)
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const dispose = mountBackgroundWork(client)
  let enabled = false
  const read = vi.fn(async () => 'status')
  const options = {
    queryKey: ['dynamic-status'],
    queryFn: read,
    initialData: 'initial',
    staleTime: Infinity,
    refetchInterval: () => (enabled ? 1_500 : false),
  }
  const hook = renderHook(() => useRequiredQuery(options, client))
  onTestFinished(() => {
    hook.unmount()
    dispose()
    client.clear()
    focusManager.setFocused(undefined)
    vi.useRealTimers()
  })
  await act(() => vi.advanceTimersByTimeAsync(3_000))
  expect(read).not.toHaveBeenCalled()
  enabled = true
  await act(() => client.setQueryData(options.queryKey, 'interval enabled'))
  await act(() => vi.advanceTimersByTimeAsync(1_000))
  await act(() => client.setQueryData(options.queryKey, 'manual'))
  await act(() => vi.advanceTimersByTimeAsync(500))
  expect(read).toHaveBeenCalledTimes(1)
  // A stream-state closure can disable an already scheduled tick without a
  // query publication or observer-option change. That tick must recheck it.
  enabled = false
  await act(() => vi.advanceTimersByTimeAsync(3_000))
  expect(read).toHaveBeenCalledTimes(1)
  hook.unmount()
  enabled = true
  await act(() => client.setQueryData(options.queryKey, 'after-unmount'))
  await act(() => vi.advanceTimersByTimeAsync(3_000))
  expect(read).toHaveBeenCalledTimes(1)
})

it('promotes a cold background command-result read before native invalidation', async () => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const dispose = mountBackgroundWork(client)
  const reads: { role: string; signal: AbortSignal; resolve: (value: string) => void }[] = []
  const key = ['review-case', 'command-result']
  const observer = new QueryObserver(client, {
    queryKey: key,
    enabled: false,
    queryFn: (context) =>
      new Promise<string>((resolve) =>
        reads.push({ role: queryWorkClass(context), signal: context.signal, resolve }),
      ),
  })
  const stop = observer.subscribe(() => undefined)
  const query = client.getQueryCache().find({ queryKey: key })
  if (!query) throw new Error('missing command result query')
  const meta: NonNullable<Query['state']['fetchMeta']> & { ownerWorkClass: 'background' } = {
    ownerWorkClass: 'background',
  }
  const old = query.fetch(undefined, { meta }).catch(() => undefined)
  observer.setOptions({ ...observer.options, enabled: true })
  onTestFinished(() => {
    stop()
    dispose()
    client.clear()
  })
  const refreshed = invalidateForegroundQueries(client, { queryKey: key })
  await new Promise((resolve) => setTimeout(resolve, 0))
  expect(reads.map((read) => read.role)).toEqual(['background', 'foreground'])
  expect(reads[0]?.signal.aborted).toBe(true)
  reads[1]?.resolve('required result')
  await refreshed
  reads[0]?.resolve('late background')
  await old
  expect(client.getQueryData(key)).toBe('required result')
})
