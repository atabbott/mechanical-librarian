import type { components } from '../generated/review-api'
import settingsVocabulary from '../../../src/mechanical_librarian/interfaces/web/settings_vocabulary.json'

type Schema<Name extends keyof components['schemas']> = components['schemas'][Name]
type LibraryDto = Schema<'_LibraryResponse'>
type AttentionDto = Schema<'_AttentionResponse'>
type CaseDetailDto = Schema<'_CaseDetailResponse'>
export type SettingsView = Schema<'SettingsView'>
export type SettingsWrite = Schema<'_SettingsWriteRequest'>
export type AppearanceView = Schema<'_AppearanceResponse'>

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

export const settingsValidationCopy = settingsVocabulary.errors

export type LibraryLens = 'needs_attention' | 'all' | 'no_attention_needed'
export type DetailSubview = 'evidence' | 'history'
export type PolicySection = 'identity' | 'publication' | 'curation' | 'unsupported'
export type DecisionOutcome = 'accept' | 'keep_current' | 'reject'
export type DecisionAction = Exclude<DecisionOutcome, 'reject'>

export interface LibrarySearch {
  lens: LibraryLens
  q?: string
  cursor?: string
  attentionCursor?: string
  preview?: string
  work?: string
  field?: string
  attention?: 'open'
}
export interface AttentionSearch {
  q?: string
  cursor?: string
}
export interface DetailSearch {
  field?: string
  lens: LibraryLens
  subview: DetailSubview
}
export interface PolicySearch {
  section: PolicySection
}

