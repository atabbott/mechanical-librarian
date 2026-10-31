export type ReadWorkClass = 'foreground' | 'background'

import type { components } from '../generated/review-api'
import settingsVocabulary from '../../../src/mechanical_librarian/interfaces/web/settings_vocabulary.json'

type Schema<Name extends keyof components['schemas']> = components['schemas'][Name]
type LibraryDto = Schema<'_LibraryResponse'>
type AttentionDto = Schema<'_AttentionResponse'>
type CaseDetailDto = Schema<'_CaseDetailResponse'>
export type SettingsView = Schema<'SettingsView'>
export type SettingsWrite = Schema<'_SettingsWriteRequest'>
export type AppearanceView = Schema<'_AppearanceResponse'>
// The closed OpenRouter failures an owner may see, each with its own remedy.
const reviewerFailureCodes = [
  'invalid_key',
  'quota_exhausted',
  'rate_limited',
  'model_unavailable',
  'model_text_only',
  'malformed_response',
  'unreachable',
  'response_too_large',
  'timed_out',
  'data_policy_refused',
  'image_limit_exceeded',
] as const
export type ReviewerFailureCode = (typeof reviewerFailureCodes)[number]
export type ReviewerState =
  | 'ready'
  | 'configured'
  | 'starting'
  | 'issued'
  | 'waiting'
  | 'validating'
  | 'usable'
  | 'expired'
  | 'cancelled'
  | 'failed'
  | 'interrupted'
  | 'host_unsupported'
  | 'not_provisioned'
  | 'unconfigured'
  | ReviewerFailureCode
export type ReviewerReason =
  | 'profile_not_provisioned'
  | 'profile_unreadable'
  | 'profile_contract_mismatch'
  | 'host_evidence_unavailable'
  | 'cgroup_limit_below_1_gib'
  | 'headroom_below_1_gib'
  | 'permission_or_profile'
  | 'permission_or_process'
  | 'protocol_or_process'
  | 'restart_interrupted'
  | 'not_selected'
  | ReviewerFailureCode
export interface ReviewerEngineStatus {
  engine: 'codex' | 'openrouter'
  state: ReviewerState
  reason: ReviewerReason | null
  model: string | null
  tier: 'free' | 'paid' | null
  host_headroom_bytes: number | null
}
export type DescriptionModelMetadata = Schema<'DescriptionModelMetadata'>
export type ReviewerTask = 'cover_observation' | 'description_rewrite'
export interface ReviewerAccessView {
  schema_version: 'reviewer-access-view-v2'
  revision: number
  mapping: { engine: 'codex' } | { engine: 'openrouter'; model: string } | null
  description_mapping: ReviewerAccessView['mapping']
  description_metadata: DescriptionModelMetadata | null
  cover_metadata: Schema<'CoverModelMetadata'> | null
  cost_evidence: Schema<'ReviewerCostEvidence'>
  description: ReviewerEngineStatus | null
  openrouter_key_configured: boolean
  codex: ReviewerEngineStatus
  openrouter: ReviewerEngineStatus
  recommendations: readonly string[]
  candidates: readonly {
    model: string
    tier: 'free' | 'paid'
    qualified: false
    evidence: 'catalogue_capability_only'
    requests_per_minute: number | null
    requests_per_day: string | null
  }[]
  free_tier_caveat: string
  data_use_caveat: string
}
export interface CodexDeviceAuthState {
  state:
    | 'ready'
    | 'configured'
    | 'starting'
    | 'issued'
    | 'waiting'
    | 'validating'
    | 'usable'
    | 'expired'
    | 'cancelled'
    | 'failed'
    | 'interrupted'
  verification_url: string | null
  user_code: string | null
  expires_in_seconds: number | null
  failure:
    | 'permission_or_profile'
    | 'permission_or_process'
    | 'protocol_or_process'
    | 'restart_interrupted'
    | null
}
export interface CodexDeviceStartResult {
  access: ReviewerAccessView
  device: CodexDeviceAuthState
}

// These validators live at the DTO mapping boundary so the editor cannot
// silently become stricter than the generated API contract. Their names make
// the contract directly testable and let TypeScript keep every caller on the
// same exact input shape.
export const settingsSecretValid = (value: string) =>
  value.length >= settingsVocabulary.secret.minimum &&
  value.length <= settingsVocabulary.secret.maximum &&
  new RegExp(settingsVocabulary.secret.printable_ascii, 'u').test(value) &&
  !value.toLowerCase().startsWith(settingsVocabulary.secret.forbidden_prefix)

export const settingsIntegerValid = (value: number, minimum: number, maximum?: number) =>
  Number.isInteger(value) && value >= minimum && (maximum === undefined || value <= maximum)

export const settingsTextValid = (value: string, minimum: number, maximum: number) => {
  const codePoints = Array.from(value).length
  return codePoints >= minimum && codePoints <= maximum
}

export const settingsEndpointValid = (value: string) =>
  settingsTextValid(value, settingsVocabulary.endpoint.minimum, settingsVocabulary.endpoint.maximum)

const openLibraryEmail = new RegExp(settingsVocabulary.open_library_email.pattern)

export const settingsOpenLibraryEmailValid = (value: string) =>
  settingsTextValid(
    value,
    settingsVocabulary.open_library_email.minimum,
    settingsVocabulary.open_library_email.maximum,
  ) && openLibraryEmail.test(value)

export const settingsLocalLibraryNameValid = (value: string) =>
  settingsTextValid(
    value,
    settingsVocabulary.local_library_name.minimum,
    settingsVocabulary.local_library_name.maximum,
  ) && new RegExp(settingsVocabulary.local_library_name.pattern, 'u').test(value)

export const settingsProviderKeyRequired = (adapter: string) =>
  settingsVocabulary.required_provider_keys.includes(adapter)

// ADR-0061 admits the audiobook providers keyless: they have no credential to
// collect, so no credential control may be offered for one.
export const settingsProviderKeyless = (adapter: string) =>
  settingsVocabulary.keyless_provider_adapters.includes(adapter)

// The order an added Audible entry starts with. It restates the settings
// contract's own default so the browser never invents one; a unit test holds
// this list to the report vocabulary the contract is built from.
export const settingsAudibleMarketplaceOrder: readonly string[] =
  settingsVocabulary.audible_marketplace_order

export const settingsValidationCopy = settingsVocabulary.errors

// The lens a work belongs to; All lists every one of them (#1407).
export const attentionStates = ['needs_attention', 'no_attention_needed', 'not_matched'] as const
export type AttentionState = (typeof attentionStates)[number]
export type LibraryLens = 'all' | AttentionState
// Why a work with no case reads as it does: nothing to decide, a preparation
// that could not finish, or the reason no provider matched it (#1407).
export const workSummaryStates = [
  'no_decision_recorded',
  'preparation_failed',
  'not_matched_refused',
  'not_matched_unavailable_scheduled',
  'not_matched_unavailable',
  'not_matched_no_match',
] as const
export type WorkSummaryState = (typeof workSummaryStates)[number]
export type DetailSubview = 'evidence' | 'history'
export type PolicySection = 'identity' | 'publication' | 'curation' | 'unsupported'
export const DECISION_OUTCOMES = [
  'accept',
  'keep_current',
  'reject',
  'owner_entered',
  'withdraw',
] as const
export type DecisionOutcome = (typeof DECISION_OUTCOMES)[number]
export type DecisionAction = Extract<DecisionOutcome, 'accept' | 'keep_current'>

export interface LibrarySearch {
  lens: LibraryLens
  q?: string
  cursor?: string
  attentionCursor?: string
  preview?: string
  work?: string
  field?: string
  attention?: 'open'
  preparation?: string
}
export interface AttentionSearch {
  q?: string
  cursor?: string
}
export interface DetailSearch {
  q?: string
  cursor?: string
  attentionCursor?: string
  attention?: 'open'
  preparation?: string
  field?: string
  lens: LibraryLens
  subview: DetailSubview
}
export interface PolicySearch {
  section: PolicySection
}

export interface ProviderOutcomeView {
  providerRequests: number
  laneKind: ProviderQueryKind
  provider: string
  reason: string
  status: ProviderStatus
}
// Every statement this build can make about an item's media. The set is
// closed on the wire too, so a view switches on it exhaustively and a value
// outside it is a server this client does not understand rather than a row it
// renders blank; `_MediaHealthObservation` in the FastAPI adapter carries the
// same thirteen values.
export const MEDIA_HEALTH_KINDS = [
  'runtime_short_of_provider',
  'chapter_structure_mismatch',
  'track_sequence_gap',
  'track_sequence_disorder',
  'track_duration_unavailable',
  'chapter_atoms_partial',
  'tag_disagrees_across_tracks',
  'asin_disagrees_across_tracks',
  'container_unexpected',
  'codec_unexpected',
  'duration_unstated',
  'stream_encrypted',
  'media_unreadable',
] as const
export type MediaHealthKind = (typeof MEDIA_HEALTH_KINDS)[number]
// The closed code sets an owner surface names. Each tuple restates its
// producer's StrEnum or literal set, is checked where its mapper reads it, and
// keys a label map that `satisfies` a Record over it, so a code the server can
// send and the browser cannot name fails the type check rather than reaching
// the owner as a raw code (#1286).
export const TARGET_DESTINATIONS = [
  'title',
  'subtitle',
  'authors',
  'narrators',
  'series',
  'genres',
  'publishedYear',
  'publishedDate',
  'publisher',
  'description',
  'isbn',
  'asin',
  'language',
  'explicit',
  'tags',
  'cover',
] as const
export type TargetDestination = (typeof TARGET_DESTINATIONS)[number]
export const TARGET_FIELD_STATES = [
  'value',
  'missing',
  'null',
  'empty',
  'unloaded',
  'malformed',
  'oversized',
] as const
export type TargetFieldState = (typeof TARGET_FIELD_STATES)[number]
// A file format, or for a work whose preparation stopped before its file was
// read, `audio` (an audiobook) or `unknown`.
export const MEDIA_KINDS = ['epub', 'm4b', 'm4a', 'mp3', 'audio', 'unknown'] as const
export type MediaKind = (typeof MEDIA_KINDS)[number]
export const PROVIDER_QUERY_KINDS = ['isbn', 'work', 'asin', 'discovery', 'none'] as const
export type ProviderQueryKind = (typeof PROVIDER_QUERY_KINDS)[number]
export const PROVIDER_STATUSES = [
  'disabled',
  'insufficient_input',
  'candidates',
  'no_match',
  'authentication',
  'authorization',
  'rate_limited',
  'unavailable',
  'incomplete',
  'malformed',
  'oversized',
  'candidate_limit_exceeded',
  'query_limit_exceeded',
] as const
export type ProviderStatus = (typeof PROVIDER_STATUSES)[number]
export const CLAIM_KINDS = [
  'title',
  'subtitle',
  'creator',
  'contributor',
  'series',
  'series_sequence',
  'isbn',
  'asin',
  'publication_date',
  'date_event',
  'contextual_credit',
  'publisher',
  'description',
  'language',
  'subject',
  'imprint',
  'genre',
  'tag',
  'duration',
  'performance_type',
  'adult_product',
] as const
export type ClaimKind = (typeof CLAIM_KINDS)[number]
export const CLAIM_BASES = [
  'opening_matter',
  'package_metadata',
  'target_metadata',
  'embedded_tag',
  'sidecar',
  'path',
  'prior_admission',
] as const
export type ClaimBasis = (typeof CLAIM_BASES)[number]
export const FIELD_STATES = [
  'valid',
  'absent',
  'malformed',
  'oversized',
  'unsupported',
  'untyped',
  'inapplicable',
] as const
export type FieldState = (typeof FIELD_STATES)[number]
export const AUDIO_SURFACES = [
  'container_format',
  'container_stream',
  'mp4_ilst',
  'mp4_freeform',
  'mp4_freeform_tone',
  'id3_frame',
  'id3_user_text',
  'sidecar_audiobookshelf',
  'sidecar_libation',
  'sidecar_opf',
  'sidecar_nfo',
  'sidecar_cue',
  'path_folder',
  'path_file',
] as const
export type AudioSurface = (typeof AUDIO_SURFACES)[number]
export const PREPARATION_FAILURE_STATUSES = [
  'provider',
  'configuration',
  'authentication',
  'stale',
  'transient',
  'projection',
  'failed',
  'currentness',
  'persistence',
  'unsupported_media',
  'unreadable_media',
  'parser_refused',
  'multi_file_budget_exceeded',
  'missing_required_input',
  'malformed_retained_data',
  'internal_failure',
  'unknown',
] as const
export type PreparationFailureStatus = (typeof PREPARATION_FAILURE_STATUSES)[number]
export const PREPARATION_FAILURE_STAGES = [
  'extraction',
  'provider',
  'proposal',
  'admission',
  'projection',
  'complete',
] as const
export type PreparationFailureStage = (typeof PREPARATION_FAILURE_STAGES)[number]
export const PROPOSAL_OUTCOMES = [
  'propose_change',
  'acceptable_choice',
  'unresolved_choice',
  'keep',
  'reject',
  'defer',
  'ineligible',
] as const
export type ProposalOutcome = (typeof PROPOSAL_OUTCOMES)[number]
export const AUTOMATIC_OUTCOMES = ['candidate', 'keep_current', 'ignore'] as const
export type AutomaticOutcome = (typeof AUTOMATIC_OUTCOMES)[number]
// Why writes to one destination, or a whole connection, are held.
export const SUSPENSION_REASONS = [
  'connection_unavailable',
  'insufficient_permission',
  'field_unavailable',
  'unexpected_effect_behavior',
] as const
export type SuspensionReason = (typeof SUSPENSION_REASONS)[number]
export const EFFECT_CAPABILITIES = ['allowed', 'unavailable', ...SUSPENSION_REASONS] as const
export type EffectCapability = (typeof EFFECT_CAPABILITIES)[number]
export const DESTINATION_STATES = ['allowed', 'not_ready', ...SUSPENSION_REASONS] as const
export type DestinationState = (typeof DESTINATION_STATES)[number]
export const CAPABILITY_STATES = [
  'allowed',
  'partially_allowed',
  'connection_unavailable',
  'not_ready',
] as const
export type CapabilityState = (typeof CAPABILITY_STATES)[number]
export const SCAN_STATES = [
  'idle',
  'queued',
  'discovering',
  'processing',
  'completed',
  'completed_with_failures',
] as const
export type ScanState = (typeof SCAN_STATES)[number]
export const SHADOW_DISPOSITIONS = [
  'abstention',
  'candidate',
  'conflict',
  'ignore',
  'keep_current',
  'manual',
  'unsupported',
] as const
export type ShadowDisposition = (typeof SHADOW_DISPOSITIONS)[number]
export const TRANSITION_OUTCOMES = [
  'admitted',
  'superseded',
  'no_case',
  'retired',
  'tombstoned',
] as const
export type TransitionOutcome = (typeof TRANSITION_OUTCOMES)[number]
export const COVER_HISTORY_OUTCOMES = [
  'automatic_prepared',
  'owner_retry_prepared',
  'superseded',
  'selected',
  'kept_incumbent',
  'no_action',
  'stale',
] as const
export type CoverHistoryOutcome = (typeof COVER_HISTORY_OUTCOMES)[number]
// A recorded description's lifecycle: its advice, then the guarded write.
export const DESCRIPTION_STATES = [
  'requested',
  'running',
  'succeeded',
  'failed',
  'interrupted',
  'planned',
  'claimed',
  'applying',
  'reconciling',
  'retryable',
  'verified',
  'stale',
  'superseded',
  'abandoned',
  'no_change_required',
] as const
export type DescriptionState = (typeof DESCRIPTION_STATES)[number]
export const COVER_SOURCE_KINDS = [
  'abs',
  'epub',
  'hardcover',
  'open_library',
  'inventaire',
  'audible_catalog',
  'itunes_search',
] as const
export type CoverSourceKind = (typeof COVER_SOURCE_KINDS)[number]
// One typed statement about an item's media, in the closed shape both the
// report detail and the terminal row carry. `cited` names what the statement
// was read from and `bound` names the rule it says the media broke, so the
// view renders evidence rather than an adjective.
export interface MediaHealthObservationView {
  bound: string
  cited: readonly string[]
  expected: string | null
  kind: MediaHealthKind
  observed: string | null
  tracks: readonly number[]
}
export interface PreparationFailureView {
  attempt: number
  diagnosticReference: string
  mediaHealth: readonly MediaHealthObservationView[]
  provider: string
  reason: string
  recovery: string
  recoveryAttempt: number
  stage: PreparationFailureStage
  status: PreparationFailureStatus
}
export interface WorkSummaryFieldView {
  destination: TargetDestination
  state: TargetFieldState
  value: string
}
export interface WorkSummaryView {
  fields: readonly WorkSummaryFieldView[]
  reason: string
  state: WorkSummaryState
}
export interface ReportEvidenceView {
  basis: ClaimBasis
  excerpt: string
  kind: ClaimKind
  role: string | null
  state: FieldState
  value: string
}
export interface ReportChapterView {
  endMilliseconds: number
  ordinal: number
  startMilliseconds: number
  title: string | null
}
export interface ReportProviderEvidenceView {
  candidate: string
  kind: string
  provider: string
  role: string | null
  state: string
  value: string | null
}
export interface RecordingIdentityBasisView {
  basis: 'chapter_runtime_and_structure'
  provider: string
  candidate: string
}
export interface ReportDetailView {
  recordingIdentityBasis: RecordingIdentityBasisView | null
  audiobookReviewScope: 'work' | 'recording' | null
  chapters: readonly ReportChapterView[]
  chaptersTruncated: boolean
  coverPresent: boolean
  coverSurface: AudioSurface | null
  evidence: readonly ReportEvidenceView[]
  evidenceTruncated: boolean
  item: string
  library: string
  libraryDisplayName?: string
  mediaHealth: readonly MediaHealthObservationView[]
  mediaKind: MediaKind
  providerSources: readonly ProviderOutcomeView[]
  providerEvidence: readonly ReportProviderEvidenceView[]
  providerEvidenceTruncated: boolean
  reportId: string
  title: string
}
// The cover the library holds for a work, retained by its current report and
// served only at the address the server names for it (#1290).
export interface LibraryCoverView {
  previewSha256: string
  width: number
  height: number
  url: string
}
export interface LibraryWorkView {
  id: string
  reviewCaseId?: string
  workId: string
  title: string
  authors: readonly string[]
  library: string
  libraryDisplayName?: string
  mediaKind: MediaKind
  attentionCount: number
  attention: AttentionState
  // Where this work opens, and what opens there. A work with a review case
  // points at that case; a work with only a published report points at the
  // report itself, which is the one route its evidence has; a work that
  // produced neither has nothing to open. The server names which, because
  // reading it back off the URL's shape would break on a route rename.
  detailUrl: string | null
  detailKind: 'case' | 'report' | null
  preparation: PreparationFailureView | null
  preparationOutcomes: readonly ProviderOutcomeView[]
  summary: WorkSummaryView | null
  cover?: LibraryCoverView
}
export interface LibraryView {
  lens: LibraryLens
  query?: string
  nextCursor?: string
  previousCursor?: string
  page: number
  counts: { attention: number; all: number; clear: number; notMatched: number }
  works: readonly LibraryWorkView[]
}
export interface AttentionItemView {
  id: string
  destination: TargetDestination
  title: string
  authors: readonly string[]
  library: string
  libraryDisplayName?: string
  mediaKind: MediaKind
  classificationReason?: string
  notices: readonly string[]
  provenance: readonly string[]
  workAttentionCount: number
}
export interface AttentionView {
  query?: string
  nextCursor?: string
  total: number
  items: readonly AttentionItemView[]
}
export type EvidenceRole = 'external_corroboration' | 'external_observation' | 'local_presence'

