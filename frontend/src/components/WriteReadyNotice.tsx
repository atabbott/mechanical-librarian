import { queryWorkClass, useRequiredQuery as useQuery } from '../queryWork'
import { Link } from '@tanstack/react-router'

import { useLiveStream, usePreparation, useReviewContext } from '../router'
import { readWriteConsent } from '../writeConsent'

/**
 * The completion handoff, stated once and compactly inside the Library flow.
 *
 * Preparation finishing and writes being off are two halves of one moment: an
 * owner is told what is ready to browse and what will happen to it, in one
 * notice with one entry to the scoped permission review (#641, #639, #1011). The offer is read from the same function Activity reads, so
 * the compact route and the long one cannot come to disagree. Its inputs come
 * from the narrow consent read, which the server projects with the code that
 * projects Activity's, so the notice never waits for Activity history (#1236).
 *
 * It is deliberately not a caution: nothing here has gone wrong. Work is ready
 * and a permission is available, which is a success the owner may act on.
 */
export const WriteReadyNotice = () => {
  const { http } = useReviewContext()
  const live = useLiveStream() === 'live'
  const preparation = usePreparation().data
  const query = useQuery({
    queryKey: ['activity', 'library-handoff'] as const,
    queryFn: (queryContext) =>
      http.getActivityConsent(queryContext.signal, queryWorkClass(queryContext)),
    staleTime: 4_000,
    retry: false,
    // The cadence is the degraded fallback only. While the stream is up, an
    // `activity` invalidation is what brings this notice up to date, which is
    // why the event needs to carry nothing but the fact that it changed.
    refetchInterval: live ? false : 15_000,
  })
  const data = query.data
  if (!data) return null
  const consent = readWriteConsent(data, {
    enabling: false,
    connectionUnavailable: (preparation?.connections ?? []).some(
      (item) => item.state !== 'recovered',
    ),
  })
  if (!consent.actionable) return null
  const ready = preparation?.libraries.reduce((total, item) => total + item.ready, 0) ?? 0
  const preparing = Boolean(preparation?.active)
  const heading =
    consent.state === 'off'
      ? `${String(ready)} ${ready === 1 ? 'work is' : 'works are'} ready to browse. Writes are off.`
      : consent.state === 'enabling'
        ? 'Enabling writes…'
        : consent.state === 'applying'
          ? 'Applying prepared changes.'
          : `${String(ready)} ${ready === 1 ? 'work is' : 'works are'} ready to browse. Writes are enabled.`
  const copy =
    consent.state === 'off'
      ? `Review current write permissions in Activity${preparing ? ' while preparation continues' : ''}. Readiness is checked when you open the review.`
      : consent.state === 'enabling'
        ? 'Confirming permission with the authoritative server. No change is sent twice.'
        : consent.state === 'applying'
          ? `${String(consent.reasons.applying)} in progress; the remaining ${String(consent.reasons.scheduled)} stay ready.`
          : `${String(consent.reasons.scheduled)} prepared ${consent.reasons.scheduled === 1 ? 'change is' : 'changes are'} ready to apply.`
  return (
    <section
      className={`cm-library-handoff is-${consent.state}`}
      aria-labelledby="library-handoff-title"
      aria-live="polite"
    >
      <div>
        <h2 id="library-handoff-title">{heading}</h2>
        <p>{copy}</p>
      </div>
      <div className="cm-library-handoff-action">
        {consent.state === 'off' ? (
          <Link
            className="cm-primary"
            to="/activity"
            search={{ eventType: 'all', rangeDays: 30, page: 1, pageSize: 25 }}
            hash="activity-consent"
          >
            Review write permissions
          </Link>
        ) : (
          <button className="cm-primary" type="button" disabled>
            {consent.state === 'applying' ? 'Applying changes…' : 'Writes enabled'}
          </button>
        )}
        <Link to="/activity" search={{ eventType: 'all', rangeDays: 30, page: 1, pageSize: 25 }}>
          Review write activity
        </Link>
      </div>
      {consent.blocked ? <p className="cm-library-handoff-reason">{consent.blocked}</p> : null}
    </section>
  )
}
