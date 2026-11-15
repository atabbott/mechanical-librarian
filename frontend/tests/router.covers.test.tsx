import { act, cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { ApiError, UncertainMutationError } from '../src/api/http'
import { coalesceMilliseconds, ownerIdleMilliseconds } from '../src/liveInvalidation'
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
  const group = await screen.findByRole('radiogroup', { name: 'Which cover should this work use?' })
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
      name: 'Which cover should this work use?',
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
        "This work's covers changed since the page loaded. Nothing was changed.",
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
        'No answer came back. Check whether your choice was recorded before choosing again.',
      ),
    ).toBeVisible()
  })

  it('says a refused choice was not recorded (#1866)', async () => {
    const user = userEvent.setup()
    const http = coversHttp({ decide })
    http.recordDecision.mockRejectedValueOnce(new ApiError(422, 'invalid_choice'))
    renderRoute('/library?lens=all&view=covers&cover=case-1', http)
    await chooseOther(user)
    expect(await screen.findByRole('alert')).toHaveTextContent(
      /^Your choice was not recorded\. Choose again\.$/u,
    )
    expect(screen.queryByText(/No answer came back/u)).toBeNull()
  })

  // Covers keeps an unread current cover as the book page does (#1865).
  it.each([
    ['a transient read', []],
    ['a missing image', [{ reason: 'source_absent' }]],
    ['refused bytes', [{ reason: 'source_decode_failed', original_digest: 'd'.repeat(64) }]],
  ])('keeps an unread current cover after %s', async (_case, omissions) => {
    const user = userEvent.setup()
    const unread = (id: string): CaseDetailView => {
      const value = coverDetail(id, titles[id] ?? id)
      return {
        ...value,
        fields: value.fields.map((field) =>
          field.coverConsideration
            ? {
                ...field,
                coverConsideration: {
                  ...field.coverConsideration,
                  candidates: [candidate('2', false)],
                  incumbent: { reference: null, state: 'unavailable' as const },
                  omissions,
                },
              }
            : field,
        ),
      }
    }
    let recorded: CaseDetailView | null = null
    const http = coversHttp({ all: page('all', [work('case-1', 'choice')]) })
    http.getCaseDetail.mockImplementation(async (id) => recorded ?? unread(id))
    http.recordDecision.mockImplementation(async (caseId, command) => {
      const acknowledgement = recordedAcknowledgement(unread(caseId), {
        actor: 'owner',
        application: 'apply' as const,
        application_reason: null,
        decision_id: 'decision-1',
        destination: command.destination,
        invalidation_sequences: [],
        schema_version: 'review-decision-v1',
        occurred_at: 1,
        outcome: command.outcome,
        proposal_id: command.proposalId,
        review_case_id: caseId,
        selected_value: command.selectedValue ?? null,
        target_fingerprint: command.targetFingerprint,
      })
      if (acknowledgement.confirmation.status === 'confirmed')
        recorded = acknowledgement.confirmation.case
      return acknowledgement
    })
    renderRoute('/library?lens=all&view=covers&tab=all&cover=case-1', http)
    await user.click(await screen.findByRole('radio', { name: 'Keep current cover' }))
    await waitFor(() => expect(http.recordDecision).toHaveBeenCalledOnce())
    expect(http.recordDecision.mock.calls[0]?.[1]).toMatchObject({
      destination: 'cover',
      outcome: 'keep_current',
      selectedValue: null,
    })
    expect(await screen.findByText('Kept First Book.')).toBeVisible()
    cleanup()
    renderRoute('/library?lens=all&view=covers&tab=all&cover=case-1', http)
    const kept = await screen.findByRole('radio', { name: 'Keep current cover' })
    expect(kept).toHaveTextContent('Your choice')
    expect(kept).toHaveAttribute('aria-checked', 'true')
  })

  it('names a Before or Now frame with no preview for what is missing (#1866)', async () => {
    const at = Math.floor(Date.now() / 1000) - 7200
    const switchedRow = (id: string, extra: Partial<CoverWorkView>) =>
      work(id, 'auto', {
        switched: {
          at,
          before: null,
          filled: false,
          put_back: null,
          put_back_offered: false,
          language: null,
          reason: null,
        },
        ...extra,
      })
    const switched = page('switched', [
      switchedRow('case-1', { cover: null, unreadable_cover: true }),
      switchedRow('case-2', { cover: null }),
    ])
    renderRoute('/library?lens=all&view=covers&tab=switched', coversHttp({ switched }))
    const [unreadable, empty] = await screen.findAllByRole('listitem')
    if (!unreadable || !empty) throw new Error('two switched rows are required')
    expect(
      within(unreadable).getByRole('img', { name: 'Previous cover unavailable' }),
    ).toBeVisible()
    expect(within(unreadable).getByRole('img', { name: 'Current cover unavailable' })).toBeVisible()
    expect(within(unreadable).queryByRole('img', { name: 'Cover now' })).toBeNull()
    expect(within(empty).getByRole('img', { name: 'No cover' })).toHaveTextContent('No cover')
  })

  it('names the current card of a switched comparison as changed automatically (#1866)', async () => {
    const user = userEvent.setup()
    const at = Math.floor(Date.now() / 1000) - 7200
    const switched = page('switched', [
      work('case-1', 'auto', {
        switched: {
          at,
          before: preview('b', 500, 500),
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
    await user.click(within(row).getByRole('button', { name: 'Others' }))
    const group = await screen.findByRole('radiogroup', {
      name: 'Which cover should this work use?',
    })
    // The name says what the visible label says, as the book page's does.
    expect(
      within(group).getByRole('radio', { name: 'Keep current cover · changed automatically' }),
    ).toHaveTextContent('Current cover · changed automatically')
    expect(within(group).queryByRole('radio', { name: 'Keep current cover' })).toBeNull()
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
    // The frame is named for what is missing, not as the picture (#1866).
    expect(within(row).getByRole('img', { name: 'Previous cover unavailable' })).toBeVisible()
    expect(within(row).queryByRole('img', { name: 'Previous cover' })).toBeNull()
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
    // The state is written under the title, read after it (#1866).
    const unreadable = await screen.findByRole('button', {
      name: /^First Book.*Image unavailable/u,
    })
    // The 3rem thumbnail is too small for the words: it shows the frame only.
    expect(within(unreadable).getAllByText('Image unavailable')).toHaveLength(1)
    expect(within(unreadable).getByText('Image unavailable')).toBeVisible()
    expect(within(unreadable).getByText('Image unavailable')).not.toHaveClass('sr-only')
    expect(unreadable.querySelector('.covers-image-missing')).toBeEmptyDOMElement()
    const missing = screen.getByRole('button', { name: /^Second Book.*No cover/u })
    expect(within(missing).getByText('No cover')).toBeVisible()
    expect(within(missing).getByText('No cover').tagName).toBe('SMALL')
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
      await screen.findByRole('radiogroup', { name: 'Which cover should this work use?' })
    ).closest('li')
    // Every tile of the row comes before the panel.
    expect(panel?.previousElementSibling).toHaveTextContent('Third Book')
    expect(screen.queryByRole('link', { name: 'Covers' })).toBeNull()
    await user.click(screen.getByRole('button', { name: 'Close' }))
    await waitFor(() => expect(screen.getByRole('button', { name: /First Book/u })).toHaveFocus())
  })

  it('lists a chosen cover not yet written under All as Your choice (#1866)', async () => {
    const http = coversHttp({
      all: page('all', [work('case-1', 'chosen'), work('case-2', 'kept')]),
    })
    renderRoute('/library?lens=all&view=covers&tab=all', http)
    const chosen = await screen.findByRole('button', { name: /First Book/u })
    const pill = within(chosen).getByText('Your choice')
    expect(pill).toHaveClass('status-pill', 'is-neutral')
    expect(within(chosen).queryByText('Kept')).toBeNull()
    expect(
      within(screen.getByRole('button', { name: /Second Book/u })).getByText('Kept'),
    ).toBeVisible()
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
        await screen.findByRole('radiogroup', { name: 'Which cover should this work use?' })
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

  it('shows Back to works for the open book', async () => {
    const user = userEvent.setup()
    const http = coversHttp({ decide })
    const { router } = renderRoute('/library?lens=all&view=covers&cover=case-1', http)
    await user.click(await screen.findByRole('button', { name: 'Back to works' }))
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
    expect(within(row).getByRole('img', { name: 'Previous cover unavailable' })).toHaveTextContent(
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
        "This work's covers changed since the page loaded. Nothing was changed.",
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
      name: 'Which cover should this work use?',
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

  it('shows a contradiction hold To decide with its own pill (#1362)', async () => {
    const decidePage = page('decide', [
      work('case-1', 'choice', { hold: { reason: 'current_contradicted' } }),
    ])
    renderRoute('/library?lens=all&view=covers&tab=decide', coversHttp({ decide: decidePage }))
    const [row] = await screen.findAllByRole('listitem')
    if (!row) throw new Error('a hold row is required')
    expect(within(row).getByText("Cover's source names another book")).toBeVisible()
    // The pill is its own row of the item, under the title and status, so it
    // is not squeezed into the title column (#1866).
    const pill = within(row).getByText("Cover's source names another book")
    expect(pill).toHaveClass('covers-queue-hold')
    expect(pill.parentElement).toHaveClass('covers-queue-item')
  })

  it('says why an unreadable cover was switched and lists it under Show (#1362)', async () => {
    const user = userEvent.setup()
    const switched = page('switched', [
      switchedFor('case-1', 'unreadable'),
      switchedFor('case-2', 'below_floor'),
    ])
    renderRoute('/library?lens=all&view=covers&tab=switched', coversHttp({ switched }))
    const [first] = await screen.findAllByRole('listitem')
    if (!first) throw new Error('a switched row is required')
    expect(within(first).getByText('Unreadable image')).toBeVisible()
    // A replaced cover was unreadable; a fill had nothing to read (#1866).
    expect(
      within(first).getByText('Mechanical Librarian could not read the previous cover.'),
    ).toBeVisible()
    await user.click(screen.getByRole('combobox', { name: 'Show' }))
    expect(screen.getAllByRole('option').map((option) => option.textContent)).toEqual([
      'All',
      'Under 300 px',
      'Unreadable image',
    ])
  })

  it('says a filled unreadable cover had no image (#1866)', async () => {
    const switched = page('switched', [
      work('case-1', 'auto', {
        switched: {
          at,
          before: null,
          filled: true,
          put_back: null,
          put_back_offered: false,
          language: null,
          reason: 'unreadable',
        },
      }),
    ])
    renderRoute('/library?lens=all&view=covers&tab=switched', coversHttp({ switched }))
    const [row] = await screen.findAllByRole('listitem')
    if (!row) throw new Error('a switched row is required')
    expect(within(row).getByText('Audiobookshelf had no image for the cover.')).toBeVisible()
    expect(within(row).queryByText(/could not read/u)).toBeNull()
  })
})

// Covers → Taste profile (#1372): its states from one read, and the owner's
// edits (PR 2).
const tasteView = (value: Partial<TasteProfileView> = {}): TasteProfileView => ({
  schema_version: 'review-taste-profile-v2',
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
    kind: 'built',
    built_at: 1_790_000_000,
    decisions: 31,
    rules: [
      { number: 1, section: 'drawn_to', text: 'Bold illustrated art.', provenance: 'model' },
      { number: 2, section: 'drawn_to', text: injected, provenance: 'model' },
      {
        number: 3,
        section: 'keep_or_switch',
        text: 'Keeps a sharp current cover.',
        provenance: 'model',
      },
    ],
  },
  versions: [
    { version: 2, kind: 'built', created_at: 1_790_000_000, decisions: 31 },
    { version: 1, kind: 'built', created_at: 1_789_000_000, decisions: 25 },
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
    expect(screen.getByText(/Set up the taste profile model in/u)).toBeVisible()
  })

  it.each([0, 1, 24])('says how many covers are chosen, %i of 25', async (chosen) => {
    renderRoute(tasteRoute, tasteHttp(tasteView({ chosen })))
    const line = await screen.findByText(/Mechanical Librarian writes it once you have chosen 25/u)
    expect(line).toHaveTextContent(
      `Mechanical Librarian writes it once you have chosen 25 covers in To decide. You have chosen ${String(chosen)}.`,
    )
    expect(within(line).getByRole('link', { name: 'To decide' })).toBeVisible()
  })

  it('makes a first build the heading while it is written', async () => {
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
    expect(await screen.findByRole('heading', { name: 'Writing your taste profile' })).toBeVisible()
    expect(screen.queryByRole('heading', { name: 'No taste profile yet' })).toBeNull()
  })

  // K1 with an idle owner: after 10 seconds without input a library notice
  // waits for the next input (#1224), and then the scheduled build appears.
  it('shows a build scheduled while the owner is idle on their next input', async () => {
    const updates = createLiveStream()
    let view = tasteView({ chosen: 25 })
    const http = tasteHttp(async () => view)
    renderRoute(tasteRoute, http, updates)
    expect(await screen.findByText(/You have chosen 25\./u)).toBeVisible()
    const reads = http.getTasteProfile.mock.calls.length
    const realNow = performance.now.bind(performance)
    const clock = vi
      .spyOn(performance, 'now')
      .mockImplementation(() => realNow() + ownerIdleMilliseconds)
    try {
      view = tasteView({
        chosen: 25,
        build: {
          build_id: `taste_build_${'a'.repeat(32)}`,
          state: 'building',
          failure: null,
          retry_after: null,
        },
      })
      await act(async () => {
        updates.announce({ sequence: 5, subject: null, topic: 'library' })
        await new Promise((resolve) => setTimeout(resolve, coalesceMilliseconds + 20))
      })
      expect(http.getTasteProfile).toHaveBeenCalledTimes(reads)
      expect(screen.getByText(/You have chosen 25\./u)).toBeVisible()
      fireEvent.wheel(document)
      expect(
        await screen.findByRole('heading', { name: 'Writing your taste profile' }),
      ).toBeVisible()
      expect(http.getTasteProfile.mock.calls.length).toBeGreaterThan(reads)
    } finally {
      clock.mockRestore()
    }
  })

  it('puts a first failure and Try again under the empty heading', async () => {
    renderRoute(
      tasteRoute,
      tasteHttp(
        tasteView({
          chosen: 25,
          build: {
            build_id: `taste_build_${'d'.repeat(32)}`,
            state: 'failed',
            failure: 'unavailable',
            retry_after: null,
          },
        }),
      ),
    )
    const heading = await screen.findByRole('heading', { name: 'No taste profile yet' })
    const region = heading.closest('section')
    if (!region) throw new Error('the empty region is required')
    const line = within(region).getByText('The taste profile could not be written.')
    const retry = within(region).getByRole('button', { name: 'Try again' })
    // Heading, then the line, then Try again, in reading order.
    expect(heading.compareDocumentPosition(line) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(line.compareDocumentPosition(retry) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })

  it('says an update failed, not that no profile exists, beside a profile', async () => {
    renderRoute(
      tasteRoute,
      tasteHttp({
        ...ready,
        build: {
          build_id: `taste_build_${'e'.repeat(32)}`,
          state: 'failed',
          failure: 'unavailable',
          retry_after: null,
        },
      }),
    )
    expect(await screen.findByText('The taste profile could not be updated.')).toBeVisible()
    expect(screen.queryByText('The taste profile could not be written.')).toBeNull()
    expect(screen.getByRole('heading', { name: 'What you’re drawn to' })).toBeVisible()
  })

  it.each(['ceiling', 'unknown_charge'] as const)(
    'hides Try again until the reset time after %s',
    async (failure) => {
      renderRoute(
        tasteRoute,
        tasteHttp(
          tasteView({
            chosen: 25,
            build: {
              build_id: `taste_build_${'f'.repeat(32)}`,
              state: 'failed',
              failure,
              retry_after: Math.floor(Date.now() / 1000) + 3_600,
            },
          }),
        ),
      )
      expect(await screen.findByRole('heading', { name: 'No taste profile yet' })).toBeVisible()
      expect(screen.getByRole('alert')).toHaveTextContent(/\S+\.$/u)
      expect(screen.queryByRole('button', { name: 'Try again' })).toBeNull()
    },
  )

  it('shows the Settings line, not an old failure, once no model is chosen', async () => {
    renderRoute(
      tasteRoute,
      tasteHttp({
        ...ready,
        configured: false,
        build: {
          build_id: `taste_build_${'9'.repeat(32)}`,
          state: 'failed',
          failure: 'unavailable',
          retry_after: null,
        },
      }),
    )
    expect(await screen.findByText(/Set up the taste profile model in/u)).toBeVisible()
    expect(screen.queryByText('The taste profile could not be updated.')).toBeNull()
    expect(screen.queryByRole('button', { name: 'Try again' })).toBeNull()
  })

  it.each([
    ['with no profile', tasteView({ chosen: 25 }), 'The taste profile could not be written.'],
    ['beside a profile', ready, 'The taste profile could not be updated.'],
  ] as const)(
    'offers Try again for a build that failed while no model was chosen, once one is chosen, %s',
    async (_case, base, copy) => {
      renderRoute(
        tasteRoute,
        tasteHttp({
          ...base,
          configured: true,
          build: {
            build_id: `taste_build_${'8'.repeat(32)}`,
            state: 'failed',
            failure: 'unconfigured',
            retry_after: null,
          },
        }),
      )
      expect(await screen.findByText(copy)).toBeVisible()
      expect(screen.getByRole('button', { name: 'Try again' })).toBeVisible()
      expect(screen.queryByText(/Set up the taste profile model in/u)).toBeNull()
    },
  )

  it.each([
    ['unavailable', null, 'The taste profile could not be written.'],
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
    // The route's own focus move is deferred a frame. Under load it can land
    // after the click and take focus from the region, so it is waited for.
    const heading = screen.getByRole('heading', { level: 1, name: 'Covers' })
    await waitFor(() => expect(heading).toHaveFocus())
    await user.click(screen.getByRole('button', { name: 'Try again' }))
    expect(http.retryTasteBuild).toHaveBeenCalledWith(`taste_build_${'b'.repeat(32)}`)
    const status = await screen.findByText('Writing your taste profile')
    await waitFor(() => expect(status.closest('.taste-build')).toHaveFocus())
  })

  it('shows the profile in use, all four sections and rule text literally', async () => {
    renderRoute(tasteRoute, tasteHttp(ready))
    expect(await screen.findByRole('heading', { name: 'What you’re drawn to' })).toBeVisible()
    // Every section shows its heading and Add rule; an empty one has no list.
    for (const name of [
      'What you’re drawn to',
      'What you avoid',
      'How you trade them off',
      'When to keep or switch',
    ]) {
      const section = screen.getByRole('heading', { name }).closest('section')
      if (!section) throw new Error('a section is required')
      expect(within(section).getByRole('button', { name: 'Add rule' })).toBeVisible()
    }
    const avoid = screen.getByRole('heading', { name: 'What you avoid' }).closest('section')
    expect(avoid?.querySelector('ol')).toBeNull()
    const rule = screen.getByText(injected)
    expect(rule.closest('li')).toHaveAttribute('value', '2')
    expect(rule.children).toHaveLength(0)
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

// The owner's edits (#1372 PR 2): edit in place, Add rule and Remove.
const owned = tasteView({
  chosen: 31,
  profile: {
    version: 3,
    kind: 'edited',
    built_at: 1_790_000_000,
    decisions: 31,
    rules: [
      { number: 1, section: 'drawn_to', text: 'Bold illustrated art.', provenance: 'model' },
      { number: 4, section: 'drawn_to', text: 'Calm serif type.', provenance: 'owner' },
      { number: 2, section: 'avoid', text: 'Busy type!', provenance: 'edited' },
    ],
  },
  versions: [
    { version: 3, kind: 'edited', created_at: 1_790_100_000, decisions: 31 },
    { version: 2, kind: 'edited', created_at: 1_790_050_000, decisions: 31 },
    { version: 1, kind: 'built', created_at: 1_790_000_000, decisions: 31 },
  ],
})
type TasteProfileRule = NonNullable<TasteProfileView['profile']>['rules'][number]
const withRules = (view: TasteProfileView, rules: readonly TasteProfileRule[], version: number) => {
  if (!view.profile) throw new Error('a profile is required')
  return {
    ...view,
    profile: { ...view.profile, version, kind: 'edited' as const, rules },
    versions: [
      { version, kind: 'edited' as const, created_at: 1_790_200_000, decisions: 31 },
      ...view.versions,
    ],
  }
}
const editHttp = (
  view: TasteProfileView,
  editTasteRule: (version: number, edit: unknown) => Promise<TasteProfileView>,
) => ({ ...tasteHttp(view), editTasteRule: vi.fn(editTasteRule) })
const sectionOf = (name: string) => {
  const section = screen.getByRole('heading', { name }).closest('section')
  if (!section) throw new Error('a section is required')
  return section
}
const statusLine = () => {
  const line = document.querySelector('.taste-status')
  if (!(line instanceof HTMLElement)) throw new Error('the status line is required')
  return line
}

describe('Covers → Taste profile edits (#1372 PR 2)', () => {
  it('names each pencil, marks the owner’s rules and lists edit versions', async () => {
    renderRoute(tasteRoute, tasteHttp(owned))
    const pencil = await screen.findByRole('button', { name: 'Edit rule 4' })
    expect(pencil).toHaveAttribute('title', 'Edit')
    expect(screen.getByRole('button', { name: 'Edit rule 1' })).toBeVisible()
    const yours = screen.getByText('Calm serif type.').closest('li')
    const edited = screen.getByText('Busy type!').closest('li')
    const model = screen.getByText('Bold illustrated art.').closest('li')
    if (!yours || !edited || !model) throw new Error('rule rows are required')
    expect(within(yours).getByText('Yours')).toHaveClass('status-pill', 'is-accent')
    expect(within(edited).getByText('Edited')).toHaveClass('status-pill', 'is-accent')
    expect(model.querySelector('.status-pill')).toBeNull()
    // The pill sits in its own column before the pencil.
    const pill = within(yours).getByText('Yours')
    expect(
      pill.compareDocumentPosition(within(yours).getByRole('button')) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy()
    expect(yours).toHaveAttribute('value', '4')
    const versions = screen.getByRole('heading', { name: 'Versions' }).closest('section')
    if (!versions) throw new Error('the Versions section is required')
    expect(
      within(versions)
        .getAllByRole('listitem')
        .map((row) => row.textContent),
    ).toEqual([
      expect.stringMatching(/^Version 3 \(in use\) Edited \S.+$/u),
      expect.stringMatching(/^Version 2 Edited \S.+$/u),
      expect.stringMatching(/^Version 1 Built .+ from 31 decisions$/u),
    ])
    expect(screen.getByText('Built').nextElementSibling?.textContent).toMatch(/\S/u)
  })

  it('opens an editor with its label, limit, counter and caret at the end', async () => {
    const user = userEvent.setup()
    renderRoute(tasteRoute, tasteHttp(owned))
    await user.click(await screen.findByRole('button', { name: 'Edit rule 2' }))
    const input = screen.getByRole('textbox', { name: 'Rule 2' })
    expect(input).toHaveFocus()
    expect(input).toHaveValue('Busy type!')
    // No native maxLength: it counts UTF-16 units, the limit counts code points.
    expect(input).not.toHaveAttribute('maxlength')
    expect((input as HTMLInputElement).selectionStart).toBe('Busy type!'.length)
    expect(screen.getByText('10 of 400 characters')).toBeVisible()
    // The counter counts code points, not UTF-16 units.
    await user.type(input, ' 😀')
    expect(screen.getByText('12 of 400 characters')).toBeVisible()
    expect(screen.getByRole('button', { name: 'Save rule' })).toHaveClass('button-primary')
    expect(screen.getByRole('button', { name: 'Remove' })).toHaveClass('button-danger')
    // One rule at a time: another pencil discards the open draft.
    await user.click(screen.getByRole('button', { name: 'Edit rule 1' }))
    expect(screen.queryByRole('textbox', { name: 'Rule 2' })).toBeNull()
    expect(screen.getByRole('textbox', { name: 'Rule 1' })).toHaveValue('Bold illustrated art.')
  })

  it.each([
    ['   ', 'Write a rule first.'],
    ['x'.repeat(399) + ' y', '400 characters at most.'],
    ['Covers like www.example.invalid', 'No web addresses or file paths.'],
    ['Covers like /books/covers/ do', 'No web addresses or file paths.'],
  ])('refuses %j in the browser and keeps what was typed', async (typed, copy) => {
    const user = userEvent.setup()
    const http = editHttp(owned, async () => owned)
    renderRoute(tasteRoute, http)
    await user.click(await screen.findByRole('button', { name: 'Edit rule 1' }))
    const input = screen.getByRole('textbox', { name: 'Rule 1' })
    fireEvent.change(input, { target: { value: typed } })
    await user.click(screen.getByRole('button', { name: 'Save rule' }))
    expect(screen.getByRole('alert')).toHaveTextContent(copy)
    expect(input).toHaveFocus()
    expect(input).toHaveValue(typed.replace(/\s+/gu, ' ').trim())
    expect(http.editTasteRule).not.toHaveBeenCalled()
  })

  it('saves an edit as the version shown, says so and returns to the pencil', async () => {
    const user = userEvent.setup()
    const next = withRules(
      owned,
      [
        { number: 1, section: 'drawn_to', text: 'Bright art.', provenance: 'edited' },
        ...(owned.profile?.rules.slice(1) ?? []),
      ],
      4,
    )
    const http = editHttp(owned, async () => next)
    renderRoute(tasteRoute, http)
    await user.click(await screen.findByRole('button', { name: 'Edit rule 1' }))
    const input = screen.getByRole('textbox', { name: 'Rule 1' })
    await user.clear(input)
    // Typed text is sent in its canonical spelling.
    await user.type(input, '  Bright   art.  ')
    await user.click(screen.getByRole('button', { name: 'Save rule' }))
    expect(http.editTasteRule).toHaveBeenCalledWith(3, {
      action: 'edit',
      number: 1,
      text: 'Bright art.',
    })
    expect(await screen.findByText('Rule 1 saved.')).toBe(statusLine())
    expect(statusLine()).toHaveAttribute('role', 'status')
    await waitFor(() => expect(screen.getByRole('button', { name: 'Edit rule 1' })).toHaveFocus())
    expect(screen.getByText('Bright art.').closest('li')).toHaveTextContent('Edited')
  })

  it('closes on unchanged text with no request or status', async () => {
    const user = userEvent.setup()
    const http = editHttp(owned, async () => owned)
    renderRoute(tasteRoute, http)
    await user.click(await screen.findByRole('button', { name: 'Edit rule 4' }))
    await user.click(screen.getByRole('button', { name: 'Save rule' }))
    expect(screen.queryByRole('textbox')).toBeNull()
    expect(http.editTasteRule).not.toHaveBeenCalled()
    expect(statusLine()).toHaveTextContent('')
    await waitFor(() => expect(screen.getByRole('button', { name: 'Edit rule 4' })).toHaveFocus())
  })

  it('cancels an edit back to its pencil', async () => {
    const user = userEvent.setup()
    renderRoute(tasteRoute, tasteHttp(owned))
    await user.click(await screen.findByRole('button', { name: 'Edit rule 2' }))
    await user.type(screen.getByRole('textbox', { name: 'Rule 2' }), ' more')
    await user.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(screen.getByText('Busy type!')).toBeVisible()
    await waitFor(() => expect(screen.getByRole('button', { name: 'Edit rule 2' })).toHaveFocus())
  })

  it('adds a rule in an empty section and moves to its pencil', async () => {
    const user = userEvent.setup()
    const added = withRules(
      owned,
      [
        ...(owned.profile?.rules ?? []),
        { number: 5, section: 'trade_offs', text: 'Takes a sharper copy.', provenance: 'owner' },
      ],
      4,
    )
    const http = editHttp(owned, async () => added)
    renderRoute(tasteRoute, http)
    const trade = await screen.findByRole('heading', { name: 'How you trade them off' })
    const section = trade.closest('section')
    if (!section) throw new Error('a section is required')
    await user.click(within(section).getByRole('button', { name: 'Add rule' }))
    const input = within(section).getByRole('textbox', { name: 'Rule 5' })
    expect(input).toHaveFocus()
    expect(input).toHaveValue('')
    // A new rule's form has no Remove.
    expect(within(section).queryByRole('button', { name: 'Remove' })).toBeNull()
    await user.type(input, 'Takes a sharper copy.')
    await user.click(within(section).getByRole('button', { name: 'Save rule' }))
    expect(http.editTasteRule).toHaveBeenCalledWith(3, {
      action: 'add',
      section: 'trade_offs',
      text: 'Takes a sharper copy.',
    })
    expect(await screen.findByText('Rule 5 saved.')).toBeVisible()
    await waitFor(() => expect(screen.getByRole('button', { name: 'Edit rule 5' })).toHaveFocus())
    expect(screen.getByText('Takes a sharper copy.').closest('li')).toHaveTextContent('Yours')
  })

  it('cancels a new rule back to its section’s Add rule', async () => {
    const user = userEvent.setup()
    renderRoute(tasteRoute, tasteHttp(owned))
    await screen.findByRole('heading', { name: 'What you avoid' })
    const avoid = sectionOf('What you avoid')
    await user.click(within(avoid).getByRole('button', { name: 'Add rule' }))
    await user.type(within(avoid).getByRole('textbox'), 'Thin type.')
    await user.click(within(avoid).getByRole('button', { name: 'Cancel' }))
    expect(within(avoid).queryByRole('textbox')).toBeNull()
    expect(screen.queryByText('Thin type.')).toBeNull()
    await waitFor(() =>
      expect(within(avoid).getByRole('button', { name: 'Add rule' })).toHaveFocus(),
    )
  })

  it('removes a rule, says so and moves to its section’s Add rule', async () => {
    const user = userEvent.setup()
    const without = withRules(owned, owned.profile?.rules.slice(0, 2) ?? [], 4)
    const http = editHttp(owned, async () => without)
    renderRoute(tasteRoute, http)
    await user.click(await screen.findByRole('button', { name: 'Edit rule 2' }))
    await user.click(screen.getByRole('button', { name: 'Remove' }))
    expect(http.editTasteRule).toHaveBeenCalledWith(3, { action: 'remove', number: 2 })
    expect(await screen.findByText('Rule 2 removed.')).toBe(statusLine())
    const avoid = sectionOf('What you avoid')
    expect(avoid.querySelector('ol')).toBeNull()
    await waitFor(() =>
      expect(within(avoid).getByRole('button', { name: 'Add rule' })).toHaveFocus(),
    )
  })

  it('says 40 rules is the limit and opens no form', async () => {
    const user = userEvent.setup()
    const full = withRules(
      owned,
      Array.from({ length: 40 }, (_unused, index) => ({
        number: index + 1,
        section: 'drawn_to' as const,
        text: `Rule ${String(index + 1)}.`,
        provenance: 'model' as const,
      })),
      4,
    )
    renderRoute(tasteRoute, tasteHttp(full))
    await screen.findByRole('heading', { name: 'What you avoid' })
    const avoid = sectionOf('What you avoid')
    const add = within(avoid).getByRole('button', { name: 'Add rule' })
    await user.click(add)
    // Said beside the Add rule pressed, which keeps focus; not at the top.
    expect(within(avoid).getByRole('status')).toHaveTextContent('40 rules is the limit.')
    expect(add).toHaveFocus()
    expect(statusLine()).toHaveTextContent('')
    expect(
      within(sectionOf('What you’re drawn to')).getByRole('status', { hidden: true }),
    ).toHaveTextContent('')
    expect(screen.queryByRole('textbox')).toBeNull()
  })

  it('says a rule was removed when a lost Remove turns out to have landed', async () => {
    const user = userEvent.setup()
    const without = withRules(owned, owned.profile?.rules.slice(0, 2) ?? [], 4)
    let current = owned
    const http = {
      ...editHttp(owned, async () => {
        current = without
        throw new UncertainMutationError({
          operation: 'POST /api/v1/covers/taste/rules',
          responseStatus: 200,
          at: '',
          reason: 'synthetic',
        })
      }),
      getTasteProfile: vi.fn(async () => current),
    }
    renderRoute(tasteRoute, http)
    await user.click(await screen.findByRole('button', { name: 'Edit rule 2' }))
    await user.click(screen.getByRole('button', { name: 'Remove' }))
    expect(await screen.findByText('Rule 2 removed.')).toBe(statusLine())
    expect(screen.queryByRole('textbox')).toBeNull()
    expect(screen.queryByRole('alert')).toBeNull()
    await waitFor(() => expect(statusLine()).toHaveFocus())
    // The editor is closed for good: a rule numbered 2 again opens nothing.
    expect(screen.queryByText('Busy type!')).toBeNull()
  })

  it('closes with the stale line when a re-read drops the open rule', async () => {
    const user = userEvent.setup()
    // A rebuild lands while rule 4 is open and numbers the rules 1 and 2.
    const rebuilt = withRules(
      owned,
      [
        { number: 1, section: 'drawn_to', text: 'Bold illustrated art.', provenance: 'model' },
        { number: 2, section: 'avoid', text: 'Busy type!', provenance: 'edited' },
      ],
      4,
    )
    let current = owned
    const http = { ...tasteHttp(owned), getTasteProfile: vi.fn(async () => current) }
    const { queryClient } = renderRoute(tasteRoute, http)
    await user.click(await screen.findByRole('button', { name: 'Edit rule 4' }))
    current = rebuilt
    await act(async () => {
      await queryClient.invalidateQueries({ queryKey: ['covers', 'taste'] })
    })
    expect(
      await screen.findByText(
        'The taste profile changed since the page loaded. Nothing was changed.',
      ),
    ).toBe(statusLine())
    expect(screen.queryByRole('textbox')).toBeNull()
    await waitFor(() => expect(statusLine()).toHaveFocus())
  })

  it('says a rule was saved when Save repeats a lost save that landed', async () => {
    const user = userEvent.setup()
    const landed = withRules(
      owned,
      (owned.profile?.rules ?? []).map((rule) =>
        rule.number === 2 ? { ...rule, text: 'Busy type! now' } : rule,
      ),
      4,
    )
    let current = owned
    const http = {
      ...editHttp(owned, async () => {
        current = landed
        throw new UncertainMutationError({
          operation: 'POST /api/v1/covers/taste/rules',
          responseStatus: 200,
          at: '',
          reason: 'synthetic',
        })
      }),
      getTasteProfile: vi.fn(async () => current),
    }
    renderRoute(tasteRoute, http)
    await user.click(await screen.findByRole('button', { name: 'Edit rule 2' }))
    await user.type(screen.getByRole('textbox', { name: 'Rule 2' }), ' now')
    await user.click(screen.getByRole('button', { name: 'Save rule' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'No answer came back. Check whether the rule was saved.',
    )
    await screen.findByText('4')
    await user.click(screen.getByRole('button', { name: 'Save rule' }))
    expect(http.editTasteRule).toHaveBeenCalledTimes(1)
    expect(await screen.findByText('Rule 2 saved.')).toBe(statusLine())
    await waitFor(() => expect(screen.getByRole('button', { name: 'Edit rule 2' })).toHaveFocus())
  })

  it('keeps the editor and its draft when the re-read after a lost answer fails', async () => {
    const user = userEvent.setup()
    let reads = 0
    const http = {
      ...editHttp(owned, async () => {
        throw new UncertainMutationError({
          operation: 'POST /api/v1/covers/taste/rules',
          responseStatus: 200,
          at: '',
          reason: 'synthetic',
        })
      }),
      getTasteProfile: vi.fn(async () => {
        reads += 1
        if (reads > 1) throw new TypeError('Failed to fetch')
        return owned
      }),
    }
    renderRoute(tasteRoute, http)
    await user.click(await screen.findByRole('button', { name: 'Edit rule 2' }))
    await user.type(screen.getByRole('textbox', { name: 'Rule 2' }), ' now')
    await user.click(screen.getByRole('button', { name: 'Save rule' }))
    await waitFor(() => expect(http.getTasteProfile).toHaveBeenCalledTimes(2))
    await screen.findByRole('button', { name: 'Reload' })
    expect(screen.getByRole('textbox', { name: 'Rule 2' })).toHaveValue('Busy type! now')
    expect(screen.getByText('No answer came back. Check whether the rule was saved.')).toBeVisible()
  })

  it('takes no typing while a save is pending', async () => {
    const user = userEvent.setup()
    let answer: (view: TasteProfileView) => void = () => undefined
    const http = editHttp(
      owned,
      () =>
        new Promise<TasteProfileView>((resolve) => {
          answer = resolve
        }),
    )
    renderRoute(tasteRoute, http)
    await user.click(await screen.findByRole('button', { name: 'Edit rule 1' }))
    const input = screen.getByRole('textbox', { name: 'Rule 1' })
    await user.type(input, ' now')
    await user.click(screen.getByRole('button', { name: 'Save rule' }))
    await waitFor(() => expect(input).toHaveAttribute('readonly'))
    await user.type(input, ' more')
    expect(input).toHaveValue('Bold illustrated art. now')
    await act(async () => {
      answer(owned)
    })
  })

  it('takes a 400-character rule whose characters need two UTF-16 units', async () => {
    const user = userEvent.setup()
    const text = 'x'.repeat(399) + '😀'
    const http = editHttp(owned, async () => owned)
    renderRoute(tasteRoute, http)
    await user.click(await screen.findByRole('button', { name: 'Edit rule 1' }))
    fireEvent.change(screen.getByRole('textbox', { name: 'Rule 1' }), { target: { value: text } })
    expect(screen.getByText('400 of 400 characters')).toBeVisible()
    await user.click(screen.getByRole('button', { name: 'Save rule' }))
    expect(http.editTasteRule).toHaveBeenCalledWith(3, { action: 'edit', number: 1, text })
  })

  it('cancels with Escape back to the pencil, or to Add rule for a new rule', async () => {
    const user = userEvent.setup()
    renderRoute(tasteRoute, tasteHttp(owned))
    await user.click(await screen.findByRole('button', { name: 'Edit rule 1' }))
    await user.type(screen.getByRole('textbox', { name: 'Rule 1' }), ' now')
    await user.keyboard('{Escape}')
    expect(screen.queryByRole('textbox')).toBeNull()
    expect(screen.getByText('Bold illustrated art.')).toBeVisible()
    await waitFor(() => expect(screen.getByRole('button', { name: 'Edit rule 1' })).toHaveFocus())
    const avoid = sectionOf('What you avoid')
    await user.click(within(avoid).getByRole('button', { name: 'Add rule' }))
    await user.keyboard('{Escape}')
    expect(screen.queryByRole('textbox')).toBeNull()
    await waitFor(() =>
      expect(within(avoid).getByRole('button', { name: 'Add rule' })).toHaveFocus(),
    )
  })

  it('keeps the editor open when Escape ends an input method composition', async () => {
    const user = userEvent.setup()
    renderRoute(tasteRoute, tasteHttp(owned))
    await user.click(await screen.findByRole('button', { name: 'Edit rule 1' }))
    const input = screen.getByRole('textbox', { name: 'Rule 1' })
    fireEvent.keyDown(input, { key: 'Escape', isComposing: true })
    expect(screen.getByRole('textbox', { name: 'Rule 1' })).toBe(input)
    fireEvent.keyDown(input, { key: 'Escape' })
    expect(screen.queryByRole('textbox')).toBeNull()
  })

  it('refuses a rule the profile has no room for before sending it', async () => {
    const user = userEvent.setup()
    const crowded = withRules(
      owned,
      Array.from({ length: 30 }, (_unused, index) => ({
        number: index + 1,
        section: 'drawn_to' as const,
        text: Array.from({ length: 40 }, () => 'a').join(' '),
        provenance: 'model' as const,
      })),
      4,
    )
    const http = editHttp(crowded, async () => crowded)
    renderRoute(tasteRoute, http)
    await screen.findByRole('heading', { name: 'What you avoid' })
    await user.click(within(sectionOf('What you avoid')).getByRole('button', { name: 'Add rule' }))
    await user.type(screen.getByRole('textbox'), 'One more word.')
    await user.click(screen.getByRole('button', { name: 'Save rule' }))
    expect(screen.getByRole('alert')).toHaveTextContent(
      'The taste profile has no room for this rule. Shorten it or remove another rule.',
    )
    expect(http.editTasteRule).not.toHaveBeenCalled()
  })

  it.each([
    [
      'profile_full',
      'The taste profile has no room for this rule. Shorten it or remove another rule.',
    ],
    [
      'rule_text',
      'Use plain words. Leave out web addresses, file paths, passwords and hidden characters.',
    ],
    ['invalid', 'The rule was not saved. Reload and try again.'],
  ])('shows the server’s %s refusal and keeps the draft', async (code, copy) => {
    const user = userEvent.setup()
    const http = editHttp(owned, async () => {
      throw new ApiError(422, code)
    })
    renderRoute(tasteRoute, http)
    await user.click(await screen.findByRole('button', { name: 'Edit rule 1' }))
    const input = screen.getByRole('textbox', { name: 'Rule 1' })
    await user.clear(input)
    await user.type(input, 'password=hunter2 style')
    await user.click(screen.getByRole('button', { name: 'Save rule' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(copy)
    expect(input).toHaveValue('password=hunter2 style')
  })

  it('closes on stale, says so, re-reads and moves to the status line', async () => {
    const user = userEvent.setup()
    const http = editHttp(owned, async () => {
      throw new ApiError(409, 'stale')
    })
    renderRoute(tasteRoute, http)
    await user.click(await screen.findByRole('button', { name: 'Edit rule 1' }))
    await user.type(screen.getByRole('textbox', { name: 'Rule 1' }), ' now')
    await user.click(screen.getByRole('button', { name: 'Save rule' }))
    expect(
      await screen.findByText(
        'The taste profile changed since the page loaded. Nothing was changed.',
      ),
    ).toBe(statusLine())
    expect(screen.queryByRole('textbox')).toBeNull()
    await waitFor(() => expect(statusLine()).toHaveFocus())
    await waitFor(() => expect(http.getTasteProfile.mock.calls.length).toBeGreaterThan(1))
  })

  it.each([
    ['saved', 'Save rule', 'No answer came back. Check whether the rule was saved.'],
    ['removed', 'Remove', 'No answer came back. Check whether the rule was removed.'],
  ])('keeps the editor open when no answer says whether it was %s', async (_kind, button, copy) => {
    const user = userEvent.setup()
    const http = editHttp(owned, async () => {
      throw new UncertainMutationError({
        operation: 'POST /api/v1/covers/taste/rules',
        responseStatus: 200,
        at: '',
        reason: 'synthetic',
      })
    })
    renderRoute(tasteRoute, http)
    await user.click(await screen.findByRole('button', { name: 'Edit rule 2' }))
    const input = screen.getByRole('textbox', { name: 'Rule 2' })
    await user.type(input, ' now')
    await user.click(screen.getByRole('button', { name: button }))
    expect(await screen.findByRole('alert')).toHaveTextContent(copy)
    expect(screen.getByRole('textbox', { name: 'Rule 2' })).toHaveValue('Busy type! now')
    await waitFor(() => expect(http.getTasteProfile.mock.calls.length).toBeGreaterThan(1))
  })

  it('sends the version the editor opened on after a re-read', async () => {
    const user = userEvent.setup()
    // A rebuild lands while the editor is open: the read now shows version 4.
    const rebuilt = withRules(owned, owned.profile?.rules ?? [], 4)
    let current = owned
    const http = {
      ...editHttp(owned, async () => {
        throw new ApiError(409, 'stale')
      }),
      getTasteProfile: vi.fn(async () => current),
    }
    const { queryClient } = renderRoute(tasteRoute, http)
    await user.click(await screen.findByRole('button', { name: 'Edit rule 1' }))
    current = rebuilt
    await act(async () => {
      await queryClient.invalidateQueries({ queryKey: ['covers', 'taste'] })
    })
    expect(await screen.findByText('4')).toBeVisible()
    await user.type(screen.getByRole('textbox', { name: 'Rule 1' }), ' now')
    await user.click(screen.getByRole('button', { name: 'Save rule' }))
    expect(http.editTasteRule).toHaveBeenCalledWith(3, {
      action: 'edit',
      number: 1,
      text: 'Bold illustrated art. now',
    })
    expect(
      await screen.findByText(
        'The taste profile changed since the page loaded. Nothing was changed.',
      ),
    ).toBeVisible()
  })

  it('marks controls aria-disabled while a save is pending', async () => {
    const user = userEvent.setup()
    let release: (view: TasteProfileView) => void = () => undefined
    const http = editHttp(
      owned,
      async () =>
        await new Promise<TasteProfileView>((resolve) => {
          release = resolve
        }),
    )
    renderRoute(tasteRoute, http)
    await user.click(await screen.findByRole('button', { name: 'Edit rule 1' }))
    await user.type(screen.getByRole('textbox', { name: 'Rule 1' }), ' now')
    await user.click(screen.getByRole('button', { name: 'Save rule' }))
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Save rule' })).toHaveAttribute(
        'aria-disabled',
        'true',
      ),
    )
    expect(screen.getByRole('button', { name: 'Edit rule 4' })).toHaveAttribute(
      'aria-disabled',
      'true',
    )
    await user.click(screen.getByRole('button', { name: 'Save rule' }))
    expect(http.editTasteRule).toHaveBeenCalledTimes(1)
    await act(async () => {
      release(owned)
    })
  })
})
