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
const preview = (digit: string, width = 600, height = 900) => ({
  height,
  preview_sha256: digit.repeat(64),
  url: `/api/v1/library/cover?preview=${digit.repeat(64)}`,
  width,
})
const work = (
  id: string,
  title: string,
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
  title,
  ...extra,
})
const page = (
  view: CoverTab,
  items: CoverWorkView[],
  next_cursor: string | null = null,
  counts = { decide: 2, kept: 1, switched: 2, all: 5 },
): CoversView => ({ counts, items, next_cursor, schema_version: 'review-covers-v1', view })

const coversHttp = (pages: Partial<Record<CoverTab, CoversView>>) => {
  const http = createHttp()
  const recordDecision = vi.fn<ReviewHttp['recordDecision']>(http.recordDecision)
  return {
    ...http,
    recordDecision,
    getCovers: vi.fn<ReviewHttp['getCovers']>(async (view) => pages[view] ?? page(view, [])),
    getCaseDetail: vi.fn<ReviewHttp['getCaseDetail']>(async (id) =>
      coverDetail(id, id === 'case-2' ? 'Second Book' : 'First Book'),
    ),
  }
}
const decide = page('decide', [
  work('case-1', 'First Book', 'proposal'),
  work('case-2', 'Second Book', 'choice'),
])

