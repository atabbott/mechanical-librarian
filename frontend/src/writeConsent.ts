import type {
  ActivityDestinationView,
  ActivityDisplayView,
  ActivityLapsedConsentView,
  ActivityPermissionReadinessView,
  ActivityWaitingReasonsView,
  DestinationState,
} from './api/http'
import { writeHoldReasons } from './labels'

/**
 * What one `Enable writes` action would cover, read off the authoritative
 * explicit permission projection. Unchecked history supplies no grant offer.
 *
 * Activity states this at length and Library states it compactly, and #639 asks
 * for both. They read the same function so the two surfaces cannot come to
 * differ about which destinations are eligible, how many prepared changes a
 * permission would carry, or whether the action can be taken at all -- a
 * disagreement between two places an owner can act would be worse than either
 * place being absent.
 */
export type WriteConsentState = 'off' | 'enabling' | 'enabled' | 'applying' | 'complete'

export interface WriteConsentScope {
  readonly library: string
  /** Destinations this action would grant now. */
  readonly eligible: readonly ActivityDestinationView[]
  /** Destinations it must leave out, each carrying the reason it is left out. */
  readonly excluded: readonly ActivityDestinationView[]
}

export interface WriteConsentGrant {
  readonly library: string
  readonly destinations: readonly string[]
}

export interface WriteConsentOffer {
  readonly state: WriteConsentState
  readonly scopes: readonly WriteConsentScope[]
  readonly grants: readonly WriteConsentGrant[]
  /** Prepared changes the eligible destinations would carry, and no others. */
  readonly changes: number
  /** Why the action cannot be taken, in the owner's words, or null. */
  readonly blocked: string | null
  /** Whether this state is one an owner can act on right now. */
  readonly actionable: boolean
  readonly reasons: ActivityWaitingReasonsView
  /** Why write permission a library had no longer applies, in the owner's words, or null. */
  readonly lapsed: string | null
}

/** Library names as one phrase: `Books`, `Books and Audiobooks`, `A, B, and C`. */
export const libraryList = (names: readonly string[]) =>
  new Intl.ListFormat('en', { type: 'conjunction' }).format(names)

/**
 * Writes are enabled for a destination only while it holds permission and is
 * not suspended. Consent and capability are independent: a current consent
 * survives a later suspension, and a suspended destination's writes are held
 * whatever its consent says (#1286). A consented destination not yet checked
 * (`not_ready`, nothing to probe it with) is not held: its permission stands
 * and every write is checked before it starts.
 */
export const writesEnabled = (entry: { consented: boolean; state: DestinationState }) =>
  entry.consented && (entry.state === 'allowed' || entry.state === 'not_ready')

/**
 * A library is held when it has destinations and none of them can be written
 * or given permission: each is suspended or unavailable here, consented or
 * not. Activity's review and the writes-off sentence say so in the same words
 * (#1286).
 */
export const libraryWritesHeld = (destinations: readonly { state: DestinationState }[]) =>
  destinations.length > 0 &&
  destinations.every((entry) => entry.state !== 'allowed' && entry.state !== 'not_ready')

/**
 * Beside a library that holds permission, which of the others can be given it
 * and which are held: a library whose every destination is held (an
 * unexpected write, a missing field, an unusable connection) cannot be given
 * permission, so it is said to be held rather than to need permission
 * (#1286). Writes are on for a library holding permission only while one of
 * its consented destinations is still allowed; one whose consented
 * destinations are all suspended is not said to have writes on. With no
 * library writing, writes are off, and a held library beside a kept
 * permission is still named as held.
 */
export const writesOffSentence = (
  data: Pick<ActivityPermissionReadinessView, 'capabilities' | 'configured_libraries'>,
  holding: readonly string[],
  libraryName: (name: string) => string,
) => {
  const destinations = (library: string) =>
    data.capabilities.find((item) => item.library === library)?.destinations ?? []
  const on = holding.filter((library) => destinations(library).some(writesEnabled))
  const others = data.configured_libraries
    .map((item) => item.library)
    .filter((library) => !on.includes(library))
  const held = others.filter((library) => libraryWritesHeld(destinations(library)))
  if (!on.length)
    return holding.length && held.length
      ? `Writes are off; writes to ${libraryList(held.map(libraryName))} are held.`
      : 'Writes are off.'
  if (!others.length) return 'Writes are off.'
  const waiting = others.filter((library) => !held.includes(library))
  return [
    `Writes are on for ${libraryList(on.map(libraryName))}`,
    ...(waiting.length
      ? [
          `${libraryList(waiting.map(libraryName))} ${waiting.length === 1 ? 'needs' : 'need'} permission`,
        ]
      : []),
    ...(held.length ? [`writes to ${libraryList(held.map(libraryName))} are held`] : []),
  ]
    .join('; ')
    .concat('.')
}

const lapseReason = (reason: ActivityLapsedConsentView['reason'], libraries: number) =>
  ({
    destinations_changed:
      'this version of Mechanical Librarian can write a different set of fields',
    library_unresolved: `the active policy does not cover ${libraries === 1 ? 'that library' : 'those libraries'}`,
    policy_changed: 'it was given under a policy that is no longer active',
  })[reason]

/**
 * Each lapse as one sentence per reason. A lapse is never calm: write
 * permission ended without the owner withdrawing it (#1370).
 */