export interface ChoiceEvidenceView {
  observation: string
  role: EvidenceRole
  source: string
}
export interface ChoiceContextView {
  alternativeId: string | null
  evidence: readonly ChoiceEvidenceView[]
  limitations: readonly string[]
  rationale: string
  recommendation: 'no_recommendation' | 'recommended'
  recommendationReason: string
  selection: 'current' | 'proposed'
}
export type DescriptionAdviceView = Schema<'_DescriptionAdviceResponse'>
export interface ReviewFieldView {
  classification: string
  classificationReason: string
  destination: TargetDestination
  decisionActions: readonly DecisionAction[]
  editable: boolean
  disposition: ProposalOutcome
  effectCapability: EffectCapability
  needsAttention: boolean
  navigationRole: string
  manualHold: boolean
  currentValue: string
  currentRaw: unknown
  proposedValues: readonly Schema<'_ProposedValue'>[]
  choiceContexts: readonly ChoiceContextView[]
  proposalId: string
  targetFingerprint: string
  humanDecision: (Schema<'_DecisionProjection'> & { outcome: DecisionOutcome }) | null
  inventoryRole?: 'primary' | 'contextual'
  automaticDecision:
    | (Schema<'_AutomaticDecisionProjection'> & { outcome: AutomaticOutcome })
    | Schema<'_CoverAutomaticDecisionProjection'>
    | null
  automaticWithheldReason: string | null
  automaticSelectionSources?: readonly string[]
  notices: readonly string[]
  provenance: readonly string[]
  preservationOnly: boolean
  corroborationSources: readonly string[]
  descriptionAdvice?: DescriptionAdviceView | null
  ownerControl?: OwnerControlView | null
  currentCoverSelectionId?: string | null
  currentCoverSelectionArtifactId?: string | null
  coverConsideration?: CoverConsiderationView | null
}
// F59's owner controls (ADR-0085): each part is what the store would accept now.
export interface OwnerControlView {
  entry: { maxCharacters: number; suggestions: readonly { rule: string; value: string }[] } | null
  subset: {
    maxItems: number
    options: readonly { value: string; current: boolean; proposed: boolean; previous: boolean }[]
  } | null
  withdraw: 'available' | 'automatic_hold' | 'in_progress' | 'written' | null
}
export interface CoverObservationView {
  candidateReference: string
  composition: 'dense' | 'minimal' | 'unknown'
  coverTextLanguage: 'english' | 'non_english' | 'multiple' | 'no_visible_title' | 'unknown'
  explicitImagery: 'absent' | 'suggestive' | 'explicit' | 'unknown'
  formatBranding: 'absent' | 'text_edition' | 'audiobook' | 'unknown'
  medium: 'illustrated' | 'mixed' | 'photographic' | 'typographic' | 'unknown'
  overlayMarking: 'absent' | 'present' | 'unknown'
  placeholder: 'absent' | 'present' | 'unknown'
  previewSha256: string
  previewVariant: string
  promotionalText: 'absent' | 'present' | 'unknown'
  reproduction: 'clean' | 'degraded' | 'unknown'
  seriesStyling: 'standalone' | 'series_branded' | 'omnibus_or_set' | 'unknown'
  thumbnailLegibility: 'legible' | 'illegible' | 'unknown'
  tieInBranding: 'absent' | 'present' | 'unknown'
}
export interface CoverCandidateView {
  artifactId: string
  deterministicCitations: readonly string[]
  deterministicRank: number | null
  deterministicRankKey: readonly (number | string)[] | null
  height: number
  identity: Readonly<Record<string, unknown>>
  incumbent: boolean
  matchState: 'same_edition' | 'same_book' | 'match_not_confirmed'
  observation: CoverObservationView | null
  originalSha256: string
  ordinal: number
  previewSha256: string
  reference: string
  selectable: boolean
  manualEligible: boolean
  sourceKinds: readonly CoverSourceKind[]
  state: 'acceptable' | 'available' | 'excluded' | 'recommended'
  width: number
}
export interface CoverConsiderationView {
  choiceContexts: readonly ChoiceContextView[]
  id: string
  current: boolean
  candidates: readonly CoverCandidateView[]
  incumbent: {
    readonly reference: string | null
    readonly state: 'absent' | 'available' | 'unavailable'
  }
  omissions: readonly Readonly<Record<string, unknown>>[]
  policy: Readonly<Record<string, unknown>> | null
  providerFetching: boolean
  ready: boolean
  policyPreferences: {
    readonly avoidPromotionalText: boolean
    readonly avoidTieInBranding: boolean
    readonly preferMinimalComposition: boolean
  }
  policyReference: string
  reviewerEngine: 'codex' | 'openrouter' | null
  reviewerModel: string | null
  reviewerAccessRevision: number | null
  reviewerStatus: string
  reviewerUnavailableReason:
    | 'profile contract requires memory_bytes=1073741824'
    | 'profile contract requires process_limit=32'
    | null
  reviewerFailureCode?: ReviewerFailureCode
  supersedesId?: string
  targetFingerprint: string
}
export interface CaseDetailView {
  recordingIdentityBasis: RecordingIdentityBasisView | null
  audiobookReviewScope: 'work' | 'recording' | null
  id: string
  current: boolean
  target: {
    connection: string
    library: string
    libraryDisplayName?: string
    item: string
    mediaKind: MediaKind
    title: string
  }
  title: string
  authors: readonly string[]
  comparisonNotices: readonly string[]
  mediaHealth: readonly MediaHealthObservationView[]
  writesConsented: boolean
  fields: readonly ReviewFieldView[]
}
export interface EvidenceObservationView {
  destinations: readonly TargetDestination[]
  observation: string
  role: EvidenceRole
  source: string
}
export interface EvidenceView {
  items: readonly EvidenceObservationView[]
  missingLineageCount: number
  nextCursor?: string
  sourceLedger: readonly ProviderOutcomeView[]
}
export interface HistoryValueView {
  kind: string
  value: string | boolean | readonly string[] | readonly { name: string; sequence: string | null }[]
}
export type HistoryItemView = (
  | {
      category: 'description'
      outcome: string
      state: DescriptionState
      model: string
      engine: string
      candidate: Schema<'DescriptionAdviceCandidate'> | null
      selected: boolean
    }
  | {
      category: 'owner'
      // What the recorded decision may go on to do. A decision retained from
      // before audiobook writes existed is never applied, and the owner reads
      // that here (#968).
      applicationReason: string | null
      destination: TargetDestination
      outcome: DecisionOutcome
      previousValue: HistoryValueView | null
      selectedValue: HistoryValueView | null
    }
  | {
      category: 'automatic'
      destination: TargetDestination
      outcome: AutomaticOutcome
      previousValue: HistoryValueView | null
      selectedValue: HistoryValueView | null
    }
  | {
      category: 'preview'
      dispositions: readonly { count: number; outcome: ShadowDisposition }[]
    }
  | {
      category: 'cover'
      automaticOutcome?: 'candidate'
      candidateCount: number
      outcome: CoverHistoryOutcome
      ownerOutcome?: 'accept' | 'keep_current' | 'reject'
      ownerMatchUnconfirmed?: boolean
      policyReasons: readonly string[]
      policyStatus?: 'recommended' | 'abstained'
      reviewerStatus: 'completed' | 'failed' | 'unavailable'
      reviewerModel?: string
      supersedesAutomatic: boolean
    }
  | { category: 'transition'; outcome: TransitionOutcome; reason: string }
) & { occurredAt: number | null }
export interface HistoryView {
  items: readonly HistoryItemView[]
  nextCursor?: string
}
type PreparationSchema = Schema<'_PreparationResponse'>
export type PreparationView = Omit<
  PreparationSchema,
  'first_boot' | 'first_boot_complete' | 'first_boot_works'
> &
  Partial<Pick<PreparationSchema, 'first_boot' | 'first_boot_complete' | 'first_boot_works'>>
export type PreparationProgress = Schema<'_PreparationProgress'>
export type ProviderThrottlingView = Schema<'_PreparationThrottling'>
export type ProviderThrottleEntry = ProviderThrottlingView['entries'][number]
export type PolicyStateView = Schema<'_PolicyProfileResponse'>
export type ShadowPostureView = Schema<'_ShadowPostureResponse'>
export type AutomaticSafetyView = Schema<'_AutomaticSafetyResponse'>
export type ShadowSampleView = Schema<'_ShadowSampleResponse'>
export type PolicyTransitionView = Schema<'_PolicyTransitionResponse'>
export type PolicyDraft = Schema<'_PolicyDraftRequest'>
export type PolicyPreviewView = Schema<'_PolicyPreviewResponse'>
export type PolicyActivationView = Schema<'_PolicyActivationResponse'>
export interface ActivityPreparedView {
  automatic: number
  human: number
  total: number
}
export interface ActivityLibraryView {
  connection: string
  library: string
  prepared?: ActivityPreparedView | null
}
export interface ActivityDestinationView {
  consented: boolean
  destination: TargetDestination
  /** Prepared changes this destination's permission would carry, and no other. */
  prepared: number
  state: DestinationState
}
/**
 * Why prepared work is not moving, split into the four different next steps an
 * owner has, plus what is moving now. One "waiting safely" number could not
 * distinguish consent they can give from a retry they must wait out (#639).
 */
export interface ActivityWaitingReasonsView {
  applying: number
  awaiting_permission: number
  held_for_recovery: number
  retrying: number
  scheduled: number
}
export interface ActivityCapabilityView {
  connection: string
  destinations: readonly ActivityDestinationView[]
  library: string
  state: CapabilityState
}
export type ActivityEventType = 'review' | 'policy' | 'metadata' | 'safety' | 'unknown'
/** How a record reached the ledger, in the owner's terms rather than the event's. */
export type ActivityCategory = 'owner' | 'automatic' | 'policy' | 'recovery'
export interface ActivityEvidenceView {
  label: string
  value: string
}
/** One exact policy setting a Policy record moved, and what it moved between. */
export interface ActivityPolicyChangeView {
  field: string
  setting: string
  previous: string
  next: string
}
export interface ActivityDiagnosticView {
  event_class: 'automatic_decision'
  reason: 'automatic_authority_unavailable'
}
export interface ActivityEventView {
  author_label?: string
  category: ActivityCategory
  /** The exact change, compact enough for one row column. */
  change: string
  /** The same change as a whole sentence, for a reader who hears the row. */
  change_detail: string
  /** Stable grouped identity for a change whose latest transition may move. */
  change_id: string
  /** The preparation an automatic no-change check belongs to; never shown. */
  check_group: string | null
  destination: string
  diagnostic?: ActivityDiagnosticView | null
  event_id: string
  event_type: ActivityEventType
  evidence: readonly ActivityEvidenceView[]
  failure: string | null
  field_label: string
  library: string
  occurred_at: number | null
  origin: string
  policy_changes: readonly ActivityPolicyChangeView[]
  state: string
  transition_id?: string
  work_id: string
  work_label: string
}
export interface ActivityTransitionView {
  event_id: string
  occurred_at: number
  sequence: number
  state: string
  failure: string | null
}
export interface ActivityTransitionsView {
  events: readonly ActivityTransitionView[]
  has_more: boolean
}
export interface ActivitySearch {
  q?: string
  eventType: 'all' | ActivityEventType
  rangeDays: 7 | 30 | 90
  page: number
  pageSize: 25 | 50 | 100
  /** The address asked for an event filter the ledger does not have. */
  filter?: 'unrecognised'
}
export interface ActivityLedgerView {
  event_type: ActivitySearch['eventType']
  page: number
  page_count: number
  page_size: ActivitySearch['pageSize']
  query: string | null
  range_days: ActivitySearch['rangeDays']
  total: number
}
export interface ActivityWaitingView {
  destination: TargetDestination
  library: string
  state: string
  work_id: string
  work_label: string
}
export interface ActivityExampleView {
  after: unknown
  before: unknown
  destination: TargetDestination
  library: string
  work_id: string
  work_label: string
}
export interface ActivitySuspensionView {
  connection: string
  destination: string | null
  reason: SuspensionReason
  scope: string
  updated_at: number
}
export type ProviderRecoveryDispatchReason =
  'missing_corroboration' | 'zero_provider_evidence' | 'settleable_disagreement' | 'blanket_scan'
export type ProviderRecoveryFailureReason =
  | 'authentication'
  | 'authorization'
  | 'configuration'
  | 'rate_limited'
  | 'burst_throttled'
  | 'quota_exhausted'
  | 'transient'
export interface ProviderRecoveryActivityView {
  failure_reasons?: readonly ProviderRecoveryFailureReason[]
  affected_count: number
  dispatch_reasons?: readonly ProviderRecoveryDispatchReason[]
  next_eligible_at: number | null
  state: 'waiting' | 'checking' | 'dormant' | 'recovered' | 'fanout' | 'exhausted'
}
/** A configured library whose write permission no longer applies, and why (#1370). */
export type ActivityLapsedConsentView = Schema<'_ActivityLapsedConsent'>
export interface ActivityDisplayView {
  backup_ready: null
  capabilities: readonly ActivityCapabilityView[]
  configured_libraries: readonly ActivityLibraryView[]
  consented_libraries: readonly ActivityLibraryView[]
  counts: Readonly<Record<string, number>>
  enabled_libraries: readonly ActivityLibraryView[]
  events: readonly ActivityEventView[]
  example: null
  lapsed_consents: readonly ActivityLapsedConsentView[]
  last_failure: string | null
  ledger: ActivityLedgerView
  current_waiting: ActivityWaitingView | null
  paused: boolean
  prepared: ActivityPreparedView
  provider_recovery?: readonly ProviderRecoveryActivityView[]
  schema_version: 'continuous-field-activity-v3'
  suspensions: readonly ActivitySuspensionView[]
  waiting: ActivityWaitingReasonsView
}

export type ActivityPermissionReadinessView = Pick<
  ActivityDisplayView,
  'capabilities' | 'configured_libraries' | 'prepared'
> & { backup_ready: boolean | null; example: ActivityExampleView | null }

/**
 * The inputs Activity's consent section reads, and nothing else. Library's
 * write-ready notice reads this so it never waits for Activity history (#1236).
 */
export type ActivityConsentView = Pick<
  ActivityDisplayView,
  'backup_ready' | 'capabilities' | 'configured_libraries' | 'lapsed_consents' | 'waiting'
>

export type ActivityView = ActivityDisplayView

export interface PolicyDraftDisplayView {
  activeName: string
  draftName: string
  changed: boolean
}

export interface PolicyFieldDefault {
  readonly destination: string
  readonly option: string
}

// Two documents describe the same policy when every field in the closed
// catalogue resolves to the same option and destination. Comparing the raw maps
// instead made an omitted key differ from the default it stands for, so a draft
// that was semantically identical to the active policy still reported a change
// and still offered validation. The catalogue is supplied by its owner rather
// than duplicated here, which is also what keeps the chapter badges and this
// comparison from ever disagreeing.
const sameResolvedPolicy = (
  left: Record<string, string>,
  right: Record<string, string>,
  defaults: Readonly<Record<string, string>>,
) =>
  Object.keys(defaults).every(
    (key) => (left[key] ?? defaults[key]) === (right[key] ?? defaults[key]),
  ) &&
  [...Object.keys(left), ...Object.keys(right)].every(
    (key) => Object.hasOwn(defaults, key) || left[key] === right[key],
  )

export interface PolicyQualificationEntry {
  readonly identity?: string
  readonly destination?: string
  readonly operation?: string
  readonly automatic_available: boolean
  readonly field: string
  readonly option: string
}

// Preserve operation identities while callers aggregate routing availability.
export const policyAutomaticQualification = (
  state: PolicyStateView,
): readonly PolicyQualificationEntry[] => [
  ...state.audiobook_qualification,
  ...state.rich_qualification,
]

export const derivePolicyDraftDisplay = (
  active: PolicyStateView,
  draft: PolicyDraft,
  fieldDefaults: Readonly<Record<string, PolicyFieldDefault>>,
): PolicyDraftDisplayView => {
  const options = Object.fromEntries(
    Object.entries(fieldDefaults).map(([key, value]) => [key, value.option]),
  )
  const destinations = Object.fromEntries(
    Object.entries(fieldDefaults).map(([key, value]) => [key, value.destination]),
  )
  return {
    activeName: active.profile.name,
    draftName: draft.name,
    changed:
      draft.name !== active.profile.name ||
      !sameResolvedPolicy(draft.options, active.profile.options, options) ||
      !sameResolvedPolicy(draft.destinations, active.profile.destinations, destinations),
  }
}

export interface DecisionCommand {
  destination: string
  outcome: DecisionOutcome
  proposalId: string
  selectedValue?: Schema<'_ProposedValue'> | null
  targetFingerprint: string
  idempotencyKey: string
  considerationId?: string
  coverConfirmation?: { expected_selection_id: string | null; confirm_unverified_match: boolean }
  authoredDescription?: { candidateId: string; binding: Schema<'DescriptionAdviceBinding'> }
}

type DecisionWireAcknowledgement =
  Schema<'_DecisionAcknowledgement'> | Schema<'_DescriptionSelectionAcknowledgement'>
export type DecisionAcknowledgementView = (
  | Omit<Schema<'_DecisionAcknowledgement'>, 'confirmation'>
  | Omit<Schema<'_DescriptionSelectionAcknowledgement'>, 'confirmation'>
) & { confirmation: { status: 'confirmed'; case: CaseDetailView } | { status: 'unavailable' } }

export interface ReviewHttp {
  getAppearance(signal?: AbortSignal, workClass?: ReadWorkClass): Promise<AppearanceView>
  getSettings(signal?: AbortSignal, workClass?: ReadWorkClass): Promise<SettingsView>
  writeSettings(value: SettingsWrite, signal?: AbortSignal): Promise<SettingsView>
  getReviewerAccess(signal?: AbortSignal, workClass?: ReadWorkClass): Promise<ReviewerAccessView>
  refreshOpenRouter(signal?: AbortSignal, task?: ReviewerTask): Promise<ReviewerAccessView>
  configureOpenRouter(
    value: { expected_revision: number; api_key: string; model: string; task?: ReviewerTask },
    signal?: AbortSignal,
  ): Promise<ReviewerAccessView>
  describeContainedDescription(): Promise<{ model: string; profile_sha256: string }>
  testDescriptionModel(
    value: { expected_revision: number; api_key: string; model: string },
    signal?: AbortSignal,
  ): Promise<DescriptionModelMetadata>
  clearOpenRouter(expectedRevision: number, signal?: AbortSignal): Promise<ReviewerAccessView>
  selectReviewer(
    engine: 'codex' | null,
    expectedRevision: number,
    signal?: AbortSignal,
    task?: ReviewerTask,
  ): Promise<ReviewerAccessView>
  startCodexDevice(expectedRevision: number, signal?: AbortSignal): Promise<CodexDeviceStartResult>
  getCodexDevice(signal?: AbortSignal, workClass?: ReadWorkClass): Promise<CodexDeviceAuthState>
  recheckCodex(signal?: AbortSignal): Promise<CodexDeviceAuthState>
  cancelCodexDevice(signal?: AbortSignal): Promise<CodexDeviceAuthState>
  clearCodex(expectedRevision: number, signal?: AbortSignal): Promise<ReviewerAccessView>
  getLibrary(
    search: LibrarySearch,
    signal?: AbortSignal,
    workClass?: ReadWorkClass,
  ): Promise<LibraryView>
  getReportDetail(
    detailUrl: string,
    signal?: AbortSignal,
    workClass?: ReadWorkClass,
  ): Promise<ReportDetailView>
  getAttention(
    search: AttentionSearch,
    signal?: AbortSignal,
    workClass?: ReadWorkClass,
  ): Promise<AttentionView>
  getCaseDetail(
    reviewCaseId: string,
    signal?: AbortSignal,
    workClass?: ReadWorkClass,
  ): Promise<CaseDetailView>
  getCaseSuccessor(
    reviewCaseId: string,
    signal?: AbortSignal,
    workClass?: ReadWorkClass,
  ): Promise<string | null>
  getEvidence(
    reviewCaseId: string,
    cursor?: string,
    signal?: AbortSignal,
    workClass?: ReadWorkClass,
  ): Promise<EvidenceView>
  getHistory(
    reviewCaseId: string,
    signal?: AbortSignal,
    workClass?: ReadWorkClass,
  ): Promise<HistoryView>
  retryCoverConsideration(
    reviewCaseId: string,
    signal?: AbortSignal,
  ): Promise<CoverConsiderationView>
  getPreparation(signal?: AbortSignal, workClass?: ReadWorkClass): Promise<PreparationView>
  preparationAction(
    path: string,
    signal?: AbortSignal,
  ): Promise<PreparationView | PreparationProgress>
  dismissPreparation(runId: string, signal?: AbortSignal): Promise<PreparationView>
  retryProviderRecovery(expectedGeneration: number, signal?: AbortSignal): Promise<SettingsView>
  recordDecision(
    reviewCaseId: string,
    command: DecisionCommand,
    signal?: AbortSignal,
  ): Promise<DecisionAcknowledgementView>
  retryDescriptionAdvice(reviewCaseId: string, signal?: AbortSignal): Promise<void>
  returnToAutomatic(reviewCaseId: string, destination: string, signal?: AbortSignal): Promise<void>
  getPolicyState(signal?: AbortSignal, workClass?: ReadWorkClass): Promise<PolicyStateView>
  getShadowPosture(signal?: AbortSignal, workClass?: ReadWorkClass): Promise<ShadowPostureView>
  getAutomaticSafety(signal?: AbortSignal, workClass?: ReadWorkClass): Promise<AutomaticSafetyView>
  getShadowSample(signal?: AbortSignal, workClass?: ReadWorkClass): Promise<ShadowSampleView>
  getPolicyTransition(
    signal?: AbortSignal,
    workClass?: ReadWorkClass,
  ): Promise<PolicyTransitionView | null>
  policyCommand<T>(path: string, body?: unknown, signal?: AbortSignal): Promise<T>
  getActivity(
    search?: ActivitySearch,
    signal?: AbortSignal,
    workClass?: ReadWorkClass,
  ): Promise<ActivityView>
  getActivityPermissionReadiness(
    signal?: AbortSignal,
    workClass?: ReadWorkClass,
  ): Promise<ActivityPermissionReadinessView>
  getActivityConsent(signal?: AbortSignal, workClass?: ReadWorkClass): Promise<ActivityConsentView>
  getActivityTransitions(
    eventId: string,
    afterSequence?: number,
    signal?: AbortSignal,
    workClass?: ReadWorkClass,
  ): Promise<ActivityTransitionsView>
  activityCommand(
    path: string,
    body?: unknown,
    signal?: AbortSignal,
  ): Promise<ActivityView | undefined>
  logout(signal?: AbortSignal): Promise<void>
}

export interface CsrfTokenStore {
  current(): string | undefined
  renew(signal?: AbortSignal): Promise<string>
}
export interface HttpClientOptions {
  csrf: CsrfTokenStore
  buildIdentity?: string
  fetch?: typeof globalThis.fetch
  onSessionExpired?: () => void
  onBuildMismatch?: () => void
}

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
  ) {
    super(`Review API request failed (${String(status)}: ${code})`)
    this.name = 'ApiError'
  }
}
export class SessionExpiredError extends ApiError {
  constructor() {
    super(401, 'session_expired')
    this.name = 'SessionExpiredError'
  }
}
export class CsrfRenewedError extends ApiError {
  constructor() {
    super(403, 'csrf_renewed')
    this.name = 'CsrfRenewedError'
  }
}
export const sessionRefreshedCopy = 'Your session was refreshed. Press again.'
export const refusalCopy = (error: unknown, fallback: string) =>
  error instanceof CsrfRenewedError ? sessionRefreshedCopy : fallback
export class BuildMismatchError extends ApiError {
  constructor() {
    super(409, 'frontend_build_mismatch')
    this.name = 'BuildMismatchError'
  }
}
export class AmbiguousMutationError extends ApiError {
  constructor() {
    super(0, 'mutation_outcome_unknown')
    this.name = 'AmbiguousMutationError'
  }
}

const csrfPattern = /^[A-Za-z0-9_-]{32,128}$/
const buildPattern = /^[0-9a-f]{64}$/

