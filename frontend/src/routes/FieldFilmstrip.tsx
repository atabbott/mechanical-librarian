// The selected work's field rail: one tab per field saying where it stands, and
// the control that shows the empty fields left out of it. Moved out of the
// Library route under the route-size rule (#1903).
import type { RefObject } from 'react'

import { authoredOwnerRecorded } from '../fieldDecision'
import type { ReviewField } from '../fieldDecision'
import { optionalChoice } from '../fieldDecisionMode'
import { destinationLabel } from '../labels'
import { actionable } from '../reviewDecisionController'

// A recording's settled fill stays choosable without asking for review (#1249).
// A field tab says where its field stands in words, never a glyph: an
// undecided optional field and a decided one both drew a check (#1290).
const fieldStatus = (field: ReviewField) =>
  actionable(field)
    ? (['needs', 'Needs you'] as const)
    : field.humanDecision || authoredOwnerRecorded(field) || field.manualHold
      ? (['decided', 'Decided'] as const)
      : field.classification === 'automatic_recorded' || field.automaticDecision
        ? (['automatic', 'Automatic'] as const)
        : optionalChoice(field)
          ? (['optional', 'Optional'] as const)
          : (['unchanged', 'No change'] as const)

export function FieldFilmstrip({
  title,
  fields,
  omittedCount,
  selectedDestination,
  fieldRail,
  activeFieldButton,
  revealFocusDestination,
  clearRevealFocus,
  centreFieldRail,
  onSelectField,
  onShowMore,
}: {
  /** The selected work's title. */
  title: string
  /** The fields the rail shows, in its order. */
  fields: readonly ReviewField[]
  /** How many empty fields are left out of the rail. */
  omittedCount: number
  selectedDestination: string | undefined
  fieldRail: RefObject<HTMLElement | null>
  activeFieldButton: RefObject<HTMLButtonElement | null>
  /** The field whose tab takes focus once it renders, after Show more. */
  revealFocusDestination: string | null
  clearRevealFocus: () => void
  centreFieldRail: (rail: HTMLElement, button: HTMLButtonElement) => void
  onSelectField: (destination: string) => void
  onShowMore: () => void
}) {
  return (
    <section className="field-map" aria-label={`Fields in ${title}`}>
      <nav ref={fieldRail} className="field-filmstrip" aria-label="Review fields">
        {fields.map((field) => (
          <button
            key={field.destination}
            ref={
              revealFocusDestination === field.destination
                ? (button) => {
                    if (button) {
                      clearRevealFocus()
                      button.focus()
                      const rail = fieldRail.current
                      if (rail) centreFieldRail(rail, button)
                    }
                  }
                : selectedDestination === field.destination
                  ? activeFieldButton
                  : undefined
            }
            aria-current={selectedDestination === field.destination}
            className={!actionable(field) ? 'is-recorded' : undefined}
            onClick={() => onSelectField(field.destination)}
          >
            <strong>{destinationLabel(field.destination)}</strong>{' '}
            <span className={`field-status is-${fieldStatus(field)[0]}`}>
              {fieldStatus(field)[1]}
            </span>
          </button>
        ))}
        {omittedCount ? (
          <button type="button" className="field-more" onClick={onShowMore}>
            {`Show ${String(omittedCount)} more field${omittedCount === 1 ? '' : 's'}`}
          </button>
        ) : null}
      </nav>
    </section>
  )
}
