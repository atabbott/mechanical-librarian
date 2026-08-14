import type { components } from '../generated/review-api'

type Schema<Name extends keyof components['schemas']> = components['schemas'][Name]
type LibraryDto = Schema<'_LibraryResponse'>
type AttentionDto = Schema<'_AttentionResponse'>
type CaseDetailDto = Schema<'_CaseDetailResponse'>

export type LibraryLens = 'needs_attention' | 'all' | 'no_attention_needed'
export type DetailSubview = 'review' | 'evidence' | 'history'
export type PolicySection = 'identity' | 'publication' | 'curation' | 'unsupported'
export type DecisionOutcome = 'accept' | 'keep_current' | 'reject'

export interface LibrarySearch { lens: LibraryLens; q?: string; cursor?: string; preview?: string; field?: string; attention?: 'open' }
export interface AttentionSearch { q?: string; cursor?: string }
export interface DetailSearch { field?: string; subview: DetailSubview }
export interface PolicySearch { section: PolicySection }

export interface ProviderOutcomeView { providerRequests: number; laneKind: string; provider: string; reason: string; status: string }
export interface PreparationFailureView { attempt: number; provider: string; reason: string; recovery: string; recoveryAttempt: number; stage: string; status: string }
export interface LibraryWorkView { id: string; reviewCaseId?: string; title: string; authors: readonly string[]; library: string; mediaKind: string; attentionCount: number; attention: string; preparation: PreparationFailureView | null; preparationOutcomes: readonly ProviderOutcomeView[] }
export interface LibraryView { lens: LibraryLens; query?: string; nextCursor?: string; counts: { attention: number; all: number; clear: number }; works: readonly LibraryWorkView[] }
export interface AttentionItemView { id: string; destination: string; title: string; authors: readonly string[]; library: string; mediaKind: string; notices: readonly string[]; provenance: readonly string[]; workAttentionCount: number }
export interface AttentionView { query?: string; nextCursor?: string; total: number; items: readonly AttentionItemView[] }
export interface ReviewFieldView {
  destination: string
  disposition: string
  effectCapability: string
  needsAttention: boolean
  manualHold: boolean
  currentValue: string
  currentRaw: unknown
  proposedValues: readonly Schema<'_ProposedValue'>[]
  proposalId: string
  targetFingerprint: string
  humanDecision: Schema<'_DecisionProjection'> | null
  automaticDecision: Schema<'_AutomaticDecisionProjection'> | null
  notices: readonly string[]
  provenance: readonly string[]
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
  id: string
  current: boolean
  candidates: readonly CoverCandidateView[]
  incumbent: { readonly reference: string | null; readonly state: 'absent' | 'available' | 'unavailable' }
  omissions: readonly Readonly<Record<string, unknown>>[]
  policy: Readonly<Record<string, unknown>> | null
  policyPreferences: { readonly avoidPromotionalText: boolean; readonly avoidTieInBranding: boolean; readonly preferMinimalComposition: boolean }
  policyReference: string
  reviewerStatus: string
  supersedesId?: string
  targetFingerprint: string
}
export interface CaseDetailView { id: string; current: boolean; title: string; authors: readonly string[]; subtitle: string; fields: readonly ReviewFieldView[] }
export type EvidenceView = Schema<'_EvidenceResponse'>
export type HistoryView = Schema<'_HistoryResponse'>
export type PreparationView = Schema<'_PreparationResponse'>
export type PreparationProgress = Schema<'_PreparationProgress'>
export type PolicyStateView = Schema<'_PolicyProfileResponse'>
export type ShadowPostureView = Schema<'_ShadowPostureResponse'>
export type AutomaticSafetyView = Schema<'_AutomaticSafetyResponse'>
export type ShadowSampleView = Schema<'_ShadowSampleResponse'>
export type PolicyTransitionView = Schema<'_PolicyTransitionResponse'>
export type PolicyDraft = Schema<'_PolicyDraftRequest'>
export type PolicyPreviewView = Schema<'_PolicyPreviewResponse'>
export type PolicyActivationView = Schema<'_PolicyActivationResponse'>
export interface ActivityPreparedView { automatic: number; human: number; total: number }
export interface ActivityLibraryView { connection: string; library: string; prepared?: ActivityPreparedView | null }
export interface ActivityDestinationView { consented: boolean; destination: string; state: string }
export interface ActivityCapabilityView { connection: string; destinations: readonly ActivityDestinationView[]; library: string; state: string }
export type ActivityEventType = 'review' | 'policy' | 'metadata' | 'safety' | 'unknown'
export interface ActivityEvidenceView { label: string; value: string }
export interface ActivityEventView { author_label?: string; destination: string; event_id: string; event_type: ActivityEventType; evidence: readonly ActivityEvidenceView[]; failure: string | null; library: string; occurred_at: number | null; origin: string; state: string; work_id: string; work_label: string }
export interface ActivitySearch { q?: string; eventType: 'all' | ActivityEventType; rangeDays: 7 | 30 | 90; page: number; pageSize: 25 | 50 | 100 }
export interface ActivityLedgerView { event_type: ActivitySearch['eventType']; page: number; page_count: number; page_size: ActivitySearch['pageSize']; query: string | null; range_days: ActivitySearch['rangeDays']; total: number }
export interface ActivityWaitingView { destination: string; library: string; state: string; work_id: string; work_label: string }
export interface ActivityExampleView { after: unknown; before: unknown; destination: string; library: string; work_id: string; work_label: string }
export interface ActivitySuspensionView { connection: string; destination: string | null; reason: string; scope: string; updated_at: number }
export interface ProviderRecoveryActivityView { affected_count: number; next_eligible_at: number | null; state: 'waiting' | 'checking' | 'dormant' }
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
}

