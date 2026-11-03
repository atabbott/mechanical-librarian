import type { RecordingIdentityBasisView, TargetDestination } from '../api/http'
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

export function RecordingIdentityBasis({
  value,
  scope,
  offered,
}: {
  value: RecordingIdentityBasisView | null
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
          {named.length || narrators ? 'Your choices apply only to these fields. ' : ''}Recording
          identifiers and automatic changes are unavailable from this evidence.
        </p>
      </section>
    )
  }
  if (value === null) return null
  return (
    <section className="diagnostic-entry" aria-label="Recording identity evidence">
      <p>
        Complete chapter timing and structure corroborate this recording where overall runtime is
        unavailable.
      </p>
      <details>
        <summary className="recording-identity-source">Recording source</summary>
        <p>
          {value.provider} · {candidateReference(value.candidate)}
        </p>
      </details>
    </section>
  )
}
