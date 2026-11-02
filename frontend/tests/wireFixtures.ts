// Base wire builders shared by the mapper tests and the fixture decode test,
// which proves each one is a complete server response (#1576). A test keeps
// every value it asserts as an override, and builds a nested response object
// from its own base rather than a partial literal.

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
