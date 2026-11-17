// Find this book (#1477): the header entry and its dialog. The owner searches
// one source, reads the cards, and links the work to the record that is its
// book. The dialog is a native modal (L3); Close, Escape and None of these
// record nothing. The link view it opens on a linked work is in `BookLink.tsx`.
import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { createPortal } from 'react-dom'

import type {
  LibraryWorkView,
  MediaKind,
  OwnerLinkScope,
  OwnerLinkView,
  OwnerSearchCard,
  OwnerSearchLane,
  OwnerSearchView,
  WorkAddress,
} from '../api/http'
import { Button } from '../components/Button'
import { Dropdown } from '../components/Dropdown'
import { SegmentedControl } from '../components/SegmentedControl'
import {
  asinValid,
  confirmQuestion,
  contradictionLines,
  disclosureLine,
  droppedLine,
  emptyLine,
  fieldRefusals,
  isbnInput,
  isbnValid,
  laneLabels,
  marketplaceDomain,
  mediumOf,
  notChoosableLine,
  ownerLinkKey,
  ownerText,
  ownerTextValid,
  refusalFor,
  resultCount,
  scopeButtons,
  sourceName,
  uncertainLinkCopy,
  withheldLines,
} from '../findThisBook'
import type { Refusal } from '../findThisBook'
import { queryWorkClass, useRequiredQuery as useQuery } from '../queryWork'
import { requestKey } from '../reviewDecisionController'
import { useReviewContext } from '../router'
import { LinkPill, LinkView } from './BookLink'
import { CardLines, Preview } from './FindThisBookCard'

export interface FindWork {
  title: string
  authors: readonly string[]
  mediaKind: MediaKind
}

/** What the header needs to show its entry, as the Library row carries it. */
export type FindEntryRow = Pick<LibraryWorkView, 'findOffered' | 'link'>

/**
 * The header's entry: Find this book while the work has no link and is
 * offered, the link's pill once it has one, nothing otherwise. The Library row
 * says which, so the entry is there with the header's first frame and nothing
 * moves when a read lands (#1477). The link view is read only to open the
 * dialog; with no row to go by, it is read to decide, and the entry's space is
 * held until it answers. After a choose or a
 * withdraw, the view read back stands until the row changes, and focus goes
 * to whichever replaced the other.
 */
