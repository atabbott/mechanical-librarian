// The generated response decoders and the hand mappers in `src/api/http.ts`
// judge the same documents. Every case runs through both, and each divergence
// is classified on `reason@at` in EXPECTED: an unclassified divergence, a stale
// row or an orphan row fails (#1650).
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

import { beforeAll, describe, expect, it, vi } from 'vitest'

import openapi from '../../docs/reference/review-openapi-v1.json'
import {
  ApiError,
  DECODED,
  ReviewApiClient,
  mapActivity,
  mapActivityConsent,
  mapActivityPermissionReadiness,
  mapAttention,
  mapCaseDetail,
  mapCoverConsideration,
  mapEvidence,
  mapHistory,
  mapReviewerAccess,
  mapSettings,
} from '../src/api/http'
import type { DecisionCommand } from '../src/api/http'
import type { D } from '../src/generated/review-decoders'
import { DecodeFailure, matchOperation, responseDecoders } from '../src/generated/review-decoders'
import descriptionAdviceActivityFixture from './descriptionAdviceActivityFixture.json'
import descriptionAdviceFixture from './descriptionAdviceFixture.json'
import descriptionAdvicePreservationFixture from './descriptionAdvicePreservationFixture.json'
import identifierEvidenceFixture from './identifierEvidenceFixture.json'
import * as settingsFixtures from './settingsFixtures'
import {
  activityEvent,
  activityResponse,
  attention,
  caseDetail,
  caseHistory,
  choiceContext,
  coverCandidate,
  coverConsideration,
  coverIdentity,
  coverPolicyReport,
  decisionAcknowledgement,
  historyCoverItem,
  historyDecisionItem,
  historyShadowItem,
  library,
  libraryWork,
  policyPreview,
  policyProfile,
  preparation,
  preparationPresentation,
  reportDetail,
  reviewField,
  scanProgress,
  targetField,
} from './wireFixtures'

type Family =
  | 'Activity'
  | 'Attention'
  | 'CaseDetail'
  | 'Library'
  | 'Policy'
  | 'Preparation'
  | 'ReviewerAccess'
  | 'Settings'
interface Case {
  readonly id: string
  readonly family: Family
  readonly method: string
  readonly path: string
  readonly status: number
  readonly doc: () => unknown
  readonly mutates?: string
  readonly provenance: 'server' | 'builder' | 'mutation'
}
type Divergence = 'decoder_stricter' | 'mapper_cross_field'
interface Expected {
  readonly class: Divergence
  readonly check: string
  // The mapper result the row's own check gives, once its family is decoded
  // (stage-3 Q7): a neighbouring check cannot then stand in for it.
  readonly result?: string
}

// ---- Documents ---------------------------------------------------------------

interface Wire {
  readonly method: string
  readonly path: string
  readonly status: number
  readonly body: unknown
}
const captures = import.meta.glob<Wire>('./wire/*.json', { eager: true, import: 'default' })

type Json = Record<string, unknown>
const segments = (pointer: string) => pointer.split('/').slice(1)
const parentOf = (doc: unknown, pointer: string): [Json, string] => {
  const path = segments(pointer)
  const key = path.pop()
  let node = doc
  for (const segment of path) node = (node as Json)[segment]
  if (key === undefined || typeof node !== 'object' || node === null || !(key in node))
    throw new Error(`no value at ${pointer}`)
  return [node as Json, key]
}
const without = (doc: unknown, pointer: string) => {
  const [parent, key] = parentOf(doc, pointer)
  // eslint-disable-next-line @typescript-eslint/no-dynamic-delete
  delete parent[key]
  return doc
}
const replacing = (pointer: string, value: unknown) => (doc: unknown) => {
  const [parent, key] = parentOf(doc, pointer)
  parent[key] = structuredClone(value)
  return doc
}

type Request = readonly [method: string, path: string, status: number]
const get = (path: string): Request => ['GET', path, 200]
const post = (path: string, status = 200): Request => ['POST', path, status]
const casePath = (id: string, tail = '') => `/api/v1/review-cases/${id}${tail}`

const cases: Case[] = []
const caseById = new Map<string, Case>()
const add = (c: Case) => {
  cases.push(c)
  caseById.set(c.id, c)
  return c
}
// An accept case. Each run gets its own clone, so a mutation never edits a
// capture module or a base's shared literal.
const accepted = (
  provenance: 'server' | 'builder',
  family: Family,
  id: string,
  [method, path, status]: Request,
  build: () => unknown,
) => add({ id, family, method, path, status, doc: () => structuredClone(build()), provenance })
const server = (family: Family, name: string) => {
  const wire = Object.entries(captures).find(([file]) => file.startsWith(`./wire/${name}.`))?.[1]
  if (wire === undefined) throw new Error(`no capture ${name}`)
  return accepted(
    'server',
    family,
    `capture:${name}`,
    [wire.method, wire.path, wire.status],
    () => wire.body,
  )
}
const builder = (family: Family, id: string, request: Request, build: () => unknown) =>
  accepted('builder', family, `base:${id}`, request, build)
const variant = (id: string, from: Case, change: (doc: unknown) => unknown) =>
  add({ ...from, id: `base:${id}`, doc: () => change(from.doc()), provenance: 'builder' })
const deletion = (id: string, from: Case, pointer: string) =>
  add({
    ...from,
    id,
    doc: () => without(from.doc(), pointer),
    mutates: pointer,
    provenance: 'mutation',
  })

// ---- Accept rows: captures with a browser mapper -----------------------------

const [codexCleared, openRouterCleared, openRouterRefresh] = [
  'delete-reviewer-access-codex',
  'delete-reviewer-access-openrouter',
  'post-reviewer-access-openrouter-refresh',
  'put-reviewer-access-openrouter',
  'put-reviewer-access-selection',
].map((name) => server('ReviewerAccess', name)) as [Case, Case, Case]
const codexDevice = server('ReviewerAccess', 'post-reviewer-access-codex-device')
const modelTest = server('ReviewerAccess', 'post-reviewer-access-description-model-test')
const attentionCapture = server('Attention', 'get-attention')
const policyState = server('Policy', 'get-metadata-policy-state')
for (const name of [
  'get-metadata-policy-shadow',
  'get-metadata-policy-shadow-outcomes',
  'get-metadata-policy-transition',
  'get-metadata-policy-automatic-safety',
  'post-metadata-policy-activate',
  'post-metadata-policy-automatic-safety-suspend',
  'post-metadata-policy-drafts',
  'post-metadata-policy-preview',
  'post-metadata-policy-shadow-confirm-automatic',
])
  server('Policy', name)
