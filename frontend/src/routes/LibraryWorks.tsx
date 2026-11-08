import { useLayoutEffect, useRef } from 'react'
import type { ComponentPropsWithRef, RefObject, SyntheticEvent } from 'react'
import { Link, createLink } from '@tanstack/react-router'

import { BuildMismatchError } from '../api/http'
import type {
  LibraryLens,
  LibrarySearch,
  LibraryView,
  LibraryWorkView,
  ProviderOutcomeView,
  WorkSummaryState,
} from '../api/http'
import { BookMark } from '../components/BookMark'
import { MediaHealth } from '../components/MediaHealth'
import { StaleBuildRead } from '../components/StaleBuildRead'
import { retryInPlace } from '../focusAfterRetry'
import { StatusPill } from '../components/StatusPill'
import { mediaKindLabels, sourceStatusLabels } from '../labels'
import type { PreparationPresentation } from '../libraryPreparation'
import { authorLine, RESTART_SENTENCE, reportEvidenceUrl, workCount } from '../libraryWork'
import { PreparationSummary } from './PreparationDetail'

const lensLabels = {
  all: 'All',
  needs_attention: 'Needs attention',
  no_attention_needed: 'No attention needed',
  not_matched: 'Not matched',
  preparation_failed: 'Could not finish',
} satisfies Record<LibraryLens, string>
// The masthead's Library link is the current page; the active lens is the
// current item within it, so it says `true` rather than a second `page` (#1530).
const LensLink = createLink((props: ComponentPropsWithRef<'a'>) => (
  <a {...props} aria-current={props['aria-current'] ? 'true' : undefined} />
))
// A search under a lens counts that lens against its whole-library total; the
// lens link names it visually and the status names it to screen readers (#1425).
const lensMatching = {
  all: ['all', undefined],
  needs_attention: ['attention', 'needing attention'],
  no_attention_needed: ['clear', 'needing no attention'],
  not_matched: ['notMatched', 'not matched'],
  preparation_failed: ['failed', 'that could not finish'],
} as const
// Match the persisted Library ordering; recovery eligibility is a separate contract.
const limitingProviderStatuses = new Set([
  'authentication',
  'authorization',
  'rate_limited',
  'unavailable',
  'incomplete',
  'malformed',
  'oversized',
  'candidate_limit_exceeded',
  'query_limit_exceeded',
])
// Why no provider matched a work, in the row's status slot (#1407).
const notMatchedReasons: Partial<Record<WorkSummaryState, string>> = {
  not_matched_refused: 'Found, but not this work',
  not_matched_unavailable_scheduled: 'Sources unavailable, retry scheduled',
  not_matched_unavailable: 'Sources unavailable',
  not_matched_adapter_fault: 'Failed in Mechanical Librarian',
  not_matched_no_match: 'No source matched',
}
// A failed preparation's group shares its lens's name, so a "needs attention"
// heading never sits outside the Needs attention lens (#1413).
const providerGroup = (work: LibraryWorkView) =>
  work.preparation
    ? 'Could not finish'
    : work.attention === 'not_matched'
      ? 'Not matched'
      : work.attentionCount === 0
        ? 'No attention needed'
        : work.preparationOutcomes.some(
              // A cover-only lane never limits a work (#1779).
              (outcome) => !outcome.coverOnly && limitingProviderStatuses.has(outcome.status),
            )
          ? 'Some lookups gave no usable answer'
          : 'Every lookup finished'
const providerSummary = (outcomes: readonly ProviderOutcomeView[]) => {
  const counts = new Map<string, number>()
  // A cover-only lane says nothing of whether the work matched (#1779).
  for (const outcome of outcomes.filter((value) => !value.coverOnly)) {
    const label = sourceStatusLabels[outcome.status]
    counts.set(label, (counts.get(label) ?? 0) + 1)
  }
  return [...counts]
    .map(([label, count]) => (count > 1 ? `${String(count)} sources: ${label}` : label))
    .join(' · ')
}

