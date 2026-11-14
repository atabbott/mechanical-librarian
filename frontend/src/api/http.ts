export type ReadWorkClass = 'foreground' | 'background'

import type { components, paths } from '../generated/review-api'
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
// The Covers view reads its page as decoded: every member is already the
// closed shape a tab renders, so nothing is renamed (#1633).
export type CoversView = ResponseSchema<'_CoversResponse'>
export type CoverWorkView = CoversView['items'][number]
export type CoverTab = CoversView['view']
// Taste profile is a Covers tab with no page of books (#1372).
export type CoversTab = CoverTab | 'taste'
export type TasteProfileView = ResponseSchema<'_TasteProfileResponse'>
export type TasteBuildFailure = NonNullable<NonNullable<TasteProfileView['build']>['failure']>
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
type RecordingAdmissionBasisDto = ResponseSchema<'_RecordingAdmissionBasis'>
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
// The reviewer-access answers are decoded whole (#1690); these are their
// response-truth types, and the mappers below keep only the named checks.
export type ReviewerEngineStatus = ResponseSchema<'ReviewerEngineStatus'>
export type ReviewerState = ReviewerEngineStatus['state']
export type ReviewerReason = NonNullable<ReviewerEngineStatus['reason']>
export type DescriptionModelMetadata = ResponseSchema<'DescriptionModelMetadata'>
export type CoverModelMetadata = ResponseSchema<'CoverModelMetadata'>
export type ReviewerTask = 'cover_observation' | 'description_rewrite' | 'cover_taste_build'
export type ReviewerAccessView = ResponseSchema<'ReviewerAccessView'>
export type CodexDeviceAuthState = ResponseSchema<'_CodexDeviceAuthResponse'>
export type CodexDeviceStartResult = ResponseSchema<'_CodexDeviceStartResponse'>
export type ContainedDescriptionDisclosure = ResponseSchema<'CodexDescriptionDisclosure'>

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
  /** Library's Covers view (#1633); absent is Works. */
  view?: 'covers'
  /** The Covers tab, and the book whose covers are open in it. */
  tab?: CoversTab
  cover?: string
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
  // The companion EPUB's ISBN lane (#1723).
  companionIsbn: boolean
  // iTunes asked for an audiobook's cover alone, so it neither matches nor
  // limits the work (#1779).
  coverOnly: boolean
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
export const PREVIEW_DISPOSITIONS = [
  'abstention',
  'candidate',
  'conflict',
  'ignore',
  'keep_current',
  'manual',
  'unsupported',
] as const
export type PreviewDisposition = (typeof PREVIEW_DISPOSITIONS)[number]
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
  // The original an automatic switch replaced, offered back to the owner (#1363).
  'retained',
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
// How the matched recording was found when its title differs from this
// audiobook's (#1723): the route, what it searched with, and the product.
export interface RecordingAdmissionBasisView {
  basis: 'companion_isbn_work' | 'companion_work' | 'companion_title' | 'path_leaf' | 'title_tag'
  searched: {
    title: string | null
    authors: readonly string[] | null
    isbn: string | null
  }
  provider: string
  candidate: string
}
export interface ReportDetailView {
  recordingIdentityBasis: RecordingIdentityBasisView | null
  recordingAdmissionBasis: RecordingAdmissionBasisView | null
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
  // The language value the observation states, else null (#1802).
  language: string | null
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
// The branch that chose a field's sentence; Library decides rows from it (#1791).
export type FieldCause = ReviewFieldDto['classification_cause']
// The comparison condition a withheld reason already states (#1802).
export type AutomaticWithheldCondition = ReviewFieldDto['automatic_withheld_condition']
export interface ReviewFieldView {
  classification: string
  classificationCause: FieldCause
  classificationReason: string
  destination: TargetDestination
  decisionActions: readonly DecisionAction[]
  editable: boolean
  disposition: ProposalOutcome
  effectCapability: EffectCapability
  needsAttention: boolean
  navigationRole: string
  manualHold: boolean
  // A return to automatic handling was requested; the hold stays until the
  // requested checks finish (#1755).
  manualReturnRequested: boolean
  // The requested checks finished with failures; the hold stays and nothing
  // retries them, so the owner can request them again.
  manualReturnFailed: boolean
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
  automaticWithheldCondition: AutomaticWithheldCondition
  // In Preview, the value Automatic chose before the owner entered Preview
  // mode on a field that now reads No change (#1575).
  earlierAutomaticValue?: ResponseSchema<'_ProposedValue'> | null
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
  /**
   * `retained` is the original an automatic switch replaced: Put back (#1363).
   * `larger_copy` is a larger original of the current cover's own picture.
   */
  candidateKind: 'candidate' | 'larger_copy' | 'retained'
  /** Why a proposed cover cannot be chosen: a source says it belongs to a different book or edition, or names a different author or contributor (#1790). */
  contradiction?: 'book' | 'edition' | 'creator'
  deterministicCitations: readonly string[]
  deterministicRank: number | null
  deterministicRankKey: readonly (number | string)[] | null
  /** Null, with `previewSha256` and `width`, only for a retained original the decoder refused (#1362). */
  height: number | null
  identity: Readonly<Record<string, unknown>> | null
  incumbent: boolean
  matchState: 'same_edition' | 'same_book' | 'match_not_confirmed'
  /**
   * A proposed cover from the matched book; the recording stays unconfirmed
   * (#1642). `title_and_author`: from a record Book matching counts as the same
   * book by title and author; the match stays unconfirmed (#1355).
   */
  matchReason?: 'matched_book' | 'title_and_author'
  observation: CoverObservationView | null
  originalSha256: string
  ordinal: number
  previewSha256: string | null
  reference: string
  selectable: boolean
  manualEligible: boolean
  sourceKinds: readonly CoverSourceKind[]
  state: 'acceptable' | 'available' | 'excluded' | 'recommended'
  width: number | null
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
  /** The measured cover rules' stage from the policy report (#1363). */
  stage?: Pick<ResponseSchema<'_CoverStage'>, 'language' | 'outcome' | 'reason'> | null
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
  recordingAdmissionBasis: RecordingAdmissionBasisView | null
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
  language: string | null
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
      // Who chose the advice. Read only on a choice; null when the event
      // carries no decision.
      chosenBy: 'owner' | 'automatic' | null
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
      dispositions: readonly { count: number; outcome: PreviewDisposition }[]
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
// The preparation controls `preparationAction` sends, under
// `/api/v1/review-preparation/`; each segment hole is one encoded name.
export type PreparationActionPath =
  | 'first-boot/acknowledge'
  | 'pause'
  | 'resume'
  | `${string}/check-connection`
  | `${string}/${string}/scan`
  | `${string}/${string}/retry`
export type PreparationProgress = ResponseSchema<'_PreparationProgress'>
export type ProviderThrottlingView = ResponseSchema<'_PreparationThrottling'>
export type ProviderThrottleEntry = ProviderThrottlingView['entries'][number]
// The policy family is decoded centrally (#1692): its views are the
// response-truth types. Request bodies stay on the request schema.
export type PolicyStateView = ResponseSchema<'_PolicyProfileResponse'>
export type PreviewPostureView = ResponseSchema<'_PreviewPostureResponse'>
export type AutomaticSafetyView = ResponseSchema<'_AutomaticSafetyResponse'>
export type PreviewSampleView = ResponseSchema<'_PreviewSampleResponse'>
export type PolicyTransitionView = ResponseSchema<'_PolicyTransitionResponse'>
// The answer `read` and `mutate` return is the one the operation their path
// names decodes to (#1730). `OperationOf` resolves a path in types the way
// `matchOperation` resolves it at run time: the query is dropped, and the path
// matches a documented route segment by segment, a `{parameter}` taking exactly
// one non-empty segment. A hole in a template path (`${string}`) is one
// segment, so it can only fill a parameter; `encodeURIComponent` keeps a `/`
// out of it. A path that matches no route, or more than one, names no
// operation, and its answer is `Unrouted`, which no mapper accepts.
type HttpMethod = 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE'
type DocumentedOperation = {
  [Route in keyof paths]: {
    [M in HttpMethod]: [paths[Route][Lowercase<M>]] extends [never]
      ? never
      : paths[Route][Lowercase<M>] extends { readonly responses: unknown }
        ? `${M} ${Route}`
        : never
  }[HttpMethod]
}[keyof paths]
type Segments<S extends string> = S extends `${infer Head}/${infer Tail}`
  ? [Head, ...Segments<Tail>]
  : [S]
type WithoutQuery<P extends string> = P extends `${infer Path}?${string}` ? Path : P
type SegmentsMatch<
  Path extends readonly string[],
  Route extends readonly string[],
> = Path extends readonly [infer S extends string, ...infer PathRest extends string[]]
  ? Route extends readonly [infer R extends string, ...infer RouteRest extends string[]]
    ? (
        R extends `{${string}}` ? (S extends '' ? false : true) : [S] extends [R] ? true : false
      ) extends true
      ? SegmentsMatch<PathRest, RouteRest>
      : false
    : false
  : Route extends readonly []
    ? true
    : false
type Matches<M extends string, P extends string> = {
  [K in DocumentedOperation]: K extends `${M} ${infer Route}`
    ? SegmentsMatch<Segments<WithoutQuery<P>>, Segments<Route>> extends true
      ? K
      : never
    : never
}[DocumentedOperation]
type IsUnion<T, U = T> = T extends unknown ? ([U] extends [T] ? false : true) : never
// Distributes over a union of paths; each single path names at most one operation.
export type OperationOf<M extends string, P extends string> = P extends string
  ? true extends IsUnion<Matches<M, P>>
    ? never
    : Matches<M, P>
  : never
type Decoders = typeof responseDecoders
type Decoded<K extends OperationKey> = {
  [Status in keyof Decoders[K]]: Decoders[K][Status] extends D<infer Value> ? Value : never
}[keyof Decoders[K]]
export interface Unrouted {
  readonly unrouted: never
}
// An operation with a decoder answers what the decoder returns. One with none
// documents only 204, and `request()` accepts nothing else from it (#1731), so
// its answer is `undefined`. `map` sees only the decoded part; `request()`
// returns the 204 before any `map` runs.
type DecodedAnswer<M extends string, P extends string> =
  OperationOf<M, P> extends infer K ? (K extends OperationKey ? Decoded<K> : never) : never
type NoContent<M extends string, P extends string> =
  OperationOf<M, P> extends infer K ? (K extends OperationKey ? never : undefined) : never
export type RequestAnswer<M extends string, P extends string> = [OperationOf<M, P>] extends [never]
  ? Unrouted
  : DecodedAnswer<M, P> | NoContent<M, P>
// Every path some documented route accepts, a parameter as `${string}`, with or
// without a query: `read` and `mutate` refuse any other path, and contextually
// type a template path so that `OperationOf` can resolve it.
type RoutePattern<Route extends string> = Route extends `${infer Head}{${string}}${infer Tail}`
  ? `${Head}${string}${RoutePattern<Tail>}`
  : Route
export type RoutablePath<M extends string> = DocumentedOperation extends infer K
  ? K extends `${M} ${infer Route}`
    ? RoutePattern<Route> | `${RoutePattern<Route>}?${string}`
    : never
  : never
export type PolicyDraft = Schema<'_PolicyDraftRequest'>
export type PolicyActivationRequest = Schema<'_PolicyActivationRequest'>
export type PolicyPreviewView = ResponseSchema<'_PolicyPreviewResponse'>
// The generic policy commands `policyCommand` sends. Preview and activation are
// left out: each carries checks the schema cannot state, so only
// `previewPolicy` and `activatePolicy` send them (ADR-0091 P-X1 to P-X3).
export type PolicyCommandPath =
  | 'validate'
  | 'drafts'
  | 'automatic-safety/pause'
  | 'automatic-safety/recover'
  | 'automatic-safety/suspend'
  | 'preview-mode/enter'
  | 'preview-mode/confirm-automatic'
  | `transitions/${string}/acknowledge`
  | `transitions/${string}/retry`
// Each command's body comes from its operation in the generated `paths`, and
// its answer is what that operation's decoder returns, so a path whose request
// or answer changes fails `tsc` here. Both distribute over a union of paths, so
// a caller holding any command gets the union of their bodies and answers.
type PolicyCommandRoute<P extends PolicyCommandPath> = P extends `transitions/${string}/acknowledge`
  ? '/api/v1/metadata-policy/transitions/{transition_id}/acknowledge'
  : P extends `transitions/${string}/retry`
    ? '/api/v1/metadata-policy/transitions/{transition_id}/retry'
    : `/api/v1/metadata-policy/${P}`
type JsonContent<Part> = Part extends { content: { 'application/json': infer Json } } ? Json : never
export type PolicyCommandBody<P extends PolicyCommandPath> = P extends PolicyCommandPath
  ? paths[PolicyCommandRoute<P>]['post'] extends { requestBody: infer Body }
    ? JsonContent<Body>
    : undefined
  : never
// `mutate` adds its path's 204 answer to what it returns, and spelling that out
// lets a generic `P` compile. Every policy command has a decoder, so for any
// actual path that part is `never`.
export type PolicyCommandAnswer<P extends PolicyCommandPath> =
  | RequestAnswer<'POST', `/api/v1/metadata-policy/${P}`>
  | NoContent<'POST', `/api/v1/metadata-policy/${P}`>
export type PolicyActivationView = ResponseSchema<'_PolicyActivationResponse'>
// The activity family is decoded centrally (#1691): its views are the
// response-truth types, and only the example adds a label of its own.
type ActivityDto = ResponseSchema<'_ActivityResponse'>
// The Activity commands `activityCommand` sends, under `/api/v1/`.
export type ActivityCommandPath =
  | 'activity/consent'
  | `activity/${string}/${string}/revoke`
  | 'field-effects/pause'
  | 'field-effects/resume'
export type ActivityPreparedView = ResponseSchema<'_ActivityPrepared'>
export type ActivityLibraryView = ResponseSchema<'_ActivityLibrary'>
/** `prepared`: prepared changes this destination's permission would carry, and no other. */
export type ActivityDestinationView = ResponseSchema<'_ActivityDestination'>
/**
 * Why prepared work is not moving, split into the four different next steps an
 * owner has, plus what is moving now. One "waiting safely" number could not
 * distinguish consent they can give from a retry they must wait out (#639).
 */
export type ActivityWaitingReasonsView = ResponseSchema<'_ActivityWaitingReasons'>
export type ActivityCapabilityView = ResponseSchema<'_ActivityCapability'>
/**
 * One ledger record. `change` is the exact change, compact enough for one row
 * column; `change_detail` is the same change as a whole sentence, for a reader
 * who hears the row; `change_id` is the stable grouped identity of a change
 * whose latest transition may move; `check_group` is the preparation an
 * automatic no-change check belongs to, and is never shown.
 */
export type ActivityEventView = ResponseSchema<'_ActivityEvent'>
export type ActivityEventType = ActivityEventView['event_type']
/** How a record reached the ledger, in the owner's terms rather than the event's. */
export type ActivityCategory = ActivityEventView['category']
export type ActivityEvidenceView = ResponseSchema<'_ActivityEvidence'>
/** One exact policy setting a Policy record moved, and what it moved between. */
export type ActivityPolicyChangeView = ResponseSchema<'_ActivityPolicyChange'>
export type ActivityDiagnosticView = ResponseSchema<'_ActivityDiagnostic'>
export type ActivityTransitionView = ResponseSchema<'_ActivityTransition'>
export type ActivityTransitionsView = ResponseSchema<'_ActivityTransitions'>
export interface ActivitySearch {
  q?: string
  eventType: 'all' | ActivityEventType
  rangeDays: 7 | 30 | 90
  page: number
  pageSize: 25 | 50 | 100
  /** The address asked for an event filter the ledger does not have. */
  filter?: 'unrecognised'
}
export type ActivityLedgerView = ResponseSchema<'_ActivityLedger'>
export type ActivityWaitingView = ResponseSchema<'_ActivityWaiting'>
export type ActivityExampleView = ResponseSchema<'_ActivityExample'> & { work_label: string }
export type ActivitySuspensionView = ResponseSchema<'_ActivitySuspension'>
export type ProviderRecoveryActivityView = ResponseSchema<'_ProviderRecoveryActivity'>
export type ProviderRecoveryDispatchReason =
  ProviderRecoveryActivityView['dispatch_reasons'][number]
export type ProviderRecoveryFailureReason = ProviderRecoveryActivityView['failure_reasons'][number]
/** A configured library whose write permission no longer applies, and why (#1370). */
export type ActivityLapsedConsentView = ResponseSchema<'_ActivityLapsedConsent'>
export type ActivityDisplayView = ActivityDto

export type ActivityPermissionReadinessView = Omit<
  ResponseSchema<'_ActivityPermissionReadiness'>,
  'example'
> & { example: ActivityExampleView | null }

/**
 * The inputs Activity's consent section reads, and nothing else. Library's
 * write-ready notice reads this so it never waits for Activity history (#1236).
 */
export type ActivityConsentView = ResponseSchema<'_ActivityConsentState'>

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
  describeContainedDescription(): Promise<ContainedDescriptionDisclosure>
  testDescriptionModel(
    value: { expected_revision: number; api_key: string; model: string },
    signal?: AbortSignal,
  ): Promise<DescriptionModelMetadata>
  testTasteModel(
    value: { expected_revision: number; api_key: string; model: string },
    signal?: AbortSignal,
  ): Promise<CoverModelMetadata>
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
  /** A Covers tab's page after `cursor`, or only the book `cover` under it. */
  getCovers(
    view: CoverTab,
    at?: { cursor?: string; cover?: string },
    signal?: AbortSignal,
    workClass?: ReadWorkClass,
  ): Promise<CoversView>
  /** Covers → Taste profile: the profile in use, its versions and build. */
  getTasteProfile(signal?: AbortSignal, workClass?: ReadWorkClass): Promise<TasteProfileView>
  /** Try again after the failed build `buildId`; it counts against the daily limit. */
  retryTasteBuild(buildId: string, signal?: AbortSignal): Promise<TasteProfileView>
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
    path: PreparationActionPath,
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
  getPreviewPosture(signal?: AbortSignal, workClass?: ReadWorkClass): Promise<PreviewPostureView>
  getAutomaticSafety(signal?: AbortSignal, workClass?: ReadWorkClass): Promise<AutomaticSafetyView>
  getPreviewSample(signal?: AbortSignal, workClass?: ReadWorkClass): Promise<PreviewSampleView>
  getPolicyTransition(
    signal?: AbortSignal,
    workClass?: ReadWorkClass,
  ): Promise<PolicyTransitionView | null>
  previewPolicy(draft: PolicyDraft, signal?: AbortSignal): Promise<PolicyPreviewView>
  activatePolicy(
    request: PolicyActivationRequest,
    signal?: AbortSignal,
  ): Promise<PolicyActivationView>
  policyCommand<P extends PolicyCommandPath>(
    path: P,
    body?: PolicyCommandBody<P>,
    signal?: AbortSignal,
  ): Promise<PolicyCommandAnswer<P>>
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
    path: ActivityCommandPath,
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
// The network lost a read, or a command that writes nothing, before any answer
// arrived. Deliberately not an ApiError: nothing is uncertain, and no caller's
// ApiError branch changes meaning (#1754).
export class NetworkUnavailableError extends Error {
  constructor() {
    super('The server could not be reached.')
    this.name = 'NetworkUnavailableError'
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
    return mapLibrary(await this.read(`/api/v1/library?${p}`, signal, workClass))
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
    // The guard above proved that the address names the report operation.
    return mapReportDetail(
      await this.read(detailUrl as `/api/v1/library/report?${string}`, signal, workClass),
    )
  }
  // The settings and appearance answers are decoded whole; the settings view
  // is the document itself, with no mapping and no named check (#1689).
  async getSettings(signal?: AbortSignal, workClass: ReadWorkClass = 'foreground') {
    return await this.read('/api/v1/settings', signal, workClass)
  }
  async getAppearance(signal?: AbortSignal, workClass: ReadWorkClass = 'foreground') {
    return await this.read('/api/v1/appearance', signal, workClass)
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
  // Reviewer access is decoded whole (#1690). Every command, including the
  // model test and the contained disclosure, maps its answer inside `mutate`:
  // an answer that fails decoding or a named check is uncertain, never a
  // refusal. The test and the disclosure write nothing, so that is the
  // conservative direction (ADR-0091).
  async getReviewerAccess(signal?: AbortSignal, workClass: ReadWorkClass = 'foreground') {
    return mapReviewerAccess(await this.read('/api/v1/reviewer-access', signal, workClass))
  }
  async refreshOpenRouter(signal?: AbortSignal, task?: ReviewerTask) {
    return this.mutate(
      `/api/v1/reviewer-access/openrouter/refresh?task=${task ?? 'cover_observation'}`,
      {},
      signal,
      undefined,
      'POST',
      false,
      false,
      reviewerAccess,
    )
  }
  async configureOpenRouter(
    value: { expected_revision: number; api_key: string; model: string; task?: ReviewerTask },
    signal?: AbortSignal,
  ) {
    return this.mutate(
      '/api/v1/reviewer-access/openrouter',
      value,
      signal,
      undefined,
      'PUT',
      false,
      false,
      reviewerAccess,
    )
  }
  async describeContainedDescription() {
    return this.mutate(
      '/api/v1/reviewer-access/description-model/contained',
      {},
      undefined,
      undefined,
      'POST',
      false,
      false,
      (value) => value,
    )
  }
  async testDescriptionModel(
    value: { expected_revision: number; api_key: string; model: string },
    signal?: AbortSignal,
  ) {
    return this.mutate(
      '/api/v1/reviewer-access/description-model/test',
      value,
      signal,
      undefined,
      'POST',
      false,
      false,
      (answer) => mapDescriptionMetadata(answer),
    )
  }
  async testTasteModel(
    value: { expected_revision: number; api_key: string; model: string },
    signal?: AbortSignal,
  ) {
    return this.mutate(
      '/api/v1/reviewer-access/taste-model/test',
      { ...value, task: 'cover_taste_build' },
      signal,
      undefined,
      'POST',
      false,
      false,
      (answer) => mapCoverMetadata(answer),
    )
  }
  async clearOpenRouter(expectedRevision: number, signal?: AbortSignal) {
    return this.mutate(
      '/api/v1/reviewer-access/openrouter',
      { expected_revision: expectedRevision },
      signal,
      undefined,
      'DELETE',
      false,
      false,
      reviewerAccess,
    )
  }
  async selectReviewer(
    engine: 'codex' | null,
    expectedRevision: number,
    signal?: AbortSignal,
    task?: ReviewerTask,
  ) {
    return this.mutate(
      '/api/v1/reviewer-access/selection',
      { engine, expected_revision: expectedRevision, ...(task ? { task } : {}) },
      signal,
      undefined,
      'PUT',
      false,
      false,
      reviewerAccess,
    )
  }
  async startCodexDevice(expectedRevision: number, signal?: AbortSignal) {
    return this.mutate(
      '/api/v1/reviewer-access/codex/device',
      { expected_revision: expectedRevision },
      signal,
      undefined,
      'POST',
      false,
      false,
      (value): CodexDeviceStartResult => {
        const { access, device } = value
        return { access: mapReviewerAccess(access), device: mapCodexDevice(device) }
      },
    )
  }
  async getCodexDevice(signal?: AbortSignal, workClass: ReadWorkClass = 'foreground') {
    return mapCodexDevice(
      await this.read('/api/v1/reviewer-access/codex/device', signal, workClass),
    )
  }
  async recheckCodex(signal?: AbortSignal) {
    return this.mutate(
      '/api/v1/reviewer-access/codex/recheck',
      {},
      signal,
      undefined,
      'POST',
      false,
      false,
      codexDevice,
    )
  }
  async cancelCodexDevice(signal?: AbortSignal) {
    return this.mutate(
      '/api/v1/reviewer-access/codex/device/cancel',
      {},
      signal,
      undefined,
      'POST',
      false,
      false,
      codexDevice,
    )
  }
  async clearCodex(expectedRevision: number, signal?: AbortSignal) {
    return this.mutate(
      '/api/v1/reviewer-access/codex',
      { expected_revision: expectedRevision },
      signal,
      undefined,
      'DELETE',
      false,
      false,
      reviewerAccess,
    )
  }
  async getCovers(
    view: CoverTab,
    at: { cursor?: string; cover?: string } = {},
    signal?: AbortSignal,
    workClass: ReadWorkClass = 'foreground',
  ) {
    const p = new URLSearchParams({ view })
    if (at.cursor) p.set('cursor', at.cursor)
    if (at.cover) p.set('cover', at.cover)
    return await this.read(`/api/v1/covers?${p}`, signal, workClass)
  }
  async getTasteProfile(signal?: AbortSignal, workClass: ReadWorkClass = 'foreground') {
    return mapTasteProfile(await this.read('/api/v1/covers/taste', signal, workClass))
  }
  async retryTasteBuild(buildId: string, signal?: AbortSignal) {
    return this.mutate(
      '/api/v1/covers/taste/retry',
      { build_id: buildId },
      signal,
      undefined,
      'POST',
      false,
      false,
      mapTasteProfile,
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
      await this.read(
        `/api/v1/attention${p.size ? (`?${p}` satisfies `?${string}`) : ''}`,
        signal,
        workClass,
      ),
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
      (value) => mapCoverConsideration(value),
    )
  }
  async getPreparation(signal?: AbortSignal, workClass: ReadWorkClass = 'foreground') {
    return mapPreparation(await this.read('/api/v1/review-preparation', signal, workClass))
  }
  // Scan and retry answer the library's progress; every other control answers
  // the whole preparation view.
  async preparationAction(
    path: PreparationActionPath,
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
      // `value` is either command's decoded answer; the path, not the type,
      // says which, so each branch narrows to its own.
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
      (value) => mapPreparation(value),
    )
  }
  // The confirmation is mapped inside `mutate`, so one the browser cannot read,
  // or one naming another case, is an uncertain outcome rather than a refusal
  // a same-key Retry would only repeat (#1688).
  async recordDecision(id: string, command: DecisionCommand, signal?: AbortSignal) {
    const map = (value: DecisionAcknowledgementDto) => mapDecisionAcknowledgement(value, id)
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
    return await this.read('/api/v1/metadata-policy/state', signal, workClass)
  }
  async getPreviewPosture(signal?: AbortSignal, workClass: ReadWorkClass = 'foreground') {
    return await this.read('/api/v1/metadata-policy/preview-mode', signal, workClass)
  }
  async getAutomaticSafety(signal?: AbortSignal, workClass: ReadWorkClass = 'foreground') {
    return await this.read('/api/v1/metadata-policy/automatic-safety', signal, workClass)
  }
  async getPreviewSample(signal?: AbortSignal, workClass: ReadWorkClass = 'foreground') {
    return await this.read(
      '/api/v1/metadata-policy/preview-mode/outcomes?limit=10',
      signal,
      workClass,
    )
  }
  async getPolicyTransition(signal?: AbortSignal, workClass: ReadWorkClass = 'foreground') {
    // The decoder accepts `null`: no transition has run.
    return await this.read('/api/v1/metadata-policy/transition', signal, workClass)
  }
  // A preview writes nothing, so an answer that fails decoding or a named check
  // is a 502, never an uncertain write (#1692).
  async previewPolicy(draft: PolicyDraft, signal?: AbortSignal) {
    return this.mutate(
      '/api/v1/metadata-policy/preview',
      draft,
      signal,
      undefined,
      'POST',
      true,
      true,
      (value) => mapPolicyPreview(value),
    )
  }
  // The activation may have committed, so an answer for another policy is an
  // uncertain write, not a refusal (ADR-0091 P-X3).
  async activatePolicy(request: PolicyActivationRequest, signal?: AbortSignal) {
    return this.mutate(
      '/api/v1/metadata-policy/activate',
      request,
      signal,
      undefined,
      'POST',
      false,
      false,
      (value) => {
        const activation = value
        if (activation.policy_id !== request.policy_id)
          throw new ApiError(502, 'invalid_policy_activation_response')
        return activation
      },
    )
  }
  // Every path is decoded by its operation's decoder before `mutate` returns;
  // `mutate`'s answer for the path is `PolicyCommandAnswer<P>` by definition.
  async policyCommand<P extends PolicyCommandPath>(
    path: P,
    body?: PolicyCommandBody<P>,
    signal?: AbortSignal,
  ): Promise<PolicyCommandAnswer<P>> {
    const writesNothing = path === 'validate'
    return this.mutate(
      `/api/v1/metadata-policy/${path}`,
      body,
      signal,
      undefined,
      'POST',
      writesNothing,
      writesNothing,
    )
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
      await this.read(
        `/api/v1/activity${p.size ? (`?${p}` satisfies `?${string}`) : ''}`,
        signal,
        workClass,
      ),
    )
  }