export class PageCsrfTokenStore implements CsrfTokenStore {
  private token?: string
  constructor(
    initialToken: string | undefined,
    private readonly fetchPage: (signal?: AbortSignal) => Promise<Response> = async (signal) =>
      fetch(window.location.href, {
        cache: 'no-store',
        credentials: 'same-origin',
        ...(signal ? { signal } : {}),
      }),
  ) {
    if (initialToken && csrfPattern.test(initialToken)) this.token = initialToken
  }
  current() {
    return this.token
  }
  async renew(signal?: AbortSignal): Promise<string> {
    const response = await this.fetchPage(signal)
    if (response.status === 401) throw new SessionExpiredError()
    if (!response.ok) throw new ApiError(response.status, 'csrf_renewal_failed')
    const page = new DOMParser().parseFromString(await response.text(), 'text/html')
    const token = page.body.dataset.csrfToken
    page.body.removeAttribute('data-csrf-token')
    if (!token || !csrfPattern.test(token)) throw new ApiError(502, 'invalid_csrf_renewal')
    this.token = token
    return token
  }
}

// An expired session signs in again and returns to the page it was on; the
// server accepts only a workspace page as the return path (#1371).
export const signInLocation = (location: Pick<Location, 'pathname' | 'search'> = window.location) =>
  `/login?next=${encodeURIComponent(location.pathname + location.search)}`

export class ReviewApiClient implements ReviewHttp {
  private readonly fetch: typeof globalThis.fetch
  private readonly onSessionExpired: () => void
  private readonly onBuildMismatch: () => void
  private readonly buildIdentity: string | undefined
  private sessionExpiryReported = false
  private sessionExpiryNavigationScheduled = false
  constructor(private readonly options: HttpClientOptions) {
    this.fetch = options.fetch ?? globalThis.fetch.bind(globalThis)
    this.onSessionExpired =
      options.onSessionExpired ?? (() => window.location.assign(signInLocation()))
    this.onBuildMismatch = options.onBuildMismatch ?? (() => undefined)
    this.buildIdentity =
      options.buildIdentity && buildPattern.test(options.buildIdentity)
        ? options.buildIdentity
        : undefined
  }

  async getLibrary(
    search: LibrarySearch,
    signal?: AbortSignal,
    workClass: ReadWorkClass = 'foreground',
  ) {
    const p = new URLSearchParams({ lens: search.lens })
    if (search.q) p.set('q', search.q)
    if (search.cursor) p.set('cursor', search.cursor)
    return mapLibrary(await this.read(`/api/v1/library?${p}`, signal, workClass))
  }
  // The address is the one the library row handed back, followed rather than
  // rebuilt: an item id may hold any character a target permits, and only the
  // server that escaped it knows how.
  async getReportDetail(
    detailUrl: string,
    signal?: AbortSignal,
    workClass: ReadWorkClass = 'foreground',
  ) {
    return mapReportDetail(await this.read(detailUrl, signal, workClass))
  }
  async getSettings(signal?: AbortSignal, workClass: ReadWorkClass = 'foreground') {
    return mapSettings(await this.read('/api/v1/settings', signal, workClass))
  }
  async getAppearance(signal?: AbortSignal, workClass: ReadWorkClass = 'foreground') {
    const value: unknown = await this.read('/api/v1/appearance', signal, workClass)
    if (
      !record(value) ||
      (value.reduced_motion !== 'follow_system' && value.reduced_motion !== 'always_reduced')
    )
      throw new ApiError(502, 'invalid_appearance_response')
    return value as AppearanceView
  }
  async writeSettings(value: SettingsWrite, signal?: AbortSignal) {
    return mapSettings(await this.mutate('/api/v1/settings', value, signal, undefined, 'PATCH'))
  }
  async getReviewerAccess(signal?: AbortSignal, workClass: ReadWorkClass = 'foreground') {
    return mapReviewerAccess(await this.read('/api/v1/reviewer-access', signal, workClass))
  }
  async refreshOpenRouter(signal?: AbortSignal, task?: ReviewerTask) {
    return mapReviewerAccess(
      await this.mutate(
        `/api/v1/reviewer-access/openrouter/refresh?task=${task ?? 'cover_observation'}`,
        {},
        signal,
      ),
    )
  }
  async configureOpenRouter(
    value: { expected_revision: number; api_key: string; model: string; task?: ReviewerTask },
    signal?: AbortSignal,
  ) {
    return mapReviewerAccess(
      await this.mutate('/api/v1/reviewer-access/openrouter', value, signal, undefined, 'PUT'),
    )
  }
  async describeContainedDescription() {
    const value = await this.mutate('/api/v1/reviewer-access/description-model/contained', {})
    if (
      !record(value) ||
      typeof value.model !== 'string' ||
      !value.model ||
      typeof value.profile_sha256 !== 'string' ||
      !/^[0-9a-f]{64}$/.test(value.profile_sha256)
    ) {
      throw new Error('Invalid contained description disclosure')
    }
    return { model: value.model, profile_sha256: value.profile_sha256 }
  }
  async testDescriptionModel(
    value: { expected_revision: number; api_key: string; model: string },
    signal?: AbortSignal,
  ) {
    return mapDescriptionMetadata(
      await this.mutate('/api/v1/reviewer-access/description-model/test', value, signal),
    )
  }
  async clearOpenRouter(expectedRevision: number, signal?: AbortSignal) {
    return mapReviewerAccess(
      await this.mutate(
        '/api/v1/reviewer-access/openrouter',
        { expected_revision: expectedRevision },
        signal,
        undefined,
        'DELETE',
      ),
    )
  }
  async selectReviewer(
    engine: 'codex' | null,
    expectedRevision: number,
    signal?: AbortSignal,
    task?: ReviewerTask,
  ) {
    return mapReviewerAccess(
      await this.mutate(
        '/api/v1/reviewer-access/selection',
        { engine, expected_revision: expectedRevision, ...(task ? { task } : {}) },
        signal,
        undefined,
        'PUT',
      ),
    )
  }
  async startCodexDevice(expectedRevision: number, signal?: AbortSignal) {
    const value: unknown = await this.mutate(
      '/api/v1/reviewer-access/codex/device',
      { expected_revision: expectedRevision },
      signal,
    )
    if (!record(value) || !exactKeys(value, ['access', 'device']))
      throw new ApiError(502, 'invalid_reviewer_access_response')
    return { access: mapReviewerAccess(value.access), device: mapCodexDevice(value.device) }
  }
  async getCodexDevice(signal?: AbortSignal, workClass: ReadWorkClass = 'foreground') {
    return mapCodexDevice(
      await this.read('/api/v1/reviewer-access/codex/device', signal, workClass),
    )
  }
  async recheckCodex(signal?: AbortSignal) {
    return mapCodexDevice(await this.mutate('/api/v1/reviewer-access/codex/recheck', {}, signal))
  }
  async cancelCodexDevice(signal?: AbortSignal) {
    return mapCodexDevice(
      await this.mutate('/api/v1/reviewer-access/codex/device/cancel', {}, signal),
    )
  }
  async clearCodex(expectedRevision: number, signal?: AbortSignal) {
    return mapReviewerAccess(
      await this.mutate(
        '/api/v1/reviewer-access/codex',
        { expected_revision: expectedRevision },
        signal,
        undefined,
        'DELETE',
      ),
    )
  }
  async getAttention(
    search: AttentionSearch,
    signal?: AbortSignal,
    workClass: ReadWorkClass = 'foreground',
  ) {
    const p = new URLSearchParams()
    if (search.q) p.set('q', search.q)
    if (search.cursor) p.set('cursor', search.cursor)
    return mapAttention(
      await this.read(`/api/v1/attention${p.size ? `?${p}` : ''}`, signal, workClass),
    )
  }
  async getCaseDetail(id: string, signal?: AbortSignal, workClass: ReadWorkClass = 'foreground') {
    return mapCaseDetail(
      await this.read(`/api/v1/review-cases/${encodeURIComponent(id)}`, signal, workClass),
    )
  }

  async getCaseSuccessor(
    id: string,
    signal?: AbortSignal,
    workClass: ReadWorkClass = 'foreground',
  ) {
    const value = await this.read(
      `/api/v1/review-cases/${encodeURIComponent(id)}/successor`,
      signal,
      workClass,
    )
    if (typeof value !== 'object' || value === null || !('review_case_id' in value))
      throw new ApiError(502, 'invalid_response')
    const successor = value.review_case_id
    if (successor !== null && typeof successor !== 'string')
      throw new ApiError(502, 'invalid_response')
    return successor
  }
  async getEvidence(
    id: string,
    cursor?: string,
    signal?: AbortSignal,
    workClass: ReadWorkClass = 'foreground',
  ) {
    const p = new URLSearchParams({ limit: '50' })
    if (cursor) p.set('cursor', cursor)
    return mapEvidence(
      await this.read(
        `/api/v1/review-cases/${encodeURIComponent(id)}/evidence?${p}`,
        signal,
        workClass,
      ),
    )
  }
  async getHistory(id: string, signal?: AbortSignal, workClass: ReadWorkClass = 'foreground') {
    return mapHistory(
      await this.read(`/api/v1/review-cases/${encodeURIComponent(id)}/history`, signal, workClass),
    )
  }
  async retryCoverConsideration(id: string, signal?: AbortSignal) {
    const value = mapCoverConsideration(
      await this.mutate(
        `/api/v1/review-cases/${encodeURIComponent(id)}/cover-considerations/retry`,
        undefined,
        signal,
      ),
    )
    if (value === null) throw new ApiError(502, 'invalid_cover_consideration_response')
    return value
  }
  async getPreparation(signal?: AbortSignal, workClass: ReadWorkClass = 'foreground') {
    return mapPreparation(await this.read('/api/v1/review-preparation', signal, workClass))
  }
  async preparationAction(path: string, signal?: AbortSignal) {
    const value = await this.mutate(`/api/v1/review-preparation/${path}`, undefined, signal)
    if (/(?:^|\/)(?:scan|retry)$/.test(path)) return preparationProgress(value)
    return mapPreparation(value)
  }
  // The expected generation makes a resend a refusal, never a second raise, so
  // a CSRF-renewed press is sent once more (#1018).
  async retryProviderRecovery(expectedGeneration: number, signal?: AbortSignal) {
    return mapSettings(
      await this.mutate(
        '/api/v1/provider-recovery/retry',
        { expected_generation: expectedGeneration },
        signal,
        undefined,
        'POST',
        true,
      ),
    )
  }
  async dismissPreparation(runId: string, signal?: AbortSignal) {
    return mapPreparation(
      await this.mutate('/api/v1/review-preparation/dismiss', { run_id: runId }, signal),
    )
  }
  async recordDecision(id: string, command: DecisionCommand, signal?: AbortSignal) {
    if (command.authoredDescription) {
      return mapDecisionAcknowledgement(
        await this.mutate(
          `/api/v1/review-cases/${encodeURIComponent(id)}/description-advice/selection`,
          {
            candidate_id: command.authoredDescription.candidateId,
            binding: command.authoredDescription.binding,
          },
          signal,
          { 'idempotency-key': command.idempotencyKey },
        ),
        'description_selection',
        id,
      )
    }
    const {
      idempotencyKey,
      proposalId,
      targetFingerprint,
      selectedValue,
      destination,
      outcome,
      considerationId,
      coverConfirmation,
    } = command
    return mapDecisionAcknowledgement(
      await this.mutate(
        `/api/v1/review-cases/${encodeURIComponent(id)}/decisions`,
        {
          destination,
          outcome,
          proposal_id: proposalId,
          selected_value: selectedValue ?? null,
          target_fingerprint: targetFingerprint,
          consideration_id: considerationId ?? null,
          cover_confirmation: coverConfirmation ?? null,
        },
        signal,
        { 'idempotency-key': idempotencyKey },
      ),
      'decision',
      id,
    )
  }
  async retryDescriptionAdvice(id: string, signal?: AbortSignal) {
    const result = await this.mutate(
      `/api/v1/review-cases/${encodeURIComponent(id)}/description-advice/retry`,
      {},
      signal,
    )
    if (!record(result) || result.queued !== true)
      throw new ApiError(502, 'invalid_description_retry')
  }
  async returnToAutomatic(id: string, destination: string, signal?: AbortSignal) {
    await this.mutate(
      `/api/v1/review-cases/${encodeURIComponent(id)}/fields/${encodeURIComponent(destination)}/return-to-automatic`,
      undefined,
      signal,
    )
  }
  async getPolicyState(signal?: AbortSignal, workClass: ReadWorkClass = 'foreground') {
    return schemaVersionOnly<PolicyStateView>(
      await this.read('/api/v1/metadata-policy/state', signal, workClass),
      'policy_state',
    )
  }
  async getShadowPosture(signal?: AbortSignal, workClass: ReadWorkClass = 'foreground') {
    return schemaVersionOnly<ShadowPostureView>(
      await this.read('/api/v1/metadata-policy/shadow', signal, workClass),
      'shadow_posture',
    )
  }
  async getAutomaticSafety(signal?: AbortSignal, workClass: ReadWorkClass = 'foreground') {
    const value = await this.read('/api/v1/metadata-policy/automatic-safety', signal, workClass)
    if (!record(value)) throw new ApiError(502, 'invalid_automatic_safety_response')
    return value as AutomaticSafetyView
  }
  async getShadowSample(signal?: AbortSignal, workClass: ReadWorkClass = 'foreground') {
    return schemaVersionOnly<ShadowSampleView>(
      await this.read('/api/v1/metadata-policy/shadow/outcomes?limit=10', signal, workClass),
      'shadow_sample',
    )
  }
  async getPolicyTransition(signal?: AbortSignal, workClass: ReadWorkClass = 'foreground') {
    const value = await this.read('/api/v1/metadata-policy/transition', signal, workClass)
    return value === null
      ? null
      : schemaVersionOnly<PolicyTransitionView>(value, 'policy_transition')
  }
  async policyCommand<T>(path: string, body?: unknown, signal?: AbortSignal) {
    const value = await this.mutate(
      `/api/v1/metadata-policy/${path}`,
      body,
      signal,
      undefined,
      'POST',
      path === 'validate' || path === 'preview',
    )
    if (!record(value)) throw new ApiError(502, 'invalid_policy_response')
    return value as T
  }
  async getActivity(
    search?: ActivitySearch,
    signal?: AbortSignal,
    workClass: ReadWorkClass = 'foreground',
  ): Promise<ActivityView> {
    const p = new URLSearchParams()
    if (search) {
      if (search.q) p.set('q', search.q)
      p.set('event_type', search.eventType)
      p.set('range_days', String(search.rangeDays))
      p.set('page', String(search.page))
      p.set('page_size', String(search.pageSize))
    }
    return mapActivity(
      await this.read(`/api/v1/activity${p.size ? `?${p}` : ''}`, signal, workClass),
    )
  }

  async getActivityPermissionReadiness(
    signal?: AbortSignal,
    workClass: ReadWorkClass = 'foreground',
  ): Promise<ActivityPermissionReadinessView> {
    return mapActivityPermissionReadiness(
      await this.read('/api/v1/activity/permission-readiness', signal, workClass),
    )
  }

  async getActivityConsent(
    signal?: AbortSignal,
    workClass: ReadWorkClass = 'foreground',
  ): Promise<ActivityConsentView> {
    return mapActivityConsent(await this.read('/api/v1/activity/consent-state', signal, workClass))
  }

  async getActivityTransitions(
    eventId: string,
    afterSequence?: number,
    signal?: AbortSignal,
    workClass: ReadWorkClass = 'foreground',
  ): Promise<ActivityTransitionsView> {
    const query = afterSequence === undefined ? '' : `?after_sequence=${String(afterSequence)}`
    const data = await this.read(
      `/api/v1/activity/${encodeURIComponent(eventId)}/transitions${query}`,
      signal,
      workClass,
    )
    if (
      !record(data) ||
      typeof data.has_more !== 'boolean' ||
      !Array.isArray(data.events) ||
      data.events.length > 20 ||
      !data.events.every(
        (event) =>
          record(event) &&
          typeof event.event_id === 'string' &&
          Number.isSafeInteger(event.occurred_at) &&
          typeof event.sequence === 'number' &&
          Number.isSafeInteger(event.sequence) &&
          event.sequence >= 1 &&
          typeof event.state === 'string' &&
          (event.failure === null || typeof event.failure === 'string'),
      )
    )
      throw new ApiError(502, 'invalid_activity_response')
    return {
      events: data.events as ActivityTransitionView[],
      has_more: data.has_more,
    }
  }
  async activityCommand(
    path: string,
    body?: unknown,
    signal?: AbortSignal,
  ): Promise<ActivityView | undefined> {
    const value = await this.mutate(`/api/v1/${path}`, body, signal)
    return value === undefined ? undefined : mapActivity(value)
  }
  async logout(signal?: AbortSignal) {
    await this.mutate('/api/v1/session/logout', undefined, signal)
  }

  private async read(path: string, signal?: AbortSignal, workClass: ReadWorkClass = 'foreground') {
    return this.request(
      path,
      { headers: { 'X-ML-Work-Class': workClass }, ...(signal ? { signal } : {}) },
      false,
    )
  }
  private async mutate(
    path: string,
    body?: unknown,
    signal?: AbortSignal,
    extraHeaders?: HeadersInit,
    method = 'POST',
    sideEffectFree = false,
  ) {
    if (!this.buildIdentity) {
      this.onBuildMismatch()
      throw new BuildMismatchError()
    }
    const headers = new Headers(extraHeaders)
    headers.set('x-ml-build-identity', this.buildIdentity)
    if (body !== undefined) headers.set('content-type', 'application/json')
    try {
      return await this.request(
        path,
        {
          method,
          headers,
          ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
          ...(signal ? { signal } : {}),
        },
        true,
        sideEffectFree || headers.has('idempotency-key'),
      )
    } catch (error) {
      if (error instanceof TypeError) throw new AmbiguousMutationError()
      throw error
    }
  }
  // A 403 `csrf` challenge renews the page token once. Only a request that is
  // safe to repeat -- idempotency-keyed or side-effect free -- is then sent
  // once more; every other one, and a resend refused again, asks the owner to
  // press again (#1018).
  private async request(
    path: string,
    init: RequestInit,
    unsafe: boolean,
    resend = false,
  ): Promise<unknown> {
    const send = async (token?: string) => {
      const headers = new Headers(init.headers)
      if (token) headers.set('x-csrf-token', token)
      return this.fetch(path, { ...init, headers, credentials: 'same-origin', cache: 'no-store' })
    }
    let response = await send(unsafe ? this.options.csrf.current() : undefined)
    if (unsafe && response.status === 403 && (await challengeCode(response)) === 'csrf') {
      let token: string
      try {
        token = await this.options.csrf.renew(init.signal ?? undefined)
      } catch (error) {
        if (error instanceof SessionExpiredError) this.reportSessionExpiry()
        throw error
      }
      if (!resend) throw new CsrfRenewedError()
      response = await send(token)
      if (response.status === 403 && (await challengeCode(response)) === 'csrf')
        throw new CsrfRenewedError()
    }
    const responseBuild = response.headers.get('x-ml-build-identity')
    if (responseBuild && this.buildIdentity && responseBuild !== this.buildIdentity) {
      this.onBuildMismatch()
      throw new BuildMismatchError()
    }
    if (response.status === 401) {
      this.reportSessionExpiry()
      throw new SessionExpiredError()
    }
    if (!response.ok) {
      const error = await structuredError(response)
      if (
        error.code === 'browser_build_mismatch' ||
        error.code === 'frontend_build_mismatch' ||
        error.code === 'build_identity_mismatch'
      ) {
        this.onBuildMismatch()
        throw new BuildMismatchError()
      }
      throw error
    }
    if (response.status === 204) return undefined
    try {
      return (await response.json()) as unknown
    } catch {
      if (unsafe) throw new AmbiguousMutationError()
      throw new ApiError(502, 'invalid_json_response')
    }
  }

  private reportSessionExpiry() {
    if (this.sessionExpiryReported) return
    this.sessionExpiryReported = true
    if (this.sessionExpiryNavigationScheduled) return
    this.sessionExpiryNavigationScheduled = true
    // All callers still reject with SessionExpiredError. The next task lets
    // their promise handlers attach before document navigation takes over.
    window.setTimeout(() => this.onSessionExpired(), 0)
  }
}

const record = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value)

const exactKeys = (value: Record<string, unknown>, keys: readonly string[]) =>
  Object.keys(value).length === keys.length && keys.every((key) => key in value)

const reviewerStates = new Set<ReviewerState>([
  'ready',
  'configured',
  'starting',
  'issued',
  'waiting',
  'validating',
  'usable',
  'expired',
  'cancelled',
  'failed',
  'interrupted',
  'host_unsupported',
  'not_provisioned',
  'unconfigured',
  ...reviewerFailureCodes,
])
const codexStates = new Set<ReviewerState>([
  'ready',
  'configured',
  'starting',
  'issued',
  'waiting',
  'validating',
  'usable',
  'expired',
  'cancelled',
  'failed',
  'interrupted',
  'host_unsupported',
  'not_provisioned',
])
const openRouterStates = new Set<ReviewerState>([
  'unconfigured',
  'configured',
  'usable',
  ...reviewerFailureCodes,
])
const reviewerReasons = new Set<ReviewerReason>([
  'profile_not_provisioned',
  'profile_unreadable',
  'profile_contract_mismatch',
  'host_evidence_unavailable',
  'cgroup_limit_below_1_gib',
  'headroom_below_1_gib',
  'permission_or_profile',
  'permission_or_process',
  'protocol_or_process',
  'restart_interrupted',
  'not_selected',
  ...reviewerFailureCodes,
])
const codexHostReasons = new Set<ReviewerReason>([
  'host_evidence_unavailable',
  'cgroup_limit_below_1_gib',
  'headroom_below_1_gib',
])
const codexFailureReasons = new Set<ReviewerReason>([
  'permission_or_profile',
  'permission_or_process',
  'protocol_or_process',
  'restart_interrupted',
])
const openRouterFailures = new Set<ReviewerState>(reviewerFailureCodes)
const openRouterModel =
  /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}\/[A-Za-z0-9][A-Za-z0-9._-]{0,190}(?::[A-Za-z0-9][A-Za-z0-9._-]{0,31})?$/u
