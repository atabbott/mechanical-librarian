import { useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'
import type { ReactNode, Ref } from 'react'

import { ApiError } from '../api/http'
import type { TastePreviewView, TasteProtection } from '../api/http'
import { previewMeta, tastePreviewQuery } from '../tastePreview'
import { coverCommand, coverField, failureOf, staleCopy, submitCover } from '../coverDecision'
import { focusAfterRetry, retryInPlace } from '../focusAfterRetry'
import type { Failure } from '../coverDecision'
import type { ReviewField } from '../fieldDecision'
import { coverFaultLabel, mediaKindLabels } from '../labels'
import { useRequiredQuery as useQuery } from '../queryWork'
import { routeFailureCopy } from '../routeFailure'
import { detailQuery, useReviewContext } from '../router'
import { StatusPill } from '../components/StatusPill'
import { TasteRuleGroups } from './TasteRuleGroups'

// The preview before Automatic is turned on (#1372 PR 5, K7): up to five
// switches the taste profile would make, read from what the suggestion model
// has checked so far under the version in use. Looks right and Keep current
// are the work's own cover decisions (K8). Turn on never waits for it.

type Item = TastePreviewView['found'][number]
type Answer = NonNullable<Item['answer']>
const size = (cover: { width: number; height: number }) =>
  `${String(cover.width)} × ${String(cover.height)} px`
const audiobook = (item: Item) => ['m4b', 'm4a', 'mp3', 'audio'].includes(item.media_kind)

function Frame({ cover, caption, alt }: { cover: Item['current']; caption: string; alt: string }) {
  const [failed, setFailed] = useState<string | null>(null)
  return (
    <figure>
      {cover && failed !== cover.url ? (
        <img
          src={cover.url}
          alt={alt}
          loading="lazy"
          decoding="async"
          onError={() => setFailed(cover.url)}
        />
      ) : (
        <span className="covers-image-missing" role="img" aria-label={`${alt} unavailable`}>
          Image unavailable
        </span>
      )}
      <figcaption>
        {caption}
        {cover ? <small>{size(cover)}</small> : null}
      </figcaption>
    </figure>
  )
}

// The switch's cover is the candidate its image route names, in the work's
// comparison that the preview read.
const switchReference = (item: Item) => {
  const match = /\/cover-considerations\/([^/]+)\/images\/([^/]+)$/.exec(item.new?.url ?? '')
  return match ? { consideration: match[1], reference: match[2] } : null
}

function PreviewItem({ item, onAnswered }: { item: Item; onAnswered: () => Promise<unknown> }) {
  const { http, queryClient } = useReviewContext()
  // The answer given here shows until the preview is read again; from then
  // the read's own answer (the owner's current selection) is shown, so a
  // change made in another tab is not hidden by this one.
  const [given, setGiven] = useState<{ outcome: Answer; of: Item } | null>(null)
  const answer = given?.outcome ?? null
  const [pending, setPending] = useState(false)
  const [failure, setFailure] = useState<Failure | null>(null)
  const state = useRef<HTMLParagraphElement>(null)
  const looksRight = useRef<HTMLButtonElement>(null)
  const staleReload = useRef<HTMLButtonElement>(null)
  const shown = given !== null && given.of === item ? given.outcome : item.answer
  const recorded = useRef(false)
  useEffect(() => {
    if (answer !== null && !recorded.current) {
      recorded.current = true
      state.current?.focus()
    }
  }, [answer])
  const titleId = `taste-preview-${item.review_case_id}`
  // Each answer reads the work's case first, so the command carries the exact
  // comparison and selection it answers; a case that no longer offers the
  // switch is stale.
  const record = async (outcome: Answer) => {
    setFailure(null)
    setPending(true)
    let field: ReviewField | undefined
    try {
      const detail = await queryClient.query({
        ...detailQuery(http, item.review_case_id),
        staleTime: 0,
      })
      field = coverField(detail.fields)
      const switched = switchReference(item)
      const candidate = field?.coverConsideration?.candidates.find(
        (choice) => choice.reference === switched?.reference && choice.selectable,
      )
      const current = field?.coverConsideration?.id === switched?.consideration
      if (!field || !current || (outcome === 'looks_right' && !candidate))
        throw new ApiError(409, 'stale')
      await submitCover(
        http,
        queryClient,
        item.review_case_id,
        outcome === 'looks_right' && candidate
          ? coverCommand(field, 'accept', { kind: 'cover', value: candidate.artifactId })
          : coverCommand(field, 'keep_current', null),
      )
      setGiven({ outcome, of: item })
      void onAnswered()
    } catch (error) {
      setFailure(failureOf(error, field))
    } finally {
      setPending(false)
    }
  }
  const fault = item.reason === null ? null : coverFaultLabel(item.reason, audiobook(item))
  return (
    <li className={`taste-preview-item${shown ? ' is-done' : ''}`}>
      <div className="taste-preview-covers">
        <Frame cover={item.current} caption="Now" alt="Current cover" />
        <span className="covers-arrow" aria-hidden="true">
          →
        </span>
        <Frame cover={item.new} caption="New" alt="New cover" />
      </div>
      <div className="taste-preview-text">
        <strong id={titleId}>{item.title}</strong>
        <small>
          {[item.authors.join(', '), mediaKindLabels[item.media_kind]].filter(Boolean).join(' · ')}
        </small>
        {fault === null ? null : (
          <p>
            <StatusPill tone="notice">{fault}</StatusPill>
          </p>
        )}
        <TasteRuleGroups rules={item.rules} />
        {/* Without the new cover's image route there is no switch to answer. */}
        {!shown && !item.new ? null : shown ? (
          <p
            ref={state}
            tabIndex={-1}
            className={`taste-preview-state${shown === 'looks_right' ? ' is-ok' : ''}`}
          >
            {shown === 'looks_right' ? 'New cover chosen' : 'Current cover kept'}
          </p>
        ) : (
          <div className="taste-preview-actions">
            <button
              ref={looksRight}
              type="button"
              aria-describedby={titleId}
              aria-disabled={pending || undefined}
              onClick={() => {
                if (!pending) void record('looks_right')
              }}
            >
              Looks right
            </button>
            <button
              type="button"
              aria-describedby={titleId}
              aria-disabled={pending || undefined}
              onClick={() => {
                if (!pending) void record('kept_current')
              }}
            >
              Keep current
            </button>
          </div>
        )}
        {failure?.kind === 'stale' ? (
          <div className="covers-failure" role="alert">
            <p>{staleCopy}</p>
            <button
              ref={staleReload}
              type="button"
              onClick={() => {
                // Reload replaces itself: focus goes back to Looks right, or
                // to the region when the switch has left the preview.
                void onAnswered().then(() => {
                  setFailure(null)
                  focusAfterRetry(
                    () => looksRight.current ?? document.getElementById('taste-preview-meta'),
                    () => staleReload.current,
                  )
                })
              }}
            >
              Reload
            </button>
          </div>
        ) : failure ? (
          <p className="covers-failure" role="alert">
            {failure.text}
          </p>
        ) : null}
      </div>
    </li>
  )
}

export function TastePreview({
  protection,
  saving,
  headingRef,
  scope,
  onTurnOn,
  onNotNow,
}: {
  protection: TasteProtection
  saving: boolean
  headingRef: Ref<HTMLParagraphElement>
  /** Only covers with a fault and its cost, above the switches it decides. */
  scope: ReactNode
  onTurnOn: () => void
  onNotNow: () => void
}) {
  const { http } = useReviewContext()
  const cache = useQueryClient()
  const read = useQuery(tastePreviewQuery(http, protection))
  const reread = () => cache.invalidateQueries({ queryKey: ['covers', 'taste', 'preview'] })
  const readRetry = useRef<HTMLButtonElement>(null)
  const meta = () => document.getElementById('taste-preview-meta')
  return (
    <section className="taste-preview" aria-labelledby="taste-preview-meta">
      <header>
        <p id="taste-preview-meta" ref={headingRef} tabIndex={-1} aria-live="polite">
          {read.data ? previewMeta(read.data) : 'Checking covers…'}
        </p>
      </header>
      {scope}
      {read.isError ? (
        <div className="covers-failure" role="alert">
          <p>{routeFailureCopy(read.error).message}</p>
          <button
            ref={readRetry}
            type="button"
            onClick={() =>
              retryInPlace(
                meta,
                () => read.refetch(),
                (result) => (result.isError ? readRetry.current : meta()),
              )
            }
          >
            Reload
          </button>
        </div>
      ) : null}
      {read.data?.found.length ? (
        <ol className="taste-preview-list">
          {read.data.found.map((item) => (
            <PreviewItem key={item.review_case_id} item={item} onAnswered={reread} />
          ))}
        </ol>
      ) : null}
      <div className="taste-preview-foot">
        <button
          type="button"
          className="button-primary"
          aria-disabled={saving || undefined}
          aria-busy={saving || undefined}
          onClick={() => {
            if (!saving) onTurnOn()
          }}
        >
          Turn on
        </button>
        <button type="button" onClick={onNotNow}>
          Not now
        </button>
      </div>
    </section>
  )
}
