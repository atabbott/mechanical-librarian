// The write plan's lead and its two files, in Activity's consent review. The
// groups themselves sit under each library's fields (`WritePlanField`); this
// says how many changes are prepared and lets the owner keep the whole plan
// as a file before granting permission (#1336).
import { useEffect, useRef, useState } from 'react'

import { BuildMismatchError } from '../api/http'
import type { WritePlanFormat, WritePlanView } from '../api/http'
import { StaleBuildRead } from '../components/StaleBuildRead'
import { focusAfterRetry } from '../focusAfterRetry'
import { useReviewContext } from '../router'

/** A file lists at most this many changes, and says so inside itself. */
export const WRITE_PLAN_FILE_ROWS = 20_000
// The progress line is announced, so it changes at most once a second.
const PROGRESS_INTERVAL_MS = 1_000

const count = (value: number) => value.toLocaleString('en-US')

const leadText = (total: number) =>
  total === 1
    ? '1 change is prepared so far. Permission also covers changes prepared after you allow writes.'
    : `${count(total)} changes are prepared so far. Permission also covers changes prepared after you allow writes.`

type DownloadState =
  | { readonly kind: 'idle' }
  | { readonly kind: 'busy'; readonly rows: number | null }
  | { readonly kind: 'failed' }
  | { readonly kind: 'stale' }

// Firefox and Safari can drop a download whose URL is revoked straight after
// the click, so a saved file's URL is kept this long, even after the panel ends.
export const SAVED_URL_LIFETIME_MS = 60_000

const save = (blob: Blob, filename: string): string => {
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  link.hidden = true
  document.body.append(link)
  try {
    link.click()
  } finally {
    link.remove()
  }
  return url
}

export const ActivityWritePlan = ({ plan }: { plan: WritePlanView }) => {
  const { http } = useReviewContext()
  const [state, setState] = useState<DownloadState>({ kind: 'idle' })
  const controller = useRef<AbortController | null>(null)
  const trailing = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)
  const block = useRef<HTMLDivElement>(null)
  const reload = useRef<HTMLButtonElement>(null)
  // A download in flight ends with the panel; nothing is saved after it.
  useEffect(
    () => () => {
      controller.current?.abort()
      clearTimeout(trailing.current)
    },
    [],
  )
  if (plan.total === 0) return null
  const busy = state.kind === 'busy'
  const expected = Math.min(plan.total, WRITE_PLAN_FILE_ROWS)

  const download = async (format: WritePlanFormat) => {
    if (controller.current !== null) return
    const current = new AbortController()
    controller.current = current
    setState({ kind: 'busy', rows: null })
    let shown = 0
    let latest = 0
    const show = () => {
      trailing.current = undefined
      shown = Date.now()
      setState({ kind: 'busy', rows: latest })
    }
    const progress = (rows: number) => {
      if (rows === 0) return
      latest = rows
      const wait = shown + PROGRESS_INTERVAL_MS - Date.now()
      if (wait <= 0) {
        clearTimeout(trailing.current)
        show()
      } else {
        // A held-back count still shows within the second, even if the
        // stream then pauses.
        trailing.current ??= setTimeout(show, wait)
      }
    }
    try {
      const file = await http.downloadActivityWritePlan(format, current.signal, progress)
      if (current.signal.aborted) return
      const url = save(file.blob, file.filename)
      // Each saved file's URL is let go at its own time; the browser may
      // still be starting that download when the panel ends.
      setTimeout(() => URL.revokeObjectURL(url), SAVED_URL_LIFETIME_MS)
      setState({ kind: 'idle' })
    } catch (error) {
      if (current.signal.aborted) return
      if (error instanceof BuildMismatchError) {
        setState({ kind: 'stale' })
        // The pressed button is gone, so focus goes on to Reload, as the
        // check's own retry does.
        focusAfterRetry(
          () => reload.current,
          () => block.current,
        )
      } else setState({ kind: 'failed' })
    } finally {
      clearTimeout(trailing.current)
      trailing.current = undefined
      if (controller.current === current) controller.current = null
    }
  }

  return (
    <div className="activity-write-plan" ref={block}>
      <p>{leadText(plan.total)}</p>
      {plan.total > WRITE_PLAN_FILE_ROWS ? (
        <p>The file lists the first {count(WRITE_PLAN_FILE_ROWS)} changes.</p>
      ) : null}
      {state.kind === 'stale' ? (
        <StaleBuildRead lead="The file was not downloaded." ref={reload} />
      ) : (
        <div className="activity-write-plan-actions">
          {(['csv', 'json'] as const).map((format) => (
            // Busy, not disabled: a disabled button would drop a keyboard owner's focus to <body>.
            <button
              key={format}
              type="button"
              aria-disabled={busy || undefined}
              onClick={busy ? undefined : () => void download(format)}
            >
              {format === 'csv' ? 'Download CSV' : 'Download JSON'}
            </button>
          ))}
        </div>
      )}
      {/* Announced once when the file starts and again if it fails; the
          count beside it moves every second and is not announced. */}
      <p className="activity-write-plan-status">
        <span role="status">
          {state.kind === 'busy'
            ? 'Preparing the file…'
            : state.kind === 'failed'
              ? 'The file was not downloaded. Try again.'
              : ''}
        </span>
        {state.kind === 'busy' && state.rows !== null ? (
          <>
            {' '}
            <span className="activity-write-plan-progress">
              {count(Math.min(state.rows, expected))} of {count(expected)} changes
            </span>
          </>
        ) : null}
      </p>
    </div>
  )
}
