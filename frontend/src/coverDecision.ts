import { ApiError, refusalCopy, uncertainWriteOutcome } from './api/http'
import type { CoverWorkView, DecisionCommand } from './api/http'
import { submitDecisionCase } from './decisionEcho'
import type { ReviewField } from './fieldDecision'
import { mediaKindLabels } from './labels'
import { invalidateForegroundQueries } from './queryWork'
import { ownerRefusalCopy, requestKey } from './reviewDecisionController'
import type { useReviewContext } from './router'

// Covers' cover decisions (#1633, #1363): the work's own decision command,
// shared by the comparison and Switched's rows, and the words for a choice
// that did not record.

export type Outcome = 'switched' | 'kept' | 'changed' | 'put_back' | 'skipped'
export const staleCopy = "This work's covers changed since the page loaded. Nothing was changed."
const lostCopy =
  'No answer came back. Check whether your choice was recorded before choosing again.'
const refusedCopy = 'Your choice was not recorded. Choose again.'

export const byline = (work: CoverWorkView) =>
  [work.authors.join(', '), mediaKindLabels[work.media_kind]].filter(Boolean).join(' · ')
export const bookSearch = (reviewCaseId: string) => ({
  lens: 'all' as const,
  preview: reviewCaseId,
})

/** The original an automatic switch replaced, while it can still be put back (#1363). */
export const retainedCandidate = (field: ReviewField | undefined) =>
  field?.coverConsideration?.candidates.find(
    (candidate) => candidate.candidateKind === 'retained' && candidate.selectable,
  )
export type Failure = { kind: 'stale' } | { kind: 'message'; text: string }
export const failureOf = (error: unknown, field: ReviewField | undefined): Failure =>
  error instanceof ApiError && error.code === 'stale'
    ? { kind: 'stale' }
    : uncertainWriteOutcome(error)
      ? { kind: 'message', text: lostCopy }
      : { kind: 'message', text: ownerRefusalCopy(error, field) ?? refusalCopy(error, refusedCopy) }

// The book's own decision command, exactly as its page sends it.
export const coverCommand = (
  field: ReviewField,
  outcome: 'accept' | 'keep_current',
  selectedValue: NonNullable<DecisionCommand['selectedValue']> | null,
): DecisionCommand => ({
  destination: 'cover',
  outcome,
  proposalId: field.proposalId,
  selectedValue,
  targetFingerprint: field.targetFingerprint,
  offerDigest: field.offerDigest,
  idempotencyKey: requestKey(),
  ...(field.coverConsideration
    ? {
        considerationId: field.coverConsideration.id,
        coverConfirmation: {
          expected_selection_id: field.currentCoverSelectionId ?? null,
          confirm_unverified_match:
            outcome === 'accept' &&
            field.coverConsideration.candidates.some(
              (candidate) =>
                candidate.artifactId === selectedValue?.value &&
                candidate.matchState === 'match_not_confirmed',
            ),
        },
      }
    : {}),
})
export const coverField = (fields: readonly ReviewField[] | undefined) =>
  fields?.find((field) => field.destination === 'cover')
/**
 * Send the book's decision and adopt the case its acknowledgement confirms
 * into the exact case read, as the book's own page does, so a later choice
 * answers the selection this one made. With no confirmed case the read is
 * renewed instead of trusted.
 */
export const submitCover = async (
  http: ReturnType<typeof useReviewContext>['http'],
  queryClient: ReturnType<typeof useReviewContext>['queryClient'],
  reviewCaseId: string,
  command: DecisionCommand,
) => {
  const { confirmed } = await submitDecisionCase(queryClient, reviewCaseId, () =>
    http.recordDecision(reviewCaseId, command),
  )
  if (!confirmed)
    void invalidateForegroundQueries(queryClient, {
      queryKey: ['review-case', reviewCaseId],
      exact: true,
    })
}