  async getActivityPermissionReadiness(
    signal?: AbortSignal,
    workClass: ReadWorkClass = 'foreground',
  ): Promise<ActivityPermissionReadinessView> {
    const value = await this.read('/api/v1/activity/permission-readiness', signal, workClass)
    const { example } = value
    return {
      ...value,
      example: example === null ? null : { ...example, work_label: 'Work details unavailable' },
    }
  }

  async getActivityConsent(
    signal?: AbortSignal,
    workClass: ReadWorkClass = 'foreground',
  ): Promise<ActivityConsentView> {
    return await this.read('/api/v1/activity/consent-state', signal, workClass)
  }

  async getActivityTransitions(
    eventId: string,
    afterSequence?: number,
    signal?: AbortSignal,
    workClass: ReadWorkClass = 'foreground',
  ): Promise<ActivityTransitionsView> {
    const query: '' | `?${string}` =
      afterSequence === undefined ? '' : `?after_sequence=${String(afterSequence)}`
    return await this.read(
      `/api/v1/activity/${encodeURIComponent(eventId)}/transitions${query}`,
      signal,
      workClass,
    )
  }
  // Consent and revoke answer the decoded Activity view, mapped inside `mutate`
  // so a failed named check is uncertain. Field-effects pause and resume
  // document only 204; `request()` refuses a body from either as an uncertain
  // write (`no_decoder`) before `map` could see it (#1731).
  async activityCommand(
    path: ActivityCommandPath,
    body?: unknown,
    signal?: AbortSignal,
  ): Promise<ActivityView | undefined> {
    const url: `/api/v1/${ActivityCommandPath}` = `/api/v1/${path}`
    return this.mutate(url, body, signal, undefined, 'POST', false, false, mapActivity)
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

  private async read<P extends RoutablePath<'GET'>>(
    path: P,
    signal?: AbortSignal,
    workClass: ReadWorkClass = 'foreground',
  ) {
    return this.request<RequestAnswer<'GET', P>>(
      path,
      { headers: { 'X-ML-Work-Class': workClass }, ...(signal ? { signal } : {}) },
      { unsafe: false },
    )
  }
  // `map` runs on the decoded answer of the operation. A named check it
  // fails on a request that may have written is uncertain, never a refusal.
  private async mutate<
    P extends RoutablePath<M>,
    M extends HttpMethod = 'POST',
    T = RequestAnswer<M, P>,
  >(
    path: P,
    body?: unknown,
    signal?: AbortSignal,
    extraHeaders?: HeadersInit,
    method: M | 'POST' = 'POST',
    sideEffectFree = false,
    writesNothing = false,
    map?: (value: DecodedAnswer<M, P>) => T,
    // `NoInfer`: without `map`, `T` stays the path's answer; a caller's
    // expected type cannot widen it (#1730).
  ): Promise<NoInfer<T> | NoContent<M, P>> {
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
    return await this.request<DecodedAnswer<M, P>, T | NoContent<M, P>>(
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
    )
  }
  // A 403 `csrf` challenge renews the page token once. Only a request that is
  // safe to repeat -- idempotency-keyed or side-effect free -- is then sent
  // once more; every other one, and a resend refused again, asks the owner to
  // press again (#1018).
  private async request<V, R = V>(
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
      map?: (value: V) => R
    },
  ): Promise<R> {
    // A write the network lost may have landed. Only the network calls make
    // that ambiguous: a TypeError from decoding or `map` is a defect and
    // propagates unchanged (#1685). A lost read, or a lost command that writes
    // nothing, is a plain network failure (#1754).
    const network = async <R>(call: () => Promise<R>) => {
      try {
        return await call()
      } catch (error) {
        if (error instanceof TypeError)
          throw unsafe && uncertain ? new AmbiguousMutationError() : new NetworkUnavailableError()
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
    const failed = (at: string, reason: string) => {
      const failure = {
        operation: operation ?? `${method} ${path}`,
        responseStatus: response.status,
        at,
        reason,
      }
      return uncertain ? new UncertainMutationError(failure) : new ResponseDecodeError(failure)
    }
    // Every 2xx answer is decoded by its operation's decoder for that status
    // (#1731). Only an operation with no decoder documents a 204 and nothing
    // else, so a 204 is accepted from it alone, and its answer type
    // (`RequestAnswer`) is `undefined`. Any other 2xx no decoder covers -- an
    // undocumented status, a body from a 204-only operation -- fails closed:
    // uncertain for a write that may have landed, otherwise a 502.
    if (response.status === 204 && operation === undefined) return undefined as R
    const decoder =
      operation === undefined ? undefined : decoders[operation][String(response.status)]
    if (decoder === undefined) throw failed('', 'no_decoder')
    let value: unknown
    try {
      value = await response.json()
    } catch {
      // Only a write that may have landed is uncertain. A command that writes
      // nothing cannot have, so an unreadable answer to it is a 502, as for a
      // read (#1731).
      if (uncertain) throw new AmbiguousMutationError()
      throw new ApiError(502, 'invalid_json_response')
    }
    try {
      value = decoder(value, '')
    } catch (error) {
      if (error instanceof DecodeFailure) throw failed(error.at, error.reason)
      throw error
    }
    // The one place the answer takes its type (#1730). `read` and `mutate` name
    // `V` as the decoded answer of their path: the answer of the operation
    // `OperationOf` resolves the path to, which is the operation `matchOperation`
    // picked above (a test holds the two together for every operation). So the
    // decoder that just ran returned `V`. Without `map`, `R` is `V`: `read`
    // names no `R`, and `mutate`'s `T` defaults to its path's answer and cannot
    // be inferred from its caller (a 204 answer is returned above).
    if (!map) return value as R
    try {
      return map(value as V)
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

// The checks the reviewer-access schema cannot state (ADR-0091); the decoder
// has already enforced every field (#1690). A status must fit the slot that
// carries it (X40-X43 and the three `reviewerStatus` rows).
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
const codexProvisioningReasons = new Set<ReviewerReason>([
  'profile_not_provisioned',
  'profile_unreadable',
  'profile_contract_mismatch',
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
// The OpenRouter model grammar on the status model, which the schema shares
// with the codex slot, so it cannot carry either pattern (X43).
const openRouterModel =
  /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}\/[A-Za-z0-9][A-Za-z0-9._-]{0,190}(?::[A-Za-z0-9][A-Za-z0-9._-]{0,31})?$/u
const codexModel = /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/u
const reviewerStatus = (value: ReviewerEngineStatus, engine: 'codex' | 'openrouter') => {
  const { state, reason, model, tier } = value
  if (value.engine !== engine) return false
  if (engine === 'codex')
    return (
      codexStates.has(state) &&
      (state === 'not_provisioned' ? model === null : model !== null && codexModel.test(model)) &&
      tier === null &&
      (state === 'not_provisioned'
        ? reason !== null && codexProvisioningReasons.has(reason)
        : state === 'host_unsupported'
          ? reason !== null && codexHostReasons.has(reason)
          : state === 'failed' || state === 'interrupted'
            ? reason !== null && codexFailureReasons.has(reason)
            : reason === null)
    )
  const named = model !== null && openRouterModel.test(model)
  return (
    openRouterStates.has(state) &&
    value.host_headroom_bytes === null &&
    (state === 'unconfigured'
      ? model === null && tier === null && reason === null
      : state === 'configured'
        ? tier === null &&
          ((named && reason === null) || (model === null && reason === 'not_selected'))
        : state === 'usable'
          ? named && tier !== null && reason === null
          : openRouterFailures.has(state) && named && tier === null && reason === state)
  )
}

// A catalogue price is a decimal in [0, 1000000]: the persisted `bounded_price`
// grammar, which no schema keyword states (X45 and the two price rows).
const boundedPrice = (price: string) =>
  price.trim() !== '' &&
  Number.isFinite(Number(price)) &&
  Number(price) >= 0 &&
  Number(price) <= 1000000

const mapDescriptionMetadata = (value: DescriptionModelMetadata): DescriptionModelMetadata => {
  if (
    ![value.prompt_price, value.completion_price, value.request_price].every(
      (price) => price === null || boundedPrice(price),
    )
  )
    throw new ApiError(502, 'invalid_description_metadata')
  return value
}

const mapCoverMetadata = (value: CoverModelMetadata): CoverModelMetadata => {
  if (
    ![value.prompt_price, value.completion_price, value.image_price, value.request_price].every(
      (price) => price === null || boundedPrice(price),
    )
  )
    throw new ApiError(502, 'invalid_cover_metadata')
  return value
}

// The profile shown is the newest version, its rules are numbered from 1 in
// section order, and a failure is named exactly when the build failed (#1372).
const tasteSections = ['drawn_to', 'avoid', 'trade_offs', 'keep_or_switch'] as const
export const mapTasteProfile = (value: TasteProfileView): TasteProfileView => {
  const { profile, versions, build } = value
  const rules = profile?.rules ?? []
  if (
    (profile === null) !== (versions.length === 0) ||
    (profile !== null &&
      (profile.version !== versions[0]?.version || profile.built_at !== versions[0].built_at)) ||
    versions.some(
      (row, index) => index > 0 && row.version >= (versions[index - 1]?.version ?? 0),
    ) ||
    rules.some(
      (rule, index) =>
        rule.number !== index + 1 ||
        tasteSections.indexOf(rule.section) <
          tasteSections.indexOf(rules[index - 1]?.section ?? 'drawn_to'),
    ) ||
    (build !== null &&
      ((build.state === 'failed') !== (build.failure !== null) ||
        (build.retry_after !== null) !==
          (build.failure === 'ceiling' || build.failure === 'unknown_charge')))
  )
    throw new ApiError(502, 'invalid_taste_profile')
  return value
}

// The measured-cost accounting (X44), its prices (X45) and one row per model
// and task (X46).
const mapCostEvidence = ({ models }: ReviewerAccessView['cost_evidence']): void => {
  const seen = new Set<string>()
  for (const row of models) {
    if (
      row.requests_attempted !== row.requests_selected ||
      row.requests_completed + row.requests_failed !== row.requests_attempted ||
      row.works > row.requests_selected ||
      (row.task === 'description' && row.distinct_images !== 0) ||
      !boundedPrice(row.total_cost_usd) ||
      !boundedPrice(row.per_1000_usd) ||
      Math.abs(
        Number(row.per_1000_usd) - (Number(row.total_cost_usd) / row.requests_attempted) * 1000,
      ) > 1e-9 ||
      seen.has(`${row.model}/${row.task}`)
    )
      throw new ApiError(502, 'invalid_cost_evidence')
    seen.add(`${row.model}/${row.task}`)
  }
}

const mapReviewerAccess = (value: ReviewerAccessView): ReviewerAccessView => {
  if (!reviewerStatus(value.codex, 'codex') || !reviewerStatus(value.openrouter, 'openrouter'))
    throw new ApiError(502, 'invalid_reviewer_access_response')
  if (value.description !== null && !reviewerStatus(value.description, value.description.engine))
    throw new ApiError(502, 'invalid_description_reviewer')
  mapCostEvidence(value.cost_evidence)
  // Cover prices are observed for exactly the saved OpenRouter cover model.
  const prices = value.cover_metadata
  if (
    prices !== null &&
    (value.mapping?.engine !== 'openrouter' ||
      prices.model !== value.mapping.model ||
      ![
        prices.prompt_price,
        prices.completion_price,
        prices.image_price,
        prices.request_price,
      ].every((price) => price === null || boundedPrice(price)))
  )
    throw new ApiError(502, 'invalid_cover_metadata')
  if (value.description_metadata !== null) mapDescriptionMetadata(value.description_metadata)
  // Taste prices are observed for exactly the saved taste profile model.
  if (value.taste_metadata !== null) {
    if (value.taste_mapping?.model !== value.taste_metadata.model)
      throw new ApiError(502, 'invalid_taste_metadata')
    mapCoverMetadata(value.taste_metadata)
  }
  return value
}
const reviewerAccess = (value: ReviewerAccessView) => mapReviewerAccess(value)

// Issued and waiting carry the sign-in prompt, and no other state does (X47).
const mapCodexDevice = (value: CodexDeviceAuthState): CodexDeviceAuthState => {
  const prompt = [value.verification_url, value.user_code, value.expires_in_seconds]
  if (
    value.state === 'issued' || value.state === 'waiting'
      ? prompt.includes(null)
      : prompt.some((part) => part !== null)
  )
    throw new ApiError(502, 'invalid_codex_device_response')
  return value
}
const codexDevice = (value: CodexDeviceAuthState) => mapCodexDevice(value)
// A settings answer is decoded before `mutate` hands it here; the decoder
// fills a library's omitted language list with its default, the empty list.
const settingsView = (value: SettingsView) => value
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
  companionIsbn: value.companion_isbn,
  coverOnly: value.cover_only,
  providerRequests: value.provider_requests,
  laneKind: value.lane_kind,
  provider: value.provider,
  reason: value.reason,
  status: value.status,
})
// Formats any target or proposed value. It throws nothing: the Activity
// example's before and after values are decoded to `unknown`.
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

// The checks the preview schema cannot state (ADR-0091): the decoder has
// already enforced every field.
const mapPolicyPreview = (value: PolicyPreviewView): PolicyPreviewView => {
  for (const library of value.libraries) {
    // P-X1: one comparison per sampled case.
    if (library.comparisons.length !== library.cases.length)
      throw new ApiError(502, 'invalid_policy_preview_comparisons')
    // P-X2: both sides of a comparison describe the same field.
    for (const comparison of library.comparisons)
      for (const field of comparison.fields)
        if (field.active.field !== field.draft.field)
          throw new ApiError(502, 'invalid_policy_preview_field')
  }
  return value
}
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
// The check the activity schema cannot state (ADR-0091); the decoder has
// already enforced every field (#1691). A recovery's failure reasons are in
// ascending order (`mapActivity.failure-reasons-order`).
export const mapActivity = (value: ActivityDto): ActivityDisplayView => {
  for (const { failure_reasons: reasons } of value.provider_recovery)
    if (reasons.some((reason, position) => position > 0 && (reasons[position - 1] ?? '') > reason))
      throw new ApiError(502, 'invalid_provider_recovery_failure_reasons')
  return value
}

// The decoder holds each member; a review scope must agree with the media kind
// and the recording bases beside it (ADR-0091). A running-time match leaves no
// chapter basis, so the two notes never meet (#1723).
const mapAudiobookReviewScope = (
  value: 'work' | 'recording' | null,
  mediaKind: MediaKind,
  basis: RecordingIdentityBasisDto | null,
  admission: RecordingAdmissionBasisDto | null,
): 'work' | 'recording' | null => {
  if (
    (!['m4b', 'm4a', 'mp3'].includes(mediaKind) && value !== null) ||
    ((basis !== null || admission !== null) && value !== 'recording') ||
    (basis !== null && admission !== null)
  )
    throw new ApiError(502, 'inconsistent_audiobook_review_scope')
  return value
}

const mapRecordingAdmissionBasis = (
  value: RecordingAdmissionBasisDto | null,
): RecordingAdmissionBasisView | null =>
  value === null
    ? null
    : {
        basis: value.basis,
        searched: {
          title: value.searched.title,
          authors: value.searched.authors,
          isbn: value.searched.isbn,
        },
        provider: value.provider,
        candidate: value.candidate,
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
  recordingAdmissionBasis: mapRecordingAdmissionBasis(value.recording_admission_basis),
  audiobookReviewScope: mapAudiobookReviewScope(
    value.audiobook_review_scope,
    value.media_kind,
    value.recording_identity_basis,
    value.recording_admission_basis,
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
    recordingAdmissionBasis: mapRecordingAdmissionBasis(value.recording_admission_basis),
    audiobookReviewScope: mapAudiobookReviewScope(
      value.audiobook_review_scope,
      target.media_kind,
      value.recording_identity_basis,
      value.recording_admission_basis,
    ),
    writesConsented: value.writes_consented,
    fields: value.fields.map(mapReviewField),
  }
}

const mapReviewField = (field: ReviewFieldDto): ReviewFieldView => ({
  classification: field.classification,
  classificationCause: field.classification_cause,
  classificationReason: field.classification_reason,
  destination: field.destination,
  // The decoder admits exactly [] or ['keep_current', 'accept'].
  decisionActions: field.decision_actions.length === 0 ? [] : ['keep_current', 'accept'],
  disposition: field.deterministic_disposition,
  editable: field.editable,
  effectCapability: field.effect_capability,
  needsAttention: field.needs_attention,
  manualHold: field.manual_hold,
  manualReturnRequested: field.manual_return_requested,
  manualReturnFailed: field.manual_return_failed,
  navigationRole: field.navigation_role,
  // An empty or unreadable current value says which, in Audiobookshelf's
  // name, rather than one word for both (#1286). A cover's value is an
  // artifact id, so it reads as the current cover, never as the id (#1363).
  currentValue:
    field.destination === 'cover' && field.current.state === 'value'
      ? 'Current cover'
      : targetValueText(field.current.state, field.current.value),
  currentRaw: field.current.value,
  proposedValues: field.proposed_values,
  choiceContexts: mapChoiceContexts(field.choice_contexts, field.proposed_values.length),
  proposalId: field.proposal_id,
  targetFingerprint: field.target_fingerprint,
  humanDecision: field.human_decision,
  inventoryRole: field.inventory_role,
  automaticDecision: field.automatic_decision,
  automaticWithheldReason: field.automatic_withheld_reason,
  automaticWithheldCondition: field.automatic_withheld_condition,
  earlierAutomaticValue: field.earlier_automatic_value,
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
  items: value.items.map(({ destinations, language, observation, role, source }) => ({
    destinations,
    language,
    observation,
    role,
    source,
  })),
  missingLineageCount: value.missing_lineage_count,
  ...(value.next_cursor ? { nextCursor: value.next_cursor } : {}),
  sourceLedger: value.source_ledger.map(mapProviderOutcome),
})

const mapCoverHistoryItem = (
  item: Extract<HistoryDto['items'][number], { readonly consideration: unknown }>,
  index: number,
): HistoryItemView => {
  const occurredAt = item.occurred_at
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

const mapHistoryItem = (item: HistoryDto['items'][number], index: number): HistoryItemView => {
  if ('checkpoint' in item)
    return { category: 'transition', occurredAt: null, outcome: item.kind, reason: item.reason }
  // A cover item is told apart by its consideration; it carries no version of
  // its own, because its event's version stays in the store (#1847).
  if ('consideration' in item) return mapCoverHistoryItem(item, index)
  const occurredAt = item.occurred_at
  // Each other item kind has its own event schema, named by its version.
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
      chosenBy:
        item.decision === null ? null : item.decision.origin === 'human' ? 'owner' : 'automatic',
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
  // What is left is the preview event.
  return {
    category: 'preview',
    occurredAt,
    // The decoder holds each key to the disposition names (propertyNames).
    dispositions: Object.entries(item.dispositions).map(([outcome, count]) => ({
      count,
      outcome: outcome as PreviewDisposition,
    })),
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
    evidence: item.evidence.map(({ source, language, observation, role }) => ({
      source,
      language,
      observation,
      role,
    })),
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

/**
 * The retained original as the owner's way back (#1363, D2): never the
 * current cover, never reviewed or ranked, and choosable exactly while the
 * server still offers it, which it does only while its copy is kept. One the
 * decoder refused has no size or preview at all (#1362).
 */
const mapRetainedCandidate = (
  candidate: CoverConsiderationDto['candidates'][number],
): CoverCandidateView => {
  const previewless = [
    candidate.height,
    candidate.width,
    candidate.preview_sha256,
    candidate.transport_sha256,
  ].filter((value) => value === null).length
  if (
    (previewless !== 0 && previewless !== 4) ||
    candidate.identity !== null ||
    candidate.incumbent ||
    candidate.match_state !== 'match_not_confirmed' ||
    candidate.match_reason !== null ||
    candidate.observation !== null ||
    candidate.deterministic_rank !== null ||
    candidate.deterministic_rank_key !== null ||
    candidate.manual_eligible !== candidate.selectable ||
    candidate.source_kinds.length !== 1 ||
    candidate.source_kinds[0] !== 'retained'
  )
    throw new ApiError(502, 'contradictory_retained_cover')
  return {
    artifactId: candidate.artifact_id,
    candidateKind: 'retained',
    deterministicCitations: candidate.deterministic_citations,
    deterministicRank: null,
    deterministicRankKey: null,
    height: candidate.height,
    identity: null,
    incumbent: false,
    matchState: 'match_not_confirmed',
    observation: null,
    originalSha256: candidate.original_sha256,
    ordinal: candidate.ordinal,
    previewSha256: candidate.preview_sha256,
    reference: candidate.reference,
    selectable: candidate.selectable,
    manualEligible: candidate.manual_eligible,
    sourceKinds: candidate.source_kinds,
    state: candidate.state,
    width: candidate.width,
  }
}

export const mapCoverConsideration = (value: CoverConsiderationDto): CoverConsiderationView => {
  const providerFetching = value.provider_fetching
  const ready = value.ready
  const candidates = value.candidates.map((candidate): CoverCandidateView => {
    if (candidate.candidate_kind === 'retained') return mapRetainedCandidate(candidate)
    const { identity, incumbent, selectable } = candidate
    if (identity === null) throw new ApiError(502, 'contradictory_cover_identity')
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
    // Why a proposed cover contradicts the book (#1790); the incumbent never
    // carries one.
    const contradiction = incumbent
      ? undefined
      : identity.outcome === 'conflict'
        ? identity.reasons.includes('manifestation_publication_incompatible')
          ? 'edition'
          : 'book'
        : identity.reasons.includes('retained_creator_or_contributor_disagreement')
          ? 'creator'
          : undefined
    const manualEligible = candidate.manual_eligible
    if (manualEligible !== (!incumbent && contradiction === undefined))
      throw new ApiError(502, 'contradictory_cover_manual_eligibility')
    const { reference, height, width } = candidate
    const previewSha256 = candidate.preview_sha256
    // Only a retained original may have no preview (#1362).
    if (
      height === null ||
      width === null ||
      previewSha256 === null ||
      candidate.transport_sha256 === null
    )
      throw new ApiError(502, 'missing_cover_preview')
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
    // A larger copy is never the current cover and always confirmed (#1363).
    if (candidate.candidate_kind === 'larger_copy' && (incumbent || !confirmed))
      throw new ApiError(502, 'contradictory_cover_identity')
    return {
      artifactId: candidate.artifact_id,
      candidateKind: candidate.candidate_kind,
      ...(contradiction === undefined ? {} : { contradiction }),
      deterministicCitations: candidate.deterministic_citations,
      deterministicRank,
      deterministicRankKey,
      height,
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
      width,
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
  // A policy judges the reviewed covers; the retained original is never
  // reviewed, so it may be the only candidate beside no policy (#1363).
  if ((policy === null) !== candidates.every((candidate) => candidate.candidateKind === 'retained'))
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
    stage:
      policy === null
        ? null
        : {
            language: policy.stage.language,
            outcome: policy.stage.outcome,
            reason: policy.stage.reason,
          },
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
