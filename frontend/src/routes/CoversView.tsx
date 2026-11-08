import { useInfiniteQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useNavigate } from '@tanstack/react-router'
import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'

import { ApiError, refusalCopy, uncertainWriteOutcome } from '../api/http'
import type {
  CoverTab,
  CoversView as CoversPage,
  CoverWorkView,
  DecisionCommand,
} from '../api/http'
import { useCoverPrepareFailure } from '../coverChoices'
import { formatClock } from '../dates'
import type { ChoiceProgress, ReviewField } from '../fieldDecision'
import { mediaKindLabels } from '../labels'
import { invalidateForegroundQueries, useRequiredQuery as useQuery } from '../queryWork'
import { ownerRefusalCopy, requestKey } from '../reviewDecisionController'
import { routeFailureCopy } from '../routeFailure'
import { detailQuery, libraryRoute, usePreparation, useReviewContext } from '../router'
import { LibraryViewSwitch } from '../components/LibraryViewSwitch'
import { StatusPill } from '../components/StatusPill'
import { LibraryCoverChoices } from './FieldDecisionCover'

// Library's Covers view (#1633, #1363): every book's cover, grouped by what it
// asks of the owner. Each choice is one gesture through the book's own
// decision command; nothing here acts on more than one book.

const groupLabels = {
  proposal: 'Switch suggested',
  choice: 'Choose',
  kept: 'Kept',
  auto: 'Switched',
  none: 'One cover',
} satisfies Record<CoverWorkView['group'], string>
const groupOrder: readonly CoverWorkView['group'][] = ['proposal', 'choice', 'kept', 'auto', 'none']
const tabLabels = {
  decide: 'To decide',
  kept: 'Kept',
  switched: 'Switched',
  all: 'All',
} satisfies Record<CoverTab, string>
const emptyCopy = {
  decide: 'No covers to decide.',
  kept: 'No kept covers with other choices.',
  switched: 'No covers switched automatically.',
  // All holds every work under review, so it is empty only when Works is.
  all: 'No works yet.',
} satisfies Record<CoverTab, string>
const staleCopy = "This book's covers changed since the page loaded. Nothing was changed."
const lostCopy = 'No answer came back. Check whether the cover was changed before choosing again.'
const question = 'Which cover should this book use?'
// The width at and below which an open book replaces the list.
const compactQuery = '(max-width: 1024px)'
const wide = () =>
  typeof window.matchMedia === 'function' && !window.matchMedia(compactQuery).matches

const byline = (work: CoverWorkView) =>
  [work.authors.join(', '), mediaKindLabels[work.media_kind]].filter(Boolean).join(' · ')
const size = (cover: { width: number; height: number }) =>
  `${String(cover.width)} × ${String(cover.height)} px`
const dayMonth = (seconds: number) =>
  new Intl.DateTimeFormat(undefined, { day: 'numeric', month: 'short' }).format(
    new Date(seconds * 1000),
  )
const dayKey = (at: Date) =>
  `${String(at.getFullYear())}-${String(at.getMonth())}-${String(at.getDate())}`
/** "Switched automatically, today at 14:05." and its yesterday and dated forms. */
const switchedLead = (seconds: number, now: Date) => {
  const at = new Date(seconds * 1000)
  const yesterday = new Date(now)
  yesterday.setDate(now.getDate() - 1)
  const clock = formatClock(seconds)
  return dayKey(at) === dayKey(now)
    ? `Switched automatically, today at ${clock}.`
    : dayKey(at) === dayKey(yesterday)
      ? `Switched automatically, yesterday at ${clock}.`
      : `Switched automatically, ${dayMonth(seconds)}, ${clock}.`
}
const bookSearch = (reviewCaseId: string) => ({ lens: 'all' as const, preview: reviewCaseId })
const focusLater = (find: () => HTMLElement | null | undefined) =>
  requestAnimationFrame(() => find()?.focus({ preventScroll: false }))

