import { QueryClient, QueryObserver } from '@tanstack/react-query'
import { expect, it, vi } from 'vitest'

import {
  beginDecisionEcho,
  confirmDecisionCase,
  endDecisionEcho,
  settleDecisionEcho,
} from '../src/decisionEcho'
import { startLiveInvalidation } from '../src/liveInvalidation'
import { createLiveStream } from './fixtures'

const fixture = () => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: Infinity } },
  })
  const key = ['review-case', 'case-1']
  const reads: {
    signal: AbortSignal
    resolve: (value: number) => void
    reject: (error: Error) => void
  }[] = []
  const options = {
    queryKey: key,
    queryFn: ({ signal }: { signal: AbortSignal }) =>
      new Promise<number>((resolve, reject) => reads.push({ signal, resolve, reject })),
  }
  client.setQueryData(key, 0)
  const observer = new QueryObserver(client, { ...options, staleTime: Infinity })
  const release = observer.subscribe(() => undefined)
  const stream = createLiveStream()
  const scheduled = new Map<number, () => void>()
  let handle = 0
  let visible = true
  const stop = startLiveInvalidation(stream, client, {
    schedule: (run) => {
      const id = ++handle
      scheduled.set(id, run)
      return id
    },
    cancel: (id) => {
      scheduled.delete(id)
    },
    visible: () => visible,
    observeVisibility: () => () => undefined,
    observeActivity: () => () => undefined,
  })
  const flush = () => {
    const callbacks = [...scheduled.values()]
    scheduled.clear()
    for (const callback of callbacks) callback()
  }
  const confirm = () =>
    confirmDecisionCase(client, 'case-1', () => client.query({ ...options, staleTime: 0 }))
  return {
    client,
    key,
    reads,
    options,
    release,
    stream,
    stop,
    flush,
    confirm,
    hide: () => {
      visible = false
    },
  }
}

it('cancels a pre-acknowledgement read once and coalesces case/resync behind native confirmation', async () => {
  const f = fixture()
  const old = f.client.query({ ...f.options, staleTime: 0 }).catch(() => undefined)
  const result = f.confirm()
  await vi.waitFor(() => expect(f.reads).toHaveLength(2))
  expect(f.reads[0]?.signal.aborted).toBe(true)
  beginDecisionEcho(f.client, 'case-1')
  endDecisionEcho(f.client, 'case-1', [])
  settleDecisionEcho(f.client)
  for (let sequence = 1; sequence <= 10; sequence++) {
    f.stream.announce({
      sequence,
      subject: sequence % 2 ? 'case-1' : null,
      topic: sequence % 2 ? 'case' : 'resync',
    })
    f.flush()
  }
  expect(f.reads).toHaveLength(2)
  expect(f.reads[1]?.signal.aborted).toBe(false)
  f.reads[1]?.resolve(1)
  expect(await result).toBe(1)
  await vi.waitFor(() => expect(f.reads).toHaveLength(3))
  f.reads[0]?.resolve(99)
  f.reads[2]?.resolve(2)
  await old.catch(() => undefined)
  await vi.waitFor(() => expect(f.client.getQueryData(f.key)).toBe(2))
  f.release()
  f.stop()
  f.client.clear()
})

it.each(['navigation', 'hidden', 'stopped'])(
  'retains confirmation through %s and leaves deferred freshness for remount',
  async (boundary) => {
    const f = fixture()
    const result = f.confirm()
    await vi.waitFor(() => expect(f.reads).toHaveLength(1))
    f.stream.announce({ sequence: 1, topic: 'case', subject: 'case-1' })
    f.flush()
    if (boundary === 'navigation') f.release()
    if (boundary === 'hidden') f.hide()
    if (boundary === 'stopped') f.stop()
    expect(f.reads[0]?.signal.aborted).toBe(false)
    f.reads[0]?.resolve(1)
    expect(await result).toBe(1)
    expect(f.reads).toHaveLength(1)
    expect(f.client.getQueryState(f.key)?.isInvalidated).toBe(true)
    if (boundary === 'navigation')
      expect(f.client.getQueryCache().find({ queryKey: f.key })?.getObserversCount()).toBe(0)
    f.release()
    f.stop()
    f.client.clear()
  },
)

