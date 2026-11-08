// A Library field's settled reading: its current value, its recorded outcome
// and the automatic statements beside it. Moved out of the Library route under
// the route-size rule (#1571).
import { displayValue } from '../api/http'
import { completionOutcome, decisionValue, sameSelectedValue, valueText } from '../fieldDecision'
import type { ReviewField } from '../fieldDecision'
import {
  choiceContext,
  combinedAutomaticTags,
  fieldReason,
  ownerContext,
  selectedAutomaticLanguageForm,
} from '../fieldReasons'
import { destinationLabel } from '../labels'
import { ChoiceEvidencePanel, DescriptionText } from './FieldDecisionChoices'

// The Library field panel for a field with nothing new to choose: why it is
// shown and its current value. Moved out of the Library route under the
// route-size rule (#1575).
export function SettledFieldContext({
  field,
  actionable,
  ownerEntry,
  expanded,
  toggle,
}: {
  field: ReviewField
  actionable: boolean
  ownerEntry: boolean
  expanded: boolean
  toggle: () => void
}) {
  return (
    <>
      {actionable || !ownerEntry || field.earlierAutomaticValue ? (
        <p className={actionable ? 'decision-recorded' : 'decision-context'}>
          {actionable
            ? `${fieldReason(field)}${field.humanDecision || field.automaticDecision ? ` The recorded value remains ${decisionValue(field)}.` : ''}`
            : field.earlierAutomaticValue
              ? 'Automatic chose a value before you entered Preview mode. Automatic doesn’t write in Preview, and turning it on decides this field again.'
              : 'No source offered a different value.'}
        </p>
      ) : null}
      {!actionable ? <ContextValue field={field} expanded={expanded} toggle={toggle} /> : null}
    </>
  )
}

export function ContextValue({
  field,
  expanded,
  toggle,
}: {
  field: ReviewField
  expanded: boolean
  toggle: () => void
}) {
  const context = choiceContext(field, null)
  const evidenceId = `context-evidence-${field.destination}`
  return (
    <section
      className="context-value"
      aria-label={`${destinationLabel(field.destination)} current value`}
    >
      <dl>
        <div>
          <dt>Current value</dt>
          <dd>
            {field.destination === 'description' ? (
              <DescriptionText key={field.currentValue} value={field.currentValue} />
            ) : (
              valueText(field.destination, field.currentValue)
            )}
          </dd>
        </div>
        {field.earlierAutomaticValue ? (
          <div>
            <dt>Earlier automatic choice</dt>
            <dd>{valueText(field.destination, displayValue(field.earlierAutomaticValue.value))}</dd>
          </div>
        ) : null}
      </dl>
      <button
        type="button"
        className="context-evidence-toggle"
        aria-controls={evidenceId}
        aria-expanded={expanded}
        onClick={toggle}
      >
        {expanded ? 'Hide evidence' : 'Evidence'}
      </button>
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
}: {
  field: ReviewField
  mediaKind: string
  expanded: boolean
  toggle: () => void
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
  const accepted = decision.outcome === 'accept' || decision.outcome === 'candidate'
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
            <dt>Chosen value</dt>
            <dd>
              {field.destination === 'description' ? (
                <DescriptionText key={decisionValue(field)} value={decisionValue(field)} />
              ) : (
                decisionValue(field)
              )}
            </dd>
          </div>
        ) : (
          <div>
            <dt>
              {decision.outcome === 'keep_current'
                ? 'Kept value'
                : decision.outcome === 'owner_entered'
                  ? 'Your value'
                  : 'Current value'}
            </dt>
            <dd>
              {field.destination === 'description' ? (
                <DescriptionText key={decisionValue(field)} value={decisionValue(field)} />
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
      <button
        type="button"
        className="context-evidence-toggle"
        aria-controls={evidenceId}
        aria-expanded={expanded}
        onClick={toggle}
      >
        {expanded ? 'Hide evidence' : 'Evidence'}
      </button>
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
