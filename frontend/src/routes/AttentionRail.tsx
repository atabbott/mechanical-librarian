import { useEffect, useRef } from 'react'
import type { RefObject } from 'react'
import type { InfiniteData, UseInfiniteQueryResult } from '@tanstack/react-query'

import { BuildMismatchError } from '../api/http'
import type { AttentionItemView, AttentionView, LibrarySearch } from '../api/http'
import { drawerQuery } from '../attentionRail'
import { StaleBuildRead } from '../components/StaleBuildRead'
import { focusAfterRetry } from '../focusAfterRetry'
import type { ReviewField } from '../fieldDecision'
import { destinationLabel, mediaKindLabels } from '../labels'

/** An attention row, with the cursor of the page that listed it. */
export type AttentionQueueItem = AttentionItemView & { attentionCursor: string | undefined }

/**
 * The Attention queue beside Library's workspace. Library owns the attention
 * read, the selection and the rail's focus lifecycle, since its footer, the
 * selected work and the toolbar toggle read them too; the rail lists the queue,
 * loads more of it and recovers a failed read.
 */
export const AttentionRail = ({
  search,
  countText,
  attention,
  attentionItems,
  attentionGroups,
  attentionRail,
  attentionHeading,
  selected,
  closeAttention,
  selectFromQueue,
}: {
  search: LibrarySearch
  /** The rail's count line, from the Library summary and the works read. */
  countText: string
  attention: UseInfiniteQueryResult<InfiniteData<AttentionView>>
  attentionItems: readonly AttentionQueueItem[]
  attentionGroups: readonly (readonly AttentionQueueItem[])[]
  attentionRail: RefObject<HTMLElement | null>
  attentionHeading: RefObject<HTMLHeadingElement | null>
  selected: Pick<ReviewField, 'destination'> | undefined
  closeAttention: () => void
  selectFromQueue: (
    reviewCaseId: string,
    field: string,
    attentionCursor: string | undefined,
  ) => void
}) => {
  const attentionRetry = useRef<HTMLButtonElement>(null)
  const loadMoreButton = useRef<HTMLButtonElement>(null)
  // A load-more failure's Try again, or Reload once the tab is on an earlier build.
  const moreRetry = useRef<HTMLButtonElement>(null)
  // Load more keeps its place: once the page lands, focus moves to the first row
  // it added, even when that row joins a work already in the rail (#1284). The
  // press ends at the query's next data or error update, whether or not a render
  // saw it fetching, so a refetch that cancels the page ends it too; a new search
  // drops it, and focus moves only for an added page the owner has not left.
  // A press from Try again (`retry`) that fails again hands focus to the control
  // that replaces it; a plain Load more failure leaves focus on Load more.
  const attentionKey = `${search.q ?? ''} ${search.attentionCursor ?? ''}`
  const loadMore = useRef<{
    key: string
    pages: number
    updated: number
    errors: number
    from: number
    retry: boolean
  } | null>(null)
  useEffect(() => {
    const pending = loadMore.current
    if (!pending) return
    if (pending.key !== attentionKey || attention.errorUpdateCount > pending.errors) {
      loadMore.current = null
      if (pending.key === attentionKey && pending.retry)
        focusAfterRetry(
          () => moreRetry.current,
          () => attentionRail.current,
        )
      return
    }
    const pages = attention.data?.pages.length ?? 0
    if (attention.dataUpdatedAt === pending.updated && pages <= pending.pages) return
    loadMore.current = null
    if (pages <= pending.pages) return
    const active = document.activeElement
    if (active && active !== document.body && !attentionRail.current?.contains(active)) return
    const first = attentionItems[pending.from]
    const row = first
      ? [
          ...(attentionRail.current?.querySelectorAll<HTMLElement>('[data-attention-row]') ?? []),
        ].find((node) => node.dataset.attentionRow === `${first.id}:${first.destination}`)
      : undefined
    ;(row ?? attentionRail.current)?.focus()
  }, [
    attention.data,
    attentionItems,
    attentionKey,
    attentionRail,
    attention.dataUpdatedAt,
    attention.errorUpdateCount,
  ])
  const fetchMore = (retry: boolean) => {
    if (loadMore.current || attention.isFetchingNextPage) return
    loadMore.current = {
      key: attentionKey,
      pages: attention.data?.pages.length ?? 0,
      updated: attention.dataUpdatedAt,
      errors: attention.errorUpdateCount,
      from: attentionItems.length,
      retry,
    }
    void attention.fetchNextPage()
  }
  if (search.attention !== 'open') return null
  return (
    <aside
      className="attention-rail"
      aria-label="Attention queue"
      aria-busy={attention.isPlaceholderData || undefined}
      tabIndex={-1}
      ref={attentionRail}
      onKeyDown={(event) => {
        if (event.key === 'Escape') {
          event.preventDefault()
          closeAttention()
          return
        }
        if (event.key !== 'Tab' || typeof window.matchMedia !== 'function') return
        if (!window.matchMedia(drawerQuery).matches) return
        const stops = [
          ...event.currentTarget.querySelectorAll<HTMLElement>('button, a[href], input'),
        ].filter((stop) => !stop.matches(':disabled') && stop.getClientRects().length)
        const edge = event.shiftKey ? stops[0] : stops.at(-1)
        const active = document.activeElement
        if (active === edge || (event.shiftKey && !stops.some((stop) => stop === active))) {
          event.preventDefault()
          ;(event.shiftKey ? stops.at(-1) : stops[0])?.focus()
        }
      }}
    >
      <header>
        <div>
          <h2 ref={attentionHeading} tabIndex={-1}>
            Attention
          </h2>
          <p>{countText}</p>
        </div>
        <button onClick={closeAttention}>Close</button>
      </header>
      {attention.data === undefined && attention.error instanceof BuildMismatchError ? (
        <StaleBuildRead lead="Attention was not loaded." ref={attentionRetry} />
      ) : attention.data === undefined ? (
        <p role={attention.isError ? 'alert' : 'status'}>
          {attention.isError ? (
            <>
              Attention could not be loaded.{' '}
              <button
                ref={attentionRetry}
                type="button"
                onClick={() =>
                  // The retry removes this button while it reads, so the
                  // rail's heading takes focus on success (#1399) and the
                  // control that replaces it does when it fails again: Try
                  // again, or Reload on a newer build.
                  void attention.refetch().then((result) =>
                    focusAfterRetry(
                      () => (result.isSuccess ? attentionHeading.current : attentionRetry.current),
                      () => attentionRail.current,
                    ),
                  )
                }
              >
                Try again
              </button>
            </>
          ) : (
            'Loading attention…'
          )}
        </p>
      ) : (
        <>
          {attentionGroups.map((group, index) => {
            const work = group[0]
            if (!work) return null
            return (
              <div key={work.id} className="attention-work-group">
                <div className="attention-work-identity">
                  <span className="attention-position">{String(index + 1)}</span>
                  <span>
                    <strong>{work.title}</strong>
                    <small>
                      {work.authors.length ? work.authors.join(', ') : 'Author details unavailable'}
                    </small>
                    <small>
                      {work.libraryDisplayName ?? work.library} · {mediaKindLabels[work.mediaKind]}
                    </small>
                  </span>
                  <span className="attention-work-count">
                    {`${String(work.workAttentionCount)} ${work.workAttentionCount === 1 ? 'field' : 'fields'}`}
                  </span>
                </div>
                <ul className="attention-field-rows">
                  {group.map((item) => {
                    const line = `${destinationLabel(item.destination)} · ${item.classificationReason ?? 'Needs your decision'}`
                    return (
                      <li key={item.destination}>
                        <button
                          className="attention-queue-item"
                          // The same field in two works would otherwise share a name in a
                          // screen reader's list of buttons; the work is shown above (#1361).
                          aria-label={`${line}, ${work.title}`}
                          aria-current={
                            item.id === search.preview && item.destination === selected?.destination
                          }
                          aria-disabled={attention.isPlaceholderData || undefined}
                          data-attention-row={`${item.id}:${item.destination}`}
                          onClick={() =>
                            selectFromQueue(item.id, item.destination, item.attentionCursor)
                          }
                        >
                          <span className="attention-queue-copy">
                            <b>{line}</b>
                          </span>
                        </button>
                      </li>
                    )
                  })}
                </ul>
              </div>
            )
          })}
          {attention.hasNextPage ? (
            <button
              ref={loadMoreButton}
              className="attention-load-more"
              type="button"
              aria-disabled={attention.isFetchingNextPage || undefined}
              onClick={() => fetchMore(false)}
            >
              {attention.isFetchingNextPage ? 'Loading more…' : 'Load more attention'}
            </button>
          ) : null}
          {attention.isFetchNextPageError && attention.error instanceof BuildMismatchError ? (
            <StaleBuildRead lead="More attention was not loaded." ref={moreRetry} />
          ) : attention.isFetchNextPageError ? (
            <p role="alert">
              More attention could not be loaded.{' '}
              <button
                ref={moreRetry}
                type="button"
                onClick={() => {
                  // Try again leaves as the read starts; Load more, busy and
                  // still mounted, holds focus until the outcome takes it.
                  loadMoreButton.current?.focus({ preventScroll: true })
                  fetchMore(true)
                }}
              >
                Try again
              </button>
            </p>
          ) : null}
        </>
      )}
    </aside>
  )
}