export type ActivityView = ActivityDisplayView

export interface PolicyDraftDisplayView { activeName: string; draftName: string; changed: boolean }

const sameStringMap = (left: Record<string, string>, right: Record<string, string>) => {
  const leftKeys = Object.keys(left)
  const rightKeys = Object.keys(right)
  return leftKeys.length === rightKeys.length
    && leftKeys.every((key) => Object.hasOwn(right, key) && left[key] === right[key])
}

export const derivePolicyDraftDisplay = (active: PolicyStateView, draft: PolicyDraft): PolicyDraftDisplayView => ({
  activeName: active.profile.name,
  draftName: draft.name,
  changed: draft.name !== active.profile.name
    || !sameStringMap(draft.options, active.profile.options)
    || !sameStringMap(draft.destinations, active.profile.destinations),
})

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
  getLibrary(search: LibrarySearch, signal?: AbortSignal): Promise<LibraryView>
  getAttention(search: AttentionSearch, signal?: AbortSignal): Promise<AttentionView>
  getCaseDetail(reviewCaseId: string, signal?: AbortSignal): Promise<CaseDetailView>
  getEvidence(reviewCaseId: string, cursor?: string, signal?: AbortSignal): Promise<EvidenceView>
  getHistory(reviewCaseId: string, signal?: AbortSignal): Promise<HistoryView>
  retryCoverConsideration(reviewCaseId: string, signal?: AbortSignal): Promise<CoverConsiderationView>
  getPreparation(signal?: AbortSignal): Promise<PreparationView>
  preparationAction(path: string, signal?: AbortSignal): Promise<PreparationView | PreparationProgress>
  recordDecision(reviewCaseId: string, command: DecisionCommand, signal?: AbortSignal): Promise<Schema<'_DecisionAcknowledgement'>>
  returnToAutomatic(reviewCaseId: string, destination: string, signal?: AbortSignal): Promise<void>
  getPolicyState(signal?: AbortSignal): Promise<PolicyStateView>
  getShadowPosture(signal?: AbortSignal): Promise<ShadowPostureView>
  getAutomaticSafety(signal?: AbortSignal): Promise<AutomaticSafetyView>
  getShadowSample(signal?: AbortSignal): Promise<ShadowSampleView>
  getPolicyTransition(signal?: AbortSignal): Promise<PolicyTransitionView | null>
  policyCommand<T>(path: string, body?: unknown, signal?: AbortSignal): Promise<T>
  getActivity(search?: ActivitySearch, signal?: AbortSignal): Promise<ActivityView>
  activityCommand(path: string, body?: unknown, signal?: AbortSignal): Promise<ActivityView | undefined>
  logout(signal?: AbortSignal): Promise<void>
}

export interface CsrfTokenStore { current(): string | undefined; renew(signal?: AbortSignal): Promise<string> }
export interface HttpClientOptions { csrf: CsrfTokenStore; buildIdentity?: string; fetch?: typeof globalThis.fetch; onSessionExpired?: () => void; onBuildMismatch?: () => void }

export class ApiError extends Error {
  constructor(readonly status: number, readonly code: string) { super(`Review API request failed (${String(status)}: ${code})`); this.name = 'ApiError' }
}
export class SessionExpiredError extends ApiError { constructor() { super(401, 'session_expired'); this.name = 'SessionExpiredError' } }
export class BuildMismatchError extends ApiError { constructor() { super(409, 'frontend_build_mismatch'); this.name = 'BuildMismatchError' } }
export class AmbiguousMutationError extends ApiError { constructor() { super(0, 'mutation_outcome_unknown'); this.name = 'AmbiguousMutationError' } }