type Outcome = 'switched' | 'kept' | 'changed' | 'skipped'
const outcomeCopy = (outcome: Outcome, title: string) =>
  ({
    switched: `Switched ${title}.`,
    kept: `Kept ${title}.`,
    changed: `Changed ${title}.`,
    skipped: `Skipped ${title}.`,
  })[outcome]
type Failure = { kind: 'stale' } | { kind: 'message'; text: string }
const failureOf = (error: unknown, field: ReviewField | undefined): Failure =>
  error instanceof ApiError && error.code === 'stale'
    ? { kind: 'stale' }
    : uncertainWriteOutcome(error)
      ? { kind: 'message', text: lostCopy }
      : { kind: 'message', text: ownerRefusalCopy(error, field) ?? refusalCopy(error, lostCopy) }

// The book's own decision command, exactly as its page sends it.
const coverCommand = (
  field: ReviewField,
  outcome: 'accept' | 'keep_current',
  selectedValue: NonNullable<DecisionCommand['selectedValue']> | null,
): DecisionCommand => ({
  destination: 'cover',
  outcome,
  proposalId: field.proposalId,
  selectedValue,
  targetFingerprint: field.targetFingerprint,
  idempotencyKey: requestKey(),
  ...(field.coverConsideration
    ? {
        considerationId: field.coverConsideration.id,
        coverConfirmation: {
          expected_selection_id: field.currentCoverSelectionId ?? null,
          confirm_unverified_match:
            outcome === 'accept' &&
            field.coverConsideration.candidates.some(
              (candidate) =>
                candidate.artifactId === selectedValue?.value &&
                candidate.matchState === 'match_not_confirmed',
            ),
        },
      }
    : {}),
})
const coverField = (fields: readonly ReviewField[] | undefined) =>
  fields?.find((field) => field.destination === 'cover')

/**
 * A cover preview, or the state in its place: "No cover" for an empty cover
 * field, "Image unavailable" for one the library holds but could not be read
 * or whose preview fails to load. With no `alt` the image is decorative,
 * because the book's title is written beside it.
 */
function CoverImage({
  cover,
  unreadable,
  alt = '',
  quiet = false,
}: {
  cover: { url: string } | null
  unreadable: boolean
  alt?: string
  /** Too small to hold the words: the frame alone, the state read beside it. */
  quiet?: boolean
}) {
  const [failed, setFailed] = useState(false)
  if (cover && !failed)
    return (
      <img
        src={cover.url}
        alt={alt}
        loading="lazy"
        decoding="async"
        onError={() => setFailed(true)}
      />
    )
  const state = cover || unreadable ? 'Image unavailable' : 'No cover'
  return alt ? (
    <span className="covers-image-missing" role="img" aria-label={alt}>
      {state}
    </span>
  ) : (
    // Beside the written title, so it is not read out as part of its name.
    <span className="covers-image-missing" aria-hidden="true">
      {quiet ? null : state}
    </span>
  )
}

/** A decorative thumbnail's state, read after the title it sits beside. */
function CoverStateText({ work }: { work: CoverWorkView }) {
  if (work.cover) return null
  return <span className="sr-only">{work.unreadable_cover ? 'Image unavailable' : 'No cover'}</span>
}

