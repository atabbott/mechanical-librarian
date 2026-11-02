import { invalidateForegroundQueries } from './queryWork'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'

import { useReviewContext } from './router'

export type CoverPrepareAction = 'prepare' | 'retry'
export interface CoverPrepareFailure {
  action: CoverPrepareAction
  error: Error
}
export type RecordCoverPrepareFailure = (failure: CoverPrepareFailure | null) => void

// The last failed Prepare or Retry press on the selected case. It lives with
// the page that owns case selection, above the consideration remount key and
// the detail error branch, so a reload that brings a new consideration (or
// fails and then recovers) still shows what happened to the press (#1595). It
// is cleared on the next press, on success, and when the selected case changes.
// Each selection of a case is its own visit, and `record` is bound to the visit
// it was read in, so a press that settles after its visit ended (even when the
// owner has since come back to the same case) is dropped.
export function useCoverPrepareFailure(reviewCaseId: string) {
  const [state, setState] = useState<{
    visit: { readonly caseId: string }
    failure: CoverPrepareFailure | null
  }>(() => ({ visit: { caseId: reviewCaseId }, failure: null }))
  if (state.visit.caseId !== reviewCaseId)
    setState({ visit: { caseId: reviewCaseId }, failure: null })
  const { visit } = state
  const record: RecordCoverPrepareFailure = (failure) =>
    setState((current) => (current.visit === visit ? { visit, failure } : current))
  return { failure: visit.caseId === reviewCaseId ? state.failure : null, record }
}

// Everything a cover layout needs that is not layout: where a candidate's
// image comes from, which images the browser has already failed to load, and
// the mutation that asks for a comparison to be prepared or its advisory
// review retried. Its outcome is recorded with the owner of case selection,
// because this layout remounts whenever the consideration changes.
export function useCoverChoiceData(reviewCaseId: string, record: RecordCoverPrepareFailure) {
  const { http } = useReviewContext()
  const queryClient = useQueryClient()
  const [failed, setFailed] = useState<ReadonlySet<string>>(new Set())
  return {
    // The press keeps the `record` of the visit it started in (#1595).
    prepare: useMutation<unknown, Error, CoverPrepareAction, RecordCoverPrepareFailure>({
      mutationFn: () => http.retryCoverConsideration(reviewCaseId),
      retry: false,
      onMutate: () => {
        record(null)
        return record
      },
      onSuccess: async (_data, _action, recordVisit) => {
        recordVisit(null)
        await invalidateForegroundQueries(queryClient, { queryKey: ['review-case', reviewCaseId] })
      },
      onError: (error, action, recordVisit) => {
        recordVisit?.({ action, error })
        void invalidateForegroundQueries(queryClient, { queryKey: ['review-case', reviewCaseId] })
      },
    }),
    imageSource: (considerationId: string, reference: string) =>
      `/api/v1/review-cases/${encodeURIComponent(reviewCaseId)}/cover-considerations/${encodeURIComponent(considerationId)}/images/${encodeURIComponent(reference)}`,
    imageFailed: (reference: string) => failed.has(reference),
    markImageFailed: (reference: string) =>
      setFailed((references) => new Set(references).add(reference)),
  }
}
