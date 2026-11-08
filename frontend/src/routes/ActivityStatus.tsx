import { Link } from '@tanstack/react-router'
import type {
  ProviderRecoveryActivityView,
  ProviderRecoveryDispatchReason,
  ProviderRecoveryFailureReason,
} from '../api/http'
import { formatWhen } from '../dates'
// The Activity status panel's summary and its provider recovery list.
const recoveryFailureLabel: Record<ProviderRecoveryFailureReason, string> = {
  authentication: 'Refused the API key',
  authorization: 'Refused access for this API key',
  configuration: 'Settings need checking',
  rate_limited: 'Limiting requests',
  burst_throttled: 'Too many requests at once',
  quota_exhausted: 'Request quota used up',
  transient: 'Did not answer',
}

const recoveryDispatchLabel: Record<ProviderRecoveryDispatchReason, string> = {
  missing_corroboration: 'needs a second source',
  zero_provider_evidence: 'no source has answered yet',
  settleable_disagreement: 'sources disagree',
  blanket_scan: 'general retry',
}

// What most needs the owner, said once: the heading and paragraph name the
// first condition that applies, in this order. A checking or failed library
// connection is said by the shell's connection notice, so the page renders
// none of this while one holds (#1765).
export function ActivityStatusCopy({
  recoveryCount,
  dormantRecovery,
  ownerAction,
  filtered,
  throttled,
  throttleRemedy,
  paused,
  writesHeld,
  lapse,
}: {
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
}) {
  return (
    <>
      <h2 id="activity-status-title">
        {recoveryCount
          ? 'Recovery needs attention'
          : ownerAction
            ? ownerAction === 1
              ? 'A stopped change needs you'
              : `${String(ownerAction)} stopped changes need you`
            : throttleRemedy
              ? 'A source needs attention'
              : throttled
                ? 'A source is limiting lookups'
                : paused
                  ? 'Writes are paused'
                  : !writesHeld
                    ? 'Writes are off'
                    : lapse
                      ? 'Some writes are off'
                      : 'No action needed'}
      </h2>
      <p>
        {dormantRecovery
          ? `${String(dormantRecovery)} ${dormantRecovery === 1 ? 'source needs' : 'sources need'} a settings change before lookups retry.`
          : recoveryCount
            ? 'Affected works are held until they can be checked again.'
            : ownerAction
              ? ownerAction === 1
                ? `Its ledger entry, marked Closed, says why. Nothing retries it on its own: follow its advice, or choose the field again. If it isn’t listed, ${filtered ? 'widen the range or clear filters' : 'widen the range'}.`
                : `Each one’s ledger entry, marked Closed, says why. Nothing retries them on their own: follow its advice, or choose the field again. If one isn’t listed, ${filtered ? 'widen the range or clear filters' : 'widen the range'}.`
              : throttled
                ? throttleRemedy
                  ? 'A source needs its API key added or checked. Its lookups wait until then.'
                  : 'Some lookups wait until the source allows them again.'
                : paused
                  ? 'No new write starts until you resume writes. Writes already under way finish.'
                  : (lapse ??
                    (writesHeld
                      ? ''
                      : 'No library has write permission. Nothing is written until you give it.'))}
      </p>
      {/* Both paragraphs that send you to Settings link it (#1765). */}
      {dormantRecovery || (throttled && !recoveryCount && !ownerAction) ? (
        <Link to="/settings" search={{ group: 'providers' }}>
          Check sources in Settings
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
    <section className="activity-recovery" aria-label="Source recovery">
      <h3>Source recovery</h3>
      {recovery.map((item, index) => (
        <p key={`${item.state}:${String(index)}`}>
          <strong>
            {item.state === 'waiting'
              ? 'Waiting to retry'
              : item.state === 'checking'
                ? 'Checking the source'
                : item.state === 'dormant'
                  ? 'Needs a settings change'
                  : item.state === 'fanout'
                    ? 'Retrying'
                    : item.state === 'exhausted'
                      ? 'No automatic retry remains'
                      : 'Recovered'}
          </strong>{' '}
          · {item.affected_count}{' '}
          {item.state === 'fanout'
            ? item.affected_count === 1
              ? 'work'
              : 'works'
            : item.affected_count === 1
              ? 'source'
              : 'sources'}
          {item.next_eligible_at === null
            ? ''
            : ` · next retry ${formatWhen(item.next_eligible_at, new Date())}`}
          {item.failure_reasons.length ? (
            <span>
              {' '}
              · {item.failure_reasons.map((reason) => recoveryFailureLabel[reason]).join('; ')}
            </span>
          ) : null}
          {item.dispatch_reasons.length ? (
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
