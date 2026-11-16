// Grouped rows: a folding group with a label and a count, over rows that set
// labelled values side by side. Activity's write plan and the Policy preview
// both pass plain values, so neither depends on the other's types (#1336,
// #1524).
export interface RowValue {
  readonly label: string
  readonly value: string
  /** A sentence about this value alone, set beneath it. */
  readonly note?: string | undefined
}
export interface GroupedRow {
  /** The work, or nothing for a row that is about no one work. */
  readonly work: string
  /** What stands beside the work: its authors, or its medium. */
  readonly aside: string
  readonly values: readonly RowValue[]
  /** A line about the whole row. */
  readonly why?: string | undefined
}

export const RowGroup = ({
  label,
  count,
  rows,
  open,
  more,
}: {
  label: string
  /** How much the group holds, in words, or empty; it can be more than it shows. */
  count: string
  rows: readonly GroupedRow[]
  open: boolean
  /** A closing line for rows the group counts and does not show. */
  more?: string | undefined
}) => {
  const summary = (
    <>
      <span className="write-plan-chooser">{label}</span>
      {/* Rendered even when empty: it carries the Show or Hide cue. */}
      <span className="write-plan-count">{count}</span>
    </>
  )
  // With no row to show there is nothing to open: the group is its line alone.
  if (rows.length === 0)
    return (
      <div className="write-plan-group">
        <p className="write-plan-summary">{summary}</p>
      </div>
    )
  return (
    <details className="write-plan-group" open={open}>
      {/* The Show or Hide cue is generated content; <details> states the
          expanded state to assistive technology itself. */}
      <summary>{summary}</summary>
      <ul className="write-plan-rows">
        {rows.map((row, index) => (
          // Rows carry no identity by design: their place is their key.
          <li key={index} className="write-plan-row">
            {row.work ? (
              <p className="write-plan-work">
                <strong>{row.work}</strong> <span>{row.aside}</span>
              </p>
            ) : null}
            {row.values.length ? (
              <dl className="write-plan-values">
                {row.values.map((value) => (
                  <div key={value.label}>
                    <dt>{value.label}</dt>
                    <dd>
                      {value.value}
                      {value.note ? <small>{value.note}</small> : null}
                    </dd>
                  </div>
                ))}
              </dl>
            ) : null}
            {row.why ? <p className="write-plan-why">{row.why}</p> : null}
          </li>
        ))}
      </ul>
      {more ? <p className="write-plan-more">{more}</p> : null}
    </details>
  )
}
