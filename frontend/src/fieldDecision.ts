import type {
  CaseDetailView,
  CoverCandidateView,
  DecisionCommand,
  TargetDestination,
} from './api/http'
import { coverSourceLabels, destinationPhrase, writeHoldReasons } from './labels'
import { automaticChoiceSelected, humanChoiceSelected } from './reviewAutomaticSelection'

export type ReviewField = CaseDetailView['fields'][number]
// A carried hold pauses an automatic authored choice as it pauses a title's
// automatic decision, so nothing reads as chosen until Resume (ADR-0085).
export const shownAdvice = (field: ReviewField) =>
  field.manualHold &&
  field.humanDecision === null &&
  field.descriptionAdvice?.selection?.origin === 'automatic_policy'
    ? { ...field.descriptionAdvice, selection: null, selected_advice: null }
    : field.descriptionAdvice
// What the owner has chosen for one field and how far that choice has got.
// `null` means this field carries no choice of theirs in this session.
export type ChoiceProgress = {
  command: DecisionCommand
  phase: 'recording' | 'confirmed' | 'failed'
} | null
// A proposed cover is named by its sources, never "Alternative N" (#1519).
// Two covers from the same sources are told apart by their size, then by
// their place in the comparison, so every name stays unique.
export const coverNames = (
  candidates: readonly Pick<
    CoverCandidateView,
    'reference' | 'incumbent' | 'sourceKinds' | 'width' | 'height'
  >[],
) => {
  const proposed = candidates.filter((item) => !item.incumbent)
  const base = (item: (typeof proposed)[number]) =>
    `${item.sourceKinds.map((kind) => coverSourceLabels[kind]).join(' and ') || 'Proposed'} cover`
  const sized = (item: (typeof proposed)[number]) =>
    `${base(item)}, ${String(item.width)} × ${String(item.height)} px`
  const count = (name: (item: (typeof proposed)[number]) => string, value: string) =>
    proposed.filter((item) => name(item) === value).length
  return new Map(
    proposed.map((item, index) => [
      item.reference,
      count(base, base(item)) === 1
        ? base(item)
        : count(sized, sized(item)) === 1
          ? sized(item)
          : `${sized(item)} (${String(index + 1)})`,
    ]),
  )
}
export const effectSummary = (field: ReviewField) => {
  const advice = field.destination === 'description' ? shownAdvice(field) : null
  if (advice?.selection) {
    if (advice.application_outcome === 'verified')
      return {
        state: 'Applied and verified',
        detail:
          'This authored description was applied and verified in the library. Its record is retained in History.',
      }
    if (advice.application_outcome === 'no_change_required')
      return {
        state: 'No change needed',
        detail:
          'The library already held the selected description. This authored choice is retained in History.',
      }
    if (!advice.selected_advice?.current)
      return {
        state: 'Earlier choice retained',
        detail:
          'This choice is no longer current. History retains the choice; Activity shows any recorded application progress.',
      }
    return field.effectCapability === 'allowed'
      ? {
          state: 'Choice recorded',
          detail: "Activity shows this change's progress.",
        }
      : field.effectCapability === 'unavailable'
        ? {
            state: 'Choice recorded · External update unavailable',
            detail:
              'No external update capability is configured. Activity shows any recorded application progress.',
          }
        : {
            state: 'Choice recorded · External update guarded',
            detail: `Writes are held because ${writeHoldReasons[field.effectCapability]}. Activity shows any recorded application progress.`,
          }
  }
  // Only a change the owner recorded for the library has an effect to report;
  // a field that is merely eligible, unavailable or guarded has none (#1519).
  const decision = field.humanDecision
  if (
    decision?.application !== 'apply' ||
    (decision.outcome !== 'accept' && decision.outcome !== 'owner_entered')
  )
    return undefined
  return field.effectCapability === 'allowed'
    ? { state: 'Change recorded', detail: "Activity shows this change's progress." }
    : field.effectCapability === 'unavailable'
      ? {
          state: 'Change recorded · External update unavailable',
          detail: 'No external update capability is configured.',
        }
      : {
          state: 'Change recorded · External update guarded',
          detail: `Writes are held because ${writeHoldReasons[field.effectCapability]}.`,
        }
}
export const sameSelectedValue = (
  left: DecisionCommand['selectedValue'],
  right: DecisionCommand['selectedValue'],
) =>
  left == null || right == null
    ? left === right
    : left.kind === right.kind && JSON.stringify(left.value) === JSON.stringify(right.value)
export const commandSelected = (
  command: DecisionCommand | null,
  destination: string,
  outcome: DecisionCommand['outcome'],
  selectedValue: DecisionCommand['selectedValue'] = null,
) =>
  command?.destination === destination &&
  !command.authoredDescription &&
  command.outcome === outcome &&
  sameSelectedValue(command.selectedValue, selectedValue)
// A recorded field is revisited with every option still shown, so which one
// is the recorded answer has to be readable. The owner's own decision is the
// answer whenever there is one; an automatic decision only speaks for a field
// they have not decided themselves.
const recordedSelected = (
  field: ReviewField,
  outcome: 'accept' | 'keep_current',
  selectedValue: DecisionCommand['selectedValue'] = null,
) =>
  field.descriptionAdvice &&
  (field.descriptionAdvice.selected_advice?.current === true ||
    field.descriptionAdvice.application_outcome !== null) &&
  field.descriptionAdvice.selection
    ? false
    : field.humanDecision !== null
      ? humanChoiceSelected(field.humanDecision, outcome, selectedValue)
      : // A carried hold pauses the automatic decision, so it chose nothing.
        !field.manualHold &&
        field.automaticDecision !== null &&
        automaticChoiceSelected(field.automaticDecision, outcome, selectedValue)
export const choiceSelected = (
  field: ReviewField,
  progress: ChoiceProgress,
  outcome: 'accept' | 'keep_current',
  selectedValue: DecisionCommand['selectedValue'] = null,
) =>
  (progress?.phase !== 'failed' &&
    commandSelected(progress?.command ?? null, field.destination, outcome, selectedValue)) ||
  ((progress === null || progress.phase === 'failed') &&
    recordedSelected(field, outcome, selectedValue))
export const isLongDescription = (destination: string, value: string) =>
  destination === 'description' && value.length > 280
export const decisionQuestion = (destination: TargetDestination) =>
  `Which ${destinationPhrase(destination)} should this work use?`
export const audioFileFormat = (format: string) => ['m4b', 'm4a', 'mp3'].includes(format)
