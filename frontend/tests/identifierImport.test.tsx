import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import {
  AmbiguousMutationError,
  ApiError,
  type IdentifierPreviewView,
  type ReviewHttp,
} from '../src/api/http'
import { activity, createHttp, renderRoute } from './fixtures'
import { view } from './settingsFixtures'

// Import identifiers (#1478) in Settings › Libraries, and Withdraw this import
// in an import's Activity Details.
const label = 'Ebooks & Audiobooks: Collected Editions'
const importId = `identifier_import_${'1'.repeat(64)}`
const sentinel = 'Sentinel/Path/ITEM-SENTINEL'

const preview = (willLink: number, refused: IdentifierPreviewView['refused'] = []) => {
  const rows = willLink + refused.length
  const counts = {
    will_link: willLink,
    already_linked: 0,
    no_item: 0,
    ambiguous: 0,
    invalid_identifier: 0,
    wrong_medium: 0,
    repeated: 0,
    conflicting: 0,
    unreadable: 0,
  }
  for (const { reason } of refused) {
    const key =
      reason === 'invalid_isbn' || reason === 'invalid_asin'
        ? 'invalid_identifier'
        : reason === 'asin_for_ebook' || reason === 'isbn_for_audiobook'
          ? 'wrong_medium'
          : reason === 'row_shape' || reason === 'row_too_long'
            ? 'unreadable'
            : reason
    counts[key] += 1
  }
  return {
    preview_id: 'preview-1',
    expires_at: 1900,
    rows,
    counts,
    will_link: Array.from({ length: willLink }, (_, index) => ({
      row: index + 2,
      title: `Neutral Title ${String(index + 1)}`,
      authors: index === 0 ? [] : ['A. Writer', 'B. Writer'],
      identifier_kind: index % 2 ? ('asin' as const) : ('isbn' as const),
      identifier: index % 2 ? 'B00TEST001' : '9780306406157',
    })),
    refused,
    schema_version: 'identifier-preview-v1' as const,
  }
}
// The issue's 20 rows: 15 link; 2 match no work, 1 matches two, 1 has a bad
// ISBN and 1 gives an ASIN for an ebook.
const demo = () =>
  preview(15, [
    { row: 6, reason: 'no_item' },
    { row: 9, reason: 'ambiguous' },
    { row: 13, reason: 'invalid_isbn' },
    { row: 17, reason: 'asin_for_ebook' },
    { row: 20, reason: 'no_item' },
  ])
const imported = (linked = 15) => ({
  import_id: importId,
  linked,
  scan_requested: true,
  schema_version: 'identifier-import-v1' as const,
})
const file = (size = 40) =>
  new File([`path\tidentifier\n${sentinel}\t9780306406157\n`.padEnd(size, '\n')], 'ids.tsv', {
    type: 'text/plain',
  })

const openImport = async (http: ReviewHttp, authority: 'file' | 'store' = 'store') => {
  const user = userEvent.setup()
  http.getSettings = vi.fn(async () => view(authority))
  renderRoute('/settings?group=libraries', http)
  await user.click(await screen.findByRole('button', { name: 'Import identifiers' }))
  return user
}
const choose = async (user: ReturnType<typeof userEvent.setup>, chosen = file()) => {
  await user.upload(screen.getByLabelText('Choose file'), chosen)
  await user.click(screen.getByRole('button', { name: 'Preview' }))
}

