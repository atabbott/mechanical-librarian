// Base wire builders shared by the mapper tests and the fixture decode test,
// which proves each one is a complete server response (#1576). A test keeps
// every value it asserts as an override, and builds a nested response object
// from its own base rather than a partial literal.

import attentionCapture from './wire/get-attention.200.json'
import policyStateCapture from './wire/get-metadata-policy-state.200.json'
import policyPreviewCapture from './wire/post-metadata-policy-preview.200.json'

type Overrides = Record<string, unknown>

// `GET /api/v1/library/report` 200.
export const reportDetail = (overrides: Overrides = {}) => ({
  chapters: [],
  chapters_truncated: false,
  connection: 'primary',
  embedded_cover: { present: false, surface: null },
  evidence: [],
  evidence_truncated: false,
  item: 'item-9',
  library: 'audio',
  library_display_name: 'Audiobooks',
  media_health: [
    {
      bound: 'The declared track sequence runs without a hole.',
      cited: ['track 1 declares sequence 1', 'track 2 declares sequence 3'],
      expected: null,
      kind: 'track_sequence_gap',
      observed: null,
      tracks: [1, 2],
    },
  ],
  media_kind: 'm4b',
  provider_sources: [],
  provider_evidence: [],
  provider_evidence_truncated: false,
  report_id: `report_${'a'.repeat(64)}`,
  schema_version: 'review-report-detail-v5',
  audiobook_review_scope: null,
  recording_identity_basis: null,
  title: 'Recorded work',
  ...overrides,
})

// `_ChoiceContext`: the current-value context every field and cover leads with.
export const choiceContext = (overrides: Overrides = {}) => ({
  alternative_id: null,
  evidence: [],
  limitations: [],
  rationale: '',
  recommendation: 'no_recommendation',
  recommendation_reason: '',
  selection: 'current',
  ...overrides,
})

// `_TargetIdentity`.
export const targetIdentity = (overrides: Overrides = {}) => ({
  authors: ['Ada Example'],
  connection: 'primary',
  item: 'internal-item-1',
  library: 'Books',
  library_display_name: null,
  media_kind: 'epub',
  title: 'A Book',
  ...overrides,
})

// `_TargetField`: the current value of `reviewField()`'s destination.
export const targetField = (overrides: Overrides = {}) => ({
  destination: 'title',
  state: 'value',
  value: 'A Book',
  ...overrides,
})

// `_ReviewField`.
export const reviewField = (overrides: Overrides = {}) => ({
  automatic_decision: null,
  automatic_selection_sources: [],
  automatic_withheld_reason: null,
  choice_contexts: [choiceContext()],
  classification: 'needs_attention',
  classification_reason: 'This field needs your review.',
  corroboration_sources: [],
  cover_consideration: null,
  current: targetField(),
  current_cover_selection_artifact_id: null,
  current_cover_selection_id: null,
  decision_actions: [],
  description_advice: null,
  destination: 'title',
  deterministic_disposition: 'propose_change',
  editable: true,
  effect_capability: 'unavailable',
  human_decision: null,
  inventory_role: 'primary',
  manual_hold: false,
  manual_required: false,
  navigation_role: 'attention',
  needs_attention: true,
  notices: [],
  owner_control: null,
  preservation_only: false,
  proposal_id: 'proposal-1',
  proposed_values: [],
  provenance: [],
  target_fingerprint: 'fingerprint-1',
  uncertainty: [],
  ...overrides,
})

// `GET /api/v1/review-cases/{review_case_id}` 200.
export const caseDetail = (overrides: Overrides = {}) => ({
  audiobook_review_scope: null,
  comparison_notices: [],
  current: true,
  fields: [reviewField()],
  media_health: [],
  recording_identity_basis: null,
  review_case_id: 'case-1',
  schema_version: 'review-case-detail-v6',
  target: targetIdentity(),
  writes_consented: false,
  ...overrides,
})

// `_CoverIdentity` of `coverCandidate()`.
export const coverIdentity = (overrides: Overrides = {}) => ({
  candidate_reference: 'candidate-1',
  evidence_references: [],
  outcome: 'verified_work_match',
  preview_reference: 'cover-preview-candidate-1',
  reasons: [],
  ...overrides,
})

// `_CoverCandidate`: a confirmed, ranked and acceptable alternative.
export const coverCandidate = (overrides: Overrides = {}) => ({
  artifact_id: 'artifact-1',
  deterministic_citations: ['citation-1'],
  deterministic_rank: 1,
  deterministic_rank_key: [1, 0, -10000, -100, -100, 0, 'candidate-1'],
  height: 100,
  identity: coverIdentity(),
  incumbent: false,
  manual_eligible: true,
  match_state: 'same_book',
  observation: null,
  ordinal: 0,
  original_sha256: 'a'.repeat(64),
  preview_sha256: 'b'.repeat(64),
  reference: 'candidate-1',
  selectable: true,
  source_kinds: [],
  state: 'acceptable',
  transport_sha256: 'c'.repeat(64),
  width: 100,
  ...overrides,
})

