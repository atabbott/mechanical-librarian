import { useId } from 'react'

import type { MediaHealthObservationView } from '../api/http'
import { fieldLabel, opaqueSource } from '../labels'

// An opaque server reference is not a label an owner can read, so it stands
// for the evidence it points at instead of being printed.
const citation = (value: string) =>
  opaqueSource.test(value)
    ? 'local evidence'
    : value.length > 160
      ? `${value.slice(0, 157)}…`
      : value

// What is wrong with the media itself, distinct from what the evidence says
// about the work. Every observation names the rule it broke and what it was
// read from, so the section reads as evidence an owner can check rather than
// as a verdict. Shared by every surface that carries the section -- the
// recorded-evidence view, a review case, and a row whose preparation stopped
// -- because one vocabulary read three ways would be three vocabularies.
export function MediaHealth({
  observations,
}: {
  observations: readonly MediaHealthObservationView[]
}) {
  const heading = useId()
  return (
    <section className="library-work-media-health" aria-labelledby={heading}>
      <h4 id={heading}>Media health</h4>
      {observations.length === 0 ? (
        <p role="status">No media-health observations were recorded.</p>
      ) : (
        <ul>
          {observations.map((observation, index) => (
            <li key={`${observation.kind}:${String(index)}`}>
              <strong>{fieldLabel(observation.kind)}</strong>
              {observation.observed ? (
                <span>
                  {observation.observed}
                  {observation.expected ? ` · expected ${observation.expected}` : ''}
                </span>
              ) : null}
              <p>{observation.bound}</p>
              <small>Read from {[...new Set(observation.cited.map(citation))].join(', ')}</small>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