const reviewerStatus = (value: unknown, engine: 'codex' | 'openrouter') => {
  if (
    !record(value) ||
    !exactKeys(value, ['engine', 'state', 'reason', 'model', 'tier', 'host_headroom_bytes']) ||
    value.engine !== engine ||
    !reviewerStates.has(value.state as ReviewerState) ||
    !(value.reason === null || reviewerReasons.has(value.reason as ReviewerReason))
  )
    return false
  const state = value.state as ReviewerState
  const reason = value.reason as ReviewerReason | null
  if (engine === 'codex')
    return (
      codexStates.has(state) &&
      (state === 'not_provisioned'
        ? value.model === null
        : typeof value.model === 'string' &&
          /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/u.test(value.model)) &&
      value.tier === null &&
      (value.host_headroom_bytes === null ||
        (Number.isInteger(value.host_headroom_bytes) && Number(value.host_headroom_bytes) >= 0)) &&
      (state === 'not_provisioned'
        ? reason !== null &&
          ['profile_not_provisioned', 'profile_unreadable', 'profile_contract_mismatch'].includes(
            reason,
          )
        : state === 'host_unsupported'
          ? reason !== null && codexHostReasons.has(reason)
          : state === 'failed' || state === 'interrupted'
            ? reason !== null && codexFailureReasons.has(reason)
            : reason === null)
    )
  const model = typeof value.model === 'string' && openRouterModel.test(value.model)
  return (
    openRouterStates.has(state) &&
    value.host_headroom_bytes === null &&
    (state === 'unconfigured'
      ? value.model === null && value.tier === null && reason === null
      : state === 'configured'
        ? value.tier === null &&
          ((model && reason === null) || (value.model === null && reason === 'not_selected'))
        : state === 'usable'
          ? model && (value.tier === 'free' || value.tier === 'paid') && reason === null
          : openRouterFailures.has(state) && model && value.tier === null && reason === state)
  )
}

const mapDescriptionMetadata = (value: unknown): DescriptionModelMetadata => {
  if (
    !record(value) ||
    !exactKeys(value, [
      'model',
      'observed_at',
      'prompt_price',
      'completion_price',
      'request_price',
      'text_input',
      'text_output',
      'structured_outputs',
    ]) ||
    typeof value.model !== 'string' ||
    !openRouterModel.test(value.model) ||
    value.text_input !== true ||
    value.text_output !== true ||
    value.structured_outputs !== true ||
    !Number.isSafeInteger(value.observed_at) ||
    Number(value.observed_at) < 0 ||
    !['prompt_price', 'completion_price', 'request_price'].every(
      (key) =>
        (key === 'request_price' && value[key] === null) ||
        (typeof value[key] === 'string' &&
          value[key].length <= 40 &&
          value[key].trim() !== '' &&
          Number.isFinite(Number(value[key])) &&
          Number(value[key]) >= 0 &&
          Number(value[key]) <= 1000000),
    )
  )
    throw new ApiError(502, 'invalid_description_metadata')
  return value as unknown as DescriptionModelMetadata
}

const costPrice = (value: unknown): value is string =>
  typeof value === 'string' &&
  value.length > 0 &&
  value.length <= 40 &&
  value.trim() !== '' &&
  Number.isFinite(Number(value)) &&
  Number(value) >= 0 &&
  Number(value) <= 1000000

const mapCostEvidence = (value: unknown): void => {
  if (
    !record(value) ||
    !exactKeys(value, ['schema_version', 'models']) ||
    value.schema_version !== 'reviewer-cost-evidence-v2' ||
    !Array.isArray(value.models) ||
    value.models.length > 100
  )
    throw new ApiError(502, 'invalid_cost_evidence')
  const seen = new Set<string>()
  for (const row of value.models) {
    const run = record(row) ? row.measured_run : undefined
    if (
      !record(row) ||
      !record(run) ||
      !exactKeys(run, ['source_head', 'cohort_sha256', 'observed_at']) ||
      typeof run.source_head !== 'string' ||
      !/^[0-9a-f]{40}$/.test(run.source_head) ||
      typeof run.cohort_sha256 !== 'string' ||
      !/^[0-9a-f]{64}$/.test(run.cohort_sha256) ||
      !Number.isSafeInteger(run.observed_at) ||
      Number(run.observed_at) < 0 ||
      !exactKeys(row, [
        'model',
        'task',
        'requests_selected',
        'requests_attempted',
        'requests_completed',
        'requests_failed',
        'works',
        'distinct_images',
        'total_cost_usd',
        'per_1000_usd',
        'measured_run',
      ]) ||
      typeof row.model !== 'string' ||
      !openRouterModel.test(row.model) ||
      typeof row.task !== 'string' ||
      !['cover', 'description'].includes(row.task) ||
      ![
        'requests_selected',
        'requests_attempted',
        'requests_completed',
        'requests_failed',
        'works',
        'distinct_images',
      ].every(
        (key) =>
          Number.isSafeInteger(row[key]) && Number(row[key]) >= 0 && Number(row[key]) <= 100000,
      ) ||
      Number(row.requests_attempted) < 1 ||
      row.requests_attempted !== row.requests_selected ||
      Number(row.requests_completed) + Number(row.requests_failed) !== row.requests_attempted ||
      Number(row.works) < 1 ||
      Number(row.works) > Number(row.requests_selected) ||
      (row.task === 'description' && row.distinct_images !== 0) ||
      !costPrice(row.total_cost_usd) ||
      !costPrice(row.per_1000_usd) ||
      Math.abs(
        Number(row.per_1000_usd) - (Number(row.total_cost_usd) / row.requests_attempted) * 1000,
      ) > 1e-9 ||
      seen.has(`${row.model}/${row.task}`)
    )
      throw new ApiError(502, 'invalid_cost_evidence')
    seen.add(`${row.model}/${row.task}`)
  }
}

export const mapReviewerAccess = (value: unknown): ReviewerAccessView => {
  if (
    !record(value) ||
    !exactKeys(value, [
      'schema_version',
      'revision',
      'mapping',
      'description_mapping',
      'description_metadata',
      'cover_metadata',
      'cost_evidence',
      'description',
      'openrouter_key_configured',
      'codex',
      'openrouter',
      'recommendations',
      'candidates',
      'free_tier_caveat',
      'data_use_caveat',
    ]) ||
    value.schema_version !== 'reviewer-access-view-v2' ||
    !Number.isInteger(value.revision) ||
    typeof value.openrouter_key_configured !== 'boolean' ||
    !reviewerStatus(value.codex, 'codex') ||
    !reviewerStatus(value.openrouter, 'openrouter') ||
    !Array.isArray(value.recommendations) ||
    value.recommendations.some((item) => typeof item !== 'string' || !openRouterModel.test(item)) ||
    !Array.isArray(value.candidates) ||
    value.candidates.some(
      (item) =>
        !record(item) ||
        !exactKeys(item, [
          'model',
          'tier',
          'qualified',
          'evidence',
          'requests_per_minute',
          'requests_per_day',
        ]) ||
        typeof item.model !== 'string' ||
        !openRouterModel.test(item.model) ||
        typeof item.tier !== 'string' ||
        !['free', 'paid'].includes(item.tier) ||
        item.qualified !== false ||
        item.evidence !== 'catalogue_capability_only' ||
        !(
          item.requests_per_minute === null ||
          (Number.isInteger(item.requests_per_minute) && Number(item.requests_per_minute) > 0)
        ) ||
        !(item.requests_per_day === null || typeof item.requests_per_day === 'string'),
    ) ||
    !(
      value.mapping === null ||
      (record(value.mapping) &&
        value.mapping.engine === 'codex' &&
        Object.keys(value.mapping).length === 1) ||
      (record(value.mapping) &&
        exactKeys(value.mapping, ['engine', 'model']) &&
        value.mapping.engine === 'openrouter' &&
        typeof value.mapping.model === 'string' &&
        openRouterModel.test(value.mapping.model))
    ) ||
    typeof value.free_tier_caveat !== 'string' ||
    typeof value.data_use_caveat !== 'string'
  )
    throw new ApiError(502, 'invalid_reviewer_access_response')
  if (
    value.description !== null &&
    (!record(value.description) ||
      !reviewerStatus(
        value.description,
        value.description.engine === 'codex' ? 'codex' : 'openrouter',
      ))
  )
    throw new ApiError(502, 'invalid_description_reviewer')
  mapCostEvidence(value.cost_evidence)
  const prices = value.cover_metadata
  if (
    prices !== null &&
    (!record(prices) ||
      !exactKeys(prices, [
        'model',
        'observed_at',
        'prompt_price',
        'completion_price',
        'image_price',
        'request_price',
      ]) ||
      !record(value.mapping) ||
      value.mapping.engine !== 'openrouter' ||
      prices.model !== value.mapping.model ||
      !Number.isSafeInteger(prices.observed_at) ||
      Number(prices.observed_at) < 0 ||
      !['prompt_price', 'completion_price', 'image_price', 'request_price'].every(
        (key) => prices[key] === null || costPrice(prices[key]),
      ))
  )
    throw new ApiError(502, 'invalid_cover_metadata')
  if (value.description_metadata !== null) mapDescriptionMetadata(value.description_metadata)
  const mapping = value.description_mapping
  if (!(
    mapping === null ||
    (record(mapping) &&
      ((mapping.engine === 'codex' && exactKeys(mapping, ['engine'])) ||
        (mapping.engine === 'openrouter' &&
          exactKeys(mapping, ['engine', 'model']) &&
          typeof mapping.model === 'string' &&
          openRouterModel.test(mapping.model))))
  ))
    throw new ApiError(502, 'invalid_description_mapping')
  return value as unknown as ReviewerAccessView
}

export const mapCodexDevice = (value: unknown): CodexDeviceAuthState => {
  if (
    !record(value) ||
    !exactKeys(value, [
      'state',
      'verification_url',
      'user_code',
      'expires_in_seconds',
      'failure',
    ]) ||
    ![
      'ready',
      'configured',
      'starting',
      'issued',
      'waiting',
      'validating',
      'usable',
      'expired',
      'cancelled',
      'failed',
      'interrupted',
    ].includes(String(value.state)) ||
    !(
      value.verification_url === null ||
      value.verification_url === 'https://auth.openai.com/codex/device'
    ) ||
    !(
      value.user_code === null ||
      (typeof value.user_code === 'string' &&
        /^[A-Z0-9]{4,8}-[A-Z0-9]{4,8}$/u.test(value.user_code))
    ) ||
    !(value.expires_in_seconds === null || value.expires_in_seconds === 900) ||
    !(
      value.failure === null ||
      (typeof value.failure === 'string' &&
        [
          'permission_or_profile',
          'permission_or_process',
          'protocol_or_process',
          'restart_interrupted',
        ].includes(value.failure))
    ) ||
    (['issued', 'waiting'].includes(String(value.state))
      ? value.verification_url !== 'https://auth.openai.com/codex/device' ||
        typeof value.user_code !== 'string' ||
        value.expires_in_seconds !== 900
      : value.verification_url !== null ||
        value.user_code !== null ||
        value.expires_in_seconds !== null)
  )
    throw new ApiError(502, 'invalid_codex_device_response')
  return value as unknown as CodexDeviceAuthState
}
const stringValue = (value: unknown, field: string) => {
  if (typeof value !== 'string') throw new ApiError(502, `invalid_${field}`)
  return value
}
const codePointLength = (value: string) => Array.from(value).length
const boundedStringValue = (value: unknown, field: string, maximum: number) => {
  const result = stringValue(value, field)
  if (codePointLength(result) > maximum) throw new ApiError(502, `invalid_${field}`)
  return result
}
const integerValue = (value: unknown, field: string) => {
  if (!Number.isSafeInteger(value) || (value as number) < 0)
    throw new ApiError(502, `invalid_${field}`)
  return value as number
}
const optionalString = (value: unknown, field: string) =>
  value === null || value === undefined ? undefined : stringValue(value, field)
const stringArray = (value: unknown, field: string) => {
  if (!Array.isArray(value) || !value.every((item) => typeof item === 'string'))
    throw new ApiError(502, `invalid_${field}`)
  return value
}
// A list the server is never entitled to send empty. Separate from
// `stringArray` because most lists are legitimately empty, and reading an
// absence as a value is the failure this refuses.
const statedStringArray = (value: unknown, field: string) => {
  const values = stringArray(value, field)
  if (values.length === 0) throw new ApiError(502, `invalid_${field}`)
  return values
}
const nullValue = (value: unknown, field: string): null => {
  if (value !== null) throw new ApiError(502, `invalid_${field}`)
  return value
}

const booleanValue = (value: unknown, field: string) => {
  if (typeof value !== 'boolean') throw new ApiError(502, `invalid_${field}`)
  return value
}
export const mapSettings = (value: unknown): SettingsView => {
  if (
    !record(value) ||
    (value.authority !== 'file' && value.authority !== 'store') ||
    !record(value.document) ||
    value.document.schema_version !== 'owner-settings-v4' ||
    !record(value.document.description_advice) ||
    !exactKeys(value.document.description_advice, [
      'remove_marketing',
      'remove_sales_boasts',
      'remove_praise',
      'automatic_application',
    ]) ||
    !Object.values(value.document.description_advice).every((item) => typeof item === 'boolean') ||
    !Array.isArray(value.document.connections) ||
    !Array.isArray(value.document.providers) ||
    !Array.isArray(value.document.libraries) ||
    !Array.isArray(value.fields) ||
    !Array.isArray(value.secrets)
  )
    throw new ApiError(502, 'invalid_settings_response')
  if (
    !value.document.connections.every(
      (item) =>
        record(item) &&
        typeof item.name === 'string' &&
        typeof item.endpoint === 'string' &&
        item.adapter === 'audiobookshelf',
    ) ||
    !value.document.libraries.every(
      (item) =>
        record(item) &&
        typeof item.name === 'string' &&
        typeof item.connection === 'string' &&
        typeof item.library_id === 'string' &&
        Array.isArray(item.provider_connections) &&
        (item.languages === undefined ||
          (Array.isArray(item.languages) &&
            item.languages.every((language) => typeof language === 'string'))),
    ) ||
    !value.fields.every(
      (item) =>
        record(item) &&
        typeof item.field_id === 'string' &&
        typeof item.group === 'string' &&
        typeof item.managed_by_file === 'boolean' &&
        ['applied_live', 'deploy_managed'].includes(String(item.apply_effect)),
    ) ||
    !value.secrets.every(
      (item) =>
        record(item) &&
        typeof item.field_id === 'string' &&
        typeof item.configured === 'boolean' &&
        (item.indicator === undefined ||
          item.indicator === null ||
          typeof item.indicator === 'string'),
    )
  )
    throw new ApiError(502, 'invalid_settings_response')
  // The server leaves an empty language list out, as the contract's default;
  // the page reads it as that empty list.
  return {
    ...value,
    document: {
      ...value.document,
      libraries: value.document.libraries.map((item: unknown) => ({
        languages: [],
        ...(item as Record<string, unknown>),
      })),
    },
  } as unknown as SettingsView
}
// `tracks` is required by review-report-detail-v5. A missing or non-array
// member is a malformed document, not an observation about the whole item:
// coercing it to no tracks would print "this item's audio files" where the
// server named the files it read.
const mediaHealthTracks = (value: unknown, field: string): number[] => {
  if (!Array.isArray(value)) throw new ApiError(502, `invalid_${field}_tracks`)
  return (value as unknown[]).map((ordinal, position) =>
    integerValue(ordinal, `${field}_track_${String(position)}`),
  )
}
const mapMediaHealth = (value: unknown, field: string): MediaHealthObservationView[] => {
  if (!Array.isArray(value)) throw new ApiError(502, `invalid_${field}`)
  return (value as unknown[]).map((observation, index) => {
    if (!record(observation)) throw new ApiError(502, `invalid_${field}_${String(index)}`)
    return {
      bound: boundedStringValue(observation.bound, `${field}_${String(index)}_bound`, 256),
      cited: statedStringArray(observation.cited, `${field}_${String(index)}_cited`),
      expected:
        observation.expected == null
          ? null
          : boundedStringValue(observation.expected, `${field}_${String(index)}_expected`, 256),
      kind: coverObservationValue(
        observation.kind,
        `${field}_${String(index)}_kind`,
        MEDIA_HEALTH_KINDS,
      ),
      observed:
        observation.observed == null
          ? null
          : boundedStringValue(observation.observed, `${field}_${String(index)}_observed`, 256),
      tracks: mediaHealthTracks(observation.tracks, `${field}_${String(index)}`),
    }
  })
}

const mapProviderOutcome = (value: unknown, index: number): ProviderOutcomeView => {
  if (!record(value)) throw new ApiError(502, `invalid_provider_outcome_${String(index)}`)
  return {
    providerRequests: integerValue(
      value.provider_requests,
      `provider_outcome_${String(index)}_requests`,
    ),
    laneKind: coverObservationValue(
      value.lane_kind,
      `provider_outcome_${String(index)}_lane`,
      PROVIDER_QUERY_KINDS,
    ),
    provider: boundedStringValue(value.provider, `provider_outcome_${String(index)}_provider`, 128),
    reason: boundedStringValue(value.reason, `provider_outcome_${String(index)}_reason`, 512),
    status: coverObservationValue(
      value.status,
      `provider_outcome_${String(index)}_status`,
      PROVIDER_STATUSES,
    ),
  }
}
const decisionAction = (value: unknown): DecisionAction => {
  if (value !== 'accept' && value !== 'keep_current')
    throw new ApiError(502, 'invalid_case_detail_actions')
  return value
}
const classificationValue = (value: unknown) => {
  const allowed: readonly string[] = [
    'needs_attention',
    'manual_fallback',
    'automatic_recorded',
    'human_recorded',
    'unsupported',
    'owner_choosable',
  ] satisfies readonly Schema<'_ReviewField'>['classification'][]
  if (typeof value !== 'string' || !allowed.includes(value))
    throw new ApiError(502, 'invalid_classification')
  return value
}
const navigationRole = (value: unknown) => {
  if (value !== 'attention' && value !== 'inspect')
    throw new ApiError(502, 'invalid_navigation_role')
  return value
}
const inventoryRole = (value: unknown) => {
  if (value !== 'primary' && value !== 'contextual')
    throw new ApiError(502, 'invalid_inventory_role')
  return value
}
export const displayValue = (value: unknown): string =>
  value === null
    ? 'Unavailable'
    : typeof value === 'string' || typeof value === 'boolean'
      ? String(value)
      : Array.isArray(value)
        ? value.map(displayValue).join(' · ')
        : record(value) && typeof value.name === 'string'
          ? `${value.name}${typeof value.sequence === 'string' ? ` · ${value.sequence}` : ''}`
          : 'Unavailable'
// A target field that holds no value says whether Audiobookshelf has none or
// could not be read, in Audiobookshelf's own name (#1286).
export const targetValueText = (state: TargetFieldState, value: unknown) =>
  state === 'value'
    ? displayValue(value)
    : state === 'missing' || state === 'null' || state === 'empty'
      ? 'Not set in Audiobookshelf'
      : 'Couldn’t be read from Audiobookshelf'

const validateObject = (value: unknown, name: string): Record<string, unknown> => {
  if (!record(value) || typeof value.schema_version !== 'string')
    throw new ApiError(502, `invalid_${name}_response`)
  return value
}
// A deliberate, named exception to the rule that every field a view reads is
// validated. These responses are handed back as their generated DTO unchanged
// instead of being narrowed into a hand-written view model, so the only
// invariant this file can assert without transcribing
// docs/reference/review-openapi-v1.json is the version handshake: a
// hand-written copy of that document would become a second contract and would
// fail closed on responses the server is entitled to send. Every response that
// is narrowed into a hand-written view validates each field it reads instead —
// see mapPreparation, mapLibrary, mapAttention, and mapCaseDetail. This is the
// one place in the file allowed to widen a validated document to a DTO type,
// which is why the call sites carry no cast of their own.
// The lint rule below objects that a type parameter used once is an assertion in
// disguise. It is, deliberately: this is the file's single declared assertion seam,
// and naming it here is what keeps the seven call sites free of casts of their own.
// eslint-disable-next-line @typescript-eslint/no-unnecessary-type-parameters
const schemaVersionOnly = <T>(value: unknown, name: string): T =>
  validateObject(value, name) as unknown as T
const mapDecisionAcknowledgement = (
  value: unknown,
  name: string,
  reviewCaseId: string,
): DecisionAcknowledgementView => {
  if (
    !record(value) ||
    !record(value.confirmation) ||
    !Array.isArray(value.invalidation_sequences) ||
    value.invalidation_sequences.length > 8 ||
    !value.invalidation_sequences.every(
      (sequence) => Number.isSafeInteger(sequence) && sequence > 0,
    )
  )
    throw new ApiError(502, 'invalid_decision_confirmation')
  const confirmation = value.confirmation
  let mapped: DecisionAcknowledgementView['confirmation']
  if (confirmation.status === 'unavailable') {
    if (!exactKeys(confirmation, ['status']))
      throw new ApiError(502, 'invalid_decision_confirmation')
    mapped = { status: 'unavailable' }
  } else if (confirmation.status === 'confirmed') {
    if (!exactKeys(confirmation, ['status', 'case']))
      throw new ApiError(502, 'invalid_decision_confirmation')
    const detail = mapCaseDetail(confirmation.case)
    if (detail.id !== reviewCaseId) throw new ApiError(502, 'invalid_decision_confirmation')
    mapped = { status: 'confirmed', case: detail }
  } else throw new ApiError(502, 'invalid_decision_confirmation')
  const wire = schemaVersionOnly<DecisionWireAcknowledgement>(value, name)
  return { ...wire, confirmation: mapped }
}

