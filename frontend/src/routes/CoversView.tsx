import { useInfiniteQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useNavigate } from '@tanstack/react-router'
import { useEffect, useRef, useState } from 'react'

import { ApiError, refusalCopy, uncertainWriteOutcome } from '../api/http'
import type { CoverTab, CoverWorkView, DecisionCommand } from '../api/http'
import { useCoverPrepareFailure } from '../coverChoices'
import { formatClock } from '../dates'
import type { ChoiceProgress, ReviewField } from '../fieldDecision'
import { mediaKindLabels } from '../labels'
import { invalidateForegroundQueries, useRequiredQuery as useQuery } from '../queryWork'
import { ownerRefusalCopy, requestKey } from '../reviewDecisionController'
import { routeFailureCopy } from '../routeFailure'
import { detailQuery, libraryRoute, useReviewContext } from '../router'
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
  all: null,
} satisfies Record<CoverTab, string | null>
const staleCopy = "This book's covers changed since the page loaded. Nothing was changed."
const lostCopy = 'No answer came back. Check whether the cover was changed before choosing again.'
const question = 'Which cover should this book use?'

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

function CoverImage({ cover, alt }: { cover: { url: string } | null; alt: string }) {
  const [failed, setFailed] = useState(false)
  return cover && !failed ? (
    <img
      src={cover.url}
      alt={alt}
      loading="lazy"
      decoding="async"
      onError={() => setFailed(true)}
    />
  ) : (
    <span className="covers-image-missing" role="img" aria-label={alt}>
      {cover ? 'Image unavailable' : 'No cover'}
    </span>
  )
}

function CoverComparison({
  work,
  onRecorded,
  onSkip,
  onBack,
}: {
  work: CoverWorkView
  onRecorded: (outcome: Outcome) => void
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
  useEffect(() => heading.current?.focus(), [work.review_case_id])
  const reload = () => {
    setFailure(null)
    setProgress(null)
    void invalidateForegroundQueries(queryClient, {
      queryKey: ['review-case', work.review_case_id],
    })
    void invalidateForegroundQueries(queryClient, { queryKey: ['covers'] })
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
        setProgress({ command, phase: 'failed' })
        setFailure(failureOf(error, field))
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
          <button type="button" onClick={reload}>
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
          <button type="button" onClick={reload}>
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
  done,
  open,
  onOpen,
  onRecorded,
}: {
  work: CoverWorkView
  done: Outcome | undefined
  open: boolean
  onOpen: () => void
  onRecorded: (outcome: Outcome) => void
}) {
  const { http, queryClient } = useReviewContext()
  const [failure, setFailure] = useState<Failure | null>(null)
  const [keeping, setKeeping] = useState(false)
  const switched = work.switched
  if (!switched) return null
  // Keep is the book's keep-current command; its case is read first so the
  // command carries the exact proposal and selection it answers.
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
        <CoverImage cover={switched.before} alt={`Before: ${work.title}`} />
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
        <CoverImage cover={work.cover} alt={`Now: ${work.title}`} />
        <figcaption>
          Now
          {work.cover ? <small>{size(work.cover)}</small> : null}
        </figcaption>
      </figure>
      <div className="covers-switched-text">
        <h3>
          <Link to="/library" search={bookSearch(work.review_case_id)}>
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
                void invalidateForegroundQueries(queryClient, { queryKey: ['covers'] })
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
              <button type="button" onClick={onOpen}>
                Change
              </button>
            ) : null}
          </>
        ) : work.choosable ? (
          <>
            <button type="button" disabled={keeping} onClick={() => void keep()}>
              Keep
            </button>
            <button type="button" aria-expanded={open} onClick={onOpen}>
              Others
            </button>
          </>
        ) : null}
      </div>
      {open ? (
        <div className="covers-row-comparison">
          <CoverComparison work={work} onRecorded={onRecorded} />
        </div>
      ) : null}
    </li>
  )
}

