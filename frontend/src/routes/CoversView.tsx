import { useInfiniteQuery, useQueryClient } from '@tanstack/react-query'
import type { UseQueryResult } from '@tanstack/react-query'
import { Link, useNavigate, useRouter } from '@tanstack/react-router'
import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import type { ReactNode, RefObject } from 'react'

import { ApiError } from '../api/http'
import type {
  CoverTab,
  CoversTab,
  CoversView as CoversPage,
  CoverWorkView,
  DecisionCommand,
  TasteProfileView as TasteProfileRead,
} from '../api/http'
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
import { tasteQuery } from '../coverPicks'
import { formatClock } from '../dates'
import type { ReviewField } from '../fieldDecision'
import { coverFaultCodes, coverFaultLabel, coverReasonLine } from '../labels'
import {
  invalidateForegroundQueries,
  queryWorkClass,
  useRequiredQuery as useQuery,
} from '../queryWork'
import { routeFailureCopy } from '../routeFailure'
import { detailQuery, libraryRoute, usePreparation, useReviewContext } from '../router'
import { Dropdown } from '../components/Dropdown'
import { LibraryViewSwitch } from '../components/LibraryViewSwitch'
import { StatusPill } from '../components/StatusPill'
import { CoverCalibration } from './CoverCalibration'
import type { CalibrationEnd } from './CoverCalibration'
import { CoverComparison } from './CoverComparison'
import { CalibrationStart, CoversWalkthrough } from './CoversIntro'
import TasteProfileView from './TasteProfileView'

// Library's Covers view (#1633, #1363): every book's cover, grouped by what it
// asks of the owner. Each choice is one gesture through the book's own
// decision command; nothing here acts on more than one book.

// A cover the owner chose that is not yet written is their choice, not a kept
// cover; it is listed only under All (#1866).
const groupLabels = {
  proposal: 'Switch suggested',
  choice: 'Choose',
  chosen: 'Your choice',
  kept: 'Kept',
  auto: 'Switched',
  none: 'One cover',
} satisfies Record<CoverWorkView['group'], string>
const groupOrder: readonly CoverWorkView['group'][] = [
  'proposal',
  'choice',
  'chosen',
  'kept',
  'auto',
  'none',
]
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
// The width at and below which an open book replaces the list.
const compactQuery = '(max-width: 1024px)'
const wide = () =>
  typeof window.matchMedia === 'function' && !window.matchMedia(compactQuery).matches

const size = (cover: { width: number; height: number }) =>
  `${String(cover.width)} × ${String(cover.height)} px`
const audiobook = (work: CoverWorkView) => ['m4b', 'm4a', 'mp3', 'audio'].includes(work.media_kind)
/** The measured rule's reason a row carries: its switch, its suggestion or its hold. */
const rowFault = (work: CoverWorkView) =>
  work.group === 'auto'
    ? (work.switched?.reason ?? null)
    : (work.suggestion?.reason ?? work.hold?.reason ?? null)
function FaultPill({ work, className }: { work: CoverWorkView; className?: string }) {
  const fault = rowFault(work)
  const label = fault === null ? null : coverFaultLabel(fault, audiobook(work))
  return label === null ? null : (
    <StatusPill tone="notice" {...(className ? { className } : {})}>
      {label}
    </StatusPill>
  )
}
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
const focusLater = (find: () => HTMLElement | null | undefined) =>
  requestAnimationFrame(() => find()?.focus({ preventScroll: false }))

const outcomeCopy = (outcome: Outcome, title: string) =>
  ({
    switched: `Switched ${title}.`,
    kept: `Kept ${title}.`,
    changed: `Changed ${title}.`,
    put_back: `Put back ${title}.`,
    skipped: `Skipped ${title}.`,
  })[outcome]
