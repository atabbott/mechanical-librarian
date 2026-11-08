// The Policy sidebar's safety controls: the Preview and Automatic posture,
// the automatic pause, and recovery from a suspension. Moved out of the Policy
// route under the route-size rule (#1571).
import type { RefObject } from 'react'

import type {
  AutomaticSafetyView,
  PolicyCommandBody,
  PolicyCommandPath,
  PreviewPostureView,
} from '../api/http'
import { readableCategory } from '../policyFields'
import { queryWorkClass, useRequiredQuery as useQuery } from '../queryWork'
import { useReviewContext } from '../router'

export const PolicySafety = ({
  posture,
  safety,
  pending,
  summaryRef,
  run,
}: {
  posture: PreviewPostureView
  safety: AutomaticSafetyView
  /** A policy command is in flight, so none of these may start another. */
  pending: boolean
  /** Where focus returns after a recovery. */
  summaryRef: RefObject<HTMLElement | null>
  run: <P extends PolicyCommandPath>(action: P, body?: PolicyCommandBody<P>) => Promise<void>
}) => {
  const { http } = useReviewContext()
  const sample = useQuery({
    queryKey: ['policy', 'preview-mode', 'sample'],
    queryFn: (queryContext) =>
      http.getPreviewSample(queryContext.signal, queryWorkClass(queryContext)),
    staleTime: 30_000,
    retry: false,
  })
  return (
    <details className="cm-policy-mode">
      <summary ref={summaryRef}>
        <strong>
          {posture.posture === 'preview'
            ? 'Preview mode active'
            : posture.posture === 'automatic'
              ? 'Automatic active'
              : 'Human review active'}
        </strong>
        <span>Mode</span>
      </summary>
      <div>
        <p>
          {posture.posture === 'preview'
            ? 'Preview records what Automatic would do. Nothing is decided or written.'
            : posture.posture === 'automatic'
              ? 'Automatic records choices. Writing them needs your write permission.'
              : 'Enter Preview mode before turning on Automatic.'}
        </p>
        <div className="cm-policy-safety-actions">
          <button
            disabled={pending || posture.posture === 'preview'}
            onClick={() => void run('preview-mode/enter')}
          >
            Enter Preview mode
          </button>
          <button
            disabled={pending || posture.posture !== 'preview'}
            onClick={() => void run('preview-mode/confirm-automatic')}
          >
            Turn on Automatic
          </button>
          <button
            disabled={pending}
            onClick={() => void run('automatic-safety/pause', { paused: !safety.paused })}
          >
            {safety.paused ? 'Resume automatic decisions' : 'Pause automatic decisions'}
          </button>
          {safety.global_suspension ? (
            <button
              disabled={pending}
              onClick={() => void run('automatic-safety/recover', { category: null })}
            >
              Recover global suspension
            </button>
          ) : null}
        </div>
        {safety.category_suspensions.title_layout ? (
          <p>
            Automatic decides Title and subtitle again once this suspension, the pause and any
            global suspension are all cleared. It uses what the sources already said for prepared
            works. Recovering writes nothing to your library. Other suspensions stay in place.
          </p>
        ) : null}
        {Object.keys(safety.category_suspensions).map((category) => (
          <button
            key={category}
            disabled={pending}
            onClick={() => void run('automatic-safety/recover', { category })}
          >
            Recover {readableCategory(category)} suspension
          </button>
        ))}
        <small>
          {sample.isPending
            ? 'Loading recent Preview results…'
            : sample.isError
              ? 'Recent Preview results could not be loaded. Reload the page to try again.'
              : sample.data.items.length
                ? `${String(sample.data.items.length)} recent Preview ${sample.data.items.length === 1 ? 'result' : 'results'}. None was decided or written.`
                : 'No recent Preview results.'}
        </small>
      </div>
    </details>
  )
}
