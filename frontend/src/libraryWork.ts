// How a Library work row and its summary name a work, shared by the Library
// route, its works column and the work summary it shows.
import type { LibraryWorkView } from './api/http'

export const authorLine = (work: LibraryWorkView) =>
  work.authors.length ? work.authors.join(', ') : 'Author details unavailable'
// A work with a review case opens that case; one with only a published report
// is addressed by the report itself, and the server names which rather than
// leaving it to be read off the URL's shape -- a prefix sniff would silently
// re-orphan this evidence the next time a route is renamed. Deciding it once
// here keeps the row and the panel agreeing on what is openable.
export const reportEvidenceUrl = (work: LibraryWorkView) =>
  work.detailKind === 'report' ? work.detailUrl : null

export const workCount = (count: number) => `${String(count)} ${count === 1 ? 'work' : 'works'}`
/** What to do when a read keeps failing, said once on a screen (#1765). */
export const RESTART_SENTENCE =
  'If trying again does not help, restart the Mechanical Librarian service.'