const lapsedSentence = (
  lapses: readonly ActivityLapsedConsentView[],
  libraryName: (name: string) => string,
) => {
  const reasons = [...new Set(lapses.map((item) => item.reason))].sort()
  return reasons.length
    ? reasons
        .map((reason) => {
          const names = [
            ...new Set(
              lapses
                .filter((item) => item.reason === reason)
                .map((item) => libraryName(item.library)),
            ),
          ]
          return `Write permission for ${libraryList(names)} lapsed because ${lapseReason(reason, names.length)}.`
        })
        .join(' ')
    : null
}

/**
 * A destination is excluded for exactly one reason, and the reason is per
 * destination rather than per library: a Cover behind its own gate is named
 * with its reason and never blocks the metadata beside it. After a lapse, a
 * destination with nothing prepared says permission can be given again once
 * there is (#1370, #1286).
 */
export const destinationReason = (state: Exclude<DestinationState, 'allowed'>, lapsed = false) =>
  state === 'not_ready'
    ? lapsed
      ? 'nothing is prepared for it yet; you can give permission again once a change is prepared'
      : 'it is not ready yet'
    : writeHoldReasons[state]

export const readWriteConsent = (
  data: Pick<ActivityDisplayView, 'lapsed_consents' | 'waiting'> &
    Pick<ActivityPermissionReadinessView, 'backup_ready' | 'capabilities' | 'configured_libraries'>,
  options: {
    readonly enabling?: boolean
    readonly connectionUnavailable?: boolean
    /** The owner's name for a library slug, which a lapse is stated in. */
    readonly libraryName?: (name: string) => string
  } = {},
): WriteConsentOffer => {
  const reasons = data.waiting
  const capability = (library: string) => data.capabilities.find((item) => item.library === library)
  const scopes: WriteConsentScope[] = []
  for (const item of data.backup_ready === null ? [] : data.configured_libraries) {
    const destinations = capability(item.library)?.destinations ?? []
    const eligible = destinations.filter((entry) => entry.state === 'allowed' && !entry.consented)
    if (!eligible.length) continue
    scopes.push({
      library: item.library,
      eligible,
      excluded: destinations.filter((entry) => entry.state !== 'allowed' && !entry.consented),
    })
  }
  const grants = scopes.map((scope) => ({
    library: scope.library,
    destinations: scope.eligible.map((entry) => entry.destination),
  }))
  const changes = scopes.reduce(
    (total, scope) => total + scope.eligible.reduce((sum, entry) => sum + entry.prepared, 0),
    0,
  )
  // Read from the queue rather than from the last command's result: a state
  // that came from a response would keep claiming success after the queue had
  // moved on, and the queue is the only thing that knows whether a prepared
  // change is still waiting on permission. An empty queue is complete only
  // while some library can still write or none ever lapsed (#1370): a
  // permission whose destinations are all suspended writes nothing, so a lapse
  // beside it reads as off, not as writes enabled (#1286).
  const lapsed = lapsedSentence(data.lapsed_consents, options.libraryName ?? ((name) => name))
  const held = data.capabilities.some((item) => item.destinations.some(writesEnabled))
  const state: WriteConsentState = options.enabling
    ? 'enabling'
    : reasons.applying > 0
      ? 'applying'
      : reasons.awaiting_permission > 0
        ? 'off'
        : reasons.scheduled > 0
          ? 'enabled'
          : lapsed && !held
            ? 'off'
            : 'complete'
  const excluded = scopes[0]?.excluded[0]
  // Unchecked readiness blocks nothing yet: each surface's own one sentence
  // sends the owner to the review that checks it (#1286).
  const blocked =
    data.backup_ready === null
      ? null
      : !data.backup_ready
        ? 'Backup readiness has not been confirmed, so writes cannot be enabled yet.'
        : grants.length === 0
          ? scopes.length === 0
            ? 'No selected destination is eligible for writes right now.'
            : `No destination is eligible yet: ${excluded && excluded.state !== 'allowed' ? destinationReason(excluded.state, lapsed !== null) : 'nothing is ready yet'}.`
          : null
  return {
    state,
    scopes,
    grants,
    changes,
    blocked,
    actionable:
      state !== 'complete' &&
      !options.connectionUnavailable &&
      (data.configured_libraries.length > 0 || reasons.scheduled > 0),
    reasons,
    lapsed,
  }
}

/**
 * What a permission draft was reviewed against: the configured libraries, each
 * library's capability and per-destination state and consent, and the active
 * policy. A draft survives anything that leaves this unchanged and is discarded
 * when it differs (#1279). Prepared counts, backup readiness, the example and
 * the waiting reasons are excluded: they move with ordinary preparation, and
 * confirmation re-checks backup and capability on the server regardless.
 */
export const consentBasis = (
  view: Pick<ActivityPermissionReadinessView, 'capabilities' | 'configured_libraries'>,
  policyId: string,
) => {
  // Each entry is serialised before sorting, so the order a response lists
  // libraries or destinations in never reads as a change.
  const sorted = (items: readonly unknown[]) => items.map((item) => JSON.stringify(item)).sort()
  return JSON.stringify([
    sorted(view.configured_libraries.map((item) => [item.connection, item.library])),
    sorted(
      view.capabilities.map((item) => [
        item.connection,
        item.library,
        item.state,
        sorted(item.destinations.map((entry) => [entry.destination, entry.state, entry.consented])),
      ]),
    ),
    policyId,
  ])
}
