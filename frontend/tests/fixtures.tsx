// Shared route-test fixtures: the synthetic views every route cohort renders,
// the fake `ReviewHttp` they compose, and the memory-history render helper.

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { RouterProvider, createMemoryHistory } from '@tanstack/react-router'
import { render, screen } from '@testing-library/react'
import type userEvent from '@testing-library/user-event'
import { vi } from 'vitest'

import type { ActivityDisplayView, ActivityView, CaseDetailView, LibraryView, PolicyStateView, PreparationView, ReviewHttp, ShadowPostureView, ShadowSampleView } from '../src/api/http'
import { createApplicationRouter } from '../src/router'

export const library: LibraryView = {
  lens: 'all',
  page: 1,
  counts: { attention: 1, all: 1, clear: 0 },
  works: [{
    id: 'case-1',
    reviewCaseId: 'case-1',
    workId: 'library_work_1',
    summary: null,
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

export const detail: CaseDetailView = {
  id: 'case-1',
  current: true,
  title: 'A Book',
  authors: [],
  subtitle: 'Books · local',
  comparisonNotices: [],
  writesConsented: false,
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

export const activity = (value: Partial<Omit<ActivityDisplayView, 'events'>> & { events?: readonly Partial<ActivityDisplayView['events'][number]>[] } = {}): ActivityView => {
  const { events = [], ...rest } = value
  return {
    schema_version: 'continuous-field-activity-v2', backup_ready: true, paused: false,
    configured_libraries: [], consented_libraries: [], enabled_libraries: [], capabilities: [],
    counts: {}, example: null, last_failure: null, current_waiting: null,
    waiting: { applying: 0, awaiting_permission: 0, held_for_recovery: 0, retrying: 0, scheduled: 0 },
    ledger: { event_type: 'all', page: 1, page_count: 1, page_size: 25, query: null, range_days: 30, total: events.length },
    prepared: { automatic: 0, human: 0, total: 0 }, suspensions: [], ...rest,
    events: events.map((event, index) => ({ event_id: `event-${String(index)}`, event_type: 'metadata', evidence: [], author_label: 'Author details are not recorded', destination: '', failure: null, library: '', occurred_at: null, origin: 'automatic_policy', state: 'verified', work_id: '', work_label: 'Recorded work', ...event })),
  }
}

export const createHttp = (): ReviewHttp => ({
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

export const renderRoute = (entry: string, http = createHttp()) => {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const router = createApplicationRouter(http, queryClient, createMemoryHistory({ initialEntries: [entry] }))
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  )
  return { http, queryClient, router }
}

export const openActivityAuthority = async (user: ReturnType<typeof userEvent.setup>) => {
  await user.click(await screen.findByText('Authority and recovery'))
}
