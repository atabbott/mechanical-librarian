// The header of the Library's selected work: its cover, title and credits, how
// many of its fields need the owner or were decided automatically, and its
// Evidence and History links. Moved out of the Library route under the
// route-size rule (#1903).
import { Link } from '@tanstack/react-router'

import type { LibraryCoverView, LibrarySearch, MediaKind } from '../api/http'
import { BookMark } from '../components/BookMark'
import type { ReviewField } from '../fieldDecision'
import { mediaKindLabels } from '../labels'
import { normalizeDetailSearch } from '../router'

export function SelectedWorkHeader({
  search,
  identity,
  cover,
  reviewCaseId,
  fields,
  needsYou,
  selectedDestination,
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
}) {
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
      <nav className="selected-work-diagnostics" aria-label="Evidence and history">
        <Link
          params={{ reviewCaseId }}
          search={normalizeDetailSearch({
            ...search,
            subview: 'evidence',
            field: selectedDestination,
          })}
          to="/review/$reviewCaseId"
        >
          Evidence
        </Link>
        <Link
          params={{ reviewCaseId }}
          search={normalizeDetailSearch({
            ...search,
            subview: 'history',
            field: selectedDestination,
          })}
          to="/review/$reviewCaseId"
        >
          History
        </Link>
      </nav>
    </header>
  )
}
