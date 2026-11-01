// The Library's preparation run: its row above the works and its detail in the
// workspace. Moved out of the Library route under the route-size rule (#1571).
import { Link } from '@tanstack/react-router'

import { BookMark } from '../components/BookMark'
import { CountPhrases } from '../components/CountPhrases'
import { preparationOutcomes } from '../labels'
import type { PreparationPresentation } from '../libraryPreparation'

// The run's works as they stand now, in the masthead's words (#1366). The
// latched passage is first-finish history and is labelled Prepared instead.
const runOutcomes = (presentation: PreparationPresentation) =>
  preparationOutcomes(presentation.now ?? null)
// What the run is doing now, said in the counts the owner is waiting on rather
// than in the store's state name. A terminal run states its own outcome here
// because that is the line a reader looks at first.
const preparationStateLine = (presentation: PreparationPresentation) => {
  switch (presentation.state) {
    case 'starting':
      return 'Preparation is starting'
    case 'running':
      return 'Preparation continues'
    case 'blocked':
      return 'Paused until the reported connection issue is resolved'
    case 'completed_clean':
    case 'completed_with_failures':
      return (
        <>
          Finished · <CountPhrases phrases={runOutcomes(presentation)} />
        </>
      )
    case 'dismissed':
      return 'Preparation dismissed'
  }
}
const preparationTriggerCopy: Record<
  PreparationPresentation['triggers'][number]['trigger'],
  string
> = {
  initial_setup: 'Started by initial setup',
  manual_scan: 'Started by an owner scan',
  library_added: 'Started when a library was added',
  title_recovery: 'Title recovery requested',
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
  manual_scan: 'Owner scan requested',
  library_added: 'Library addition requested',
  title_recovery: 'Title recovery requested',
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
      `${presentation.now ? String(presentation.now.in_progress) : '…'} ${recoveryRequired ? 'retained' : 'in progress'}`,
    ]}
    separator={'\u00a0· '}
  />
)
const preparationPassage = [
  ['read', 'Read from library'],
  ['searches_finished', 'Searches finished'],
  ['compared', 'Candidates compared'],
  ['checked', 'Metadata checked'],
] as const

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
      title = 'Preparing your library'
      rest = ` · ${String(active)} active`
      break
    case 'blocked':
      title = 'Preparation needs attention'
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
            'Reconnecting · last confirmed progress shown'
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
  let heading: string
  let introduction: string
  switch (presentation.state) {
    case 'starting':
      heading = 'Preparation is starting'
      introduction = 'The run is open and waiting for preparation work to begin or resume.'
      break
    case 'running':
      heading = 'Preparing your library'
      introduction = 'Prepared works remain selectable while the rest of this run finishes.'
      break
    case 'blocked':
      heading = 'Preparation needs attention'
      introduction = 'Preparation is paused until the reported issue is resolved.'
      break
    case 'completed_clean':
      heading = 'Preparation complete'
      introduction = 'This run finished without a reported failure.'
      break
    case 'completed_with_failures':
      heading = 'Preparation completed with failures'
      introduction =
        'This run finished with reported failures; prepared books remain available and the failed outcomes stay visible.'
      break
    case 'dismissed':
      heading = 'Preparation dismissed'
      introduction = 'This preparation record has been dismissed.'
      break
  }
  if (recoveryRequired) {
    heading = 'Preparation stopped'
    introduction =
      'Restart the Mechanical Librarian service to resume preparation. Refreshing this page or scanning again cannot recover it. Prepared books remain available.'
  }
  return (
    <div className="preparation-detail">
      <header>
        <div>
          <h2 tabIndex={-1} data-route-focus="true">
            {heading}
          </h2>
          <p>{introduction}</p>
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
          ? 'Service restart required · last saved progress shown'
          : reconnecting
            ? 'Reconnecting · showing the last confirmed state'
            : preparationStateLine(presentation)}
      </p>
      <p className="preparation-provenance">
        {presentation.triggers.map(preparationTriggerLine).join(' · ')}
      </p>
      <section aria-labelledby="now-preparing-title">
        <div className="preparation-section-heading">
          <h3 id="now-preparing-title">{recoveryRequired ? 'Retained work' : 'Now preparing'}</h3>
          <span>
            {String(presentation.active_books.length + presentation.active_overflow)}{' '}
            {recoveryRequired ? 'retained' : 'active'}
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
              ? 'No work is retained at the last confirmed refresh.'
              : 'No book is active at the last confirmed refresh.'}
          </p>
        )}
        {presentation.active_overflow ? (
          <p>
            {String(presentation.active_overflow)} more {recoveryRequired ? 'retained' : 'active'}{' '}
            books follow in this run.
          </p>
        ) : null}
        {presentation.active_books.length ? (
          <small className="activity-backup-note">
            A book appears in Works once it is prepared.
          </small>
        ) : null}
      </section>
      <section aria-labelledby="preparation-progress-title">
        <div className="preparation-section-heading">
          <div>
            <h3 id="preparation-progress-title">Preparation progress</h3>
            <p>
              Each stage counts the books that first reached it in this run, and a book that could
              not finish stops at its last stage; In this run counts them as they stand now.
            </p>
          </div>
          {/*
           * A rate is only a fact while the run is moving. A finished, paused
           * or dismissed run has no current rate, and a reconnected page has
           * no current anything -- printing 0.0 books/min there read as a
           * performance fault rather than as silence.
           */}
          {!recoveryRequired &&
          !reconnecting &&
          (presentation.state === 'starting' || presentation.state === 'running') ? (
            <strong>
              {presentation.throughput_per_minute > 0
                ? `${presentation.throughput_per_minute.toFixed(1)} books/min over the last five minutes`
                : 'No book finished in the last five minutes'}
            </strong>
          ) : null}
        </div>
        <dl className="preparation-passage">
          {preparationPassage.map(([field, label]) => (
            <div key={field}>
              <dt>{label}</dt>
              <dd>
                <progress
                  aria-label={`${label}: ${String(presentation.passage[field])} of ${String(presentation.total)}`}
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
      <section aria-labelledby="recent-completions-title">
        <div className="preparation-section-heading">
          <h3 id="recent-completions-title">Recent completions</h3>
          <span>{String(outcomeTotal)} outcomes</span>
        </div>
        {presentation.recent_completions.length ? (
          <ul className="preparation-completions">
            {presentation.recent_completions.map((completion, index) => (
              <li key={`${completion.name}:${String(completion.completed_at)}:${String(index)}`}>
                <span>
                  {completion.name}{' '}
                  {/*
                   * The link sits inside the name so it wraps with it, and the
                   * name shown is the only thing this row has to search with.
                   */}
                  <Link
                    to="/library"
                    search={{ lens: 'all', q: boundedSearchName(completion.name) }}
                  >
                    Find in Works
                  </Link>
                </span>
                <strong>
                  {completion.outcome === 'ready' ? 'Prepared' : 'Stopped before finishing'}
                </strong>
              </li>
            ))}
          </ul>
        ) : (
          <p>No terminal book has been reported yet.</p>
        )}
        {presentation.passage.failed > 0 ? (
          <small className="activity-backup-note">
            A book that could not finish is listed in Works under its library title, with its stage,
            reason, next step and reference. If its library record has since changed, it is listed
            as Preparation unavailable.
          </small>
        ) : null}
      </section>
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
          Dismissal could not be confirmed. This preparation is still shown as current; dismiss it
          again if you still want to.
        </p>
      ) : null}
    </div>
  )
}
