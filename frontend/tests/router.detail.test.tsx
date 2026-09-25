import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { ApiError, mapCaseDetail, mapEvidence } from '../src/api/http'
import descriptionAdviceFixture from './descriptionAdviceFixture.json'
import identifierEvidenceFixture from './identifierEvidenceFixture.json'
import { normalizeDetailSearch } from '../src/router'
import type { AttentionView, CaseDetailView, ReviewHttp } from '../src/api/http'
import { createHttp, detail, renderRoute } from './fixtures'

describe('Library review ownership', () => {
  it('opens readable direct Evidence and restores the exact Library selection', async () => {
    const { router } = renderRoute(
      '/review/case-1?lens=needs_attention&field=title&subview=evidence',
    )
    expect(await screen.findByRole('heading', { name: 'A Book' })).toBeVisible()
    expect(router.state.location.pathname).toBe('/review/case-1')
    expect(screen.getByRole('heading', { name: 'Evidence' })).toBeVisible()
    expect(screen.getByText('Books · ebook')).toBeVisible()
    expect(screen.queryByText('internal-item-1')).toBeNull()
    const back = screen.getByRole('link', { name: 'Back to work' })
    expect(document.querySelector('#main-content .diagnostic-route > :first-child')).toBe(back)
    expect(back).toHaveAttribute('href', '/library?lens=needs_attention&preview=case-1&field=title')
    expect(screen.queryByRole('button', { name: /Choose|Return|Resume|Try again/u })).toBeNull()
    await userEvent.setup().click(back)
    await waitFor(() =>
      expect(router.state.location.search).toEqual({
        lens: 'needs_attention',
        preview: 'case-1',
        field: 'title',
      }),
    )
  })

  it.each(['/review/case-1?subview=evidence', '/library?lens=all&preview=case-1&field=title'])(
    'explains chapter recording identity at %s with secondary source attribution',
    async (route) => {
      const http: ReviewHttp = {
        ...createHttp(),
        getCaseDetail: vi.fn(async () => ({
          ...detail,
          recordingIdentityBasis: {
            basis: 'chapter_runtime_and_structure' as const,
            provider: 'Audnexus',
            candidate: 'recording-1',
          },
        })),
      }
      renderRoute(route, http)
      const section = await screen.findByRole('region', { name: 'Recording identity evidence' })
      expect(
        within(section).getByText(
          /Complete chapter timing and structure corroborate this recording/u,
        ),
      ).toBeVisible()
      expect(within(section).getByText(/where overall runtime is unavailable/u)).toBeVisible()
      const source = within(section).getByText('Recording source')
      expect(within(section).getByText(/Candidate reference: recording-1/u)).not.toBeVisible()
      await userEvent.setup().click(source)
      expect(within(section).getByText(/Candidate reference: recording-1/u)).toBeVisible()
      expect(screen.queryByRole('region', { name: 'Media health' })).toBeNull()
    },
  )

  it('states media health on the case an admitted audiobook opens', async () => {
    // An audiobook's row opens its case, never the recorded-evidence view, so
    // a section that existed only there would be invisible for exactly the
    // items it was built for.
    const http: ReviewHttp = {
      ...createHttp(),
      getCaseDetail: vi.fn(async () => ({
        ...detail,
        mediaHealth: [
          {
            bound:
              'The local running time is within 90 seconds of the running time the matched edition states.',
            cited: ['local_abc123', 'audible-product-B08G9PRS1K'],
            expected: '72000 seconds stated by the matched edition',
            kind: 'runtime_short_of_provider' as const,
            observed: '43200 seconds of local audio',
            tracks: [],
          },
        ],
      })),
    }
    renderRoute('/review/case-1?subview=evidence', http)

    const section = await screen.findByRole('region', { name: 'Media health' })
    expect(within(section).getByText('Runtime short of provider')).toBeVisible()
    expect(
      within(section).getByText(
        'Expected rule: The local running time is within 90 seconds of the running time the matched edition states.',
      ),
    ).toBeVisible()
    expect(
      within(section).getByText(/43200 seconds of local audio · expected 72000 seconds/u),
    ).toBeVisible()
    // The opaque local reference stands for what it points at rather than
    // being printed at the owner.
    expect(within(section).getByText(/Read from local evidence, audible-product/u)).toBeVisible()
    expect(screen.queryByText('local_abc123')).toBeNull()
  })

  it('states no media-health section on a case with nothing wrong with its media', async () => {
    renderRoute('/review/case-1?subview=evidence')

    expect(await screen.findByRole('heading', { name: 'Evidence' })).toBeVisible()
    expect(screen.queryByRole('region', { name: 'Media health' })).toBeNull()
    expect(screen.queryByRole('region', { name: 'Recording identity evidence' })).toBeNull()
  })

  it('cross-links diagnostics with URL-owned state and browser history', async () => {
    const user = userEvent.setup()
    const { router } = renderRoute(
      '/review/case-1?lens=no_attention_needed&field=title&subview=evidence',
    )
    await screen.findByRole('heading', { name: 'Evidence' })
    await user.click(screen.getByRole('link', { name: 'History' }))
    await waitFor(() =>
      expect(router.state.location.search).toEqual({
        field: 'title',
        lens: 'no_attention_needed',
        subview: 'history',
      }),
    )
    expect(await screen.findByRole('heading', { name: 'History' })).toBeVisible()
    expect(screen.queryByRole('button', { name: /Choose|Return|Resume/u })).toBeNull()
    router.history.back()
    await waitFor(() => expect(router.state.location.search.subview).toBe('evidence'))
    router.history.forward()
    await waitFor(() => expect(router.state.location.search.subview).toBe('history'))
  })

  it('renders semantic evidence, limitations, provider outcomes, and pagination', async () => {
    const getEvidence = vi.fn<ReviewHttp['getEvidence']>(async (_id, cursor) =>
      cursor
        ? {
            items: [
              {
                destinations: ['title'],
                observation: 'Title: <script>Second observation</script>',
                role: 'external_corroboration',
                source: 'Open Library',
              },
            ],
            missingLineageCount: 2,
            sourceLedger: [],
          }
        : {
            items: [
              {
                destinations: ['title'],
                observation: 'Title: A Book',
                role: 'local_presence',
                source: 'This library',
              },
            ],
            missingLineageCount: 2,
            nextCursor: 'next-page',
            sourceLedger: [
              {
                laneKind: 'identity',
                provider: 'Hardcover',
                providerRequests: 1,
                reason: 'Returned no matching metadata.',
                status: 'no_match',
              },
              {
                laneKind: 'cover',
                provider: 'Open Library',
                providerRequests: 2,
                reason: 'Returned more than the accepted bound, so nothing was retained.',
                status: 'oversized',
              },
            ],
          },
    )
    const user = userEvent.setup()
    renderRoute('/review/case-1?field=title&subview=evidence', { ...createHttp(), getEvidence })
    expect(await screen.findByText('Title: A Book')).toBeVisible()
    expect(screen.getByText(/2 referenced observations are no longer available/u)).toBeVisible()
    await user.click(screen.getByText('Sources'))
    expect(screen.getByText('no match')).toBeVisible()
    expect(document.querySelector('pre')).toBeNull()
    expect(document.body).not.toHaveTextContent('evidence_')
    await user.click(screen.getByRole('button', { name: 'Load more evidence' }))
    expect(await screen.findByText('Title: <script>Second observation</script>')).toBeVisible()
    expect(document.querySelector('script')).toBeNull()
    expect(getEvidence).toHaveBeenLastCalledWith(
      'case-1',
      'next-page',
      expect.any(AbortSignal),
      'foreground',
    )
  })

  it('shows retained conflicting identifiers as observations without offering choices', async () => {
    const evidence = mapEvidence(identifierEvidenceFixture.evidence)
    const current = mapCaseDetail(identifierEvidenceFixture.detail)
    const changed = structuredClone(identifierEvidenceFixture.detail)
    const choiceEvidence = changed.fields
      .flatMap((field) => field.choice_contexts)
      .flatMap((context) => context.evidence)[0]
    if (!choiceEvidence) throw new Error('actual linked choice evidence required')
    choiceEvidence.role = 'invented_support'
    expect(() => mapCaseDetail(changed)).toThrow(ApiError)
    renderRoute(`/review/${current.id}?field=isbn&subview=evidence`, {
      ...createHttp(),
      getCaseDetail: vi.fn(async () => current),
      getEvidence: vi.fn(async () => evidence),
    })
    expect(await screen.findByText(/ISBN: 9781250077004/u)).toBeVisible()
    expect(screen.getByText(/ISBN: 9780593135204/u)).toBeVisible()
    const observations = evidence.items.filter((item) => item.role === 'external_observation')
    for (const observation of new Set(observations.map((item) => item.observation))) {
      const rendered = screen.getAllByText(observation)
      expect(rendered).toHaveLength(
        observations.filter((item) => item.observation === observation).length,
      )
      for (const fact of rendered) expect(fact).toBeVisible()
    }
    expect(screen.getAllByText(/External observation/u)).toHaveLength(observations.length)
    expect(screen.queryByRole('button', { name: /Choose|Accept|Apply/u })).toBeNull()
    expect(current.fields.find((field) => field.destination === 'isbn')?.proposedValues).toEqual([])
  })

  it('renders history values only when the read model carries them', async () => {
    const getHistory = vi.fn<ReviewHttp['getHistory']>(async () => ({
      items: [
        {
          category: 'owner',
          applicationReason: null,
          destination: 'title',
          outcome: 'accept',
          previousValue: { kind: 'text', value: '<script>previous</script>' },
          selectedValue: { kind: 'text', value: '<script>chosen</script>' },
        },
        {
          category: 'owner',
          applicationReason: null,
          destination: 'language',
          outcome: 'accept',
          previousValue: null,
          selectedValue: { kind: 'text', value: 'English' },
        },
        {
          category: 'owner',
          applicationReason: null,
          destination: 'publisher',
          outcome: 'accept',
          previousValue: { kind: 'text', value: 'Former publisher' },
          selectedValue: null,
        },
        {
          category: 'owner',
          applicationReason: null,
          destination: 'title_reversal',
          outcome: 'keep_current',
          previousValue: { kind: 'text', value: 'Applied candidate' },
          selectedValue: { kind: 'text', value: 'Original value' },
        },
        {
          category: 'owner',
          applicationReason: null,
          destination: 'subtitle',
          outcome: 'keep_current',
          previousValue: { kind: 'text', value: 'Existing subtitle' },
          selectedValue: { kind: 'text', value: 'Existing subtitle' },
        },
        {
          category: 'owner',
          applicationReason: null,
          destination: 'edition',
          outcome: 'keep_current',
          previousValue: null,
          selectedValue: null,
        },
        {
          category: 'automatic',
          destination: 'series',
          outcome: 'keep_current',
          previousValue: { kind: 'series', value: [{ name: 'Existing series', sequence: '2' }] },
          selectedValue: { kind: 'series', value: [{ name: 'Existing series', sequence: '2' }] },
        },
        {
          category: 'owner',
          applicationReason: null,
          destination: 'isbn',
          outcome: 'reject',
          previousValue: { kind: 'text', value: '9780000000001' },
          selectedValue: null,
        },
        {
          category: 'automatic',
          destination: 'authors',
          outcome: 'candidate',
          previousValue: { kind: 'list', value: ['Old Author'] },
          selectedValue: { kind: 'list', value: ['New Author'] },
        },
        {
          category: 'automatic',
          destination: 'description',
          outcome: 'candidate',
          previousValue: null,
          selectedValue: null,
        },
        {
          category: 'automatic',
          destination: 'genres',
          outcome: 'ignore',
          previousValue: { kind: 'list', value: ['Preserved genre'] },
          selectedValue: null,
        },
        {
          category: 'automatic',
          destination: 'tags',
          outcome: 'ignore',
          previousValue: null,
          selectedValue: null,
        },
        { category: 'preview', dispositions: [{ count: 3, outcome: 'would_accept' }] },
        {
          category: 'cover',
          candidateCount: 2,
          outcome: 'selected',
          ownerOutcome: 'accept',
          policyReasons: ['credible_source'],
          policyStatus: 'recommended',
          reviewerStatus: 'complete',
          supersedesAutomatic: false,
        },
        {
          automaticOutcome: 'candidate',
          category: 'cover',
          candidateCount: 3,
          outcome: 'automatic_prepared',
          policyReasons: [],
          reviewerStatus: 'complete',
          supersedesAutomatic: true,
        },
        { category: 'transition', outcome: 'admitted', reason: '<script>actionable</script>' },
      ],
    }))
    renderRoute('/review/case-1?field=title&subview=history', { ...createHttp(), getHistory })

    const title = await screen.findByRole('heading', { name: 'Title' })
    const titleChange = title.closest('article')
    if (!titleChange) throw new Error('title change history article required')
    expect(titleChange).toHaveTextContent('Owner selected a proposed value.')
    expect(screen.getByText('<script>previous</script>')).toBeVisible()
    expect(screen.getByText('<script>chosen</script>')).toBeVisible()
    const language = screen.getByRole('heading', { name: 'Language' }).closest('article')
    if (!language) throw new Error('absence to value history article required')
    expect(language).toHaveTextContent('From No value is recorded.')
    expect(language).toHaveTextContent('To English')
    const publisher = screen.getByRole('heading', { name: 'Publisher' }).closest('article')
    if (!publisher) throw new Error('value to absence history article required')
    expect(publisher).toHaveTextContent('From Former publisher')
    expect(publisher).toHaveTextContent('To No value is recorded.')
    const reversal = screen.getByText('Applied candidate').closest('article')
    if (!reversal) throw new Error('keep current reversal history article required')
    expect(reversal).toHaveTextContent('Owner chose the current value.')
    expect(reversal).toHaveTextContent('From Applied candidate')
    expect(reversal).toHaveTextContent('To Original value')
    expect(reversal).not.toHaveTextContent('Retained')
    const keptOwner = screen.getByRole('heading', { name: 'Subtitle' }).closest('article')
    if (!keptOwner) throw new Error('same-valued owner keep current article required')
    expect(keptOwner).toHaveTextContent('Owner chose the current value.')
    expect(keptOwner).toHaveTextContent('Retained Existing subtitle')
    expect(keptOwner).not.toHaveTextContent('From')
    expect(keptOwner).not.toHaveTextContent('To')
    expect(screen.getAllByText('Retained')).toHaveLength(6)
    const typedIdentical = screen.getByRole('heading', { name: 'Series' }).closest('article')
    if (!typedIdentical) throw new Error('typed identical history article required')
    expect(typedIdentical).toHaveTextContent('Automatic policy chose the current value.')
    expect(typedIdentical).toHaveTextContent('Existing series · 2')
    expect(typedIdentical).not.toHaveTextContent('From')
    expect(typedIdentical).not.toHaveTextContent('To')
    const rejected = screen.getByRole('heading', { name: 'Isbn' }).closest('article')
    if (!rejected) throw new Error('reject history article required')
    expect(rejected).toHaveTextContent('Owner declined the proposed change.')
    expect(rejected).toHaveTextContent('9780000000001')
    expect(rejected).not.toHaveTextContent('From')
    expect(rejected).not.toHaveTextContent('To')
    const ignored = screen.getByRole('heading', { name: 'Genres' }).closest('article')
    if (!ignored) throw new Error('ignore history article required')
    expect(ignored).toHaveTextContent('Preserved genre')
    expect(ignored).not.toHaveTextContent('From')
    expect(ignored).not.toHaveTextContent('To')
    const retainedAbsence = screen.getByRole('heading', { name: 'Tags' }).closest('article')
    if (!retainedAbsence) throw new Error('retained absence article required')
    expect(retainedAbsence).toHaveTextContent('No value is recorded.')
    const keptAbsence = screen.getByRole('heading', { name: 'Edition' }).closest('article')
    if (!keptAbsence) throw new Error('kept absence history article required')
    expect(keptAbsence).toHaveTextContent('Retained No value is recorded.')
    expect(keptAbsence).not.toHaveTextContent('From')
    expect(keptAbsence).not.toHaveTextContent('To')
    const unmatchedCandidate = screen
      .getByRole('heading', { name: 'Description' })
      .closest('article')
    if (!unmatchedCandidate) throw new Error('unmatched automatic candidate article required')
    expect(unmatchedCandidate).not.toHaveTextContent('Retained')
    expect(unmatchedCandidate).toHaveTextContent('From No value is recorded.')
    expect(unmatchedCandidate).toHaveTextContent('To No value is recorded.')
    expect(ignored).not.toHaveTextContent('Previous')
    expect(ignored).not.toHaveTextContent('New')
    expect(screen.getByText(/Visual review complete across 2 candidates/u)).toBeVisible()
    expect(screen.getByText('Automatic policy selected this cover.')).toBeVisible()
    expect(
      screen.getByText(
        'This strictly better confirmed cover replaced the earlier automatic choice.',
      ),
    ).toBeVisible()
    expect(screen.getByText('This read-only preview recorded no decision authority.')).toBeVisible()
    expect(screen.getByText('This work entered the review queue.')).toBeVisible()
    expect(document.body).not.toHaveTextContent('<script>actionable</script>')
    expect(document.body).not.toHaveTextContent(/decision-|event-|candidate-|reference-/u)
    expect(document.querySelector('script')).toBeNull()
  })

  it.each(['evidence', 'history'] as const)(
    'guards a superseded %s diagnostic and returns to Library authority',
    async (subview) => {
      const evidence = vi.fn()
      const history = vi.fn()
      const user = userEvent.setup()
      const { router } = renderRoute(
        `/review/case-1?lens=needs_attention&field=title&subview=${subview}`,
        {
          ...createHttp(),
          getCaseDetail: vi.fn(async () => ({ ...detail, current: false })),
          getEvidence: evidence,
          getHistory: history,
        },
      )

      expect(
        await screen.findByRole('heading', {
          name: 'This diagnostic case is no longer current.',
        }),
      ).toBeVisible()
      expect(screen.queryByRole('heading', { name: 'Evidence' })).toBeNull()
      expect(screen.queryByRole('heading', { name: 'History' })).toBeNull()
      expect(evidence).not.toHaveBeenCalled()
      expect(history).not.toHaveBeenCalled()
      await user.click(screen.getByRole('link', { name: 'Back to work' }))
      await waitFor(() =>
        expect(router.state.location.search).toEqual({
          lens: 'needs_attention',
          preview: 'case-1',
          field: 'title',
        }),
      )
    },
  )

  it('keeps Back first during diagnostic loading and typed read failure without retry', async () => {
    const pending = new Promise<CaseDetailView>(() => undefined)
    renderRoute('/review/case-1?lens=needs_attention&field=title&subview=evidence', {
      ...createHttp(),
      getCaseDetail: vi.fn(() => pending),
    })
    const loadingBack = await screen.findByRole('link', { name: 'Back to work' })
    expect(document.querySelector('#main-content .diagnostic-route > :first-child')).toBe(
      loadingBack,
    )
    expect(screen.getByRole('heading', { name: 'Loading work diagnostics…' })).toBeVisible()
    renderRoute('/review/case-failed?lens=no_attention_needed&field=authors&subview=history', {
      ...createHttp(),
      getCaseDetail: vi.fn(async () => Promise.reject(new ApiError(503, 'unavailable'))),
    })
    expect(await screen.findByRole('heading', { name: 'Work diagnostics are unavailable.' }))
    expect(screen.getAllByRole('link', { name: 'Back to work' }).at(-1)).toHaveAttribute(
      'href',
      '/library?lens=no_attention_needed&preview=case-failed&field=authors',
    )
    expect(screen.queryByRole('button', { name: 'Try again' })).toBeNull()
    expect(screen.queryByText(/503|Review API request failed/u)).toBeNull()
  })

  it('opens an Attention field directly in its selected Library preview', async () => {
    const attention: AttentionView = {
      total: 1,
      items: [
        {
          id: 'case-1',
          destination: 'title',
          title: 'A Book',
          authors: [],
          library: 'Books',
          mediaKind: 'ebook',
          classificationReason: 'This field needs your review.',
          notices: [],
          provenance: [],
          workAttentionCount: 1,
        },
      ],
    }
    const user = userEvent.setup()
    const { router } = renderRoute('/attention', {
      ...createHttp(),
      getAttention: vi.fn(async () => attention),
    })
    await user.click(await screen.findByRole('link', { name: /Open exact field/u }))
    expect(await screen.findByRole('heading', { name: 'A Book' })).toBeVisible()
    expect(router.state.location.pathname).toBe('/library')
    expect(router.state.location.search).toEqual({ lens: 'all', preview: 'case-1', field: 'title' })
  })

  it('keeps a later Attention page identity through its exact Library field', async () => {
    const later: AttentionView = {
      total: 2,
      items: [
        {
          id: 'case-later',
          destination: 'authors',
          title: 'Later attention work',
          authors: ['Later owner'],
          library: 'Books',
          mediaKind: 'ebook',
          classificationReason: 'This later field needs your review.',
          notices: [],
          provenance: [],
          workAttentionCount: 1,
        },
      ],
    }
    const field = detail.fields[0]
    if (!field) throw new Error('detail field fixture required')
    const user = userEvent.setup()
    const http = {
      ...createHttp(),
      getAttention: vi.fn(async () => later),
      getCaseDetail: vi.fn(async (id: string) => ({
        ...detail,
        id,
        title: 'Later attention work',
        fields: [{ ...field, destination: 'authors' }],
      })),
    }
    const { router } = renderRoute('/attention?cursor=later-page', http)

    await user.click(await screen.findByRole('link', { name: /Open exact field/u }))
    expect(await screen.findByRole('heading', { name: 'Later attention work' })).toBeVisible()
    expect(router.state.location.search).toEqual({
      lens: 'all',
      attentionCursor: 'later-page',
      preview: 'case-later',
      field: 'authors',
    })
    expect(http.getAttention).toHaveBeenLastCalledWith(
      { cursor: 'later-page' },
      expect.any(AbortSignal),
      'foreground',
    )
  })

  it('records the exact selected scalar choice and refreshes the Library read model', async () => {
    const user = userEvent.setup()
    const field = detail.fields[0]
    if (!field) throw new Error('title fixture required')
    const settled: CaseDetailView = {
      ...detail,
      fields: [
        {
          ...field,
          needsAttention: false,
          humanDecision: {
            actor: 'owner',
            application: 'apply' as const,
            decision_id: 'decision-1',
            destination: field.destination,
            occurred_at: 1,
            outcome: 'accept',
            proposal_id: field.proposalId,
            review_case_id: 'case-1',
            selected_value: field.proposedValues[0] ?? null,
            target_fingerprint: field.targetFingerprint,
          },
        },
      ],
    }
    let current = detail
    const recordDecision = vi.fn(async () => {
      current = settled
      return {
        schema_version: 'review-decision-v1' as const,
        actor: 'owner' as const,
        application: 'apply' as const,
        decision_id: 'decision-1',
        destination: 'title' as const,
        occurred_at: 1,
        outcome: 'accept' as const,
        proposal_id: field.proposalId,
        review_case_id: 'case-1',
        selected_value: field.proposedValues[0] ?? null,
        target_fingerprint: field.targetFingerprint,
        confirmation: { status: 'confirmed' as const, case: current },
        invalidation_sequences: [],
      }
    })
    renderRoute('/library?lens=all&preview=case-1&field=title', {
      ...createHttp(),
      getCaseDetail: vi.fn(async () => current),
      recordDecision,
    })
    await user.click(await screen.findByRole('button', { name: /Choose proposed value/u }))
    await waitFor(() =>
      expect(recordDecision).toHaveBeenCalledWith(
        'case-1',
        expect.objectContaining({
          destination: 'title',
          outcome: 'accept',
          selectedValue: field.proposedValues[0],
        }),
      ),
    )
    expect(await screen.findByRole('heading', { name: 'Review complete' })).toBeVisible()
  })

  it('keeps a current-only field selectable without fabricating an alternative', async () => {
    const field = detail.fields[0]
    if (!field) throw new Error('title fixture required')
    renderRoute('/library?lens=all&preview=case-1&field=title', {
      ...createHttp(),
      getCaseDetail: vi.fn(async () => ({
        ...detail,
        fields: [{ ...field, preservationOnly: true, proposedValues: [] }],
      })),
    })
    expect(await screen.findByRole('button', { name: /Choose current value/u })).toBeVisible()
    expect(screen.queryByText('Choose a new value')).toBeNull()
  })

  it('keeps named retained evidence optional, expandable, sticky, and part of its whole choice', async () => {
    const user = userEvent.setup()
    const field = detail.fields[0]
    if (!field) throw new Error('title fixture required')
    const contextual = {
      alternativeId: null,
      selection: 'current' as const,
      evidence: [
        {
          source: 'Hardcover',
          role: 'external_corroboration' as const,
          observation: 'Title: Old title',
        },
      ],
      limitations: [],
      rationale: 'Retained rationale.',
      recommendation: 'recommended' as const,
      recommendationReason: 'Retained recommendation.',
    }
    renderRoute('/library?lens=all&preview=case-1&field=title', {
      ...createHttp(),
      getCaseDetail: vi.fn(async () => ({
        ...detail,
        fields: [{ ...field, choiceContexts: [contextual] }],
      })),
    })
    const evidence = await screen.findByRole('button', { name: /Evidence for Current value/u })
    await user.click(evidence)
    expect(screen.getByText('Hardcover')).toBeVisible()
    expect(screen.getAllByText('Retained recommendation.')).not.toHaveLength(0)
    expect(evidence).toHaveAttribute('aria-expanded', 'true')
    expect(evidence).toHaveTextContent('Hide evidence')
    await user.click(evidence)
    expect(evidence).toHaveFocus()
    await waitFor(() =>
      expect(screen.getByRole('button', { name: /Evidence for Current value/u })).toHaveAttribute(
        'aria-expanded',
        'false',
      ),
    )
  })

  it('renders recorded fields as safely escaped voluntary revisits outside Attention', async () => {
    const field = detail.fields[0]
    if (!field) throw new Error('title fixture required')
    const recorded = {
      ...field,
      needsAttention: false,
      classification: 'automatic_recorded' as const,
      classificationReason: '<script>untrusted</script>',
      currentValue: '<script>untrusted</script>',
      currentRaw: '<script>untrusted</script>',
      automaticDecision: {
        candidate_id: null,
        decision_id: 'automatic-1',
        destination: 'title' as const,
        occurred_at: 1,
        outcome: 'keep_current' as const,
        proposal_id: field.proposalId,
        review_case_id: 'case-1',
        selected_value: null,
        target_fingerprint: field.targetFingerprint,
      },
    }
    renderRoute('/library?lens=all&preview=case-1&field=title', {
      ...createHttp(),
      getCaseDetail: vi.fn(async () => ({ ...detail, fields: [recorded] })),
    })
    await screen.findByRole('heading', { name: 'A Book' })
    expect(screen.getByText('<script>untrusted</script>')).toBeVisible()
    expect(document.querySelector('script')).toBeNull()
    expect(
      screen.getByText(
        'The recorded outcome is marked below. You can select another eligible value.',
      ),
    ).toBeVisible()
    expect(screen.getByRole('button', { name: /Choose current value/u })).toBeVisible()
    expect(screen.queryByText('Current review case')).toBeNull()
  })

  it('requests fresh checks only after the Library manual-handling confirmation', async () => {
    const user = userEvent.setup()
    const field = detail.fields[0]
    if (!field) throw new Error('title fixture required')
    let settleRequest: (() => void) | undefined
    const returnToAutomatic = vi.fn(
      () =>
        new Promise<void>((resolve) => {
          settleRequest = resolve
        }),
    )
    renderRoute('/library?lens=all&preview=case-1&field=title', {
      ...createHttp(),
      getCaseDetail: vi.fn(async () => ({ ...detail, fields: [{ ...field, manualHold: true }] })),
      returnToAutomatic,
    })

    await screen.findByRole('button', { name: 'Resume automatic handling' })
    await user.click(screen.getByRole('button', { name: 'Resume automatic handling' }))
    const cancel = await screen.findByRole('button', { name: 'Keep manual handling' })
    expect(cancel).toHaveFocus()
    await user.keyboard('{Escape}')
    expect(screen.getByRole('button', { name: 'Resume automatic handling' })).toHaveFocus()
    expect(returnToAutomatic).not.toHaveBeenCalled()

    await user.click(screen.getByRole('button', { name: 'Resume automatic handling' }))
    await user.click(screen.getByRole('button', { name: 'Resume after fresh checks' }))
    expect(screen.getByRole('button', { name: 'Requesting fresh checks…' })).toBeDisabled()
    expect(returnToAutomatic).toHaveBeenCalledOnce()
    settleRequest?.()
    expect(
      await screen.findByText(
        'Fresh checks requested. Manual handling remains until they complete.',
      ),
    ).toBeVisible()
    await user.click(screen.getByRole('button', { name: 'Keep manual handling' }))
    expect(screen.getByRole('button', { name: 'Fresh checks requested' })).toHaveAttribute(
      'aria-disabled',
      'true',
    )
    expect(screen.getByRole('button', { name: 'Fresh checks requested' })).toHaveFocus()
    await user.click(screen.getByRole('button', { name: 'Fresh checks requested' }))
    expect(returnToAutomatic).toHaveBeenCalledOnce()
  })

  it('keeps manual handling unchanged when fresh-check request fails', async () => {
    const user = userEvent.setup()
    const field = detail.fields[0]
    if (!field) throw new Error('title fixture required')
    const returnToAutomatic = vi.fn(async () => Promise.reject(new Error('offline')))
    renderRoute('/library?lens=all&preview=case-1&field=title', {
      ...createHttp(),
      getCaseDetail: vi.fn(async () => ({ ...detail, fields: [{ ...field, manualHold: true }] })),
      returnToAutomatic,
    })

    await user.click(await screen.findByRole('button', { name: 'Resume automatic handling' }))
    await user.click(screen.getByRole('button', { name: 'Resume after fresh checks' }))
    expect(
      await screen.findByText('Fresh checks could not be requested. Manual handling is unchanged.'),
    ).toBeVisible()
    expect(returnToAutomatic).toHaveBeenCalledOnce()
    expect(screen.getByRole('button', { name: 'Keep manual handling' })).toBeEnabled()
  })

  it('keeps a fresh-check success with the field that requested it after navigation', async () => {
    const user = userEvent.setup()
    const title = detail.fields[0]
    if (!title) throw new Error('title fixture required')
    const authors = {
      ...title,
      destination: 'authors',
      targetFingerprint: 'fingerprint-authors',
      manualHold: true,
    }
    let resolveRequest: (() => void) | undefined
    const returnToAutomatic = vi.fn(
      () =>
        new Promise<void>((resolve) => {
          resolveRequest = resolve
        }),
    )
    renderRoute('/library?lens=all&preview=case-1&field=title', {
      ...createHttp(),
      getCaseDetail: vi.fn(async () => ({
        ...detail,
        fields: [{ ...title, manualHold: true }, authors],
      })),
      returnToAutomatic,
    })

    await user.click(await screen.findByRole('button', { name: 'Resume automatic handling' }))
    await user.click(screen.getByRole('button', { name: 'Resume after fresh checks' }))
    await user.click(screen.getByRole('button', { name: /^Authors/u }))
    await screen.findByRole('heading', { name: 'Which authors should this work use?' })
    await user.click(screen.getByRole('button', { name: 'Resume automatic handling' }))
    expect(
      screen.queryByText('Fresh checks requested. Manual handling remains until they complete.'),
    ).toBeNull()
    resolveRequest?.()
    await waitFor(() => expect(returnToAutomatic).toHaveBeenCalledOnce())
    expect(
      screen.queryByText('Fresh checks requested. Manual handling remains until they complete.'),
    ).toBeNull()
    await user.click(screen.getByRole('button', { name: /^Title/u }))
    await screen.findByRole('heading', { name: 'Which title should this work use?' })
    expect(screen.getByRole('button', { name: 'Fresh checks requested' })).toHaveAttribute(
      'aria-disabled',
      'true',
    )
  })

  it('keeps a fresh-check failure with the field that requested it after navigation', async () => {
    const user = userEvent.setup()
    const title = detail.fields[0]
    if (!title) throw new Error('title fixture required')
    const authors = {
      ...title,
      destination: 'authors',
      targetFingerprint: 'fingerprint-authors',
      manualHold: true,
    }
    let rejectRequest: ((error: Error) => void) | undefined
    const returnToAutomatic = vi.fn(
      () =>
        new Promise<void>((_resolve, reject) => {
          rejectRequest = reject
        }),
    )
    renderRoute('/library?lens=all&preview=case-1&field=title', {
      ...createHttp(),
      getCaseDetail: vi.fn(async () => ({
        ...detail,
        fields: [{ ...title, manualHold: true }, authors],
      })),
      returnToAutomatic,
    })

    await user.click(await screen.findByRole('button', { name: 'Resume automatic handling' }))
    await user.click(screen.getByRole('button', { name: 'Resume after fresh checks' }))
    await user.click(screen.getByRole('button', { name: /^Authors/u }))
    await screen.findByRole('heading', { name: 'Which authors should this work use?' })
    await user.click(screen.getByRole('button', { name: 'Resume automatic handling' }))
    rejectRequest?.(new Error('offline'))
    await waitFor(() => expect(returnToAutomatic).toHaveBeenCalledOnce())
    expect(
      screen.queryByText('Fresh checks could not be requested. Manual handling is unchanged.'),
    ).toBeNull()
    await user.click(screen.getByRole('button', { name: /^Title/u }))
    await screen.findByRole('heading', { name: 'Which title should this work use?' })
    await user.click(screen.getByRole('button', { name: 'Resume automatic handling' }))
    expect(
      await screen.findByText('Fresh checks could not be requested. Manual handling is unchanged.'),
    ).toBeVisible()
    expect(screen.getByRole('button', { name: 'Keep manual handling' })).toHaveFocus()
  })

  it('preserves a diagnostic direct entry through Back and Forward', async () => {
    const { router } = renderRoute('/library?lens=all')
    await router.navigate({
      to: '/review/$reviewCaseId',
      params: { reviewCaseId: 'case-1' },
      search: { field: 'title', lens: 'all', subview: 'evidence' },
    })
    await screen.findByRole('heading', { name: 'Evidence' })
    router.history.back()
    await waitFor(() => expect(router.state.location.pathname).toBe('/library'))
    router.history.forward()
    await waitFor(() =>
      expect(router.state.location.search).toEqual({
        field: 'title',
        lens: 'all',
        subview: 'evidence',
      }),
    )
  })
})

