import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { RouterProvider, createMemoryHistory } from '@tanstack/react-router'
import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { AmbiguousMutationError, ApiError } from '../src/api/http'
import type { ActivityDisplayView, ActivityView, AttentionView, CaseDetailView, LibrarySearch, LibraryView, PolicyStateView, PreparationView, ReviewHttp, ShadowPostureView, ShadowSampleView } from '../src/api/http'
import { createApplicationRouter, normalizeLibrarySearch, normalizePolicySearch } from '../src/router'
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
  getPreparation: vi.fn(async (): Promise<PreparationView> => ({ schema_version: 'review-preparation-v1', active: false, actionable: false, paused: false, libraries: [] })),
  preparationAction: vi.fn(async (): Promise<PreparationView> => ({ schema_version: 'review-preparation-v1', active: false, actionable: false, paused: false, libraries: [] })),
  recordDecision: vi.fn(async () => ({ schema_version: 'review-decision-v1', actor: 'owner', decision_id: 'decision-1', destination: 'title', occurred_at: 1, outcome: 'keep_current', proposal_id: 'proposal-1', review_case_id: 'case-1', selected_value: null, target_fingerprint: 'fingerprint-1' })),
  returnToAutomatic: vi.fn(async () => undefined),
  getPolicyState: vi.fn(async (): Promise<PolicyStateView> => ({ schema_version: 'metadata-policy-notebook-v1', policy_id: 'policy-1', profile: { schema_version: 'metadata-policy-profile-v1', name: 'Default', options: {}, destinations: {} } })),
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

  it('loads the critical library query and progressively reveals detail', async () => {
    const user = userEvent.setup()
    const http: ReviewHttp = { ...createHttp(), getCaseDetail: vi.fn(async () => ({ ...detail, title: 'item-1', subtitle: 'internal identity' })) }
    renderRoute('/library?lens=all&q=title', http)

    expect(await screen.findByRole('heading', { name: 'Every work, with exceptions first' })).toBeVisible()
    expect(http.getLibrary).toHaveBeenCalledWith({ lens: 'all', q: 'title' }, expect.any(AbortSignal))
    await user.click(screen.getByRole('button', { name: 'Preview' }))
    expect(await screen.findByText('Progressive detail')).toBeVisible()
    expect(screen.getByRole('heading', { level: 2, name: 'A Book' })).toBeVisible()
    expect(screen.queryByText('item-1')).toBeNull()
    expect(screen.getByText('Author details unavailable · Books · ebook')).toBeVisible()
    expect(http.getCaseDetail).toHaveBeenCalledWith('case-1', expect.any(AbortSignal))
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
    await waitFor(() => expect(screen.getByRole('main')).toHaveFocus())
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
    await user.click(await screen.findByRole('link', { name: 'A Book' }))
    await waitFor(() => expect(http.getCaseDetail).toHaveBeenCalledTimes(3))

    expect(screen.getByText(status)).toHaveAttribute('role', 'alert')
    expect(screen.queryByRole('button', { name: /Choose current value/ })).toBeNull()
    expect(screen.queryByRole('button', { name: /Choose proposed value/ })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Try again' })).toBeNull()
    expect(screen.getAllByLabelText('Reviewer facts').at(0)).toHaveTextContent('Available choices0')
    await user.click(screen.getByRole('button', { name: 'authors' }))
    expect(screen.getAllByLabelText('Reviewer facts').at(1)).toHaveTextContent('Available choices0')

    await user.click(screen.getByRole('link', { name: 'Library' }))
    refreshAvailable = true
    await user.click(await screen.findByRole('link', { name: 'A Book' }))

    expect(await screen.findByText('Human decision: keep current by owner. Recorded value: Old title.')).toBeVisible()
    await user.click(screen.getByRole('button', { name: 'authors' }))
    expect(screen.getByRole('button', { name: 'Choose current value: Old title' })).toBeVisible()
    expect(screen.getByRole('button', { name: 'Choose proposed value 1: New title' })).toBeVisible()
    expect(screen.getAllByLabelText('Reviewer facts').at(1)).toHaveTextContent('Available choices2')
  })

  it('replaces invalid detail state with the resolved canonical field and subview', async () => {
    const { router } = renderRoute('/review/case-1?field=missing&subview=hostile')

    expect(await screen.findByRole('heading', { name: 'Current review case' })).toBeVisible()
    await waitFor(() => expect(router.state.location.search).toEqual({ field: 'title', subview: 'review' }))
  })

  it('presents Policy as a semantic notebook and preserves guarded activation currentness', async () => {
    const user = userEvent.setup()
    const options = {
      title_layout: 'title_and_subtitle', date_precision: 'year', language_rendering: 'display_name', contributor_names: 'canonical', publisher_preference: 'publisher', genres: 'catalogue', tags: 'catalogue', description_fill: 'fill_when_empty', isbn_form: 'isbn13', cover_order: 'fixed_source_order', asin: 'canonical_asin', explicit: 'unsupported',
    }
    const destinations = {
      title_layout: 'ask', date_precision: 'ask', language_rendering: 'ask', contributor_names: 'ask', publisher_preference: 'ask', genres: 'ask', tags: 'ignore', description_fill: 'ask', isbn_form: 'ask', cover_order: 'ask', asin: 'ask', explicit: 'unsupported',
    }
    let draftSaves = 0
    const policyCommand = vi.fn(async (path: string, body?: unknown): Promise<unknown> => {
      if (path === 'validate' && (body as { name?: string }).name === '') throw new ApiError(422, 'invalid_policy')
      if (path === 'drafts' && ++draftSaves > 1) throw new AmbiguousMutationError()
      if (path === 'drafts') return { policy_id: 'policy-draft', schema_version: 'metadata-policy-profile-v1' }
      if (path === 'preview') return { policy_id: 'policy-draft', schema_version: 'metadata-policy-preview-v1', libraries: [{ library: 'Hostile <script> library', affected_categories: ['title_layout', 'tags'], current_unapplied_count: 3, resolved_policy_id: 'resolved-policy' }] }
      if (path === 'activate') throw new ApiError(409, 'active_policy_changed')
      return { policy_id: 'policy-valid', schema_version: 'metadata-policy-profile-v1' }
    })
    const http: ReviewHttp = {
      ...createHttp(),
      getPolicyState: vi.fn(async (): Promise<PolicyStateView> => ({ schema_version: 'metadata-policy-notebook-v1', policy_id: 'opaque-policy-identity', profile: { schema_version: 'metadata-policy-profile-v1', name: 'Qualified default', options, destinations } })),
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
    expect(within(isbn).getByText(/Automatic is not qualified/)).toBeVisible()

    await user.clear(screen.getByRole('textbox', { name: 'Policy name' }))
    await user.click(screen.getByRole('button', { name: 'Check draft' }))
    expect(await screen.findByText('The draft is not valid. Review the highlighted policy intent and try again.')).toBeVisible()
    await user.type(screen.getByRole('textbox', { name: 'Policy name' }), 'Qualified draft')
    const titleLayout = screen.getByRole('group', { name: /Title and subtitle/ })
    await user.selectOptions(within(titleLayout).getByRole('combobox', { name: 'Title and subtitle representation' }), 'preserve')
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
    const http: ReviewHttp = { ...createHttp(), getActivity: vi.fn(async () => activity({
      backup_ready: false,
      configured_libraries: [{ connection: 'primary', library: 'fiction', prepared: { automatic: 1, human: 0, total: 1 } }],
      capabilities: [{ connection: 'primary', library: 'fiction', state: 'allowed', destinations: [{ destination: 'title', state: 'allowed', consented: false }] }],
      events: [{ work_id: 'book-1', library: 'fiction', destination: 'title', state: 'verified', origin: 'human', occurred_at: null, failure: null, work_label: 'Recorded work' }],
    })) }
    renderRoute('/activity', http)

    expect(await screen.findByText(/A verified backup is required/)).toBeVisible()
    expect(screen.getByRole('button', { name: 'Enable guarded writes' })).toBeDisabled()
    expect(screen.getByText('Recorded work')).toBeVisible()
  })

  it('returns a revoked library with history to consent readiness instead of a false pause control', async () => {
    const http: ReviewHttp = { ...createHttp(), getActivity: vi.fn(async () => activity({
      configured_libraries: [{ connection: 'primary', library: 'fiction', prepared: { automatic: 0, human: 0, total: 0 } }],
      capabilities: [{ connection: 'primary', library: 'fiction', state: 'allowed', destinations: [{ destination: 'title', state: 'allowed', consented: false }] }],
      events: [{ work_id: 'book-1', library: 'fiction', destination: 'title', state: 'verified', origin: 'human', occurred_at: null, failure: null, work_label: 'Recorded work' }],
    })) }
    renderRoute('/activity', http)

    expect(await screen.findByRole('heading', { name: 'Enable guarded writes' })).toBeVisible()
    expect(screen.getByText('Recorded work')).toBeVisible()
    expect(screen.getByRole('heading', { name: 'Completed work is up to date' })).toBeVisible()
    expect(screen.queryByRole('button', { name: /pause new work|resume new work/i })).toBeNull()
  })

  it('keeps an unknown direct entry inside a safe shell fallback', async () => {
    renderRoute('/not-a-review-route')

    expect(await screen.findByRole('heading', { name: 'This review route does not exist.' })).toBeVisible()
    expect(screen.getByRole('link', { name: 'Open review library' })).toHaveAttribute('href', '/library?lens=all')
  })
})