// `_CoverPolicyReport` over `coverCandidate()`.
export const coverPolicyReport = (overrides: Overrides = {}) => ({
  acceptable_candidate_references: ['candidate-1'],
  actual_target_state: 'unknown',
  cover_packet_reference: 'cover-packet-synthetic',
  identity_classifier_reference: 'cover-identity-synthetic',
  identity_report_sha256: 'd'.repeat(64),
  observation_report_sha256: null,
  observation_taxonomy_reference: 'cover-taxonomy-synthetic',
  owner_ready: true,
  policy_reference: 'cover-policy-synthetic',
  reasons: [],
  recommended_candidate_reference: null,
  schema_version: 'cover-policy-report-v2',
  status: 'abstained',
  ...overrides,
})

// `POST /api/v1/review-cases/{review_case_id}/cover-considerations/retry` 200:
// a consideration with no candidates and no reviewer.
export const coverConsideration = (overrides: Overrides = {}) => ({
  candidates: [],
  choice_contexts: [choiceContext()],
  consideration_id: 'consideration',
  current: true,
  incumbent: { reference: null, state: 'absent' },
  omissions: [],
  policy: null,
  policy_preferences: {
    avoid_promotional_text: false,
    avoid_tie_in_branding: false,
    prefer_minimal_composition: false,
    schema_version: 'cover-policy-v1',
  },
  policy_reference: 'cover-policy-synthetic',
  provider_fetching: false,
  ready: false,
  reviewer_access_revision: null,
  reviewer_engine: null,
  reviewer_failure_code: null,
  reviewer_model: null,
  reviewer_status: 'unavailable',
  reviewer_unavailable_reason: null,
  schema_version: 'cover-consideration-v4',
  supersedes_id: null,
  target_fingerprint: 'fingerprint-1',
  ...overrides,
})

// `_DecisionProjection`: the owner's recorded cover decision.
export const decisionProjection = (overrides: Overrides = {}) => ({
  actor: 'owner',
  application: 'apply',
  application_reason: null,
  decision_id: 'decision-1',
  destination: 'cover',
  occurred_at: 3,
  outcome: 'keep_current',
  proposal_id: 'proposal-1',
  review_case_id: 'case-1',
  selected_value: null,
  target_fingerprint: 'fingerprint-1',
  ...overrides,
})

// `_CoverAutomaticDecisionProjection`.
export const coverAutomaticDecision = (overrides: Overrides = {}) => ({
  candidate_id: 'candidate-1',
  decision_id: 'automatic-1',
  destination: 'cover',
  occurred_at: 3,
  outcome: 'candidate',
  proposal_id: 'proposal-1',
  review_case_id: 'case-1',
  selected_value: null,
  supersedes_decision_id: null,
  target_fingerprint: 'fingerprint-1',
  ...overrides,
})

// `_HistoryDecisionItem`.
export const historyDecisionItem = (overrides: Overrides = {}) => ({
  actor: 'owner',
  application: 'apply',
  application_reason: null,
  decision_id: 'decision-1',
  destination: 'title',
  event_id: 'event-1',
  kind: 'human_decision_recorded',
  occurred_at: 2,
  outcome: 'accept',
  previous_value: null,
  review_case_id: 'case-1',
  schema_version: 'human-decision-event-v1',
  selected_value: null,
  ...overrides,
})

// `_HistoryCoverItem`.
export const historyCoverItem = (overrides: Overrides = {}) => ({
  consideration: coverConsideration(),
  consideration_id: 'consideration',
  decision: null,
  decision_id: null,
  kind: 'no_action',
  occurred_at: 3,
  review_case_id: 'case-1',
  schema_version: 'cover-consideration-event-v1',
  ...overrides,
})

// `_HistoryShadowItem`.
export const historyShadowItem = (overrides: Overrides = {}) => ({
  authority: 'none',
  dispositions: {},
  kind: 'shadow_preview_recorded',
  occurred_at: 4,
  origin: 'policy_preview',
  outcome_set_id: 'outcome-set-1',
  review_case_id: 'case-1',
  schema_version: 'shadow-preview-history-event-v1',
  ...overrides,
})

// `GET /api/v1/review-cases/{review_case_id}/history` 200.
export const caseHistory = (overrides: Overrides = {}) => ({
  items: [],
  next_cursor: null,
  review_case_id: 'case-1',
  schema_version: 'review-case-history-v3',
  ...overrides,
})

// `POST /api/v1/review-cases/{review_case_id}/decisions` 200: the owner's
// decision, confirmed with the case it leaves.
export const decisionAcknowledgement = (overrides: Overrides = {}) => ({
  ...decisionProjection({ destination: 'title' }),
  confirmation: { case: caseDetail(), status: 'confirmed' },
  invalidation_sequences: [],
  schema_version: 'review-decision-v1',
  ...overrides,
})

// `_PreparationProgress`, which `POST /api/v1/review-preparation/{connection}/{library_name}/scan`
// acknowledges with 202: a queued scan of one library.
export const scanProgress = (overrides: Overrides = {}) => ({
  blocked: 0,
  checked: 0,
  connection: 'primary',
  library: 'Books',
  library_id: 'books',
  not_matched: 0,
  ready: 0,
  recorded: 0,
  rescan_requested: false,
  scan_id: 'scan-1',
  stages: [],
  state: 'queued',
  total: 0,
  ...overrides,
})

