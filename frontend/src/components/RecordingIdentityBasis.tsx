import type { RecordingIdentityBasisView, TargetDestination } from '../api/http'
import { candidateReference, destinationPhrase } from '../labels'

// The fields book-level evidence can decide, in the order the note names them.
const workScopeFields = ['series', 'title', 'description', 'genres'] as const

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
    const named = workScopeFields.filter((field) => offered.includes(field))
    return (
      <section className="diagnostic-entry" aria-label="Book-level evidence">
        <p>
          {named.length
            ? `Book-level evidence supports ${new Intl.ListFormat('en', { type: 'conjunction' }).format(named.map((field) => (field === 'genres' ? 'genre' : destinationPhrase(field))))} choices.`
            : 'Book-level evidence is recorded for this work.'}{' '}
          The exact recording remains unresolved.
        </p>
        <p>
          {named.length ? 'Your choices apply only to these fields. ' : ''}Recording details, covers
          and automatic changes are unavailable from this evidence.
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