const csrfPattern = /^[A-Za-z0-9_-]{32,128}$/
const buildPattern = /^[0-9a-f]{64}$/

export class PageCsrfTokenStore implements CsrfTokenStore {
  private token?: string
  constructor(initialToken: string | undefined, private readonly fetchPage: (signal?: AbortSignal) => Promise<Response> = async (signal) => fetch(window.location.href, { cache: 'no-store', credentials: 'same-origin', ...(signal ? { signal } : {}) })) {
    if (initialToken && csrfPattern.test(initialToken)) this.token = initialToken
  }
  current() { return this.token }
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
    this.buildIdentity = options.buildIdentity && buildPattern.test(options.buildIdentity) ? options.buildIdentity : undefined
  }

  async getLibrary(search: LibrarySearch, signal?: AbortSignal) { const p = new URLSearchParams({ lens: search.lens }); if (search.q) p.set('q', search.q); if (search.cursor) p.set('cursor', search.cursor); return mapLibrary(await this.read(`/api/v1/library?${p}`, signal)) }
  async getAttention(search: AttentionSearch, signal?: AbortSignal) { const p = new URLSearchParams(); if (search.q) p.set('q', search.q); if (search.cursor) p.set('cursor', search.cursor); return mapAttention(await this.read(`/api/v1/attention${p.size ? `?${p}` : ''}`, signal)) }
  async getCaseDetail(id: string, signal?: AbortSignal) { return mapCaseDetail(await this.read(`/api/v1/review-cases/${encodeURIComponent(id)}`, signal)) }
  async getEvidence(id: string, cursor?: string, signal?: AbortSignal) { const p = new URLSearchParams({ limit: '50' }); if (cursor) p.set('cursor', cursor); return validateObject(await this.read(`/api/v1/review-cases/${encodeURIComponent(id)}/evidence?${p}`, signal), 'evidence') as unknown as EvidenceView }
  async getHistory(id: string, signal?: AbortSignal) { return validateObject(await this.read(`/api/v1/review-cases/${encodeURIComponent(id)}/history`, signal), 'history') as unknown as HistoryView }
  async retryCoverConsideration(id: string, signal?: AbortSignal) {
    const value = mapCoverConsideration(await this.mutate(`/api/v1/review-cases/${encodeURIComponent(id)}/cover-considerations/retry`, undefined, signal))
    if (value === null) throw new ApiError(502, 'invalid_cover_consideration_response')
    return value
  }
  async getPreparation(signal?: AbortSignal) { return validateObject(await this.read('/api/v1/review-preparation', signal), 'preparation') as unknown as PreparationView }
  async preparationAction(path: string, signal?: AbortSignal) {
    const value = await this.mutate(`/api/v1/review-preparation/${path}`, undefined, signal)
    if (/(?:^|\/)(?:scan|retry)$/.test(path)) return preparationProgress(value)
    return validateObject(value, 'preparation') as unknown as PreparationView
  }
  async recordDecision(id: string, command: DecisionCommand, signal?: AbortSignal) {
    const { idempotencyKey, proposalId, targetFingerprint, selectedValue, destination, outcome, considerationId } = command
    return validateObject(await this.mutate(`/api/v1/review-cases/${encodeURIComponent(id)}/decisions`, { destination, outcome, proposal_id: proposalId, selected_value: selectedValue ?? null, target_fingerprint: targetFingerprint, consideration_id: considerationId ?? null }, signal, { 'idempotency-key': idempotencyKey }), 'decision') as unknown as Schema<'_DecisionAcknowledgement'>
  }
  async returnToAutomatic(id: string, destination: string, signal?: AbortSignal) { await this.mutate(`/api/v1/review-cases/${encodeURIComponent(id)}/fields/${encodeURIComponent(destination)}/return-to-automatic`, undefined, signal) }
  async getPolicyState(signal?: AbortSignal) { return validateObject(await this.read('/api/v1/metadata-policy/state', signal), 'policy_state') as unknown as PolicyStateView }
  async getShadowPosture(signal?: AbortSignal) { return validateObject(await this.read('/api/v1/metadata-policy/shadow', signal), 'shadow_posture') as unknown as ShadowPostureView }
  async getAutomaticSafety(signal?: AbortSignal) { const value = await this.read('/api/v1/metadata-policy/automatic-safety', signal); if (!record(value)) throw new ApiError(502, 'invalid_automatic_safety_response'); return value as AutomaticSafetyView }
  async getShadowSample(signal?: AbortSignal) { return validateObject(await this.read('/api/v1/metadata-policy/shadow/outcomes?limit=10', signal), 'shadow_sample') as unknown as ShadowSampleView }
  async getPolicyTransition(signal?: AbortSignal) { const value = await this.read('/api/v1/metadata-policy/transition', signal); return value === null ? null : validateObject(value, 'policy_transition') as unknown as PolicyTransitionView }
  async policyCommand<T>(path: string, body?: unknown, signal?: AbortSignal) { const value = await this.mutate(`/api/v1/metadata-policy/${path}`, body, signal); if (!record(value)) throw new ApiError(502, 'invalid_policy_response'); return value as T }
  async getActivity(search?: ActivitySearch, signal?: AbortSignal): Promise<ActivityView> {
    const p = new URLSearchParams()
    if (search) { if (search.q) p.set('q', search.q); p.set('event_type', search.eventType); p.set('range_days', String(search.rangeDays)); p.set('page', String(search.page)); p.set('page_size', String(search.pageSize)) }
    return mapActivity(await this.read(`/api/v1/activity${p.size ? `?${p}` : ''}`, signal))
  }
  async activityCommand(path: string, body?: unknown, signal?: AbortSignal): Promise<ActivityView | undefined> { const value = await this.mutate(`/api/v1/${path}`, body, signal); return value === undefined ? undefined : mapActivity(value) }
  async logout(signal?: AbortSignal) { await this.mutate('/api/v1/session/logout', undefined, signal) }

  private async read(path: string, signal?: AbortSignal) { return this.request(path, { ...(signal ? { signal } : {}) }, false) }
  private async mutate(path: string, body?: unknown, signal?: AbortSignal, extraHeaders?: HeadersInit) {
    if (!this.buildIdentity) { this.onBuildMismatch(); throw new BuildMismatchError() }
    const headers = new Headers(extraHeaders)
    headers.set('x-ml-build-identity', this.buildIdentity)
    if (body !== undefined) headers.set('content-type', 'application/json')
    try { return await this.request(path, { method: 'POST', headers, ...(body !== undefined ? { body: JSON.stringify(body) } : {}), ...(signal ? { signal } : {}) }, true) }
    catch (error) { if (error instanceof TypeError) throw new AmbiguousMutationError(); throw error }
  }
  private async request(path: string, init: RequestInit, unsafe: boolean): Promise<unknown> {
    const send = async (token?: string) => { const headers = new Headers(init.headers); if (token) headers.set('x-csrf-token', token); return this.fetch(path, { ...init, headers, credentials: 'same-origin', cache: 'no-store' }) }
    const response = await send(unsafe ? this.options.csrf.current() : undefined)
    if (unsafe && response.status === 403) {
      try { await this.options.csrf.renew(init.signal ?? undefined) } catch (error) { if (error instanceof SessionExpiredError) this.onSessionExpired(); throw error }
      throw new ApiError(403, 'csrf_renewed')
    }
    const responseBuild = response.headers.get('x-ml-build-identity')
    if (responseBuild && this.buildIdentity && responseBuild !== this.buildIdentity) { this.onBuildMismatch(); throw new BuildMismatchError() }
    if (response.status === 401) { this.onSessionExpired(); throw new SessionExpiredError() }
    if (!response.ok) {
      const error = await structuredError(response)
      if (error.code === 'browser_build_mismatch' || error.code === 'frontend_build_mismatch' || error.code === 'build_identity_mismatch') { this.onBuildMismatch(); throw new BuildMismatchError() }
      throw error
    }
    if (response.status === 204) return undefined
    try { return await response.json() as unknown } catch { throw new ApiError(502, 'invalid_json_response') }
  }
}

