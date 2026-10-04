import type {
  ActivityDestinationView,
  ActivityDisplayView,
  ActivityWaitingReasonsView,
} from './api/http'

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
}

/**
 * A destination is excluded for exactly one reason, and the reason is per
 * destination rather than per library: a Cover behind its own gate is named
 * with its reason and never blocks the metadata beside it.
 */
export const destinationReason = (state: string) =>
  ({
    suspended: 'suspended after a failed write',
    connection_unavailable: 'the library connection needs checking',
    insufficient_permission: 'the connection lacks permission for it',
    field_unavailable: 'the library does not expose it',
    not_ready: 'it is not ready yet',
  })[state] ?? 'it is not ready yet'

export const readWriteConsent = (
  data: ActivityDisplayView,
  options: { readonly enabling?: boolean; readonly connectionUnavailable?: boolean } = {},
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
  // change is still waiting on permission.
  const state: WriteConsentState = options.enabling
    ? 'enabling'
    : reasons.applying > 0
      ? 'applying'
      : reasons.awaiting_permission > 0
        ? 'off'
        : reasons.scheduled > 0
          ? 'enabled'
          : 'complete'
  const excluded = scopes[0]?.excluded[0]
  const blocked =
    data.backup_ready === null
      ? 'Open permission review to check current readiness.'
      : !data.backup_ready
        ? 'Backup readiness has not been confirmed, so writes cannot be enabled yet.'
        : grants.length === 0
          ? scopes.length === 0
            ? 'No selected destination is eligible for writes right now.'
            : `No destination is eligible yet: ${excluded ? destinationReason(excluded.state) : 'nothing is ready yet'}.`
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
  }
}
