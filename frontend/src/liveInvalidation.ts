import { useEffect, useSyncExternalStore } from 'react'

import type { QueryClient } from '@tanstack/react-query'
import { classifyDecisionEcho, forgetSettledKeys } from './decisionEcho'
import type { LiveStreamState, LiveUpdate, LiveUpdateStream } from './liveUpdates'

/**
 * Turn one stream of invalidations into refetches of the queries that hold the
 * authoritative answer, and nothing else.
 *
 * The whole of the live experience is here: a completed work appears because
 * its Library page was refetched, counts move because the snapshot was
 * refetched, and the write queue changes because Activity was refetched. No
 * cache is ever written from a frame, so no surface can show a value the server
 * would not have given it (#641).
 */

/**
 * Long enough that fifty works finishing in a burst cost a handful of refetches
 * instead of fifty, short enough that one work finishing still reads as
 * immediate. The bar this serves is "no visible cadence", which a multi-second
 * batch would fail exactly as the 1.5-second poll it replaces did.
 */
export const coalesceMilliseconds = 120

/**
 * An untouched foreground tab still shows truthful preparation progress, but
 * it must not rebuild Library and attention on every committed work. Twenty
 * preparation snapshots fit inside the fixed 42-minute responsiveness probe;
 * any owner input pays the deferred debt immediately with one full resync.
 */
export const ownerIdleMilliseconds = 10_000
export const idlePreparationRefreshMilliseconds = (42 * 60 * 1_000) / 20
export const preparationInvalidationDebtKey = ['review-preparation-invalidation-debt'] as const

const caseKey = (subject: string | null) =>
  subject === null ? ['review-case'] : ['review-case', subject]

export const invalidationKeys = (update: LiveUpdate): readonly (readonly unknown[])[] => {
  switch (update.topic) {
    case 'preparation':
      return [['review-preparation']]
    case 'library':
      // The bounded page the owner is looking at, and the attention rail beside
      // it. Never a whole-Library rebuild: these refetch the same cursor and
      // limit the surface already asked for.
      return [['library'], ['attention']]
    case 'case':
      return [caseKey(update.subject)]
    case 'activity':
      return [['activity']]
    default:
      return [['review-preparation'], ['library'], ['attention'], ['activity'], ['review-case']]
  }
}

export interface LiveInvalidationOptions {
  readonly schedule?: (run: () => void, delay: number) => number
  readonly cancel?: (handle: number) => void
  /** Test seam for the browser visibility boundary. */
  readonly visible?: () => boolean
  /** Test seam for the browser visibility listener. */
  readonly observeVisibility?: (listener: () => void) => () => void
  /** Test seam for purposeful owner input. */
  readonly observeActivity?: (listener: () => void) => () => void
  /** Test seam for the idle boundary. */
  readonly now?: () => number
}