it('keeps automatic withholding attached to its field on direct Evidence entry', async () => {
  const reason =
    'Automatic decision withheld: a contributing provider lookup did not supply usable evidence. You can review this field.'
  const http = createHttp()
  const field = detail.fields[0]
  if (!field) throw new Error('Detail fixture field missing')
  http.getCaseDetail = vi.fn(async () => ({
    ...detail,
    fields: [{ ...field, automaticWithheldReason: reason }],
  }))
  renderRoute('/review/case-1?subview=evidence', http)
  expect(await screen.findByText(reason)).toBeVisible()
  expect(screen.getByRole('heading', { name: 'Title' })).toBeVisible()
  expect(screen.queryByRole('button', { name: /Choose/ })).toBeNull()
})

describe('bounded diagnostic return context', () => {
  it.each([undefined, '', 1, [], ['x'], {}, 'x'.repeat(129)])(
    'rejects invalid context %j',
    (value) => {
      expect(
        normalizeDetailSearch({
          q: value,
          cursor: value,
          attentionCursor: value,
          preparation: value,
          field: value,
          attention: value,
          lens: value,
          subview: value,
        }),
      ).toEqual({ lens: 'all', subview: 'evidence' })
    },
  )
  it('projects bounded raw context through tabs, history and Back', async () => {
    const user = userEvent.setup()
    const context = {
      lens: 'needs_attention',
      field: 'title',
      q: 'x'.repeat(128),
      cursor: 'library-page',
      attentionCursor: 'attention-page',
      preparation: 'run',
      attention: 'open',
    }
    expect(normalizeDetailSearch({ ...context, subview: 'history' })).toEqual({
      ...context,
      subview: 'history',
    })
    const params = new URLSearchParams({
      ...context,
      subview: 'evidence',
      preview: 'forged',
      work: 'foreign',
      limit: '500',
      unknown: 'foreign',
    })
    const { router } = renderRoute(`/review/case-1?${params.toString()}`)
    await screen.findByRole('heading', { name: 'Evidence' })
    for (const tab of ['History', 'Evidence']) {
      const link = screen.getByRole('link', { name: tab })
      expect(
        Object.fromEntries(
          new URL(link.getAttribute('href') ?? '', 'https://example.invalid').searchParams,
        ),
      ).toEqual({ ...context, subview: tab.toLowerCase() })
      await user.click(link)
      await screen.findByRole('heading', { name: tab })
    }
    router.history.back()
    await screen.findByRole('heading', { name: 'History' })
    router.history.forward()
    await screen.findByRole('heading', { name: 'Evidence' })
    expect(
      Object.fromEntries(
        new URL(
          screen.getByRole('link', { name: 'Back to work' }).getAttribute('href') ?? '',
          'https://example.invalid',
        ).searchParams,
      ),
    ).toEqual({ ...context, preview: 'case-1' })
  })
  it.each(['loading', 'unavailable', 'stale'] as const)(
    'keeps bounded return context in %s',
    async (state) => {
      const http = createHttp()
      http.getCaseDetail = vi.fn(() =>
        state === 'loading'
          ? new Promise<CaseDetailView>(() => undefined)
          : Promise.reject(new ApiError(state === 'stale' ? 409 : 503, state)),
      )
      renderRoute(
        '/review/case-1?lens=needs_attention&q=needle&cursor=page-two&attentionCursor=queue-two&attention=open&preparation=run&field=title&preview=forged&work=foreign&limit=100',
        http,
      )
      await screen.findByRole('heading', {
        name:
          state === 'loading' ? 'Loading work diagnostics…' : 'Work diagnostics are unavailable.',
      })
      expect(
        Object.fromEntries(
          new URL(
            screen.getByRole('link', { name: 'Back to work' }).getAttribute('href') ?? '',
            'https://example.invalid',
          ).searchParams,
        ),
      ).toEqual({
        lens: 'needs_attention',
        q: 'needle',
        cursor: 'page-two',
        attentionCursor: 'queue-two',
        attention: 'open',
        preparation: 'run',
        field: 'title',
        preview: 'case-1',
      })
    },
  )
  it('drops malformed raw context from actual rendered links', async () => {
    const params = new URLSearchParams({
      q: '["array"]',
      cursor: '1',
      attentionCursor: '{}',
      preparation: 'x'.repeat(129),
      attention: 'closed',
      field: '',
      preview: 'foreign',
      work: 'foreign',
      unknown: 'x',
    })
    renderRoute(`/review/case-1?${params.toString()}`)
    await screen.findByRole('heading', { name: 'Evidence' })
    for (const name of ['History', 'Back to work']) {
      const query = Object.fromEntries(
        new URL(
          screen.getByRole('link', { name }).getAttribute('href') ?? '',
          'https://example.invalid',
        ).searchParams,
      )
      expect(query).toEqual(
        name === 'History'
          ? { lens: 'all', subview: 'history' }
          : { lens: 'all', preview: 'case-1' },
      )
    }
  })
})