describe('Import identifiers in Settings › Libraries', () => {
  it.each(['store', 'file'] as const)(
    'opens from the foot of a saved library in %s mode, and Back returns to it',
    async (authority) => {
      const http = createHttp()
      const user = await openImport(http, authority)
      const heading = screen.getByRole('heading', { name: `Import identifiers for ${label}` })
      expect(heading).toHaveFocus()
      expect(screen.queryByText('Audiobookshelf library ID')).toBeNull()
      expect(screen.getByRole('button', { name: 'Preview' })).toBeDisabled()
      // Back is the one way out: no Cancel beside it.
      expect(screen.queryByRole('button', { name: 'Cancel' })).toBeNull()
      await user.click(screen.getByRole('button', { name: 'Back to libraries' }))
      await waitFor(() =>
        expect(screen.getByRole('button', { name: 'Import identifiers' })).toHaveFocus(),
      )
      expect(screen.getByText('Audiobookshelf library ID')).toBeVisible()
    },
  )

  it('refuses a file over 1 MiB without sending it', async () => {
    const http = createHttp()
    const user = await openImport(http)
    await choose(user, file(1_048_577))
    expect(
      await screen.findByText('This file is over 1 MiB. Split it and import each part.'),
    ).toHaveFocus()
    expect(http.previewIdentifiers).not.toHaveBeenCalled()
  })

  it.each([
    ['body_too_large', 413, 'This file is over 1 MiB. Split it and import each part.'],
    ['file_encoding', 422, 'This file is not UTF-8 text. Save it as UTF-8 and choose it again.'],
    [
      'file_control_character',
      422,
      'This file has a control character. Save it as plain text and choose it again.',
    ],
    ['file_header', 422, 'The first line must be item or path, a tab, then identifier.'],
    [
      'file_too_many_rows',
      422,
      'This file has more than 5,000 rows. Split it and import each part.',
    ],
    ['file_no_rows', 422, 'This file has no rows after its first line.'],
    ['library_not_found', 404, 'This library is not in the saved settings. Reload the page.'],
    ['library_unreadable', 503, "This library's works could not be read. Try again in a minute."],
  ])('says what to do for %s', async (code, status, text) => {
    const http = createHttp()
    http.previewIdentifiers = vi.fn(async () => {
      throw new ApiError(status, code)
    })
    const user = await openImport(http)
    await choose(user)
    const refusal = await screen.findByRole('alert')
    expect(refusal).toHaveTextContent(text)
    expect(refusal).toHaveFocus()
    expect(http.previewIdentifiers).toHaveBeenCalledWith('home', 'a-very_long-library-name', file())
  })

  it('previews the issue’s 20 rows: counts that are not zero, and both lists', async () => {
    const http = createHttp()
    http.previewIdentifiers = vi.fn(async () => demo())
    const user = await openImport(http)
    await choose(user)
    const summary = await screen.findByText('20 rows: 15 will be linked, 5 will not.')
    expect(summary).toHaveFocus()
    expect(screen.getAllByRole('listitem').map((item) => item.textContent)).toContain(
      '2 match no work',
    )
    const counts = summary.nextElementSibling as HTMLElement
    expect(
      within(counts)
        .getAllByRole('listitem')
        .map((item) => item.textContent),
    ).toEqual([
      '15 will be linked',
      '2 match no work',
      '1 matches more than one work',
      '1 has an identifier that is not an ISBN or ASIN',
      '1 gives an ASIN for an ebook or an ISBN for an audiobook',
    ])
    const willLink = screen.getByRole('list', { name: 'Will be linked' })
    const rows = within(willLink).getAllByRole('listitem')
    expect(rows).toHaveLength(15)
    expect(rows[0]).toHaveTextContent(
      'Row 2Neutral Title 1Author details unavailableISBN 9780306406157',
    )
    expect(rows[1]).toHaveTextContent('Row 3Neutral Title 2A. Writer, B. WriterASIN B00TEST001')
    // No path or item ID reaches the page.
    expect(document.body.textContent).not.toContain('SENTINEL')
    await user.click(screen.getByRole('button', { name: 'Not linked (5)' }))
    const notLinked = screen.getByRole('list', { name: 'Not linked' })
    expect(
      within(notLinked)
        .getAllByRole('listitem')
        .map((item) => item.textContent),
    ).toEqual([
      'Row 6Matches no work in this library',
      'Row 9Matches more than one work. Use the item ID or a longer path.',
      'Row 13Needs an ISBN of 10 or 13 digits with a matching check digit',
      'Row 17This is an ASIN, and an ebook needs an ISBN',
      'Row 20Matches no work in this library',
    ])
  })

  it('shows 100 rows at a time and moves focus to the first one added', async () => {
    const http = createHttp()
    http.previewIdentifiers = vi.fn(async () => preview(250))
    const user = await openImport(http)
    await choose(user)
    const list = await screen.findByRole('list', { name: 'Will be linked' })
    expect(within(list).getAllByRole('listitem')).toHaveLength(100)
    await user.click(screen.getByRole('button', { name: 'Show 100 more' }))
    await waitFor(() => expect(within(list).getAllByRole('listitem')[100]).toHaveFocus())
    await user.click(screen.getByRole('button', { name: 'Show 50 more' }))
    expect(within(list).getAllByRole('listitem')).toHaveLength(250)
    expect(screen.queryByRole('button', { name: /more$/u })).toBeNull()
  })

  it('offers no link when no row can be linked', async () => {
    const http = createHttp()
    http.previewIdentifiers = vi.fn(async () => preview(0, [{ row: 2, reason: 'row_shape' }]))
    const user = await openImport(http)
    await choose(user)
    expect(await screen.findByText('1 row: 0 will be linked, 1 will not.')).toBeVisible()
    expect(screen.getByText('1 could not be read')).toBeVisible()
    expect(screen.getByText('No rows can be linked.')).toBeVisible()
    expect(screen.queryByRole('button', { name: /^Link/u })).toBeNull()
    expect(screen.getByRole('list', { name: 'Not linked' })).toHaveTextContent(
      'Needs two values separated by one tab',
    )
  })

  it('links the works with one key and says where to withdraw them', async () => {
    const http = createHttp()
    http.previewIdentifiers = vi.fn(async () => demo())
    http.importIdentifiers = vi.fn(async () => imported())
    const user = await openImport(http)
    await choose(user)
    await user.click(await screen.findByRole('button', { name: 'Link 15 works' }))
    const success = await screen.findByText(/^Linked 15 works\. They are prepared again/u)
    expect(success).toHaveFocus()
    expect(within(success).getByRole('link', { name: 'Activity' })).toBeVisible()
    expect(http.importIdentifiers).toHaveBeenCalledWith('preview-1', expect.any(String))
    const key = vi.mocked(http.importIdentifiers).mock.calls[0]?.[1] ?? ''
    expect(key.length).toBeGreaterThanOrEqual(16)
  })

  it.each([
    [
      'preview_stale',
      409,
      "This library's works changed after the preview. Preview the file again.",
      true,
    ],
    ['preview_not_found', 404, 'This preview is no longer open. Preview the file again.', true],
    [
      'preview_imported',
      409,
      'This preview was already imported. Its import is in Activity.',
      false,
    ],
  ])('answers %s', async (code, status, text, again) => {
    const http = createHttp()
    http.previewIdentifiers = vi.fn(async () => demo())
    http.importIdentifiers = vi.fn(async () => {
      throw new ApiError(status, code)
    })
    const user = await openImport(http)
    await choose(user)
    await user.click(await screen.findByRole('button', { name: 'Link 15 works' }))
    const refusal = await screen.findByRole('alert')
    expect(refusal).toHaveTextContent(text)
    expect(refusal).toHaveFocus()
    expect(http.getIdentifierPreviewStatus).not.toHaveBeenCalled()
    const previewAgain = screen.queryByRole('button', { name: 'Preview again' })
    if (!again) return expect(previewAgain).toBeNull()
    if (previewAgain === null) throw new Error('Expected Preview again')
    await user.click(previewAgain)
    expect(await screen.findByText('20 rows: 15 will be linked, 5 will not.')).toHaveFocus()
    expect(http.previewIdentifiers).toHaveBeenCalledTimes(2)
  })

  it.each([
    ['imported', /^Linked 15 works\./u],
    ['open', /^The import was not recorded\.$/u],
    ['closed', /^The import was not recorded, and the preview has closed\./u],
  ] as const)('reads back an import whose answer was lost: %s', async (state, line) => {
    const http = createHttp()
    http.previewIdentifiers = vi.fn(async () => demo())
    http.importIdentifiers = vi.fn(async () => {
      throw new AmbiguousMutationError()
    })
    http.getIdentifierPreviewStatus = vi.fn(async () => ({
      state,
      import_id: state === 'imported' ? importId : null,
      linked: state === 'imported' ? 15 : null,
      schema_version: 'identifier-preview-status-v1' as const,
    }))
    const user = await openImport(http)
    await choose(user)
    await user.click(await screen.findByRole('button', { name: 'Link 15 works' }))
    expect(await screen.findByText(line)).toHaveFocus()
    expect(http.importIdentifiers).toHaveBeenCalledOnce()
    if (state !== 'open') return
    // Linking again replays the same import if the first one did land.
    await user.click(screen.getByRole('button', { name: 'Link 15 works' }))
    const [first, second] = vi.mocked(http.importIdentifiers).mock.calls
    expect(second?.[1]).toBe(first?.[1])
  })

  it('sends the owner to Activity when the import cannot be checked', async () => {
    const http = createHttp()
    http.previewIdentifiers = vi.fn(async () => demo())
    http.importIdentifiers = vi.fn(async () => {
      throw new ApiError(503, 'unavailable')
    })
    http.getIdentifierPreviewStatus = vi.fn(async () => {
      throw new ApiError(503, 'unavailable')
    })
    const user = await openImport(http)
    await choose(user)
    await user.click(await screen.findByRole('button', { name: 'Link 15 works' }))
    const line = await screen.findByText(/^No answer came back, and Mechanical Librarian/u)
    expect(line).toHaveFocus()
    expect(within(line).getByRole('link', { name: 'Activity' })).toBeVisible()
    expect(http.importIdentifiers).toHaveBeenCalledOnce()
  })

  it('starts again from the file step on Choose another file', async () => {
    const http = createHttp()
    http.previewIdentifiers = vi.fn(async () => demo())
    const user = await openImport(http)
    await choose(user)
    await user.click(await screen.findByRole('button', { name: 'Choose another file' }))
    expect(screen.getByLabelText('Choose file')).toHaveFocus()
    expect(screen.getByRole('button', { name: 'Preview' })).toBeDisabled()
  })
})