const record = (value: unknown): value is Record<string, unknown> => typeof value === 'object' && value !== null && !Array.isArray(value)
const stringValue = (value: unknown, field: string) => { if (typeof value !== 'string') throw new ApiError(502, `invalid_${field}`); return value }
const integerValue = (value: unknown, field: string) => { if (!Number.isSafeInteger(value) || (value as number) < 0) throw new ApiError(502, `invalid_${field}`); return value as number }
const optionalString = (value: unknown, field: string) => value === null || value === undefined ? undefined : stringValue(value, field)
const stringArray = (value: unknown, field: string) => { if (!Array.isArray(value) || !value.every((item) => typeof item === 'string')) throw new ApiError(502, `invalid_${field}`); return value }
export const displayValue = (value: unknown): string => value === null ? 'Not set' : typeof value === 'string' || typeof value === 'boolean' ? String(value) : Array.isArray(value) ? value.map(displayValue).join(' · ') : record(value) && typeof value.name === 'string' ? `${value.name}${typeof value.sequence === 'string' ? ` · ${value.sequence}` : ''}` : 'Unavailable'

const validateObject = (value: unknown, name: string): Record<string, unknown> => { if (!record(value) || typeof value.schema_version !== 'string') throw new ApiError(502, `invalid_${name}_response`); return value }
const preparationProgress = (value: unknown): PreparationProgress => {
  if (!record(value)) throw new ApiError(502, 'invalid_preparation_progress')
  for (const field of ['scan_id', 'connection', 'library', 'library_id', 'state']) stringValue(value[field], `preparation_${field}`)
  for (const field of ['checked', 'total', 'ready', 'blocked']) integerValue(value[field], `preparation_${field}`)
  if (typeof value.rescan_requested !== 'boolean') throw new ApiError(502, 'invalid_preparation_rescan_requested')
  return value as unknown as PreparationProgress
}
const activityPrepared = (value: unknown, field: string): ActivityPreparedView => {
  if (!record(value)) throw new ApiError(502, `invalid_${field}`)
  return { automatic: integerValue(value.automatic, `${field}_automatic`), human: integerValue(value.human, `${field}_human`), total: integerValue(value.total, `${field}_total`) }
}
const activityExample = (value: unknown): ActivityExampleView | null => {
  if (value === null) return null
  if (!record(value)) throw new ApiError(502, 'invalid_activity_example')
  return { after: value.after, before: value.before, destination: stringValue(value.destination, 'activity_example_destination'), library: stringValue(value.library, 'activity_example_library'), work_id: stringValue(value.work_id, 'activity_example_work_id'), work_label: 'Work details unavailable' }
}
const activitySuspensions = (value: unknown): ActivitySuspensionView[] => {
  if (!Array.isArray(value)) throw new ApiError(502, 'invalid_activity_suspensions')
  return value.map((item, index) => { if (!record(item)) throw new ApiError(502, `invalid_activity_suspension_${String(index)}`); return { connection: stringValue(item.connection, 'activity_suspension_connection'), destination: item.destination === null ? null : stringValue(item.destination, 'activity_suspension_destination'), reason: stringValue(item.reason, 'activity_suspension_reason'), scope: stringValue(item.scope, 'activity_suspension_scope'), updated_at: integerValue(item.updated_at, 'activity_suspension_updated_at') } })
}
export const mapActivity = (value: unknown): ActivityDisplayView => {
  if (!record(value) || value.schema_version !== 'continuous-field-activity-v2' || !record(value.ledger)) throw new ApiError(502, 'invalid_activity_response')
  const libraries = (raw: unknown, field: string): ActivityLibraryView[] => {
    if (!Array.isArray(raw)) throw new ApiError(502, `invalid_${field}`)
    return raw.map((item, index) => { if (!record(item)) throw new ApiError(502, `invalid_${field}_${String(index)}`); return { connection: stringValue(item.connection, `${field}_connection`), library: stringValue(item.library, `${field}_library`), ...(item.prepared === undefined ? {} : { prepared: item.prepared === null ? null : activityPrepared(item.prepared, `${field}_prepared`) }) } })
  }
  const capabilities = value.capabilities
  const events = value.events
  if (!Array.isArray(capabilities) || !Array.isArray(events) || !record(value.counts)) throw new ApiError(502, 'invalid_activity_response')
  const counts: Record<string, number> = {}
  for (const [key, count] of Object.entries(value.counts)) counts[stringValue(key, 'activity_count_name')] = integerValue(count, 'activity_count')
  const providerRecovery = value.provider_recovery === undefined ? [] : value.provider_recovery
  if (!Array.isArray(providerRecovery)) throw new ApiError(502, 'invalid_provider_recovery')
  const recovery = providerRecovery.map((item, index) => {
    if (!record(item) || !['waiting', 'checking', 'dormant'].includes(stringValue(item.state, `provider_recovery_state_${String(index)}`))) throw new ApiError(502, `invalid_provider_recovery_${String(index)}`)
    return { state: item.state as ProviderRecoveryActivityView['state'], affected_count: integerValue(item.affected_count, 'provider_recovery_affected_count'), next_eligible_at: item.next_eligible_at === null ? null : integerValue(item.next_eligible_at, 'provider_recovery_next_eligible_at') }
  })
  return {
    backup_ready: typeof value.backup_ready === 'boolean' ? value.backup_ready : (() => { throw new ApiError(502, 'invalid_activity_backup_ready') })(), capabilities: capabilities.map((item, index) => { if (!record(item) || !Array.isArray(item.destinations)) throw new ApiError(502, `invalid_activity_capability_${String(index)}`); return { connection: stringValue(item.connection, 'activity_connection'), library: stringValue(item.library, 'activity_library'), state: stringValue(item.state, 'activity_state'), destinations: item.destinations.map((destination, destinationIndex) => { if (!record(destination) || typeof destination.consented !== 'boolean') throw new ApiError(502, `invalid_activity_destination_${String(destinationIndex)}`); return { destination: stringValue(destination.destination, 'activity_destination'), state: stringValue(destination.state, 'activity_destination_state'), consented: destination.consented } }) } }),
    configured_libraries: libraries(value.configured_libraries, 'activity_configured_libraries'), consented_libraries: libraries(value.consented_libraries, 'activity_consented_libraries'), counts, enabled_libraries: libraries(value.enabled_libraries, 'activity_enabled_libraries'), provider_recovery: recovery, events: events.map((event, index) => { if (!record(event) || !Array.isArray(event.evidence)) throw new ApiError(502, `invalid_activity_event_${String(index)}`); const occurred_at = event.occurred_at === undefined || event.occurred_at === null ? null : integerValue(event.occurred_at, 'activity_occurred_at'); const failure = event.failure === undefined || event.failure === null ? null : stringValue(event.failure, 'activity_failure'); const author_label = event.author_label === undefined ? undefined : stringValue(event.author_label, 'activity_author_label'); const eventType = stringValue(event.event_type, 'activity_event_type'); if (!['review', 'policy', 'metadata', 'safety', 'unknown'].includes(eventType) || event.evidence.length > 8) throw new ApiError(502, 'invalid_activity_event'); return { destination: stringValue(event.destination, 'activity_event_destination'), event_id: stringValue(event.event_id, 'activity_event_id'), event_type: eventType as ActivityEventType, evidence: event.evidence.map((item) => { if (!record(item)) throw new ApiError(502, 'invalid_activity_evidence'); return { label: stringValue(item.label, 'activity_evidence_label'), value: stringValue(item.value, 'activity_evidence_value') } }), library: stringValue(event.library, 'activity_event_library'), origin: stringValue(event.origin, 'activity_event_origin'), state: stringValue(event.state, 'activity_event_state'), work_id: stringValue(event.work_id, 'activity_work_id'), work_label: event.work_label === undefined ? 'Recorded work' : stringValue(event.work_label, 'activity_work_label'), ...(author_label === undefined ? {} : { author_label }), occurred_at, failure } }),
    example: activityExample(value.example), last_failure: value.last_failure === null ? null : stringValue(value.last_failure, 'activity_last_failure'), ledger: (() => { const item = value.ledger; const eventType = stringValue(item.event_type, 'activity_ledger_type'); const pageSize = integerValue(item.page_size, 'activity_page_size'); const rangeDays = integerValue(item.range_days, 'activity_range_days'); if (!['all', 'review', 'policy', 'metadata', 'safety', 'unknown'].includes(eventType) || ![25, 50, 100].includes(pageSize) || ![7, 30, 90].includes(rangeDays)) throw new ApiError(502, 'invalid_activity_ledger'); return { event_type: eventType as ActivitySearch['eventType'], page: integerValue(item.page, 'activity_page'), page_count: integerValue(item.page_count, 'activity_page_count'), page_size: pageSize as ActivitySearch['pageSize'], query: item.query === null ? null : stringValue(item.query, 'activity_query'), range_days: rangeDays as ActivitySearch['rangeDays'], total: integerValue(item.total, 'activity_total') } })(), current_waiting: value.current_waiting === null ? null : (() => { if (!record(value.current_waiting)) throw new ApiError(502, 'invalid_activity_waiting'); return { destination: stringValue(value.current_waiting.destination, 'activity_waiting_destination'), library: stringValue(value.current_waiting.library, 'activity_waiting_library'), state: stringValue(value.current_waiting.state, 'activity_waiting_state'), work_id: stringValue(value.current_waiting.work_id, 'activity_waiting_work_id'), work_label: stringValue(value.current_waiting.work_label, 'activity_waiting_work_label') } })(), paused: typeof value.paused === 'boolean' ? value.paused : (() => { throw new ApiError(502, 'invalid_activity_paused') })(), prepared: activityPrepared(value.prepared, 'activity_prepared'), schema_version: 'continuous-field-activity-v2', suspensions: activitySuspensions(value.suspensions),
  }
}

