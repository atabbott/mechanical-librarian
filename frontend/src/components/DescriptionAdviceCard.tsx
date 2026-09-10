import { useMutation, useQueryClient } from '@tanstack/react-query'
import type { DescriptionAdviceView } from '../api/http'
import { useReviewContext } from '../router'

export const DescriptionAdviceCard = ({
  advice,
  reviewCaseId,
  disabled = false,
  onSelect,
}: {
  advice: DescriptionAdviceView | null | undefined
  reviewCaseId: string
  disabled?: boolean
  onSelect?: () => void
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
  if (!advice) return null
  const candidate = advice.candidate
  const busy = advice.state === 'requested' || advice.state === 'running'
  return (
    <article className="description-advice-card" aria-label="Advised description">
      <h3>
        Advised description <span>Authored content</span>
      </h3>
      <p>
        {advice.engine} · {advice.model}
      </p>
      {!advice.current ? (
        <p role="status">
          This advice is stale. Its original text remains in history; it cannot be selected against
          this case binding.
        </p>
      ) : advice.selection ? (
        <p role="status">
          Advised description selected. It is queued under the existing write requirements, or
          recorded without a write when already equal. Activity shows the verified outcome.
        </p>
      ) : busy ? (
        <p role="status">Preparing description advice…</p>
      ) : candidate?.output.disposition === 'needs_review' ? (
        <p>Review this authored description before selecting it. It cannot apply automatically.</p>
      ) : candidate ? (
        <p>Ready for review. This is authored advice, not independent provider evidence.</p>
      ) : (
        <p>
          Description advice{' '}
          {advice.state === 'interrupted' ? 'was interrupted' : 'could not be prepared'}. No
          automatic retry or model call is implied.
        </p>
      )}
      {candidate ? <p className="description-advice-text">{candidate.output.description}</p> : null}
      {onSelect ? (
        <button
          disabled={
            disabled ||
            !advice.current ||
            !candidate ||
            busy ||
            retry.isPending ||
            advice.selection?.candidate_id === candidate.candidate_id
          }
          onClick={onSelect}
        >
          Use advised description
        </button>
      ) : (
        <a href={`/library?preview=${encodeURIComponent(reviewCaseId)}&field=description&lens=all`}>
          Review description choices in Library
        </a>
      )}
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
    </article>
  )
}
