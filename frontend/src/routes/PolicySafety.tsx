// The Policy sidebar's safety controls: the Preview and Automatic posture,
// the automatic pause, and recovery from a suspension. Moved out of the Policy
// route under the route-size rule (#1571).
import type { RefObject } from 'react'

import type { AutomaticSafetyView, ShadowPostureView } from '../api/http'
import { readableCategory } from '../policyFields'
import { queryWorkClass, useRequiredQuery as useQuery } from '../queryWork'
import { useReviewContext } from '../router'

export const PolicySafety = ({
  shadow,
  safety,
  pending,
  summaryRef,
  run,
}: {
  shadow: ShadowPostureView
  safety: AutomaticSafetyView
  /** A policy command is in flight, so none of these may start another. */
  pending: boolean
  /** Where focus returns after a recovery. */
  summaryRef: RefObject<HTMLElement | null>
  run: (action: string, body?: unknown) => Promise<void>
}) => {
  const { http } = useReviewContext()
  const sample = useQuery({
    queryKey: ['policy', 'shadow', 'sample'],
    queryFn: (queryContext) =>
      http.getShadowSample(queryContext.signal, queryWorkClass(queryContext)),
    staleTime: 30_000,
    retry: false,
  })
  return (
    <details className="cm-policy-mode">
      <summary ref={summaryRef}>
        <strong>
          {shadow.posture === 'preview'
            ? 'Preview mode active'
            : shadow.posture === 'automatic'
              ? 'Automatic active'
              : 'Human review active'}
        </strong>
        <span>Safety</span>
      </summary>
      <div>
        <p>
          {shadow.posture === 'preview'
            ? 'Preview records what Automatic would do; nothing is decided or written.'
            : shadow.posture === 'automatic'
              ? 'Automatic remains decision-only and separately controlled.'
              : 'Turn on Preview before enabling Automatic.'}
        </p>
        <div className="cm-policy-safety-actions">
          <button
            disabled={pending || shadow.posture === 'preview'}
            onClick={() => void run('shadow/enter')}
          >
            Enter Preview mode
          </button>
          <button
            disabled={pending || shadow.posture !== 'preview'}
            onClick={() => void run('shadow/confirm-automatic')}
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
            Eligible completed books are re-evaluated from retained evidence once the title
            suspension, automatic pause and global suspension are all cleared. A supported unchanged
            title records no change; recovery does not write to your library. Unrelated category
            suspensions remain in place.
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
            ? 'Loading recent Preview outcomes…'
            : sample.isError
              ? 'Recent Preview outcomes are unavailable.'
              : sample.data.items.length
                ? `${String(sample.data.items.length)} recent preview ${sample.data.items.length === 1 ? 'result' : 'results'}; none was decided or written.`
                : 'No Preview outcomes or safety suspensions need attention.'}
        </small>
      </div>
    </details>
  )
}
