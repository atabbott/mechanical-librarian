// Shared route-test fixtures: the synthetic views every route cohort renders,
// the fake `ReviewHttp` they compose, and the memory-history render helper.

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { RouterProvider, createMemoryHistory } from '@tanstack/react-router'
import { render, screen } from '@testing-library/react'
import type userEvent from '@testing-library/user-event'
import { vi } from 'vitest'

import type {
  ActivityDisplayView,
  ActivityView,
  AppearanceView,
  CaseDetailView,
  LibraryView,
  PolicyStateView,
  PreparationView,
  ReviewHttp,
  ShadowPostureView,
  ShadowSampleView,
} from '../src/api/http'
import type { LiveStreamState, LiveUpdate, LiveUpdateStream } from '../src/liveUpdates'
import { createApplicationRouter } from '../src/router'

export const library: LibraryView = {
  lens: 'all',
  page: 1,
  counts: { attention: 1, all: 1, clear: 0 },
  works: [
    {
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
      detailUrl: '/review/case-1',
      detailKind: 'case' as const,
      preparation: null,
      preparationOutcomes: [],
    },
  ],
}

export const detail: CaseDetailView = {
  id: 'case-1',
  current: true,
  target: {
    connection: 'local',
    library: 'Books',
    item: 'internal-item-1',
    mediaKind: 'ebook',
    title: 'A Book',
  },
  title: 'A Book',
  authors: [],
  subtitle: 'Books · ebook',
  comparisonNotices: [],
  writesConsented: false,
  fields: [
    {
      classification: 'needs_attention',
      classificationReason: 'This field needs your review.',
      destination: 'title',
      decisionActions: ['keep_current', 'accept'],
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
      automaticWithheldReason: null,
      notices: [],
      provenance: [],
      preservationOnly: false,
      corroborationSources: [],
    },
  ],
}

export const activity = (
  value: Partial<Omit<ActivityDisplayView, 'events'>> & {
    events?: readonly Partial<ActivityDisplayView['events'][number]>[]
  } = {},
): ActivityView => {
  const { events = [], ...rest } = value
  return {
    schema_version: 'continuous-field-activity-v2',
    backup_ready: true,
    paused: false,
    configured_libraries: [],
    consented_libraries: [],
    enabled_libraries: [],
    capabilities: [],
    counts: {},
    example: null,
    last_failure: null,
    current_waiting: null,
    waiting: {
      applying: 0,
      awaiting_permission: 0,
      held_for_recovery: 0,
      retrying: 0,
      scheduled: 0,
    },
    ledger: {
      event_type: 'all',
      page: 1,
      page_count: 1,
      page_size: 25,
      query: null,
      range_days: 30,
      total: events.length,
    },
    prepared: { automatic: 0, human: 0, total: 0 },
    suspensions: [],
    ...rest,
    // Captured from the real projection in tests/support/activity_seed.py rather
    // than written by hand. The previous defaults were the API schema's absent-field
    // fallbacks ('Recorded', 'Recorded field'), which no producer emits: the
    // application always derives field_label from the destination. That made a row
    // no backend can produce the baseline every component test was measured against.
    events: events.map((event, index) => {
      const eventId = event.event_id ?? `effect_event_${String(index).padStart(64, '0')}`
      return {
        event_id: eventId,
        event_type: 'review',
        category: 'owner',
        applicationReason: null,
        change: 'Not set \u2192 9780060512750',
        change_detail: 'Set ISBN from Not set to 9780060512750',
        field_label: 'ISBN',
        policy_changes: [],
        evidence: [
          { label: 'Previous value', value: 'Not set' },
          { label: 'New value', value: '9780060512750' },
          { label: 'Chosen by', value: 'Library owner' },
          { label: 'Confirmed in Audiobookshelf', value: 'Confirmed as 9780060512750' },
        ],
        author_label: 'Author details are not recorded',
        destination: 'isbn',
        failure: null,
        library: 'books',
        occurred_at: 1785742465,
        origin: 'human',
        state: 'verified',
        work_id: 'book-1',
        work_label: 'Paper Moons: Larkspur',
        ...event,
        change_id: event.change_id ?? eventId,
      }
    }),
  }
}

