import { act, cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { ApiError, UncertainMutationError } from '../src/api/http'
import { coalesceMilliseconds } from '../src/liveInvalidation'
import type {
  CaseDetailView,
  CoverTab,
  CoversView,
  CoverWorkView,
  ReviewHttp,
  TasteProfileView,
} from '../src/api/http'
import {
  createHttp,
  createLiveStream,
  detail,
  idlePreparation,
  library,
  recordedAcknowledgement,
  renderRoute,
} from './fixtures'

afterEach(cleanup)

const base = detail.fields[0]
if (!base) throw new Error('the cover fixture needs a field')
const candidate = (suffix: string, incumbent: boolean) => ({
  artifactId: `cover_${suffix.repeat(64)}`,
  candidateKind: 'candidate' as const,
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
// The original an automatic switch replaced, offered back (#1363).
const retained = (selectable = true) => ({
  ...candidate('4', false),
  candidateKind: 'retained' as const,
  deterministicRank: null,
  deterministicRankKey: null,
  identity: null,
  matchState: 'match_not_confirmed' as const,
  ordinal: 2,
  selectable,
  manualEligible: selectable,
  sourceKinds: ['retained' as const],
  state: selectable ? ('available' as const) : ('excluded' as const),
  width: 500,
  height: 500,
})
const withRetained = (value: CaseDetailView, selectable = true): CaseDetailView => ({
  ...value,
  fields: value.fields.map((field) =>
    field.coverConsideration
      ? {
          ...field,
          coverConsideration: {
            ...field.coverConsideration,
            candidates: [...field.coverConsideration.candidates, retained(selectable)],
          },
        }
      : field,
  ),
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
  hold: null,
  suggestion: null,
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
// One connected library, so an empty All has nothing to name.
const library0 = {
  blocked: 0,
  recorded: 0,
  not_matched: 0,
  checked: 0,
  connection: 'primary',
  library: 'books',
  library_id: 'books',
  ready: 0,
  rescan_requested: false,
  scan_id: null,
  state: 'idle' as const,
  total: 0,
  stages: [],
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

  it('says why a cover that contradicts the book cannot be chosen (#1790)', async () => {
    const user = userEvent.setup()
    const http = coversHttp({ decide })
    http.getCaseDetail.mockImplementation(async (id) => {
      const value = coverDetail(id, titles[id] ?? id)
      return {
        ...value,
        fields: value.fields.map((field) =>
          field.coverConsideration
            ? {
                ...field,
                coverConsideration: {
                  ...field.coverConsideration,
                  candidates: [
                    ...field.coverConsideration.candidates,
                    {
                      ...candidate('5', false),
                      contradiction: 'edition' as const,
                      deterministicRank: null,
                      deterministicRankKey: null,
                      identity: {
                        outcome: 'conflict',
                        reasons: ['manifestation_publication_incompatible'],
                      },
                      manualEligible: false,
                      matchState: 'match_not_confirmed' as const,
                      ordinal: 2,
                      selectable: false,
                      sourceKinds: ['hardcover' as const],
                      state: 'excluded' as const,
                    },
                  ],
                },
              }
            : field,
        ),
      }
    })
    renderRoute('/library?lens=all&view=covers&tab=decide&cover=case-1', http)
    const group = await screen.findByRole('radiogroup', {
      name: 'Which cover should this book use?',
    })
    const card = within(group).getByRole('radio', { name: /Hardcover cover/u })
    expect(card.querySelector('strong')).toHaveTextContent(/^Contradicts this book$/u)
    expect(card.querySelector('.choice-recommendation')).toHaveTextContent(
      /^A source says it belongs to a different edition, so you cannot choose it\.$/u,
    )
    expect(card).not.toHaveTextContent('Match not confirmed')
    expect(card).toBeDisabled()
    await user.click(card)
    expect(http.recordDecision).not.toHaveBeenCalled()
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

  it('records a late acknowledgement without leaving the book opened since', async () => {
    const user = userEvent.setup()
    const http = coversHttp({
      decide: page('decide', [...decide.items, work('case-3', 'choice')]),
    })
    const recorded = http.recordDecision.getMockImplementation()
    if (!recorded) throw new Error('a decision double is required')
    let release: () => void = () => undefined
    const gate = new Promise<void>((resolve) => {
      release = resolve
    })
    http.recordDecision.mockImplementationOnce(async (...args) => {
      await gate
      return recorded(...args)
    })
    const { router } = renderRoute('/library?lens=all&view=covers&tab=decide&cover=case-1', http)
    await chooseOther(user)
    await waitFor(() => expect(http.recordDecision).toHaveBeenCalledOnce())
    await user.click(screen.getByRole('button', { name: /^Third Book/u }))
    await waitFor(() => expect(router.state.location.search.cover).toBe('case-3'))
    await act(async () => {
      release()
      await gate
    })
    expect(await screen.findByText('Switched First Book.')).toBeVisible()
    expect(router.state.location.search.cover).toBe('case-3')
    expect(screen.getByRole('heading', { level: 2, name: 'Third Book' })).toBeVisible()
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
          put_back: null,
          put_back_offered: true,
          language: null,
          reason: null,
        },
      }),
      work('case-2', 'auto', {
        switched: {
          at,
          before: null,
          filled: true,
          put_back: null,
          put_back_offered: false,
          language: null,
          reason: null,
        },
      }),
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
    // A replaced cover offers Put back beside Keep and Others (#1363).
    expect(within(first).getByRole('button', { name: 'Put back' })).toHaveAccessibleDescription(
      'First Book',
    )
    expect(within(first).getByRole('button', { name: 'Others' })).toHaveAccessibleDescription(
      'First Book',
    )
    // A fill says "No cover" once, as its Before caption.
    const fill = rows[1]
    if (!fill) throw new Error('a fill row is required')
    expect(within(fill).getAllByText('No cover')).toHaveLength(1)
    expect(within(fill).queryByRole('img', { name: 'Previous cover' })).toBeNull()
    // A fill replaced nothing, so there is nothing to put back.
    expect(within(fill).queryByRole('button', { name: 'Put back' })).toBeNull()
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

  it('says Image unavailable when a replaced cover has no stored preview', async () => {
    const at = Math.floor(Date.now() / 1000) - 7200
    const switched = page('switched', [
      work('case-1', 'auto', {
        switched: {
          at,
          before: null,
          filled: false,
          put_back: null,
          put_back_offered: false,
          language: null,
          reason: null,
        },
      }),
    ])
    renderRoute('/library?lens=all&view=covers&tab=switched', coversHttp({ switched }))
    const [row] = await screen.findAllByRole('listitem')
    if (!row) throw new Error('a switched row is required')
    expect(within(row).getByText('Image unavailable')).toBeVisible()
    expect(within(row).queryByText('No cover')).toBeNull()
  })

  it('reads Covers again on a library update and keeps the decided book in place', async () => {
    const user = userEvent.setup()
    const updates = createLiveStream()
    const http = coversHttp({ decide })
    renderRoute('/library?lens=all&view=covers&tab=decide&cover=case-1', http, updates)
    await chooseOther(user)
    expect(await screen.findByText('Switched First Book.')).toBeVisible()
    // The server no longer lists the decided book, and a new one has arrived.
    http.getCovers.mockResolvedValue(
      page('decide', [work('case-2', 'choice'), work('case-3', 'choice')]),
    )
    await act(async () => {
      updates.announce({ sequence: 5, subject: 'case-1', topic: 'library' })
      await new Promise((resolve) => setTimeout(resolve, coalesceMilliseconds + 20))
    })
    const choose = screen.getByRole('region', { name: 'Choose' })
    expect(await within(choose).findByText('Third Book')).toBeVisible()
    expect(within(choose).getByText('Second Book')).toBeVisible()
    // The first read is the owner's; a live update reads again as background work.
    expect(http.getCovers.mock.calls[0]?.[3]).toBe('foreground')
    expect(http.getCovers.mock.calls.at(-1)?.[3]).toBe('background')
    const suggested = screen.getByRole('region', { name: 'Switch suggested' })
    expect(within(suggested).getByText('First Book')).toBeVisible()
    expect(within(suggested).getByText('Reviewed')).toBeVisible()
  })

  it('reads Covers again when a polled snapshot changes while the stream is down', async () => {
    const user = userEvent.setup()
    const http = coversHttp({ decide })
    const { queryClient } = renderRoute(
      '/library?lens=all&view=covers&tab=decide&cover=case-1',
      http,
    )
    await chooseOther(user)
    expect(await screen.findByText('Switched First Book.')).toBeVisible()
    http.getCovers.mockResolvedValue(
      page('decide', [work('case-2', 'choice'), work('case-3', 'choice')]),
    )
    http.getPreparation = vi.fn(async () => ({ ...idlePreparation, libraries: [library0] }))
    await act(() => queryClient.refetchQueries({ queryKey: ['review-preparation'] }))
    const choose = screen.getByRole('region', { name: 'Choose' })
    expect(await within(choose).findByText('Third Book')).toBeVisible()
    const suggested = screen.getByRole('region', { name: 'Switch suggested' })
    expect(within(suggested).getByText('Reviewed')).toBeVisible()
  })

  it('chooses again after Keep with the case Keep confirmed, without Reload', async () => {
    const user = userEvent.setup()
    const switched = page('switched', [
      work('case-1', 'auto', {
        switched: {
          at: Math.floor(Date.now() / 1000) - 60,
          before: preview('b'),
          filled: false,
          put_back: null,
          put_back_offered: true,
          language: null,
          reason: null,
        },
      }),
    ])
    const http = coversHttp({ switched })
    // The server holds one selection at a time: each decision must name the
    // one the last decision left, or it is refused as stale.
    let selection: string | null = null
    http.recordDecision.mockImplementation(async (caseId, command) => {
      if (command.coverConfirmation?.expected_selection_id !== selection)
        throw new ApiError(409, 'stale')
      selection = `selection-${String(http.recordDecision.mock.calls.length)}`
      const before = coverDetail(caseId, titles[caseId] ?? caseId)
      return recordedAcknowledgement(
        {
          ...before,
          fields: before.fields.map((field) => ({ ...field, currentCoverSelectionId: selection })),
        },
        {
          invalidation_sequences: [],
          schema_version: 'review-decision-v1',
          actor: 'owner',
          application: 'apply' as const,
          application_reason: null,
          decision_id: 'decision-1',
          destination: command.destination,
          occurred_at: 1,
          outcome: command.outcome,
          proposal_id: command.proposalId,
          review_case_id: caseId,
          selected_value: command.selectedValue ?? null,
          target_fingerprint: command.targetFingerprint,
        },
      )
    })
    renderRoute('/library?lens=all&view=covers&tab=switched', http)
    const [row] = await screen.findAllByRole('listitem')
    if (!row) throw new Error('a switched row is required')
    await user.click(within(row).getByRole('button', { name: 'Keep' }))
    await user.click(await within(row).findByRole('button', { name: 'Change' }))
    await chooseOther(user)
    await waitFor(() => expect(http.recordDecision).toHaveBeenCalledTimes(2))
    expect(http.recordDecision.mock.calls[1]?.[1].coverConfirmation).toMatchObject({
      expected_selection_id: 'selection-1',
    })
    expect(await screen.findByText('Changed First Book.')).toBeVisible()
    expect(screen.queryByRole('button', { name: 'Reload' })).toBeNull()
  })

  it('tells an unreadable cover from a missing one', async () => {
    const http = coversHttp({
      decide: page('decide', [
        work('case-1', 'choice', { cover: null, unreadable_cover: true }),
        work('case-2', 'choice', { cover: null }),
      ]),
    })
    renderRoute('/library?lens=all&view=covers', http)
    // The thumbnail shows the state; a screen reader hears it after the title.
    const unreadable = await screen.findByRole('button', {
      name: /^First Book.*Image unavailable/u,
    })
    // The 3rem thumbnail is too small for the words: it shows the frame only.
    expect(within(unreadable).getAllByText('Image unavailable')).toHaveLength(1)
    expect(unreadable.querySelector('.covers-image-missing')).toBeEmptyDOMElement()
    expect(screen.getByRole('button', { name: /^Second Book.*No cover/u })).toBeVisible()
  })

  it.each([
    ['decide', 'No covers to decide.'],
    ['kept', 'No kept covers with other choices.'],
    ['switched', 'No covers switched automatically.'],
    ['all', 'No works yet.'],
  ] as const)('says when %s has no covers', async (tab, sentence) => {
    const http = coversHttp({})
    http.getPreparation = vi.fn(async () => ({ ...idlePreparation, libraries: [library0] }))
    renderRoute(`/library?lens=all&view=covers&tab=${tab}`, http)
    expect(await screen.findByText(sentence)).toBeVisible()
  })

  it('names Settings when All is empty because no library is connected', async () => {
    renderRoute('/library?lens=all&view=covers&tab=all', coversHttp({}))
    const empty = await screen.findByText(/^No works yet\. Add a library in/u)
    expect(empty).toHaveTextContent('No works yet. Add a library in Settings.')
    expect(within(empty).getByRole('link', { name: 'Settings' })).toHaveAttribute(
      'href',
      '/settings?group=libraries',
    )
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
    // The next book opens and takes focus first, as the owner sees it, so
    // Show more's focus is not raced by that book's read on a slow runner.
    const next = await screen.findByRole('heading', { level: 2, name: 'Second Book' })
    await waitFor(() => expect(next).toHaveFocus())
    await user.click(screen.getByRole('button', { name: 'Show more covers' }))
    const added = await screen.findByRole('button', { name: /^Third Book/u })
    // The next page resumes after the last book shown, wherever decisions moved
    // the books before it (#1633 review B1).
    expect(http.getCovers).toHaveBeenCalledWith(
      'decide',
      { cursor: 'cursor-2' },
      expect.anything(),
      'foreground',
    )
    await waitFor(() => expect(added).toHaveFocus())
    expect(screen.getByRole('button', { name: /^First Book.*Reviewed$/u })).toBeVisible()
    expect(screen.queryByRole('button', { name: 'Show more covers' })).toBeNull()
  })

  it('counts from the newest page after Show more covers', async () => {
    const user = userEvent.setup()
    const http = coversHttp({})
    const first = page('all', [work('case-1', 'kept')], 'cursor-2')
    const second = page('all', [work('case-2', 'kept')], null, { ...counts, kept: 3, all: 7 })
    http.getCovers.mockImplementation(async (view, at) =>
      at?.cursor === 'cursor-2' ? second : view === 'all' ? first : page(view, []),
    )
    renderRoute('/library?lens=all&view=covers&tab=all', http)
    const tabs = await screen.findByRole('navigation', { name: 'Cover views' })
    expect(await within(tabs).findByRole('button', { name: 'All 5' })).toBeVisible()
    await user.click(screen.getByRole('button', { name: 'Show more covers' }))
    expect(await within(tabs).findByRole('button', { name: 'All 7' })).toBeVisible()
    expect(within(tabs).getByRole('button', { name: 'Kept 3' })).toBeVisible()
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

  it('places the open book after the last tile of its row, measured without the panel', async () => {
    const user = userEvent.setup()
    // Two tiles a row: the panel would split the row if it were measured in place.
    const descriptor = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'offsetTop')
    Object.defineProperty(HTMLElement.prototype, 'offsetTop', {
      configurable: true,
      get(this: HTMLElement) {
        const wall = this.parentElement
        if (!this.classList.contains('covers-tile') || !wall) return 0
        const tiles = [...wall.children]
        const index = tiles.indexOf(this)
        // A panel in flow takes a row of its own and pushes later tiles down.
        const panel = tiles.findIndex((child) => child.classList.contains('covers-wall-panel'))
        const slot = panel >= 0 && index > panel ? index + 2 - (index % 2) : index
        return Math.floor(slot / 2) * 100
      },
    })
    try {
      const http = coversHttp({
        all: page('all', [work('case-1', 'kept'), work('case-2', 'none'), work('case-3', 'kept')]),
      })
      renderRoute('/library?lens=all&view=covers&tab=all', http)
      await user.click(await screen.findByRole('button', { name: /First Book/u }))
      const panel = (
        await screen.findByRole('radiogroup', { name: 'Which cover should this book use?' })
      ).closest('li')
      await waitFor(() => expect(panel?.previousElementSibling).toHaveTextContent('Second Book'))
      expect(panel?.nextElementSibling).toHaveTextContent('Third Book')
      // A resize measures the row again, the same way.
      fireEvent(window, new Event('resize'))
      await waitFor(() =>
        expect(
          document.querySelector('.covers-wall-panel')?.previousElementSibling,
        ).toHaveTextContent('Second Book'),
      )
    } finally {
      if (descriptor) Object.defineProperty(HTMLElement.prototype, 'offsetTop', descriptor)
    }
  })

  it('shows only the newest count read when an older one answers late', async () => {
    const user = userEvent.setup()
    const http = coversHttp({ decide })
    let releaseFirst: (page: CoversView) => void = () => undefined
    const late = new Promise<CoversView>((resolve) => {
      releaseFirst = resolve
    })
    http.getCovers
      .mockResolvedValueOnce(decide)
      .mockReturnValueOnce(late)
      .mockResolvedValueOnce(page('decide', [...decide.items], null, { ...counts, decide: 0 }))
    renderRoute('/library?lens=all&view=covers&cover=case-1', http)
    await chooseOther(user)
    expect(await screen.findByText('Switched First Book.')).toBeVisible()
    await chooseOther(user)
    expect(await screen.findByText('Switched Second Book.')).toBeVisible()
    const tabs = screen.getByRole('navigation', { name: 'Cover views' })
    expect(await within(tabs).findByRole('button', { name: 'To decide 0' })).toBeVisible()
    releaseFirst(page('decide', [...decide.items], null, { ...counts, decide: 1 }))
    await late
    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(within(tabs).getByRole('button', { name: 'To decide 0' })).toBeVisible()
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
    expect(http.getCovers).toHaveBeenCalledWith(
      'all',
      { cover: 'case-77' },
      expect.anything(),
      'foreground',
    )
  })

  it('offers Reload when a linked book past the loaded pages cannot be read', async () => {
    const user = userEvent.setup()
    const http = coversHttp({})
    let failing = true
    http.getCovers.mockImplementation(async (view, at) => {
      if (at?.cover !== 'case-77') return page(view, [work('case-1', 'kept')], 'cursor-2')
      if (failing) throw new ApiError(503, 'unavailable')
      return page(view, [work('case-77', 'kept')])
    })
    renderRoute('/library?lens=all&view=covers&tab=all&cover=case-77', http)
    const alert = await screen.findByRole('alert')
    failing = false
    await user.click(within(alert).getByRole('button', { name: 'Reload' }))
    expect(await screen.findByRole('heading', { level: 2, name: 'Late Book' })).toBeVisible()
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('tries a new preview for the same book after an earlier one failed to load', async () => {
    const updates = createLiveStream()
    const http = coversHttp({ all: page('all', [work('case-1', 'kept')]) })
    renderRoute('/library?lens=all&view=covers&tab=all', http, updates)
    await screen.findByText('First Book')
    const image = document.querySelector('.covers-list img')
    if (!image) throw new Error('a cover image is required')
    fireEvent.error(image)
    expect(document.querySelector('.covers-list img')).toBeNull()
    http.getCovers.mockResolvedValue(page('all', [work('case-1', 'kept', { cover: preview('c') })]))
    await act(async () => {
      updates.announce({ sequence: 5, subject: 'case-1', topic: 'library' })
      await new Promise((resolve) => setTimeout(resolve, coalesceMilliseconds + 20))
    })
    await waitFor(() =>
      expect(document.querySelector('.covers-list img')).toHaveAttribute('src', preview('c').url),
    )
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

describe('Put back (#1363)', () => {
  const switchedRow = (
    put_back: NonNullable<CoverWorkView['switched']>['put_back'] = null,
    put_back_offered = true,
  ) =>
    page('switched', [
      work('case-1', 'auto', {
        switched: {
          at: Math.floor(Date.now() / 1000) - 60,
          before: preview('b', 500, 500),
          filled: false,
          put_back,
          put_back_offered,
          language: null,
          reason: null,
        },
      }),
    ])

  it('puts the replaced original back through the book decision and lands on Change', async () => {
    const user = userEvent.setup()
    const http = coversHttp({ switched: switchedRow() })
    http.getCaseDetail.mockImplementation(async (id) =>
      withRetained(coverDetail(id, titles[id] ?? id)),
    )
    renderRoute('/library?lens=all&view=covers&tab=switched', http)
    const [row] = await screen.findAllByRole('listitem')
    if (!row) throw new Error('a switched row is required')
    await user.click(within(row).getByRole('button', { name: 'Put back' }))
    await waitFor(() => expect(http.recordDecision).toHaveBeenCalledOnce())
    expect(http.recordDecision.mock.calls[0]?.[1]).toMatchObject({
      destination: 'cover',
      outcome: 'accept',
      selectedValue: { kind: 'cover', value: `cover_${'4'.repeat(64)}` },
      considerationId: 'cover_consideration_case-1',
      // The original carries no match claim, so the choice itself confirms it.
      coverConfirmation: { expected_selection_id: null, confirm_unverified_match: true },
    })
    expect(await screen.findByText('Put back First Book.')).toBeVisible()
    expect(within(row).getByText('Put back')).toBeVisible()
    await waitFor(() => expect(within(row).getByRole('button', { name: 'Change' })).toHaveFocus())
  })

  it('puts back an original that does not decode, shown as Image unavailable', async () => {
    // The decoder refused the original, so it has no preview anywhere (#1362).
    const user = userEvent.setup()
    const http = coversHttp({
      switched: page('switched', [
        work('case-1', 'auto', {
          switched: {
            at: Math.floor(Date.now() / 1000) - 60,
            before: null,
            filled: false,
            put_back: null,
            put_back_offered: true,
            language: null,
            reason: null,
          },
        }),
      ]),
    })
    http.getCaseDetail.mockImplementation(async (id) => {
      const value = withRetained(coverDetail(id, titles[id] ?? id))
      return {
        ...value,
        fields: value.fields.map((field) =>
          field.coverConsideration
            ? {
                ...field,
                coverConsideration: {
                  ...field.coverConsideration,
                  candidates: field.coverConsideration.candidates.map((item) =>
                    item.candidateKind === 'retained'
                      ? { ...item, height: null, previewSha256: null, width: null }
                      : item,
                  ),
                },
              }
            : field,
        ),
      }
    })
    renderRoute('/library?lens=all&view=covers&tab=switched', http)
    const [row] = await screen.findAllByRole('listitem')
    if (!row) throw new Error('a switched row is required')
    expect(within(row).getByRole('img', { name: 'Previous cover' })).toHaveTextContent(
      'Image unavailable',
    )
    expect(within(row).queryByText('No cover')).toBeNull()
    await user.click(within(row).getByRole('button', { name: 'Put back' }))
    await waitFor(() => expect(http.recordDecision).toHaveBeenCalledOnce())
    expect(http.recordDecision.mock.calls[0]?.[1]).toMatchObject({
      outcome: 'accept',
      selectedValue: { kind: 'cover', value: `cover_${'4'.repeat(64)}` },
      coverConfirmation: { expected_selection_id: null, confirm_unverified_match: true },
    })
    expect(await screen.findByText('Put back First Book.')).toBeVisible()
  })

  it('refuses as stale when the original is no longer offered, recording nothing', async () => {
    const user = userEvent.setup()
    const http = coversHttp({ switched: switchedRow() })
    http.getCaseDetail.mockImplementation(async (id) =>
      withRetained(coverDetail(id, titles[id] ?? id), false),
    )
    renderRoute('/library?lens=all&view=covers&tab=switched', http)
    const [row] = await screen.findAllByRole('listitem')
    if (!row) throw new Error('a switched row is required')
    await user.click(within(row).getByRole('button', { name: 'Put back' }))
    expect(
      await within(row).findByText(
        "This book's covers changed since the page loaded. Nothing was changed.",
      ),
    ).toBeVisible()
    expect(http.recordDecision).not.toHaveBeenCalled()
  })

  it('offers no Put back before the book is prepared again after its switch', async () => {
    // The comparison has no original yet, so Put back could not be recorded.
    const http = coversHttp({ switched: switchedRow(null, false) })
    renderRoute('/library?lens=all&view=covers&tab=switched', http)
    const [row] = await screen.findAllByRole('listitem')
    if (!row) throw new Error('a switched row is required')
    expect(within(row).queryByRole('button', { name: 'Put back' })).toBeNull()
    expect(within(row).getByRole('button', { name: 'Keep' })).toBeVisible()
    expect(within(row).getByRole('button', { name: 'Others' })).toBeVisible()
  })

  it('reads a running Put back as put back', async () => {
    const http = coversHttp({ switched: switchedRow({ state: 'applying', failure: null }) })
    renderRoute('/library?lens=all&view=covers&tab=switched', http)
    const [row] = await screen.findAllByRole('listitem')
    if (!row) throw new Error('a switched row is required')
    expect(within(row).getByText('Put back')).toBeVisible()
    expect(within(row).queryByRole('button', { name: 'Put back' })).toBeNull()
    expect(within(row).queryByRole('button', { name: 'Keep' })).toBeNull()
    expect(within(row).getByRole('button', { name: 'Change' })).toBeVisible()
  })

  it('says why nothing was put back when the cover changed in Audiobookshelf', async () => {
    const http = coversHttp({
      switched: switchedRow({ state: 'stale', failure: 'target_changed' }),
    })
    renderRoute('/library?lens=all&view=covers&tab=switched', http)
    const [row] = await screen.findAllByRole('listitem')
    if (!row) throw new Error('a switched row is required')
    const refusal = within(row).getByText(
      'The cover was changed in Audiobookshelf, so it was not put back.',
    )
    expect(refusal).toBeVisible()
    // Announced if it arrives with a live update while the row is open.
    expect(refusal).toHaveAttribute('role', 'status')
    expect(within(row).queryByRole('button', { name: 'Put back' })).toBeNull()
    expect(within(row).getByRole('button', { name: 'Keep' })).toBeVisible()
  })

  it('drops its own Put back mark when a live update reports the write refused', async () => {
    const user = userEvent.setup()
    const updates = createLiveStream()
    const http = coversHttp({ switched: switchedRow() })
    http.getCaseDetail.mockImplementation(async (id) =>
      withRetained(coverDetail(id, titles[id] ?? id)),
    )
    renderRoute('/library?lens=all&view=covers&tab=switched', http, updates)
    const [row] = await screen.findAllByRole('listitem')
    if (!row) throw new Error('a switched row is required')
    await user.click(within(row).getByRole('button', { name: 'Put back' }))
    expect(await screen.findByText('Put back First Book.')).toBeVisible()
    expect(within(row).getByRole('button', { name: 'Change' })).toBeVisible()
    // The write then finds the cover changed in Audiobookshelf first.
    http.getCovers.mockResolvedValue(switchedRow({ state: 'stale', failure: 'target_changed' }))
    await act(async () => {
      updates.announce({ sequence: 5, subject: 'case-1', topic: 'library' })
      await new Promise((resolve) => setTimeout(resolve, coalesceMilliseconds + 20))
    })
    const [refusedRow] = await screen.findAllByRole('listitem')
    if (!refusedRow) throw new Error('a switched row is required')
    expect(
      await within(refusedRow).findByText(
        'The cover was changed in Audiobookshelf, so it was not put back.',
      ),
    ).toHaveAttribute('role', 'status')
    // Neither the Put back mark nor its button remains.
    expect(within(refusedRow).queryByText('Put back')).toBeNull()
    expect(within(refusedRow).queryByRole('button', { name: 'Change' })).toBeNull()
    expect(within(refusedRow).queryByRole('button', { name: 'Put back' })).toBeNull()
    expect(within(refusedRow).getByRole('button', { name: 'Keep' })).toBeVisible()
    expect(within(refusedRow).getByRole('button', { name: 'Others' })).toBeVisible()
  })

  it('labels the original Replaced cover in the comparison, with no match claim', async () => {
    const user = userEvent.setup()
    const http = coversHttp({ switched: switchedRow() })
    http.getCaseDetail.mockImplementation(async (id) =>
      withRetained(coverDetail(id, titles[id] ?? id)),
    )
    renderRoute('/library?lens=all&view=covers&tab=switched', http)
    const [row] = await screen.findAllByRole('listitem')
    if (!row) throw new Error('a switched row is required')
    await user.click(within(row).getByRole('button', { name: 'Others' }))
    const group = await screen.findByRole('radiogroup', {
      name: 'Which cover should this book use?',
    })
    const card = within(group).getByRole('radio', { name: 'Choose Replaced cover' })
    expect(within(card).getByText('Replaced cover')).toBeVisible()
    expect(within(card).queryByText('Match not confirmed')).toBeNull()
    await user.click(card)
    await waitFor(() => expect(http.recordDecision).toHaveBeenCalledOnce())
    expect(http.recordDecision.mock.calls[0]?.[1].selectedValue).toEqual({
      kind: 'cover',
      value: `cover_${'4'.repeat(64)}`,
    })
    expect(await screen.findByText('Put back First Book.')).toBeVisible()
  })
})

describe('Measured cover rules (#1363)', () => {
  const at = Math.floor(Date.now() / 1000) - 60
  const switchedFor = (
    id: string,
    reason: NonNullable<CoverWorkView['switched']>['reason'],
    extra: Partial<CoverWorkView> = {},
  ): CoverWorkView =>
    work(id, 'auto', {
      switched: {
        at,
        before: reason === null ? null : preview('b', 200, 300),
        filled: reason === null,
        put_back: null,
        put_back_offered: false,
        language: null,
        reason,
      },
      ...extra,
    })

  it('names the fault and says why each switch happened', async () => {
    const switched = page('switched', [
      switchedFor('case-1', 'below_floor'),
      switchedFor('case-2', 'wrong_shape', { media_kind: 'm4b' }),
    ])
    renderRoute('/library?lens=all&view=covers&tab=switched', coversHttp({ switched }))
    const [first, second] = await screen.findAllByRole('listitem')
    if (!first || !second) throw new Error('two switched rows are required')
    expect(within(first).getByText('Under 300 px')).toBeVisible()
    expect(
      within(first).getByText('The cover was 200 × 300 px, under 300 px on its short side.'),
    ).toBeVisible()
    // The shape fault names the medium it is wrong for.
    expect(within(second).getByText('Book shape on an audiobook')).toBeVisible()
    expect(
      within(second).getByText('Square art for an audiobook whose cover was book-shaped.'),
    ).toBeVisible()
  })

  it('filters switches by fault, showing fills only under All', async () => {
    const user = userEvent.setup()
    const switched = page('switched', [
      switchedFor('case-1', 'below_floor'),
      switchedFor('case-2', null),
      switchedFor('case-3', 'sharper_copy'),
    ])
    renderRoute('/library?lens=all&view=covers&tab=switched', coversHttp({ switched }))
    expect(await screen.findAllByRole('listitem')).toHaveLength(3)
    const show = screen.getByRole('combobox', { name: 'Show' })
    expect(show).toHaveTextContent('All')
    await user.click(show)
    // Only the faults present, in the copy table's order; a fill has none.
    expect(screen.getAllByRole('option').map((option) => option.textContent)).toEqual([
      'All',
      'Sharper copy available',
      'Under 300 px',
    ])
    await user.click(screen.getByRole('option', { name: 'Sharper copy available' }))
    const [only, ...rest] = screen.getAllByRole('listitem')
    if (!only) throw new Error('a shown row is required')
    expect(rest).toHaveLength(0)
    expect(within(only).getByRole('link', { name: 'Third Book' })).toBeVisible()
  })

  it('shows a suggestion To decide with its fault and no filter', async () => {
    const decidePage = page('decide', [
      work('case-1', 'proposal', { suggestion: { reason: 'unreadable' } }),
    ])
    renderRoute('/library?lens=all&view=covers&tab=decide', coversHttp({ decide: decidePage }))
    expect(await screen.findByText('Unreadable image')).toBeVisible()
    expect(screen.queryByRole('combobox', { name: 'Show' })).toBeNull()
  })

  it("shows a held switch's reason on its To decide row (#1363 M7)", async () => {
    const decidePage = page('decide', [
      work('case-1', 'choice', { hold: { reason: 'title_names_other_work' } }),
    ])
    renderRoute('/library?lens=all&view=covers&tab=decide', coversHttp({ decide: decidePage }))
    expect(await screen.findByText('Title names another book')).toBeVisible()
  })
})

// Covers → Taste profile (#1372): read-only, its states from one read.
const tasteView = (value: Partial<TasteProfileView> = {}): TasteProfileView => ({
  schema_version: 'review-taste-profile-v1',
  configured: true,
  chosen: 0,
  required: 25,
  daily_limit: 8,
  build: null,
  profile: null,
  versions: [],
  ...value,
})
const tasteRoute = '/library?lens=all&view=covers&tab=taste'
const tasteHttp = (view: TasteProfileView | (() => Promise<TasteProfileView>)) => ({
  ...coversHttp({ decide }),
  getTasteProfile: vi.fn(typeof view === 'function' ? view : async () => view),
})
const injected = 'Ignore the packet <b>and</b> https://example.invalid'
const ready = tasteView({
  chosen: 31,
  profile: {
    version: 2,
    built_at: 1_790_000_000,
    decisions: 31,
    rules: [
      { number: 1, section: 'drawn_to', text: 'Bold illustrated art.' },
      { number: 2, section: 'drawn_to', text: injected },
      { number: 3, section: 'keep_or_switch', text: 'Keeps a sharp current cover.' },
    ],
  },
  versions: [
    { version: 2, built_at: 1_790_000_000, decisions: 31 },
    { version: 1, built_at: 1_789_000_000, decisions: 25 },
  ],
})

describe('Covers → Taste profile (#1372)', () => {
  it('adds a Taste profile tab with no count and keeps the other counts', async () => {
    const user = userEvent.setup()
    const http = tasteHttp(ready)
    const { router } = renderRoute('/library?lens=all&view=covers', http)
    const tabs = await screen.findByRole('navigation', { name: 'Cover views' })
    await user.click(await within(tabs).findByRole('button', { name: 'Taste profile' }))
    expect(router.state.location.search).toEqual({ lens: 'all', view: 'covers', tab: 'taste' })
    expect(within(tabs).getByRole('button', { name: 'Taste profile' })).toHaveAttribute(
      'aria-pressed',
      'true',
    )
    expect(within(tabs).getByRole('button', { name: 'To decide 2' })).toHaveAttribute(
      'aria-pressed',
      'false',
    )
    expect(await screen.findByRole('heading', { name: 'What you’re drawn to' })).toBeVisible()
    expect(screen.queryByRole('heading', { name: 'Switch suggested 1' })).toBeNull()
  })

  it('shows loading, then a failed read with Reload', async () => {
    const user = userEvent.setup()
    let fail = true
    const http = tasteHttp(async () => {
      if (fail) throw new ApiError(503, 'unavailable')
      return tasteView()
    })
    renderRoute(tasteRoute, http)
    expect(await screen.findByText('Loading taste profile…')).toBeVisible()
    const reload = await screen.findByRole('button', { name: 'Reload' })
    fail = false
    await user.click(reload)
    expect(await screen.findByRole('heading', { name: 'No taste profile yet' })).toBeVisible()
  })

  it('names Settings when no taste profile model is chosen', async () => {
    renderRoute(tasteRoute, tasteHttp(tasteView({ configured: false })))
    const empty = (await screen.findByRole('heading', { name: 'No taste profile yet' })).closest(
      'section',
    )
    if (!empty) throw new Error('the empty state is required')
    expect(within(empty).getByRole('link', { name: 'Settings' })).toHaveAttribute(
      'href',
      '/settings?group=reviewer',
    )
    expect(screen.getByText(/Choose a taste profile model in/u)).toBeVisible()
  })

  it.each([0, 1, 24])('says how many covers are chosen, %i of 25', async (chosen) => {
    renderRoute(tasteRoute, tasteHttp(tasteView({ chosen })))
    const line = await screen.findByText(/Mechanical Librarian writes it once you have chosen 25/u)
    expect(line).toHaveTextContent(
      `Mechanical Librarian writes it once you have chosen 25 covers in To decide. You have chosen ${String(chosen)}.`,
    )
    expect(within(line).getByRole('link', { name: 'To decide' })).toBeVisible()
  })

  it('says a build is running', async () => {
    renderRoute(
      tasteRoute,
      tasteHttp(
        tasteView({
          chosen: 25,
          build: {
            build_id: `taste_build_${'a'.repeat(32)}`,
            state: 'building',
            failure: null,
            retry_after: null,
          },
        }),
      ),
    )
    expect(await screen.findByText('Writing your taste profile')).toBeVisible()
  })

  it.each([
    ['unavailable', null, 'OpenRouter did not answer, so the taste profile was not written.'],
    [
      'invalid_output',
      null,
      'The model returned a profile that was too long or incomplete, so it was not kept.',
    ],
    [
      'ceiling',
      86_400,
      /^Mechanical Librarian has asked for 8 taste profiles today, the daily limit\. Try again after \S+\.$/u,
    ],
    [
      'unknown_charge',
      86_400,
      /^OpenRouter did not confirm what the last request cost, so taste profiles wait until \S+\.$/u,
    ],
    ['interrupted', null, 'Writing the taste profile stopped when Mechanical Librarian restarted.'],
  ] as const)('says why %s failed and offers Try again', async (failure, retryAfter, copy) => {
    const user = userEvent.setup()
    const failed = tasteView({
      chosen: 25,
      build: {
        build_id: `taste_build_${'b'.repeat(32)}`,
        state: 'failed',
        failure,
        retry_after: retryAfter,
      },
    })
    const building = tasteView({
      chosen: 25,
      build: {
        build_id: `taste_build_${'c'.repeat(32)}`,
        state: 'building',
        failure: null,
        retry_after: null,
      },
    })
    const http = {
      ...tasteHttp(failed),
      retryTasteBuild: vi.fn(async () => building),
    }
    renderRoute(tasteRoute, http)
    expect(await screen.findByText(copy)).toBeVisible()
    await user.click(screen.getByRole('button', { name: 'Try again' }))
    expect(http.retryTasteBuild).toHaveBeenCalledWith(`taste_build_${'b'.repeat(32)}`)
    const status = await screen.findByText('Writing your taste profile')
    await waitFor(() => expect(status.closest('.taste-build')).toHaveFocus())
  })

  it('shows the profile in use, hides empty sections and renders rule text literally', async () => {
    renderRoute(tasteRoute, tasteHttp(ready))
    expect(await screen.findByRole('heading', { name: 'What you’re drawn to' })).toBeVisible()
    expect(screen.getByRole('heading', { name: 'When to keep or switch' })).toBeVisible()
    expect(screen.queryByRole('heading', { name: 'What you avoid' })).toBeNull()
    expect(screen.queryByRole('heading', { name: 'How you trade them off' })).toBeNull()
    const rule = screen.getByText(injected)
    expect(rule.tagName).toBe('LI')
    expect(rule.querySelector('b')).toBeNull()
    expect(screen.queryByRole('link', { name: /example/u })).toBeNull()
    const meta = screen.getByText('Rules').closest('div')
    expect(meta).toHaveTextContent('Rules3 of 40')
    const versions = screen.getByRole('heading', { name: 'Versions' }).closest('section')
    if (!versions) throw new Error('the Versions section is required')
    const rows = within(versions).getAllByRole('listitem')
    expect(rows.map((row) => row.textContent)).toEqual([
      expect.stringMatching(/^Version 2 \(in use\) Built .+ from 31 decisions$/u),
      expect.stringMatching(/^Version 1 Built .+ from 25 decisions$/u),
    ])
  })
})
