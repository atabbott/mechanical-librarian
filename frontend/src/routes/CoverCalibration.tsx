import { useQuery as useOptionalQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'

import type { CoverWorkView, TasteProfileView } from '../api/http'
import { coverField } from '../coverDecision'
import { picksSaved } from '../coverPicks'
import { prefetchBackgroundQuery, queryWorkClass } from '../queryWork'
import { routeFailureCopy } from '../routeFailure'
import { detailQuery, useReviewContext } from '../router'
import { CoverComparison } from './CoverComparison'

// Covers' picks (#1372 PR 3): up to 25 works whose cover the owner can pick
// now, one at a time, until the taste profile has 25 picks to learn from.
// Each pick is the work's ordinary cover decision; nothing here writes
// anything else, and the set is read from the server, never kept.

const emptyCopy = 'No works have two different covers to pick from yet.'
const ranOutCopy = 'No more works have two different covers to pick from.'

/** How the screen ends: Stop, an empty set, the set run out, or 25 picks. */
export type CalibrationEnd = { kind: 'notice'; text: string } | { kind: 'done' }

export function CoverCalibration({
  taste,
  tasteReadAt,
  onEnd,
}: {
  taste: TasteProfileView
  /** When the count was last read, so an end that names it waits for a read after the last pick. */
  tasteReadAt: number
  onEnd: (end: CalibrationEnd) => void
}) {
  const { http } = useReviewContext()
  const queryClient = useQueryClient()
  const set = useOptionalQuery({
    queryKey: ['covers', 'calibration'] as const,
    queryFn: (context) => http.getCoverCalibration(context.signal, queryWorkClass(context)),
    staleTime: 0,
    refetchOnWindowFocus: false,
    retry: false,
  })
  // The walk: every work read this session, in the order first read. A
  // re-read of the set (live invalidation, Reload, polling) refreshes the
  // works it still lists in place and adds the others to the end, so the open
  // work changes only on a pick, Skip for now or passing over a work decided
  // elsewhere.
  const [walk, setWalk] = useState<{ read: unknown; works: readonly CoverWorkView[] }>({
    read: undefined,
    works: [],
  })
  if (set.data !== undefined && set.data !== walk.read) {
    const fresh = new Map(set.data.items.map((work) => [work.review_case_id, work]))
    const known = new Set(walk.works.map((work) => work.review_case_id))
    setWalk({
      read: set.data,
      works: [
        ...walk.works.map((work) => fresh.get(work.review_case_id) ?? work),
        ...set.data.items.filter((work) => !known.has(work.review_case_id)),
      ],
    })
  }
  // Every work this session picked, skipped or passed over, so a re-read
  // never offers it again.
  const [seen, setSeen] = useState<ReadonlySet<string>>(new Set())
  // The seen count when the set was last read again after running out, and
  // the seen count whose read has answered: the run-out ends only after it.
  const [rereadAt, setRereadAt] = useState<number | null>(null)
  const [rereadDone, setRereadDone] = useState<number | null>(null)
  const [picked, setPicked] = useState(0)
  const [pickedAt, setPickedAt] = useState(0)
  const heading = useRef<HTMLHeadingElement>(null)
  useEffect(() => heading.current?.focus(), [])
  const items = walk.works
  const open = items.filter((work) => !seen.has(work.review_case_id))
  const current = open[0]
  const next = open[1]
  const pass = (id: string) => setSeen((was) => new Set(was).add(id))
  const required = taste.required
  const done = taste.chosen >= required
  // Re-checked when it opens: a work decided elsewhere, no longer current,
  // fetching or with nothing to choose is passed over without a word (S4).
  const detail = useOptionalQuery({
    ...detailQuery(http, current?.review_case_id ?? ''),
    enabled: current !== undefined,
  })
  const field = coverField(detail.data?.fields)
  const pickable =
    field?.humanDecision === null &&
    field.coverConsideration?.current === true &&
    !field.coverConsideration.providerFetching &&
    (field.decisionActions.includes('accept') || field.decisionActions.includes('keep_current'))
  const unpickable = detail.isSuccess && !pickable
  if (current && unpickable) pass(current.review_case_id)
  useEffect(() => {
    if (next) prefetchBackgroundQuery(queryClient, detailQuery(http, next.review_case_id))
  }, [next, http, queryClient])
  // Run out: the set is read once more, leaving out what this session saw,
  // and the screen ends only when that read brings nothing new (S4).
  const ranOut =
    !done && set.isSuccess && !set.isFetching && walk.read === set.data && current === undefined
  const empty = ranOut && items.length === 0 && seen.size === 0
  if (ranOut && !empty && rereadAt !== seen.size) setRereadAt(seen.size)
  const { refetch } = set
  useEffect(() => {
    if (rereadAt === null) return
    let live = true
    void refetch().then(() => {
      if (live) setRereadDone(rereadAt)
    })
    return () => {
      live = false
    }
  }, [rereadAt, refetch])
  // Stop, like running out, names the count only once taste is read again
  // after the last pick, so it is never one short; a pick still being
  // recorded settles first.
  const [stopped, setStopped] = useState(false)
  const [recording, setRecording] = useState(0)
  const counted = tasteReadAt >= pickedAt && recording === 0
  // Each end is said once.
  const ended = useRef(false)
  const end = done
    ? ({ kind: 'done' } as const)
    : stopped && counted
      ? ({ kind: 'notice', text: picksSaved(taste.chosen, required) } as const)
      : empty
        ? ({ kind: 'notice', text: emptyCopy } as const)
        : ranOut && rereadAt === seen.size && rereadDone === seen.size && counted
          ? ({
              kind: 'notice',
              text: `${picksSaved(taste.chosen, required)} ${ranOutCopy}`,
            } as const)
          : null
  useEffect(() => {
    if (end === null || ended.current) return
    ended.current = true
    onEnd(end)
  })
  const stop = () => {
    setStopped(true)
    void queryClient.invalidateQueries({ queryKey: ['covers'] })
  }
  // A case prepared again takes its predecessor's place in the walk.
  const replace = (from: string, to: string) =>
    setWalk((was) => {
      const later = was.works.find((work) => work.review_case_id === to)
      return {
        read: was.read,
        works: was.works.flatMap((work) =>
          work.review_case_id === to
            ? []
            : work.review_case_id === from
              ? [later ?? { ...work, review_case_id: to }]
              : [work],
        ),
      }
    })
  const position = Math.min(taste.chosen + 1, required)
  return (
    <section className="covers-picks" aria-labelledby="covers-picks-heading">
      <header className="covers-picks-bar">
        <h2 id="covers-picks-heading" ref={heading} tabIndex={-1}>
          Pick the cover you like
        </h2>
        <div className="covers-picks-progress">
          <span>{`${String(position)} of ${String(required)}`}</span>
          <span className="covers-picks-meter" aria-hidden="true">
            <span
              style={{ width: `${String((100 * Math.min(taste.chosen, required)) / required)}%` }}
            />
          </span>
          <button type="button" onClick={stop}>
            Stop
          </button>
        </div>
      </header>
      {set.isError ? (
        <div className="covers-failure" role="alert">
          <p>{routeFailureCopy(set.error).message}</p>
          <button type="button" onClick={() => void set.refetch()}>
            Reload
          </button>
        </div>
      ) : current && !unpickable ? (
        <CoverComparison
          key={current.review_case_id}
          work={current}
          calibration
          focusTitle={picked > 0 || seen.size > 0}
          onRecorded={() => {
            setPicked((count) => count + 1)
            setPickedAt(Date.now())
            pass(current.review_case_id)
            void queryClient.invalidateQueries({ queryKey: ['covers', 'taste'] })
            void queryClient.invalidateQueries({ queryKey: ['library'] })
          }}
          onReplaced={(successor) => {
            // The work was prepared again: its successor opens in its place,
            // and the set is read afresh.
            replace(current.review_case_id, successor)
            void set.refetch()
          }}
          onRecording={(sent) => setRecording((count) => count + (sent ? 1 : -1))}
          onSkip={() => pass(current.review_case_id)}
        />
      ) : (
        <p role="status">Loading covers…</p>
      )}
    </section>
  )
}
