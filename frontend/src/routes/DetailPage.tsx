import { queryWorkClass, useRequiredQuery as useQuery } from '../queryWork'
import { RecordingIdentityBasis } from '../components/RecordingIdentityBasis'
import { useInfiniteQuery } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'

import { MediaHealth } from '../components/MediaHealth'
import type {
  CaseDetailView,
  DescriptionState,
  EvidenceView,
  HistoryValueView,
  HistoryView,
  TargetDestination,
} from '../api/http'
import { BuildMismatchError, displayValue } from '../api/http'
import { StaleBuildRead } from '../components/StaleBuildRead'
import { railOffers } from '../reviewChoiceCounts'
import { RecordedTime } from '../components/RecordedTime'
import {
  coverHistoryLabels,
  coverNoteLabel,
  coverReviewerLabel,
  destinationLabel,
  evidenceRoleLabel,
  languageName,
  observationText,
  sourceLookupLabel,
  mediaKindLabels,
  previewDispositionLabels,
  sourceStatusLabel,
  transitionLabels,
} from '../labels'
import { detailRoute, normalizeDetailSearch, useReviewContext } from '../router'

// A recorded value, a language read by its name (#1286).
const historyValue = (destination: TargetDestination, value: HistoryValueView | null) =>
  value === null
    ? 'No value is recorded.'
    : destination === 'language' && typeof value.value === 'string'
      ? languageName(value.value)
      : displayValue(value.value)
const reviewerStatusWords = {
  completed: 'looked at',
  failed: 'failed on',
  unavailable: 'was unavailable for',
} as const
// Each history event says what happened at that moment, in the past tense:
// a finished write lists its earlier steps below it, newest first.
const descriptionWriteSentences = {
  requested: 'The write was requested.',
  running: 'The write started.',
  succeeded: 'The write finished.',
  failed: 'The write failed.',
  interrupted: 'The write was interrupted.',
  planned: 'The write was planned.',
  claimed: 'The write was prepared.',
  applying: 'The write started.',
  reconciling: 'Checking the write started.',
  retryable: 'The write was set to try again.',
  verified: 'Written to the library and checked.',
  stale: 'The write was closed before it finished.',
  superseded: 'The write was replaced by a later choice.',
  abandoned: 'The write was stopped.',
  no_change_required: 'No change needed. The library already had this text.',
} satisfies Record<DescriptionState, string>
// Advice events only ever carry the advice run's states.
const descriptionAdviceSentences: Partial<Record<DescriptionState, string>> = {
  requested: 'Advice was requested.',
  running: 'Advice writing started.',
  succeeded: 'Advice was written.',
  failed: 'Advice could not be written.',
  interrupted: 'Advice was interrupted.',
}
const coverOwnerSentences = {
  accept: 'You chose a proposed cover.',
  keep_current: 'You kept the current cover.',
  reject: 'You declined the cover change.',
} as const
const coverPolicySentences = {
  recommended: 'The policy recommended a cover.',
  abstained: 'The policy recommended no cover.',
} as const

