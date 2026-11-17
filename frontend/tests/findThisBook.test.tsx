// Find this book (#1477): the header entry, the More list, the dialog's form,
// results and confirmation, each refusal's words, and what None of these and
// Close send (nothing).
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import type { LibraryWorkView, OwnerLinkView, OwnerSearchView, ReviewHttp } from '../src/api/http'
import {
  ApiError,
  AmbiguousMutationError,
  CsrfRenewedError,
  mapReportDetail,
} from '../src/api/http'
import {
  WORK_ACTIONS,
  asinValid,
  isbnInput,
  isbnValid,
  ownerTextValid,
  refusalFor,
  withheldLines,
} from '../src/findThisBook'
import { createHttp, detail, library, renderRoute } from './fixtures'
import { Preview } from '../src/routes/FindThisBookCard'
import { card, linked, withLinkView } from './findThisBookFixtures'
import { reportDetail } from './wireFixtures'

const offered: OwnerLinkView = {
  offered: true,
  sources: [
    { provider: 'hardcover', lanes: ['title_authors', 'isbn'], marketplaces: null },
    { provider: 'open_library', lanes: ['title_authors', 'isbn'], marketplaces: null },
  ],
  prefill: { title: 'A Book', authors: ['Ada Example'], isbn: null, asin: null },
  link: null,
  schema_version: 'owner-link-view-v2',
}
const audible: OwnerLinkView = {
  ...offered,
  sources: [
    { provider: 'audible_catalog', lanes: ['title_authors', 'asin'], marketplaces: ['us', 'uk'] },
  ],
}
const results = (overrides: Partial<OwnerSearchView> = {}): OwnerSearchView => ({
  search_id: 'search_secret-identity',
  provider: 'hardcover',
  lane: 'title_authors',
  marketplace: null,
  cards: [
    card(),
    card({
      position: 1,
      title: 'Dune Messiah',
      preview: true,
      scopes: ['work'],
      withheld: [{ scope: 'edition', reason: 'no_isbn' }],
      isbn: null,
    }),
    card({ position: 2, title: 'Dune (unlinkable)', choosable: false, scopes: [], isbn: null }),
  ],
  more: true,
  dropped: 2,
  schema_version: 'owner-search-v1',
  ...overrides,
})
const openCase = async (http: ReviewHttp) => {
  renderRoute('/library?lens=all&preview=case-1', http)
  return screen.findByRole('navigation', { name: 'Work actions' })
}
const openDialog = async (http: ReviewHttp) => {
  const user = userEvent.setup()
  const actions = await openCase(http)
  await user.click(await within(actions).findByRole('button', { name: 'Find this book' }))
  const dialog = await screen.findByRole('dialog', { name: 'Find this book' })
  return { user, dialog, actions }
}
const withView = withLinkView