export const mapLibrary = (value: unknown): LibraryView => {
  if (!record(value) || !record(value.counts) || !Array.isArray(value.items)) throw new ApiError(502, 'invalid_library_response')
  const lens = stringValue(value.lens, 'library_lens'); if (!['needs_attention', 'all', 'no_attention_needed'].includes(lens)) throw new ApiError(502, 'invalid_library_lens')
  const dto = value as unknown as LibraryDto; stringValue(dto.schema_version, 'schema_version')
  const query = optionalString(dto.query, 'library_query'); const nextCursor = optionalString(dto.next_cursor, 'library_cursor')
  return { lens: lens as LibraryLens, ...(query ? { query } : {}), ...(nextCursor ? { nextCursor } : {}), counts: { attention: integerValue(dto.counts.needs_attention, 'attention_count'), all: integerValue(dto.counts.all, 'all_count'), clear: integerValue(dto.counts.no_attention_needed, 'clear_count') }, works: dto.items.map((item) => {
    const reviewCaseId = optionalString(item.review_case_id, 'review_case_id')
    const preparation = item.preparation == null ? null : { attempt: integerValue(item.preparation.attempt, 'preparation_attempt'), provider: stringValue(item.preparation.provider, 'preparation_provider'), reason: stringValue(item.preparation.reason, 'preparation_reason'), recovery: stringValue(item.preparation.recovery, 'preparation_recovery'), recoveryAttempt: integerValue(item.preparation.recovery_attempt, 'preparation_recovery_attempt'), stage: stringValue(item.preparation.stage, 'preparation_stage'), status: stringValue(item.preparation.status, 'preparation_status') }
    if (!Array.isArray(item.preparation_outcomes)) throw new ApiError(502, 'invalid_preparation_outcomes')
    const preparationOutcomes = (item.preparation_outcomes as unknown[]).map((outcome, index) => {
      if (!record(outcome)) throw new ApiError(502, `invalid_provider_outcome_${String(index)}`)
      return { providerRequests: integerValue(outcome.provider_requests, `provider_outcome_${String(index)}_requests`), laneKind: stringValue(outcome.lane_kind, `provider_outcome_${String(index)}_lane`), provider: stringValue(outcome.provider, `provider_outcome_${String(index)}_provider`), reason: stringValue(outcome.reason, `provider_outcome_${String(index)}_reason`), status: stringValue(outcome.status, `provider_outcome_${String(index)}_status`) }
    })
    return { id: reviewCaseId ?? stringValue(item.work_id, 'work_id'), ...(reviewCaseId ? { reviewCaseId } : {}), title: stringValue(item.title, 'title'), authors: stringArray(item.authors, 'authors'), library: stringValue(item.library, 'library'), mediaKind: stringValue(item.media_kind, 'media_kind'), attentionCount: integerValue(item.attention_count, 'attention_count'), attention: stringValue(item.attention_state, 'attention_state'), preparation, preparationOutcomes }
  }) }
}