export interface ProviderOutcomeView {
  providerRequests: number
  laneKind: string
  provider: string
  reason: string
  status: string
}
export interface PreparationFailureView {
  attempt: number
  diagnosticReference: string
  provider: string
  reason: string
  recovery: string
  recoveryAttempt: number
  stage: string
  status: string
}
export interface WorkSummaryFieldView {
  destination: string
  state: string
  value: string
}
export interface WorkSummaryView {
  fields: readonly WorkSummaryFieldView[]
  reason: string
  state: string
}
export interface LibraryWorkView {
  id: string
  reviewCaseId?: string
  workId: string
  title: string
  authors: readonly string[]
  library: string
  mediaKind: string
  attentionCount: number
  attention: string
  preparation: PreparationFailureView | null
  preparationOutcomes: readonly ProviderOutcomeView[]
  summary: WorkSummaryView | null
}
export interface LibraryView {
  lens: LibraryLens
  query?: string
  nextCursor?: string
  previousCursor?: string
  page: number
  counts: { attention: number; all: number; clear: number }
  works: readonly LibraryWorkView[]
}
export interface AttentionItemView {
  id: string
  destination: string
  title: string
  authors: readonly string[]
  library: string
  mediaKind: string
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
export interface ChoiceEvidenceView {
  observation: string
  role: 'external_corroboration' | 'local_presence'
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
export interface ReviewFieldView {
  classification: string
  classificationReason: string
  destination: string
  decisionActions: readonly DecisionAction[]
  editable: boolean
  disposition: string
  effectCapability: string
  needsAttention: boolean
  navigationRole: string
  manualHold: boolean
  currentValue: string
  currentRaw: unknown
  proposedValues: readonly Schema<'_ProposedValue'>[]
  choiceContexts: readonly ChoiceContextView[]
  proposalId: string
  targetFingerprint: string
  humanDecision: Schema<'_DecisionProjection'> | null
  inventoryRole?: 'primary' | 'contextual'
  automaticDecision: Schema<'_AutomaticDecisionProjection'> | null
  notices: readonly string[]
  provenance: readonly string[]
  preservationOnly: boolean
  corroborationSources: readonly string[]
  coverConsideration?: CoverConsiderationView | null
}
export interface CoverCandidateView {
  artifactId: string
  height: number
  identity: Readonly<Record<string, unknown>>
  incumbent: boolean
  observation: Readonly<Record<string, unknown>> | null
  originalSha256: string
  ordinal: number
  previewSha256: string
  reference: string
  selectable: boolean
  sourceKinds: readonly string[]
  state: string
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
  policyPreferences: {
    readonly avoidPromotionalText: boolean
    readonly avoidTieInBranding: boolean
    readonly preferMinimalComposition: boolean
  }
  policyReference: string
  reviewerStatus: string
  reviewerUnavailableReason:
    | 'profile contract requires memory_bytes=1073741824'
    | 'profile contract requires process_limit=32'
    | null
  supersedesId?: string
  targetFingerprint: string
}
export interface CaseDetailView {
  id: string
  current: boolean
  target: { connection: string; library: string; item: string; mediaKind: string; title: string }
  title: string
  authors: readonly string[]
  subtitle: string
  comparisonNotices: readonly string[]
  writesConsented: boolean
  fields: readonly ReviewFieldView[]
}
export interface EvidenceObservationView {
  destinations: readonly string[]
  observation: string
  role: 'external_corroboration' | 'local_presence'
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
export type HistoryItemView =
  | {
      category: 'owner'
      destination: string
      outcome: string
      previousValue: HistoryValueView | null
      selectedValue: HistoryValueView | null
    }
  | {
      category: 'automatic'
      destination: string
      outcome: string
      previousValue: HistoryValueView | null
      selectedValue: HistoryValueView | null
    }
  | {
      category: 'preview'
      dispositions: readonly { count: number; outcome: string }[]
    }
  | {
      category: 'cover'
      candidateCount: number
      outcome: string
      ownerOutcome?: string
      policyReasons: readonly string[]
      policyStatus?: string
      reviewerStatus: string
    }
  | { category: 'transition'; outcome: string; reason: string }
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
  destination: string
  /** Prepared changes this destination's permission would carry, and no other. */
  prepared: number
  state: string
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
  state: string
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
  destination: string
  library: string
  state: string
  work_id: string
  work_label: string
}
export interface ActivityExampleView {
  after: unknown
  before: unknown
  destination: string
  library: string
  work_id: string
  work_label: string
}
export interface ActivitySuspensionView {
  connection: string
  destination: string | null
  reason: string
  scope: string
  updated_at: number
}
export interface ProviderRecoveryActivityView {
  affected_count: number
  next_eligible_at: number | null
  state: 'waiting' | 'checking' | 'dormant' | 'recovered' | 'fanout' | 'exhausted'
}
export interface ActivityDisplayView {
  backup_ready: boolean
  capabilities: readonly ActivityCapabilityView[]
  configured_libraries: readonly ActivityLibraryView[]
  consented_libraries: readonly ActivityLibraryView[]
  counts: Readonly<Record<string, number>>
  enabled_libraries: readonly ActivityLibraryView[]
  events: readonly ActivityEventView[]
  example: ActivityExampleView | null
  last_failure: string | null
  ledger: ActivityLedgerView
  current_waiting: ActivityWaitingView | null
  paused: boolean
  prepared: ActivityPreparedView
  provider_recovery?: readonly ProviderRecoveryActivityView[]
  schema_version: 'continuous-field-activity-v2'
  suspensions: readonly ActivitySuspensionView[]
  waiting: ActivityWaitingReasonsView
}

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
}

export interface ReviewHttp {
  getAppearance(signal?: AbortSignal): Promise<AppearanceView>
  getSettings(signal?: AbortSignal): Promise<SettingsView>
  writeSettings(value: SettingsWrite, signal?: AbortSignal): Promise<SettingsView>
  getLibrary(search: LibrarySearch, signal?: AbortSignal): Promise<LibraryView>
  getAttention(search: AttentionSearch, signal?: AbortSignal): Promise<AttentionView>
  getCaseDetail(reviewCaseId: string, signal?: AbortSignal): Promise<CaseDetailView>
  getCaseSuccessor(reviewCaseId: string, signal?: AbortSignal): Promise<string | null>
  getEvidence(reviewCaseId: string, cursor?: string, signal?: AbortSignal): Promise<EvidenceView>
  getHistory(reviewCaseId: string, signal?: AbortSignal): Promise<HistoryView>
  retryCoverConsideration(
    reviewCaseId: string,
    signal?: AbortSignal,
  ): Promise<CoverConsiderationView>
  getPreparation(signal?: AbortSignal): Promise<PreparationView>
  preparationAction(
    path: string,
    signal?: AbortSignal,
  ): Promise<PreparationView | PreparationProgress>
  recordDecision(
    reviewCaseId: string,
    command: DecisionCommand,
    signal?: AbortSignal,
  ): Promise<Schema<'_DecisionAcknowledgement'>>
  returnToAutomatic(reviewCaseId: string, destination: string, signal?: AbortSignal): Promise<void>
  getPolicyState(signal?: AbortSignal): Promise<PolicyStateView>
  getShadowPosture(signal?: AbortSignal): Promise<ShadowPostureView>
  getAutomaticSafety(signal?: AbortSignal): Promise<AutomaticSafetyView>
  getShadowSample(signal?: AbortSignal): Promise<ShadowSampleView>
  getPolicyTransition(signal?: AbortSignal): Promise<PolicyTransitionView | null>
  policyCommand<T>(path: string, body?: unknown, signal?: AbortSignal): Promise<T>
  getActivity(search?: ActivitySearch, signal?: AbortSignal): Promise<ActivityView>
  getActivityTransitions(
    eventId: string,
    afterSequence?: number,
    signal?: AbortSignal,
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

export class ReviewApiClient implements ReviewHttp {
  private readonly fetch: typeof globalThis.fetch
  private readonly onSessionExpired: () => void
  private readonly onBuildMismatch: () => void
  private readonly buildIdentity: string | undefined
  constructor(private readonly options: HttpClientOptions) {
    this.fetch = options.fetch ?? globalThis.fetch.bind(globalThis)
    this.onSessionExpired = options.onSessionExpired ?? (() => window.location.assign('/login'))
    this.onBuildMismatch = options.onBuildMismatch ?? (() => undefined)
    this.buildIdentity =
      options.buildIdentity && buildPattern.test(options.buildIdentity)
        ? options.buildIdentity
        : undefined
  }

  async getLibrary(search: LibrarySearch, signal?: AbortSignal) {
    const p = new URLSearchParams({ lens: search.lens })
    if (search.q) p.set('q', search.q)
    if (search.cursor) p.set('cursor', search.cursor)
    return mapLibrary(await this.read(`/api/v1/library?${p}`, signal))
  }
  async getSettings(signal?: AbortSignal) {
    return mapSettings(await this.read('/api/v1/settings', signal))
  }
  async getAppearance(signal?: AbortSignal) {
    const value: unknown = await this.read('/api/v1/appearance', signal)
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
  async getAttention(search: AttentionSearch, signal?: AbortSignal) {
    const p = new URLSearchParams()
    if (search.q) p.set('q', search.q)
    if (search.cursor) p.set('cursor', search.cursor)
    return mapAttention(await this.read(`/api/v1/attention${p.size ? `?${p}` : ''}`, signal))
  }
  async getCaseDetail(id: string, signal?: AbortSignal) {
    return mapCaseDetail(await this.read(`/api/v1/review-cases/${encodeURIComponent(id)}`, signal))
  }

  async getCaseSuccessor(id: string, signal?: AbortSignal) {
    const value = await this.read(
      `/api/v1/review-cases/${encodeURIComponent(id)}/successor`,
      signal,
    )
    if (typeof value !== 'object' || value === null || !('review_case_id' in value))
      throw new ApiError(502, 'invalid_response')
    const successor = value.review_case_id
    if (successor !== null && typeof successor !== 'string')
      throw new ApiError(502, 'invalid_response')
    return successor
  }
  async getEvidence(id: string, cursor?: string, signal?: AbortSignal) {
    const p = new URLSearchParams({ limit: '50' })
    if (cursor) p.set('cursor', cursor)
    return mapEvidence(
      await this.read(`/api/v1/review-cases/${encodeURIComponent(id)}/evidence?${p}`, signal),
    )
  }
  async getHistory(id: string, signal?: AbortSignal) {
    return mapHistory(
      await this.read(`/api/v1/review-cases/${encodeURIComponent(id)}/history`, signal),
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
  async getPreparation(signal?: AbortSignal) {
    return mapPreparation(await this.read('/api/v1/review-preparation', signal))
  }
  async preparationAction(path: string, signal?: AbortSignal) {
    const value = await this.mutate(`/api/v1/review-preparation/${path}`, undefined, signal)
    if (/(?:^|\/)(?:scan|retry)$/.test(path)) return preparationProgress(value)
    return mapPreparation(value)
  }
  async recordDecision(id: string, command: DecisionCommand, signal?: AbortSignal) {
    const {
      idempotencyKey,
      proposalId,
      targetFingerprint,
      selectedValue,
      destination,
      outcome,
      considerationId,
    } = command
    return schemaVersionOnly<Schema<'_DecisionAcknowledgement'>>(
      await this.mutate(
        `/api/v1/review-cases/${encodeURIComponent(id)}/decisions`,
        {
          destination,
          outcome,
          proposal_id: proposalId,
          selected_value: selectedValue ?? null,
          target_fingerprint: targetFingerprint,
          consideration_id: considerationId ?? null,
        },
        signal,
        { 'idempotency-key': idempotencyKey },
      ),
      'decision',
    )
  }
  async returnToAutomatic(id: string, destination: string, signal?: AbortSignal) {
    await this.mutate(
      `/api/v1/review-cases/${encodeURIComponent(id)}/fields/${encodeURIComponent(destination)}/return-to-automatic`,
      undefined,
      signal,
    )
  }
  async getPolicyState(signal?: AbortSignal) {
    return schemaVersionOnly<PolicyStateView>(
      await this.read('/api/v1/metadata-policy/state', signal),
      'policy_state',
    )
  }
  async getShadowPosture(signal?: AbortSignal) {
    return schemaVersionOnly<ShadowPostureView>(
      await this.read('/api/v1/metadata-policy/shadow', signal),
      'shadow_posture',
    )
  }
  async getAutomaticSafety(signal?: AbortSignal) {
    const value = await this.read('/api/v1/metadata-policy/automatic-safety', signal)
    if (!record(value)) throw new ApiError(502, 'invalid_automatic_safety_response')
    return value as AutomaticSafetyView
  }
  async getShadowSample(signal?: AbortSignal) {
    return schemaVersionOnly<ShadowSampleView>(
      await this.read('/api/v1/metadata-policy/shadow/outcomes?limit=10', signal),
      'shadow_sample',
    )
  }
  async getPolicyTransition(signal?: AbortSignal) {
    const value = await this.read('/api/v1/metadata-policy/transition', signal)
    return value === null
      ? null
      : schemaVersionOnly<PolicyTransitionView>(value, 'policy_transition')
  }
  async policyCommand<T>(path: string, body?: unknown, signal?: AbortSignal) {
    const value = await this.mutate(`/api/v1/metadata-policy/${path}`, body, signal)
    if (!record(value)) throw new ApiError(502, 'invalid_policy_response')
    return value as T
  }
  async getActivity(search?: ActivitySearch, signal?: AbortSignal): Promise<ActivityView> {
    const p = new URLSearchParams()
    if (search) {
      if (search.q) p.set('q', search.q)
      p.set('event_type', search.eventType)
      p.set('range_days', String(search.rangeDays))
      p.set('page', String(search.page))
      p.set('page_size', String(search.pageSize))
    }
    return mapActivity(await this.read(`/api/v1/activity${p.size ? `?${p}` : ''}`, signal))
  }

  async getActivityTransitions(
    eventId: string,
    afterSequence?: number,
    signal?: AbortSignal,
  ): Promise<ActivityTransitionsView> {
    const query = afterSequence === undefined ? '' : `?after_sequence=${String(afterSequence)}`
    const data = await this.read(
      `/api/v1/activity/${encodeURIComponent(eventId)}/transitions${query}`,
      signal,
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
          typeof event.state === 'string',
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

  private async read(path: string, signal?: AbortSignal) {
    return this.request(path, { ...(signal ? { signal } : {}) }, false)
  }
  private async mutate(
    path: string,
    body?: unknown,
    signal?: AbortSignal,
    extraHeaders?: HeadersInit,
    method = 'POST',
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
      )
    } catch (error) {
      if (error instanceof TypeError) throw new AmbiguousMutationError()
      throw error
    }
  }
  private async request(path: string, init: RequestInit, unsafe: boolean): Promise<unknown> {
    const send = async (token?: string) => {
      const headers = new Headers(init.headers)
      if (token) headers.set('x-csrf-token', token)
      return this.fetch(path, { ...init, headers, credentials: 'same-origin', cache: 'no-store' })
    }
    const response = await send(unsafe ? this.options.csrf.current() : undefined)
    if (unsafe && response.status === 403) {
      try {
        await this.options.csrf.renew(init.signal ?? undefined)
      } catch (error) {
        if (error instanceof SessionExpiredError) this.onSessionExpired()
        throw error
      }
      throw new ApiError(403, 'csrf_renewed')
    }
    const responseBuild = response.headers.get('x-ml-build-identity')
    if (responseBuild && this.buildIdentity && responseBuild !== this.buildIdentity) {
      this.onBuildMismatch()
      throw new BuildMismatchError()
    }
    if (response.status === 401) {
      this.onSessionExpired()
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
      throw new ApiError(502, 'invalid_json_response')
    }
  }
}

const record = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value)
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
const booleanValue = (value: unknown, field: string) => {
  if (typeof value !== 'boolean') throw new ApiError(502, `invalid_${field}`)
  return value
}
export const mapSettings = (value: unknown): SettingsView => {
  if (
    !record(value) ||
    (value.authority !== 'file' && value.authority !== 'store') ||
    !record(value.document) ||
    value.document.schema_version !== 'owner-settings-v1' ||
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
        Array.isArray(item.provider_connections),
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
  return value as unknown as SettingsView
}
const mapProviderOutcome = (value: unknown, index: number): ProviderOutcomeView => {
  if (!record(value)) throw new ApiError(502, `invalid_provider_outcome_${String(index)}`)
  return {
    providerRequests: integerValue(
      value.provider_requests,
      `provider_outcome_${String(index)}_requests`,
    ),
    laneKind: boundedStringValue(value.lane_kind, `provider_outcome_${String(index)}_lane`, 64),
    provider: boundedStringValue(value.provider, `provider_outcome_${String(index)}_provider`, 128),
    reason: boundedStringValue(value.reason, `provider_outcome_${String(index)}_reason`, 512),
    status: boundedStringValue(value.status, `provider_outcome_${String(index)}_status`, 64),
  }
}
const decisionAction = (value: unknown): DecisionAction => {
  if (value !== 'accept' && value !== 'keep_current')
    throw new ApiError(502, 'invalid_case_detail_actions')
  return value
}
const classificationValue = (value: unknown) => {
  const allowed = [
    'needs_attention',
    'manual_fallback',
    'automatic_recorded',
    'human_recorded',
    'unsupported',
  ]
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
const preparationProgress = (value: unknown): PreparationProgress => {
  if (!record(value)) throw new ApiError(502, 'invalid_preparation_progress')
  for (const field of ['scan_id', 'connection', 'library', 'library_id', 'state'])
    stringValue(value[field], `preparation_${field}`)
  for (const field of ['checked', 'total', 'ready', 'blocked'])
    integerValue(value[field], `preparation_${field}`)
  if (typeof value.rescan_requested !== 'boolean')
    throw new ApiError(502, 'invalid_preparation_rescan_requested')
  // Validated: every field of the progress DTO is checked above, so the cast
  // only re-attaches the generated type to an already-verified document.
  return value as unknown as PreparationProgress
}
export const mapPreparation = (value: unknown): PreparationView => {
  if (
    !record(value) ||
    value.schema_version !== 'review-preparation-v1' ||
    !Array.isArray(value.libraries) ||
    !Array.isArray(value.connections) ||
    typeof value.paused !== 'boolean' ||
    typeof value.active !== 'boolean' ||
    typeof value.actionable !== 'boolean' ||
    (value.first_boot !== undefined && typeof value.first_boot !== 'boolean') ||
    (value.first_boot_complete !== undefined && typeof value.first_boot_complete !== 'boolean') ||
    (value.first_boot_works !== undefined &&
      value.first_boot_works !== null &&
      (typeof value.first_boot_works !== 'number' ||
        !Number.isInteger(value.first_boot_works) ||
        value.first_boot_works < 0))
  )
    throw new ApiError(502, 'invalid_preparation_response')
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
    destination: stringValue(value.destination, 'activity_example_destination'),
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
      reason: stringValue(item.reason, 'activity_suspension_reason'),
      scope: stringValue(item.scope, 'activity_suspension_scope'),
      updated_at: integerValue(item.updated_at, 'activity_suspension_updated_at'),
    }
  })
}
export const mapActivity = (value: unknown): ActivityDisplayView => {
  if (
    !record(value) ||
    value.schema_version !== 'continuous-field-activity-v2' ||
    !record(value.ledger)
  )
    throw new ApiError(502, 'invalid_activity_response')
  const libraries = (raw: unknown, field: string): ActivityLibraryView[] => {
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
                item.prepared === null
                  ? null
                  : activityPrepared(item.prepared, `${field}_prepared`),
            }),
      }
    })
  }
  const capabilities = value.capabilities
  const events = value.events
  if (!Array.isArray(capabilities) || !Array.isArray(events) || !record(value.counts))
    throw new ApiError(502, 'invalid_activity_response')
  const counts: Record<string, number> = {}
  for (const [key, count] of Object.entries(value.counts))
    counts[stringValue(key, 'activity_count_name')] = integerValue(count, 'activity_count')
  const waitingReasons = value.waiting
  if (!record(waitingReasons)) throw new ApiError(502, 'invalid_activity_waiting')
  const waiting: ActivityWaitingReasonsView = {
    applying: integerValue(waitingReasons.applying, 'activity_waiting_applying'),
    awaiting_permission: integerValue(
      waitingReasons.awaiting_permission,
      'activity_waiting_awaiting_permission',
    ),
    held_for_recovery: integerValue(
      waitingReasons.held_for_recovery,
      'activity_waiting_held_for_recovery',
    ),
    retrying: integerValue(waitingReasons.retrying, 'activity_waiting_retrying'),
    scheduled: integerValue(waitingReasons.scheduled, 'activity_waiting_scheduled'),
  }
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
    return {
      state: item.state as ProviderRecoveryActivityView['state'],
      affected_count: integerValue(item.affected_count, 'provider_recovery_affected_count'),
      next_eligible_at: next,
    }
  })
  return {
    backup_ready:
      typeof value.backup_ready === 'boolean'
        ? value.backup_ready
        : (() => {
            throw new ApiError(502, 'invalid_activity_backup_ready')
          })(),
    capabilities: capabilities.map((item, index) => {
      if (!record(item) || !Array.isArray(item.destinations))
        throw new ApiError(502, `invalid_activity_capability_${String(index)}`)
      return {
        connection: stringValue(item.connection, 'activity_connection'),
        library: stringValue(item.library, 'activity_library'),
        state: stringValue(item.state, 'activity_state'),
        destinations: item.destinations.map((destination, destinationIndex) => {
          if (!record(destination) || typeof destination.consented !== 'boolean')
            throw new ApiError(502, `invalid_activity_destination_${String(destinationIndex)}`)
          return {
            destination: stringValue(destination.destination, 'activity_destination'),
            state: stringValue(destination.state, 'activity_destination_state'),
            consented: destination.consented,
            prepared: integerValue(destination.prepared, 'activity_destination_prepared'),
          }
        }),
      }
    }),
    waiting,
    configured_libraries: libraries(value.configured_libraries, 'activity_configured_libraries'),
    consented_libraries: libraries(value.consented_libraries, 'activity_consented_libraries'),
    counts,
    enabled_libraries: libraries(value.enabled_libraries, 'activity_enabled_libraries'),
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
    example: activityExample(value.example),
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
              destination: stringValue(
                value.current_waiting.destination,
                'activity_waiting_destination',
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
    schema_version: 'continuous-field-activity-v2',
    suspensions: activitySuspensions(value.suspensions),
  }
}

