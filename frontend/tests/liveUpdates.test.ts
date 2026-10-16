import { mountBackgroundWork } from '../src/queryWork'
import { QueryClient, QueryObserver, onlineManager } from '@tanstack/react-query'
import { describe, expect, it, onTestFinished, vi } from 'vitest'

import {
  coalesceMilliseconds,
  invalidationKeys,
  libraryRefreshMilliseconds,
  ownerIdleMilliseconds,
  startLiveInvalidation,
} from '../src/liveInvalidation'
import { createLiveUpdateStream, readLiveUpdate } from '../src/liveUpdates'
import type { LiveUpdate } from '../src/liveUpdates'
import { beginDecisionEcho, endDecisionEcho, settleDecisionEcho } from '../src/decisionEcho'

// These harnesses own a complete query lifetime, separately from its stream bridge.
const startOwnedLiveInvalidation = (...args: Parameters<typeof startLiveInvalidation>) => {
  const disposeOwner = mountBackgroundWork(args[1], args[2])
  const stopStream = startLiveInvalidation(...args)
  return () => {
    stopStream()
    disposeOwner()
  }
}

const CASE = `review_case_${'a'.repeat(64)}`

const collectionHarness = (warm = false, key: readonly string[] = ['library', 'all']) => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  client.mount()
  const reads: {
    resolve: (value: string) => void
    reject: (error: Error) => void
    signal: AbortSignal
  }[] = []
  const timers = new Map<number, () => void>()
  const delays = new Map<number, number>()
  let timerId = 0
  let visible = true
  let clock = 0
  let visibilityListener: () => void = () => undefined
  const listeners = new Set<(update: LiveUpdate) => void>()
  const stop = startOwnedLiveInvalidation(
    {
      state: 'live',
      subscribe(listener) {
        listeners.add(listener)
        return () => listeners.delete(listener)
      },
      observeState: () => () => undefined,
      close: () => undefined,
    },
    client,
    {
      schedule: (run, delay) => {
        timers.set(++timerId, run)
        delays.set(timerId, delay)
        return timerId
      },
      cancel: (id) => {
        timers.delete(id)
      },
      visible: () => visible,
      observeVisibility: (listener) => {
        visibilityListener = listener
        return () => undefined
      },
      observeActivity: () => () => undefined,
      now: () => clock,
    },
  )
  const options = {
    queryKey: key,
    staleTime: Infinity,
    ...(warm ? { initialData: 'old' } : {}),
    queryFn: ({ signal }: { signal: AbortSignal }) =>
      new Promise<string>((resolve, reject) => {
        reads.push({ resolve, reject, signal })
      }),
  }
  const observer = new QueryObserver(client, options)
  let unsubscribe = observer.subscribe(() => undefined)
  if (warm) void observer.refetch()
  const query = client.getQueryCache().find({ queryKey: key, exact: true })
  if (!query) throw new Error('collection query was not constructed')
  const close = () => {
    stop()
    unsubscribe()
    client.unmount()
    client.clear()
  }
  onTestFinished(close)
  return {
    client,
    reads,
    timers,
    query,
    stop,
    options,
    emit: (topic: 'library' | 'resync' = 'library') => {
      for (const listener of listeners) listener({ sequence: 1, subject: null, topic })
    },
    send: (update: LiveUpdate) => {
      for (const listener of listeners) listener(update)
    },
    flush: () => {
      const pending = [...timers.values()]
      timers.clear()
      for (const run of pending) run()
    },
    /** Run only the timers due within the ordinary coalescing. */
    flushCoalesced: () => {
      for (const [id, run] of [...timers]) {
        if ((delays.get(id) ?? 0) > coalesceMilliseconds) continue
        timers.delete(id)
        run()
      }
    },
    /** The delays of the timers still waiting to run. */
    pendingDelays: () => [...timers.keys()].map((id) => delays.get(id)),
    /** Let the bridge's clock pass, e.g. beyond the Library refresh bound. */
    elapse: (milliseconds: number) => {
      clock += milliseconds
    },
    hide: () => {
      visible = false
      visibilityListener()
    },
    show: () => {
      visible = true
      visibilityListener()
    },
    unmount: () => {
      unsubscribe()
      unsubscribe = () => undefined
    },
    remount: () => {
      unsubscribe = new QueryObserver(client, options).subscribe(() => undefined)
    },
  }
}

