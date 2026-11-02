import { invalidateForegroundQueries } from '../queryWork'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { refusalCopy, uncertainWriteOutcome } from '../api/http'
import type { DescriptionAdviceView } from '../api/http'
import { useReviewContext } from '../router'
import { descriptionAdviceStatus } from '../descriptionAdviceStatus'

export const DescriptionAdvicePreparation = ({
  advice,
  reviewCaseId,
  disabled,
}: {
  advice: DescriptionAdviceView
  reviewCaseId: string
  disabled: boolean
}) => {
  const { http } = useReviewContext()
  const cache = useQueryClient()
  const retry = useMutation({
    mutationFn: () => http.retryDescriptionAdvice(reviewCaseId),
    retry: false,
    onSuccess: () => {
      void invalidateForegroundQueries(cache, { queryKey: ['review-case', reviewCaseId] })
    },
    onError: () => {
      void invalidateForegroundQueries(cache, { queryKey: ['review-case', reviewCaseId] })
    },
  })
  const busy = advice.state === 'requested' || advice.state === 'running'
  return (
    <div className="description-advice-preparation">
      {!advice.candidate ? (
        <p role="status">
          {advice.engine} · {advice.model}. {descriptionAdviceStatus(advice)}
        </p>
      ) : null}
      {!busy ? (
        <button
          disabled={disabled}
          aria-disabled={retry.isPending || retry.isSuccess || undefined}
          aria-busy={retry.isPending || undefined}
          onClick={() => {
            if (!retry.isPending && !retry.isSuccess) retry.mutate()
          }}
        >
          {retry.isPending ? 'Requesting retry…' : 'Retry description advice'}
        </button>
      ) : null}
      {retry.isSuccess ? (
        <p role="status">Description preparation requested. Paused preparation stays paused.</p>
      ) : null}
      {retry.isError ? (
        <p role="alert">
          {uncertainWriteOutcome(retry.error)
            ? 'Description preparation could not be confirmed. It may have been requested; the current case was reloaded. Check it before retrying.'
            : refusalCopy(
                retry.error,
                'Description preparation could not be requested. Reload the current case and retry.',
              )}
        </p>
      ) : null}
    </div>
  )
}
