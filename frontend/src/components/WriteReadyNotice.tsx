import { useRequiredQuery as useQuery } from '../queryWork'
import { Link } from '@tanstack/react-router'
import { Button } from './Button'

import {
  handoffConsentQuery,
  librarySummaryQuery,
  useLiveStream,
  usePreparation,
  useReviewContext,
} from '../router'
import { useLibraryName } from '../libraryNames'
import {
  handoffNeedsLibraryNames,
  libraryList,
  readWriteConsent,
  writesEnabled,
  writesOffSentence,
} from '../writeConsent'

/**
 * The completion handoff, stated once and compactly inside the Library flow.
 *
 * Preparation finishing and writes being off are two halves of one moment: an
 * owner is told what is in Library and what will happen to it, in one
 * notice with one entry to the scoped permission review (#641, #639, #1011). The offer is read from the same function Activity reads, so
 * the compact route and the long one cannot come to disagree. Its inputs come
 * from the narrow consent read, which the server projects with the code that
 * projects Activity's, so the notice never waits for Activity history (#1236).
 *
 * It is deliberately not a caution: nothing here has gone wrong. Work is ready
 * and a permission is available, which is a success the owner may act on.
 * When permission lapsed without the owner withdrawing it, the same notice
 * says writes are off and why, rather than disappearing (#1370).
 */
export const WriteReadyNotice = () => {
  const { http } = useReviewContext()
  const live = useLiveStream() === 'live'
  const preparation = usePreparation().data
  // The figure is the library summary's work count, never a scan's case count (#1284).
  const summary = useQuery(librarySummaryQuery(http))
  const works = summary.data?.counts.all
  const query = useQuery({
    ...handoffConsentQuery(http),
    // The cadence is the degraded fallback only. While the stream is up, an
    // `activity` invalidation is what brings this notice up to date, which is
    // why the event needs to carry nothing but the fact that it changed.
    refetchInterval: live ? false : 15_000,
  })
  const data = query.data
  // The notice waits for the names so it is announced once, with them (#1370, #1286).
  const names = useLibraryName(http, handoffNeedsLibraryNames(data))
  if (!data || !names.ready) return null
  const connectionUnavailable = (preparation?.connections ?? []).some(
    (item) => item.state !== 'recovered',
  )
  const consent = readWriteConsent(data, {
    libraryName: names.name,
    enabling: false,
    connectionUnavailable,
  })
  // A lapse is stated even while another library keeps permission (#1370).
  const lapsedBesideHeld =
    consent.state === 'complete' && consent.lapsed !== null && !connectionUnavailable
  if (!consent.actionable && !lapsedBesideHeld) return null
  const lapse = consent.lapsed ? `${consent.lapsed} ` : ''
  // Beside a lapse, writes are enabled only for the libraries that kept
  // permission, so the heading names them (#1370), and only while a kept
  // destination is still allowed: consent outlives a suspension (#1286).
  const holding = data.capabilities
    .filter((item) => item.destinations.some((entry) => entry.consented))
    .map((item) => item.library)
  const held = data.capabilities
    .filter((item) => item.destinations.some(writesEnabled))
    .map((item) => names.name(item.library))
  const enabledFor = consent.lapsed && held.length ? ` for ${libraryList(held)}` : ''
  // Writes are off only for the libraries still waiting on permission; beside
  // one that holds it, the heading says which is which, and a held one is
  // named as held (#1286).
  const offFor = writesOffSentence(data, holding, names.name)
  const preparing = Boolean(preparation?.active)
  const inLibrary =
    works !== undefined
      ? `${String(works)} ${works === 1 ? 'work is' : 'works are'} in Library. `
      : summary.isError
        ? ''
        : 'Counting works in Library… '
  const heading =
    consent.state === 'off'
      ? `${inLibrary}${offFor}`
      : consent.state === 'enabling'
        ? 'Enabling writes…'
        : consent.state === 'applying'
          ? 'Applying prepared changes.'
          : `${inLibrary}Writes are enabled${enabledFor}.`
  const copy =
    consent.state === 'off'
      ? `${lapse}Open permission review in Activity to check current readiness${preparing ? ' while preparation continues' : ''}.`
      : consent.state === 'enabling'
        ? 'Confirming permission with the authoritative server. No change is sent twice.'
        : consent.state === 'applying'
          ? `${lapse}${String(consent.reasons.applying)} in progress; the remaining ${String(consent.reasons.scheduled)} stay ready.`
          : consent.state === 'complete'
            ? `${lapse}Review current write permissions in Activity.`
            : `${lapse}${String(consent.reasons.scheduled)} prepared ${consent.reasons.scheduled === 1 ? 'change is' : 'changes are'} ready to apply.`
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
        {consent.state === 'off' || consent.state === 'complete' ? (
          <Link
            className="button-primary"
            to="/activity"
            search={{ eventType: 'all', rangeDays: 30, page: 1, pageSize: 25 }}
            hash="activity-consent"
          >
            Review write permissions
          </Link>
        ) : (
          <Button variant="primary" type="button" disabled>
            {consent.state === 'applying' ? 'Applying changes…' : 'Writes enabled'}
          </Button>
        )}
        <Link to="/activity" search={{ eventType: 'all', rangeDays: 30, page: 1, pageSize: 25 }}>
          Review write activity
        </Link>
      </div>
      {consent.blocked ? <p className="cm-library-handoff-reason">{consent.blocked}</p> : null}
    </section>
  )
}
