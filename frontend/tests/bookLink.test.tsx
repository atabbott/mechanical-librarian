// A work's book link (#1477, L2 and L4): the header pill, the link view it
// opens, Change into the search, Withdraw with its confirmation, Could not
// finish, and the works list's row phrase.
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import type { OwnerLinkCurrent, OwnerLinkView, ReviewHttp } from '../src/api/http'
import { ApiError } from '../src/api/http'
import { pillText, rowLinkPhrase, stateLine } from '../src/findThisBook'
import { createHttp, library, renderRoute } from './fixtures'
import { linked, withLinkView } from './findThisBookFixtures'

const view = (link: OwnerLinkCurrent | null, offered = true): OwnerLinkView => ({
  offered,
  sources: offered
    ? [{ provider: 'hardcover', lanes: ['title_authors', 'isbn'], marketplaces: null }]
    : [],
  prefill: { title: 'A Book', authors: ['Ada Example'], isbn: null, asin: null },
  link,
  schema_version: 'owner-link-view-v2',
})
const openLink = async (http: ReviewHttp, pill: string) => {
  const user = userEvent.setup()
  renderRoute('/library?lens=all&preview=case-1', http)
  const actions = await screen.findByRole('navigation', { name: 'Work actions' })
  await user.click(await within(actions).findByRole('button', { name: pill }))
  const dialog = await screen.findByRole('dialog', { name: 'Book link' })
  return { user, actions, dialog }
}

