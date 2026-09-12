import type { RecordingIdentityBasisView } from '../api/http'

export function RecordingIdentityBasis({ value }: { value: RecordingIdentityBasisView | null }) {
  if (value === null) return null
  return (
    <section className="diagnostic-entry" aria-label="Recording identity evidence">
      <p>
        Chapter timing and structure corroborate this recording. {value.provider} did not provide a
        separate book runtime.
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