const preparationProgress = (value: unknown): PreparationProgress => {
  if (!record(value)) throw new ApiError(502, 'invalid_preparation_progress')
  if (value.scan_id !== null) stringValue(value.scan_id, 'preparation_scan_id')
  for (const field of ['connection', 'library', 'library_id'])
    stringValue(value[field], `preparation_${field}`)
  coverObservationValue(value.state, 'preparation_state', SCAN_STATES)
  for (const field of ['checked', 'total', 'ready', 'blocked', 'recorded', 'not_matched'])
    integerValue(value[field], `preparation_${field}`)
  if (typeof value.rescan_requested !== 'boolean')
    throw new ApiError(502, 'invalid_preparation_rescan_requested')
  // Validated: every field of the progress DTO is checked above, so the cast
  // only re-attaches the generated type to an already-verified document.
  return value as unknown as PreparationProgress
}
const preparationPresentation = (value: unknown) => {
  if (!record(value)) throw new ApiError(502, 'invalid_preparation_presentation')
  const runId = stringValue(value.run_id, 'preparation_run_id')
  if (!/^preparation_run_[0-9a-f]{64}$/u.test(runId))
    throw new ApiError(502, 'invalid_preparation_run_id')
  const state = stringValue(value.state, 'preparation_state')
  const terminal = ['completed_clean', 'completed_with_failures', 'dismissed'].includes(state)
  if (
    ![
      'starting',
      'running',
      'blocked',
      'completed_clean',
      'completed_with_failures',
      'dismissed',
    ].includes(state) ||
    !record(value.passage) ||
    !Array.isArray(value.triggers) ||
    !Array.isArray(value.active_books) ||
    value.active_books.length > 5 ||
    !Array.isArray(value.recent_completions) ||
    value.recent_completions.length > 8 ||
    typeof value.dismissible !== 'boolean' ||
    typeof value.throughput_per_minute !== 'number' ||
    !Number.isFinite(value.throughput_per_minute) ||
    value.throughput_per_minute < 0
  )
    throw new ApiError(502, 'invalid_preparation_presentation')
  const rawPassage = value.passage
  const passage = {
    read: integerValue(rawPassage.read, 'preparation_passage_read'),
    searchesFinished: integerValue(
      rawPassage.searches_finished,
      'preparation_passage_searches_finished',
    ),
    compared: integerValue(rawPassage.compared, 'preparation_passage_compared'),
    checked: integerValue(rawPassage.checked, 'preparation_passage_checked'),
    ready: integerValue(rawPassage.ready, 'preparation_passage_ready'),
    failed: integerValue(rawPassage.failed, 'preparation_passage_failed'),
  }
  if (
    passage.searchesFinished > passage.read ||
    passage.compared > passage.searchesFinished ||
    passage.checked > passage.compared ||
    passage.ready > passage.checked
  )
    throw new ApiError(502, 'invalid_preparation_passage_order')
  if (value.triggers.length < 1 || value.triggers.length > 4)
    throw new ApiError(502, 'invalid_preparation_trigger')
  const triggerFamilies = new Set<string>()
  for (const trigger of value.triggers) {
    if (!record(trigger)) throw new ApiError(502, 'invalid_preparation_trigger')
    if (
      !['initial_setup', 'manual_scan', 'library_added', 'title_recovery'].includes(
        stringValue(trigger.trigger, 'preparation_trigger'),
      )
    )
      throw new ApiError(502, 'invalid_preparation_trigger')
    const family = stringValue(trigger.trigger, 'preparation_trigger')
    if (triggerFamilies.has(family)) throw new ApiError(502, 'invalid_preparation_trigger')
    triggerFamilies.add(family)
    const acceptedCount = integerValue(trigger.accepted_count, 'preparation_trigger_accepted_count')
    const firstAccepted = integerValue(
      trigger.first_accepted_at,
      'preparation_trigger_first_accepted_at',
    )
    const lastAccepted = integerValue(
      trigger.last_accepted_at,
      'preparation_trigger_last_accepted_at',
    )
    if (acceptedCount < 1 || lastAccepted < firstAccepted)
      throw new ApiError(502, 'invalid_preparation_trigger')
  }
  const total = integerValue(value.total, 'preparation_total')
  const activeOverflow = integerValue(value.active_overflow, 'preparation_active_overflow')
  const startedAt = integerValue(value.started_at, 'preparation_started_at')
  const updatedAt = integerValue(value.updated_at, 'preparation_updated_at')
  const completedAt =
    value.completed_at === null
      ? null
      : integerValue(value.completed_at, 'preparation_completed_at')
  if (
    Object.values(passage).some((count) => count > total) ||
    value.active_books.length + activeOverflow > total ||
    updatedAt < startedAt ||
    (terminal && (completedAt === null || completedAt < startedAt)) ||
    (!terminal && completedAt !== null)
  )
    throw new ApiError(502, 'invalid_preparation_passage_total')
  for (const active of value.active_books) {
    if (!record(active)) throw new ApiError(502, 'invalid_preparation_active_book')
    boundedStringValue(active.name, 'preparation_active_name', 2048)
    boundedStringValue(active.stage, 'preparation_active_stage', 64)
  }
  for (const completion of value.recent_completions) {
    if (!record(completion)) throw new ApiError(502, 'invalid_preparation_completion')
    boundedStringValue(completion.name, 'preparation_completion_name', 2048)
    if (
      !['ready', 'failed'].includes(
        stringValue(completion.outcome, 'preparation_completion_outcome'),
      )
    )
      throw new ApiError(502, 'invalid_preparation_completion')
    integerValue(completion.completed_at, 'preparation_completion_completed_at')
  }
  // The run's works as they stand now; absent or null means not yet known.
  const now = value.now
  if (now === undefined || now === null) return
  if (
    !record(now) ||
    !exactKeys(now, ['ready', 'recorded', 'not_matched', 'failed', 'in_progress', 'scan_ids'])
  )
    throw new ApiError(502, 'invalid_preparation_now')
  for (const field of ['ready', 'recorded', 'not_matched', 'failed', 'in_progress'])
    integerValue(now[field], `preparation_now_${field}`)
  if (stringArray(now.scan_ids, 'preparation_now_scan_ids').length > 128)
    throw new ApiError(502, 'invalid_preparation_now_scan_ids')
}
const throttleAdapters = Object.keys(settingsVocabulary.provider_labels)
const throttleReasons = [
  'authentication',
  'authorization',
  'configuration',
  'rate_limited',
  'burst_throttled',
  'quota_exhausted',
  'transient',
] as const
const throttleEntryKeys = [
  'library',
  'adapter',
  'state',
  'reasons',
  'deferred',
  'next_retry_at',
  'remedy',
] as const
const providerThrottling = (value: unknown): ProviderThrottlingView | null => {
  if (value === null || value === undefined) return null
  if (
    !record(value) ||
    !exactKeys(value, ['generation', 'retry', 'entries']) ||
    !Array.isArray(value.entries) ||
    value.entries.length > 256 ||
    integerValue(value.generation, 'provider_throttling_generation') < 1
  )
    throw new ApiError(502, 'invalid_provider_throttling')
  coverObservationValue(value.retry, 'provider_throttling_retry', [
    'available',
    'managed_by_file',
    'unavailable',
  ])
  for (const entry of value.entries) {
    if (
      !record(entry) ||
      !exactKeys(entry, throttleEntryKeys) ||
      !Array.isArray(entry.reasons) ||
      entry.reasons.length > throttleReasons.length ||
      boundedStringValue(entry.library, 'provider_throttling_library', 64).length === 0
    )
      throw new ApiError(502, 'invalid_provider_throttling_entry')
    coverObservationValue(entry.adapter, 'provider_throttling_adapter', throttleAdapters)
    coverObservationValue(entry.state, 'provider_throttling_state', [
      'waiting',
      'dormant',
      'checking',
      'exhausted',
    ])
    for (const reason of entry.reasons)
      coverObservationValue(reason, 'provider_throttling_reason', throttleReasons)
    integerValue(entry.deferred, 'provider_throttling_deferred')
    if (entry.next_retry_at !== null)
      integerValue(entry.next_retry_at, 'provider_throttling_next_retry_at')
    if (entry.remedy !== null)
      coverObservationValue(entry.remedy, 'provider_throttling_remedy', [
        'add_api_key',
        'check_api_key',
      ])
  }
  // Validated: the envelope and every member of every entry are checked above.
  return value as unknown as ProviderThrottlingView
}
export const mapPreparation = (value: unknown): PreparationView => {
  if (
    !record(value) ||
    value.schema_version !== 'review-preparation-v2' ||
    !Array.isArray(value.libraries) ||
    !Array.isArray(value.connections) ||
    typeof value.paused !== 'boolean' ||
    typeof value.active !== 'boolean' ||
    typeof value.actionable !== 'boolean' ||
    typeof value.recovery_required !== 'boolean' ||
    (value.first_boot !== undefined && typeof value.first_boot !== 'boolean') ||
    (value.first_boot_complete !== undefined && typeof value.first_boot_complete !== 'boolean') ||
    (value.presentation !== null &&
      value.presentation !== undefined &&
      !record(value.presentation)) ||
    (value.first_boot_works !== undefined &&
      value.first_boot_works !== null &&
      (typeof value.first_boot_works !== 'number' ||
        !Number.isInteger(value.first_boot_works) ||
        value.first_boot_works < 0))
  )
    throw new ApiError(502, 'invalid_preparation_response')
  if (value.presentation !== null && value.presentation !== undefined)
    preparationPresentation(value.presentation)
  value.libraries.forEach((item, index) => {
    if (!record(item)) throw new ApiError(502, `invalid_preparation_library_${String(index)}`)
    preparationProgress(item)
  })
  value.connections.forEach((item, index) => {
    if (!record(item)) throw new ApiError(502, `invalid_preparation_connection_${String(index)}`)
    stringValue(item.connection, 'preparation_connection')
    if (
      !['tls', 'authentication', 'authorization', 'unavailable'].includes(
        stringValue(item.code, 'preparation_connection_code'),
      ) ||
      !['paused', 'checking', 'recovered'].includes(
        stringValue(item.state, 'preparation_connection_state'),
      )
    )
      throw new ApiError(502, `invalid_preparation_connection_${String(index)}`)
    integerValue(item.affected, 'preparation_connection_affected')
    integerValue(item.updated_at, 'preparation_connection_updated_at')
  })
  // Validated: the envelope, both collections, and every field inside them are
  // checked above, so the cast only re-attaches the generated type.
  return {
    ...value,
    first_boot: value.first_boot ?? false,
    first_boot_complete: value.first_boot_complete ?? false,
    first_boot_works: value.first_boot_works ?? null,
    provider_throttling: providerThrottling(value.provider_throttling),
  } as unknown as PreparationView
}
const activityPrepared = (value: unknown, field: string): ActivityPreparedView => {
  if (!record(value)) throw new ApiError(502, `invalid_${field}`)
  return {
    automatic: integerValue(value.automatic, `${field}_automatic`),
    human: integerValue(value.human, `${field}_human`),
    total: integerValue(value.total, `${field}_total`),
  }
}
const activityExample = (value: unknown): ActivityExampleView | null => {
  if (value === null) return null
  if (!record(value)) throw new ApiError(502, 'invalid_activity_example')
  return {
    after: value.after,
    before: value.before,
    destination: coverObservationValue(
      value.destination,
      'activity_example_destination',
      TARGET_DESTINATIONS,
    ),
    library: stringValue(value.library, 'activity_example_library'),
    work_id: stringValue(value.work_id, 'activity_example_work_id'),
    work_label: 'Work details unavailable',
  }
}
const activitySuspensions = (value: unknown): ActivitySuspensionView[] => {
  if (!Array.isArray(value)) throw new ApiError(502, 'invalid_activity_suspensions')
  return value.map((item, index) => {
    if (!record(item)) throw new ApiError(502, `invalid_activity_suspension_${String(index)}`)
    return {
      connection: stringValue(item.connection, 'activity_suspension_connection'),
      destination:
        item.destination === null
          ? null
          : stringValue(item.destination, 'activity_suspension_destination'),
      reason: coverObservationValue(item.reason, 'activity_suspension_reason', SUSPENSION_REASONS),
      scope: stringValue(item.scope, 'activity_suspension_scope'),
      updated_at: integerValue(item.updated_at, 'activity_suspension_updated_at'),
    }
  })
}
const activityLibraries = (raw: unknown, field: string): ActivityLibraryView[] => {
  if (!Array.isArray(raw)) throw new ApiError(502, `invalid_${field}`)
  return raw.map((item, index) => {
    if (!record(item)) throw new ApiError(502, `invalid_${field}_${String(index)}`)
    return {
      connection: stringValue(item.connection, `${field}_connection`),
      library: stringValue(item.library, `${field}_library`),
      ...(item.prepared === undefined
        ? {}
        : {
            prepared:
              item.prepared === null ? null : activityPrepared(item.prepared, `${field}_prepared`),
          }),
    }
  })
}

const activityCapabilities = (raw: unknown): ActivityCapabilityView[] => {
  if (!Array.isArray(raw)) throw new ApiError(502, 'invalid_activity_capabilities')
  return raw.map((item, index) => {
    if (!record(item) || !Array.isArray(item.destinations))
      throw new ApiError(502, `invalid_activity_capability_${String(index)}`)
    return {
      connection: stringValue(item.connection, 'activity_connection'),
      library: stringValue(item.library, 'activity_library'),
      state: coverObservationValue(item.state, 'activity_state', CAPABILITY_STATES),
      destinations: item.destinations.map((destination, destinationIndex) => {
        if (!record(destination) || typeof destination.consented !== 'boolean')
          throw new ApiError(502, `invalid_activity_destination_${String(destinationIndex)}`)
        return {
          destination: coverObservationValue(
            destination.destination,
            'activity_destination',
            TARGET_DESTINATIONS,
          ),
          state: coverObservationValue(
            destination.state,
            'activity_destination_state',
            DESTINATION_STATES,
          ),
          consented: destination.consented,
          prepared: integerValue(destination.prepared, 'activity_destination_prepared'),
        }
      }),
    }
  })
}

const activityBackupReady = (value: unknown): boolean | null => {
  if (value !== null && typeof value !== 'boolean')
    throw new ApiError(502, 'invalid_activity_backup_ready')
  return value
}

const activityWaiting = (raw: unknown): ActivityWaitingReasonsView => {
  if (!record(raw)) throw new ApiError(502, 'invalid_activity_waiting')
  return {
    applying: integerValue(raw.applying, 'activity_waiting_applying'),
    awaiting_permission: integerValue(
      raw.awaiting_permission,
      'activity_waiting_awaiting_permission',
    ),
    held_for_recovery: integerValue(raw.held_for_recovery, 'activity_waiting_held_for_recovery'),
    retrying: integerValue(raw.retrying, 'activity_waiting_retrying'),
    scheduled: integerValue(raw.scheduled, 'activity_waiting_scheduled'),
  }
}

const lapseReasons: readonly ActivityLapsedConsentView['reason'][] = [
  'destinations_changed',
  'library_unresolved',
  'policy_changed',
]
const activityLapses = (raw: unknown): ActivityLapsedConsentView[] => {
  if (!Array.isArray(raw)) throw new ApiError(502, 'invalid_activity_lapsed_consents')
  return raw.map((item, index) => {
    if (!record(item) || !exactKeys(item, ['connection', 'library', 'reason']))
      throw new ApiError(502, `invalid_activity_lapsed_consent_${String(index)}`)
    const reason = stringValue(item.reason, 'activity_lapsed_consent_reason')
    if (!lapseReasons.includes(reason as ActivityLapsedConsentView['reason']))
      throw new ApiError(502, 'invalid_activity_lapsed_consent_reason')
    return {
      connection: stringValue(item.connection, 'activity_lapsed_consent_connection'),
      library: stringValue(item.library, 'activity_lapsed_consent_library'),
      reason: reason as ActivityLapsedConsentView['reason'],
    }
  })
}

export const mapActivityConsent = (value: unknown): ActivityConsentView => {
  if (
    !record(value) ||
    !exactKeys(value, [
      'backup_ready',
      'capabilities',
      'configured_libraries',
      'lapsed_consents',
      'waiting',
    ])
  )
    throw new ApiError(502, 'invalid_activity_consent')
  return {
    backup_ready: nullValue(value.backup_ready, 'activity_backup_ready'),
    capabilities: activityCapabilities(value.capabilities),
    configured_libraries: activityLibraries(
      value.configured_libraries,
      'activity_configured_libraries',
    ),
    lapsed_consents: activityLapses(value.lapsed_consents),
    waiting: activityWaiting(value.waiting),
  }
}

export const mapActivityPermissionReadiness = (value: unknown): ActivityPermissionReadinessView => {
  if (
    !record(value) ||
    !exactKeys(value, [
      'backup_ready',
      'capabilities',
      'configured_libraries',
      'prepared',
      'example',
    ])
  )
    throw new ApiError(502, 'invalid_activity_permission_readiness')
  return {
    backup_ready: activityBackupReady(value.backup_ready),
    capabilities: activityCapabilities(value.capabilities),
    configured_libraries: activityLibraries(
      value.configured_libraries,
      'activity_configured_libraries',
    ),
    prepared: activityPrepared(value.prepared, 'activity_prepared'),
    example: activityExample(value.example),
  }
}

export const mapActivity = (value: unknown): ActivityDisplayView => {
  if (
    !record(value) ||
    value.schema_version !== 'continuous-field-activity-v3' ||
    !record(value.ledger)
  )
    throw new ApiError(502, 'invalid_activity_response')
  const capabilities = value.capabilities
  const events = value.events
  if (!Array.isArray(capabilities) || !Array.isArray(events) || !record(value.counts))
    throw new ApiError(502, 'invalid_activity_response')
  const counts: Record<string, number> = {}
  for (const [key, count] of Object.entries(value.counts))
    counts[stringValue(key, 'activity_count_name')] = integerValue(count, 'activity_count')
  const waiting = activityWaiting(value.waiting)
  const providerRecovery = value.provider_recovery === undefined ? [] : value.provider_recovery
  if (!Array.isArray(providerRecovery)) throw new ApiError(502, 'invalid_provider_recovery')
  const recovery = providerRecovery.map((item, index) => {
    if (
      !record(item) ||
      !['waiting', 'checking', 'dormant', 'recovered', 'fanout', 'exhausted'].includes(
        stringValue(item.state, `provider_recovery_state_${String(index)}`),
      )
    )
      throw new ApiError(502, `invalid_provider_recovery_${String(index)}`)
    const next =
      item.next_eligible_at === null
        ? null
        : integerValue(item.next_eligible_at, 'provider_recovery_next_eligible_at')
    if (next !== null && (!Number.isSafeInteger(next) || next < 0 || next > 8640000000000))
      throw new ApiError(502, 'invalid_provider_recovery_timestamp')
    const failureReasons =
      item.failure_reasons === undefined
        ? []
        : stringArray(item.failure_reasons, 'provider_recovery_failure_reasons')
    if (
      failureReasons.length > 7 ||
      new Set(failureReasons).size !== failureReasons.length ||
      failureReasons.some(
        (reason, position) =>
          ![
            'authentication',
            'authorization',
            'configuration',
            'rate_limited',
            'burst_throttled',
            'quota_exhausted',
            'transient',
          ].includes(reason) ||
          (position > 0 && (failureReasons[position - 1] ?? '') > reason),
      )
    )
      throw new ApiError(502, 'invalid_provider_recovery_failure_reasons')
    const rawDispatchReasons = item.dispatch_reasons
    const dispatchReasons =
      rawDispatchReasons === undefined
        ? undefined
        : (() => {
            if (
              !Array.isArray(rawDispatchReasons) ||
              rawDispatchReasons.length > 4 ||
              new Set(rawDispatchReasons).size !== rawDispatchReasons.length ||
              rawDispatchReasons.some(
                (reason) =>
                  ![
                    'missing_corroboration',
                    'zero_provider_evidence',
                    'settleable_disagreement',
                    'blanket_scan',
                  ].includes(typeof reason === 'string' ? reason : ''),
              )
            )
              throw new ApiError(502, `invalid_provider_recovery_dispatch_${String(index)}`)
            return rawDispatchReasons as ProviderRecoveryDispatchReason[]
          })()
    return {
      state: item.state as ProviderRecoveryActivityView['state'],
      affected_count: integerValue(item.affected_count, 'provider_recovery_affected_count'),
      ...(dispatchReasons === undefined ? {} : { dispatch_reasons: dispatchReasons }),
      failure_reasons: failureReasons as ProviderRecoveryFailureReason[],
      next_eligible_at: next,
    }
  })
  return {
    backup_ready: nullValue(value.backup_ready, 'activity_backup_ready'),
    capabilities: activityCapabilities(value.capabilities),
    waiting,
    configured_libraries: activityLibraries(
      value.configured_libraries,
      'activity_configured_libraries',
    ),
    consented_libraries: activityLibraries(
      value.consented_libraries,
      'activity_consented_libraries',
    ),
    counts,
    enabled_libraries: activityLibraries(value.enabled_libraries, 'activity_enabled_libraries'),
    lapsed_consents: activityLapses(value.lapsed_consents),
    provider_recovery: recovery,
    events: events.map((event, index) => {
      if (!record(event) || !Array.isArray(event.evidence))
        throw new ApiError(502, `invalid_activity_event_${String(index)}`)
      const occurred_at =
        event.occurred_at === undefined || event.occurred_at === null
          ? null
          : integerValue(event.occurred_at, 'activity_occurred_at')
      const failure =
        event.failure === undefined || event.failure === null
          ? null
          : stringValue(event.failure, 'activity_failure')
      const author_label =
        event.author_label === undefined
          ? undefined
          : stringValue(event.author_label, 'activity_author_label')
      const eventType = stringValue(event.event_type, 'activity_event_type')
      const category = stringValue(event.category, 'activity_event_category')
      const policyChanges = event.policy_changes === undefined ? [] : event.policy_changes
      const diagnostic: ActivityDiagnosticView | null =
        event.diagnostic === undefined || event.diagnostic === null
          ? null
          : (() => {
              if (!record(event.diagnostic)) throw new ApiError(502, 'invalid_activity_diagnostic')
              const eventClass = stringValue(
                event.diagnostic.event_class,
                'activity_diagnostic_event_class',
              )
              const reason = stringValue(event.diagnostic.reason, 'activity_diagnostic_reason')
              if (
                eventClass !== 'automatic_decision' ||
                reason !== 'automatic_authority_unavailable'
              )
                throw new ApiError(502, 'invalid_activity_diagnostic')
              return {
                event_class: 'automatic_decision',
                reason: 'automatic_authority_unavailable',
              }
            })()
      if (
        !['review', 'policy', 'metadata', 'safety', 'unknown'].includes(eventType) ||
        !['owner', 'automatic', 'policy', 'recovery'].includes(category) ||
        !Array.isArray(policyChanges) ||
        policyChanges.length > 32 ||
        event.evidence.length > 8
      )
        throw new ApiError(502, 'invalid_activity_event')
      return {
        category: category as ActivityCategory,
        change: stringValue(event.change, 'activity_event_change'),
        change_detail: stringValue(event.change_detail, 'activity_event_change_detail'),
        change_id:
          event.change_id === undefined
            ? stringValue(event.event_id, 'activity_event_id')
            : stringValue(event.change_id, 'activity_change_id'),
        check_group: optionalString(event.check_group, 'activity_check_group') ?? null,
        field_label: stringValue(event.field_label, 'activity_event_field_label'),
        policy_changes: policyChanges.map((item) => {
          if (!record(item)) throw new ApiError(502, 'invalid_activity_policy_change')
          return {
            field: stringValue(item.field, 'activity_policy_change_field'),
            setting: stringValue(item.setting, 'activity_policy_change_setting'),
            previous: stringValue(item.previous, 'activity_policy_change_previous'),
            next: stringValue(item.next, 'activity_policy_change_next'),
          }
        }),
        destination: stringValue(event.destination, 'activity_event_destination'),
        diagnostic,
        event_id: stringValue(event.event_id, 'activity_event_id'),
        event_type: eventType as ActivityEventType,
        evidence: event.evidence.map((item) => {
          if (!record(item)) throw new ApiError(502, 'invalid_activity_evidence')
          return {
            label: stringValue(item.label, 'activity_evidence_label'),
            value: stringValue(item.value, 'activity_evidence_value'),
          }
        }),
        library: stringValue(event.library, 'activity_event_library'),
        origin: stringValue(event.origin, 'activity_event_origin'),
        state: stringValue(event.state, 'activity_event_state'),
        ...(event.transition_id === undefined || event.transition_id === null
          ? {}
          : { transition_id: stringValue(event.transition_id, 'activity_transition_id') }),
        work_id: stringValue(event.work_id, 'activity_work_id'),
        work_label:
          event.work_label === undefined
            ? 'Recorded work'
            : stringValue(event.work_label, 'activity_work_label'),
        ...(author_label === undefined ? {} : { author_label }),
        occurred_at,
        failure,
      }
    }),
    example: nullValue(value.example, 'activity_example'),
    last_failure:
      value.last_failure === null ? null : stringValue(value.last_failure, 'activity_last_failure'),
    ledger: (() => {
      const item = value.ledger
      const eventType = stringValue(item.event_type, 'activity_ledger_type')
      const pageSize = integerValue(item.page_size, 'activity_page_size')
      const rangeDays = integerValue(item.range_days, 'activity_range_days')
      if (
        !['all', 'review', 'policy', 'metadata', 'safety', 'unknown'].includes(eventType) ||
        ![25, 50, 100].includes(pageSize) ||
        ![7, 30, 90].includes(rangeDays)
      )
        throw new ApiError(502, 'invalid_activity_ledger')
      return {
        event_type: eventType as ActivitySearch['eventType'],
        page: integerValue(item.page, 'activity_page'),
        page_count: integerValue(item.page_count, 'activity_page_count'),
        page_size: pageSize as ActivitySearch['pageSize'],
        query: item.query === null ? null : stringValue(item.query, 'activity_query'),
        range_days: rangeDays as ActivitySearch['rangeDays'],
        total: integerValue(item.total, 'activity_total'),
      }
    })(),
    current_waiting:
      value.current_waiting === null
        ? null
        : (() => {
            if (!record(value.current_waiting)) throw new ApiError(502, 'invalid_activity_waiting')
            return {
              destination: coverObservationValue(
                value.current_waiting.destination,
                'activity_waiting_destination',
                TARGET_DESTINATIONS,
              ),
              library: stringValue(value.current_waiting.library, 'activity_waiting_library'),
              state: stringValue(value.current_waiting.state, 'activity_waiting_state'),
              work_id: stringValue(value.current_waiting.work_id, 'activity_waiting_work_id'),
              work_label: stringValue(
                value.current_waiting.work_label,
                'activity_waiting_work_label',
              ),
            }
          })(),
    paused:
      typeof value.paused === 'boolean'
        ? value.paused
        : (() => {
            throw new ApiError(502, 'invalid_activity_paused')
          })(),
    prepared: activityPrepared(value.prepared, 'activity_prepared'),
    schema_version: 'continuous-field-activity-v3',
    suspensions: activitySuspensions(value.suspensions),
  }
}

