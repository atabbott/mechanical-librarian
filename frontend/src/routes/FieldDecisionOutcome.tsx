// A Library field's settled reading: its current value, its recorded outcome
// and the automatic statements beside it. Moved out of the Library route under
// the route-size rule (#1571).
import { displayValue, isClear } from '../api/http'
import {
  authoredOwnerRecorded,
  completionOutcome,
  decisionValue,
  sameSelectedValue,
  valueText,
} from '../fieldDecision'
import type { ReviewField } from '../fieldDecision'
import {
  choiceContext,
  combinedAutomaticTags,
  fieldReason,
  offeredAnotherValue,
  ownerContext,
  selectedAutomaticLanguageForm,
} from '../fieldReasons'
import { clearedSentence, destinationLabel } from '../labels'
import { ChoiceEvidencePanel, DescriptionText } from './FieldDecisionChoices'
import { FormattedDescription, visibleText } from '../descriptionFormatting'
import type { PinControl } from '../fieldPin'
import { PinButton } from './FieldDecisionPin'

// The Library field panel for a field with nothing new to choose: why it is
// shown and its current value. Moved out of the Library route under the
// route-size rule (#1575).
export function SettledFieldContext({
  field,
  actionable,
  ownerEntry,
  expanded,
  toggle,
  pin,
}: {
  field: ReviewField
  actionable: boolean
  ownerEntry: boolean
  expanded: boolean
  toggle: () => void
  pin?: PinControl | undefined
}) {
  // A pinned field says why it reads as it does at its foot, once. A field
  // an earlier decision holds in Preview says so in the server's words, and
  // "No source offered" shows only where none did.
  const held = field.classificationCause === 'held_in_preview'
  const reasonShown = field.pin
    ? Boolean(field.earlierAutomaticValue)
    : actionable ||
      held ||
      Boolean(field.earlierAutomaticValue) ||
      (!ownerEntry && !offeredAnotherValue(field))
  return (
    <>
      {reasonShown ? (
        <p className={actionable ? 'decision-recorded' : 'decision-context'}>
          {actionable
            ? `${fieldReason(field)}${field.humanDecision || field.automaticDecision ? ` ${recordedValueSentence(field)}` : ''}`
            : held
              ? fieldReason(field)
              : field.earlierAutomaticValue
                ? 'Automatic chose a value before you entered Preview mode. Automatic doesn’t write in Preview, and turning it on decides this field again.'
                : 'No source offered a different value.'}
        </p>
      ) : null}
      {!actionable || field.pin ? (
        <ContextValue field={field} expanded={expanded} toggle={toggle} pin={pin} />
      ) : null}
    </>
  )
}

// A recorded clear has no value to remain; its sentence says what was done (#1523).
const recordedValueSentence = (field: ReviewField) =>
  isClear(field.humanDecision?.selected_value)
    ? clearedSentence(field.destination)
    : `The recorded value remains ${field.destination === 'description' ? visibleText(decisionValue(field)) : decisionValue(field)}.`

export function ContextValue({
  field,
  expanded,
  toggle,
  pin,
}: {
  field: ReviewField
  expanded: boolean
  toggle: () => void
  // The pin beside the current value of a field with nothing to choose.
  pin?: PinControl | undefined
}) {
  const context = choiceContext(field, null)
  const evidenceId = `context-evidence-${field.destination}`
  const valueId = `context-value-${field.destination}`
  const earlier = field.earlierAutomaticValue ? displayValue(field.earlierAutomaticValue.value) : ''
  return (
    <section
      className={`context-value${pin ? ' has-pin' : ''}`}
      aria-label={`${destinationLabel(field.destination)} current value`}
    >
      <dl>
        <div>
          <dt>{field.pin?.state === 'in_force' ? 'Current value · Pinned' : 'Current value'}</dt>
          <dd id={valueId}>
            {field.destination === 'description' ? (
              <DescriptionText
                key={field.currentValue}
                value={field.currentFormatted ?? field.currentValue}
              />
            ) : (
              valueText(field.destination, field.currentValue)
            )}
          </dd>
        </div>
        {field.earlierAutomaticValue ? (
          <div>
            <dt>Earlier automatic choice</dt>
            <dd>
              {field.destination === 'description' ? (
                <FormattedDescription value={earlier}>{earlier}</FormattedDescription>
              ) : (
                valueText(field.destination, earlier)
              )}
            </dd>
          </div>
        ) : null}
      </dl>
      <button
        type="button"
        className="choice-evidence-toggle"
        aria-controls={evidenceId}
        aria-expanded={expanded}
        onClick={toggle}
      >
        {expanded ? 'Hide evidence' : 'Evidence'}
      </button>
      {pin ? <PinButton {...pin} describedBy={valueId} /> : null}
      {expanded ? (
        <ChoiceEvidencePanel
          context={context}
          evidenceId={evidenceId}
          label={`${destinationLabel(field.destination)} current value`}
        />
      ) : null}
    </section>
  )
}

