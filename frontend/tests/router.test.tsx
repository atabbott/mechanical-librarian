import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { RouterProvider, createMemoryHistory } from '@tanstack/react-router'
import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { AmbiguousMutationError, ApiError } from '../src/api/http'
import type { ActivityDisplayView, ActivityView, AttentionSearch, AttentionView, CaseDetailView, HistoryView, LibrarySearch, LibraryView, PolicyStateView, PreparationView, ReviewHttp, ShadowPostureView, ShadowSampleView } from '../src/api/http'
import { createApplicationRouter, detailQuery, normalizeLibrarySearch, normalizePolicySearch } from '../src/router'
import { activityEventKeys, activityOutcome } from '../src/activityState'

const library: LibraryView = {
  lens: 'all',
  counts: { attention: 1, all: 1, clear: 0 },
  works: [{
    id: 'case-1',
    reviewCaseId: 'case-1',
    title: 'A Book',
    authors: [],
    library: 'Books',
    mediaKind: 'ebook',
    attentionCount: 1,
    attention: 'needs_attention',
  }],
}

const detail: CaseDetailView = {
  id: 'case-1',
  current: true,
  title: 'A Book',
  authors: [],
  subtitle: 'Books · local',
  fields: [{
    destination: 'title',
    disposition: 'review',
    effectCapability: 'manual',
    needsAttention: true,
    manualHold: false,
    currentValue: 'Old title',
    currentRaw: 'Old title',
    proposedValues: [{ kind: 'text', value: 'New title' }],
    proposalId: 'proposal-1',
    targetFingerprint: 'fingerprint-1',
    humanDecision: null,
    automaticDecision: null,
    notices: [],
    provenance: [],
  }],
}

const activity = (value: Partial<ActivityDisplayView> = {}): ActivityView => ({
  schema_version: 'continuous-field-activity-v1', backup_ready: true, paused: false,
  configured_libraries: [], consented_libraries: [], enabled_libraries: [], capabilities: [],
  events: [], counts: {}, example: null, last_failure: null,
  prepared: { automatic: 0, human: 0, total: 0 }, suspensions: [], ...value,
})

const createHttp = (): ReviewHttp => ({
  getLibrary: vi.fn(async () => library),
  getAttention: vi.fn(async () => ({ total: 0, items: [] })),
  getCaseDetail: vi.fn(async () => detail),
  getEvidence: vi.fn(async () => ({ schema_version: 'review-case-evidence-v2', review_case_id: 'case-1', items: [], next_cursor: null })),
  getHistory: vi.fn(async () => ({ schema_version: 'review-case-history-v1', review_case_id: 'case-1', items: [], next_cursor: null })),
  retryCoverConsideration: vi.fn(async () => { throw new Error('not configured') }),
  getPreparation: vi.fn(async (): Promise<PreparationView> => ({ schema_version: 'review-preparation-v1', active: false, actionable: false, paused: false, libraries: [] })),
  preparationAction: vi.fn(async (): Promise<PreparationView> => ({ schema_version: 'review-preparation-v1', active: false, actionable: false, paused: false, libraries: [] })),
  recordDecision: vi.fn(async () => ({ schema_version: 'review-decision-v1', actor: 'owner', decision_id: 'decision-1', destination: 'title', occurred_at: 1, outcome: 'keep_current', proposal_id: 'proposal-1', review_case_id: 'case-1', selected_value: null, target_fingerprint: 'fingerprint-1' })),
  returnToAutomatic: vi.fn(async () => undefined),
  getPolicyState: vi.fn(async (): Promise<PolicyStateView> => ({ schema_version: 'metadata-policy-notebook-v1', policy_id: 'policy-1', profile: { schema_version: 'metadata-policy-profile-v2', name: 'Default', options: {}, destinations: {} }, rich_qualification: [] })),
  getShadowPosture: vi.fn(async (): Promise<ShadowPostureView> => ({ schema_version: 'shadow-posture-v1', posture: 'review', authority: 'none', can_leave_preview: false })),
  getAutomaticSafety: vi.fn(async () => ({ paused: false, global_suspension: null, category_suspensions: {} })),
  getShadowSample: vi.fn(async (): Promise<ShadowSampleView> => ({ schema_version: 'shadow-outcome-sample-v1', authority: 'none', items: [], next_cursor: null })),
  getPolicyTransition: vi.fn(async () => null),
  policyCommand: async <T,>() => ({} as T),
  getActivity: vi.fn(async () => activity()),
  activityCommand: vi.fn(async () => undefined),
  logout: vi.fn(async () => undefined),
})

const renderRoute = (entry: string, http = createHttp()) => {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const router = createApplicationRouter(http, queryClient, createMemoryHistory({ initialEntries: [entry] }))
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  )
  return { http, queryClient, router }
}

