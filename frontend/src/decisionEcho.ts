import { QueryObserver } from '@tanstack/react-query'
import type { Query, QueryClient } from '@tanstack/react-query'

import { invalidationKeys } from './liveInvalidation'
import type { LiveUpdate } from './liveUpdates'

/**
 * Recognise the stream frames this tab's own decision caused, and settle them
 * without a whole-Library read on the decision path.
 *
 * #635 removed the whole-Library read from recording a decision. #641 then made
 * every decision announce the case, its Library row and the write queue, and a
 * tab refetches on any announcement -- so the read came back, on a coalescing
 * delay long enough to land after the guard had counted.
 *
 * The correlation is exact rather than approximate. `record_decision` returns
 * the positions it published at, and only the tab that recorded the decision
 * can hold them, so a frame is recognised as this tab's echo by identity and
 * never by resemblance. That is what makes this default-open: a frame whose
 * position is not one this tab caused reaches it untouched, including every
 * cause nobody enumerated. Guessing wrong costs a redundant refetch, never a
 * panel that stops updating.
 *
 * The echo is published before the response that names it is written, so it can
 * and does arrive first. That ordering is not raced against: while a decision
 * on a case is in flight, a Library frame naming that case is *held* rather
 * than acted on or discarded, because it is the one frame whose explanation is
 * still in transit. When the decision settles, its token either claims the held
 * frame or does not, and anything unclaimed is invalidated then. A held frame
 * is always eventually acted on unless it is proven to be the echo, and the
 * hold is bounded by the request's own lifetime rather than by a window.
 *
 * Two properties of the server's coalescing are load-bearing, and both were
 * measured against `LiveUpdateChannel` rather than reasoned about. A
 * subscription holds one pending entry per `(topic, subject)` and keeps the
 * highest position it replaces, so a delivered frame stands for everything
 * collapsed into it. When a subscription outgrows its bound it collapses every
 * entry to topic-wide, emitting `library` with **no subject** at the maximum
 * position -- which can be this tab's own. Dropping that frame would discard
 * every other work collapsed into it, so a subjectless `library` frame is never
 * recognised as an echo. A decision's announcement always names its case
 * (`review_application.py`), so requiring the subject costs nothing and closes
 * that whole class.
 *
 * The residue is stated rather than hidden: a cutover for the *same* case,
 * published before the decision and collapsed with it into one delivered frame,
 * is indistinguishable from the echo once the two share a position. It needs
 * the owner to be recording in a case during the window between its admission
 * and its projection, and it costs that one row one refresh -- the cutover's
 * `case` frame is a different key and still arrives, so the canvas the owner is
 * looking at is unaffected, and the row self-heals on the next `library` frame.
 * Only the server can tell those two apart; the client cannot.
 */

/**
 * More than a burst of decisions can leave outstanding at once, and small enough
 * that none of these can ever become a log. Positions are consumed on
 * recognition and everything is cleared when the case is left, so these are
 * backstops rather than working limits.
 */
export const outstandingEchoLimit = 64

/** What the invalidation bridge should do with one frame. */
export type EchoDisposition =
  /** Not this tab's echo. Invalidate as usual. */
  | 'act'
  /** This tab's echo. Settled when the owner leaves the case. */
  | 'defer'
  /** Might be this tab's echo; the decision that would say so is still in flight. */
  | 'hold'

interface ConfirmationRead {
  readonly query: Query
  tail: Promise<void>
  outstanding: number
  readonly release: () => void
  deferred: ((succeeded: boolean) => void) | null
}

interface EchoState {
  /** One entry per decision in flight, by case. A burst leaves several. */
  readonly confirmations?: Map<string, ConfirmationRead>
  readonly inFlight: readonly string[]
  /** Frames withheld until the decision that could explain them settles. */
  readonly held: readonly LiveUpdate[]
  /** Positions this tab's decisions were told they caused, not yet matched. */
  readonly caused: readonly number[]
  /** Keys whose invalidation was recognised as an echo and not yet settled. */
  readonly deferred: readonly (readonly unknown[])[]
}

const empty: EchoState = { inFlight: [], held: [], caused: [], deferred: [] }
const states = new WeakMap<QueryClient, EchoState>()

const read = (queryClient: QueryClient): EchoState => states.get(queryClient) ?? empty
const bounded = <T>(values: readonly T[]): readonly T[] =>
  values.slice(Math.max(values.length - outstandingEchoLimit, 0))
const serialised = (key: readonly unknown[]) => JSON.stringify(key)
const withDeferred = (
  deferred: readonly (readonly unknown[])[],
  added: readonly (readonly unknown[])[],
) => {
  const taken = new Set(added.map(serialised))
  return [...deferred.filter((key) => !taken.has(serialised(key))), ...added]
}

/** A decision has been sent for this case; its token has not come back yet. */
export const beginDecisionEcho = (queryClient: QueryClient, reviewCaseId: string): void => {
  const state = read(queryClient)
  states.set(queryClient, { ...state, inFlight: bounded([...state.inFlight, reviewCaseId]) })
}

/**
 * The decision settled. Its token claims whatever it caused; every frame held
 * on its account that the token does not claim is somebody else's news and is
 * invalidated now.
 */
