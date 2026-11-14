// The Library's preparation run: its row above the works and its detail in the
// workspace. Moved out of the Library route under the route-size rule (#1571).
import { Link } from '@tanstack/react-router'
import { useEffect, useRef, useState } from 'react'

import { BookMark } from '../components/BookMark'
import { CountPhrases } from '../components/CountPhrases'
import { RecordedTime } from '../components/RecordedTime'
import { preparationOutcomes } from '../labels'
import type { PreparationPresentation } from '../libraryPreparation'

// The run's works as they stand now, in the masthead's words (#1366). The
// latched passage is first-finish history and is labelled Prepared instead.
const runOutcomes = (presentation: PreparationPresentation) => preparationOutcomes(presentation.now)
// The one sentence under the heading: what the heading does not already say,
// or the run's counts where nothing else is left to say (#1765). A paused run
// may be an owner pause, a deferred library or a connection check, so the
// reason is left to the notice or control that owns it.
const preparationStateLine = (presentation: PreparationPresentation) => {
  switch (presentation.state) {
    case 'starting':
      return 'Waiting for the first book to begin'
    case 'running':
      return 'Prepared works can be reviewed while the rest of the run finishes.'
    case 'blocked':
      return 'Prepared works can still be reviewed.'
    case 'completed_clean':
    case 'completed_with_failures':
      // The heading says how it finished and the counts are below; this line
      // says when (#1765).
      return presentation.completed_at === null ? (
        'Finished'
      ) : (
        <>
          Finished{' '}
          {/*
           * The time re-reads itself each minute; inside the status line it
           * would be announced each time, so it is its own quiet region.
           */}
          <span aria-live="off">
            <RecordedTime seconds={presentation.completed_at} />
          </span>
        </>
      )
    case 'dismissed':
      return (
        <>
          Dismissed · <CountPhrases phrases={runOutcomes(presentation)} />
        </>
      )
  }
}
const preparationTriggerCopy: Record<
  PreparationPresentation['triggers'][number]['trigger'],
  string
> = {
  initial_setup: 'Started by setup',
  manual_scan: 'Started by Scan now',
  library_added: 'Started when a library was added',
  title_recovery: 'Title recovery requested',
  automatic_confirmed: 'Started when Automatic was turned on',
  matching_changed: 'Started when Book matching was saved',
}
// A request accepted once is just how the run began; a number there would be
// noise on every ordinary run. Repeats are the case worth counting, so only
// they carry one.
// Initial setup records one acceptance per library, which is not a repeated
// owner request, so it never carries a count.
const preparationRepeatedTriggerCopy: Record<
  Exclude<PreparationPresentation['triggers'][number]['trigger'], 'initial_setup'>,
  string
> = {
  manual_scan: 'Scan now used',
  library_added: 'Library addition requested',
  title_recovery: 'Title recovery requested',
  automatic_confirmed: 'Automatic turned on',
  matching_changed: 'Book matching saved',
}
const preparationTriggerLine = (trigger: PreparationPresentation['triggers'][number]) =>
  trigger.accepted_count > 1 && trigger.trigger !== 'initial_setup'
    ? `${preparationRepeatedTriggerCopy[trigger.trigger]} ${String(trigger.accepted_count)} times`
    : preparationTriggerCopy[trigger.trigger]
// The scope every count on this page reconciles to: one run, whose books are
// either ready, unable to finish, or still moving.
/**
 * The display name, bounded to what the search route and the backend accept.
 *
 * The two bounds count differently: the route's ``bounded`` (router.tsx) tests
 * ``value.length``, which is UTF-16 units, while the backend's `max_length=128`
 * counts characters. At most 128 units therefore satisfies both, and bounding
 * by characters does not -- 127 ASCII plus one astral character is 128
 * characters but 129 units, which the route drops entirely. So the slice is by
 * units, and a trailing high surrogate left behind by it is dropped rather than
 * sent as half a character.
 */
const boundedSearchName = (name: string) => {
  const bounded = name.slice(0, 128)
  const last = bounded.charCodeAt(bounded.length - 1)
  return last >= 0xd800 && last <= 0xdbff ? bounded.slice(0, -1) : bounded
}

// A separator never starts a line (#1013).
const preparationScope = (presentation: PreparationPresentation, recoveryRequired: boolean) => (
  <CountPhrases
    phrases={[
      ...runOutcomes(presentation),
      `${presentation.now ? String(presentation.now.in_progress) : '…'} ${recoveryRequired ? 'still to prepare' : 'in progress'}`,
    ]}
    separator={'\u00a0· '}
  />
)
const preparationPassage = [
  ['read', 'Files read'],
  ['searches_finished', 'Sources asked'],
  ['compared', 'Sources compared'],
  ['checked', 'Automatic choices checked'],
] as const

// The stage bars glide to each new count (#1516). The value moves, not a
// width, so the bar stays a native <progress> with no style attribute, which
// the page's `style-src 'self'` policy would refuse in markup.
const GLIDE_MS = 300
const reducedMotion = () =>
  document.documentElement.dataset.reducedMotion === 'always-reduced' ||
  (typeof window.matchMedia === 'function' &&
    window.matchMedia('(prefers-reduced-motion: reduce)').matches)