// The lens links, in one row that scrolls sideways.
const LibraryLenses = ({
  search,
  lensIntent,
}: {
  search: LibrarySearch
  lensIntent: (lens: LibrarySearch['lens']) => void
}) => {
  const lensRow = useRef<HTMLElement>(null)
  // The lenses scroll sideways in one row. An edge with more lenses beyond it
  // fades, so a row that happens to end between two lenses still shows it
  // continues. The current lens is brought into the row's view, clear of the
  // fade, without moving the page: on load, on a lens change, and when the row
  // or its lenses change size, as with enlarged text.
  useLayoutEffect(() => {
    const row = lensRow.current
    if (!row) return
    const mark = () => {
      const start = row.scrollLeft > 1
      const end = row.scrollLeft + row.clientWidth < row.scrollWidth - 1
      if (start || end) row.dataset.more = start && end ? 'both' : start ? 'start' : 'end'
      else delete row.dataset.more
    }
    // The first and last lenses scroll the row to its end, so no fade covers
    // them; any other lens is brought clear of both fades.
    const reveal = (lens: Element | null) => {
      if (lens instanceof HTMLElement) {
        let left = row.scrollLeft
        if (lens === row.firstElementChild) left = 0
        else if (lens === row.lastElementChild) left = row.scrollWidth - row.clientWidth
        else {
          const fade = parseFloat(getComputedStyle(row).scrollPaddingInlineStart) || 0
          const rowBox = row.getBoundingClientRect()
          const box = lens.getBoundingClientRect()
          // A lens too wide to clear both fades is centred, or starts at the
          // row's edge when even the row is narrower, so its label's start shows.
          if (box.width > rowBox.width - 2 * fade)
            left +=
              box.width > rowBox.width
                ? box.left - rowBox.left
                : box.left - rowBox.left - (rowBox.width - box.width) / 2
          else if (box.left < rowBox.left + fade) left -= rowBox.left + fade - box.left
          else if (box.right > rowBox.right - fade) left += box.right - (rowBox.right - fade)
        }
        if (Math.abs(left - row.scrollLeft) > 0.5) row.scrollLeft = left
      }
      mark()
    }
    const revealCurrent = () => reveal(row.querySelector('[aria-current]'))
    // Focus leaves a partly shown lens where it is, so Tab reveals it too.
    const revealFocused = (event: FocusEvent) => reveal(event.target as Element | null)
    revealCurrent()
    row.addEventListener('scroll', mark, { passive: true })
    row.addEventListener('focusin', revealFocused)
    const resized = typeof ResizeObserver === 'function' ? new ResizeObserver(revealCurrent) : null
    for (const node of [row, ...row.children]) resized?.observe(node)
    return () => {
      row.removeEventListener('scroll', mark)
      row.removeEventListener('focusin', revealFocused)
      resized?.disconnect()
    }
  }, [search.lens])
  return (
    <nav className="lenses" aria-label="Library lenses" ref={lensRow}>
      {(
        [
          'all',
          'needs_attention',
          'no_attention_needed',
          'not_matched',
          'preparation_failed',
        ] as const
      ).map((lens) => (
        <LensLink
          key={lens}
          to="/library"
          search={{
            lens,
            ...(search.q ? { q: search.q } : {}),
            ...(search.preview ? { preview: search.preview } : {}),
            ...(search.work ? { work: search.work } : {}),
            ...(search.field ? { field: search.field } : {}),
            ...(search.preparation ? { preparation: search.preparation } : {}),
          }}
          preload="intent"
          onPointerEnter={() => lensIntent(lens)}
          onFocus={() => lensIntent(lens)}
          activeProps={{ 'aria-current': 'page' }}
        >
          {lensLabels[lens]}
        </LensLink>
      ))}
    </nav>
  )
}

// The works count beside the heading, with the lens and search it counts.
const WorksCount = ({
  search,
  data,
  failed,
  retained,
  summaryCounts,
  summaryUnavailable,
}: {
  search: LibrarySearch
  data: LibraryView | undefined
  failed: boolean
  retained: boolean
  summaryCounts: LibraryView['counts'] | undefined
  summaryUnavailable: boolean
}) => {
  const [lensCount, lensWords] = lensMatching[search.lens]
  const lensTotal = summaryCounts?.[lensCount]
  const summaryNotMatched = summaryCounts?.notMatched
  const summaryFailed = summaryCounts?.failed
  return (
    <span role="status" aria-live="polite" aria-busy={retained || undefined}>
      {data ? (
        search.q ? (
          <>
            {lensTotal === undefined
              ? `Found ${workCount(data.counts[lensCount])}`
              : `Found ${String(data.counts[lensCount])} of ${workCount(lensTotal)}`}
            {lensWords === undefined ? null : <span className="sr-only">{` ${lensWords}`}</span>}
          </>
        ) : (
          workCount(data.counts.all) +
          // The works outside both decision lenses, named beside the
          // total in the masthead's words (#1407, #1413).
          // One placeholder while the summary loads, so the line
          // does not wrap and then jump when it arrives.
          (summaryNotMatched === undefined || summaryFailed === undefined
            ? summaryUnavailable
              ? ''
              : ' · …'
            : (
                [
                  [summaryNotMatched, 'not matched'],
                  [summaryFailed, 'could not finish'],
                ] as const
              )
                .map(([count, words]) => (count === 0 ? '' : ` · ${String(count)} ${words}`))
                .join(''))
        )
      ) : failed ? (
        'Count unavailable'
      ) : (
        'Loading count…'
      )}
    </span>
  )
}

