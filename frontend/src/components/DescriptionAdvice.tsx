import { useMutation, useQueryClient } from '@tanstack/react-query'
import type { DescriptionAdviceView } from '../api/http'
import { useReviewContext } from '../router'

export const descriptionAdviceStatus = (advice: DescriptionAdviceView) =>
  advice.application_outcome === 'verified'
    ? 'Applied and verified in the library. This authored result is retained in History.'
    : advice.application_outcome === 'no_change_required'
      ? 'No change needed in the library. This authored choice is retained in History.'
      : !advice.current
        ? 'Stale advice. Retained in history; select a current or provider value, or request new advice.'
        : advice.selection
          ? 'Selected. Activity shows whether the guarded write completed or no change was needed.'
          : advice.state === 'requested' || advice.state === 'running'
            ? 'Preparing description advice…'
            : advice.candidate?.output.disposition === 'needs_review'
              ? 'Needs review. This authored option cannot apply automatically.'
              : advice.candidate
                ? 'Ready for review. Authored advice is not provider evidence.'
                : advice.state === 'interrupted'
                  ? 'Preparation was interrupted. Retry only when you want another attempt.'
                  : 'Description advice could not be prepared. No automatic retry is implied.'

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
      void cache.invalidateQueries({ queryKey: ['review-case', reviewCaseId] })
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
          disabled={disabled || retry.isPending || retry.isSuccess}
          onClick={() => retry.mutate()}
        >
          {retry.isPending ? 'Requesting retry…' : 'Retry description advice'}
        </button>
      ) : null}
      {retry.isSuccess ? (
        <p role="status">Description preparation requested. Paused preparation stays paused.</p>
      ) : null}
      {retry.isError ? (
        <p role="alert">
          Description preparation could not be requested. Reload the current case and retry.
        </p>
      ) : null}
    </div>
  )
}
