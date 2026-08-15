import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { RouterProvider, createMemoryHistory } from '@tanstack/react-router'
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { AmbiguousMutationError, ApiError, BuildMismatchError } from '../src/api/http'
import type { ActivityDisplayView, ActivitySearch, ActivityView, AttentionSearch, AttentionView, CaseDetailView, HistoryView, LibrarySearch, LibraryView, PolicyStateView, PreparationView, ReviewHttp, ShadowPostureView, ShadowSampleView } from '../src/api/http'
import { activityRoute, createApplicationRouter, detailQuery, normalizeLibrarySearch, normalizePolicySearch } from '../src/router'
import { activityEventKeys, activityOutcome } from '../src/activityState'
import { policyImpactDisplay, policyImpactTotals, policyRepresentationGuidance } from '../src/routes/PolicyPage'

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
    preparation: null,
    preparationOutcomes: [],
  }],
}

const detail: CaseDetailView = {
  id: 'case-1',
  current: true,
  title: 'A Book',
  authors: [],
  subtitle: 'Books · local',
  fields: [{
    classification: 'needs_attention',
    classificationReason: 'This field needs your review.',
    destination: 'title',
    editable: true,
    disposition: 'review',
    effectCapability: 'manual',
    needsAttention: true,
    navigationRole: 'attention',
    manualHold: false,
    currentValue: 'Old title',
    currentRaw: 'Old title',
    proposedValues: [{ kind: 'text', value: 'New title' }],
    choiceContexts: [],
    proposalId: 'proposal-1',
    targetFingerprint: 'fingerprint-1',
    humanDecision: null,
    automaticDecision: null,
    notices: [],
    provenance: [],
    preservationOnly: false,
    corroborationSources: [],
  }],
}

const activity = (value: Partial<Omit<ActivityDisplayView, 'events'>> & { events?: readonly Partial<ActivityDisplayView['events'][number]>[] } = {}): ActivityView => {
  const { events = [], ...rest } = value
  return {
    schema_version: 'continuous-field-activity-v2', backup_ready: true, paused: false,
    configured_libraries: [], consented_libraries: [], enabled_libraries: [], capabilities: [],
    counts: {}, example: null, last_failure: null, current_waiting: null,
    ledger: { event_type: 'all', page: 1, page_count: 1, page_size: 25, query: null, range_days: 30, total: events.length },
    prepared: { automatic: 0, human: 0, total: 0 }, suspensions: [], ...rest,
    events: events.map((event, index) => ({ event_id: `event-${String(index)}`, event_type: 'metadata', evidence: [], author_label: 'Author details are not recorded', destination: '', failure: null, library: '', occurred_at: null, origin: 'automatic_policy', state: 'verified', work_id: '', work_label: 'Recorded work', ...event })),
  }
}

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

const openActivityAuthority = async (user: ReturnType<typeof userEvent.setup>) => {
  await user.click(await screen.findByText('Authority and recovery'))
}

