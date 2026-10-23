import { queryWorkClass, useRequiredQuery as useQuery } from '../queryWork'
import { RecordingIdentityBasis } from '../components/RecordingIdentityBasis'
import { useInfiniteQuery } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'

import { MediaHealth } from '../components/MediaHealth'
import type { CaseDetailView, EvidenceView, HistoryValueView, HistoryView } from '../api/http'
import { displayValue } from '../api/http'
import { coverReviewerLabel, evidenceRoleLabel, fieldLabel, labelWords } from '../labels'
import { detailRoute, normalizeDetailSearch, useReviewContext } from '../router'

function HistoryItem({ item }: { item: HistoryView['items'][number] }) {
  if (item.category === 'description')
    return (
      <article className="diagnostic-entry">
        <h3>Advised description · Authored content</h3>
        <p>
          {item.engine} · {item.model}.{' '}
          {item.outcome === 'description_application'
            ? item.state === 'verified'
              ? 'Applied and verified in the library.'
              : item.state === 'no_change_required'
                ? 'No change needed; the library already had this text.'
                : `Guarded application: ${labelWords(item.state)}.`
            : item.selected
              ? item.state === 'superseded'
                ? 'This authored selection was superseded by a later decision.'
                : 'Selected under the existing write requirements.'
              : `Preparation ${item.state}.`}
        </p>
        {item.candidate ? (
          <p className="description-advice-text">{item.candidate.output.description}</p>
        ) : (
          <p>No authored candidate was produced.</p>
        )}
      </article>
    )
  if (item.category === 'owner' || item.category === 'automatic') {
    const action =
      item.category === 'owner'
        ? item.outcome === 'accept'
          ? 'Owner selected a proposed value.'
          : item.outcome === 'keep_current'
            ? 'Owner chose the current value.'
            : item.outcome === 'owner_entered'
              ? 'Owner entered a value.'
              : item.outcome === 'withdraw'
                ? 'Owner withdrew the decision.'
                : item.outcome === 'revert'
                  ? 'Owner restored the earlier value.'
                  : 'Owner declined the proposed change.'
        : item.outcome === 'candidate'
          ? 'Automatic policy selected a candidate.'
          : item.outcome === 'keep_current'
            ? 'Automatic policy chose the current value.'
            : 'Automatic policy left this field unchanged.'
    return (
      <article className="diagnostic-entry">
        <h3>{fieldLabel(item.destination)}</h3>
        <p>{action}</p>
        {item.category === 'owner' && item.applicationReason ? (
          <p className="diagnostic-note">{item.applicationReason}</p>
        ) : null}
        {isRetention(item) ? (
          <p className="diagnostic-value">
            <span>Retained</span> {displayRetainedHistoryValue(item)}
          </p>
        ) : (
          <>
            <p className="diagnostic-value">
              <span>From</span>{' '}
              {item.previousValue
                ? displayValue(item.previousValue.value)
                : 'No value is recorded.'}
            </p>
            <p className="diagnostic-value">
              <span>To</span>{' '}
              {item.selectedValue
                ? displayValue(item.selectedValue.value)
                : 'No value is recorded.'}
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
        <p>This read-only preview recorded no decision authority.</p>
        {item.dispositions.length ? (
          <ul className="diagnostic-facts" aria-label="Preview outcomes">
            {item.dispositions.map((disposition) => (
              <li key={disposition.outcome}>
                <strong>{labelWords(disposition.outcome)}</strong>
                <span>{String(disposition.count)}</span>
              </li>
            ))}
          </ul>
        ) : null}
      </article>
    )
  if (item.category === 'transition')
    return (
      <article className="diagnostic-entry">
        <h3>{fieldLabel(item.outcome)}</h3>
        <p>
          {item.outcome === 'admitted'
            ? 'This work entered the review queue.'
            : item.outcome === 'superseded'
              ? 'A newer review case replaced the previous one.'
              : item.outcome === 'no_case'
                ? 'No review case was needed for this work.'
                : item.outcome === 'retired'
                  ? 'This review case was retired.'
                  : 'This work is no longer present in the retained library scan.'}
        </p>
      </article>
    )
  return (
    <article className="diagnostic-entry">
      <h3>Cover {labelWords(item.outcome)}</h3>
      <p>
        Visual review {labelWords(item.reviewerStatus)}
        {item.reviewerModel ? ` by ${coverReviewerLabel(item.reviewerModel)}` : ''} across{' '}
        {String(item.candidateCount)} {item.candidateCount === 1 ? 'candidate' : 'candidates'}.
      </p>
      {item.ownerOutcome ? (
        <p>
          Owner-confirmed choice: {labelWords(item.ownerOutcome)}.
          {item.ownerMatchUnconfirmed ? ' Match not confirmed by automated evidence.' : ''}{' '}
          Recording a choice is separate from verified application.
        </p>
      ) : null}
      {item.automaticOutcome ? <p>Automatic policy selected this cover.</p> : null}
      {item.supersedesAutomatic ? (
        <p>This strictly better confirmed cover replaced the earlier automatic choice.</p>
      ) : null}
      {item.policyStatus ? <p>Policy outcome: {labelWords(item.policyStatus)}.</p> : null}
      {item.policyReasons.length ? (
        <ul className="diagnostic-limitations" aria-label="Cover policy limitations">
          {item.policyReasons.map((reason) => (
            <li key={reason}>{fieldLabel(reason)}</li>
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
  const value = item.selectedValue ?? item.previousValue
  return value ? displayValue(value.value) : 'No value is recorded.'
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
  const missingLineageCount = evidence.data?.pages[0]?.missingLineageCount ?? 0
  if (!data.current)
    return (
      <section className="diagnostic-state diagnostic-limitation" data-route-focus tabIndex={-1}>
        <h1>This diagnostic case is no longer current.</h1>
        <p>Library remains the authority for the current work and its available diagnostics.</p>
      </section>
    )
  return (
    <>
      <header className="diagnostic-heading" data-route-focus tabIndex={-1}>
        <h1>{data.title}</h1>
        <p className="diagnostic-authors">
          {data.authors.length ? data.authors.join(', ') : 'Author details unavailable'}
        </p>
        <p className="diagnostic-origin">{data.subtitle}</p>
      </header>
      <nav className="subviews" aria-label="Case diagnostics">
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
            <p>Retained observations that informed this work’s review.</p>
          </header>
          {evidence.isPending ? (
            <p role="status">Loading evidence…</p>
          ) : evidence.isError ? (
            <p className="diagnostic-read-failure" role="alert">
              Evidence could not be read safely.
            </p>
          ) : (
            <>
              {missingLineageCount ? (
                <p className="diagnostic-limitation" role="status">
                  {String(missingLineageCount)} referenced{' '}
                  {missingLineageCount === 1 ? 'observation is' : 'observations are'} no longer
                  available. The evidence below is the retained portion only.
                </p>
              ) : null}
              <RecordingIdentityBasis
                value={data.recordingIdentityBasis}
                scope={data.audiobookReviewScope}
              />
              {data.mediaHealth.length ? <MediaHealth observations={data.mediaHealth} /> : null}
              <div className="diagnostic-list evidence-fields">
                {data.fields.map((field) => {
                  const fieldObservations = observations.filter((item) =>
                    item.destinations.includes(field.destination),
                  )
                  return (
                    <section className="diagnostic-entry evidence-field" key={field.destination}>
                      <h3>{fieldLabel(field.destination)}</h3>
                      {field.automaticWithheldReason ? (
                        <p className="automatic-withheld-reason">{field.automaticWithheldReason}</p>
                      ) : null}
                      {fieldObservations.length ? (
                        <ul className="evidence-values">
                          {fieldObservations.map((item, index) => (
                            <li key={`${item.source}-${String(index)}-${item.observation}`}>
                              <p>{item.observation}</p>
                              <small>
                                {item.source} · {evidenceRoleLabel(item.role)}
                              </small>
                            </li>
                          ))}
                        </ul>
                      ) : evidence.hasNextPage ? (
                        <p className="diagnostic-complete">
                          More retained observations may load below.
                        </p>
                      ) : (
                        <p className="diagnostic-complete">
                          No retained observations for this field.
                        </p>
                      )}
                    </section>
                  )
                })}
              </div>
              {evidence.hasNextPage ? (
                <button
                  className="diagnostic-load-more"
                  disabled={evidence.isFetchingNextPage}
                  onClick={() => void evidence.fetchNextPage()}
                >
                  Load more evidence
                </button>
              ) : (
                <p className="diagnostic-complete">All retained evidence is shown.</p>
              )}
              <details className="source-ledger">
                <summary>Sources</summary>
                {sourceLedger.length ? (
                  <ul>
                    {sourceLedger.map((source, index) => (
                      <li key={`${source.provider}-${source.laneKind}-${String(index)}`}>
                        <strong>{source.provider}</strong>
                        <span>{fieldLabel(source.laneKind)}</span>
                        <b>{labelWords(source.status)}</b>
                        <p>{source.reason}</p>
                        <small>
                          {String(source.providerRequests)} bounded{' '}
                          {source.providerRequests === 1 ? 'request' : 'requests'}
                        </small>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p>No provider outcomes were retained for this work.</p>
                )}
              </details>
            </>
          )}
        </section>
      ) : (
        <section className="diagnostic-sheet" aria-label="Current-case history">
          <header>
            <h2>History</h2>
            <p>Durable owner and automatic outcomes retained for this work.</p>
          </header>
          {history.isPending ? (
            <p role="status">Loading history…</p>
          ) : history.isError ? (
            <p className="diagnostic-read-failure" role="alert">
              History could not be read safely.
            </p>
          ) : history.data.items.length ? (
            <div className="diagnostic-list">
              {history.data.items.map((item, index) => (
                <div key={index}>
                  {item.occurredAt === null ? (
                    <small>Time unavailable — lifecycle checkpoint only</small>
                  ) : (
                    <time dateTime={new Date(item.occurredAt * 1000).toISOString()}>
                      {new Date(item.occurredAt * 1000).toLocaleString()}
                    </time>
                  )}
                  <HistoryItem item={item} />
                </div>
              ))}
            </div>
          ) : (
            <p>No durable history has been retained for this work.</p>
          )}
        </section>
      )}
    </>
  )
}
