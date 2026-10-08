import type { RecordingIdentityBasisView } from '../api/http'

export function RecordingIdentityBasis({
  value,
  scope,
}: {
  value: RecordingIdentityBasisView | null
  scope: 'work' | 'recording' | null
}) {
  if (scope === 'work')
    return (
      <section className="diagnostic-entry" aria-label="Book-level evidence">
        <p>
          Book-level evidence supports description and genre choices. The exact recording remains
          unresolved.
        </p>
        <p>
          Your choices apply only to these fields. Recording details, covers and automatic changes
          are unavailable from this evidence.
        </p>
      </section>
    )
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
          {value.provider} · Candidate reference: {value.candidate}
        </p>
      </details>
    </section>
  )
}