const GlidingProgress = ({ label, value, max }: { label: string; value: number; max: number }) => {
  const [shown, setShown] = useState(value)
  const shownValue = useRef(value)
  useEffect(() => {
    const from = shownValue.current
    if (from === value) return
    if (reducedMotion()) {
      shownValue.current = value
      setShown(value)
      return
    }
    let start: number | null = null
    let frame = requestAnimationFrame(function step(now) {
      start ??= now
      const progress = Math.min((now - start) / GLIDE_MS, 1)
      // Ease out: most of the distance early, settling onto the count.
      const next = progress === 1 ? value : from + (value - from) * (1 - (1 - progress) ** 3)
      shownValue.current = next
      setShown(next)
      if (progress < 1) frame = requestAnimationFrame(step)
    })
    // A count arriving mid-glide starts from where the bar is.
    return () => cancelAnimationFrame(frame)
  }, [value])
  // The label always states the real count, wherever the bar is.
  return <progress aria-label={label} max={max} value={shown} />
}

export const PreparationSummary = ({
  presentation,
  reconnecting,
  recoveryRequired,
  selected,
  onSelect,
}: {
  presentation: PreparationPresentation
  reconnecting: boolean
  recoveryRequired: boolean
  selected: boolean
  onSelect: () => void
}) => {
  const active = presentation.active_books.length + presentation.active_overflow
  let title: string
  // What follows the run's outcomes on the summary line.
  let rest: string
  switch (presentation.state) {
    case 'starting':
      title = 'Preparation is starting'
      rest = ` · ${String(active)} active`
      break
    case 'running':
      title = 'Preparing libraries'
      rest = ` · ${String(active)} active`
      break
    case 'blocked':
      title = 'Preparation paused'
      rest = ` · ${String(active)} active`
      break
    case 'completed_clean':
      title = 'Preparation complete'
      rest = ''
      break
    case 'completed_with_failures':
      title = 'Preparation completed with failures'
      rest = ''
      break
    case 'dismissed':
      title = 'Preparation dismissed'
      rest = ''
      break
  }
  if (recoveryRequired) {
    title = 'Preparation stopped'
    rest = ' · Service restart required'
  }
  return (
    <button
      type="button"
      className={`preparation-summary state-${presentation.state}${selected ? ' active' : ''}`}
      aria-current={selected ? 'true' : undefined}
      onClick={onSelect}
    >
      <span className="preparation-summary-mark" aria-hidden="true" />
      <span>
        <strong>{title}</strong>
        <small>
          {reconnecting && !recoveryRequired ? (
            'Reconnecting…'
          ) : (
            <>
              This run: <CountPhrases phrases={runOutcomes(presentation)} />
              {rest}
            </>
          )}
        </small>
      </span>
    </button>
  )
}