const selection = server('CaseDetail', 'post-review-cases-description-advice-selection')
server('CaseDetail', 'post-review-cases-description-advice-retry')

const advice = accepted(
  'server',
  'CaseDetail',
  'capture:descriptionAdviceFixture',
  get(casePath(descriptionAdviceFixture.review_case_id)),
  () => descriptionAdviceFixture,
)
const preserved = accepted(
  'server',
  'CaseDetail',
  'capture:descriptionAdvicePreservationFixture',
  get(casePath(descriptionAdvicePreservationFixture.review_case_id)),
  () => descriptionAdvicePreservationFixture,
)
const { detail: identifierDetail, evidence: identifierEvidence } = identifierEvidenceFixture
const identifierCase = identifierDetail.review_case_id
accepted(
  'server',
  'CaseDetail',
  'capture:identifierEvidenceFixture.detail',
  get(casePath(identifierCase)),
  () => identifierDetail,
)
accepted(
  'server',
  'CaseDetail',
  'capture:identifierEvidenceFixture.evidence',
  get(casePath(identifierCase, '/evidence?limit=50')),
  () => identifierEvidence,
)
accepted(
  'server',
  'Activity',
  'capture:descriptionAdviceActivityFixture',
  get('/api/v1/activity'),
  () => descriptionAdviceActivityFixture,
)

// ---- Accept rows: root bases and settings ------------------------------------

const ACTIVITY = get('/api/v1/activity')
const CASE = get(casePath('case-1'))
const COVER = post(casePath('case-1', '/cover-considerations/retry'))
const HISTORY = get(casePath('case-1', '/history'))
const LIBRARY = get('/api/v1/library?lens=all')
const PREPARATION = get('/api/v1/review-preparation')
const REPORT = get('/api/v1/library/report?item=item-9')
const SETTINGS = get('/api/v1/settings')
builder('Library', 'reportDetail', REPORT, reportDetail)
const caseBase = builder('CaseDetail', 'caseDetail', CASE, caseDetail)
const coverBase = builder('CaseDetail', 'coverConsideration', COVER, coverConsideration)
builder('CaseDetail', 'caseHistory', HISTORY, caseHistory)
const decisionBase = builder(
  'CaseDetail',
  'decisionAcknowledgement',
  post(casePath('case-1', '/decisions')),
  decisionAcknowledgement,
)
builder(
  'Preparation',
  'scanProgress',
  post('/api/v1/review-preparation/primary/Books/scan', 202),
  scanProgress,
)
const preparationBase = builder('Preparation', 'preparation', PREPARATION, preparation)
builder('Library', 'library', LIBRARY, library)
builder('Activity', 'activityResponse', ACTIVITY, activityResponse)
builder('Attention', 'attention', get('/api/v1/attention'), attention)
builder('Policy', 'policyProfile', post('/api/v1/metadata-policy/validate'), policyProfile)
builder('Policy', 'policyPreview', post('/api/v1/metadata-policy/preview'), policyPreview)
const settings = builder('Settings', 'settings.store', SETTINGS, () =>
  settingsFixtures.view('store'),
)
builder('Settings', 'settings.file', SETTINGS, () => settingsFixtures.view('file'))

// ---- Accept rows: presence and tolerance variants (inline, Q11) --------------

const issued = variant(
  'codex-device.issued',
  codexDevice,
  replacing('/device', {
    state: 'issued',
    verification_url: 'https://auth.openai.com/codex/device',
    user_code: 'ABCD-EFGH',
    expires_in_seconds: 900,
    failure: null,
  }),
)
const describedByOpenRouter = variant('refresh.description-openrouter', openRouterRefresh, (doc) =>
  replacing('/description_mapping', {
    engine: 'openrouter',
    model: ((doc as Json).mapping as Json).model,
  })(doc),
)
const unavailable = variant(
  'decisionAcknowledgement.unavailable',
  decisionBase,
  replacing('/confirmation', { status: 'unavailable' }),
)
const now = {
  ready: 1,
  recorded: 2,
  not_matched: 0,
  failed: 0,
  in_progress: 1,
  scan_ids: ['scan-1'],
}
const preparationNow = builder('Preparation', 'preparation.now', PREPARATION, () =>
  preparation({ active: true, presentation: preparationPresentation({ now }) }),
)
const throttleEntry = {
  library: 'Synthetic ebooks',
  adapter: 'google_books',
  state: 'waiting',
  reasons: ['burst_throttled'],
  deferred: 214,
  next_retry_at: 1_800_000_000,
  remedy: 'add_api_key',
}
const throttled = builder('Preparation', 'preparation.throttling', PREPARATION, () =>
  preparation({
    provider_throttling: { generation: 3, retry: 'available', entries: [throttleEntry] },
  }),
)
const lapsed = builder('Activity', 'activity.lapsed', ACTIVITY, () =>
  activityResponse({
    lapsed_consents: [{ connection: 'primary', library: 'Fiction', reason: 'policy_changed' }],
  }),
)
// The two Activity roots are the five keys of their component, taken from the
// Activity base.
const projection =
  (...keys: readonly string[]) =>
  () => {
    const activity: Json = activityResponse()
    return Object.fromEntries(keys.map((key) => [key, activity[key]]))
  }
