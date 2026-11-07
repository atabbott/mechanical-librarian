export type ReadWorkClass = 'foreground' | 'background'

import type { components } from '../generated/review-api'
import type { components as responseComponents } from '../generated/review-api-response'
import type { D, OperationKey } from '../generated/review-decoders'
import { DecodeFailure, matchOperation, responseDecoders } from '../generated/review-decoders'
import settingsVocabulary from '../../../src/mechanical_librarian/interfaces/web/settings_vocabulary.json'

// Request bodies keep the document's own types; a decoded response has the
// response-truth type, where every property but the pinned omissions is
// required (ADR-0091 decision 2).
type Schema<Name extends keyof components['schemas']> = components['schemas'][Name]
type ResponseSchema<Name extends keyof responseComponents['schemas']> =
  responseComponents['schemas'][Name]
// Holds a literal tuple to exactly the members of a generated enum: an extra
// member fails the element type, and a missing one fails the parameter type,
// which names the missing members (#1576).
export const lits =
  <T extends string>() =>
  <const V extends readonly T[]>(
    values: V &
      ([Exclude<T, V[number]>] extends [never]
        ? unknown
        : { missing_members: Exclude<T, V[number]> }),
  ) =>
    values
type LibraryDto = ResponseSchema<'_LibraryResponse'>
type LibraryCoverDto = ResponseSchema<'_LibraryCover'>
type ReportDetailDto = ResponseSchema<'_ReportDetailResponse'>
type AttentionDto = ResponseSchema<'_AttentionResponse'>
type CaseDetailDto = ResponseSchema<'_CaseDetailResponse'>
type ReviewFieldDto = ResponseSchema<'_ReviewField'>
type DescriptionCandidateDto = ResponseSchema<'DescriptionAdviceCandidate'>
type DescriptionBindingDto = ResponseSchema<'DescriptionAdviceBinding'>
type OwnerControlDto = ResponseSchema<'_OwnerControl'>
type EvidenceDto = ResponseSchema<'_EvidenceResponse'>
type HistoryDto = ResponseSchema<'_HistoryResponse'>
type ChoiceContextDto = ResponseSchema<'_ChoiceContext'>
type CoverObservationDto = ResponseSchema<'_CoverObservation'>
type CoverConsiderationDto = ResponseSchema<'_CoverConsiderationResponse'>
type ProviderOutcomeDto = ResponseSchema<'_ProviderOutcome'>
type MediaHealthDto = ResponseSchema<'_MediaHealthObservation'>
type RecordingIdentityBasisDto = ResponseSchema<'_RecordingIdentityBasis'>
type DecisionAcknowledgementDto =
  | ResponseSchema<'_DecisionAcknowledgement'>
  | ResponseSchema<'_DescriptionSelectionAcknowledgement'>
export type SettingsView = ResponseSchema<'SettingsView'>
export type SettingsWrite = Schema<'_SettingsWriteRequest'>
export type AppearanceView = ResponseSchema<'_AppearanceResponse'>
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

// The lens a work belongs to; All lists every one of them (#1407). A work with
// nothing open whose latest preparation failed has its own (#1413).
export const attentionStates = [
  'needs_attention',
  'no_attention_needed',
  'not_matched',
  'preparation_failed',
] as const
lits<Schema<'_LibraryWork'>['attention_state']>()(attentionStates)
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
  'not_matched_adapter_fault',
  'not_matched_no_match',
] as const
lits<Schema<'_LibraryWorkSummary'>['state']>()(workSummaryStates)
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
lits<Schema<'_HistoryDecisionItem'>['outcome']>()(DECISION_OUTCOMES)
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
lits<Schema<'_MediaHealthObservation'>['kind']>()(MEDIA_HEALTH_KINDS)
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
  // Not asked: Audible or Audnexus had already matched the recording by stated runtime (#1309).
  'not_needed',
  // Not a provider status: the lane failed inside Mechanical Librarian (#1498).
  'adapter_fault',
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
lits<Schema<'_HistoryAutomaticItem'>['outcome']>()(AUTOMATIC_OUTCOMES)
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
lits<Schema<'_HistoryCoverItem'>['kind']>()(COVER_HISTORY_OUTCOMES)
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
lits<Schema<'_HistoryDescriptionItem'>['state']>()(DESCRIPTION_STATES)
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
  counts: { attention: number; all: number; clear: number; notMatched: number; failed: number }
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
export type DescriptionAdviceView = ResponseSchema<'_DescriptionAdviceResponse'>
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
  proposedValues: readonly ResponseSchema<'_ProposedValue'>[]
  choiceContexts: readonly ChoiceContextView[]
  proposalId: string
  targetFingerprint: string
  humanDecision: ResponseSchema<'_DecisionProjection'> | null
  inventoryRole?: 'primary' | 'contextual'
  automaticDecision:
    | ResponseSchema<'_AutomaticDecisionProjection'>
    | ResponseSchema<'_CoverAutomaticDecisionProjection'>
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
  /** A proposed cover from the matched book; the recording stays unconfirmed (#1642). */
  matchReason?: 'matched_book'
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
      candidate: DescriptionCandidateDto | null
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
      /** The chosen cover came from the matched book; its recording is unconfirmed. */
      ownerMatchedBook?: boolean
      /** A proposed cover came from the matched book, so policy notes name the recording. */
      matchedBookOffered?: boolean
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
export type PreparationView = ResponseSchema<'_PreparationResponse'>
export type PreparationProgress = ResponseSchema<'_PreparationProgress'>
export type ProviderThrottlingView = ResponseSchema<'_PreparationThrottling'>
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
  owner_action_required: number
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

