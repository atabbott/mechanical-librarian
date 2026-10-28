import type { CaseDetailView } from './api/http'

type ReviewField = CaseDetailView['fields'][number]

// The work rail shows primary fields and folds away one that is empty with
// nothing proposed until the owner asks for it. `railOffers` is the fields a
// case offers before that: Library and Detail name a book-level note's fields
// through it, so one case names the same fields on both pages (#1286).
export const primaryField = (field: ReviewField) => (field.inventoryRole ?? 'primary') === 'primary'
export const emptyUnproposed = (field: ReviewField) =>
  (field.currentRaw === null ||
    field.currentRaw === '' ||
    (Array.isArray(field.currentRaw) && field.currentRaw.length === 0)) &&
  field.proposedValues.length === 0
export const railOffers = (field: ReviewField) => primaryField(field) && !emptyUnproposed(field)

export const ownerChoiceCount = (field: ReviewField) => {
  const accepts = field.decisionActions.includes('accept')
  const keeps = field.decisionActions.includes('keep_current')
  if (field.destination !== 'cover')
    return (
      (keeps ? 1 : 0) +
      (accepts ? field.proposedValues.length : 0) +
      (field.editable && field.descriptionAdvice?.current && field.descriptionAdvice.candidate
        ? 1
        : 0)
    )
  const consideration = field.coverConsideration
  if (!consideration) return keeps ? 1 : 0
  if (!consideration.current || !consideration.ready) return 0
  const candidates = consideration.candidates.filter(
    (candidate) => candidate.manualEligible || candidate.incumbent,
  )
  const incumbent = candidates.some((candidate) => candidate.incumbent)
  return (keeps ? 1 : 0) + (accepts ? candidates.length - (incumbent ? 1 : 0) : 0)
}