const mapAudiobookReviewScope = (
  value: unknown,
  mediaKind: unknown,
  basis: unknown,
): 'work' | 'recording' | null => {
  if (value !== null && value !== 'work' && value !== 'recording')
    throw new ApiError(502, 'invalid_audiobook_review_scope')
  if (
    (!['m4b', 'm4a', 'mp3'].includes(String(mediaKind)) && value !== null) ||
    (basis !== null && value !== 'recording')
  )
    throw new ApiError(502, 'inconsistent_audiobook_review_scope')
  return value
}

const mapRecordingIdentityBasis = (value: unknown): RecordingIdentityBasisView | null => {
  if (value === null) return null
  if (
    !record(value) ||
    !exactKeys(value, ['basis', 'provider', 'candidate']) ||
    value.basis !== 'chapter_runtime_and_structure'
  )
    throw new ApiError(502, 'invalid_recording_identity_basis')
  const provider = boundedStringValue(value.provider, 'recording_identity_provider', 128)
  const candidate = boundedStringValue(value.candidate, 'recording_identity_candidate', 128)
  if (
    !provider.trim() ||
    !candidate.trim() ||
    Array.from(provider + candidate).some(
      (character) => character.charCodeAt(0) < 32 || character.charCodeAt(0) === 127,
    )
  )
    throw new ApiError(502, 'invalid_recording_identity_basis')
  return { basis: 'chapter_runtime_and_structure', provider, candidate }
}

export const mapReportDetail = (value: unknown): ReportDetailView => {
  if (
    !record(value) ||
    value.schema_version !== 'review-report-detail-v5' ||
    !record(value.embedded_cover) ||
    !Array.isArray(value.chapters) ||
    !Array.isArray(value.evidence) ||
    !Array.isArray(value.provider_sources) ||
    !Array.isArray(value.provider_evidence)
  )
    throw new ApiError(502, 'invalid_report_detail_response')
  const cover = value.embedded_cover
  if (typeof cover.present !== 'boolean' || typeof value.chapters_truncated !== 'boolean')
    throw new ApiError(502, 'invalid_report_detail_response')
  if (
    typeof value.evidence_truncated !== 'boolean' ||
    typeof value.provider_evidence_truncated !== 'boolean'
  )
    throw new ApiError(502, 'invalid_report_detail_response')
  return {
    chapters: (value.chapters as unknown[]).map((chapter, index) => {
      if (!record(chapter)) throw new ApiError(502, `invalid_report_chapter_${String(index)}`)
      return {
        endMilliseconds: integerValue(chapter.end_milliseconds, 'report_chapter_end'),
        ordinal: integerValue(chapter.ordinal, 'report_chapter_ordinal'),
        startMilliseconds: integerValue(chapter.start_milliseconds, 'report_chapter_start'),
        title: chapter.title == null ? null : stringValue(chapter.title, 'report_chapter_title'),
      }
    }),
    chaptersTruncated: value.chapters_truncated,
    coverPresent: cover.present,
    coverSurface:
      cover.surface == null
        ? null
        : coverObservationValue(cover.surface, 'report_cover_surface', AUDIO_SURFACES),
    evidence: (value.evidence as unknown[]).map((claim, index) => {
      if (!record(claim)) throw new ApiError(502, `invalid_report_evidence_${String(index)}`)
      return {
        basis: coverObservationValue(claim.basis, 'report_evidence_basis', CLAIM_BASES),
        excerpt: stringValue(claim.excerpt, 'report_evidence_excerpt'),
        kind: coverObservationValue(claim.kind, 'report_evidence_kind', CLAIM_KINDS),
        role: claim.role == null ? null : stringValue(claim.role, 'report_evidence_role'),
        state: coverObservationValue(claim.state, 'report_evidence_state', FIELD_STATES),
        value: stringValue(claim.value, 'report_evidence_value'),
      }
    }),
    evidenceTruncated: value.evidence_truncated,
    item: stringValue(value.item, 'report_item'),
    library: stringValue(value.library, 'report_library'),
    libraryDisplayName:
      optionalString(value.library_display_name, 'report_library_display_name') ??
      stringValue(value.library, 'report_library'),
    mediaHealth: mapMediaHealth(value.media_health, 'report_media_health'),
    recordingIdentityBasis: mapRecordingIdentityBasis(value.recording_identity_basis),
    audiobookReviewScope: mapAudiobookReviewScope(
      value.audiobook_review_scope,
      value.media_kind,
      value.recording_identity_basis,
    ),
    mediaKind: coverObservationValue(value.media_kind, 'report_media_kind', MEDIA_KINDS),
    providerSources: (value.provider_sources as unknown[]).map(mapProviderOutcome),
    providerEvidence: (value.provider_evidence as unknown[]).map((claim, index) => {
      if (!record(claim)) throw new ApiError(502, `invalid_report_provider_${String(index)}`)
      return {
        candidate: stringValue(claim.candidate, 'report_provider_candidate'),
        kind: stringValue(claim.kind, 'report_provider_kind'),
        provider: stringValue(claim.provider, 'report_provider_name'),
        role: claim.role == null ? null : stringValue(claim.role, 'report_provider_role'),
        state: stringValue(claim.state, 'report_provider_state'),
        value: claim.value == null ? null : stringValue(claim.value, 'report_provider_value'),
      }
    }),
    providerEvidenceTruncated: value.provider_evidence_truncated,
    reportId: stringValue(value.report_id, 'report_id'),
    title: stringValue(value.title, 'report_title'),
  }
}

export const mapLibrary = (value: unknown): LibraryView => {
  // The one token this build's library document is served under. Reading any
  // token the server happens to send would let a version the client was never
  // written against render as though it were understood, which is the failure
  // the sibling mappers already refuse; the library is held to the same rule.
  if (
    !record(value) ||
    value.schema_version !== 'review-library-v3' ||
    !record(value.counts) ||
    !Array.isArray(value.items)
  )
    throw new ApiError(502, 'invalid_library_response')
  const lens = coverObservationValue(value.lens, 'library_lens', ['all', ...attentionStates])
  // Validated at each read: this cast names the DTO's fields, and every one the
  // view model consumes passes through the validators below before it is used.
  const dto = value as unknown as LibraryDto
  const query = optionalString(dto.query, 'library_query')
  const nextCursor = optionalString(dto.next_cursor, 'library_cursor')
  const previousCursor = optionalString(dto.previous_cursor, 'library_previous_cursor')
  return {
    lens,
    ...(query ? { query } : {}),
    ...(nextCursor ? { nextCursor } : {}),
    ...(previousCursor ? { previousCursor } : {}),
    page: integerValue(dto.page, 'library_page'),
    counts: {
      attention: integerValue(dto.counts.needs_attention, 'attention_count'),
      all: integerValue(dto.counts.all, 'all_count'),
      clear: integerValue(dto.counts.no_attention_needed, 'clear_count'),
      notMatched: integerValue(dto.counts.not_matched, 'not_matched_count'),
    },
    works: dto.items.map((item) => {
      const reviewCaseId = optionalString(item.review_case_id, 'review_case_id')
      const preparation =
        item.preparation == null
          ? null
          : {
              attempt: integerValue(item.preparation.attempt, 'preparation_attempt'),
              diagnosticReference: stringValue(
                item.preparation.diagnostic_reference,
                'preparation_diagnostic_reference',
              ),
              mediaHealth: mapMediaHealth(
                item.preparation.media_health,
                'preparation_media_health',
              ),
              provider: stringValue(item.preparation.provider, 'preparation_provider'),
              reason: stringValue(item.preparation.reason, 'preparation_reason'),
              recovery: stringValue(item.preparation.recovery, 'preparation_recovery'),
              recoveryAttempt: integerValue(
                item.preparation.recovery_attempt,
                'preparation_recovery_attempt',
              ),
              stage: coverObservationValue(
                item.preparation.stage,
                'preparation_stage',
                PREPARATION_FAILURE_STAGES,
              ),
              status: coverObservationValue(
                item.preparation.status,
                'preparation_status',
                PREPARATION_FAILURE_STATUSES,
              ),
            }
      if (!Array.isArray(item.preparation_outcomes))
        throw new ApiError(502, 'invalid_preparation_outcomes')
      const preparationOutcomes = (item.preparation_outcomes as unknown[]).map(mapProviderOutcome)
      const workId = stringValue(item.work_id, 'work_id')
      const cover = item.cover == null ? undefined : mapLibraryCover(item.cover)
      const summary =
        item.summary == null
          ? null
          : {
              fields: (Array.isArray(item.summary.fields) ? item.summary.fields : []).map(
                (field, index) => {
                  if (!record(field)) throw new ApiError(502, `invalid_summary_${String(index)}`)
                  const state = coverObservationValue(
                    field.state,
                    `summary_${String(index)}_state`,
                    TARGET_FIELD_STATES,
                  )
                  return {
                    destination: coverObservationValue(
                      field.destination,
                      `summary_${String(index)}_destination`,
                      TARGET_DESTINATIONS,
                    ),
                    state,
                    value: targetValueText(state, field.value),
                  }
                },
              ),
              reason: stringValue(item.summary.reason, 'summary_reason'),
              state: coverObservationValue(item.summary.state, 'summary_state', workSummaryStates),
            }
      return {
        id: reviewCaseId ?? workId,
        ...(reviewCaseId ? { reviewCaseId } : {}),
        workId,
        title: stringValue(item.title, 'title'),
        authors: stringArray(item.authors, 'authors'),
        library: stringValue(item.library, 'library'),
        libraryDisplayName:
          optionalString(item.library_display_name, 'library_display_name') ??
          stringValue(item.library, 'library'),
        mediaKind: coverObservationValue(item.media_kind, 'media_kind', MEDIA_KINDS),
        attentionCount: integerValue(item.attention_count, 'attention_count'),
        attention: coverObservationValue(item.attention_state, 'attention_state', attentionStates),
        detailUrl: item.detail_url === null ? null : stringValue(item.detail_url, 'detail_url'),
        detailKind: (() => {
          // Read back through `unknown`, as `lens` above is: the DTO type says
          // what the server promises, and this is the check that it kept it.
          const kind: unknown = item.detail_kind
          if (kind === null) return null
          const named = stringValue(kind, 'detail_kind')
          if (named !== 'case' && named !== 'report') throw new ApiError(502, 'invalid_detail_kind')
          return named
        })(),
        preparation,
        preparationOutcomes,
        summary,
        ...(cover ? { cover } : {}),
      }
    }),
  }
}

// A cover is shown only from the route that serves the preview its report
// binds, named by that same digest, so a row cannot point the image elsewhere.
const mapLibraryCover = (value: unknown): LibraryCoverView => {
  if (!record(value)) throw new ApiError(502, 'invalid_library_cover')
  const previewSha256 = stringValue(value.preview_sha256, 'library_cover_preview')
  const width = integerValue(value.width, 'library_cover_width')
  const height = integerValue(value.height, 'library_cover_height')
  const url = stringValue(value.url, 'library_cover_url')
  const prefix = '/api/v1/library/cover?'
  if (
    !/^[0-9a-f]{64}$/u.test(previewSha256) ||
    width < 1 ||
    height < 1 ||
    !url.startsWith(prefix) ||
    new URLSearchParams(url.slice(prefix.length)).get('preview') !== previewSha256
  )
    throw new ApiError(502, 'invalid_library_cover')
  return { previewSha256, width, height, url }
}

export const mapAttention = (value: unknown): AttentionView => {
  if (!record(value) || !Array.isArray(value.items))
    throw new ApiError(502, 'invalid_attention_response')
  // Validated at each read: this cast names the DTO's fields, and every one the
  // view model consumes passes through the validators below before it is used.
  const dto = value as unknown as AttentionDto
  stringValue(dto.schema_version, 'schema_version')
  const query = optionalString(dto.query, 'attention_query')
  const nextCursor = optionalString(dto.next_cursor, 'attention_cursor')
  return {
    ...(query ? { query } : {}),
    ...(nextCursor ? { nextCursor } : {}),
    total: integerValue(dto.total, 'attention_total'),
    items: dto.items.map((item) => ({
      id: stringValue(item.review_case_id, 'review_case_id'),
      destination: coverObservationValue(item.destination, 'destination', TARGET_DESTINATIONS),
      title: stringValue(item.title, 'title'),
      authors: stringArray(item.authors, 'authors'),
      library: stringValue(item.library, 'library'),
      libraryDisplayName:
        optionalString(item.library_display_name, 'library_display_name') ??
        stringValue(item.library, 'library'),
      mediaKind: coverObservationValue(item.media_kind, 'media_kind', MEDIA_KINDS),
      classificationReason: stringValue(
        item.classification_reason,
        'attention_classification_reason',
      ),
      notices: stringArray(item.notices, 'notices'),
      provenance: stringArray(item.provenance, 'provenance'),
      workAttentionCount: integerValue(item.work_attention_count, 'work_attention_count'),
    })),
  }
}

const mapDescriptionCandidate = (value: unknown): Schema<'DescriptionAdviceCandidate'> | null => {
  if (value === null) return null
  if (
    !record(value) ||
    !exactKeys(value, [
      'schema_version',
      'candidate_id',
      'attempt_id',
      'request_identity',
      'request_digest',
      'instruction_digest',
      'schema_digest',
      'preferences_digest',
      'profile_digest',
      'engine',
      'model',
      'access_revision',
      'output',
      'provenance',
      'advised',
      'created_at',
    ]) ||
    value.schema_version !== 'description-advice-candidate-v1' ||
    value.provenance !== 'authored_content' ||
    value.advised !== true ||
    typeof value.candidate_id !== 'string' ||
    !/^description_candidate_[0-9a-f]{64}$/.test(value.candidate_id) ||
    typeof value.attempt_id !== 'string' ||
    !/^description_attempt_[0-9a-f]{32}$/.test(value.attempt_id) ||
    !record(value.output) ||
    !exactKeys(value.output, ['description', 'disposition']) ||
    !['ready', 'needs_review'].includes(String(value.output.disposition)) ||
    !['codex', 'openrouter'].includes(String(value.engine))
  )
    throw new ApiError(502, 'invalid_description_candidate')
  boundedStringValue(value.output.description, 'description_text', 16384)
  if (!String(value.output.description).trim()) throw new ApiError(502, 'invalid_description_text')
  boundedStringValue(value.model, 'description_model', 256)
  integerValue(value.created_at, 'description_created_at')
  integerValue(value.access_revision, 'description_access_revision')
  for (const name of [
    'request_identity',
    'request_digest',
    'instruction_digest',
    'schema_digest',
    'preferences_digest',
    'profile_digest',
  ])
    if (typeof value[name] !== 'string' || !/^[0-9a-f]{64}$/.test(value[name]))
      throw new ApiError(502, 'invalid_description_digest')
  return value as unknown as Schema<'DescriptionAdviceCandidate'>
}
const mapDescriptionBinding = (value: unknown): Schema<'DescriptionAdviceBinding'> => {
  if (
    !record(value) ||
    !exactKeys(value, [
      'review_case_id',
      'report_id',
      'proposal_set_id',
      'target_fingerprint',
      'policy_generation',
      'access_revision',
      'preferences_digest',
    ])
  )
    throw new ApiError(502, 'invalid_description_binding')
  for (const [name, prefix] of [
    ['review_case_id', 'review_case_'],
    ['report_id', 'report_'],
    ['proposal_set_id', 'proposal_set_'],
    ['target_fingerprint', ''],
    ['preferences_digest', ''],
  ] as const)
    if (typeof value[name] !== 'string' || !new RegExp(`^${prefix}[0-9a-f]{64}$`).test(value[name]))
      throw new ApiError(502, 'invalid_description_binding')
  for (const name of ['policy_generation', 'access_revision'])
    if (!Number.isSafeInteger(value[name]) || Number(value[name]) < 1)
      throw new ApiError(502, 'invalid_description_binding')
  return value as unknown as Schema<'DescriptionAdviceBinding'>
}
const mapDescriptionAdvice = (value: unknown): DescriptionAdviceView | null => {
  if (value === null || value === undefined) return null
  if (
    !record(value) ||
    !exactKeys(value, [
      'state',
      'failure',
      'engine',
      'model',
      'attempt_id',
      'current',
      'candidate',
      'binding',
      'selection',
      'selected_advice',
      'application_outcome',
    ]) ||
    !['requested', 'running', 'succeeded', 'failed', 'interrupted'].includes(String(value.state)) ||
    !(
      value.failure === null ||
      (typeof value.failure === 'string' &&
        ['unavailable', 'invalid_output', 'interrupted'].includes(value.failure))
    ) ||
    !['codex', 'openrouter'].includes(String(value.engine)) ||
    !record(value.binding)
  )
    throw new ApiError(502, 'invalid_description_advice')
  if (
    value.application_outcome !== null &&
    (typeof value.application_outcome !== 'string' ||
      !['verified', 'no_change_required'].includes(value.application_outcome) ||
      value.selection === null)
  )
    throw new ApiError(502, 'invalid_description_application_outcome')
  booleanValue(value.current, 'description_current')
  boundedStringValue(value.model, 'description_model', 256)
  if (
    typeof value.attempt_id !== 'string' ||
    !/^description_attempt_[0-9a-f]{32}$/.test(value.attempt_id)
  )
    throw new ApiError(502, 'invalid_description_attempt')
  mapDescriptionBinding(value.binding)
  const candidate = mapDescriptionCandidate(value.candidate)
  if (
    candidate &&
    (candidate.attempt_id !== value.attempt_id ||
      candidate.engine !== value.engine ||
      candidate.model !== value.model)
  )
    throw new ApiError(502, 'invalid_description_candidate_binding')
  if (
    value.selection !== null &&
    (!record(value.selection) ||
      !exactKeys(value.selection, [
        'schema_version',
        'decision_id',
        'candidate_id',
        'binding',
        'origin',
        'resolved_policy_id',
        'qualification_manifest_sha256',
        'idempotency_digest',
        'occurred_at',
        'destination',
        'outcome',
        'application',
      ]) ||
      value.selection.schema_version !== 'authored-description-decision-v1' ||
      !['human', 'automatic_policy'].includes(String(value.selection.origin)) ||
      value.selection.destination !== 'description' ||
      value.selection.outcome !== 'accept' ||
      value.selection.application !== 'guarded')
  )
    throw new ApiError(502, 'invalid_description_selection')
  if ((value.selection === null) !== (value.selected_advice === null))
    throw new ApiError(502, 'invalid_description_selection')
  let selectedCandidate: ReturnType<typeof mapDescriptionCandidate> = null
  let selectedAdviceBinding: ReturnType<typeof mapDescriptionBinding> | null = null
  if (value.selected_advice !== null) {
    if (
      !record(value.selected_advice) ||
      !exactKeys(value.selected_advice, ['candidate', 'binding', 'current'])
    )
      throw new ApiError(502, 'invalid_description_selection')
    booleanValue(value.selected_advice.current, 'description_selected_current')
    selectedCandidate = mapDescriptionCandidate(value.selected_advice.candidate)
    if (!selectedCandidate) throw new ApiError(502, 'invalid_description_selection')
    selectedAdviceBinding = mapDescriptionBinding(value.selected_advice.binding)
  }
  if (record(value.selection)) {
    const selection = value.selection
    const selectedBinding = mapDescriptionBinding(selection.binding)
    if (
      typeof selection.decision_id !== 'string' ||
      !new RegExp(
        `^${selection.origin === 'human' ? 'human' : 'automatic'}_decision_[0-9a-f]{64}$`,
      ).test(selection.decision_id) ||
      typeof selection.candidate_id !== 'string' ||
      !/^description_candidate_[0-9a-f]{64}$/.test(selection.candidate_id) ||
      selection.candidate_id !== selectedCandidate?.candidate_id ||
      selectedAdviceBinding === null ||
      Object.keys(selectedBinding).some(
        (key) =>
          selectedBinding[key as keyof typeof selectedBinding] !==
          selectedAdviceBinding[key as keyof typeof selectedBinding],
      )
    )
      throw new ApiError(502, 'invalid_description_selection')
    for (const name of ['qualification_manifest_sha256', 'idempotency_digest'])
      if (typeof selection[name] !== 'string' || !/^[0-9a-f]{64}$/.test(selection[name]))
        throw new ApiError(502, 'invalid_description_selection')
    if (
      typeof selection.resolved_policy_id !== 'string' ||
      !/^resolved_policy_[0-9a-f]{64}$/.test(selection.resolved_policy_id)
    )
      throw new ApiError(502, 'invalid_description_selection')
    integerValue(selection.occurred_at, 'description_selection_time')
  }
  return value as unknown as DescriptionAdviceView
}