describe('the work header', () => {
  it('reads Evidence, Find this book and More in the one order, with History in More', async () => {
    expect(WORK_ACTIONS).toEqual(['evidence', 'find', 'more'])
    const user = userEvent.setup()
    const actions = await openCase(withView(offered))
    await within(actions).findByRole('button', { name: 'Find this book' })
    const names = Array.from(actions.querySelectorAll('a, button'))
      .filter((element) => !element.closest('[hidden]'))
      .map((element) => element.textContent)
    expect(names).toEqual(['Evidence', 'Find this book', 'More'])
    expect(within(actions).queryByRole('link', { name: 'History' })).toBeNull()
    const more = within(actions).getByRole('button', { name: 'More for this work' })
    expect(more).toHaveAttribute('aria-expanded', 'false')
    await user.click(more)
    expect(more).toHaveAttribute('aria-expanded', 'true')
    expect(within(actions).getByRole('link', { name: 'History' })).toBeVisible()
  })

  it('closes More on Escape and on a press outside, and gives focus back to More', async () => {
    const user = userEvent.setup()
    const actions = await openCase(withView(offered))
    const more = within(actions).getByRole('button', { name: 'More for this work' })
    more.focus()
    await user.keyboard('{Enter}')
    expect(within(actions).getByRole('link', { name: 'History' })).toBeVisible()
    await user.keyboard('{Escape}')
    expect(within(actions).queryByRole('link', { name: 'History' })).toBeNull()
    expect(more).toHaveFocus()
    await user.click(more)
    fireEvent.pointerDown(document.body)
    expect(more).toHaveAttribute('aria-expanded', 'false')
    expect(within(actions).queryByRole('link', { name: 'History' })).toBeNull()
    expect(more).toHaveFocus()
  })

  it('offers nothing on a work Find this book is not offered for, and keeps Evidence and More', async () => {
    const actions = await openCase(createHttp())
    await waitFor(() => expect(actions.children).toHaveLength(3))
    expect(within(actions).queryByRole('button', { name: 'Find this book' })).toBeNull()
    expect(within(actions).getByRole('link', { name: 'Evidence' })).toBeVisible()
    expect(within(actions).getByRole('button', { name: 'More for this work' })).toBeVisible()
  })

  it.each([
    ['Find this book', offered],
    ['Linked on Hardcover', { ...offered, link: linked() }],
  ])(
    'shows %s with the header’s first frame and reads the link view only when it is opened',
    async (name, view) => {
      // A link view that never answers: the entry must come from the row alone.
      const read = vi.fn<ReviewHttp['getOwnerLinkView']>(() => new Promise(() => undefined))
      const http = withView(view, { getOwnerLinkView: read })
      const user = userEvent.setup()
      const actions = await openCase(http)
      // In the same frame as the nav itself, with no read behind it.
      const entry = within(actions).getByRole('button', { name })
      expect(
        Array.from(actions.querySelectorAll(':scope > a, :scope > button, :scope > div')),
      ).toHaveLength(3)
      expect(read).not.toHaveBeenCalled()
      await user.click(entry)
      await waitFor(() => expect(read).toHaveBeenCalledTimes(1))
      // While the read is out, the entry says so in place.
      expect(entry).toHaveAttribute('aria-busy', 'true')
    },
  )

  it.each([
    ['Find this book', offered, 'Find this book'],
    ['the pill', { ...offered, link: linked() }, 'Linked on Hardcover'],
    ['nothing', { ...offered, offered: false }, null],
  ])(
    'holds the entry’s space, unseen and unreachable, while a case with no Library row on the page reads its link view, then shows %s',
    async (_, view, name) => {
      // The selected case is not on the current Library page, so no row says
      // which entry it has: only the link view can, and it answers late.
      let answer: (value: OwnerLinkView) => void = () => undefined
      const read = vi.fn<ReviewHttp['getOwnerLinkView']>(
        () => new Promise((resolve) => (answer = resolve)),
      )
      const http = withView(view, {
        getOwnerLinkView: read,
        getLibrary: vi.fn(async () => ({
          ...library,
          works: library.works.filter((work) => work.reviewCaseId !== 'case-1'),
        })),
      })
      const actions = await openCase(http)
      await waitFor(() => expect(read).toHaveBeenCalledTimes(1))
      const slots = () =>
        Array.from(actions.querySelectorAll(':scope > a, :scope > button, :scope > div'))
      // Three slots while the view is read: Evidence, the held space, More.
      expect(slots()).toHaveLength(3)
      const held = slots()[1] as HTMLButtonElement
      expect(held).toHaveClass('work-action-reserved')
      expect(held).toHaveAttribute('aria-hidden', 'true')
      expect(held).toHaveAttribute('inert')
      expect(held).toBeDisabled()
      expect(held.tabIndex).toBe(-1)
      expect(within(actions).queryByRole('button', { name: 'Find this book' })).toBeNull()
      expect(within(actions).getAllByRole('button')).toHaveLength(1)
      await act(async () => answer(view))
      if (name === null) {
        await waitFor(() => expect(slots()).toHaveLength(2))
        expect(actions.querySelector('.work-action-reserved')).toBeNull()
      } else {
        const entry = await within(actions).findByRole('button', { name })
        expect(slots()).toHaveLength(3)
        expect(slots()[1]).toBe(entry)
        expect(actions.querySelector('.work-action-reserved')).toBeNull()
      }
    },
  )

  it.each([
    ['Find this book', offered],
    ['Linked on Hardcover', { ...offered, link: linked() }],
  ])(
    'says beside %s, not only to a screen reader, that its link view could not be read',
    async (name, view) => {
      const user = userEvent.setup()
      const read = vi.fn<ReviewHttp['getOwnerLinkView']>(async () => {
        throw new ApiError(503, 'unavailable')
      })
      const actions = await openCase(withView(view, { getOwnerLinkView: read }))
      const entry = within(actions).getByRole('button', { name })
      // A read that fails opens nothing and says so where the owner looks.
      await user.click(entry)
      const line = await waitFor(() => {
        const found = actions.querySelector('.work-action-failure')
        if (!found) throw new Error('no failure line')
        return found
      })
      expect(line).toHaveTextContent('The book link could not be read. Try again.')
      expect(line).toBeVisible()
      expect(line).toHaveAttribute('aria-hidden', 'true')
      expect(screen.getAllByRole('status').map((node) => node.textContent)).toContain(
        'The book link could not be read. Try again.',
      )
      expect(screen.queryByRole('dialog')).toBeNull()
      expect(entry).toHaveFocus()
      await user.keyboard('{Escape}')
      expect(actions.querySelector('.work-action-failure')).toBeNull()
    },
  )

  it('says beside Find this book when a work with no case could not read its address, and tries again on the next press', async () => {
    const user = userEvent.setup()
    const first = library.works[0]
    if (!first) throw new Error('library fixture requires one work')
    const work: LibraryWorkView = {
      ...first,
      id: 'library_work_9',
      workId: 'library_work_9',
      detailKind: 'report',
      detailUrl: '/api/v1/library/report?item=item-9',
      attention: 'not_matched',
      attentionCount: 0,
      findOffered: true,
    }
    delete (work as { reviewCaseId?: string }).reviewCaseId
    const report = vi
      .fn<ReviewHttp['getReportDetail']>()
      .mockRejectedValueOnce(new ApiError(503, 'unavailable'))
      .mockResolvedValue(
        mapReportDetail(reportDetail({ schema_version: 'review-report-detail-v7' }) as never),
      )
    const http = withView(offered, {
      getLibrary: vi.fn(async () => ({ ...library, works: [work] })),
      getReportDetail: report,
    })
    renderRoute('/library?lens=all', http)
    await user.click(await screen.findByRole('button', { name: /^A Book\./u }))
    const actions = await screen.findByRole('navigation', { name: 'Work actions' })
    await waitFor(() => expect(report).toHaveBeenCalled())
    const find = await within(actions).findByRole('button', { name: 'Find this book' })
    await user.click(find)
    const line = await waitFor(() => {
      const found = actions.querySelector('.work-action-failure')
      if (!found) throw new Error('no failure line')
      return found
    })
    expect(line).toHaveTextContent('The book link could not be read. Try again.')
    expect(line).toBeVisible()
    expect(screen.getAllByRole('status').map((node) => node.textContent)).toContain(
      'The book link could not be read. Try again.',
    )
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(http.getOwnerLinkView).not.toHaveBeenCalled()
    // Pressing again is trying again: the address is read, then the dialog opens.
    await user.click(find)
    expect(actions.querySelector('.work-action-failure')).toBeNull()
    expect(await screen.findByRole('dialog', { name: 'Find this book' })).toBeVisible()
    expect(report.mock.calls.length).toBeGreaterThanOrEqual(2)
  })

  it('gives a work with no case one Work actions nav holding only Find this book', async () => {
    const user = userEvent.setup()
    const first = library.works[0]
    if (!first) throw new Error('library fixture requires one work')
    const work: LibraryWorkView = {
      ...first,
      id: 'library_work_9',
      workId: 'library_work_9',
      detailKind: 'report',
      detailUrl: '/api/v1/library/report?item=item-9',
      attention: 'not_matched',
      attentionCount: 0,
      findOffered: true,
    }
    delete (work as { reviewCaseId?: string }).reviewCaseId
    const http = withView(offered, {
      getLibrary: vi.fn(async () => ({ ...library, works: [work] })),
      getReportDetail: vi.fn(async () =>
        mapReportDetail(reportDetail({ schema_version: 'review-report-detail-v7' }) as never),
      ),
    })
    renderRoute('/library?lens=all', http)
    await user.click(await screen.findByRole('button', { name: /^A Book\./u }))
    const actions = await screen.findByRole('navigation', { name: 'Work actions' })
    const find = await within(actions).findByRole('button', { name: 'Find this book' })
    expect(find).toBeVisible()
    expect(within(actions).queryByRole('button', { name: 'More for this work' })).toBeNull()
    expect(screen.queryByRole('link', { name: 'History' })).toBeNull()
    // The row says the entry is offered; the link view is read only to open it.
    expect(http.getOwnerLinkView).not.toHaveBeenCalled()
    await user.click(find)
    expect(await screen.findByRole('dialog', { name: 'Find this book' })).toBeVisible()
    expect(http.getOwnerLinkView).toHaveBeenCalledWith(
      { connection: 'primary', library: 'audio', item: 'item-9' },
      expect.any(AbortSignal),
      'foreground',
    )
  })
})