describe('live collection completion debt', () => {
  it('does not spin a paused read and refreshes its completed snapshot once', async () => {
    onlineManager.setOnline(false)
    const h = collectionHarness()
    try {
      expect(h.query.state.fetchStatus).toBe('paused')
      h.emit()
      h.flush()
      h.flush()
      expect(h.reads).toHaveLength(0)
      expect(h.timers.size).toBe(0)
      onlineManager.setOnline(true)
      await vi.waitFor(() => expect(h.reads).toHaveLength(1))
      h.reads[0]?.resolve('before-frame')
      await vi.waitFor(() => expect(h.query.state.fetchStatus).toBe('idle'))
      expect(h.query.state.isInvalidated).toBe(true)
      h.flush()
      expect(h.reads).toHaveLength(2)
    } finally {
      onlineManager.setOnline(true)
    }
  })

  it('marks an inactive page stale without fetching until remount', async () => {
    const h = collectionHarness(true)
    h.reads[0]?.resolve('old')
    await vi.waitFor(() => expect(h.query.state.fetchStatus).toBe('idle'))
    h.unmount()
    h.emit()
    h.flush()
    expect(h.query.state.isInvalidated).toBe(true)
    expect(h.reads).toHaveLength(1)
    h.remount()
    expect(h.reads).toHaveLength(2)
  })

  it('preserves completion debt when an independent explicit refresh replaces the request', async () => {
    const h = collectionHarness(true)
    h.emit()
    h.flush()
    void h.client.invalidateQueries({ queryKey: h.query.queryKey, exact: true })
    expect(h.reads[0]?.signal.aborted).toBe(true)
    expect(h.reads).toHaveLength(2)
    h.elapse(libraryRefreshMilliseconds)
    h.emit()
    h.flush()
    expect(h.reads[1]?.signal.aborted).toBe(false)
    h.reads[0]?.resolve('obsolete')
    h.reads[1]?.resolve('replacement')
    await vi.waitFor(() => expect(h.query.state.data).toBe('replacement'))
    expect(h.query.state.isInvalidated).toBe(true)
    h.flush()
    expect(h.reads).toHaveLength(3)
  })

  it('transfers a deferred decision echo only after concrete refresh debt owns it', async () => {
    const h = collectionHarness(true)
    beginDecisionEcho(h.client, CASE)
    endDecisionEcho(h.client, CASE, [8])
    h.send({ sequence: 8, subject: CASE, topic: 'library' })
    h.flush()
    expect(h.reads).toHaveLength(1)
    h.emit()
    h.flush()
    settleDecisionEcho(h.client)
    expect(h.reads[0]?.signal.aborted).toBe(false)
    expect(h.reads).toHaveLength(1)
    h.reads[0]?.resolve('before-frame')
    await vi.waitFor(() => expect(h.query.state.fetchStatus).toBe('idle'))
    h.flush()
    expect(h.reads).toHaveLength(2)
  })

  it.each([false, true])(
    'retains a %s warm read and coalesces repeated frames into one follow-up',
    async (warm) => {
      const h = collectionHarness(warm)
      expect(h.reads).toHaveLength(1)
      for (let i = 0; i < 20; i += 1) {
        h.emit()
        h.flush()
      }
      expect(h.reads).toHaveLength(1)
      expect(h.reads[0]?.signal.aborted).toBe(false)
      h.reads[0]?.resolve('before-frame')
      await vi.waitFor(() => expect(h.query.state.fetchStatus).toBe('idle'))
      expect(h.query.state.isInvalidated).toBe(true)
      h.flush()
      expect(h.reads).toHaveLength(2)
      h.reads[1]?.resolve('after-frame')
      await vi.waitFor(() => expect(h.query.state.data).toBe('after-frame'))
      expect(h.query.state.isInvalidated).toBe(false)
      h.flush()
      expect(h.reads).toHaveLength(2)
    },
  )

  it('retains one new debt for frames received during the follow-up', async () => {
    const h = collectionHarness(true)
    h.emit()
    h.flush()
    h.reads[0]?.resolve('first')
    await vi.waitFor(() => expect(h.query.state.fetchStatus).toBe('idle'))
    h.flush()
    for (let i = 0; i < 10; i += 1) {
      h.emit()
      h.flush()
    }
    expect(h.reads).toHaveLength(2)
    expect(h.reads[1]?.signal.aborted).toBe(false)
    h.reads[1]?.resolve('second')
    await vi.waitFor(() => expect(h.query.state.data).toBe('second'))
    expect(h.query.state.isInvalidated).toBe(true)
    h.flush()
    expect(h.reads).toHaveLength(3)
    h.reads[2]?.resolve('third')
    await vi.waitFor(() => expect(h.query.state.data).toBe('third'))
    expect(h.query.state.isInvalidated).toBe(false)
  })

  it('refetches a sustained run of Library frames once per bound and converges after the last', async () => {
    const h = collectionHarness(true)
    h.reads[0]?.resolve('initial')
    await vi.waitFor(() => expect(h.query.state.fetchStatus).toBe('idle'))
    // The first frame after a quiet bound reads at the ordinary coalescing.
    h.emit()
    expect(h.pendingDelays()).toEqual([coalesceMilliseconds])
    h.flush()
    expect(h.reads).toHaveLength(2)
    h.reads[1]?.resolve('first work')
    await vi.waitFor(() => expect(h.query.state.data).toBe('first work'))
    expect(h.query.state.isInvalidated).toBe(false)
    // Preparation keeps publishing: each frame marks the page stale at once,
    // but the next read waits for the bound measured from the last one.
    h.elapse(coalesceMilliseconds)
    h.emit()
    h.flush()
    expect(h.query.state.isInvalidated).toBe(true)
    expect(h.reads).toHaveLength(2)
    expect(h.pendingDelays()).toEqual([libraryRefreshMilliseconds - coalesceMilliseconds])
    for (let i = 0; i < 20; i += 1) {
      h.elapse(50)
      h.emit()
      const [coalesce] = [...h.pendingDelays()].slice(-1)
      expect(coalesce).toBe(coalesceMilliseconds)
    }
    // Twenty more frames schedule no further Library read of their own.
    expect(h.pendingDelays().filter((delay) => delay !== coalesceMilliseconds)).toEqual([
      libraryRefreshMilliseconds - coalesceMilliseconds,
    ])
    h.flush()
    expect(h.reads).toHaveLength(3)
    // Preparation ends. The bounded read began after the last frame, so it is
    // the converged answer and nothing further is owed.
    h.reads[2]?.resolve('final')
    await vi.waitFor(() => expect(h.query.state.data).toBe('final'))
    expect(h.query.state.isInvalidated).toBe(false)
    expect(h.timers.size).toBe(0)
    h.flush()
    expect(h.reads).toHaveLength(3)
    // After a quiet bound the next frame is immediate again.
    h.elapse(libraryRefreshMilliseconds)
    h.emit()
    h.flush()
    expect(h.reads).toHaveLength(4)
  })

  it('pays bounded Library debt at once when a resync reads, and bounds from there', async () => {
    const h = collectionHarness(true)
    h.reads[0]?.resolve('initial')
    await vi.waitFor(() => expect(h.query.state.fetchStatus).toBe('idle'))
    h.emit()
    h.flush()
    h.reads[1]?.resolve('first work')
    await vi.waitFor(() => expect(h.query.state.data).toBe('first work'))
    h.elapse(400)
    h.emit()
    h.flush()
    expect(h.reads).toHaveLength(2)
    expect(h.pendingDelays()).toEqual([libraryRefreshMilliseconds - 400])
    h.emit('resync')
    h.flushCoalesced()
    // The resync's own read pays the debt; its bounded timer is withdrawn.
    expect(h.reads).toHaveLength(3)
    expect(h.timers.size).toBe(0)
    h.reads[2]?.resolve('resynced')
    await vi.waitFor(() => expect(h.query.state.data).toBe('resynced'))
    h.elapse(100)
    h.emit()
    h.flush()
    expect(h.reads).toHaveLength(3)
    expect(h.pendingDelays()).toEqual([libraryRefreshMilliseconds - 100])
  })

  it('reads nothing when bounded Library debt falls due in a hidden tab, and reads on return', async () => {
    const h = collectionHarness(true)
    h.reads[0]?.resolve('initial')
    await vi.waitFor(() => expect(h.query.state.fetchStatus).toBe('idle'))
    h.emit()
    h.flush()
    h.reads[1]?.resolve('first work')
    await vi.waitFor(() => expect(h.query.state.data).toBe('first work'))
    h.elapse(coalesceMilliseconds)
    h.emit()
    h.flush()
    expect(h.pendingDelays()).toEqual([libraryRefreshMilliseconds - coalesceMilliseconds])
    h.hide()
    h.flush()
    // A hidden browser does no catch-up work, even when the bound falls due.
    expect(h.reads).toHaveLength(2)
    expect(h.query.state.isInvalidated).toBe(true)
    h.show()
    h.flush()
    expect(h.reads).toHaveLength(3)
  })

  it('does not discharge an in-flight debt through a manual cache update', async () => {
    const h = collectionHarness(true)
    h.emit()
    h.flush()
    h.client.setQueryData(h.query.queryKey, 'manual')
    h.flush()
    expect(h.reads).toHaveLength(1)
    h.reads[0]?.resolve('server')
    await vi.waitFor(() => expect(h.query.state.data).toBe('server'))
    expect(h.query.state.isInvalidated).toBe(true)
    h.flush()
    expect(h.reads).toHaveLength(2)
  })

  it('leaves failures stale without creating a retry loop', async () => {
    const h = collectionHarness(true)
    h.emit()
    h.flush()
    h.reads[0]?.reject(new Error('unavailable'))
    await vi.waitFor(() => expect(h.query.state.status).toBe('error'))
    expect(h.query.state.isInvalidated).toBe(true)
    h.flush()
    h.flush()
    expect(h.reads).toHaveLength(1)
    h.elapse(libraryRefreshMilliseconds)
    h.emit()
    h.flush()
    expect(h.reads).toHaveLength(2)
  })

  it('holds completed debt while hidden and consumes foreground resync once', async () => {
    const h = collectionHarness(true)
    h.emit()
    h.flush()
    h.hide()
    h.reads[0]?.resolve('before-frame')
    await vi.waitFor(() => expect(h.query.state.fetchStatus).toBe('idle'))
    expect(h.query.state.isInvalidated).toBe(true)
    h.flush()
    expect(h.reads).toHaveLength(1)
    h.show()
    h.flush()
    expect(h.reads).toHaveLength(2)
    h.reads[1]?.resolve('fresh')
    await vi.waitFor(() => expect(h.query.state.data).toBe('fresh'))
    h.flush()
    expect(h.reads).toHaveLength(2)
  })

  it('drops unmounted debt after rollback and fetches stale data on remount', async () => {
    const h = collectionHarness(true)
    h.emit()
    h.flush()
    h.unmount()
    expect(h.reads[0]?.signal.aborted).toBe(true)
    expect(h.query.state.isInvalidated).toBe(true)
    h.reads[0]?.resolve('late')
    await Promise.resolve()
    h.flush()
    expect(h.query.state.data).toBe('old')
    expect(h.reads).toHaveLength(1)
    h.remount()
    expect(h.reads).toHaveLength(2)
  })

  it('never transfers a removed query debt to a replacement with the same key', async () => {
    const h = collectionHarness(true)
    h.emit()
    h.flush()
    h.client.removeQueries({ queryKey: h.query.queryKey, exact: true })
    h.unmount()
    h.remount()
    const replacement = h.client.getQueryCache().find({ queryKey: h.query.queryKey, exact: true })
    expect(replacement).not.toBe(h.query)
    expect(h.reads).toHaveLength(1)
    h.reads[0]?.resolve('late')
    await Promise.resolve()
    h.flush()
    expect(replacement?.state.data).toBe('old')
    expect(h.reads).toHaveLength(1)
  })

  it('cancels tracked debt at teardown and preserves stale state after a late result', async () => {
    const h = collectionHarness(true)
    h.emit()
    h.flush()
    h.stop()
    expect(h.reads[0]?.signal.aborted).toBe(true)
    expect(h.query.state.isInvalidated).toBe(true)
    h.reads[0]?.resolve('late')
    await Promise.resolve()
    h.flush()
    expect(h.query.state.data).toBe('old')
    expect(h.query.state.isInvalidated).toBe(true)
    expect(h.reads).toHaveLength(1)
  })
})

