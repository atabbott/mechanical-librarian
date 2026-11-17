// The header of the Library's selected work: its cover, title and credits, how
// many of its fields need the owner or were decided automatically, and its
// work actions: Evidence, Find this book or its link, and More, which holds
// History (#1477, L1). Moved out of the Library route under the route-size
// rule (#1903).
import { Link } from '@tanstack/react-router'
import { useEffect, useId, useRef, useState } from 'react'
import type { ReactNode } from 'react'

import type { LibraryCoverView, LibrarySearch, MediaKind, WorkAddress } from '../api/http'
import { BookMark } from '../components/BookMark'
import type { ReviewField } from '../fieldDecision'
import { WORK_ACTIONS } from '../findThisBook'
import { mediaKindLabels } from '../labels'
import { normalizeDetailSearch } from '../router'
import { FindThisBookEntry } from './FindThisBook'
import type { FindEntryRow } from './FindThisBook'

// A disclosure whose list lies over what is below it, so opening it moves
// nothing. Escape and a press outside close it and give focus back to More.
function MoreMenu({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState(false)
  const wrap = useRef<HTMLDivElement>(null)
  const button = useRef<HTMLButtonElement>(null)
  const list = useId()
  useEffect(() => {
    if (!open) return
    const outside = (event: PointerEvent) => {
      if (event.target instanceof Node && wrap.current?.contains(event.target)) return
      setOpen(false)
      button.current?.focus({ preventScroll: true })
    }
    const escape = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return
      event.preventDefault()
      setOpen(false)
      button.current?.focus()
    }
    document.addEventListener('pointerdown', outside)
    document.addEventListener('keydown', escape)
    return () => {
      document.removeEventListener('pointerdown', outside)
      document.removeEventListener('keydown', escape)
    }
  }, [open])
  return (
    <div
      className="work-more"
      ref={wrap}
      onBlur={(event) => {
        if (!wrap.current?.contains(event.relatedTarget)) setOpen(false)
      }}
    >
      <button
        ref={button}
        type="button"
        className="work-action"
        aria-label="More for this work"
        aria-expanded={open}
        aria-controls={list}
        onClick={() => setOpen(!open)}
      >
        More
      </button>
      <ul id={list} className="work-more-list" hidden={!open}>
        <li
          onClick={(event) => {
            // A choice in the list closes it.
            if (event.target instanceof Element && event.target.closest('a, button')) setOpen(false)
          }}
        >
          {children}
        </li>
      </ul>
    </div>
  )
}

export function SelectedWorkHeader({
  search,
  identity,
  cover,
  reviewCaseId,
  fields,
  needsYou,
  selectedDestination,
  address,
  row,
}: {
  search: LibrarySearch
  identity: {
    title: string
    authors: readonly string[]
    library: string
    libraryDisplayName?: string
    mediaKind: MediaKind
  }
  cover: LibraryCoverView | undefined
  reviewCaseId: string
  /** Every field of the case, for the Automatic count. */
  fields: readonly ReviewField[]
  /** How many of its fields need the owner. */
  needsYou: number
  selectedDestination: string | undefined
  /** The catalogue address the work's book link belongs to. */
  address: WorkAddress
  /** The work's Library row, which says whether Find this book or its pill shows. */
  row: FindEntryRow | undefined
}) {
  const detail = (subview: 'evidence' | 'history') =>
    normalizeDetailSearch({ ...search, subview, field: selectedDestination })
  const actions = {
    evidence: (
      <Link
        key="evidence"
        params={{ reviewCaseId }}
        search={detail('evidence')}
        to="/review/$reviewCaseId"
      >
        Evidence
      </Link>
    ),
    find: <FindThisBookEntry key="find" address={address} row={row} work={identity} />,
    more: (
      <MoreMenu key="more">
        <Link params={{ reviewCaseId }} search={detail('history')} to="/review/$reviewCaseId">
          History
        </Link>
      </MoreMenu>
    ),
  }
  return (
    <header
      className="selected-work-header"
      tabIndex={-1}
      {...(search.preview && search.attention !== 'open' ? { 'data-route-focus': 'true' } : {})}
    >
      <BookMark title={identity.title} cover={cover} />
      <div>
        <h2>{identity.title}</h2>
        <p>
          {identity.authors.length ? identity.authors.join(', ') : 'Author details unavailable'} ·{' '}
          {identity.libraryDisplayName ?? identity.library} · {mediaKindLabels[identity.mediaKind]}
        </p>
      </div>
      <dl className="selected-work-status">
        <div>
          <dt>Needs you</dt>
          <dd>{String(needsYou)}</dd>
        </div>
        <div>
          <dt>Automatic</dt>
          <dd>
            {String(
              fields.filter(
                (field) =>
                  field.humanDecision === null &&
                  field.automaticDecision !== null &&
                  !field.manualHold,
              ).length,
            )}
          </dd>
        </div>
      </dl>
      <nav className="selected-work-diagnostics" aria-label="Work actions">
        {WORK_ACTIONS.map((action) => actions[action])}
      </nav>
    </header>
  )
}