describe('the pill and the link view', () => {
  const now = new Date('2026-11-16T10:00:00Z')
  it.each([
    [linked(), 'Linked on Hardcover', null],
    [linked({ state: 'preparing' }), 'Linked, preparing', 'Preparing this work with the link.'],
    [
      linked({ state: 'preparing', paused: true }),
      'Linked, preparing',
      'Preparation is paused. The link is used when it resumes.',
    ],
    [
      linked({ state: 'waiting' }),
      'Linked, waiting for Hardcover',
      'Hardcover did not answer. The link is used once it does.',
    ],
    [
      linked({
        state: 'contradicted',
        reason: 'format',
        scope: 'edition',
        identifier: '9780441172719',
      }),
      'Link contradicted',
      'Link contradicted. Hardcover now lists ISBN 9780441172719 as a print edition. Change the link or withdraw it.',
    ],
    [
      linked({
        state: 'contradicted',
        reason: 'narrators',
        provider: 'audible_catalog',
        scope: 'recording',
        identifier: 'B00B7NPRY8',
      }),
      'Link contradicted',
      'Link contradicted. The recording now credits none of this audiobook’s narrators. Change the link or withdraw it.',
    ],
    [
      linked({
        state: 'contradicted',
        reason: 'variant',
        provider: 'audible_catalog',
        scope: 'recording',
        identifier: 'B00B7NPRY8',
      }),
      'Link contradicted',
      'Link contradicted. Audible now lists this recording as abridged. Change the link or withdraw it.',
    ],
    [
      linked({ state: 'needs_replacing', reason: 'missing' }),
      'Link needs replacing',
      'Link needs replacing. Hardcover no longer has this record. Change the link.',
    ],
    [
      linked({ state: 'needs_replacing', reason: 'merged' }),
      'Link needs replacing',
      'Link needs replacing. Hardcover merged this record into another one. Change the link.',
    ],
    [
      linked({ state: 'needs_replacing', reason: 'source_disabled' }),
      'Link needs replacing',
      'Link needs replacing. Hardcover is turned off for this library. Change the link.',
    ],
  ] as const)('reads %o as its pill and line', (link, pill, line) => {
    expect(pillText(link.state, link.provider)).toBe(pill)
    expect(stateLine(link, 'ebook', now)).toBe(line)
  })

  it('names the next automatic retry while waiting', () => {
    const at = Math.floor(now.getTime() / 1000) + 3 * 3600
    expect(stateLine(linked({ state: 'waiting', retry_at: at }), 'ebook', now)).toMatch(
      /^Hardcover did not answer\. Next automatic retry .+\.$/u,
    )
  })

  it('opens the link view from the pill, with its lines, Change and Withdraw', async () => {
    const http = withLinkView(
      view(
        linked({
          state: 'needs_replacing',
          reason: 'source_disabled',
          scope: 'edition',
          identifier: '9780441172719',
        }),
      ),
    )
    const { user, actions, dialog } = await openLink(http, 'Link needs replacing')
    expect(within(actions).queryByRole('button', { name: 'Find this book' })).toBeNull()
    expect(within(dialog).getByRole('heading', { level: 2, name: 'Book link' })).toHaveFocus()
    expect(within(dialog).getByText('Linked to Dune by Frank Herbert on Hardcover')).toBeVisible()
    expect(within(dialog).getByText('As this edition, ISBN 9780441172719')).toBeVisible()
    expect(within(dialog).getByRole('link', { name: 'Settings' })).toBeVisible()
    await user.click(within(dialog).getByRole('button', { name: 'Change the link' }))
    expect(within(dialog).getByRole('heading', { level: 2, name: 'Find this book' })).toHaveFocus()
    expect(within(dialog).getByRole('textbox', { name: 'Title' })).toHaveValue('A Book')
    await user.click(within(dialog).getByRole('button', { name: 'Close' }))
    expect(within(actions).getByRole('button', { name: 'Link needs replacing' })).toHaveFocus()
  })

  it('withdraws after its confirmation, says so, and gives focus to Find this book', async () => {
    let current = view(linked())
    const withdraw = vi.fn<ReviewHttp['withdrawOwnerLink']>(async () => {
      current = view(null)
      return {
        action: 'withdraw',
        link_row_id: `owner_link_${'a'.repeat(64)}`,
        scan_requested: true,
        schema_version: 'owner-link-change-v1',
      }
    })
    const http = withLinkView(current, {
      getOwnerLinkView: vi.fn(async () => current),
      withdrawOwnerLink: withdraw,
    })
    const { user, actions, dialog } = await openLink(http, 'Linked on Hardcover')
    await user.click(within(dialog).getByRole('button', { name: 'Withdraw the link' }))
    const question = within(dialog).getByText(
      'Withdraw the link to Dune? Its evidence leaves this work at the next preparation.',
    )
    expect(question).toHaveFocus()
    await user.click(within(dialog).getByRole('button', { name: 'Keep the link' }))
    expect(within(dialog).getByRole('button', { name: 'Withdraw the link' })).toHaveFocus()
    expect(withdraw).not.toHaveBeenCalled()
    await user.click(within(dialog).getByRole('button', { name: 'Withdraw the link' }))
    await user.click(within(dialog).getByRole('button', { name: 'Withdraw' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    expect(withdraw).toHaveBeenCalledWith(
      { connection: 'local', library: 'Books', item: 'internal-item-1' },
      `owner_link_${'a'.repeat(64)}`,
      expect.stringMatching(/^.{16,128}$/u),
    )
    const find = within(actions).getByRole('button', { name: 'Find this book' })
    expect(find).toHaveFocus()
    expect(screen.getByText('Link withdrawn.')).toHaveAttribute('role', 'status')
  })

  it('keeps the confirmation open with the race refusal beside Withdraw', async () => {
    const http = withLinkView(view(linked()), {
      withdrawOwnerLink: vi.fn(async () => {
        throw new ApiError(409, 'link_changed')
      }),
    })
    const { user, dialog } = await openLink(http, 'Linked on Hardcover')
    await user.click(within(dialog).getByRole('button', { name: 'Withdraw the link' }))
    await user.click(within(dialog).getByRole('button', { name: 'Withdraw' }))
    expect(await within(dialog).findByRole('alert')).toHaveTextContent(
      'The link was changed in another window. Check it, then withdraw again.',
    )
  })

  it('offers Withdraw only on a Could not finish work that is linked (L4)', async () => {
    const http = withLinkView(view(linked({ state: 'waiting' }), false))
    const { dialog } = await openLink(http, 'Linked, waiting for Hardcover')
    expect(within(dialog).queryByRole('button', { name: 'Change the link' })).toBeNull()
    expect(within(dialog).getByRole('button', { name: 'Withdraw the link' })).toBeVisible()
  })
})

describe('the works list', () => {
  it('adds the link phrase to the row’s status line', async () => {
    const first = library.works[0]
    if (!first) throw new Error('library fixture requires one work')
    const http: ReviewHttp = {
      ...createHttp(),
      getLibrary: vi.fn(async () => ({
        ...library,
        works: [
          {
            ...first,
            link: {
              state: 'contradicted' as const,
              source: 'search' as const,
              provider: 'hardcover' as const,
            },
          },
        ],
      })),
    }
    renderRoute('/library?lens=all', http)
    const row = await screen.findByRole('button', { name: /^A Book\./u })
    const pill = within(row.closest('li') ?? row).getByText('Link contradicted')
    expect(pill).toHaveClass('status-pill', 'is-notice')
    expect(pill.parentElement).toHaveClass('library-work-status')
    expect(rowLinkPhrase('linked', 'hardcover')).toBe('Linked')
  })

  it('names the source a waiting row waits for', async () => {
    const first = library.works[0]
    if (!first) throw new Error('library fixture requires one work')
    const http: ReviewHttp = {
      ...createHttp(),
      getLibrary: vi.fn(async () => ({
        ...library,
        works: [
          {
            ...first,
            link: {
              state: 'waiting' as const,
              source: 'search' as const,
              provider: 'open_library' as const,
            },
          },
        ],
      })),
    }
    renderRoute('/library?lens=all', http)
    const row = await screen.findByRole('button', { name: /^A Book\./u })
    const pill = within(row.closest('li') ?? row).getByText('Linked, waiting for Open Library')
    expect(pill).toHaveClass('status-pill', 'is-notice')
  })
})
