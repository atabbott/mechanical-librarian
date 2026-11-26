// Why a Library field reads as it does: the reason under its question, the
// comparison conditions beside it, and how differing source values are named.
// Moved out of the Library route under the route-size rule (#1497).
import { EMPTY_TARGET_TEXT, displayValue, isClear } from './api/http'
import type {
  AskedAgainView,
  AutomaticOutcome,
  ChoiceContextView,
  DecisionOutcome,
  FieldCause,
  ProposalOutcome,
} from './api/http'
import { formatDate } from './dates'
import type { DateZone } from './dates'
import { audioFileFormat, sameSelectedValue } from './fieldDecision'
import type { ReviewField } from './fieldDecision'
import { choiceRequestReasons, materialReasonLabel, opaqueSource } from './labels'
import { actionable } from './reviewDecisionController'

export const bounded = (value: string, fallback: string) =>
  opaqueSource.test(value) ? fallback : value.length > 160 ? `${value.slice(0, 157)}…` : value
// Why a field reads as it does when the server gives no reason of its own.
export const humanReasons = {
  accept: 'You chose a proposed value for this field.',
  keep_current: 'You kept the current value for this field.',
  reject: 'You declined the proposed change for this field.',
  owner_entered: 'You entered a value for this field.',
  withdraw: 'You withdrew your decision for this field.',
  // A field's decision is never a pin or an unpin: the server reads a pin
  // from its hold (ADR-0085, Amendment 2).
  pin: 'You pinned this field.',
  unpin: 'You unpinned this field.',
} satisfies Record<DecisionOutcome, string>
export const automaticReasons = {
  candidate: 'Automatic chose a proposed value.',
  keep_current: 'Automatic kept the current value.',
  ignore: 'Automatic leaves this field as it is.',
} satisfies Record<AutomaticOutcome, string>
export const dispositionReasons = {
  reject: 'The proposed change was set aside. Compare the values yourself.',
  propose_change: 'A change is proposed. Compare it with the current value.',
  acceptable_choice: 'More than one value would do. Choose the one this work should use.',
  unresolved_choice: 'The sources disagree.',
  keep: 'The current value is proposed to stay. Compare the other values.',
  defer: 'This field waits for your choice.',
  ineligible: 'None of these values can be applied for you.',
} satisfies Record<ProposalOutcome, string>
export const fieldReason = (field: ReviewField) =>
  bounded(
    field.humanDecision
      ? field.classificationReason || humanReasons[field.humanDecision.outcome]
      : field.automaticDecision
        ? field.classificationReason || automaticReasons[field.automaticDecision.outcome]
        : actionable(field)
          ? field.classificationReason || dispositionReasons[field.disposition]
          : field.classificationReason && field.classificationCause !== 'review_needed'
            ? field.classificationReason
            : 'Nothing to decide for this field.',
    'No reason can be shown.',
  )
// A field with nothing to decide is either unchanged, because no source
// offered a different value, or held: unavailable to this service, or waiting
// on a recovery. The server's cause code tells them apart, never the wording of
// its reason, so a reworded reason moves no row (#1514, #1791). A new code
// fails the typecheck here until its row is decided.
const contextKinds = {
  unsupported: 'unavailable',
  automatic_safety_suspended: 'waiting',
  external_field_failure: 'waiting',
  external_connection_failure: 'waiting',
  external_recovered: 'unavailable',
  human_decided: 'unavailable',
  automatic_abstained: 'unavailable',
  automatic_decided: 'unavailable',
  review_needed: 'unavailable',
  owner_choosable: 'unavailable',
  nothing_to_decide: 'unchanged',
  pinned: 'unchanged',
  pin_changed: 'unavailable',
  // Never contextual: a held field needs a value a source offered.
  held_in_preview: 'unchanged',
  asked_again: 'unavailable',
} as const satisfies Record<FieldCause, 'unchanged' | 'waiting' | 'unavailable'>
// A held field never reads unchanged.
export const contextKind = (field: ReviewField) => {
  const kind = contextKinds[field.classificationCause]
  return field.manualHold && kind === 'unchanged' ? 'unavailable' : kind
}
// Whether a source offered a value other than the current one, read from the
// server's facts, never from its reason's wording.
export const offeredAnotherValue = (field: ReviewField) =>
  field.proposedValues.length > 0 && !field.preservationOnly