function HistoryItem({ item }: { item: HistoryView['items'][number] }) {
  if (item.category === 'description')
    return (
      <article className="diagnostic-entry">
        <h3>Description advice</h3>
        <p>
          {item.engine} · {item.model}.{' '}
          {item.outcome === 'description_application'
            ? descriptionWriteSentences[item.state]
            : item.selected
              ? item.state === 'superseded'
                ? 'Replaced by a later choice.'
                : item.chosenBy === 'automatic'
                  ? 'Automatic chose this description.'
                  : 'You chose this description.'
              : (descriptionAdviceSentences[item.state] ?? 'Advice was recorded.')}
        </p>
        {item.candidate ? (
          <p className="description-advice-text">{item.candidate.output.description}</p>
        ) : item.state === 'requested' || item.state === 'running' ? null : (
          // Only once advice has stopped is "no text" news.
          <p>No description was written.</p>
        )}
      </article>
    )
  if (item.category === 'owner' || item.category === 'automatic') {
    // A withdrawal selects nothing, so it has no value to change to; its
    // sentence says what happened.
    const withdrawn = item.category === 'owner' && item.outcome === 'withdraw'
    const action =
      item.category === 'owner'
        ? item.outcome === 'accept'
          ? 'You chose a proposed value.'
          : item.outcome === 'keep_current'
            ? 'You kept the current value.'
            : item.outcome === 'owner_entered'
              ? 'You entered a value.'
              : item.outcome === 'withdraw'
                ? 'You withdrew your choice.'
                : 'You declined the proposed change.'
        : item.outcome === 'candidate'
          ? 'Automatic chose a proposed value.'
          : item.outcome === 'keep_current'
            ? 'Automatic kept the current value.'
            : 'Automatic left this field unchanged.'
    return (
      <article className="diagnostic-entry">
        <h3>{destinationLabel(item.destination)}</h3>
        <p>{action}</p>
        {item.category === 'owner' && item.applicationReason ? (
          <p className="diagnostic-note">{item.applicationReason}</p>
        ) : null}
        {isRetention(item) ? (
          <p className="diagnostic-value">
            <span>Kept</span> {displayRetainedHistoryValue(item)}
          </p>
        ) : withdrawn ? null : (
          <>
            <p className="diagnostic-value">
              <span>From</span> {historyValue(item.destination, item.previousValue)}
            </p>
            <p className="diagnostic-value">
              <span>To</span> {historyValue(item.destination, item.selectedValue)}
            </p>
          </>
        )}
      </article>
    )
  }
  if (item.category === 'preview')
    return (
      <article className="diagnostic-entry">
        <h3>Policy preview</h3>
        <p>Nothing was changed.</p>
        {item.dispositions.length ? (
          <ul className="diagnostic-facts" aria-label="Preview outcomes">
            {item.dispositions.map((disposition) => (
              <li key={disposition.outcome}>
                <strong>{previewDispositionLabels[disposition.outcome]}</strong>
                <span>{String(disposition.count)}</span>
              </li>
            ))}
          </ul>
        ) : null}
      </article>
    )
  // The heading says what happened; a sentence under it would only repeat it
  // (#1765).
  if (item.category === 'transition')
    return (
      <article className="diagnostic-entry">
        <h3>{transitionLabels[item.outcome]}</h3>
      </article>
    )
  return (
    <article className="diagnostic-entry">
      <h3>{coverHistoryLabels[item.outcome]}</h3>
      <p>
        The cover reviewer
        {item.reviewerModel ? ` (${coverReviewerLabel(item.reviewerModel)})` : ''}{' '}
        {reviewerStatusWords[item.reviewerStatus]} {String(item.candidateCount)}{' '}
        {item.candidateCount === 1 ? 'cover' : 'covers'}.
      </p>
      {item.ownerOutcome ? (
        <p>
          {coverOwnerSentences[item.ownerOutcome]}
          {item.ownerMatchedBook
            ? ' This cover is from the matched book and is not confirmed for this recording.'
            : item.ownerMatchUnconfirmed
              ? ' No source confirmed the match.'
              : ''}
        </p>
      ) : null}
      {item.automaticOutcome ? <p>Automatic chose this cover.</p> : null}
      {item.supersedesAutomatic ? (
        <p>It replaced the earlier automatic choice with a clearly better confirmed cover.</p>
      ) : null}
      {item.policyStatus ? <p>{coverPolicySentences[item.policyStatus]}</p> : null}
      {item.policyReasons.length ? (
        <ul className="diagnostic-limitations" aria-label="Policy reasons">
          {item.policyReasons.map((reason) => (
            <li key={reason}>
              {item.matchedBookOffered && reason === 'no_confirmed_candidates'
                ? 'No cover is confirmed for this recording'
                : coverNoteLabel(reason)}
            </li>
          ))}
        </ul>
      ) : null}
    </article>
  )
}

function isRetention(
  item: Extract<HistoryView['items'][number], { category: 'owner' | 'automatic' }>,
) {
  if (item.outcome === 'reject' || item.outcome === 'ignore') return true
  if (item.outcome === 'keep_current')
    return (
      historyValuesMatch(item.previousValue, item.selectedValue) ||
      (item.previousValue === null && item.selectedValue === null)
    )
  return historyValuesMatch(item.previousValue, item.selectedValue)
}