const consentState = builder(
  'Activity',
  'activity.consent-state',
  get('/api/v1/activity/consent-state'),
  projection('backup_ready', 'capabilities', 'configured_libraries', 'lapsed_consents', 'waiting'),
)
const permissionReadiness = builder(
  'Activity',
  'activity.permission-readiness',
  get('/api/v1/activity/permission-readiness'),
  projection('backup_ready', 'capabilities', 'configured_libraries', 'example', 'prepared'),
)
// A recording basis requires the recording scope (`mapAudiobookReviewScope`).
const recordingBasis = builder('Library', 'reportDetail.recording-basis', REPORT, () =>
  reportDetail({
    audiobook_review_scope: 'recording',
    recording_identity_basis: {
      basis: 'chapter_runtime_and_structure',
      provider: 'Audnexus',
      candidate: 'recording-1',
    },
  }),
)
const suggested = variant(
  'descriptionAdviceFixture.suggestion',
  advice,
  replacing('/fields/3/owner_control/entry/suggestions', [
    { rule: 'previous', value: 'Old Title' },
  ]),
)
const activityItems = builder('Activity', 'activity.event-and-recovery', ACTIVITY, () =>
  activityResponse({
    events: [activityEvent()],
    provider_recovery: [
      {
        state: 'waiting',
        affected_count: 2,
        next_eligible_at: 2,
        dispatch_reasons: ['missing_corroboration', 'blanket_scan'],
        failure_reasons: [],
      },
    ],
  }),
)
const series = builder('CaseDetail', 'caseDetail.series', CASE, () =>
  caseDetail({
    fields: [
      reviewField({
        destination: 'series',
        current: targetField({
          destination: 'series',
          value: [{ name: 'Standalone', sequence: null }],
        }),
      }),
    ],
  }),
)
const libraryWithWork = builder('Library', 'library.work', LIBRARY, () =>
  library({ items: [libraryWork()] }),
)

// ---- Presence rows: one per `exactKeys` call site (lines at 04fa742) --------

// [site, family whose PR deletes the site, component, accept document, pointer
// of the component instance in it]. A decoded family's sites are gone from
// http.ts; their cases stay, and both sides must still reject (stage-3 Q6).
const PRESENCE_SITES: readonly (readonly [string, Family, string, Case, string])[] = [
  [
    'ReviewApiClient.startCodexDevice:1517',
    'ReviewerAccess',
    '_CodexDeviceStartResponse',
    issued,
    '',
  ],
  ['reviewerStatus:2020', 'ReviewerAccess', 'ReviewerEngineStatus', codexCleared, '/codex'],
  ['mapDescriptionMetadata:2067', 'ReviewerAccess', 'DescriptionModelMetadata', modelTest, ''],
  [
    'mapCostEvidence:2111',
    'ReviewerAccess',
    'ReviewerCostEvidence',
    codexCleared,
    '/cost_evidence',
  ],
  [
    'mapCostEvidence:2123',
    'ReviewerAccess',
    'MeasuredRun',
    codexCleared,
    '/cost_evidence/models/0/measured_run',
  ],
  [
    'mapCostEvidence:2130',
    'ReviewerAccess',
    'ReviewerCostEstimate',
    codexCleared,
    '/cost_evidence/models/0',
  ],
  ['mapReviewerAccess:2179', 'ReviewerAccess', 'ReviewerAccessView', codexCleared, ''],
  [
    'mapReviewerAccess:2207',
    'ReviewerAccess',
    'OpenRouterRecommendation',
    codexCleared,
    '/candidates/0',
  ],
  [
    'mapReviewerAccess:2233',
    'ReviewerAccess',
    'OpenRouterTaskSelection',
    openRouterRefresh,
    '/mapping',
  ],
  [
    'mapReviewerAccess:2256',
    'ReviewerAccess',
    'CoverModelMetadata',
    openRouterRefresh,
    '/cover_metadata',
  ],
  [
    'mapReviewerAccess:2279',
    'ReviewerAccess',
    'CodexTaskSelection',
    openRouterCleared,
    '/description_mapping',
  ],
  [
    'mapReviewerAccess:2281',
    'ReviewerAccess',
    'OpenRouterTaskSelection',
    describedByOpenRouter,
    '/description_mapping',
  ],
  ['mapCodexDevice:2292', 'ReviewerAccess', '_CodexDeviceAuthResponse', issued, '/device'],
  [
    'mapSettings:2389',
    'Settings',
    'DescriptionAdviceSettings',
    settings,
    '/document/description_advice',
  ],
  [
    'mapDecisionAcknowledgement:2598',
    'CaseDetail',
    '_UnavailableDecisionConfirmation',
    unavailable,
    '/confirmation',
  ],
  [
    'mapDecisionAcknowledgement:2602',
    'CaseDetail',
    '_ConfirmedDecision',
    selection,
    '/confirmation',
  ],
  [
    'preparationPresentation:2736',
    'Preparation',
    '_PreparationNow',
    preparationNow,
    '/presentation/now',
  ],
  [
    'providerThrottling:2767',
    'Preparation',
    '_PreparationThrottling',
    throttled,
    '/provider_throttling',
  ],
  [
    'providerThrottling:2781',
    'Preparation',
    '_PreparationThrottleEntry',
    throttled,
    '/provider_throttling/entries/0',
  ],
  ['activityLapses:2977', 'Activity', '_ActivityLapsedConsent', lapsed, '/lapsed_consents/0'],
  ['mapActivityConsent:2993', 'Activity', '_ActivityConsentState', consentState, ''],
  [
    'mapActivityPermissionReadiness:3017',
    'Activity',
    '_ActivityPermissionReadiness',
    permissionReadiness,
    '',
  ],
  [
    'mapRecordingIdentityBasis:3308',
    'CaseDetail',
    '_RecordingIdentityBasis',
    recordingBasis,
    '/recording_identity_basis',
  ],
  [
    'mapDescriptionCandidate:3589',
    'CaseDetail',
    'DescriptionAdviceCandidate',
    advice,
    '/fields/8/description_advice/candidate',
  ],
  [
    'mapDescriptionCandidate:3615',
    'CaseDetail',
    'DescriptionAdviceOutput',
    advice,
    '/fields/8/description_advice/candidate/output',
  ],
  [
    'mapDescriptionBinding:3640',
    'CaseDetail',
    'DescriptionAdviceBinding',
    advice,
    '/fields/8/description_advice/binding',
  ],
  [
    'mapDescriptionAdvice:3669',
    'CaseDetail',
    '_DescriptionAdviceResponse',
    advice,
    '/fields/8/description_advice',
  ],
  [
    'mapDescriptionAdvice:3718',
    'CaseDetail',
    'AuthoredDescriptionDecision',
    preserved,
    '/fields/8/description_advice/selection',
  ],
  [
    'mapDescriptionAdvice:3746',
    'CaseDetail',
    '_SelectedDescriptionAdvice',
    preserved,
    '/fields/8/description_advice/selected_advice',
  ],
  ['mapOwnerControl:3932', 'CaseDetail', '_OwnerControl', advice, '/fields/1/owner_control'],
  ['mapOwnerControl:3938', 'CaseDetail', '_OwnerEntry', advice, '/fields/3/owner_control/entry'],
  ['mapOwnerControl:3943', 'CaseDetail', '_OwnerSubset', advice, '/fields/1/owner_control/subset'],
  [
    'mapOwnerControl:3956',
    'CaseDetail',
    '_OwnerSuggestion',
    suggested,
    '/fields/3/owner_control/entry/suggestions/0',
  ],
  [
    'mapOwnerControl:3976',
    'CaseDetail',
    '_OwnerSubsetOption',
    advice,
    '/fields/1/owner_control/subset/options/0',
  ],
]