describe('typed review routes', () => {
  it('projects bounded policy impact language without promoting transport enums', () => {
    expect(policyImpactDisplay({ current_value: 'Old title', desired_value: 'New title', field: 'title_layout', outcome: 'projected', reason: null, target_state: 'value' })).toMatchObject({ changed: true, title: 'Change', detail: 'Old title → New title' })
    expect(policyImpactDisplay({ current_value: null, desired_value: 'A bounded subtitle', field: 'subtitle', outcome: 'projected', reason: null, target_state: 'missing' })).toMatchObject({ changed: true, title: 'Change', detail: 'fills the empty field with A bounded subtitle' })
    expect(policyImpactDisplay({ current_value: 'Old subtitle', desired_value: null, field: 'subtitle', outcome: 'projected', reason: null, target_state: 'missing' })).toMatchObject({ changed: true, title: 'Change', detail: 'clears Old subtitle' })
    expect(policyImpactDisplay({ current_value: null, desired_value: '', field: 'subtitle', outcome: 'projected', reason: null, target_state: 'missing' })).toMatchObject({ changed: false, title: 'No change', detail: 'remains empty — no supported value found' })
    expect(policyImpactDisplay({ current_value: 'Current title', desired_value: 'Current title', field: 'title_layout', outcome: 'projected', reason: null, target_state: 'value' })).toMatchObject({ changed: false, title: 'No change', detail: 'remains Current title — the exact current value is preserved' })
    expect(policyImpactDisplay({ current_value: null, desired_value: null, field: 'subtitle', outcome: 'abstain', reason: 'no_supported_evidence', target_state: 'missing' })).toMatchObject({ changed: false, title: 'No change', detail: 'remains empty — no supported value found' })
    expect(policyImpactDisplay({ current_value: null, desired_value: null, field: 'title_layout', outcome: 'no_op', reason: 'target_state_unusable', target_state: 'malformed' })).toMatchObject({ changed: false, title: 'Needs review', detail: 'Current value is unavailable or unreadable — inspect the source before deciding' })
    expect(policyImpactDisplay({ current_value: null, desired_value: null, field: 'tags', outcome: 'abstain', reason: 'policy_ignored', target_state: 'missing' })).toMatchObject({ changed: false, title: 'No change', detail: 'remains empty — this policy leaves the field unchanged' })
    expect(policyImpactDisplay({ current_value: null, desired_value: null, field: 'explicit', outcome: 'abstain', reason: 'explicit_unsupported', target_state: 'missing' })).toMatchObject({ changed: false, title: 'No supported value found', detail: 'empty remains — this destination is not currently supported' })
    expect(policyImpactDisplay({ current_value: 'Current title', desired_value: 'Current title', field: 'title_layout', outcome: 'no_op', reason: 'unchanged', target_state: 'value' })).toMatchObject({ changed: false, title: 'No change', detail: 'remains Current title — the exact current value is preserved' })
    expect(policyImpactDisplay({ current_value: 'Current title', desired_value: null, field: 'title_layout', outcome: 'abstain', reason: 'ambiguous_evidence', target_state: 'value' })).toMatchObject({ changed: false, title: 'Needs review', detail: 'Current title remains — evidence is ambiguous' })
    expect(policyImpactDisplay({ current_value: 'Current title', desired_value: null, field: 'title_layout', outcome: 'abstain', reason: 'policy_ignored', target_state: 'value' })).toMatchObject({ changed: false, title: 'No change', detail: 'remains Current title — this policy leaves the field unchanged' })
    expect(policyImpactDisplay({ current_value: 'Current title', desired_value: null, field: 'explicit', outcome: 'abstain', reason: 'explicit_unsupported', target_state: 'value' })).toMatchObject({ changed: false, title: 'No supported value found', detail: 'Current title remains — this destination is not currently supported' })
    expect(policyImpactTotals([
      { caseId: 'work-a', impact: policyImpactDisplay({ current_value: 'Old', desired_value: 'New', field: 'title_layout', outcome: 'projected', reason: null, target_state: 'value' }) },
      { caseId: 'work-a', impact: policyImpactDisplay({ current_value: 'Current', desired_value: 'Current', field: 'subtitle', outcome: 'no_op', reason: 'target_preserved', target_state: 'value' }) },
      { caseId: 'work-a', impact: policyImpactDisplay({ current_value: 'Current', desired_value: null, field: 'tags', outcome: 'abstain', reason: 'policy_ignored', target_state: 'value' }) },
      { caseId: 'work-b', impact: policyImpactDisplay({ current_value: 'Current', desired_value: null, field: 'title_layout', outcome: 'abstain', reason: 'ambiguous_evidence', target_state: 'value' }) },
    ])).toEqual({ changedWorks: 1, materialChanges: 1 })
  })

  it('keeps every supported closed Policy representation context-complete', () => {
    const expected = [
      'title_layout:preserve', 'title_layout:title_and_subtitle', 'contributor_names:preserve', 'contributor_names:canonical', 'language_rendering:preserve', 'language_rendering:display_name', 'subtitle:fill_empty', 'subtitle:corroborated_split', 'date_precision:year', 'date_precision:full_date', 'publisher_preference:publisher', 'publisher_preference:imprint', 'isbn_form:preserve_exact', 'isbn_form:prefer_isbn13', 'series:primary_narrative', 'series:source_order', 'series:all_qualified', 'genres:fill_empty', 'genres:add', 'genres:replace', 'tags:off', 'tags:fill_empty', 'tags:add', 'tags:replace', 'description_fill:safe_plain_text', 'description_fill:google_visible_text', 'cover_order:fixed_source_order', 'asin:preserve_exact', 'asin:ebook_manifestation',
    ]
    expect(Object.keys(policyRepresentationGuidance).sort()).toEqual(expected.sort())
    for (const guidance of Object.values(policyRepresentationGuidance)) {
      expect(guidance.admitted).not.toHaveLength(0)
      expect(guidance.transformation).not.toHaveLength(0)
      expect(guidance.effect).not.toHaveLength(0)
      expect(guidance.fallback).not.toHaveLength(0)
    }
    for (const key of ['contributor_names:preserve', 'contributor_names:canonical', 'language_rendering:preserve', 'language_rendering:display_name', 'date_precision:year', 'date_precision:full_date', 'publisher_preference:publisher']) {
      const guidance = policyRepresentationGuidance[key]
      if (!guidance) throw new Error(`missing guidance: ${key}`)
      expect(guidance.admitted).toContain('already-produced exact candidate')
      expect(guidance.transformation).toContain('without')
      expect(guidance.effect).toContain('only that exact selected')
      expect(guidance.fallback).toContain('no decision is qualified')
    }
    expect(policyRepresentationGuidance['date_precision:year']?.effect).toContain('Publication date or Publication year destination value')
    expect(policyRepresentationGuidance['date_precision:full_date']?.effect).toContain('Publication date or Publication year destination value')
    expect(policyRepresentationGuidance['publisher_preference:imprint']?.effect).toContain('no Automatic decision is qualified')
  })

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

  it('loads evidence only after the owner opens the Evidence subview', async () => {
    const user = userEvent.setup()
    const getEvidence = vi.fn(async () => ({ schema_version: 'review-case-evidence-v2', review_case_id: 'case-1', items: [], next_cursor: null }))
    const http: ReviewHttp = { ...createHttp(), getEvidence }
    renderRoute('/review/case-1?field=title&subview=review', http)

    expect(await screen.findByRole('heading', { name: 'Current review case' })).toBeVisible()
    expect(getEvidence).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: 'Evidence' }))
    await waitFor(() => expect(getEvidence).toHaveBeenCalledTimes(1))
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

  it('shows bound incumbent and manual alternatives and retries only failed advice', async () => {
    const user = userEvent.setup()
    const base = detail.fields[0]
    if (!base) throw new Error('cover fixture requires a field')
    const consideration = {
      id: `cover_consideration_${'c'.repeat(64)}`,
      choiceContexts: [{ alternativeId: null, selection: 'current', evidence: [], limitations: [], rationale: 'Keep the local cover.', recommendation: 'no_recommendation', recommendationReason: 'No recommendation retained.' }, { alternativeId: `cover_transport_ref_${'2'.repeat(64)}`, selection: 'proposed', evidence: [{ source: 'Open Library', role: 'external_corroboration', observation: 'Retained catalogue cover supports this choice.' }], limitations: [], rationale: 'Selectable retained cover.', recommendation: 'no_recommendation', recommendationReason: 'No recommendation retained.' }],
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
    const retryCoverConsideration = vi.fn(async () => { throw new Error('retry failed') })
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
    expect(await screen.findByRole('alert')).toHaveTextContent('The advisory retry failed safely. The existing cover and manual choices are unchanged.')
    await user.click(screen.getByRole('button', { name: 'Choose this cover' }))
    await waitFor(() => expect(recordDecision).toHaveBeenCalledWith('case-1', expect.objectContaining({ considerationId: consideration.id, outcome: 'accept', selectedValue: { kind: 'cover', value: `cover_${'b'.repeat(64)}` } })))
    await waitFor(() => expect(getHistory).toHaveBeenCalledTimes(2))
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
          choice_contexts: [],
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
        { id: 'case-1', destination: 'title', title: 'A Book', authors: ['Ada Example'], library: 'Books', mediaKind: 'ebook', classificationReason: 'Canonical standalone attention reason', notices: ['internal-standalone-notice'], provenance: ['opaque-source-1'], workAttentionCount: 3 },
        { id: 'case-1', destination: 'authors', title: 'A Book', authors: ['Ada Example'], library: 'Books', mediaKind: 'ebook', classificationReason: 'This field needs your review.', notices: [], provenance: [], workAttentionCount: 3 },
      ],
    }
    const http: ReviewHttp = { ...createHttp(), getAttention: vi.fn(async () => attention) }
    renderRoute('/attention', http)

    expect(await screen.findByRole('heading', { name: 'A Book' })).toBeVisible()
    expect(screen.getByText('3 fields')).toBeVisible()
    expect(screen.getByText('Canonical standalone attention reason')).toBeVisible()
    expect(screen.queryByText('internal-standalone-notice')).toBeNull()
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

    expect((await screen.findAllByLabelText('Reviewer facts')).at(0)).toHaveTextContent('Available choices4')
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
    expect(screen.getAllByLabelText('Reviewer facts').at(0)).toHaveTextContent('Available choices2')
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

  it('keeps an automatically decided field inspectable and owner-editable', async () => {
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
    expect(screen.getByRole('button', { name: /Choose current value/ })).toBeVisible()
    expect(screen.getByRole('button', { name: /Choose proposed value/ })).toBeVisible()
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
    expect(screen.getAllByLabelText('Reviewer facts').at(1)).toHaveTextContent('Available choices3')
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
      title_layout: 'ask', date_precision: 'ask', language_rendering: 'ask', contributor_names: 'ask', publisher_preference: 'ask', genres: 'automatic', tags: 'ignore', description_fill: 'automatic', isbn_form: 'ask', cover_order: 'ask', asin: 'ask', explicit: 'unsupported',
    }
    let validationCalls = 0
    let draftSaves = 0
    let activePolicyId = 'opaque-policy-identity'
    const policyCommand = vi.fn(async (path: string, body?: unknown): Promise<unknown> => {
      void body
      if (path === 'validate' && ++validationCalls === 1) throw new ApiError(422, 'invalid_policy')
      if (path === 'drafts' && ++draftSaves > 1) throw new AmbiguousMutationError()
      if (path === 'drafts') return { policy_id: 'policy-draft', schema_version: 'metadata-policy-profile-v2' }
      if (path === 'preview') {
        activePolicyId = 'policy-preview-authority'
        return { active_policy_id: activePolicyId, policy_id: 'policy-draft', schema_version: 'metadata-policy-preview-v1', libraries: [{ library: 'Hostile <script> library', affected_categories: ['title_layout', 'tags'], cases: [], cases_total: 0, cases_truncated: false, current_unapplied_count: 3, resolved_policy_id: 'resolved-policy' }] }
      }
      if (path === 'activate') throw new ApiError(409, 'policy_activation_is_stale')
      return { policy_id: 'policy-valid', schema_version: 'metadata-policy-profile-v2' }
    })
    const http: ReviewHttp = {
      ...createHttp(),
      getPolicyState: vi.fn(async (): Promise<PolicyStateView> => ({ schema_version: 'metadata-policy-notebook-v1', policy_id: activePolicyId, profile: { schema_version: 'metadata-policy-profile-v2', name: 'Qualified default', options, destinations }, rich_qualification: [
        { automatic_available: false, candidate_sha256: 'a'.repeat(64), disposition: 'not_qualified', field: 'subtitle', option: 'fill_empty' },
        { automatic_available: false, candidate_sha256: 'a'.repeat(64), disposition: 'not_qualified', field: 'subtitle', option: 'corroborated_split' },
        { automatic_available: true, candidate_sha256: 'a'.repeat(64), disposition: 'passing_automatic', field: 'series', option: 'primary_narrative' },
        { automatic_available: true, candidate_sha256: 'a'.repeat(64), disposition: 'passing_automatic', field: 'series', option: 'source_order' },
        { automatic_available: true, candidate_sha256: 'a'.repeat(64), disposition: 'passing_automatic', field: 'series', option: 'all_qualified' },
        { automatic_available: true, candidate_sha256: 'a'.repeat(64), disposition: 'passing_automatic', field: 'genres', option: 'fill_empty' },
        { automatic_available: false, candidate_sha256: 'a'.repeat(64), disposition: 'policy_only', field: 'tags', option: 'off' },
        { automatic_available: true, candidate_sha256: 'a'.repeat(64), disposition: 'passing_automatic', field: 'isbn_form', option: 'preserve_exact' },
        { automatic_available: true, candidate_sha256: 'a'.repeat(64), disposition: 'passing_automatic', field: 'isbn_form', option: 'prefer_isbn13' },
        { automatic_available: true, candidate_sha256: 'a'.repeat(64), disposition: 'passing_automatic', field: 'description_fill', option: 'safe_plain_text' },
        { automatic_available: false, candidate_sha256: 'a'.repeat(64), disposition: 'not_qualified', field: 'description_fill', option: 'google_visible_text' },
        { automatic_available: false, candidate_sha256: 'a'.repeat(64), disposition: 'insufficient_coverage', field: 'asin', option: 'preserve_exact' },
        { automatic_available: false, candidate_sha256: 'a'.repeat(64), disposition: 'insufficient_coverage', field: 'asin', option: 'ebook_manifestation' },
        { automatic_available: false, candidate_sha256: 'a'.repeat(64), disposition: 'passing_human_only', field: 'explicit', option: 'unsupported' },
      ] })),
      policyCommand: <T,>(path: string, body?: unknown) => policyCommand(path, body) as Promise<T>,
    }
    const { queryClient } = renderRoute('/policy?section=publication', http)

    expect(await screen.findByRole('heading', { name: 'Library policy' })).toBeVisible()
    expect(screen.getByRole('heading', { name: 'Publication' })).toBeVisible()
    expect(screen.queryByRole('heading', { name: 'Identity' })).toBeNull()
    expect(screen.queryByText('opaque-policy-identity')).toBeNull()
    expect(screen.queryByRole('textbox', { name: /Policy name/i })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Save draft' })).toBeNull()
    expect(screen.getByText('Choose how metadata is stored and when you want to review it.')).toBeVisible()
    expect(screen.getByRole('button', { name: 'Identity How a work names itself and its contributors.' })).toHaveAccessibleDescription('0 changed fields')
    expect(screen.getByRole('button', { name: 'Publication How edition facts and memberships are represented.' })).toHaveAccessibleDescription('0 changed fields')
    expect(screen.getByRole('button', { name: 'Curation Fields that remain intentionally owner-directed.' })).toHaveAccessibleDescription('0 changed fields')
    expect(screen.getByRole('button', { name: 'Unsupported Fields Mechanical Librarian can detect but cannot change yet.' })).toHaveAccessibleDescription('0 changed fields')
    expect(screen.getByText('Changed fields')).toBeVisible()
    expect(screen.getByRole('button', { name: /^Unsupported/ })).toHaveTextContent('Fields Mechanical Librarian can detect but cannot change yet.')
    const isbn = screen.getByRole('region', { name: 'ISBN' })
    expect(within(isbn).getByRole('button', { name: 'Automatic' })).toBeDisabled()
    expect(within(isbn).getByText('Enter Preview for Automatic')).toBeVisible()
    await user.click(within(isbn).getByRole('button', { name: 'Ignore' }))
    expect(screen.getByRole('button', { name: /^Publication/ })).toHaveAccessibleDescription('1 changed field')
    const publisher = screen.getByRole('region', { name: 'Publisher' })
    await user.click(within(publisher).getByRole('button', { name: 'Ignore' }))
    expect(screen.getByRole('button', { name: /^Publication/ })).toHaveAccessibleDescription('2 changed fields')
    await user.click(screen.getByRole('button', { name: /Curation/ }))
    const tags = screen.getByRole('region', { name: 'Tags' })
    expect(within(tags).getByText(/Manual review only: this policy option is intentionally off/i)).toBeVisible()
    expect(within(tags).getByRole('button', { name: 'Ask me' })).toBeEnabled()
    expect(within(tags).getByRole('button', { name: 'Ignore' })).toBeEnabled()
    const asin = screen.getByRole('region', { name: 'ASIN' })
    expect(within(asin).getByText(/Manual review only: this representation has insufficient Automatic qualification coverage/i)).toBeVisible()
    expect(within(asin).getByRole('button', { name: 'Ask me' })).toBeEnabled()
    expect(within(asin).getByRole('button', { name: 'Ignore' })).toBeEnabled()
    const description = screen.getByRole('region', { name: 'Description' })
    expect(within(description).getByText('Policy preview context:').parentElement).toHaveTextContent('one exact bounded plain-text description')
    expect(within(description).getByText(/does not transform HTML/i)).toBeVisible()
    await user.click(within(description).getByRole('combobox', { name: 'Description representation' }))
    await user.click(screen.getByRole('option', { name: 'Visible Google text' }))
    expect(await screen.findByText('Description now uses a Manual review only representation, so review mode changed from Automatic to Ask me.')).toBeVisible()
    expect(within(description).getByRole('button', { name: 'Ask me' })).toHaveAttribute('aria-pressed', 'true')
    expect(within(description).getByText(/eligible Google Books visible text only/i)).toBeVisible()
    expect(within(description).getByText(/bounded Google HTML into inert visible text/i)).toBeVisible()
    expect(within(description).getByText(/never falls back to Hardcover/i)).toBeVisible()
    expect(within(description).getByText('Ask me:').parentElement).toHaveTextContent('applies the exact displayed proposal')
    const changedGenres = screen.getByRole('region', { name: 'Genres' })
    await user.click(within(changedGenres).getByRole('button', { name: 'Ignore' }))
    expect(screen.getByRole('button', { name: /^Curation/ })).toHaveAccessibleDescription('2 changed fields')
    expect(screen.getByRole('button', { name: /^Publication/ })).toHaveAccessibleDescription('2 changed fields')
    await user.click(screen.getByRole('button', { name: /Publication/ }))
    const returnedPublisher = screen.getByRole('region', { name: 'Publisher' })
    await user.click(within(returnedPublisher).getByRole('button', { name: 'Ask me' }))
    expect(screen.getByRole('button', { name: /^Publication/ })).toHaveAccessibleDescription('1 changed field')
    const returnedIsbn = screen.getByRole('region', { name: 'ISBN' })
    await user.click(within(returnedIsbn).getByRole('button', { name: 'Ask me' }))
    expect(screen.getByRole('button', { name: /^Publication/ })).toHaveAccessibleDescription('0 changed fields')
    await user.click(screen.getByRole('button', { name: /Curation/ }))
    await user.click(screen.getByRole('button', { name: /Unsupported/ }))
    const explicit = screen.getByRole('region', { name: 'Explicit-content flag' })
    expect(within(explicit).getByText('Unsupported destination')).toBeVisible()
    expect(within(explicit).getByText(/shown for context only.*cannot be changed in this draft/i)).toBeVisible()
    expect(screen.queryByRole('region', { name: 'Title and subtitle' })).toBeNull()
    expect(screen.getAllByText('Draft has unreviewed changes').length).toBeGreaterThan(0)
    await user.click(screen.getByRole('button', { name: 'Validate and preview' }))
    expect(await screen.findByText('The draft is not valid. Review its field choices and try again.')).toBeVisible()
    await user.click(screen.getByRole('button', { name: 'Validate and preview' }))
    expect(await screen.findByText('Impact preview is current. The active policy is unchanged.')).toBeVisible()
    expect(await screen.findByText('Hostile <script> library')).toBeVisible()
    expect(screen.getByText(/Title and subtitle, Tags/)).toBeVisible()
    expect(screen.getByText('Needs review', { selector: '.cm-policy-impact > b' })).toBeVisible()
    expect(screen.getByText('· No sampled field outcome; 3 current unapplied works remain to inspect')).toBeVisible()
    const stateCallsBeforeChange = vi.mocked(http.getPolicyState).mock.calls.length
    // Simulate A → B during preview, then B → A before activation. The
    // activation must stay bound to B, the server-issued preview authority.
    activePolicyId = 'opaque-policy-identity'
    await queryClient.invalidateQueries({ queryKey: ['policy', 'state'] })
    await waitFor(() => expect(http.getPolicyState).toHaveBeenCalledTimes(stateCallsBeforeChange + 1))
    const reviewActivation = screen.getByRole('button', { name: 'Review activation' })
    await user.click(reviewActivation)
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(screen.getByText('Activate this reviewed draft?')).toBeVisible()
    await user.click(screen.getByRole('button', { name: 'Back to preview' }))
    expect(screen.getByRole('button', { name: 'Review activation' })).toBeVisible()
    await user.click(screen.getByRole('button', { name: 'Review activation' }))
    await user.click(screen.getByRole('button', { name: 'Activate policy' }))
    expect(await screen.findByText('The active policy changed before activation. Review the refreshed policy and create a new draft.')).toBeVisible()
    expect(policyCommand).toHaveBeenCalledWith('activate', {
      policy_id: 'policy-draft',
      expected_active_policy_id: 'policy-preview-authority',
    })
    expect(screen.queryByRole('button', { name: 'Review activation' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Activate policy' })).toBeNull()
    await user.click(screen.getByRole('button', { name: 'Validate and preview' }))
    expect(await screen.findByText('The result is uncertain. Authoritative policy state is refreshing; verify it before trying again.')).toBeVisible()
    expect(policyCommand.mock.calls.map(([path]) => path)).toEqual(['validate', 'validate', 'drafts', 'preview', 'activate', 'validate', 'drafts'])
  })

  it('identifies each affected work beside its exact policy outcome', async () => {
    const user = userEvent.setup()
    const field = (current_value: string, desired_value: string, name = 'title_layout') => ({
      current_value, desired_value, field: name, narrowed_for_target: false,
      option: 'title_only', outcome: 'projected', qualified_value: desired_value,
      reason: null, target_state: 'available',
    })
    const policyCommand = vi.fn(async (path: string): Promise<unknown> => {
      if (path === 'drafts') return { policy_id: 'policy-draft', schema_version: 'metadata-policy-profile-v2' }
      if (path === 'preview') return {
        active_policy_id: 'policy-active', policy_id: 'policy-draft', schema_version: 'metadata-policy-preview-v1', rich_qualification: [],
        libraries: [{
          library: 'Fiction', affected_categories: ['title_layout'], cases_total: 2,
          cases_truncated: true, current_unapplied_count: 7, resolved_policy_id: 'resolved-policy',
          cases: [
            { fields: [field('Old one', 'New one'), ...Array.from({ length: 7 }, (_, index) => field(`Old extra ${String(index)}`, `New extra ${String(index)}`, `extra_${String(index)}`))], proposal_set_id: 'proposal-1', report_id: 'report-1', resolved_policy_id: 'resolved-policy', work_label: 'First book' },
            { fields: [field('Old two', 'New two'), ...Array.from({ length: 7 }, (_, index) => field(`Second old extra ${String(index)}`, `Second new extra ${String(index)}`, `second_extra_${String(index)}`))], proposal_set_id: 'proposal-2', report_id: 'report-2', resolved_policy_id: 'resolved-policy', work_label: 'Second book' },
          ],
        }],
      }
      return { policy_id: 'policy-valid', schema_version: 'metadata-policy-profile-v2' }
    })
    const http: ReviewHttp = { ...createHttp(), policyCommand: <T,>(path: string) => policyCommand(path) as Promise<T> }
    renderRoute('/policy?section=identity', http)

    const title = await screen.findByRole('region', { name: 'Title and subtitle' })
    await user.click(within(title).getByRole('button', { name: 'Ignore' }))
    await user.click(screen.getByRole('button', { name: 'Validate and preview' }))

    expect((await screen.findAllByText('First book'))[0]).toBeVisible()
    expect(screen.getByText('Second book')).toBeVisible()
    expect(screen.getAllByText('Fiction · Title and subtitle')).toHaveLength(2)
    expect(screen.getAllByText('Change')).toHaveLength(6)
    expect(screen.getByText('· Old one → New one')).toBeVisible()
    expect(screen.getByText('· Old two → New two')).toBeVisible()
    expect(screen.getByText('2 sampled works with changes')).toBeVisible()
    expect(screen.getByText(/16 material changes in the bounded sample/)).toBeVisible()
    expect(screen.getByLabelText('2 sampled works with changes')).toHaveTextContent('2')
  })

  it('tracks the protected whole-draft cadence and invalidates preview after an edit', async () => {
    const user = userEvent.setup()
    const policyCommand = vi.fn(async (path: string): Promise<unknown> => {
      if (path === 'drafts') return { policy_id: 'policy-draft', schema_version: 'metadata-policy-profile-v2' }
      if (path === 'preview') return { active_policy_id: 'policy-active', policy_id: 'policy-draft', schema_version: 'metadata-policy-preview-v1', libraries: [{ library: 'Empty library', affected_categories: [], cases: [], cases_total: 0, cases_truncated: false, current_unapplied_count: 0, resolved_policy_id: 'resolved-policy' }] }
      return { policy_id: 'policy-valid', schema_version: 'metadata-policy-profile-v2' }
    })
    const http: ReviewHttp = { ...createHttp(), policyCommand: <T,>(path: string) => policyCommand(path) as Promise<T> }
    renderRoute('/policy?section=identity', http)

    const cadence = await screen.findByRole('list', { name: 'Policy activation steps' })
    const step = (name: string) => within(cadence).getByText(name).closest('li')
    expect(step('Validate')).toHaveAttribute('aria-current', 'step')
    expect(step('Validate')).not.toHaveClass('done')
    const title = await screen.findByRole('region', { name: 'Title and subtitle' })
    const representation = within(title).getByRole('combobox', { name: 'Title and subtitle representation' })
    await user.click(representation)
    await user.click(await screen.findByRole('option', { name: 'Separate title and subtitle' }))
    await user.click(screen.getByRole('button', { name: 'Validate and preview' }))
    expect(await screen.findByText('Impact preview is current. The active policy is unchanged.')).toBeVisible()
    expect(screen.getByText('· No current unapplied work remains to inspect')).toBeVisible()
    expect(step('Validate')).toHaveClass('done')
    expect(step('Preview')).toHaveAttribute('aria-current', 'step')
    await user.click(screen.getByRole('button', { name: 'Review activation' }))
    expect(step('Preview')).toHaveClass('done')
    expect(step('Activate')).toHaveAttribute('aria-current', 'step')
    await user.click(within(title).getByRole('button', { name: 'Ignore' }))
    expect(step('Validate')).toHaveAttribute('aria-current', 'step')
    expect(step('Validate')).not.toHaveClass('done')
    expect(screen.queryByText('Validated and saved')).toBeNull()
    expect(screen.queryByRole('button', { name: 'Review activation' })).toBeNull()
  })

  it('keeps every completed Policy cadence marker after successful activation', async () => {
    const user = userEvent.setup()
    const policyCommand = vi.fn(async (path: string): Promise<unknown> => {
      if (path === 'drafts') return { policy_id: 'policy-draft', schema_version: 'metadata-policy-profile-v2' }
      if (path === 'preview') return { active_policy_id: 'policy-active', policy_id: 'policy-draft', schema_version: 'metadata-policy-preview-v1', libraries: [] }
      if (path === 'activate') return { policy_id: 'policy-draft', schema_version: 'metadata-policy-activation-v1', transition: null }
      return { policy_id: 'policy-valid', schema_version: 'metadata-policy-profile-v2' }
    })
    const http: ReviewHttp = { ...createHttp(), policyCommand: <T,>(path: string) => policyCommand(path) as Promise<T> }
    renderRoute('/policy?section=identity', http)

    const title = await screen.findByRole('region', { name: 'Title and subtitle' })
    await user.click(within(title).getByRole('button', { name: 'Ignore' }))
    await user.click(screen.getByRole('button', { name: 'Validate and preview' }))
    expect(await screen.findByText('Impact preview is current. The active policy is unchanged.')).toBeVisible()
    await user.click(screen.getByRole('button', { name: 'Review activation' }))
    await user.click(screen.getByRole('button', { name: 'Activate policy' }))

    expect(await screen.findByText('Policy activated. Current unapplied work is being reconciled.')).toBeVisible()
    const cadence = screen.getByRole('list', { name: 'Policy activation steps' })
    for (const name of ['Validate', 'Preview', 'Activate']) {
      expect(within(cadence).getByText(name).closest('li')).toHaveClass('done')
    }
    expect(policyCommand.mock.calls.map(([path]) => path)).toEqual(['validate', 'drafts', 'preview', 'activate'])
  })

  it.each([
    ['invalid success payload', new ApiError(502, 'invalid_policy_response')],
    ['gateway timeout', new ApiError(504, 'gateway_timeout')],
    ['post-response build mismatch', new BuildMismatchError()],
  ])('treats a potentially committed activation with %s as uncertain', async (_name, activationError) => {
    const user = userEvent.setup()
    const policyCommand = vi.fn(async (path: string): Promise<unknown> => {
      if (path === 'drafts') return { policy_id: 'policy-draft', schema_version: 'metadata-policy-profile-v2' }
      if (path === 'preview') return { active_policy_id: 'policy-active', policy_id: 'policy-draft', schema_version: 'metadata-policy-preview-v1', libraries: [] }
      if (path === 'activate') throw activationError
      return { policy_id: 'policy-valid', schema_version: 'metadata-policy-profile-v2' }
    })
    const http: ReviewHttp = { ...createHttp(), policyCommand: <T,>(path: string) => policyCommand(path) as Promise<T> }
    renderRoute('/policy?section=identity', http)

    const title = await screen.findByRole('region', { name: 'Title and subtitle' })
    await user.click(within(title).getByRole('button', { name: 'Ignore' }))
    await user.click(screen.getByRole('button', { name: 'Validate and preview' }))
    await user.click(await screen.findByRole('button', { name: 'Review activation' }))
    await user.click(screen.getByRole('button', { name: 'Activate policy' }))

    expect(await screen.findByText('The activation result is uncertain. Authoritative policy state is refreshing; verify it before trying again.')).toBeVisible()
    expect(screen.queryByText('Activation failed safely. The reviewed snapshot remains available to retry.')).toBeNull()
  })

  it('treats a malformed successful activation response as uncertain', async () => {
    const user = userEvent.setup()
    const policyCommand = vi.fn(async (path: string): Promise<unknown> => {
      if (path === 'drafts') return { policy_id: 'policy-draft', schema_version: 'metadata-policy-profile-v2' }
      if (path === 'preview') return { active_policy_id: 'policy-active', policy_id: 'policy-draft', schema_version: 'metadata-policy-preview-v1', libraries: [] }
      if (path === 'activate') return {}
      return { policy_id: 'policy-valid', schema_version: 'metadata-policy-profile-v2' }
    })
    const http: ReviewHttp = { ...createHttp(), policyCommand: <T,>(path: string) => policyCommand(path) as Promise<T> }
    renderRoute('/policy?section=identity', http)

    const title = await screen.findByRole('region', { name: 'Title and subtitle' })
    await user.click(within(title).getByRole('button', { name: 'Ignore' }))
    await user.click(screen.getByRole('button', { name: 'Validate and preview' }))
    await user.click(await screen.findByRole('button', { name: 'Review activation' }))
    await user.click(screen.getByRole('button', { name: 'Activate policy' }))

    expect(await screen.findByText('The activation result is uncertain. Authoritative policy state is refreshing; verify it before trying again.')).toBeVisible()
    expect(screen.queryByText('Policy activated. Current unapplied work is being reconciled.')).toBeNull()
  })

  it('retains the reviewed preview while an earlier policy transition is processing', async () => {
    const user = userEvent.setup()
    const policyCommand = vi.fn(async (path: string): Promise<unknown> => {
      if (path === 'drafts') return { policy_id: 'policy-draft', schema_version: 'metadata-policy-profile-v2' }
      if (path === 'preview') return { active_policy_id: 'policy-active', policy_id: 'policy-draft', schema_version: 'metadata-policy-preview-v1', libraries: [] }
      if (path === 'activate') throw new ApiError(409, 'policy_transition_is_processing')
      return { policy_id: 'policy-valid', schema_version: 'metadata-policy-profile-v2' }
    })
    const http: ReviewHttp = { ...createHttp(), policyCommand: <T,>(path: string) => policyCommand(path) as Promise<T> }
    renderRoute('/policy?section=identity', http)

    const title = await screen.findByRole('region', { name: 'Title and subtitle' })
    await user.click(within(title).getByRole('button', { name: 'Ignore' }))
    await user.click(screen.getByRole('button', { name: 'Validate and preview' }))
    await user.click(await screen.findByRole('button', { name: 'Review activation' }))
    await user.click(screen.getByRole('button', { name: 'Activate policy' }))

    expect(await screen.findByText('The previous policy update is still processing. This reviewed snapshot remains available; retry after the transition is resolved.')).toBeVisible()
    expect(screen.getByRole('button', { name: 'Review activation' })).toBeVisible()
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

  it.each([
    ['paused', 'Library connection needs attention', 'Check connection again', false],
    ['checking', 'Checking library connection', 'Checking connection…', true],
    ['recovered', 'Library connection recovered', 'Recovered', true],
  ] as const)('renders %s connection recovery through Library, full Detail, and Activity without replacing route state', async (state, title, action, disabled) => {
    const preparation: PreparationView = { schema_version: 'review-preparation-v1', active: true, actionable: false, paused: false, libraries: [{ blocked: 1, checked: 2, connection: 'primary', library: 'Books', library_id: 'books', ready: 1, rescan_requested: false, scan_id: 'scan-1', state: 'processing', total: 3 }], connections: [{ connection: 'primary', code: 'unavailable', state, affected: state === 'recovered' ? 0 : 2, updated_at: 1 }] }
    const http: ReviewHttp = { ...createHttp(), getPreparation: vi.fn(async () => preparation) }
    const { router } = renderRoute('/library?lens=needs_attention&q=A%20Book', http)
    expect((await screen.findAllByText(title)).some((element) => getComputedStyle(element).display !== 'none')).toBe(true)
    expect(screen.getByText('Ready')).toBeVisible()
    expect(screen.getByRole('button', { name: action })).toHaveAttribute('aria-disabled', String(disabled))
    expect(screen.getByDisplayValue('A Book')).toBeVisible()
    await router.navigate({ to: '/review/$reviewCaseId', params: { reviewCaseId: 'case-1' }, search: { field: 'title', subview: 'review' } })
    expect((await screen.findAllByText(title)).some((element) => getComputedStyle(element).display !== 'none')).toBe(true)
    expect(await screen.findByRole('heading', { name: 'Current review case' })).toBeVisible()
    await router.navigate({ to: '/activity', search: { eventType: 'all', rangeDays: 30, page: 1, pageSize: 25 } })
    await screen.findByRole('heading', { name: 'Activity and recovery' })
    expect((await screen.findAllByText(title)).some((element) => getComputedStyle(element).display !== 'none')).toBe(true)
    const activityTitle = state === 'paused' ? 'Library connection is paused' : title
    expect(screen.getByRole('heading', { name: activityTitle })).toBeVisible()
    expect(screen.getByText('Recovery needed').parentElement).toHaveTextContent(state === 'recovered' ? '0' : '2')
    if (state !== 'recovered') {
      expect(screen.queryByRole('heading', { name: 'No intervention needed' })).toBeNull()
      expect(screen.queryByText('No action required')).toBeNull()
    }
  })

  it('sums simultaneous connection holds while keeping one focused recovery control', async () => {
    const preparation: PreparationView = { schema_version: 'review-preparation-v1', active: true, actionable: false, paused: false, libraries: [], connections: [
      { connection: 'primary', code: 'unavailable', state: 'paused', affected: 2, updated_at: 1 },
      { connection: 'secondary', code: 'authorization', state: 'checking', affected: 3, updated_at: 2 },
    ] }
    const http: ReviewHttp = { ...createHttp(), getPreparation: vi.fn(async () => preparation) }
    renderRoute('/activity', http)

    expect(await screen.findByRole('heading', { name: 'Library connection is paused' })).toBeVisible()
    expect(screen.getByText('Recovery needed').parentElement).toHaveTextContent('5')
  })

  it.each([
    ['tls', 'secure connection settings'],
    ['authentication', 'sign-in settings'],
    ['authorization', 'access permission'],
    ['unavailable', 'connection is unavailable'],
  ] as const)('uses safe owner-readable %s connection copy without raw connection values', async (code, expected) => {
    const preparation: PreparationView = { schema_version: 'review-preparation-v1', active: true, actionable: false, paused: false, libraries: [], connections: [{ connection: 'https://private.example/token', code, state: 'paused', affected: 1, updated_at: 1 }] }
    const http: ReviewHttp = { ...createHttp(), getPreparation: vi.fn(async () => preparation) }
    renderRoute('/library?lens=all', http)

    const notice = await screen.findByLabelText('Shared library connection status')
    expect(notice).toHaveTextContent(expected)
    expect(notice).not.toHaveTextContent('private.example')
    expect(notice).not.toHaveTextContent('token')
  })

  it('announces checking immediately and settles the retained route authoritatively', async () => {
    const user = userEvent.setup()
    const connection = { connection: 'primary', code: 'unavailable' as const, state: 'paused' as const, affected: 2, updated_at: 1 }
    const paused: PreparationView = { schema_version: 'review-preparation-v1', active: true, actionable: false, paused: false, libraries: [], connections: [connection] }
    const recovered: PreparationView = { ...paused, connections: [{ ...connection, state: 'recovered', affected: 0, updated_at: 2 }] }
    let current = paused
    let settle: ((value: PreparationView) => void) | undefined
    const pending = new Promise<PreparationView>((resolve) => { settle = resolve })
    const http: ReviewHttp = {
      ...createHttp(),
      getPreparation: vi.fn(async () => current),
      preparationAction: vi.fn(() => pending),
    }
    const { router } = renderRoute('/library?lens=needs_attention&q=A%20Book', http)

    const check = await screen.findByRole('button', { name: 'Check connection again' })
    await user.click(check)
    expect((await screen.findAllByText('Checking library connection')).some((element) => getComputedStyle(element).display !== 'none')).toBe(true)
    expect(check).toHaveFocus()
    expect(screen.getByRole('button', { name: 'Checking connection…' })).toHaveFocus()
    expect(screen.getByDisplayValue('A Book')).toBeVisible()

    await router.navigate({ to: '/review/$reviewCaseId', params: { reviewCaseId: 'case-1' }, search: { field: 'title', subview: 'review' } })
    expect(await screen.findByRole('heading', { name: 'Current review case' })).toBeVisible()
    current = recovered
    settle?.(recovered)
    expect((await screen.findAllByText('Library connection recovered')).some((element) => getComputedStyle(element).display !== 'none')).toBe(true)
    expect(await screen.findByRole('heading', { name: 'Current review case' })).toBeVisible()
  })

  it.each(['pointer', 'keyboard'] as const)('keeps the focused connection control stable for %s activation', async (activation) => {
    const user = userEvent.setup()
    const connection = { connection: 'primary', code: 'unavailable' as const, state: 'paused' as const, affected: 2, updated_at: 1 }
    const paused: PreparationView = { schema_version: 'review-preparation-v1', active: true, actionable: false, paused: false, libraries: [], connections: [connection] }
    let settle: ((value: PreparationView) => void) | undefined
    const http: ReviewHttp = {
      ...createHttp(), getPreparation: vi.fn(async () => paused),
      preparationAction: vi.fn(() => new Promise<PreparationView>((resolve) => { settle = resolve })),
    }
    renderRoute('/library?lens=all', http)

    const heading = await screen.findByRole('heading', { name: 'Every work, with exceptions first' })
    await waitFor(() => expect(heading.parentElement?.parentElement).toHaveFocus())
    const check = await screen.findByRole('button', { name: 'Check connection again' })
    check.focus()
    expect(check).toHaveFocus()
    if (activation === 'pointer') await user.click(check)
    else await user.keyboard('{Enter}')

    expect(http.preparationAction).toHaveBeenCalledOnce()
    expect(document.getElementById('main-content')).toHaveAttribute('data-route-focus-identity', '/:all:::::')
    const checking = screen.getByRole('button', { name: 'Checking connection…' })
    expect(checking).toBe(check)
    expect(checking).toHaveFocus()
    expect(checking).toHaveAttribute('aria-disabled', 'true')
    expect(checking).toHaveAttribute('aria-busy', 'true')
    settle?.(paused)
  })

  it('restores the actionable paused connection notice when mutation and refetch fail', async () => {
    const user = userEvent.setup()
    const connection = { connection: 'primary', code: 'unavailable' as const, state: 'paused' as const, affected: 2, updated_at: 1 }
    const paused: PreparationView = { schema_version: 'review-preparation-v1', active: true, actionable: false, paused: false, libraries: [], connections: [connection] }
    let calls = 0
    const http: ReviewHttp = {
      ...createHttp(),
      getPreparation: vi.fn(async () => { calls += 1; if (calls > 1) throw new Error('refetch failed'); return paused }),
      preparationAction: vi.fn(async () => { throw new Error('mutation failed') }),
    }
    renderRoute('/library?lens=all', http)
    const check = await screen.findByRole('button', { name: 'Check connection again' })
    await user.click(check)

    await waitFor(() => expect(screen.getByRole('button', { name: 'Check connection again' })).toHaveFocus())
    expect(screen.getByText('Safely paused').parentElement).toHaveTextContent('2')
    expect(screen.getByRole('alert')).toHaveTextContent('Connection checking could not be completed')
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

  it('keeps incremental consent behind the protected authority control', async () => {
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

    await openActivityAuthority(user)
    const consentTrigger = screen.getByRole('button', { name: 'Review write permissions' })
    expect(consentTrigger).toHaveAttribute('aria-expanded', 'false')
    expect(consentTrigger).toHaveAttribute('aria-controls', 'activity-consent')
    await user.click(consentTrigger)
    expect(screen.getByRole('heading', { name: 'Enable guarded writes' })).toBeVisible()
    expect(screen.getByRole('button', { name: 'Enable selected libraries' })).toBeDisabled()
  })

  it.each([
    ['a build mismatch', new BuildMismatchError()],
    ['a gateway failure', new ApiError(504, 'gateway_timeout')],
    ['an invalid authoritative response', new ApiError(502, 'invalid_activity_response')],
  ])('reconciles %s after a committed pause without claiming it failed safely', async (_name, failure) => {
    const user = userEvent.setup()
    const initial: ActivityView = activity({ enabled_libraries: [{ connection: 'primary', library: 'fiction' }] })
    let current = initial
    const http: ReviewHttp = { ...createHttp(), getActivity: vi.fn(async () => current), activityCommand: vi.fn(async () => { throw failure }) }
    renderRoute('/activity', http)
    await openActivityAuthority(user)
    current = { ...initial, paused: true }
    await user.click(screen.getByRole('button', { name: 'Pause new work' }))

    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('The requested activity change was applied, confirmed by the authoritative server.'))
    expect(screen.getByRole('alert')).not.toHaveTextContent('failed safely')
    expect(http.activityCommand).toHaveBeenCalledOnce()
    expect(http.getActivity).toHaveBeenCalledTimes(2)
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
    await openActivityAuthority(user)
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
    await openActivityAuthority(user)
    available = false
    await user.click(screen.getByRole('button', { name: 'Pause new work' }))

    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('The activity outcome could not be confirmed. No command was resent.'))
    expect(http.activityCommand).toHaveBeenCalledOnce()
  })

  it('keeps ambiguous consent unconfirmed without resending', async () => {
    const user = userEvent.setup()
    const initial: ActivityView = activity({ configured_libraries: [{ connection: 'primary', library: 'fiction', prepared: { automatic: 0, human: 0, total: 1 } }], capabilities: [{ connection: 'primary', library: 'fiction', state: 'allowed', destinations: [{ destination: 'title', state: 'allowed', consented: false }] }] })
    let current = initial
    const http: ReviewHttp = { ...createHttp(), getActivity: vi.fn(async () => current), activityCommand: vi.fn(async () => { throw new AmbiguousMutationError() }) }
    renderRoute('/activity', http)
    await openActivityAuthority(user)
    await user.click(screen.getByRole('button', { name: 'Review write permissions' }))
    await user.click(screen.getByRole('checkbox'))
    current = { ...initial, capabilities: [] }
    await user.click(screen.getByRole('button', { name: 'Enable selected libraries' }))

    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('The activity outcome could not be confirmed. No command was resent.'))
    expect(http.activityCommand).toHaveBeenCalledOnce()
  })

  it('clears protected consent selection when current eligibility changes', async () => {
    const user = userEvent.setup()
    const partial: ActivityView = activity({
      configured_libraries: [{ connection: 'primary', library: 'fiction', prepared: { automatic: 1, human: 0, total: 1 } }],
      enabled_libraries: [],
      capabilities: [{ connection: 'primary', library: 'fiction', state: 'allowed', destinations: [{ destination: 'title', state: 'allowed', consented: false }] }],
    })
    let current = partial
    const http: ReviewHttp = { ...createHttp(), getActivity: vi.fn(async () => current) }
    const { queryClient } = renderRoute('/activity', http)
    await openActivityAuthority(user)
    await user.click(screen.getByRole('button', { name: 'Review write permissions' }))
    const choice = await screen.findByRole('checkbox')
    await user.click(choice)
    expect(choice).toBeChecked()

    current = { ...partial, enabled_libraries: [{ connection: 'primary', library: 'fiction' }], capabilities: [{ connection: 'primary', library: 'fiction', state: 'allowed', destinations: [{ destination: 'title', state: 'allowed', consented: true }] }] }
    await queryClient.invalidateQueries({ queryKey: ['activity'] })
    await waitFor(() => expect(screen.getByRole('checkbox')).toBeDisabled())
    expect(screen.getByRole('button', { name: 'Enable selected libraries' })).toBeDisabled()

    current = partial
    await queryClient.invalidateQueries({ queryKey: ['activity'] })
    const restored = await screen.findByRole('checkbox')
    expect(restored).toBeEnabled()
    expect(restored).not.toBeChecked()
    expect(screen.getByRole('button', { name: 'Enable selected libraries' })).toBeDisabled()
  })

  it('keeps unavailable consent disabled in the protected authority view', async () => {
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

    const user = userEvent.setup()
    await openActivityAuthority(user)
    await user.click(screen.getByRole('button', { name: 'Review write permissions' }))
    expect(screen.getByRole('checkbox')).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Enable selected libraries' })).toBeDisabled()
  })

  it('bounds unknown ledger types neutrally', async () => {
    const unknown = 'unexpected_origin_' + 'x'.repeat(100)
    const http: ReviewHttp = {
      ...createHttp(),
      getActivity: vi.fn(async () => activity({
        backup_ready: true, configured_libraries: [], enabled_libraries: [], capabilities: [], paused: false,
        events: [
          { work_id: 'book-1', library: 'fiction', destination: 'title', state: 'verified', origin: 'automatic_policy', occurred_at: null, failure: null, work_label: 'Work details unavailable' },
          { event_type: 'unknown', work_id: 'book-2', library: 'fiction', destination: 'subtitle', state: 'stale', origin: unknown, occurred_at: null, failure: null, work_label: 'Work details unavailable' },
        ],
      })),
    }
    renderRoute('/activity', http)

    expect(await screen.findByText('Recorded')).toBeVisible()
    expect(document.body.textContent).not.toContain(unknown)
  })

  it.each([
    ['prepared', { enabled_libraries: [{ connection: 'primary', library: 'fiction' }], prepared: { automatic: 1, human: 0, total: 1 }, events: [{ work_label: 'Prepared work', state: 'planned' }] }, 'Planned'],
    ['applying', { enabled_libraries: [{ connection: 'primary', library: 'fiction' }], events: [{ work_label: 'Applying work', state: 'applying' }] }, 'Applying'],
    ['waiting', { enabled_libraries: [{ connection: 'primary', library: 'fiction' }], events: [{ work_label: 'Waiting work', state: 'retryable' }] }, 'Waiting safely'],
    ['stale', { enabled_libraries: [{ connection: 'primary', library: 'fiction' }], events: [{ work_label: 'Stale work', state: 'stale' }] }, 'Stale'],
    ['stopped', { enabled_libraries: [{ connection: 'primary', library: 'fiction' }], events: [{ work_label: 'Stopped work', state: 'abandoned' }] }, 'Stopped safely'],
    ['verified', { enabled_libraries: [{ connection: 'primary', library: 'fiction' }], events: [{ work_label: 'Verified work', state: 'verified' }] }, 'Verified'],
    ['no change', { events: [{ work_label: 'Unchanged work', state: 'no_change_required', origin: 'automatic_policy' }] }, 'No change required'],
    ['accepted', { events: [{ work_label: 'Accepted work', state: 'accept', origin: 'human' }] }, 'Accepted'],
    ['kept current', { events: [{ work_label: 'Kept work', state: 'keep_current', origin: 'human' }] }, 'Kept current'],
    ['rejected', { events: [{ work_label: 'Rejected work', state: 'reject', origin: 'human' }] }, 'Rejected'],
    ['completed automatic cover advice', { events: [{ work_label: 'Cover work', state: 'completed', origin: 'automatic_pipeline' }] }, 'Cover advice completed'],
    ['unavailable owner cover retry', { events: [{ work_label: 'Cover work', state: 'unavailable', origin: 'owner_retry' }] }, 'Cover advice unavailable'],
    ['failed owner cover retry', { events: [{ work_label: 'Cover work', state: 'failed', origin: 'owner_retry' }] }, 'Cover advice failed safely'],
  ])('keeps the protected ledger row truthful for %s work', async (_name, current, rowStatus) => {
    const http: ReviewHttp = { ...createHttp(), getActivity: vi.fn(async () => activity(current)) }
    renderRoute('/activity', http)

    expect(await screen.findByRole('heading', { name: 'No intervention needed' })).toBeVisible()
    expect(screen.getAllByText(rowStatus).at(-1)).toBeVisible()
  })

  it('keeps failed outcomes bounded in ledger rows', async () => {
    const failures = ['target_changed', 'target_failure', 'backup', 'authority_superseded', 'readback', 'retry_exhausted']
    const http: ReviewHttp = { ...createHttp(), getActivity: vi.fn(async () => activity({
      events: failures.map((failure, index) => ({ work_id: `book-${String(index)}`, library: 'fiction', destination: 'title', state: 'abandoned', origin: 'human', occurred_at: null, failure, work_label: `Work ${String(index)}` })),
    })) }
    renderRoute('/activity', http)

    expect(await screen.findAllByText('Stopped safely')).toHaveLength(failures.length)
    for (const failure of failures) expect(document.body.textContent).not.toContain(failure)
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

  it('keeps recovery visible without retry authority', async () => {
    const user = userEvent.setup()
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

    expect(await screen.findByRole('heading', { name: 'Recovery needs attention' })).toBeVisible()
    await openActivityAuthority(user)
    expect(screen.getAllByText(/Unexpected Effect Behavior/)).toHaveLength(2)
    expect(screen.queryByRole('button', { name: /retry/i })).toBeNull()
    expect(screen.getByRole('button', { name: 'Pause new work' })).toBeVisible()
  })

  it('projects all durable provider-recovery phases with safe scope and work units', async () => {
    const http: ReviewHttp = { ...createHttp(), getActivity: vi.fn(async () => activity({
      provider_recovery: [
        { state: 'waiting', affected_count: 2, next_eligible_at: 1 },
        { state: 'checking', affected_count: 1, next_eligible_at: null },
        { state: 'fanout', affected_count: 3, next_eligible_at: null },
        { state: 'recovered', affected_count: 1, next_eligible_at: null },
        { state: 'dormant', affected_count: 2, next_eligible_at: null },
        { state: 'exhausted', affected_count: 1, next_eligible_at: null },
      ],
    })) }
    renderRoute('/activity', http)

    expect(await screen.findByRole('heading', { name: 'Provider recovery' })).toBeVisible()
    expect(screen.getByText('Waiting for a safe retry').parentElement).toHaveTextContent('2 affected scopes')
    expect(screen.getByText('Checking provider health').parentElement).toHaveTextContent('1 affected scope')
    expect(screen.getByText('Retrying affected work').parentElement).toHaveTextContent('3 affected works')
    expect(screen.getByText('Recovered').parentElement).toHaveTextContent('1 affected scope')
    expect(screen.getByText('Needs a configuration change').parentElement).toHaveTextContent('2 affected scopes')
    expect(screen.getByText('No further retry scheduled').parentElement).toHaveTextContent('1 affected scope')
    expect(screen.queryByRole('button', { name: /provider.*retry/i })).toBeNull()
  })

  it('retains distinct recovery facts without a completion estimate', async () => {
    const user = userEvent.setup()
    const http: ReviewHttp = { ...createHttp(), getActivity: vi.fn(async () => activity({
      enabled_libraries: [{ connection: 'primary', library: 'fiction' }],
      suspensions: [
        { connection: 'primary', destination: 'title', reason: 'unexpected_effect_behavior', scope: 'destination', updated_at: 1 },
        { connection: 'primary', destination: 'subtitle', reason: 'field_unavailable', scope: 'destination', updated_at: 1 },
        { connection: 'secondary', destination: null, reason: 'connection_unavailable', scope: 'connection', updated_at: 1 },
      ],
    })) }
    renderRoute('/activity', http)

    await openActivityAuthority(user)
    expect(await screen.findAllByText(/^Recovery:/)).toHaveLength(3)
    expect(screen.queryByRole('progressbar')).toBeNull()
  })

  it('retains backup-disabled consent beside existing history', async () => {
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
    await openActivityAuthority(user)
    await user.click(screen.getByRole('button', { name: 'Review write permissions' }))
    expect(await screen.findByRole('checkbox')).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Enable selected libraries' })).toBeDisabled()
  })

  it('does not invent a pause control after revocation', async () => {
    const user = userEvent.setup()
    const http: ReviewHttp = { ...createHttp(), getActivity: vi.fn(async () => activity({
      configured_libraries: [{ connection: 'primary', library: 'fiction', prepared: { automatic: 0, human: 0, total: 0 } }],
      capabilities: [{ connection: 'primary', library: 'fiction', state: 'allowed', destinations: [{ destination: 'title', state: 'allowed', consented: false }] }],
      events: [{ work_id: 'book-1', library: 'fiction', destination: 'title', state: 'verified', origin: 'human', occurred_at: null, failure: null, work_label: 'Recorded work' }],
    })) }
    renderRoute('/activity', http)

    expect(await screen.findByText('Recorded work')).toBeVisible()
    await openActivityAuthority(user)
    await user.click(screen.getByRole('button', { name: 'Review write permissions' }))
    expect(await screen.findByRole('heading', { name: 'Enable guarded writes' })).toBeVisible()
    expect(screen.getByRole('checkbox')).toBeDisabled()
    expect(screen.queryByRole('button', { name: /pause new work|resume new work/i })).toBeNull()
  })

  it('requires an explicit resume for current waiting work while paused', async () => {
    const user = userEvent.setup()
    const http: ReviewHttp = { ...createHttp(), getActivity: vi.fn(async () => activity({
      paused: true,
      enabled_libraries: [{ connection: 'primary', library: 'fiction' }],
      current_waiting: { destination: 'title', library: 'fiction', state: 'retryable', work_id: 'current-work', work_label: 'Current waiting work' },
    })) }
    renderRoute('/activity', http)

    expect(await screen.findByText('New work paused')).toBeVisible()
    expect(screen.getByText('Resume new work when you are ready. No automatic check will start while new work is paused.')).toBeVisible()
    expect(screen.queryByText('Next exact-current check runs automatically. No action is required.')).toBeNull()
    await openActivityAuthority(user)
    expect(screen.getByRole('button', { name: 'Resume new work' })).toBeVisible()
  })

  it('keeps the protected ledger query, evidence, and owner controls bounded', async () => {
    const user = userEvent.setup()
    const http: ReviewHttp = { ...createHttp(), getActivity: vi.fn(async () => activity({
      enabled_libraries: [{ connection: 'primary', library: 'fiction' }],
      ledger: { event_type: 'all', page: 1, page_count: 3, page_size: 25, query: null, range_days: 30, total: 51 },
      current_waiting: { destination: 'title', library: 'fiction', state: 'retryable', work_id: 'current-work', work_label: 'Current waiting work' },
      events: [{ event_id: 'event-proof', event_type: 'safety', evidence: [{ label: 'Recorded cover safeguard', value: 'retryable' }, { label: 'Recorded failure', value: 'currentness' }], work_id: 'book-1', library: 'fiction', destination: 'title', state: 'verified', origin: 'human', occurred_at: 1, failure: null, work_label: 'Recorded work' }],
    })) }
    renderRoute('/activity', http)

    expect(await screen.findByRole('heading', { name: 'Activity and recovery' })).toBeVisible()
    expect(screen.getByRole('heading', { name: 'No intervention needed' })).toBeVisible()
    await openActivityAuthority(user)
    expect(screen.getByRole('button', { name: 'Pause new work' })).toBeVisible()
    expect(screen.getAllByText('Waiting safely')).toHaveLength(1)
    await user.click(screen.getByRole('button', { name: 'Inspect' }))
    expect(screen.getByText('Durable evidence')).toBeVisible()
    expect(screen.getByText('Recorded cover safeguard')).toBeVisible()
    expect(screen.getByText('Current waiting work · Title update')).toBeVisible()
    await user.click(screen.getByRole('button', { name: 'Close' }))
    const pageInput = screen.getByRole('spinbutton', { name: 'Activity page number' })
    await user.clear(pageInput)
    await user.type(pageInput, '3')
    await user.click(screen.getByRole('button', { name: 'Policy' }))
    await waitFor(() => expect(http.getActivity).toHaveBeenLastCalledWith(
      expect.objectContaining({ eventType: 'policy', page: 1 }), expect.anything(),
    ))
    expect(screen.getByRole('spinbutton', { name: 'Activity page number' })).toHaveValue(1)
    await user.click(screen.getByRole('button', { name: 'Next' }))
    await waitFor(() => expect(http.getActivity).toHaveBeenLastCalledWith(
      expect.objectContaining({ eventType: 'policy', page: 2 }), expect.anything(),
    ))
  })

  it.each([
    ['after the stale restore is queued', true],
    ['before the final navigation resolves', false],
  ])('keeps Inspect focused when Activity All navigation completes %s', async (_ordering, restoreQueued) => {
    const user = userEvent.setup()
    const frames: FrameRequestCallback[] = []
    const requestFrame = vi.spyOn(window, 'requestAnimationFrame').mockImplementation((callback) => {
      frames.push(callback)
      return frames.length
    })
    const cancelFrame = vi.spyOn(window, 'cancelAnimationFrame').mockImplementation(() => undefined)
    let releaseNavigation: (() => void) | undefined
    let restoreNavigation: (() => void) | undefined
    const view = (search: ActivitySearch) => activity({
      ledger: { event_type: search.eventType, page: search.page, page_count: 1, page_size: search.pageSize, query: search.q ?? null, range_days: search.rangeDays, total: 1 },
      events: [{ evidence: [{ label: 'Record', value: 'retained' }], work_label: 'Focused activity' }],
    })
    const http: ReviewHttp = {
      ...createHttp(),
      getActivity: vi.fn((search: ActivitySearch) => Promise.resolve(view(search))),
    }
    try {
      const { router } = renderRoute('/activity', http)
      await screen.findByRole('heading', { name: 'Activity and recovery' })
      frames.length = 0
      await user.click(screen.getByRole('button', { name: 'Reviews' }))
      await waitFor(() => expect(http.getActivity).toHaveBeenLastCalledWith(
        expect.objectContaining({ eventType: 'review' }), expect.anything(),
      ))
      const originalNavigate = router.navigate.bind(router)
      const delayedNavigate = vi.spyOn(router, 'navigate').mockImplementation(async (options) => {
        const result = await originalNavigate(options)
        return await new Promise<typeof result>((resolve) => { releaseNavigation = () => resolve(result) })
      })
      restoreNavigation = () => delayedNavigate.mockRestore()
      await user.click(screen.getByRole('button', { name: 'All' }))
      await waitFor(() => expect(releaseNavigation).toBeTypeOf('function'))
      if (restoreQueued) {
        act(() => { releaseNavigation?.() })
        await waitFor(() => expect(frames).not.toHaveLength(0))
        act(() => { frames.shift()?.(0) })
      }
      const inspect = screen.getByRole('button', { name: 'Inspect' })
      act(() => { inspect.focus() })
      expect(inspect).toHaveFocus()
      if (!restoreQueued) act(() => { releaseNavigation?.() })
      await act(async () => { await Promise.resolve() })
      act(() => { while (frames.length) frames.shift()?.(0) })
      expect(inspect).toHaveFocus()
      await user.keyboard('{Enter}')
      expect(inspect).toHaveAttribute('aria-expanded', 'true')
      expect(screen.getByText('Durable evidence')).toBeVisible()
    } finally {
      requestFrame.mockRestore()
      cancelFrame.mockRestore()
      restoreNavigation?.()
    }
  })

  it('preserves Activity drafts and control focus across interleaved route completion', async () => {
    const user = userEvent.setup()
    const http: ReviewHttp = {
      ...createHttp(),
      getActivity: vi.fn(async (search: ActivitySearch) => activity({
        ledger: { event_type: search.eventType, page: search.page, page_count: 3, page_size: search.pageSize, query: search.q ?? null, range_days: search.rangeDays, total: 75 },
      })),
    }
    renderRoute('/activity', http)

    await screen.findByRole('heading', { name: 'Activity and recovery' })
    await act(async () => { await new Promise((resolve) => window.setTimeout(resolve, 0)) })
    const search = screen.getByRole('searchbox', { name: 'Search activity' })
    await user.click(search)
    await user.type(search, 'ab')
    await waitFor(() => expect(search).toHaveValue('ab'))
    await waitFor(() => expect(http.getActivity).toHaveBeenLastCalledWith(
      expect.objectContaining({ q: 'ab', page: 1 }), expect.anything(),
    ))
    await waitFor(() => expect(screen.getByRole('searchbox', { name: 'Search activity' })).toHaveFocus())

    await user.clear(search)
    await user.type(search, 'desktop')
    await waitFor(() => expect(http.getActivity).toHaveBeenLastCalledWith(
      expect.objectContaining({ q: 'desktop', page: 1 }), expect.anything(),
    ))
    expect(search).toHaveValue('desktop')
    expect(search).toHaveFocus()

    const next = screen.getByRole('button', { name: 'Next' })
    await user.click(next)
    await waitFor(() => expect(http.getActivity).toHaveBeenLastCalledWith(
      expect.objectContaining({ q: 'desktop', page: 2 }), expect.anything(),
    ))
    await waitFor(() => expect(screen.getByRole('button', { name: 'Next' })).toHaveFocus())

    const page = screen.getByRole('spinbutton', { name: 'Activity page number' })
    await user.clear(page)
    await user.type(page, '99{Enter}')
    await waitFor(() => expect(http.getActivity).toHaveBeenLastCalledWith(
      expect.objectContaining({ page: 3 }), expect.anything(),
    ))
    await waitFor(() => expect(screen.getByRole('spinbutton', { name: 'Activity page number' })).toHaveFocus())

    const currentPage = screen.getByRole('spinbutton', { name: 'Activity page number' })
    await user.clear(currentPage)
    await user.type(currentPage, '2{Escape}')
    expect(http.getActivity).toHaveBeenLastCalledWith(
      expect.objectContaining({ page: 3 }), expect.anything(),
    )
    await waitFor(() => expect(screen.getByRole('spinbutton', { name: 'Activity page number' })).toHaveValue(3))
    await waitFor(() => expect(screen.getByRole('spinbutton', { name: 'Activity page number' })).toHaveFocus())

    const dateRange = screen.getByRole('combobox', { name: 'Activity date range' })
    await user.click(dateRange)
    await user.click(screen.getByRole('option', { name: 'Last 90 days' }))
    await waitFor(() => expect(screen.getByRole('combobox', { name: 'Activity date range' })).toHaveFocus())

    const escapedPage = screen.getByRole('spinbutton', { name: 'Activity page number' })
    await user.clear(escapedPage)
    await user.type(escapedPage, '2{Escape}')
    await waitFor(() => expect(screen.getByRole('spinbutton', { name: 'Activity page number' })).toHaveFocus())

    const rows = screen.getByRole('combobox', { name: 'Rows per activity page' })
    await user.click(rows)
    await user.click(screen.getByRole('option', { name: '50 per page' }))
    await waitFor(() => expect(screen.getByRole('combobox', { name: 'Rows per activity page' })).toHaveFocus())

    await user.click(screen.getByRole('button', { name: 'Last' }))
    await waitFor(() => expect(screen.getByRole('spinbutton', { name: 'Activity page number' })).toHaveValue(3))
    const previous = screen.getByRole('button', { name: 'Previous' })
    act(() => { previous.click(); previous.click() })
    await waitFor(() => expect(screen.getByRole('spinbutton', { name: 'Activity page number' })).toHaveValue(1))
    await waitFor(() => expect(screen.getByRole('spinbutton', { name: 'Activity page number' })).toHaveFocus())

    await user.click(screen.getByRole('button', { name: 'Last' }))
    await waitFor(() => expect(screen.getByRole('spinbutton', { name: 'Activity page number' })).toHaveValue(3))
    await waitFor(() => expect(screen.getByRole('spinbutton', { name: 'Activity page number' })).toHaveFocus())

    await waitFor(() => expect(http.getActivity).toHaveBeenLastCalledWith(
      expect.objectContaining({ page: 3, pageSize: 50 }), expect.anything(),
    ))
  })

  it('does not let an unmounted Activity focus callback target a fresh Activity route', async () => {
    const frames = new Map<number, FrameRequestCallback>()
    let nextFrame = 1
    const requestFrame = vi.spyOn(window, 'requestAnimationFrame').mockImplementation((callback) => {
      const frame = nextFrame++
      frames.set(frame, callback)
      return frame
    })
    const cancelFrame = vi.spyOn(window, 'cancelAnimationFrame').mockImplementation((frame) => { frames.delete(frame) })
    try {
      const getActivity = vi.fn(async (search: ActivitySearch) => activity({
        ledger: { event_type: search.eventType, page: search.page, page_count: 3, page_size: search.pageSize, query: search.q ?? null, range_days: search.rangeDays, total: 75 },
      }))
      const { router } = renderRoute('/activity', { ...createHttp(), getActivity })
      await screen.findByRole('heading', { name: 'Activity and recovery' })

      fireEvent.click(screen.getByRole('button', { name: 'Next' }))
      await waitFor(() => expect(screen.getByRole('spinbutton', { name: 'Activity page number' })).toHaveValue(2))
      const outer = [...frames.entries()].at(-1)
      expect(outer).toBeDefined()
      await act(async () => { outer?.[1](0) })
      const staleInner = [...frames.entries()].at(-1)
      expect(staleInner).toBeDefined()

      await act(async () => { await router.navigate({ to: '/library', search: { lens: 'all' } }) })
      await screen.findByRole('heading', { name: 'Every work, with exceptions first' })
      await act(async () => { await router.navigate({ to: '/activity', search: { eventType: 'all', rangeDays: 30, page: 1, pageSize: 25 } }) })
      await screen.findByRole('heading', { name: 'Activity and recovery' })
      const freshSearch = screen.getByRole('searchbox', { name: 'Search activity' })
      freshSearch.focus()

      await act(async () => { staleInner?.[1](0) })
      expect(freshSearch).toHaveFocus()
    } finally {
      requestFrame.mockRestore()
      cancelFrame.mockRestore()
    }
  })

  it('commits the next page edit once after an Escape cancellation', async () => {
    const user = userEvent.setup()
    const getActivity = vi.fn(async (search: ActivitySearch) => activity({
      ledger: { event_type: search.eventType, page: search.page, page_count: 3, page_size: search.pageSize, query: search.q ?? null, range_days: search.rangeDays, total: 75 },
    }))
    renderRoute('/activity', { ...createHttp(), getActivity })

    const heading = await screen.findByRole('heading', { name: 'Activity and recovery' })
    const focusTarget = heading.closest<HTMLElement>('[data-route-focus]')
    expect(focusTarget).not.toBeNull()
    await waitFor(() => expect(focusTarget).toHaveFocus())
    const page = screen.getByRole('spinbutton', { name: 'Activity page number' })
    const callsBeforeEscape = getActivity.mock.calls.length
    await user.clear(page)
    await user.type(page, '2{Escape}')
    expect(page).toHaveValue(1)
    expect(page).toHaveFocus()
    expect(getActivity).toHaveBeenCalledTimes(callsBeforeEscape)

    await user.clear(page)
    await user.type(page, '2')
    await user.click(screen.getByRole('searchbox', { name: 'Search activity' }))
    await waitFor(() => expect(getActivity).toHaveBeenLastCalledWith(
      expect.objectContaining({ page: 2 }), expect.anything(),
    ))
    expect(getActivity.mock.calls.filter(([search]) => search.page === 2)).toHaveLength(1)
  })

  it.each([['a', 'ab'], ['ab', 'a']] as const)('keeps the latest Activity query draft when deferred replacements complete %s then %s', async (first, second) => {
    const deferred = new Map<string, { resolve: (value: ActivityView) => void }>()
    const http: ReviewHttp = {
      ...createHttp(),
      getActivity: vi.fn((search: ActivitySearch) => {
        if (search.q !== 'a' && search.q !== 'ab') return Promise.resolve(activity({ ledger: { event_type: search.eventType, page: search.page, page_count: 3, page_size: search.pageSize, query: search.q ?? null, range_days: search.rangeDays, total: 75 } }))
        return new Promise<ActivityView>((resolve) => { deferred.set(search.q ?? '', { resolve }) })
      }),
    }
    renderRoute('/activity', http)
    await screen.findByRole('heading', { name: 'Activity and recovery' })
    vi.useFakeTimers()
    try {
      const query = screen.getByRole('searchbox', { name: 'Search activity' })
      fireEvent.change(query, { target: { value: 'a' } })
      await act(async () => { await vi.advanceTimersByTimeAsync(150) })
      expect(deferred.has('a')).toBe(true)
      fireEvent.change(query, { target: { value: 'ab' } })
      await act(async () => { await vi.advanceTimersByTimeAsync(150) })
      expect(deferred.has('ab')).toBe(true)

      act(() => { deferred.get(first)?.resolve(activity({ ledger: { event_type: 'all', page: 1, page_count: 3, page_size: 25, query: first, range_days: 30, total: 75 } })) })
      act(() => { deferred.get(second)?.resolve(activity({ ledger: { event_type: 'all', page: 1, page_count: 3, page_size: 25, query: second, range_days: 30, total: 75 } })) })

      await act(async () => { await vi.advanceTimersByTimeAsync(1) })
      expect(screen.getByRole('searchbox', { name: 'Search activity' })).toHaveValue('ab')
    } finally {
      vi.useRealTimers()
    }
  })

  it('keeps slow sequential search editing focused when a prior dropdown replacement settles late', async () => {
    const deferred = new Map<string, { resolve: (value: ActivityView) => void }>()
    const http: ReviewHttp = {
      ...createHttp(),
      getActivity: vi.fn((search: ActivitySearch) => {
        const key = search.q ?? `rows-${String(search.pageSize)}`
        if (search.pageSize !== 50 && !search.q?.startsWith('desktop')) return Promise.resolve(activity({ ledger: { event_type: search.eventType, page: search.page, page_count: 3, page_size: search.pageSize, query: search.q ?? null, range_days: search.rangeDays, total: 75 } }))
        return new Promise<ActivityView>((resolve) => { deferred.set(key, { resolve }) })
      }),
    }
    const user = userEvent.setup({ delay: 175 })
    renderRoute('/activity', http)

    await screen.findByRole('heading', { name: 'Activity and recovery' })
    const rows = screen.getByRole('combobox', { name: 'Rows per activity page' })
    await user.click(rows)
    await user.click(screen.getByRole('option', { name: '50 per page' }))
    await waitFor(() => expect(deferred.has('rows-50')).toBe(true))

    const search = screen.getByRole('searchbox', { name: 'Search activity' })
    await user.click(search)
    await user.type(search, 'desktop')
    await waitFor(() => expect(deferred.has('desktop')).toBe(true))
    expect(screen.getByRole('searchbox', { name: 'Search activity' })).toHaveValue('desktop')
    expect(screen.getByRole('searchbox', { name: 'Search activity' })).toHaveFocus()

    act(() => { deferred.get('rows-50')?.resolve(activity({ ledger: { event_type: 'all', page: 1, page_count: 3, page_size: 50, query: null, range_days: 30, total: 75 } })) })
    await waitFor(() => expect(screen.getByRole('searchbox', { name: 'Search activity' })).toHaveValue('desktop'))
    expect(screen.getByRole('searchbox', { name: 'Search activity' })).toHaveFocus()

    act(() => { deferred.get('desktop')?.resolve(activity({ ledger: { event_type: 'all', page: 1, page_count: 3, page_size: 50, query: 'desktop', range_days: 30, total: 75 } })) })
    await waitFor(() => expect(http.getActivity).toHaveBeenLastCalledWith(expect.objectContaining({ q: 'desktop' }), expect.anything()))
  }, 10_000)

  it('retains the Activity search draft through a pending same-query page-size replacement', async () => {
    expect(activityRoute.options.pendingMs).toBe(Infinity)
    let resolveRows: ((value: ActivityView) => void) | undefined
    const http: ReviewHttp = {
      ...createHttp(),
      getActivity: vi.fn((search: ActivitySearch) => {
        if (search.q === 'ab' && search.pageSize === 50) return new Promise<ActivityView>((resolve) => { resolveRows = resolve })
        return Promise.resolve(activity({ ledger: { event_type: search.eventType, page: search.page, page_count: 3, page_size: search.pageSize, query: search.q ?? null, range_days: search.rangeDays, total: 75 } }))
      }),
    }
    const user = userEvent.setup()
    renderRoute('/activity?q=ab', http)

    const search = await screen.findByRole('searchbox', { name: 'Search activity' })
    const rows = screen.getByRole('combobox', { name: 'Rows per activity page' })
    await user.click(rows)
    await user.click(screen.getByRole('option', { name: '50 per page' }))
    await waitFor(() => expect(resolveRows).toBeTypeOf('function'))
    search.focus()
    await user.clear(search)
    expect(screen.getByRole('searchbox', { name: 'Search activity' })).toBe(search)
    expect(search).toHaveValue('')
    expect(search).toHaveFocus()

    act(() => { resolveRows?.(activity({ ledger: { event_type: 'all', page: 1, page_count: 3, page_size: 50, query: 'ab', range_days: 30, total: 75 } })) })
    await waitFor(() => expect(search).toHaveValue(''))
    expect(screen.getByRole('searchbox', { name: 'Search activity' })).toBe(search)
    expect(search).toHaveFocus()

    await user.type(search, 'desktop')
    expect(search).toHaveValue('desktop')
    expect(search).toHaveFocus()
    await waitFor(() => expect(http.getActivity).toHaveBeenLastCalledWith(expect.objectContaining({ q: 'desktop' }), expect.anything()))
  })

  it('uses memory-history traversal, rather than synthetic popstate, as Activity query and page authority', async () => {
    const http: ReviewHttp = {
      ...createHttp(),
      getActivity: vi.fn(async (search: ActivitySearch) => activity({ ledger: { event_type: search.eventType, page: search.page, page_count: 3, page_size: search.pageSize, query: search.q ?? null, range_days: search.rangeDays, total: 75 } })),
    }
    const user = userEvent.setup()
    const { router } = renderRoute('/activity?q=first&page=1', http)

    await screen.findByRole('heading', { name: 'Activity and recovery' })
    await router.navigate({ to: '/activity', search: { eventType: 'all', q: 'second', rangeDays: 30, page: 2, pageSize: 25 } })
    await waitFor(() => expect(screen.getByRole('searchbox', { name: 'Search activity' })).toHaveValue('second'))
    await waitFor(() => expect(screen.getByRole('spinbutton', { name: 'Activity page number' })).toHaveValue(2))

    const editingSearch = screen.getByRole('searchbox', { name: 'Search activity' })
    await user.clear(editingSearch)
    await user.type(editingSearch, 'editing')
    act(() => router.history.back())
    await waitFor(() => expect(screen.getByRole('searchbox', { name: 'Search activity' })).toHaveValue('first'))
    await waitFor(() => expect(screen.getByRole('spinbutton', { name: 'Activity page number' })).toHaveValue(1))

    act(() => router.history.forward())
    await waitFor(() => expect(screen.getByRole('searchbox', { name: 'Search activity' })).toHaveValue('second'))
    await waitFor(() => expect(screen.getByRole('spinbutton', { name: 'Activity page number' })).toHaveValue(2))

    await router.navigate({ to: '/activity', search: { eventType: 'all', q: 'second', rangeDays: 30, page: 3, pageSize: 25 } })
    await waitFor(() => expect(screen.getByRole('spinbutton', { name: 'Activity page number' })).toHaveValue(3))
    act(() => router.history.back())
    await waitFor(() => expect(screen.getByRole('searchbox', { name: 'Search activity' })).toHaveValue('second'))
    await waitFor(() => expect(screen.getByRole('spinbutton', { name: 'Activity page number' })).toHaveValue(2))

    fireEvent.popState(window)
    expect(screen.getByRole('searchbox', { name: 'Search activity' })).toHaveValue('second')
    expect(screen.getByRole('spinbutton', { name: 'Activity page number' })).toHaveValue(2)
  })

  it('gives a traversal target authority over an abandoned local page identity', async () => {
    const deferred = new Map<number, (value: ActivityView) => void>()
    const getActivity = vi.fn((search: ActivitySearch) => {
      if (search.page === 1) return Promise.resolve(activity({ ledger: { event_type: search.eventType, page: 1, page_count: 3, page_size: search.pageSize, query: search.q ?? null, range_days: search.rangeDays, total: 75 } }))
      return new Promise<ActivityView>((resolve) => { deferred.set(search.page, resolve) })
    })
    const http: ReviewHttp = {
      ...createHttp(),
      getActivity,
    }
    const user = userEvent.setup()
    const { router } = renderRoute('/activity?q=canonical&page=1', http)

    await screen.findByRole('heading', { name: 'Activity and recovery' })
    await user.click(screen.getByRole('button', { name: 'Next' }))
    await waitFor(() => expect(deferred.has(2)).toBe(true))
    await user.click(screen.getByRole('button', { name: 'Next' }))
    await waitFor(() => expect(deferred.has(3)).toBe(true))
    act(() => { deferred.get(3)?.(activity({ ledger: { event_type: 'all', page: 3, page_count: 3, page_size: 25, query: 'canonical', range_days: 30, total: 75 } })) })
    await waitFor(() => expect(screen.getByRole('spinbutton', { name: 'Activity page number' })).toHaveValue(3))

    vi.useFakeTimers()
    try {
      fireEvent.change(screen.getByRole('searchbox', { name: 'Search activity' }), { target: { value: 'draft' } })
      act(() => router.history.back())
      await act(async () => { await vi.advanceTimersByTimeAsync(151) })
      expect(getActivity.mock.calls.some(([search]) => search.q === 'draft')).toBe(false)
      expect(router.state.location.search).toMatchObject({ q: 'canonical', page: 2 })
      act(() => { deferred.get(2)?.(activity({ ledger: { event_type: 'all', page: 2, page_count: 3, page_size: 25, query: 'canonical', range_days: 30, total: 75 } })) })
      await act(async () => { await Promise.resolve() })
      expect(screen.getByRole('searchbox', { name: 'Search activity' })).toHaveValue('canonical')
      expect(screen.getByRole('spinbutton', { name: 'Activity page number' })).toHaveValue(2)
      expect(router.state.location.search).toMatchObject({ q: 'canonical', page: 2 })
    } finally {
      vi.useRealTimers()
    }
  })

  it('keeps an unknown direct entry inside a safe shell fallback', async () => {
    renderRoute('/not-a-review-route')

    expect(await screen.findByRole('heading', { name: 'This review route does not exist.' })).toBeVisible()
    expect(screen.getByRole('link', { name: 'Open review library' })).toHaveAttribute('href', '/library?lens=all')
  })
})