it.each(['evidence', 'history'])(
  'keeps %s diagnostic-only while retaining authored history',
  async (subview) => {
    const authored = mapCaseDetail(descriptionAdviceFixture)
    const candidate = authored.fields.find((field) => field.destination === 'description')
      ?.descriptionAdvice?.candidate
    if (!candidate) throw new Error('description fixture requires its candidate')
    const http = createHttp()
    http.getCaseDetail = vi.fn(async () => ({ ...authored, id: 'case-1' }))
    http.getHistory = vi.fn(async () => ({
      items: [
        {
          category: 'description' as const,
          outcome: 'description_advice_recorded',
          state: 'succeeded',
          engine: candidate.engine,
          model: candidate.model,
          candidate,
          selected: false,
        },
        {
          category: 'description' as const,
          outcome: 'description_selected',
          state: 'superseded',
          engine: candidate.engine,
          model: candidate.model,
          candidate,
          selected: true,
        },
        {
          category: 'description' as const,
          outcome: 'description_application',
          state: 'verified',
          engine: candidate.engine,
          model: candidate.model,
          candidate,
          selected: false,
        },
      ],
    }))
    renderRoute(`/review/case-1?field=description&subview=${subview}`, http)
    expect(await screen.findByRole('heading', { level: 1, name: authored.title })).toBeVisible()
    expect(
      screen.queryByRole('button', { name: /Choose advised|Retry description|Use advised/ }),
    ).toBeNull()
    expect(screen.queryByRole('article', { name: 'Advised description' })).toBeNull()
    if (subview === 'history') {
      expect(
        await screen.findByText('Applied and verified in the library.', { exact: false }),
      ).toBeVisible()
      expect(
        screen.getByText('This authored selection was superseded by a later decision.', {
          exact: false,
        }),
      ).toBeVisible()
      expect(
        screen.getAllByRole('heading', { name: 'Advised description · Authored content' }),
      ).toHaveLength(3)
    } else {
      await waitFor(() => expect(http.getEvidence).toHaveBeenCalled())
      expect(screen.queryByText(candidate.output.description)).toBeNull()
      expect(http.getHistory).not.toHaveBeenCalled()
    }
  },
)
