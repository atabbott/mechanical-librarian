import { QueryClient } from '@tanstack/react-query'
import { describe, expect, it, vi } from 'vitest'

import {
  invalidationKeys,
  ownerIdleMilliseconds,
  startLiveInvalidation,
} from '../src/liveInvalidation'
import { createLiveUpdateStream, readLiveUpdate } from '../src/liveUpdates'
import type { LiveUpdate } from '../src/liveUpdates'

const CASE = `review_case_${'a'.repeat(64)}`

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
      .fetchQuery({
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
    const stop = startLiveInvalidation(stream, queryClient, {
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
    const stop = startLiveInvalidation(stream, queryClient, {
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
    const stop = startLiveInvalidation(stream, queryClient, {
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
    const stop = startLiveInvalidation(stream, queryClient, {
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
    const stop = startLiveInvalidation(stream, queryClient, {
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
    const stop = startLiveInvalidation(stream, queryClient, {
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