it('serializes overlapping same-case confirmations and continues after one fails', async () => {
  const f = fixture()
  const first = f.confirm().catch((error: unknown) => error)
  const second = f.confirm()
  await vi.waitFor(() => expect(f.reads).toHaveLength(1))
  f.reads[0]?.reject(new Error('first failed'))
  expect(await first).toEqual(new Error('first failed'))
  await vi.waitFor(() => expect(f.reads).toHaveLength(2))
  f.reads[1]?.resolve(2)
  expect(await second).toBe(2)
  expect(f.client.getQueryData(f.key)).toBe(2)
  f.release()
  f.stop()
  f.client.clear()
})

it('keeps genuine confirmation failure stale/error without a trailing retry loop', async () => {
  const f = fixture()
  const result = f.confirm().catch((error: unknown) => error)
  await vi.waitFor(() => expect(f.reads).toHaveLength(1))
  f.stream.announce({ sequence: 1, topic: 'case', subject: 'case-1' })
  f.flush()
  f.reads[0]?.reject(new Error('unavailable'))
  expect(await result).toEqual(new Error('unavailable'))
  expect(f.client.getQueryState(f.key)).toMatchObject({ status: 'error', isInvalidated: true })
  expect(f.reads).toHaveLength(1)
  f.release()
  f.stop()
  f.client.clear()
})

it('does not recreate a removed exact query or accept reverted pre-acknowledgement data', async () => {
  for (const boundary of ['remove', 'revert']) {
    const f = fixture()
    const result = f.confirm().catch((error: unknown) => error)
    await vi.waitFor(() => expect(f.reads).toHaveLength(1))
    if (boundary === 'remove') f.client.clear()
    else await f.client.cancelQueries({ queryKey: f.key, exact: true })
    expect(await result).toBeInstanceOf(Error)
    expect(f.reads[0]?.signal.aborted).toBe(true)
    f.reads[0]?.resolve(99)
    if (boundary === 'remove') expect(f.client.getQueryCache().getAll()).toHaveLength(0)
    else expect(f.client.getQueryData(f.key)).toBe(0)
    f.release()
    f.stop()
    f.client.clear()
  }
})

it('allows other cases and history children to refresh during exact-case ownership', async () => {
  const f = fixture()
  const historyKey = [...f.key, 'history']
  const historyRead = vi.fn(async () => 'current history')
  f.client.setQueryData(historyKey, 'old history')
  const historyObserver = new QueryObserver(f.client, {
    queryKey: historyKey,
    queryFn: historyRead,
    staleTime: Infinity,
  })
  const releaseHistory = historyObserver.subscribe(() => undefined)
  const otherKey = ['review-case', 'case-2']
  f.client.setQueryData(otherKey, 0)
  const owned = f.confirm()
  await vi.waitFor(() => expect(f.reads).toHaveLength(1))
  const other = confirmDecisionCase(f.client, 'case-2', () =>
    f.client.query({ queryKey: otherKey, queryFn: () => Promise.resolve(2), staleTime: 0 }),
  )
  expect(await other).toBe(2)
  f.stream.announce({ sequence: 1, topic: 'resync', subject: null })
  f.flush()
  await vi.waitFor(() => expect(historyRead).toHaveBeenCalledTimes(1))
  expect(f.reads).toHaveLength(1)
  f.reads[0]?.resolve(1)
  expect(await owned).toBe(1)
  await vi.waitFor(() => expect(f.reads).toHaveLength(2))
  f.reads[1]?.resolve(3)
  releaseHistory()
  f.release()
  f.stop()
  f.client.clear()
})

it('serializes two successful confirmations so the second owns the newest cache publication', async () => {
  const f = fixture()
  const first = f.confirm()
  const second = f.confirm()
  await vi.waitFor(() => expect(f.reads).toHaveLength(1))
  f.reads[0]?.resolve(1)
  expect(await first).toBe(1)
  await vi.waitFor(() => expect(f.reads).toHaveLength(2))
  expect(f.reads[1]?.signal.aborted).toBe(false)
  f.reads[1]?.resolve(2)
  expect(await second).toBe(2)
  expect(f.client.getQueryData(f.key)).toBe(2)
  f.release()
  f.stop()
  f.client.clear()
})

it('fails closed when no exact case query exists', async () => {
  const client = new QueryClient()
  const refresh = vi.fn(async () => 1)
  await expect(confirmDecisionCase(client, 'missing', refresh)).rejects.toThrow('unavailable')
  expect(refresh).not.toHaveBeenCalled()
  expect(client.getQueryCache().getAll()).toHaveLength(0)
})