describe('Covers view (#1633)', () => {
  it('switches from Works to Covers and shows each tab with its count', async () => {
    const user = userEvent.setup()
    const http = coversHttp({ decide })
    const { router } = renderRoute('/library?lens=all', {
      ...http,
      getLibrary: vi.fn(async () => library),
    })
    const switcher = await screen.findByRole('group', { name: 'Library' })
    expect(within(switcher).getByRole('button', { name: 'Works' })).toHaveAttribute(
      'aria-pressed',
      'true',
    )
    await user.click(within(switcher).getByRole('button', { name: 'Covers' }))
    expect(await screen.findByRole('heading', { level: 1, name: 'Covers' })).toBeVisible()
    expect(router.state.location.search).toEqual({ lens: 'all', view: 'covers', tab: 'decide' })
    const tabs = screen.getByRole('navigation', { name: 'Covers' })
    expect(await within(tabs).findByRole('button', { name: 'To decide 2' })).toHaveAttribute(
      'aria-pressed',
      'true',
    )
    expect(within(tabs).getByRole('button', { name: 'Kept 1' })).toBeVisible()
    expect(within(tabs).getByRole('button', { name: 'Switched 2' })).toBeVisible()
    expect(within(tabs).getByRole('button', { name: 'All 5' })).toBeVisible()
    // Suggestions come before choices, each under its group.
    const headings = screen.getAllByRole('heading', { level: 2 }).map((node) => node.textContent)
    expect(headings).toEqual(['Switch suggested', 'Choose'])
  })

  it('records one tap as the choice and opens the next book', async () => {
    const user = userEvent.setup()
    const http = coversHttp({ decide })
    const { router } = renderRoute('/library?lens=all&view=covers&tab=decide&cover=case-1', http)
    const group = await screen.findByRole('radiogroup', {
      name: 'Which cover should this book use?',
    })
    await user.click(within(group).getByRole('radio', { name: /Open Library cover/u }))
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
    expect(await screen.findByRole('heading', { level: 2, name: 'Second Book' })).toBeVisible()
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

  it('refuses a stale book on its own while the next book still works', async () => {
    const user = userEvent.setup()
    const http = coversHttp({ decide })
    http.recordDecision.mockRejectedValueOnce(new ApiError(409, 'stale'))
    renderRoute('/library?lens=all&view=covers&cover=case-1', http)
    const first = await screen.findByRole('radiogroup', {
      name: 'Which cover should this book use?',
    })
    await user.click(within(first).getByRole('radio', { name: /Open Library cover/u }))
    expect(
      await screen.findByText(
        "This book's covers changed since the page loaded. Nothing was changed.",
      ),
    ).toBeVisible()
    expect(screen.getByRole('button', { name: 'Reload' })).toBeVisible()
    await user.click(screen.getByRole('button', { name: /Second Book/u }))
    const second = await screen.findByRole('radiogroup', {
      name: 'Which cover should this book use?',
    })
    await user.click(within(second).getByRole('radio', { name: /Open Library cover/u }))
    expect(await screen.findByText('Switched Second Book.')).toBeVisible()
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
    const group = await screen.findByRole('radiogroup', {
      name: 'Which cover should this book use?',
    })
    await user.click(within(group).getByRole('radio', { name: /Open Library cover/u }))
    expect(
      await screen.findByText(
        'No answer came back. Check whether the cover was changed before choosing again.',
      ),
    ).toBeVisible()
  })

  it('lists automatic switches with Before and Now, and Keep records the keep', async () => {
    const user = userEvent.setup()
    const at = Math.floor(Date.now() / 1000) - 7200
    const switched = page('switched', [
      work('case-1', 'First Book', 'auto', {
        switched: {
          at,
          before: {
            ...preview('b', 500, 500),
            url: `/api/v1/covers/replaced?plan=p&preview=${'b'.repeat(64)}`,
          },
          filled: false,
        },
      }),
      work('case-2', 'Second Book', 'auto', {
        switched: { at, before: null, filled: true },
      }),
    ])
    const http = coversHttp({ switched })
    renderRoute('/library?lens=all&view=covers&tab=switched', http)
    const rows = await screen.findAllByRole('listitem')
    const first = rows[0]
    if (!first) throw new Error('a switched row is required')
    expect(within(first).getByText('Before')).toBeVisible()
    expect(within(first).getByText('500 × 500 px')).toBeVisible()
    expect(within(first).getByText('Now')).toBeVisible()
    expect(within(first).getByText('600 × 900 px')).toBeVisible()
    expect(within(first).getByText(/^Switched automatically, (today|yesterday) at /u)).toBeVisible()
    expect(within(first).getByRole('link', { name: 'First Book' })).toHaveAttribute(
      'href',
      '/library?lens=all&preview=case-1',
    )
    // Put back lands with #1363 PR 4; until then a switch offers Keep and Others.
    expect(within(first).queryByRole('button', { name: 'Put back' })).toBeNull()
    expect(within(first).getByRole('button', { name: 'Others' })).toBeVisible()
    const fill = rows[1]
    if (!fill) throw new Error('a fill row is required')
    expect(within(fill).getByRole('img', { name: 'Before: Second Book' })).toHaveTextContent(
      'No cover',
    )
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
    expect(within(first).getByRole('button', { name: 'Change' })).toBeVisible()
  })

  it.each([
    ['decide', 'No covers to decide.'],
    ['kept', 'No kept covers with other choices.'],
    ['switched', 'No covers switched automatically.'],
  ] as const)('says when %s has no covers', async (tab, sentence) => {
    renderRoute(`/library?lens=all&view=covers&tab=${tab}`, coversHttp({}))
    expect(await screen.findByText(sentence)).toBeVisible()
  })

  it('reads the next page of 50 with Show more covers', async () => {
    const user = userEvent.setup()
    const first = page('all', [work('case-1', 'First Book', 'kept')], 'cursor-2')
    const second = page('all', [work('case-2', 'Second Book', 'none')])
    const http = coversHttp({})
    http.getCovers.mockImplementation(async (view, cursor) =>
      cursor === 'cursor-2' ? second : view === 'all' ? first : page(view, []),
    )
    renderRoute('/library?lens=all&view=covers&tab=all', http)
    await user.click(await screen.findByRole('button', { name: 'Show more covers' }))
    expect(await screen.findByText('One cover')).toBeVisible()
    expect(http.getCovers).toHaveBeenCalledWith('all', 'cursor-2', expect.anything())
    expect(screen.queryByRole('button', { name: 'Show more covers' })).toBeNull()
  })

  it('opens a book from the wall beneath it, with no link back to Covers', async () => {
    const user = userEvent.setup()
    const http = coversHttp({ all: page('all', [work('case-1', 'First Book', 'kept')]) })
    renderRoute('/library?lens=all&view=covers&tab=all', http)
    await user.click(await screen.findByRole('button', { name: /First Book/u }))
    expect(
      await screen.findByRole('radiogroup', { name: 'Which cover should this book use?' }),
    ).toBeVisible()
    expect(screen.queryByRole('link', { name: 'Covers' })).toBeNull()
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