class FakeEventSource {
  readyState = 0
  onopen: (() => void) | null = null
  onerror: (() => void) | null = null
  closed = false
  private readonly handlers = new Map<string, ((event: { data: string }) => void)[]>()
  addEventListener(name: string, handler: (event: { data: string }) => void) {
    this.handlers.set(name, [...(this.handlers.get(name) ?? []), handler])
  }
  close() {
    this.closed = true
    this.readyState = 2
  }
  emit(name: string, data: unknown) {
    for (const handler of this.handlers.get(name) ?? []) handler({ data: JSON.stringify(data) })
  }
}

const openStream = () => {
  const sources: FakeEventSource[] = []
  const timers: (() => void)[] = []
  const stream = createLiveUpdateStream({
    open: () => {
      const source = new FakeEventSource()
      sources.push(source)
      return source as unknown as EventSource
    },
    schedule: (run) => {
      timers.push(run)
      return timers.length
    },
    cancel: () => undefined,
  })
  const received: LiveUpdate[] = []
  stream.subscribe((update) => received.push(update))
  const source = sources[0]
  if (!source) throw new Error('the stream opened no connection')
  return { stream, source, sources, timers, received }
}

describe('live update frames', () => {
  it('refuses anything that is not exactly the three invalidation fields', () => {
    expect(
      readLiveUpdate(JSON.stringify({ sequence: 4, subject: null, topic: 'library' })),
    ).toEqual({ sequence: 4, subject: null, topic: 'library' })
    expect(readLiveUpdate(JSON.stringify({ sequence: 4, subject: CASE, topic: 'case' }))).toEqual({
      sequence: 4,
      subject: CASE,
      topic: 'case',
    })
    // A payload is the failure this refusal exists for: a server that started
    // sending state must be discarded whole, never partially trusted.
    expect(
      readLiveUpdate(JSON.stringify({ sequence: 4, subject: null, topic: 'library', ready: 12 })),
    ).toBeNull()
    expect(readLiveUpdate(JSON.stringify({ sequence: 4, topic: 'library' }))).toBeNull()
    expect(
      readLiveUpdate(JSON.stringify({ sequence: -1, subject: null, topic: 'library' })),
    ).toBeNull()
    expect(
      readLiveUpdate(JSON.stringify({ sequence: 1.5, subject: null, topic: 'library' })),
    ).toBeNull()
    expect(
      readLiveUpdate(JSON.stringify({ sequence: 4, subject: null, topic: 'secrets' })),
    ).toBeNull()
    expect(
      readLiveUpdate(JSON.stringify({ sequence: 4, subject: '../etc', topic: 'case' })),
    ).toBeNull()
    expect(readLiveUpdate(JSON.stringify([1, 2, 3]))).toBeNull()
    expect(readLiveUpdate('not json')).toBeNull()
    expect(readLiveUpdate(`"${'a'.repeat(600)}"`)).toBeNull()
  })

  it('drops a position it has already seen and adopts one a restart hands back', () => {
    const { source, received } = openStream()

    source.emit('resync', { sequence: 0, subject: null, topic: 'resync' })
    source.emit('library', { sequence: 7, subject: null, topic: 'library' })
    // Redelivery after a reconnect must not move a counter twice.
    source.emit('library', { sequence: 7, subject: null, topic: 'library' })
    source.emit('case', { sequence: 6, subject: CASE, topic: 'case' })
    // A restarted server's order begins again. Its resync is adopted rather
    // than compared, so the low positions that follow are not read as stale.
    source.emit('resync', { sequence: 0, subject: null, topic: 'resync' })
    source.emit('library', { sequence: 1, subject: null, topic: 'library' })

    expect(received.map((update) => [update.topic, update.sequence])).toEqual([
      ['resync', 0],
      ['library', 7],
      ['resync', 0],
      ['library', 1],
    ])
  })

  it('reports degraded and retries once only when the browser has given up', () => {
    const { stream, source: first, sources, timers } = openStream()

    expect(stream.hasBeenLive).toBe(false)
    first.onopen?.()
    expect(stream.state).toBe('live')
    expect(stream.hasBeenLive).toBe(true)

    // A break the browser will reconnect on its own: degraded, but no second
    // connection of ours, which would leave two streams on one tab.
    first.readyState = 0
    first.onerror?.()
    expect(stream.state).toBe('degraded')
    expect(timers).toHaveLength(0)
    expect(sources).toHaveLength(1)

    // A refusal the browser will not retry -- an expired session -- is the one
    // case that needs an attempt of our own.
    first.readyState = 2
    first.onerror?.()
    expect(timers).toHaveLength(1)
    timers[0]?.()
    expect(sources).toHaveLength(2)
    expect(first.closed).toBe(true)
  })
})