export const createHttp = (): ReviewHttp => ({
  getReportDetail: vi.fn(async () => {
    throw new Error('not configured')
  }),
  getAppearance: vi.fn(async (): Promise<AppearanceView> => ({ reduced_motion: 'follow_system' })),
  getSettings: vi.fn(async () => {
    throw new Error('not configured')
  }),
  writeSettings: vi.fn(async () => {
    throw new Error('not configured')
  }),
  getReviewerAccess: vi.fn(async () => {
    throw new Error('not configured')
  }),
  refreshOpenRouter: vi.fn(async () => {
    throw new Error('not configured')
  }),
  describeContainedDescription: vi.fn(async () => {
    throw new Error('unavailable')
  }),
  testDescriptionModel: vi.fn(async () => {
    throw new Error('not configured')
  }),
  configureOpenRouter: vi.fn(async () => {
    throw new Error('not configured')
  }),
  clearOpenRouter: vi.fn(async () => {
    throw new Error('not configured')
  }),
  selectReviewer: vi.fn(async () => {
    throw new Error('not configured')
  }),
  startCodexDevice: vi.fn(async () => {
    throw new Error('not configured')
  }),
  getCodexDevice: vi.fn(async () => {
    throw new Error('not configured')
  }),
  recheckCodex: vi.fn(async () => ({
    state: 'usable' as const,
    failure: null,
    verification_url: null,
    user_code: null,
    expires_in_seconds: null,
  })),
  cancelCodexDevice: vi.fn(async () => {
    throw new Error('not configured')
  }),
  clearCodex: vi.fn(async () => {
    throw new Error('not configured')
  }),
  getLibrary: vi.fn(async () => library),
  getAttention: vi.fn(async () => ({ total: 0, items: [] })),
  getCaseDetail: vi.fn(async () => detail),
  getCaseSuccessor: vi.fn(async () => null),
  getEvidence: vi.fn(async () => ({ items: [], missingLineageCount: 0, sourceLedger: [] })),
  getHistory: vi.fn(async () => ({ items: [] })),
  retryCoverConsideration: vi.fn(async () => {
    throw new Error('not configured')
  }),
  getPreparation: vi.fn(async (): Promise<PreparationView> => ({
    schema_version: 'review-preparation-v2',
    active: false,
    actionable: false,
    paused: false,
    libraries: [],
  })),
  preparationAction: vi.fn(async (): Promise<PreparationView> => ({
    schema_version: 'review-preparation-v2',
    active: false,
    actionable: false,
    paused: false,
    libraries: [],
  })),
  dismissPreparation: vi.fn(async (): Promise<PreparationView> => ({
    schema_version: 'review-preparation-v2',
    active: false,
    actionable: false,
    paused: false,
    libraries: [],
  })),
  recordDecision: vi.fn(async () => ({
    invalidation_sequences: [],
    schema_version: 'review-decision-v1',
    actor: 'owner',
    application: 'apply' as const,
    decision_id: 'decision-1',
    destination: 'title',
    occurred_at: 1,
    outcome: 'keep_current',
    proposal_id: 'proposal-1',
    review_case_id: 'case-1',
    selected_value: null,
    target_fingerprint: 'fingerprint-1',
  })),
  retryDescriptionAdvice: vi.fn(async () => {
    throw new Error('not configured')
  }),
  returnToAutomatic: vi.fn(async () => undefined),
  getPolicyState: vi.fn(async (): Promise<PolicyStateView> => ({
    schema_version: 'metadata-policy-notebook-v1',
    policy_id: 'policy-1',
    profile: {
      schema_version: 'metadata-policy-profile-v2',
      name: 'Default',
      options: {},
      destinations: {},
    },
    rich_qualification: [],
  })),
  getShadowPosture: vi.fn(async (): Promise<ShadowPostureView> => ({
    schema_version: 'shadow-posture-v1',
    posture: 'review',
    authority: 'none',
    can_leave_preview: false,
  })),
  getAutomaticSafety: vi.fn(async () => ({
    paused: false,
    global_suspension: null,
    category_suspensions: {},
  })),
  getShadowSample: vi.fn(async (): Promise<ShadowSampleView> => ({
    schema_version: 'shadow-outcome-sample-v1',
    authority: 'none',
    items: [],
    next_cursor: null,
  })),
  getPolicyTransition: vi.fn(async () => null),
  policyCommand: async <T,>() => ({}) as T,
  getActivity: vi.fn(async () => activity()),
  getActivityTransitions: vi.fn(async () => ({ events: [], has_more: false })),
  activityCommand: vi.fn(async () => undefined),
  logout: vi.fn(async () => undefined),
})

/**
 * A stream the test drives by hand, with the shape the shipped bridge sees.
 * `announce` delivers one frame exactly as the server's would, so a test can say
 * what changed, for whom, and at which position, without a server.
 */
export const createLiveStream = (): LiveUpdateStream & {
  announce: (update: LiveUpdate) => void
  setState: (state: LiveStreamState) => void
} => {
  const listeners = new Set<(update: LiveUpdate) => void>()
  const stateListeners = new Set<(state: LiveStreamState) => void>()
  let state: LiveStreamState = 'live'
  let hasBeenLive = true
  return {
    get state() {
      return state
    },
    get hasBeenLive() {
      return hasBeenLive
    },
    subscribe: (listener) => {
      listeners.add(listener)
      return () => {
        listeners.delete(listener)
      }
    },
    observeState: (listener) => {
      stateListeners.add(listener)
      return () => stateListeners.delete(listener)
    },
    close: () => undefined,
    announce: (update) => {
      for (const listener of [...listeners]) listener(update)
    },
    setState: (next) => {
      state = next
      if (next === 'live') hasBeenLive = true
      for (const listener of [...stateListeners]) listener(state)
    },
  }
}

export const renderRoute = (entry: string, http = createHttp(), updates?: LiveUpdateStream) => {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const router = createApplicationRouter(
    http,
    queryClient,
    createMemoryHistory({ initialEntries: [entry] }),
    updates,
  )
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