export function FindThisBookEntry({
  address: target,
  addressFailed = false,
  readAddress,
  row,
  work,
  wrap,
}: {
  /** The catalogue address, once known; the dialog waits for it. */
  address: WorkAddress | undefined
  /** The address could not be read, so the dialog cannot open. */
  addressFailed?: boolean
  /** Reads the address again, when pressing the entry is trying again. */
  readAddress?: () => void
  row: FindEntryRow | undefined
  work: FindWork
  /** A work with no case has no Evidence or More, so the entry brings its own nav. */
  wrap?: (entry: ReactNode) => ReactNode
}) {
  const { http } = useReviewContext()
  // Only the three parts that address the link travel, whatever else the target names.
  const address = target && {
    connection: target.connection,
    library: target.library,
    item: target.item,
  }
  const [open, setOpen] = useState<'link' | 'form' | null>(null)
  const [said, setSaid] = useState('')
  // A failure to open, shown beside the entry until the owner moves on.
  const [failed, setFailed] = useState('')
  const [refocus, setRefocus] = useState(0)
  const [written, setWritten] = useState<{ row: string | null; view: OwnerLinkView } | null>(null)
  const view = useQuery({
    queryKey: ownerLinkKey(address ?? { connection: '', library: '', item: '' }),
    queryFn: (context) =>
      // `enabled` holds the read until the address is known.
      http.getOwnerLinkView(address as WorkAddress, context.signal, queryWorkClass(context)),
    enabled: address !== undefined && (open !== null || row === undefined),
    staleTime: 15_000,
    retry: false,
  })
  const entry = useRef<HTMLButtonElement>(null)
  useLayoutEffect(() => {
    if (refocus) entry.current?.focus()
  }, [refocus])
  const data = view.data
  const rowKey =
    row === undefined
      ? null
      : `${String(row.findOffered)}|${row.link?.state ?? ''}|${row.link?.provider ?? ''}`
  const shown =
    written !== null && written.row === rowKey
      ? { offered: written.view.offered, link: written.view.link }
      : row
        ? { offered: row.findOffered, link: row.link }
        : data
          ? { offered: data.offered, link: data.link }
          : null
  const close = (said = '') => {
    setOpen(null)
    setSaid(said)
    setRefocus((count) => count + 1)
  }
  // A view or an address that could not be read opens nothing, and says so
  // beside the entry, where the owner is, as well as to a screen reader.
  if (
    open !== null &&
    data === undefined &&
    ((view.isError && !view.isFetching) || (address === undefined && addressFailed))
  ) {
    close(unreadable)
    setFailed(unreadable)
  }
  useEffect(() => {
    if (!failed) return
    const dismiss = (event: Event) => {
      if (event instanceof KeyboardEvent && event.key !== 'Escape') return
      setFailed('')
    }
    document.addEventListener('pointerdown', dismiss)
    document.addEventListener('keydown', dismiss)
    return () => {
      document.removeEventListener('pointerdown', dismiss)
      document.removeEventListener('keydown', dismiss)
    }
  }, [failed])
  const press = (start: 'link' | 'form') => {
    setFailed('')
    if (address === undefined && addressFailed) readAddress?.()
    setOpen(start)
  }
  // While the dialog waits on a read, the entry says it is busy, in place.
  const busy = open !== null && !(data && address)
  const link = shown?.link ?? null
  const control = link ? (
    <LinkPill ref={entry} link={link} busy={busy} onOpen={() => press('link')} />
  ) : shown?.offered ? (
    <button
      ref={entry}
      type="button"
      className="work-action"
      aria-haspopup="dialog"
      aria-busy={busy || undefined}
      onClick={() => press('form')}
    >
      Find this book
    </button>
  ) : row === undefined && data === undefined && !view.isError ? (
    // With no row to go by, the entry's space is held while the view is read,
    // so the controls below can only stay or rise when it answers, never drop.
    // It is hidden, inert and disabled: never focused, pressed or announced.
    <button
      type="button"
      className="work-action work-action-reserved"
      aria-hidden="true"
      tabIndex={-1}
      disabled
      inert
    >
      Find this book
    </button>
  ) : null
  const status = (
    <span className="sr-only" role="status">
      {said}
    </span>
  )
  // Laid over what is below, so showing it moves nothing; the status above
  // announces it, so this copy is not read twice.
  const failure =
    failed && control !== null ? (
      <p className="work-action-failure" aria-hidden="true">
        {failed}
      </p>
    ) : null
  const entryParts = (
    <>
      {control}
      {failure}
    </>
  )
  return (
    <>
      {control === null ? null : wrap ? wrap(entryParts) : entryParts}
      {status}
      {open && data && address ? (
        <FindThisBookDialog
          address={address}
          work={work}
          view={data}
          start={open}
          refresh={async () => {
            const read = await view.refetch()
            if (read.data) setWritten({ row: rowKey, view: read.data })
          }}
          close={close}
        />
      ) : null}
    </>
  )
}

const unreadable = 'The book link could not be read. Try again.'

type Step =
  | { at: 'link' }
  | { at: 'form' }
  | { at: 'results'; search: OwnerSearchView }
  | { at: 'confirm'; search: OwnerSearchView; card: OwnerSearchCard }
interface Draft {
  provider: OwnerLinkView['sources'][number]['provider']
  marketplace: string | null
  lane: OwnerSearchLane
  title: string
  authors: string[]
  isbn: string
  asin: string
}
// The key a choose or withdraw is sent under, kept while the same attempt is
// pressed again so the server answers it once.
interface Attempt {
  what: string
  key: string
}