// A site's key list is its component's property set, so a key the schema
// gains becomes a deletion here without a hand edit.
const schemas: Record<string, { properties?: object } | undefined> = openapi.components.schemas
const tagKeys = new Set(
  [...JSON.stringify(openapi).matchAll(/"propertyName":"([^"]+)"/g)].map((match) => match[1]),
)
// Each presence case with the decoder results its deletion may give.
const presence = PRESENCE_SITES.flatMap(([site, , component, instance, pointer]) =>
  Object.keys(schemas[component]?.properties ?? { [`<no ${component}>`]: null }).map((key) => {
    const at = `${pointer}/${key}`
    const results = [`required@${at}`, ...(tagKeys.has(key) ? [`discriminator@${at}`] : [])]
    return [deletion(`presence:${site}:${component}:${key}`, instance, at).id, results] as const
  }),
)

// ---- Tolerance rows T1-T6: the mapper reads a deliberate absence ------------

const TOLERANCES = [
  [
    'T1',
    activityItems,
    '/events/0/check_group',
    'mapActivity http.ts:3191 reads an absent check_group as null (optionalString)',
  ],
  [
    'T2',
    series,
    '/fields/0/current/value/0/sequence',
    'mapCaseDetail http.ts:3868 shows the current value through displayValue (:2546), which reads an absent series sequence as none',
  ],
  [
    'T3',
    coverBase,
    '/reviewer_failure_code',
    'mapCoverConsideration http.ts:4679 reads an absent reviewer_failure_code as null (?? null)',
  ],
  [
    'T6',
    activityItems,
    '/provider_recovery/0/failure_reasons',
    'mapActivity http.ts:3070 reads absent failure_reasons as [] (=== undefined)',
  ],
] as const
for (const [row, instance, pointer] of TOLERANCES) deletion(`tolerance:${row}`, instance, pointer)
// Tolerances whose family is decoded: the absence is now refused on both sides,
// so the case agrees and keeps no row (#1685 drained T5, #1686 drained T4).
const DRAINED = [
  ['T4', libraryWithWork, '/items/0/cover'],
  ['T5', preparationBase, '/provider_throttling'],
] as const
for (const [row, instance, pointer] of DRAINED) deletion(`tolerance:${row}`, instance, pointer)

// ---- B4 reject rows for the two families with no `exactKeys` site -----------

deletion('reject:attention.items', attentionCapture, '/items')
deletion('reject:policy-state.schema_version', policyState, '/schema_version')

// ---- X/S rows: the mapper-stricter checks the suite exercises ---------------

// Accept bases for the checks that no case above reaches.
builder('Activity', 'activity.capability-and-suspension', ACTIVITY, () =>
  activityResponse({
    capabilities: [
      {
        connection: 'primary',
        library: 'Fiction',
        state: 'allowed',
        destinations: [{ destination: 'title', state: 'allowed', consented: true, prepared: 4 }],
      },
    ],
    suspensions: [
      {
        connection: 'primary',
        destination: null,
        reason: 'connection_unavailable',
        scope: 'library',
        updated_at: 1,
      },
    ],
  }),
)
const preview = 'a'.repeat(64)
const coveredWork = builder('Library', 'library.cover-and-failure', LIBRARY, () =>
  library({
    items: [
      libraryWork({
        cover: {
          height: 452,
          preview_sha256: preview,
          url: `/api/v1/library/cover?connection=primary&library=books&item=item-1&preview=${preview}`,
          width: 302,
        },
        preparation: {
          attempt: 1,
          diagnostic_reference: 'diagnostic-1',
          media_health: [],
          provider: 'none',
          reason: 'The file could not be read.',
          recovery: 'Replace the file.',
          recovery_attempt: 0,
          stage: 'extraction',
          status: 'unreadable_media',
        },
      }),
    ],
  }),
)
builder('Library', 'reportDetail.evidence', REPORT, () =>
  reportDetail({
    embedded_cover: { present: true, surface: 'mp4_ilst' },
    evidence: [
      {
        basis: 'sidecar',
        excerpt: 'Recorded Narrator',
        kind: 'contributor',
        role: 'narrator',
        state: 'valid',
        value: 'Recorded Narrator',
      },
    ],
  }),
)
const completed = builder('Preparation', 'preparation.completed', PREPARATION, () =>
  preparation({
    active: true,
    presentation: preparationPresentation({ state: 'completed_clean', completed_at: 101 }),
  }),
)
// One ranked, acceptable candidate with its observation, under an abstaining
// policy.
const consideration = () =>
  coverConsideration({
    candidates: [
      coverCandidate({
        observation: {
          candidate_reference: 'candidate-1',
          composition: 'minimal',
          cover_text_language: 'english',
          explicit_imagery: 'absent',
          format_branding: 'absent',
          medium: 'illustrated',
          overlay_marking: 'absent',
          placeholder: 'absent',
          preview_sha256: 'b'.repeat(64),
          preview_variant: 'cover-preview-v1',
          promotional_text: 'absent',
          reproduction: 'clean',
          series_styling: 'standalone',
          thumbnail_legibility: 'legible',
          tie_in_branding: 'absent',
        },
      }),
    ],
    choice_contexts: [
      choiceContext(),
      choiceContext({ alternative_id: 'candidate-1', selection: 'proposed' }),
    ],
    policy: coverPolicyReport(),
    ready: true,
  })
