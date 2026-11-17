// Import identifiers (#1478): the words the import reads in, its plurals, and
// the size the browser checks before sending a file. The components
// `IdentifierImport.tsx`, `IdentifierImportPreview.tsx` and
// `IdentifierImportWithdrawal.tsx` render from here; nothing here reads or
// writes.
import type {
  IdentifierCountKey,
  IdentifierPreviewView,
  IdentifierRefusedRow,
  IdentifierWillLinkRow,
} from './api/http'

/** The server refuses a larger body before it reads it; the browser does not send one. */
export const fileLimitBytes = 1_048_576

const number = (value: number) => value.toLocaleString('en-US')

// The counts, in this order, each shown only when it is not zero:
// [more than one, exactly one].
const countWords: Readonly<Record<IdentifierCountKey, readonly [string, string]>> = {
  will_link: ['will be linked', 'will be linked'],
  already_linked: ['already linked', 'already linked'],
  no_item: ['match no work', 'matches no work'],
  ambiguous: ['match more than one work', 'matches more than one work'],
  invalid_identifier: [
    'have an identifier that is not an ISBN or ASIN',
    'has an identifier that is not an ISBN or ASIN',
  ],
  wrong_medium: [
    'give an ASIN for an ebook or an ISBN for an audiobook',
    'gives an ASIN for an ebook or an ISBN for an audiobook',
  ],
  repeated: ['repeat an earlier row', 'repeats an earlier row'],
  conflicting: [
    'give a work a different identifier from another row',
    'gives a work a different identifier from another row',
  ],
  unreadable: ['could not be read', 'could not be read'],
}
export const countOrder = Object.keys(countWords) as readonly IdentifierCountKey[]
export const countPhrase = (key: IdentifierCountKey, value: number) =>
  `${number(value)} ${countWords[key][value === 1 ? 1 : 0]}`
export const summaryLine = (preview: Pick<IdentifierPreviewView, 'rows' | 'counts'>) =>
  `${number(preview.rows)} ${preview.rows === 1 ? 'row' : 'rows'}: ${number(preview.counts.will_link)} will be linked, ${number(preview.rows - preview.counts.will_link)} will not.`

export const reasonCopy: Readonly<Record<IdentifierRefusedRow['reason'], string>> = {
  no_item: 'Matches no work in this library',
  ambiguous: 'Matches more than one work. Use the item ID or a longer path.',
  invalid_isbn: 'Needs an ISBN of 10 or 13 digits with a matching check digit',
  invalid_asin: 'Needs an ASIN of 10 letters and digits',
  asin_for_ebook: 'This is an ASIN, and an ebook needs an ISBN',
  isbn_for_audiobook: 'This is an ISBN, and an audiobook needs an ASIN',
  repeated: 'Repeats an earlier row',
  conflicting: 'Another row gives this work a different identifier',
  already_linked: 'Already linked. Change the link from the work in Library.',
  row_shape: 'Needs two values separated by one tab',
  row_too_long: 'Longer than 4,096 bytes',
}

export const formatText =
  'The first line is `item` or `path`, a tab, then `identifier`. Each line after it names one work, by its Audiobookshelf item ID or its path in the library. Then comes a tab and the ISBN of an ebook or the ASIN of an audiobook.'
export const formatExample = ['path\tidentifier', 'Fantasy/The Hollow Crown\t9780306406157']

const libraryUnreadable = "This library's works could not be read. Try again in a minute."
/** Why a file was refused before or at its preview, by the server's code. */
export const fileRefusalCopy = (code: string) =>
  ({
    body_too_large: 'This file is over 1 MiB. Split it and import each part.',
    file_encoding: 'This file is not UTF-8 text. Save it as UTF-8 and choose it again.',
    file_control_character:
      'This file has a control character. Save it as plain text and choose it again.',
    file_header: 'The first line must be `item` or `path`, a tab, then `identifier`.',
    file_too_many_rows: 'This file has more than 5,000 rows. Split it and import each part.',
    file_no_rows: 'This file has no rows after its first line.',
    library_not_found: 'This library is not in the saved settings. Reload the page.',
  })[code] ?? libraryUnreadable

export const staleCopy = "This library's works changed after the preview. Preview the file again."
export const closedCopy = 'This preview is no longer open. Preview the file again.'
export const importedCopy = 'This preview was already imported. Its import is in Activity.'
export const checkingImportCopy =
  'No answer came back for the import. Checking whether it was recorded…'
export const notRecordedCopy = 'The import was not recorded.'
export const notRecordedClosedCopy =
  'The import was not recorded, and the preview has closed. Preview the file again.'
export const uncheckedCopy =
  'No answer came back, and Mechanical Librarian could not check the import. Check Activity before importing this file again.'

const counted = (count: number, one: string, many: string) =>
  `${number(count)} ${count === 1 ? one : many}`
export const linkLabel = (count: number) => `Link ${counted(count, 'work', 'works')}`
export const linkedWorks = (count: number) => `Linked ${counted(count, 'work', 'works')}`
export const withdrawQuestion = (count: number) =>
  `Withdraw ${counted(count, 'link', 'links')} from this import? Each work is prepared again without its link.`
export const withdrewLine = (count: number) => `Withdrew ${counted(count, 'link', 'links')}.`
export const authorsText = (authors: readonly string[]) =>
  authors.length ? authors.join(', ') : 'Author details unavailable'
export const identifierKind = (row: Pick<IdentifierWillLinkRow, 'identifier_kind'>) =>
  row.identifier_kind === 'isbn' ? 'ISBN' : 'ASIN'