function FindThisBookDialog({
  address,
  work,
  view,
  start,
  refresh,
  close,
}: {
  address: WorkAddress
  work: FindWork
  view: OwnerLinkView
  start: 'link' | 'form'
  refresh: () => Promise<void>
  close: (said?: string) => void
}) {
  const { http } = useReviewContext()
  const dialog = useRef<HTMLDialogElement>(null)
  const heading = useRef<HTMLHeadingElement>(null)
  const medium = mediumOf(work.mediaKind)
  const first = view.sources[0]
  const [step, setStep] = useState<Step>({ at: start })
  const [draft, setDraft] = useState<Draft>(() => ({
    provider: first?.provider ?? 'hardcover',
    marketplace: first?.marketplaces?.[0] ?? null,
    lane: first?.lanes[0] ?? 'title_authors',
    title: view.prefill.title ?? '',
    authors: view.prefill.authors.length ? view.prefill.authors.slice(0, 4) : [''],
    isbn: view.prefill.isbn ?? '',
    asin: view.prefill.asin ?? '',
  }))
  const [searched, setSearched] = useState(false)
  const [busy, setBusy] = useState<'search' | 'choose' | 'withdraw' | null>(null)
  const [refusal, setRefusal] = useState<Refusal | null>(null)
  const attempt = useRef<Attempt | null>(null)
  useEffect(() => {
    const element = dialog.current
    if (!element || element.open) return
    element.showModal()
    heading.current?.focus()
    return () => element.close()
  }, [])
  // Each view starts at its heading, or at its first line for results and the confirmation.
  const body = useRef<HTMLDivElement>(null)
  const opened = useRef(false)
  useLayoutEffect(() => {
    if (!opened.current) {
      opened.current = true
      return
    }
    const target =
      step.at === 'results' || step.at === 'confirm'
        ? body.current?.querySelector<HTMLElement>('[data-step-start]')
        : heading.current
    target?.focus()
    if (body.current) body.current.scrollTop = 0
  }, [step])
  const end = (said?: string) => {
    dialog.current?.close()
    close(said)
  }
  const source = view.sources.find((each) => each.provider === draft.provider) ?? first
  const keyFor = (what: string) => {
    if (attempt.current?.what !== what) attempt.current = { what, key: requestKey() }
    return attempt.current.key
  }
  const search = async () => {
    const authors = draft.authors.map(ownerText).filter(Boolean)
    const local: Refusal | null =
      draft.lane === 'title_authors'
        ? !ownerTextValid(draft.title)
          ? { at: 'title', text: fieldRefusals.title }
          : authors.length === 0 ||
              !draft.authors.every((each) => !each.trim() || ownerTextValid(each))
            ? { at: 'author', text: fieldRefusals.author }
            : null
        : draft.lane === 'isbn'
          ? isbnValid(isbnInput(draft.isbn))
            ? null
            : { at: 'isbn', text: fieldRefusals.isbn }
          : asinValid(draft.asin.trim().toUpperCase())
            ? null
            : { at: 'asin', text: fieldRefusals.asin }
    setRefusal(local)
    if (local) return
    setBusy('search')
    try {
      const answer = await http.searchOwnerLink({
        connection: address.connection,
        library: address.library,
        item: address.item,
        provider: draft.provider,
        lane: draft.lane,
        ...(draft.lane === 'title_authors'
          ? { title: ownerText(draft.title), authors }
          : draft.lane === 'isbn'
            ? { isbn: isbnInput(draft.isbn) }
            : { asin: draft.asin.trim().toUpperCase() }),
        ...(draft.provider === 'audible_catalog' ? { marketplace: draft.marketplace } : {}),
      })
      setSearched(true)
      setStep({ at: 'results', search: answer })
    } catch (error) {
      setRefusal(refusalFor(error, draft.provider, 'search', new Date()))
    } finally {
      setBusy(null)
    }
  }
  const choose = async (search: OwnerSearchView, card: OwnerSearchCard, scope: OwnerLinkScope) => {
    setBusy('choose')
    setRefusal(null)
    try {
      await http.chooseOwnerLink(
        search.search_id,
        { position: card.position, scope },
        keyFor(`choose:${search.search_id}:${String(card.position)}:${scope}`),
      )
      attempt.current = null
      await refresh()
      end()
    } catch (error) {
      const refused = refusalFor(error, search.provider, 'choose', new Date())
      setRefusal(refused)
      // The write may have landed: the link view says whether it did.
      if (refused.text === uncertainLinkCopy) await refresh()
    } finally {
      setBusy(null)
    }
  }
  const withdraw = async () => {
    const link = view.link
    if (!link) return
    setBusy('withdraw')
    setRefusal(null)
    try {
      await http.withdrawOwnerLink(
        address,
        link.link_row_id,
        keyFor(`withdraw:${link.link_row_id}`),
      )
      attempt.current = null
      await refresh()
      end('Link withdrawn.')
    } catch (error) {
      const refused = refusalFor(error, link.provider, 'withdraw', new Date())
      setRefusal(refused)
      if (refused.text === uncertainLinkCopy) await refresh()
    } finally {
      setBusy(null)
    }
  }
  const said = (at: Refusal['at']) => (refusal?.at === at ? refusal.text : null)
  const headingText = step.at === 'link' ? 'Book link' : 'Find this book'
  return createPortal(
    <dialog
      ref={dialog}
      className="find-book-dialog"
      aria-labelledby="find-book-heading"
      onCancel={(event) => {
        event.preventDefault()
        if (busy !== 'choose' && busy !== 'withdraw') end()
      }}
    >
      <header className="find-book-head">
        <div>
          <h2 id="find-book-heading" ref={heading} tabIndex={-1}>
            {headingText}
          </h2>
          <p>
            {work.title}
            {work.authors.length ? ` · ${work.authors.join(', ')}` : ''}
          </p>
        </div>
        <Button type="button" onClick={() => end()}>
          Close
        </Button>
      </header>
      <div className="find-book-body" ref={body}>
        {step.at === 'link' && view.link ? (
          <LinkView
            link={view.link}
            medium={medium}
            canChange={view.offered}
            now={new Date()}
            onChange={() => {
              setRefusal(null)
              setStep({ at: 'form' })
            }}
            onWithdraw={() => void withdraw()}
            withdrawing={busy === 'withdraw'}
            refusal={said('withdraw')}
          />
        ) : step.at === 'confirm' ? (
          <Confirmation
            search={step.search}
            card={step.card}
            medium={medium}
            busy={busy === 'choose'}
            refusal={said('choose')}
            choose={(scope) => void choose(step.search, step.card, scope)}
            back={() => {
              setRefusal(null)
              setStep({ at: 'results', search: step.search })
            }}
          />
        ) : (
          <>
            <SearchForm
              view={view}
              draft={draft}
              setDraft={(change) => {
                setDraft((current) => ({ ...current, ...change }))
                if (refusal && refusal.at !== 'choose') setRefusal(null)
              }}
              source={source}
              searched={searched}
              busy={busy === 'search'}
              said={said}
              submit={() => void search()}
            />
            {step.at === 'results' ? (
              <Results
                search={step.search}
                refusal={said('choose')}
                open={(card) => {
                  setRefusal(null)
                  setStep({ at: 'confirm', search: step.search, card })
                }}
                none={() => end()}
              />
            ) : null}
          </>
        )}
      </div>
    </dialog>,
    document.body,
  )
}