// One work in the list, under its provider group's heading when it starts one.
const LibraryWorkRow = ({
  work,
  previousWork,
  lens,
  selectedWorkRow,
  retained,
  select,
  workIntent,
}: {
  work: LibraryWorkView
  previousWork: LibraryWorkView | undefined
  /** The lens these rows were read for. */
  lens: LibraryLens
  selectedWorkRow: boolean
  retained: boolean
  select: (work: LibraryWorkView) => Promise<void>
  workIntent: (work: LibraryWorkView) => void
}) => {
  const needsAttention = work.attentionCount > 0
  // A published report is not a failure. The row for a work
  // that stopped before producing one has nothing to open and
  // stays an article; a work whose evidence is addressable is a
  // successful entry the owner can read.
  const evidence = reportEvidenceUrl(work) !== null
  // Retained evidence is not by itself a clean outcome. A
  // completed preparation reports no terminal failure at all, so
  // its absence is what makes the recorded report the outcome; a
  // later real failure over the same retained report is still a
  // failure the owner has to see.
  const recorded = evidence && work.preparation === null
  // A terminal preparation failure outranks a retained report's
  // not-matched reason: it is the failure the owner must see (#1407).
  const status =
    (work.summary && !work.preparation ? notMatchedReasons[work.summary.state] : undefined) ??
    (recorded
      ? 'Recorded evidence available'
      : work.preparation
        ? 'Could not finish'
        : needsAttention
          ? work.attentionCount === 1
            ? '1 field needs attention'
            : `${String(work.attentionCount)} fields need attention`
          : 'No attention needed')
  const terminalFailure = work.preparation !== null && !work.reviewCaseId && !evidence
  const group = providerGroup(work)
  // Grouping follows the lens these rows were read for, not a retained URL's.
  const showGroup =
    lens !== 'no_attention_needed' &&
    lens !== 'not_matched' &&
    lens !== 'preparation_failed' &&
    (!previousWork || providerGroup(previousWork) !== group)
  const summary = providerSummary(work.preparationOutcomes)
  const summaryId = summary ? `provider-summary-${work.id}` : undefined
  const preparation = work.preparation
  const preparationId = preparation ? `preparation-${work.id}` : undefined
  const row = (
    <>
      <BookMark title={work.title} cover={work.cover} />
      <span className="library-work-copy">
        <strong className="library-work-title">{work.title}</strong>
        <span className="library-work-author">{authorLine(work)}</span>
        <span className="library-work-origin">
          {work.libraryDisplayName ?? work.library} · {mediaKindLabels[work.mediaKind]}
        </span>
        {summary ? (
          <span id={summaryId} className="library-provider-summary">
            {summary}
          </span>
        ) : null}
        <span className="library-work-status">
          <StatusPill
            tone={!recorded && (needsAttention || work.preparation) ? 'notice' : 'accent'}
          >
            {status}
          </StatusPill>
          {preparation ? (
            <span id={preparationId} className="terminal-preparation-copy">
              {preparation.reason} {preparation.recovery} Reference:{' '}
              <code>{preparation.diagnosticReference}</code>
            </span>
          ) : null}
        </span>
      </span>
    </>
  )
  return (
    <>
      {showGroup ? (
        <div className="library-provider-group">
          <h3>{group}</h3>
        </div>
      ) : null}
      {terminalFailure ? (
        <article
          key={work.id}
          className="library-work-row is-terminal"
          aria-label="Could not finish"
        >
          {row}
          {preparation?.mediaHealth.length ? (
            <MediaHealth observations={preparation.mediaHealth} />
          ) : null}
        </article>
      ) : (
        <>
          <button
            key={work.id}
            type="button"
            className={`library-work-row${selectedWorkRow ? ' active' : ''}`}
            aria-current={selectedWorkRow ? 'true' : undefined}
            aria-disabled={retained || undefined}
            aria-label={`${work.title}. ${authorLine(work)}. ${status}`}
            aria-describedby={[preparationId, summaryId].filter(Boolean).join(' ') || undefined}
            onClick={() => void select(work)}
            onPointerEnter={() => workIntent(work)}
            onFocus={() => workIntent(work)}
          >
            {row}
          </button>
          {/* A work whose latest preparation could not read a file still opens
              what it retained, so the row is a control and the section has to
              sit beside it rather than inside it: a section nested in a button
              is not an openable row, and the observation is the one thing the
              failed attempt proved. */}
          {preparation?.mediaHealth.length ? (
            <div className="library-work-row-aside">
              <MediaHealth observations={preparation.mediaHealth} />
            </div>
          ) : null}
        </>
      )}
    </>
  )
}

