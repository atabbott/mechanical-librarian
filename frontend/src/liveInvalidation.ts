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
}

export const startLiveInvalidation = (
  stream: LiveUpdateStream,
  queryClient: QueryClient,
  options: LiveInvalidationOptions = {},
): (() => void) => {
  const schedule =
    options.schedule ?? ((run, delay) => globalThis.setTimeout(run, delay) as unknown as number)
  const cancel = options.cancel ?? ((handle) => globalThis.clearTimeout(handle))
  const pending = new Map<string, readonly unknown[]>()
  let handle: number | null = null

  const flush = () => {
    handle = null
    const keys = [...pending.values()]
    pending.clear()
    // A read taken now is a read this tab no longer owes itself for an echo it
    // recognised earlier, so the debt is discharged rather than repeated.
    forgetSettledKeys(queryClient, keys)
    for (const queryKey of keys) void queryClient.invalidateQueries({ queryKey })
  }

  const unsubscribe = stream.subscribe((update) => {
    // The one frame this tab does not act on: the echo of its own decision,
    // recognised by the position the server told it that decision caused. It is
    // settled when the owner leaves the case rather than discarded, so the row
    // behind them is still correct when they come back to it. Everything else,
    // enumerated or not, invalidates exactly as it did.
    if (classifyDecisionEcho(queryClient, update) !== 'act') return
    for (const key of invalidationKeys(update)) pending.set(JSON.stringify(key), key)
    handle ??= schedule(flush, coalesceMilliseconds)
  })

  return () => {
    unsubscribe()
    if (handle !== null) cancel(handle)
    handle = null
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
