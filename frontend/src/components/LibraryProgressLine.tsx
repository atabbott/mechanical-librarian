import { Link } from '@tanstack/react-router'
import type { ReactNode } from 'react'

import type { LibraryCounts, LibraryLens } from '../api/http'

// Grouped as the owner reads a count of works: "1,840" (#1335).
const grouped = (value: number) => value.toLocaleString('en-US')

/**
 * Library's works in one line: "1,840 of 2,026 works settled · 174 need you",
 * then "· 3 could not finish" and "· 9 not matched" only when nonzero (#1335).
 * Settled is the No attention needed lens, so the parts add up to All.
 *
 * Linked, each nonzero number opens its Library lens; a zero is plain text,
 * since its lens would be empty. A count not yet known reads as an ellipsis,
 * never 0. Each number keeps its first word on its line (`.count-pair`, as in
 * `CountPhrases`).
 */
export const LibraryProgressLine = ({
  counts,
  linked,
}: {
  counts: LibraryCounts | undefined
  linked: boolean
}) => {
  const number = (value: number | undefined, lens: LibraryLens): ReactNode =>
    value === undefined ? (
      '…'
    ) : linked && value > 0 ? (
      <Link to="/library" search={{ lens }}>
        {grouped(value)}
      </Link>
    ) : (
      grouped(value)
    )
  const needYou = counts?.attention
  return (
    <span className="library-progress-line">
      <span className="count-pair">{number(counts?.clear, 'no_attention_needed')} of</span>{' '}
      <span className="count-pair">
        {number(counts?.all, 'all')} {counts?.all === 1 ? 'work' : 'works'}
      </span>{' '}
      settled ·{' '}
      <span className="count-pair">
        {number(needYou, 'needs_attention')} {needYou === 1 ? 'needs' : 'need'}
      </span>{' '}
      you
      {counts && counts.failed > 0 ? (
        <>
          {' · '}
          <span className="count-pair">
            {number(counts.failed, 'preparation_failed')} could
          </span>{' '}
          not finish
        </>
      ) : null}
      {counts && counts.notMatched > 0 ? (
        <>
          {' · '}
          <span className="count-pair">{number(counts.notMatched, 'not_matched')} not</span> matched
        </>
      ) : null}
    </span>
  )
}
