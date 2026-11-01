import { invalidateForegroundQueries } from './queryWork'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'

import { useReviewContext } from './router'

// Everything a cover layout needs that is not layout: where a candidate's
// image comes from, which images the browser has already failed to load, and
// the mutation that asks for a comparison to be prepared. Detail and Library
// derived all three identically while rendering them deliberately
// differently, and only the second half of that is worth keeping.
export function useCoverChoiceData(reviewCaseId: string) {
  const { http } = useReviewContext()
  const queryClient = useQueryClient()
  const [failed, setFailed] = useState<ReadonlySet<string>>(new Set())
  return {
    prepare: useMutation({
      mutationFn: () => http.retryCoverConsideration(reviewCaseId),
      retry: false,
      onSuccess: async () => {
        await invalidateForegroundQueries(queryClient, { queryKey: ['review-case', reviewCaseId] })
      },
      onError: () => {
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