describe('typed review routes', () => {
  it('fails closed while classifying every authoritative Activity command outcome', () => {
    expect(activityOutcome({}, { path: 'field-effects/pause' })).toBe('unknown')
    expect(activityOutcome({ paused: true }, { path: 'field-effects/pause' })).toBe('applied')
    expect(activityOutcome({ paused: false }, { path: 'field-effects/pause' })).toBe('unknown')
    expect(activityOutcome({ paused: false }, { path: 'field-effects/resume' })).toBe('applied')
    expect(activityOutcome({ paused: true }, { path: 'field-effects/resume' })).toBe('unknown')
    expect(activityOutcome({ paused: 'false' }, { path: 'field-effects/resume' })).toBe('unknown')

    const revoke = { path: 'activity/primary/fiction/revoke' }
    expect(activityOutcome({ enabled_libraries: [] }, revoke)).toBe('applied')
    expect(activityOutcome({ enabled_libraries: [{ connection: 'primary', library: 'fiction' }] }, revoke)).toBe('unknown')
    expect(activityOutcome({ enabled_libraries: [{}] }, revoke)).toBe('unknown')

    const consent = { path: 'activity/consent', body: { grants: [{ library: 'fiction', destinations: ['title'] }] } }
    expect(activityOutcome({ capabilities: [{ library: 'fiction', destinations: [{ destination: 'title', consented: true }] }] }, consent)).toBe('applied')
    expect(activityOutcome({ capabilities: [{ library: 'fiction', destinations: [{ destination: 'title', consented: false }] }] }, consent)).toBe('unknown')
    expect(activityOutcome({ capabilities: [{ library: 'fiction', destinations: [{ destination: 'title' }] }] }, consent)).toBe('unknown')
    expect(activityOutcome({ capabilities: [{ library: 'fiction', destinations: [{ destination: 'title', consented: true }, { destination: 'title', consented: true }] }] }, consent)).toBe('unknown')
    expect(activityOutcome({ capabilities: [{ library: 'fiction', destinations: [{ destination: 'title', consented: true }] }, { library: 'fiction', destinations: [{ destination: 'title', consented: false }] }] }, consent)).toBe('unknown')
    expect(activityOutcome({ capabilities: [{}] }, consent)).toBe('unknown')
  })

  it('keeps numeric ledger identities stable across newest-first capped polling', () => {
    const existing = Array.from({ length: 50 }, (_, index) => ({
      work_id: `work-${String(index)}`,
      destination: index % 2 === 0 ? 'title' : 'authors',
      state: 'verified',
      occurred_at: 1_000 - index,
      library: 'fiction',
      origin: 'human',
      failure: null,
    }))
    const next = [{ ...existing[0], work_id: 'new-work', occurred_at: 1_001 }, ...existing.slice(0, 49)]
    expect(activityEventKeys(next).slice(1)).toEqual(activityEventKeys(existing).slice(0, 49))

    const duplicate = { work_id: 'same', destination: 'title', state: 'verified', occurred_at: 1_000, library: 'fiction', origin: 'human', failure: null }
    const duplicateWindow = Array.from({ length: 50 }, () => duplicate)
    expect(new Set(activityEventKeys([duplicate, ...duplicateWindow.slice(0, 49)]))).toEqual(new Set(activityEventKeys(duplicateWindow)))
  })
  it('bounds and normalizes URL-owned library search state', () => {
    expect(normalizeLibrarySearch({ lens: 'all', q: 'title', cursor: 'next', preview: 'case-1' })).toEqual({
      lens: 'all', q: 'title', cursor: 'next', preview: 'case-1',
    })
    expect(normalizeLibrarySearch({ lens: 'hostile', q: 'x'.repeat(129) })).toEqual({ lens: 'all' })
  })

  it('normalizes Policy deep links to the four protected semantic chapters', () => {
    expect(normalizePolicySearch({ section: 'publication' })).toEqual({ section: 'publication' })
    expect(normalizePolicySearch({ section: 'curation' })).toEqual({ section: 'curation' })
    expect(normalizePolicySearch({ section: 'unsupported' })).toEqual({ section: 'unsupported' })
    expect(normalizePolicySearch({ section: 'effects' })).toEqual({ section: 'identity' })
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
      getCaseDetail: vi.fn(async () => ({ ...detail, fields: [{ ...titleField, effectCapability: 'allowed', provenance: ['Local catalogue: Old title'] }, automaticField] })),
      getEvidence: vi.fn(async () => ({
        schema_version: 'review-case-evidence-v2', review_case_id: 'case-1', next_cursor: null,
        items: [{ reference: 'evidence-1', kind: 'title', source: 'provider_claim', provider: 'Open Library', candidate: null, fact: 'New title' }],
      })),
    }
    renderRoute('/library?lens=all&preview=case-1&field=title', http)

    expect(await screen.findByRole('button', { name: 'Choose current value: Old title' })).toBeVisible()
    expect(screen.getByText('Eligible for guarded planning')).toBeVisible()
    await user.click(screen.getByText('Source ledger'))
    expect(await screen.findByText('Open Library')).toBeVisible()
    expect(screen.getByText('New title', { selector: '[role="cell"]' })).toBeVisible()
    expect(screen.getByText('Provider claim · Title')).toBeVisible()
    expect(screen.getAllByText(/2 named sources/u).length).toBeGreaterThan(0)
    await user.click(screen.getByText('1 automatic or recorded field'))
    expect(screen.getByText('Resolved automatically · Atlas Cycle 2.5')).toBeVisible()
    expect(screen.getByText('Series already agrees with the deterministic policy.')).toBeVisible()
  })

  it('shows bound incumbent and manual alternatives and retries only failed advice', async () => {
    const user = userEvent.setup()
    const base = detail.fields[0]
    if (!base) throw new Error('cover fixture requires a field')
    const consideration = {
      id: `cover_consideration_${'c'.repeat(64)}`,
      current: true,
      candidates: [
        { artifactId: `cover_${'a'.repeat(64)}`, height: 1200, identity: { outcome: 'verified_manifestation_match' }, incumbent: true, observation: null, originalSha256: 'a'.repeat(64), ordinal: 0, previewSha256: '1'.repeat(64), reference: `cover_transport_ref_${'1'.repeat(64)}`, selectable: false, sourceKinds: ['audiobookshelf_current'], state: 'incumbent', width: 800 },
        { artifactId: `cover_${'b'.repeat(64)}`, height: 1200, identity: { outcome: 'plausible_match' }, incumbent: false, observation: null, originalSha256: 'b'.repeat(64), ordinal: 1, previewSha256: '2'.repeat(64), reference: `cover_transport_ref_${'2'.repeat(64)}`, selectable: true, sourceKinds: ['provider'], state: 'acceptable', width: 800 },
      ],
      incumbent: { reference: `cover_transport_ref_${'1'.repeat(64)}`, state: 'available' as const },
      omissions: [], policy: null,
      policyPreferences: { avoidPromotionalText: false, avoidTieInBranding: false, preferMinimalComposition: false },
      policyReference: `cover_policy_${'3'.repeat(64)}`,
      reviewerStatus: 'failed', targetFingerprint: 'fingerprint-cover',
    } as const
    const coverField = { ...base, destination: 'cover', currentValue: `cover_${'a'.repeat(64)}`, currentRaw: `cover_${'a'.repeat(64)}`, proposalId: 'proposal-cover', targetFingerprint: 'fingerprint-cover', proposedValues: [{ kind: 'cover', value: `cover_${'b'.repeat(64)}` }], coverConsideration: consideration }
    const retryCoverConsideration = vi.fn(async () => consideration)
    const recordDecision = vi.fn(async () => ({ schema_version: 'review-decision-v1', actor: 'owner', decision_id: 'decision-cover', destination: 'cover', occurred_at: 1, outcome: 'accept' as const, proposal_id: 'proposal-cover', review_case_id: 'case-1', selected_value: { kind: 'cover', value: `cover_${'b'.repeat(64)}` }, target_fingerprint: 'fingerprint-cover' }))
    const getHistory = vi.fn(async () => ({ schema_version: 'review-case-history-v1' as const, review_case_id: 'case-1', items: [], next_cursor: null }))
    const http: ReviewHttp = { ...createHttp(), getCaseDetail: vi.fn(async () => ({ ...detail, fields: [coverField] })), getHistory, retryCoverConsideration, recordDecision }
    renderRoute('/review/case-1?field=cover&subview=review', http)

    expect(await screen.findByRole('heading', { name: 'Incumbent' })).toBeVisible()
    await waitFor(() => expect(getHistory).toHaveBeenCalledOnce())
    expect(screen.getByRole('heading', { name: 'Acceptable' })).toBeVisible()
    expect(screen.getByText(/Observation or recommendation is unavailable/u)).toBeVisible()
    await user.click(screen.getByRole('button', { name: 'Retry advisory review' }))
    await waitFor(() => expect(retryCoverConsideration).toHaveBeenCalledWith('case-1'))
    await waitFor(() => expect(getHistory).toHaveBeenCalledTimes(2))
    await user.click(screen.getByRole('button', { name: 'Choose this cover' }))
    await waitFor(() => expect(recordDecision).toHaveBeenCalledWith('case-1', expect.objectContaining({ considerationId: consideration.id, outcome: 'accept', selectedValue: { kind: 'cover', value: `cover_${'b'.repeat(64)}` } })))
    await waitFor(() => expect(getHistory).toHaveBeenCalledTimes(3))
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
    expect(within(comparison).getByText('One candidate is recommended as advice only.')).toBeVisible()
    expect(within(comparison).getByText(/Recommended/u)).toBeVisible()
    await user.click(within(comparison).getByText('Reasons retained'))
    expect(within(comparison).getByText('Preview unavailable')).toBeVisible()
    expect(within(comparison).getByText('Observations ambiguous')).toBeVisible()
    expect(within(comparison).getByText('Promotional text excluded by policy')).toBeVisible()
    expect(comparison.querySelector('.library-cover-pinned')).not.toBeNull()
    expect(comparison.querySelectorAll('.library-cover-gallery .library-cover-card')).toHaveLength(3)
  })

  it('retains an explicit keep-current control when the incumbent preview is unavailable', async () => {
    const base = detail.fields[0]
    if (!base) throw new Error('cover fixture requires a field')
    const consideration = {
      id: `cover_consideration_${'u'.repeat(64)}`,
      current: true,
      candidates: [{ artifactId: `cover_${'a'.repeat(64)}`, height: 1200, identity: { outcome: 'plausible_match' }, incumbent: false, observation: null, originalSha256: 'a'.repeat(64), ordinal: 1, previewSha256: 'a'.repeat(64), reference: `cover_transport_ref_${'a'.repeat(64)}`, selectable: true, sourceKinds: ['provider'], state: 'acceptable', width: 800 }],
      incumbent: { reference: `cover_transport_ref_${'b'.repeat(64)}`, state: 'unavailable' as const }, omissions: [], policy: null,
      policyPreferences: { avoidPromotionalText: false, avoidTieInBranding: false, preferMinimalComposition: false }, policyReference: `cover_policy_${'u'.repeat(64)}`, reviewerStatus: 'completed', targetFingerprint: 'fingerprint-cover',
    } as const
    const coverField = { ...base, destination: 'cover', proposedValues: [{ kind: 'cover', value: `cover_${'a'.repeat(64)}` }], coverConsideration: consideration }
    renderRoute('/library?lens=all&preview=case-1&field=cover', { ...createHttp(), getCaseDetail: vi.fn(async () => ({ ...detail, fields: [coverField] })) })

    expect(await screen.findByRole('button', { name: /Current cover.*Unavailable/u })).toBeVisible()
    expect(screen.getByText('Choices').nextElementSibling).toHaveTextContent('3')
  })

  it('prepares a missing cover comparison inside the Library workflow', async () => {
    const user = userEvent.setup()
    const base = detail.fields[0]
    if (!base) throw new Error('cover fixture requires a field')
    const consideration = {
      id: `cover_consideration_${'p'.repeat(64)}`, current: true,
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

  it('shows immutable historical cover evidence and the exact owner choice', async () => {
    const reference = `cover_transport_ref_${'1'.repeat(64)}`
    const history: HistoryView = {
      schema_version: 'review-case-history-v1', review_case_id: 'case-1', next_cursor: null,
      items: [{
        schema_version: 'cover-consideration-event-v1', kind: 'selected', occurred_at: 2,
        consideration_id: `cover_consideration_${'c'.repeat(64)}`, review_case_id: 'case-1', decision_id: 'decision-cover',
        decision: { actor: 'owner', decision_id: 'decision-cover', destination: 'cover', occurred_at: 2, outcome: 'accept', proposal_id: 'proposal-cover', review_case_id: 'case-1', selected_value: { kind: 'cover', value: `cover_${'a'.repeat(64)}` }, target_fingerprint: 'fingerprint-cover' },
        consideration: {
          schema_version: 'cover-consideration-v1', consideration_id: `cover_consideration_${'c'.repeat(64)}`, current: false,
          candidates: [{ artifact_id: `cover_${'a'.repeat(64)}`, height: 1200, identity: { candidate_reference: reference, evidence_references: [], outcome: 'verified_work_match', preview_reference: `cover_preview_${'2'.repeat(64)}`, reasons: [] }, incumbent: false, observation: { candidate_reference: reference, composition: 'minimal', medium: 'illustration', preview_sha256: '3'.repeat(64), preview_variant: 'normalized', promotional_text: 'absent', tie_in_branding: 'absent' }, original_sha256: '4'.repeat(64), ordinal: 0, preview_sha256: '3'.repeat(64), reference, selectable: true, source_kinds: ['epub'], state: 'acceptable', transport_sha256: '5'.repeat(64), width: 800 }],
          incumbent: { reference: null, state: 'absent' }, omissions: [], policy_preferences: { avoid_promotional_text: true, avoid_tie_in_branding: false, prefer_minimal_composition: true, schema_version: 'cover-policy-v1' }, policy_reference: `cover_policy_${'6'.repeat(64)}`, reviewer_status: 'completed', supersedes_id: null, target_fingerprint: 'fingerprint-cover',
          policy: { acceptable_candidate_references: [reference], cover_packet_reference: `cover_packet_${'7'.repeat(64)}`, identity_classifier_reference: `cover_identity_${'8'.repeat(64)}`, identity_report_sha256: '9'.repeat(64), observation_report_sha256: 'a'.repeat(64), observation_taxonomy_reference: `cover_taxonomy_${'b'.repeat(64)}`, policy_reference: `cover_policy_${'6'.repeat(64)}`, reasons: ['minimal_composition_preferred'], recommended_candidate_reference: reference, schema_version: 'cover-policy-report-v1', status: 'recommended' },
        },
      }],
    }
    const http: ReviewHttp = { ...createHttp(), getHistory: vi.fn(async () => history) }
    renderRoute('/review/case-1?field=title&subview=history', http)

    expect(await screen.findByRole('heading', { name: 'Cover selected' })).toBeVisible()
    expect(screen.getByText(/Historical; it grants no current authority/u)).toBeVisible()
    expect(screen.getByText(/Owner outcome: accept/u)).toHaveTextContent(`cover_${'a'.repeat(64)}`)
    expect(screen.getByText(/recommendation:/u)).toHaveTextContent(reference)
    expect(screen.getByRole('list', { name: 'Historical cover candidates' })).toHaveTextContent('composition minimal')
  })

  it('keeps retained case provenance visible when supplemental evidence fails', async () => {
    const user = userEvent.setup()
    const titleField = detail.fields[0]
    if (!titleField) throw new Error('workspace fixture requires a title field')
    const http: ReviewHttp = {
      ...createHttp(),
      getCaseDetail: vi.fn(async () => ({ ...detail, fields: [{ ...titleField, provenance: ['Local catalogue record: retained current value'] }] })),
      getEvidence: vi.fn(async () => { throw new Error('supplemental evidence unavailable') }),
    }
    renderRoute('/library?lens=all&preview=case-1&field=title', http)

    expect(await screen.findByText(/Supplemental evidence could not be loaded/u)).toHaveAttribute('role', 'alert')
    await user.click(screen.getByText('Source ledger'))
    expect(screen.getByText('Local catalogue record')).toBeVisible()
    expect(screen.getByText('retained current value')).toBeVisible()
    expect(screen.getByText(/1 named source below/u)).toBeVisible()
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
    expect(screen.getByRole('button', { name: /Authors.*Needs review/u })).not.toHaveAttribute('aria-current', 'true')

    await act(async () => { release?.() })
    const confirmationText = await screen.findByText('The authoritative case confirms this exact field outcome.')
    const confirmed = confirmationText.closest<HTMLElement>('[role="status"]')
    if (!confirmed) throw new Error('authoritative confirmation region is required')
    expect(confirmed).toHaveTextContent('The authoritative case confirms this exact field outcome.')
    expect(confirmed).toHaveFocus()
    await waitFor(() => expect(screen.getByRole('button', { name: /Authors.*Needs review/u })).toHaveAttribute('aria-current', 'true'))
  })

  it('clears authoritative confirmation when the reviewer navigates during its dwell', async () => {
    const user = userEvent.setup()
    const titleField = detail.fields[0]
    if (!titleField) throw new Error('workspace fixture requires a title field')
    const authorsField = { ...titleField, destination: 'authors', proposalId: 'proposal-authors', targetFingerprint: 'fingerprint-authors' }
    const settled: CaseDetailView = { ...detail, fields: [{ ...titleField, needsAttention: false, humanDecision: { actor: 'owner', decision_id: 'decision-title', destination: 'title', occurred_at: 1, outcome: 'keep_current', proposal_id: titleField.proposalId, review_case_id: 'case-1', selected_value: null, target_fingerprint: titleField.targetFingerprint } }, authorsField] }
    let current: CaseDetailView = { ...detail, fields: [titleField, authorsField] }
    let rejectAuthors: ((error: unknown) => void) | undefined
    const authorsDecision = new Promise<never>((_resolve, reject) => { rejectAuthors = reject })
    const recordDecision = vi.fn<ReviewHttp['recordDecision']>(async (_id, command) => { if (command.destination === 'authors') return authorsDecision; current = settled; return { schema_version: 'review-decision-v1', actor: 'owner', decision_id: 'decision-title', destination: 'title', occurred_at: 1, outcome: 'keep_current', proposal_id: titleField.proposalId, review_case_id: 'case-1', selected_value: null, target_fingerprint: titleField.targetFingerprint } })
    const http: ReviewHttp = { ...createHttp(), getCaseDetail: vi.fn(async () => current), recordDecision }
    renderRoute('/library?lens=all&preview=case-1&field=title', http)

    await user.click(await screen.findByRole('button', { name: 'Choose current value: Old title' }))
    expect(await screen.findByText('The authoritative case confirms this exact field outcome.')).toBeVisible()
    await user.click(screen.getByRole('button', { name: /Authors.*Needs review/u }))
    const authorsChoice = screen.getByRole('button', { name: 'Choose current value: Old title' })
    await user.click(authorsChoice)
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 360)) })

    expect(screen.queryByText('The authoritative case confirms this exact field outcome.')).toBeNull()
    expect(authorsChoice).toHaveTextContent('Recording…')
    await act(async () => { rejectAuthors?.(new ApiError(503, 'decision_failed')) })
    expect(await screen.findByRole('button', { name: 'Try the same decision again' })).toBeVisible()
    await user.click(screen.getByRole('button', { name: /Title.*Decision recorded/u }))
    expect(screen.getByText(/The recorded value remains Old title/u)).toBeVisible()
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
    const item = { id: 'case-1', destination: 'title', title: 'A Book', authors: [], library: 'Books', mediaKind: 'ebook', notices: [], provenance: [], workAttentionCount: 1 }
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

  it('settles a deferred Detail decision without reviving recording status after Evidence and Library traversal', async () => {
    const user = userEvent.setup()
    const field = detail.fields[0]
    if (!field) throw new Error('detail fixture requires a field')
    let resolveDecision: (() => void) | undefined
    const deferred = new Promise<void>((resolve) => { resolveDecision = resolve })
    const settled: CaseDetailView = { ...detail, fields: [{ ...field, needsAttention: false, humanDecision: { actor: 'owner', decision_id: 'settled', destination: 'title', occurred_at: 1, outcome: 'keep_current', proposal_id: field.proposalId, review_case_id: 'case-1', selected_value: null, target_fingerprint: field.targetFingerprint } }] }
    let current = detail
    const http: ReviewHttp = { ...createHttp(), getCaseDetail: vi.fn(async () => current), recordDecision: vi.fn(async () => { await deferred; current = settled; return { schema_version: 'review-decision-v1', actor: 'owner', decision_id: 'settled', destination: 'title', occurred_at: 1, outcome: 'keep_current', proposal_id: field.proposalId, review_case_id: 'case-1', selected_value: null, target_fingerprint: field.targetFingerprint } }) }
    const { router } = renderRoute('/review/case-1?field=title&subview=review', http)

    await user.click(await screen.findByRole('button', { name: 'Choose current value: Old title' }))
    await user.click(screen.getByRole('button', { name: 'Evidence' }))
    await act(async () => { resolveDecision?.() })
    await waitFor(() => expect(router.state.location.search).toEqual({ field: 'title', subview: 'evidence' }))
    expect(await screen.findByText('Decision recorded from the authoritative case.')).toBeVisible()
    expect(screen.queryByText(/Recording keep current/u)).toBeNull()

    await user.click(screen.getByRole('link', { name: 'Library' }))
    await user.click(await screen.findByRole('button', { name: 'A Book' }))
    await user.click(await screen.findByRole('link', { name: 'Open full review' }))
    expect(await screen.findByText('Decision recorded from the authoritative case.')).toBeVisible()
    expect(screen.queryByText(/Recording keep current/u)).toBeNull()
  })

  it('settles durable decision status after leaving the submitting route', async () => {
    const user = userEvent.setup()
    const field = detail.fields[0]
    if (!field) throw new Error('detail fixture requires a field')
    let resolveDecision: (() => void) | undefined
    const deferred = new Promise<void>((resolve) => { resolveDecision = resolve })
    const settled = { ...detail, fields: [{ ...field, needsAttention: false, humanDecision: { actor: 'owner', decision_id: 'settled-after-leave', destination: 'title', occurred_at: 1, outcome: 'keep_current' as const, proposal_id: field.proposalId, review_case_id: 'case-1', selected_value: null, target_fingerprint: field.targetFingerprint } }] }
    let current = detail
    const http: ReviewHttp = { ...createHttp(), getCaseDetail: vi.fn(async () => current), recordDecision: vi.fn(async () => { await deferred; current = settled; return { schema_version: 'review-decision-v1', actor: 'owner', decision_id: 'settled-after-leave', destination: 'title', occurred_at: 1, outcome: 'keep_current', proposal_id: field.proposalId, review_case_id: 'case-1', selected_value: null, target_fingerprint: field.targetFingerprint } }) }
    renderRoute('/review/case-1?field=title&subview=review', http)

    await user.click(await screen.findByRole('button', { name: 'Choose current value: Old title' }))
    await user.click(screen.getByRole('link', { name: 'Library' }))
    await act(async () => { resolveDecision?.() })
    await user.click(await screen.findByRole('button', { name: 'A Book' }))
    await user.click(await screen.findByRole('link', { name: 'Open full review' }))
    expect(await screen.findByText('Decision recorded from the authoritative case.')).toBeVisible()
    expect(screen.queryByText(/Recording keep current/u)).toBeNull()
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

  it('groups human-beneficial fields by work and links each exact destination', async () => {
    const attention: AttentionView = {
      total: 2,
      items: [
        { id: 'case-1', destination: 'title', title: 'A Book', authors: ['Ada Example'], library: 'Books', mediaKind: 'ebook', notices: ['conflicting_evidence'], provenance: ['opaque-source-1'], workAttentionCount: 3 },
        { id: 'case-1', destination: 'authors', title: 'A Book', authors: ['Ada Example'], library: 'Books', mediaKind: 'ebook', notices: [], provenance: [], workAttentionCount: 3 },
      ],
    }
    const http: ReviewHttp = { ...createHttp(), getAttention: vi.fn(async () => attention) }
    renderRoute('/attention', http)

    expect(await screen.findByRole('heading', { name: 'A Book' })).toBeVisible()
    expect(screen.getByText('3 fields')).toBeVisible()
    expect(screen.getByText('Conflicting evidence')).toBeVisible()
    expect(screen.queryByText('opaque-source-1')).toBeNull()
    expect(screen.getByText(/1 evidence record available/u)).toBeVisible()
    expect(screen.getByText(/Inspect the current value and available authors choices/u)).toBeVisible()
    expect(screen.getByRole('link', { name: /Title/u })).toHaveAttribute('href', '/review/case-1?field=title&subview=review')
    expect(screen.getByRole('link', { name: /Authors/u })).toHaveAttribute('href', '/review/case-1?field=authors&subview=review')
  })

  it('supports direct detail entry and focuses the settled main landmark', async () => {
    const { router } = renderRoute('/review/case-1')

    expect(await screen.findByRole('heading', { name: 'Current review case', level: 1 })).toBeVisible()
    await waitFor(() => expect(document.querySelector('#field-title')).toHaveFocus())
    expect(screen.getByText('Review case loaded')).toHaveAttribute('role', 'status')
    await waitFor(() => expect(router.state.location.search).toEqual({ field: 'title', subview: 'review' }))
  })

  it('records the exact selected proposal and refreshes authoritative detail', async () => {
    const user = userEvent.setup()
    const second = { kind: 'text', value: 'Another title' } as const
    const titleField = detail.fields[0]
    if (!titleField) throw new Error('detail fixture requires a title field')
    const staleNextField = { ...titleField, destination: 'subtitle', proposalId: 'proposal-2', targetFingerprint: 'fingerprint-2' }
    const actualNextField = { ...titleField, destination: 'authors', proposalId: 'proposal-3', targetFingerprint: 'fingerprint-3' }
    const initial: CaseDetailView = {
      ...detail,
      fields: [{ ...titleField, proposedValues: [...titleField.proposedValues, second] }, staleNextField, actualNextField],
    }
    const initialField = initial.fields[0]
    if (!initialField) throw new Error('detail fixture requires an initial title field')
    const decided: CaseDetailView = {
      ...initial,
      fields: [{
        ...initialField,
        needsAttention: false,
        humanDecision: {
          actor: 'owner', decision_id: 'decision-1', destination: 'title', occurred_at: 1,
          outcome: 'accept', proposal_id: 'proposal-1', review_case_id: 'case-1',
          selected_value: second, target_fingerprint: 'fingerprint-1',
        },
      }, {
        ...staleNextField,
        needsAttention: false,
        automaticDecision: {
          candidate_id: null, decision_id: 'automatic-decision-2', destination: 'subtitle', occurred_at: 1,
          outcome: 'accept', proposal_id: 'proposal-2', review_case_id: 'case-1',
          selected_value: staleNextField.proposedValues[0] ?? null, target_fingerprint: 'fingerprint-2',
        },
      }, actualNextField],
    }
    let current = initial
    const http: ReviewHttp = {
      ...createHttp(),
      getCaseDetail: vi.fn(async () => current),
      recordDecision: vi.fn(async () => {
        current = decided
        return { schema_version: 'review-decision-v1', actor: 'owner', decision_id: 'decision-1', destination: 'title', occurred_at: 1, outcome: 'accept', proposal_id: 'proposal-1', review_case_id: 'case-1', selected_value: second, target_fingerprint: 'fingerprint-1' }
      }),
    }
    const { router } = renderRoute('/review/case-1?field=title&subview=review', http)

    expect((await screen.findAllByLabelText('Reviewer facts')).at(0)).toHaveTextContent('Available choices3')
    await user.click(await screen.findByRole('button', { name: 'Choose proposed value 2: Another title' }))

    await waitFor(() => expect(http.recordDecision).toHaveBeenCalledWith('case-1', expect.objectContaining({ selectedValue: second })))
    expect(await screen.findByText('Decision recorded. Continuing with authors.')).toBeVisible()
    await waitFor(() => expect(router.state.location.search).toEqual({ field: 'authors', subview: 'review' }))
    expect(await screen.findByRole('heading', { name: 'authors' })).toBeVisible()
    expect(http.getCaseDetail).toHaveBeenCalledTimes(2)
  })

  it('offers only the current-value choice when an unresolved field has no proposal', async () => {
    const user = userEvent.setup()
    const titleField = detail.fields[0]
    if (!titleField) throw new Error('detail fixture requires a title field')
    const currentOnly: CaseDetailView = { ...detail, fields: [{ ...titleField, proposedValues: [] }] }
    const http: ReviewHttp = { ...createHttp(), getCaseDetail: vi.fn(async () => currentOnly) }
    renderRoute('/review/case-1?field=title&subview=review', http)

    expect(await screen.findByRole('button', { name: 'Choose current value: Old title' })).toBeVisible()
    expect(screen.getAllByLabelText('Reviewer facts').at(0)).toHaveTextContent('Available choices1')
    expect(screen.queryByRole('button', { name: /Choose proposed value/ })).toBeNull()
    await user.click(screen.getByRole('button', { name: 'Choose current value: Old title' }))
    await waitFor(() => expect(http.recordDecision).toHaveBeenCalledWith('case-1', expect.objectContaining({
      destination: 'title', outcome: 'keep_current', selectedValue: null,
    })))
  })

  it('halts the acknowledged field when a successful decision cannot refresh current detail', async () => {
    const user = userEvent.setup()
    const titleField = detail.fields[0]
    if (!titleField) throw new Error('detail fixture requires a title field')
    const failedRefresh = { ...detail, fields: [titleField, { ...titleField, destination: 'series', proposalId: 'proposal-2', targetFingerprint: 'fingerprint-2' }] }
    const http: ReviewHttp = {
      ...createHttp(),
      getCaseDetail: vi.fn().mockResolvedValueOnce(failedRefresh).mockRejectedValue(new Error('refresh unavailable')),
    }
    const { router } = renderRoute('/review/case-1?field=title&subview=review', http)

    await user.click(await screen.findByRole('button', { name: 'Choose current value: Old title' }))

    expect(await screen.findByText('Decision recorded, but the current case could not be refreshed. Reload before reviewing another value.')).toBeVisible()
    expect(router.state.location.search).toEqual({ field: 'title', subview: 'review' })
    expect(screen.queryByRole('button', { name: /Choose current value/ })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Try again' })).toBeNull()
    expect(screen.getAllByLabelText('Reviewer facts').at(0)).toHaveTextContent('Available choices0')
    await user.click(screen.getByRole('button', { name: 'series' }))
    expect(screen.queryByRole('button', { name: /Choose current value/ })).toBeNull()
    expect(screen.getAllByLabelText('Reviewer facts').at(1)).toHaveTextContent('Available choices0')
  })

  it('advances in the displayed pre-decision order after refreshed fields are reordered', async () => {
    const user = userEvent.setup()
    const titleField = detail.fields[0]
    if (!titleField) throw new Error('detail fixture requires a title field')
    const authors = { ...titleField, destination: 'authors', proposalId: 'proposal-2', targetFingerprint: 'fingerprint-2' }
    const series = { ...titleField, destination: 'series', proposalId: 'proposal-3', targetFingerprint: 'fingerprint-3' }
    const initial = { ...detail, fields: [titleField, authors, series] }
    const refreshed = { ...initial, fields: [titleField, series, { ...authors, needsAttention: false, humanDecision: { actor: 'owner', decision_id: 'decision-2', destination: 'authors', occurred_at: 1, outcome: 'keep_current' as const, proposal_id: 'proposal-2', review_case_id: 'case-1', selected_value: null, target_fingerprint: 'fingerprint-2' } }] }
    let current = initial
    const http: ReviewHttp = { ...createHttp(), getCaseDetail: vi.fn(async () => current), recordDecision: vi.fn(async () => { current = refreshed; return { schema_version: 'review-decision-v1', actor: 'owner', decision_id: 'decision-2', destination: 'authors', occurred_at: 1, outcome: 'keep_current', proposal_id: 'proposal-2', review_case_id: 'case-1', selected_value: null, target_fingerprint: 'fingerprint-2' } }) }
    const { router } = renderRoute('/review/case-1?field=authors&subview=review', http)

    await user.click(await screen.findByRole('button', { name: 'Choose current value: Old title' }))

    expect(await screen.findByText('Decision recorded. Continuing with series.')).toBeVisible()
    await waitFor(() => expect(router.state.location.search).toEqual({ field: 'series', subview: 'review' }))
  })

  it('preserves masthead navigation made while a decision is settling', async () => {
    const user = userEvent.setup()
    let resolveDecision: (() => void) | undefined
    const completion = new Promise<void>((resolve) => { resolveDecision = resolve })
    const titleField = detail.fields[0]
    if (!titleField) throw new Error('detail fixture requires a title field')
    const resolved = { ...detail, fields: [{ ...titleField, needsAttention: false, humanDecision: { actor: 'owner', decision_id: 'decision-3', destination: 'title', occurred_at: 1, outcome: 'keep_current' as const, proposal_id: 'proposal-1', review_case_id: 'case-1', selected_value: null, target_fingerprint: 'fingerprint-1' } }] }
    let current = detail
    const http: ReviewHttp = { ...createHttp(), getCaseDetail: vi.fn(async () => current), recordDecision: vi.fn(async () => { await completion; current = resolved; return { schema_version: 'review-decision-v1', actor: 'owner', decision_id: 'decision-3', destination: 'title', occurred_at: 1, outcome: 'keep_current', proposal_id: 'proposal-1', review_case_id: 'case-1', selected_value: null, target_fingerprint: 'fingerprint-1' } }) }
    const { router } = renderRoute('/review/case-1?field=title&subview=review', http)

    await user.click(await screen.findByRole('button', { name: 'Choose current value: Old title' }))
    await user.click(screen.getByRole('link', { name: 'Library' }))
    await act(async () => { resolveDecision?.() })

    await waitFor(() => expect(router.state.location.pathname).toBe('/library'))
    expect(router.state.location.pathname).not.toContain('undefined')
  })

  it('replaces opaque provenance references with distinct bounded reviewer facts', async () => {
    const titleField = detail.fields[0]
    if (!titleField) throw new Error('detail fixture requires a title field')
    const opaque = `provider_${'a'.repeat(64)}`
    const longHostile = `<img src=x>${'evidence '.repeat(100)}`
    const http: ReviewHttp = { ...createHttp(), getCaseDetail: vi.fn(async () => ({ ...detail, fields: [{ ...titleField, provenance: [opaque, longHostile] }] })) }
    renderRoute('/review/case-1?field=title&subview=review', http)

    expect(await screen.findByText('Evidence record 1')).toBeVisible()
    expect(screen.getByText(/Reviewed source 2/u)).toBeVisible()
    expect(screen.queryByText(opaque)).toBeNull()
    expect(screen.queryByRole('img')).toBeNull()
  })

  it('never offers a false human choice for an automatically decided field', async () => {
    const titleField = detail.fields[0]
    if (!titleField) throw new Error('detail fixture requires a title field')
    const automatic: CaseDetailView = {
      ...detail,
      fields: [{
        ...titleField,
        needsAttention: false,
        automaticDecision: {
          candidate_id: null,
          decision_id: 'automatic-decision-1',
          destination: 'title',
          occurred_at: 1,
          outcome: 'accept',
          proposal_id: 'proposal-1',
          review_case_id: 'case-1',
          selected_value: titleField.proposedValues[0] ?? null,
          target_fingerprint: 'fingerprint-1',
        },
      }],
    }
    const http: ReviewHttp = { ...createHttp(), getCaseDetail: vi.fn(async () => automatic) }
    renderRoute('/review/case-1?field=title&subview=review', http)

    expect(await screen.findByText('Handled automatically: accept · policy origin.')).toBeVisible()
    expect(screen.queryByRole('button', { name: /Choose current value/ })).toBeNull()
    expect(screen.queryByRole('button', { name: /Choose proposed value/ })).toBeNull()
    expect(http.recordDecision).not.toHaveBeenCalled()
  })

  it('shows the exact accepted recorded value without interpreting hostile content', async () => {
    const titleField = detail.fields[0]
    if (!titleField) throw new Error('detail fixture requires a title field')
    const recordedValue = { kind: 'text', value: `<img src=x>${'selected '.repeat(80)}` } as const
    const decided: CaseDetailView = {
      ...detail,
      fields: [{
        ...titleField,
        needsAttention: false,
        humanDecision: {
          actor: 'owner', decision_id: 'decision-accepted', destination: 'title', occurred_at: 1,
          outcome: 'accept', proposal_id: 'proposal-1', review_case_id: 'case-1',
          selected_value: recordedValue, target_fingerprint: 'fingerprint-1',
        },
      }],
    }
    renderRoute('/review/case-1?field=title&subview=review', { ...createHttp(), getCaseDetail: vi.fn(async () => decided) })

    expect(await screen.findByText(`Human decision: accept by owner. Recorded value: ${recordedValue.value}.`)).toBeVisible()
    expect(screen.queryByRole('img')).toBeNull()
  })

  it('reconciles an ambiguous decision without retrying and routes stale cases safely', async () => {
    const user = userEvent.setup()
    const titleField = detail.fields[0]
    if (!titleField) throw new Error('detail fixture requires a title field')
    const reconciled: CaseDetailView = {
      ...detail,
      fields: [{
        ...titleField,
        needsAttention: false,
        humanDecision: {
          actor: 'owner', decision_id: 'decision-1', destination: 'title', occurred_at: 1,
          outcome: 'keep_current', proposal_id: 'proposal-1', review_case_id: 'case-1',
          selected_value: null, target_fingerprint: 'fingerprint-1',
        },
      }],
    }
    let reads = 0
    const ambiguous = vi.fn(async () => { throw new AmbiguousMutationError() })
    const http: ReviewHttp = {
      ...createHttp(),
      getCaseDetail: vi.fn(async () => ++reads === 1 ? detail : reconciled),
      recordDecision: ambiguous,
    }
    renderRoute('/review/case-1?field=title&subview=review', http)

    await user.click(await screen.findByRole('button', { name: 'Choose current value: Old title' }))

    expect(await screen.findByText('Human decision: keep current by owner. Recorded value: Old title.')).toBeVisible()
    expect(screen.getAllByLabelText('Reviewer facts').at(0)).toHaveTextContent('Deterministic dispositionReview')
    expect(screen.queryByText('Review state')).toBeNull()
    expect(screen.getByText('The response was lost. The current case was reloaded before any retry.')).toBeVisible()
    expect(screen.queryByRole('button', { name: 'Try again' })).toBeNull()
    expect(screen.queryByRole('button', { name: /Choose current value/ })).toBeNull()
    expect(ambiguous).toHaveBeenCalledOnce()

    const staleHttp: ReviewHttp = { ...createHttp(), recordDecision: vi.fn(async () => { throw new ApiError(409, 'stale') }) }
    const stale = renderRoute('/review/case-1?field=title&subview=review', staleHttp)
    const keep = (await screen.findAllByRole('button', { name: 'Choose current value: Old title' })).at(-1)
    if (!keep) throw new Error('stale fixture requires a decision control')
    await user.click(keep)
    await waitFor(() => expect(stale.router.state.location.pathname).toBe('/library'))
  })

  it.each([
    {
      name: 'ambiguous response',
      error: new AmbiguousMutationError(),
      status: 'The decision outcome is uncertain and the current case could not be refreshed. Reload to reconcile it before reviewing another value.',
    },
    {
      name: 'ordinary failure',
      error: new ApiError(500, 'decision_failed'),
      status: 'The current decision state could not be reconciled because the case could not be refreshed. Reload before reviewing another value.',
    },
  ])('locks every field when an $name cannot be reconciled', async ({ error, status }) => {
    const user = userEvent.setup()
    const titleField = detail.fields[0]
    if (!titleField) throw new Error('detail fixture requires a title field')
    const authorsField = { ...titleField, destination: 'authors', proposalId: 'proposal-2', targetFingerprint: 'fingerprint-2' }
    const unresolved = { ...detail, fields: [titleField, authorsField] }
    const reconciled = { ...unresolved, fields: [{
      ...titleField,
      needsAttention: false,
      humanDecision: {
        actor: 'owner', decision_id: 'decision-reconciled', destination: 'title', occurred_at: 1,
        outcome: 'keep_current' as const, proposal_id: 'proposal-1', review_case_id: 'case-1',
        selected_value: null, target_fingerprint: 'fingerprint-1',
      },
    }, authorsField] }
    let reads = 0
    let refreshAvailable = false
    const recordDecision = vi.fn(async () => { throw error })
    const http: ReviewHttp = {
      ...createHttp(),
      getCaseDetail: vi.fn(async () => {
        reads += 1
        if (reads === 1) return unresolved
        if (!refreshAvailable) throw new Error('refresh unavailable')
        return reconciled
      }),
      recordDecision,
    }
    renderRoute('/review/case-1?field=title&subview=review', http)

    await user.click(await screen.findByRole('button', { name: 'Choose current value: Old title' }))

    expect(await screen.findByText(status)).toBeVisible()
    expect(screen.queryByText('The response was lost. The current case was reloaded before any retry.')).toBeNull()
    expect(screen.queryByText('The decision was not recorded. Your choice is unchanged.')).toBeNull()
    expect(screen.queryByRole('button', { name: /Choose current value/ })).toBeNull()
    expect(screen.queryByRole('button', { name: /Choose proposed value/ })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Try again' })).toBeNull()
    expect(screen.getAllByLabelText('Reviewer facts').at(0)).toHaveTextContent('Available choices0')

    await user.click(screen.getByRole('button', { name: 'authors' }))

    expect(screen.queryByRole('button', { name: /Choose current value/ })).toBeNull()
    expect(screen.queryByRole('button', { name: /Choose proposed value/ })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Try again' })).toBeNull()
    expect(screen.getAllByLabelText('Reviewer facts').at(1)).toHaveTextContent('Available choices0')
    expect(recordDecision).toHaveBeenCalledOnce()

    await user.click(screen.getByRole('link', { name: 'Library' }))
    await user.click(await screen.findByRole('button', { name: 'A Book' }))
    await user.click(await screen.findByRole('link', { name: 'Open full review' }))
    await waitFor(() => expect(http.getCaseDetail).toHaveBeenCalledTimes(4))

    expect(screen.getByText(status)).toHaveAttribute('role', 'alert')
    expect(screen.queryByRole('button', { name: /Choose current value/ })).toBeNull()
    expect(screen.queryByRole('button', { name: /Choose proposed value/ })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Try again' })).toBeNull()
    expect(screen.getAllByLabelText('Reviewer facts').at(0)).toHaveTextContent('Available choices0')
    await user.click(screen.getByRole('button', { name: 'authors' }))
    expect(screen.getAllByLabelText('Reviewer facts').at(1)).toHaveTextContent('Available choices0')

    refreshAvailable = true
    await user.click(screen.getByRole('link', { name: 'Library' }))
    await user.click(await screen.findByRole('button', { name: 'A Book' }))
    await user.click(await screen.findByRole('link', { name: 'Open full review' }))

    expect(await screen.findByRole('button', { name: 'Choose current value: Old title' })).toBeVisible()
    await user.click(screen.getByRole('button', { name: 'title' }))
    expect(screen.getByText('Human decision: keep current by owner. Recorded value: Old title.')).toBeVisible()
    await user.click(screen.getByRole('button', { name: 'authors' }))
    expect(screen.getByRole('button', { name: 'Choose current value: Old title' })).toBeVisible()
    expect(screen.getByRole('button', { name: 'Choose proposed value 1: New title' })).toBeVisible()
    expect(screen.getAllByLabelText('Reviewer facts').at(1)).toHaveTextContent('Available choices2')
  })

  it('binds an ambiguous reconciliation lock after a pending background detail refresh', async () => {
    const user = userEvent.setup()
    const titleField = detail.fields[0]
    if (!titleField) throw new Error('detail fixture requires a title field')
    const authorsField = { ...titleField, destination: 'authors', proposalId: 'proposal-2', targetFingerprint: 'fingerprint-2' }
    const unresolved = { ...detail, fields: [titleField, authorsField] }
    const background = { ...unresolved, title: 'Background authoritative detail' }
    let rejectDecision: ((error: unknown) => void) | undefined
    const decisionResult = new Promise<never>((_resolve, reject) => { rejectDecision = reject })
    let reads = 0
    let postLockRefreshAvailable = false
    const http: ReviewHttp = {
      ...createHttp(),
      getCaseDetail: vi.fn(async () => {
        reads += 1
        if (reads === 1) return unresolved
        if (reads === 2) return background
        if (postLockRefreshAvailable) return unresolved
        throw new Error('refresh unavailable')
      }),
      recordDecision: vi.fn(() => decisionResult),
    }
    const { queryClient } = renderRoute('/review/case-1?field=title&subview=review', http)

    await user.click(await screen.findByRole('button', { name: 'Choose current value: Old title' }))
    await act(async () => { await queryClient.fetchQuery({ ...detailQuery(http, 'case-1'), staleTime: 0 }) })
    expect(await screen.findByText('Background authoritative detail')).toBeVisible()
    await act(async () => { rejectDecision?.(new AmbiguousMutationError()) })

    const locked = 'The decision outcome is uncertain and the current case could not be refreshed. Reload to reconcile it before reviewing another value.'
    expect(await screen.findByText(locked)).toHaveAttribute('role', 'alert')
    expect(screen.queryByRole('button', { name: /Choose current value/ })).toBeNull()
    expect(screen.queryByRole('button', { name: /Choose proposed value/ })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Try again' })).toBeNull()

    await user.click(screen.getByRole('link', { name: 'Library' }))
    await user.click(await screen.findByRole('button', { name: 'A Book' }))
    await user.click(await screen.findByRole('link', { name: 'Open full review' }))
    await waitFor(() => expect(http.getCaseDetail).toHaveBeenCalledTimes(5))
    expect(screen.getByText(locked)).toHaveAttribute('role', 'alert')
    expect(screen.queryByRole('button', { name: /Choose current value/ })).toBeNull()
    await user.click(screen.getByRole('button', { name: 'authors' }))
    expect(screen.queryByRole('button', { name: /Choose proposed value/ })).toBeNull()

    postLockRefreshAvailable = true
    await user.click(screen.getByRole('link', { name: 'Library' }))
    await user.click(await screen.findByRole('button', { name: 'A Book' }))
    await user.click(await screen.findByRole('link', { name: 'Open full review' }))
    await user.click(await screen.findByRole('button', { name: 'authors' }))
    expect(screen.getByRole('button', { name: 'Choose current value: Old title' })).toBeVisible()
    expect(screen.getByRole('button', { name: 'Choose proposed value 1: New title' })).toBeVisible()
    expect(screen.queryByText(locked)).toBeNull()
  })

  it('replaces invalid detail state with the resolved canonical field and subview', async () => {
    const { router } = renderRoute('/review/case-1?field=missing&subview=hostile')

    expect(await screen.findByRole('heading', { name: 'Current review case' })).toBeVisible()
    await waitFor(() => expect(router.state.location.search).toEqual({ field: 'title', subview: 'review' }))
  })

  it('presents Policy as a semantic notebook and preserves guarded activation currentness', async () => {
    const user = userEvent.setup()
    const options = {
      title_layout: 'title_and_subtitle', date_precision: 'year', language_rendering: 'display_name', contributor_names: 'canonical', publisher_preference: 'publisher', subtitle: 'fill_empty', series: 'primary_narrative', genres: 'fill_empty', tags: 'off', description_fill: 'safe_plain_text', isbn_form: 'preserve_exact', cover_order: 'fixed_source_order', asin: 'preserve_exact', explicit: 'unsupported',
    }
    const destinations = {
      title_layout: 'ask', date_precision: 'ask', language_rendering: 'ask', contributor_names: 'ask', publisher_preference: 'ask', genres: 'automatic', tags: 'ignore', description_fill: 'ask', isbn_form: 'ask', cover_order: 'ask', asin: 'ask', explicit: 'unsupported',
    }
    let draftSaves = 0
    const policyCommand = vi.fn(async (path: string, body?: unknown): Promise<unknown> => {
      if (path === 'validate' && (body as { name?: string }).name === '') throw new ApiError(422, 'invalid_policy')
      if (path === 'drafts' && ++draftSaves > 1) throw new AmbiguousMutationError()
      if (path === 'drafts') return { policy_id: 'policy-draft', schema_version: 'metadata-policy-profile-v2' }
      if (path === 'preview') return { policy_id: 'policy-draft', schema_version: 'metadata-policy-preview-v1', libraries: [{ library: 'Hostile <script> library', affected_categories: ['title_layout', 'tags'], cases: [], cases_total: 0, cases_truncated: false, current_unapplied_count: 3, resolved_policy_id: 'resolved-policy' }] }
      if (path === 'activate') throw new ApiError(409, 'active_policy_changed')
      return { policy_id: 'policy-valid', schema_version: 'metadata-policy-profile-v2' }
    })
    const http: ReviewHttp = {
      ...createHttp(),
      getPolicyState: vi.fn(async (): Promise<PolicyStateView> => ({ schema_version: 'metadata-policy-notebook-v1', policy_id: 'opaque-policy-identity', profile: { schema_version: 'metadata-policy-profile-v2', name: 'Qualified default', options, destinations }, rich_qualification: [
        { automatic_available: false, candidate_sha256: 'a'.repeat(64), disposition: 'not_qualified', field: 'subtitle', option: 'fill_empty' },
        { automatic_available: false, candidate_sha256: 'a'.repeat(64), disposition: 'not_qualified', field: 'subtitle', option: 'corroborated_split' },
        { automatic_available: true, candidate_sha256: 'a'.repeat(64), disposition: 'passing_automatic', field: 'series', option: 'primary_narrative' },
        { automatic_available: true, candidate_sha256: 'a'.repeat(64), disposition: 'passing_automatic', field: 'series', option: 'source_order' },
        { automatic_available: true, candidate_sha256: 'a'.repeat(64), disposition: 'passing_automatic', field: 'series', option: 'all_qualified' },
        { automatic_available: true, candidate_sha256: 'a'.repeat(64), disposition: 'passing_automatic', field: 'genres', option: 'fill_empty' },
        { automatic_available: true, candidate_sha256: 'a'.repeat(64), disposition: 'passing_automatic', field: 'isbn_form', option: 'preserve_exact' },
        { automatic_available: true, candidate_sha256: 'a'.repeat(64), disposition: 'passing_automatic', field: 'isbn_form', option: 'prefer_isbn13' },
        { automatic_available: false, candidate_sha256: 'a'.repeat(64), disposition: 'passing_human_only', field: 'explicit', option: 'unsupported' },
      ] })),
      policyCommand: <T,>(path: string, body?: unknown) => policyCommand(path, body) as Promise<T>,
    }
    renderRoute('/policy?section=publication', http)

    expect(await screen.findByRole('heading', { name: 'Choose meaning before automation.' })).toBeVisible()
    expect(screen.getByRole('heading', { name: 'Identity' })).toBeVisible()
    expect(screen.getByRole('heading', { name: 'Publication' })).toBeVisible()
    expect(screen.getByRole('heading', { name: 'Curation' })).toBeVisible()
    expect(screen.getByRole('heading', { name: 'Unsupported content' })).toBeVisible()
    expect(screen.getByRole('combobox', { name: 'Policy chapter' })).toHaveValue('publication')
    expect(screen.queryByText('opaque-policy-identity')).toBeNull()
    const isbn = screen.getByRole('group', { name: /ISBN/ })
    expect(within(isbn).getByRole('button', { name: 'Automatic' })).toBeDisabled()
    expect(within(isbn).getByText(/Enter Preview mode before choosing qualified Automatic/)).toBeVisible()
    await user.selectOptions(within(isbn).getByRole('combobox', { name: 'ISBN representation' }), 'prefer_isbn13')
    expect(within(isbn).getByText(/Enter Preview mode before choosing qualified Automatic/)).toBeVisible()
    const series = screen.getByRole('group', { name: /Series memberships/ })
    expect(within(series).getByText(/Enter Preview mode before choosing qualified Automatic/)).toBeVisible()
    await user.click(screen.getByRole('button', { name: /Open curation/i }))
    const genres = screen.getByRole('group', { name: /^Genres/ })
    expect(within(genres).getByRole('button', { name: 'Automatic' })).toHaveAttribute('aria-pressed', 'true')
    expect(within(genres).getByText(/Enter Preview mode before choosing qualified Automatic/)).toBeVisible()
    await user.click(screen.getByRole('button', { name: /Open unsupported content/i }))
    const explicit = screen.getByRole('group', { name: /Explicit-content flag/ })
    expect(within(explicit).getByText(/remains unsupported and has no policy choice/)).toBeVisible()
    expect(screen.queryByRole('group', { name: /Title and subtitle/ })).toBeNull()
    expect(screen.getByRole('button', { name: /Open identity/i })).toBeVisible()

    await user.clear(screen.getByRole('textbox', { name: 'Policy name' }))
    await user.click(screen.getByRole('button', { name: 'Check draft' }))
    expect(await screen.findByText('The draft is not valid. Review the highlighted policy intent and try again.')).toBeVisible()
    await user.type(screen.getByRole('textbox', { name: 'Policy name' }), 'Qualified draft')
    await user.click(screen.getByRole('button', { name: /Open identity/i }))
    const titleLayout = screen.getByRole('group', { name: /Title and subtitle/ })
    const subtitle = screen.getByRole('group', { name: /^Subtitle/ })
    expect(within(subtitle).getByText(/did not meet the qualification gate/)).toBeVisible()
    await user.selectOptions(within(titleLayout).getByRole('combobox', { name: 'Title and subtitle representation' }), 'preserve')
    expect(within(titleLayout).getByText('Draft changed')).toBeVisible()
    expect(screen.getAllByText('Draft has unreviewed changes').length).toBeGreaterThan(0)
    await user.click(screen.getByRole('button', { name: 'Check draft' }))
    expect(await screen.findByText('Draft is valid. The active policy is unchanged.')).toBeVisible()
    await user.click(screen.getByRole('button', { name: 'Save draft' }))
    expect(await screen.findByText('Immutable draft saved. Preview its impact before activation.')).toBeVisible()
    await user.click(screen.getByRole('button', { name: 'Preview impact' }))
    expect(await screen.findByText('Hostile <script> library')).toBeVisible()
    expect(screen.getByText(/Title and subtitle, Tags/)).toBeVisible()
    const reviewActivation = screen.getByRole('button', { name: 'Review activation' })
    await user.click(reviewActivation)
    let activationDialog = screen.getByRole('dialog', { name: 'Apply this policy to current unapplied work?' })
    const cancelActivation = within(activationDialog).getByRole('button', { name: 'Cancel' })
    const activatePolicy = within(activationDialog).getByRole('button', { name: 'Activate policy' })
    expect(cancelActivation).toHaveFocus()
    await user.tab()
    expect(activatePolicy).toHaveFocus()
    await user.tab()
    expect(cancelActivation).toHaveFocus()
    await user.tab({ shift: true })
    expect(activatePolicy).toHaveFocus()
    await user.keyboard('{Escape}')
    await waitFor(() => expect(reviewActivation).toHaveFocus())
    expect(screen.queryByRole('dialog')).toBeNull()

    await user.click(reviewActivation)
    activationDialog = screen.getByRole('dialog', { name: 'Apply this policy to current unapplied work?' })
    await user.click(within(activationDialog).getByRole('button', { name: 'Cancel' }))
    await waitFor(() => expect(reviewActivation).toHaveFocus())

    await user.click(reviewActivation)
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Activate policy' }))

    expect(await screen.findByText('The active policy changed before activation. Review the refreshed policy and create a new draft.')).toBeVisible()
    await waitFor(() => expect(reviewActivation).toHaveFocus())
    await user.click(screen.getByRole('button', { name: 'Save draft' }))
    expect(await screen.findByText('The result is uncertain. Authoritative policy state is refreshing; verify it before trying again.')).toBeVisible()
    expect(policyCommand.mock.calls.map(([path]) => path)).toEqual(['validate', 'validate', 'drafts', 'preview', 'activate', 'drafts'])
  })

  it('tracks truthful Policy cadence stages and resets them after a draft edit', async () => {
    const user = userEvent.setup()
    const policyCommand = vi.fn(async (path: string): Promise<unknown> => {
      if (path === 'drafts') return { policy_id: 'policy-draft', schema_version: 'metadata-policy-profile-v2' }
      if (path === 'preview') return { policy_id: 'policy-draft', schema_version: 'metadata-policy-preview-v1', libraries: [] }
      return { policy_id: 'policy-valid', schema_version: 'metadata-policy-profile-v2' }
    })
    const http: ReviewHttp = { ...createHttp(), policyCommand: <T,>(path: string) => policyCommand(path) as Promise<T> }
    renderRoute('/policy?section=identity', http)

    const cadence = await screen.findByRole('list', { name: 'Policy activation steps' })
    const step = (name: string) => within(cadence).getByText(name).closest('li')
    expect(step('Validate')).toHaveAttribute('data-complete', 'false')
    expect(step('Preview')).toHaveTextContent('Not started')
    expect(step('Inspect impact')).toHaveAttribute('data-complete', 'false')

    await user.click(screen.getByRole('button', { name: 'Check draft' }))
    expect(await screen.findByText('Draft is valid. The active policy is unchanged.')).toBeVisible()
    expect(step('Validate')).toHaveAttribute('data-complete', 'true')
    expect(step('Preview')).toHaveAttribute('data-complete', 'false')

    await user.click(screen.getByRole('button', { name: 'Save draft' }))
    expect(await screen.findByText('Immutable draft saved. Preview its impact before activation.')).toBeVisible()
    expect(step('Preview')).toHaveAttribute('data-complete', 'false')
    expect(step('Preview')).toHaveAttribute('data-current', 'true')
    expect(step('Preview')).toHaveTextContent('Draft saved')

    await user.click(screen.getByRole('button', { name: 'Preview impact' }))
    expect(await screen.findByText('Impact preview is current. The active policy is unchanged.')).toBeVisible()
    expect(step('Preview')).toHaveAttribute('data-complete', 'true')
    expect(step('Inspect impact')).toHaveAttribute('data-complete', 'false')

    await user.click(screen.getByRole('button', { name: 'Review activation' }))
    expect(step('Inspect impact')).toHaveAttribute('data-complete', 'true')
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Cancel' }))
    await waitFor(() => expect(screen.getByRole('button', { name: 'Review activation' })).toHaveFocus())

    await user.type(screen.getByRole('textbox', { name: 'Policy name' }), ' revised')
    await waitFor(() => {
      for (const name of ['Validate', 'Preview', 'Inspect impact', 'Activate']) {
        expect(step(name)).toHaveAttribute('data-complete', 'false')
      }
    })
    expect(step('Preview')).toHaveTextContent('Not started')
    expect(screen.getByRole('button', { name: 'Review activation' })).toBeDisabled()
  })

  it('keeps every completed Policy cadence marker after successful activation', async () => {
    const user = userEvent.setup()
    const policyCommand = vi.fn(async (path: string): Promise<unknown> => {
      if (path === 'drafts') return { policy_id: 'policy-draft', schema_version: 'metadata-policy-profile-v2' }
      if (path === 'preview') return { policy_id: 'policy-draft', schema_version: 'metadata-policy-preview-v1', libraries: [] }
      if (path === 'activate') return { policy_id: 'policy-active', schema_version: 'metadata-policy-activation-v1', transition_id: 'transition-1' }
      return { policy_id: 'policy-valid', schema_version: 'metadata-policy-profile-v2' }
    })
    const http: ReviewHttp = { ...createHttp(), policyCommand: <T,>(path: string) => policyCommand(path) as Promise<T> }
    renderRoute('/policy?section=identity', http)

    await user.click(await screen.findByRole('button', { name: 'Check draft' }))
    await user.click(screen.getByRole('button', { name: 'Save draft' }))
    await user.click(screen.getByRole('button', { name: 'Preview impact' }))
    await user.click(screen.getByRole('button', { name: 'Review activation' }))
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Activate policy' }))

    expect(await screen.findByText('Policy activated. Current unapplied work is being reconciled.')).toBeVisible()
    const cadence = screen.getByRole('list', { name: 'Policy activation steps' })
    for (const name of ['Validate', 'Preview', 'Inspect impact', 'Activate']) {
      expect(within(cadence).getByText(name).closest('li')).toHaveAttribute('data-complete', 'true')
    }
    expect(within(cadence).getByText('Activate').closest('li')).toHaveTextContent('Activated')
    expect(policyCommand.mock.calls.map(([path]) => path)).toEqual(['validate', 'drafts', 'preview', 'activate'])
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

  it('refreshes active projections when passive preparation progress changes', async () => {
    vi.useFakeTimers()
    try {
      let checked = 0
      const http: ReviewHttp = {
        ...createHttp(),
        getPreparation: vi.fn(async (): Promise<PreparationView> => ({
          schema_version: 'review-preparation-v1',
          active: true,
          actionable: false,
          paused: false,
          libraries: [{
            blocked: 0,
            checked: checked++,
            connection: 'primary',
            library: 'Books',
            library_id: 'books',
            ready: 0,
            rescan_requested: false,
            scan_id: 'scan-1',
            state: 'discovering',
            total: 2,
          }],
        })),
      }
      const { queryClient } = renderRoute('/library?lens=all', http)
      const invalidations = vi.spyOn(queryClient, 'invalidateQueries')
      await vi.waitFor(() => expect(http.getPreparation).toHaveBeenCalledOnce())

      await act(async () => { await vi.advanceTimersByTimeAsync(1_600) })

      await vi.waitFor(() => expect(invalidations).toHaveBeenCalledWith({ queryKey: ['library'] }))
      expect(invalidations).toHaveBeenCalledWith({ queryKey: ['attention'] })
    } finally {
      vi.useRealTimers()
    }
  })

  it('returns focus to the preparation trigger when its activity panel closes', async () => {
    const user = userEvent.setup()
    const http: ReviewHttp = { ...createHttp(), getPreparation: vi.fn(async (): Promise<PreparationView> => ({ schema_version: 'review-preparation-v1', active: true, actionable: false, paused: false, libraries: [{ blocked: 0, checked: 1, connection: 'primary', library: 'Books', library_id: 'books', ready: 0, rescan_requested: false, scan_id: 'scan-1', state: 'scanning', total: 2 }] })) }
    renderRoute('/library?lens=all', http)

    const trigger = await screen.findByRole('button', { name: 'Preparing libraries' })
    await user.click(trigger)
    await user.click(screen.getByRole('button', { name: 'Close' }))
    await waitFor(() => expect(trigger).toHaveFocus())
  })

  it('moves focus to the persistent main landmark when polling removes preparation activity', async () => {
    const user = userEvent.setup()
    const active: PreparationView = { schema_version: 'review-preparation-v1', active: true, actionable: false, paused: false, libraries: [] }
    const inactive: PreparationView = { schema_version: 'review-preparation-v1', active: false, actionable: false, paused: false, libraries: [] }
    let current = active
    const http: ReviewHttp = { ...createHttp(), getPreparation: vi.fn(async () => current) }
    const { queryClient } = renderRoute('/library?lens=all', http)
    const trigger = await screen.findByRole('button', { name: 'Preparing libraries' })
    await user.click(trigger)
    current = inactive
    await queryClient.invalidateQueries({ queryKey: ['review-preparation'] })

    await waitFor(() => expect(screen.queryByRole('button', { name: 'Preparing libraries' })).toBeNull())
    expect(document.getElementById('main-content')).toHaveFocus()
  })

  it('polls idle Activity while mounted and tears the poll down after navigation', async () => {
    vi.useFakeTimers()
    try {
      const http = createHttp()
      const { router } = renderRoute('/activity', http)
      await vi.waitFor(() => expect(http.getActivity).toHaveBeenCalledOnce())

      await act(async () => { await vi.advanceTimersByTimeAsync(5_100) })
      await vi.waitFor(() => expect(http.getActivity).toHaveBeenCalledTimes(2))
      await act(async () => { await router.navigate({ to: '/library', search: { lens: 'all' } }) })
      const callsAfterNavigation = vi.mocked(http.getActivity).mock.calls.length

      await act(async () => { await vi.advanceTimersByTimeAsync(5_100) })

      expect(http.getActivity).toHaveBeenCalledTimes(callsAfterNavigation)
    } finally {
      vi.useRealTimers()
    }
  })

  it('discloses the exact destination subset and offers incremental consent', async () => {
    const user = userEvent.setup()
    const http: ReviewHttp = {
      ...createHttp(),
      getActivity: vi.fn(async () => activity({
        prepared: { automatic: 1, human: 1, total: 2 },
        configured_libraries: [{ connection: 'primary', library: 'fiction', prepared: { automatic: 1, human: 1, total: 2 } }],
        enabled_libraries: [{ connection: 'primary', library: 'fiction' }],
        capabilities: [{
          connection: 'primary', library: 'fiction', state: 'partially_allowed',
          destinations: [
            { destination: 'authors', state: 'allowed', consented: false },
            { destination: 'subtitle', state: 'field_unavailable', consented: false },
            { destination: 'title', state: 'allowed', consented: true },
          ],
        }],
        events: [], paused: false,
      })),
    }
    renderRoute('/activity', http)

    expect(await screen.findByRole('heading', { name: 'Ready work is waiting safely' })).toBeVisible()
    expect(screen.getByText('1 library is ready for consent · 2 prepared decisions')).toBeVisible()
    await user.click(screen.getByRole('button', { name: 'Open write-permission details' }))
    const disclosure = await screen.findByRole('list', { name: 'fiction destination consent' })
    expect(disclosure).toHaveTextContent('Authors: Included · consent requested')
    expect(disclosure).toHaveTextContent('Subtitle: Excluded · A field is unavailable')
    expect(disclosure).toHaveTextContent('Title: Included · already consented')
    const choice = screen.getByRole('checkbox', { name: /fiction · 2 prepared/ })
    await user.click(choice)
    await user.click(screen.getByRole('button', { name: 'Enable selected libraries' }))

    await waitFor(() => expect(http.activityCommand).toHaveBeenCalledWith(
      'activity/consent',
      { grants: [{ library: 'fiction', destinations: ['authors', 'title'] }] },
    ))
    await waitFor(() => expect(choice).not.toBeChecked())
    expect(screen.getByRole('button', { name: 'Enable selected libraries' })).toBeDisabled()
    expect(screen.getAllByText('Activity state refreshed from the authoritative server.')).toHaveLength(1)
    expect(screen.getByText('Activity state refreshed from the authoritative server.')).toHaveAttribute('role', 'status')
  })

  it.each([
    ['applied', true, 'The requested activity change was applied, confirmed by the authoritative server.'],
    ['unconfirmed', false, 'The activity outcome could not be confirmed. No command was resent.'],
  ])('reconciles an ambiguous pause as %s without resending it', async (_name, paused, message) => {
    const user = userEvent.setup()
    const initial: ActivityView = activity({ enabled_libraries: [{ connection: 'primary', library: 'fiction' }] })
    let current = initial
    const http: ReviewHttp = { ...createHttp(), getActivity: vi.fn(async () => current), activityCommand: vi.fn(async () => { throw new AmbiguousMutationError() }) }
    renderRoute('/activity', http)
    await screen.findByRole('button', { name: 'Pause new work' })
    current = { ...initial, paused }
    await user.click(screen.getByRole('button', { name: 'Pause new work' }))

    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent(message))
    expect(http.activityCommand).toHaveBeenCalledOnce()
    expect(http.getActivity).toHaveBeenCalledTimes(2)
  })

  it('reports an unresolved ambiguous pause without calling it safe or resending it', async () => {
    const user = userEvent.setup()
    const initial: ActivityView = activity({ enabled_libraries: [{ connection: 'primary', library: 'fiction' }] })
    let available = true
    const http: ReviewHttp = { ...createHttp(), getActivity: vi.fn(async () => { if (!available) throw new Error('offline'); return initial }), activityCommand: vi.fn(async () => { throw new AmbiguousMutationError() }) }
    renderRoute('/activity', http)
    await screen.findByRole('button', { name: 'Pause new work' })
    available = false
    await user.click(screen.getByRole('button', { name: 'Pause new work' }))

    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('The activity outcome could not be confirmed. No command was resent.'))
    expect(http.activityCommand).toHaveBeenCalledOnce()
  })

  it('does not treat a missing requested consent destination as applied after ambiguity', async () => {
    const user = userEvent.setup()
    const initial: ActivityView = activity({ configured_libraries: [{ connection: 'primary', library: 'fiction', prepared: { automatic: 0, human: 0, total: 1 } }], capabilities: [{ connection: 'primary', library: 'fiction', state: 'allowed', destinations: [{ destination: 'title', state: 'allowed', consented: false }] }] })
    let current = initial
    const http: ReviewHttp = { ...createHttp(), getActivity: vi.fn(async () => current), activityCommand: vi.fn(async () => { throw new AmbiguousMutationError() }) }
    renderRoute('/activity', http)
    await user.click(await screen.findByRole('checkbox', { name: /fiction · 1 prepared/ }))
    current = { ...initial, capabilities: [] }
    await user.click(screen.getByRole('button', { name: 'Enable guarded writes' }))

    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('The activity outcome could not be confirmed. No command was resent.'))
    expect(http.activityCommand).toHaveBeenCalledOnce()
  })

  it('prunes a selection when polling removes and later restores eligibility', async () => {
    const user = userEvent.setup()
    const partial: ActivityView = activity({
      configured_libraries: [{ connection: 'primary', library: 'fiction', prepared: { automatic: 1, human: 0, total: 1 } }],
      enabled_libraries: [],
      capabilities: [{ connection: 'primary', library: 'fiction', state: 'allowed', destinations: [{ destination: 'title', state: 'allowed', consented: false }] }],
    })
    let current = partial
    const http: ReviewHttp = { ...createHttp(), getActivity: vi.fn(async () => current) }
    const { queryClient } = renderRoute('/activity', http)
    const choice = await screen.findByRole('checkbox', { name: /fiction · 1 prepared/ })
    await user.click(choice)
    expect(choice).toBeChecked()

    current = { ...partial, enabled_libraries: [{ connection: 'primary', library: 'fiction' }], capabilities: [{ connection: 'primary', library: 'fiction', state: 'allowed', destinations: [{ destination: 'title', state: 'allowed', consented: true }] }] }
    await queryClient.invalidateQueries({ queryKey: ['activity'] })
    await waitFor(() => expect(screen.queryByRole('checkbox', { name: /fiction · 1 prepared/ })).toBeNull())

    current = partial
    await queryClient.invalidateQueries({ queryKey: ['activity'] })
    const restored = await screen.findByRole('checkbox', { name: /fiction · 1 prepared/ })
    expect(restored).toBeEnabled()
    expect(restored).not.toBeChecked()
    expect(screen.getByRole('button', { name: 'Enable guarded writes' })).toBeDisabled()
  })

  it('keeps a fully connection-blocked prepared library visible but unselectable', async () => {
    const http: ReviewHttp = {
      ...createHttp(),
      getActivity: vi.fn(async () => activity({
        configured_libraries: [{ connection: 'primary', library: 'blocked', prepared: { automatic: 2, human: 0, total: 2 } }],
        enabled_libraries: [],
        capabilities: [{
          connection: 'primary', library: 'blocked', state: 'connection_unavailable',
          destinations: [
            { destination: 'authors', state: 'connection_unavailable', consented: false },
            { destination: 'title', state: 'connection_unavailable', consented: false },
          ],
        }],
        events: [], paused: false,
      })),
    }
    renderRoute('/activity', http)

    const disclosure = await screen.findByRole('list', { name: 'blocked destination consent' })
    expect(disclosure).toHaveTextContent('Authors: Excluded · Connection needs checking')
    expect(disclosure).toHaveTextContent('Title: Excluded · Connection needs checking')
    expect(screen.getByRole('checkbox', { name: /blocked · 2 prepared/ })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Enable guarded writes' })).toBeDisabled()
  })

  it('labels qualified automatic activity and bounds unknown origins neutrally', async () => {
    const unknown = 'unexpected_origin_' + 'x'.repeat(100)
    const http: ReviewHttp = {
      ...createHttp(),
      getActivity: vi.fn(async () => activity({
        backup_ready: true, configured_libraries: [], enabled_libraries: [], capabilities: [], paused: false,
        events: [
          { work_id: 'book-1', library: 'fiction', destination: 'title', state: 'verified', origin: 'automatic_policy', occurred_at: null, failure: null, work_label: 'Work details unavailable' },
          { work_id: 'book-2', library: 'fiction', destination: 'subtitle', state: 'stale', origin: unknown, occurred_at: null, failure: null, work_label: 'Work details unavailable' },
        ],
      })),
    }
    renderRoute('/activity', http)

    expect(await screen.findByText(/Qualified automatic/)).toBeVisible()
    expect(screen.getByText(/No recovery condition is active/)).toBeVisible()
    const neutral = screen.getByText(/Recorded decision/)
    expect(neutral).toBeVisible()
    expect(neutral.textContent).not.toContain(unknown)
    expect(neutral.textContent).not.toContain('automatic')
  })

  it.each([
    ['prepared work after consent', { enabled_libraries: [{ connection: 'primary', library: 'fiction' }], prepared: { automatic: 1, human: 0, total: 1 } }, 'Ready work is waiting safely'],
    ['an idle enabled library', { enabled_libraries: [{ connection: 'primary', library: 'fiction' }] }, 'Ready for new work'],
    ['a retryable outcome', { enabled_libraries: [{ connection: 'primary', library: 'fiction' }], events: [{ work_id: 'book-1', library: 'fiction', destination: 'title', state: 'retryable', origin: 'human', occurred_at: null, failure: 'readback', work_label: 'Recorded work' }] }, 'A later safe check is needed'],
    ['a stopped outcome', { enabled_libraries: [{ connection: 'primary', library: 'fiction' }], events: [{ work_id: 'book-1', library: 'fiction', destination: 'title', state: 'abandoned', origin: 'human', occurred_at: null, failure: 'owner_abandoned', work_label: 'Recorded work' }] }, 'No work is active'],
    ['a verified outcome', { enabled_libraries: [{ connection: 'primary', library: 'fiction' }], events: [{ work_id: 'book-1', library: 'fiction', destination: 'title', state: 'verified', origin: 'human', occurred_at: null, failure: null, work_label: 'Recorded work' }] }, 'Completed work is up to date'],
  ])('derives the state summary from %s', async (_name, current, summary) => {
    const http: ReviewHttp = { ...createHttp(), getActivity: vi.fn(async () => activity(current)) }
    renderRoute('/activity', http)

    expect(await screen.findByRole('heading', { name: summary })).toBeVisible()
  })

  it('maps persisted failure codes to readable bounded explanations', async () => {
    const failures = ['target_changed', 'target_failure', 'backup', 'authority_superseded', 'readback', 'retry_exhausted']
    const http: ReviewHttp = { ...createHttp(), getActivity: vi.fn(async () => activity({
      events: failures.map((failure, index) => ({ work_id: `book-${String(index)}`, library: 'fiction', destination: 'title', state: 'abandoned', origin: 'human', occurred_at: null, failure, work_label: `Work ${String(index)}` })),
    })) }
    renderRoute('/activity', http)

    expect(await screen.findByText('The current value changed before completion.')).toBeVisible()
    expect(screen.getByText('The destination could not be reached safely.')).toBeVisible()
    expect(screen.getByText('Backup or recovery readiness prevented completion.')).toBeVisible()
    expect(screen.getByText('The recorded authority is no longer current.')).toBeVisible()
    expect(screen.getByText('The result is being checked before anything continues.')).toBeVisible()
    expect(screen.getByText('Automatic recovery stopped safely for owner review.')).toBeVisible()
    for (const failure of failures) expect(screen.queryByText(failure)).toBeNull()
  })

  it('renders duplicate activity events without duplicate React keys', async () => {
    const duplicate = { work_id: 'book-1', library: 'fiction', destination: 'title', state: 'verified', occurred_at: '2026-08-10T00:00:00Z' }
    const error = vi.spyOn(console, 'error').mockImplementation(() => undefined)
    const http: ReviewHttp = { ...createHttp(), getActivity: vi.fn(async () => activity({ events: [duplicate, duplicate].map((event) => ({ ...event, occurred_at: null, origin: '', failure: null, work_label: 'Work details unavailable' })) })) }
    renderRoute('/activity', http)

    await screen.findAllByText('Work details unavailable')
    await screen.findAllByText('Verified')
    expect(error).not.toHaveBeenCalledWith(expect.stringContaining('same key'), expect.anything())
    error.mockRestore()
  })

  it('groups a shared recovery once and keeps recovery authority on the persisted path', async () => {
    const http: ReviewHttp = {
      ...createHttp(),
      getActivity: vi.fn(async () => activity({
        enabled_libraries: [{ connection: 'primary', library: 'fiction' }],
        suspensions: [
          { connection: 'primary', destination: 'title', reason: 'unexpected_effect_behavior', scope: 'destination', updated_at: 1 },
          { connection: 'primary', destination: 'authors', reason: 'unexpected_effect_behavior', scope: 'destination', updated_at: 1 },
        ],
      })),
    }
    renderRoute('/activity', http)

    const recovery = await screen.findByRole('region', { name: 'Shared recovery' })
    expect(recovery).toHaveTextContent('One shared condition · 2 affected destinations')
    expect(recovery).toHaveTextContent('Affected new work pauses automatically')
    expect(recovery).toHaveTextContent('Affected destinations: Authors, Title')
    expect(recovery).toHaveTextContent('unrelated eligible destinations may continue')
    expect(screen.queryByRole('button', { name: /retry/i })).toBeNull()
    expect(screen.getByRole('button', { name: 'Pause new work' })).toBeVisible()
  })

  it('keeps distinct recovery causes separate and never invents a completion estimate', async () => {
    const http: ReviewHttp = { ...createHttp(), getActivity: vi.fn(async () => activity({
      enabled_libraries: [{ connection: 'primary', library: 'fiction' }],
      suspensions: [
        { connection: 'primary', destination: 'title', reason: 'unexpected_effect_behavior', scope: 'destination', updated_at: 1 },
        { connection: 'primary', destination: 'subtitle', reason: 'field_unavailable', scope: 'destination', updated_at: 1 },
        { connection: 'secondary', destination: null, reason: 'connection_unavailable', scope: 'connection', updated_at: 1 },
      ],
    })) }
    renderRoute('/activity', http)

    expect(await screen.findAllByRole('region', { name: 'Shared recovery' })).toHaveLength(3)
    expect(screen.getByText('The service response needs reconciliation')).toBeVisible()
    expect(screen.getByText('A supported field is unavailable')).toBeVisible()
    expect(screen.getByText('The service connection needs checking')).toBeVisible()
    expect(screen.getByText(/1 affected connection/)).toBeVisible()
    expect(screen.getByText('Affected libraries: Connected service')).toBeVisible()
    expect(screen.queryByRole('progressbar')).toBeNull()
  })

  it('explains why backup readiness disables consent while retaining existing history', async () => {
    const user = userEvent.setup()
    const http: ReviewHttp = { ...createHttp(), getActivity: vi.fn(async () => activity({
      backup_ready: false,
      prepared: { automatic: 1, human: 0, total: 1 },
      configured_libraries: [{ connection: 'primary', library: 'fiction', prepared: { automatic: 1, human: 0, total: 1 } }],
      capabilities: [{ connection: 'primary', library: 'fiction', state: 'allowed', destinations: [{ destination: 'title', state: 'allowed', consented: false }] }],
      events: [{ work_id: 'book-1', library: 'fiction', destination: 'title', state: 'verified', origin: 'human', occurred_at: null, failure: null, work_label: 'Recorded work' }],
    })) }
    renderRoute('/activity', http)

    expect(await screen.findByText('Recorded work')).toBeVisible()
    expect(screen.getByText('0 libraries are ready for consent · 1 prepared decision')).toBeVisible()
    await user.click(screen.getByRole('button', { name: 'Open write-permission details' }))
    expect(await screen.findByText(/A verified backup is required/)).toBeVisible()
    expect(screen.getByRole('checkbox', { name: /fiction · 1 prepared/ })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Enable guarded writes' })).toBeDisabled()
  })

  it('returns a revoked library with history to consent readiness instead of a false pause control', async () => {
    const user = userEvent.setup()
    const http: ReviewHttp = { ...createHttp(), getActivity: vi.fn(async () => activity({
      configured_libraries: [{ connection: 'primary', library: 'fiction', prepared: { automatic: 0, human: 0, total: 0 } }],
      capabilities: [{ connection: 'primary', library: 'fiction', state: 'allowed', destinations: [{ destination: 'title', state: 'allowed', consented: false }] }],
      events: [{ work_id: 'book-1', library: 'fiction', destination: 'title', state: 'verified', origin: 'human', occurred_at: null, failure: null, work_label: 'Recorded work' }],
    })) }
    renderRoute('/activity', http)

    expect(await screen.findByText('Recorded work')).toBeVisible()
    expect(screen.getByRole('heading', { name: 'Completed work is up to date' })).toBeVisible()
    expect(screen.getByText('0 libraries are ready for consent · 0 prepared decisions')).toBeVisible()
    await user.click(screen.getByRole('button', { name: 'Open write-permission details' }))
    expect(await screen.findByRole('heading', { name: 'Enable guarded writes' })).toBeVisible()
    expect(screen.getByRole('checkbox', { name: /fiction · 0 prepared/ })).toBeDisabled()
    expect(screen.queryByRole('button', { name: /pause new work|resume new work/i })).toBeNull()
  })

  it('keeps an unknown direct entry inside a safe shell fallback', async () => {
    renderRoute('/not-a-review-route')

    expect(await screen.findByRole('heading', { name: 'This review route does not exist.' })).toBeVisible()
    expect(screen.getByRole('link', { name: 'Open review library' })).toHaveAttribute('href', '/library?lens=all')
  })
})
