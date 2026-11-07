import type {
  RecordingAdmissionBasisView,
  RecordingIdentityBasisView,
  TargetDestination,
} from '../api/http'
import { candidateReference, destinationPhrase } from '../labels'

// The fields book-level evidence can decide, in the order the note names them.
// Narrators come from an audio edition, not the book, so they have a sentence
// of their own (#1642).
const workScopeFields = [
  'series',
  'title',
  'description',
  'genres',
  'publishedDate',
  'cover',
  'narrators',
] as const

const runningTime = 'Its running time closely matches this audiobook’s.'

// How the matched recording was found when its title differs (#1723). Each
// quoted value is what was searched, isolated so a right-to-left title never
// reorders the sentence around it.
function admissionSentence(value: RecordingAdmissionBasisView) {
  const title = <bdi>{value.searched.title ?? ''}</bdi>
  switch (value.basis) {
    case 'path_leaf':
      return (
        <>
          Mechanical Librarian found the matched recording by searching with “{title}”, read from
          this folder’s name, which differs from this audiobook’s title. {runningTime}
        </>
      )
    case 'title_tag':
      return (
        <>
          Mechanical Librarian found the matched recording by searching with “{title}”, read from
          the title tag in this audiobook’s audio file, which differs from this audiobook’s title.{' '}
          {runningTime}
        </>
      )
    case 'companion_title':
      return (
        <>
          Mechanical Librarian found the matched recording by searching with “{title}”, the title of
          the EPUB in this folder, which differs from this audiobook’s title. {runningTime}
        </>
      )
    case 'companion_work': {
      const authors = value.searched.authors ?? []
      return (
        <>
          Mechanical Librarian found the matched recording by searching with the title and{' '}
          {authors.length > 1 ? 'authors' : 'author'} of the EPUB in this folder, “{title}” by{' '}
          <bdi>{new Intl.ListFormat('en', { type: 'conjunction' }).format(authors)}</bdi>, because
          this audiobook’s own title or author is missing or incomplete. {runningTime}
        </>
      )
    }
    case 'companion_isbn_work':
      return (
        <>
          Hardcover lists the matched recording as an audio edition of the book with ISBN{' '}
          <bdi>{value.searched.isbn ?? ''}</bdi>, which is stated in the EPUB in this folder. The
          matched recording’s running time closely matches this audiobook’s.
        </>
      )
  }
}

export function RecordingIdentityBasis({
  value,
  admission = null,
  scope,
  offered,
}: {
  value: RecordingIdentityBasisView | null
  /** How the matched recording was found when its title differs (#1723). */
  admission?: RecordingAdmissionBasisView | null
  scope: 'work' | 'recording' | null
  /** The fields this case offers; a report with no case offers none. */
  offered: readonly TargetDestination[]
}) {
  if (scope === 'work') {
    // Only the fields this case actually offers are named (#1253, #1286).
    const named = workScopeFields.filter(
      (field) => field !== 'narrators' && offered.includes(field),
    )
    const narrators = offered.includes('narrators')
    const fieldCount = named.length + (narrators ? 1 : 0)
    const sentences = [
      named.length
        ? `Book-level evidence supports ${new Intl.ListFormat('en', { type: 'conjunction' }).format(named.map((field) => (field === 'genres' ? 'genre' : destinationPhrase(field))))} choices.`
        : narrators
          ? ''
          : 'Book-level evidence is recorded for this work.',
      narrators ? 'A Hardcover audio edition supports a narrator choice.' : '',
      'The exact recording remains unresolved.',
    ]
    return (
      <section className="diagnostic-entry" aria-label="Book-level evidence">
        <p>{sentences.filter(Boolean).join(' ')}</p>
        <p>
          {fieldCount === 1
            ? 'Your choice applies only to this field. '
            : fieldCount > 1
              ? 'Your choices apply only to these fields. '
              : ''}
          Recording identifiers and automatic changes are unavailable from this evidence.
        </p>
      </section>
    )
  }
  const source = value ?? admission
  if (source === null) return null
  return (
    <section className="diagnostic-entry" aria-label="How this recording was matched">
      {value !== null ? (
        <p>
          Complete chapter timing and structure corroborate this recording where overall runtime is
          unavailable.
        </p>
      ) : null}
      {admission !== null ? <p>{admissionSentence(admission)}</p> : null}
      <details>
        <summary className="recording-identity-source">Recording source</summary>
        <p>
          {source.provider} · {candidateReference(source.candidate)}
        </p>
      </details>
    </section>
  )
}
