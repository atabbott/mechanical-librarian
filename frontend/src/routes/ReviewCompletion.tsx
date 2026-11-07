// The card shown when the selected work's review is complete: the recorded
// outcomes, and the way on to the next work that needs attention. Moved out of
// the Library route under the route-size rule (#1742); the page keeps the
// attention read, the completion record, the next work and the card's focus.
import type { InfiniteData, UseInfiniteQueryResult } from '@tanstack/react-query'
import type { RefObject } from 'react'

import { BuildMismatchError } from '../api/http'
import type { AttentionView } from '../api/http'
import { Button } from '../components/Button'
import { StaleBuildRead } from '../components/StaleBuildRead'
import { completionOutcome, decisionValue } from '../fieldDecision'
import type { ReviewField } from '../fieldDecision'
import { destinationLabel } from '../labels'

export const ReviewCompletion = ({
  fields,
  writesActive,
  completionRegion,
  attention,
  hasNextWork,
  searching,
  outsideSearch,
  inspect,
  reviewNextWork,
}: {
  /** The recorded field outcomes. */
  fields: readonly ReviewField[]
  writesActive: boolean
  completionRegion: RefObject<HTMLElement | null>
  attention: UseInfiniteQueryResult<InfiniteData<AttentionView>>
  hasNextWork: boolean
  /** Whether Library is showing a search. */
  searching: boolean
  /** Works needing a decision outside the search, once both counts are read. */
  outsideSearch: number | undefined
  inspect: (destination: string) => void
  reviewNextWork: () => void
}) => {
  const retryNextWork = () => {
    if (attention.isFetchNextPageError) void attention.fetchNextPage()
    else void attention.refetch()
  }
  return (
    <article className="review-complete" aria-live="polite" tabIndex={-1} ref={completionRegion}>
      <span className="review-complete-mark">✓</span>
      <div>
        <h3>Review complete</h3>
        <span>
          {String(fields.length)} recorded field outcome
          {fields.length === 1 ? '' : 's'}
          {writesActive ? ' — each began applying the moment you decided it.' : '.'}
        </span>
      </div>
      <ol>
        {fields.map((field) => (
          <li key={field.destination}>
            <button
              type="button"
              aria-label={
                field.destination === 'description' && decisionValue(field).length > 240
                  ? `Revisit description: ${completionOutcome(field)}`
                  : undefined
              }
              onClick={() => inspect(field.destination)}
            >
              <small>
                {destinationLabel(field.destination)} · {completionOutcome(field)}
              </small>
              <strong>{decisionValue(field)}</strong>
            </button>
          </li>
        ))}
      </ol>
      {writesActive ? <p>Every write is logged in Activity.</p> : null}
      <footer>
        {hasNextWork ? (
          <Button variant="primary" type="button" onClick={reviewNextWork}>
            Review next work →
          </Button>
        ) : attention.isPending || attention.isPlaceholderData || attention.isFetchingNextPage ? (
          <span>Looking for the next visible work…</span>
        ) : attention.isError && attention.error instanceof BuildMismatchError ? (
          <StaleBuildRead lead="The next attention work was not loaded." />
        ) : attention.isError ? (
          <>
            <span>The next attention work could not be loaded.</span>
            <Button variant="primary" type="button" onClick={retryNextWork}>
              Try finding next work again
            </Button>
          </>
        ) : !searching ? (
          <span>All visible review work is complete.</span>
        ) : (
          <span>
            All review work matching this search is complete.
            {outsideSearch === undefined
              ? null
              : outsideSearch > 0
                ? ` ${String(outsideSearch)} more ${outsideSearch === 1 ? 'work needs' : 'works need'} a decision outside this search.`
                : ' No other work needs a decision.'}
          </span>
        )}
      </footer>
    </article>
  )
}
