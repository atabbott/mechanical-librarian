import { Link } from '@tanstack/react-router'
import type { ReactNode } from 'react'

import type { LibraryCounts, LibraryLens } from '../api/http'

// Grouped as the owner reads a count of works: "1,840" (#1335).
const grouped = (value: number | undefined) =>
  value === undefined ? '…' : value.toLocaleString('en-US')

/**
 * Library's works in one line: "1,840 of 2,026 works settled · 174 need you",
 * then "· 3 could not finish" and "· 9 not matched" only when nonzero (#1335).
 * Settled is the No attention needed lens, so the parts add up to All.
 *
 * Linked, each count phrase opens its Library lens: the settled phrase opens
 * No attention needed. A phrase whose count is zero is plain text, since its
 * lens would be empty. A count not yet known reads as an ellipsis, never 0.
 * Each number keeps its first word on its line (`.count-pair`, as in
 * `CountPhrases`).
 */
export const LibraryProgressLine = ({
  counts,
  linked,
}: {
  counts: LibraryCounts | undefined
  linked: boolean
}) => {
  const phrase = (value: number | undefined, lens: LibraryLens, words: ReactNode): ReactNode =>
    linked && value !== undefined && value > 0 ? (
      <Link to="/library" search={{ lens }}>
        {words}
      </Link>
    ) : (
      words
    )
  const needYou = counts?.attention
  return (
    <span className="library-progress-line">
      {phrase(
        counts?.clear,
        'no_attention_needed',
        <>
          <span className="count-pair">{grouped(counts?.clear)} of</span>{' '}
          <span className="count-pair">
            {grouped(counts?.all)} {counts?.all === 1 ? 'work' : 'works'}
          </span>{' '}
          settled
        </>,
      )}
      {' · '}
      {phrase(
        needYou,
        'needs_attention',
        <>
          <span className="count-pair">
            {grouped(needYou)} {needYou === 1 ? 'needs' : 'need'}
          </span>{' '}
          you
        </>,
      )}
      {counts && counts.failed > 0 ? (
        <>
          {' · '}
          {phrase(
            counts.failed,
            'preparation_failed',
            <>
              <span className="count-pair">{grouped(counts.failed)} could</span> not finish
            </>,
          )}
        </>
      ) : null}
      {counts && counts.notMatched > 0 ? (
        <>
          {' · '}
          {phrase(
            counts.notMatched,
            'not_matched',
            <>
              <span className="count-pair">{grouped(counts.notMatched)} not</span> matched
            </>,
          )}
        </>
      ) : null}
    </span>
  )
}
