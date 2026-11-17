// An identifiers file's preview (#1478): the summary, the counts that are not
// zero, then one of its two lists, 100 rows at a time.
import { useRef, useState } from 'react'
import type { ReactNode, Ref } from 'react'

import type { IdentifierPreviewView } from '../api/http'
import {
  authorsText,
  countOrder,
  countPhrase,
  identifierKind,
  reasonCopy,
  summaryLine,
} from '../identifierImport'
import { SegmentedControl } from './SegmentedControl'

const page = 100
const number = (value: number) => value.toLocaleString('en-US')

// Show more moves focus to the first row it adds.
function PagedList<Row extends { row: number }>({
  rows,
  label,
  render,
}: {
  rows: readonly Row[]
  label: string
  render: (row: Row) => ReactNode
}) {
  const [shown, setShown] = useState(page)
  const list = useRef<HTMLOListElement>(null)
  const remaining = rows.length - shown
  return (
    <>
      <ol className="identifier-import-list" aria-label={label} ref={list}>
        {rows.slice(0, shown).map((row) => (
          <li key={row.row} tabIndex={-1}>
            <span className="identifier-import-row">Row {number(row.row)}</span>
            {render(row)}
          </li>
        ))}
      </ol>
      {remaining > 0 ? (
        <button
          type="button"
          className="identifier-import-more"
          onClick={() => {
            const from = shown
            setShown(shown + page)
            requestAnimationFrame(() =>
              list.current?.querySelectorAll<HTMLElement>(':scope > li')[from]?.focus(),
            )
          }}
        >
          Show {number(Math.min(page, remaining))} more
        </button>
      ) : null}
    </>
  )
}

export function IdentifierImportPreview({
  preview,
  ref,
}: {
  preview: IdentifierPreviewView
  /** The summary line, which takes focus when the preview opens. */
  ref?: Ref<HTMLParagraphElement> | undefined
}) {
  const willLink = preview.counts.will_link
  const notLinked = preview.rows - willLink
  const [view, setView] = useState<'will' | 'not'>(willLink ? 'will' : 'not')
  return (
    <div className="identifier-import-preview">
      <p className="identifier-import-summary" tabIndex={-1} ref={ref}>
        {summaryLine(preview)}
      </p>
      <ul className="identifier-import-counts">
        {countOrder
          .filter((key) => preview.counts[key] > 0)
          .map((key) => (
            <li key={key}>{countPhrase(key, preview.counts[key])}</li>
          ))}
      </ul>
      <SegmentedControl
        label="Rows"
        value={view}
        options={[
          { value: 'will', label: `Will be linked (${number(willLink)})` },
          { value: 'not', label: `Not linked (${number(notLinked)})` },
        ]}
        onChange={setView}
      />
      {view === 'will' ? (
        <PagedList
          key="will"
          rows={preview.will_link}
          label="Will be linked"
          render={(row) => (
            <>
              <span className="identifier-import-work">
                <strong>{row.title}</strong>
                <small>{authorsText(row.authors)}</small>
              </span>
              <span className="identifier-import-id">
                {identifierKind(row)} <span>{row.identifier}</span>
              </span>
            </>
          )}
        />
      ) : (
        <PagedList
          key="not"
          rows={preview.refused}
          label="Not linked"
          render={(row) => (
            <span className="identifier-import-reason">{reasonCopy[row.reason]}</span>
          )}
        />
      )}
    </div>
  )
}
