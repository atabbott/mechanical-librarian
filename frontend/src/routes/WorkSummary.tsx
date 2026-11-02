// A Library work without an open review case: its summary, its recorded
// evidence and the sources consulted for it. Moved out of the Library route
// under the route-size rule (#1571).
import { BuildMismatchError } from '../api/http'
import type { LibraryWorkView, ProviderOutcomeView } from '../api/http'
import { BookMark } from '../components/BookMark'
import { MediaHealth } from '../components/MediaHealth'
import { RecordingIdentityBasis } from '../components/RecordingIdentityBasis'
import { StaleBuildRead } from '../components/StaleBuildRead'
import { formatRunningTime } from '../dates'
import { valueText } from '../fieldDecision'
import {
  audioSurfaceLabels,
  claimBasisLabels,
  claimKindLabels,
  destinationLabel,
  fieldStateLabels,
  lookupLabels,
  mediaKindLabels,
  preparationStageLabels,
  preparationStatusLabels,
  sourceStatusLabels,
} from '../labels'
import { authorLine, reportEvidenceUrl } from '../libraryWork'
import { queryWorkClass, useRequiredQuery as useQuery } from '../queryWork'
import { useReviewContext } from '../router'

const chapterSpan = (start: number, end: number) => {
  const clock = (value: number) => {
    const seconds = Math.floor(value / 1000)
    const parts = [Math.floor(seconds / 3600), Math.floor((seconds % 3600) / 60), seconds % 60]
    return parts.map((part) => String(part).padStart(2, '0')).join(':')
  }
  return `${clock(start)} – ${clock(end)}`
}
// A claim's role is the source's own words, shown as written; a role the
// source supplied as unsafe or oversized text reads as such, never as its digest.
const claimRole = (role: string) =>
  /^(?:unsafe|oversized)-source-text-sha256:/u.test(role) ? 'role not shown' : role
// Two surfaces, one disclosure. The review comparison carries the protected
// evidence footer -- one line, no subtitle -- while the work summary, which is
// not a comparison, keeps naming what it consulted.
export function WorkSources({
  outcomes,
  comparison = false,
}: {
  outcomes: readonly ProviderOutcomeView[]
  comparison?: boolean
}) {
  if (!outcomes.length) return null
  const ledgerLabel = `${String(outcomes.length)} ${outcomes.length === 1 ? 'lookup recorded' : 'lookups recorded'} for this work`
  return (
    <details className="work-sources">
      <summary>
        {comparison ? (
          <span>{ledgerLabel}</span>
        ) : (
          <>
            <span>Sources</span>
            <small>{ledgerLabel}</small>
          </>
        )}
      </summary>
      <ul>
        {outcomes.map((outcome, index) => (
          <li key={`${outcome.provider}:${outcome.laneKind}:${outcome.status}:${String(index)}`}>
            <strong>{outcome.provider}</strong>
            <b>{sourceStatusLabels[outcome.status]}</b>
            <span>{outcome.reason}</span>
            <small>
              {lookupLabels[outcome.laneKind]} · {String(outcome.providerRequests)}{' '}
              {outcome.providerRequests === 1 ? 'request' : 'requests'}
            </small>
          </li>
        ))}
      </ul>
    </details>
  )
}