export const mapCaseDetail = (value: unknown): CaseDetailView => {
  if (
    !record(value) ||
    value.schema_version !== 'review-case-detail-v6' ||
    !record(value.target) ||
    !Array.isArray(value.fields)
  )
    throw new ApiError(502, 'invalid_case_detail_response')
  // Validated at each read: this cast names the DTO's fields, and every one the
  // view model consumes passes through the validators below before it is used.
  const dto = value as unknown as CaseDetailDto
  if (typeof dto.current !== 'boolean') throw new ApiError(502, 'invalid_case_currentness')
  const authors = stringArray(dto.target.authors, 'target_authors')
  if (authors.length > 128 || authors.some((author) => codePointLength(author) > 512))
    throw new ApiError(502, 'invalid_target_authors')
  const library = boundedStringValue(dto.target.library, 'target_library', 512)
  const mediaKind = coverObservationValue(dto.target.media_kind, 'target_media_kind', MEDIA_KINDS)
  const libraryDisplayName =
    optionalString(dto.target.library_display_name, 'library_display_name') ?? library
  return {
    id: stringValue(dto.review_case_id, 'review_case_id'),
    current: dto.current,
    target: {
      connection: boundedStringValue(dto.target.connection, 'target_connection', 512),
      library,
      libraryDisplayName,
      item: boundedStringValue(dto.target.item, 'target_item', 512),
      mediaKind,
      title: boundedStringValue(dto.target.title, 'target_title', 512),
    },
    title: boundedStringValue(dto.target.title, 'target_title', 512),
    authors,
    comparisonNotices: stringArray(dto.comparison_notices, 'comparison_notices'),
    mediaHealth: mapMediaHealth(value.media_health, 'case_media_health'),
    recordingIdentityBasis: mapRecordingIdentityBasis(value.recording_identity_basis),
    audiobookReviewScope: mapAudiobookReviewScope(
      value.audiobook_review_scope,
      mediaKind,
      value.recording_identity_basis,
    ),
    writesConsented: booleanValue(dto.writes_consented, 'writes_consented'),
    fields: dto.fields.map((field) => {
      // The three fields dereferenced without a validator below: an entry that
      // is not an object, or that omits `current` or `proposed_values`, used to
      // surface as an unhandled TypeError instead of a rejected response.
      if (
        !record(field) ||
        !record(field.current) ||
        !Array.isArray(field.proposed_values) ||
        !Array.isArray(field.decision_actions)
      )
        throw new ApiError(502, 'invalid_case_detail_field')
      const decisionActions = field.decision_actions.map(decisionAction)
      if (
        new Set(decisionActions).size !== decisionActions.length ||
        (decisionActions.length !== 0 &&
          (decisionActions.length !== 2 ||
            decisionActions[0] !== 'keep_current' ||
            decisionActions[1] !== 'accept'))
      )
        throw new ApiError(502, 'invalid_case_detail_actions')
      return {
        classification: classificationValue(field.classification),
        classificationReason: stringValue(field.classification_reason, 'classification_reason'),
        destination: coverObservationValue(field.destination, 'destination', TARGET_DESTINATIONS),
        decisionActions,
        disposition: coverObservationValue(
          field.deterministic_disposition,
          'disposition',
          PROPOSAL_OUTCOMES,
        ),
        editable: booleanValue(field.editable, 'editable'),
        effectCapability: coverObservationValue(
          field.effect_capability,
          'effect_capability',
          EFFECT_CAPABILITIES,
        ),
        needsAttention: booleanValue(field.needs_attention, 'needs_attention'),
        manualHold: booleanValue(field.manual_hold, 'manual_hold'),
        navigationRole: navigationRole(field.navigation_role),
        // An empty or unreadable current value says which, in Audiobookshelf's
        // name, rather than one word for both (#1286).
        currentValue: targetValueText(
          coverObservationValue(field.current.state, 'current_state', TARGET_FIELD_STATES),
          field.current.value,
        ),
        currentRaw: field.current.value,
        proposedValues: field.proposed_values,
        choiceContexts: mapChoiceContexts(field.choice_contexts, field.proposed_values.length),
        proposalId: stringValue(field.proposal_id, 'proposal_id'),
        targetFingerprint: stringValue(field.target_fingerprint, 'target_fingerprint'),
        humanDecision:
          field.human_decision === null
            ? null
            : {
                ...field.human_decision,
                outcome: coverObservationValue(
                  field.human_decision.outcome,
                  'human_decision_outcome',
                  DECISION_OUTCOMES,
                ),
              },
        inventoryRole: inventoryRole(field.inventory_role),
        automaticDecision:
          field.automatic_decision?.destination === 'cover' || field.automatic_decision == null
            ? (field.automatic_decision ?? null)
            : {
                ...field.automatic_decision,
                outcome: coverObservationValue(
                  field.automatic_decision.outcome,
                  'automatic_decision_outcome',
                  AUTOMATIC_OUTCOMES,
                ),
              },
        automaticWithheldReason:
          field.automatic_withheld_reason == null
            ? null
            : boundedStringValue(field.automatic_withheld_reason, 'automatic_withheld_reason', 512),
        automaticSelectionSources: stringArray(
          field.automatic_selection_sources,
          'automatic_selection_sources',
        ),
        notices: stringArray(field.notices, 'notices'),
        provenance: stringArray(field.provenance, 'provenance'),
        preservationOnly: booleanValue(field.preservation_only, 'preservation_only'),
        corroborationSources: stringArray(field.corroboration_sources, 'corroboration_sources'),
        // `record(field)` above widens the entry to a string-keyed record, which
        // is how this reads a field the generated DTO does not declare;
        // mapCoverConsideration then validates the value in full.
        descriptionAdvice: mapDescriptionAdvice(field.description_advice),
        ownerControl: mapOwnerControl(field.owner_control),
        coverConsideration: mapCoverConsideration(field.cover_consideration),
        currentCoverSelectionArtifactId:
          optionalString(
            field.current_cover_selection_artifact_id,
            'current_cover_selection_artifact_id',
          ) ?? null,
        currentCoverSelectionId:
          optionalString(field.current_cover_selection_id, 'current_cover_selection_id') ?? null,
      }
    }),
  }
}

const mapOwnerControl = (value: unknown): OwnerControlView | null => {
  if (value === null) return null
  if (!record(value) || !exactKeys(value, ['entry', 'subset', 'withdraw']))
    throw new ApiError(502, 'invalid_owner_control')
  const { entry, subset, withdraw } = value
  if (
    (entry !== null &&
      (!record(entry) ||
        !exactKeys(entry, ['max_characters', 'suggestions']) ||
        !Array.isArray(entry.suggestions) ||
        entry.suggestions.length > 5)) ||
    (subset !== null &&
      (!record(subset) ||
        !exactKeys(subset, ['max_items', 'options']) ||
        !Array.isArray(subset.options) ||
        subset.options.length === 0 ||
        subset.options.length > 160))
  )
    throw new ApiError(502, 'invalid_owner_control')
  return {
    entry:
      entry === null
        ? null
        : {
            maxCharacters: integerValue(entry.max_characters, 'owner_entry_max_characters'),
            suggestions: (entry.suggestions as unknown[]).map((item) => {
              if (!record(item) || !exactKeys(item, ['rule', 'value']))
                throw new ApiError(502, 'invalid_owner_suggestion')
              return {
                rule: coverObservationValue(item.rule, 'owner_suggestion_rule', [
                  'title_case',
                  'strip_part',
                  'split_author',
                  'combined',
                  'previous',
                ] as const),
                value: boundedStringValue(item.value, 'owner_suggestion_value', 512),
              }
            }),
          },
    subset:
      subset === null
        ? null
        : {
            maxItems: integerValue(subset.max_items, 'owner_subset_max_items'),
            options: (subset.options as unknown[]).map((item) => {
              if (!record(item) || !exactKeys(item, ['value', 'current', 'proposed', 'previous']))
                throw new ApiError(502, 'invalid_owner_subset_option')
              return {
                value: boundedStringValue(item.value, 'owner_subset_value', 1024),
                current: booleanValue(item.current, 'owner_subset_current'),
                proposed: booleanValue(item.proposed, 'owner_subset_proposed'),
                previous: booleanValue(item.previous, 'owner_subset_previous'),
              }
            }),
          },
    withdraw:
      withdraw === null
        ? null
        : coverObservationValue(withdraw, 'owner_withdraw', [
            'available',
            'automatic_hold',
            'in_progress',
            'written',
          ] as const),
  }
}

export const mapEvidence = (value: unknown): EvidenceView => {
  if (
    !record(value) ||
    value.schema_version !== 'review-case-evidence-v4' ||
    !Array.isArray(value.items) ||
    value.items.length > 50 ||
    !Array.isArray(value.source_ledger) ||
    value.source_ledger.length > 100
  )
    throw new ApiError(502, 'invalid_evidence_response')
  stringValue(value.review_case_id, 'evidence_review_case_id')
  const nextCursor = optionalString(value.next_cursor, 'evidence_next_cursor')
  return {
    items: value.items.map((item, index) => {
      if (!record(item)) throw new ApiError(502, `invalid_evidence_item_${String(index)}`)
      const role = item.role
      if (
        role !== 'external_corroboration' &&
        role !== 'external_observation' &&
        role !== 'local_presence'
      )
        throw new ApiError(502, `invalid_evidence_item_${String(index)}_role`)
      return {
        destinations: (() => {
          if (
            !Array.isArray(item.destinations) ||
            item.destinations.length === 0 ||
            item.destinations.length > 16
          )
            throw new ApiError(502, `invalid_evidence_item_${String(index)}_destinations`)
          const destinations = item.destinations.map((destination, destinationIndex) =>
            coverObservationValue(
              destination,
              `evidence_item_${String(index)}_destination_${String(destinationIndex)}`,
              TARGET_DESTINATIONS,
            ),
          )
          if (new Set(destinations).size !== destinations.length)
            throw new ApiError(502, `invalid_evidence_item_${String(index)}_destinations`)
          return destinations
        })(),
        observation: boundedStringValue(
          item.observation,
          `evidence_item_${String(index)}_observation`,
          1024,
        ),
        role,
        source: boundedStringValue(item.source, `evidence_item_${String(index)}_source`, 128),
      }
    }),
    missingLineageCount: (() => {
      const count = integerValue(value.missing_lineage_count, 'evidence_missing_lineage_count')
      if (count > 10_000) throw new ApiError(502, 'invalid_evidence_missing_lineage_count')
      return count
    })(),
    ...(nextCursor ? { nextCursor } : {}),
    sourceLedger: value.source_ledger.map(mapProviderOutcome),
  }
}

const mapHistoryValue = (value: unknown, field: string): HistoryValueView | null => {
  if (value === null) return null
  if (!record(value)) throw new ApiError(502, `invalid_${field}`)
  const kind = boundedStringValue(value.kind, `${field}_kind`, 64)
  const retained = value.value
  if (typeof retained === 'string') {
    if (codePointLength(retained) > 16_384) throw new ApiError(502, `invalid_${field}_value`)
    return { kind, value: retained }
  }
  if (typeof retained === 'boolean') return { kind, value: retained }
  if (!Array.isArray(retained) || retained.length > 128)
    throw new ApiError(502, `invalid_${field}_value`)
  if (retained.every((item) => typeof item === 'string' && codePointLength(item) <= 512))
    return { kind, value: retained }
  if (
    retained.every(
      (item) =>
        record(item) &&
        typeof item.name === 'string' &&
        codePointLength(item.name) <= 512 &&
        Object.hasOwn(item, 'sequence') &&
        (item.sequence === null ||
          (typeof item.sequence === 'string' && codePointLength(item.sequence) <= 512)),
    )
  )
    return { kind, value: retained as { name: string; sequence: string | null }[] }
  throw new ApiError(502, `invalid_${field}_value`)
}

export const mapHistory = (value: unknown): HistoryView => {
  if (
    !record(value) ||
    value.schema_version !== 'review-case-history-v3' ||
    !Array.isArray(value.items) ||
    value.items.length > 150
  )
    throw new ApiError(502, 'invalid_history_response')
  stringValue(value.review_case_id, 'history_review_case_id')
  const nextCursor = optionalString(value.next_cursor, 'history_next_cursor')
  return {
    items: value.items.map((item, index): HistoryItemView => {
      if (!record(item)) throw new ApiError(502, `invalid_history_item_${String(index)}`)
      const kind = stringValue(item.kind, `history_item_${String(index)}_kind`)
      const occurredAt = Object.hasOwn(item, 'checkpoint')
        ? null
        : integerValue(item.occurred_at, `history_item_${String(index)}_occurred_at`)
      if (occurredAt !== null && occurredAt > 8640000000000)
        throw new ApiError(502, 'invalid_history_time')
      if (
        kind === 'description_advice_recorded' ||
        kind === 'description_selected' ||
        kind === 'description_application'
      ) {
        integerValue(item.occurred_at, 'description_history_at')
        return {
          category: 'description',
          occurredAt,
          outcome: kind,
          state: coverObservationValue(item.state, 'description_state', DESCRIPTION_STATES),
          model: boundedStringValue(item.model, 'description_model', 256),
          engine: boundedStringValue(item.engine, 'description_engine', 32),
          candidate: mapDescriptionCandidate(item.candidate),
          selected: kind === 'description_selected',
        }
      }
      if (kind === 'human_decision_recorded' || kind === 'automatic_decision_recorded') {
        integerValue(item.occurred_at, `history_item_${String(index)}_occurred_at`)
        const outcome = `history_item_${String(index)}_outcome`
        const decision = {
          occurredAt,
          destination: coverObservationValue(
            item.destination,
            `history_item_${String(index)}_destination`,
            TARGET_DESTINATIONS,
          ),
          previousValue: mapHistoryValue(
            item.previous_value,
            `history_item_${String(index)}_previous_value`,
          ),
          selectedValue: mapHistoryValue(
            item.selected_value,
            `history_item_${String(index)}_selected_value`,
          ),
        }
        // Only an owner decision states what it may go on to do; a retained
        // F45 decision is the only one that carries a reason (#968).
        return kind === 'human_decision_recorded'
          ? {
              category: 'owner' as const,
              applicationReason:
                optionalString(
                  item.application_reason,
                  `history_item_${String(index)}_application_reason`,
                ) ?? null,
              outcome: coverObservationValue(item.outcome, outcome, DECISION_OUTCOMES),
              ...decision,
            }
          : {
              category: 'automatic' as const,
              outcome: coverObservationValue(item.outcome, outcome, AUTOMATIC_OUTCOMES),
              ...decision,
            }
      }
      if (kind === 'shadow_preview_recorded') {
        integerValue(item.occurred_at, `history_item_${String(index)}_occurred_at`)
        if (!record(item.dispositions) || Object.keys(item.dispositions).length > 32)
          throw new ApiError(502, `invalid_history_item_${String(index)}_dispositions`)
        return {
          category: 'preview',
          occurredAt,
          dispositions: Object.entries(item.dispositions).map(([outcome, count]) => ({
            count: integerValue(count, `history_item_${String(index)}_disposition`),
            outcome: coverObservationValue(
              outcome,
              `history_item_${String(index)}_disposition_name`,
              SHADOW_DISPOSITIONS,
            ),
          })),
        }
      }
      if (
        kind === 'automatic_prepared' ||
        kind === 'owner_retry_prepared' ||
        (kind === 'superseded' && Object.hasOwn(item, 'consideration')) ||
        kind === 'selected' ||
        kind === 'kept_incumbent' ||
        kind === 'no_action' ||
        kind === 'stale'
      ) {
        integerValue(item.occurred_at, `history_item_${String(index)}_occurred_at`)
        if (
          !record(item.consideration) ||
          !Array.isArray(item.consideration.candidates) ||
          item.consideration.candidates.length > 12
        )
          throw new ApiError(502, `invalid_history_item_${String(index)}_consideration`)
        const policy = item.consideration.policy
        if (policy !== null && !record(policy))
          throw new ApiError(502, `invalid_history_item_${String(index)}_policy`)
        const decision = item.decision
        if (decision !== null && !record(decision))
          throw new ApiError(502, `invalid_history_item_${String(index)}_decision`)
        const decisionOutcome = decision
          ? boundedStringValue(
              decision.outcome,
              `history_item_${String(index)}_decision_outcome`,
              64,
            )
          : undefined
        const ownerOutcome =
          decisionOutcome === 'accept' ||
          decisionOutcome === 'keep_current' ||
          decisionOutcome === 'reject'
            ? decisionOutcome
            : undefined
        const automaticOutcome = decisionOutcome === 'candidate' ? decisionOutcome : undefined
        if (decisionOutcome && !ownerOutcome && !automaticOutcome)
          throw new ApiError(502, `invalid_history_item_${String(index)}_decision_outcome`)
        const supersedesAutomatic =
          automaticOutcome !== undefined &&
          optionalString(
            decision?.supersedes_decision_id,
            `history_item_${String(index)}_supersedes_decision_id`,
          ) !== undefined
        const policyReasons = policy
          ? stringArray(policy.reasons, `history_item_${String(index)}_policy_reasons`)
          : []
        if (
          policyReasons.length > 32 ||
          policyReasons.some((reason) => codePointLength(reason) > 128)
        )
          throw new ApiError(502, `invalid_history_item_${String(index)}_policy_reasons`)
        const reviewerStatus = coverObservationValue(
          item.consideration.reviewer_status,
          `history_item_${String(index)}_reviewer_status`,
          ['completed', 'failed', 'unavailable'] as const,
        )
        return {
          category: 'cover',
          occurredAt,
          ...(automaticOutcome ? { automaticOutcome } : {}),
          candidateCount: item.consideration.candidates.length,
          ...(item.consideration.reviewer_model == null
            ? {}
            : {
                reviewerModel: boundedStringValue(
                  item.consideration.reviewer_model,
                  'history_reviewer_model',
                  256,
                ),
              }),
          outcome: coverObservationValue(
            kind,
            `history_item_${String(index)}_kind`,
            COVER_HISTORY_OUTCOMES,
          ),
          ...(ownerOutcome
            ? {
                ownerOutcome,
                ...(item.consideration.candidates.some(
                  (candidate) =>
                    record(candidate) &&
                    (ownerOutcome === 'keep_current'
                      ? candidate.incumbent === true
                      : ownerOutcome === 'accept' &&
                        record(decision?.selected_value) &&
                        candidate.artifact_id === decision.selected_value.value) &&
                    candidate.match_state === 'match_not_confirmed',
                )
                  ? { ownerMatchUnconfirmed: true }
                  : {}),
              }
            : {}),
          policyReasons,
          ...(policy
            ? {
                policyStatus: coverObservationValue(
                  policy.status,
                  `history_item_${String(index)}_policy_status`,
                  ['recommended', 'abstained'] as const,
                ),
              }
            : {}),
          reviewerStatus,
          supersedesAutomatic,
        }
      }
      integerValue(item.checkpoint, `history_item_${String(index)}_checkpoint`)
      return {
        category: 'transition',
        occurredAt,
        outcome: coverObservationValue(
          kind,
          `history_item_${String(index)}_kind`,
          TRANSITION_OUTCOMES,
        ),
        reason: boundedStringValue(item.reason, `history_item_${String(index)}_reason`, 512),
      }
    }),
    ...(nextCursor ? { nextCursor } : {}),
  }
}