export const startLiveInvalidation = (
  stream: LiveUpdateStream,
  queryClient: QueryClient,
  options: LiveInvalidationOptions = {},
): (() => void) => {
  const schedule =
    options.schedule ?? ((run, delay) => globalThis.setTimeout(run, delay) as unknown as number)
  const cancel = options.cancel ?? ((handle) => globalThis.clearTimeout(handle))
  const visible =
    options.visible ??
    (() => typeof document === 'undefined' || document.visibilityState !== 'hidden')
  const observeVisibility =
    options.observeVisibility ??
    ((listener) => {
      if (typeof document === 'undefined') return () => undefined
      document.addEventListener('visibilitychange', listener)
      return () => document.removeEventListener('visibilitychange', listener)
    })
  const observeActivity =
    options.observeActivity ??
    ((listener) => {
      if (typeof document === 'undefined') return () => undefined
      const events = ['pointerdown', 'keydown', 'click', 'wheel'] as const
      for (const event of events) document.addEventListener(event, listener)
      return () => {
        for (const event of events) document.removeEventListener(event, listener)
      }
    })
  const now = options.now ?? Date.now
  const pending = new Map<string, readonly unknown[]>()
  let handle: number | null = null
  let idleHandle: number | null = null
  let deferredWhileIdle = false
  let lastActivityAt = now()
  let lastIdleRefreshAt = lastActivityAt
  let wasVisible = visible()
  let confirmationBeganLive =
    queryClient.getQueryState(['review-preparation'])?.fetchStatus === 'fetching' &&
    stream.state === 'live'

  const setPreparationDebt = (debt: boolean) =>
    queryClient.setQueryData(preparationInvalidationDebtKey, debt)
  if (queryClient.getQueryData(preparationInvalidationDebtKey) === undefined)
    setPreparationDebt(false)

  const observePreparation = queryClient.getQueryCache().subscribe((event) => {
    if (event.query.queryHash !== '["review-preparation"]' || event.type !== 'updated') return
    if (event.query.state.fetchStatus === 'fetching') {
      confirmationBeganLive = stream.state === 'live'
      return
    }
    if (event.query.state.fetchStatus !== 'idle') return
    if (
      event.action.type === 'success' &&
      event.action.manual !== true &&
      confirmationBeganLive &&
      stream.state === 'live'
    )
      setPreparationDebt(false)
    confirmationBeganLive = false
  })

  const queueResync = (includePreparation = true) => {
    for (const key of invalidationKeys({ sequence: 0, subject: null, topic: 'resync' })) {
      if (!includePreparation && key[0] === 'review-preparation') continue
      pending.set(JSON.stringify(key), key)
    }
    handle ??= schedule(flush, coalesceMilliseconds)
  }

  function flush() {
    handle = null
    // A hidden browser does no catch-up work.  Its next foreground transition
    // invalidates snapshot keys once, so frames cannot fabricate state or make
    // an old counter look current.
    if (!visible()) return
    const keys = [...pending.values()]
    pending.clear()
    // A read taken now is a read this tab no longer owes itself for an echo it
    // recognised earlier, so the debt is discharged rather than repeated.
    forgetSettledKeys(queryClient, keys)
    for (const queryKey of keys) {
      if (
        queryKey[0] === 'review-preparation' &&
        stream.hasBeenLive === true &&
        stream.state !== 'live'
      ) {
        setPreparationDebt(true)
        continue
      }
      void queryClient.invalidateQueries({ queryKey })
    }
  }

  const flushIdlePreparation = () => {
    idleHandle = null
    if (!visible() || !deferredWhileIdle) return
    if (now() - lastActivityAt < ownerIdleMilliseconds) {
      queueResync()
      deferredWhileIdle = false
      return
    }
    lastIdleRefreshAt = now()
    const key = ['review-preparation'] as const
    pending.set(JSON.stringify(key), key)
    handle ??= schedule(flush, coalesceMilliseconds)
  }

  const deferIdleUpdate = () => {
    deferredWhileIdle = true
    if (idleHandle !== null) return
    idleHandle = schedule(
      flushIdlePreparation,
      Math.max(0, lastIdleRefreshAt + idlePreparationRefreshMilliseconds - now()),
    )
  }

  const unsubscribe = stream.subscribe((update) => {
    if (!visible()) return
    // The one frame this tab does not act on: the echo of its own decision,
    // recognised by the position the server told it that decision caused. It is
    // settled when the owner leaves the case rather than discarded, so the row
    // behind them is still correct when they come back to it. Everything else,
    // enumerated or not, invalidates exactly as it did.
    if (classifyDecisionEcho(queryClient, update) !== 'act') return
    if (
      (update.topic === 'preparation' || update.topic === 'library') &&
      now() - lastActivityAt >= ownerIdleMilliseconds
    ) {
      deferIdleUpdate()
      return
    }
    for (const key of invalidationKeys(update)) pending.set(JSON.stringify(key), key)
    handle ??= schedule(flush, coalesceMilliseconds)
  })
  const handleStreamState = (state: LiveStreamState) => {
    if (state === 'degraded' && stream.hasBeenLive === true) {
      confirmationBeganLive = false
      pending.delete(JSON.stringify(['review-preparation']))
      setPreparationDebt(true)
      void queryClient.cancelQueries(
        { queryKey: ['review-preparation'], exact: true },
        { silent: true },
      )
      return
    }
    if (state === 'live') {
      const degradedReadPending =
        queryClient.getQueryState(['review-preparation'])?.fetchStatus === 'fetching' &&
        !confirmationBeganLive
      if (degradedReadPending) {
        setPreparationDebt(true)
        void queryClient
          .cancelQueries({ queryKey: ['review-preparation'], exact: true }, { silent: true })
          .then(() => {
            if (
              stream.state === 'live' &&
              queryClient.getQueryData(preparationInvalidationDebtKey) === true
            )
              void queryClient.invalidateQueries({
                queryKey: ['review-preparation'],
                exact: true,
              })
          })
        return
      }
      if (queryClient.getQueryData(preparationInvalidationDebtKey) === true)
        void queryClient.invalidateQueries({ queryKey: ['review-preparation'], exact: true })
    }
  }
  const stopObservingState = stream.observeState(handleStreamState)
  handleStreamState(stream.state)
  const stopObservingActivity = observeActivity(() => {
    const owedResync = deferredWhileIdle || idleHandle !== null
    const preparationAlreadyRefreshed = deferredWhileIdle && idleHandle === null
    lastActivityAt = now()
    lastIdleRefreshAt = lastActivityAt
    if (!owedResync) return
    deferredWhileIdle = false
    if (idleHandle !== null) cancel(idleHandle)
    idleHandle = null
    queueResync(!preparationAlreadyRefreshed)
  })
  const stopObservingVisibility = observeVisibility(() => {
    const isVisible = visible()
    if (!isVisible) {
      wasVisible = false
      return
    }
    if (wasVisible) return
    wasVisible = true
    lastActivityAt = now()
    lastIdleRefreshAt = lastActivityAt
    deferredWhileIdle = false
    if (idleHandle !== null) cancel(idleHandle)
    idleHandle = null
    // HTTP remains the authority.  A foreground tab deliberately does not
    // replay individual frames it missed; one coalesced snapshot read restores
    // every mounted surface without creating a backwards transition.
    queueResync()
  })

  return () => {
    unsubscribe()
    stopObservingState()
    observePreparation()
    stopObservingActivity()
    stopObservingVisibility()
    void queryClient.cancelQueries(
      { queryKey: ['review-preparation'], exact: true },
      { silent: true },
    )
    if (handle !== null) cancel(handle)
    if (idleHandle !== null) cancel(idleHandle)
    handle = null
    idleHandle = null
    confirmationBeganLive = false
  }
}

/** Subscribe a component to the stream's health without re-rendering on frames. */
export const useLiveStreamState = (stream: LiveUpdateStream | undefined): LiveStreamState =>
  useSyncExternalStore(
    (notify) => (stream ? stream.observeState(() => notify()) : () => undefined),
    () => stream?.state ?? 'degraded',
    () => 'degraded',
  )

/**
 * Mount the bridge for the life of the tab.
 *
 * It renders nothing: a live update is not a thing on the page, it is a reason
 * to ask the server again.
 */
export const useLiveInvalidation = (
  stream: LiveUpdateStream | undefined,
  queryClient: QueryClient,
): void => {
  useEffect(() => {
    if (!stream) return
    return startLiveInvalidation(stream, queryClient)
  }, [queryClient, stream])
}
