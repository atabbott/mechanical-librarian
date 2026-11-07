// The recovery offered when a cover choice meets a comparison that changed
// before it was recorded: the notice, and the owner's read of the current
// comparison it opens. Moved out of the Library route under the route-size
// rule (#1742); the page keeps the decision controller, navigation and the
// selected case and passes them in.
import type { UseNavigateResult } from '@tanstack/react-router'
import { useEffect, useRef, useState } from 'react'

import type { ReviewHttp } from '../api/http'

export const useStaleCoverReview = ({
  http,
  settle,
  navigate,
  selectedReviewCaseId,
  setReconciledSuccessorIdentity,
  identityShown,
  detailFailed,
}: {
  http: ReviewHttp
  /** Waits for the decision controller's commands to settle. */
  settle: () => Promise<unknown>
  navigate: UseNavigateResult<'/library'>
  selectedReviewCaseId: string
  setReconciledSuccessorIdentity: (caseId: string) => void
  /** Whether the selected work's identity is shown above the notice. */
  identityShown: boolean
  /** Whether the selected work's detail read failed. */
  detailFailed: boolean
}) => {
  const staleCoverRecoveryButton = useRef<HTMLButtonElement>(null)
  const [staleCoverReview, setStaleCoverReview] = useState<{
    caseId: string
    title: string
    failed: boolean
    loading: boolean
  } | null>(null)
  const reviewCurrentCoverComparison = async () => {
    const stale = staleCoverReview
    if (!stale || stale.loading) return
    setStaleCoverReview({ ...stale, failed: false, loading: true })
    try {
      await settle()
      const successor = await http.getCaseSuccessor(stale.caseId)
      const currentCaseId = successor ?? stale.caseId
      const currentDetail = await http.getCaseDetail(currentCaseId)
      const currentCover = currentDetail.fields.find(
        (field) => field.destination === 'cover' && field.coverConsideration !== null,
      )
      if (!currentDetail.current || currentCover === undefined) throw new Error('stale cover')
      if (successor !== null) setReconciledSuccessorIdentity(currentCaseId)
      await navigate({
        replace: true,
        search: (current) =>
          current.preview === stale.caseId
            ? { ...current, preview: currentCaseId, field: 'cover' }
            : current,
      })
      setStaleCoverReview(null)
    } catch {
      setStaleCoverReview({ ...stale, failed: true, loading: false })
    }
  }
  const staleCoverCaseId = staleCoverReview?.caseId
  useEffect(() => {
    if (!staleCoverCaseId) return
    const frame = requestAnimationFrame(() => {
      if (document.activeElement === document.body) staleCoverRecoveryButton.current?.focus()
    })
    return () => cancelAnimationFrame(frame)
  }, [staleCoverCaseId, identityShown])
  const notice =
    staleCoverReview?.caseId === selectedReviewCaseId ? (
      <aside className="comparison-notice" role="alert">
        {!identityShown || detailFailed ? <h2>{staleCoverReview.title}</h2> : null}
        <p>This cover comparison changed before your choice was recorded. No cover was changed.</p>
        {staleCoverReview.failed ? (
          <p>The current comparison could not be loaded. Your prior choice remains unrecorded.</p>
        ) : null}
        <button
          type="button"
          ref={staleCoverRecoveryButton}
          disabled={staleCoverReview.loading}
          onClick={() => void reviewCurrentCoverComparison()}
        >
          {staleCoverReview.loading ? 'Loading current comparison…' : 'Review current comparison'}
        </button>
      </aside>
    ) : null
  return {
    /** Offers the owner the current comparison for the case whose cover choice went stale. */
    open: (caseId: string, title: string) =>
      setStaleCoverReview({ caseId, title, failed: false, loading: false }),
    /** Ends the offer once a cover choice for the case is recorded. */
    clear: (caseId: string) =>
      setStaleCoverReview((current) => (current?.caseId === caseId ? null : current)),
    notice,
  }
}
