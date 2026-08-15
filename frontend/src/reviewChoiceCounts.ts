import type { CaseDetailView } from './api/http'

type ReviewField = CaseDetailView['fields'][number]

export const ownerChoiceCount = (field: ReviewField) => {
  if (field.destination !== 'cover')
    return field.preservationOnly ? 2 : field.proposedValues.length + 2
  const consideration = field.coverConsideration
  if (!consideration) return 0
  const candidates = consideration.candidates.filter(
    (candidate) => candidate.selectable || candidate.incumbent,
  )
  const unavailableIncumbent =
    consideration.incumbent.state === 'unavailable' &&
    !candidates.some((candidate) => candidate.incumbent)
  return candidates.length + (unavailableIncumbent ? 1 : 0) + 1
}