export const endDecisionEcho = (
  queryClient: QueryClient,
  reviewCaseId: string,
  sequences: readonly number[],
): void => {
  const state = read(queryClient)
  const outstanding = [...state.inFlight]
  const at = outstanding.indexOf(reviewCaseId)
  if (at >= 0) outstanding.splice(at, 1)
  const caused = bounded([...state.caused, ...sequences.filter(Number.isSafeInteger)])
  const stillHeld: LiveUpdate[] = []
  const release: LiveUpdate[] = []
  let deferred = state.deferred
  const remaining = new Set(caused)
  for (const update of state.held) {
    if (remaining.has(update.sequence)) {
      remaining.delete(update.sequence)
      deferred = withDeferred(deferred, invalidationKeys(update))
    } else if (outstanding.includes(update.subject ?? '')) stillHeld.push(update)
    else release.push(update)
  }
  states.set(queryClient, {
    ...state,
    inFlight: outstanding,
    held: stillHeld,
    caused: [...remaining],
    deferred,
  })
  for (const update of release)
    for (const queryKey of invalidationKeys(update))
      void queryClient.invalidateQueries({ queryKey })
}

/** What the bridge should do with this frame. */
export const classifyDecisionEcho = (
  queryClient: QueryClient,
  update: LiveUpdate,
): EchoDisposition => {
  if (update.topic !== 'library' || update.subject === null) return 'act'
  const state = read(queryClient)
  if (state.caused.includes(update.sequence)) {
    states.set(queryClient, {
      ...state,
      caused: state.caused.filter((sequence) => sequence !== update.sequence),
      deferred: withDeferred(state.deferred, invalidationKeys(update)),
    })
    return 'defer'
  }
  if (!state.inFlight.includes(update.subject)) return 'act'
  states.set(queryClient, { ...state, held: bounded([...state.held, update]) })
  return 'hold'
}

/**
 * A refetch that happened for another reason settles the same debt: the read
 * this tab owed itself has just been taken.
 */
export const forgetSettledKeys = (
  queryClient: QueryClient,
  settled: readonly (readonly unknown[])[],
): void => {
  const state = read(queryClient)
  if (state.deferred.length === 0) return
  const taken = new Set(settled.map(serialised))
  states.set(queryClient, {
    ...state,
    deferred: state.deferred.filter((key) => !taken.has(serialised(key))),
  })
}

/**
 * Pay what the recognised echoes deferred, once, when the owner leaves the case.
 *
 * This is the half that keeps #641's promise for the tab that recorded: the row
 * is correct when the owner returns to it, with no reload and no poll. Nothing
 * is owed when nothing was recognised, so leaving a case costs nothing on its
 * own. Anything still held is released rather than dropped -- leaving is not
 * evidence that a frame was this tab's echo.
 */
export const settleDecisionEcho = (queryClient: QueryClient): void => {
  const state = read(queryClient)
  states.set(queryClient, { ...state, ...empty })
  const keys = [...state.deferred]
  for (const update of state.held) keys.push(...invalidationKeys(update))
  for (const queryKey of new Map(keys.map((key) => [serialised(key), key])).values())
    void queryClient.invalidateQueries({ queryKey })
}

/** Serialize post-acknowledgement reads without borrowing a pre-decision fetch. */
export const confirmDecisionCase = <T>(
  queryClient: QueryClient,
  caseId: string,
  refresh: () => Promise<T>,
): Promise<T> => {
  const query = queryClient.getQueryCache().find({ queryKey: ['review-case', caseId], exact: true })
  if (!query) return Promise.reject(new Error('Decision case query is unavailable'))
  const state = read(queryClient)
  const confirmations = state.confirmations ?? new Map<string, ConfirmationRead>()
  let entry = confirmations.get(caseId)
  if (entry && entry.query !== query)
    return Promise.reject(new Error('Decision case query was replaced'))
  if (!entry) {
    const observer = new QueryObserver(queryClient, {
      ...query.options,
      queryKey: query.queryKey,
      enabled: false,
    })
    entry = {
      query,
      tail: Promise.resolve(),
      outstanding: 0,
      release: observer.subscribe(() => undefined),
      deferred: null,
    }
    confirmations.set(caseId, entry)
    states.set(queryClient, { ...state, confirmations })
  }
  const owned = entry
  owned.outstanding += 1
  const result = owned.tail.then(async () => {
    const current = () => queryClient.getQueryCache().get(query.queryHash) === query
    if (!current()) throw new Error('Decision case query was removed')
    await queryClient.cancelQueries(
      { queryKey: query.queryKey, exact: true, predicate: (cached) => cached === query },
      { silent: true },
    )
    if (!current()) throw new Error('Decision case query was removed')
    const priorUpdates = query.state.dataUpdateCount
    const value = await refresh()
    // Revert cancellation can resolve old data. Only a new native cache
    // publication on this same query establishes post-acknowledgement truth.
    if (!current() || query.state.dataUpdateCount <= priorUpdates)
      throw new Error('Decision case refresh did not complete')
    return value
  })
  owned.tail = result.then(
    () => undefined,
    () => undefined,
  )
  const finish = (succeeded: boolean) => {
    owned.outstanding -= 1
    if (owned.outstanding !== 0) return
    confirmations.delete(caseId)
    owned.release()
    if (queryClient.getQueryCache().get(query.queryHash) === query && owned.deferred) {
      query.invalidate()
      owned.deferred(succeeded)
    }
  }
  return result.then(
    (value) => {
      finish(true)
      return value
    },
    (error: unknown) => {
      finish(false)
      throw error
    },
  )
}

/** Hold one background obligation while an exact case confirms owner choices. */
export const deferDecisionCaseRefresh = (
  queryClient: QueryClient,
  query: Query,
  refresh: (succeeded: boolean) => void,
): boolean => {
  const entry = [...(read(queryClient).confirmations?.values() ?? [])].find(
    (confirmation) => confirmation.query === query,
  )
  if (!entry) return false
  query.invalidate()
  entry.deferred = refresh
  return true
}