describe('live invalidation', () => {
  it('releases observers, timers, and a nonsettling preparation read on teardown', async () => {
    const queryClient = new QueryClient()
    let aborted = false
    void queryClient
      .query({
        queryKey: ['review-preparation'],
        queryFn: ({ signal }) =>
          new Promise((_resolve, reject) => {
            signal.addEventListener('abort', () => {
              aborted = true
              reject(new DOMException('aborted', 'AbortError'))
            })
          }),
      })
      .catch(() => undefined)
    let updateStopped = false
    let stateStopped = false
    let activityStopped = false
    let visibilityStopped = false
    const canceled: number[] = []
    let updateListener: ((update: LiveUpdate) => void) | undefined
    const stream = {
      state: 'live' as const,
      subscribe: (listener: (update: LiveUpdate) => void) => {
        updateListener = listener
        return () => {
          updateStopped = true
        }
      },
      observeState: () => () => {
        stateStopped = true
      },
      close: () => undefined,
    }
    const stop = startOwnedLiveInvalidation(stream, queryClient, {
      schedule: () => 7,
      cancel: (handle) => canceled.push(handle),
      observeActivity: () => () => {
        activityStopped = true
      },
      observeVisibility: () => () => {
        visibilityStopped = true
      },
    })

    updateListener?.({ sequence: 1, subject: null, topic: 'activity' })
    stop()
    await vi.waitFor(() => expect(aborted).toBe(true))
    expect({ activityStopped, stateStopped, updateStopped, visibilityStopped }).toEqual({
      activityStopped: true,
      stateStopped: true,
      updateStopped: true,
      visibilityStopped: true,
    })
    expect(canceled).toEqual([7])
  })

  it('invalidates only the queries a topic names, never the whole Library', () => {
    expect(invalidationKeys({ sequence: 1, subject: CASE, topic: 'case' })).toEqual([
      ['review-case', CASE],
    ])
    // The load-bearing absence: one case changing must not rebuild Library.
    expect(invalidationKeys({ sequence: 1, subject: CASE, topic: 'case' })).not.toContainEqual([
      'library',
    ])
    expect(invalidationKeys({ sequence: 1, subject: null, topic: 'preparation' })).toEqual([
      ['review-preparation'],
    ])
    expect(invalidationKeys({ sequence: 1, subject: null, topic: 'library' })).toEqual([
      ['library'],
      ['attention'],
    ])
    expect(invalidationKeys({ sequence: 1, subject: null, topic: 'activity' })).toEqual([
      ['activity'],
    ])
    // Only a resync, which is the server admitting it cannot say what changed,
    // is answered by asking about everything.
    expect(invalidationKeys({ sequence: 1, subject: null, topic: 'resync' })).toHaveLength(5)
  })

  it('collapses a burst of announcements into one refetch of each distinct query', () => {
    const queryClient = new QueryClient()
    const invalidate = vi.spyOn(queryClient, 'invalidateQueries').mockResolvedValue(undefined)
    const timers: (() => void)[] = []
    const listeners: ((update: LiveUpdate) => void)[] = []
    const stream = {
      state: 'live' as const,
      subscribe(listener: (update: LiveUpdate) => void) {
        listeners.push(listener)
        return () => undefined
      },
      observeState: () => () => undefined,
      close: () => undefined,
    }
    const stop = startOwnedLiveInvalidation(stream, queryClient, {
      schedule: (run) => {
        timers.push(run)
        return timers.length
      },
      cancel: () => undefined,
    })

    // Fifty works finishing in one publication is the case that made polling
    // hurt. It must cost two refetches -- the page and the rail -- not fifty.
    for (let index = 0; index < 50; index += 1)
      for (const listener of listeners)
        listener({ sequence: index, subject: null, topic: 'library' })
    expect(invalidate).not.toHaveBeenCalled()
    timers[0]?.()

    expect(invalidate.mock.calls.map(([argument]) => argument?.queryKey)).toEqual([
      ['library'],
      ['attention'],
    ])
    stop()
  })

  it('does no hidden-tab work and takes one snapshot-authoritative foreground resync', () => {
    const queryClient = new QueryClient()
    const invalidate = vi.spyOn(queryClient, 'invalidateQueries').mockResolvedValue(undefined)
    const timers: (() => void)[] = []
    const listeners: ((update: LiveUpdate) => void)[] = []
    let hidden = true
    let visibilityListener: (() => void) | undefined
    const stream = {
      state: 'live' as const,
      subscribe(listener: (update: LiveUpdate) => void) {
        listeners.push(listener)
        return () => undefined
      },
      observeState: () => () => undefined,
      close: () => undefined,
    }
    const stop = startOwnedLiveInvalidation(stream, queryClient, {
      schedule: (run) => {
        timers.push(run)
        return timers.length
      },
      cancel: () => undefined,
      visible: () => !hidden,
      observeVisibility: (listener) => {
        visibilityListener = listener
        return () => undefined
      },
    })

    for (const listener of listeners) listener({ sequence: 4, subject: null, topic: 'library' })
    expect(invalidate).not.toHaveBeenCalled()
    expect(timers).toHaveLength(0)

    hidden = false
    visibilityListener?.()
    visibilityListener?.()
    expect(timers).toHaveLength(1)
    timers[0]?.()
    expect(invalidate.mock.calls.map(([argument]) => argument?.queryKey)).toEqual([
      ['review-preparation'],
      ['library'],
      ['attention'],
      ['activity'],
      ['review-case'],
    ])
    stop()
  })

  it('defers an untouched foreground burst and resyncs once on owner input', () => {
    const queryClient = new QueryClient()
    const invalidate = vi.spyOn(queryClient, 'invalidateQueries').mockResolvedValue(undefined)
    const timers = new Map<number, { delay: number; run: () => void }>()
    const listeners: ((update: LiveUpdate) => void)[] = []
    let nextTimer = 0
    let now = 0
    const stream = {
      state: 'live' as const,
      subscribe(listener: (update: LiveUpdate) => void) {
        listeners.push(listener)
        return () => undefined
      },
      observeState: () => () => undefined,
      close: () => undefined,
    }
    const stop = startOwnedLiveInvalidation(stream, queryClient, {
      schedule: (run, delay) => {
        nextTimer += 1
        timers.set(nextTimer, { delay, run })
        return nextTimer
      },
      cancel: (handle) => void timers.delete(handle),
      now: () => now,
      observeVisibility: () => () => undefined,
    })

    now = ownerIdleMilliseconds
    for (const listener of listeners) listener({ sequence: 1, subject: null, topic: 'library' })
    expect(invalidate).not.toHaveBeenCalled()
    expect(timers).toHaveLength(1)

    document.dispatchEvent(new WheelEvent('wheel'))
    expect(timers).toHaveLength(1)
    const queued = [...timers.entries()][0]
    expect(queued?.[1].delay).toBe(120)
    if (queued) {
      timers.delete(queued[0])
      queued[1].run()
    }
    expect(invalidate.mock.calls.map(([argument]) => argument?.queryKey)).toEqual([
      ['review-preparation'],
      ['library'],
      ['attention'],
      ['activity'],
      ['review-case'],
    ])
    stop()
  })

  it('keeps Library debt after the periodic preparation refresh until owner input', () => {
    const queryClient = new QueryClient()
    const invalidate = vi.spyOn(queryClient, 'invalidateQueries').mockResolvedValue(undefined)
    const timers = new Map<number, () => void>()
    const listeners: ((update: LiveUpdate) => void)[] = []
    let nextTimer = 0
    let now = 0
    let activityListener: (() => void) | undefined
    const stream = {
      state: 'live' as const,
      subscribe(listener: (update: LiveUpdate) => void) {
        listeners.push(listener)
        return () => undefined
      },
      observeState: () => () => undefined,
      close: () => undefined,
    }
    const stop = startOwnedLiveInvalidation(stream, queryClient, {
      schedule: (run) => {
        nextTimer += 1
        timers.set(nextTimer, run)
        return nextTimer
      },
      cancel: (handle) => void timers.delete(handle),
      now: () => now,
      observeActivity: (listener) => {
        activityListener = listener
        return () => undefined
      },
      observeVisibility: () => () => undefined,
    })

    now = ownerIdleMilliseconds
    for (const listener of listeners) listener({ sequence: 1, subject: null, topic: 'library' })
    const periodic = [...timers.entries()][0]
    expect(periodic).toBeDefined()
    if (periodic) {
      timers.delete(periodic[0])
      periodic[1]()
    }
    const preparationFlush = [...timers.entries()][0]
    expect(preparationFlush).toBeDefined()
    if (preparationFlush) {
      timers.delete(preparationFlush[0])
      preparationFlush[1]()
    }
    expect(invalidate.mock.calls.map(([argument]) => argument?.queryKey)).toEqual([
      ['review-preparation'],
    ])

    invalidate.mockClear()
    activityListener?.()
    const resync = [...timers.entries()][0]
    expect(resync).toBeDefined()
    if (resync) {
      timers.delete(resync[0])
      resync[1]()
    }
    expect(invalidate.mock.calls.map(([argument]) => argument?.queryKey)).toEqual([
      ['library'],
      ['attention'],
      ['activity'],
      ['review-case'],
    ])
    stop()
  })

  it.each([
    {
      expected: [['review-case', CASE]],
      topic: 'case' as const,
    },
    {
      expected: [['activity']],
      topic: 'activity' as const,
    },
  ])('keeps idle-visible $topic invalidation immediate', ({ expected, topic }) => {
    const queryClient = new QueryClient()
    const invalidate = vi.spyOn(queryClient, 'invalidateQueries').mockResolvedValue(undefined)
    const timers: (() => void)[] = []
    const listeners: ((update: LiveUpdate) => void)[] = []
    const stream = {
      state: 'live' as const,
      subscribe(listener: (update: LiveUpdate) => void) {
        listeners.push(listener)
        return () => undefined
      },
      observeState: () => () => undefined,
      close: () => undefined,
    }
    const stop = startOwnedLiveInvalidation(stream, queryClient, {
      schedule: (run) => {
        timers.push(run)
        return timers.length
      },
      cancel: () => undefined,
      now: () => ownerIdleMilliseconds,
      observeActivity: () => () => undefined,
      observeVisibility: () => () => undefined,
    })

    for (const listener of listeners)
      listener({ sequence: 1, subject: topic === 'case' ? CASE : null, topic })
    expect(invalidate).not.toHaveBeenCalled()
    expect(timers).toHaveLength(1)
    timers[0]?.()
    expect(invalidate.mock.calls.map(([argument]) => argument?.queryKey)).toEqual(expected)
    stop()
  })
})
