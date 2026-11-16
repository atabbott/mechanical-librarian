// The selected work's contextual fields: a one-line summary of those with no
// change, waiting or unavailable, which opens to say why for each. Moved out of
// the Library route under the route-size rule (#1903).
import { StatusPill } from '../components/StatusPill'
import { decisionValue } from '../fieldDecision'
import type { ReviewField } from '../fieldDecision'
import { contextKind, fieldReason } from '../fieldReasons'
import { destinationLabel } from '../labels'

const heldPhrase = (count: number, kind: 'waiting' | 'unavailable') =>
  `${String(count)} ${count === 1 ? 'field' : 'fields'} ${kind === 'waiting' ? 'waiting' : 'unavailable'}`

export function ContextualFields({ fields }: { fields: readonly ReviewField[] }) {
  const contextualSummary = {
    unchanged: fields.filter((field) => contextKind(field) === 'unchanged'),
    waiting: fields.filter((field) => contextKind(field) === 'waiting').length,
    unavailable: fields.filter((field) => contextKind(field) === 'unavailable').length,
  }
  return (
    <details className="automatic-resolution-details">
      <summary>
        <span>
          {contextualSummary.unchanged.length ? (
            <span>
              No change:{' '}
              {contextualSummary.unchanged
                .map((field) => destinationLabel(field.destination))
                .join(', ')}
            </span>
          ) : null}
          {contextualSummary.waiting ? (
            <span>{heldPhrase(contextualSummary.waiting, 'waiting')}</span>
          ) : null}
          {contextualSummary.unavailable ? (
            <span>{heldPhrase(contextualSummary.unavailable, 'unavailable')}</span>
          ) : null}
        </span>
        <small>Show why</small>
      </summary>
      <div className="automatic-resolution-list">
        {fields.map((field) => {
          const kind = contextKind(field)
          return (
            <div className={`automatic-resolution-row is-${kind}`} key={field.destination}>
              <strong>
                {destinationLabel(field.destination)}
                {kind === 'unchanged' ? null : (
                  <StatusPill tone="notice">
                    {kind === 'waiting' ? 'Waiting' : 'Unavailable'}
                  </StatusPill>
                )}
              </strong>
              <small>{decisionValue(field)}</small>
              <small>
                {kind === 'unchanged' ? 'No source offered a different value.' : fieldReason(field)}
              </small>
            </div>
          )
        })}
      </div>
    </details>
  )
}