const covered = builder('CaseDetail', 'coverConsideration.candidate', COVER, consideration)
// One item of each kind mapHistory reads: transition, preview, two owner
// decisions (text and authors, then series) and a cover consideration.
builder('CaseDetail', 'caseHistory.items', HISTORY, () =>
  caseHistory({
    items: [
      { checkpoint: 1, kind: 'admitted', reason: 'actionable', transition_id: 'transition-1' },
      historyShadowItem({ dispositions: { candidate: 1 } }),
      historyDecisionItem({
        destination: 'authors',
        previous_value: { kind: 'text', value: 'Old' },
        selected_value: { kind: 'authors', value: ['Ada Example'] },
      }),
      historyDecisionItem({
        destination: 'series',
        selected_value: { kind: 'series', value: [{ name: 'Saga', sequence: '1' }] },
      }),
      historyCoverItem({ consideration: consideration() }),
    ],
  }),
)

// [case id, accept case, pointer, replacement, the http.ts check (lines at
// 6f660c8, or where its family PR moved it) and why it stays hand-written, and
// once the family is decoded the mapper result of that check alone]. Each
// relates two or more fields, or a position or identity across items or
// documents (A5), or is a single-field check whose only owner is persisted (B3).
const CROSS_FIELD: readonly (readonly [string, Case, string, unknown, string, string?])[] = [
  [
    'check:mapActivity.failure-reasons-order',
    activityItems,
    '/provider_recovery/0/failure_reasons',
    ['transient', 'authentication'],
    'mapActivity http.ts:3087: failure_reasons are in ascending order; positional',
  ],
  [
    'check:mapLibraryCover.preview-binding',
    coveredWork,
    '/items/0/cover/url',
    `/api/v1/library/cover?connection=primary&library=books&item=item-1&preview=${'b'.repeat(64)}`,
    'mapLibraryCover http.ts:3369: the cover url names its own preview_sha256; cross-field',
    'reject:invalid_library_cover',
  ],
  [
    'check:mapAudiobookReviewScope.media-kind',
    recordingBasis,
    '/media_kind',
    'epub',
    'mapAudiobookReviewScope http.ts:3227: a scope needs an audio media_kind; cross-field',
    'reject:inconsistent_audiobook_review_scope',
  ],
  [
    'check:mapAudiobookReviewScope.basis',
    recordingBasis,
    '/audiobook_review_scope',
    'work',
    'mapAudiobookReviewScope http.ts:3228: a recording basis needs the recording scope; cross-field',
    'reject:inconsistent_audiobook_review_scope',
  ],
  [
    'check:mapDescriptionAdvice.outcome-needs-selection',
    advice,
    '/fields/8/description_advice/application_outcome',
    'verified',
    'mapDescriptionAdvice http.ts:3693-3698: an application outcome needs a selection; cross-field',
  ],
  [
    'check:mapDescriptionAdvice.selection-pairing',
    preserved,
    '/fields/8/description_advice/selected_advice',
    null,
    'mapDescriptionAdvice http.ts:3739: selection and selected_advice are both present or both null; cross-field',
  ],
  [
    'check:mapDescriptionAdvice.selection-candidate',
    preserved,
    '/fields/8/description_advice/selected_advice/candidate/candidate_id',
    `description_candidate_${'f'.repeat(64)}`,
    'mapDescriptionAdvice http.ts:3764: the selection names the selected candidate; cross-field',
  ],
  [
    'check:mapDescriptionAdvice.selection-binding',
    preserved,
    '/fields/8/description_advice/selected_advice/binding/access_revision',
    99,
    'mapDescriptionAdvice http.ts:3766-3770: the selection binding equals the selected advice binding; cross-field',
  ],
  [
    'check:mapChoiceContexts.current-first',
    caseBase,
    '/fields/0/choice_contexts',
    [],
    'mapChoiceContexts http.ts:4360: the first choice context is the current value; positional',
  ],
  [
    'check:preparationPresentation.passage-order',
    preparationNow,
    '/presentation/passage/checked',
    3,
    'preparationPresentation http.ts:2742-2748: each passage count is at most the one before it; cross-field',
    'reject:invalid_preparation_passage_order',
  ],
  [
    'check:preparationPresentation.trigger-families',
    preparationNow,
    '/presentation/triggers',
    [
      { trigger: 'manual_scan', accepted_count: 1, first_accepted_at: 100, last_accepted_at: 100 },
      { trigger: 'manual_scan', accepted_count: 1, first_accepted_at: 100, last_accepted_at: 100 },
    ],
    'preparationPresentation http.ts:2749: one entry per trigger family; identity across items',
    'reject:invalid_preparation_trigger',
  ],
  [
    'check:preparationPresentation.active-within-total',
    preparationNow,
    '/presentation/active_overflow',
    5,
    'preparationPresentation http.ts:2756: active books plus overflow are at most total; cross-field',
    'reject:invalid_preparation_passage_total',
  ],
  [
    'check:preparationPresentation.updated-after-start',
    preparationNow,
    '/presentation/updated_at',
    99,
    'preparationPresentation http.ts:2757: updated_at is not before started_at; cross-field',
    'reject:invalid_preparation_passage_total',
  ],
  [
    'check:preparationPresentation.completion-iff-terminal',
    preparationNow,
    '/presentation/state',
    'completed_clean',
    'preparationPresentation http.ts:2758-2759: completed_at is set exactly for a terminal state; cross-field',
    'reject:invalid_preparation_passage_total',
  ],
  [
    'check:preparationPresentation.completed-after-start',
    completed,
    '/presentation/completed_at',
    99,
    'preparationPresentation http.ts:2758: completed_at is not before started_at; cross-field',
    'reject:invalid_preparation_passage_total',
  ],
  [
    'check:reviewerStatus.codex-reason',
    codexCleared,
    '/codex/reason',
    'profile_not_provisioned',
    'reviewerStatus http.ts:2038-2047: the codex state fixes which reasons it may carry; cross-field',
  ],
  [
    'check:reviewerStatus.codex-model-presence',
    codexCleared,
    '/codex/model',
    null,
    'reviewerStatus http.ts:2031-2034: the codex model is null exactly when not_provisioned; cross-field',
  ],
  [
    'check:reviewerStatus.codex-model',
    codexCleared,
    '/codex/model',
    'provider/model',
    'reviewerStatus http.ts:2032-2034: a codex model matches ^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$; ReviewerEngineStatus.model is shared with the openrouter statuses, whose provider/model values are valid, so only a codex-specific status type could carry this pattern',
  ],
  [
    'check:mapReviewerAccess.cover-model',
    openRouterRefresh,
    '/cover_metadata/model',
    'example/other',
    'mapReviewerAccess http.ts:2264-2266: cover_metadata prices the saved openrouter mapping model; cross-field',
  ],
  [
    'check:mapReviewerAccess.cover-price',
    openRouterRefresh,
    '/cover_metadata/image_price',
    'NaN',
    'mapReviewerAccess http.ts:2269-2270 (costPrice :2099): a price is a decimal string in [0, 1000000]. Single-field, but its only owner, domain/reviewer_access.py CoverModelMetadata, is persisted in ReviewerAccessDocument (adapters/persistence/reviewer_access.py:120, :203), so X by B3',
  ],
  [
    'check:mapDescriptionMetadata.price',
    modelTest,
    '/request_price',
    '-1',
    'mapDescriptionMetadata http.ts:2085-2093: a price is a decimal string in [0, 1000000]. Single-field, but its only owner, domain/reviewer_access.py DescriptionModelMetadata, is persisted in ReviewerAccessDocument (adapters/persistence/reviewer_access.py:120, :203), so X by B3',
  ],
  [
    'check:mapCoverConsideration.manual-eligibility',
    covered,
    '/candidates/0/manual_eligible',
    false,
    'mapCoverConsideration http.ts:4568-4574: manual_eligible follows incumbent and identity; cross-field',
  ],
  [
    'check:mapCoverConsideration.rank-key-reference',
    covered,
    '/candidates/0/deterministic_rank_key/6',
    'candidate-2',
    'mapCoverConsideration http.ts:4541: the rank key ends with the candidate reference; cross-field',
  ],
  [
    'check:mapCoverConsideration.match-state',
    covered,
    '/candidates/0/match_state',
    'same_edition',
    'mapCoverConsideration http.ts:4579: match_state follows the identity outcome; cross-field',
  ],
  [
    'check:mapCoverConsideration.unconfirmed-unranked',
    covered,
    '/candidates/0',
    coverCandidate({
      identity: coverIdentity({ outcome: 'unknown' }),
      match_state: 'match_not_confirmed',
    }),
    'mapCoverConsideration http.ts:4580: an unconfirmed candidate is neither selectable nor ranked; cross-field',
  ],
  [
    'check:mapCoverObservation.binding',
    covered,
    '/candidates/0/observation/candidate_reference',
    'candidate-2',
    'mapCoverObservation http.ts:4409-4411: an observation names its candidate reference and preview; cross-field',
  ],
  [
    'check:mapCoverConsideration.unique-references',
    covered,
    '/candidates',
    [coverCandidate(), coverCandidate({ artifact_id: 'artifact-2', ordinal: 1 })],
    'mapCoverConsideration http.ts:4614: candidate references are distinct; identity across items',
  ],
  [
    'check:mapCoverConsideration.ordinal-position',
    covered,
    '/candidates/0/ordinal',
    1,
    'mapCoverConsideration http.ts:4617: a candidate ordinal is its position; positional',
  ],
  [
    'check:mapCoverConsideration.absent-incumbent',
    covered,
    '/incumbent/reference',
    'candidate-1',
    'mapCoverConsideration http.ts:4623-4624: an absent or unavailable incumbent has no reference and no incumbent candidate; cross-field',
  ],
  [
    'check:mapCoverConsideration.available-incumbent',
    covered,
    '/incumbent/state',
    'available',
    'mapCoverConsideration http.ts:4625-4626: an available incumbent is the one incumbent candidate; cross-field',
  ],
  [
    'check:mapChoiceContexts.proposed-count',
    covered,
    '/choice_contexts',
    [choiceContext()],
    'mapChoiceContexts http.ts:4362 (count from mapCoverConsideration :4632): one proposed context per manually eligible alternative; cross-field',
  ],
  [
    'check:mapCoverConsideration.policy-presence',
    covered,
    '/policy',
    null,
    'mapCoverConsideration http.ts:4647: a policy report exists exactly when there are candidates; cross-field',
  ],
  [
    'check:mapCoverConsideration.reviewer-failure-status',
    coverBase,
    '/reviewer_failure_code',
    'timed_out',
    'mapCoverConsideration http.ts:4680-4685: a reviewer failure code needs the failed status; cross-field',
  ],
  [
    'check:mapCoverConsideration.recommended-reference',
    covered,
    '/policy/status',
    'recommended',
    'mapCoverConsideration http.ts:4695: a recommended policy names its candidate; cross-field',
  ],
  [
    'check:mapCoverConsideration.acceptable-ranking',
    covered,
    '/policy/acceptable_candidate_references',
    [],
    'mapCoverConsideration http.ts:4704-4719: acceptable references are the ranked alternatives in rank order; cross-field',
  ],
  [
    'check:mapCoverConsideration.derived-candidate-state',
    covered,
    '/candidates/0/state',
    'available',
    'mapCoverConsideration http.ts:4720-4733: a candidate state follows selectable, the recommendation and the acceptable list; cross-field',
  ],
  [
    'check:mapCoverConsideration.recommendation-context',
    covered,
    '/choice_contexts/0/recommendation',
    'recommended',
    'mapCoverConsideration http.ts:4745-4756: only the recommended candidate context is recommended; cross-field',
  ],
  [
    'check:mapCoverConsideration.readiness',
    covered,
    '/ready',
    false,
    'mapCoverConsideration http.ts:4757-4776: ready follows provider_fetching and the candidates; cross-field',
  ],
  [
    'check:mapDecisionAcknowledgement.case-identity',
    decisionBase,
    '/confirmation/case/review_case_id',
    'another-case',
    'mapDecisionAcknowledgement http.ts:2605: the confirmed case is the requested case; cross-document identity',
  ],
]