// `_PreparationPresentation`: a running pass over four works, none in flight.
export const preparationPresentation = (overrides: Overrides = {}) => ({
  active_books: [],
  active_overflow: 0,
  completed_at: null,
  dismissible: false,
  now: null,
  passage: { read: 4, searches_finished: 3, compared: 2, checked: 2, ready: 1, failed: 1 },
  recent_completions: [],
  run_id: `preparation_run_${'a'.repeat(64)}`,
  started_at: 100,
  state: 'running',
  throughput_per_minute: 0,
  total: 4,
  triggers: [
    { trigger: 'manual_scan', accepted_count: 1, first_accepted_at: 100, last_accepted_at: 100 },
  ],
  updated_at: 101,
  ...overrides,
})

// `GET /api/v1/review-preparation` 200, which the preparation controls also
// return: idle, with nothing configured.
export const preparation = (overrides: Overrides = {}) => ({
  actionable: false,
  active: false,
  connections: [],
  first_boot: false,
  first_boot_complete: false,
  first_boot_works: null,
  libraries: [],
  paused: false,
  presentation: null,
  provider_throttling: null,
  recovery_required: false,
  schema_version: 'review-preparation-v2',
  ...overrides,
})

// `_LibraryWork`: a catalogue work with no review case, cover or summary.
export const libraryWork = (overrides: Overrides = {}) => ({
  attention_count: 0,
  attention_state: 'no_attention_needed',
  authors: [],
  cover: null,
  detail_kind: 'report',
  detail_url: '/api/v1/library/report?item=item-1',
  dispositions: [],
  item: 'item-1',
  library: 'Books',
  library_display_name: null,
  media_kind: 'epub',
  preparation: null,
  preparation_outcomes: [],
  review_case_id: null,
  summary: null,
  title: 'A Book',
  work_id: 'library_work_1',
  ...overrides,
})

// `GET /api/v1/library` 200: the first and only page of the All lens.
export const library = (overrides: Overrides = {}) => ({
  counts: {
    all: 0,
    needs_attention: 0,
    no_attention_needed: 0,
    not_matched: 0,
    preparation_failed: 0,
  },
  items: [],
  lens: 'all',
  next_cursor: null,
  page: 1,
  previous_cursor: null,
  query: null,
  schema_version: 'review-library-v4',
  ...overrides,
})

// `_ActivityEvent`: an owner's recorded review of one field.
export const activityEvent = (overrides: Overrides = {}) => ({
  author_label: 'Author details are not recorded',
  category: 'owner',
  change: 'Recorded',
  change_detail: 'Recorded',
  change_id: 'change-1',
  check_group: null,
  destination: 'title',
  diagnostic: null,
  event_id: 'event-1',
  event_type: 'review',
  evidence: [],
  failure: null,
  field_label: 'Title',
  library: 'Fiction',
  occurred_at: 1,
  origin: 'human',
  policy_changes: [],
  state: 'verified',
  transition_id: null,
  work_id: 'work-1',
  work_label: 'Recorded work',
  ...overrides,
})

// `GET /api/v1/activity` 200: nothing configured, waiting or recorded.
export const activityResponse = (overrides: Overrides = {}) => ({
  backup_ready: null,
  capabilities: [],
  configured_libraries: [],
  consented_libraries: [],
  counts: {},
  current_waiting: null,
  enabled_libraries: [],
  events: [],
  example: null,
  lapsed_consents: [],
  last_failure: null,
  ledger: {
    event_type: 'all',
    page: 1,
    page_count: 1,
    page_size: 25,
    query: null,
    range_days: 30,
    total: 0,
  },
  owner_action_required: 0,
  paused: false,
  prepared: { automatic: 0, human: 0, total: 0 },
  provider_recovery: [],
  schema_version: 'continuous-field-activity-v3',
  suspensions: [],
  waiting: { applying: 0, awaiting_permission: 0, held_for_recovery: 0, retrying: 0, scheduled: 0 },
  ...overrides,
})

// The capture-backed bases copy their capture, so an override never edits it.

// `GET /api/v1/attention` 200.
export const attention = (overrides: Overrides = {}) => ({
  ...structuredClone(attentionCapture.body),
  ...overrides,
})

// `_AttentionItem`: the attention capture's first item.
export const attentionItem = (overrides: Overrides = {}) => ({
  ...structuredClone(attentionCapture.body.items[0]),
  ...overrides,
})

// `POST /api/v1/metadata-policy/validate` 200, which returns the same profile
// document as the captured policy state.
export const policyProfile = (overrides: Overrides = {}) => ({
  ...structuredClone(policyStateCapture.body),
  ...overrides,
})

// `POST /api/v1/metadata-policy/preview` 200.
export const policyPreview = (overrides: Overrides = {}) => ({
  ...structuredClone(policyPreviewCapture.body),
  ...overrides,
})