function SearchForm({
  view,
  draft,
  setDraft,
  source,
  searched,
  busy,
  said,
  submit,
}: {
  view: OwnerLinkView
  draft: Draft
  setDraft: (change: Partial<Draft>) => void
  source: OwnerLinkView['sources'][number] | undefined
  searched: boolean
  busy: boolean
  said: (at: Refusal['at']) => string | null
  submit: () => void
}) {
  const lanes = source?.lanes ?? ['title_authors']
  const refusalLine = (at: Refusal['at'], id: string) => {
    const text = said(at)
    return text ? (
      <p id={id} className="find-book-refusal" role="alert">
        {text}
      </p>
    ) : null
  }
  const described = (at: Refusal['at'], id: string) =>
    said(at) ? { 'aria-describedby': id, 'aria-invalid': true } : {}
  return (
    <form
      className="find-book-form"
      aria-label="Search"
      noValidate
      onSubmit={(event) => {
        event.preventDefault()
        if (!busy) submit()
      }}
    >
      <div className="find-book-pickers">
        <div className="find-book-field">
          <span id="find-book-source">Source</span>
          <Dropdown
            ariaLabel="Source"
            value={draft.provider}
            options={view.sources.map((each) => ({
              value: each.provider,
              label: sourceName(each.provider),
            }))}
            describedBy={said('source') ? 'find-book-source-refusal' : undefined}
            onChange={(value) => {
              const next = view.sources.find((each) => each.provider === value)
              if (!next) return
              setDraft({
                provider: next.provider,
                marketplace: next.marketplaces?.[0] ?? null,
                lane: next.lanes.includes(draft.lane)
                  ? draft.lane
                  : (next.lanes[0] ?? 'title_authors'),
              })
            }}
          />
          {refusalLine('source', 'find-book-source-refusal')}
        </div>
        {source?.marketplaces ? (
          <div className="find-book-field">
            <span>Marketplace</span>
            <Dropdown
              ariaLabel="Marketplace"
              value={draft.marketplace ?? ''}
              options={source.marketplaces.map((code) => ({
                value: code,
                label: marketplaceDomain(code),
              }))}
              onChange={(value) => setDraft({ marketplace: value })}
            />
          </div>
        ) : null}
      </div>
      {lanes.length > 1 ? (
        <div className="find-book-field">
          <span>Search by</span>
          <SegmentedControl
            label="Search by"
            value={draft.lane}
            options={lanes.map((lane) => ({ value: lane, label: laneLabels[lane] }))}
            onChange={(lane) => setDraft({ lane })}
          />
        </div>
      ) : null}
      {draft.lane === 'title_authors' ? (
        <div className="find-book-query">
          <label className="find-book-field">
            <span>Title</span>
            <input
              type="text"
              value={draft.title}
              maxLength={512}
              onChange={(event) => setDraft({ title: event.target.value })}
              {...described('title', 'find-book-title-refusal')}
            />
            {refusalLine('title', 'find-book-title-refusal')}
          </label>
          <fieldset className="find-book-authors">
            <legend>Authors</legend>
            {draft.authors.map((author, index) => (
              <div key={index} className="find-book-author">
                <input
                  type="text"
                  aria-label={`Author ${String(index + 1)}`}
                  value={author}
                  maxLength={512}
                  onChange={(event) =>
                    setDraft({
                      authors: draft.authors.map((each, at) =>
                        at === index ? event.target.value : each,
                      ),
                    })
                  }
                  {...described('author', 'find-book-author-refusal')}
                />
                {draft.authors.length > 1 ? (
                  <Button
                    type="button"
                    onClick={() =>
                      setDraft({ authors: draft.authors.filter((_, at) => at !== index) })
                    }
                  >
                    {`Remove author ${String(index + 1)}`}
                  </Button>
                ) : null}
              </div>
            ))}
            {draft.authors.length < 4 ? (
              <Button
                type="button"
                className="find-book-add"
                onClick={() => setDraft({ authors: [...draft.authors, ''] })}
              >
                Add an author
              </Button>
            ) : null}
            {refusalLine('author', 'find-book-author-refusal')}
          </fieldset>
        </div>
      ) : draft.lane === 'isbn' ? (
        <label className="find-book-field find-book-identifier">
          <span>ISBN</span>
          <input
            type="text"
            value={draft.isbn}
            inputMode="numeric"
            placeholder="978…"
            maxLength={64}
            onChange={(event) => setDraft({ isbn: event.target.value })}
            {...described('isbn', 'find-book-isbn-refusal')}
          />
          {refusalLine('isbn', 'find-book-isbn-refusal')}
        </label>
      ) : (
        <label className="find-book-field find-book-identifier">
          <span>ASIN</span>
          <input
            type="text"
            value={draft.asin}
            placeholder="B0…"
            maxLength={64}
            onChange={(event) => setDraft({ asin: event.target.value })}
            {...described('asin', 'find-book-asin-refusal')}
          />
          {refusalLine('asin', 'find-book-asin-refusal')}
        </label>
      )}
      {searched ? null : (
        <p className="find-book-disclosure">
          {disclosureLine(draft.provider, draft.lane, draft.marketplace)}
        </p>
      )}
      <div className="find-book-actions">
        <Button variant="primary" type="submit" aria-disabled={busy || undefined}>
          {busy ? `Searching ${sourceName(draft.provider)}…` : 'Search'}
        </Button>
      </div>
      {refusalLine('search', 'find-book-search-refusal')}
    </form>
  )
}

