import { cleanup, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { ApiError, UncertainMutationError } from '../src/api/http'
import type {
  CaseDetailView,
  CoverTab,
  CoversView,
  CoverWorkView,
  ReviewHttp,
} from '../src/api/http'
import { createHttp, detail, library, renderRoute } from './fixtures'

afterEach(cleanup)

const base = detail.fields[0]
if (!base) throw new Error('the cover fixture needs a field')
const candidate = (suffix: string, incumbent: boolean) => ({
  artifactId: `cover_${suffix.repeat(64)}`,
  deterministicCitations: [`citation-${suffix}`],
  deterministicRank: incumbent ? null : 0,
  deterministicRankKey: [1, 0, -960000, -800, -1200, incumbent ? 0 : 1, suffix],
  height: 1200,
  identity: { outcome: 'verified_work_match' as const },
  incumbent,
  matchState: 'same_book' as const,
  observation: null,
  originalSha256: suffix.repeat(64),
  ordinal: incumbent ? 0 : 1,
  previewSha256: suffix.repeat(64),
  reference: `cover_ref_${suffix.repeat(64)}`,
  selectable: !incumbent,
  manualEligible: !incumbent,
  sourceKinds: [incumbent ? ('epub' as const) : ('open_library' as const)],
  state: incumbent ? ('excluded' as const) : ('acceptable' as const),
  width: 800,
})
const coverDetail = (id: string, title: string): CaseDetailView => ({
  ...detail,
  id,
  title,
  fields: [
    {
      ...base,
      destination: 'cover' as const,
      currentValue: `cover_${'1'.repeat(64)}`,
      currentRaw: `cover_${'1'.repeat(64)}`,
      proposedValues: [{ kind: 'cover', value: `cover_${'2'.repeat(64)}` }],
      proposalId: `proposal-${id}`,
      targetFingerprint: `fingerprint-${id}`,
      currentCoverSelectionId: null,
      coverConsideration: {
        id: `cover_consideration_${id}`,
        choiceContexts: [],
        current: true,
        candidates: [candidate('1', true), candidate('2', false)],
        incumbent: { reference: `cover_ref_${'1'.repeat(64)}`, state: 'available' },
        omissions: [],
        policy: null,
        policyPreferences: {
          avoidPromotionalText: false,
          avoidTieInBranding: false,
          preferMinimalComposition: false,
        },
        policyReference: `cover_policy_${'3'.repeat(64)}`,
        providerFetching: false,
        ready: true,
        reviewerAccessRevision: null,
        reviewerEngine: null,
        reviewerModel: null,
        reviewerStatus: 'unavailable',
        reviewerUnavailableReason: null,
        targetFingerprint: `fingerprint-${id}`,
      },
    },
  ],
})
const titles: Record<string, string> = {
  'case-1': 'First Book',
  'case-2': 'Second Book',
  'case-3': 'Third Book',
  'case-9': 'First Book',
  'case-77': 'Late Book',
}
const preview = (digit: string, width = 600, height = 900) => ({
  height,
  preview_sha256: digit.repeat(64),
  url: `/api/v1/library/cover?preview=${digit.repeat(64)}`,
  width,
})
const work = (
  id: string,
  group: CoverWorkView['group'],
  extra: Partial<CoverWorkView> = {},
): CoverWorkView => ({
  authors: ['An Author'],
  choosable: group !== 'none',
  cover: preview('a'),
  group,
  media_kind: 'epub',
  review_case_id: id,
  switched: null,
  title: titles[id] ?? id,
  unreadable_cover: false,
  ...extra,
})
const counts = { decide: 2, kept: 1, switched: 2, all: 5 }
const page = (
  view: CoverTab,
  items: CoverWorkView[],
  next_cursor: string | null = null,
  pageCounts = counts,
): CoversView => ({
  counts: pageCounts,
  items,
  next_cursor,
  schema_version: 'review-covers-v1',
  view,
})

const coversHttp = (pages: Partial<Record<CoverTab, CoversView>>) => {
  const http = createHttp()
  return {
    ...http,
    recordDecision: vi.fn<ReviewHttp['recordDecision']>(http.recordDecision),
    getCaseSuccessor: vi.fn<ReviewHttp['getCaseSuccessor']>(async () => null),
    getCovers: vi.fn<ReviewHttp['getCovers']>(async (view, at) => {
      const tabPage = pages[view] ?? page(view, [])
      return at?.cover
        ? { ...tabPage, items: tabPage.items.filter((item) => item.review_case_id === at.cover) }
        : tabPage
    }),
    getCaseDetail: vi.fn<ReviewHttp['getCaseDetail']>(async (id) =>
      coverDetail(id, titles[id] ?? id),
    ),
  }
}
const decide = page('decide', [work('case-1', 'proposal'), work('case-2', 'choice')])
const chooseOther = async (user: ReturnType<typeof userEvent.setup>) => {
  const group = await screen.findByRole('radiogroup', { name: 'Which cover should this book use?' })
  await user.click(within(group).getByRole('radio', { name: /Open Library cover/u }))
}
// A wide screen, where an open book sits beside the list.
const wideScreen = () =>
  vi.stubGlobal(
    'matchMedia',
    (query: string) =>
      ({
        matches: false,
        media: query,
        addEventListener: () => undefined,
        removeEventListener: () => undefined,
      }) as unknown as MediaQueryList,
  )
afterEach(() => vi.unstubAllGlobals())

describe('Covers view (#1633)', () => {
  it('switches from Works to Covers above the heading and focuses the heading', async () => {
    const user = userEvent.setup()
    const http = coversHttp({ decide })
    const { router } = renderRoute('/library?lens=all', {
      ...http,
      getLibrary: vi.fn(async () => library),
    })
    const switcher = await screen.findByRole('group', { name: 'Library views' })
    expect(within(switcher).getByRole('button', { name: 'Works' })).toHaveAttribute(
      'aria-pressed',
      'true',
    )
    await user.click(within(switcher).getByRole('button', { name: 'Covers' }))
    const heading = await screen.findByRole('heading', { level: 1, name: 'Covers' })
    await waitFor(() => expect(heading).toHaveFocus())
    const moved = screen.getByRole('group', { name: 'Library views' })
    expect(moved.compareDocumentPosition(heading) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(router.state.location.search).toEqual({ lens: 'all', view: 'covers', tab: 'decide' })
    const tabs = screen.getByRole('navigation', { name: 'Cover views' })
    expect(await within(tabs).findByRole('button', { name: 'To decide 2' })).toHaveAttribute(
      'aria-pressed',
      'true',
    )
    expect(within(tabs).getByRole('button', { name: 'Kept 1' })).toBeVisible()
    expect(within(tabs).getByRole('button', { name: 'Switched 2' })).toBeVisible()
    expect(within(tabs).getByRole('button', { name: 'All 5' })).toBeVisible()
    // Suggestions come before choices, each under its group with its count.
    const headings = screen.getAllByRole('heading', { level: 2 }).map((node) => node.textContent)
    expect(headings).toEqual(['Switch suggested 1', 'Choose 1'])
    // Queue thumbnails are decorative: each book is named once.
    expect(screen.getByRole('button', { name: /^First BookAn Author/u })).toBeVisible()
  })

  it('opens the first undecided book beside the list on a wide screen', async () => {
    wideScreen()
    const { router } = renderRoute('/library?lens=all&view=covers', coversHttp({ decide }))
    expect(await screen.findByRole('heading', { level: 2, name: 'First Book' })).toBeVisible()
    expect(router.state.location.search.cover).toBe('case-1')
  })

  it('records one tap as the choice, opens the next book and counts again', async () => {
    const user = userEvent.setup()
    const http = coversHttp({ decide })
    const after = page('decide', [...decide.items], null, { ...counts, decide: 1, kept: 2 })
    http.getCovers.mockResolvedValueOnce(decide).mockResolvedValue(after)
    const { router } = renderRoute('/library?lens=all&view=covers&tab=decide&cover=case-1', http)
    await chooseOther(user)
    await waitFor(() => expect(http.recordDecision).toHaveBeenCalledOnce())
    const [id, command] = http.recordDecision.mock.calls[0] ?? []
    expect(id).toBe('case-1')
    expect(command).toMatchObject({
      destination: 'cover',
      outcome: 'accept',
      selectedValue: { kind: 'cover', value: `cover_${'2'.repeat(64)}` },
      considerationId: 'cover_consideration_case-1',
      coverConfirmation: { expected_selection_id: null, confirm_unverified_match: false },
    })
    expect(await screen.findByText('Switched First Book.')).toBeVisible()
    await waitFor(() => expect(router.state.location.search.cover).toBe('case-2'))
    const next = await screen.findByRole('heading', { level: 2, name: 'Second Book' })
    await waitFor(() => expect(next).toHaveFocus())
    const tabs = screen.getByRole('navigation', { name: 'Cover views' })
    expect(await within(tabs).findByRole('button', { name: 'To decide 1' })).toBeVisible()
    expect(within(tabs).getByRole('button', { name: 'Kept 2' })).toBeVisible()
    // The decided book stays in the list, marked.
    expect(screen.getByRole('button', { name: /^First Book.*Reviewed$/u })).toBeVisible()
  })

  it('moves on with Skip for now and decides nothing', async () => {
    const user = userEvent.setup()
    const http = coversHttp({ decide })
    const { router } = renderRoute('/library?lens=all&view=covers&cover=case-1', http)
    await user.click(await screen.findByRole('button', { name: 'Skip for now' }))
    expect(await screen.findByText('Skipped First Book.')).toBeVisible()
    await waitFor(() => expect(router.state.location.search.cover).toBe('case-2'))
    expect(http.recordDecision).not.toHaveBeenCalled()
  })

  it('refuses a stale book on its own, focuses Reload and clears the refused choice', async () => {
    const user = userEvent.setup()
    const http = coversHttp({ decide })
    http.recordDecision.mockRejectedValueOnce(new ApiError(409, 'stale'))
    renderRoute('/library?lens=all&view=covers&cover=case-1', http)
    await chooseOther(user)
    expect(
      await screen.findByText(
        "This book's covers changed since the page loaded. Nothing was changed.",
      ),
    ).toBeVisible()
    await waitFor(() => expect(screen.getByRole('button', { name: 'Reload' })).toHaveFocus())
    expect(screen.queryByRole('radio', { checked: true })).toBeNull()
    await user.click(screen.getByRole('button', { name: /^Second Book/u }))
    await chooseOther(user)
    expect(await screen.findByText('Switched Second Book.')).toBeVisible()
  })

  it('reloads a replaced book by following it to its successor', async () => {
    const user = userEvent.setup()
    const reloaded = page('decide', [work('case-9', 'choice'), work('case-2', 'choice')])
    const http = coversHttp({ decide })
    http.recordDecision.mockRejectedValueOnce(new ApiError(409, 'stale'))
    http.getCaseSuccessor.mockResolvedValue('case-9')
    const { router } = renderRoute('/library?lens=all&view=covers&cover=case-1', http)
    await chooseOther(user)
    http.getCovers.mockResolvedValue(reloaded)
    await user.click(await screen.findByRole('button', { name: 'Reload' }))
    await waitFor(() => expect(router.state.location.search.cover).toBe('case-9'))
    const heading = await screen.findByRole('heading', { level: 2, name: 'First Book' })
    await waitFor(() => expect(heading).toHaveFocus())
    expect(http.getCaseSuccessor).toHaveBeenCalledWith('case-1')
  })

  it('says when no answer came back', async () => {
    const user = userEvent.setup()
    const http = coversHttp({ decide })
    http.recordDecision.mockRejectedValueOnce(
      new UncertainMutationError({
        operation: 'POST /api/v1/review-cases/{review_case_id}/decisions',
        responseStatus: 0,
        at: '',
        reason: 'network',
      }),
    )
    renderRoute('/library?lens=all&view=covers&cover=case-1', http)
    await chooseOther(user)
    expect(
      await screen.findByText(
        'No answer came back. Check whether the cover was changed before choosing again.',
      ),
    ).toBeVisible()
  })

  it('lists automatic switches with Before and Now, and Keep lands on Change', async () => {
    const user = userEvent.setup()
    const at = Math.floor(Date.now() / 1000) - 7200
    const switched = page('switched', [
      work('case-1', 'auto', {
        switched: {
          at,
          before: {
            ...preview('b', 500, 500),
            url: `/api/v1/covers/replaced?plan=p&preview=${'b'.repeat(64)}`,
          },
          filled: false,
        },
      }),
      work('case-2', 'auto', { switched: { at, before: null, filled: true } }),
    ])
    const http = coversHttp({ switched })
    renderRoute('/library?lens=all&view=covers&tab=switched', http)
    const rows = await screen.findAllByRole('listitem')
    const first = rows[0]
    if (!first) throw new Error('a switched row is required')
    expect(within(first).getByRole('img', { name: 'Previous cover' })).toBeVisible()
    expect(within(first).getByText('500 × 500 px')).toBeVisible()
    expect(within(first).getByRole('img', { name: 'Cover now' })).toBeVisible()
    expect(within(first).getByText('600 × 900 px')).toBeVisible()
    expect(within(first).getByText(/^Switched automatically, (today|yesterday) at /u)).toBeVisible()
    expect(within(first).getByRole('link', { name: 'First Book' })).toHaveAttribute(
      'href',
      '/library?lens=all&preview=case-1',
    )
    // Put back lands with #1363 PR 4; until then a switch offers Keep and Others.
    expect(within(first).queryByRole('button', { name: 'Put back' })).toBeNull()
    expect(within(first).getByRole('button', { name: 'Others' })).toHaveAccessibleDescription(
      'First Book',
    )
    // A fill says "No cover" once, as its Before caption.
    const fill = rows[1]
    if (!fill) throw new Error('a fill row is required')
    expect(within(fill).getAllByText('No cover')).toHaveLength(1)
    expect(within(fill).queryByRole('img', { name: 'Previous cover' })).toBeNull()
    await user.click(within(first).getByRole('button', { name: 'Keep' }))
    await waitFor(() => expect(http.recordDecision).toHaveBeenCalledOnce())
    expect(http.recordDecision.mock.calls[0]?.[1]).toMatchObject({
      destination: 'cover',
      outcome: 'keep_current',
      selectedValue: null,
      considerationId: 'cover_consideration_case-1',
    })
    expect(await screen.findByText('Kept First Book.')).toBeVisible()
    expect(within(first).getByText('Kept')).toBeVisible()
    await waitFor(() => expect(within(first).getByRole('button', { name: 'Change' })).toHaveFocus())
  })

  it('tells an unreadable cover from a missing one', async () => {
    const http = coversHttp({
      decide: page('decide', [
        work('case-1', 'choice', { cover: null, unreadable_cover: true }),
        work('case-2', 'choice', { cover: null }),
      ]),
    })
    renderRoute('/library?lens=all&view=covers', http)
    expect(
      within(await screen.findByRole('button', { name: /^First Book/u })).getByText(
        'Image unavailable',
      ),
    ).toBeVisible()
    expect(
      within(screen.getByRole('button', { name: /^Second Book/u })).getByText('No cover'),
    ).toBeVisible()
  })

  it.each([
    ['decide', 'No covers to decide.'],
    ['kept', 'No kept covers with other choices.'],
    ['switched', 'No covers switched automatically.'],
    ['all', 'No works yet.'],
  ] as const)('says when %s has no covers', async (tab, sentence) => {
    renderRoute(`/library?lens=all&view=covers&tab=${tab}`, coversHttp({}))
    expect(await screen.findByText(sentence)).toBeVisible()
  })

  it('shows more after a decision without skipping a book, and focuses the first added', async () => {
    const user = userEvent.setup()
    const http = coversHttp({})
    const first = page('decide', [work('case-1', 'choice'), work('case-2', 'choice')], 'cursor-2')
    const second = page('decide', [work('case-3', 'choice')])
    http.getCovers.mockImplementation(async (view, at) =>
      at?.cursor === 'cursor-2' ? second : view === 'decide' ? first : page(view, []),
    )
    renderRoute('/library?lens=all&view=covers&cover=case-1', http)
    await chooseOther(user)
    expect(await screen.findByText('Switched First Book.')).toBeVisible()
    await user.click(screen.getByRole('button', { name: 'Show more covers' }))
    const added = await screen.findByRole('button', { name: /^Third Book/u })
    // The next page resumes after the last book shown, wherever decisions moved
    // the books before it (#1633 review B1).
    expect(http.getCovers).toHaveBeenCalledWith('decide', { cursor: 'cursor-2' }, expect.anything())
    await waitFor(() => expect(added).toHaveFocus())
    expect(screen.getByRole('button', { name: /^First Book.*Reviewed$/u })).toBeVisible()
    expect(screen.queryByRole('button', { name: 'Show more covers' })).toBeNull()
  })

  it('shows the wall with titles and opens a book after its row, with Close', async () => {
    const user = userEvent.setup()
    const http = coversHttp({
      all: page('all', [work('case-1', 'kept'), work('case-2', 'none'), work('case-3', 'kept')]),
    })
    renderRoute('/library?lens=all&view=covers&tab=all', http)
    const tile = await screen.findByRole('button', { name: /First Book/u })
    expect(within(tile).getByText('An Author')).toBeVisible()
    await user.click(tile)
    const panel = (
      await screen.findByRole('radiogroup', { name: 'Which cover should this book use?' })
    ).closest('li')
    // Every tile of the row comes before the panel.
    expect(panel?.previousElementSibling).toHaveTextContent('Third Book')
    expect(screen.queryByRole('link', { name: 'Covers' })).toBeNull()
    await user.click(screen.getByRole('button', { name: 'Close' }))
    await waitFor(() => expect(screen.getByRole('button', { name: /First Book/u })).toHaveFocus())
  })

  it('opens a linked book that is past the loaded pages on its own', async () => {
    const http = coversHttp({ all: page('all', [work('case-1', 'kept')], 'cursor-2') })
    http.getCovers.mockImplementation(async (view, at) =>
      at?.cover === 'case-77'
        ? page(view, [work('case-77', 'kept')])
        : page(view, [work('case-1', 'kept')], 'cursor-2'),
    )
    renderRoute('/library?lens=all&view=covers&tab=all&cover=case-77', http)
    expect(await screen.findByRole('heading', { level: 2, name: 'Late Book' })).toBeVisible()
    expect(http.getCovers).toHaveBeenCalledWith('all', { cover: 'case-77' }, expect.anything())
  })

  it('drops a linked book the tab does not hold', async () => {
    const { router } = renderRoute(
      '/library?lens=all&view=covers&tab=all&cover=case-77',
      coversHttp({ all: page('all', [work('case-1', 'kept')]) }),
    )
    await waitFor(() => expect(router.state.location.search.cover).toBeUndefined())
  })

  it('shows Back to books for the open book', async () => {
    const user = userEvent.setup()
    const http = coversHttp({ decide })
    const { router } = renderRoute('/library?lens=all&view=covers&cover=case-1', http)
    await user.click(await screen.findByRole('button', { name: 'Back to books' }))
    await waitFor(() => expect(router.state.location.search.cover).toBeUndefined())
  })

  it('says Loading covers while the first page is read', async () => {
    const http = coversHttp({})
    http.getCovers.mockImplementation(() => new Promise(() => undefined))
    renderRoute('/library?lens=all&view=covers', http)
    expect(await screen.findByText('Loading covers…')).toBeVisible()
  })
})