// Only the moves that exist are offered; with one page there is no pager.
const LibraryPager = ({
  data,
  retained,
  movePage,
  pageIntent,
}: {
  data: LibraryView
  retained: boolean
  movePage: (cursor: string | undefined) => void
  pageIntent: (cursor: string | undefined) => void
}) => (
  <div className="library-pager" aria-busy={retained || undefined}>
    {data.previousCursor ? (
      <button
        type="button"
        className="library-pager-previous"
        aria-disabled={retained || undefined}
        onClick={() => movePage(data.previousCursor)}
        onPointerEnter={() => pageIntent(data.previousCursor)}
        onFocus={() => pageIntent(data.previousCursor)}
      >
        ← <span className="library-pager-word">Previous</span>
      </button>
    ) : null}
    <span>Page {String(data.page)}</span>
    {data.nextCursor ? (
      <button
        type="button"
        className="library-pager-next"
        aria-disabled={retained || undefined}
        onClick={() => movePage(data.nextCursor)}
        onPointerEnter={() => pageIntent(data.nextCursor)}
        onFocus={() => pageIntent(data.nextCursor)}
      >
        <span className="library-pager-word">Next</span> →
      </button>
    ) : null}
  </div>
)

// The works read's loading line, its failure with Try again, or Reload once the
// tab is on an earlier build.
const WorksNotLoaded = ({
  failed,
  error,
  retry,
  worksHeading,
}: {
  failed: boolean
  error: unknown
  retry: () => Promise<unknown>
  /** Where focus goes when a Try again's read brings the works. */
  worksHeading: RefObject<HTMLHeadingElement | null>
}) => {
  const worksRetry = useRef<HTMLButtonElement>(null)
  const worksStatus = useRef<HTMLDivElement>(null)
  return (
    // The loading line, failure and Reload share this region, which
    // holds focus while a Try again reads.
    <div className="empty-copy" aria-busy={!failed} tabIndex={-1} ref={worksStatus}>
      {failed && error instanceof BuildMismatchError ? (
        <StaleBuildRead lead="Works were not loaded." ref={worksRetry} />
      ) : failed ? (
        <>
          <p role="alert">Works could not be loaded. {RESTART_SENTENCE}</p>
          <button
            type="button"
            ref={worksRetry}
            onClick={() =>
              retryInPlace(
                () => worksStatus.current,
                retry,
                // A read that failed again renders Try again or Reload;
                // one that succeeded shows the works under the heading.
                () => worksRetry.current ?? worksHeading.current,
              )
            }
          >
            Try again
          </button>
        </>
      ) : (
        <p role="status">Loading works…</p>
      )}
    </div>
  )
}

/**
 * Library's works column: the preparation summary, the count, the lenses, the
 * search, the works themselves with their empty and failed states, and the
 * pager. Library owns the reads, the selection and the search; the column
 * shows them and reports the owner's moves.
 */