for (const [id, from, pointer, value] of CROSS_FIELD)
  add({
    ...from,
    id,
    doc: () => replacing(pointer, value)(from.doc()),
    mutates: pointer,
    provenance: 'mutation',
  })

// ---- Expected divergences ----------------------------------------------------

const EXPECTED: Readonly<Record<string, Expected>> = {
  'capture:post-reviewer-access-codex-device': {
    class: 'mapper_cross_field',
    check:
      'mapCodexDevice http.ts:2332-2339: issued and waiting require verification_url, user_code and expires_in_seconds 900, every other state requires all three null. The capture is the bare waiting of the test double _Auth.start(); #1652 makes it realistic and removes this row.',
  },
  ...Object.fromEntries(
    TOLERANCES.map(([row, , , check]): [string, Expected] => [
      `tolerance:${row}`,
      { class: 'decoder_stricter', check: `${row}: ${check}` },
    ]),
  ),
  ...Object.fromEntries(
    CROSS_FIELD.map(([id, , , , check, result]): [string, Expected] => [
      id,
      { class: 'mapper_cross_field', check, ...(result === undefined ? {} : { result }) },
    ]),
  ),
}

// ---- Runners -----------------------------------------------------------------

const decoders = responseDecoders as Record<string, Record<string, D<unknown>> | undefined>
const decode = (c: Case) => {
  const key = matchOperation(c.method, c.path)
  const decoder = key === undefined ? undefined : decoders[key]?.[String(c.status)]
  if (decoder === undefined) throw new Error(`no decoder for ${c.id}`)
  try {
    decoder(c.doc(), '')
    return 'ok'
  } catch (error) {
    if (!(error instanceof DecodeFailure)) throw error
    return `${error.reason}@${error.at}`
  }
}

