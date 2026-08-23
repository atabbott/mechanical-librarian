import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { RouterProvider, createMemoryHistory } from '@tanstack/react-router'
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { ApiError } from '../src/api/http'
import { coalesceMilliseconds } from '../src/liveInvalidation'
import { sourceStatusLabel } from '../src/routes/LibraryPage'
import type {
  AttentionSearch,
  CaseDetailView,
  LibrarySearch,
  LibraryView,
  ReviewHttp,
} from '../src/api/http'
import { createApplicationRouter, normalizeLibrarySearch } from '../src/router'
import { activity, createHttp, createLiveStream, detail, library, renderRoute } from './fixtures'

describe('Library route', () => {
  it('bounds and normalizes URL-owned library search state', () => {
    expect(
      normalizeLibrarySearch({ lens: 'all', q: 'title', cursor: 'next', preview: 'case-1' }),
    ).toEqual({
      lens: 'all',
      q: 'title',
      cursor: 'next',
      preview: 'case-1',
    })
    expect(normalizeLibrarySearch({ lens: 'hostile', q: 'x'.repeat(129) })).toEqual({ lens: 'all' })
  })

  it.each(['needs_attention', 'no_attention_needed'] as const)(
    'keeps primary Library navigation active for the %s lens',
    async (lens) => {
      renderRoute(`/library?lens=${lens}`)
      expect(await screen.findByRole('heading', { level: 2, name: 'Works' })).toBeVisible()
      expect(screen.queryByText('Every work, with exceptions first')).toBeNull()
      expect(screen.getByRole('link', { name: 'Library' })).toHaveAttribute('aria-current', 'page')
    },
  )

  it('does not query successor lineage without a preview selection', async () => {
    const getCaseSuccessor = vi.fn<ReviewHttp['getCaseSuccessor']>()
    renderRoute('/library?lens=all', { ...createHttp(), getCaseSuccessor })

    expect(await screen.findByRole('heading', { level: 2, name: 'Works' })).toBeVisible()
    expect(getCaseSuccessor).not.toHaveBeenCalled()
  })

  it('keeps the selected-work header as the only Library route-focus target', async () => {
    renderRoute('/library?lens=all&preview=case-1&field=title')
    expect(await screen.findByRole('heading', { level: 2, name: 'A Book' })).toBeVisible()
    const targets = document.querySelectorAll('[data-route-focus]')
    expect(targets).toHaveLength(1)
    expect(targets[0]).toHaveClass('selected-work-header')
  })

  it('puts compact selected-work navigation below the masthead and before the work', async () => {
    renderRoute('/library?lens=all&preview=case-1&field=title')
    const heading = await screen.findByRole('heading', { level: 2, name: 'A Book' })
    const back = screen.getByRole('button', { name: 'Back to works' })
    const attention = screen.getByRole('button', { name: 'Attention queue' })
    const masthead = document.querySelector('.masthead')
    if (!masthead) throw new Error('application fixture requires the masthead')

    expect(masthead.compareDocumentPosition(back) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(back.compareDocumentPosition(heading) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(
      attention.compareDocumentPosition(heading) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy()
    expect(screen.getByRole('heading', { level: 1, name: 'Library' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /^Attention \d+$/u })).toBeNull()
    expect(back).toHaveClass('compact-workspace-back')
  })

  it('loads the critical library query and progressively reveals detail', async () => {
    const user = userEvent.setup()
    const http: ReviewHttp = {
      ...createHttp(),
      getCaseDetail: vi.fn(async () => ({
        ...detail,
        title: 'item-1',
        subtitle: 'internal identity',
      })),
    }
    renderRoute('/library?lens=all&q=title', http)

    expect(await screen.findByRole('heading', { level: 2, name: 'Works' })).toBeVisible()
    expect(http.getLibrary).toHaveBeenCalledWith(
      { lens: 'all', q: 'title' },
      expect.any(AbortSignal),
    )
    await user.click(screen.getByRole('button', { name: /^A Book\./u }))
    expect(await screen.findByRole('heading', { level: 2, name: 'A Book' })).toBeVisible()
    expect(screen.queryByText('item-1')).toBeNull()
    expect(screen.getByText('Author details unavailable · Books · ebook')).toBeVisible()
    expect(http.getCaseDetail).toHaveBeenCalledWith('case-1', expect.any(AbortSignal))
  })

  it('opens a current summary and its source ledger for a work with no review case', async () => {
    const firstWork = library.works[0]
    if (!firstWork) throw new Error('library fixture requires one work')
    const baseWork = { ...firstWork }
    delete baseWork.reviewCaseId
    const failed = {
      ...baseWork,
      id: 'failed-work',
      title: 'Preparation unavailable',
      attentionCount: 0,
      attention: 'no_attention_needed',
      preparation: {
        attempt: 1,
        diagnosticReference: 'prep-0123456789abcdef',
        provider: 'hardcover-main',
        reason: 'Provider credentials need owner attention.',
        recovery: 'Update the configured provider credentials, then scan this library again.',
        recoveryAttempt: 0,
        stage: 'provider',
        status: 'authentication',
      },
      preparationOutcomes: [
        {
          providerRequests: 1,
          laneKind: 'work',
          provider: 'hardcover-main',
          reason: 'Refused the credentials it was given; nothing was retained.',
          status: 'authentication',
        },
      ],
      summary: {
        state: 'preparation_failed',
        reason: 'Provider credentials need owner attention.',
        fields: [],
      },
    }
    const http: ReviewHttp = {
      ...createHttp(),
      getLibrary: vi.fn(async () => ({
        lens: 'all' as const,
        page: 1,
        counts: { attention: 0, all: 1, clear: 1 },
        works: [failed],
      })),
    }
    renderRoute('/library?lens=all', http)

    expect(await screen.findByText('Preparation failed safely')).toBeVisible()
    const terminal = document.querySelector('.library-work-row.is-terminal')
    expect(terminal?.tagName).toBe('ARTICLE')
    expect(terminal).toHaveAttribute('aria-label', 'Terminal preparation failure')
    expect(terminal?.querySelector('button, a, details, [tabindex]')).toBeNull()
    expect(screen.queryByRole('button', { name: /^Preparation unavailable\./u })).toBeNull()
    expect(terminal).toHaveTextContent(
      'Update the configured provider credentials, then scan this library again.',
    )
    expect(screen.getByText('prep-0123456789abcdef')).toBeVisible()
  })

  it('keeps an open case-less work open when a search hides its card', async () => {
    const user = userEvent.setup()
    const firstWork = library.works[0]
    if (!firstWork) throw new Error('library fixture requires one work')
    const orphan = { ...firstWork }
    delete orphan.reviewCaseId
    const listed = {
      ...orphan,
      id: 'orphan-work',
      workId: 'work-orphan',
      title: 'Unfiled observation',
      attentionCount: 0,
      summary: {
        state: 'no_decision_recorded',
        reason: 'No review decision is recorded for this work.',
        fields: [{ destination: 'title', state: 'value', value: 'Unfiled observation' }],
      },
    }
    const getLibrary = vi.fn(async (search: { q?: string }) => ({
      lens: 'all' as const,
      page: 1,
      counts: { attention: 0, all: 1, clear: 1 },
      works: search.q ? [] : [listed],
    }))
    renderRoute('/library?lens=all', { ...createHttp(), getLibrary })

    await user.click(await screen.findByRole('button', { name: /^Unfiled observation\./u }))
    expect(
      await screen.findByRole('heading', { level: 2, name: 'Unfiled observation' }),
    ).toBeVisible()
    await user.type(
      screen.getByLabelText('Search title, author, identifier, library, or format'),
      'nothing matches',
    )

    // The card is gone from the narrowed result, but the open work stays open.
    await screen.findByText('No exact-current works match this search and lens.')
    expect(screen.getByRole('heading', { level: 2, name: 'Unfiled observation' })).toBeVisible()
    await user.click(screen.getByRole('button', { name: 'Back to works' }))
    expect(screen.queryByRole('heading', { level: 2, name: 'Unfiled observation' })).toBeNull()
  })

  it('keeps the latest case-less selection when an older navigation settles last', async () => {
    const user = userEvent.setup()
    const firstWork = library.works[0]
    if (!firstWork) throw new Error('library fixture requires one work')
    const caseLess = (id: string, title: string) => {
      const work = { ...firstWork }
      delete work.reviewCaseId
      return {
        ...work,
        id,
        workId: `work-${id}`,
        title,
        attentionCount: 0,
        summary: {
          state: 'no_decision_recorded' as const,
          reason: 'No review decision is recorded for this work.',
          fields: [{ destination: 'title', state: 'value' as const, value: title }],
        },
      }
    }
    const first = caseLess('first-observation', 'First observation')
    const second = caseLess('second-observation', 'Second observation')
    const getLibrary = vi.fn(async (search: { q?: string }) => ({
      lens: 'all' as const,
      page: 1,
      counts: { attention: 0, all: 2, clear: 2 },
      works: search.q ? [] : [first, second],
    }))
    const { router } = renderRoute('/library?lens=all', { ...createHttp(), getLibrary })
    const firstRow = await screen.findByRole('button', { name: /^First observation\./u })
    const secondRow = screen.getByRole('button', { name: /^Second observation\./u })
    const navigate = router.navigate.bind(router)
    let releaseFirstNavigation: () => void = () => undefined
    const firstNavigationHeld = new Promise<void>((resolve) => {
      releaseFirstNavigation = resolve
    })
    let selectionNavigations = 0
    vi.spyOn(router, 'navigate').mockImplementation(async (options) => {
      selectionNavigations += 1
      const invocation = selectionNavigations
      const result = await navigate(options)
      if (invocation === 1) await firstNavigationHeld
      return result
    })

    fireEvent.click(firstRow)
    fireEvent.click(secondRow)
    await waitFor(() =>
      expect(router.state.location.search).toMatchObject({
        lens: 'all',
        work: 'work-second-observation',
      }),
    )
    await act(async () => {
      releaseFirstNavigation()
      await firstNavigationHeld
    })
    await user.type(
      screen.getByLabelText('Search title, author, identifier, library, or format'),
      'hide both',
    )
    await screen.findByText('No exact-current works match this search and lens.')
    expect(
      await screen.findByRole('heading', { level: 2, name: 'Second observation' }),
    ).toBeVisible()
    await waitFor(() =>
      expect(router.state.location.search).toMatchObject({
        lens: 'all',
        q: 'hide both',
        work: 'work-second-observation',
      }),
    )
  })

  it('normalises an unresolvable work selection out of the URL', async () => {
    const http: ReviewHttp = { ...createHttp(), getLibrary: vi.fn(async () => library) }
    const { router } = renderRoute('/library?lens=all&work=work-that-is-not-on-this-page', http)

    // The URL asserted a selection nothing can render, so it stops asserting it
    // rather than leaving the view and the address bar disagreeing.
    await waitFor(() => expect(router.state.location.search).toEqual({ lens: 'all' }))
    expect(screen.getByRole('heading', { name: 'Choose a work' })).toBeVisible()
  })

  it.each([
    ['candidates', 'Match'],
    ['no_match', 'No match'],
    ['rate_limited', 'Retry scheduled'],
    ['unavailable', 'Retry scheduled'],
    ['candidate_limit_exceeded', 'Too many candidates'],
    ['authentication', 'Provider failure'],
    ['malformed', 'Provider failure'],
    ['a_status_this_build_has_never_seen', 'Provider failure'],
  ])('names the %s source status for an owner as %s', (status, label) => {
    expect(sourceStatusLabel(status)).toBe(label)
  })

  it('keeps the whole work card as the single selection target', async () => {
    const user = userEvent.setup()
    const { router } = renderRoute('/library?lens=all')

    const card = await screen.findByRole('button', { name: /^A Book\./u })
    expect(card).toHaveClass('library-work-row')
    // No nested title-only target competes with the card.
    expect(within(card).queryByRole('button')).toBeNull()
    expect(within(card).queryByRole('link')).toBeNull()
    expect(card).toHaveTextContent('A Book')
    expect(card).toHaveTextContent('Author details unavailable')
    expect(card).toHaveTextContent('1 field needs attention')

    await user.click(card)
    await waitFor(() =>
      expect(router.state.location.search).toEqual({ lens: 'all', preview: 'case-1' }),
    )
    expect(screen.getByRole('button', { name: /^A Book\./u })).toHaveAttribute(
      'aria-current',
      'true',
    )
  })

  it('offers both pagination directions with readable page context', async () => {
    const user = userEvent.setup()
    const paged = { ...library, page: 2, nextCursor: 'page-3', previousCursor: 'page-1' }
    const http: ReviewHttp = { ...createHttp(), getLibrary: vi.fn(async () => paged) }
    const { router } = renderRoute('/library?lens=all&q=book&cursor=page-2', http)

    expect(await screen.findByText('Page 2')).toBeVisible()
    await user.click(screen.getByRole('button', { name: '← Previous' }))
    await waitFor(() =>
      expect(router.state.location.search).toEqual({ lens: 'all', q: 'book', cursor: 'page-1' }),
    )
    await user.click(screen.getByRole('button', { name: 'Next →' }))
    await waitFor(() =>
      expect(router.state.location.search).toEqual({ lens: 'all', q: 'book', cursor: 'page-3' }),
    )
  })

  it('filters progressively while typing, resets on clear, and keeps the lens and selection', async () => {
    const user = userEvent.setup()
    const { router } = renderRoute('/library?lens=needs_attention&preview=case-1&cursor=page-2')

    // Wait for the route to finish arriving before taking the keyboard. The
    // arrival re-renders the panel, and a box grabbed before that is a
    // detached node every keystroke goes into and nothing comes out of --
    // which passes whenever the machine is quick and fails whenever the rest
    // of the suite is competing for it.
    await waitFor(() => expect(document.querySelector('[data-route-focus]')).toHaveFocus())
    const box = screen.getByLabelText('Search title, author, identifier, library, or format')
    await user.type(box, 'clock')
    await waitFor(() =>
      expect(router.state.location.search).toEqual({
        lens: 'needs_attention',
        preview: 'case-1',
        q: 'clock',
      }),
    )
    await user.click(screen.getByRole('button', { name: 'Clear' }))
    await waitFor(() =>
      expect(router.state.location.search).toEqual({ lens: 'needs_attention', preview: 'case-1' }),
    )
    expect(box).toHaveValue('')
  })

  it.each([
    [1, '1 field needs attention'],
    [2, '2 fields need attention'],
  ] as const)(
    'agrees the work-card attention noun and verb for %i field(s)',
    async (attentionCount, copy) => {
      const firstWork = library.works[0]
      if (!firstWork) throw new Error('library fixture requires one work')
      const http: ReviewHttp = {
        ...createHttp(),
        getLibrary: vi.fn(async () => ({ ...library, works: [{ ...firstWork, attentionCount }] })),
      }
      renderRoute('/library?lens=all', http)

      expect(await screen.findByText(copy)).toBeVisible()
    },
  )

  it('shows bounded provider absence once at the selected-work comparison boundary', async () => {
    const http: ReviewHttp = {
      ...createHttp(),
      getCaseDetail: vi.fn(async () => ({
        ...detail,
        comparisonNotices: [
          'Provider limitation: Open Library — Provider returned no matching metadata.',
        ],
      })),
    }
    renderRoute('/library?lens=all&preview=case-1&field=title', http)

    const condition = await screen.findByRole('complementary', {
      name: 'Provider comparison condition',
    })
    expect(condition).toHaveTextContent('Open Library')
    expect(condition).toHaveTextContent('Provider returned no matching metadata.')
    expect(within(condition).getAllByText(/Provider limitation/u)).toHaveLength(1)
  })

  it('keeps Variant M field, automatic, evidence, and apply context in the responsive Library workspace', async () => {
    const user = userEvent.setup()
    const titleField = detail.fields[0]
    if (!titleField) throw new Error('workspace fixture requires a title field')
    const automaticField = {
      ...titleField,
      destination: 'series',
      currentValue: 'Atlas Cycle 2.5',
      currentRaw: { name: 'Atlas Cycle', sequence: '2.5' },
      proposedValues: [],
      proposalId: 'proposal-series',
      targetFingerprint: 'fingerprint-series',
      classificationReason:
        'Automatic handling recorded this field; you may inspect or override it.',
      needsAttention: false,
      automaticDecision: {
        candidate_id: null,
        decision_id: 'automatic-series',
        destination: 'series',
        occurred_at: 1,
        outcome: 'keep_current' as const,
        proposal_id: 'proposal-series',
        review_case_id: 'case-1',
        selected_value: null,
        target_fingerprint: 'fingerprint-series',
      },
      notices: ['Series already agrees with the deterministic policy.'],
    }
    const http: ReviewHttp = {
      ...createHttp(),
      getCaseDetail: vi.fn(async () => ({
        ...detail,
        fields: [
          {
            ...titleField,
            effectCapability: 'allowed',
            notices: ['competing_evidence'],
            choiceContexts: [
              {
                alternativeId: null,
                selection: 'current',
                rationale: 'Keep the value currently held by the local library.',
                recommendation: 'no_recommendation',
                recommendationReason:
                  'No deterministic recommendation is retained for the current value.',
                limitations: [],
                evidence: [
                  {
                    source: 'Local library record',
                    observation: 'Title: Old title',
                    role: 'local_presence',
                  },
                ],
              },
              {
                alternativeId: 'alternative-title-1',
                selection: 'proposed',
                rationale: 'This retained alternative is eligible for owner selection.',
                recommendation: 'no_recommendation',
                recommendationReason:
                  'No deterministic recommendation is retained for this alternative.',
                limitations: [],
                evidence: [
                  {
                    source: 'Open Library',
                    observation: 'Title: New title',
                    role: 'external_corroboration',
                  },
                ],
              },
            ] as const,
          },
          automaticField,
        ],
      })),
    }
    renderRoute('/library?lens=all&preview=case-1&field=title', http)

    expect(
      await screen.findByRole('button', { name: 'Choose current value: Old title' }),
    ).toBeVisible()
    expect(screen.getByText('Eligible for guarded planning')).toBeVisible()
    const evidenceButtons = screen.getAllByRole('button', { name: /Evidence for/u })
    const currentEvidence = evidenceButtons[0]
    const proposedEvidence = evidenceButtons[1]
    if (!currentEvidence || !proposedEvidence)
      throw new Error('choice evidence controls are missing')
    expect(currentEvidence).toHaveAccessibleName('Evidence for Current value: Old title')
    expect(proposedEvidence).toHaveAccessibleName('Evidence for Alternative 1: New title')
    await user.click(currentEvidence)
    expect(
      screen.queryByRole('button', { name: 'Evidence for Current value: Old title' }),
    ).toBeNull()
    expect(await screen.findByText('Local library record')).toBeVisible()
    expect(screen.getByText('Title: Old title')).toBeVisible()
    expect(screen.getByText('Local presence')).toBeVisible()
    expect(screen.getAllByText('No recommendation retained').length).toBeGreaterThan(0)
    expect(screen.queryByText('Source ledger')).not.toBeInTheDocument()
    await user.click(proposedEvidence)
    expect(await screen.findByText('Open Library')).toBeVisible()
    expect(screen.getByText('Title: New title')).toBeVisible()
    expect(screen.getByText('External corroboration')).toBeVisible()
    expect(screen.getAllByText('Competing evidence').length).toBeGreaterThan(0)
    const fieldRail = screen.getByRole('navigation', { name: 'Review fields' })
    expect(
      within(fieldRail).getByRole('button', { name: /Series.*Resolved automatically/u }),
    ).toBeVisible()
  })

  it('opens a long description without recording a choice', async () => {
    const user = userEvent.setup()
    const description = 'A complete retained paragraph. '.repeat(20)
    const titleField = detail.fields[0]
    if (!titleField) throw new Error('description fixture requires a field')
    const field = { ...titleField, destination: 'description', currentValue: description }
    const http = {
      ...createHttp(),
      getCaseDetail: vi.fn(async () => ({ ...detail, fields: [field] })),
    }
    renderRoute('/library?lens=all&preview=case-1&field=description', http)

    const disclosure = await screen.findByText('Read full description')
    await user.click(disclosure)
    expect(screen.getAllByText((_, node) => node?.textContent === description)).toHaveLength(1)
    disclosure.focus()
    await user.keyboard('{Enter}')
    expect(http.recordDecision).not.toHaveBeenCalled()

    const fullValue = screen.getByRole('button', { name: 'Choose current full description' })
    await user.click(fullValue)
    await waitFor(() => expect(http.recordDecision).toHaveBeenCalledOnce())
  })

  it('keeps the current cover pinned beside a complete eligible gallery and retains advisory reasons', async () => {
    const user = userEvent.setup()
    const base = detail.fields[0]
    if (!base) throw new Error('cover fixture requires a field')
    const reference = (index: number) => `cover_transport_ref_${String(index).repeat(64)}`
    const candidate = (index: number, incumbent = false) => ({
      artifactId: `cover_${String(index).repeat(64)}`,
      height: 1200,
      identity: { outcome: 'plausible_match' },
      incumbent,
      observation: null,
      originalSha256: String(index).repeat(64),
      ordinal: index,
      previewSha256: String(index).repeat(64),
      reference: reference(index),
      selectable: !incumbent,
      sourceKinds: [incumbent ? 'audiobookshelf_current' : 'provider'],
      state: incumbent ? 'incumbent' : 'acceptable',
      width: 800,
    })
    const consideration = {
      id: `cover_consideration_${'c'.repeat(64)}`,
      choiceContexts: [
        {
          alternativeId: null,
          selection: 'current',
          evidence: [],
          limitations: [],
          rationale: 'Keep the local cover.',
          recommendation: 'no_recommendation',
          recommendationReason: 'No recommendation retained.',
        },
        {
          alternativeId: reference(2),
          selection: 'proposed',
          evidence: [],
          limitations: [],
          rationale: 'Selectable retained cover.',
          recommendation: 'no_recommendation',
          recommendationReason: 'No recommendation retained.',
        },
        {
          alternativeId: reference(3),
          selection: 'proposed',
          evidence: [],
          limitations: [],
          rationale: 'Selectable retained cover.',
          recommendation: 'no_recommendation',
          recommendationReason: 'No recommendation retained.',
        },
        {
          alternativeId: reference(4),
          selection: 'proposed',
          evidence: [],
          limitations: [],
          rationale: 'Selectable retained cover.',
          recommendation: 'no_recommendation',
          recommendationReason: 'No recommendation retained.',
        },
      ],
      current: true,
      candidates: [
        candidate(1, true),
        candidate(2),
        candidate(3),
        candidate(4),
        {
          ...candidate(5),
          selectable: false,
          identity: { outcome: 'verified_work_match', reasons: ['accepted_work_identifier'] },
          observation: {
            composition: 'dense',
            medium: 'photographic',
            promotional_text: 'absent',
            tie_in_branding: 'absent',
          },
        },
        {
          ...candidate(6),
          selectable: false,
          identity: { outcome: 'verified_work_match', reasons: ['accepted_work_identifier'] },
          observation: {
            composition: 'dense',
            medium: 'photographic',
            promotional_text: 'present',
            tie_in_branding: 'absent',
          },
        },
      ],
      incumbent: { reference: reference(1), state: 'available' as const },
      omissions: [{ reason: 'preview_unavailable', reference: 'retained-unavailable-source' }],
      policy: {
        recommended_candidate_reference: reference(3),
        acceptable_candidate_references: [reference(2), reference(3), reference(4)],
        status: 'abstained',
        reasons: ['observations_ambiguous'],
      },
      policyPreferences: {
        avoidPromotionalText: true,
        avoidTieInBranding: false,
        preferMinimalComposition: false,
      },
      policyReference: `cover_policy_${'3'.repeat(64)}`,
      reviewerStatus: 'completed',
      targetFingerprint: 'fingerprint-cover',
    } as const
    const coverField = {
      ...base,
      destination: 'cover',
      currentValue: `cover_${'1'.repeat(64)}`,
      currentRaw: `cover_${'1'.repeat(64)}`,
      proposalId: 'proposal-cover',
      targetFingerprint: 'fingerprint-cover',
      proposedValues: [{ kind: 'cover', value: `cover_${'2'.repeat(64)}` }],
      coverConsideration: consideration,
    }
    const http: ReviewHttp = {
      ...createHttp(),
      getCaseDetail: vi.fn(async () => ({ ...detail, fields: [coverField] })),
    }
    renderRoute('/library?lens=all&preview=case-1&field=cover', http)

    const comparison = await screen.findByLabelText('Choose a cover')
    expect(
      within(comparison).getByText('Recommendation is unavailable; comparison remains manual.'),
    ).toBeVisible()
    expect(within(comparison).queryByText(/Recommended/u)).not.toBeInTheDocument()
    await user.click(within(comparison).getByText('Reasons retained'))
    expect(within(comparison).getByText('Preview unavailable')).toBeVisible()
    expect(within(comparison).getByText('Observations ambiguous')).toBeVisible()
    expect(within(comparison).getByText('Promotional text excluded by policy')).toBeVisible()
    expect(comparison.querySelector('.library-cover-pinned')).not.toBeNull()
    expect(comparison.querySelectorAll('.library-cover-gallery .library-cover-card')).toHaveLength(
      3,
    )
  })

  it('retains an explicit keep-current control when the incumbent preview is unavailable', async () => {
    const user = userEvent.setup()
    const base = detail.fields[0]
    if (!base) throw new Error('cover fixture requires a field')
    const consideration = {
      id: `cover_consideration_${'u'.repeat(64)}`,
      choiceContexts: [
        {
          alternativeId: null,
          selection: 'current',
          evidence: [
            {
              source: 'Local library record',
              role: 'local_presence',
              observation: 'Current cover preview is unavailable; local presence is retained.',
            },
          ],
          limitations: ['preview_unavailable'],
          rationale: 'Keep the retained local cover.',
          recommendation: 'no_recommendation',
          recommendationReason: 'No recommendation retained.',
        },
        {
          alternativeId: `cover_transport_ref_${'a'.repeat(64)}`,
          selection: 'proposed',
          evidence: [],
          limitations: [],
          rationale: 'Selectable retained cover.',
          recommendation: 'no_recommendation',
          recommendationReason: 'No recommendation retained.',
        },
      ],
      current: true,
      candidates: [
        {
          artifactId: `cover_${'a'.repeat(64)}`,
          height: 1200,
          identity: { outcome: 'plausible_match' },
          incumbent: false,
          observation: null,
          originalSha256: 'a'.repeat(64),
          ordinal: 1,
          previewSha256: 'a'.repeat(64),
          reference: `cover_transport_ref_${'a'.repeat(64)}`,
          selectable: true,
          sourceKinds: ['provider'],
          state: 'acceptable',
          width: 800,
        },
      ],
      incumbent: {
        reference: `cover_transport_ref_${'b'.repeat(64)}`,
        state: 'unavailable' as const,
      },
      omissions: [],
      policy: null,
      policyPreferences: {
        avoidPromotionalText: false,
        avoidTieInBranding: false,
        preferMinimalComposition: false,
      },
      policyReference: `cover_policy_${'u'.repeat(64)}`,
      reviewerStatus: 'completed',
      targetFingerprint: 'fingerprint-cover',
    } as const
    const coverField = {
      ...base,
      destination: 'cover',
      proposedValues: [{ kind: 'cover', value: `cover_${'a'.repeat(64)}` }],
      coverConsideration: consideration,
    }
    renderRoute('/library?lens=all&preview=case-1&field=cover', {
      ...createHttp(),
      getCaseDetail: vi.fn(async () => ({ ...detail, fields: [coverField] })),
    })

    expect(await screen.findByRole('button', { name: /Current cover.*Unavailable/u })).toBeVisible()
    const evidence = screen.getByRole('button', { name: 'Evidence for Current cover' })
    await user.click(evidence)
    expect(screen.queryByRole('button', { name: 'Evidence for Current cover' })).toBeNull()
    expect(screen.getByText('Local presence')).toBeVisible()
    expect(screen.getByText('Preview unavailable')).toBeVisible()
    expect(screen.getByRole('button', { name: /Current cover.*Unavailable/u })).toHaveAttribute(
      'aria-pressed',
      'false',
    )
    expect(screen.getByText('Choices').nextElementSibling).toHaveTextContent('3')
  })

  it('closes cached cover evidence after client-side A to B to A work navigation', async () => {
    const user = userEvent.setup()
    const baseField = detail.fields[0]
    const baseWork = library.works[0]
    if (!baseField || !baseWork) throw new Error('cover reset fixture requires a work and field')
    const cover = (id: string): CaseDetailView => ({
      ...detail,
      id,
      fields: [
        {
          ...baseField,
          destination: 'cover',
          proposedValues: [{ kind: 'cover', value: 'cover-alt' }],
          coverConsideration: {
            id: `consideration-${id}`,
            current: true,
            candidates: [
              {
                artifactId: 'cover-current',
                height: 100,
                identity: {},
                incumbent: true,
                observation: null,
                originalSha256: 'a'.repeat(64),
                ordinal: 0,
                previewSha256: 'b'.repeat(64),
                reference: 'current',
                selectable: true,
                sourceKinds: [],
                state: 'available',
                width: 100,
              },
              {
                artifactId: 'cover-alt',
                height: 100,
                identity: {},
                incumbent: false,
                observation: null,
                originalSha256: 'c'.repeat(64),
                ordinal: 1,
                previewSha256: 'd'.repeat(64),
                reference: 'alternative',
                selectable: true,
                sourceKinds: [],
                state: 'available',
                width: 100,
              },
            ],
            choiceContexts: [
              {
                alternativeId: null,
                selection: 'current',
                evidence: [
                  {
                    source: 'Local library record',
                    role: 'local_presence',
                    observation: 'Local cover retained.',
                  },
                ],
                limitations: [],
                rationale: 'Current cover.',
                recommendation: 'no_recommendation',
                recommendationReason: 'No recommendation.',
              },
              {
                alternativeId: 'alternative',
                selection: 'proposed',
                evidence: [],
                limitations: [],
                rationale: 'Alternative cover.',
                recommendation: 'no_recommendation',
                recommendationReason: 'No recommendation.',
              },
            ],
            incumbent: { reference: 'current', state: 'available' },
            omissions: [],
            policy: {
              status: 'abstained',
              recommended_candidate_reference: null,
              acceptable_candidate_references: [],
            },
            policyPreferences: {
              avoidPromotionalText: false,
              avoidTieInBranding: false,
              preferMinimalComposition: false,
            },
            policyReference: 'policy',
            reviewerStatus: 'completed',
            targetFingerprint: 'fingerprint',
          },
        },
      ],
    })
    const twoWorks: LibraryView = {
      ...library,
      works: [
        { ...baseWork, title: 'Work A' },
        { ...baseWork, id: 'case-2', reviewCaseId: 'case-2', title: 'Work B' },
      ],
    }
    const http = {
      ...createHttp(),
      getLibrary: vi.fn(async () => twoWorks),
      getCaseDetail: vi.fn(async (id: string) => cover(id)),
    }
    renderRoute('/library?lens=all&preview=case-1&field=cover', http)
    const evidence = await screen.findByRole('button', { name: 'Evidence for Current cover' })
    await user.click(evidence)
    expect(screen.getByLabelText('Current cover evidence')).toBeVisible()
    await user.click(screen.getByRole('button', { name: /^Work B\./u }))
    await screen.findByRole('button', { name: 'Evidence for Current cover' })
    expect(screen.queryByLabelText('Current cover evidence')).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /^Work A\./u }))
    await screen.findByRole('button', { name: 'Evidence for Current cover' })
    expect(screen.queryByLabelText('Current cover evidence')).not.toBeInTheDocument()
    expect(http.recordDecision).not.toHaveBeenCalled()
  })

  it('prepares a missing cover comparison inside the Library workflow', async () => {
    const user = userEvent.setup()
    const base = detail.fields[0]
    if (!base) throw new Error('cover fixture requires a field')
    const consideration = {
      id: `cover_consideration_${'p'.repeat(64)}`,
      current: true,
      choiceContexts: [
        {
          alternativeId: null,
          selection: 'current',
          evidence: [],
          limitations: [],
          rationale: 'Keep the local cover.',
          recommendation: 'no_recommendation',
          recommendationReason: 'No recommendation retained.',
        },
        {
          alternativeId: `cover_transport_ref_${'a'.repeat(64)}`,
          selection: 'proposed',
          evidence: [],
          limitations: [],
          rationale: 'Selectable retained cover.',
          recommendation: 'no_recommendation',
          recommendationReason: 'No recommendation retained.',
        },
      ],
      candidates: [
        {
          artifactId: `cover_${'a'.repeat(64)}`,
          height: 1200,
          identity: { outcome: 'plausible_match' },
          incumbent: false,
          observation: null,
          originalSha256: 'a'.repeat(64),
          ordinal: 1,
          previewSha256: 'a'.repeat(64),
          reference: `cover_transport_ref_${'a'.repeat(64)}`,
          selectable: true,
          sourceKinds: ['provider'],
          state: 'acceptable',
          width: 800,
        },
      ],
      incumbent: {
        reference: `cover_transport_ref_${'b'.repeat(64)}`,
        state: 'unavailable' as const,
      },
      omissions: [],
      policy: null,
      policyPreferences: {
        avoidPromotionalText: false,
        avoidTieInBranding: false,
        preferMinimalComposition: false,
      },
      policyReference: `cover_policy_${'p'.repeat(64)}`,
      reviewerStatus: 'completed',
      targetFingerprint: 'fingerprint-cover',
    } as const
    const absent: CaseDetailView['fields'][number] = {
      ...base,
      destination: 'cover',
      proposedValues: [{ kind: 'cover', value: `cover_${'a'.repeat(64)}` }],
      coverConsideration: null,
    }
    const prepared: CaseDetailView['fields'][number] = {
      ...absent,
      coverConsideration: consideration,
    }
    let field: CaseDetailView['fields'][number] = absent
    let preparationAttempts = 0
    const retryCoverConsideration = vi.fn(async () => {
      preparationAttempts += 1
      if (preparationAttempts === 1) throw new Error('temporary preparation failure')
      field = prepared
      return consideration
    })
    const http: ReviewHttp = {
      ...createHttp(),
      getCaseDetail: vi.fn(async () => ({ ...detail, fields: [field] })),
      retryCoverConsideration,
    }
    renderRoute('/library?lens=all&preview=case-1&field=cover', http)

    const prepare = await screen.findByRole('button', { name: 'Prepare cover comparison' })
    expect(screen.getByText('Choices').nextElementSibling).toHaveTextContent('0')
    await user.click(prepare)
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'The comparison could not be prepared. The existing cover remains unchanged.',
    )
    expect(prepare).toBeEnabled()
    await user.click(prepare)
    await waitFor(() => expect(retryCoverConsideration).toHaveBeenCalledTimes(2))
    expect(retryCoverConsideration).toHaveBeenLastCalledWith('case-1')
    expect(await screen.findByLabelText('Choose a cover')).toBeVisible()
    expect(screen.getByText('Choices').nextElementSibling).toHaveTextContent('3')
  })

  it('keeps retained per-choice provenance in Library without loading diagnostic evidence', async () => {
    const user = userEvent.setup()
    const titleField = detail.fields[0]
    if (!titleField) throw new Error('workspace fixture requires a title field')
    const http: ReviewHttp = {
      ...createHttp(),
      getCaseDetail: vi.fn(async () => ({
        ...detail,
        fields: [
          {
            ...titleField,
            choiceContexts: [
              {
                alternativeId: null,
                selection: 'current',
                rationale: 'Keep the value currently held by the local library.',
                recommendation: 'no_recommendation',
                recommendationReason:
                  'No deterministic recommendation is retained for the current value.',
                limitations: [],
                evidence: [
                  {
                    source: 'Local library record',
                    observation: 'Title: retained current value',
                    role: 'local_presence',
                  },
                ],
              },
              {
                alternativeId: 'alternative-title-1',
                selection: 'proposed',
                rationale: 'This retained alternative is eligible for owner selection.',
                recommendation: 'no_recommendation',
                recommendationReason:
                  'No deterministic recommendation is retained for this alternative.',
                limitations: [],
                evidence: [
                  {
                    source: 'Open Library',
                    observation: 'Title: New title',
                    role: 'external_corroboration',
                  },
                ],
              },
            ] as const,
          },
        ],
      })),
      getEvidence: vi.fn(async () => {
        throw new Error('diagnostic evidence unavailable')
      }),
    }
    renderRoute('/library?lens=all&preview=case-1&field=title', http)

    const evidenceButton = (await screen.findAllByRole('button', { name: /Evidence for/u }))[0]
    if (!evidenceButton) throw new Error('current choice evidence control is missing')
    await user.click(evidenceButton)
    expect(screen.getByText('Local library record')).toBeVisible()
    expect(screen.getByText('Title: retained current value')).toBeVisible()
    expect(http.getEvidence).not.toHaveBeenCalled()
    expect(screen.queryByLabelText('Comparison condition')).not.toBeInTheDocument()
  })

  it('keeps frozen field positions after an earlier field is decided', async () => {
    const user = userEvent.setup()
    const titleField = detail.fields[0]
    if (!titleField) throw new Error('workspace fixture requires a title field')
    const authorsField = {
      ...titleField,
      destination: 'authors',
      currentValue: 'Ada Example',
      proposalId: 'proposal-authors',
      targetFingerprint: 'fingerprint-authors',
    }
    const contextualField = {
      ...titleField,
      destination: 'publishedYear',
      inventoryRole: 'contextual' as const,
      navigationRole: 'inspect' as const,
      needsAttention: false,
    }
    const initial = { ...detail, fields: [titleField, authorsField, contextualField] }
    const settled = {
      ...detail,
      fields: [
        authorsField,
        contextualField,
        {
          ...titleField,
          needsAttention: false,
          humanDecision: {
            actor: 'owner',
            decision_id: 'decision-title',
            destination: 'title',
            occurred_at: 1,
            outcome: 'keep_current' as const,
            proposal_id: titleField.proposalId,
            review_case_id: 'case-1',
            selected_value: null,
            target_fingerprint: titleField.targetFingerprint,
          },
        },
      ],
    }
    let current: CaseDetailView = initial
    const http: ReviewHttp = {
      ...createHttp(),
      getCaseDetail: vi.fn(async () => current),
      recordDecision: vi.fn(async () => {
        current = settled
        return {
          invalidation_sequences: [],
          schema_version: 'review-decision-v1' as const,
          actor: 'owner' as const,
          decision_id: 'decision-title',
          destination: 'title',
          occurred_at: 1,
          outcome: 'keep_current' as const,
          proposal_id: titleField.proposalId,
          review_case_id: 'case-1',
          selected_value: null,
          target_fingerprint: titleField.targetFingerprint,
        }
      }),
    }
    renderRoute('/library?lens=all&preview=case-1&field=title', http)

    expect(await screen.findByRole('button', { name: /Authors.*Needs review/u })).toBeVisible()
    await user.click(screen.getByRole('button', { name: 'Choose current value: Old title' }))
    await waitFor(() =>
      expect(screen.getByRole('button', { name: /Authors.*Needs review/u })).toHaveAttribute(
        'aria-current',
        'true',
      ),
    )
    // Advancement leaves the next field ready for the keyboard without moving
    // the document to centre the rail cell.
    expect(screen.getByRole('button', { name: 'Choose current value: Ada Example' })).toHaveFocus()
    expect(screen.getByRole('button', { name: /Title.*Decision recorded/u })).toBeVisible()
    await screen.findByText('Decision recorded from the authoritative case.')
    expect(
      within(screen.getByRole('navigation', { name: 'Review fields' }))
        .getAllByRole('button')
        .map((button) => button.textContent),
    ).toEqual(['Title✓', 'Authors•'])
  })

  it('retries the embedded Library decision with the exact preserved command and idempotency key', async () => {
    const user = userEvent.setup()
    const titleField = detail.fields[0]
    if (!titleField) throw new Error('workspace fixture requires a title field')
    const settled: CaseDetailView = {
      ...detail,
      fields: [
        {
          ...titleField,
          needsAttention: false,
          humanDecision: {
            actor: 'owner',
            decision_id: 'decision-retried',
            destination: 'title',
            occurred_at: 1,
            outcome: 'keep_current',
            proposal_id: titleField.proposalId,
            review_case_id: 'case-1',
            selected_value: null,
            target_fingerprint: titleField.targetFingerprint,
          },
        },
      ],
    }
    let current = detail
    let attempts = 0
    const recordDecision = vi.fn<ReviewHttp['recordDecision']>(async () => {
      attempts += 1
      if (attempts === 1) throw new ApiError(503, 'decision_failed')
      current = settled
      return {
        invalidation_sequences: [],
        schema_version: 'review-decision-v1' as const,
        actor: 'owner' as const,
        decision_id: 'decision-retried',
        destination: 'title',
        occurred_at: 1,
        outcome: 'keep_current' as const,
        proposal_id: titleField.proposalId,
        review_case_id: 'case-1',
        selected_value: null,
        target_fingerprint: titleField.targetFingerprint,
      }
    })
    const http: ReviewHttp = {
      ...createHttp(),
      getCaseDetail: vi.fn(async () => current),
      recordDecision,
    }
    renderRoute('/library?lens=all&preview=case-1&field=title', http)

    await user.click(await screen.findByRole('button', { name: 'Choose current value: Old title' }))
    expect(
      await screen.findByText('The decision was not recorded. Your choice is unchanged.'),
    ).toBeVisible()
    expect(
      screen.getByRole('button', { name: 'Choose current value: Old title' }),
    ).not.toHaveTextContent('Recording…')
    const retry = screen.getByRole('button', { name: 'Try the same decision again' })
    expect(retry).toHaveFocus()
    const firstCommand = recordDecision.mock.calls[0]?.[1]
    await user.click(retry)

    await waitFor(() => expect(recordDecision).toHaveBeenCalledTimes(2))
    expect(recordDecision.mock.calls[1]?.[1]).toEqual(firstCommand)
    expect(recordDecision.mock.calls[1]?.[1].idempotencyKey).toBe(firstCommand?.idempotencyKey)
    expect(await screen.findByText('Review complete')).toBeVisible()
    expect(
      screen.getByRole('button', { name: /Title.*Current value retained.*Old title/u }),
    ).toBeVisible()
    expect(document.activeElement).toHaveClass('review-complete')
  })

  it('does not carry failed same-field advancement into a manual field change', async () => {
    const user = userEvent.setup()
    const titleField = detail.fields[0]
    if (!titleField) throw new Error('workspace fixture requires a title field')
    const authorsField = {
      ...titleField,
      destination: 'authors',
      currentValue: 'Ada Example',
      proposalId: 'proposal-authors',
      targetFingerprint: 'fingerprint-authors',
    }
    const http: ReviewHttp = {
      ...createHttp(),
      getCaseDetail: vi.fn(async () => ({ ...detail, fields: [titleField, authorsField] })),
      recordDecision: vi.fn(async () => {
        throw new ApiError(503, 'decision_failed')
      }),
    }
    const scrollTo = vi.mocked(window.scrollTo)
    scrollTo.mockClear()
    renderRoute('/library?lens=all&preview=case-1&field=title', http)

    await user.click(await screen.findByRole('button', { name: 'Choose current value: Old title' }))
    expect(
      await screen.findByText('The decision was not recorded. Your choice is unchanged.'),
    ).toBeVisible()
    scrollTo.mockClear()
    const authors = screen.getByRole('button', { name: /Authors.*Needs review/u })
    await user.click(authors)

    await waitFor(() => expect(authors).toHaveAttribute('aria-current', 'true'))
    expect(authors).toHaveFocus()
    expect(scrollTo).not.toHaveBeenCalledWith({ top: 0 })
  })

  it('preserves final completion when the reviewer moves on while a decision settles', async () => {
    const user = userEvent.setup()
    const titleField = detail.fields[0]
    if (!titleField) throw new Error('workspace fixture requires a title field')
    const recordedAuthors = {
      ...titleField,
      destination: 'authors',
      needsAttention: false,
      proposalId: 'proposal-authors',
      targetFingerprint: 'fingerprint-authors',
      humanDecision: {
        actor: 'owner',
        decision_id: 'decision-authors',
        destination: 'authors',
        occurred_at: 1,
        outcome: 'keep_current' as const,
        proposal_id: 'proposal-authors',
        review_case_id: 'case-1',
        selected_value: null,
        target_fingerprint: 'fingerprint-authors',
      },
    }
    const settled: CaseDetailView = {
      ...detail,
      fields: [
        {
          ...titleField,
          needsAttention: false,
          humanDecision: {
            actor: 'owner',
            decision_id: 'decision-title',
            destination: 'title',
            occurred_at: 1,
            outcome: 'keep_current',
            proposal_id: titleField.proposalId,
            review_case_id: 'case-1',
            selected_value: null,
            target_fingerprint: titleField.targetFingerprint,
          },
        },
        recordedAuthors,
      ],
    }
    let current: CaseDetailView = { ...detail, fields: [titleField, recordedAuthors] }
    const http: ReviewHttp = {
      ...createHttp(),
      getCaseDetail: vi.fn(async () => current),
      recordDecision: vi.fn(async () => {
        current = settled
        return {
          invalidation_sequences: [],
          schema_version: 'review-decision-v1',
          actor: 'owner',
          decision_id: 'decision-title',
          destination: 'title',
          occurred_at: 1,
          outcome: 'keep_current',
          proposal_id: titleField.proposalId,
          review_case_id: 'case-1',
          selected_value: null,
          target_fingerprint: titleField.targetFingerprint,
        }
      }),
    }
    renderRoute('/library?lens=all&preview=case-1&field=title', http)

    await user.click(await screen.findByRole('button', { name: 'Choose current value: Old title' }))
    expect(await screen.findByText('Review complete')).toBeVisible()
    // A recorded field opens for inspection and hands the completion back.
    await user.click(screen.getByRole('button', { name: /Authors.*Decision recorded/u }))
    await user.click(await screen.findByRole('button', { name: '← Back to completion' }))

    expect(await screen.findByText('Review complete')).toBeVisible()
    expect(screen.getByRole('button', { name: /Title.*Old title/u })).toBeVisible()
  })

  it('keeps visible route focus while an uncached selected detail loads', async () => {
    const user = userEvent.setup()
    let release: ((value: CaseDetailView) => void) | undefined
    const pendingDetail = new Promise<CaseDetailView>((resolve) => {
      release = resolve
    })
    renderRoute('/library?lens=all', {
      ...createHttp(),
      getCaseDetail: vi.fn(async () => pendingDetail),
    })

    await user.click(await screen.findByRole('button', { name: /^A Book\./u }))
    expect(await screen.findByText('Loading selected work…')).toBeVisible()
    const loadingRegion = screen.getByText('Loading selected work…').closest('section')
    expect(loadingRegion).toHaveClass('workspace-detail')
    expect(loadingRegion).toHaveAttribute('data-route-focus', 'true')
    await waitFor(() => expect(loadingRegion).toHaveFocus())
    await act(async () => {
      release?.(detail)
    })
    expect(
      await screen.findByRole('button', { name: 'Choose current value: Old title' }),
    ).toBeVisible()
  })

  it('suppresses workspace decisions for a superseded case', async () => {
    const http: ReviewHttp = {
      ...createHttp(),
      getCaseDetail: vi.fn(async () => ({ ...detail, current: false })),
    }
    renderRoute('/library?lens=all&preview=case-1', http)

    expect(
      await screen.findByText(
        'This review case is no longer current. Reopen its current replacement from Library.',
      ),
    ).toHaveAttribute('role', 'alert')
    expect(screen.queryByRole('button', { name: /Current value/ })).toBeNull()
    expect(screen.queryByRole('button', { name: /Specific proposed value/ })).toBeNull()
  })

  it('preserves the selected field in Library and clears it when another work is selected', async () => {
    const user = userEvent.setup()
    const titleField = detail.fields[0]
    const firstWork = library.works[0]
    if (!titleField || !firstWork) throw new Error('workspace fixture requires a field and work')
    const authorsField = {
      ...titleField,
      destination: 'authors',
      proposalId: 'proposal-authors',
      targetFingerprint: 'fingerprint-authors',
    }
    const secondWork = { ...firstWork, id: 'work-2', reviewCaseId: 'case-2', title: 'Second work' }
    const http: ReviewHttp = {
      ...createHttp(),
      getLibrary: vi.fn(async () => ({
        ...library,
        counts: { ...library.counts, all: 2 },
        works: [firstWork, secondWork],
      })),
      getCaseDetail: vi.fn(async (id: string) => ({
        ...detail,
        id,
        title: id === 'case-2' ? 'Second work' : detail.title,
        fields: [titleField, authorsField],
      })),
    }
    const { router } = renderRoute('/library?lens=all&preview=case-1&field=authors', http)

    expect(screen.queryByRole('link', { name: /Open full review|Open all evidence/u })).toBeNull()
    await user.click(await screen.findByRole('button', { name: /^Second work\./u }))
    await waitFor(() =>
      expect(router.state.location.search).toEqual({ lens: 'all', preview: 'case-2' }),
    )
  })

  it('recovers and paginates the contextual attention queue', async () => {
    const user = userEvent.setup()
    let calls = 0
    const item = {
      id: 'case-1',
      destination: 'title',
      title: 'A Book',
      authors: [],
      library: 'Books',
      mediaKind: 'ebook',
      classificationReason: 'Canonical library attention reason',
      notices: ['internal-library-notice'],
      provenance: [],
      workAttentionCount: 1,
    }
    const http: ReviewHttp = {
      ...createHttp(),
      getAttention: vi.fn(async (search: AttentionSearch) => {
        calls += 1
        if (calls === 1) throw new Error('temporary attention failure')
        return search.cursor
          ? { total: 2, items: [{ ...item, id: 'case-2', title: 'Second queued work' }] }
          : { total: 2, nextCursor: 'next', items: [item] }
      }),
    }
    renderRoute('/library?lens=all', http)

    await user.click(await screen.findByRole('button', { name: /Attention/ }))
    await user.click(await screen.findByRole('button', { name: 'Try again' }))
    const rail = screen.getByLabelText('Attention queue')
    await waitFor(() => expect(rail).toHaveFocus())
    expect(within(rail).getByText('A Book')).toBeVisible()
    expect(within(rail).getByText(/Canonical library attention reason/u)).toBeVisible()
    expect(within(rail).queryByText('internal-library-notice')).toBeNull()
    await user.click(screen.getByRole('button', { name: 'Load more attention' }))
    expect(await screen.findByText('Second queued work')).toBeVisible()
    expect(http.getAttention).toHaveBeenLastCalledWith({ cursor: 'next' }, expect.any(AbortSignal))
  })

  it('uses the review-case identity for workspace selection and advances through the frozen attention cohort', async () => {
    const user = userEvent.setup()
    const titleField = detail.fields[0]
    const firstWork = library.works[0]
    if (!titleField) throw new Error('detail fixture requires a title field')
    if (!firstWork) throw new Error('library fixture requires a work')
    const selectedLibrary: LibraryView = {
      ...library,
      works: [{ ...firstWork, id: 'work-identity-is-not-case-identity' }],
    }
    const settled: CaseDetailView = {
      ...detail,
      fields: [
        {
          ...titleField,
          needsAttention: false,
          humanDecision: {
            actor: 'owner',
            decision_id: 'decision-identity',
            destination: 'title',
            occurred_at: 1,
            outcome: 'keep_current',
            proposal_id: titleField.proposalId,
            review_case_id: 'case-1',
            selected_value: null,
            target_fingerprint: titleField.targetFingerprint,
          },
        },
      ],
    }
    let current = detail
    const http: ReviewHttp = {
      ...createHttp(),
      getLibrary: vi.fn(async () => selectedLibrary),
      getAttention: vi.fn(async () => ({
        total: 2,
        items: [
          {
            id: 'case-1',
            destination: 'title',
            title: 'A Book',
            authors: [],
            library: 'Books',
            mediaKind: 'ebook',
            notices: [],
            provenance: ['Local catalogue'],
            workAttentionCount: 1,
          },
          {
            id: 'case-2',
            destination: 'authors',
            title: 'Off-page successor',
            authors: ['Ada Example'],
            library: 'Project Gutenberg',
            mediaKind: 'audiobook',
            notices: [],
            provenance: ['Project Gutenberg record'],
            workAttentionCount: 1,
          },
        ],
      })),
      getCaseDetail: vi.fn(async (id: string) =>
        id === 'case-2' ? { ...detail, id, title: 'Off-page successor' } : current,
      ),
      recordDecision: vi.fn(async () => {
        current = settled
        return {
          invalidation_sequences: [],
          schema_version: 'review-decision-v1',
          actor: 'owner',
          decision_id: 'decision-identity',
          destination: 'title',
          occurred_at: 1,
          outcome: 'keep_current',
          proposal_id: titleField.proposalId,
          review_case_id: 'case-1',
          selected_value: null,
          target_fingerprint: titleField.targetFingerprint,
        }
      }),
    }
    const { router } = renderRoute('/library?lens=all', http)

    expect(screen.queryByRole('link', { name: 'Attention' })).toBeNull()
    await user.click(await screen.findByRole('button', { name: /^A Book\./u }))
    const currentValue = (await screen.findByText('Old title')).closest('button')
    if (!currentValue)
      throw new Error('workspace fixture requires a current-value decision control')
    await user.click(currentValue)

    expect(await screen.findByText('Review complete')).toBeVisible()
    await user.click(screen.getByRole('button', { name: 'Review next work →' }))
    await waitFor(() =>
      expect(router.state.location.search).toEqual({
        lens: 'all',
        preview: 'case-2',
        field: 'authors',
      }),
    )
    expect(await screen.findByRole('heading', { name: 'Off-page successor' })).toBeVisible()
    expect(http.getCaseDetail).toHaveBeenCalledWith('case-1', expect.any(AbortSignal))
    expect(http.getCaseDetail).toHaveBeenCalledWith('case-2', expect.any(AbortSignal))
  })

  it('retains completion identity and a return path after the completed work leaves its lens', async () => {
    const user = userEvent.setup()
    const titleField = detail.fields[0]
    const firstWork = library.works[0]
    if (!titleField || !firstWork)
      throw new Error('completion fixture requires a work and title field')
    const settled: CaseDetailView = {
      ...detail,
      fields: [
        {
          ...titleField,
          needsAttention: false,
          humanDecision: {
            actor: 'owner',
            decision_id: 'decision-complete',
            destination: 'title',
            occurred_at: 1,
            outcome: 'keep_current',
            proposal_id: titleField.proposalId,
            review_case_id: 'case-1',
            selected_value: null,
            target_fingerprint: titleField.targetFingerprint,
          },
        },
      ],
    }
    const successor = {
      id: 'case-2',
      destination: 'authors',
      title: 'Next work',
      authors: [],
      library: 'Books',
      mediaKind: 'ebook',
      notices: [],
      provenance: [],
      workAttentionCount: 1,
    }
    let current = detail
    let libraryReads = 0
    let attentionReads = 0
    const http: ReviewHttp = {
      ...createHttp(),
      getLibrary: vi.fn(async () =>
        ++libraryReads === 1
          ? { ...library, lens: 'needs_attention' as const }
          : {
              ...library,
              lens: 'needs_attention' as const,
              counts: { attention: 1, all: 1, clear: 0 },
              works: [],
            },
      ),
      getAttention: vi.fn(async () =>
        ++attentionReads === 1
          ? {
              total: 2,
              items: [
                { ...successor, id: 'case-1', destination: 'title', title: firstWork.title },
                successor,
              ],
            }
          : { total: 1, items: [successor] },
      ),
      getCaseDetail: vi.fn(async () => current),
      recordDecision: vi.fn(async () => {
        current = settled
        return {
          invalidation_sequences: [90, 91, 92],
          schema_version: 'review-decision-v1',
          actor: 'owner',
          decision_id: 'decision-complete',
          destination: 'title',
          occurred_at: 1,
          outcome: 'keep_current',
          proposal_id: titleField.proposalId,
          review_case_id: 'case-1',
          selected_value: null,
          target_fingerprint: titleField.targetFingerprint,
        }
      }),
    }
    const updates = createLiveStream()
    renderRoute('/library?lens=needs_attention&preview=case-1&field=title', http, updates)

    await user.click(await screen.findByRole('button', { name: 'Choose current value: Old title' }))
    const complete = (await screen.findByText('Review complete')).closest('article')
    if (!complete) throw new Error('completion boundary is required')
    expect(within(complete).getByRole('heading', { name: 'Review complete' })).toBeVisible()
    expect(screen.getByRole('heading', { level: 2, name: 'A Book' })).toBeVisible()
    expect(screen.getByRole('button', { name: 'Review next work →' })).toBeVisible()

    await user.click(screen.getByRole('button', { name: /Title.*Old title/u }))
    expect(await screen.findByRole('button', { name: '← Back to completion' })).toBeVisible()
    // Revisiting a recorded field restores every eligible option and marks the
    // recorded one, so the owner can change it rather than only read it.
    expect(screen.getByRole('button', { name: 'Choose current value: Old title' })).toHaveAttribute(
      'aria-pressed',
      'true',
    )
    expect(
      screen.getByRole('button', { name: 'Choose proposed value: New title' }),
    ).toHaveAttribute('aria-pressed', 'false')
    await user.click(screen.getByRole('button', { name: '← Back to completion' }))
    expect(await screen.findByText('Review complete')).toBeVisible()
    // The work left its lens on the server when the decision landed, and the
    // decision's own announcement came back to the tab that caused it. It is
    // recognised rather than acted on, so no request was spent finding out
    // what this owner already knows (#635).
    await act(async () => {
      updates.announce({ sequence: 91, subject: 'case-1', topic: 'library' })
    })
    expect([libraryReads, attentionReads]).toEqual([1, 1])
    // Leaving settles it once, and the completed work is gone from the lens
    // without a reload.
    await user.click(screen.getByRole('button', { name: 'Back to works' }))
    expect(
      await screen.findByText('No exact-current works match this search and lens.'),
    ).toBeVisible()
    await waitFor(() => expect([libraryReads, attentionReads]).toEqual([2, 2]))
  })

  const renderLaterPageProviderRetry = () => {
    const predecessor = { ...detail, id: 'case-1', current: true }
    const successor: CaseDetailView = {
      ...detail,
      id: 'case-2',
      current: true,
      title: 'Recovered work',
      fields: detail.fields.map((field) => ({ ...field, needsAttention: false })),
    }
    const unrelated: CaseDetailView = {
      ...detail,
      id: 'case-3',
      target: { ...detail.target, item: 'Other work' },
      title: 'Other work',
    }
    let libraryReads = 0
    const http: ReviewHttp = {
      ...createHttp(),
      getLibrary: vi.fn(async () =>
        ++libraryReads === 1
          ? {
              ...library,
              lens: 'needs_attention' as const,
              works: library.works.map((work) => ({
                ...work,
                preparationOutcomes: [
                  {
                    providerRequests: 1,
                    laneKind: 'work',
                    provider: 'catalogue',
                    reason: 'Provider retry is awaiting its next permitted request.',
                    status: 'rate_limited',
                  },
                ],
              })),
            }
          : {
              ...library,
              lens: 'needs_attention' as const,
              counts: { attention: 0, all: 1, clear: 1 },
              works: [],
            },
      ),
      getCaseDetail: vi.fn(async (id: string) =>
        id === 'case-2'
          ? successor
          : id === 'case-3'
            ? unrelated
            : libraryReads > 1
              ? { ...predecessor, current: false }
              : predecessor,
      ),
    }
    const updates = createLiveStream()
    const { router } = renderRoute(
      '/library?lens=needs_attention&cursor=later-page&preview=case-1&field=title',
      http,
      updates,
    )
    return { http, libraryReads: () => libraryReads, router, updates }
  }

  it('follows an off-page successor named by a null-subject invalidation', async () => {
    const predecessor = { ...detail, id: 'case-1', current: true }
    const successor = { ...detail, id: 'case-2', current: true, title: 'Null-subject successor' }
    let stale = false
    const updates = createLiveStream()
    const http: ReviewHttp = {
      ...createHttp(),
      getLibrary: vi.fn(async () => (stale ? { ...library, works: [] } : library)),
      getCaseDetail: vi.fn(async (id: string) =>
        id === 'case-2' ? successor : { ...predecessor, current: !stale },
      ),
      getCaseSuccessor: vi.fn(async () => (stale ? 'case-2' : null)),
    }
    const { router } = renderRoute('/library?lens=all&preview=case-1&field=title', http, updates)
    expect(await screen.findByRole('heading', { name: 'A Book' })).toBeVisible()

    stale = true
    await act(async () => {
      updates.announce({ sequence: 76, subject: null, topic: 'library' })
      await new Promise((resolve) => setTimeout(resolve, coalesceMilliseconds + 20))
    })

    expect(http.getCaseSuccessor).toHaveBeenCalledTimes(1)
    expect(await screen.findByRole('heading', { name: 'Null-subject successor' })).toBeVisible()
    expect(http.getCaseSuccessor).toHaveBeenCalledWith('case-1', expect.any(AbortSignal))
    expect(router.state.location.search.preview).toBe('case-2')
  })

  it('retries a failed null-subject lineage lookup with the invalidated Library query', async () => {
    const predecessor = { ...detail, id: 'case-1', current: true }
    const successor = { ...detail, id: 'case-2', current: true, title: 'Retried successor' }
    let stale = false
    const updates = createLiveStream()
    const getCaseSuccessor = vi
      .fn<ReviewHttp['getCaseSuccessor']>()
      .mockRejectedValueOnce(new Error('synthetic transport failure'))
      .mockResolvedValueOnce('case-2')
    const http: ReviewHttp = {
      ...createHttp(),
      getLibrary: vi.fn(async () => (stale ? { ...library, works: [] } : library)),
      getCaseDetail: vi.fn(async (id: string) =>
        id === 'case-2' ? successor : { ...predecessor, current: !stale },
      ),
      getCaseSuccessor,
    }
    const { router } = renderRoute('/library?lens=all&preview=case-1&field=title', http, updates)
    expect(await screen.findByRole('heading', { name: 'A Book' })).toBeVisible()

    stale = true
    await act(async () => {
      updates.announce({ sequence: 78, subject: null, topic: 'library' })
      await new Promise((resolve) => setTimeout(resolve, coalesceMilliseconds + 20))
    })

    expect(await screen.findByRole('heading', { name: 'Retried successor' })).toBeVisible()
    await waitFor(() => expect(getCaseSuccessor).toHaveBeenCalledTimes(2))
    expect(router.state.location.search.preview).toBe('case-2')
  })

  it('clears a null-subject reconciliation after an authoritative no-successor result', async () => {
    const updates = createLiveStream()
    const getCaseSuccessor = vi.fn<ReviewHttp['getCaseSuccessor']>().mockResolvedValue(null)
    const http: ReviewHttp = {
      ...createHttp(),
      getLibrary: vi.fn(async () => ({ ...library })),
      getCaseSuccessor,
    }
    renderRoute('/library?lens=all&preview=case-1&field=title', http, updates)
    expect(await screen.findByRole('heading', { name: 'A Book' })).toBeVisible()

    await act(async () => {
      updates.announce({ sequence: 79, subject: null, topic: 'library' })
      await new Promise((resolve) => setTimeout(resolve, coalesceMilliseconds * 2))
      window.dispatchEvent(new Event('focus'))
      window.dispatchEvent(new Event('online'))
      await new Promise((resolve) => setTimeout(resolve, coalesceMilliseconds + 20))
    })

    expect(getCaseSuccessor).toHaveBeenCalledTimes(1)
  })

  it('retires an exhausted null-subject cycle across focus, reconnect, and selection re-enable', async () => {
    const first = { ...detail, id: 'case-1', current: true }
    const second = { ...detail, id: 'case-2', current: true, title: 'New selection' }
    const updates = createLiveStream()
    const getCaseSuccessor = vi
      .fn<ReviewHttp['getCaseSuccessor']>()
      .mockRejectedValueOnce(new Error('first synthetic transport failure'))
      .mockRejectedValueOnce(new Error('second synthetic transport failure'))
    const http: ReviewHttp = {
      ...createHttp(),
      getCaseDetail: vi.fn(async (id: string) => (id === 'case-2' ? second : first)),
      getCaseSuccessor,
    }
    const { queryClient, router } = renderRoute(
      '/library?lens=all&preview=case-1&field=title',
      http,
      updates,
    )
    expect(await screen.findByRole('heading', { name: 'A Book' })).toBeVisible()

    await act(async () => {
      updates.announce({ sequence: 80, subject: null, topic: 'library' })
      await new Promise((resolve) => setTimeout(resolve, coalesceMilliseconds * 2 + 40))
    })
    await waitFor(() => expect(getCaseSuccessor).toHaveBeenCalledTimes(2))

    await act(async () => {
      window.dispatchEvent(new Event('focus'))
      window.dispatchEvent(new Event('online'))
      await router.navigate({
        to: '/library',
        search: { lens: 'all', preview: 'case-2', field: 'title' },
      })
      await router.navigate({
        to: '/library',
        search: { lens: 'all', preview: 'case-1', field: 'title' },
      })
      await new Promise((resolve) => setTimeout(resolve, coalesceMilliseconds * 2 + 40))
    })

    expect(await screen.findByRole('heading', { name: 'A Book' })).toBeVisible()
    await waitFor(() => expect(getCaseSuccessor).toHaveBeenCalledTimes(2))

    cleanup()
    render(
      <QueryClientProvider client={queryClient}>
        <RouterProvider router={router} />
      </QueryClientProvider>,
    )
    await new Promise((resolve) => setTimeout(resolve, coalesceMilliseconds + 20))
    expect(getCaseSuccessor).toHaveBeenCalledTimes(2)
  })

  it('retires a mid-flight null-subject generation when its selection leaves and returns', async () => {
    const first = { ...detail, id: 'case-1', current: true }
    const second = { ...detail, id: 'case-2', current: true, title: 'New selection' }
    const updates = createLiveStream()
    let rejectLookup: ((reason: Error) => void) | undefined
    const getCaseSuccessor = vi
      .fn<ReviewHttp['getCaseSuccessor']>()
      .mockImplementationOnce(
        () =>
          new Promise<string | null>((_resolve, reject) => {
            rejectLookup = reject
          }),
      )
      .mockResolvedValueOnce('case-2')
    const http: ReviewHttp = {
      ...createHttp(),
      getCaseDetail: vi.fn(async (id: string) => (id === 'case-2' ? second : first)),
      getCaseSuccessor,
    }
    const { router } = renderRoute('/library?lens=all&preview=case-1&field=title', http, updates)
    expect(await screen.findByRole('heading', { name: 'A Book' })).toBeVisible()

    updates.announce({ sequence: 83, subject: null, topic: 'library' })
    await waitFor(() => expect(getCaseSuccessor).toHaveBeenCalledTimes(1))
    await act(async () => {
      await router.navigate({
        to: '/library',
        search: { lens: 'all', preview: 'case-2', field: 'title' },
      })
      await router.navigate({
        to: '/library',
        search: { lens: 'all', preview: 'case-1', field: 'title' },
      })
      rejectLookup?.(new Error('stale transport failure'))
      await new Promise((resolve) => setTimeout(resolve, coalesceMilliseconds * 2 + 40))
    })

    expect(router.state.location.search.preview).toBe('case-1')
    expect(getCaseSuccessor).toHaveBeenCalledTimes(1)
  })

  it('starts a fresh bounded cycle when a later null-subject invalidation repeats its predecessor', async () => {
    const successor = { ...detail, id: 'case-2', current: true, title: 'Later successor' }
    let stale = false
    const updates = createLiveStream()
    const getCaseSuccessor = vi
      .fn<ReviewHttp['getCaseSuccessor']>()
      .mockRejectedValueOnce(new Error('first synthetic transport failure'))
      .mockRejectedValueOnce(new Error('second synthetic transport failure'))
      .mockRejectedValueOnce(new Error('third synthetic transport failure'))
      .mockResolvedValueOnce('case-2')
    const http: ReviewHttp = {
      ...createHttp(),
      getLibrary: vi.fn(async () => (stale ? { ...library, works: [] } : library)),
      getCaseDetail: vi.fn(async (id: string) =>
        id === 'case-2' ? successor : { ...detail, id: 'case-1', current: !stale },
      ),
      getCaseSuccessor,
    }
    const { router } = renderRoute('/library?lens=all&preview=case-1&field=title', http, updates)
    expect(await screen.findByRole('heading', { name: 'A Book' })).toBeVisible()

    await act(async () => {
      updates.announce({ sequence: 81, subject: null, topic: 'library' })
      await new Promise((resolve) => setTimeout(resolve, coalesceMilliseconds * 2 + 40))
    })
    await waitFor(() => expect(getCaseSuccessor).toHaveBeenCalledTimes(2))

    stale = true
    await act(async () => {
      updates.announce({ sequence: 82, subject: null, topic: 'library' })
      await new Promise((resolve) => setTimeout(resolve, coalesceMilliseconds + 20))
    })

    expect(await screen.findByRole('heading', { name: 'Later successor' })).toBeVisible()
    expect(getCaseSuccessor).toHaveBeenCalledTimes(4)
    expect(router.state.location.search.preview).toBe('case-2')
  })

  it('reconciles a buffered successor after deferred detail proves its predecessor historical', async () => {
    const predecessor = { ...detail, id: 'case-1', current: false }
    const successor = { ...detail, id: 'case-2', current: true, title: 'Deferred successor' }
    let stale = false
    let resolveInitial: ((value: typeof predecessor) => void) | undefined
    const initial = new Promise<typeof predecessor>((resolve) => {
      resolveInitial = resolve
    })
    let firstDetail = true
    const updates = createLiveStream()
    const http: ReviewHttp = {
      ...createHttp(),
      getLibrary: vi.fn(async () => (stale ? { ...library, works: [] } : library)),
      getCaseDetail: vi.fn(async (id: string) => {
        if (id === 'case-2') return successor
        if (firstDetail) {
          firstDetail = false
          return initial
        }
        return predecessor
      }),
      getCaseSuccessor: vi.fn(async () => (stale ? 'case-2' : null)),
    }
    const { router } = renderRoute('/library?lens=all&preview=case-1&field=title', http, updates)
    await waitFor(() => expect(http.getCaseDetail).toHaveBeenCalled())

    stale = true
    await act(async () => {
      updates.announce({ sequence: 77, subject: null, topic: 'library' })
      await new Promise((resolve) => setTimeout(resolve, coalesceMilliseconds + 20))
    })
    resolveInitial?.(predecessor)

    expect(await screen.findByRole('heading', { name: 'Deferred successor' })).toBeVisible()
    expect(router.state.location.search.preview).toBe('case-2')
  })

  it('buffers a named successor until deferred detail establishes the selected predecessor', async () => {
    const predecessor = { ...detail, id: 'case-1', current: false }
    const successor = { ...detail, id: 'case-2', current: true, title: 'Buffered successor' }
    let stale = false
    let resolveInitial: ((value: typeof predecessor) => void) | undefined
    const initial = new Promise<typeof predecessor>((resolve) => {
      resolveInitial = resolve
    })
    let firstDetail = true
    const updates = createLiveStream()
    const getCaseSuccessor = vi.fn(async () => null)
    const http: ReviewHttp = {
      ...createHttp(),
      getLibrary: vi.fn(async () => (stale ? { ...library, works: [] } : library)),
      getCaseDetail: vi.fn(async (id: string) => {
        if (id === 'case-2') return successor
        if (firstDetail) {
          firstDetail = false
          return initial
        }
        return predecessor
      }),
      getCaseSuccessor,
    }
    const { router } = renderRoute('/library?lens=all&preview=case-1&field=title', http, updates)
    await waitFor(() =>
      expect(http.getCaseDetail).toHaveBeenCalledWith('case-1', expect.any(AbortSignal)),
    )

    stale = true
    await act(async () => {
      updates.announce({ sequence: 78, subject: 'case-2', topic: 'library' })
      await new Promise((resolve) => setTimeout(resolve, coalesceMilliseconds + 20))
    })
    resolveInitial?.(predecessor)

    expect(await screen.findByRole('heading', { name: 'Buffered successor' })).toBeVisible()
    expect(router.state.location.search.preview).toBe('case-2')
    expect(getCaseSuccessor).not.toHaveBeenCalled()
  })

  it('follows a rightful later-page provider-retry successor when an unrelated frame arrives last', async () => {
    const { http, libraryReads, router, updates } = renderLaterPageProviderRetry()

    expect(await screen.findByRole('heading', { level: 2, name: 'A Book' })).toBeVisible()
    await act(async () => {
      updates.announce({ sequence: 76, subject: 'case-2', topic: 'library' })
      updates.announce({ sequence: 77, subject: 'case-3', topic: 'library' })
    })

    await waitFor(() =>
      expect(router.state.location.search).toEqual({
        lens: 'needs_attention',
        cursor: 'later-page',
        preview: 'case-2',
        field: 'title',
      }),
    )
    expect(await screen.findByRole('heading', { level: 2, name: 'Recovered work' })).toBeVisible()
    expect(http.getCaseDetail).toHaveBeenCalledWith('case-2')
    expect(http.getCaseDetail).not.toHaveBeenCalledWith('case-3')
    await waitFor(() => expect(libraryReads()).toBe(2))
  })

  it('inspects every coalesced later-page provider-retry candidate until it finds the rightful successor', async () => {
    const { http, libraryReads, router, updates } = renderLaterPageProviderRetry()

    expect(await screen.findByRole('heading', { level: 2, name: 'A Book' })).toBeVisible()
    await act(async () => {
      updates.announce({ sequence: 76, subject: 'case-3', topic: 'library' })
      updates.announce({ sequence: 77, subject: 'case-2', topic: 'library' })
    })

    await waitFor(() =>
      expect(router.state.location.search).toEqual({
        lens: 'needs_attention',
        cursor: 'later-page',
        preview: 'case-2',
        field: 'title',
      }),
    )
    expect(await screen.findByRole('heading', { level: 2, name: 'Recovered work' })).toBeVisible()
    expect(http.getCaseDetail).toHaveBeenCalledWith('case-3')
    expect(http.getCaseDetail).toHaveBeenCalledWith('case-2')
    await waitFor(() => expect(libraryReads()).toBe(2))
  })

  it('keeps a later-page provider-retry selection when no coalesced candidate has its target', async () => {
    const { http, libraryReads, router, updates } = renderLaterPageProviderRetry()

    expect(await screen.findByRole('heading', { level: 2, name: 'A Book' })).toBeVisible()
    await act(async () => {
      updates.announce({ sequence: 76, subject: 'case-3', topic: 'library' })
    })

    await waitFor(() => expect(libraryReads()).toBe(2))
    await waitFor(() => expect(http.getCaseDetail).toHaveBeenCalledWith('case-3'))
    expect(router.state.location.search.preview).toBe('case-1')
    expect(http.getCaseDetail).not.toHaveBeenCalledWith('case-2')
  })

  it('reconciles a later provider-retry successor after an unchanged no-match batch without looping', async () => {
    const { http, libraryReads, router, updates } = renderLaterPageProviderRetry()
    const detailReads = (id: string) =>
      vi.mocked(http.getCaseDetail).mock.calls.filter(([requested]) => requested === id).length

    expect(await screen.findByRole('heading', { level: 2, name: 'A Book' })).toBeVisible()
    await act(async () => {
      updates.announce({ sequence: 76, subject: 'case-3', topic: 'library' })
    })
    await waitFor(() => expect(libraryReads()).toBe(2))
    await waitFor(() => expect(detailReads('case-3')).toBe(1))
    expect(detailReads('case-1')).toBe(2)
    await new Promise((resolve) => setTimeout(resolve, coalesceMilliseconds + 20))
    expect(libraryReads()).toBe(2)
    expect(detailReads('case-1')).toBe(2)
    expect(detailReads('case-3')).toBe(1)

    await act(async () => {
      updates.announce({ sequence: 77, subject: 'case-2', topic: 'library' })
    })
    await waitFor(() =>
      expect(router.state.location.search).toEqual({
        lens: 'needs_attention',
        cursor: 'later-page',
        preview: 'case-2',
        field: 'title',
      }),
    )
    expect(await screen.findByRole('heading', { level: 2, name: 'Recovered work' })).toBeVisible()
    await waitFor(() => expect(libraryReads()).toBe(3))
    expect(detailReads('case-1')).toBe(3)
    expect(detailReads('case-3')).toBe(1)
    // One read proves target identity before navigation; the other is the
    // ordinary selected-case query for the replacement URL.
    expect(detailReads('case-2')).toBe(2)
  })

  it('acts on every Library announcement except the echo of its own decision', async () => {
    const user = userEvent.setup()
    const titleField = detail.fields[0]
    if (!titleField) throw new Error('workspace fixture requires a title field')
    const settled: CaseDetailView = {
      ...detail,
      fields: [
        {
          ...titleField,
          needsAttention: false,
          humanDecision: {
            actor: 'owner',
            decision_id: 'd',
            destination: 'title',
            occurred_at: 1,
            outcome: 'keep_current',
            proposal_id: titleField.proposalId,
            review_case_id: 'case-1',
            selected_value: null,
            target_fingerprint: titleField.targetFingerprint,
          },
        },
      ],
    }
    let current = detail
    let libraryReads = 0
    let attentionReads = 0
    const http: ReviewHttp = {
      ...createHttp(),
      getLibrary: vi.fn(async () => {
        libraryReads += 1
        return library
      }),
      getAttention: vi.fn(async () => {
        attentionReads += 1
        return { total: 0, items: [] }
      }),
      getCaseDetail: vi.fn(async () => current),
      // The positions the server says this decision caused. 41 is its Library
      // row, which is the only one this tab may recognise.
      recordDecision: vi.fn(async () => {
        current = settled
        return {
          invalidation_sequences: [40, 41, 42],
          schema_version: 'review-decision-v1' as const,
          actor: 'owner' as const,
          decision_id: 'decision-echo',
          destination: 'title',
          occurred_at: 1,
          outcome: 'keep_current' as const,
          proposal_id: titleField.proposalId,
          review_case_id: 'case-1',
          selected_value: null,
          target_fingerprint: titleField.targetFingerprint,
        }
      }),
    }
    const updates = createLiveStream()
    renderRoute('/library?lens=all&preview=case-1&field=title', http, updates)
    expect(await screen.findByRole('heading', { level: 2, name: 'A Book' })).toBeVisible()
    await waitFor(() => expect([libraryReads, attentionReads]).toEqual([1, 1]))

    const announce = async (update: Parameters<typeof updates.announce>[0]) => {
      await act(async () => {
        updates.announce(update)
        await new Promise((resolve) => setTimeout(resolve, coalesceMilliseconds + 20))
      })
    }

    await user.click(screen.getByRole('button', { name: 'Choose current value: Old title' }))
    expect(await screen.findByText('Review complete')).toBeVisible()
    // Recording spends nothing on the Library by itself: #635's cost is gone
    // from the client as well as from the wait.
    expect([libraryReads, attentionReads]).toEqual([1, 1])

    // The echo of this tab's own decision. Recognised by position, so it is
    // settled rather than acted on.
    await announce({ sequence: 41, subject: 'case-1', topic: 'library' })
    expect([libraryReads, attentionReads]).toEqual([1, 1])

    // Everything else still streams in while the owner keeps recording. A work
    // finishing preparation names its own case, which is not this one.
    await announce({ sequence: 55, subject: 'case-2', topic: 'library' })
    await waitFor(() => expect([libraryReads, attentionReads]).toEqual([2, 2]))

    // A work that could not finish names no case at all.
    await announce({ sequence: 56, subject: null, topic: 'library' })
    await waitFor(() => expect([libraryReads, attentionReads]).toEqual([3, 3]))

    // A subjectless frame is what the channel emits when a subscription
    // outgrows its bound and collapses to topic-wide, keeping the highest
    // position it replaced -- which can be one this tab caused. Acting on it is
    // one redundant read; recognising it would discard every work collapsed
    // into it, so position alone is never enough.
    await announce({ sequence: 42, subject: null, topic: 'library' })
    await waitFor(() => expect([libraryReads, attentionReads]).toEqual([4, 4]))

    // The positions this decision caused on other topics are never treated as
    // Library echoes, whatever they carry.
    await announce({ sequence: 40, subject: 'case-1', topic: 'case' })
    expect([libraryReads, attentionReads]).toEqual([4, 4])

    // A read taken for another reason discharges what the echo deferred, so
    // leaving costs nothing more.
    await user.click(screen.getByRole('button', { name: 'Back to works' }))
    expect(await screen.findByRole('heading', { level: 2, name: 'Works' })).toBeVisible()
    expect([libraryReads, attentionReads]).toEqual([4, 4])
  })

  it('settles a recognised echo on the way out when nothing else read the Library', async () => {
    const user = userEvent.setup()
    const titleField = detail.fields[0]
    if (!titleField) throw new Error('workspace fixture requires a title field')
    const settled: CaseDetailView = {
      ...detail,
      fields: [
        {
          ...titleField,
          needsAttention: false,
          humanDecision: {
            actor: 'owner',
            decision_id: 'd',
            destination: 'title',
            occurred_at: 1,
            outcome: 'keep_current',
            proposal_id: titleField.proposalId,
            review_case_id: 'case-1',
            selected_value: null,
            target_fingerprint: titleField.targetFingerprint,
          },
        },
      ],
    }
    let current = detail
    let libraryReads = 0
    let attentionReads = 0
    const http: ReviewHttp = {
      ...createHttp(),
      getLibrary: vi.fn(async () => {
        libraryReads += 1
        return library
      }),
      getAttention: vi.fn(async () => {
        attentionReads += 1
        return { total: 0, items: [] }
      }),
      getCaseDetail: vi.fn(async () => current),
      recordDecision: vi.fn(async () => {
        current = settled
        return {
          invalidation_sequences: [70, 71, 72],
          schema_version: 'review-decision-v1' as const,
          actor: 'owner' as const,
          decision_id: 'decision-settle',
          destination: 'title',
          occurred_at: 1,
          outcome: 'keep_current' as const,
          proposal_id: titleField.proposalId,
          review_case_id: 'case-1',
          selected_value: null,
          target_fingerprint: titleField.targetFingerprint,
        }
      }),
    }
    const updates = createLiveStream()
    renderRoute('/library?lens=all&preview=case-1&field=title', http, updates)
    expect(await screen.findByRole('heading', { level: 2, name: 'A Book' })).toBeVisible()
    await waitFor(() => expect([libraryReads, attentionReads]).toEqual([1, 1]))

    await user.click(screen.getByRole('button', { name: 'Choose current value: Old title' }))
    expect(await screen.findByText('Review complete')).toBeVisible()
    await act(async () => {
      updates.announce({ sequence: 71, subject: 'case-1', topic: 'library' })
      await new Promise((resolve) => setTimeout(resolve, coalesceMilliseconds + 20))
    })
    expect([libraryReads, attentionReads]).toEqual([1, 1])

    // The row behind the owner is correct when they return to it, with no
    // reload and no poll -- which is what #641 promises the recording tab too.
    await user.click(screen.getByRole('button', { name: 'Back to works' }))
    await waitFor(() => expect([libraryReads, attentionReads]).toEqual([2, 2]))

    // And leaving a case that recognised nothing costs nothing.
    await user.click(await screen.findByRole('button', { name: /^A Book\./u }))
    expect(await screen.findByRole('heading', { level: 2, name: 'A Book' })).toBeVisible()
    await user.click(screen.getByRole('button', { name: 'Back to works' }))
    expect(await screen.findByRole('heading', { level: 2, name: 'Works' })).toBeVisible()
    expect([libraryReads, attentionReads]).toEqual([2, 2])
  })

  it('holds a Library frame that outruns the response naming it, and releases what the response disowns', async () => {
    const user = userEvent.setup()
    const titleField = detail.fields[0]
    if (!titleField) throw new Error('workspace fixture requires a title field')
    const settled: CaseDetailView = {
      ...detail,
      fields: [
        {
          ...titleField,
          needsAttention: false,
          humanDecision: {
            actor: 'owner',
            decision_id: 'd',
            destination: 'title',
            occurred_at: 1,
            outcome: 'keep_current',
            proposal_id: titleField.proposalId,
            review_case_id: 'case-1',
            selected_value: null,
            target_fingerprint: titleField.targetFingerprint,
          },
        },
      ],
    }
    let current = detail
    let libraryReads = 0
    let release: (() => void) | undefined
    const answered = new Promise<void>((resolve) => {
      release = resolve
    })
    const http: ReviewHttp = {
      ...createHttp(),
      getLibrary: vi.fn(async () => {
        libraryReads += 1
        return library
      }),
      getCaseDetail: vi.fn(async () => current),
      recordDecision: vi.fn(async () => {
        await answered
        current = settled
        return {
          invalidation_sequences: [80, 81, 82],
          schema_version: 'review-decision-v1' as const,
          actor: 'owner' as const,
          decision_id: 'decision-outrun',
          destination: 'title',
          occurred_at: 1,
          outcome: 'keep_current' as const,
          proposal_id: titleField.proposalId,
          review_case_id: 'case-1',
          selected_value: null,
          target_fingerprint: titleField.targetFingerprint,
        }
      }),
    }
    const updates = createLiveStream()
    renderRoute('/library?lens=all&preview=case-1&field=title', http, updates)
    expect(await screen.findByRole('heading', { level: 2, name: 'A Book' })).toBeVisible()
    await waitFor(() => expect(libraryReads).toBe(1))

    await user.click(screen.getByRole('button', { name: 'Choose current value: Old title' }))
    // The server publishes the echo before it writes the response that names
    // it, so this is the real order, not a contrived one. The frame arrives
    // with nothing yet able to explain it.
    await act(async () => {
      updates.announce({ sequence: 81, subject: 'case-1', topic: 'library' })
      updates.announce({ sequence: 99, subject: 'case-1', topic: 'library' })
      await new Promise((resolve) => setTimeout(resolve, coalesceMilliseconds + 20))
    })
    // Held, not acted on: a read here would be the whole-Library read on the
    // decision path that #635 removed.
    expect(libraryReads).toBe(1)

    // The response arrives and claims 81. It does not claim 99, which was
    // therefore somebody else's news all along and is acted on now.
    await act(async () => {
      release?.()
      await answered
    })
    await waitFor(() => expect(libraryReads).toBe(2))
    expect(await screen.findByText('Review complete')).toBeVisible()

    // 81 stays owed, and leaving pays it.
    await user.click(screen.getByRole('button', { name: 'Back to works' }))
    await waitFor(() => expect(libraryReads).toBe(3))
  })

  it("a tab that recorded nothing acts on every announcement, including another tab's decision", async () => {
    let libraryReads = 0
    let attentionReads = 0
    const http: ReviewHttp = {
      ...createHttp(),
      getLibrary: vi.fn(async () => {
        libraryReads += 1
        return library
      }),
      getAttention: vi.fn(async () => {
        attentionReads += 1
        return { total: 0, items: [] }
      }),
    }
    const updates = createLiveStream()
    renderRoute('/library?lens=all&preview=case-1&field=title', http, updates)
    expect(await screen.findByRole('heading', { level: 2, name: 'A Book' })).toBeVisible()
    await waitFor(() => expect([libraryReads, attentionReads]).toEqual([1, 1]))

    // The same frame the recording tab recognises. This one caused nothing, so
    // it holds no positions and acts on all of them.
    await act(async () => {
      updates.announce({ sequence: 41, subject: 'case-1', topic: 'library' })
      await new Promise((resolve) => setTimeout(resolve, coalesceMilliseconds + 20))
    })
    await waitFor(() => expect([libraryReads, attentionReads]).toEqual([2, 2]))
  })

  it('acknowledges a choice in place and opens the next field before the server answers', async () => {
    const user = userEvent.setup()
    const titleField = detail.fields[0]
    if (!titleField) throw new Error('workspace fixture requires a title field')
    const authorsField = {
      ...titleField,
      destination: 'authors',
      proposalId: 'proposal-authors',
      targetFingerprint: 'fingerprint-authors',
    }
    let release: (() => void) | undefined
    const deferred = new Promise<void>((resolve) => {
      release = resolve
    })
    const http: ReviewHttp = {
      ...createHttp(),
      getCaseDetail: vi.fn(async () => ({ ...detail, fields: [titleField, authorsField] })),
      recordDecision: vi.fn(async () => {
        await deferred
        return {
          invalidation_sequences: [],
          schema_version: 'review-decision-v1',
          actor: 'owner',
          decision_id: 'decision-title',
          destination: 'title',
          occurred_at: 1,
          outcome: 'keep_current',
          proposal_id: titleField.proposalId,
          review_case_id: 'case-1',
          selected_value: null,
          target_fingerprint: titleField.targetFingerprint,
        }
      }),
    }
    renderRoute('/library?lens=all&preview=case-1&field=title', http)

    const choice = await screen.findByRole('button', { name: 'Choose current value: Old title' })
    const readsBefore = vi.mocked(http.getLibrary).mock.calls.length
    const caseReadsBefore = vi.mocked(http.getCaseDetail).mock.calls.length
    await user.click(choice)

    // The next field is current while the decision is still in flight, so no
    // Library projection and no second case read stood between the choice and
    // the field that followed it.
    await waitFor(() =>
      expect(screen.getByRole('button', { name: /Authors.*Needs review/u })).toHaveAttribute(
        'aria-current',
        'true',
      ),
    )
    expect(vi.mocked(http.recordDecision)).toHaveBeenCalledTimes(1)
    expect(vi.mocked(http.getLibrary).mock.calls.length).toBe(readsBefore)
    expect(vi.mocked(http.getCaseDetail).mock.calls.length).toBe(caseReadsBefore)

    // The chosen field keeps its whole composition: the choice is marked in
    // place and its sibling is still there to be read.
    await user.click(
      screen.getByRole('button', { name: /Title.*Needs review|Title.*Decision recorded/u }),
    )
    const chosen = await screen.findByRole('button', { name: 'Choose current value: Old title' })
    expect(chosen).toHaveAttribute('aria-pressed', 'true')
    expect(chosen).toHaveTextContent('Recording…')
    expect(screen.getByRole('button', { name: 'Choose proposed value: New title' })).toBeEnabled()
    await act(async () => {
      release?.()
    })
  })

  it('records one command per field however often its choices are pressed', async () => {
    const user = userEvent.setup()
    const titleField = detail.fields[0]
    if (!titleField) throw new Error('workspace fixture requires a title field')
    const authorsField = {
      ...titleField,
      destination: 'authors',
      proposalId: 'proposal-authors',
      targetFingerprint: 'fingerprint-authors',
    }
    let release: (() => void) | undefined
    const deferred = new Promise<void>((resolve) => {
      release = resolve
    })
    const recordDecision = vi.fn<ReviewHttp['recordDecision']>(async (_id, command) => {
      await deferred
      return {
        invalidation_sequences: [],
        schema_version: 'review-decision-v1' as const,
        actor: 'owner' as const,
        decision_id: `decision-${command.destination}`,
        destination: command.destination,
        occurred_at: 1,
        outcome: 'keep_current' as const,
        proposal_id: command.proposalId,
        review_case_id: 'case-1',
        selected_value: null,
        target_fingerprint: command.targetFingerprint,
      }
    })
    const http: ReviewHttp = {
      ...createHttp(),
      getCaseDetail: vi.fn(async () => ({ ...detail, fields: [titleField, authorsField] })),
      recordDecision,
    }
    renderRoute('/library?lens=all&preview=case-1&field=title', http)

    await user.click(await screen.findByRole('button', { name: 'Choose current value: Old title' }))
    await waitFor(() =>
      expect(screen.getByRole('button', { name: /Authors.*Needs review/u })).toHaveAttribute(
        'aria-current',
        'true',
      ),
    )
    await user.click(screen.getByRole('button', { name: /Title.*/u }))
    await user.click(
      await screen.findByRole('button', { name: 'Choose proposed value: New title' }),
    )

    expect(recordDecision).toHaveBeenCalledTimes(1)
    expect(recordDecision.mock.calls[0]?.[1].destination).toBe('title')
    await act(async () => {
      release?.()
    })
  })

  it('sends sequential choices in the order they were made while the first settles', async () => {
    const user = userEvent.setup()
    const titleField = detail.fields[0]
    if (!titleField) throw new Error('workspace fixture requires a title field')
    const authorsField = {
      ...titleField,
      destination: 'authors',
      proposalId: 'proposal-authors',
      targetFingerprint: 'fingerprint-authors',
    }
    let release: (() => void) | undefined
    const deferred = new Promise<void>((resolve) => {
      release = resolve
    })
    const sent: string[] = []
    const recordDecision = vi.fn<ReviewHttp['recordDecision']>(async (_id, command) => {
      sent.push(command.destination)
      if (command.destination === 'title') await deferred
      return {
        invalidation_sequences: [],
        schema_version: 'review-decision-v1' as const,
        actor: 'owner' as const,
        decision_id: `decision-${command.destination}`,
        destination: command.destination,
        occurred_at: 1,
        outcome: 'keep_current' as const,
        proposal_id: command.proposalId,
        review_case_id: 'case-1',
        selected_value: null,
        target_fingerprint: command.targetFingerprint,
      }
    })
    const http: ReviewHttp = {
      ...createHttp(),
      getCaseDetail: vi.fn(async () => ({ ...detail, fields: [titleField, authorsField] })),
      recordDecision,
    }
    renderRoute('/library?lens=all&preview=case-1&field=title', http)

    await user.click(await screen.findByRole('button', { name: 'Choose current value: Old title' }))
    await waitFor(() =>
      expect(screen.getByRole('button', { name: /Authors.*Needs review/u })).toHaveAttribute(
        'aria-current',
        'true',
      ),
    )
    // The second choice is made while the first is unanswered.
    await user.click(await screen.findByRole('button', { name: 'Choose current value: Old title' }))
    expect(sent).toEqual(['title'])

    await act(async () => {
      release?.()
    })
    await waitFor(() => expect(sent).toEqual(['title', 'authors']))
  })

  it('states what write consent makes true when the work is complete', async () => {
    const titleField = detail.fields[0]
    if (!titleField) throw new Error('workspace fixture requires a title field')
    const settled = (writesConsented: boolean): CaseDetailView => ({
      ...detail,
      writesConsented,
      fields: [
        {
          ...titleField,
          needsAttention: false,
          humanDecision: {
            actor: 'owner',
            decision_id: 'decision-title',
            destination: 'title',
            occurred_at: 1,
            outcome: 'keep_current',
            proposal_id: titleField.proposalId,
            review_case_id: 'case-1',
            selected_value: null,
            target_fingerprint: titleField.targetFingerprint,
          },
        },
      ],
    })
    for (const [writesConsented, expected] of [
      [false, 'Nothing is written yet — applying happens separately, behind your consent.'],
      [
        true,
        'Every write is logged in Activity, and read-back verification remains a separate guarded stage.',
      ],
    ] as const) {
      const user = userEvent.setup()
      let current: CaseDetailView = { ...detail, writesConsented }
      const http: ReviewHttp = {
        ...createHttp(),
        getCaseDetail: vi.fn(async () => current),
        recordDecision: vi.fn(async () => {
          current = settled(writesConsented)
          return {
            invalidation_sequences: [],
            schema_version: 'review-decision-v1',
            actor: 'owner',
            decision_id: 'decision-title',
            destination: 'title',
            occurred_at: 1,
            outcome: 'keep_current',
            proposal_id: titleField.proposalId,
            review_case_id: 'case-1',
            selected_value: null,
            target_fingerprint: titleField.targetFingerprint,
          }
        }),
      }
      renderRoute('/library?lens=all&preview=case-1&field=title', http)
      const choices = await screen.findAllByRole('button', {
        name: 'Choose current value: Old title',
      })
      const latest = choices[choices.length - 1]
      if (!latest) throw new Error('the current route rendered no choice')
      await user.click(latest)
      expect(await screen.findByText(expected)).toBeVisible()
      expect(
        screen.queryByText(
          writesConsented
            ? 'Nothing is written yet — applying happens separately, behind your consent.'
            : 'Every write is logged in Activity, and read-back verification remains a separate guarded stage.',
        ),
      ).toBeNull()
      cleanup()
    }
  })

  it('gives the review comparison a one-line evidence footer and the summary its own', async () => {
    const first = library.works[0]
    if (!first) throw new Error('library fixture requires a work')
    const consulted = {
      ...first,
      preparationOutcomes: [
        {
          providerRequests: 1,
          laneKind: 'work',
          provider: 'hardcover-main',
          reason: 'Answered with one candidate.',
          status: 'candidates',
        },
      ],
    }
    const http: ReviewHttp = {
      ...createHttp(),
      getLibrary: vi.fn(async () => ({ ...library, works: [consulted] })),
    }
    renderRoute('/library?lens=all&preview=case-1&field=title', http)
    // The protected footer: one line, no subtitle, and it says what it is
    // evidence for. The work summary is not a comparison and keeps its own.
    expect(await screen.findByText('1 source supports this comparison')).toBeVisible()
    expect(screen.queryByText(/consulted for this work$/u)).toBeNull()
  })

  it('names every field in the rail and carries the count in the field-map header', async () => {
    const titleField = detail.fields[0]
    if (!titleField) throw new Error('workspace fixture requires a title field')
    const authorsField = {
      ...titleField,
      destination: 'authors',
      proposalId: 'proposal-authors',
      targetFingerprint: 'fingerprint-authors',
    }
    const http: ReviewHttp = {
      ...createHttp(),
      getCaseDetail: vi.fn(async () => ({ ...detail, fields: [titleField, authorsField] })),
    }
    renderRoute('/library?lens=all&preview=case-1&field=title', http)

    const rail = await screen.findByRole('navigation', { name: 'Review fields' })
    for (const cell of within(rail).getAllByRole('button')) {
      expect(cell.textContent).toMatch(/[A-Za-z]/u)
      expect(cell.textContent).not.toMatch(/^\s*\d/u)
    }
    expect(within(rail).getByRole('button', { name: /^Title/u })).toBeVisible()
    expect(within(rail).getByRole('button', { name: /^Authors/u })).toBeVisible()
    expect(screen.getByText('2 remaining · field 1 of 2')).toBeVisible()
    // The teaching line is offered once, on the first field.
    expect(screen.getByText('One tap records and opens the next field')).toBeVisible()
    await userEvent.setup().click(within(rail).getByRole('button', { name: /^Authors/u }))
    expect(await screen.findByText('2 remaining · field 2 of 2')).toBeVisible()
    expect(screen.queryByText('One tap records and opens the next field')).toBeNull()
  })

  it('keeps applicable unavailable fields inspectable without making them actionable', async () => {
    const titleField = detail.fields[0]
    if (!titleField) throw new Error('workspace fixture requires a title field')
    const unavailableField = {
      ...titleField,
      classification: 'unsupported' as const,
      classificationReason: 'This field is unavailable for the current service capability.',
      destination: 'subtitle',
      editable: false,
      inventoryRole: 'contextual' as const,
      needsAttention: false,
      navigationRole: 'inspect' as const,
      proposalId: 'proposal-subtitle',
      proposedValues: [],
      targetFingerprint: 'fingerprint-subtitle',
    }
    const http: ReviewHttp = {
      ...createHttp(),
      getCaseDetail: vi.fn(async () => ({ ...detail, fields: [titleField, unavailableField] })),
    }
    renderRoute('/library?lens=all&preview=case-1&field=title', http)

    const rail = await screen.findByRole('navigation', { name: 'Review fields' })
    expect(within(rail).getByRole('button', { name: /^Title/u })).toBeVisible()
    expect(within(rail).queryByRole('button', { name: /^Subtitle/u })).toBeNull()
    expect(screen.getByText('1 field with context')).toBeVisible()
    await userEvent.setup().click(screen.getByText('1 field with context'))
    expect(
      screen.getByText('This field is unavailable for the current service capability.'),
    ).toBeVisible()
  })

  it('presents a date and year as one publication field', async () => {
    const titleField = detail.fields[0]
    if (!titleField) throw new Error('workspace fixture requires a title field')
    const publicationDate = {
      ...titleField,
      destination: 'publishedDate',
      proposalId: 'proposal-publication-date',
      targetFingerprint: 'fingerprint-publication-date',
    }
    const publicationYear = {
      ...titleField,
      destination: 'publishedYear',
      inventoryRole: 'contextual' as const,
      proposalId: 'proposal-publication-year',
      targetFingerprint: 'fingerprint-publication-year',
    }
    const http: ReviewHttp = {
      ...createHttp(),
      getCaseDetail: vi.fn(async () => ({ ...detail, fields: [publicationDate, publicationYear] })),
    }
    renderRoute('/library?lens=all&preview=case-1&field=publishedYear', http)

    const rail = await screen.findByRole('navigation', { name: 'Review fields' })
    expect(within(rail).getByRole('button', { name: /^Publication date/u })).toBeVisible()
    expect(within(rail).queryByRole('button', { name: /^PublishedYear/u })).toBeNull()
  })

  it('uses non-animated rail movement when reduced motion is requested', async () => {
    const originalScrollTo = HTMLElement.prototype.scrollTo
    const scrollTo = vi.fn()
    Object.defineProperty(HTMLElement.prototype, 'scrollTo', {
      configurable: true,
      value: scrollTo,
    })
    const original = window.matchMedia
    window.matchMedia = vi.fn((query: string) => ({
      matches: query === '(prefers-reduced-motion: reduce)',
      media: query,
      onchange: null,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    }))
    try {
      renderRoute('/library?lens=all&preview=case-1&field=title')
      await screen.findByRole('button', { name: 'Choose current value: Old title' })
      expect(scrollTo).toHaveBeenCalledWith(expect.objectContaining({ behavior: 'auto' }))
    } finally {
      window.matchMedia = original
      Object.defineProperty(HTMLElement.prototype, 'scrollTo', {
        configurable: true,
        value: originalScrollTo,
      })
    }
  })

  it('centres a positioned rail from viewport coordinates and its existing scroll', async () => {
    const originalScrollTo = HTMLElement.prototype.scrollTo
    const originalRect = HTMLElement.prototype.getBoundingClientRect
    const scrollLeft = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'scrollLeft')
    const clientWidth = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'clientWidth')
    const scrollTo = vi.fn()
    Object.defineProperty(HTMLElement.prototype, 'scrollTo', {
      configurable: true,
      value: scrollTo,
    })
    Object.defineProperty(HTMLElement.prototype, 'scrollLeft', {
      configurable: true,
      get(this: HTMLElement) {
        return this.classList.contains('field-filmstrip')
          ? 40
          : Number(scrollLeft?.get?.call(this) ?? 0)
      },
    })
    Object.defineProperty(HTMLElement.prototype, 'clientWidth', {
      configurable: true,
      get(this: HTMLElement) {
        return this.classList.contains('field-filmstrip')
          ? 300
          : Number(clientWidth?.get?.call(this) ?? 0)
      },
    })
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (
      this: HTMLElement,
    ) {
      if (this.classList.contains('field-filmstrip')) return { left: 240, width: 300 } as DOMRect
      if (this.getAttribute('aria-current') === 'true') return { left: 540, width: 100 } as DOMRect
      return originalRect.call(this)
    })
    try {
      renderRoute('/library?lens=all&preview=case-1&field=title')
      await screen.findByRole('button', { name: 'Choose current value: Old title' })
      expect(scrollTo).toHaveBeenCalledWith(expect.objectContaining({ left: 240 }))
    } finally {
      vi.restoreAllMocks()
      Object.defineProperty(HTMLElement.prototype, 'scrollTo', {
        configurable: true,
        value: originalScrollTo,
      })
      if (scrollLeft) Object.defineProperty(HTMLElement.prototype, 'scrollLeft', scrollLeft)
      if (clientWidth) Object.defineProperty(HTMLElement.prototype, 'clientWidth', clientWidth)
    }
  })

  it('loads later attention pages until completion finds a deliberate next-work choice', async () => {
    const user = userEvent.setup()
    const titleField = detail.fields[0]
    if (!titleField) throw new Error('detail fixture requires a title field')
    const settled: CaseDetailView = {
      ...detail,
      fields: [
        {
          ...titleField,
          needsAttention: false,
          humanDecision: {
            actor: 'owner',
            decision_id: 'decision-page',
            destination: 'title',
            occurred_at: 1,
            outcome: 'keep_current',
            proposal_id: titleField.proposalId,
            review_case_id: 'case-1',
            selected_value: null,
            target_fingerprint: titleField.targetFingerprint,
          },
        },
      ],
    }
    let current = detail
    const getAttention = vi.fn<ReviewHttp['getAttention']>(async (search) =>
      search.cursor
        ? {
            total: 2,
            items: [
              {
                id: 'case-2',
                destination: 'authors',
                title: 'Later page work',
                authors: [],
                library: 'Books',
                mediaKind: 'ebook',
                notices: [],
                provenance: [],
                workAttentionCount: 1,
              },
            ],
          }
        : {
            total: 2,
            nextCursor: 'later',
            items: [
              {
                id: 'case-1',
                destination: 'title',
                title: 'A Book',
                authors: [],
                library: 'Books',
                mediaKind: 'ebook',
                notices: [],
                provenance: [],
                workAttentionCount: 1,
              },
            ],
          },
    )
    const http: ReviewHttp = {
      ...createHttp(),
      getAttention,
      getCaseDetail: vi.fn(async () => current),
      recordDecision: vi.fn(async () => {
        current = settled
        return {
          invalidation_sequences: [],
          schema_version: 'review-decision-v1',
          actor: 'owner',
          decision_id: 'decision-page',
          destination: 'title',
          occurred_at: 1,
          outcome: 'keep_current',
          proposal_id: titleField.proposalId,
          review_case_id: 'case-1',
          selected_value: null,
          target_fingerprint: titleField.targetFingerprint,
        }
      }),
    }
    renderRoute('/library?lens=all&preview=case-1&field=title', http)

    await user.click(await screen.findByRole('button', { name: 'Choose current value: Old title' }))
    expect(await screen.findByText('Review complete')).toBeVisible()
    expect(await screen.findByRole('button', { name: 'Review next work →' })).toBeVisible()
    expect(getAttention).toHaveBeenCalledWith({ cursor: 'later' }, expect.any(AbortSignal))
  })

  it('clears the cursor but keeps the open work when the Library search query changes', async () => {
    const user = userEvent.setup()
    const { router } = renderRoute('/library?lens=all&q=first&cursor=next&preview=case-1')

    expect(await screen.findByRole('heading', { level: 2, name: 'A Book' })).toBeVisible()
    await user.clear(screen.getByLabelText('Search title, author, identifier, library, or format'))
    await user.type(
      screen.getByLabelText('Search title, author, identifier, library, or format'),
      'second',
    )

    // A new page of results starts at the first page, but the work under review
    // stays open even when the search no longer lists its card.
    await waitFor(() =>
      expect(router.state.location.search).toEqual({ lens: 'all', preview: 'case-1', q: 'second' }),
    )
    expect(screen.getByRole('heading', { level: 2, name: 'A Book' })).toBeVisible()
  })

  it('keeps the latest Library query when a prior replacement finishes late', async () => {
    const user = userEvent.setup()
    const { router } = renderRoute('/library?lens=all&q=first&cursor=next&preview=case-1')
    await screen.findByRole('heading', { level: 2, name: 'A Book' })
    const originalNavigate = router.navigate.bind(router)
    let releaseClear: (() => void) | undefined
    let replacements = 0
    const delayedNavigate = vi.spyOn(router, 'navigate').mockImplementation((options) => {
      replacements += 1
      if (replacements !== 1) return originalNavigate(options)
      return new Promise((resolve, reject) => {
        releaseClear = () => {
          void originalNavigate(options).then(resolve, reject)
        }
      })
    })
    const query = screen.getByLabelText('Search title, author, identifier, library, or format')

    await user.clear(query)
    await waitFor(() => expect(replacements).toBe(1))
    await user.type(query, 'second')
    expect(query).toHaveValue('second')
    act(() => releaseClear?.())

    await waitFor(() =>
      expect(router.state.location.search).toEqual({ lens: 'all', preview: 'case-1', q: 'second' }),
    )
    expect(replacements).toBe(2)
    delayedNavigate.mockRestore()
  })

  it('coalesces a cleared Library query before its route load begins', async () => {
    const getLibrary = vi.fn<ReviewHttp['getLibrary']>(async () => library)
    const { router } = renderRoute('/library?lens=all&q=first&cursor=next&preview=case-1', {
      ...createHttp(),
      getLibrary,
    })
    await screen.findByRole('heading', { level: 2, name: 'A Book' })
    const query = screen.getByLabelText('Search title, author, identifier, library, or format')
    vi.useFakeTimers()
    try {
      fireEvent.change(query, { target: { value: '' } })
      fireEvent.change(query, { target: { value: 'second' } })
      expect(query).toHaveValue('second')
      expect(getLibrary.mock.calls.map(([search]) => search)).not.toContainEqual({
        lens: 'all',
        preview: 'case-1',
      })
      await act(async () => {
        await vi.advanceTimersByTimeAsync(150)
      })
      vi.useRealTimers()
      await waitFor(() =>
        expect(router.state.location.search).toEqual({
          lens: 'all',
          preview: 'case-1',
          q: 'second',
        }),
      )

      const searches = getLibrary.mock.calls.map(([search]) => search)
      expect(searches.filter((search) => search.q === 'second')).toHaveLength(1)
      expect(searches).not.toContainEqual({ lens: 'all', preview: 'case-1' })
    } finally {
      vi.useRealTimers()
    }
  })

  it('relinquishes a pending Library query to a same-route URL change', async () => {
    const getLibrary = vi.fn<ReviewHttp['getLibrary']>(async () => library)
    const { router } = renderRoute('/library?lens=all&q=first&cursor=next&preview=case-1', {
      ...createHttp(),
      getLibrary,
    })
    await screen.findByRole('heading', { level: 2, name: 'A Book' })
    const query = screen.getByLabelText('Search title, author, identifier, library, or format')
    vi.useFakeTimers()
    try {
      fireEvent.change(query, { target: { value: 'typed' } })
      await act(async () => {
        await router.navigate({
          to: '/library',
          search: { lens: 'needs_attention', preview: 'case-1', q: 'first' },
        })
        await Promise.resolve()
      })
      await act(async () => {
        await vi.advanceTimersByTimeAsync(150)
      })
      vi.useRealTimers()

      expect(router.state.location.search).toEqual({
        lens: 'needs_attention',
        preview: 'case-1',
        q: 'first',
      })
      expect(getLibrary.mock.calls.map(([search]) => search)).not.toContainEqual({
        lens: 'all',
        preview: 'case-1',
        q: 'typed',
      })
    } finally {
      vi.useRealTimers()
    }
  })

  it('starts a later owner query after invalidating an active Library replacement', async () => {
    const { router } = renderRoute('/library?lens=all&q=first&cursor=next&preview=case-1')
    await screen.findByRole('heading', { level: 2, name: 'A Book' })
    const originalNavigate = router.navigate.bind(router)
    let releaseFirst: (() => void) | undefined
    let libraryReplacements = 0
    const delayedNavigate = vi.spyOn(router, 'navigate').mockImplementation((options) => {
      if (options.from !== '/library') return originalNavigate(options)
      libraryReplacements += 1
      if (libraryReplacements !== 1) return originalNavigate(options)
      if (typeof options.search === 'function') options.search(router.state.location.search)
      return new Promise((resolve) => {
        releaseFirst = resolve
      })
    })
    const query = screen.getByLabelText('Search title, author, identifier, library, or format')
    vi.useFakeTimers()
    try {
      fireEvent.change(query, { target: { value: 'first ' } })
      await act(async () => {
        await vi.advanceTimersByTimeAsync(150)
      })
      expect(libraryReplacements).toBe(1)
      await act(async () => {
        await originalNavigate({
          to: '/library',
          search: { lens: 'needs_attention', preview: 'case-1', q: 'first' },
        })
        await Promise.resolve()
      })
      fireEvent.change(query, { target: { value: 'latest' } })
      await act(async () => {
        await vi.advanceTimersByTimeAsync(150)
        releaseFirst?.()
        await Promise.resolve()
        await vi.advanceTimersByTimeAsync(150)
      })
      vi.useRealTimers()

      await waitFor(() =>
        expect(router.state.location.search).toEqual({
          lens: 'needs_attention',
          preview: 'case-1',
          q: 'latest',
        }),
      )
      expect(libraryReplacements).toBe(2)
    } finally {
      vi.useRealTimers()
      delayedNavigate.mockRestore()
    }
  })

  it('starts a fresh Library search after a rejected replacement', async () => {
    const user = userEvent.setup()
    const { router } = renderRoute('/library?lens=all&q=first&cursor=next&preview=case-1')
    await screen.findByRole('heading', { level: 2, name: 'A Book' })
    const failedNavigate = vi
      .spyOn(router, 'navigate')
      .mockRejectedValueOnce(new Error('route unavailable'))
    const query = screen.getByLabelText('Search title, author, identifier, library, or format')

    await user.clear(query)
    await waitFor(() => expect(failedNavigate).toHaveBeenCalledTimes(1))
    await user.clear(query)
    await user.type(query, 'second')

    await waitFor(() =>
      expect(router.state.location.search).toEqual({ lens: 'all', preview: 'case-1', q: 'second' }),
    )
    failedNavigate.mockRestore()
  })

  it('does not revive queued Library search after leaving the route', async () => {
    const user = userEvent.setup()
    let releaseFirst: (() => void) | undefined
    const { router } = renderRoute('/library?lens=all&q=first&cursor=next&preview=case-1')
    await screen.findByRole('heading', { level: 2, name: 'A Book' })
    const originalNavigate = router.navigate.bind(router)
    let libraryReplacements = 0
    const delayedNavigate = vi.spyOn(router, 'navigate').mockImplementation((options) => {
      if (options.from !== '/library') return originalNavigate(options)
      libraryReplacements += 1
      if (libraryReplacements !== 1) return originalNavigate(options)
      return new Promise((resolve) => {
        releaseFirst = resolve
      })
    })
    const query = screen.getByLabelText('Search title, author, identifier, library, or format')

    await user.clear(query)
    await waitFor(() => expect(releaseFirst).toBeTypeOf('function'))
    await user.type(query, 'second')
    await originalNavigate({
      to: '/activity',
      search: { eventType: 'all', rangeDays: 30, page: 1, pageSize: 25 },
    })
    await screen.findByRole('heading', { name: 'Activity and recovery' })
    await originalNavigate({ to: '/policy', search: { section: 'identity' } })
    await screen.findByRole('heading', { name: 'Library policy' })
    await act(async () => {
      releaseFirst?.()
      await Promise.resolve()
    })

    expect(router.state.location.pathname).toBe('/policy')
    expect(libraryReplacements).toBe(1)
    delayedNavigate.mockRestore()
  })

  it('preserves an off-page preview deep link without exposing detail target identity', async () => {
    const emptyLibrary: LibraryView = {
      lens: 'all',
      page: 1,
      counts: { attention: 0, all: 0, clear: 0 },
      works: [],
    }
    const http: ReviewHttp = {
      ...createHttp(),
      getLibrary: vi.fn(async () => emptyLibrary),
      getCaseDetail: vi.fn(async () => ({
        ...detail,
        title: 'item-1',
        subtitle: 'internal identity',
      })),
    }
    renderRoute('/library?lens=all&preview=case-1', http)

    expect(await screen.findByRole('heading', { name: 'Selected work' })).toBeVisible()
    expect(
      screen.getByText('Readable work identity is not available on this Library page.'),
    ).toBeVisible()
    expect(screen.queryByText('item-1')).toBeNull()
    expect(screen.queryByText('internal identity')).toBeNull()
    expect(screen.queryByRole('link', { name: /Open full review|Open all evidence/u })).toBeNull()
  })

  it('keeps Evidence as a read-only diagnostic instead of a Library decision surface', async () => {
    const { router } = renderRoute('/review/case-1?subview=evidence&field=title', createHttp())

    expect(await screen.findByRole('heading', { name: 'A Book' })).toBeVisible()
    expect(router.state.location.pathname).toBe('/review/case-1')
    expect(screen.getByRole('heading', { name: /Evidence and source ledger/u })).toBeVisible()
    expect(screen.queryByRole('button', { name: /Choose current value/u })).toBeNull()
  })

  it('aborts a superseded critical load and renders only the current route result', async () => {
    const signals: AbortSignal[] = []
    let resolveFirst: ((value: LibraryView) => void) | undefined
    const withTitle = (title: string): LibraryView => ({
      ...library,
      works: library.works.map((work, index) => (index === 0 ? { ...work, title } : work)),
    })
    const current = withTitle('Current result')
    const http: ReviewHttp = {
      ...createHttp(),
      getLibrary: vi.fn((search: LibrarySearch, signal?: AbortSignal): Promise<LibraryView> => {
        if (signal) signals.push(signal)
        if (search.lens === 'no_attention_needed') return Promise.resolve(current)
        return new Promise<LibraryView>((resolve) => {
          resolveFirst = resolve
        })
      }),
    }
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const router = createApplicationRouter(
      http,
      queryClient,
      createMemoryHistory({ initialEntries: ['/library?lens=all'] }),
    )
    render(
      <QueryClientProvider client={queryClient}>
        <RouterProvider router={router} />
      </QueryClientProvider>,
    )
    await waitFor(() => expect(http.getLibrary).toHaveBeenCalledOnce())
    await router.navigate({ to: '/library', search: { lens: 'no_attention_needed' } })
    resolveFirst?.(withTitle('Stale result'))

    expect(await screen.findByRole('button', { name: /^Current result\./u })).toBeVisible()
    expect(screen.queryByRole('button', { name: /^Stale result\./u })).toBeNull()
    expect(signals[0]?.aborted).toBe(true)
  })

  it('offers the completion handoff inside Library with one action for every eligible destination', async () => {
    const user = userEvent.setup()
    const http: ReviewHttp = {
      ...createHttp(),
      getPreparation: vi.fn(async () => ({
        schema_version: 'review-preparation-v1' as const,
        active: true,
        actionable: false,
        paused: false,
        libraries: [
          {
            scan_id: 'scan-1',
            connection: 'primary',
            library: 'fiction',
            library_id: 'library-1',
            state: 'running',
            checked: 9,
            total: 12,
            ready: 9,
            blocked: 0,
            rescan_requested: false,
          },
        ],
      })),
      getActivity: vi.fn(async () =>
        activity({
          configured_libraries: [{ connection: 'primary', library: 'fiction' }],
          capabilities: [
            {
              connection: 'primary',
              library: 'fiction',
              state: 'partially_allowed',
              destinations: [
                { consented: false, destination: 'title', prepared: 4, state: 'allowed' },
                {
                  consented: false,
                  destination: 'cover',
                  prepared: 6,
                  state: 'insufficient_permission',
                },
              ],
            },
          ],
          waiting: {
            applying: 0,
            awaiting_permission: 4,
            held_for_recovery: 0,
            retrying: 0,
            scheduled: 0,
          },
        }),
      ),
    }
    renderRoute('/library?lens=all', http)

    // One compact notice inside the existing Library flow: what is ready, what
    // the write state is, and the single action that changes it (#641, #639).
    expect(
      await screen.findByRole('heading', { name: '9 works are ready to browse. Writes are off.' }),
    ).toBeVisible()
    // Preparation still running does not withdraw the action.
    expect(screen.getByText(/preparation is still running/u)).toBeVisible()
    await user.click(screen.getByRole('button', { name: 'Enable writes' }))

    // Every eligible destination, and only those: the Cover behind its own gate
    // is left out rather than taking Title with it.
    expect(http.activityCommand).toHaveBeenCalledWith('activity/consent', {
      grants: [{ library: 'fiction', destinations: ['title'] }],
    })
  })
})
