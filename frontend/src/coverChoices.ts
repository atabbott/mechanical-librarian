import { invalidateForegroundQueries } from './queryWork'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'

import { useReviewContext } from './router'

export type CoverPrepareAction = 'prepare' | 'retry'
export interface CoverPrepareFailure {
  action: CoverPrepareAction
  error: Error
}
export type RecordCoverPrepareFailure = (
  reviewCaseId: string,
  failure: CoverPrepareFailure | null,
) => void

// The last failed Prepare or Retry press on the selected case. It lives with
// the page that owns case selection, above the consideration remount key and
// the detail error branch, so a reload that brings a new consideration (or
// fails and then recovers) still shows what happened to the press (#1595). It
// is cleared on the next press, on success, and when the selected case changes;
// a press that settles after its case was left is dropped.
export function useCoverPrepareFailure(reviewCaseId: string) {
  const [state, setState] = useState<{
    owner: string
    failure: CoverPrepareFailure | null
  }>({ owner: reviewCaseId, failure: null })
  if (state.owner !== reviewCaseId) setState({ owner: reviewCaseId, failure: null })
  const record: RecordCoverPrepareFailure = (caseId, failure) =>
    setState((current) => (current.owner === caseId ? { owner: caseId, failure } : current))
  return { failure: state.owner === reviewCaseId ? state.failure : null, record }
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
    prepare: useMutation<unknown, Error, CoverPrepareAction>({
      mutationFn: () => http.retryCoverConsideration(reviewCaseId),
      retry: false,
      onMutate: () => {
        record(reviewCaseId, null)
      },
      onSuccess: async () => {
        record(reviewCaseId, null)
        await invalidateForegroundQueries(queryClient, { queryKey: ['review-case', reviewCaseId] })
      },
      onError: (error, action) => {
        record(reviewCaseId, { action, error })
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
