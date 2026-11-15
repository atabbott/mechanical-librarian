// Activity's Library progress card: Library's works settled and needing you,
// in total, per library and per field (#1335). It owns its read, its states
// and its hash handoff, so the Activity page only places it.
import { useRouterState } from '@tanstack/react-router'
import { useEffect, useRef } from 'react'

import { BuildMismatchError } from '../api/http'
import type { LibraryProgressView, ReviewHttp } from '../api/http'
import { LibraryProgressLine } from '../components/LibraryProgressLine'
import { StaleBuildRead } from '../components/StaleBuildRead'
import { retryInPlace } from '../focusAfterRetry'
import { destinationLabel } from '../labels'
import { queryWorkClass, useRequiredQuery as useQuery } from '../queryWork'

// The hash a link to the card carries: /activity#activity-library-progress.
const libraryProgressHash = 'activity-library-progress'
const headingId = 'activity-library-progress-heading'

export const ActivityLibraryProgress = ({ http }: { http: ReviewHttp }) => {
  // Under Library's key, so a live Library update refreshes it as it does
  // Library's own counts.
  const query = useQuery({
    queryKey: ['library', 'progress'],
    queryFn: (context) => http.getLibraryProgress(context.signal, queryWorkClass(context)),
    retry: false,
  })
  const hash = useRouterState({ select: (state) => state.location.hash })
  const section = useRef<HTMLElement>(null)
  // Try again, or Reload once the tab is on an earlier build.
  const retry = useRef<HTMLButtonElement>(null)
  const arrived = useRef(false)
  // A library with no works has nothing to count; a read not yet answered or
  // refused still shows where the counts will be.
  const shown = query.data === undefined || query.data.totals.all > 0
  useEffect(() => {
    if (hash !== libraryProgressHash) {
      arrived.current = false
      return
    }
    // Once per arrival, when the card has settled into its place.
    if (arrived.current || query.isPending || !shown) return
    arrived.current = true
    section.current?.focus({ preventScroll: true })
    section.current?.scrollIntoView({ block: 'nearest' })
  }, [hash, query.isPending, shown])
  if (!shown) return null
  return (
    <section
      id={libraryProgressHash}
      ref={section}
      tabIndex={-1}
      className="activity-library-progress"
      aria-labelledby={headingId}
    >
      <h2 id={headingId} tabIndex={-1}>
        Library progress
      </h2>
      {query.isPending ? (
        <>
          <p role="status">Counting works…</p>
          <p>
            <LibraryProgressLine counts={undefined} linked={false} />
          </p>
        </>
      ) : query.error instanceof BuildMismatchError ? (
        <StaleBuildRead lead="Library progress was not loaded." ref={retry} />
      ) : query.isError ? (
        <div role="alert">
          <p>Library progress could not be loaded.</p>
          <button
            type="button"
            ref={retry}
            onClick={() => {
              // The heading, which stays, holds focus while the counts read.
              const heading = () => document.getElementById(headingId)
              retryInPlace(
                heading,
                () => query.refetch(),
                (result) => (result.isSuccess ? heading() : retry.current),
              )
            }}
          >
            Try again
          </button>
        </div>
      ) : (
        <LibraryProgress view={query.data} />
      )}
    </section>
  )
}

const LibraryProgress = ({ view }: { view: LibraryProgressView }) => (
  <>
    <p>
      <LibraryProgressLine counts={view.totals} linked />
    </p>
    {/* Only the totals link: Library has no exact library or field slice. */}
    {view.libraries.length > 1 ? (
      <>
        <h3>By library</h3>
        <ul className="activity-library-progress-libraries">
          {view.libraries.map((library) => (
            <li key={`${library.connection}:${library.library}`}>
              <strong>{library.libraryDisplayName}</strong>{' '}
              <LibraryProgressLine counts={library.counts} linked={false} />
            </li>
          ))}
        </ul>
      </>
    ) : null}
    {view.fields.length > 0 ? (
      <>
        <h3>Fields that need you</h3>
        <ul className="activity-library-progress-fields">
          {view.fields.map((field) => (
            <li key={field.destination}>
              {destinationLabel(field.destination)}{' '}
              <span className="count-pair">
                {field.needYou.toLocaleString('en-US')} {field.needYou === 1 ? 'work' : 'works'}
              </span>
            </li>
          ))}
        </ul>
      </>
    ) : null}
  </>
)