export const mapLibrary = (value: unknown): LibraryView => {
  if (!record(value) || !record(value.counts) || !Array.isArray(value.items))
    throw new ApiError(502, 'invalid_library_response')
  const lens = stringValue(value.lens, 'library_lens')
  if (!['needs_attention', 'all', 'no_attention_needed'].includes(lens))
    throw new ApiError(502, 'invalid_library_lens')
  // Validated at each read: this cast names the DTO's fields, and every one the
  // view model consumes passes through the validators below before it is used.
  const dto = value as unknown as LibraryDto
  stringValue(dto.schema_version, 'schema_version')
  const query = optionalString(dto.query, 'library_query')
  const nextCursor = optionalString(dto.next_cursor, 'library_cursor')
  const previousCursor = optionalString(dto.previous_cursor, 'library_previous_cursor')
  return {
    lens: lens as LibraryLens,
    ...(query ? { query } : {}),
    ...(nextCursor ? { nextCursor } : {}),
    ...(previousCursor ? { previousCursor } : {}),
    page: integerValue(dto.page, 'library_page'),
    counts: {
      attention: integerValue(dto.counts.needs_attention, 'attention_count'),
      all: integerValue(dto.counts.all, 'all_count'),
      clear: integerValue(dto.counts.no_attention_needed, 'clear_count'),
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
              provider: stringValue(item.preparation.provider, 'preparation_provider'),
              reason: stringValue(item.preparation.reason, 'preparation_reason'),
              recovery: stringValue(item.preparation.recovery, 'preparation_recovery'),
              recoveryAttempt: integerValue(
                item.preparation.recovery_attempt,
                'preparation_recovery_attempt',
              ),
              stage: stringValue(item.preparation.stage, 'preparation_stage'),
              status: stringValue(item.preparation.status, 'preparation_status'),
            }
      if (!Array.isArray(item.preparation_outcomes))
        throw new ApiError(502, 'invalid_preparation_outcomes')
      const preparationOutcomes = (item.preparation_outcomes as unknown[]).map(mapProviderOutcome)
      const workId = stringValue(item.work_id, 'work_id')
      const summary =
        item.summary == null
          ? null
          : {
              fields: (Array.isArray(item.summary.fields) ? item.summary.fields : []).map(
                (field, index) => {
                  if (!record(field)) throw new ApiError(502, `invalid_summary_${String(index)}`)
                  return {
                    destination: stringValue(
                      field.destination,
                      `summary_${String(index)}_destination`,
                    ),
                    state: stringValue(field.state, `summary_${String(index)}_state`),
                    value: displayValue(field.value),
                  }
                },
              ),
              reason: stringValue(item.summary.reason, 'summary_reason'),
              state: stringValue(item.summary.state, 'summary_state'),
            }
      return {
        id: reviewCaseId ?? workId,
        ...(reviewCaseId ? { reviewCaseId } : {}),
        workId,
        title: stringValue(item.title, 'title'),
        authors: stringArray(item.authors, 'authors'),
        library: stringValue(item.library, 'library'),
        mediaKind: stringValue(item.media_kind, 'media_kind'),
        attentionCount: integerValue(item.attention_count, 'attention_count'),
        attention: stringValue(item.attention_state, 'attention_state'),
        preparation,
        preparationOutcomes,
        summary,
      }
    }),
  }
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
      destination: stringValue(item.destination, 'destination'),
      title: stringValue(item.title, 'title'),
      authors: stringArray(item.authors, 'authors'),
      library: stringValue(item.library, 'library'),
      mediaKind: stringValue(item.media_kind, 'media_kind'),
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

