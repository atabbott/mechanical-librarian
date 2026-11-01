import { useMutation } from '@tanstack/react-query'
import { useRef, useState, type ReactNode } from 'react'
import type { ActivityEventView, ActivityTransitionView } from '../api/http'
import { categoryLabel, stateLabel } from '../activityLabels'
import { RecordedTime } from '../components/RecordedTime'
import { formatWhen } from '../dates'
import { languageName } from '../labels'
import { queryWorkClass, useRequiredQuery as useQuery } from '../queryWork'
import { useReviewContext } from '../router'
// The Activity ledger's rows: one durable record's row, its recorded
// transitions, the page jump, and the words they read in.
// A row sits under its day's heading, so it states only its time.
export function RowTime({ seconds }: { seconds: number | null }) {
  return seconds === null ? '—' : <RecordedTime seconds={seconds} dayShown />
}
export function PageJump({
  page,
  pageCount,
  shownFor,
  onCommit,
  onEdit,
}: {
  page: number
  pageCount: number
  // The page and search the value was last reset for.
  shownFor: string
  onCommit: (page: number, focusKey: string) => void
  onEdit: () => void
}) {
  const [value, setValue] = useState(String(page))
  // A changed page or search resets the value in place. Remounting the input
  // instead dropped its focus to the document when the change landed after the
  // owner had moved into it (#1554).
  const [resetFor, setResetFor] = useState(shownFor)
  if (resetFor !== shownFor) {
    setResetFor(shownFor)
    setValue(String(page))
  }
  const cancelCommit = useRef(false)
  const commit = () => {
    if (cancelCommit.current) {
      cancelCommit.current = false
      setValue(String(page))
      return
    }
    const requested = Number.parseInt(value, 10)
    if (!Number.isFinite(requested)) {
      setValue(String(page))
      return
    }
    const clamped = Math.max(1, Math.min(pageCount, requested))
    setValue(String(clamped))
    if (clamped !== page) onCommit(clamped, 'page-jump')
  }
  return (
    <label className="cm-activity-page-jump">
      <span>Page</span>
      <input
        data-activity-focus="page-jump"
        aria-label="Activity page number"
        inputMode="numeric"
        type="number"
        min="1"
        max={pageCount}
        value={value}
        onChange={(event) => {
          cancelCommit.current = false
          onEdit()
          setValue(event.target.value)
        }}
        onBlur={commit}
        onKeyDown={(event) => {
          if (event.key === 'Enter') event.currentTarget.blur()
          if (event.key === 'Escape') {
            event.preventDefault()
            cancelCommit.current = true
            setValue(String(page))
          }
        }}
      />
      <span>of {pageCount}</span>
    </label>
  )
}
function ActivityTransitions({
  eventId,
  advice,
}: {
  eventId: string
  advice: 'description' | 'cover' | undefined
}) {
  const { http } = useReviewContext()
  const [extraPages, setExtraPages] = useState<ActivityTransitionView[][]>([])
  const [hasMore, setHasMore] = useState(false)
  const query = useQuery({
    queryKey: ['activity-transitions', eventId],
    queryFn: (queryContext) =>
      http.getActivityTransitions(
        eventId,
        undefined,
        queryContext.signal,
        queryWorkClass(queryContext),
      ),
    retry: false,
  })
  const more = useMutation({
    mutationFn: (afterSequence: number) => http.getActivityTransitions(eventId, afterSequence),
    onSuccess: (page) => {
      setExtraPages((current) => [...current, [...page.events]])
      setHasMore(page.has_more)
    },
  })
  if (query.isLoading) return <p>Loading recorded transitions…</p>
  if (query.isError)
    return (
      <p role="alert">
        Recorded transitions could not be loaded.{' '}
        <button type="button" onClick={() => void query.refetch()}>
          Try again
        </button>
      </p>
    )
  const events = [...(query.data?.events ?? []), ...extraPages.flat()]
  const moreAvailable = extraPages.length ? hasMore : Boolean(query.data?.has_more)
  if (!events.length) return null
  return (
    <ol className="cm-activity-transitions" aria-label="Recorded lifecycle transitions">
      {events.map((transition) => (
        // One flow; the no-break space keeps each separator off a line start.
        <li key={transition.event_id}>
          <time>
            <RecordedTime seconds={transition.occurred_at} />
          </time>
          &nbsp;· {stateLabel(transition.state, advice, transition.failure)}
        </li>
      ))}
      {moreAvailable ? (
        <li>
          <button
            type="button"
            onClick={() => more.mutate(events[events.length - 1]?.sequence ?? 1)}
            disabled={more.isPending}
          >
            {more.isPending ? 'Loading transitions…' : 'Load more transitions'}
          </button>
          {more.isError ? (
            <p role="alert">
              More recorded transitions could not be loaded.{' '}
              <button
                type="button"
                onClick={() => more.mutate(events[events.length - 1]?.sequence ?? 1)}
              >
                Try again
              </button>
            </p>
          ) : null}
        </li>
      ) : null}
    </ol>
  )
}
// A language row's change and values come from the server as exact tags;
// they read by name, as Library and Policy name them, `English (en)`, inside
// the server's own words (#1490).
const languages = (value: string) => value.split(', ').map(languageName).join(', ')
const languageChange = (text: string) =>
  text
    .replace(
      /^Set Language from (.+) to (.+)$/u,
      (_, before: string, after: string) =>
        `Set Language from ${languages(before)} to ${languages(after)}`,
    )
    .replace(
      /^(Set language to |Kept Language as |Kept )(.+)$/u,
      (_, words: string, value: string) => words + languages(value),
    )
    .replace(
      /^(.+) → (.+)$/u,
      (_, before: string, after: string) => `${languages(before)} → ${languages(after)}`,
    )