function displayRetainedHistoryValue(
  item: Extract<HistoryView['items'][number], { category: 'owner' | 'automatic' }>,
) {
  return historyValue(item.destination, item.selectedValue ?? item.previousValue)
}

function historyValuesMatch(previous: HistoryValueView | null, selected: HistoryValueView | null) {
  if (previous === null || selected === null) return false
  return (
    previous.kind === selected.kind &&
    JSON.stringify(previous.value) === JSON.stringify(selected.value)
  )
}

export default function DetailPage({ data }: { data: CaseDetailView }) {
  const { http } = useReviewContext()
  const search = normalizeDetailSearch(detailRoute.useSearch())
  const { reviewCaseId } = detailRoute.useParams()
  // Diagnostics belong to the case on screen, which a retained read keeps
  // after the URL has moved to the next case.
  const evidence = useInfiniteQuery({
    queryKey: ['review-case', data.id, 'evidence'],
    initialPageParam: undefined as string | undefined,
    queryFn: (queryContext) =>
      http.getEvidence(
        data.id,
        queryContext.pageParam,
        queryContext.signal,
        queryWorkClass(queryContext),
      ),
    getNextPageParam: (page: EvidenceView) => page.nextCursor,
    staleTime: 30_000,
    retry: false,
    enabled: data.current && search.subview === 'evidence',
  })
  const history = useQuery({
    queryKey: ['review-case', data.id, 'history'],
    queryFn: (queryContext) =>
      http.getHistory(data.id, queryContext.signal, queryWorkClass(queryContext)),
    staleTime: 30_000,
    retry: false,
    enabled: data.current && search.subview === 'history',
  })
  const observations = evidence.data?.pages.flatMap((page) => page.items) ?? []
  const sourceLedger = evidence.data?.pages[0]?.sourceLedger ?? []
  const evidenceFields = data.fields
    .map((field) => ({
      field,
      fieldObservations: observations.filter((item) =>
        item.destinations.includes(field.destination),
      ),
    }))
    .filter(
      ({ field, fieldObservations }) =>
        fieldObservations.length > 0 || field.automaticWithheldReason !== null,
    )
  const missingLineageCount = evidence.data?.pages[0]?.missingLineageCount ?? 0
  if (!data.current)
    return (
      <section className="diagnostic-state diagnostic-limitation" data-route-focus tabIndex={-1}>
        <h1>This review case is out of date</h1>
        <p>Go back to the work to open the current one.</p>
      </section>
    )
  return (
    <>
      <header className="diagnostic-heading" data-route-focus tabIndex={-1}>
        <h1>{data.title}</h1>
        <p className="diagnostic-authors">
          {data.authors.length ? data.authors.join(', ') : 'Author details unavailable'}
        </p>
        <p className="diagnostic-origin">
          {data.target.libraryDisplayName ?? data.target.library} ·{' '}
          {mediaKindLabels[data.target.mediaKind]}
        </p>
      </header>
      <nav className="subviews" aria-label="Evidence and history">
        <Link
          aria-current={search.subview === 'evidence' ? 'page' : undefined}
          params={{ reviewCaseId }}
          search={{ ...search, subview: 'evidence' }}
          to="/review/$reviewCaseId"
        >
          Evidence
        </Link>
        <Link
          aria-current={search.subview === 'history' ? 'page' : undefined}
          params={{ reviewCaseId }}
          search={{ ...search, subview: 'history' }}
          to="/review/$reviewCaseId"
        >
          History
        </Link>
      </nav>
      {search.subview === 'evidence' ? (
        <section className="diagnostic-sheet evidence-sheet" aria-label="Evidence">
          <header>
            <h2>Evidence</h2>
          </header>
          {evidence.isPending ? (
            <p role="status">Loading evidence…</p>
          ) : evidence.error instanceof BuildMismatchError ? (
            // The shell's build notice is the alert; this names what is missing.
            <StaleBuildRead lead="Evidence was not loaded." />
          ) : evidence.isError ? (
            <p className="diagnostic-read-failure" role="alert">
              Evidence could not be loaded. Reload the page to try again.
            </p>
          ) : (
            <>
              {missingLineageCount ? (
                <p className="diagnostic-limitation" role="status">
                  {String(missingLineageCount)}{' '}
                  {missingLineageCount === 1 ? 'source answer is' : 'source answers are'} no longer
                  available. The rest is shown below.
                </p>
              ) : null}
              <RecordingIdentityBasis
                value={data.recordingIdentityBasis}
                admission={data.recordingAdmissionBasis}
                scope={data.audiobookReviewScope}
                offered={data.fields.filter(railOffers).map((field) => field.destination)}
              />
              {data.mediaHealth.length ? <MediaHealth observations={data.mediaHealth} /> : null}
              {/* A field with nothing from the sources is left out; when every
                  field is empty, one line says so. */}
              {evidenceFields.length ? (
                <div className="diagnostic-list evidence-fields">
                  {evidenceFields.map(({ field, fieldObservations }) => (
                    <section className="diagnostic-entry evidence-field" key={field.destination}>
                      <h3>{destinationLabel(field.destination)}</h3>
                      {field.automaticWithheldReason ? (
                        <p className="automatic-withheld-reason">{field.automaticWithheldReason}</p>
                      ) : null}
                      {fieldObservations.length ? (
                        <ul className="evidence-values">
                          {fieldObservations.map((item, index) => (
                            <li key={`${item.source}-${String(index)}-${item.observation}`}>
                              <p>{observationText(item)}</p>
                              <small>
                                {item.source} · {evidenceRoleLabel(item.role)}
                              </small>
                            </li>
                          ))}
                        </ul>
                      ) : null}
                    </section>
                  ))}
                </div>
              ) : (
                <p className="diagnostic-complete">
                  {evidence.hasNextPage
                    ? 'Nothing from the sources yet. More may load below.'
                    : 'Nothing from the sources for this work.'}
                </p>
              )}
              {evidence.hasNextPage ? (
                <button
                  className="diagnostic-load-more"
                  disabled={evidence.isFetchingNextPage}
                  onClick={() => void evidence.fetchNextPage()}
                >
                  Load more evidence
                </button>
              ) : evidenceFields.length ? (
                <p className="diagnostic-complete">All evidence is shown.</p>
              ) : null}
              {sourceLedger.length ? (
                <details className="source-ledger">
                  <summary>Sources</summary>
                  <ul>
                    {sourceLedger.map((source, index) => (
                      <li key={`${source.provider}-${source.laneKind}-${String(index)}`}>
                        <strong>{source.provider}</strong>
                        <span>{sourceLookupLabel(source)}</span>
                        <b>{sourceStatusLabel(source)}</b>
                        <p>{source.reason}</p>
                        <small>
                          {String(source.providerRequests)}{' '}
                          {source.providerRequests === 1 ? 'request' : 'requests'}
                        </small>
                      </li>
                    ))}
                  </ul>
                </details>
              ) : null}
            </>
          )}
        </section>
      ) : (
        <section className="diagnostic-sheet" aria-label="History">
          <header>
            <h2>History</h2>
          </header>
          {history.isPending ? (
            <p role="status">Loading history…</p>
          ) : history.error instanceof BuildMismatchError ? (
            // The shell's build notice is the alert; this names what is missing.
            <StaleBuildRead lead="History was not loaded." />
          ) : history.isError ? (
            <p className="diagnostic-read-failure" role="alert">
              History could not be loaded. Reload the page to try again.
            </p>
          ) : history.data.items.length ? (
            <div className="diagnostic-list">
              {history.data.items.map((item, index) => (
                <div key={index}>
                  {item.occurredAt === null ? null : (
                    <time dateTime={new Date(item.occurredAt * 1000).toISOString()}>
                      <RecordedTime seconds={item.occurredAt} />
                    </time>
                  )}
                  <HistoryItem item={item} />
                </div>
              ))}
            </div>
          ) : (
            <p>Nothing is recorded for this work yet.</p>
          )}
        </section>
      )}
    </>
  )
}