export const mapCaseDetail = (value: unknown): CaseDetailView => {
  if (
    !record(value) ||
    value.schema_version !== 'review-case-detail-v2' ||
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
  const mediaKind = boundedStringValue(dto.target.media_kind, 'target_media_kind', 64)
  return {
    id: stringValue(dto.review_case_id, 'review_case_id'),
    current: dto.current,
    target: {
      connection: boundedStringValue(dto.target.connection, 'target_connection', 512),
      library,
      item: boundedStringValue(dto.target.item, 'target_item', 512),
      mediaKind,
      title: boundedStringValue(dto.target.title, 'target_title', 512),
    },
    title: boundedStringValue(dto.target.title, 'target_title', 512),
    authors,
    subtitle: `${library} · ${mediaKind}`,
    comparisonNotices: stringArray(dto.comparison_notices, 'comparison_notices'),
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
        destination: stringValue(field.destination, 'destination'),
        decisionActions,
        disposition: stringValue(field.deterministic_disposition, 'disposition'),
        editable: booleanValue(field.editable, 'editable'),
        effectCapability: stringValue(field.effect_capability, 'effect_capability'),
        needsAttention: booleanValue(field.needs_attention, 'needs_attention'),
        manualHold: booleanValue(field.manual_hold, 'manual_hold'),
        navigationRole: navigationRole(field.navigation_role),
        currentValue: displayValue(field.current.value),
        currentRaw: field.current.value,
        proposedValues: field.proposed_values,
        choiceContexts: mapChoiceContexts(field.choice_contexts, field.proposed_values.length),
        proposalId: stringValue(field.proposal_id, 'proposal_id'),
        targetFingerprint: stringValue(field.target_fingerprint, 'target_fingerprint'),
        humanDecision: field.human_decision,
        inventoryRole: inventoryRole(field.inventory_role),
        automaticDecision: field.automatic_decision,
        notices: stringArray(field.notices, 'notices'),
        provenance: stringArray(field.provenance, 'provenance'),
        preservationOnly: booleanValue(field.preservation_only, 'preservation_only'),
        corroborationSources: stringArray(field.corroboration_sources, 'corroboration_sources'),
        // `record(field)` above widens the entry to a string-keyed record, which
        // is how this reads a field the generated DTO does not declare;
        // mapCoverConsideration then validates the value in full.
        coverConsideration: mapCoverConsideration(field.cover_consideration),
      }
    }),
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
      if (role !== 'external_corroboration' && role !== 'local_presence')
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
            boundedStringValue(
              destination,
              `evidence_item_${String(index)}_destination_${String(destinationIndex)}`,
              64,
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
    value.schema_version !== 'review-case-history-v2' ||
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
      if (kind === 'human_decision_recorded' || kind === 'automatic_decision_recorded') {
        integerValue(item.occurred_at, `history_item_${String(index)}_occurred_at`)
        const outcome = stringValue(item.outcome, `history_item_${String(index)}_outcome`)
        const allowed =
          kind === 'human_decision_recorded'
            ? ['accept', 'keep_current', 'reject']
            : ['candidate', 'keep_current', 'ignore']
        if (!allowed.includes(outcome))
          throw new ApiError(502, `invalid_history_item_${String(index)}_outcome`)
        return {
          category: kind === 'human_decision_recorded' ? 'owner' : 'automatic',
          destination: boundedStringValue(
            item.destination,
            `history_item_${String(index)}_destination`,
            64,
          ),
          outcome,
          previousValue: mapHistoryValue(
            item.previous_value,
            `history_item_${String(index)}_previous_value`,
          ),
          selectedValue: mapHistoryValue(
            item.selected_value,
            `history_item_${String(index)}_selected_value`,
          ),
        }
      }
      if (kind === 'shadow_preview_recorded') {
        integerValue(item.occurred_at, `history_item_${String(index)}_occurred_at`)
        if (!record(item.dispositions) || Object.keys(item.dispositions).length > 32)
          throw new ApiError(502, `invalid_history_item_${String(index)}_dispositions`)
        return {
          category: 'preview',
          dispositions: Object.entries(item.dispositions).map(([outcome, count]) => ({
            count: integerValue(count, `history_item_${String(index)}_disposition`),
            outcome: boundedStringValue(
              outcome,
              `history_item_${String(index)}_disposition_name`,
              64,
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
        const ownerOutcome = decision
          ? boundedStringValue(
              decision.outcome,
              `history_item_${String(index)}_decision_outcome`,
              64,
            )
          : undefined
        if (ownerOutcome && !['accept', 'keep_current', 'reject'].includes(ownerOutcome))
          throw new ApiError(502, `invalid_history_item_${String(index)}_decision_outcome`)
        const policyReasons = policy
          ? stringArray(policy.reasons, `history_item_${String(index)}_policy_reasons`)
          : []
        if (
          policyReasons.length > 32 ||
          policyReasons.some((reason) => codePointLength(reason) > 128)
        )
          throw new ApiError(502, `invalid_history_item_${String(index)}_policy_reasons`)
        const reviewerStatus = boundedStringValue(
          item.consideration.reviewer_status,
          `history_item_${String(index)}_reviewer_status`,
          64,
        )
        if (!['completed', 'failed', 'unavailable'].includes(reviewerStatus))
          throw new ApiError(502, `invalid_history_item_${String(index)}_reviewer_status`)
        return {
          category: 'cover',
          candidateCount: item.consideration.candidates.length,
          outcome: kind,
          ...(ownerOutcome ? { ownerOutcome } : {}),
          policyReasons,
          ...(policy
            ? {
                policyStatus: boundedStringValue(
                  policy.status,
                  `history_item_${String(index)}_policy_status`,
                  64,
                ),
              }
            : {}),
          reviewerStatus,
        }
      }
      integerValue(item.checkpoint, `history_item_${String(index)}_checkpoint`)
      if (!['admitted', 'superseded', 'no_case', 'retired', 'tombstoned'].includes(kind))
        throw new ApiError(502, `invalid_history_item_${String(index)}_kind`)
      return {
        category: 'transition',
        outcome: boundedStringValue(kind, `history_item_${String(index)}_kind`, 64),
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
        if (!['local_presence', 'external_corroboration'].includes(role))
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

export const mapCoverConsideration = (value: unknown): CoverConsiderationView | null => {
  if (value === null || value === undefined) return null
  if (
    !record(value) ||
    value.schema_version !== 'cover-consideration-v1' ||
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
  const candidates = value.candidates.map((candidate, index) => {
    if (
      !record(candidate) ||
      !record(candidate.identity) ||
      (candidate.observation !== null && !record(candidate.observation))
    )
      throw new ApiError(502, `invalid_cover_candidate_${String(index)}`)
    const state = stringValue(candidate.state, 'cover_state')
    if (!['acceptable', 'available', 'excluded', 'recommended'].includes(state))
      throw new ApiError(502, 'invalid_cover_candidate_state')
    return {
      artifactId: stringValue(candidate.artifact_id, 'cover_artifact_id'),
      height: integerValue(candidate.height, 'cover_height'),
      identity: candidate.identity,
      incumbent: candidate.incumbent === true,
      observation: candidate.observation,
      originalSha256: stringValue(candidate.original_sha256, 'cover_original_digest'),
      ordinal: integerValue(candidate.ordinal, 'cover_ordinal'),
      previewSha256: stringValue(candidate.preview_sha256, 'cover_preview_digest'),
      reference: stringValue(candidate.reference, 'cover_reference'),
      selectable: candidate.selectable === true,
      sourceKinds: stringArray(candidate.source_kinds, 'cover_source_kinds'),
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
    .filter((candidate) => candidate.selectable && !candidate.incumbent)
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
    (policyStatus === 'abstained' && acceptable.length !== 0) ||
    new Set(acceptable).size !== acceptable.length
  )
    throw new ApiError(502, 'contradictory_cover_policy')
  const byReference = new Map(candidates.map((candidate) => [candidate.reference, candidate]))
  if (
    acceptable.some((reference) => byReference.get(reference)?.selectable !== true) ||
    (recommendedReference !== null &&
      (!acceptable.includes(recommendedReference) ||
        byReference.get(recommendedReference)?.selectable !== true))
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
    policyPreferences: {
      avoidPromotionalText: preferences.avoid_promotional_text,
      avoidTieInBranding: preferences.avoid_tie_in_branding,
      preferMinimalComposition: preferences.prefer_minimal_composition,
    },
    policyReference: stringValue(value.policy_reference, 'cover_policy_reference'),
    reviewerStatus,
    reviewerUnavailableReason: reviewerUnavailableReason as
      | 'profile contract requires memory_bytes=1073741824'
      | 'profile contract requires process_limit=32'
      | null,
    ...(value.supersedes_id === null
      ? {}
      : { supersedesId: stringValue(value.supersedes_id, 'cover_supersedes_id') }),
    targetFingerprint: stringValue(value.target_fingerprint, 'cover_target_fingerprint'),
  }
}

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