// An optional choice already says no review is needed, so a limitation worded
// as asking the owner to choose is left off its evidence too (#1557).
export const ownerContext = (field: ReviewField, context: ChoiceContextView) =>
  field.classification === 'owner_choosable'
    ? {
        ...context,
        limitations: context.limitations.filter(
          (limitation) => !choiceRequestReasons.has(limitation),
        ),
      }
    : context
const nothingToApplyReasons: ReadonlySet<string> = new Set([
  ...choiceRequestReasons,
  'no_safe_bibliographic_combination',
])
// An optional choice already says no review is needed, so a condition worded
// as asking the owner to choose is left off it (#1450). Other conditions still
// explain the choice, such as a language respelling (#1496, #1557).
// A field no source offered a different value for has nothing to choose or
// apply, so a condition asking the owner to choose or saying no combination is
// safe to apply is left off it; one saying why the current value is kept stays
// (#1924, #2012).
export const comparisonNotices = (field: ReviewField) =>
  field.choiceContexts.some((context) =>
    ownerContext(field, context).limitations.some(
      (limitation) => materialReasonLabel(limitation) !== undefined,
    ),
  )
    ? []
    : field.notices.filter(
        (notice) =>
          materialReasonLabel(notice) !== undefined &&
          !restatesReason(field, notice) &&
          !(field.classification === 'owner_choosable' && choiceRequestReasons.has(notice)) &&
          !(!offeredAnotherValue(field) && nothingToApplyReasons.has(notice)),
      )
// A comparison condition in words; differing source tag lists or synopses are
// named as such rather than as a disagreement.
export const noticeText = (field: ReviewField, notice: string, mediaKind: string) =>
  notice === 'competing_evidence' && combinedAutomaticTags(field, mediaKind)
    ? 'Different source tag lists'
    : notice === 'competing_evidence' && selectedAutomaticSynopsis(field, mediaKind)
      ? 'Different source descriptions'
      : notice === 'competing_evidence' && loneCandidateForEmpty(field)
        ? UNOFFERED_DISSENT_TEXT
        : materialReasonLabel(notice)
export const UNOFFERED_DISSENT_TEXT =
  'Another source gave a value that isn’t offered here. Choose whether to use this one'
// One candidate for an empty field shows no disagreement: the value it
// disagrees with is not offered, such as one in another language (#1497).
export const loneCandidateForEmpty = (field: ReviewField) =>
  field.proposedValues.length === 1 && field.currentValue === EMPTY_TARGET_TEXT
// A notice that says again what the reason under the question already says is
// left out: the condition that is that line (#1519). The server names the
// condition its reason states, so either sentence can be reworded (#1802).
export const restatesReason = (field: ReviewField, notice: string) =>
  notice === questionNotice(field) || notice === field.automaticWithheldCondition
// Under an actionable field's question, one line gives its own reason: why it
// was withheld, else its first comparison condition, else its disposition. The
// classification sentence, the same on every field, is only the last fallback
// (#1519).
export const questionNotice = (field: ReviewField) =>
  actionable(field) && field.automaticWithheldReason === null
    ? field.notices.find((notice) => materialReasonLabel(notice) !== undefined)
    : undefined