function CoverComparison({
  work,
  onRecorded,
  onReplaced,
  onSkip,
  onBack,
}: {
  work: CoverWorkView
  onRecorded: (outcome: Outcome) => void
  /** The book's case was replaced; the view follows it to its successor. */
  onReplaced: (reviewCaseId: string) => void
  onSkip?: () => void
  onBack?: () => void
}) {
  const { http, queryClient } = useReviewContext()
  const detail = useQuery(detailQuery(http, work.review_case_id))
  const field = coverField(detail.data?.fields)
  const prepareFailure = useCoverPrepareFailure(work.review_case_id)
  const [progress, setProgress] = useState<ChoiceProgress>(null)
  const [failure, setFailure] = useState<Failure | null>(null)
  const [evidence, setEvidence] = useState<ReadonlySet<string>>(new Set())
  const heading = useRef<HTMLHeadingElement>(null)
  const reloadButton = useRef<HTMLButtonElement>(null)
  useEffect(() => heading.current?.focus(), [work.review_case_id])
  useEffect(() => {
    if (failure?.kind === 'stale') reloadButton.current?.focus()
  }, [failure])
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
    http.recordDecision(work.review_case_id, command).then(
      () => {
        setProgress({ command, phase: 'confirmed' })
        onRecorded(
          outcome === 'keep_current' ? 'kept' : work.group === 'auto' ? 'changed' : 'switched',
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
  return (
    <section className="covers-comparison" aria-labelledby={`covers-book-${work.review_case_id}`}>
      {onBack ? (
        <button className="covers-back" type="button" onClick={onBack}>
          Back to books
        </button>
      ) : null}
      <header>
        <h2 id={`covers-book-${work.review_case_id}`} ref={heading} tabIndex={-1}>
          <Link to="/library" search={bookSearch(work.review_case_id)}>
            {work.title}
          </Link>
        </h2>
        <p>{byline(work)}</p>
        {count ? <small>{count === 1 ? '1 cover' : `${String(count)} covers`}</small> : null}
      </header>
      {failure?.kind === 'stale' ? (
        <div className="covers-failure" role="alert">
          <p>{staleCopy}</p>
          <button ref={reloadButton} type="button" onClick={() => void reload()}>
            Reload
          </button>
        </div>
      ) : failure ? (
        <p className="covers-failure" role="alert">
          {failure.text}
        </p>
      ) : null}
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
          question={question}
          {...(work.group === 'auto'
            ? { currentLabel: 'Current cover · changed automatically' }
            : {})}
          coversLink={false}
        />
      ) : detail.isError ? (
        <div className="covers-failure" role="alert">
          <p>{routeFailureCopy(detail.error).message}</p>
          <button type="button" onClick={() => void reload()}>
            Reload
          </button>
        </div>
      ) : null}
      {onSkip ? (
        <button className="covers-skip" type="button" onClick={onSkip}>
          Skip for now
        </button>
      ) : null}
    </section>
  )
}

function SwitchedRow({
  work,
  index,
  done,
  open,
  onOpen,
  onRecorded,
  onReplaced,
}: {
  work: CoverWorkView
  index: number
  done: Outcome | undefined
  open: boolean
  onOpen: () => void
  onRecorded: (outcome: Outcome) => void
  onReplaced: (reviewCaseId: string) => void
}) {
  const { http, queryClient } = useReviewContext()
  const [failure, setFailure] = useState<Failure | null>(null)
  const [keeping, setKeeping] = useState(false)
  const change = useRef<HTMLButtonElement>(null)
  const recorded = useRef(done)
  // Once Keep or a choice under Others is recorded, focus lands on Change,
  // the row's one remaining control.
  useEffect(() => {
    if (done && recorded.current !== done) change.current?.focus()
    recorded.current = done
  }, [done])
  const switched = work.switched
  if (!switched) return null
  const titleId = `covers-switched-${work.review_case_id}`
  // Keep is the book's keep-current command; its case is read first so the
  // command carries the exact proposal and selection it answers. The command
  // admits keep-current only beside another cover, so a switch with no other
  // choice offers no Keep (human_decision.decision_actions).
  const keep = async () => {
    setFailure(null)
    setKeeping(true)
    let field: ReviewField | undefined
    try {
      const detail = await queryClient.query({
        ...detailQuery(http, work.review_case_id),
        staleTime: 0,
      })
      field = coverField(detail.fields)
      if (!field) throw new ApiError(409, 'stale')
      await http.recordDecision(work.review_case_id, coverCommand(field, 'keep_current', null))
      onRecorded('kept')
    } catch (error) {
      setFailure(failureOf(error, field))
    } finally {
      setKeeping(false)
    }
  }
  return (
    <li className="covers-switched-row">
      <figure>
        {switched.filled ? (
          // A fill replaced nothing: its caption says so, once.
          <span className="covers-image-missing" aria-hidden="true" />
        ) : (
          <CoverImage cover={switched.before} unreadable={false} alt="Previous cover" />
        )}
        <figcaption>
          Before
          {switched.before ? <small>{size(switched.before)}</small> : null}
          {switched.filled ? <small>No cover</small> : null}
        </figcaption>
      </figure>
      <span className="covers-arrow" aria-hidden="true">
        →
      </span>
      <figure>
        <CoverImage cover={work.cover} unreadable={work.unreadable_cover} alt="Cover now" />
        <figcaption>
          Now
          {work.cover ? <small>{size(work.cover)}</small> : null}
        </figcaption>
      </figure>
      <div className="covers-switched-text">
        <h3 id={titleId}>
          <Link to="/library" search={bookSearch(work.review_case_id)} data-cover-index={index}>
            {work.title}
          </Link>
        </h3>
        <p>{byline(work)}</p>
        <p>{switchedLead(switched.at, new Date())}</p>
        {failure?.kind === 'stale' ? (
          <div className="covers-failure" role="alert">
            <p>{staleCopy}</p>
            <button
              type="button"
              onClick={() => {
                setFailure(null)
                void http
                  .getCaseSuccessor(work.review_case_id)
                  .catch(() => null)
                  .then((successor) => {
                    void invalidateForegroundQueries(queryClient, { queryKey: ['covers'] })
                    if (successor && successor !== work.review_case_id) onReplaced(successor)
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
      <div className="covers-switched-actions">
        {done ? (
          <>
            <StatusPill tone="accent">{done === 'kept' ? 'Kept' : 'Changed'}</StatusPill>
            {work.choosable ? (
              <button ref={change} type="button" aria-describedby={titleId} onClick={onOpen}>
                Change
              </button>
            ) : null}
          </>
        ) : work.choosable ? (
          <>
            <button
              type="button"
              aria-describedby={titleId}
              disabled={keeping}
              onClick={() => void keep()}
            >
              Keep
            </button>
            <button type="button" aria-describedby={titleId} aria-expanded={open} onClick={onOpen}>
              Others
            </button>
          </>
        ) : null}
      </div>
      {open ? (
        <div className="covers-row-comparison">
          <CoverComparison work={work} onRecorded={onRecorded} onReplaced={onReplaced} />
        </div>
      ) : null}
    </li>
  )
}

function QueueItem({
  work,
  index,
  selected,
  done,
  onSelect,
}: {
  work: CoverWorkView
  index: number
  selected: boolean
  done: boolean
  onSelect: () => void
}) {
  return (
    <li>
      <button
        type="button"
        className={`covers-queue-item${selected ? ' is-selected' : ''}`}
        aria-current={selected ? 'true' : undefined}
        data-cover-index={index}
        onClick={onSelect}
      >
        <CoverImage cover={work.cover} unreadable={work.unreadable_cover} quiet />
        <span>
          <strong>{work.title}</strong>
          <small>{byline(work)}</small>
          <CoverStateText work={work} />
        </span>
        <StatusPill
          tone={
            done
              ? 'accent'
              : work.group === 'proposal' || work.group === 'choice'
                ? 'notice'
                : 'neutral'
          }
        >
          {done ? 'Reviewed' : groupLabels[work.group]}
        </StatusPill>
      </button>
    </li>
  )
}

/**
 * The All wall. An open book's panel follows the last tile of its row, so
 * the row stays whole above it; the row is read from the laid-out tiles.
 */
function CoverWall({
  works,
  selectedId,
  done,
  select,
  panel,
}: {
  works: readonly CoverWorkView[]
  selectedId: string | undefined
  done: Readonly<Record<string, Outcome>>
  select: (id: string | undefined) => void
  panel: (work: CoverWorkView) => ReactNode
}) {
  const wall = useRef<HTMLUListElement>(null)
  const openIndex = works.findIndex((work) => work.review_case_id === selectedId)
  // Where the panel goes, for this open tile and this many tiles. Until it is
  // measured the panel sits after the last tile, out of every row, so the
  // tiles lie as they would without it (v8 placeExpander).
  const [placed, setPlaced] = useState<{ open: number; tiles: number; end: number } | null>(null)
  const rowEnd = placed?.open === openIndex && placed.tiles === works.length ? placed.end : null
  useLayoutEffect(() => {
    if (openIndex < 0 || rowEnd !== null) return
    const tiles = [...(wall.current?.querySelectorAll<HTMLElement>(':scope > .covers-tile') ?? [])]
    const top = tiles[openIndex]?.offsetTop
    let end = openIndex
    tiles.forEach((tile, index) => {
      if (index > openIndex && tile.offsetTop === top) end = index
    })
    setPlaced({ open: openIndex, tiles: works.length, end })
  }, [openIndex, rowEnd, works.length])
  // A resize lays the rows out again: the panel returns to the end and the
  // row is measured afresh.
  useEffect(() => {
    if (openIndex < 0) return
    const remeasure = () => setPlaced(null)
    window.addEventListener('resize', remeasure)
    return () => window.removeEventListener('resize', remeasure)
  }, [openIndex])
  const open = openIndex >= 0 ? works[openIndex] : undefined
  return (
    <ul className="covers-wall" ref={wall}>
      {works.flatMap((work, index) => {
        const isOpen = work.review_case_id === selectedId
        const tile = (
          <li key={work.review_case_id} className="covers-tile">
            <button
              type="button"
              aria-expanded={isOpen}
              data-cover-index={index}
              onClick={() => select(isOpen ? undefined : work.review_case_id)}
            >
              <CoverImage cover={work.cover} unreadable={work.unreadable_cover} />
              <StatusPill
                tone={
                  work.group === 'proposal' || work.group === 'choice'
                    ? 'notice'
                    : work.group === 'auto'
                      ? 'accent'
                      : 'neutral'
                }
              >
                {done[work.review_case_id] ? 'Reviewed' : groupLabels[work.group]}
              </StatusPill>
              <strong>{work.title}</strong>
              <small>{work.authors.join(', ')}</small>
              <CoverStateText work={work} />
            </button>
          </li>
        )
        return open && index === (rowEnd ?? works.length - 1)
          ? [
              tile,
              <li key="covers-wall-panel" className="covers-wall-panel">
                {panel(open)}
              </li>,
            ]
          : [tile]
      })}
    </ul>
  )
}

export default function CoversView() {
  const search = libraryRoute.useSearch()
  const navigate = useNavigate({ from: '/library' })
  const { http } = useReviewContext()
  const queryClient = useQueryClient()
  const tab: CoverTab = search.tab ?? 'decide'
  // As Works says it: only a confirmed snapshot with no library names the
  // way to fill the list.
  const noLibraryConnected = usePreparation().data?.libraries.length === 0
  const pages = useInfiniteQuery({
    queryKey: ['covers', tab] as const,
    queryFn: ({ pageParam, signal }) =>
      http.getCovers(tab, pageParam ? { cursor: pageParam } : {}, signal),
    initialPageParam: null as string | null,
    getNextPageParam: (page) => page.next_cursor,
    staleTime: 15_000,
    retry: false,
  })
  const [notice, setNotice] = useState<string | null>(null)
  const [done, setDone] = useState<Readonly<Record<string, Outcome>>>({})
  // Counts are read again after each decision, else the newest page's
  // count. The rows on screen stay as they were, with the decided ones
  // marked, and the next page resumes after them.
  const [countRead, setCountRead] = useState<{ tab: CoverTab; seq: number; pageAt: number }>()
  const [liveCounts, setLiveCounts] = useState<{
    tab: CoverTab
    counts: CoversPage['counts']
    pageAt: number
  }>()
  const works = pages.data?.pages.flatMap((page) => page.items) ?? []
  const counts =
    (liveCounts?.tab === tab && liveCounts.pageAt === pages.dataUpdatedAt
      ? liveCounts.counts
      : undefined) ?? pages.data?.pages.at(-1)?.counts
  const selectedId = search.cover
  const listed = works.find((work) => work.review_case_id === selectedId)
  // A linked book past the loaded pages is read on its own.
  const linked = useQuery({
    queryKey: ['covers', tab, 'book', selectedId] as const,
    queryFn: ({ signal }) => http.getCovers(tab, { cover: selectedId ?? '' }, signal),
    enabled: Boolean(selectedId) && pages.isSuccess && !listed,
    staleTime: 15_000,
    retry: false,
  })
  const selected = listed ?? (linked.isSuccess ? linked.data.items[0] : undefined)
  const select = (id: string | undefined, replace = false) =>
    void navigate({
      replace,
      resetScroll: false,
      search: (current) => {
        const next = { ...current }
        if (id) next.cover = id
        else delete next.cover
        return next
      },
    })
  const ordered =
    tab === 'decide' || tab === 'kept'
      ? groupOrder.flatMap((group) => works.filter((work) => work.group === group))
      : works
  const firstOpen = ordered.find((work) => done[work.review_case_id] === undefined)
  // A selection the tab no longer holds is dropped, so a phone never shows
  // neither the list nor a book.
  useEffect(() => {
    if (selectedId && !listed && linked.isSuccess && linked.data.items.length === 0)
      select(undefined, true)
  })
  // On a wide screen To decide and Kept open their first undecided book
  // beside the list, so the first tap already chooses.
  useEffect(() => {
    if (!selectedId && (tab === 'decide' || tab === 'kept') && firstOpen && wide())
      select(firstOpen.review_case_id, true)
  })
  // Each decision asks for the counts again. Only the newest read is shown:
  // a newer request cancels the answer of an older one, and a later read of
  // the first page replaces it.
  const recordCounts = () =>
    setCountRead((current) => ({
      tab,
      seq: (current?.seq ?? 0) + 1,
      pageAt: pages.dataUpdatedAt,
    }))
  useEffect(() => {
    if (!countRead) return
    let current = true
    void http.getCovers(countRead.tab).then(
      (page) => {
        if (current)
          setLiveCounts({ tab: countRead.tab, counts: page.counts, pageAt: countRead.pageAt })
      },
      () => undefined,
    )
    return () => {
      current = false
    }
  }, [countRead, http])
  // After a choice or a skip the next book opens, so one tap moves on.
  const advance = (work: CoverWorkView, outcome: Outcome) => {
    setNotice(outcomeCopy(outcome, work.title))
    if (outcome !== 'skipped') {
      setDone((current) => ({ ...current, [work.review_case_id]: outcome }))
      void queryClient.invalidateQueries({ queryKey: ['library'] })
      recordCounts()
    }
    if (tab === 'switched' || tab === 'all') {
      select(undefined)
      return
    }
    const index = ordered.indexOf(work)
    const next = ordered.slice(index + 1).find((item) => done[item.review_case_id] === undefined)
    select(next?.review_case_id)
  }
  const replaced = (reviewCaseId: string) => {
    setNotice(null)
    select(reviewCaseId, true)
  }
  const changeTab = (next: CoverTab) => {
    setNotice(null)
    void navigate({ search: { lens: 'all', view: 'covers', tab: next } })
  }
  const showMore = () => {
    const first = works.length
    void pages
      .fetchNextPage()
      .then(() =>
        focusLater(() =>
          document.querySelector<HTMLElement>(`.covers-list [data-cover-index="${String(first)}"]`),
        ),
      )
  }
  const comparison = (work: CoverWorkView, queue: boolean) => (
    <CoverComparison
      key={work.review_case_id}
      work={work}
      onRecorded={(outcome) => advance(work, outcome)}
      onReplaced={replaced}
      {...(queue
        ? { onSkip: () => advance(work, 'skipped'), onBack: () => select(undefined) }
        : {})}
    />
  )
  const closable = (work: CoverWorkView) => (
    <div className="covers-row-comparison">
      <button
        className="covers-close"
        type="button"
        onClick={() => {
          select(undefined)
          focusLater(() =>
            document.querySelector<HTMLElement>(
              `.covers-list [data-cover-index="${String(works.indexOf(work))}"]`,
            ),
          )
        }}
      >
        Close
      </button>
      {comparison(work, false)}
    </div>
  )
  return (
    <div className={`covers-page${selected ? ' has-selection' : ''}`}>
      <LibraryViewSwitch view="covers" />
      <header className="library-header covers-header">
        <h1 data-route-focus tabIndex={-1}>
          Covers
        </h1>
      </header>
      <nav className="covers-tabs" aria-label="Cover views">
        {(Object.keys(tabLabels) as CoverTab[]).map((value) => (
          <button
            key={value}
            type="button"
            aria-pressed={value === tab}
            onClick={() => {
              if (value !== tab) changeTab(value)
            }}
          >
            {tabLabels[value]} <strong>{counts ? String(counts[value]) : '…'}</strong>
          </button>
        ))}
      </nav>
      <p className="covers-notice" role="status">
        {notice}
      </p>
      {selected && !listed && tab !== 'decide' && tab !== 'kept' ? closable(selected) : null}
      <div className="covers-list">
        {pages.isPending ? (
          <p role="status">Loading covers…</p>
        ) : pages.isError ? (
          <div className="covers-failure" role="alert">
            <p>{routeFailureCopy(pages.error).message}</p>
            <button type="button" onClick={() => void pages.refetch()}>
              Reload
            </button>
          </div>
        ) : works.length === 0 ? (
          <p className="covers-empty">
            {tab === 'all' && noLibraryConnected ? (
              <>
                No works yet. Add a library in{' '}
                <Link to="/settings" search={{ group: 'libraries' }}>
                  Settings
                </Link>
                .
              </>
            ) : (
              emptyCopy[tab]
            )}
          </p>
        ) : tab === 'switched' ? (
          <ul className="covers-switched">
            {works.map((work, index) => (
              <SwitchedRow
                key={work.review_case_id}
                work={work}
                index={index}
                done={done[work.review_case_id]}
                open={selectedId === work.review_case_id}
                onOpen={() =>
                  select(selectedId === work.review_case_id ? undefined : work.review_case_id)
                }
                onRecorded={(outcome) => advance(work, outcome)}
                onReplaced={replaced}
              />
            ))}
          </ul>
        ) : tab === 'all' ? (
          <CoverWall
            works={works}
            selectedId={selectedId}
            done={done}
            select={select}
            panel={closable}
          />
        ) : (
          <div className="covers-queue">
            <div className="covers-queue-list">
              {groupOrder
                .filter((group) => ordered.some((work) => work.group === group))
                .map((group) => {
                  const members = ordered.filter((work) => work.group === group)
                  return (
                    <section key={group} aria-label={groupLabels[group]}>
                      <h2>
                        {groupLabels[group]} <span>{String(members.length)}</span>
                      </h2>
                      <ul>
                        {members.map((work) => (
                          <QueueItem
                            key={work.review_case_id}
                            work={work}
                            index={works.indexOf(work)}
                            selected={work.review_case_id === selected?.review_case_id}
                            done={done[work.review_case_id] !== undefined}
                            onSelect={() => select(work.review_case_id)}
                          />
                        ))}
                      </ul>
                    </section>
                  )
                })}
            </div>
            {selected ? comparison(selected, true) : null}
          </div>
        )}
      </div>
      {pages.hasNextPage ? (
        <button
          className="covers-more"
          type="button"
          disabled={pages.isFetchingNextPage}
          onClick={showMore}
        >
          Show more covers
        </button>
      ) : null}
    </div>
  )
}