export type DecisionAcknowledgementView = (
  | Omit<ResponseSchema<'_DecisionAcknowledgement'>, 'confirmation'>
  | Omit<ResponseSchema<'_DescriptionSelectionAcknowledgement'>, 'confirmation'>
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
// Where a 2xx response failed: the decoder's `reason@at`, or for a named check
// on an unsafe answer `invariant@<its code>`. The message stays the ApiError
// one, so no pointer reaches the page.
export interface ResponseFailure {
  readonly operation: string
  readonly responseStatus: number
  readonly at: string
  readonly reason: string
}
// A read, or a side-effect-free command, whose 2xx body the decoder refused.
export class ResponseDecodeError extends ApiError implements ResponseFailure {
  readonly operation: string
  readonly responseStatus: number
  readonly at: string
  readonly reason: string
  constructor({ operation, responseStatus, at, reason }: ResponseFailure) {
    super(502, 'invalid_response')
    this.name = 'ResponseDecodeError'
    this.operation = operation
    this.responseStatus = responseStatus
    this.at = at
    this.reason = reason
  }
}
// An unsafe request answered 2xx with a body the browser cannot read: the write
// may have committed, so it is reported as uncertain like a lost answer.
export class UncertainMutationError extends AmbiguousMutationError implements ResponseFailure {
  readonly operation: string
  readonly responseStatus: number
  readonly at: string
  readonly reason: string
  constructor({ operation, responseStatus, at, reason }: ResponseFailure) {
    super()
    this.name = 'UncertainMutationError'
    this.operation = operation
    this.responseStatus = responseStatus
    this.at = at
    this.reason = reason
  }
}
// The operations whose 2xx JSON body `request()` decodes with its generated
// decoder. Each stage-3 family adds its keys; stage 4 removes the gate and
// decodes every operation (#1684).
export const DECODED: ReadonlySet<OperationKey> = new Set<OperationKey>([
  'GET /api/v1/review-preparation',
  'POST /api/v1/review-preparation/dismiss',
  'POST /api/v1/review-preparation/first-boot/acknowledge',
  'POST /api/v1/review-preparation/pause',
  'POST /api/v1/review-preparation/resume',
  'POST /api/v1/review-preparation/{connection}/check-connection',
  'POST /api/v1/review-preparation/{connection}/{library_name}/retry',
  'POST /api/v1/review-preparation/{connection}/{library_name}/scan',
  'GET /api/v1/library',
  'GET /api/v1/library/report',
  'GET /api/v1/attention',
  'GET /api/v1/review-cases/{review_case_id}',
  'GET /api/v1/review-cases/{review_case_id}/evidence',
  'GET /api/v1/review-cases/{review_case_id}/history',
  'GET /api/v1/review-cases/{review_case_id}/successor',
  'POST /api/v1/review-cases/{review_case_id}/cover-considerations/retry',
  'POST /api/v1/review-cases/{review_case_id}/decisions',
  'POST /api/v1/review-cases/{review_case_id}/description-advice/retry',
  'POST /api/v1/review-cases/{review_case_id}/description-advice/selection',
  'GET /api/v1/settings',
  'PATCH /api/v1/settings',
  'POST /api/v1/provider-recovery/retry',
  'GET /api/v1/appearance',
])
const decoders = responseDecoders as Readonly<
  Record<OperationKey, Readonly<Record<string, D<unknown> | undefined>>>
>
// Server codes proven to be raised before any handler body runs, so an unsafe
// request answered with one of them changed nothing. The sole member comes from
// the session dependency; tests pin that proof on both sides.
export const PRE_CALL_SERVER_CODES: ReadonlySet<string> = new Set(['react_assets_unavailable'])
// Whether an unsafe write may have committed although it answered a failure.
export const uncertainWriteOutcome = (error: unknown) =>
  error instanceof AmbiguousMutationError ||
  (error instanceof ApiError && error.status >= 500 && !PRE_CALL_SERVER_CODES.has(error.code))

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
  // Set while signing out: a 401 then is the sign-out itself, so the shell's
  // own navigation to /login is the only one (#1588). Once a logout has
  // succeeded the mark stays; a refused one replays any expiry it held back.
  private signingOut = false
  private signedOut = false
  private expiryWhileSigningOut = false
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
    return mapLibrary((await this.read(`/api/v1/library?${p}`, signal, workClass)) as LibraryDto)
  }
  // The address is the one the library row handed back, followed rather than
  // rebuilt: an item id may hold any character a target permits, and only the
  // server that escaped it knows how. It is followed only when it is the report
  // route on this origin, so the body is decoded as a report before it is
  // mapped as one; any other address fails closed without a request (#1686).
  async getReportDetail(
    detailUrl: string,
    signal?: AbortSignal,
    workClass: ReadWorkClass = 'foreground',
  ) {
    if (
      new URL(detailUrl, 'http://local').origin !== 'http://local' ||
      matchOperation('GET', detailUrl) !== 'GET /api/v1/library/report'
    )
      throw new ResponseDecodeError({
        operation: 'GET /api/v1/library/report',
        responseStatus: 0,
        at: '',
        reason: 'unexpected_operation',
      })
    return mapReportDetail((await this.read(detailUrl, signal, workClass)) as ReportDetailDto)
  }
  // The settings and appearance answers are decoded whole; the settings view
  // is the document itself, with no mapping and no named check (#1689).
  async getSettings(signal?: AbortSignal, workClass: ReadWorkClass = 'foreground') {
    return (await this.read('/api/v1/settings', signal, workClass)) as SettingsView
  }
  async getAppearance(signal?: AbortSignal, workClass: ReadWorkClass = 'foreground') {
    return (await this.read('/api/v1/appearance', signal, workClass)) as AppearanceView
  }
  async writeSettings(value: SettingsWrite, signal?: AbortSignal) {
    return this.mutate(
      '/api/v1/settings',
      value,
      signal,
      undefined,
      'PATCH',
      false,
      false,
      settingsView,
    )
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
      (await this.read(
        `/api/v1/attention${p.size ? `?${p}` : ''}`,
        signal,
        workClass,
      )) as AttentionDto,
    )
  }
  async getCaseDetail(id: string, signal?: AbortSignal, workClass: ReadWorkClass = 'foreground') {
    return mapCaseDetail(
      (await this.read(
        `/api/v1/review-cases/${encodeURIComponent(id)}`,
        signal,
        workClass,
      )) as CaseDetailDto,
    )
  }

  async getCaseSuccessor(
    id: string,
    signal?: AbortSignal,
    workClass: ReadWorkClass = 'foreground',
  ) {
    const value = (await this.read(
      `/api/v1/review-cases/${encodeURIComponent(id)}/successor`,
      signal,
      workClass,
    )) as ResponseSchema<'_CaseSuccessorResponse'>
    return value.review_case_id
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
      (await this.read(
        `/api/v1/review-cases/${encodeURIComponent(id)}/evidence?${p}`,
        signal,
        workClass,
      )) as EvidenceDto,
    )
  }
  async getHistory(id: string, signal?: AbortSignal, workClass: ReadWorkClass = 'foreground') {
    return mapHistory(
      (await this.read(
        `/api/v1/review-cases/${encodeURIComponent(id)}/history`,
        signal,
        workClass,
      )) as HistoryDto,
    )
  }
  // The answer is decoded as a consideration, never null, before it is mapped.
  async retryCoverConsideration(id: string, signal?: AbortSignal) {
    return this.mutate(
      `/api/v1/review-cases/${encodeURIComponent(id)}/cover-considerations/retry`,
      undefined,
      signal,
      undefined,
      'POST',
      false,
      false,
      (value) => mapCoverConsideration(value as CoverConsiderationDto),
    )
  }
  async getPreparation(signal?: AbortSignal, workClass: ReadWorkClass = 'foreground') {
    return mapPreparation(
      (await this.read('/api/v1/review-preparation', signal, workClass)) as PreparationDto,
    )
  }
  // Scan and retry answer the library's progress; every other control answers
  // the whole preparation view.
  async preparationAction(
    path: string,
    signal?: AbortSignal,
  ): Promise<PreparationProgress | PreparationView> {
    const progress = /(?:^|\/)(?:scan|retry)$/.test(path)
    return this.mutate(
      `/api/v1/review-preparation/${path}`,
      undefined,
      signal,
      undefined,
      'POST',
      false,
      false,
      (value) =>
        progress ? (value as PreparationProgress) : mapPreparation(value as PreparationDto),
    )
  }
  // The expected generation makes a resend a refusal, never a second raise, so
  // a CSRF-renewed press is sent once more (#1018). The command still writes.
  async retryProviderRecovery(expectedGeneration: number, signal?: AbortSignal) {
    return this.mutate(
      '/api/v1/provider-recovery/retry',
      { expected_generation: expectedGeneration },
      signal,
      undefined,
      'POST',
      true,
      false,
      settingsView,
    )
  }
  async dismissPreparation(runId: string, signal?: AbortSignal) {
    return this.mutate(
      '/api/v1/review-preparation/dismiss',
      { run_id: runId },
      signal,
      undefined,
      'POST',
      false,
      false,
      (value) => mapPreparation(value as PreparationDto),
    )
  }
  // The confirmation is mapped inside `mutate`, so one the browser cannot read,
  // or one naming another case, is an uncertain outcome rather than a refusal
  // a same-key Retry would only repeat (#1688).
  async recordDecision(id: string, command: DecisionCommand, signal?: AbortSignal) {
    const map = (value: unknown) =>
      mapDecisionAcknowledgement(value as DecisionAcknowledgementDto, id)
    if (command.authoredDescription) {
      return this.mutate(
        `/api/v1/review-cases/${encodeURIComponent(id)}/description-advice/selection`,
        {
          candidate_id: command.authoredDescription.candidateId,
          binding: command.authoredDescription.binding,
        },
        signal,
        { 'idempotency-key': command.idempotencyKey },
        'POST',
        false,
        false,
        map,
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
    return this.mutate(
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
      'POST',
      false,
      false,
      map,
    )
  }
  // The decoder holds the one value the answer may carry (`queued: true`).
  async retryDescriptionAdvice(id: string, signal?: AbortSignal) {
    await this.mutate(
      `/api/v1/review-cases/${encodeURIComponent(id)}/description-advice/retry`,
      {},
      signal,
    )
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
    const writesNothing = path === 'validate' || path === 'preview'
    const value = await this.mutate(
      `/api/v1/metadata-policy/${path}`,
      body,
      signal,
      undefined,
      'POST',
      writesNothing,
      writesNothing,
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
    this.signingOut = true
    try {
      await this.mutate('/api/v1/session/logout', undefined, signal)
      this.signingOut = this.signedOut = true
    } catch (error) {
      if (!(error instanceof SessionExpiredError) && !this.signedOut) {
        this.signingOut = false
        if (this.expiryWhileSigningOut) this.reportSessionExpiry()
      }
      throw error
    }
  }

  private async read(path: string, signal?: AbortSignal, workClass: ReadWorkClass = 'foreground') {
    return this.request(
      path,
      { headers: { 'X-ML-Work-Class': workClass }, ...(signal ? { signal } : {}) },
      { unsafe: false },
    )
  }
  // `map` runs on the decoded answer of a migrated operation. A named check it
  // fails on a request that may have written is uncertain, never a refusal.
  private async mutate<T = unknown>(
    path: string,
    body?: unknown,
    signal?: AbortSignal,
    extraHeaders?: HeadersInit,
    method = 'POST',
    sideEffectFree = false,
    writesNothing = false,
    map?: (value: unknown) => T,
  ): Promise<T> {
    if (!this.buildIdentity) {
      this.onBuildMismatch()
      throw new BuildMismatchError()
    }
    const headers = new Headers(extraHeaders)
    headers.set('x-ml-build-identity', this.buildIdentity)
    if (body !== undefined) headers.set('content-type', 'application/json')
    // `sideEffectFree` only lets a CSRF-renewed request be sent again;
    // `writesNothing` says the command changes nothing, so an answer that fails
    // decoding or a named check is a 502 rather than an uncertain write (#1685).
    return (await this.request(
      path,
      {
        method,
        headers,
        ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
        ...(signal ? { signal } : {}),
      },
      {
        unsafe: true,
        resend: sideEffectFree || headers.has('idempotency-key'),
        uncertain: !writesNothing,
        ...(map ? { map } : {}),
      },
    )) as T
  }
  // A 403 `csrf` challenge renews the page token once. Only a request that is
  // safe to repeat -- idempotency-keyed or side-effect free -- is then sent
  // once more; every other one, and a resend refused again, asks the owner to
  // press again (#1018).
  private async request(
    path: string,
    init: RequestInit,
    {
      unsafe,
      resend = false,
      uncertain = false,
      map,
    }: {
      unsafe: boolean
      resend?: boolean
      uncertain?: boolean
      map?: (value: unknown) => unknown
    },
  ): Promise<unknown> {
    // A write the network lost may have landed. Only the network calls make
    // that ambiguous: a TypeError from decoding or `map` is a defect and
    // propagates unchanged (#1685).
    const network = async <R>(call: () => Promise<R>) => {
      try {
        return await call()
      } catch (error) {
        if (unsafe && error instanceof TypeError) throw new AmbiguousMutationError()
        throw error
      }
    }
    const send = (token?: string) => {
      const headers = new Headers(init.headers)
      if (token) headers.set('x-csrf-token', token)
      return network(() =>
        this.fetch(path, { ...init, headers, credentials: 'same-origin', cache: 'no-store' }),
      )
    }
    let response = await send(unsafe ? this.options.csrf.current() : undefined)
    if (unsafe && response.status === 403 && (await challengeCode(response)) === 'csrf') {
      let token: string
      try {
        token = await network(() => this.options.csrf.renew(init.signal ?? undefined))
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
    const method = init.method ?? 'GET'
    const operation = matchOperation(method, path)
    const gated = operation !== undefined && DECODED.has(operation)
    const failed = (at: string, reason: string) => {
      const failure = {
        operation: operation ?? `${method} ${path}`,
        responseStatus: response.status,
        at,
        reason,
      }
      return uncertain ? new UncertainMutationError(failure) : new ResponseDecodeError(failure)
    }
    // A 204 carries no body, so only an operation outside the gate may answer
    // one; a gated operation documents no 204 and fails closed below.
    if (response.status === 204 && !gated) return undefined
    const decoder = gated ? decoders[operation][String(response.status)] : undefined
    if (gated && decoder === undefined) throw failed('', 'no_decoder')
    let value: unknown
    try {
      value = (await response.json()) as unknown
    } catch {
      if (unsafe) throw new AmbiguousMutationError()
      throw new ApiError(502, 'invalid_json_response')
    }
    if (decoder !== undefined) {
      try {
        value = decoder(value, '')
      } catch (error) {
        if (error instanceof DecodeFailure) throw failed(error.at, error.reason)
        throw error
      }
    }
    if (!map) return value
    try {
      return map(value)
    } catch (error) {
      if (uncertain && error instanceof ApiError) throw failed(error.code, 'invariant')
      throw error
    }
  }

  private reportSessionExpiry() {
    if (this.signingOut) {
      this.expiryWhileSigningOut = true
      return
    }
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
const nullValue = (value: unknown, field: string): null => {
  if (value !== null) throw new ApiError(502, `invalid_${field}`)
  return value
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
// A settings answer is decoded before `mutate` hands it here; the decoder
// fills a library's omitted language list with its default, the empty list.
const settingsView = (value: unknown) => value as SettingsView
// The media-health and provider-outcome lists are decoded with the document
// that carries them (report, works list, case detail, evidence); these only
// rename (#1688).
const mapMediaHealth = (value: readonly MediaHealthDto[]): MediaHealthObservationView[] =>
  value.map(({ bound, cited, expected, kind, observed, tracks }) => ({
    bound,
    cited,
    expected,
    kind,
    observed,
    tracks,
  }))

const mapProviderOutcome = (value: ProviderOutcomeDto): ProviderOutcomeView => ({
  providerRequests: value.provider_requests,
  laneKind: value.lane_kind,
  provider: value.provider,
  reason: value.reason,
  status: value.status,
})
// Formats any target or proposed value. It throws nothing: the Activity
// example still reaches it undecoded until that family is decoded.
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
export const EMPTY_TARGET_TEXT = 'Not set in Audiobookshelf'
export const targetValueText = (
  state: TargetFieldState,
  value: ResponseSchema<'_TargetField'>['value'],
) =>
  state === 'value'
    ? displayValue(value)
    : state === 'missing' || state === 'null' || state === 'empty'
      ? EMPTY_TARGET_TEXT
      : 'Couldn’t be read from Audiobookshelf'

const validateObject = (value: unknown, name: string): Record<string, unknown> => {
  if (!record(value) || typeof value.schema_version !== 'string')
    throw new ApiError(502, `invalid_${name}_response`)
  return value
}
// A deliberate, named exception to the rule that every field a view reads is
// checked. These responses are handed back as their generated DTO unchanged
// instead of being narrowed into a hand-written view model, and their
// operations are not yet decoded centrally, so the only invariant this file
// asserts for them is the version handshake: a hand-written copy of
// docs/reference/review-openapi-v1.json would become a second contract and
// would fail closed on responses the server is entitled to send. The families
// that are decoded (`DECODED`: preparation, library, attention, the case
// detail and settings) are checked field by field by their generated decoders
// instead, and their mappers keep only the checks the schema cannot state
// (ADR-0091). This
// is the one place in the file allowed to widen an unchecked document to a DTO
// type, which is why the call sites carry no cast of their own.
// The lint rule below objects that a type parameter used once is an assertion in
// disguise. It is, deliberately: this is the file's single declared assertion seam,
// and naming it here is what keeps the call sites free of casts of their own.
// eslint-disable-next-line @typescript-eslint/no-unnecessary-type-parameters
const schemaVersionOnly = <T>(value: unknown, name: string): T =>
  validateObject(value, name) as unknown as T
// The decoder holds the acknowledgement's shape; the confirmed case must be the
// one the decision was recorded on (ADR-0091).
const mapDecisionAcknowledgement = (
  value: DecisionAcknowledgementDto,
  reviewCaseId: string,
): DecisionAcknowledgementView => {
  const { confirmation } = value
  if (confirmation.status === 'unavailable') return { ...value, confirmation }
  const detail = mapCaseDetail(confirmation.case)
  if (detail.id !== reviewCaseId) throw new ApiError(502, 'invalid_decision_confirmation')
  return { ...value, confirmation: { status: 'confirmed', case: detail } }
}

type PreparationDto = ResponseSchema<'_PreparationResponse'>
// The checks the preparation schema cannot state (ADR-0091): the decoder has
// already enforced every field.
const preparationPresentation = (value: NonNullable<PreparationDto['presentation']>) => {
  const { passage, total } = value
  if (
    passage.searches_finished > passage.read ||
    passage.compared > passage.searches_finished ||
    passage.checked > passage.compared ||
    passage.ready > passage.checked
  )
    throw new ApiError(502, 'invalid_preparation_passage_order')
  if (new Set(value.triggers.map(({ trigger }) => trigger)).size !== value.triggers.length)
    throw new ApiError(502, 'invalid_preparation_trigger')
  if (value.triggers.some((trigger) => trigger.last_accepted_at < trigger.first_accepted_at))
    throw new ApiError(502, 'invalid_preparation_trigger')
  const terminal = ['completed_clean', 'completed_with_failures', 'dismissed'].includes(value.state)
  if (
    Object.values(passage).some((count) => count > total) ||
    value.active_books.length + value.active_overflow > total ||
    value.updated_at < value.started_at ||
    (terminal && (value.completed_at === null || value.completed_at < value.started_at)) ||
    (!terminal && value.completed_at !== null)
  )
    throw new ApiError(502, 'invalid_preparation_passage_total')
}
export const mapPreparation = (value: PreparationDto): PreparationView => {
  if (value.presentation !== null) preparationPresentation(value.presentation)
  return value
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
    owner_action_required: integerValue(
      value.owner_action_required,
      'activity_owner_action_required',
    ),
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

// The decoder holds each member; a review scope must agree with the media kind
// and the recording basis beside it (ADR-0091).
const mapAudiobookReviewScope = (
  value: 'work' | 'recording' | null,
  mediaKind: MediaKind,
  basis: RecordingIdentityBasisDto | null,
): 'work' | 'recording' | null => {
  if (
    (!['m4b', 'm4a', 'mp3'].includes(mediaKind) && value !== null) ||
    (basis !== null && value !== 'recording')
  )
    throw new ApiError(502, 'inconsistent_audiobook_review_scope')
  return value
}

// The report detail, the works list and the case detail are decoded centrally
// (#1686, #1688), so their mappers only rename; the audiobook scope keeps the
// checks the schema cannot state.
export const mapReportDetail = (value: ReportDetailDto): ReportDetailView => ({
  chapters: value.chapters.map((chapter) => ({
    endMilliseconds: chapter.end_milliseconds,
    ordinal: chapter.ordinal,
    startMilliseconds: chapter.start_milliseconds,
    title: chapter.title,
  })),
  chaptersTruncated: value.chapters_truncated,
  coverPresent: value.embedded_cover.present,
  coverSurface: value.embedded_cover.surface,
  evidence: value.evidence.map((claim) => ({
    basis: claim.basis,
    excerpt: claim.excerpt,
    kind: claim.kind,
    role: claim.role,
    state: claim.state,
    value: claim.value,
  })),
  evidenceTruncated: value.evidence_truncated,
  item: value.item,
  library: value.library,
  libraryDisplayName: value.library_display_name ?? value.library,
  mediaHealth: mapMediaHealth(value.media_health),
  recordingIdentityBasis: value.recording_identity_basis,
  audiobookReviewScope: mapAudiobookReviewScope(
    value.audiobook_review_scope,
    value.media_kind,
    value.recording_identity_basis,
  ),
  mediaKind: value.media_kind,
  providerSources: value.provider_sources.map(mapProviderOutcome),
  providerEvidence: value.provider_evidence.map((claim) => ({
    candidate: claim.candidate,
    kind: claim.kind,
    provider: claim.provider,
    role: claim.role,
    state: claim.state,
    value: claim.value,
  })),
  providerEvidenceTruncated: value.provider_evidence_truncated,
  reportId: value.report_id,
  title: value.title,
})

export const mapLibrary = (value: LibraryDto): LibraryView => ({
  lens: value.lens,
  ...(value.query ? { query: value.query } : {}),
  ...(value.next_cursor ? { nextCursor: value.next_cursor } : {}),
  ...(value.previous_cursor ? { previousCursor: value.previous_cursor } : {}),
  page: value.page,
  counts: {
    attention: value.counts.needs_attention,
    all: value.counts.all,
    clear: value.counts.no_attention_needed,
    notMatched: value.counts.not_matched,
    failed: value.counts.preparation_failed,
  },
  works: value.items.map((item) => {
    const preparation = item.preparation
    const summary = item.summary
    return {
      id: item.review_case_id ?? item.work_id,
      ...(item.review_case_id ? { reviewCaseId: item.review_case_id } : {}),
      workId: item.work_id,
      title: item.title,
      authors: item.authors,
      library: item.library,
      libraryDisplayName: item.library_display_name ?? item.library,
      mediaKind: item.media_kind,
      attentionCount: item.attention_count,
      attention: item.attention_state,
      detailUrl: item.detail_url,
      detailKind: item.detail_kind,
      preparation:
        preparation === null
          ? null
          : {
              attempt: preparation.attempt,
              diagnosticReference: preparation.diagnostic_reference,
              mediaHealth: mapMediaHealth(preparation.media_health),
              provider: preparation.provider,
              reason: preparation.reason,
              recovery: preparation.recovery,
              recoveryAttempt: preparation.recovery_attempt,
              stage: preparation.stage,
              status: preparation.status,
            },
      preparationOutcomes: item.preparation_outcomes.map(mapProviderOutcome),
      summary:
        summary === null
          ? null
          : {
              fields: summary.fields.map((field) => ({
                destination: field.destination,
                state: field.state,
                value: targetValueText(field.state, field.value),
              })),
              reason: summary.reason,
              state: summary.state,
            },
      ...(item.cover === null ? {} : { cover: mapLibraryCover(item.cover) }),
    }
  }),
})

// A cover is shown only from the route that serves the preview its report
// binds, named by that same digest, so a row cannot point the image elsewhere.
// The decoder holds the digest's form and the route prefix; this binds the two.
const mapLibraryCover = (value: LibraryCoverDto): LibraryCoverView => {
  const prefix = '/api/v1/library/cover?'
  if (new URLSearchParams(value.url.slice(prefix.length)).get('preview') !== value.preview_sha256)
    throw new ApiError(502, 'invalid_library_cover')
  return {
    previewSha256: value.preview_sha256,
    width: value.width,
    height: value.height,
    url: value.url,
  }
}

export const mapAttention = (value: AttentionDto): AttentionView => ({
  ...(value.query ? { query: value.query } : {}),
  ...(value.next_cursor ? { nextCursor: value.next_cursor } : {}),
  total: value.total,
  items: value.items.map((item) => ({
    id: item.review_case_id,
    destination: item.destination,
    title: item.title,
    authors: item.authors,
    library: item.library,
    libraryDisplayName: item.library_display_name ?? item.library,
    mediaKind: item.media_kind,
    classificationReason: item.classification_reason,
    notices: item.notices,
    provenance: item.provenance,
    workAttentionCount: item.work_attention_count,
  })),
})

// The case-detail family is decoded centrally (#1688). Its mappers rename, and
// keep only the checks the schema cannot state, each named in ADR-0091.
// X49: a candidate's description is not blank. Its only owner is persisted.
const mapDescriptionCandidate = (value: DescriptionCandidateDto): DescriptionCandidateDto => {
  if (!value.output.description.trim()) throw new ApiError(502, 'invalid_description_text')
  return value
}
const mapDescriptionAdvice = (value: DescriptionAdviceView): DescriptionAdviceView => {
  if (value.application_outcome !== null && value.selection === null)
    throw new ApiError(502, 'invalid_description_application_outcome')
  const { candidate } = value
  if (candidate !== null) mapDescriptionCandidate(candidate)
  if (
    candidate !== null &&
    (candidate.attempt_id !== value.attempt_id ||
      candidate.engine !== value.engine ||
      candidate.model !== value.model)
  )
    throw new ApiError(502, 'invalid_description_candidate_binding')
  if ((value.selection === null) !== (value.selected_advice === null))
    throw new ApiError(502, 'invalid_description_selection')
  const { selection, selected_advice: selectedAdvice } = value
  if (selection !== null && selectedAdvice !== null) {
    const selectedCandidate = mapDescriptionCandidate(selectedAdvice.candidate)
    const selectedBinding = selection.binding
    if (
      !new RegExp(
        `^${selection.origin === 'human' ? 'human' : 'automatic'}_decision_[0-9a-f]{64}$`,
      ).test(selection.decision_id) ||
      selection.candidate_id !== selectedCandidate.candidate_id ||
      (Object.keys(selectedBinding) as (keyof DescriptionBindingDto)[]).some(
        (key) => selectedBinding[key] !== selectedAdvice.binding[key],
      )
    )
      throw new ApiError(502, 'invalid_description_selection')
  }
  return value
}

export const mapCaseDetail = (value: CaseDetailDto): CaseDetailView => {
  const { target } = value
  return {
    id: value.review_case_id,
    current: value.current,
    target: {
      connection: target.connection,
      library: target.library,
      libraryDisplayName: target.library_display_name ?? target.library,
      item: target.item,
      mediaKind: target.media_kind,
      title: target.title,
    },
    title: target.title,
    authors: target.authors,
    comparisonNotices: value.comparison_notices,
    mediaHealth: mapMediaHealth(value.media_health),
    recordingIdentityBasis: value.recording_identity_basis,
    audiobookReviewScope: mapAudiobookReviewScope(
      value.audiobook_review_scope,
      target.media_kind,
      value.recording_identity_basis,
    ),
    writesConsented: value.writes_consented,
    fields: value.fields.map(mapReviewField),
  }
}

const mapReviewField = (field: ReviewFieldDto): ReviewFieldView => ({
  classification: field.classification,
  classificationReason: field.classification_reason,
  destination: field.destination,
  // The decoder admits exactly [] or ['keep_current', 'accept'].
  decisionActions: field.decision_actions.length === 0 ? [] : ['keep_current', 'accept'],
  disposition: field.deterministic_disposition,
  editable: field.editable,
  effectCapability: field.effect_capability,
  needsAttention: field.needs_attention,
  manualHold: field.manual_hold,
  navigationRole: field.navigation_role,
  // An empty or unreadable current value says which, in Audiobookshelf's
  // name, rather than one word for both (#1286).
  currentValue: targetValueText(field.current.state, field.current.value),
  currentRaw: field.current.value,
  proposedValues: field.proposed_values,
  choiceContexts: mapChoiceContexts(field.choice_contexts, field.proposed_values.length),
  proposalId: field.proposal_id,
  targetFingerprint: field.target_fingerprint,
  humanDecision: field.human_decision,
  inventoryRole: field.inventory_role,
  automaticDecision: field.automatic_decision,
  automaticWithheldReason: field.automatic_withheld_reason,
  automaticSelectionSources: field.automatic_selection_sources,
  notices: field.notices,
  provenance: field.provenance,
  preservationOnly: field.preservation_only,
  corroborationSources: field.corroboration_sources,
  descriptionAdvice:
    field.description_advice === null ? null : mapDescriptionAdvice(field.description_advice),
  ownerControl: field.owner_control === null ? null : mapOwnerControl(field.owner_control),
  coverConsideration:
    field.cover_consideration === null ? null : mapCoverConsideration(field.cover_consideration),
  currentCoverSelectionArtifactId: field.current_cover_selection_artifact_id,
  currentCoverSelectionId: field.current_cover_selection_id,
})

const mapOwnerControl = ({ entry, subset, withdraw }: OwnerControlDto): OwnerControlView => ({
  entry:
    entry === null
      ? null
      : {
          maxCharacters: entry.max_characters,
          suggestions: entry.suggestions.map(({ rule, value }) => ({ rule, value })),
        },
  subset:
    subset === null
      ? null
      : {
          maxItems: subset.max_items,
          options: subset.options.map(({ value, current, proposed, previous }) => ({
            value,
            current,
            proposed,
            previous,
          })),
        },
  withdraw,
})

export const mapEvidence = (value: EvidenceDto): EvidenceView => ({
  items: value.items.map(({ destinations, observation, role, source }) => ({
    destinations,
    observation,
    role,
    source,
  })),
  missingLineageCount: value.missing_lineage_count,
  ...(value.next_cursor ? { nextCursor: value.next_cursor } : {}),
  sourceLedger: value.source_ledger.map(mapProviderOutcome),
})

const mapHistoryItem = (item: HistoryDto['items'][number], index: number): HistoryItemView => {
  if ('checkpoint' in item)
    return { category: 'transition', occurredAt: null, outcome: item.kind, reason: item.reason }
  const occurredAt = item.occurred_at
  // Each item kind has its own event schema, named by its version.
  if (item.schema_version === 'description-advice-history-v1')
    return {
      category: 'description',
      occurredAt,
      outcome: item.kind,
      state: item.state,
      model: item.model,
      engine: item.engine,
      candidate: item.candidate === null ? null : mapDescriptionCandidate(item.candidate),
      selected: item.kind === 'description_selected',
    }
  // Only an owner decision states what it may go on to do; a retained F45
  // decision is the only one that carries a reason (#968).
  if (item.schema_version === 'human-decision-event-v1')
    return {
      category: 'owner',
      applicationReason: item.application_reason,
      outcome: item.outcome,
      occurredAt,
      destination: item.destination,
      previousValue: item.previous_value,
      selectedValue: item.selected_value,
    }
  if (item.schema_version === 'automatic-decision-history-event-v1')
    return {
      category: 'automatic',
      outcome: item.outcome,
      occurredAt,
      destination: item.destination,
      previousValue: item.previous_value,
      selectedValue: item.selected_value,
    }
  if (item.schema_version === 'shadow-preview-history-event-v1')
    return {
      category: 'preview',
      occurredAt,
      // The decoder holds each key to the disposition names (propertyNames).
      dispositions: Object.entries(item.dispositions).map(([outcome, count]) => ({
        count,
        outcome: outcome as ShadowDisposition,
      })),
    }
  const { consideration, decision } = item
  const decisionOutcome = decision?.outcome
  const ownerOutcome =
    decisionOutcome === 'accept' ||
    decisionOutcome === 'keep_current' ||
    decisionOutcome === 'reject'
      ? decisionOutcome
      : undefined
  const automaticOutcome = decisionOutcome === 'candidate' ? decisionOutcome : undefined
  // X53: a cover decision is an owner choice or the automatic candidate.
  if (decisionOutcome !== undefined && !ownerOutcome && !automaticOutcome)
    throw new ApiError(502, `invalid_history_item_${String(index)}_decision_outcome`)
  const { policy } = consideration
  return {
    category: 'cover',
    occurredAt,
    ...(automaticOutcome ? { automaticOutcome } : {}),
    candidateCount: consideration.candidates.length,
    ...(consideration.reviewer_model === null
      ? {}
      : { reviewerModel: consideration.reviewer_model }),
    outcome: item.kind,
    ...(ownerOutcome
      ? (() => {
          const chosen = consideration.candidates.find((candidate) =>
            ownerOutcome === 'keep_current'
              ? candidate.incumbent
              : ownerOutcome === 'accept' &&
                candidate.artifact_id === decision?.selected_value?.value,
          )
          return {
            ownerOutcome,
            ...(chosen?.match_state === 'match_not_confirmed'
              ? { ownerMatchUnconfirmed: true }
              : {}),
            ...(chosen?.match_reason === 'matched_book' ? { ownerMatchedBook: true } : {}),
          }
        })()
      : {}),
    ...(consideration.candidates.some((candidate) => candidate.match_reason === 'matched_book')
      ? { matchedBookOffered: true }
      : {}),
    policyReasons: policy === null ? [] : policy.reasons,
    ...(policy === null ? {} : { policyStatus: policy.status }),
    reviewerStatus: consideration.reviewer_status,
    supersedesAutomatic:
      decision?.outcome === 'candidate' && decision.supersedes_decision_id !== null,
  }
}

export const mapHistory = (value: HistoryDto): HistoryView => ({
  items: value.items.map(mapHistoryItem),
  ...(value.next_cursor ? { nextCursor: value.next_cursor } : {}),
})

const mapChoiceContexts = (
  value: readonly ChoiceContextDto[],
  proposedValueCount: number,
): readonly ChoiceContextView[] => {
  const contexts = value.map((item) => ({
    alternativeId: item.alternative_id,
    evidence: item.evidence.map(({ source, observation, role }) => ({ source, observation, role })),
    limitations: item.limitations,
    rationale: item.rationale,
    recommendation: item.recommendation,
    recommendationReason: item.recommendation_reason,
    selection: item.selection,
  }))
  const [current, ...proposed] = contexts
  // current-first and proposed-count; X55 (the current context names no
  // alternative), X56 (later ones are proposed and name one), X57 (distinct).
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

const mapCoverObservation = (
  value: CoverObservationDto | null,
  candidateReference: string,
  previewSha256: string,
): CoverObservationView | null => {
  if (value === null) return null
  if (value.candidate_reference !== candidateReference || value.preview_sha256 !== previewSha256)
    throw new ApiError(502, 'misaligned_cover_observation')
  return {
    candidateReference: value.candidate_reference,
    composition: value.composition,
    coverTextLanguage: value.cover_text_language,
    explicitImagery: value.explicit_imagery,
    formatBranding: value.format_branding,
    medium: value.medium,
    overlayMarking: value.overlay_marking,
    placeholder: value.placeholder,
    previewSha256: value.preview_sha256,
    previewVariant: value.preview_variant,
    promotionalText: value.promotional_text,
    reproduction: value.reproduction,
    seriesStyling: value.series_styling,
    thumbnailLegibility: value.thumbnail_legibility,
    tieInBranding: value.tie_in_branding,
  }
}

export const mapCoverConsideration = (value: CoverConsiderationDto): CoverConsiderationView => {
  const providerFetching = value.provider_fetching
  const ready = value.ready
  const candidates = value.candidates.map((candidate): CoverCandidateView => {
    const { identity, incumbent, selectable } = candidate
    const deterministicRank = candidate.deterministic_rank
    const deterministicRankKey = candidate.deterministic_rank_key
    // X52: the rank key's first six items are integers and the last is the
    // candidate's reference.
    if (
      deterministicRankKey !== null &&
      (!deterministicRankKey.slice(0, 6).every((item) => Number.isInteger(item)) ||
        deterministicRankKey[6] !== candidate.reference)
    )
      throw new ApiError(502, 'invalid_cover_deterministic_rank_key')
    const expectedMatchState =
      identity.outcome === 'verified_manifestation_match'
        ? 'same_edition'
        : identity.outcome === 'verified_work_match'
          ? 'same_book'
          : 'match_not_confirmed'
    const confirmed = expectedMatchState !== 'match_not_confirmed'
    const manualEligible = candidate.manual_eligible
    if (
      manualEligible !==
      (!incumbent &&
        identity.outcome !== 'conflict' &&
        !identity.reasons.includes('retained_creator_or_contributor_disagreement'))
    )
      throw new ApiError(502, 'contradictory_cover_manual_eligibility')
    const { reference } = candidate
    const previewSha256 = candidate.preview_sha256
    const observation = mapCoverObservation(candidate.observation, reference, previewSha256)
    const matchState = candidate.match_state
    // match-state and unconfirmed-unranked; X59 (a confirmed incumbent is
    // unselectable and unranked with a key; a confirmed alternative has a key).
    if (
      matchState !== expectedMatchState ||
      (!confirmed && (selectable || deterministicRank !== null || deterministicRankKey !== null)) ||
      (confirmed &&
        incumbent &&
        (selectable || deterministicRank !== null || deterministicRankKey === null)) ||
      (confirmed && !incumbent && deterministicRankKey === null)
    )
      throw new ApiError(502, 'contradictory_cover_identity')
    // match-reason. Display only: the identity, selection and rank checks
    // above are unchanged.
    const matchReason = candidate.match_reason
    if (matchReason !== null && (incumbent || identity.outcome === 'conflict'))
      throw new ApiError(502, 'contradictory_cover_match_reason')
    return {
      artifactId: candidate.artifact_id,
      deterministicCitations: candidate.deterministic_citations,
      deterministicRank,
      deterministicRankKey,
      height: candidate.height,
      identity,
      incumbent,
      matchState,
      ...(matchReason === null ? {} : { matchReason }),
      observation,
      originalSha256: candidate.original_sha256,
      ordinal: candidate.ordinal,
      previewSha256,
      reference,
      selectable,
      manualEligible,
      sourceKinds: candidate.source_kinds,
      state: candidate.state,
      width: candidate.width,
    }
  })
  // unique-references, X58 (distinct artifacts) and ordinal-position, which
  // also leaves no ordinal repeated.
  if (
    new Set(candidates.map((candidate) => candidate.reference)).size !== candidates.length ||
    new Set(candidates.map((candidate) => candidate.artifactId)).size !== candidates.length ||
    candidates.some((candidate, index) => candidate.ordinal !== index)
  )
    throw new ApiError(502, 'misaligned_cover_candidates')
  const incumbentState = value.incumbent.state
  const incumbents = candidates.filter((candidate) => candidate.incumbent)
  // Two incumbents fail both states: an absent or unavailable one admits none,
  // an available one exactly one.
  if (
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
  // X60: proposed contexts follow the eligible alternatives in order.
  if (
    choiceContexts
      .slice(1)
      .some((context, index) => context.alternativeId !== proposedReferences[index])
  )
    throw new ApiError(502, 'misaligned_cover_choice_contexts')
  const { policy } = value
  if ((policy === null) !== (candidates.length === 0))
    throw new ApiError(502, 'invalid_cover_policy_candidates')
  const policyStatus = policy === null ? null : policy.status
  const policyOwnerReady = policy === null ? false : policy.owner_ready
  const reviewerStatus = value.reviewer_status
  const reviewerUnavailableReason = value.reviewer_unavailable_reason
  // X61: an unavailable reason needs the unavailable status.
  if (reviewerUnavailableReason !== null && reviewerStatus !== 'unavailable')
    throw new ApiError(502, 'invalid_cover_reviewer_unavailable_reason')
  const reviewerFailureCode = value.reviewer_failure_code
  if (reviewerFailureCode !== null && reviewerStatus !== 'failed')
    throw new ApiError(502, 'invalid_cover_reviewer_failure_code')
  const recommendedReference = policy === null ? null : policy.recommended_candidate_reference
  const acceptable = policy === null ? [] : policy.acceptable_candidate_references
  if ((policyStatus === 'recommended') !== (recommendedReference !== null))
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
    id: value.consideration_id,
    current: value.current,
    candidates,
    choiceContexts,
    incumbent: { reference: value.incumbent.reference, state: incumbentState },
    omissions: value.omissions,
    policy,
    providerFetching,
    ready,
    policyPreferences: {
      avoidPromotionalText: value.policy_preferences.avoid_promotional_text,
      avoidTieInBranding: value.policy_preferences.avoid_tie_in_branding,
      preferMinimalComposition: value.policy_preferences.prefer_minimal_composition,
    },
    policyReference: value.policy_reference,
    reviewerEngine: value.reviewer_engine,
    reviewerModel: value.reviewer_model,
    reviewerAccessRevision: value.reviewer_access_revision,
    reviewerStatus,
    reviewerUnavailableReason,
    ...(reviewerFailureCode === null ? {} : { reviewerFailureCode }),
    ...(value.supersedes_id === null ? {} : { supersedesId: value.supersedes_id }),
    targetFingerprint: value.target_fingerprint,
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
