import { Link } from '@tanstack/react-router'
import type {
  PreparationView,
  ProviderRecoveryActivityView,
  ProviderRecoveryDispatchReason,
  ProviderRecoveryFailureReason,
} from '../api/http'
import { formatWhen } from '../dates'
// The Activity status panel's summary and its provider recovery list.
const recoveryFailureLabel: Record<ProviderRecoveryFailureReason, string> = {
  authentication: 'Provider credentials need attention',
  authorization: 'Provider access was denied',
  configuration: 'Provider configuration needs attention',
  rate_limited: 'Provider rate limit',
  burst_throttled: 'Provider burst limit',
  quota_exhausted: 'Provider quota exhausted',
  transient: 'Provider temporarily unavailable',
}

const recoveryDispatchLabel: Record<ProviderRecoveryDispatchReason, string> = {
  missing_corroboration: 'needs independent evidence',
  zero_provider_evidence: 'no provider evidence yet',
  settleable_disagreement: 'evidence disagreement',
  blanket_scan: 'general recovery scan',
}

// What most needs the owner, said once: the heading and paragraph name the
// first condition that applies, in this order.
export function ActivityStatusCopy({
  connection,
  recoveryCount,
  dormantRecovery,
  ownerAction,
  filtered,
  throttled,
  throttleRemedy,
  paused,
  writesHeld,
  lapse,
  cardLapse,
}: {
  connection: NonNullable<PreparationView['connections']>[number] | undefined
  recoveryCount: number
  dormantRecovery: number
  /** Changes stopped until the owner acts on their ledger entry (#1430). */
  ownerAction: number
  /** A search or event-type filter can hide that entry as the range can (#1542). */
  filtered: boolean
  throttled: boolean
  throttleRemedy: boolean
  paused: boolean
  writesHeld: boolean
  lapse: string | null
  cardLapse: boolean
}) {
  return (
    <>
      <h2 id="activity-status-title">
        {connection?.state === 'checking'
          ? 'Checking library connection'
          : connection?.state === 'paused'
            ? 'Library connection is paused'
            : recoveryCount
              ? 'Recovery needs attention'
              : ownerAction
                ? ownerAction === 1
                  ? 'A stopped change needs you'
                  : `${String(ownerAction)} stopped changes need you`
                : throttleRemedy
                  ? 'A metadata source needs attention'
                  : throttled
                    ? 'A metadata source is limiting lookups'
                    : paused
                      ? 'New work is paused'
                      : !writesHeld
                        ? 'Writes are off'
                        : lapse
                          ? 'Some writes are off'
                          : 'No intervention needed'}
      </h2>
      <p>
        {connection?.state === 'checking'
          ? 'Preparation remains safely paused while the authoritative connection check settles.'
          : connection?.state === 'paused'
            ? `${String(connection.affected)} preparation ${connection.affected === 1 ? 'work remains' : 'works remain'} safely paused for an owner connection check.`
            : dormantRecovery
              ? `${String(dormantRecovery)} provider ${dormantRecovery === 1 ? 'scope needs' : 'scopes need'} owner correction before a future check can become eligible.`
              : recoveryCount
                ? 'Affected work remains held until its current state can be reconciled safely.'
                : ownerAction
                  ? ownerAction === 1
                    ? `Its “Prepared change closed” entry in the ledger says why it stopped. Nothing retries it on its own: follow that advice or choose the field again. If it isn’t listed, ${filtered ? 'widen the range or clear filters' : 'widen the range'}.`
                    : `Each “Prepared change closed” entry in the ledger says why it stopped. Nothing retries them on their own: follow that advice or choose the field again. If one isn’t listed, ${filtered ? 'widen the range or clear filters' : 'widen the range'}.`
                  : throttled
                    ? `${throttleRemedy ? 'A source needs an API key added or checked; until then its lookups wait.' : 'Some lookups wait until the source allows them again; nothing is lost.'} Settings shows each source.`
                    : paused
                      ? 'No new work will be claimed. Work already claimed may finish its safe checkpoint; resume new work when you are ready for planned work to continue.'
                      : (lapse ??
                        (writesHeld
                          ? 'Guarded work continues automatically. Recovery controls appear only when your decision is required.'
                          : cardLapse
                            ? 'Nothing is written to your libraries until you give permission again.'
                            : 'No library has write permission, so nothing is written to your libraries until you give it.'))}
      </p>
      {throttled && !connection && !recoveryCount && !ownerAction ? (
        <Link to="/settings" search={{ group: 'providers' }}>
          Review metadata sources in Settings
        </Link>
      ) : null}
    </>
  )
}
export function ProviderRecoveryPanel({
  recovery,
}: {
  recovery: readonly ProviderRecoveryActivityView[]
}) {
  if (!recovery.length) return null
  return (
    <section className="activity-recovery" aria-label="Provider recovery">
      <h3>Provider recovery</h3>
      {recovery.map((item, index) => (
        <p key={`${item.state}:${String(index)}`}>
          <strong>
            {item.state === 'waiting'
              ? 'Waiting for a safe retry'
              : item.state === 'checking'
                ? 'Checking provider health'
                : item.state === 'dormant'
                  ? 'Needs a configuration change'
                  : item.state === 'fanout'
                    ? 'Retrying affected work'
                    : item.state === 'exhausted'
                      ? 'No further retry scheduled'
                      : 'Recovered'}
          </strong>{' '}
          · {item.affected_count} affected{' '}
          {item.state === 'fanout'
            ? item.affected_count === 1
              ? 'work'
              : 'works'
            : item.affected_count === 1
              ? 'scope'
              : 'scopes'}
          {item.next_eligible_at === null
            ? ''
            : ` · next eligible ${formatWhen(item.next_eligible_at, new Date())}`}
          {item.failure_reasons?.length ? (
            <span>
              {' '}
              · {item.failure_reasons.map((reason) => recoveryFailureLabel[reason]).join('; ')}
            </span>
          ) : null}
          {item.dispatch_reasons?.length ? (
            <span>
              {' '}
              · Priority:{' '}
              {item.dispatch_reasons.map((reason) => recoveryDispatchLabel[reason]).join(', ')}
            </span>
          ) : null}
        </p>
      ))}
    </section>
  )
}