export const mapAttention = (value: unknown): AttentionView => {
  if (!record(value) || !Array.isArray(value.items)) throw new ApiError(502, 'invalid_attention_response')
  const dto = value as unknown as AttentionDto; stringValue(dto.schema_version, 'schema_version')
  const query = optionalString(dto.query, 'attention_query'); const nextCursor = optionalString(dto.next_cursor, 'attention_cursor')
  return { ...(query ? { query } : {}), ...(nextCursor ? { nextCursor } : {}), total: integerValue(dto.total, 'attention_total'), items: dto.items.map((item) => ({ id: stringValue(item.review_case_id, 'review_case_id'), destination: stringValue(item.destination, 'destination'), title: stringValue(item.title, 'title'), authors: stringArray(item.authors, 'authors'), library: stringValue(item.library, 'library'), mediaKind: stringValue(item.media_kind, 'media_kind'), notices: stringArray(item.notices, 'notices'), provenance: stringArray(item.provenance, 'provenance'), workAttentionCount: integerValue(item.work_attention_count, 'work_attention_count') })) }
}

export const mapCaseDetail = (value: unknown): CaseDetailView => {
  if (!record(value) || !record(value.target) || !Array.isArray(value.fields)) throw new ApiError(502, 'invalid_case_detail_response')
  const dto = value as unknown as CaseDetailDto; stringValue(dto.schema_version, 'schema_version'); if (typeof dto.current !== 'boolean') throw new ApiError(502, 'invalid_case_currentness')
  return { id: stringValue(dto.review_case_id, 'review_case_id'), current: dto.current, title: stringValue(dto.target.item, 'target_item'), authors: stringArray(dto.target.authors, 'target_authors'), subtitle: `${stringValue(dto.target.library, 'target_library')} · ${stringValue(dto.target.media_kind, 'target_media_kind')} · ${stringValue(dto.target.connection, 'target_connection')}`, fields: dto.fields.map((field) => ({ destination: stringValue(field.destination, 'destination'), disposition: stringValue(field.deterministic_disposition, 'disposition'), effectCapability: stringValue(field.effect_capability, 'effect_capability'), needsAttention: field.needs_attention, manualHold: field.manual_hold, currentValue: displayValue(field.current.value), currentRaw: field.current.value, proposedValues: field.proposed_values, proposalId: stringValue(field.proposal_id, 'proposal_id'), targetFingerprint: stringValue(field.target_fingerprint, 'target_fingerprint'), humanDecision: field.human_decision, automaticDecision: field.automatic_decision, notices: stringArray(field.notices, 'notices'), provenance: stringArray(field.provenance, 'provenance'), coverConsideration: mapCoverConsideration((field as unknown as Record<string, unknown>).cover_consideration) })) }
}

