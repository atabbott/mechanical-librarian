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