function QueueItem({
  work,
  selected,
  done,
  onSelect,
}: {
  work: CoverWorkView
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
        onClick={onSelect}
      >
        <CoverImage cover={work.cover} alt={work.title} />
        <span>
          <strong>{work.title}</strong>
          <small>{byline(work)}</small>
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

export default function CoversView() {
  const search = libraryRoute.useSearch()
  const navigate = useNavigate({ from: '/library' })
  const { http } = useReviewContext()
  const queryClient = useQueryClient()
  const tab: CoverTab = search.tab ?? 'decide'
  const pages = useInfiniteQuery({
    queryKey: ['covers', tab] as const,
    queryFn: ({ pageParam, signal }) => http.getCovers(tab, pageParam ?? undefined, signal),
    initialPageParam: null as string | null,
    getNextPageParam: (page) => page.next_cursor,
    staleTime: 15_000,
    retry: false,
  })
  const [notice, setNotice] = useState<string | null>(null)
  const [done, setDone] = useState<Readonly<Record<string, Outcome>>>({})
  const works = pages.data?.pages.flatMap((page) => page.items) ?? []
  const counts = pages.data?.pages[0]?.counts
  const selectedId = search.cover
  const selected = works.find((work) => work.review_case_id === selectedId)
  const select = (id: string | undefined) =>
    void navigate({
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
  // After a choice or a skip the next book opens, so one tap moves on.
  const advance = (work: CoverWorkView, outcome: Outcome) => {
    setNotice(outcomeCopy(outcome, work.title))
    if (outcome !== 'skipped') {
      setDone((current) => ({ ...current, [work.review_case_id]: outcome }))
      void queryClient.invalidateQueries({ queryKey: ['library'] })
    }
    if (tab === 'switched' || tab === 'all') {
      select(undefined)
      return
    }
    const index = ordered.indexOf(work)
    const next = ordered.slice(index + 1).find((item) => done[item.review_case_id] === undefined)
    select(next?.review_case_id)
  }
  const changeTab = (next: CoverTab) =>
    void navigate({ search: { lens: 'all', view: 'covers', tab: next } })
  return (
    <div className={`covers-page${selectedId ? ' has-selection' : ''}`}>
      <header className="library-header covers-header">
        <h1 data-route-focus tabIndex={-1}>
          Covers
        </h1>
        <LibraryViewSwitch view="covers" />
      </header>
      <nav className="covers-tabs" aria-label="Covers">
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
      {pages.isPending ? (
        <p role="status">Loading covers…</p>
      ) : pages.isError ? (
        <div className="covers-failure" role="alert">
          <p>{routeFailureCopy(pages.error).message}</p>
          <button type="button" onClick={() => void pages.refetch()}>
            Reload
          </button>
        </div>
      ) : works.length === 0 && emptyCopy[tab] ? (
        <p className="covers-empty">{emptyCopy[tab]}</p>
      ) : tab === 'switched' ? (
        <ul className="covers-switched">
          {works.map((work) => (
            <SwitchedRow
              key={work.review_case_id}
              work={work}
              done={done[work.review_case_id]}
              open={selectedId === work.review_case_id}
              onOpen={() =>
                select(selectedId === work.review_case_id ? undefined : work.review_case_id)
              }
              onRecorded={(outcome) => advance(work, outcome)}
            />
          ))}
        </ul>
      ) : tab === 'all' ? (
        <ul className="covers-wall">
          {works.map((work) => (
            <li
              key={work.review_case_id}
              className={selectedId === work.review_case_id ? 'is-open' : undefined}
            >
              <button
                type="button"
                aria-expanded={selectedId === work.review_case_id}
                onClick={() =>
                  select(selectedId === work.review_case_id ? undefined : work.review_case_id)
                }
              >
                <CoverImage cover={work.cover} alt={work.title} />
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
              </button>
              {selectedId === work.review_case_id ? (
                <div className="covers-row-comparison">
                  <CoverComparison work={work} onRecorded={(outcome) => advance(work, outcome)} />
                </div>
              ) : null}
            </li>
          ))}
        </ul>
      ) : (
        <div className="covers-queue">
          <div className="covers-queue-list">
            {groupOrder
              .filter((group) => ordered.some((work) => work.group === group))
              .map((group) => (
                <section key={group} aria-label={groupLabels[group]}>
                  <h2>{groupLabels[group]}</h2>
                  <ul>
                    {ordered
                      .filter((work) => work.group === group)
                      .map((work) => (
                        <QueueItem
                          key={work.review_case_id}
                          work={work}
                          selected={work === selected}
                          done={done[work.review_case_id] !== undefined}
                          onSelect={() => select(work.review_case_id)}
                        />
                      ))}
                  </ul>
                </section>
              ))}
          </div>
          {selected ? (
            <CoverComparison
              key={selected.review_case_id}
              work={selected}
              onRecorded={(outcome) => advance(selected, outcome)}
              onSkip={() => advance(selected, 'skipped')}
              onBack={() => select(undefined)}
            />
          ) : null}
        </div>
      )}
      {pages.hasNextPage ? (
        <button
          className="covers-more"
          type="button"
          disabled={pages.isFetchingNextPage}
          onClick={() => void pages.fetchNextPage()}
        >
          Show more covers
        </button>
      ) : null}
    </div>
  )
}
