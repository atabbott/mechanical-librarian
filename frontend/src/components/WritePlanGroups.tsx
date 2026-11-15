// The write plan's grouped rows: what each prepared change will set, the
// current value beside the new one, and why. Shown under each field of each
// library in Activity's consent review, so the owner reads a field's changes
// where they decide on it (#1336).
import type { WritePlanGroupView, WritePlanRowView, WritePlanView } from '../api/http'

/** Up to this many changes, every group starts open and every row is shown. */
export const WRITE_PLAN_OPEN_ALL = 25

const changesText = (count: number) =>
  count === 1 ? '1 change' : `${count.toLocaleString('en-US')} changes`

const WritePlanRow = ({ row }: { row: WritePlanRowView }) => (
  <li className="write-plan-row">
    <p className="write-plan-work">
      <strong>{row.work}</strong> <span>{row.authors}</span>
    </p>
    <dl className="write-plan-values">
      <div>
        <dt>Current</dt>
        <dd>{row.current}</dd>
      </div>
      <div>
        <dt>New</dt>
        <dd>{row.new}</dd>
      </div>
    </dl>
    <p className="write-plan-why">
      {row.reason}
      {row.sources.length ? ` · ${row.sources.join(', ')}` : ''}
    </p>
  </li>
)

const WritePlanGroup = ({
  group,
  everyRowShown,
}: {
  group: WritePlanGroupView
  everyRowShown: boolean
}) => {
  // When every listable change is shown, a missing row could not be read, and
  // the file leaves it out too, so nothing is announced as being there.
  const more = everyRowShown ? 0 : group.count - group.rows.length
  return (
    <details className="write-plan-group" open={everyRowShown}>
      {/* The Show or Hide cue is generated content; <details> states the
          expanded state to assistive technology itself. */}
      <summary>
        <span className="write-plan-chooser">
          {group.kind === 'human' ? 'Chosen by you' : 'Chosen automatically'}
        </span>
        <span className="write-plan-count">{changesText(group.count)}</span>
      </summary>
      {group.rows.length ? (
        <ul className="write-plan-rows">
          {group.rows.map((row, index) => (
            // Rows carry no identity by design: their place is their key.
            <WritePlanRow key={index} row={row} />
          ))}
        </ul>
      ) : null}
      {more > 0 ? (
        <p className="write-plan-more">{more.toLocaleString('en-US')} more in the file</p>
      ) : null}
    </details>
  )
}

/** The plan's groups for one library and field, or nothing when it has none. */
export const WritePlanField = ({
  plan,
  library,
  destination,
}: {
  plan: WritePlanView | undefined
  library: string
  destination: string
}) => {
  const groups =
    plan?.groups.filter(
      (group) => group.library === library && group.destination === destination,
    ) ?? []
  if (plan === undefined || groups.length === 0) return null
  const everyRowShown = plan.total <= WRITE_PLAN_OPEN_ALL
  return (
    <div className="write-plan-field">
      {groups.map((group) => (
        <WritePlanGroup key={group.kind} group={group} everyRowShown={everyRowShown} />
      ))}
    </div>
  )
}