// One durable record's row. It stands on its own in the ledger, or as one
// member of a preparation's checks row, with the same Details either way.
export function ActivityEventRow({
  event,
  expanded,
  onToggle,
  day,
  library,
  result,
  children,
}: {
  event: ActivityEventView
  expanded: boolean
  onToggle: () => void
  /** A member recorded on another day than its checks row says so. */
  day?: string
  /** The library as Settings names it. */
  library: string
  /** A result in place of the row's own state, for a decision its check confirmed. */
  result?: string
  /** Records shown inside this row's Details, after its own. */
  children?: ReactNode
}) {
  const changeId = event.change_id || event.event_id
  const language = event.destination === 'language'
  const change = language ? languageChange(event.change) : event.change
  const changeDetail = language ? languageChange(event.change_detail) : event.change_detail
  const advice =
    event.destination === 'description' && event.origin === 'adviser'
      ? 'description'
      : event.event_type !== 'policy' &&
          event.destination === 'cover' &&
          ['adviser', 'automatic_pipeline', 'owner_retry'].includes(event.origin)
        ? 'cover'
        : undefined
  return (
    <article className={expanded ? 'is-expanded' : ''}>
      <time>
        {day === undefined ? null : <span className="cm-activity-day">{day} </span>}
        <RowTime seconds={event.occurred_at} />
      </time>
      <span className={`cm-activity-category is-${event.category}`}>
        {categoryLabel[event.category]}
      </span>
      <div className="cm-activity-work">
        <strong title={event.work_label}>{event.work_label}</strong>
        <span>
          {event.field_label} · {library}
        </span>
      </div>
      {/*
      The arrow in `change` is a glyph most screen readers drop,
      so the announced row carries the same change as a whole
      sentence. Both come from the one projection, so they
      cannot come to disagree. The sentence is text, not a label
      on a plain div, which assistive technology may ignore (#1288).
    */}
      <div className="cm-activity-change">
        {change === changeDetail ? (
          <span>{change}</span>
        ) : (
          <>
            {/* First, so the hidden sentence sits where the cell starts and
                never past a clipped change. */}
            <span className="sr-only">{changeDetail}</span>
            <span aria-hidden="true">{change}</span>
          </>
        )}
      </div>
      <b>{result ?? stateLabel(event.state, advice, event.failure)}</b>
      {/* Every disclosure names its row, so a screen reader hears which one (#1368). */}
      <button
        data-activity-focus={`details-${changeId}`}
        aria-expanded={expanded}
        aria-controls={`activity-evidence-${event.event_id}`}
        // The row's time names it apart from another change to the same field.
        aria-label={`${expanded ? 'Hide details' : 'Details'} for ${event.field_label}, ${event.work_label}${result ? `, ${result.toLowerCase()}` : ''}${event.occurred_at === null ? '' : `, ${formatWhen(event.occurred_at, new Date(), { dayShown: true })}`}`}
        onClick={onToggle}
      >
        {expanded ? 'Hide details' : 'Details'}
      </button>
      {expanded ? (
        <div
          className={`cm-activity-evidence${
            event.policy_changes.length ? ' has-policy-changes' : ''
          }`}
          id={`activity-evidence-${event.event_id}`}
        >
          {event.policy_changes.length ? (
            <section
              className="cm-activity-policy-changes"
              aria-label={`${String(event.policy_changes.length)} exact policy changes`}
            >
              <header>
                <strong>
                  {event.policy_changes.length} exact{' '}
                  {event.policy_changes.length === 1 ? 'change' : 'changes'}
                </strong>
                <span>Field and setting</span>
                <span>From</span>
                <span>To</span>
              </header>
              <ul>
                {event.policy_changes.map((change) => (
                  <li key={`${change.field}-${change.setting}`}>
                    <span>
                      <strong>{change.field}</strong>
                      <small>{change.setting}</small>
                    </span>
                    <b>{change.previous}</b>
                    <b>{change.next}</b>
                  </li>
                ))}
              </ul>
            </section>
          ) : null}
          <dl>
            {event.evidence.map((item) => (
              <div key={item.label}>
                <dt>{item.label}</dt>
                <dd>
                  {language && item.label.endsWith(' value') ? languages(item.value) : item.value}
                </dd>
              </div>
            ))}
          </dl>
          {event.transition_id ? (
            <ActivityTransitions key={changeId} eventId={event.transition_id} advice={advice} />
          ) : null}
          {children}
        </div>
      ) : null}
    </article>
  )
}