// Mappers that are not exported are reached through the client with a one-shot
// fetch (Q13).
const csrf = { current: () => 'a'.repeat(32), renew: async () => 'a'.repeat(32) }
const client = (c: Case) =>
  new ReviewApiClient({
    csrf,
    fetch: vi.fn<typeof fetch>(async () => Response.json(c.doc(), { status: c.status })),
    buildIdentity: 'c'.repeat(64),
  })
const caseId = (c: Case) => segments(new URL(c.path, 'http://local').pathname)[3] ?? ''
const decision: DecisionCommand = {
  destination: 'title',
  outcome: 'accept',
  proposalId: 'p',
  targetFingerprint: 't',
  idempotencyKey: 'k',
}
type Binding = NonNullable<DecisionCommand['authoredDescription']>['binding']
const authored = { ...decision, authoredDescription: { candidateId: 'c', binding: {} as Binding } }
const policy = (c: Case) => client(c).policyCommand(c.path.slice('/api/v1/metadata-policy/'.length))
const reviewerAccess = (c: Case) => mapReviewerAccess(c.doc())
const RUNNERS: Readonly<Record<string, (c: Case) => unknown>> = {
  'GET /api/v1/library': (c) => client(c).getLibrary({ lens: 'all' }),
  'GET /api/v1/library/report': (c) => client(c).getReportDetail(c.path),
  'GET /api/v1/settings': (c) => mapSettings(c.doc()),
  'PUT /api/v1/reviewer-access/openrouter': reviewerAccess,
  'DELETE /api/v1/reviewer-access/openrouter': reviewerAccess,
  'POST /api/v1/reviewer-access/openrouter/refresh': reviewerAccess,
  'PUT /api/v1/reviewer-access/selection': reviewerAccess,
  'DELETE /api/v1/reviewer-access/codex': reviewerAccess,
  'POST /api/v1/reviewer-access/codex/device': (c) => client(c).startCodexDevice(1),
  'POST /api/v1/reviewer-access/description-model/test': (c) =>
    client(c).testDescriptionModel({ expected_revision: 1, api_key: '', model: 'example/text' }),
  'GET /api/v1/attention': (c) => mapAttention(c.doc()),
  'GET /api/v1/review-cases/{review_case_id}': (c) => mapCaseDetail(c.doc()),
  'GET /api/v1/review-cases/{review_case_id}/evidence': (c) => mapEvidence(c.doc()),
  'GET /api/v1/review-cases/{review_case_id}/history': (c) => mapHistory(c.doc()),
  'POST /api/v1/review-cases/{review_case_id}/cover-considerations/retry': (c) =>
    mapCoverConsideration(c.doc()),
  'POST /api/v1/review-cases/{review_case_id}/decisions': (c) =>
    client(c).recordDecision(caseId(c), decision),
  'POST /api/v1/review-cases/{review_case_id}/description-advice/selection': (c) =>
    client(c).recordDecision(caseId(c), authored),
  'POST /api/v1/review-cases/{review_case_id}/description-advice/retry': (c) =>
    client(c).retryDescriptionAdvice(caseId(c)),
  'GET /api/v1/review-preparation': (c) => client(c).getPreparation(),
  'POST /api/v1/review-preparation/{connection}/{library_name}/scan': (c) =>
    client(c).preparationAction(c.path.slice('/api/v1/review-preparation/'.length)),
  'GET /api/v1/activity': (c) => mapActivity(c.doc()),
  'GET /api/v1/activity/consent-state': (c) => mapActivityConsent(c.doc()),
  'GET /api/v1/activity/permission-readiness': (c) => mapActivityPermissionReadiness(c.doc()),
  'GET /api/v1/metadata-policy/state': (c) => client(c).getPolicyState(),
  'GET /api/v1/metadata-policy/shadow': (c) => client(c).getShadowPosture(),
  'GET /api/v1/metadata-policy/shadow/outcomes': (c) => client(c).getShadowSample(),
  'GET /api/v1/metadata-policy/transition': (c) => client(c).getPolicyTransition(),
  'GET /api/v1/metadata-policy/automatic-safety': (c) => client(c).getAutomaticSafety(),
  'POST /api/v1/metadata-policy/activate': policy,
  'POST /api/v1/metadata-policy/automatic-safety/suspend': policy,
  'POST /api/v1/metadata-policy/drafts': policy,
  'POST /api/v1/metadata-policy/preview': policy,
  'POST /api/v1/metadata-policy/shadow/confirm-automatic': policy,
  'POST /api/v1/metadata-policy/validate': policy,
}
const map = async (c: Case) => {
  const key = matchOperation(c.method, c.path)
  const runner = key === undefined ? undefined : RUNNERS[key]
  if (runner === undefined) throw new Error(`no runner for ${c.id}`)
  try {
    await runner(c)
    return 'accept'
  } catch (error) {
    if (error instanceof ApiError) return `reject:${error.code}`
    // Only a plain Error is a mapper's own refusal; a crash (TypeError and the like) must fail the case.
    if (error instanceof Error && error.constructor === Error) return `reject:${error.message}`
    throw error
  }
}