export const LibraryWorks = ({
  search,
  data,
  works,
  retained,
  failed,
  error,
  retry,
  summaryCounts,
  summaryUnavailable,
  presentation,
  reconnecting,
  recoveryRequired,
  preparationSelected,
  onSelectPreparation,
  noLibraryConnected,
  selectedWork,
  queryText,
  runSearch,
  submitSearch,
  select,
  movePage,
  lensIntent,
  workIntent,
  pageIntent,
}: {
  search: LibrarySearch
  data: LibraryView | undefined
  works: readonly LibraryWorkView[]
  /** `data` is the previous key's result, shown busy until the URL's own read settles. */
  retained: boolean
  failed: boolean
  /** The works read's failure, which a build mismatch makes unrecoverable by another try. */
  error: unknown
  /** Reads the works again; settles when the read does. */
  retry: () => Promise<unknown>
  /** The whole library's counts, which carry no search (#1284). */
  summaryCounts: LibraryView['counts'] | undefined
  summaryUnavailable: boolean
  presentation: PreparationPresentation | null
  reconnecting: boolean
  recoveryRequired: boolean
  preparationSelected: boolean
  onSelectPreparation: (runId: string) => void
  noLibraryConnected: boolean
  selectedWork: LibraryWorkView | undefined
  queryText: string
  runSearch: (value: string) => void
  submitSearch: (event: SyntheticEvent<HTMLFormElement, SubmitEvent>) => void
  select: (work: LibraryWorkView) => Promise<void>
  movePage: (cursor: string | undefined) => void
  lensIntent: (lens: LibrarySearch['lens']) => void
  workIntent: (work: LibraryWorkView) => void
  pageIntent: (cursor: string | undefined) => void
}) => {
  const searchInput = useRef<HTMLInputElement>(null)
  const worksHeading = useRef<HTMLHeadingElement>(null)
  return (
    <section className="library-works" aria-labelledby="library-works-title">
      {presentation ? (
        <PreparationSummary
          presentation={presentation}
          reconnecting={reconnecting}
          recoveryRequired={recoveryRequired}
          selected={preparationSelected}
          onSelect={() => onSelectPreparation(presentation.run_id)}
        />
      ) : null}
      <div className="library-works-heading">
        <h2 id="library-works-title" tabIndex={-1} ref={worksHeading}>
          Works
        </h2>
        <WorksCount
          search={search}
          data={data}
          failed={failed}
          retained={retained}
          summaryCounts={summaryCounts}
          summaryUnavailable={summaryUnavailable}
        />
      </div>
      <LibraryLenses search={search} lensIntent={lensIntent} />
      <form className="search library-search" role="search" onSubmit={submitSearch}>
        <label className="sr-only" htmlFor="library-query">
          Search title, author, identifier, library, or format
        </label>
        <div>
          <input
            ref={searchInput}
            id="library-query"
            name="q"
            type="search"
            maxLength={128}
            value={queryText}
            placeholder="Search works…"
            onChange={(event) => runSearch(event.target.value)}
          />
          {queryText ? (
            <button
              type="button"
              aria-label="Clear search text"
              onClick={() => {
                runSearch('')
                searchInput.current?.focus()
              }}
            >
              <span aria-hidden="true">×</span>
            </button>
          ) : null}
        </div>
      </form>
      <div className="library-work-list" aria-busy={retained || undefined}>
        {!data ? (
          <WorksNotLoaded failed={failed} error={error} retry={retry} worksHeading={worksHeading} />
        ) : works.length === 0 ? (
          <div className="empty-copy">
            <p>
              {search.q ? (
                'No works match this search.'
              ) : data.lens !== 'all' ? (
                'No works here.'
              ) : noLibraryConnected ? (
                // Only a confirmed snapshot with no library names the way
                // to fill the list; with one, the first scan fills it.
                <>
                  No works yet. Add a library in{' '}
                  <Link to="/settings" search={{ group: 'libraries' }}>
                    Settings
                  </Link>
                  .
                </>
              ) : (
                'No works yet.'
              )}
            </p>
            {search.q ? (
              <button
                type="button"
                onClick={() => {
                  runSearch('')
                  searchInput.current?.focus()
                }}
              >
                Clear search
              </button>
            ) : null}
          </div>
        ) : (
          works.map((work, index) => (
            <LibraryWorkRow
              key={work.id}
              work={work}
              previousWork={works[index - 1]}
              lens={data.lens}
              selectedWorkRow={
                !preparationSelected &&
                (selectedWork
                  ? selectedWork.workId === work.workId
                  : work.reviewCaseId !== undefined && search.preview === work.reviewCaseId)
              }
              retained={retained}
              select={select}
              workIntent={workIntent}
            />
          ))
        )}
      </div>
      {data && (data.previousCursor || data.nextCursor) ? (
        <LibraryPager data={data} retained={retained} movePage={movePage} pageIntent={pageIntent} />
      ) : null}
    </section>
  )
}
