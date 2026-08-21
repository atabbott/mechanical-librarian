import { useInfiniteQuery, useQuery } from '@tanstack/react-query'
import { Link, useNavigate } from '@tanstack/react-router'

import type { CaseDetailView, EvidenceView, HistoryView } from '../api/http'
import { displayValue } from '../api/http'
import { fieldLabel, labelWords } from '../labels'
import { detailRoute, useReviewContext } from '../router'

function HistoryItem({ item }: { item: HistoryView['items'][number] }) {
  if (!('consideration' in item))
    return (
      <p>
        {fieldLabel(item.kind)} · {'destination' in item ? item.destination : ''}
      </p>
    )
  const policy = item.consideration.policy
  const recommendation = policy?.recommended_candidate_reference ?? null
  return (
    <article className="history-cover">
      <h3>Cover {labelWords(item.kind)}</h3>
      <p>
        Consideration {item.consideration_id}. Observer status:{' '}
        {labelWords(item.consideration.reviewer_status)}.{' '}
        {item.consideration.current ? 'Current.' : 'Historical; it grants no current authority.'}
      </p>
      <p>
        Owner outcome:{' '}
        {item.decision ? labelWords(item.decision.outcome) : 'No owner outcome on this event'}
        {item.decision?.selected_value
          ? ` · ${displayValue(item.decision.selected_value.value)}`
          : ''}
        .
      </p>
      <p>
        Policy {item.consideration.policy_reference}:{' '}
        {policy ? labelWords(policy.status) : 'observation unavailable'}; recommendation:{' '}
        {recommendation ?? 'none'}.
      </p>
      {policy?.reasons.length ? (
        <p>Policy reasons: {policy.reasons.map(fieldLabel).join(' · ')}</p>
      ) : null}
      <ul aria-label="Historical cover candidates">
        {item.consideration.candidates.map((candidate) => (
          <li key={candidate.reference}>
            <strong>{candidate.reference}</strong> ·{' '}
            {candidate.incumbent ? 'incumbent' : 'alternative'} · identity{' '}
            {labelWords(candidate.identity.outcome)} ·{' '}
            {candidate.observation
              ? `composition ${labelWords(candidate.observation.composition)}, medium ${labelWords(candidate.observation.medium)}, promotional text ${labelWords(candidate.observation.promotional_text)}, tie-in branding ${labelWords(candidate.observation.tie_in_branding)}`
              : 'visual observation unavailable'}
          </li>
        ))}
      </ul>
    </article>
  )
}

export default function DetailPage({ data }: { data: CaseDetailView }) {
  const { http } = useReviewContext()
  const search = detailRoute.useSearch()
  const { reviewCaseId } = detailRoute.useParams()
  const navigate = useNavigate({ from: '/review/$reviewCaseId' })
  const evidence = useInfiniteQuery({
    queryKey: ['review-case', reviewCaseId, 'evidence'],
    initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam, signal }) => http.getEvidence(reviewCaseId, pageParam, signal),
    getNextPageParam: (page: EvidenceView) => page.next_cursor ?? undefined,
    staleTime: 30_000,
    retry: false,
    enabled: search.subview === 'evidence',
  })
  const history = useQuery({
    queryKey: ['review-case', reviewCaseId, 'history'],
    queryFn: ({ signal }) => http.getHistory(reviewCaseId, signal),
    staleTime: 30_000,
    retry: false,
    enabled: search.subview === 'history',
  })
  const backSearch = {
    lens: 'all' as const,
    preview: reviewCaseId,
    ...(search.field ? { field: search.field } : {}),
  }
  const pages = evidence.data?.pages.flatMap((page) => page.items) ?? []
  return (
    <>
      <header className="page-heading" data-route-focus tabIndex={-1}>
        <div>
          <p className="eyebrow">Read-only case diagnostics</p>
          <h1>{data.title}</h1>
          <p>{data.authors.length ? data.authors.join(', ') : 'Author details unavailable'}</p>
        </div>
        <Link to="/library" search={backSearch}>
          Back to work
        </Link>
      </header>
      <nav className="subviews" aria-label="Case diagnostics">
        <button
          aria-current={search.subview === 'evidence'}
          onClick={() => void navigate({ search: { ...search, subview: 'evidence' } })}
        >
          Evidence
        </button>
        <button
          aria-current={search.subview === 'history'}
          onClick={() => void navigate({ search: { ...search, subview: 'history' } })}
        >
          History
        </button>
      </nav>
      {search.subview === 'evidence' ? (
        <section className="progressive-panel" aria-label="Evidence and source ledger">
          <h2>Evidence and source ledger</h2>
          {evidence.isPending ? (
            <p role="status">Loading evidence…</p>
          ) : evidence.isError ? (
            <>
              <p role="alert">Evidence could not be loaded.</p>
              <button onClick={() => void evidence.refetch()}>Try again</button>
            </>
          ) : (
            <>
              {pages.map((item) => (
                <article className="evidence-item" key={item.reference}>
                  <h3>{fieldLabel(item.kind)}</h3>
                  <pre>{item.fact}</pre>
                </article>
              ))}
              {evidence.hasNextPage ? (
                <button
                  disabled={evidence.isFetchingNextPage}
                  onClick={() => void evidence.fetchNextPage()}
                >
                  Load more evidence
                </button>
              ) : (
                <p>All referenced evidence is shown.</p>
              )}
            </>
          )}
        </section>
      ) : (
        <section className="progressive-panel" aria-label="Current-case history">
          <h2>Current-case history</h2>
          {history.isPending ? (
            <p role="status">Loading history…</p>
          ) : history.isError ? (
            <>
              <p role="alert">History could not be loaded.</p>
              <button onClick={() => void history.refetch()}>Try again</button>
            </>
          ) : (
            history.data.items.map((item, index) => <HistoryItem item={item} key={index} />)
          )}
        </section>
      )}
    </>
  )
}
