// Why a Library field reads as it does: the reason under its question, the
// comparison conditions beside it, and how differing source values are named.
// Moved out of the Library route under the route-size rule (#1497).
import { EMPTY_TARGET_TEXT } from './api/http'
import type {
  AutomaticOutcome,
  ChoiceContextView,
  DecisionOutcome,
  ProposalOutcome,
} from './api/http'
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
} satisfies Record<DecisionOutcome, string>
export const automaticReasons = {
  candidate: 'Automatic policy chose a proposed value.',
  keep_current: 'Automatic policy kept the current value.',
  ignore: 'Automatic policy leaves this field unchanged.',
} satisfies Record<AutomaticOutcome, string>
export const dispositionReasons = {
  reject: 'The proposed change was set aside; compare the values yourself.',
  propose_change: 'A change is proposed; compare it with the current value.',
  acceptable_choice: 'More than one value is acceptable; choose the one this work should use.',
  unresolved_choice: 'The sources do not settle one value; compare the values yourself.',
  keep: 'The current value is proposed to stay; compare the alternatives.',
  defer: 'This field waits for your comparison of the values.',
  ineligible: 'None of these values can be applied for you; compare them yourself.',
} satisfies Record<ProposalOutcome, string>
export const fieldReason = (field: ReviewField) =>
  bounded(
    field.humanDecision
      ? field.classificationReason || humanReasons[field.humanDecision.outcome]
      : field.automaticDecision
        ? field.classificationReason || automaticReasons[field.automaticDecision.outcome]
        : actionable(field)
          ? field.classificationReason || dispositionReasons[field.disposition]
          : field.classificationReason &&
              !/needs (?:your )?review/iu.test(field.classificationReason)
            ? field.classificationReason
            : 'No eligible owner decision is available.',
    'Retained reviewer notice',
  )
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
// An optional choice already says no review is needed, so a condition worded
// as asking the owner to choose is left off it (#1450). Other conditions still
// explain the choice, such as a language respelling (#1496, #1557).
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
          !(field.classification === 'owner_choosable' && choiceRequestReasons.has(notice)),
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
  'Another source gave a value that isn’t offered here; choose whether to use this one'
// One candidate for an empty field shows no disagreement: the value it
// disagrees with is not offered, such as one in another language (#1497).
export const loneCandidateForEmpty = (field: ReviewField) =>
  field.proposedValues.length === 1 && field.currentValue === EMPTY_TARGET_TEXT
// A notice that says again what the reason under the question already says is
// left out: the condition that is that line (#1519).
export const restatesReason = (field: ReviewField, notice: string) =>
  notice === questionNotice(field) ||
  (field.automaticWithheldReason !== null &&
    (`${materialReasonLabel(notice) ?? ''}.` === field.automaticWithheldReason ||
      (notice === 'competing_evidence' &&
        loneCandidateForEmpty(field) &&
        field.automaticWithheldReason === `${UNOFFERED_DISSENT_TEXT}.`)))
// Under an actionable field's question, one line gives its own reason: why it
// was withheld, else its first comparison condition, else its disposition. The
// classification sentence, the same on every field, is only the last fallback
// (#1519).
export const questionNotice = (field: ReviewField) =>
  actionable(field) && field.automaticWithheldReason === null
    ? field.notices.find((notice) => materialReasonLabel(notice) !== undefined)
    : undefined
export const questionReason = (field: ReviewField, mediaKind: string) => {
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