describe('the dialog', () => {
  it('opens on the prefilled form with its disclosure line, and Close sends nothing', async () => {
    const http = withView(offered)
    const { user, dialog, actions } = await openDialog(http)
    expect(dialog).toHaveAttribute('open')
    expect(within(dialog).getByRole('heading', { level: 2, name: 'Find this book' })).toHaveFocus()
    expect(within(dialog).getByRole('textbox', { name: 'Title' })).toHaveValue('A Book')
    expect(within(dialog).getByRole('textbox', { name: 'Author 1' })).toHaveValue('Ada Example')
    expect(within(dialog).getByRole('combobox', { name: 'Source' })).toHaveTextContent('Hardcover')
    expect(
      within(dialog).getByText('Search sends the title and authors to Hardcover.'),
    ).toBeVisible()
    await user.click(within(dialog).getByRole('button', { name: 'ISBN' }))
    expect(within(dialog).getByText('Search sends the ISBN to Hardcover.')).toBeVisible()
    expect(within(dialog).getByRole('textbox', { name: 'ISBN' })).toHaveAttribute(
      'placeholder',
      '978…',
    )
    await user.click(within(dialog).getByRole('button', { name: 'Close' }))
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(within(actions).getByRole('button', { name: 'Find this book' })).toHaveFocus()
    expect(http.searchOwnerLink).not.toHaveBeenCalled()
  })

  it('adds and removes authors, up to four', async () => {
    const { user, dialog } = await openDialog(withView(offered))
    const add = () => within(dialog).getByRole('button', { name: 'Add an author' })
    await user.click(add())
    await user.click(add())
    await user.click(add())
    expect(within(dialog).getByRole('textbox', { name: 'Author 4' })).toBeVisible()
    expect(within(dialog).queryByRole('button', { name: 'Add an author' })).toBeNull()
    await user.click(within(dialog).getByRole('button', { name: 'Remove author 2' }))
    expect(within(dialog).queryByRole('textbox', { name: 'Author 4' })).toBeNull()
  })

  it('offers Marketplace and ASIN for Audible only', async () => {
    const { user, dialog } = await openDialog(withView(audible))
    expect(within(dialog).getByRole('combobox', { name: 'Marketplace' })).toHaveTextContent(
      'audible.com',
    )
    await user.click(within(dialog).getByRole('button', { name: 'ASIN' }))
    expect(within(dialog).getByText('Search sends the ASIN to audible.com.')).toBeVisible()
    expect(within(dialog).getByRole('textbox', { name: 'ASIN' })).toHaveAttribute(
      'placeholder',
      'B0…',
    )
  })

  it('refuses a field beside it, keeps what was typed, and sends nothing', async () => {
    const http = withView(offered)
    const { user, dialog } = await openDialog(http)
    const title = within(dialog).getByRole('textbox', { name: 'Title' })
    await user.clear(title)
    await user.type(title, 'see https://example.com')
    await user.click(within(dialog).getByRole('button', { name: 'Search' }))
    expect(within(dialog).getByRole('alert')).toHaveTextContent(
      'Enter a title of up to 512 characters, with no web address or file path.',
    )
    expect(title).toHaveValue('see https://example.com')
    expect(title).toHaveAttribute('aria-invalid', 'true')
    await user.click(within(dialog).getByRole('button', { name: 'ISBN' }))
    await user.type(within(dialog).getByRole('textbox', { name: 'ISBN' }), '9780441172718')
    await user.click(within(dialog).getByRole('button', { name: 'Search' }))
    expect(within(dialog).getByRole('alert')).toHaveTextContent('Enter an ISBN of 10 or 13 digits.')
    expect(http.searchOwnerLink).not.toHaveBeenCalled()
  })

  it('shows results as text, with counts, More results, the dropped line and None of these', async () => {
    const search = vi.fn<ReviewHttp['searchOwnerLink']>(async () => results())
    const http = withView(offered, { searchOwnerLink: search })
    const { user, dialog, actions } = await openDialog(http)
    await user.click(within(dialog).getByRole('button', { name: 'Search' }))
    expect(search).toHaveBeenCalledWith({
      connection: 'local',
      library: 'Books',
      item: 'internal-item-1',
      provider: 'hardcover',
      lane: 'title_authors',
      title: 'A Book',
      authors: ['Ada Example'],
    })
    const count = await within(dialog).findByText('3 results')
    expect(count).toHaveFocus()
    expect(within(dialog).getByText('More results. Refine the search.')).toBeVisible()
    expect(within(dialog).getByText('2 results could not be shown.')).toBeVisible()
    expect(within(dialog).getAllByText('By Frank Herbert')).toHaveLength(3)
    expect(within(dialog).getAllByText('ISBN 9780441172719')).toHaveLength(1)
    expect(within(dialog).getAllByText('1965 · Chilton')).toHaveLength(3)
    expect(within(dialog).getByRole('button', { name: 'Choose Dune' })).toBeVisible()
    expect(within(dialog).queryByRole('button', { name: 'Choose Dune (unlinkable)' })).toBeNull()
    expect(
      within(dialog).getByText(
        'Hardcover lists no ISBN or ID for this record, so it cannot be linked.',
      ),
    ).toBeVisible()
    // The disclosure line is said before the first search only.
    expect(within(dialog).queryByText(/^Search sends/u)).toBeNull()
    // Nothing a source sent becomes a link, and no address reaches the page.
    expect(dialog.querySelector('a')).toBeNull()
    const image = dialog.querySelector('img')
    if (!image) throw new Error('the first card shows its preview')
    expect(image).toHaveAttribute(
      'src',
      '/api/v1/library/find/searches/search_secret-identity/previews/1',
    )
    expect(dialog.querySelectorAll('.is-spine')).toHaveLength(2)
    fireEvent.error(image)
    expect(dialog.querySelectorAll('.is-spine')).toHaveLength(3)
    expect(document.body.textContent).not.toContain('internal-item-1')
    expect(document.body.textContent).not.toContain('search_secret-identity')
    await user.click(within(dialog).getByRole('button', { name: 'None of these' }))
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(within(actions).getByRole('button', { name: 'Find this book' })).toHaveFocus()
    expect(search).toHaveBeenCalledTimes(1)
    expect(http.chooseOwnerLink).not.toHaveBeenCalled()
  })

  it("tries a new search's preview after the last search's preview at that position failed", () => {
    const shown = card({ position: 1, title: 'Dune Messiah', preview: true })
    const first = results()
    const { container, rerender } = render(<Preview search={first} card={shown} />)
    const image = container.querySelector('img')
    if (!image) throw new Error('the card shows its preview')
    fireEvent.error(image)
    expect(container.querySelector('img')).toBeNull()
    expect(container.querySelector('.is-spine')).not.toBeNull()
    rerender(<Preview search={results({ search_id: 'search_next' })} card={shown} />)
    expect(container.querySelector('img')).toHaveAttribute(
      'src',
      '/api/v1/library/find/searches/search_next/previews/1',
    )
  })

  it('says what an empty search found for each way of searching', async () => {
    const search = vi.fn<ReviewHttp['searchOwnerLink']>(async (request) =>
      results({ cards: [], more: false, dropped: 0, lane: request.lane }),
    )
    const { user, dialog } = await openDialog(withView(offered, { searchOwnerLink: search }))
    await user.click(within(dialog).getByRole('button', { name: 'Search' }))
    expect(
      await within(dialog).findByText(
        'Hardcover found nothing for this title and these authors. Change them and search again.',
      ),
    ).toBeVisible()
    await user.click(within(dialog).getByRole('button', { name: 'ISBN' }))
    await user.type(within(dialog).getByRole('textbox', { name: 'ISBN' }), '9780441172719')
    await user.click(within(dialog).getByRole('button', { name: 'Search' }))
    expect(
      await within(dialog).findByText(
        'Hardcover found nothing for this ISBN. Check it, or search by title and authors.',
      ),
    ).toBeVisible()
  })

  it.each([
    ['978-0-441-17271-9', '9780441172719'],
    [' 978 0441 172719 ', '9780441172719'],
    ['0-8044-2957-x', '080442957X'],
  ])('takes the ISBN %s the way books print it and sends %s', async (typed, sent) => {
    const search = vi.fn<ReviewHttp['searchOwnerLink']>(async () => results({ cards: [] }))
    const { user, dialog } = await openDialog(withView(offered, { searchOwnerLink: search }))
    await user.click(within(dialog).getByRole('button', { name: 'ISBN' }))
    const field = within(dialog).getByRole('textbox', { name: 'ISBN' })
    await user.clear(field)
    await user.type(field, typed)
    await user.click(within(dialog).getByRole('button', { name: 'Search' }))
    await waitFor(() => expect(search).toHaveBeenCalledTimes(1))
    expect(search.mock.calls[0]?.[0]).toMatchObject({ lane: 'isbn', isbn: sent })
    expect(within(dialog).queryByText('Enter an ISBN of 10 or 13 digits.')).toBeNull()
  })

  it('puts a busy source beside Search and keeps the query', async () => {
    const search = vi.fn<ReviewHttp['searchOwnerLink']>(async () => {
      throw new ApiError(503, 'provider_busy')
    })
    const { user, dialog } = await openDialog(withView(offered, { searchOwnerLink: search }))
    await user.click(within(dialog).getByRole('button', { name: 'Search' }))
    expect(await within(dialog).findByRole('alert')).toHaveTextContent(
      'Hardcover is busy with other lookups. Search again in a minute.',
    )
    expect(within(dialog).getByRole('textbox', { name: 'Title' })).toHaveValue('A Book')
  })

  it.each([
    [
      new ApiError(404, 'work_not_found'),
      'This work is no longer in Audiobookshelf. Close this and choose another work.',
    ],
    [
      new ApiError(409, 'find_not_offered'),
      'No source can search for this work now. Check its library’s sources in Settings.',
    ],
  ])('puts %s beside Search and keeps the query', async (error, text) => {
    const search = vi.fn<ReviewHttp['searchOwnerLink']>(async () => {
      throw error
    })
    const { user, dialog } = await openDialog(withView(offered, { searchOwnerLink: search }))
    const title = within(dialog).getByRole('textbox', { name: 'Title' })
    await user.clear(title)
    await user.type(title, 'Dune')
    await user.click(within(dialog).getByRole('button', { name: 'Search' }))
    const alert = await within(dialog).findByRole('alert')
    expect(alert).toHaveTextContent(text)
    expect(alert).toHaveAttribute('id', 'find-book-search-refusal')
    expect(within(dialog).getByRole('textbox', { name: 'Title' })).toHaveValue('Dune')
  })

  it('reads an idempotency conflict beside the scope buttons as an uncertain write', async () => {
    const http = withView(offered, {
      searchOwnerLink: vi.fn(async () => results()),
      chooseOwnerLink: vi.fn(async () => {
        throw new ApiError(409, 'idempotency_conflict')
      }),
    })
    const { user, dialog } = await openDialog(http)
    await user.click(within(dialog).getByRole('button', { name: 'Search' }))
    await user.click(await within(dialog).findByRole('button', { name: 'Choose Dune' }))
    await user.click(within(dialog).getByRole('button', { name: 'This book' }))
    expect(await within(dialog).findByRole('alert')).toHaveTextContent(
      'No answer came back. Check whether the work is linked before choosing again.',
    )
    // The confirmation stays open on the same card, so nothing is lost.
    expect(
      within(dialog).getByText('Link this work to Dune by Frank Herbert from Hardcover?'),
    ).toBeVisible()
    expect(within(dialog).getByRole('button', { name: 'This book' })).toBeVisible()
  })

  it('confirms with contradiction and withheld lines, and links under one key per attempt', async () => {
    let view: OwnerLinkView = offered
    const contradicted = card({
      language: 'de',
      scopes: ['work'],
      withheld: [{ scope: 'edition', reason: 'format_print' }],
      contradictions: [
        {
          code: 'identifier',
          identifier: 'isbn',
          language: null,
          record: '9780441172719',
          work: '9780000000002',
        },
        { code: 'creators_disjoint', identifier: null, language: null, record: null, work: null },
      ],
    })
    const choose = vi
      .fn<ReviewHttp['chooseOwnerLink']>()
      .mockRejectedValueOnce(new CsrfRenewedError())
      .mockImplementationOnce(async () => {
        view = { ...offered, link: linked({ state: 'preparing' }) }
        return {
          action: 'link',
          link_row_id: `owner_link_${'a'.repeat(64)}`,
          scan_requested: true,
          schema_version: 'owner-link-change-v1',
        }
      })
    const http = withView(offered, {
      getOwnerLinkView: vi.fn(async () => view),
      searchOwnerLink: vi.fn(async () =>
        results({ cards: [contradicted], more: false, dropped: 0 }),
      ),
      chooseOwnerLink: choose,
    })
    const { user, dialog, actions } = await openDialog(http)
    await user.click(within(dialog).getByRole('button', { name: 'Search' }))
    await user.click(await within(dialog).findByRole('button', { name: 'Choose Dune' }))
    const question = within(dialog).getByText(
      'Link this work to Dune by Frank Herbert from Hardcover?',
    )
    expect(question).toHaveFocus()
    expect(
      within(dialog).getByText(
        'The record lists ISBN 9780441172719. This work has ISBN 9780000000002.',
      ),
    ).toBeVisible()
    expect(within(dialog).getByText('The record names none of this work’s authors.')).toBeVisible()
    expect(
      within(dialog).getByText(
        'This edition is not offered because Hardcover lists it as a print edition.',
      ),
    ).toBeVisible()
    expect(within(dialog).queryByRole('button', { name: 'This edition' })).toBeNull()
    await user.click(within(dialog).getByRole('button', { name: 'This book' }))
    expect(await within(dialog).findByRole('alert')).toBeVisible()
    await user.click(within(dialog).getByRole('button', { name: 'This book' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    expect(choose).toHaveBeenCalledTimes(2)
    const [first, second] = choose.mock.calls
    expect(first?.[0]).toBe('search_secret-identity')
    expect(first?.[1]).toEqual({ position: 0, scope: 'work' })
    expect(first?.[2]).toBe(second?.[2])
    const pill = within(actions).getByRole('button', { name: 'Linked, preparing' })
    expect(pill).toHaveAttribute('aria-haspopup', 'dialog')
    expect(pill).toHaveFocus()
    expect(within(actions).queryByRole('button', { name: 'Find this book' })).toBeNull()
  })

  it('reads a lost answer as uncertain and reads the link again', async () => {
    const view = vi.fn(async () => offered)
    const http = withView(offered, {
      getOwnerLinkView: view,
      searchOwnerLink: vi.fn(async () => results()),
      chooseOwnerLink: vi.fn(async () => {
        throw new ApiError(503, 'unavailable')
      }),
    })
    const { user, dialog } = await openDialog(http)
    await user.click(within(dialog).getByRole('button', { name: 'Search' }))
    await user.click(await within(dialog).findByRole('button', { name: 'Choose Dune' }))
    const before = view.mock.calls.length
    await user.click(within(dialog).getByRole('button', { name: 'This edition' }))
    expect(await within(dialog).findByRole('alert')).toHaveTextContent(
      'No answer came back. Check whether the work is linked before choosing again.',
    )
    expect(view.mock.calls.length).toBeGreaterThan(before)
    expect(http.chooseOwnerLink).toHaveBeenCalledWith(
      'search_secret-identity',
      { position: 0, scope: 'edition' },
      expect.stringMatching(/^.{16,128}$/u),
    )
  })

  it('keeps a link it wrote to its own work, so the next work selected offers Find this book', async () => {
    // Two unlinked works whose rows read alike; preparation stays paused, so
    // neither row changes after the first is linked (#1477).
    const linkedItems = new Set<string>()
    const [first] = library.works
    if (first === undefined) throw new Error('the library fixture has a work')
    const second: LibraryWorkView = {
      ...first,
      id: 'case-2',
      reviewCaseId: 'case-2',
      workId: 'library_work_2',
      title: 'Another Book',
      detailUrl: '/review/case-2',
    }
    const http = withView(offered, {
      getOwnerLinkView: vi.fn<ReviewHttp['getOwnerLinkView']>(async (address) =>
        linkedItems.has(address.item)
          ? { ...offered, link: linked({ state: 'preparing', paused: true }) }
          : offered,
      ),
      getLibrary: vi.fn(async () => ({
        ...library,
        works: [first, second].map((work) => ({
          ...work,
          findOffered: true,
          link: null,
        })),
      })),
      getCaseDetail: vi.fn<ReviewHttp['getCaseDetail']>(async (id) =>
        id === 'case-2'
          ? {
              ...detail,
              id: 'case-2',
              title: 'Another Book',
              target: { ...detail.target, item: 'internal-item-2', title: 'Another Book' },
            }
          : detail,
      ),
      searchOwnerLink: vi.fn(async () => results({ cards: [card()], more: false, dropped: 0 })),
      chooseOwnerLink: vi.fn(async () => {
        linkedItems.add('internal-item-1')
        return {
          action: 'link' as const,
          link_row_id: `owner_link_${'a'.repeat(64)}`,
          scan_requested: true,
          schema_version: 'owner-link-change-v1' as const,
        }
      }),
    })
    const { user, dialog, actions } = await openDialog(http)
    await user.click(within(dialog).getByRole('button', { name: 'Search' }))
    await user.click(await within(dialog).findByRole('button', { name: 'Choose Dune' }))
    await user.click(within(dialog).getByRole('button', { name: 'This book' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    expect(within(actions).getByRole('button', { name: 'Linked, preparing' })).toBeVisible()

    // With the next work's detail already read, the header stays in place
    // across the switch, as it does when the owner moves along the list.
    const row = screen.getByRole('button', { name: /^Another Book\./u })
    await user.hover(row)
    await waitFor(() =>
      expect(http.getCaseDetail).toHaveBeenCalledWith(
        'case-2',
        expect.any(AbortSignal),
        'background',
      ),
    )
    await user.click(row)
    await screen.findByRole('heading', { name: 'Another Book' })
    const next = await screen.findByRole('navigation', { name: 'Work actions' })
    expect(await within(next).findByRole('button', { name: 'Find this book' })).toBeVisible()
    expect(within(next).queryByRole('button', { name: 'Linked, preparing' })).toBeNull()
  })

  it('closes on Escape without recording anything', async () => {
    const http = withView(offered)
    const { actions } = await openDialog(http)
    await act(async () => {
      fireEvent.keyDown(document.activeElement ?? document.body, { key: 'Escape' })
    })
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(within(actions).getByRole('button', { name: 'Find this book' })).toHaveFocus()
    expect(http.searchOwnerLink).not.toHaveBeenCalled()
  })
})

describe('refusal words', () => {
  const now = new Date('2026-11-16T10:00:00Z')
  const at = Math.floor(now.getTime() / 1000) + 30 * 60
  it.each([
    [
      new ApiError(422, 'title_invalid'),
      'search',
      'title',
      'Enter a title of up to 512 characters, with no web address or file path.',
    ],
    [
      new ApiError(422, 'author_invalid'),
      'search',
      'author',
      'Enter the author’s name, up to 512 characters, with no web address or file path.',
    ],
    [new ApiError(422, 'isbn_invalid'), 'search', 'isbn', 'Enter an ISBN of 10 or 13 digits.'],
    [
      new ApiError(422, 'asin_invalid'),
      'search',
      'asin',
      'Enter an ASIN of 10 letters and digits.',
    ],
    [
      new ApiError(422, 'provider_not_offered'),
      'search',
      'source',
      'Hardcover is no longer searched for this library. Choose another source.',
    ],
    [
      new ApiError(409, 'search_in_flight'),
      'search',
      'search',
      'Another search is still running. Search again when it finishes.',
    ],
    [
      new ApiError(503, 'provider_busy'),
      'search',
      'search',
      'Hardcover is busy with other lookups. Search again in a minute.',
    ],
    [
      new ApiError(429, 'provider_deferred', 'key', at),
      'search',
      'search',
      'Hardcover refused the API key. Check it in Settings.',
    ],
    [
      new ApiError(502, 'search_failed', null, null, 'unavailable'),
      'search',
      'search',
      'Hardcover did not answer. Search again.',
    ],
    [
      new ApiError(502, 'search_failed', null, null, 'unreadable'),
      'search',
      'search',
      'Hardcover sent an answer Mechanical Librarian could not read. Search again later.',
    ],
    [
      new ApiError(404, 'search_not_found'),
      'choose',
      'choose',
      'This search has expired. Search again.',
    ],
    [
      new ApiError(409, 'work_changed'),
      'choose',
      'choose',
      'This work changed in Audiobookshelf. Search again.',
    ],
    [
      new ApiError(409, 'link_changed'),
      'choose',
      'choose',
      'The link was changed in another window. Search again to change it.',
    ],
    [
      new ApiError(422, 'card_not_choosable'),
      'choose',
      'choose',
      'This record cannot be linked that way. Choose This book.',
    ],
    [
      new ApiError(422, 'scope_not_offered'),
      'choose',
      'choose',
      'This record cannot be linked that way. Choose This book.',
    ],
    [
      new ApiError(409, 'link_changed'),
      'withdraw',
      'withdraw',
      'The link was changed in another window. Check it, then withdraw again.',
    ],
    [
      new ApiError(503, 'unavailable'),
      'withdraw',
      'withdraw',
      'No answer came back. Check whether the work is linked before choosing again.',
    ],
    [
      new AmbiguousMutationError(),
      'choose',
      'choose',
      'No answer came back. Check whether the work is linked before choosing again.',
    ],
    [
      new ApiError(404, 'work_not_found'),
      'search',
      'search',
      'This work is no longer in Audiobookshelf. Close this and choose another work.',
    ],
    [
      new ApiError(404, 'work_not_found'),
      'choose',
      'choose',
      'This work is no longer in Audiobookshelf. Close this and choose another work.',
    ],
    [
      new ApiError(409, 'find_not_offered'),
      'search',
      'search',
      'No source can search for this work now. Check its library’s sources in Settings.',
    ],
    [
      new ApiError(409, 'idempotency_conflict'),
      'choose',
      'choose',
      'No answer came back. Check whether the work is linked before choosing again.',
    ],
    [
      new ApiError(409, 'idempotency_conflict'),
      'withdraw',
      'withdraw',
      'No answer came back. Check whether the work is linked before choosing again.',
    ],
  ] as const)('%s on %s reads at %s', (error, action, place, text) => {
    expect(refusalFor(error, 'hardcover', action, now)).toEqual({ at: place, text })
  })
  it('names the time a limited source may be searched again', () => {
    const limit = refusalFor(
      new ApiError(429, 'search_limit', null, at),
      'hardcover',
      'search',
      now,
    )
    expect(limit.text).toMatch(
      /^Hardcover has been searched 30 times in the last hour\. Search again after .+\.$/u,
    )
    expect(
      refusalFor(
        new ApiError(429, 'provider_deferred', 'rate_limited', at),
        'audible_catalog',
        'search',
        now,
      ).text,
    ).toMatch(/^Audible is limiting requests\. Search again after .+\.$/u)
    expect(
      refusalFor(
        new ApiError(429, 'provider_deferred', 'quota_exhausted', at),
        'google_books',
        'search',
        now,
      ).text,
    ).toMatch(/^Google Books’s request quota is used up\. Search again after .+\.$/u)
  })
})

describe('withheld scope words', () => {
  it('names a print edition and an audiobook apart', () => {
    expect(
      withheldLines(
        card({ scopes: ['work'], withheld: [{ scope: 'edition', reason: 'format_print' }] }),
        'open_library',
      ),
    ).toEqual(['This edition is not offered because Open Library lists it as a print edition.'])
    expect(
      withheldLines(
        card({ scopes: ['work'], withheld: [{ scope: 'edition', reason: 'format_audio' }] }),
        'open_library',
      ),
    ).toEqual(['This edition is not offered because Open Library lists it as an audiobook.'])
  })
})

describe('request checks', () => {
  it('mirrors the server: owner text, ISBN checksums and the ASIN grammar', () => {
    expect(ownerTextValid('Dune')).toBe(true)
    expect(ownerTextValid('  Dune   Messiah ')).toBe(true)
    expect(ownerTextValid('')).toBe(false)
    expect(ownerTextValid('x'.repeat(513))).toBe(false)
    expect(ownerTextValid('see http://example.com')).toBe(false)
    expect(ownerTextValid('/srv/books/dune.epub')).toBe(false)
    expect(ownerTextValid('Either/Or')).toBe(true)
    expect(isbnValid('9780441172719')).toBe(true)
    expect(isbnValid('9780441172718')).toBe(false)
    expect(isbnValid('044117271X')).toBe(false)
    expect(isbnValid('0441172717')).toBe(true)
    expect(isbnValid('080442957X')).toBe(true)
    expect(isbnValid('978-0441172719')).toBe(false)
    expect(isbnInput('978-0-441-17271-9')).toBe('9780441172719')
    expect(isbnInput('0 8044 2957 x')).toBe('080442957X')
    expect(isbnInput('x044117271')).toBe('x044117271')
    expect(asinValid('B00B7NPRY8')).toBe(true)
    expect(asinValid('b00b7npry8')).toBe(false)
    expect(asinValid('B00B7NPRY')).toBe(false)
  })
})