function Results({
  search,
  refusal,
  open,
  none,
}: {
  search: OwnerSearchView
  refusal: string | null
  open: (card: OwnerSearchCard) => void
  none: () => void
}) {
  const empty = search.cards.length === 0
  return (
    <section className="find-book-results" aria-label="Results">
      <p className="find-book-count" role="status" tabIndex={-1} data-step-start="">
        {empty ? emptyLine(search) : resultCount(search.cards.length)}
      </p>
      {empty ? null : (
        <ol className="find-book-cards">
          {search.cards.map((card) => (
            <li key={card.position} className="find-book-card">
              <Preview search={search} card={card} />
              <div className="find-book-card-body">
                <CardLines card={card} />
                {card.choosable ? null : (
                  <p className="find-book-card-refused">{notChoosableLine(search.provider)}</p>
                )}
              </div>
              {card.choosable ? (
                <Button
                  type="button"
                  className="find-book-choose"
                  aria-label={`Choose ${card.title}`}
                  onClick={() => open(card)}
                >
                  Choose
                </Button>
              ) : null}
            </li>
          ))}
        </ol>
      )}
      {search.more || search.dropped ? (
        <div className="find-book-results-foot">
          {search.more ? <p>More results. Refine the search.</p> : null}
          {search.dropped ? <p>{droppedLine(search.dropped)}</p> : null}
        </div>
      ) : null}
      {refusal ? (
        <p className="find-book-refusal" role="alert">
          {refusal}
        </p>
      ) : null}
      <div className="find-book-actions">
        <Button type="button" onClick={none}>
          None of these
        </Button>
      </div>
    </section>
  )
}