// Why the record's earlier answer is asked again (#2039), in one sentence
// that names the answer and its day; undefined where the field is not asked
// again, or for a decline whose sentence would need its value. The verb is
// the earlier outcome's; a clear, or a keep of an empty field, quotes nothing,
// as the owner controls do; a decline has no value and names the proposed
// change instead. A long value is cut as `bounded` cuts a reason.
const askedAgainVerbs = {
  accept: 'chose',
  owner_entered: 'entered',
  keep_current: 'kept',
  reject: 'declined',
} as const satisfies Record<AskedAgainView['earlier']['outcome'], string>
const askedAgainValue = (value: AskedAgainView['earlier']['value']) => {
  if (!value || isClear(value)) return 'nothing'
  const shown = displayValue(value.value)
  return `“${shown.length > 160 ? `${shown.slice(0, 157)}…` : shown}”`
}
export const askedAgainSentence = (
  askedAgain: AskedAgainView | null | undefined,
  now: Date = new Date(),
  zone: DateZone = {},
) => {
  if (!askedAgain) return undefined
  const { reason, earlier } = askedAgain
  const day = formatDate(earlier.occurred_at, now, zone)
  const declined = earlier.outcome === 'reject'
  const answer = declined
    ? `${askedAgainVerbs.reject} the proposed change`
    : `${askedAgainVerbs[earlier.outcome]} ${askedAgainValue(earlier.value)}`
  switch (reason) {
    case 'question_changed':
      return `The choices changed since you ${answer} on ${day}.`
    case 'target_changed':
      return `Audiobookshelf shows a different value from when you ${answer} on ${day}.`
    case 'withdrawn':
      return `You withdrew your later choice. Before it, you ${answer} on ${day}.`
    case 'resume_failed':
      return `Resume automatic handling did not finish. You ${answer} on ${day}.`
    case 'retired':
      return declined
        ? undefined
        : `Your choice of ${askedAgainValue(earlier.value)} on ${day} was not written before the upgrade.`
    case 'matching_changed':
      return `Book matching changed what this field offers since you ${answer} on ${day}.`
    case 'other':
      return `You ${answer} on ${day}. This field asks you again.`
  }
}
// A carried answer's line in History: the day of the choice it kept (#2039).
export const keptFromSentence = (seconds: number, now: Date = new Date(), zone: DateZone = {}) =>
  `Kept from your choice on ${formatDate(seconds, now, zone)}, because the same choices were offered.`
export const questionReason = (field: ReviewField, mediaKind: string) => {
  // The record's earlier answer asked again says why, ahead of all else.
  const askedAgain = askedAgainSentence(field.askedAgain)
  if (askedAgain) return askedAgain
  // A changed pin is the question: the server's sentence says what changed.
  if (field.pin?.state === 'changed' && field.classificationReason)
    return field.classificationReason
  const notice = questionNotice(field)
  const label = notice === undefined ? undefined : noticeText(field, notice, mediaKind)
  return (
    field.automaticWithheldReason ??
    (label
      ? `${label}.`
      : ((dispositionReasons as Partial<Record<string, string>>)[field.disposition] ??
        fieldReason(field)))
  )
}

export const combinedAutomaticTags = (field: ReviewField, mediaKind: string) =>
  audioFileFormat(mediaKind) &&
  field.humanDecision === null &&
  field.destination === 'tags' &&
  field.automaticDecision?.outcome === 'candidate' &&
  !field.proposedValues.some((value) =>
    sameSelectedValue(value, field.automaticDecision?.selected_value),
  )

export const selectedAutomaticSynopsis = (field: ReviewField, mediaKind: string) =>
  audioFileFormat(mediaKind) &&
  field.destination === 'description' &&
  field.humanDecision === null &&
  field.automaticDecision?.outcome === 'candidate' &&
  field.proposedValues.some((value) =>
    sameSelectedValue(value, field.automaticDecision?.selected_value),
  )

export const selectedAutomaticLanguageForm = (field: ReviewField) =>
  field.destination === 'language' &&
  field.humanDecision === null &&
  field.automaticDecision?.outcome === 'candidate' &&
  !field.proposedValues.some((value) =>
    sameSelectedValue(value, field.automaticDecision?.selected_value),
  )
export const choiceContext = (field: ReviewField, alternativeId: string | null) => {
  const context = field.choiceContexts.find((item) => item.alternativeId === alternativeId)
  return context && ownerContext(field, context)
}
