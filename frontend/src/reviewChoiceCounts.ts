import type { CaseDetailView } from './api/http'

type ReviewField = CaseDetailView['fields'][number]

export const ownerChoiceCount = (field: ReviewField) => {
  const accepts = field.decisionActions.includes('accept')
  const keeps = field.decisionActions.includes('keep_current')
  if (field.destination !== 'cover')
    return (keeps ? 1 : 0) + (accepts ? field.proposedValues.length : 0)
  const consideration = field.coverConsideration
  if (!consideration) return keeps ? 1 : 0
  const candidates = consideration.candidates.filter(
    (candidate) => candidate.selectable || candidate.incumbent,
  )
  const incumbent = candidates.some((candidate) => candidate.incumbent)
  return (keeps ? 1 : 0) + (accepts ? candidates.length - (incumbent ? 1 : 0) : 0)
}