function Confirmation({
  search,
  card,
  medium,
  busy,
  refusal,
  choose,
  back,
}: {
  search: OwnerSearchView
  card: OwnerSearchCard
  medium: 'ebook' | 'audiobook'
  busy: boolean
  refusal: string | null
  choose: (scope: OwnerLinkScope) => void
  back: () => void
}) {
  const contradictions = contradictionLines(card, medium)
  const withheld = withheldLines(card, search.provider)
  return (
    <section className="find-book-confirm" aria-label="Confirm the link">
      <div className="find-book-confirm-record">
        <Preview search={search} card={card} />
        <div className="find-book-card-body">
          <p className="find-book-question" tabIndex={-1} data-step-start="">
            {confirmQuestion(card, search.provider)}
          </p>
          <CardLines card={card} />
        </div>
      </div>
      {contradictions.length ? (
        <ul className="find-book-differs">
          {contradictions.map((line) => (
            <li key={line}>{line}</li>
          ))}
        </ul>
      ) : null}
      {withheld.map((line) => (
        <p key={line} className="find-book-withheld">
          {line}
        </p>
      ))}
      <div className="find-book-actions" role="group" aria-label="Link as">
        {card.scopes.map((scope) => (
          <Button
            key={scope}
            variant="primary"
            type="button"
            aria-disabled={busy || undefined}
            onClick={() => {
              if (!busy) choose(scope)
            }}
          >
            {scopeButtons[scope]}
          </Button>
        ))}
        <Button type="button" onClick={back}>
          Back to results
        </Button>
      </div>
      {refusal ? (
        <p className="find-book-refusal" role="alert">
          {refusal}
        </p>
      ) : null}
    </section>
  )
}
