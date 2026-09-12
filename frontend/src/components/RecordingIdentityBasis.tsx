import type { RecordingIdentityBasisView } from '../api/http'

export function RecordingIdentityBasis({ value }: { value: RecordingIdentityBasisView | null }) {
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