export const PreparationDetail = ({
  presentation,
  reconnecting,
  recoveryRequired,
  animateChange,
  dismissing,
  uncertain,
  onDismiss,
}: {
  presentation: PreparationPresentation
  reconnecting: boolean
  recoveryRequired: boolean
  animateChange: boolean
  dismissing: boolean
  uncertain: boolean
  onDismiss: () => void
}) => {
  const outcomeTotal = presentation.passage.ready + presentation.passage.failed
  // The heading names the state; the status line below it says the rest, once
  // (#1765).
  let heading: string
  switch (presentation.state) {
    case 'starting':
      heading = 'Preparation is starting'
      break
    case 'running':
      heading = 'Preparing libraries'
      break
    case 'blocked':
      heading = 'Preparation paused'
      break
    case 'completed_clean':
      heading = 'Preparation complete'
      break
    case 'completed_with_failures':
      heading = 'Preparation completed with failures'
      break
    case 'dismissed':
      heading = 'Preparation dismissed'
      break
  }
  if (recoveryRequired) heading = 'Preparation stopped'
  // A section with nothing in it is left out rather than captioned "none"
  // (#1765). Now preparing stays while the run is moving or stopped, because
  // nothing in flight is news then; Recent completions stays once a book has
  // finished or could not.
  const showActive =
    presentation.active_books.length > 0 ||
    recoveryRequired ||
    presentation.state === 'running' ||
    presentation.state === 'blocked'
  const showCompletions =
    presentation.recent_completions.length > 0 || presentation.passage.failed > 0
  return (
    <div className="preparation-detail">
      <header>
        <div>
          <h2 tabIndex={-1} data-route-focus="true">
            {heading}
          </h2>
        </div>
        <strong>
          {presentation.now ? String(presentation.now.ready) : '…'} ready to browse in this run
        </strong>
      </header>
      <p
        className={`preparation-run-state state-${presentation.state}${animateChange ? ' reported-change' : ''}`}
        role="status"
      >
        {recoveryRequired
          ? 'Restart the Mechanical Librarian service to resume preparation. Prepared works can still be reviewed.'
          : reconnecting
            ? 'Reconnecting… The progress shown is from before the connection dropped.'
            : preparationStateLine(presentation)}
      </p>
      <p className="preparation-provenance">
        {presentation.triggers.map(preparationTriggerLine).join(' · ')}
      </p>
      {showActive ? (
        <section aria-labelledby="now-preparing-title">
          <div className="preparation-section-heading">
            <h3 id="now-preparing-title">
              {recoveryRequired ? 'Waiting for restart' : 'Now preparing'}
            </h3>
            <span>
              {String(presentation.active_books.length + presentation.active_overflow)}{' '}
              {recoveryRequired ? 'waiting' : 'active'}
            </span>
          </div>
          {presentation.active_books.length ? (
            <ol className="preparation-active-books">
              {presentation.active_books.map((book, index) => (
                <li key={`${book.name}:${String(index)}`}>
                  <BookMark title={book.name} cover={undefined} />
                  <span>
                    <strong>{book.name}</strong>
                    <small>{book.stage}</small>
                  </span>
                </li>
              ))}
            </ol>
          ) : (
            <p>
              {recoveryRequired
                ? 'No book was in progress when preparation stopped.'
                : 'No book is being prepared right now.'}
            </p>
          )}
          {presentation.active_overflow ? (
            <p>
              {String(presentation.active_overflow)} more{' '}
              {recoveryRequired ? 'are waiting.' : 'are being prepared.'}
            </p>
          ) : null}
          {presentation.active_books.length ? (
            <small className="activity-backup-note">
              A book appears in Works once it is prepared.
            </small>
          ) : null}
        </section>
      ) : null}
      <section aria-labelledby="preparation-progress-title">
        <div className="preparation-section-heading">
          {/* How the stages count is said in the product page, not here (#1765). */}
          <h3 id="preparation-progress-title">Preparation progress</h3>
          {/*
           * A rate is only a fact while the run is moving. A finished, paused
           * or dismissed run has no current rate, and a reconnected page has
           * no current anything -- printing 0.0 works a minute there read as a
           * performance fault rather than as silence.
           */}
          {!recoveryRequired && !reconnecting && presentation.state === 'running' ? (
            <strong>
              {presentation.throughput_per_minute > 0
                ? `${presentation.throughput_per_minute.toFixed(1)} works a minute`
                : 'Nothing finished in the last five minutes'}
            </strong>
          ) : null}
        </div>
        {/* A new run's bars start where it is, not gliding from the last run's. */}
        <dl className="preparation-passage" key={presentation.run_id}>
          {preparationPassage.map(([field, label]) => (
            <div key={field}>
              <dt>{label}</dt>
              <dd>
                <GlidingProgress
                  label={`${label}: ${String(presentation.passage[field])} of ${String(presentation.total)}`}
                  max={Math.max(presentation.total, 1)}
                  value={presentation.passage[field]}
                />
                <strong>
                  {String(presentation.passage[field])} of {String(presentation.total)}
                </strong>
              </dd>
            </div>
          ))}
          <div className="preparation-outcomes">
            <dt>In this run</dt>
            <dd>
              <strong>{preparationScope(presentation, recoveryRequired)}</strong>
            </dd>
          </div>
        </dl>
      </section>
      {/*
       * An empty list in a populated detail is removed, not captioned (#1765).
       */}
      {showCompletions ? (
        <section aria-labelledby="recent-completions-title">
          <div className="preparation-section-heading">
            <h3 id="recent-completions-title">Recent completions</h3>
            <span>{String(outcomeTotal)} finished</span>
          </div>
          {presentation.recent_completions.length ? (
            <ul className="preparation-completions">
              {presentation.recent_completions.map((completion, index) => (
                // One line per book, so every row is the same height: the title
                // is the link, and the name shown is the only thing this row has
                // to search with (#1516).
                <li key={`${completion.name}:${String(completion.completed_at)}:${String(index)}`}>
                  <Link
                    to="/library"
                    search={{ lens: 'all', q: boundedSearchName(completion.name) }}
                  >
                    <span>{completion.name}</span>
                  </Link>
                  {/*
                   * First-finish history, so never the run count's phrase for
                   * books as they stand now (#1366); the owner's wording (#1516).
                   */}
                  <small className={`outcome-${completion.outcome}`}>
                    {completion.outcome === 'ready' ? 'Prepared' : "Couldn't finish"}
                  </small>
                </li>
              ))}
            </ul>
          ) : (
            <p>No book has finished yet.</p>
          )}
          {presentation.passage.failed > 0 ? (
            <small className="activity-backup-note">
              Books that could not finish are listed in Works.
            </small>
          ) : null}
        </section>
      ) : null}
      {presentation.dismissible ? (
        // A pending dismissal is aria-disabled, not disabled, so focus stays on it
        // while the command settles; recovery disables it outright.
        <button
          type="button"
          disabled={recoveryRequired}
          aria-disabled={dismissing || undefined}
          aria-busy={dismissing || undefined}
          onClick={() => {
            if (!dismissing) onDismiss()
          }}
        >
          {dismissing ? 'Dismissing…' : 'Dismiss preparation'}
        </button>
      ) : null}
      {uncertain ? (
        <p role="alert">
          No answer came back. Check whether the run was dismissed before dismissing it again.
        </p>
      ) : null}
    </div>
  )
}
