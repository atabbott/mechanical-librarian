// The card shown when the selected work's review is complete: the recorded
// outcomes, and the way on to the next work that needs attention. Moved out of
// the Library route under the route-size rule (#1742); the page keeps the
// attention read, the completion record, the next work and the card's focus.
import type { InfiniteData, UseInfiniteQueryResult } from '@tanstack/react-query'
import { useRef } from 'react'
import type { RefObject } from 'react'

import { BuildMismatchError } from '../api/http'
import type { AttentionView } from '../api/http'
import { Button } from '../components/Button'
import { StaleBuildRead } from '../components/StaleBuildRead'
import { completionOutcome, decisionValue } from '../fieldDecision'
import { focusAfterRetry } from '../focusAfterRetry'
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
  restartSentence,
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
  /** " If trying again does not help, restart…", or empty where the works column says it. */
  restartSentence: string
}) => {
  // Try finding next work again, or Reload once the tab is on an earlier build.
  const nextWorkRetry = useRef<HTMLButtonElement>(null)
  const retryNextWork = () => {
    const nextPage = attention.isFetchNextPageError
    // The next-page read shows its loading line, which removes this control:
    // the card holds focus until the outcome takes it.
    if (nextPage) completionRegion.current?.focus({ preventScroll: true })
    void (nextPage ? attention.fetchNextPage() : attention.refetch()).then(() =>
      focusAfterRetry(
        () =>
          completionRegion.current?.querySelector<HTMLElement>('[data-next-work]') ??
          nextWorkRetry.current ??
          completionRegion.current,
        () => completionRegion.current,
      ),
    )
  }
  return (
    <article className="review-complete" aria-live="polite" tabIndex={-1} ref={completionRegion}>
      <span className="review-complete-mark">✓</span>
      <div>
        <h3>Review complete</h3>
        <span>
          {`${String(fields.length)} ${fields.length === 1 ? 'field' : 'fields'} recorded.${writesActive ? ' Each started applying when you chose it.' : ''}`}
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
          <Button variant="primary" type="button" data-next-work onClick={reviewNextWork}>
            Review next work →
          </Button>
        ) : attention.isPending || attention.isPlaceholderData || attention.isFetchingNextPage ? (
          <span>Finding the next work…</span>
        ) : attention.isError && attention.error instanceof BuildMismatchError ? (
          <StaleBuildRead lead="The next work was not loaded." ref={nextWorkRetry} />
        ) : attention.isError ? (
          <>
            <span>{`The next work could not be loaded.${restartSentence}`}</span>
            <button
              ref={nextWorkRetry}
              className="button-primary"
              type="button"
              onClick={retryNextWork}
            >
              Try again
            </button>
          </>
        ) : !searching ? (
          <span>No other work needs a decision.</span>
        ) : (
          <span>
            {outsideSearch === 0
              ? 'No other work needs a decision.'
              : 'No other work matching this search needs a decision.'}
            {outsideSearch === undefined || outsideSearch === 0
              ? null
              : ` ${String(outsideSearch)} more ${outsideSearch === 1 ? 'work needs' : 'works need'} one outside this search.`}
          </span>
        )}
      </footer>
    </article>
  )
}