describe('Withdraw this import in Activity', () => {
  const importRow = () =>
    activity({
      events: [
        {
          event_id: 'import-row',
          change: 'Linked 15 works',
          change_detail: 'Linked 15 works from an identifiers file.',
          destination: 'links',
          field_label: 'Links',
          identifier_import_id: importId,
          state: 'imported',
          work_label: 'Identifiers file',
          evidence: [
            { label: 'Rows in the file', value: '20' },
            { label: 'Linked', value: '15' },
            { label: 'Not linked', value: '5' },
          ],
        },
      ],
    })
  const status = (still: number, withdrawn = false) => ({
    import_id: importId,
    linked: 15,
    still_linked: still,
    withdrawn,
    schema_version: 'identifier-import-v1' as const,
  })
  const openDetails = async (http: ReviewHttp) => {
    const user = userEvent.setup()
    vi.mocked(http.getActivity).mockResolvedValue(importRow())
    renderRoute('/activity', http)
    const row = (await screen.findByText('Identifiers file')).closest('article')
    if (row === null) throw new Error('Expected the import row')
    expect(within(row).getByText('Imported')).toBeVisible()
    await user.click(within(row).getByRole('button', { name: /^Details for Links/u }))
    return { user, row }
  }

  it('asks before withdrawing the links still current, then says how many it withdrew', async () => {
    const http = createHttp()
    http.getIdentifierImport = vi.fn(async () => status(15))
    http.withdrawIdentifierImport = vi.fn(async () => ({
      import_id: importId,
      withdrawn: 15,
      scan_requested: true,
      schema_version: 'identifier-import-withdrawal-v1' as const,
    }))
    const { user, row } = await openDetails(http)
    const withdraw = await within(row).findByRole('button', {
      name: /^Withdraw this import, Identifiers file, /u,
    })
    expect(within(row).queryByText('Still linked')).toBeNull()
    await user.click(withdraw)
    const question = within(row).getByText(
      'Withdraw 15 links from this import? Each work is prepared again without its link.',
    )
    expect(question).toHaveFocus()
    await user.click(within(row).getByRole('button', { name: 'Keep links' }))
    expect(within(row).getByRole('button', { name: /^Withdraw this import/u })).toHaveFocus()
    expect(http.withdrawIdentifierImport).not.toHaveBeenCalled()
    await user.click(within(row).getByRole('button', { name: /^Withdraw this import/u }))
    await user.click(within(row).getByRole('button', { name: 'Withdraw links' }))
    expect(await within(row).findByText('Withdrew 15 links.')).toHaveFocus()
    expect(http.withdrawIdentifierImport).toHaveBeenCalledWith(importId, expect.any(String))
  })

  it('names how many are still linked when some changed since', async () => {
    const http = createHttp()
    http.getIdentifierImport = vi.fn(async () => status(12))
    const { user, row } = await openDetails(http)
    expect(await within(row).findByText('Still linked')).toBeVisible()
    expect(within(row).getByText('12')).toBeVisible()
    await user.click(within(row).getByRole('button', { name: /^Withdraw this import/u }))
    expect(within(row).getByText(/^Withdraw 12 links from this import\?/u)).toBeVisible()
  })

  it.each([
    ['none still linked', status(0)],
    ['a withdrawn import', status(0, true)],
  ])('offers nothing for %s', async (_name, read) => {
    const http = createHttp()
    http.getIdentifierImport = vi.fn(async () => read)
    const { row } = await openDetails(http)
    expect(await within(row).findByText('No links from this import remain.')).toBeVisible()
    expect(within(row).queryByRole('button', { name: /^Withdraw/u })).toBeNull()
  })

  it.each([
    [new AmbiguousMutationError(), /^No answer came back\. The ledger below/u],
    [new ApiError(409, 'nothing_to_withdraw'), /^No links from this import remain\.$/u],
  ])('reads a refused or lost withdrawal: %s', async (error, line) => {
    const http = createHttp()
    http.getIdentifierImport = vi.fn(async () => status(15))
    http.withdrawIdentifierImport = vi.fn(async () => {
      throw error
    })
    const { user, row } = await openDetails(http)
    await user.click(await within(row).findByRole('button', { name: /^Withdraw this import/u }))
    await user.click(within(row).getByRole('button', { name: 'Withdraw links' }))
    expect(await within(row).findByText(line)).toHaveFocus()
    expect(http.withdrawIdentifierImport).toHaveBeenCalledOnce()
  })

  it('offers no withdrawal on a row that is not an import', async () => {
    const http = createHttp()
    vi.mocked(http.getActivity).mockResolvedValue(activity({ events: [{ event_id: 'plain' }] }))
    renderRoute('/activity', http)
    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: /^Details for ISBN/u }))
    expect(screen.queryByRole('button', { name: /^Withdraw this import/u })).toBeNull()
    expect(http.getIdentifierImport).not.toHaveBeenCalled()
  })
})