function emptyCurrentValue(field: ReviewField) {
  return field.currentRaw == null || (Array.isArray(field.currentRaw) && !field.currentRaw.length)
}

export function ProviderConsensusAutomaticStatement({ field }: { field: ReviewField }) {
  const decision = field.automaticDecision
  if (
    field.humanDecision !== null ||
    decision?.outcome !== 'candidate' ||
    !field.notices.includes('provider_consensus_contributor_display')
  ) {
    return null
  }
  return (
    <p>
      Sources write the names as {decisionValue(field)}. The library has {field.currentValue}.
    </p>
  )
}

export function DescriptionAutomaticStatement({ field }: { field: ReviewField }) {
  const decision = field.automaticDecision
  if (
    field.humanDecision !== null ||
    field.destination !== 'description' ||
    decision?.outcome !== 'candidate' ||
    field.proposedValues.length < 2
  ) {
    return null
  }
  const providers = field.automaticSelectionSources ?? []
  if (!providers.length) return null
  return <p>Chosen automatically from {providers.join(' and ')}, which matched this book.</p>
}

export function VerifiedManifestationIsbnAutomaticStatement({ field }: { field: ReviewField }) {
  if (
    field.humanDecision !== null ||
    field.destination !== 'isbn' ||
    field.automaticDecision?.outcome !== 'candidate' ||
    !field.notices.includes('verified_manifestation_isbn')
  ) {
    return null
  }
  return <p>This file’s own identifiers confirm this ISBN.</p>
}

export function SeriesAutomaticStatement({ field }: { field: ReviewField }) {
  const providers = field.automaticSelectionSources ?? []
  if (
    field.humanDecision !== null ||
    field.destination !== 'series' ||
    field.automaticDecision?.outcome !== 'candidate' ||
    !providers.length ||
    !emptyCurrentValue(field)
  ) {
    return null
  }
  const memberships = field.automaticDecision.selected_value?.value
  const hasNumber =
    Array.isArray(memberships) &&
    memberships.some(
      (membership: unknown) =>
        typeof membership === 'object' &&
        membership !== null &&
        'sequence' in membership &&
        typeof membership.sequence === 'string',
    )
  return (
    <p>
      Chosen from {providers.join(' and ')} to fill the empty Series field.{' '}
      {hasNumber ? 'Nothing else confirms the series number.' : 'No source gave a series number.'}
    </p>
  )
}

export function NarratorsAutomaticStatement({ field }: { field: ReviewField }) {
  const providers = field.automaticSelectionSources ?? []
  if (
    field.humanDecision !== null ||
    field.destination !== 'narrators' ||
    field.automaticDecision?.outcome !== 'candidate' ||
    !providers.length ||
    !emptyCurrentValue(field)
  ) {
    return null
  }
  return (
    <p>
      Chosen from {providers.join(' and ')}, which give the same cast, to fill the empty Narrators
      field.
    </p>
  )
}

export function AsinAutomaticStatement({ field }: { field: ReviewField }) {
  const providers = field.automaticSelectionSources ?? []
  if (
    field.humanDecision !== null ||
    field.destination !== 'asin' ||
    field.automaticDecision?.outcome !== 'candidate' ||
    !providers.length ||
    !emptyCurrentValue(field)
  ) {
    return null
  }
  return (
    <p>
      Chosen from {providers.join(' and ')} to fill the empty ASIN field. The running time confirms
      this recording is that product.
    </p>
  )
}