// The evidence a work with no review case carries. It is recorded, retained,
// and -- until this read -- reachable from nowhere in the browser: an audiobook
// acquires no case, so `detail_url` on its row is the only route it has.
// Read-only by construction: nothing here proposes, decides, or writes.
function WorkEvidence({ detailUrl }: { detailUrl: string }) {
  const { http } = useReviewContext()
  const report = useQuery({
    queryKey: ['library-report', detailUrl],
    queryFn: (queryContext) =>
      http.getReportDetail(detailUrl, queryContext.signal, queryWorkClass(queryContext)),
    staleTime: 15_000,
    retry: false,
  })
  if (report.isPending)
    return (
      <p role="status" className="library-work-summary-reason">
        Loading recorded evidence…
      </p>
    )
  if (report.error instanceof BuildMismatchError)
    return <StaleBuildRead lead="Recorded evidence was not loaded." />
  if (report.isError)
    return (
      <p role="alert" className="library-work-summary-reason">
        Recorded evidence could not be loaded.{' '}
        <button type="button" onClick={() => void report.refetch()}>
          Try again
        </button>
      </p>
    )
  const data = report.data
  return (
    <section className="library-work-evidence" aria-label="Recorded evidence">
      <h3>Recorded evidence</h3>
      <RecordingIdentityBasis
        value={data.recordingIdentityBasis}
        scope={data.audiobookReviewScope}
        offered={[]}
      />
      {data.evidence.length === 0 ? (
        <p role="status">No local evidence was recorded for this work.</p>
      ) : (
        <dl className="library-work-values">
          {data.evidence.map((claim, index) => (
            <div key={`${claim.kind}-${claim.basis}-${String(index)}`}>
              <dt>
                {claimKindLabels[claim.kind]}
                {claim.role ? ` · ${claimRole(claim.role)}` : ''}
              </dt>
              <dd>
                {/* Only a valid running time reads in hours and minutes; any
                    other claim keeps its recorded text. */}
                {(claim.kind === 'duration' && claim.state === 'valid'
                  ? formatRunningTime(claim.value)
                  : null) ?? claim.value}
                {/* An oversized value is retained as its beginning only, so it
                    says so rather than reading as the whole (#1290). */}
                {claim.state === 'oversized' ? '…' : null}
                <small>
                  {claimBasisLabels[claim.basis]}
                  {claim.state === 'valid' ? '' : ` · ${fieldStateLabels[claim.state]}`}
                  {claim.state === 'oversized' ? ' · Only the beginning is shown' : null}
                </small>
              </dd>
            </div>
          ))}
        </dl>
      )}
      {data.evidenceTruncated ? (
        <p role="status">
          Only the first {String(data.evidence.length)} recorded observations are shown.
        </p>
      ) : null}
      <MediaHealth observations={data.mediaHealth} />
      <p className="library-work-cover-presence">
        {data.coverPresent
          ? `Embedded cover art is present${
              data.coverSurface ? ` (${audioSurfaceLabels[data.coverSurface]})` : ''
            }.`
          : 'No embedded cover art was recorded.'}
      </p>
      {data.chapters.length ? (
        <details className="library-work-chapters">
          <summary>
            Chapters ({String(data.chapters.length)}
            {data.chaptersTruncated ? ', truncated' : ''})
          </summary>
          <ol>
            {data.chapters.map((chapter) => (
              <li key={chapter.ordinal}>
                {chapter.title ?? `Chapter ${String(chapter.ordinal)}`}
                <small>{chapterSpan(chapter.startMilliseconds, chapter.endMilliseconds)}</small>
              </li>
            ))}
          </ol>
        </details>
      ) : null}
    </section>
  )
}

export function WorkSummary({ work }: { work: LibraryWorkView }) {
  const summary = work.summary
  const evidenceUrl = reportEvidenceUrl(work)
  return (
    <>
      <header className="selected-work-header" tabIndex={-1} data-route-focus="true">
        <BookMark title={work.title} cover={work.cover} />
        <div>
          <h2>{work.title}</h2>
          <p>
            {authorLine(work)} · {work.libraryDisplayName ?? work.library} ·{' '}
            {mediaKindLabels[work.mediaKind]}
          </p>
        </div>
      </header>
      <p className="library-work-summary-reason" role="status">
        {summary?.reason ?? 'No review decision is recorded for this work.'}
      </p>
      {work.preparation ? (
        <aside className="comparison-notice" aria-label="Preparation condition">
          <p>
            <strong>
              {preparationStatusLabels[work.preparation.status]}{' '}
              {preparationStageLabels[work.preparation.stage]}
            </strong>
          </p>
          <p>
            {work.preparation.reason} {work.preparation.recovery}
          </p>
        </aside>
      ) : null}
      {summary?.fields.length ? (
        <dl className="library-work-values">
          {summary.fields.map((field) => (
            <div key={field.destination}>
              <dt>{destinationLabel(field.destination)}</dt>
              <dd>
                {field.state === 'value' ? valueText(field.destination, field.value) : field.value}
              </dd>
            </div>
          ))}
        </dl>
      ) : null}
      {evidenceUrl ? <WorkEvidence detailUrl={evidenceUrl} /> : null}
      <WorkSources outcomes={work.preparationOutcomes} />
    </>
  )
}