const mapChoiceContexts = (
  value: unknown,
  proposedValueCount: number,
): readonly ChoiceContextView[] => {
  if (!Array.isArray(value)) throw new ApiError(502, 'invalid_choice_contexts')
  const contexts = value.map((item, index) => {
    if (!record(item) || !Array.isArray(item.evidence))
      throw new ApiError(502, `invalid_choice_context_${String(index)}`)
    const selection = stringValue(item.selection, `choice_context_${String(index)}_selection`)
    const recommendation = stringValue(
      item.recommendation,
      `choice_context_${String(index)}_recommendation`,
    )
    if (
      !['current', 'proposed'].includes(selection) ||
      !['no_recommendation', 'recommended'].includes(recommendation)
    )
      throw new ApiError(502, `invalid_choice_context_${String(index)}_state`)
    return {
      alternativeId:
        item.alternative_id === null
          ? null
          : stringValue(item.alternative_id, `choice_context_${String(index)}_alternative`),
      evidence: item.evidence.map((row, rowIndex) => {
        if (!record(row))
          throw new ApiError(502, `invalid_choice_evidence_${String(index)}_${String(rowIndex)}`)
        const role = stringValue(
          row.role,
          `choice_evidence_${String(index)}_${String(rowIndex)}_role`,
        )
        if (!['local_presence', 'external_corroboration', 'external_observation'].includes(role))
          throw new ApiError(
            502,
            `invalid_choice_evidence_${String(index)}_${String(rowIndex)}_role`,
          )
        return {
          source: stringValue(
            row.source,
            `choice_evidence_${String(index)}_${String(rowIndex)}_source`,
          ),
          observation: stringValue(
            row.observation,
            `choice_evidence_${String(index)}_${String(rowIndex)}_observation`,
          ),
          role: role as ChoiceEvidenceView['role'],
        }
      }),
      limitations: stringArray(item.limitations, `choice_context_${String(index)}_limitations`),
      rationale: stringValue(item.rationale, `choice_context_${String(index)}_rationale`),
      recommendation: recommendation as ChoiceContextView['recommendation'],
      recommendationReason: stringValue(
        item.recommendation_reason,
        `choice_context_${String(index)}_recommendation_reason`,
      ),
      selection: selection as ChoiceContextView['selection'],
    }
  })
  const [current, ...proposed] = contexts
  if (
    current?.selection !== 'current' ||
    current.alternativeId !== null ||
    proposed.length < proposedValueCount ||
    proposed.some(
      (context) => context.selection !== 'proposed' || context.alternativeId === null,
    ) ||
    new Set(proposed.map((context) => context.alternativeId)).size !== proposed.length
  )
    throw new ApiError(502, 'misaligned_choice_contexts')
  return contexts
}

const coverObservationValue = <T extends string>(
  value: unknown,
  label: string,
  allowed: readonly T[],
): T => {
  const result = stringValue(value, label)
  if (!allowed.includes(result as T)) throw new ApiError(502, `invalid_${label}`)
  return result as T
}

const mapCoverObservation = (
  value: unknown,
  candidateReference: string,
  previewSha256: string,
): CoverObservationView | null => {
  if (value === null) return null
  if (!record(value)) throw new ApiError(502, 'invalid_cover_observation')
  const keys = [
    'candidate_reference',
    'composition',
    'cover_text_language',
    'explicit_imagery',
    'format_branding',
    'medium',
    'overlay_marking',
    'placeholder',
    'preview_sha256',
    'preview_variant',
    'promotional_text',
    'reproduction',
    'series_styling',
    'thumbnail_legibility',
    'tie_in_branding',
  ]
  if (Object.keys(value).length !== keys.length || keys.some((key) => !Object.hasOwn(value, key)))
    throw new ApiError(502, 'invalid_cover_observation_shape')
  const boundReference = stringValue(value.candidate_reference, 'cover_observation_reference')
  const boundPreview = stringValue(value.preview_sha256, 'cover_observation_preview')
  if (boundReference !== candidateReference || boundPreview !== previewSha256)
    throw new ApiError(502, 'misaligned_cover_observation')
  return {
    candidateReference: boundReference,
    composition: coverObservationValue(value.composition, 'cover_observation_composition', [
      'dense',
      'minimal',
      'unknown',
    ]),
    coverTextLanguage: coverObservationValue(
      value.cover_text_language,
      'cover_observation_text_language',
      ['english', 'non_english', 'multiple', 'no_visible_title', 'unknown'],
    ),
    explicitImagery: coverObservationValue(
      value.explicit_imagery,
      'cover_observation_explicit_imagery',
      ['absent', 'suggestive', 'explicit', 'unknown'],
    ),
    formatBranding: coverObservationValue(
      value.format_branding,
      'cover_observation_format_branding',
      ['absent', 'text_edition', 'audiobook', 'unknown'],
    ),
    medium: coverObservationValue(value.medium, 'cover_observation_medium', [
      'illustrated',
      'mixed',
      'photographic',
      'typographic',
      'unknown',
    ]),
    overlayMarking: coverObservationValue(
      value.overlay_marking,
      'cover_observation_overlay_marking',
      ['absent', 'present', 'unknown'],
    ),
    placeholder: coverObservationValue(value.placeholder, 'cover_observation_placeholder', [
      'absent',
      'present',
      'unknown',
    ]),
    previewSha256: boundPreview,
    previewVariant: stringValue(value.preview_variant, 'cover_observation_preview_variant'),
    promotionalText: coverObservationValue(
      value.promotional_text,
      'cover_observation_promotional_text',
      ['absent', 'present', 'unknown'],
    ),
    reproduction: coverObservationValue(value.reproduction, 'cover_observation_reproduction', [
      'clean',
      'degraded',
      'unknown',
    ]),
    seriesStyling: coverObservationValue(value.series_styling, 'cover_observation_series_styling', [
      'standalone',
      'series_branded',
      'omnibus_or_set',
      'unknown',
    ]),
    thumbnailLegibility: coverObservationValue(
      value.thumbnail_legibility,
      'cover_observation_thumbnail_legibility',
      ['legible', 'illegible', 'unknown'],
    ),
    tieInBranding: coverObservationValue(
      value.tie_in_branding,
      'cover_observation_tie_in_branding',
      ['absent', 'present', 'unknown'],
    ),
  }
}

export const mapCoverConsideration = (value: unknown): CoverConsiderationView | null => {
  if (value === null || value === undefined) return null
  if (
    !record(value) ||
    value.schema_version !== 'cover-consideration-v4' ||
    !Array.isArray(value.candidates) ||
    !Array.isArray(value.omissions) ||
    !record(value.incumbent) ||
    !record(value.policy_preferences)
  )
    throw new ApiError(502, 'invalid_cover_consideration')
  const incumbentState = stringValue(value.incumbent.state, 'cover_incumbent_state')
  if (!['absent', 'available', 'unavailable'].includes(incumbentState))
    throw new ApiError(502, 'invalid_cover_incumbent_state')
  const preferences = value.policy_preferences
  if (
    typeof preferences.avoid_promotional_text !== 'boolean' ||
    typeof preferences.avoid_tie_in_branding !== 'boolean' ||
    typeof preferences.prefer_minimal_composition !== 'boolean'
  )
    throw new ApiError(502, 'invalid_cover_policy_preferences')
  if (typeof value.provider_fetching !== 'boolean' || typeof value.ready !== 'boolean')
    throw new ApiError(502, 'invalid_cover_readiness')
  const providerFetching = value.provider_fetching
  const ready = value.ready
  const candidates = value.candidates.map((candidate, index): CoverCandidateView => {
    if (
      !record(candidate) ||
      !record(candidate.identity) ||
      (candidate.observation !== null && !record(candidate.observation))
    )
      throw new ApiError(502, `invalid_cover_candidate_${String(index)}`)
    const state = stringValue(candidate.state, 'cover_state')
    if (
      state !== 'acceptable' &&
      state !== 'available' &&
      state !== 'excluded' &&
      state !== 'recommended'
    )
      throw new ApiError(502, 'invalid_cover_candidate_state')
    const matchStateRaw = stringValue(candidate.match_state, 'cover_match_state')
    if (!['same_edition', 'same_book', 'match_not_confirmed'].includes(matchStateRaw))
      throw new ApiError(502, 'invalid_cover_match_state')
    const matchState: CoverCandidateView['matchState'] =
      matchStateRaw === 'same_edition'
        ? 'same_edition'
        : matchStateRaw === 'same_book'
          ? 'same_book'
          : 'match_not_confirmed'
    const deterministicRank =
      candidate.deterministic_rank === null
        ? null
        : integerValue(candidate.deterministic_rank, 'cover_deterministic_rank')
    const deterministicRankKey =
      candidate.deterministic_rank_key === null
        ? null
        : Array.isArray(candidate.deterministic_rank_key) &&
            candidate.deterministic_rank_key.length === 7 &&
            candidate.deterministic_rank_key.slice(0, 6).every((item) => Number.isInteger(item)) &&
            candidate.deterministic_rank_key[6] === candidate.reference
          ? (candidate.deterministic_rank_key as readonly (number | string)[])
          : (() => {
              throw new ApiError(502, 'invalid_cover_deterministic_rank_key')
            })()
    const identityOutcome = stringValue(candidate.identity.outcome, 'cover_identity_outcome')
    if (
      ![
        'verified_manifestation_match',
        'verified_work_match',
        'plausible_match',
        'unknown',
        'conflict',
      ].includes(identityOutcome)
    )
      throw new ApiError(502, 'invalid_cover_identity_outcome')
    const expectedMatchState =
      identityOutcome === 'verified_manifestation_match'
        ? 'same_edition'
        : identityOutcome === 'verified_work_match'
          ? 'same_book'
          : 'match_not_confirmed'
    const confirmed = expectedMatchState !== 'match_not_confirmed'
    const incumbent = candidate.incumbent === true
    const selectable = candidate.selectable === true
    const manualEligible = booleanValue(candidate.manual_eligible, 'cover_manual_eligible')
    const identityReasons = stringArray(candidate.identity.reasons, 'cover_identity_reasons')
    if (
      manualEligible !==
      (!incumbent &&
        identityOutcome !== 'conflict' &&
        !identityReasons.includes('retained_creator_or_contributor_disagreement'))
    )
      throw new ApiError(502, 'contradictory_cover_manual_eligibility')
    const reference = stringValue(candidate.reference, 'cover_reference')
    const previewSha256 = stringValue(candidate.preview_sha256, 'cover_preview_digest')
    const observation = mapCoverObservation(candidate.observation, reference, previewSha256)
    if (
      matchState !== expectedMatchState ||
      (!confirmed && (selectable || deterministicRank !== null || deterministicRankKey !== null)) ||
      (confirmed &&
        incumbent &&
        (selectable || deterministicRank !== null || deterministicRankKey === null)) ||
      (confirmed && !incumbent && deterministicRankKey === null)
    )
      throw new ApiError(502, 'contradictory_cover_identity')
    return {
      artifactId: stringValue(candidate.artifact_id, 'cover_artifact_id'),
      deterministicCitations: stringArray(
        candidate.deterministic_citations,
        'cover_deterministic_citations',
      ),
      deterministicRank,
      deterministicRankKey,
      height: integerValue(candidate.height, 'cover_height'),
      identity: candidate.identity,
      incumbent,
      matchState,
      observation,
      originalSha256: stringValue(candidate.original_sha256, 'cover_original_digest'),
      ordinal: integerValue(candidate.ordinal, 'cover_ordinal'),
      previewSha256,
      reference,
      selectable,
      manualEligible,
      sourceKinds: stringArray(candidate.source_kinds, 'cover_source_kinds').map((kind) =>
        coverObservationValue(kind, 'cover_source_kind', COVER_SOURCE_KINDS),
      ),
      state,
      width: integerValue(candidate.width, 'cover_width'),
    }
  })
  if (
    new Set(candidates.map((candidate) => candidate.reference)).size !== candidates.length ||
    new Set(candidates.map((candidate) => candidate.artifactId)).size !== candidates.length ||
    new Set(candidates.map((candidate) => candidate.ordinal)).size !== candidates.length ||
    candidates.some((candidate, index) => candidate.ordinal !== index)
  )
    throw new ApiError(502, 'misaligned_cover_candidates')
  const incumbents = candidates.filter((candidate) => candidate.incumbent)
  if (
    incumbents.length > 1 ||
    ((incumbentState === 'absent' || incumbentState === 'unavailable') &&
      (incumbents.length !== 0 || value.incumbent.reference !== null)) ||
    (incumbentState === 'available' &&
      (incumbents.length !== 1 || value.incumbent.reference !== incumbents[0]?.reference))
  )
    throw new ApiError(502, 'misaligned_cover_incumbent')
  const proposedReferences = candidates
    .filter((candidate) => candidate.manualEligible && !candidate.incumbent)
    .map((candidate) => candidate.reference)
  const choiceContexts = mapChoiceContexts(value.choice_contexts, proposedReferences.length)
  if (
    choiceContexts
      .slice(1)
      .some((context, index) => context.alternativeId !== proposedReferences[index])
  )
    throw new ApiError(502, 'misaligned_cover_choice_contexts')
  const policy =
    value.policy === null
      ? null
      : record(value.policy)
        ? value.policy
        : (() => {
            throw new ApiError(502, 'invalid_cover_policy')
          })()
  if ((policy === null) !== (candidates.length === 0))
    throw new ApiError(502, 'invalid_cover_policy_candidates')
  const policyStatus = policy === null ? null : stringValue(policy.status, 'cover_policy_status')
  if (policyStatus !== null && !['recommended', 'abstained'].includes(policyStatus))
    throw new ApiError(502, 'invalid_cover_policy_status')
  const policyOwnerReady = policy === null ? false : policy.owner_ready === true
  const actualTargetState =
    policy === null ? null : stringValue(policy.actual_target_state, 'cover_actual_target_state')
  if (
    policy !== null &&
    (policy.schema_version !== 'cover-policy-report-v2' ||
      typeof policy.owner_ready !== 'boolean' ||
      !['known_empty', 'confirmed', 'unknown'].includes(actualTargetState ?? ''))
  )
    throw new ApiError(502, 'invalid_cover_policy')
  const reviewerStatus = stringValue(value.reviewer_status, 'cover_reviewer_status')
  if (!['completed', 'failed', 'unavailable'].includes(reviewerStatus))
    throw new ApiError(502, 'invalid_cover_reviewer_status')
  const reviewerUnavailableReason =
    value.reviewer_unavailable_reason === null
      ? null
      : stringValue(value.reviewer_unavailable_reason, 'cover_reviewer_unavailable_reason')
  if (
    reviewerUnavailableReason !== null &&
    (reviewerStatus !== 'unavailable' ||
      ![
        'profile contract requires memory_bytes=1073741824',
        'profile contract requires process_limit=32',
      ].includes(reviewerUnavailableReason))
  )
    throw new ApiError(502, 'invalid_cover_reviewer_unavailable_reason')
  // Optional on the wire; absent and null both mean no closed failure code.
  const reviewerFailureCode = value.reviewer_failure_code ?? null
  if (
    reviewerFailureCode !== null &&
    (reviewerStatus !== 'failed' ||
      !(reviewerFailureCodes as readonly unknown[]).includes(reviewerFailureCode))
  )
    throw new ApiError(502, 'invalid_cover_reviewer_failure_code')
  const recommendedReference =
    policy === null || policy.recommended_candidate_reference === null
      ? null
      : stringValue(policy.recommended_candidate_reference, 'cover_recommended_reference')
  const acceptable =
    policy === null
      ? []
      : stringArray(policy.acceptable_candidate_references, 'cover_acceptable_references')
  if (
    (policyStatus === 'recommended') !== (recommendedReference !== null) ||
    new Set(acceptable).size !== acceptable.length
  )
    throw new ApiError(502, 'contradictory_cover_policy')
  const byReference = new Map(candidates.map((candidate) => [candidate.reference, candidate]))
  const rankedAlternatives = candidates
    .filter((candidate) => candidate.deterministicRank !== null && !candidate.incumbent)
    .sort((left, right) => (left.deterministicRank ?? 0) - (right.deterministicRank ?? 0))
  const rankedReferences = rankedAlternatives.map((candidate) => candidate.reference)
  if (
    acceptable.some(
      (reference) =>
        !byReference.has(reference) || byReference.get(reference)?.deterministicRank === null,
    ) ||
    rankedAlternatives.some((candidate, index) => candidate.deterministicRank !== index + 1) ||
    (acceptable.length === 0
      ? !providerFetching && rankedReferences.length !== 0
      : acceptable.length !== rankedReferences.length ||
        acceptable.some((reference, index) => reference !== rankedReferences[index])) ||
    (recommendedReference !== null &&
      (!acceptable.includes(recommendedReference) ||
        byReference.get(recommendedReference)?.selectable !== true ||
        byReference.get(recommendedReference)?.incumbent === true))
  )
    throw new ApiError(502, 'contradictory_cover_policy')
  if (
    candidates.some(
      (candidate) =>
        candidate.state !==
        (!candidate.selectable
          ? 'excluded'
          : candidate.reference === recommendedReference
            ? 'recommended'
            : acceptable.includes(candidate.reference)
              ? 'acceptable'
              : 'available'),
    )
  )
    throw new ApiError(502, 'contradictory_cover_candidate_state')
  const recommendedContexts = choiceContexts.filter(
    (context) => context.recommendation === 'recommended',
  )
  const currentReference = candidates.find((candidate) => candidate.incumbent)?.reference ?? null
  const expectedReference = recommendedReference
  const expectedContext =
    expectedReference === null
      ? undefined
      : expectedReference === currentReference
        ? choiceContexts[0]
        : choiceContexts.slice(1).find((context) => context.alternativeId === expectedReference)
  if (
    (expectedContext === undefined && recommendedContexts.length !== 0) ||
    (expectedContext !== undefined &&
      (recommendedContexts.length !== 1 || recommendedContexts[0] !== expectedContext)) ||
    candidates.some(
      (candidate) => candidate.state === 'recommended' && candidate.reference !== expectedReference,
    ) ||
    (expectedReference !== null &&
      candidates.find((candidate) => candidate.reference === expectedReference)?.state !==
        'recommended')
  )
    throw new ApiError(502, 'contradictory_cover_recommendation')
  if (
    ready !==
      (!providerFetching &&
        candidates.some((candidate) => candidate.incumbent || candidate.manualEligible)) ||
    candidates.some(
      (candidate) =>
        candidate.matchState !== 'match_not_confirmed' &&
        !candidate.incumbent &&
        candidate.selectable !== policyOwnerReady,
    ) ||
    (providerFetching === ready && acceptable.length > 0) ||
    candidates.some(
      (candidate) =>
        (candidate.deterministicRank === null) !== !acceptable.includes(candidate.reference),
    ) ||
    acceptable.some(
      (reference, index) => byReference.get(reference)?.deterministicRank !== index + 1,
    )
  )
    throw new ApiError(502, 'contradictory_cover_readiness')
  return {
    id: stringValue(value.consideration_id, 'cover_consideration_id'),
    current: value.current === true,
    candidates,
    choiceContexts,
    incumbent: {
      reference:
        value.incumbent.reference === null
          ? null
          : stringValue(value.incumbent.reference, 'cover_incumbent_reference'),
      state: incumbentState as 'absent' | 'available' | 'unavailable',
    },
    omissions: value.omissions.map((item, index) => {
      if (!record(item)) throw new ApiError(502, `invalid_cover_omission_${String(index)}`)
      return item
    }),
    policy,
    providerFetching,
    ready,
    policyPreferences: {
      avoidPromotionalText: preferences.avoid_promotional_text,
      avoidTieInBranding: preferences.avoid_tie_in_branding,
      preferMinimalComposition: preferences.prefer_minimal_composition,
    },
    policyReference: stringValue(value.policy_reference, 'cover_policy_reference'),
    reviewerEngine:
      value.reviewer_engine === null
        ? null
        : value.reviewer_engine === 'codex' || value.reviewer_engine === 'openrouter'
          ? value.reviewer_engine
          : (() => {
              throw new ApiError(502, 'invalid_cover_reviewer_engine')
            })(),
    reviewerModel:
      value.reviewer_model === null
        ? null
        : stringValue(value.reviewer_model, 'cover_reviewer_model'),
    reviewerAccessRevision:
      value.reviewer_access_revision === null
        ? null
        : integerValue(value.reviewer_access_revision, 'cover_reviewer_access_revision'),
    reviewerStatus,
    reviewerUnavailableReason: reviewerUnavailableReason as
      | 'profile contract requires memory_bytes=1073741824'
      | 'profile contract requires process_limit=32'
      | null,
    ...(reviewerFailureCode === null
      ? {}
      : { reviewerFailureCode: reviewerFailureCode as ReviewerFailureCode }),
    ...(value.supersedes_id === null
      ? {}
      : { supersedesId: stringValue(value.supersedes_id, 'cover_supersedes_id') }),
    targetFingerprint: stringValue(value.target_fingerprint, 'cover_target_fingerprint'),
  }
}

// Read on a clone so a non-`csrf` 403 still reaches the ordinary error path.
const challengeCode = async (response: Response) => (await structuredError(response.clone())).code

const structuredError = async (response: Response) => {
  let value: unknown
  try {
    value = await response.json()
  } catch {
    return new ApiError(response.status, 'invalid_error_response')
  }
  if (!record(value) || !record(value.error) || typeof value.error.code !== 'string')
    return new ApiError(response.status, 'invalid_error_response')
  return new ApiError(response.status, value.error.code)
}
