// The write plan's grouped rows: what each prepared change will set, the
// current value beside the new one, and why. Shown under each field of each
// library in Activity's consent review, so the owner reads a field's changes
// where they decide on it (#1336).
import type { WritePlanView } from '../api/http'
import { RowGroup } from './RowGroups'

/** Up to this many changes, every group starts open and every row is shown. */
export const WRITE_PLAN_OPEN_ALL = 25

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
      {groups.map((group) => {
        // When every listable change is shown, a missing row could not be
        // read, and the file leaves it out too, so nothing is announced as
        // being there.
        const more = everyRowShown ? 0 : group.count - group.rows.length
        return (
          <RowGroup
            key={group.kind}
            label={group.kind === 'human' ? 'Chosen by you' : 'Chosen automatically'}
            count={group.count}
            rows={group.rows.map((row) => ({
              work: row.work,
              aside: row.authors,
              values: [
                { label: 'Current', value: row.current },
                { label: 'New', value: row.new },
              ],
              why: `${row.reason}${row.sources.length ? ` · ${row.sources.join(', ')}` : ''}`,
            }))}
            open={everyRowShown}
            more={more > 0 ? `${more.toLocaleString('en-US')} more in the file` : undefined}
          />
        )
      })}
    </div>
  )
}
