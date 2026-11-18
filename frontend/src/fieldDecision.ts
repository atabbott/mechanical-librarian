import type {
  CaseDetailView,
  CoverCandidateView,
  DecisionCommand,
  TargetDestination,
} from './api/http'
import { CLEARED_TEXT, displayValue, isClear } from './api/http'
import { coverSourceLabels, destinationPhrase, languageName, writeHoldReasons } from './labels'
import { automaticChoiceSelected, humanChoiceSelected } from './reviewAutomaticSelection'
import { isFormatted } from './descriptionFormatting'

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
  // The original a switch replaced is named for what it is, not a source (#1363).
  const base = (item: (typeof proposed)[number]) =>
    item.sourceKinds.includes('retained')
      ? coverSourceLabels.retained
      : `${item.sourceKinds.map((kind) => coverSourceLabels[kind]).join(' and ') || 'Proposed'} cover`
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
        detail: 'Activity lists it.',
      }
    if (advice.application_outcome === 'no_change_required')
      return {
        state: 'No change needed',
        detail: 'Activity lists it.',
      }
    if (!advice.selected_advice?.current)
      return {
        state: 'Earlier choice',
        detail: 'Activity shows whether it was written.',
      }
    // The advice card already says Activity shows whether the library was
    // changed, so the box adds nothing while writing is on (L5).
    return field.effectCapability === 'allowed'
      ? { state: 'Choice recorded', detail: null }
      : field.effectCapability === 'unavailable'
        ? {
            state: 'Choice recorded · Not written',
            detail: 'No library connection is set up to write it.',
          }
        : {
            state: 'Choice recorded · Paused',
            detail: `Writing is paused because ${writeHoldReasons[field.effectCapability]}.`,
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
          state: 'Change recorded · Not written',
          detail: 'No library connection is set up to write it.',
        }
      : {
          state: 'Change recorded · Paused',
          detail: `Writing is paused because ${writeHoldReasons[field.effectCapability]}.`,
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
// A recorded pick is Automatic's while the owner has not decided, no carried
// hold pauses it and no press of theirs is pending, so its card says so (#1765).
export const automaticallySelected = (field: ReviewField, progress: ChoiceProgress) =>
  (progress === null || progress.phase === 'failed') &&
  field.humanDecision === null &&
  !field.manualHold &&
  field.automaticDecision !== null
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
// Fields an owner may leave empty (ADR-0105); the store's own list.
const CLEARABLE: readonly TargetDestination[] = ['subtitle', 'publisher', 'genres', 'narrators']
const emptyRaw = (value: unknown) =>
  value === null ||
  value === undefined ||
  value === '' ||
  (Array.isArray(value) && value.length === 0)
const sameNames = (left: readonly string[], right: unknown) =>
  Array.isArray(right) &&
  left.length === right.length &&
  left.every((name, index) => name === right[index])
const sameGenres = (left: readonly string[], right: unknown) =>
  Array.isArray(right) &&
  left.length === right.length &&
  left.every((genre) => right.includes(genre))
// Whether owner entry already offers the way back to the value the case read
// before its write: Clear for an empty one, or that value as the previous
// value, names or genres (#1523).
const wayBackInEntry = (field: ReviewField) => {
  const control = field.ownerControl
  const before = field.currentRaw
  if (!control) return false
  if (emptyRaw(before)) return control.clear
  if (control.entry?.suggestions.some(({ rule, value }) => rule === 'previous' && value === before))
    return true
  if (control.names?.previous && sameNames(control.names.previous, before)) return true
  const previousGenres =
    control.subset?.options.filter((option) => option.previous).map((option) => option.value) ?? []
  return previousGenres.length > 0 && sameGenres(previousGenres, before)
}
// Whether a field this case wrote had nothing before the write and can't be
// emptied, so there is no way back to that value (#1523).
export const noWayBack = (field: ReviewField) =>
  field.writtenByCase === true &&
  field.decisionActions.includes('keep_current') &&
  emptyRaw(field.currentRaw) &&
  !CLEARABLE.includes(field.destination)
// The card that keeps the value the case read. Elsewhere it is Keep current
// value. On the case that wrote the field that value is no longer the
// library's, so it reads Before your change, and pressing it writes that value
// back. It is left out where owner entry already offers the same way back, and
// where there is none: nothing to restore and a field that can't be emptied
// (#1523, ADR-0105).
export const keepCard = (field: ReviewField): 'keep' | 'before' | null =>
  !field.decisionActions.includes('keep_current')
    ? null
    : field.writtenByCase !== true
      ? 'keep'
      : wayBackInEntry(field) || noWayBack(field)
        ? null
        : 'before'
export const keepOffered = (field: ReviewField) => keepCard(field) !== null
// A description that carries formatting reads as body text, as a long one does.
export const isLongDescription = (destination: string, value: string) =>
  destination === 'description' && (value.length > 280 || isFormatted(value))
export const decisionQuestion = (destination: TargetDestination) =>
  `Which ${destinationPhrase(destination)} should this work use?`
export const audioFileFormat = (format: string) => ['m4b', 'm4a', 'mp3'].includes(format)
export const authoredOwnerRecorded = (field: ReviewField) =>
  field.descriptionAdvice?.selection?.origin === 'human' &&
  Boolean(
    field.descriptionAdvice.selected_advice?.current === true ||
    field.descriptionAdvice.application_outcome,
  )
// A language value reads by its name beside its code, `English (en)` (#1286).
export const valueText = (destination: TargetDestination, value: string) =>
  destination === 'language' ? languageName(value) : value
export const decisionValue = (field: ReviewField) => {
  if (authoredOwnerRecorded(field) && field.descriptionAdvice?.selected_advice)
    return field.descriptionAdvice.selected_advice.candidate.output.description
  // A hold carried from an earlier case keeps the library's value (ADR-0085).
  const selected = (field.humanDecision ?? (field.manualHold ? null : field.automaticDecision))
    ?.selected_value
  if (field.destination === 'cover') {
    const candidates = field.coverConsideration?.candidates ?? []
    const candidate = candidates.find((item) => item.artifactId === selected?.value)
    return candidate?.incumbent || !selected
      ? 'Current cover'
      : candidate
        ? (coverNames(candidates).get(candidate.reference) ?? 'Selected cover')
        : 'Selected cover'
  }
  return valueText(
    field.destination,
    selected === null || selected === undefined
      ? field.currentValue
      : isClear(selected)
        ? CLEARED_TEXT
        : displayValue(selected.value),
  )
}
export const completionOutcome = (field: ReviewField) =>
  authoredOwnerRecorded(field)
    ? 'Adviser’s description chosen'
    : field.humanDecision?.outcome === 'reject'
      ? 'Change declined'
      : field.humanDecision?.outcome === 'keep_current'
        ? 'Current value kept'
        : field.humanDecision?.outcome === 'accept'
          ? 'Proposed value chosen'
          : 'Decided'
