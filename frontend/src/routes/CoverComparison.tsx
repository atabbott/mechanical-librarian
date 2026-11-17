import { Link } from '@tanstack/react-router'
import { useEffect, useRef, useState } from 'react'

import type { CoverWorkView, DecisionCommand } from '../api/http'
import { useCoverPrepareFailure } from '../coverChoices'
import {
  bookSearch,
  byline,
  coverCommand,
  coverField,
  failureOf,
  retainedCandidate,
  staleCopy,
  submitCover,
} from '../coverDecision'
import type { Failure, Outcome } from '../coverDecision'
import type { ChoiceProgress } from '../fieldDecision'
import { invalidateForegroundQueries, useRequiredQuery as useQuery } from '../queryWork'
import { routeFailureCopy } from '../routeFailure'
import { detailQuery, useReviewContext } from '../router'
import { LibraryCoverChoices } from './FieldDecisionCover'

// One work's cover comparison in Covers (#1633). To decide, Switched's Others
// and Covers' picks (#1372 PR 3) open it; every choice is the work's own
// decision, exactly as its page sends it.

const question = 'Which cover should this work use?'
const calibrationQuestion = 'Which cover do you like?'

/**
 * Why a choice did not record, said right under the choices (DESIGN.md,
 * Direct Value Choice). It scrolls just far enough to show, and takes focus:
 * Reload when the work changed, else the line itself.
 */
function ChoiceFailure({ failure, onReload }: { failure: Failure; onReload: () => void }) {
  const line = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const element = line.current
    if (!element) return
    element.scrollIntoView({ block: 'nearest' })
    ;(element.querySelector('button') ?? element).focus({ preventScroll: true })
  }, [failure])
  return failure.kind === 'stale' ? (
    <div ref={line} className="covers-failure" role="alert">
      <p>{staleCopy}</p>
      <button type="button" onClick={onReload}>
        Reload
      </button>
    </div>
  ) : (
    <div ref={line} className="covers-failure" role="alert" tabIndex={-1}>
      <p>{failure.text}</p>
    </div>
  )
}

export function CoverComparison({
  work,
  onRecorded,
  onReplaced,
  onSkip,
  onBack,
  calibration = false,
  focusTitle = true,
}: {
  work: CoverWorkView
  onRecorded: (outcome: Outcome) => void
  /** The book's case was replaced; the view follows it to its successor. */
  onReplaced: (reviewCaseId: string) => void
  onSkip?: () => void
  onBack?: () => void
  /** Covers' picks: the title is under the screen's heading, with no links (S7). */
  calibration?: boolean
  /** Whether opening this work moves focus to its title. */
  focusTitle?: boolean
}) {
  const { http, queryClient } = useReviewContext()
  const detail = useQuery(detailQuery(http, work.review_case_id))
  const field = coverField(detail.data?.fields)
  const prepareFailure = useCoverPrepareFailure(work.review_case_id)
  const [progress, setProgress] = useState<ChoiceProgress>(null)
  const [failure, setFailure] = useState<Failure | null>(null)
  const [evidence, setEvidence] = useState<ReadonlySet<string>>(new Set())
  const heading = useRef<HTMLHeadingElement>(null)
  // An open walkthrough keeps focus until it closes (#1372 R7).
  useEffect(() => {
    if (focusTitle && !document.querySelector('dialog[open]')) heading.current?.focus()
  }, [work.review_case_id, focusTitle])
  // Reload keeps this book: a rescan that replaced its case is followed to
  // the successor, otherwise the same case is read again.
  const reload = async () => {
    setFailure(null)
    setProgress(null)
    const successor = await http.getCaseSuccessor(work.review_case_id).catch(() => null)
    void invalidateForegroundQueries(queryClient, { queryKey: ['covers'] })
    if (successor && successor !== work.review_case_id) {
      onReplaced(successor)
      return
    }
    await invalidateForegroundQueries(queryClient, {
      queryKey: ['review-case', work.review_case_id],
    })
    heading.current?.focus()
  }
  const choose = (
    outcome: DecisionCommand['outcome'],
    value?: DecisionCommand['selectedValue'],
  ) => {
    if (!field || (outcome !== 'accept' && outcome !== 'keep_current')) return
    const command = coverCommand(field, outcome, value ?? null)
    setFailure(null)
    setProgress({ command, phase: 'recording' })
    submitCover(http, queryClient, work.review_case_id, command).then(
      () => {
        setProgress({ command, phase: 'confirmed' })
        onRecorded(
          outcome === 'keep_current'
            ? 'kept'
            : value?.value !== undefined && value.value === retainedCandidate(field)?.artifactId
              ? 'put_back'
              : work.group === 'auto'
                ? 'changed'
                : 'switched',
        )
      },
      (error: unknown) => {
        const next = failureOf(error, field)
        // A refused stale choice leaves nothing marked as chosen.
        setProgress(next.kind === 'stale' ? null : { command, phase: 'failed' })
        setFailure(next)
      },
    )
  }
  const count = field?.coverConsideration?.candidates.length ?? 0
  const Title = calibration ? 'h3' : 'h2'
  return (
    <section className="covers-comparison" aria-labelledby={`covers-book-${work.review_case_id}`}>
      {onBack && !calibration ? (
        <button className="covers-back" type="button" onClick={onBack}>
          Back to works
        </button>
      ) : null}
      <header>
        <Title id={`covers-book-${work.review_case_id}`} ref={heading} tabIndex={-1}>
          {work.title}
        </Title>
        <p>
          {byline(work)}
          {count ? ` · ${count === 1 ? '1 cover' : `${String(count)} covers`}` : ''}
        </p>
        {calibration ? null : (
          <Link to="/library" search={bookSearch(work.review_case_id)}>
            Open in Works
          </Link>
        )}
      </header>
      {detail.isPending ? (
        <p role="status">Loading covers…</p>
      ) : field ? (
        <LibraryCoverChoices
          reviewCaseId={work.review_case_id}
          mediaKind={work.media_kind}
          field={field}
          disabled={progress?.phase === 'recording'}
          progress={progress}
          expandedEvidence={evidence}
          revealEvidence={(key) =>
            setEvidence((current) => {
              const next = new Set(current)
              if (!next.delete(key)) next.add(key)
              return next
            })
          }
          choose={choose}
          prepareFailure={prepareFailure}
          question={calibration ? calibrationQuestion : question}
          {...(work.group === 'auto'
            ? {
                currentLabel: 'Current cover · changed automatically',
                keepLabel: 'Keep current cover · changed automatically',
              }
            : {})}
          coversLink={false}
          calibration={calibration}
        />
      ) : detail.isError ? (
        <div className="covers-failure" role="alert">
          <p>{routeFailureCopy(detail.error).message}</p>
          <button type="button" onClick={() => void reload()}>
            Reload
          </button>
        </div>
      ) : null}
      {failure ? <ChoiceFailure failure={failure} onReload={() => void reload()} /> : null}
      {onSkip ? (
        <button className="covers-skip" type="button" onClick={onSkip}>
          Skip for now
        </button>
      ) : null}
    </section>
  )
}