// ---- Agreement rule ----------------------------------------------------------

type Observed = 'agree' | 'misplaced' | 'unclassified' | Divergence
interface Observation {
  readonly decoder: string
  readonly mapper: string
  readonly observed: Observed
}
// A segment-boundary prefix: `/a/1` is not under `/a/10`.
const under = (at: string, pointer: string) => at === pointer || at.startsWith(`${pointer}/`)
const classify = (c: Case, decoder: string, mapper: string): Observed => {
  if (decoder === 'ok') {
    if (mapper === 'accept') return 'agree'
    const row = EXPECTED[c.id]?.class
    return row === 'mapper_cross_field' ? row : 'unclassified'
  }
  if (mapper === 'accept') return 'decoder_stricter'
  const at = decoder.slice(decoder.indexOf('@') + 1)
  return c.mutates !== undefined && under(at, c.mutates) ? 'agree' : 'misplaced'
}
const observations = new Map<string, Observation>()
const seen = (id: string) => {
  const observation = observations.get(id)
  if (observation === undefined) throw new Error(`no observation for ${id}`)
  return observation
}
beforeAll(async () => {
  for (const c of cases) {
    const decoder = decode(c)
    const mapper = await map(c)
    observations.set(c.id, { decoder, mapper, observed: classify(c, decoder, mapper) })
  }
})

// ---- Tests -------------------------------------------------------------------

describe('decoder and mapper differential', () => {
  it.each(cases.map(({ id }) => id))('%s', (id) => {
    const { decoder, mapper, observed } = seen(id)
    const expected = EXPECTED[id]?.class ?? 'agree'
    expect({ id, decoder, mapper, observed }).toEqual({ id, decoder, mapper, observed: expected })
  })
})

describe('expected-divergence table', () => {
  it('has unique case ids', () => {
    expect(caseById.size).toBe(cases.length)
  })
  it('names an existing case in every row', () => {
    expect(Object.keys(EXPECTED).filter((id) => !caseById.has(id))).toEqual([])
  })
  it('has a row for every case that does not agree', () => {
    const unlisted = cases.filter(({ id }) => seen(id).observed !== 'agree' && !(id in EXPECTED))
    expect(unlisted.map(({ id }) => id)).toEqual([])
  })
  it('has no row that the observed class contradicts', () => {
    const stale = Object.entries(EXPECTED).filter(
      ([id, row]) => caseById.has(id) && seen(id).observed !== row.class,
    )
    expect(stale.map(([id]) => id)).toEqual([])
  })
})

// A family is decoded once `request()` decodes the operation of every case it
// owns; its mapper then has no presence check of its own.
const decodedFamilies = new Set(
  cases
    .map(({ family }) => family)
    .filter((family) =>
      cases
        .filter((c) => c.family === family)
        .every((c) => {
          const key = matchOperation(c.method, c.path)
          return key !== undefined && DECODED.has(key)
        }),
    ),
)

describe('presence parity', () => {
  it('has a row for every exactKeys call site of a family not yet decoded', () => {
    const source = readFileSync(resolve(process.cwd(), 'src/api/http.ts'), 'utf8')
    const undecoded = PRESENCE_SITES.filter(([, family]) => !decodedFamilies.has(family))
    expect(source.match(/\bexactKeys\(/g)?.length ?? 0).toBe(undecoded.length)
  })
  it.each(presence)('%s rejects at the deleted key on both sides', (id, results) => {
    expect(results).toContain(seen(id).decoder)
    expect(seen(id).mapper).toMatch(/^reject:/)
  })
  it.each(DRAINED.map(([row, , pointer]) => [row, pointer]))(
    '%s, drained, is refused at its one deleted key on both sides',
    (row, pointer) => {
      expect(seen(`tolerance:${row}`)).toMatchObject({
        decoder: `required@${pointer}`,
        mapper: 'reject:invalid_response',
        observed: 'agree',
      })
    },
  )
  it.each(TOLERANCES.map(([row, , pointer]) => [row, pointer]))(
    '%s is decoder-stricter at its one deleted key',
    (row, pointer) => {
      expect(seen(`tolerance:${row}`)).toMatchObject({
        decoder: `required@${pointer}`,
        mapper: 'accept',
      })
    },
  )
})

describe('pinned cross-field results', () => {
  const pinned = Object.entries(EXPECTED).flatMap(([id, row]) =>
    row.result === undefined ? [] : [[id, row.result] as const],
  )
  it('pins every row of a decoded family, and only those', () => {
    const decodedRows = CROSS_FIELD.filter(([, from]) => decodedFamilies.has(from.family))
    expect(pinned.map(([id]) => id).sort()).toEqual(decodedRows.map(([id]) => id).sort())
  })
  it.each(pinned)('%s fails its own check', (id, result) => {
    expect(seen(id).mapper).toBe(result)
  })
})

describe('bars', () => {
  it('decodes every captured document', () => {
    const rejected = cases.filter((c) => c.provenance === 'server' && seen(c.id).decoder !== 'ok')
    expect(rejected.map(({ id }) => id)).toEqual([])
  })
  // The accept is a capture where the family has one, otherwise a decode-tested
  // base (Q4).
  it('has an accept and an agreeing reject in every family', () => {
    const families: readonly Family[] = [
      'Activity',
      'Attention',
      'CaseDetail',
      'Library',
      'Policy',
      'Preparation',
      'ReviewerAccess',
      'Settings',
    ]
    const missing = families.flatMap((family) => {
      const own = cases.filter((c) => c.family === family && seen(c.id).observed === 'agree')
      const accept = cases.some((c) => c.family === family && c.provenance === 'server')
        ? 'server'
        : 'builder'
      return [
        ...(own.some((c) => c.provenance === accept) ? [] : [`${family} ${accept} accept`]),
        ...(own.some((c) => c.mutates !== undefined && seen(c.id).mapper !== 'accept')
          ? []
          : [`${family} reject`]),
      ]
    })
    expect(missing).toEqual([])
  })
})
