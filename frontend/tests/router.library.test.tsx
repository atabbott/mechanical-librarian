import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { RouterProvider, createMemoryHistory } from '@tanstack/react-router'
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { ApiError } from '../src/api/http'
import type { AttentionSearch, CaseDetailView, LibrarySearch, LibraryView, ReviewHttp } from '../src/api/http'
import { createApplicationRouter, normalizeLibrarySearch } from '../src/router'
import { createHttp, detail, library, renderRoute } from './fixtures'

describe('Library route', () => {
  it('bounds and normalizes URL-owned library search state', () => {
    expect(normalizeLibrarySearch({ lens: 'all', q: 'title', cursor: 'next', preview: 'case-1' })).toEqual({
      lens: 'all', q: 'title', cursor: 'next', preview: 'case-1',
    })
    expect(normalizeLibrarySearch({ lens: 'hostile', q: 'x'.repeat(129) })).toEqual({ lens: 'all' })
  })

  it.each(['needs_attention', 'no_attention_needed'] as const)(
    'keeps primary Library navigation active for the %s lens',
    async (lens) => {
      renderRoute(`/library?lens=${lens}`)
      expect(await screen.findByRole('heading', { name: 'Every work, with exceptions first' })).toBeVisible()
      expect(screen.getByRole('link', { name: 'Library' })).toHaveAttribute('aria-current', 'page')
    },
  )

  it('keeps the selected-work header as the only Library route-focus target', async () => {
    renderRoute('/library?lens=all&preview=case-1&field=title')
    expect(await screen.findByText('Progressive detail')).toBeVisible()
    const targets = document.querySelectorAll('[data-route-focus]')
    expect(targets).toHaveLength(1)
    expect(targets[0]).toHaveClass('selected-work-header')
  })

  it('loads the critical library query and progressively reveals detail', async () => {
    const user = userEvent.setup()
    const http: ReviewHttp = { ...createHttp(), getCaseDetail: vi.fn(async () => ({ ...detail, title: 'item-1', subtitle: 'internal identity' })) }
    renderRoute('/library?lens=all&q=title', http)

    expect(await screen.findByRole('heading', { name: 'Every work, with exceptions first' })).toBeVisible()
    expect(http.getLibrary).toHaveBeenCalledWith({ lens: 'all', q: 'title' }, expect.any(AbortSignal))
    await user.click(screen.getByRole('button', { name: 'A Book' }))
    expect(await screen.findByText('Progressive detail')).toBeVisible()
    expect(screen.getByRole('heading', { level: 2, name: 'A Book' })).toBeVisible()
    expect(screen.queryByText('item-1')).toBeNull()
    expect(screen.getByText('Author details unavailable · Books · ebook')).toBeVisible()
    expect(http.getCaseDetail).toHaveBeenCalledWith('case-1', expect.any(AbortSignal))
  })

  it('keeps bounded provider outcomes and failed preparation inspectable in Library', async () => {
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
      preparation: { attempt: 1, provider: 'hardcover-main', reason: 'Provider credentials need owner attention.', recovery: 'Retry review preparation after correcting the reported condition.', recoveryAttempt: 0, stage: 'provider', status: 'authentication' },
      preparationOutcomes: [{ providerRequests: 1, laneKind: 'work', provider: 'hardcover-main', reason: 'Provider credentials need owner attention.', status: 'authentication' }],
    }
    const http: ReviewHttp = { ...createHttp(), getLibrary: vi.fn(async () => ({ lens: 'all' as const, counts: { attention: 0, all: 1, clear: 1 }, works: [failed] })) }
    renderRoute('/library?lens=all', http)

    expect(await screen.findByText('Preparation failed safely')).toBeVisible()
    await userEvent.click(screen.getByText('Preparation needs owner recovery'))
    const outcomes = screen.getByText('Preparation needs owner recovery').closest('details')
    expect(outcomes).toHaveTextContent('Authentication at Provider')
    expect(outcomes).toHaveTextContent('hardcover-main · Work · Authentication · 1 bounded provider request')
    expect(screen.queryByRole('button', { name: 'Preparation unavailable' })).toBeNull()
  })

  it.each([
    [1, '1 field needs attention'],
    [2, '2 fields need attention'],
  ] as const)('agrees the work-card attention noun and verb for %i field(s)', async (attentionCount, copy) => {
    const firstWork = library.works[0]
    if (!firstWork) throw new Error('library fixture requires one work')
    const http: ReviewHttp = {
      ...createHttp(),
      getLibrary: vi.fn(async () => ({ ...library, works: [{ ...firstWork, attentionCount }] })),
    }
    renderRoute('/library?lens=all', http)

    expect(await screen.findByText(copy)).toBeVisible()
  })

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

    const condition = await screen.findByRole('complementary', { name: 'Provider comparison condition' })
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
      classificationReason: 'Automatic handling recorded this field; you may inspect or override it.',
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
      getCaseDetail: vi.fn(async () => ({ ...detail, fields: [{ ...titleField, effectCapability: 'allowed', notices: ['competing_evidence'], choiceContexts: [
        { alternativeId: null, selection: 'current', rationale: 'Keep the value currently held by the local library.', recommendation: 'no_recommendation', recommendationReason: 'No deterministic recommendation is retained for the current value.', limitations: [], evidence: [{ source: 'Local library record', observation: 'Title: Old title', role: 'local_presence' }] },
        { alternativeId: 'alternative-title-1', selection: 'proposed', rationale: 'This retained alternative is eligible for owner selection.', recommendation: 'no_recommendation', recommendationReason: 'No deterministic recommendation is retained for this alternative.', limitations: [], evidence: [{ source: 'Open Library', observation: 'Title: New title', role: 'external_corroboration' }] },
      ] as const }, automaticField] })),
    }
    renderRoute('/library?lens=all&preview=case-1&field=title', http)

    expect(await screen.findByRole('button', { name: 'Choose current value: Old title' })).toBeVisible()
    expect(screen.getByText('Eligible for guarded planning')).toBeVisible()
    const evidenceButtons = screen.getAllByRole('button', { name: /Evidence for/u })
    const currentEvidence = evidenceButtons[0]
    const proposedEvidence = evidenceButtons[1]
    if (!currentEvidence || !proposedEvidence) throw new Error('choice evidence controls are missing')
    expect(currentEvidence).toHaveAccessibleName('Evidence for Current value: Old title')
    expect(proposedEvidence).toHaveAccessibleName('Evidence for Alternative 1: New title')
    await user.click(currentEvidence)
    expect(currentEvidence).toHaveFocus()
    expect(currentEvidence).toHaveAttribute('aria-controls', 'choice-evidence-current')
    expect(await screen.findByText('Local library record')).toBeVisible()
    expect(screen.getByText('Title: Old title')).toBeVisible()
    expect(screen.getByText('Local presence')).toBeVisible()
    expect(screen.getAllByText('No recommendation retained').length).toBeGreaterThan(0)
    expect(screen.queryByText('Source ledger')).not.toBeInTheDocument()
    await user.click(currentEvidence)
    expect(currentEvidence).toHaveFocus()
    await user.click(proposedEvidence)
    expect(await screen.findByText('Open Library')).toBeVisible()
    expect(screen.getByText('Title: New title')).toBeVisible()
    expect(screen.getByText('External corroboration')).toBeVisible()
    expect(screen.getAllByText('Competing evidence').length).toBeGreaterThan(0)
    await user.click(screen.getByText('1 automatic or recorded field'))
    expect(screen.getByText('Resolved automatically · Atlas Cycle 2.5')).toBeVisible()
    expect(screen.getByText('Automatic handling recorded this field; you may inspect or override it.')).toBeVisible()
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
      choiceContexts: [{ alternativeId: null, selection: 'current', evidence: [], limitations: [], rationale: 'Keep the local cover.', recommendation: 'no_recommendation', recommendationReason: 'No recommendation retained.' }, { alternativeId: reference(2), selection: 'proposed', evidence: [], limitations: [], rationale: 'Selectable retained cover.', recommendation: 'no_recommendation', recommendationReason: 'No recommendation retained.' }, { alternativeId: reference(3), selection: 'proposed', evidence: [], limitations: [], rationale: 'Selectable retained cover.', recommendation: 'no_recommendation', recommendationReason: 'No recommendation retained.' }, { alternativeId: reference(4), selection: 'proposed', evidence: [], limitations: [], rationale: 'Selectable retained cover.', recommendation: 'no_recommendation', recommendationReason: 'No recommendation retained.' }],
      current: true,
      candidates: [candidate(1, true), candidate(2), candidate(3), candidate(4), { ...candidate(5), selectable: false, identity: { outcome: 'verified_work_match', reasons: ['accepted_work_identifier'] }, observation: { composition: 'dense', medium: 'photographic', promotional_text: 'absent', tie_in_branding: 'absent' } }, { ...candidate(6), selectable: false, identity: { outcome: 'verified_work_match', reasons: ['accepted_work_identifier'] }, observation: { composition: 'dense', medium: 'photographic', promotional_text: 'present', tie_in_branding: 'absent' } }],
      incumbent: { reference: reference(1), state: 'available' as const },
      omissions: [{ reason: 'preview_unavailable', reference: 'retained-unavailable-source' }],
      policy: { recommended_candidate_reference: reference(3), acceptable_candidate_references: [reference(2), reference(3), reference(4)], status: 'abstained', reasons: ['observations_ambiguous'] },
      policyPreferences: { avoidPromotionalText: true, avoidTieInBranding: false, preferMinimalComposition: false },
      policyReference: `cover_policy_${'3'.repeat(64)}`,
      reviewerStatus: 'completed', targetFingerprint: 'fingerprint-cover',
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
    const http: ReviewHttp = { ...createHttp(), getCaseDetail: vi.fn(async () => ({ ...detail, fields: [coverField] })) }
    renderRoute('/library?lens=all&preview=case-1&field=cover', http)

    const comparison = await screen.findByLabelText('Choose a cover')
    expect(within(comparison).getByText('Recommendation is unavailable; comparison remains manual.')).toBeVisible()
    expect(within(comparison).queryByText(/Recommended/u)).not.toBeInTheDocument()
    await user.click(within(comparison).getByText('Reasons retained'))
    expect(within(comparison).getByText('Preview unavailable')).toBeVisible()
    expect(within(comparison).getByText('Observations ambiguous')).toBeVisible()
    expect(within(comparison).getByText('Promotional text excluded by policy')).toBeVisible()
    expect(comparison.querySelector('.library-cover-pinned')).not.toBeNull()
    expect(comparison.querySelectorAll('.library-cover-gallery .library-cover-card')).toHaveLength(3)
  })

  it('retains an explicit keep-current control when the incumbent preview is unavailable', async () => {
    const user = userEvent.setup()
    const base = detail.fields[0]
    if (!base) throw new Error('cover fixture requires a field')
    const consideration = {
      id: `cover_consideration_${'u'.repeat(64)}`,
      choiceContexts: [{ alternativeId: null, selection: 'current', evidence: [{ source: 'Local library record', role: 'local_presence', observation: 'Current cover preview is unavailable; local presence is retained.' }], limitations: ['preview_unavailable'], rationale: 'Keep the retained local cover.', recommendation: 'no_recommendation', recommendationReason: 'No recommendation retained.' }, { alternativeId: `cover_transport_ref_${'a'.repeat(64)}`, selection: 'proposed', evidence: [], limitations: [], rationale: 'Selectable retained cover.', recommendation: 'no_recommendation', recommendationReason: 'No recommendation retained.' }],
      current: true,
      candidates: [{ artifactId: `cover_${'a'.repeat(64)}`, height: 1200, identity: { outcome: 'plausible_match' }, incumbent: false, observation: null, originalSha256: 'a'.repeat(64), ordinal: 1, previewSha256: 'a'.repeat(64), reference: `cover_transport_ref_${'a'.repeat(64)}`, selectable: true, sourceKinds: ['provider'], state: 'acceptable', width: 800 }],
      incumbent: { reference: `cover_transport_ref_${'b'.repeat(64)}`, state: 'unavailable' as const }, omissions: [], policy: null,
      policyPreferences: { avoidPromotionalText: false, avoidTieInBranding: false, preferMinimalComposition: false }, policyReference: `cover_policy_${'u'.repeat(64)}`, reviewerStatus: 'completed', targetFingerprint: 'fingerprint-cover',
    } as const
    const coverField = { ...base, destination: 'cover', proposedValues: [{ kind: 'cover', value: `cover_${'a'.repeat(64)}` }], coverConsideration: consideration }
    renderRoute('/library?lens=all&preview=case-1&field=cover', { ...createHttp(), getCaseDetail: vi.fn(async () => ({ ...detail, fields: [coverField] })) })

    expect(await screen.findByRole('button', { name: /Current cover.*Unavailable/u })).toBeVisible()
    const evidence = screen.getByRole('button', { name: 'Evidence for Current cover' })
    await user.click(evidence)
    expect(evidence).toHaveFocus()
    expect(screen.getByText('Local presence')).toBeVisible()
    expect(screen.getByText('Preview unavailable')).toBeVisible()
    expect(screen.getByRole('button', { name: /Current cover.*Unavailable/u })).toHaveAttribute('aria-pressed', 'false')
    expect(screen.getByText('Choices').nextElementSibling).toHaveTextContent('3')
  })

  it('closes cached cover evidence after client-side A to B to A work navigation', async () => {
    const user = userEvent.setup()
    const baseField = detail.fields[0]
    const baseWork = library.works[0]
    if (!baseField || !baseWork) throw new Error('cover reset fixture requires a work and field')
    const cover = (id: string): CaseDetailView => ({ ...detail, id, fields: [{ ...baseField, destination: 'cover', proposedValues: [{ kind: 'cover', value: 'cover-alt' }], coverConsideration: { id: `consideration-${id}`, current: true, candidates: [{ artifactId: 'cover-current', height: 100, identity: {}, incumbent: true, observation: null, originalSha256: 'a'.repeat(64), ordinal: 0, previewSha256: 'b'.repeat(64), reference: 'current', selectable: true, sourceKinds: [], state: 'available', width: 100 }, { artifactId: 'cover-alt', height: 100, identity: {}, incumbent: false, observation: null, originalSha256: 'c'.repeat(64), ordinal: 1, previewSha256: 'd'.repeat(64), reference: 'alternative', selectable: true, sourceKinds: [], state: 'available', width: 100 }], choiceContexts: [{ alternativeId: null, selection: 'current', evidence: [{ source: 'Local library record', role: 'local_presence', observation: 'Local cover retained.' }], limitations: [], rationale: 'Current cover.', recommendation: 'no_recommendation', recommendationReason: 'No recommendation.' }, { alternativeId: 'alternative', selection: 'proposed', evidence: [], limitations: [], rationale: 'Alternative cover.', recommendation: 'no_recommendation', recommendationReason: 'No recommendation.' }], incumbent: { reference: 'current', state: 'available' }, omissions: [], policy: { status: 'abstained', recommended_candidate_reference: null, acceptable_candidate_references: [] }, policyPreferences: { avoidPromotionalText: false, avoidTieInBranding: false, preferMinimalComposition: false }, policyReference: 'policy', reviewerStatus: 'completed', targetFingerprint: 'fingerprint' } }] })
    const twoWorks: LibraryView = { ...library, works: [{ ...baseWork, title: 'Work A' }, { ...baseWork, id: 'case-2', reviewCaseId: 'case-2', title: 'Work B' }] }
    const http = { ...createHttp(), getLibrary: vi.fn(async () => twoWorks), getCaseDetail: vi.fn(async (id: string) => cover(id)) }
    renderRoute('/library?lens=all&preview=case-1&field=cover', http)
    const evidence = await screen.findByRole('button', { name: 'Evidence for Current cover' })
    await user.click(evidence)
    expect(screen.getByLabelText('Current cover evidence')).toBeVisible()
    await user.click(screen.getByRole('button', { name: 'Work B' }))
    await screen.findByRole('button', { name: 'Evidence for Current cover' })
    expect(screen.queryByLabelText('Current cover evidence')).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Work A' }))
    await screen.findByRole('button', { name: 'Evidence for Current cover' })
    expect(screen.queryByLabelText('Current cover evidence')).not.toBeInTheDocument()
    expect(http.recordDecision).not.toHaveBeenCalled()
  })

  it('prepares a missing cover comparison inside the Library workflow', async () => {
    const user = userEvent.setup()
    const base = detail.fields[0]
    if (!base) throw new Error('cover fixture requires a field')
    const consideration = {
      id: `cover_consideration_${'p'.repeat(64)}`, current: true,
      choiceContexts: [{ alternativeId: null, selection: 'current', evidence: [], limitations: [], rationale: 'Keep the local cover.', recommendation: 'no_recommendation', recommendationReason: 'No recommendation retained.' }, { alternativeId: `cover_transport_ref_${'a'.repeat(64)}`, selection: 'proposed', evidence: [], limitations: [], rationale: 'Selectable retained cover.', recommendation: 'no_recommendation', recommendationReason: 'No recommendation retained.' }],
      candidates: [{ artifactId: `cover_${'a'.repeat(64)}`, height: 1200, identity: { outcome: 'plausible_match' }, incumbent: false, observation: null, originalSha256: 'a'.repeat(64), ordinal: 1, previewSha256: 'a'.repeat(64), reference: `cover_transport_ref_${'a'.repeat(64)}`, selectable: true, sourceKinds: ['provider'], state: 'acceptable', width: 800 }],
      incumbent: { reference: `cover_transport_ref_${'b'.repeat(64)}`, state: 'unavailable' as const }, omissions: [], policy: null,
      policyPreferences: { avoidPromotionalText: false, avoidTieInBranding: false, preferMinimalComposition: false }, policyReference: `cover_policy_${'p'.repeat(64)}`, reviewerStatus: 'completed', targetFingerprint: 'fingerprint-cover',
    } as const
    const absent: CaseDetailView['fields'][number] = { ...base, destination: 'cover', proposedValues: [{ kind: 'cover', value: `cover_${'a'.repeat(64)}` }], coverConsideration: null }
    const prepared: CaseDetailView['fields'][number] = { ...absent, coverConsideration: consideration }
    let field: CaseDetailView['fields'][number] = absent
    let preparationAttempts = 0
    const retryCoverConsideration = vi.fn(async () => {
      preparationAttempts += 1
      if (preparationAttempts === 1) throw new Error('temporary preparation failure')
      field = prepared
      return consideration
    })
    const http: ReviewHttp = { ...createHttp(), getCaseDetail: vi.fn(async () => ({ ...detail, fields: [field] })), retryCoverConsideration }
    renderRoute('/library?lens=all&preview=case-1&field=cover', http)

    const prepare = await screen.findByRole('button', { name: 'Prepare cover comparison' })
    expect(screen.getByText('Choices').nextElementSibling).toHaveTextContent('0')
    await user.click(prepare)
    expect(await screen.findByRole('alert')).toHaveTextContent('The comparison could not be prepared. The existing cover remains unchanged.')
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
      getCaseDetail: vi.fn(async () => ({ ...detail, fields: [{ ...titleField, choiceContexts: [{ alternativeId: null, selection: 'current', rationale: 'Keep the value currently held by the local library.', recommendation: 'no_recommendation', recommendationReason: 'No deterministic recommendation is retained for the current value.', limitations: [], evidence: [{ source: 'Local library record', observation: 'Title: retained current value', role: 'local_presence' }] }, { alternativeId: 'alternative-title-1', selection: 'proposed', rationale: 'This retained alternative is eligible for owner selection.', recommendation: 'no_recommendation', recommendationReason: 'No deterministic recommendation is retained for this alternative.', limitations: [], evidence: [{ source: 'Open Library', observation: 'Title: New title', role: 'external_corroboration' }] }] as const }] })),
      getEvidence: vi.fn(async () => { throw new Error('diagnostic evidence unavailable') }),
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
    const authorsField = { ...titleField, destination: 'authors', currentValue: 'Ada Example', proposalId: 'proposal-authors', targetFingerprint: 'fingerprint-authors' }
    const initial = { ...detail, fields: [titleField, authorsField] }
    const settled = { ...detail, fields: [{
      ...titleField,
      needsAttention: false,
      humanDecision: {
        actor: 'owner', decision_id: 'decision-title', destination: 'title', occurred_at: 1,
        outcome: 'keep_current' as const, proposal_id: titleField.proposalId, review_case_id: 'case-1',
        selected_value: null, target_fingerprint: titleField.targetFingerprint,
      },
    }, authorsField] }
    let current: CaseDetailView = initial
    const http: ReviewHttp = {
      ...createHttp(),
      getCaseDetail: vi.fn(async () => current),
      recordDecision: vi.fn(async () => {
        current = settled
        return { schema_version: 'review-decision-v1' as const, actor: 'owner' as const, decision_id: 'decision-title', destination: 'title', occurred_at: 1, outcome: 'keep_current' as const, proposal_id: titleField.proposalId, review_case_id: 'case-1', selected_value: null, target_fingerprint: titleField.targetFingerprint }
      }),
    }
    renderRoute('/library?lens=all&preview=case-1&field=title', http)

    expect(await screen.findByRole('button', { name: /Authors.*Needs review/u })).toBeVisible()
    await user.click(screen.getByRole('button', { name: 'Choose current value: Old title' }))
    expect(await screen.findByText('Decision recorded. Continuing with authors.')).toBeVisible()
    await waitFor(() => expect(screen.getByRole('button', { name: /Authors.*Needs review/u })).toHaveAttribute('aria-current', 'true'))
    expect(screen.getByRole('button', { name: /Title.*Decision recorded/u })).toBeVisible()
  })

  it('retries the embedded Library decision with the exact preserved command and idempotency key', async () => {
    const user = userEvent.setup()
    const titleField = detail.fields[0]
    if (!titleField) throw new Error('workspace fixture requires a title field')
    const settled: CaseDetailView = { ...detail, fields: [{
      ...titleField,
      needsAttention: false,
      humanDecision: {
        actor: 'owner', decision_id: 'decision-retried', destination: 'title', occurred_at: 1,
        outcome: 'keep_current', proposal_id: titleField.proposalId, review_case_id: 'case-1',
        selected_value: null, target_fingerprint: titleField.targetFingerprint,
      },
    }] }
    let current = detail
    let attempts = 0
    const recordDecision = vi.fn<ReviewHttp['recordDecision']>(async () => {
      attempts += 1
      if (attempts === 1) throw new ApiError(503, 'decision_failed')
      current = settled
      return { schema_version: 'review-decision-v1' as const, actor: 'owner' as const, decision_id: 'decision-retried', destination: 'title', occurred_at: 1, outcome: 'keep_current' as const, proposal_id: titleField.proposalId, review_case_id: 'case-1', selected_value: null, target_fingerprint: titleField.targetFingerprint }
    })
    const http: ReviewHttp = { ...createHttp(), getCaseDetail: vi.fn(async () => current), recordDecision }
    renderRoute('/library?lens=all&preview=case-1&field=title', http)

    await user.click(await screen.findByRole('button', { name: 'Choose current value: Old title' }))
    expect(await screen.findByText('The decision was not recorded. Your choice is unchanged.')).toBeVisible()
    expect(screen.getByRole('button', { name: 'Choose current value: Old title' })).not.toHaveTextContent('Recording…')
    const retry = screen.getByRole('button', { name: 'Try the same decision again' })
    expect(retry).toHaveFocus()
    const firstCommand = recordDecision.mock.calls[0]?.[1]
    await user.click(retry)

    await waitFor(() => expect(recordDecision).toHaveBeenCalledTimes(2))
    expect(recordDecision.mock.calls[1]?.[1]).toEqual(firstCommand)
    expect(recordDecision.mock.calls[1]?.[1].idempotencyKey).toBe(firstCommand?.idempotencyKey)
    expect(await screen.findByText('Review complete')).toBeVisible()
    expect(screen.getByRole('button', { name: /Title.*Current value retained.*Old title/u })).toBeVisible()
    expect(document.activeElement).toHaveClass('review-complete')
  })

  it('marks the exact choice while recording and focuses authoritative confirmation before advancing', async () => {
    const user = userEvent.setup()
    const titleField = detail.fields[0]
    if (!titleField) throw new Error('workspace fixture requires a title field')
    const authorsField = { ...titleField, destination: 'authors', proposalId: 'proposal-authors', targetFingerprint: 'fingerprint-authors' }
    const settled: CaseDetailView = { ...detail, fields: [{ ...titleField, needsAttention: false, humanDecision: { actor: 'owner', decision_id: 'decision-title', destination: 'title', occurred_at: 1, outcome: 'keep_current', proposal_id: titleField.proposalId, review_case_id: 'case-1', selected_value: null, target_fingerprint: titleField.targetFingerprint } }, authorsField] }
    let current: CaseDetailView = { ...detail, fields: [titleField, authorsField] }
    let release: (() => void) | undefined
    const deferred = new Promise<void>((resolve) => { release = resolve })
    const http: ReviewHttp = { ...createHttp(), getCaseDetail: vi.fn(async () => current), recordDecision: vi.fn(async () => { await deferred; current = settled; return { schema_version: 'review-decision-v1', actor: 'owner', decision_id: 'decision-title', destination: 'title', occurred_at: 1, outcome: 'keep_current', proposal_id: titleField.proposalId, review_case_id: 'case-1', selected_value: null, target_fingerprint: titleField.targetFingerprint } }) }
    renderRoute('/library?lens=all&preview=case-1&field=title', http)

    const choice = await screen.findByRole('button', { name: 'Choose current value: Old title' })
    await user.click(choice)
    expect(choice).toHaveAttribute('aria-pressed', 'true')
    expect(choice).toHaveTextContent('Recording…')
    const competing = screen.getByRole('button', { name: 'Choose proposed value: New title' })
    expect(competing).toBeDisabled()
    fireEvent.click(competing)
    expect(http.recordDecision).toHaveBeenCalledTimes(1)
    expect(screen.getByRole('button', { name: /Authors.*Needs review/u })).not.toHaveAttribute('aria-current', 'true')

    await act(async () => { release?.() })
    const confirmationText = await screen.findByText('The authoritative case confirms this exact field outcome.')
    const confirmed = confirmationText.closest<HTMLElement>('[role="status"]')
    if (!confirmed) throw new Error('authoritative confirmation region is required')
    expect(confirmed).toHaveTextContent('The authoritative case confirms this exact field outcome.')
    expect(confirmed).toHaveFocus()
    await waitFor(() => expect(screen.getByRole('button', { name: /Authors.*Needs review/u })).toHaveAttribute('aria-current', 'true'))
  })

  it('suspends the confirmation dwell for its whole interval before continuing', async () => {
    const titleField = detail.fields[0]
    if (!titleField) throw new Error('workspace fixture requires a title field')
    const authorsField = { ...titleField, destination: 'authors', proposalId: 'proposal-authors', targetFingerprint: 'fingerprint-authors' }
    const settled: CaseDetailView = { ...detail, fields: [{ ...titleField, needsAttention: false, humanDecision: { actor: 'owner', decision_id: 'decision-title', destination: 'title', occurred_at: 1, outcome: 'keep_current', proposal_id: titleField.proposalId, review_case_id: 'case-1', selected_value: null, target_fingerprint: titleField.targetFingerprint } }, authorsField] }
    let current: CaseDetailView = { ...detail, fields: [titleField, authorsField] }
    const http: ReviewHttp = { ...createHttp(), getCaseDetail: vi.fn(async () => current), recordDecision: vi.fn(async () => { current = settled; return { schema_version: 'review-decision-v1', actor: 'owner', decision_id: 'decision-title', destination: 'title', occurred_at: 1, outcome: 'keep_current', proposal_id: titleField.proposalId, review_case_id: 'case-1', selected_value: null, target_fingerprint: titleField.targetFingerprint } }) }
    // Fake clock installed before the first decision, and `settle` pinned to
    // interval 0, for the reason recorded on the navigation test below.
    vi.useFakeTimers()
    try {
      const settle = async (until: () => unknown) => { await vi.waitFor(until, { interval: 0 }) }
      const continued = () => screen.queryByText('Decision recorded. Continuing with authors.')
      const choice = () => screen.getByRole('button', { name: 'Choose current value: Old title' })
      renderRoute('/library?lens=all&preview=case-1&field=title', http)

      await vi.waitFor(() => expect(choice()).toBeEnabled())
      fireEvent.click(choice())
      await settle(() => expect(screen.getByText('The authoritative case confirms this exact field outcome.')).toBeVisible())

      // Only the code after the awaited dwell writes this status, so its absence
      // at 319 ms and its arrival at 320 ms pin the controller to that timer. A
      // timer-count delta cannot: it survives a dwell that schedules the 320 ms
      // timer and then awaits a different one.
      await act(async () => { await vi.advanceTimersByTimeAsync(319) })
      expect(continued()).toBeNull()
      await act(async () => { await vi.advanceTimersByTimeAsync(1) })
      await settle(() => expect(continued()).toBeVisible())
    } finally {
      vi.useRealTimers()
    }
  })

  it('clears authoritative confirmation when the reviewer navigates during its dwell', async () => {
    const titleField = detail.fields[0]
    if (!titleField) throw new Error('workspace fixture requires a title field')
    const authorsField = { ...titleField, destination: 'authors', proposalId: 'proposal-authors', targetFingerprint: 'fingerprint-authors' }
    const settled: CaseDetailView = { ...detail, fields: [{ ...titleField, needsAttention: false, humanDecision: { actor: 'owner', decision_id: 'decision-title', destination: 'title', occurred_at: 1, outcome: 'keep_current', proposal_id: titleField.proposalId, review_case_id: 'case-1', selected_value: null, target_fingerprint: titleField.targetFingerprint } }, authorsField] }
    let current: CaseDetailView = { ...detail, fields: [titleField, authorsField] }
    let rejectAuthors: ((error: unknown) => void) | undefined
    const authorsDecision = new Promise<never>((_resolve, reject) => { rejectAuthors = reject })
    const recordDecision = vi.fn<ReviewHttp['recordDecision']>(async (_id, command) => { if (command.destination === 'authors') return authorsDecision; current = settled; return { schema_version: 'review-decision-v1', actor: 'owner', decision_id: 'decision-title', destination: 'title', occurred_at: 1, outcome: 'keep_current', proposal_id: titleField.proposalId, review_case_id: 'case-1', selected_value: null, target_fingerprint: titleField.targetFingerprint } })
    const http: ReviewHttp = { ...createHttp(), getCaseDetail: vi.fn(async () => current), recordDecision }
    // The dwell is scheduled the instant the confirmation renders, so the fake
    // clock has to be installed before the first decision is recorded: a timer
    // created on the real clock cannot be advanced afterwards. That rules out
    // `userEvent` and `findBy*` for the whole block — see Test economy in
    // docs/development/testing-strategy.md. `settle` polls on the real clock
    // and advances the fake one by nothing, so the dwell elapses only where
    // this test says it does.
    vi.useFakeTimers()
    try {
      const settle = async (until: () => unknown) => { await vi.waitFor(until, { interval: 0 }) }
      const confirmation = () => screen.queryByText('The authoritative case confirms this exact field outcome.')
      const choice = () => screen.getByRole('button', { name: 'Choose current value: Old title' })
      renderRoute('/library?lens=all&preview=case-1&field=title', http)

      // The route's own load needs the clock; no dwell exists yet to disturb.
      await vi.waitFor(() => expect(choice()).toBeEnabled())
      fireEvent.click(choice())
      await settle(() => expect(confirmation()).toBeVisible())
      fireEvent.click(screen.getByRole('button', { name: /Authors.*Needs review/u }))
      await settle(() => expect(screen.getByRole('button', { name: /Authors.*Needs review/u })).toHaveAttribute('aria-current', 'true'))
      const authorsChoice = choice()
      fireEvent.click(authorsChoice)
      await settle(() => expect(authorsChoice).toHaveTextContent('Recording…'))

      // Navigating away is what hides the confirmation, so here the dwell's
      // expiry shows in nothing the reviewer can see — its only duty is to
      // leave the decision started after it alone. The advance is made
      // load-bearing on the timer instead: still scheduled a millisecond short
      // of 320 ms, retired on the millisecond it was set for. That is all a
      // count delta can show; the dwell itself is pinned to the controller by
      // the continuation test above.
      await act(async () => { await vi.advanceTimersByTimeAsync(319) })
      const pendingDwell = vi.getTimerCount()
      await act(async () => { await vi.advanceTimersByTimeAsync(1) })
      expect(vi.getTimerCount()).toBe(pendingDwell - 1)

      expect(confirmation()).toBeNull()
      expect(authorsChoice).toHaveTextContent('Recording…')
      await act(async () => { rejectAuthors?.(new ApiError(503, 'decision_failed')) })
      await settle(() => expect(screen.getByRole('button', { name: 'Try the same decision again' })).toBeVisible())
      fireEvent.click(screen.getByRole('button', { name: /Title.*Decision recorded/u }))
      await settle(() => expect(screen.getByText(/The recorded value remains Old title/u)).toBeVisible())
    } finally {
      vi.useRealTimers()
    }
  })

  it('preserves final completion when navigation ends the confirmation dwell', async () => {
    const user = userEvent.setup()
    const titleField = detail.fields[0]
    if (!titleField) throw new Error('workspace fixture requires a title field')
    const recordedAuthors = { ...titleField, destination: 'authors', needsAttention: false, proposalId: 'proposal-authors', targetFingerprint: 'fingerprint-authors', humanDecision: { actor: 'owner', decision_id: 'decision-authors', destination: 'authors', occurred_at: 1, outcome: 'keep_current' as const, proposal_id: 'proposal-authors', review_case_id: 'case-1', selected_value: null, target_fingerprint: 'fingerprint-authors' } }
    const settled: CaseDetailView = { ...detail, fields: [{ ...titleField, needsAttention: false, humanDecision: { actor: 'owner', decision_id: 'decision-title', destination: 'title', occurred_at: 1, outcome: 'keep_current', proposal_id: titleField.proposalId, review_case_id: 'case-1', selected_value: null, target_fingerprint: titleField.targetFingerprint } }, recordedAuthors] }
    let current: CaseDetailView = { ...detail, fields: [titleField, recordedAuthors] }
    const http: ReviewHttp = { ...createHttp(), getCaseDetail: vi.fn(async () => current), recordDecision: vi.fn(async () => { current = settled; return { schema_version: 'review-decision-v1', actor: 'owner', decision_id: 'decision-title', destination: 'title', occurred_at: 1, outcome: 'keep_current', proposal_id: titleField.proposalId, review_case_id: 'case-1', selected_value: null, target_fingerprint: titleField.targetFingerprint } }) }
    renderRoute('/library?lens=all&preview=case-1&field=title', http)

    await user.click(await screen.findByRole('button', { name: 'Choose current value: Old title' }))
    expect(await screen.findByText('The authoritative case confirms this exact field outcome.')).toBeVisible()
    await user.click(screen.getByRole('button', { name: /Authors.*Decision recorded/u }))

    expect(await screen.findByText('Review complete')).toBeVisible()
    expect(screen.getByRole('button', { name: /Title.*Old title/u })).toBeVisible()
  })

  it('keeps visible route focus while an uncached selected detail loads', async () => {
    const user = userEvent.setup()
    let release: ((value: CaseDetailView) => void) | undefined
    const pendingDetail = new Promise<CaseDetailView>((resolve) => { release = resolve })
    renderRoute('/library?lens=all', { ...createHttp(), getCaseDetail: vi.fn(async () => pendingDetail) })

    await user.click(await screen.findByRole('button', { name: 'A Book' }))
    expect(await screen.findByText('Loading selected work…')).toBeVisible()
    const loadingRegion = screen.getByText('Loading selected work…').closest('section')
    expect(loadingRegion).toHaveClass('workspace-detail')
    expect(loadingRegion).toHaveAttribute('data-route-focus', 'true')
    await waitFor(() => expect(loadingRegion).toHaveFocus())
    await act(async () => { release?.(detail) })
    expect(await screen.findByRole('button', { name: 'Choose current value: Old title' })).toBeVisible()
  })

  it('suppresses workspace decisions for a superseded case', async () => {
    const http: ReviewHttp = { ...createHttp(), getCaseDetail: vi.fn(async () => ({ ...detail, current: false })) }
    renderRoute('/library?lens=all&preview=case-1', http)

    expect(await screen.findByText('This review case is no longer current. Reopen its current replacement from Library.')).toHaveAttribute('role', 'alert')
    expect(screen.queryByRole('button', { name: /Current value/ })).toBeNull()
    expect(screen.queryByRole('button', { name: /Specific proposed value/ })).toBeNull()
  })

  it('preserves the selected field in full review and clears it when another work is selected', async () => {
    const user = userEvent.setup()
    const titleField = detail.fields[0]
    const firstWork = library.works[0]
    if (!titleField || !firstWork) throw new Error('workspace fixture requires a field and work')
    const authorsField = { ...titleField, destination: 'authors', proposalId: 'proposal-authors', targetFingerprint: 'fingerprint-authors' }
    const secondWork = { ...firstWork, id: 'work-2', reviewCaseId: 'case-2', title: 'Second work' }
    const http: ReviewHttp = {
      ...createHttp(),
      getLibrary: vi.fn(async () => ({ ...library, counts: { ...library.counts, all: 2 }, works: [firstWork, secondWork] })),
      getCaseDetail: vi.fn(async (id: string) => ({ ...detail, id, title: id === 'case-2' ? 'Second work' : detail.title, fields: [titleField, authorsField] })),
    }
    const { router } = renderRoute('/library?lens=all&preview=case-1&field=authors', http)

    expect(await screen.findByRole('link', { name: 'Open full review' })).toHaveAttribute('href', '/review/case-1?subview=review&field=authors')
    await user.click(screen.getByRole('button', { name: 'Second work' }))
    await waitFor(() => expect(router.state.location.search).toEqual({ lens: 'all', preview: 'case-2' }))
  })

  it('recovers and paginates the contextual attention queue', async () => {
    const user = userEvent.setup()
    let calls = 0
    const item = { id: 'case-1', destination: 'title', title: 'A Book', authors: [], library: 'Books', mediaKind: 'ebook', classificationReason: 'Canonical library attention reason', notices: ['internal-library-notice'], provenance: [], workAttentionCount: 1 }
    const http: ReviewHttp = {
      ...createHttp(),
      getAttention: vi.fn(async (search: AttentionSearch) => {
        calls += 1
        if (calls === 1) throw new Error('temporary attention failure')
        return search.cursor ? { total: 2, items: [{ ...item, id: 'case-2', title: 'Second queued work' }] } : { total: 2, nextCursor: 'next', items: [item] }
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
    const selectedLibrary: LibraryView = { ...library, works: [{ ...firstWork, id: 'work-identity-is-not-case-identity' }] }
    const settled: CaseDetailView = { ...detail, fields: [{ ...titleField, needsAttention: false, humanDecision: { actor: 'owner', decision_id: 'decision-identity', destination: 'title', occurred_at: 1, outcome: 'keep_current', proposal_id: titleField.proposalId, review_case_id: 'case-1', selected_value: null, target_fingerprint: titleField.targetFingerprint } }] }
    let current = detail
    const http: ReviewHttp = {
      ...createHttp(),
      getLibrary: vi.fn(async () => selectedLibrary),
      getAttention: vi.fn(async () => ({ total: 2, items: [
        { id: 'case-1', destination: 'title', title: 'A Book', authors: [], library: 'Books', mediaKind: 'ebook', notices: [], provenance: ['Local catalogue'], workAttentionCount: 1 },
        { id: 'case-2', destination: 'authors', title: 'Off-page successor', authors: ['Ada Example'], library: 'Project Gutenberg', mediaKind: 'audiobook', notices: [], provenance: ['Project Gutenberg record'], workAttentionCount: 1 },
      ] })),
      getCaseDetail: vi.fn(async (id: string) => id === 'case-2' ? { ...detail, id, title: 'Off-page successor' } : current),
      recordDecision: vi.fn(async () => { current = settled; return { schema_version: 'review-decision-v1', actor: 'owner', decision_id: 'decision-identity', destination: 'title', occurred_at: 1, outcome: 'keep_current', proposal_id: titleField.proposalId, review_case_id: 'case-1', selected_value: null, target_fingerprint: titleField.targetFingerprint } }),
    }
    const { router } = renderRoute('/library?lens=all', http)

    expect(screen.queryByRole('link', { name: 'Attention' })).toBeNull()
    await user.click(await screen.findByRole('button', { name: 'A Book' }))
    const currentValue = (await screen.findByText('Old title')).closest('button')
    if (!currentValue) throw new Error('workspace fixture requires a current-value decision control')
    await user.click(currentValue)

    expect(await screen.findByText('Review complete')).toBeVisible()
    await user.click(screen.getByRole('button', { name: 'Review next work →' }))
    await waitFor(() => expect(router.state.location.search).toEqual({ lens: 'all', preview: 'case-2', field: 'authors' }))
    expect(await screen.findByRole('heading', { name: 'Off-page successor' })).toBeVisible()
    expect(http.getCaseDetail).toHaveBeenCalledWith('case-1', expect.any(AbortSignal))
    expect(http.getCaseDetail).toHaveBeenCalledWith('case-2', expect.any(AbortSignal))
  })

  it('retains completion identity and a return path after the completed work leaves its lens', async () => {
    const user = userEvent.setup()
    const titleField = detail.fields[0]
    const firstWork = library.works[0]
    if (!titleField || !firstWork) throw new Error('completion fixture requires a work and title field')
    const settled: CaseDetailView = { ...detail, fields: [{ ...titleField, needsAttention: false, humanDecision: { actor: 'owner', decision_id: 'decision-complete', destination: 'title', occurred_at: 1, outcome: 'keep_current', proposal_id: titleField.proposalId, review_case_id: 'case-1', selected_value: null, target_fingerprint: titleField.targetFingerprint } }] }
    const successor = { id: 'case-2', destination: 'authors', title: 'Next work', authors: [], library: 'Books', mediaKind: 'ebook', notices: [], provenance: [], workAttentionCount: 1 }
    let current = detail
    let libraryReads = 0
    let attentionReads = 0
    const http: ReviewHttp = {
      ...createHttp(),
      getLibrary: vi.fn(async () => ++libraryReads === 1 ? { ...library, lens: 'needs_attention' as const } : { ...library, lens: 'needs_attention' as const, counts: { attention: 1, all: 1, clear: 0 }, works: [] }),
      getAttention: vi.fn(async () => ++attentionReads === 1 ? { total: 2, items: [{ ...successor, id: 'case-1', destination: 'title', title: firstWork.title }, successor] } : { total: 1, items: [successor] }),
      getCaseDetail: vi.fn(async () => current),
      recordDecision: vi.fn(async () => { current = settled; return { schema_version: 'review-decision-v1', actor: 'owner', decision_id: 'decision-complete', destination: 'title', occurred_at: 1, outcome: 'keep_current', proposal_id: titleField.proposalId, review_case_id: 'case-1', selected_value: null, target_fingerprint: titleField.targetFingerprint } }),
    }
    renderRoute('/library?lens=needs_attention&preview=case-1&field=title', http)

    await user.click(await screen.findByRole('button', { name: 'Choose current value: Old title' }))
    const complete = (await screen.findByText('Review complete')).closest('article')
    if (!complete) throw new Error('completion boundary is required')
    expect(within(complete).getByRole('heading', { name: 'A Book' })).toBeVisible()
    expect(screen.getByRole('button', { name: 'Review next work →' })).toBeVisible()

    await user.click(screen.getByRole('button', { name: /Title.*Old title/u }))
    expect(await screen.findByRole('button', { name: '← Back to completion' })).toBeVisible()
    expect(screen.getByText(/The recorded value remains Old title/u)).toBeVisible()
    await user.click(screen.getByRole('button', { name: '← Back to completion' }))
    expect(await screen.findByText('Review complete')).toBeVisible()
  })

  it('uses non-animated rail movement when reduced motion is requested', async () => {
    const scrollIntoView = vi.mocked(Element.prototype.scrollIntoView)
    scrollIntoView.mockClear()
    const original = window.matchMedia
    window.matchMedia = vi.fn((query: string) => ({ matches: query === '(prefers-reduced-motion: reduce)', media: query, onchange: null, addEventListener: vi.fn(), removeEventListener: vi.fn(), addListener: vi.fn(), removeListener: vi.fn(), dispatchEvent: vi.fn() }))
    try {
      renderRoute('/library?lens=all&preview=case-1&field=title')
      await screen.findByRole('button', { name: 'Choose current value: Old title' })
      expect(scrollIntoView).toHaveBeenCalledWith(expect.objectContaining({ behavior: 'auto' }))
    } finally {
      window.matchMedia = original
    }
  })

  it('loads later attention pages until completion finds a deliberate next-work choice', async () => {
    const user = userEvent.setup()
    const titleField = detail.fields[0]
    if (!titleField) throw new Error('detail fixture requires a title field')
    const settled: CaseDetailView = { ...detail, fields: [{ ...titleField, needsAttention: false, humanDecision: { actor: 'owner', decision_id: 'decision-page', destination: 'title', occurred_at: 1, outcome: 'keep_current', proposal_id: titleField.proposalId, review_case_id: 'case-1', selected_value: null, target_fingerprint: titleField.targetFingerprint } }] }
    let current = detail
    const getAttention = vi.fn<ReviewHttp['getAttention']>(async (search) => search.cursor
      ? { total: 2, items: [{ id: 'case-2', destination: 'authors', title: 'Later page work', authors: [], library: 'Books', mediaKind: 'ebook', notices: [], provenance: [], workAttentionCount: 1 }] }
      : { total: 2, nextCursor: 'later', items: [{ id: 'case-1', destination: 'title', title: 'A Book', authors: [], library: 'Books', mediaKind: 'ebook', notices: [], provenance: [], workAttentionCount: 1 }] })
    const http: ReviewHttp = { ...createHttp(), getAttention, getCaseDetail: vi.fn(async () => current), recordDecision: vi.fn(async () => { current = settled; return { schema_version: 'review-decision-v1', actor: 'owner', decision_id: 'decision-page', destination: 'title', occurred_at: 1, outcome: 'keep_current', proposal_id: titleField.proposalId, review_case_id: 'case-1', selected_value: null, target_fingerprint: titleField.targetFingerprint } }) }
    renderRoute('/library?lens=all&preview=case-1&field=title', http)

    await user.click(await screen.findByRole('button', { name: 'Choose current value: Old title' }))
    expect(await screen.findByText('Review complete')).toBeVisible()
    expect(await screen.findByRole('button', { name: 'Review next work →' })).toBeVisible()
    expect(getAttention).toHaveBeenCalledWith({ cursor: 'later' }, expect.any(AbortSignal))
  })

  it('clears preview and cursor when the Library search query changes', async () => {
    const user = userEvent.setup()
    const { router } = renderRoute('/library?lens=all&q=first&cursor=next&preview=case-1')

    expect(await screen.findByText('Progressive detail')).toBeVisible()
    await user.clear(screen.getByLabelText('Search title, author, library, or media'))
    await user.type(screen.getByLabelText('Search title, author, library, or media'), 'second')
    await user.click(screen.getByRole('button', { name: 'Search' }))

    await waitFor(() => expect(router.state.location.search).toEqual({ lens: 'all', q: 'second' }))
    expect(screen.queryByText('Progressive detail')).toBeNull()
  })

  it('preserves an off-page preview deep link without exposing detail target identity', async () => {
    const emptyLibrary: LibraryView = { lens: 'all', counts: { attention: 0, all: 0, clear: 0 }, works: [] }
    const http: ReviewHttp = {
      ...createHttp(),
      getLibrary: vi.fn(async () => emptyLibrary),
      getCaseDetail: vi.fn(async () => ({ ...detail, title: 'item-1', subtitle: 'internal identity' })),
    }
    renderRoute('/library?lens=all&preview=case-1', http)

    expect(await screen.findByRole('heading', { name: 'Selected work' })).toBeVisible()
    expect(screen.getByText('Readable work identity is not available on this Library page.')).toBeVisible()
    expect(screen.queryByText('item-1')).toBeNull()
    expect(screen.queryByText('internal identity')).toBeNull()
    expect(screen.getByRole('link', { name: 'Open full review' })).toHaveAttribute('href', '/review/case-1?subview=review')
  })

  it('aborts a superseded critical load and renders only the current route result', async () => {
    const signals: AbortSignal[] = []
    let resolveFirst: ((value: LibraryView) => void) | undefined
    const withTitle = (title: string): LibraryView => ({
      ...library,
      works: library.works.map((work, index) => index === 0 ? { ...work, title } : work),
    })
    const current = withTitle('Current result')
    const http: ReviewHttp = {
      ...createHttp(),
      getLibrary: vi.fn((search: LibrarySearch, signal?: AbortSignal): Promise<LibraryView> => {
        if (signal) signals.push(signal)
        if (search.lens === 'no_attention_needed') return Promise.resolve(current)
        return new Promise<LibraryView>((resolve) => { resolveFirst = resolve })
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

    expect(await screen.findByRole('heading', { name: 'Current result' })).toBeVisible()
    expect(screen.queryByRole('heading', { name: 'Stale result' })).toBeNull()
    expect(signals[0]?.aborted).toBe(true)
  })
})