export function RecordedOutcome({
  field,
  mediaKind,
  expanded,
  toggle,
  pin,
}: {
  field: ReviewField
  mediaKind: string
  expanded: boolean
  toggle: () => void
  // The pin beside a recorded value the library already shows (#1346).
  pin?: PinControl | undefined
}) {
  const decision = field.humanDecision ?? field.automaticDecision
  if (!decision) return null
  const proposed = field.proposedValues.findIndex((value) =>
    sameSelectedValue(value, decision.selected_value),
  )
  const selectedProposed =
    (decision.outcome === 'accept' || decision.outcome === 'candidate') && proposed >= 0
  const context = selectedAutomaticLanguageForm(field)
    ? field.choiceContexts.find((item) => item.recommendation === 'recommended')
    : selectedProposed
      ? choiceContext(
          field,
          field.choiceContexts.filter((item) => item.selection === 'proposed')[proposed]
            ?.alternativeId ?? null,
        )
      : choiceContext(field, null)
  const combinedTags = combinedAutomaticTags(field, mediaKind)
  const evidenceId = `recorded-evidence-${field.destination}`
  const valueId = `recorded-value-${field.destination}`
  const pinned = pin && field.pin?.state === 'in_force' ? ' · Pinned' : ''
  const accepted = decision.outcome === 'accept' || decision.outcome === 'candidate'
  // A recorded description shows the formatted copy it is written with, in
  // place of its visible text, which names and sentences keep (#1995).
  const recordedFormatted =
    field.destination !== 'description'
      ? null
      : authoredOwnerRecorded(field) && field.descriptionAdvice?.selected_advice
        ? (field.descriptionAdvice.selected_advice.formatted ?? null)
        : selectedProposed
          ? (field.proposedFormatted?.[proposed] ?? null)
          : decision.outcome === 'keep_current'
            ? (field.currentFormatted ?? null)
            : null
  const recordedDescription = recordedFormatted ?? decisionValue(field)
  const evidenceToggle = (
    <button
      type="button"
      className="choice-evidence-toggle"
      aria-controls={evidenceId}
      aria-expanded={expanded}
      onClick={toggle}
    >
      {expanded ? 'Hide evidence' : 'Evidence'}
    </button>
  )
  return (
    <section
      className="recorded-outcome"
      aria-label={`${destinationLabel(field.destination)} recorded outcome`}
    >
      <p>
        <strong>
          {field.humanDecision === null && field.automaticDecision !== null && !field.manualHold
            ? 'Automatic'
            : completionOutcome(field)}
        </strong>
      </p>
      <dl>
        {accepted ? (
          <div>
            <dt>Chosen value{pinned}</dt>
            <dd id={valueId}>
              {field.destination === 'description' ? (
                <DescriptionText key={recordedDescription} value={recordedDescription} />
              ) : (
                decisionValue(field)
              )}
            </dd>
          </div>
        ) : (
          <div>
            <dt>
              {decision.outcome === 'keep_current'
                ? `Kept value${pinned}`
                : decision.outcome === 'owner_entered'
                  ? field.pin && field.pin.state !== 'changed'
                    ? 'Your value · Pinned'
                    : 'Your value'
                  : `Current value${pinned}`}
            </dt>
            <dd id={valueId}>
              {field.destination === 'description' ? (
                <DescriptionText key={recordedDescription} value={recordedDescription} />
              ) : (
                decisionValue(field)
              )}
            </dd>
          </div>
        )}
      </dl>
      {selectedAutomaticLanguageForm(field) ? (
        <p>Automatic chose this way of writing the same language.</p>
      ) : null}
      {combinedTags ? (
        <p>Automatic combined the sources’ tags and kept the existing ones.</p>
      ) : null}
      <ProviderConsensusAutomaticStatement field={field} />
      <DescriptionAutomaticStatement field={field} />
      <SeriesAutomaticStatement field={field} />
      <NarratorsAutomaticStatement field={field} />
      <AsinAutomaticStatement field={field} />
      <VerifiedManifestationIsbnAutomaticStatement field={field} />
      {pin ? (
        <div className="recorded-outcome-actions">
          {evidenceToggle}
          <PinButton {...pin} describedBy={valueId} />
        </div>
      ) : (
        evidenceToggle
      )}
      {expanded ? (
        combinedTags ? (
          <div id={evidenceId}>
            {field.choiceContexts
              .filter((item) => item.evidence.length > 0)
              .map((item, index) => (
                <ChoiceEvidencePanel
                  key={item.alternativeId ?? 'current'}
                  context={ownerContext(field, item)}
                  evidenceId={`${evidenceId}-${String(index)}`}
                  label={
                    item.selection === 'current'
                      ? 'Existing tags'
                      : `Source tags ${String(index + 1)}`
                  }
                />
              ))}
          </div>
        ) : (
          <ChoiceEvidencePanel
            context={context}
            evidenceId={evidenceId}
            label={`${destinationLabel(field.destination)} recorded outcome`}
          />
        )
      ) : null}
    </section>
  )
}