const donePills = {
  switched: 'Changed',
  kept: 'Kept',
  changed: 'Changed',
  put_back: 'Put back',
  skipped: 'Changed',
} satisfies Record<Outcome, string>
const putBackRefusedCopy = 'The cover was changed in Audiobookshelf, so it was not put back.'
// A Put back's write still running: the row already reads as put back.
const putBackPending = new Set(['planned', 'claimed', 'applying', 'reconciling', 'retryable'])
/** A Put back whose write found the cover changed in Audiobookshelf first. */
const putBackRefused = (work: CoverWorkView) =>
  work.switched?.put_back?.state === 'stale' && work.switched.put_back.failure === 'target_changed'
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
  missingAlt = alt,
  quiet = false,
}: {
  cover: { url: string } | null
  unreadable: boolean
  alt?: string
  /** The frame's name when no preview shows: what is missing, not the image. */
  missingAlt?: string
  /** Too small to hold the words: the frame alone, the state read beside it. */
  quiet?: boolean
}) {
  // A failure belongs to the preview that failed: a refresh that names a
  // new preview for the same book tries it.
  const [failedUrl, setFailedUrl] = useState<string | null>(null)
  if (cover && failedUrl !== cover.url)
    return (
      <img
        src={cover.url}
        alt={alt}
        loading="lazy"
        decoding="async"
        onError={() => setFailedUrl(cover.url)}
      />
    )
  const state = cover || unreadable ? 'Image unavailable' : 'No cover'
  return alt ? (
    <span className="covers-image-missing" role="img" aria-label={missingAlt}>
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

const reasonLine = (work: CoverWorkView) =>
  work.switched?.reason
    ? coverReasonLine(work.switched.reason, {
        audiobook: audiobook(work),
        before: work.switched.before,
        after: work.cover,
        language: work.switched.language,
        filled: work.switched.filled,
      })
    : null
/** A switched row's Before and Now frames, with the arrow between them. */
function SwitchedFrames({
  work,
  switched,
}: {
  work: CoverWorkView
  switched: NonNullable<CoverWorkView['switched']>
}) {
  return (
    <>
      <figure>
        {switched.filled ? (
          // A fill replaced nothing: its caption says so, once.
          <span className="covers-image-missing" aria-hidden="true" />
        ) : (
          // A replacement always had a cover; with no preview to show, it is
          // unavailable, never "No cover".
          <CoverImage
            cover={switched.before}
            unreadable
            alt="Previous cover"
            missingAlt="Previous cover unavailable"
          />
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
        <CoverImage
          cover={work.cover}
          unreadable={work.unreadable_cover}
          alt="Cover now"
          missingAlt={
            work.cover || work.unreadable_cover ? 'Current cover unavailable' : 'No cover'
          }
        />
        <figcaption>
          Now
          {work.cover ? <small>{size(work.cover)}</small> : null}
        </figcaption>
      </figure>
    </>
  )
}
/** Why a switched row's last action did not record, or why nothing was put back. */
function SwitchedFailure({
  failure,
  refused,
  onReload,
}: {
  failure: Failure | null
  refused: boolean
  onReload: () => void
}) {
  return failure?.kind === 'stale' ? (
    <div className="covers-failure" role="alert">
      <p>{staleCopy}</p>
      <button type="button" onClick={onReload}>
        Reload
      </button>
    </div>
  ) : failure ? (
    <p className="covers-failure" role="alert">
      {failure.text}
    </p>
  ) : refused ? (
    // Announced once if it arrives with a live update while the row is open.
    <p className="covers-failure" role="status">
      {putBackRefusedCopy}
    </p>
  ) : null
}
/** A switched row's outcome and Change, or its Put back, Keep and Others. */
function SwitchedActions({
  work,
  titleId,
  shown,
  offerPutBack,
  busy,
  open,
  change,
  onPutBack,
  onKeep,
  onOpen,
}: {
  work: CoverWorkView
  titleId: string
  shown: Outcome | undefined
  offerPutBack: boolean
  busy: boolean
  open: boolean
  change: RefObject<HTMLButtonElement | null>
  onPutBack: () => void
  onKeep: () => void
  onOpen: () => void
}) {
  return (
    <div className="covers-switched-actions">
      {shown ? (
        <>
          <StatusPill tone="accent">{donePills[shown]}</StatusPill>
          {work.choosable ? (
            <button ref={change} type="button" aria-describedby={titleId} onClick={onOpen}>
              Change
            </button>
          ) : null}
        </>
      ) : work.choosable ? (
        <>
          {/* Offered only while the book's comparison has the original: before
              the book is prepared again after its switch, it cannot be chosen. */}
          {offerPutBack ? (
            <button type="button" aria-describedby={titleId} disabled={busy} onClick={onPutBack}>
              Put back
            </button>
          ) : null}
          <button type="button" aria-describedby={titleId} disabled={busy} onClick={onKeep}>
            Keep
          </button>
          <button type="button" aria-describedby={titleId} aria-expanded={open} onClick={onOpen}>
            Others
          </button>
        </>
      ) : null}
    </div>
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
  // The server's own record of a Put back: still writing reads as put back,
  // and a target that changed first says why nothing was put back.
  const putBack = switched.put_back
  // A refusal outranks this view's own Put back mark, which the list drops,
  // so the row offers Keep and Others again and says why.
  const refused = putBackRefused(work)
  const shown = done ?? (putBack && putBackPending.has(putBack.state) ? 'put_back' : undefined)
  // Keep and Put back read the book's case first, so the command carries the
  // exact proposal and selection it answers; a case with nothing to send is
  // stale.
  const recordFromCase = async (
    command: (field: ReviewField | undefined) => DecisionCommand | null,
    outcome: Outcome,
  ) => {
    setFailure(null)
    setKeeping(true)
    let field: ReviewField | undefined
    try {
      const detail = await queryClient.query({
        ...detailQuery(http, work.review_case_id),
        staleTime: 0,
      })
      field = coverField(detail.fields)
      const next = command(field)
      if (!next) throw new ApiError(409, 'stale')
      await submitCover(http, queryClient, work.review_case_id, next)
      onRecorded(outcome)
    } catch (error) {
      setFailure(failureOf(error, field))
    } finally {
      setKeeping(false)
    }
  }
  // Keep is the book's keep-current command. The command admits keep-current
  // only beside another cover, so a switch with no other choice offers no
  // Keep (human_decision.decision_actions).
  const keep = () =>
    recordFromCase((field) => (field ? coverCommand(field, 'keep_current', null) : null), 'kept')
  // Put back is the book's accept of the original the switch replaced; an
  // original no longer offered is stale.
  const putBackOriginal = () =>
    recordFromCase((field) => {
      const original = retainedCandidate(field)
      return field && original
        ? coverCommand(field, 'accept', { kind: 'cover', value: original.artifactId })
        : null
    }, 'put_back')
  const reload = () => {
    setFailure(null)
    void http
      .getCaseSuccessor(work.review_case_id)
      .catch(() => null)
      .then((successor) => {
        void invalidateForegroundQueries(queryClient, { queryKey: ['covers'] })
        if (successor && successor !== work.review_case_id) onReplaced(successor)
      })
  }
  return (
    <li className="covers-switched-row">
      <SwitchedFrames work={work} switched={switched} />
      <div className="covers-switched-text">
        <h3 id={titleId}>
          <Link to="/library" search={bookSearch(work.review_case_id)} data-cover-index={index}>
            {work.title}
          </Link>
        </h3>
        <p>{byline(work)}</p>
        <p>{switchedLead(switched.at, new Date())}</p>
        {switched.reason ? (
          <>
            <FaultPill work={work} />
            {reasonLine(work) ? <p>{reasonLine(work)}</p> : null}
          </>
        ) : null}
        <SwitchedFailure failure={failure} refused={refused && !done} onReload={reload} />
      </div>
      <SwitchedActions
        work={work}
        titleId={titleId}
        shown={shown}
        offerPutBack={switched.put_back_offered && !refused}
        busy={keeping}
        open={open}
        change={change}
        onPutBack={() => void putBackOriginal()}
        onKeep={() => void keep()}
        onOpen={onOpen}
      />
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
          {/* The 48 px frame is too small for the words, so the state is read here. */}
          {work.cover ? null : (
            <small>{work.unreadable_cover ? 'Image unavailable' : 'No cover'}</small>
          )}
        </span>
        {/* On a phone the two pills share one row under the title. */}
        <span className="covers-queue-pills">
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
          {/* Its own row under the title and status, so a long hold stays on one line. */}
          {done ? null : <FaultPill work={work} className="covers-queue-hold" />}
        </span>
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

/** A decided book kept on its tab's list, after the book it followed. */
interface Retained {
  tab: CoverTab
  work: CoverWorkView
  after: string | null
}
/**
 * The listed books with each decided one the newest read left out put back
 * after the book it followed, or first when it led the list. A book whose
 * predecessor was decided too follows it wherever that one was put back.
 */
const withRetained = (listed: CoverWorkView[], retained: readonly Retained[]) => {
  const ids = new Set(listed.map((work) => work.review_case_id))
  const missing = retained.filter((row) => !ids.has(row.work.review_case_id))
  if (missing.length === 0) return listed
  const merged = [...listed]
  const pending = [...missing]
  while (pending.length) {
    const ready = pending.findIndex(
      (row) => row.after === null || merged.some((work) => work.review_case_id === row.after),
    )
    // A book whose predecessor is nowhere to be found leads the list.
    const [row] = pending.splice(Math.max(ready, 0), 1)
    if (!row) break
    const at = ready < 0 ? -1 : merged.findIndex((work) => work.review_case_id === row.after)
    merged.splice(at + 1, 0, row.work)
  }
  return merged
}

// Brings a tab fully into its sideways row, with room for its focus ring
// (3 px with a 3 px offset), except where the row's own ends prevent it.
const tabRingRoom = 6
function revealTab(row: HTMLElement, tab: Element) {
  if (row.scrollWidth <= row.clientWidth || tab === row) return
  const bounds = row.getBoundingClientRect()
  const box = tab.getBoundingClientRect()
  if (box.left - tabRingRoom < bounds.left) row.scrollLeft -= bounds.left - box.left + tabRingRoom
  else if (box.right + tabRingRoom > bounds.right)
    row.scrollLeft += box.right + tabRingRoom - bounds.right
}

/** The row of Covers tabs with each tab's count, and Taste profile. */
function CoverTabs({
  tab,
  taste,
  counts,
  onChange,
}: {
  tab: CoverTab
  taste: boolean
  counts: CoversPage['counts'] | undefined
  onChange: (next: CoversTab) => void
}) {
  // On a phone the tabs are one row that scrolls sideways; the selected tab
  // is brought into that row's view without scrolling the page. The router
  // restores the row's old scroll position once a navigation has rendered,
  // after this view's effects, so the selected tab is brought back after it.
  const tabsRef = useRef<HTMLElement>(null)
  const revealSelectedTab = () => {
    const row = tabsRef.current
    const selectedTab = row?.querySelector('[aria-pressed="true"]')
    if (row && selectedTab) revealTab(row, selectedTab)
  }
  useEffect(revealSelectedTab, [tab, taste])
  const router = useRouter()
  useEffect(() => router.subscribe('onRendered', revealSelectedTab), [router])
  return (
    <nav
      ref={tabsRef}
      className="covers-tabs"
      aria-label="Cover views"
      // Chromium scrolls a focused tab into the row only when it is fully
      // hidden, so a partly hidden tab reached with Tab is brought in here.
      onFocus={(event) => revealTab(event.currentTarget, event.target)}
    >
      {(Object.keys(tabLabels) as CoverTab[]).map((value) => (
        <button
          key={value}
          type="button"
          aria-pressed={!taste && value === tab}
          onClick={() => {
            if (taste || value !== tab) onChange(value)
          }}
        >
          {tabLabels[value]} <strong>{counts ? String(counts[value]) : '…'}</strong>
        </button>
      ))}
      <button
        type="button"
        aria-pressed={taste}
        onClick={() => {
          if (!taste) onChange('taste')
        }}
      >
        Taste profile
      </button>
    </nav>
  )
}

/**
 * One tab's books: its pages, its counts read again after each decision, the
 * open book, and the decided books kept where they were.
 */
function useCoverQueue(tab: CoverTab, inList: boolean, setNotice: (notice: string | null) => void) {
  const search = libraryRoute.useSearch()
  const navigate = useNavigate({ from: '/library' })
  const router = useRouter()
  const { http } = useReviewContext()
  const queryClient = useQueryClient()
  const pages = useInfiniteQuery({
    queryKey: ['covers', tab] as const,
    queryFn: (context) =>
      http.getCovers(
        tab,
        context.pageParam ? { cursor: context.pageParam } : {},
        context.signal,
        queryWorkClass(context),
      ),
    initialPageParam: null as string | null,
    getNextPageParam: (page) => page.next_cursor,
    staleTime: 15_000,
    retry: false,
  })
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
  // A decided book stays where it was when the list is read again, after a
  // library update or a full resync, even once its tab no longer holds it.
  const [retained, setRetained] = useState<readonly Retained[]>([])
  const works = withRetained(
    pages.data?.pages.flatMap((page) => page.items) ?? [],
    retained.filter((row) => row.tab === tab),
  )
  const counts =
    (liveCounts?.tab === tab && liveCounts.pageAt === pages.dataUpdatedAt
      ? liveCounts.counts
      : undefined) ?? pages.data?.pages.at(-1)?.counts
  const selectedId = search.cover
  const listed = works.find((work) => work.review_case_id === selectedId)
  // A linked book past the loaded pages is read on its own.
  const linked = useQuery({
    queryKey: ['covers', tab, 'book', selectedId] as const,
    queryFn: (context) =>
      http.getCovers(tab, { cover: selectedId ?? '' }, context.signal, queryWorkClass(context)),
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
  // A Put back recorded here whose write the server then reports refused is
  // no longer marked: the book is undecided again (#1363).
  const marked: Readonly<Record<string, Outcome>> = Object.fromEntries(
    Object.entries(done).filter(
      ([id, outcome]) =>
        outcome !== 'put_back' ||
        !works.some((work) => work.review_case_id === id && putBackRefused(work)),
    ),
  )
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
  // An acknowledgement can arrive after the owner has opened another book
  // or tab: the outcome is still recorded, but only the book still open
  // moves on, so a slow answer never takes over the comparison in use.
  const stillShown = (work: CoverWorkView) => {
    const current = router.state.location.search as { tab?: CoversTab; cover?: string }
    return (current.tab ?? 'decide') === tab && current.cover === work.review_case_id
  }
  // After a choice or a skip the next book opens, so one tap moves on.
  const advance = (work: CoverWorkView, outcome: Outcome) => {
    setNotice(outcomeCopy(outcome, work.title))
    if (outcome !== 'skipped') {
      setDone((current) => ({ ...current, [work.review_case_id]: outcome }))
      const at = works.indexOf(work)
      setRetained((current) => [
        ...current.filter(
          (row) => row.tab !== tab || row.work.review_case_id !== work.review_case_id,
        ),
        { tab, work, after: at > 0 ? (works[at - 1]?.review_case_id ?? null) : null },
      ])
      void queryClient.invalidateQueries({ queryKey: ['library'] })
      recordCounts()
    }
    if (!stillShown(work)) return
    if (tab === 'switched' || tab === 'all') {
      select(undefined)
      return
    }
    const index = ordered.indexOf(work)
    const next = ordered.slice(index + 1).find((item) => marked[item.review_case_id] === undefined)
    select(next?.review_case_id)
  }
  const replaced = (reviewCaseId: string) => {
    setNotice(null)
    select(reviewCaseId, true)
  }
  const firstOpen = ordered.find((work) => marked[work.review_case_id] === undefined)
  // A selection the tab no longer holds is dropped, so a phone never shows
  // neither the list nor a book.
  useEffect(() => {
    if (inList && selectedId && !listed && linked.isSuccess && linked.data.items.length === 0)
      select(undefined, true)
  })
  // On a wide screen To decide and Kept open their first undecided book
  // beside the list, so the first tap already chooses.
  useEffect(() => {
    if (inList && !selectedId && (tab === 'decide' || tab === 'kept') && firstOpen && wide())
      select(firstOpen.review_case_id, true)
  })
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
  return {
    pages,
    works,
    counts,
    selectedId,
    listed,
    linked,
    selected,
    select,
    ordered,
    marked,
    advance,
    replaced,
    showMore,
  }
}

/** To decide and Kept: the list beside the open book, with an entry at its top. */
function CoverQueue({
  ordered,
  works,
  selected,
  marked,
  select,
  entry,
  comparison,
}: {
  ordered: readonly CoverWorkView[]
  works: readonly CoverWorkView[]
  selected: CoverWorkView | undefined
  marked: Readonly<Record<string, Outcome>>
  select: (id: string) => void
  entry: ReactNode
  comparison: (work: CoverWorkView) => ReactNode
}) {
  return (
    <div className="covers-queue">
      <div className="covers-queue-list">
        {entry}
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
                      done={marked[work.review_case_id] !== undefined}
                      onSelect={() => select(work.review_case_id)}
                    />
                  ))}
                </ul>
              </section>
            )
          })}
      </div>
      {selected ? comparison(selected) : null}
    </div>
  )
}

/** Switched's rows, which the owner can show by the reason they were switched. */
function SwitchedList({
  works,
  marked,
  selectedId,
  select,
  advance,
  replaced,
}: {
  works: readonly CoverWorkView[]
  marked: Readonly<Record<string, Outcome>>
  selectedId: string | undefined
  select: (id: string | undefined) => void
  advance: (work: CoverWorkView, outcome: Outcome) => void
  replaced: (reviewCaseId: string) => void
}) {
  // Switched rows by the reason they were switched; fills only under All
  // (#1363 Q14). Component state, not a URL parameter.
  const [show, setShow] = useState('all')
  const faults = coverFaultCodes.flatMap((code) => {
    const first = works.find((work) => work.group === 'auto' && work.switched?.reason === code)
    const label = first ? coverFaultLabel(code, audiobook(first)) : null
    return label === null ? [] : [{ value: code, label }]
  })
  const shown = faults.some((fault) => fault.value === show) ? show : 'all'
  return (
    <>
      {faults.length ? (
        <div className="covers-show">
          <span aria-hidden="true">Show</span>
          <Dropdown
            ariaLabel="Show"
            compact
            value={shown}
            options={[{ value: 'all', label: 'All' }, ...faults]}
            onChange={setShow}
          />
        </div>
      ) : null}
      <ul className="covers-switched">
        {works
          .filter((work) => shown === 'all' || work.switched?.reason === shown)
          .map((work, index) => (
            <SwitchedRow
              key={work.review_case_id}
              work={work}
              index={index}
              done={marked[work.review_case_id]}
              open={selectedId === work.review_case_id}
              onOpen={() =>
                select(selectedId === work.review_case_id ? undefined : work.review_case_id)
              }
              onRecorded={(outcome) => advance(work, outcome)}
              onReplaced={replaced}
            />
          ))}
      </ul>
    </>
  )
}

/** Whether To decide offers picks: a taste model, no profile, fewer than 25 picks. */
const picksDue = (taste: TasteProfileRead) =>
  taste.configured && taste.profile === null && taste.chosen < taste.required

/** Covers' picks in the tabs' place, once the taste read says where the owner is. */
function CoverPicks({
  taste,
  onEnd,
}: {
  taste: UseQueryResult<TasteProfileRead>
  onEnd: (end: CalibrationEnd) => void
}) {
  const navigate = useNavigate({ from: '/library' })
  // Picks open only as Start offers them (S9): a typed or kept link while no
  // taste model is set up, a profile exists or 25 picks count goes back to
  // To decide. Judged on the first read only, so the 25th pick still ends here.
  const [admitted, setAdmitted] = useState<boolean | null>(null)
  if (admitted === null && taste.data) setAdmitted(picksDue(taste.data))
  useEffect(() => {
    if (admitted === false)
      void navigate({ replace: true, search: { lens: 'all', view: 'covers', tab: 'decide' } })
  }, [admitted, navigate])
  return admitted === false ? null : taste.data ? (
    <CoverCalibration
      taste={taste.data}
      // Only a read that answered counts: a failed one keeps the old count.
      tasteReadAt={taste.dataUpdatedAt}
      countFailure={taste.errorUpdatedAt > taste.dataUpdatedAt ? taste.error : null}
      onRecount={() => void taste.refetch()}
      onEnd={onEnd}
    />
  ) : taste.isError ? (
    <div className="covers-failure" role="alert">
      <p>{routeFailureCopy(taste.error).message}</p>
      <button type="button" onClick={() => void taste.refetch()}>
        Reload
      </button>
    </div>
  ) : (
    <p role="status">Loading covers…</p>
  )
}

export default function CoversView() {
  const search = libraryRoute.useSearch()
  const navigate = useNavigate({ from: '/library' })
  const { http } = useReviewContext()
  // Taste profile and picks have no page of books; To decide's page keeps the
  // counts.
  const taste = search.tab === 'taste'
  const picks = search.tab === 'picks'
  const tab: CoverTab =
    search.tab === undefined || search.tab === 'taste' || search.tab === 'picks'
      ? 'decide'
      : search.tab
  const inList = !taste && !picks
  // As Works says it: only a confirmed snapshot with no library names the
  // way to fill the list.
  const noLibraryConnected = usePreparation().data?.libraries.length === 0
  const [notice, setNotice] = useState<string | null>(null)
  const queue = useCoverQueue(tab, inList, setNotice)
  const { pages, works, selectedId, listed, linked, selected, select, ordered, marked } = queue
  // While no taste profile exists, To decide offers 25 picks (#1372 S9).
  const tasteRead = useQuery(tasteQuery(http))
  const profileDue = tasteRead.data !== undefined && picksDue(tasteRead.data)
  const offerPicks = inList && tab === 'decide' && profileDue
  const startRef = useRef<HTMLButtonElement>(null)
  // After picks end on To decide, focus stays on Start rather than the book
  // that opens beside the list; the next book the owner opens takes it.
  const [holdFocus, setHoldFocus] = useState(false)
  const [focusBuild, setFocusBuild] = useState(false)
  const changeTab = (next: CoversTab) => {
    setNotice(null)
    setHoldFocus(false)
    setFocusBuild(false)
    void navigate({ search: { lens: 'all', view: 'covers', tab: next } })
  }
  const endPicks = (end: CalibrationEnd) => {
    if (end.kind === 'done') {
      setFocusBuild(true)
      void navigate({ replace: true, search: { lens: 'all', view: 'covers', tab: 'taste' } })
      return
    }
    setNotice(end.text)
    setHoldFocus(true)
    void navigate({ replace: true, search: { lens: 'all', view: 'covers', tab: 'decide' } })
    focusLater(() => startRef.current)
  }
  const comparison = (work: CoverWorkView, inQueue: boolean) => (
    <CoverComparison
      key={work.review_case_id}
      work={work}
      onRecorded={(outcome) => queue.advance(work, outcome)}
      onReplaced={queue.replaced}
      focusTitle={!holdFocus}
      {...(inQueue
        ? { onSkip: () => queue.advance(work, 'skipped'), onBack: () => select(undefined) }
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
  const entry = offerPicks ? (
    <CalibrationStart startRef={startRef} onStart={() => changeTab('picks')} />
  ) : null
  return (
    <div className={`covers-page${selected && inList ? ' has-selection' : ''}`}>
      <header className="library-header">
        <div className="library-title">
          <h1 data-route-focus tabIndex={-1}>
            Library
          </h1>
          <LibraryViewSwitch view="covers" />
        </div>
      </header>
      {picks ? (
        <CoverPicks taste={tasteRead} onEnd={endPicks} />
      ) : (
        <CoverTabs tab={tab} taste={taste} counts={queue.counts} onChange={changeTab} />
      )}
      {taste ? (
        <TasteProfileView onStart={() => changeTab('picks')} focusBuild={focusBuild} />
      ) : null}
      {offerPicks ? (
        <CoversWalkthrough startRef={startRef} onStart={() => changeTab('picks')} />
      ) : null}
      <p className="covers-notice" role="status">
        {notice}
      </p>
      {selected && !listed && inList && tab !== 'decide' && tab !== 'kept'
        ? closable(selected)
        : null}
      {inList && selectedId && !listed && linked.isError ? (
        <div className="covers-failure" role="alert">
          <p>{routeFailureCopy(linked.error).message}</p>
          <button type="button" onClick={() => void linked.refetch()}>
            Reload
          </button>
        </div>
      ) : null}
      <div className="covers-list" hidden={!inList}>
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
          <>
            {entry}
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
          </>
        ) : tab === 'switched' ? (
          <SwitchedList
            works={works}
            marked={marked}
            selectedId={selectedId}
            select={select}
            advance={queue.advance}
            replaced={queue.replaced}
          />
        ) : tab === 'all' ? (
          <CoverWall
            works={works}
            selectedId={selectedId}
            done={marked}
            select={select}
            panel={closable}
          />
        ) : (
          <CoverQueue
            ordered={ordered}
            works={works}
            selected={selected}
            marked={marked}
            select={(id) => {
              setHoldFocus(false)
              select(id)
            }}
            entry={entry}
            comparison={(work) => comparison(work, true)}
          />
        )}
      </div>
      {inList && pages.hasNextPage ? (
        <button
          className="covers-more"
          type="button"
          disabled={pages.isFetchingNextPage}
          onClick={queue.showMore}
        >
          Show more covers
        </button>
      ) : null}
    </div>
  )
}
