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
  ReviewApiClient,
  mapActivity,
  mapActivityConsent,
  mapActivityPermissionReadiness,
  mapAttention,
  mapCaseDetail,
  mapCoverConsideration,
  mapEvidence,
  mapHistory,
  mapLibrary,
  mapPreparation,
  mapReportDetail,
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
  coverConsideration,
  decisionAcknowledgement,
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
type Divergence = 'decoder_stricter' | 'mapper_cross_field' | 'pending_server'
interface Expected {
  readonly class: Divergence
  readonly check: string
  readonly owner?: string
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
const LIBRARY = get('/api/v1/library?lens=all')
const PREPARATION = get('/api/v1/review-preparation')
const REPORT = get('/api/v1/library/report?item=item-9')
const SETTINGS = get('/api/v1/settings')
builder('Library', 'reportDetail', REPORT, reportDetail)
builder('CaseDetail', 'caseDetail', CASE, caseDetail)
const coverBase = builder(
  'CaseDetail',
  'coverConsideration',
  post(casePath('case-1', '/cover-considerations/retry')),
  coverConsideration,
)
builder('CaseDetail', 'caseHistory', get(casePath('case-1', '/history')), caseHistory)
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

// [site, component, accept document, pointer of the component instance in it]
const PRESENCE_SITES: readonly (readonly [string, string, Case, string])[] = [
  ['ReviewApiClient.startCodexDevice:1517', '_CodexDeviceStartResponse', issued, ''],
  ['reviewerStatus:2020', 'ReviewerEngineStatus', codexCleared, '/codex'],
  ['mapDescriptionMetadata:2067', 'DescriptionModelMetadata', modelTest, ''],
  ['mapCostEvidence:2111', 'ReviewerCostEvidence', codexCleared, '/cost_evidence'],
  ['mapCostEvidence:2123', 'MeasuredRun', codexCleared, '/cost_evidence/models/0/measured_run'],
  ['mapCostEvidence:2130', 'ReviewerCostEstimate', codexCleared, '/cost_evidence/models/0'],
  ['mapReviewerAccess:2179', 'ReviewerAccessView', codexCleared, ''],
  ['mapReviewerAccess:2207', 'OpenRouterRecommendation', codexCleared, '/candidates/0'],
  ['mapReviewerAccess:2233', 'OpenRouterTaskSelection', openRouterRefresh, '/mapping'],
  ['mapReviewerAccess:2256', 'CoverModelMetadata', openRouterRefresh, '/cover_metadata'],
  ['mapReviewerAccess:2279', 'CodexTaskSelection', openRouterCleared, '/description_mapping'],
  [
    'mapReviewerAccess:2281',
    'OpenRouterTaskSelection',
    describedByOpenRouter,
    '/description_mapping',
  ],
  ['mapCodexDevice:2292', '_CodexDeviceAuthResponse', issued, '/device'],
  ['mapSettings:2389', 'DescriptionAdviceSettings', settings, '/document/description_advice'],
  [
    'mapDecisionAcknowledgement:2598',
    '_UnavailableDecisionConfirmation',
    unavailable,
    '/confirmation',
  ],
  ['mapDecisionAcknowledgement:2602', '_ConfirmedDecision', selection, '/confirmation'],
  ['preparationPresentation:2736', '_PreparationNow', preparationNow, '/presentation/now'],
  ['providerThrottling:2767', '_PreparationThrottling', throttled, '/provider_throttling'],
  [
    'providerThrottling:2781',
    '_PreparationThrottleEntry',
    throttled,
    '/provider_throttling/entries/0',
  ],
  ['activityLapses:2977', '_ActivityLapsedConsent', lapsed, '/lapsed_consents/0'],
  ['mapActivityConsent:2993', '_ActivityConsentState', consentState, ''],
  ['mapActivityPermissionReadiness:3017', '_ActivityPermissionReadiness', permissionReadiness, ''],
  [
    'mapRecordingIdentityBasis:3308',
    '_RecordingIdentityBasis',
    recordingBasis,
    '/recording_identity_basis',
  ],
  [
    'mapDescriptionCandidate:3589',
    'DescriptionAdviceCandidate',
    advice,
    '/fields/8/description_advice/candidate',
  ],
  [
    'mapDescriptionCandidate:3615',
    'DescriptionAdviceOutput',
    advice,
    '/fields/8/description_advice/candidate/output',
  ],
  [
    'mapDescriptionBinding:3640',
    'DescriptionAdviceBinding',
    advice,
    '/fields/8/description_advice/binding',
  ],
  [
    'mapDescriptionAdvice:3669',
    '_DescriptionAdviceResponse',
    advice,
    '/fields/8/description_advice',
  ],
  [
    'mapDescriptionAdvice:3718',
    'AuthoredDescriptionDecision',
    preserved,
    '/fields/8/description_advice/selection',
  ],
  [
    'mapDescriptionAdvice:3746',
    '_SelectedDescriptionAdvice',
    preserved,
    '/fields/8/description_advice/selected_advice',
  ],
  ['mapOwnerControl:3932', '_OwnerControl', advice, '/fields/1/owner_control'],
  ['mapOwnerControl:3938', '_OwnerEntry', advice, '/fields/3/owner_control/entry'],
  ['mapOwnerControl:3943', '_OwnerSubset', advice, '/fields/1/owner_control/subset'],
  [
    'mapOwnerControl:3956',
    '_OwnerSuggestion',
    suggested,
    '/fields/3/owner_control/entry/suggestions/0',
  ],
  [
    'mapOwnerControl:3976',
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
const presence = PRESENCE_SITES.flatMap(([site, component, instance, pointer]) =>
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
    'T4',
    libraryWithWork,
    '/items/0/cover',
    'mapLibrary http.ts:3472 reads an absent work cover as none (item.cover == null)',
  ],
  [
    'T5',
    preparationBase,
    '/provider_throttling',
    'providerThrottling http.ts:2764 reads an absent provider_throttling as null',
  ],
  [
    'T6',
    activityItems,
    '/provider_recovery/0/failure_reasons',
    'mapActivity http.ts:3070 reads absent failure_reasons as [] (=== undefined)',
  ],
] as const
for (const [row, instance, pointer] of TOLERANCES) deletion(`tolerance:${row}`, instance, pointer)

// ---- B4 reject rows for the two families with no `exactKeys` site -----------

deletion('reject:attention.items', attentionCapture, '/items')
deletion('reject:policy-state.schema_version', policyState, '/schema_version')

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
  'GET /api/v1/library': (c) => mapLibrary(c.doc()),
  'GET /api/v1/library/report': (c) => mapReportDetail(c.doc()),
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
  'GET /api/v1/review-preparation': (c) => mapPreparation(c.doc()),
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
    if (error instanceof Error) return `reject:${error.message}`
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
    return row === 'mapper_cross_field' || row === 'pending_server' ? row : 'unclassified'
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

describe('presence parity', () => {
  it('has a row for every exactKeys call site', () => {
    const source = readFileSync(resolve(process.cwd(), 'src/api/http.ts'), 'utf8')
    expect(source.match(/\bexactKeys\(/g)?.length).toBe(PRESENCE_SITES.length)
  })
  it.each(presence)('%s rejects at the deleted key on both sides', (id, results) => {
    expect(results).toContain(seen(id).decoder)
    expect(seen(id).mapper).toMatch(/^reject:/)
  })
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

describe('bars', () => {
  it('decodes every captured document', () => {
    const rejected = cases.filter((c) => c.provenance === 'server' && seen(c.id).decoder !== 'ok')
    expect(rejected.map(({ id }) => id)).toEqual([])
  })
  // B3: a pending-server row names the server field that 2b tightens; no other
  // row has an owner, so a class flip without its owner edit fails.
  it('names an owner for exactly the pending-server rows', () => {
    const wrong = Object.entries(EXPECTED).filter(
      ([, row]) => (row.class === 'pending_server') !== Boolean(row.owner),
    )
    expect(wrong.map(([id]) => id)).toEqual([])
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