const mapCoverConsideration = (value: unknown): CoverConsiderationView | null => {
  if (value === null || value === undefined) return null
  if (!record(value) || value.schema_version !== 'cover-consideration-v1' || !Array.isArray(value.candidates) || !Array.isArray(value.omissions) || !record(value.incumbent) || !record(value.policy_preferences)) throw new ApiError(502, 'invalid_cover_consideration')
  const incumbentState = stringValue(value.incumbent.state, 'cover_incumbent_state')
  if (!['absent', 'available', 'unavailable'].includes(incumbentState)) throw new ApiError(502, 'invalid_cover_incumbent_state')
  const preferences = value.policy_preferences
  if (typeof preferences.avoid_promotional_text !== 'boolean' || typeof preferences.avoid_tie_in_branding !== 'boolean' || typeof preferences.prefer_minimal_composition !== 'boolean') throw new ApiError(502, 'invalid_cover_policy_preferences')
  return {
    id: stringValue(value.consideration_id, 'cover_consideration_id'),
    current: value.current === true,
    candidates: value.candidates.map((candidate, index) => {
      if (!record(candidate) || !record(candidate.identity) || (candidate.observation !== null && !record(candidate.observation))) throw new ApiError(502, `invalid_cover_candidate_${String(index)}`)
      return { artifactId: stringValue(candidate.artifact_id, 'cover_artifact_id'), height: integerValue(candidate.height, 'cover_height'), identity: candidate.identity, incumbent: candidate.incumbent === true, observation: candidate.observation, originalSha256: stringValue(candidate.original_sha256, 'cover_original_digest'), ordinal: integerValue(candidate.ordinal, 'cover_ordinal'), previewSha256: stringValue(candidate.preview_sha256, 'cover_preview_digest'), reference: stringValue(candidate.reference, 'cover_reference'), selectable: candidate.selectable === true, sourceKinds: stringArray(candidate.source_kinds, 'cover_source_kinds'), state: stringValue(candidate.state, 'cover_state'), width: integerValue(candidate.width, 'cover_width') }
    }),
    incumbent: { reference: value.incumbent.reference === null ? null : stringValue(value.incumbent.reference, 'cover_incumbent_reference'), state: incumbentState as 'absent' | 'available' | 'unavailable' },
    omissions: value.omissions.map((item, index) => { if (!record(item)) throw new ApiError(502, `invalid_cover_omission_${String(index)}`); return item }),
    policy: value.policy === null ? null : record(value.policy) ? value.policy : (() => { throw new ApiError(502, 'invalid_cover_policy') })(),
    policyPreferences: { avoidPromotionalText: preferences.avoid_promotional_text, avoidTieInBranding: preferences.avoid_tie_in_branding, preferMinimalComposition: preferences.prefer_minimal_composition },
    policyReference: stringValue(value.policy_reference, 'cover_policy_reference'),
    reviewerStatus: stringValue(value.reviewer_status, 'cover_reviewer_status'),
    ...(value.supersedes_id === null ? {} : { supersedesId: stringValue(value.supersedes_id, 'cover_supersedes_id') }),
    targetFingerprint: stringValue(value.target_fingerprint, 'cover_target_fingerprint'),
  }
}

const structuredError = async (response: Response) => { let value: unknown; try { value = await response.json() } catch { return new ApiError(response.status, 'invalid_error_response') }; if (!record(value) || !record(value.error) || typeof value.error.code !== 'string') return new ApiError(response.status, 'invalid_error_response'); return new ApiError(response.status, value.error.code) }
