import activityV2Predecessor from './activityV2Predecessor.json'
import currentActivity from './descriptionAdviceActivityFixture.json'
import {
  activityEvent,
  activityResponse,
  attention,
  attentionItem,
  caseDetail,
  caseHistory,
  choiceContext,
  fieldChoiceContext,
  coverAutomaticDecision,
  coverCandidate,
  coverConsideration,
  coverIdentity,
  coverPolicyReport,
  decisionAcknowledgement,
  decisionProjection,
  historyCoverItem,
  historyDecisionItem,
  historyPreviewItem,
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
  targetIdentity,
} from './wireFixtures'
import activationCapture from './wire/post-metadata-policy-activate.200.json'
import libraryProgressCapture from './wire/get-library-progress.200.json'
import { describe, expect, expectTypeOf, it, vi } from 'vitest'

import {
  signInLocation,
  ApiError,
  AmbiguousMutationError,
  BuildMismatchError,
  CsrfRenewedError,
  MEDIA_HEALTH_KINDS,
  NetworkUnavailableError,
  PageCsrfTokenStore,
  ReviewApiClient,
  mapCoverConsideration,
  derivePolicyDraftDisplay,
  policyAutomaticQualification,
  SessionExpiredError,
  refusalCopy,
  PRE_CALL_SERVER_CODES,
  uncertainWriteOutcome,
  ResponseDecodeError,
  UncertainMutationError,
  normalizeTasteRule,
  tasteRuleRefusal,
} from '../src/api/http'
import { matchOperation, responseDecoders } from '../src/generated/review-decoders'
import type { OperationKey } from '../src/generated/review-decoders'
import type {
  ActivityCommandPath,
  AutomaticSafetyView,
  CoverCalibrationView,
  OperationOf,
  PolicyCommandAnswer,
  PolicyCommandBody,
  PolicyCommandPath,
  PolicyDraft,
  PolicyStateView,
  PolicyTransitionView,
  PreparationActionPath,
  RequestAnswer,
  RoutablePath,
  SettingsView,
  SettingsWrite,
  PreviewPostureView,
  Unrouted,
} from '../src/api/http'
import type { components } from '../src/generated/review-api'
import type { components as responseComponents } from '../src/generated/review-api-response'
import { classifyDecisionFailure } from '../src/reviewDecisionController'
import { view as settingsView } from './settingsFixtures'

const token = 'a'.repeat(32)
const renewedToken = 'b'.repeat(32)

const csrf = {
  current: vi.fn(() => token),
  renew: vi.fn(async () => renewedToken),
}

// The preparation family is decoded centrally (#1685): a document reaches the
// mapper's named checks only through the client, after its generated decoder.
const readPreparation = (document: unknown) =>
  new ReviewApiClient({
    csrf,
    fetch: vi.fn<typeof fetch>(async () => Response.json(document)),
  }).getPreparation()
// The library family is decoded centrally too (#1686).
const readLibrary = (document: unknown) =>
  new ReviewApiClient({
    csrf,
    fetch: vi.fn<typeof fetch>(async () => Response.json(document)),
  }).getLibrary({ lens: 'all' })
const readReport = (document: unknown) =>
  new ReviewApiClient({
    csrf,
    fetch: vi.fn<typeof fetch>(async () => Response.json(document)),
  }).getReportDetail('/api/v1/library/report?item=item-9')
// The attention family is decoded centrally too (#1687).
const readAttention = (document: unknown) =>
  new ReviewApiClient({
    csrf,
    fetch: vi.fn<typeof fetch>(async () => Response.json(document)),
  }).getAttention({})
// The case-detail family is decoded centrally too (#1688).
const readCase = (document: unknown) =>
  new ReviewApiClient({
    csrf,
    fetch: vi.fn<typeof fetch>(async () => Response.json(document)),
  }).getCaseDetail('case-1')
// The settings family is decoded centrally too (#1689).
const readSettings = (document: unknown) =>
  new ReviewApiClient({
    csrf,
    fetch: vi.fn<typeof fetch>(async () => Response.json(document)),
  }).getSettings()
// The reviewer-access family is decoded centrally too (#1690).
const readReviewerAccess = (document: unknown) =>
  new ReviewApiClient({
    csrf,
    fetch: vi.fn<typeof fetch>(async () => Response.json(document)),
  }).getReviewerAccess()
const readCodexDevice = (document: unknown) =>
  new ReviewApiClient({
    csrf,
    fetch: vi.fn<typeof fetch>(async () => Response.json(document)),
  }).getCodexDevice()
// The activity family is decoded centrally too (#1691).
const answeringActivity = (document: unknown) =>
  new ReviewApiClient({
    csrf,
    fetch: vi.fn<typeof fetch>(async () => Response.json(document)),
  })
const readActivity = (document: unknown) => answeringActivity(document).getActivity()
const readActivityConsent = (document: unknown) => answeringActivity(document).getActivityConsent()
const readPermissionReadiness = (document: unknown) =>
  answeringActivity(document).getActivityPermissionReadiness()
const readEvidence = (document: unknown) =>
  new ReviewApiClient({
    csrf,
    fetch: vi.fn<typeof fetch>(async () => Response.json(document)),
  }).getEvidence('case-1')
const readHistory = (document: unknown) =>
  new ReviewApiClient({
    csrf,
    fetch: vi.fn<typeof fetch>(async () => Response.json(document)),
  }).getHistory('case-1')
// The cover retry is an unsafe write: its unreadable answer is uncertain.
const retryCover = (document: unknown) =>
  new ReviewApiClient({
    csrf,
    buildIdentity: 'c'.repeat(64),
    fetch: vi.fn<typeof fetch>(async () => Response.json(document)),
  }).retryCoverConsideration('case-1')
// The decoder's `reason@at` for a refused read, with the route-safe message.
const refused = (failure: string) => {
  const [reason, at] = failure.split('@')
  return {
    name: 'ResponseDecodeError',
    status: 502,
    code: 'invalid_response',
    message: 'Review API request failed (502: invalid_response)',
    reason,
    at,
  }
}

// An unsafe answer's `reason@at`: the decoder's, or `invariant@<code>` for a
// named check, with the ambiguous-write message.
const uncertain = (failure: string) => {
  const [reason, at] = failure.split('@')
  return {
    name: 'UncertainMutationError',
    status: 0,
    code: 'mutation_outcome_unknown',
    message: 'Review API request failed (0: mutation_outcome_unknown)',
    reason,
    at,
  }
}

const reviewerAccessPayload = (overrides: Record<string, unknown> = {}) => ({
  schema_version: 'reviewer-access-view-v6',
  taste_mapping: null,
  taste_metadata: null,
  taste_daily_requests: 8,
  taste_recommendations: ['anthropic/claude-sonnet-5.5'],
  judge_mapping: null,
  judge_metadata: null,
  judge_daily_requests: 5_000,
  judge_recommendations: ['openai/gpt-6-luna'],
  cover_metadata: null,
  cost_evidence: { models: [] },
  revision: 3,
  mapping: null,
  description_mapping: null,
  description_metadata: null,
  description: null,
  openrouter_key_configured: false,
  codex: {
    engine: 'codex',
    state: 'ready',
    reason: null,
    model: 'gpt-5.6-sol',
    tier: null,
    host_headroom_bytes: 2_147_483_648,
  },
  openrouter: {
    engine: 'openrouter',
    state: 'unconfigured',
    reason: null,
    model: null,
    tier: null,
    host_headroom_bytes: null,
  },
  recommendations: [],
  candidates: [],
  free_tier_caveat: 'Current limits may change.',
  data_use_caveat: 'Review current provider policies.',
  ...overrides,
})

// A reviewer-access read that fails one of the named checks the mapper keeps
// (ADR-0091) is the mapper's own 502; a structural one is the decoder's.
const unnamed = (code: string) => ({ status: 502, code })

describe('reviewer access transport vocabulary', () => {
  it.each(['profile_not_provisioned', 'profile_unreadable', 'profile_contract_mismatch'])(
    'admits provisioning reason %s only in its exact state',
    async (reason) => {
      const codex = {
        ...reviewerAccessPayload().codex,
        state: 'not_provisioned',
        reason,
        model: null,
        host_headroom_bytes: null,
      }
      expect((await readReviewerAccess(reviewerAccessPayload({ codex }))).codex.reason).toBe(reason)
      for (const invalid of [
        { ...codex, state: 'ready' },
        { ...codex, model: 'gpt-5.6-sol' },
        { ...codex, reason: 'host_evidence_unavailable' },
      ]) {
        await expect(
          readReviewerAccess(reviewerAccessPayload({ codex: invalid })),
        ).rejects.toMatchObject(unnamed('invalid_reviewer_access_response'))
      }
    },
  )
  it('rejects the prior view and malformed contained model grammar', async () => {
    await expect(
      readReviewerAccess(reviewerAccessPayload({ schema_version: 'reviewer-access-view-v1' })),
    ).rejects.toMatchObject(refused('const@/schema_version'))
    for (const model of [null, '', 'model\nsecret', 'provider/model', 'x'.repeat(129)]) {
      await expect(
        readReviewerAccess(
          reviewerAccessPayload({ codex: { ...reviewerAccessPayload().codex, model } }),
        ),
      ).rejects.toMatchObject(unnamed('invalid_reviewer_access_response'))
    }
  })

  it.each(['openai/gpt-6-luna'])('preserves the exact cover recommendation %s', async (model) => {
    expect(
      (await readReviewerAccess(reviewerAccessPayload({ recommendations: [model] })))
        .recommendations,
    ).toEqual([model])
  })

  it.each([
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
  ] as const)('preserves the closed %s refusal', async (state) => {
    const mapped = await readReviewerAccess(
      reviewerAccessPayload({
        openrouter: {
          engine: 'openrouter',
          state,
          reason: state,
          model: 'provider/model',
          tier: null,
          host_headroom_bytes: null,
        },
      }),
    )
    expect(mapped.openrouter).toMatchObject({ state, reason: state })
  })

  it('rejects a cross-engine reason with the named slot check', async () => {
    await expect(
      readReviewerAccess(
        reviewerAccessPayload({
          codex: {
            engine: 'codex',
            state: 'failed',
            reason: 'invalid_key',
            model: 'gpt-5.6-sol',
            tier: null,
            host_headroom_bytes: 1,
          },
        }),
      ),
    ).rejects.toMatchObject(unnamed('invalid_reviewer_access_response'))
  })

  // Each answer is schema-valid and fails one named slot check only
  // (ADR-0091 X40-X42 and the description slot) (#1690).
  const codexReady = reviewerAccessPayload().codex
  const openRouterIdle = reviewerAccessPayload().openrouter
  it.each([
    [
      'X40: a codex slot carrying an openrouter status',
      { codex: { ...codexReady, engine: 'openrouter' } },
      'invalid_reviewer_access_response',
    ],
    [
      'X41: a codex slot in an openrouter-only state',
      { codex: { ...codexReady, state: 'unconfigured' } },
      'invalid_reviewer_access_response',
    ],
    [
      'X42: a codex slot with a tier',
      { codex: { ...codexReady, tier: 'free' } },
      'invalid_reviewer_access_response',
    ],
    [
      'X42: an openrouter slot with host headroom',
      { openrouter: { ...openRouterIdle, host_headroom_bytes: 1 } },
      'invalid_reviewer_access_response',
    ],
    [
      'the description slot: a codex status in an openrouter-only state',
      { description: { ...codexReady, state: 'unconfigured' } },
      'invalid_description_reviewer',
    ],
  ])('refuses %s with its named check', async (_label, overrides, code) => {
    await expect(readReviewerAccess(reviewerAccessPayload(overrides))).rejects.toMatchObject(
      unnamed(code),
    )
  })

  it('reports a committed answer whose description slot fails its check as uncertain', async () => {
    const refusal = await new ReviewApiClient({
      csrf,
      buildIdentity: 'c'.repeat(64),
      fetch: vi.fn<typeof fetch>(async () =>
        Response.json(
          reviewerAccessPayload({ description: { ...codexReady, state: 'unconfigured' } }),
        ),
      ),
    })
      .selectReviewer(null, 3, undefined, 'description_rewrite')
      .catch((error: unknown) => error)
    expect(refusal).toMatchObject({
      ...uncertain('invariant@invalid_description_reviewer'),
      operation: 'PUT /api/v1/reviewer-access/selection',
    })
  })

  const candidate = {
    model: 'provider/model',
    tier: 'free',
    qualified: false,
    evidence: 'catalogue_capability_only',
    requests_per_minute: null,
    requests_per_day: null,
  }
  // The checks the reviewer-access mapper made by hand, each now the
  // decoder's (S83-S87, the `mapReviewerAccess` E list and its presence sites
  // at d53effa) (#1690).
  it.each([
    [
      'hostile model',
      { mapping: { engine: 'openrouter', model: 'provider/model\nBearer secret' } },
      'pattern@/mapping/model',
    ],
    [
      'hostile recommendation',
      { recommendations: ['provider/model\nBearer secret'] },
      'pattern@/recommendations/0',
    ],
    [
      'extra mapping field',
      { mapping: { engine: 'codex', model: 'invented' } },
      'unknown_key@/mapping/model',
    ],
    [
      'non-integer headroom',
      {
        codex: { ...reviewerAccessPayload().codex, host_headroom_bytes: '1' },
      },
      'type@/codex/host_headroom_bytes',
    ],
    [
      'negative headroom',
      {
        codex: { ...reviewerAccessPayload().codex, host_headroom_bytes: -1 },
      },
      'min@/codex/host_headroom_bytes',
    ],
    [
      'unknown openrouter tier',
      {
        openrouter: {
          engine: 'openrouter',
          state: 'usable',
          reason: null,
          model: 'provider/model',
          tier: 'trial',
          host_headroom_bytes: null,
        },
      },
      'enum@/openrouter/tier',
    ],
    [
      'array candidate model',
      { candidates: [{ ...candidate, model: ['provider/model'] }] },
      'type@/candidates/0/model',
    ],
    [
      'hostile candidate model',
      { candidates: [{ ...candidate, model: 'provider/model\nBearer secret' }] },
      'pattern@/candidates/0/model',
    ],
    [
      'array candidate tier',
      { candidates: [{ ...candidate, tier: ['free'] }] },
      'type@/candidates/0/tier',
    ],
    [
      'zero candidate rate',
      { candidates: [{ ...candidate, requests_per_minute: 0 }] },
      'min@/candidates/0/requests_per_minute',
    ],
    [
      'qualified candidate',
      { candidates: [{ ...candidate, tier: 'paid', qualified: true }] },
      'const@/candidates/0/qualified',
    ],
    ['extra top-level field', { invented: true }, 'unknown_key@/invented'],
  ])('rejects %s at the decoder', async (_label, overrides, failure) => {
    await expect(readReviewerAccess(reviewerAccessPayload(overrides))).rejects.toMatchObject(
      refused(failure),
    )
  })

  it.each([
    'permission_or_profile',
    'permission_or_process',
    'protocol_or_process',
    'restart_interrupted',
  ] as const)('preserves the closed device failure %s', async (failure) => {
    expect(
      (
        await readCodexDevice({
          state: failure === 'restart_interrupted' ? 'interrupted' : 'failed',
          verification_url: null,
          user_code: null,
          expires_in_seconds: null,
          failure,
        })
      ).failure,
    ).toBe(failure)
  })

  it('admits an unavailable description request price and keeps the other prices required', async () => {
    const metadata = {
      model: 'example/text',
      observed_at: 1,
      prompt_price: '0.000001',
      completion_price: '0.000002',
      request_price: null,
      text_input: true,
      text_output: true,
      structured_outputs: true,
    }
    expect(
      (await readReviewerAccess(reviewerAccessPayload({ description_metadata: metadata })))
        .description_metadata?.request_price,
    ).toBeNull()
    const client = new ReviewApiClient({
      csrf,
      fetch: vi.fn<typeof fetch>(async () => Response.json(metadata)),
      buildIdentity: 'c'.repeat(64),
    })
    expect(
      (
        await client.testDescriptionModel({
          expected_revision: 3,
          api_key: '',
          model: 'example/text',
        })
      ).request_price,
    ).toBeNull()
    for (const [invalid, failure] of [
      [{ ...metadata, prompt_price: null }, refused('type@/description_metadata/prompt_price')],
      [
        { ...metadata, request_price: '' },
        refused('min_length@/description_metadata/request_price'),
      ],
      [{ ...metadata, request_price: '-1' }, unnamed('invalid_description_metadata')],
    ] as const) {
      await expect(
        readReviewerAccess(reviewerAccessPayload({ description_metadata: invalid })),
      ).rejects.toMatchObject(failure)
    }
  })

  it('accepts only the fixed issued prompt shape', async () => {
    const issued = {
      state: 'issued',
      verification_url: 'https://auth.openai.com/codex/device',
      user_code: 'ABCD-1234',
      expires_in_seconds: 900,
      failure: null,
    }
    expect((await readCodexDevice(issued)).state).toBe('issued')
    expect((await readCodexDevice({ ...issued, user_code: 'ABCD-EFGHJ' })).user_code).toBe(
      'ABCD-EFGHJ',
    )
    for (const user_code of ['Bearer secret', 'ABC-1234', 'ABCD-123456789']) {
      await expect(readCodexDevice({ ...issued, user_code })).rejects.toMatchObject(
        refused('pattern@/user_code'),
      )
    }
    await expect(readCodexDevice({ ...issued, invented: true })).rejects.toMatchObject(
      refused('unknown_key@/invented'),
    )
    // The prompt belongs to issued and waiting only (X47).
    for (const invalid of [
      { ...issued, user_code: null },
      { ...issued, state: 'ready' },
    ])
      await expect(readCodexDevice(invalid)).rejects.toMatchObject(
        unnamed('invalid_codex_device_response'),
      )
  })
})

describe('ReviewApiClient', () => {
  it('fails closed on cover candidate, policy, incumbent, and recommendation relationship contradictions', async () => {
    const context = (
      alternative_id: string | null,
      selection: 'current' | 'proposed',
      recommendation: 'recommended' | 'no_recommendation' = 'no_recommendation',
    ) =>
      choiceContext({
        alternative_id,
        selection,
        recommendation,
        recommendation_reason:
          recommendation === 'recommended' ? 'Authoritative policy recommendation.' : '',
        rationale: 'Retained cover choice.',
      })
    const observation = (overrides: Record<string, unknown> = {}) => ({
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
      ...overrides,
    })
    // An acceptable, ranked candidate under an abstaining policy.
    const candidate = coverCandidate
    const policy = coverPolicyReport
    const payload = (overrides: Record<string, unknown> = {}) =>
      coverConsideration({
        candidates: [candidate()],
        choice_contexts: [context(null, 'current'), context('candidate-1', 'proposed')],
        policy: policy(),
        ready: true,
        reviewer_access_revision: 7,
        reviewer_engine: 'openrouter',
        reviewer_model: 'provider/model',
        reviewer_status: 'completed',
        ...overrides,
      })

    expect((await retryCover(payload())).choiceContexts).toHaveLength(2)
    const retainedOnly = payload({
      candidates: [
        candidate({
          incumbent: true,
          manual_eligible: false,
          selectable: false,
          deterministic_rank: null,
          state: 'excluded',
        }),
      ],
      policy: policy({
        owner_ready: false,
        actual_target_state: 'confirmed',
        acceptable_candidate_references: [],
      }),
      choice_contexts: [context(null, 'current')],
      incumbent: { state: 'available', reference: 'candidate-1' },
    })
    expect((await retryCover(retainedOnly)).ready).toBe(true)
    await expect(retryCover({ ...retainedOnly, ready: false })).rejects.toThrow(ApiError)
    expect(
      (await retryCover({ ...retainedOnly, provider_fetching: true, ready: false })).ready,
    ).toBe(false)

    expect(
      await retryCover(
        payload({
          candidates: [],
          choice_contexts: [context(null, 'current')],
          policy: null,
          ready: false,
        }),
      ),
    ).not.toBeNull()
    expect(
      (
        await retryCover(
          payload({
            candidates: [candidate({ state: 'recommended' })],
            policy: policy({
              status: 'recommended',
              recommended_candidate_reference: 'candidate-1',
              acceptable_candidate_references: ['candidate-1'],
            }),
            provider_fetching: false,
            ready: true,
            choice_contexts: [
              context(null, 'current'),
              context('candidate-1', 'proposed', 'recommended'),
            ],
          }),
        )
      ).choiceContexts[1]?.recommendation,
    ).toBe('recommended')
    expect(
      (await retryCover(payload({ incumbent: { state: 'unavailable', reference: null } })))
        .incumbent.state,
    ).toBe('unavailable')
    expect(
      (await retryCover(payload({ candidates: [candidate({ observation: observation() })] })))
        .candidates[0]?.observation?.reproduction,
    ).toBe('clean')
    const invalid: readonly [string, Record<string, unknown>][] = [
      ['policy-null-candidates', { policy: null }],
      [
        'nonnull-policy-zero-candidates',
        { candidates: [], choice_contexts: [context(null, 'current')] },
      ],
      ['recommended-null-reference', { policy: policy({ status: 'recommended' }) }],
      ['invalid-candidate-enum', { candidates: [candidate({ state: 'incumbent' })] }],
      [
        'duplicate-reference',
        { candidates: [candidate(), candidate({ artifact_id: 'artifact-2', ordinal: 1 })] },
      ],
      [
        'duplicate-artifact',
        { candidates: [candidate(), candidate({ reference: 'candidate-2', ordinal: 1 })] },
      ],
      [
        'duplicate-ordinal',
        {
          candidates: [
            candidate(),
            candidate({ reference: 'candidate-2', artifact_id: 'artifact-2' }),
          ],
        },
      ],
      ['noncanonical-ordinal', { candidates: [candidate({ ordinal: 1 })] }],
      ['absent-reference', { incumbent: { state: 'absent', reference: 'candidate-1' } }],
      [
        'unavailable-reference',
        {
          candidates: [],
          choice_contexts: [context(null, 'current')],
          incumbent: { state: 'unavailable', reference: 'candidate-1' },
          policy: null,
        },
      ],
      [
        'unavailable-incumbent-candidate',
        {
          incumbent: { state: 'unavailable', reference: null },
          candidates: [candidate({ incumbent: true })],
        },
      ],
      [
        'available-incumbent-missing',
        { incumbent: { state: 'available', reference: 'candidate-1' } },
      ],
      [
        'available-incumbent-mismatch',
        {
          candidates: [candidate({ incumbent: true })],
          incumbent: { state: 'available', reference: 'other' },
          choice_contexts: [context(null, 'current')],
        },
      ],
      ['excluded-selectable', { candidates: [candidate({ state: 'excluded', selectable: true })] }],
      [
        'acceptable-outside-policy',
        {
          candidates: [candidate({ state: 'acceptable' })],
          policy: policy({ acceptable_candidate_references: [] }),
        },
      ],
      [
        'selectable-omitted-from-ranking',
        {
          candidates: [candidate({ state: 'available' })],
          policy: policy({ acceptable_candidate_references: [] }),
          provider_fetching: false,
        },
      ],
      [
        'available-inside-acceptable',
        {
          candidates: [candidate({ state: 'available' })],
          policy: policy({ acceptable_candidate_references: ['candidate-1'] }),
        },
      ],
      [
        'recommended-nonselectable',
        {
          candidates: [candidate({ state: 'recommended', selectable: false })],
          policy: policy({
            status: 'recommended',
            recommended_candidate_reference: 'candidate-1',
            acceptable_candidate_references: ['candidate-1'],
          }),
          choice_contexts: [context(null, 'current')],
        },
      ],
      [
        'recommended-incumbent',
        {
          candidates: [
            candidate({
              incumbent: true,
              selectable: false,
              deterministic_rank: null,
              state: 'excluded',
            }),
          ],
          incumbent: { state: 'available', reference: 'candidate-1' },
          policy: policy({
            status: 'recommended',
            recommended_candidate_reference: 'candidate-1',
            acceptable_candidate_references: ['candidate-1'],
          }),
          ready: true,
          choice_contexts: [context(null, 'current', 'recommended')],
        },
      ],
      [
        'unconfirmed-selectable',
        {
          candidates: [
            candidate({
              identity: coverIdentity({ outcome: 'unknown' }),
              match_state: 'match_not_confirmed',
            }),
          ],
        },
      ],
      [
        'conflict-same-book',
        {
          candidates: [
            candidate({ identity: coverIdentity({ outcome: 'conflict' }), manual_eligible: false }),
          ],
        },
      ],
      [
        'manifestation-wrong-label',
        {
          candidates: [
            candidate({ identity: coverIdentity({ outcome: 'verified_manifestation_match' }) }),
          ],
        },
      ],
      [
        'incumbent-ranked',
        {
          candidates: [candidate({ incumbent: true, selectable: false, state: 'excluded' })],
          incumbent: { state: 'available', reference: 'candidate-1' },
          policy: policy(),
          choice_contexts: [context(null, 'current')],
        },
      ],
      [
        'recommended-ref-nonmember',
        {
          candidates: [candidate({ state: 'recommended' })],
          policy: policy({
            status: 'recommended',
            recommended_candidate_reference: 'candidate-1',
            acceptable_candidate_references: [],
          }),
          choice_contexts: [
            context(null, 'current'),
            context('candidate-1', 'proposed', 'recommended'),
          ],
        },
      ],
      [
        'duplicate-acceptable',
        {
          candidates: [candidate({ state: 'recommended' })],
          policy: policy({
            status: 'recommended',
            recommended_candidate_reference: 'candidate-1',
            acceptable_candidate_references: ['candidate-1', 'candidate-1'],
          }),
          choice_contexts: [
            context(null, 'current'),
            context('candidate-1', 'proposed', 'recommended'),
          ],
        },
      ],
      [
        'acceptable-unknown',
        {
          candidates: [candidate({ state: 'recommended' })],
          policy: policy({
            status: 'recommended',
            recommended_candidate_reference: 'candidate-1',
            acceptable_candidate_references: ['candidate-1', 'ghost'],
          }),
          choice_contexts: [
            context(null, 'current'),
            context('candidate-1', 'proposed', 'recommended'),
          ],
        },
      ],
      [
        'multiple-incumbents',
        {
          candidates: [
            candidate({ incumbent: true }),
            candidate({
              incumbent: true,
              reference: 'candidate-2',
              artifact_id: 'artifact-2',
              ordinal: 1,
            }),
          ],
          incumbent: { state: 'available', reference: 'candidate-1' },
          choice_contexts: [context(null, 'current')],
        },
      ],
      [
        'stray-recommendation-context',
        {
          choice_contexts: [
            context(null, 'current', 'recommended'),
            context('candidate-1', 'proposed'),
          ],
        },
      ],
      [
        'hostile-observation-value',
        {
          candidates: [
            candidate({ observation: observation({ reproduction: '/private/hostile-value' }) }),
          ],
        },
      ],
      [
        'missing-observation-dimension',
        {
          candidates: [
            candidate({
              observation: (() => {
                const value = observation()
                delete (value as Partial<typeof value>).placeholder
                return value
              })(),
            }),
          ],
        },
      ],
      [
        'extra-observation-dimension',
        { candidates: [candidate({ observation: observation({ invented: 'clean' }) })] },
      ],
      [
        'misbound-observation-reference',
        {
          candidates: [
            candidate({ observation: observation({ candidate_reference: 'candidate-2' }) }),
          ],
        },
      ],
      [
        'misbound-observation-preview',
        {
          candidates: [candidate({ observation: observation({ preview_sha256: 'c'.repeat(64) }) })],
        },
      ],
    ]
    for (const [label, overrides] of invalid)
      await expect(retryCover(payload(overrides)), label).rejects.toThrow(ApiError)
    // A cover's sources name closed kinds; one outside them is refused (#1286 C6).
    expect(
      (await retryCover(payload({ candidates: [candidate({ source_kinds: ['open_library'] })] })))
        .candidates[0]?.sourceKinds,
    ).toEqual(['open_library'])
    await expect(
      retryCover(payload({ candidates: [candidate({ source_kinds: ['scraped'] })] })),
    ).rejects.toMatchObject(uncertain('enum@/candidates/0/source_kinds/0'))
    // A matched-book reason is display only (#1642); the named check
    // match-reason below keeps it to a proposed, non-conflicting cover.
    expect(
      (await retryCover(payload({ candidates: [candidate({ match_reason: 'matched_book' })] })))
        .candidates[0]?.matchReason,
    ).toBe('matched_book')
    expect('matchReason' in ((await retryCover(payload())).candidates[0] ?? {})).toBe(false)
    // A title-and-author reason (#1355) maps without a 502, display only too.
    expect(
      (await retryCover(payload({ candidates: [candidate({ match_reason: 'title_and_author' })] })))
        .candidates[0]?.matchReason,
    ).toBe('title_and_author')
    await expect(
      retryCover(payload({ candidates: [candidate({ match_reason: 'same_book' })] })),
    ).rejects.toMatchObject(uncertain('enum@/candidates/0/match_reason'))
  })
  it('maps only the fixed unavailable reviewer-contract reasons', async () => {
    const payload = (reviewer_unavailable_reason: unknown) =>
      coverConsideration({ reviewer_status: 'unavailable', reviewer_unavailable_reason })

    expect((await retryCover(payload(null))).reviewerUnavailableReason).toBeNull()
    expect(
      (await retryCover(payload('profile contract requires memory_bytes=1073741824')))
        .reviewerUnavailableReason,
    ).toBe('profile contract requires memory_bytes=1073741824')
    expect(
      (await retryCover(payload('profile contract requires process_limit=32')))
        .reviewerUnavailableReason,
    ).toBe('profile contract requires process_limit=32')
    await expect(retryCover(payload('/private/profile token=secret'))).rejects.toThrow(ApiError)
  })
  it('maps only a closed reviewer failure code on a failed review', async () => {
    const payload = (reviewer_status: string, reviewer_failure_code?: unknown) => {
      const body: Record<string, unknown> = coverConsideration({
        reviewer_access_revision: 3,
        reviewer_engine: 'openrouter',
        reviewer_model: 'provider/model',
        reviewer_status,
        reviewer_failure_code,
      })
      // The schema requires the code; the absent case removes it.
      if (reviewer_failure_code === undefined) delete body.reviewer_failure_code
      return body
    }

    for (const code of [
      'timed_out',
      'response_too_large',
      'data_policy_refused',
      'image_limit_exceeded',
    ] as const)
      expect((await retryCover(payload('failed', code))).reviewerFailureCode).toBe(code)
    expect(await retryCover(payload('failed', null))).not.toHaveProperty('reviewerFailureCode')
    await expect(retryCover(payload('failed'))).rejects.toMatchObject(
      uncertain('required@/reviewer_failure_code'),
    )
    await expect(retryCover(payload('failed', 'transport'))).rejects.toMatchObject(
      uncertain('enum@/reviewer_failure_code'),
    )
    await expect(retryCover(payload('completed', 'timed_out'))).rejects.toMatchObject(
      uncertain('invariant@invalid_cover_reviewer_failure_code'),
    )
  })
  it('preserves present and null series sequences and refuses an absent one', async () => {
    const detail = (
      series: readonly Record<string, unknown>[],
      choiceContexts: unknown = [fieldChoiceContext()],
    ) =>
      readCase(
        caseDetail({
          fields: [
            reviewField({
              destination: 'series',
              current: targetField({ destination: 'series', value: series }),
              choice_contexts: choiceContexts,
            }),
          ],
        }),
      )

    expect((await detail([{ name: 'Archive', sequence: '2.5' }])).fields[0]?.currentValue).toBe(
      'Archive · 2.5',
    )
    expect((await detail([{ name: 'Standalone', sequence: null }])).fields[0]?.currentValue).toBe(
      'Standalone',
    )
    // The schema requires the key; the mapper used to read its absence as null.
    await expect(detail([{ name: 'Standalone' }])).rejects.toMatchObject(
      refused('required@/fields/0/current/value/0/sequence'),
    )
    await expect(detail([], [])).rejects.toThrow(new ApiError(502, 'misaligned_choice_contexts'))
  })

  it('requires exact creator corroboration source lists in case-detail responses', async () => {
    const response = (corroboration_sources: unknown) =>
      caseDetail({
        fields: [
          reviewField({
            destination: 'authors',
            current: targetField({ destination: 'authors', value: ['Ada Example'] }),
            corroboration_sources,
          }),
        ],
      })

    await expect(readCase(response(undefined))).rejects.toThrow(ApiError)
    await expect(readCase(response('Hardcover'))).rejects.toThrow(ApiError)
    expect(
      (await readCase(response(['Hardcover', 'Open Library']))).fields[0]?.corroborationSources,
    ).toEqual(['Hardcover', 'Open Library'])
  })

  // The transport closes the code families the backend closes, so an owner
  // surface never meets a code it has no words for (#1286, R1).
  const closedDetail = (
    field: Record<string, unknown> = {},
    target: Record<string, unknown> = {},
  ) => caseDetail({ fields: [reviewField(field)], target: targetIdentity(target) })

  it.each([
    ['destination', { destination: 'publication_date' }, {}, 'enum@/fields/0/destination'],
    [
      'disposition',
      { deterministic_disposition: 'review' },
      {},
      'enum@/fields/0/deterministic_disposition',
    ],
    ['write capability', { effect_capability: 'manual' }, {}, 'enum@/fields/0/effect_capability'],
    [
      'current state',
      { current: targetField({ state: 'absent', value: null }) },
      {},
      'enum@/fields/0/current/state',
    ],
    ['media kind', {}, { media_kind: 'ebook' }, 'enum@/target/media_kind'],
  ])('refuses an unknown %s code in a case detail', async (_name, field, target, failure) => {
    expect((await readCase(closedDetail())).fields[0]?.destination).toBe('title')
    await expect(readCase(closedDetail(field, target))).rejects.toMatchObject(refused(failure))
  })

  it.each([
    ['value', 'A Book', 'A Book'],
    ['missing', null, 'Not set in Audiobookshelf'],
    ['null', null, 'Not set in Audiobookshelf'],
    ['empty', '', 'Not set in Audiobookshelf'],
    ['unloaded', null, 'Couldn’t be read from Audiobookshelf'],
    ['malformed', null, 'Couldn’t be read from Audiobookshelf'],
    ['oversized', null, 'Couldn’t be read from Audiobookshelf'],
  ])('reads a %s current value in Audiobookshelf’s terms', async (state, value, shown) => {
    expect(
      (await readCase(closedDetail({ current: targetField({ state, value }) }))).fields[0]
        ?.currentValue,
    ).toBe(shown)
  })

  it('reads whether a field’s accept needs no write (ADR-0105)', async () => {
    expect((await readCase(closedDetail())).fields[0]?.noChangeRequired).toBe(false)
    expect(
      (await readCase(closedDetail({ no_change_required: true }))).fields[0]?.noChangeRequired,
    ).toBe(true)
  })

  it('reads a field’s pin and pin control, and refuses an unknown pin state (#1346)', async () => {
    const pinned = targetField({ state: 'value', value: 'A Pinned Book' })
    const field = (await readCase(closedDetail())).fields[0]
    expect([field?.pin, field?.pinControl]).toEqual([null, null])
    const read = (
      await readCase(
        closedDetail({
          pin: { state: 'changed', value: pinned },
          pin_control: { pin: 'available', unpin: true },
        }),
      )
    ).fields[0]
    expect(read?.pin).toEqual({ state: 'changed', value: pinned })
    expect(read?.pinControl).toEqual({ pin: 'available', unpin: true })
    const waiting = (
      await readCase(closedDetail({ pin_control: { pin: 'change_waiting', unpin: false } }))
    ).fields[0]
    expect(waiting?.pinControl).toEqual({ pin: 'change_waiting', unpin: false })
    // A pin on a chosen value waits until the value is read back (K5).
    const chosen = (
      await readCase(
        closedDetail({
          pin: { state: 'waiting', value: pinned },
          pin_control: { pin: 'available', unpin: true },
        }),
      )
    ).fields[0]
    expect(chosen?.pin).toEqual({ state: 'waiting', value: pinned })
    expect(chosen?.pinControl).toEqual({ pin: 'available', unpin: true })
    await expect(
      readCase(closedDetail({ pin_control: { pin: 'other', unpin: false } })),
    ).rejects.toMatchObject(refused('enum@/fields/0/pin_control/pin'))
    await expect(
      readCase(closedDetail({ pin_control: { pin: true, unpin: false } })),
    ).rejects.toMatchObject(refused('type@/fields/0/pin_control/pin'))
    await expect(
      readCase(closedDetail({ pin: { state: 'other', value: pinned } })),
    ).rejects.toMatchObject(refused('enum@/fields/0/pin/state'))
  })

  it('reads a cover’s current value as the current cover, never its id (#1363)', async () => {
    const cover = (state: string, value: string | null) =>
      readCase(closedDetail({ destination: 'cover', current: targetField({ state, value }) })).then(
        (detail) => detail.fields[0],
      )
    const current = await cover('value', 'cover_' + 'a'.repeat(64))
    expect(current?.currentValue).toBe('Current cover')
    expect(current?.currentRaw).toBe('cover_' + 'a'.repeat(64))
    expect((await cover('missing', null))?.currentValue).toBe('Not set in Audiobookshelf')
  })

  it('uses Unicode code point bounds for readable case identity', async () => {
    const astral = '📚'
    const response = (target: Record<string, unknown>) =>
      caseDetail({ fields: [], target: targetIdentity(target) })

    const exact = astral.repeat(512)
    expect(await readCase(response({ title: exact, authors: [exact] }))).toMatchObject({
      title: exact,
      authors: [exact],
    })
    await expect(readCase(response({ title: astral.repeat(513) }))).rejects.toThrow(ApiError)
    await expect(readCase(response({ authors: [astral.repeat(513)] }))).rejects.toThrow(ApiError)
  })

  it('rejects case-detail fields that omit the objects the view dereferences', async () => {
    const field = reviewField
    const response = (fields: readonly unknown[]) => caseDetail({ fields })

    expect((await readCase(response([field()]))).fields[0]?.currentValue).toBe('A Book')
    expect((await readCase(response([field()]))).fields[0]?.automaticWithheldReason).toBeNull()
    // The earlier value is one more suggestion and one more tagged genre; a
    // restore control is not part of the contract (ADR-0085).
    const ownerControl = {
      entry: { max_characters: 512, suggestions: [{ rule: 'previous', value: 'Old Title' }] },
      subset: {
        max_items: 32,
        options: [{ value: 'Drama', current: false, proposed: false, previous: true }],
      },
      names: null,
      clear: false,
      current: null,
      withdraw: null,
    }
    expect(
      (await readCase(response([field({ owner_control: ownerControl })]))).fields[0]?.ownerControl,
    ).toEqual({
      entry: { maxCharacters: 512, suggestions: [{ rule: 'previous', value: 'Old Title' }] },
      subset: {
        maxItems: 32,
        options: [{ value: 'Drama', current: false, proposed: false, previous: true }],
      },
      names: null,
      clear: false,
      current: null,
      withdraw: null,
    })
    await expect(
      readCase(response([field({ owner_control: { ...ownerControl, revert: null } })])),
    ).rejects.toThrow(ApiError)
    // A recording's optional choice maps; a state the server never states does not.
    const optional = (
      await readCase(
        response([
          field({
            classification: 'owner_choosable',
            classification_cause: 'owner_choosable',
            needs_attention: false,
            navigation_role: 'inspect',
          }),
        ]),
      )
    ).fields[0]
    expect(optional?.classification).toBe('owner_choosable')
    // The cause code maps beside the state; a code the server never sends does not (#1791).
    expect(optional?.classificationCause).toBe('owner_choosable')
    expect((await readCase(response([field()]))).fields[0]?.classificationCause).toBe(
      'review_needed',
    )
    await expect(readCase(response([field({ classification: 'queued' })]))).rejects.toThrow(
      ApiError,
    )
    await expect(readCase(response([field({ classification_cause: 'queued' })]))).rejects.toThrow(
      ApiError,
    )
    // The condition a withheld reason states maps; one the server never sends
    // does not (#1802).
    expect(
      (
        await readCase(
          response([
            field({
              automatic_withheld_reason: 'Reworded title reason.',
              automatic_withheld_condition: 'isbn_title_material_conflict',
            }),
          ]),
        )
      ).fields[0]?.automaticWithheldCondition,
    ).toBe('isbn_title_material_conflict')
    expect((await readCase(response([field()]))).fields[0]?.automaticWithheldCondition).toBeNull()
    await expect(
      readCase(response([field({ automatic_withheld_condition: 'queued' })])),
    ).rejects.toThrow(ApiError)
    expect(
      (await readCase(response([field({ automatic_withheld_reason: null })]))).fields[0]
        ?.automaticWithheldReason,
    ).toBeNull()
    expect(
      (
        await readCase(
          response([field({ automatic_withheld_reason: 'Contributing lookup unavailable.' })]),
        )
      ).fields[0]?.automaticWithheldReason,
    ).toBe('Contributing lookup unavailable.')
    await expect(readCase(response([field({ automatic_withheld_reason: 1 })]))).rejects.toThrow(
      ApiError,
    )
    await expect(
      readCase(response([field({ automatic_withheld_reason: 'x'.repeat(513) })])),
    ).rejects.toThrow(ApiError)
    // The value Automatic chose before Preview (#1575). The server always
    // sends the property, so the decoder refuses a field that omits it.
    const absent: Record<string, unknown> = { ...field() }
    delete absent.earlier_automatic_value
    await expect(readCase(response([absent]))).rejects.toThrow(ApiError)
    expect(
      (await readCase(response([field({ earlier_automatic_value: null })]))).fields[0]
        ?.earlierAutomaticValue,
    ).toBeNull()
    expect(
      (
        await readCase(
          response([field({ earlier_automatic_value: { kind: 'list', value: ['Ada Example'] } })]),
        )
      ).fields[0]?.earlierAutomaticValue,
    ).toEqual({ kind: 'list', value: ['Ada Example'] })
    for (const malformed of [
      'Ada Example',
      { kind: 'list' },
      { kind: 1, value: 'Ada Example' },
      { kind: 'text', value: 'x'.repeat(16_385) },
      { kind: 'list', value: [1] },
      { kind: 'list', value: ['Ada Example'], extra: true },
    ])
      await expect(
        readCase(response([field({ earlier_automatic_value: malformed })])),
      ).rejects.toThrow(ApiError)

    const retainedEvidence = field({
      decision_actions: ['keep_current', 'accept'],
      proposed_values: [{ kind: 'text', value: 'Alternative' }],
      proposed_formatted: [null],
      choice_contexts: [
        fieldChoiceContext({ rationale: 'Current evidence.' }),
        fieldChoiceContext({
          alternative_id: 'eligible',
          rationale: 'Eligible evidence.',
          selection: 'proposed',
        }),
        fieldChoiceContext({
          alternative_id: 'preserves-target',
          rationale: 'Retained non-selectable evidence.',
          selection: 'proposed',
        }),
      ],
    })
    const retainedContexts = (await readCase(response([retainedEvidence]))).fields[0]
      ?.choiceContexts
    expect(retainedContexts).toHaveLength(3)
    expect(retainedContexts?.[1]?.alternativeId).toBe('eligible')
    expect(retainedContexts?.[2]?.alternativeId).toBe('preserves-target')
    // A choice card's language row carries the value it states (#1802).
    const languageRow = {
      language: 'pt-BR',
      observation: 'Language: pt-BR',
      role: 'local_presence',
      source: 'Local library record',
    }
    expect(
      (
        await readCase(
          response([field({ choice_contexts: [fieldChoiceContext({ evidence: [languageRow] })] })]),
        )
      ).fields[0]?.choiceContexts[0]?.evidence,
    ).toEqual([languageRow])
    await expect(
      readCase(
        response([
          field({
            choice_contexts: [
              fieldChoiceContext({ evidence: [{ ...languageRow, language: undefined }] }),
            ],
          }),
        ]),
      ),
    ).rejects.toThrow(ApiError)
    for (const [invalid, failure] of [
      [null, 'type@/fields/0'],
      ['field', 'type@/fields/0'],
      [field({ current: undefined }), 'required@/fields/0/current'],
      [field({ current: 'A Book' }), 'type@/fields/0/current'],
      [field({ proposed_values: undefined }), 'required@/fields/0/proposed_values'],
      [field({ proposed_values: {} }), 'type@/fields/0/proposed_values'],
    ] as const)
      await expect(readCase(response([invalid]))).rejects.toMatchObject(refused(failure))
    for (const actions of [
      ['keep_current'],
      ['accept'],
      ['accept', 'keep_current'],
      ['keep_current', 'accept', 'reject'],
      ['keep_current', 'keep_current'],
      ['reject'],
      ['unknown'],
    ])
      await expect(readCase(response([field({ decision_actions: actions })]))).rejects.toThrow(
        ApiError,
      )
    expect(
      (await readCase(response([field({ decision_actions: ['keep_current', 'accept'] })])))
        .fields[0]?.decisionActions,
    ).toEqual(['keep_current', 'accept'])
  })

  it('maps only bounded readable diagnostic evidence', async () => {
    const payload = {
      schema_version: 'review-case-evidence-v6',
      review_case_id: 'case-1',
      missing_lineage_count: 1,
      next_cursor: 'next',
      items: [
        {
          destinations: ['title'],
          language: null,
          observation: 'Title: A Book',
          role: 'external_corroboration',
          source: 'Hardcover',
        },
      ],
      source_ledger: [
        {
          companion_isbn: false,
          anchored: false,
          cover_only: false,
          provider_requests: 1,
          lane_kind: 'work',
          provider: 'Hardcover',
          reason: 'Returned matching metadata for this work.',
          status: 'candidates',
        },
      ],
    }
    await expect(
      readEvidence({
        ...payload,
        source_ledger: [{ ...payload.source_ledger[0], lane_kind: 'identity' }],
      }),
    ).rejects.toMatchObject(refused('enum@/source_ledger/0/lane_kind'))
    expect(await readEvidence(payload)).toEqual({
      items: [
        {
          destinations: ['title'],
          language: null,
          observation: 'Title: A Book',
          role: 'external_corroboration',
          source: 'Hardcover',
        },
      ],
      missingLineageCount: 1,
      nextCursor: 'next',
      sourceLedger: [
        {
          companionIsbn: false,
          anchored: false,
          coverOnly: false,
          providerRequests: 1,
          laneKind: 'work',
          provider: 'Hardcover',
          reason: 'Returned matching metadata for this work.',
          status: 'candidates',
        },
      ],
    })
    expect(
      (
        await readEvidence({
          ...payload,
          items: [{ ...payload.items[0], role: 'external_observation' }],
        })
      ).items[0]?.role,
    ).toBe('external_observation')
    // A language row carries the value it states (#1802).
    expect(
      (
        await readEvidence({
          ...payload,
          items: [
            {
              ...payload.items[0],
              destinations: ['language'],
              language: 'pt-BR',
              observation: 'Language: pt-BR. Not offered as a choice.',
            },
          ],
        })
      ).items[0]?.language,
    ).toBe('pt-BR')
    for (const invalid of [
      { ...payload, schema_version: 'review-case-evidence-v3' },
      { ...payload, items: [{ fact: '{}', kind: 'raw', reference: 'evidence-1' }] },
      { ...payload, items: [{ ...payload.items[0], destinations: [] }] },
      { ...payload, items: [{ ...payload.items[0], destinations: ['title', 'title'] }] },
      { ...payload, items: [{ ...payload.items[0], role: 'unknown' }] },
      { ...payload, items: [{ ...payload.items[0], observation: 'x'.repeat(1025) }] },
      { ...payload, items: [{ ...payload.items[0], language: 1 }] },
      { ...payload, items: [{ ...payload.items[0], language: 'x'.repeat(1025) }] },
      { ...payload, source_ledger: [{ provider: 'Hardcover' }] },
    ])
      await expect(readEvidence(invalid)).rejects.toThrow(ApiError)
  })

  it('maps history without exposing retained identities and rejects malformed values', async () => {
    const items = [
      historyDecisionItem({
        application_reason: null,
        decision_id: 'decision-secret',
        destination: 'title',
        event_id: 'event-secret',
        occurred_at: 2,
        outcome: 'accept',
        previous_value: { kind: 'text', value: 'Old' },
        selected_value: { kind: 'text', value: 'New' },
      }),
      {
        decision_id: 'automatic-secret',
        destination: 'authors',
        kind: 'automatic_decision_recorded',
        occurred_at: 1,
        origin: 'automatic_policy',
        outcome: 'ignore',
        previous_value: null,
        review_case_id: 'case-1',
        schema_version: 'automatic-decision-history-event-v1',
        selected_value: null,
      },
      {
        checkpoint: 1,
        kind: 'admitted',
        reason: '<script>actionable</script>',
        transition_id: 'transition-secret',
      },
      {
        checkpoint: 2,
        kind: 'superseded',
        reason: 'A newer review replaced this one.',
        transition_id: 'transition-next',
      },
    ]
    const payload = caseHistory({ items })
    expect(await readHistory(payload)).toEqual({
      items: [
        {
          category: 'owner',
          occurredAt: 2,
          applicationReason: null,
          destination: 'title',
          outcome: 'accept',
          previousValue: { kind: 'text', value: 'Old' },
          selectedValue: { kind: 'text', value: 'New' },
        },
        {
          category: 'automatic',
          occurredAt: 1,
          destination: 'authors',
          outcome: 'ignore',
          previousValue: null,
          selectedValue: null,
        },
        {
          category: 'transition',
          occurredAt: null,
          outcome: 'admitted',
          reason: '<script>actionable</script>',
        },
        {
          category: 'transition',
          occurredAt: null,
          outcome: 'superseded',
          reason: 'A newer review replaced this one.',
        },
      ],
    })
    const cover = historyCoverItem({
      consideration_id: 'cover-secret',
      consideration: coverConsideration({
        candidates: [coverCandidate({ reference: 'candidate-secret' })],
        policy: coverPolicyReport({ reasons: ['policy_satisfied'], status: 'recommended' }),
        reviewer_status: 'completed',
      }),
      decision: decisionProjection({ outcome: 'accept' }),
      decision_id: 'decision-secret',
      kind: 'selected',
      occurred_at: 3,
    })
    expect(
      (
        await readHistory({
          ...payload,
          items: [
            {
              ...cover,
              consideration: {
                ...cover.consideration,
                reviewer_model: 'mistralai/ministral-3b-2512',
              },
            },
          ],
        })
      ).items[0],
    ).toMatchObject({ reviewerModel: 'mistralai/ministral-3b-2512' })
    expect(await readHistory({ ...payload, items: [cover] })).toEqual({
      items: [
        {
          category: 'cover',
          occurredAt: 3,
          candidateCount: 1,
          outcome: 'selected',
          ownerOutcome: 'accept',
          policyReasons: ['policy_satisfied'],
          policyStatus: 'recommended',
          reviewerStatus: 'completed',
          supersedesAutomatic: false,
        },
      ],
    })
    expect(
      (
        await readHistory({
          ...payload,
          items: [
            {
              ...cover,
              decision: decisionProjection({
                outcome: 'accept',
                selected_value: { kind: 'cover', value: 'retained-artifact' },
              }),
              consideration: {
                ...cover.consideration,
                candidates: [
                  coverCandidate({
                    artifact_id: 'retained-artifact',
                    match_state: 'match_not_confirmed',
                  }),
                ],
              },
            },
          ],
        })
      ).items[0],
    ).toMatchObject({ category: 'cover', ownerOutcome: 'accept', ownerMatchUnconfirmed: true })
    // A chosen cover from the matched book keeps its unconfirmed match and
    // names its basis; History says so in its own words (#1642).
    expect(
      (
        await readHistory({
          ...payload,
          items: [
            {
              ...cover,
              decision: decisionProjection({
                outcome: 'accept',
                selected_value: { kind: 'cover', value: 'retained-artifact' },
              }),
              consideration: {
                ...cover.consideration,
                candidates: [
                  coverCandidate({
                    artifact_id: 'retained-artifact',
                    match_reason: 'matched_book',
                    match_state: 'match_not_confirmed',
                  }),
                ],
              },
            },
          ],
        })
      ).items[0],
    ).toMatchObject({
      category: 'cover',
      ownerOutcome: 'accept',
      ownerMatchUnconfirmed: true,
      ownerMatchedBook: true,
      matchedBookOffered: true,
    })
    for (const outcome of ['keep_current', 'reject']) {
      const mapped = (
        await readHistory({
          ...payload,
          items: [
            {
              ...cover,
              kind: outcome === 'keep_current' ? 'kept_incumbent' : 'no_action',
              decision: decisionProjection({ outcome, selected_value: null }),
              consideration: {
                ...cover.consideration,
                candidates: [
                  coverCandidate({ incumbent: true, match_state: 'match_not_confirmed' }),
                ],
              },
            },
          ],
        })
      ).items[0]
      expect(mapped).toMatchObject({ category: 'cover', ownerOutcome: outcome })
      if (!mapped) throw new Error('Expected retained cover history item')
      expect('ownerMatchUnconfirmed' in mapped).toBe(outcome === 'keep_current')
    }
    expect(await readHistory({ ...payload, items: [{ ...cover, kind: 'superseded' }] })).toEqual({
      items: [
        {
          category: 'cover',
          occurredAt: 3,
          candidateCount: 1,
          outcome: 'superseded',
          ownerOutcome: 'accept',
          policyReasons: ['policy_satisfied'],
          policyStatus: 'recommended',
          reviewerStatus: 'completed',
          supersedesAutomatic: false,
        },
      ],
    })
    expect(
      await readHistory({
        ...payload,
        items: [
          {
            ...cover,
            decision: coverAutomaticDecision({
              outcome: 'candidate',
              supersedes_decision_id: 'automatic-prior',
            }),
          },
        ],
      }),
    ).toEqual({
      items: [
        {
          automaticOutcome: 'candidate',
          category: 'cover',
          occurredAt: 3,
          candidateCount: 1,
          outcome: 'selected',
          policyReasons: ['policy_satisfied'],
          policyStatus: 'recommended',
          reviewerStatus: 'completed',
          supersedesAutomatic: true,
        },
      ],
    })
    for (const invalid of [
      { ...payload, schema_version: 'review-case-history-v1' },
      { ...payload, items: [{ ...items[0], outcome: 'unknown' }] },
      { ...payload, items: [{ ...items[0], selected_value: { kind: 'text', value: {} } }] },
      {
        ...payload,
        items: [{ ...items[0], selected_value: { kind: 'text', value: 'x'.repeat(16_385) } }],
      },
      {
        ...payload,
        items: [
          {
            checkpoint: 1,
            kind: 'invented',
            reason: 'actionable',
            transition_id: 'transition-secret',
          },
        ],
      },
      {
        ...payload,
        items: [
          {
            ...cover,
            consideration: {
              ...cover.consideration,
              candidates: Array.from({ length: 15 }, () => coverCandidate()),
            },
          },
        ],
      },
      {
        ...payload,
        items: [
          { ...cover, consideration: { ...cover.consideration, reviewer_status: 'invented' } },
        ],
      },
    ])
      await expect(readHistory(invalid)).rejects.toThrow(ApiError)
    // Every closed history code is refused by name outside its set (#1286 C6).
    const [human, automatic, transition] = items
    const preview = historyPreviewItem({ occurred_at: 4, dispositions: { abstention: 1 } })
    expect((await readHistory({ ...payload, items: [preview] })).items[0]).toMatchObject({
      category: 'preview',
      dispositions: [{ count: 1, outcome: 'abstention' }],
    })
    // An outcome outside its item's set matches no branch of the item union, so
    // the decoder names the first key the next branch refuses.
    for (const [item, failure] of [
      [{ ...human, outcome: 'approve' }, 'unknown_key@/items/0/actor'],
      [{ ...automatic, outcome: 'accept' }, 'unknown_key@/items/0/decision_id'],
      [{ ...preview, dispositions: { approve: 1 } }, 'property_name@/items/0/dispositions/approve'],
      [{ ...transition, kind: 'invented' }, 'enum@/items/0/kind'],
    ] as const)
      await expect(readHistory({ ...payload, items: [item] })).rejects.toMatchObject(
        refused(failure),
      )
    // X53: the shared projection admits five outcomes, a cover decision four.
    for (const outcome of ['owner_entered', 'withdraw'])
      await expect(
        readHistory({
          ...payload,
          items: [{ ...cover, decision: decisionProjection({ outcome }) }],
        }),
      ).rejects.toThrow(new ApiError(502, 'invalid_history_item_0_decision_outcome'))
  })

  it('accepts domain-bounded history values by Unicode code points', async () => {
    const astral = '📚'
    const history = (selected_value: unknown) =>
      caseHistory({
        items: [
          historyDecisionItem({
            application_reason: null,
            destination: 'description',
            selected_value,
          }),
        ],
      })

    const description = astral.repeat(16_384)
    expect(
      (await readHistory(history({ kind: 'text', value: description }))).items[0],
    ).toMatchObject({
      category: 'owner',
      applicationReason: null,
      selectedValue: { kind: 'text', value: description },
    })
    await expect(
      readHistory(history({ kind: 'text', value: astral.repeat(16_385) })),
    ).rejects.toThrow(ApiError)
    expect(
      (await readHistory(history({ kind: 'list', value: [astral.repeat(512)] }))).items,
    ).toHaveLength(1)
    await expect(
      readHistory(history({ kind: 'list', value: [astral.repeat(513)] })),
    ).rejects.toThrow(ApiError)
    expect(
      (
        await readHistory(
          history({ kind: 'series', value: [{ name: astral.repeat(512), sequence: null }] }),
        )
      ).items[0],
    ).toMatchObject({
      selectedValue: { kind: 'series', value: [{ name: astral.repeat(512), sequence: null }] },
    })
    expect(
      (
        await readHistory(
          history({ kind: 'series', value: [{ name: 'Archive', sequence: astral.repeat(512) }] }),
        )
      ).items,
    ).toHaveLength(1)
    await expect(
      readHistory(
        history({ kind: 'series', value: [{ name: astral.repeat(513), sequence: null }] }),
      ),
    ).rejects.toThrow(ApiError)
    await expect(
      readHistory(history({ kind: 'series', value: [{ name: 'Archive' }] })),
    ).rejects.toThrow(ApiError)
    await expect(
      readHistory(
        history({ kind: 'series', value: [{ name: 'Archive', sequence: astral.repeat(513) }] }),
      ),
    ).rejects.toThrow(ApiError)

    const cover = (reason: string) =>
      historyCoverItem({
        consideration: coverConsideration({
          policy: coverPolicyReport({ reasons: [reason], status: 'abstained' }),
          reviewer_status: 'completed',
        }),
      })
    expect(
      (await readHistory({ ...history(null), items: [cover(astral.repeat(128))] })).items,
    ).toHaveLength(1)
    await expect(
      readHistory({ ...history(null), items: [cover(astral.repeat(129))] }),
    ).rejects.toThrow(ApiError)
  })

  it('maps exact library DTOs into presentation-safe view models', async () => {
    const fetchMock = vi.fn<typeof fetch>(async () =>
      Response.json(
        library({
          previous_cursor: 'page-1',
          page: 2,
          counts: {
            needs_attention: 1,
            all: 2,
            no_attention_needed: 1,
            not_matched: 0,
            preparation_failed: 0,
            flagged: 0,
            new: 0,
          },
          items: [
            libraryWork({
              review_case_id: 'case-1',
              title: 'A Book',
              authors: ['Ada Example'],
              library: 'Books',
              library_display_name: 'Reading Room',
              media_kind: 'epub',
              item: 'item-1',
              find_offered: true,
              attention_count: 1,
              attention_state: 'needs_attention',
              detail_url: '/review/case-1',
              detail_kind: 'case',
              dispositions: ['unresolved_choice'],
              work_id: 'library_work_1',
              preparation: null,
              preparation_outcomes: [],
              summary: null,
            }),
            libraryWork({
              review_case_id: null,
              title: 'Catalogue-only work',
              authors: ['Grace Example'],
              library: 'Books',
              media_kind: 'epub',
              item: 'internal-item-2',
              link: { state: 'waiting', source: 'search', provider: 'open_library' },
              attention_count: 0,
              attention_state: 'no_attention_needed',
              detail_url: null,
              detail_kind: null,
              dispositions: [],
              work_id: 'library_work_2',
              preparation: null,
              preparation_outcomes: [
                {
                  companion_isbn: false,
                  anchored: false,
                  cover_only: false,
                  provider_requests: 2,
                  lane_kind: 'isbn',
                  provider: 'google-books',
                  reason: 'Returned no matching metadata.',
                  status: 'no_match',
                },
              ],
              summary: {
                state: 'no_decision_recorded',
                reason: 'No review decision is recorded for this work.',
                fields: [
                  {
                    destination: 'title',
                    state: 'value',
                    value: 'Catalogue-only work',
                    formatted: null,
                  },
                  { destination: 'subtitle', state: 'missing', value: null, formatted: null },
                ],
              },
            }),
          ],
        }),
      ),
    )
    const client = new ReviewApiClient({ csrf, fetch: fetchMock, buildIdentity: 'c'.repeat(64) })

    const controller = new AbortController()
    const view = await client.getLibrary({ lens: 'all' }, controller.signal)

    expect(view.page).toBe(2)
    expect(view.previousCursor).toBe('page-1')
    expect(view.works[0]).toEqual({
      id: 'case-1',
      reviewCaseId: 'case-1',
      workId: 'library_work_1',
      summary: null,
      title: 'A Book',
      authors: ['Ada Example'],
      library: 'Books',
      libraryDisplayName: 'Reading Room',
      mediaKind: 'epub',
      attentionCount: 1,
      pinnedCount: 0,
      attention: 'needs_attention',
      detailUrl: '/review/case-1',
      detailKind: 'case',
      preparation: null,
      preparationOutcomes: [],
      valueFlags: [],
      findOffered: true,
      link: null,
    })
    expect(view.works[1]).toEqual({
      id: 'library_work_2',
      workId: 'library_work_2',
      title: 'Catalogue-only work',
      authors: ['Grace Example'],
      library: 'Books',
      libraryDisplayName: 'Books',
      mediaKind: 'epub',
      attentionCount: 0,
      pinnedCount: 0,
      attention: 'no_attention_needed',
      detailUrl: null,
      detailKind: null,
      preparation: null,
      preparationOutcomes: [
        {
          companionIsbn: false,
          anchored: false,
          coverOnly: false,
          providerRequests: 2,
          laneKind: 'isbn',
          provider: 'google-books',
          reason: 'Returned no matching metadata.',
          status: 'no_match',
        },
      ],
      // A work with no review case still travels with its current values, so
      // Library can open a summary for it without a case identity.
      summary: {
        state: 'no_decision_recorded',
        reason: 'No review decision is recorded for this work.',
        fields: [
          {
            destination: 'title',
            state: 'value',
            value: 'Catalogue-only work',
            formatted: null,
          },
          {
            destination: 'subtitle',
            state: 'missing',
            value: 'Not set in Audiobookshelf',
            formatted: null,
          },
        ],
      },
      valueFlags: [],
      findOffered: false,
      link: { state: 'waiting', source: 'search', provider: 'open_library' },
    })
    expect(JSON.stringify(view)).not.toContain('internal-item-2')
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/library?lens=all',
      expect.objectContaining({
        cache: 'no-store',
        credentials: 'same-origin',
        signal: controller.signal,
      }),
    )
  })

  it('maps field-level attention context without promoting internal target keys', async () => {
    const view = await readAttention(
      attention({
        total: 1,
        items: [
          attentionItem({
            review_case_id: 'case-1',
            destination: 'publishedDate',
            title: 'A Book',
            authors: ['Ada Example'],
            library: 'Books',
            library_display_name: null,
            media_kind: 'epub',
            item: 'internal-item-1',
            detail_url: '/review/case-1?field=publication_date',
            classification_reason: 'The available dates disagree.',
            notices: ['The available dates disagree.'],
            provenance: ['Selected EPUB supports a complete date.'],
            work_attention_count: 2,
          }),
        ],
      }),
    )

    expect(view.items[0]).toEqual({
      id: 'case-1',
      destination: 'publishedDate',
      title: 'A Book',
      authors: ['Ada Example'],
      library: 'Books',
      libraryDisplayName: 'Books',
      mediaKind: 'epub',
      classificationReason: 'The available dates disagree.',
      notices: ['The available dates disagree.'],
      provenance: ['Selected EPUB supports a complete date.'],
      workAttentionCount: 2,
    })
    expect(JSON.stringify(view)).not.toContain('internal-item-1')
  })

  // The checks `mapAttention` held before the decoder (S30-S34, and the type
  // and presence checks) now refuse the read with the decoder's pointer (#1687).
  it.each([
    [attention({ schema_version: 'review-attention-v2' }), 'const@/schema_version'],
    [attention({ total: -1 }), 'min@/total'],
    [attention({ query: 7 }), 'type@/query'],
    [attention({ next_cursor: undefined }), 'required@/next_cursor'],
    [attention({ items: {} }), 'type@/items'],
    [attention({ items: [attentionItem({ destination: 'blurb' })] }), 'enum@/items/0/destination'],
    [attention({ items: [attentionItem({ media_kind: 'pdf' })] }), 'enum@/items/0/media_kind'],
    [
      attention({ items: [attentionItem({ work_attention_count: -1 })] }),
      'min@/items/0/work_attention_count',
    ],
    [attention({ items: [attentionItem({ authors: [1] })] }), 'type@/items/0/authors/0'],
    [
      attention({ items: [attentionItem({ library_display_name: undefined })] }),
      'required@/items/0/library_display_name',
    ],
  ])('refuses an attention answer the schema does not allow (%#) (#1687)', async (doc, failure) => {
    await expect(readAttention(doc)).rejects.toMatchObject(refused(failure))
  })

  it('rejects a malformed activity display projection before it reaches the workspace', async () => {
    await expect(
      readActivity(activityResponse({ schema_version: 'continuous-field-activity-v1' })),
    ).rejects.toMatchObject(refused('const@/schema_version'))
  })

  it('maps bounded activity labels and grouped state into the display seam', async () => {
    // The negatives below mutate these wire parts, never the mapped view.
    const capability = {
      connection: 'primary',
      library: 'Fiction',
      state: 'allowed',
      destinations: [{ destination: 'title', state: 'allowed', consented: true, prepared: 4 }],
    }
    const recovery = {
      state: 'waiting',
      affected_count: 2,
      next_eligible_at: 2,
      dispatch_reasons: ['missing_corroboration', 'blanket_scan'],
      failure_reasons: [],
    }
    const event = activityEvent({
      destination: 'title',
      event_id: 'event-1',
      event_type: 'review',
      category: 'owner',
      change: 'Not set → Earthsea Cycle',
      change_detail: 'Set Series from Not set to Earthsea Cycle',
      field_label: 'Series',
      policy_changes: [
        { field: 'Title', setting: 'Setting', previous: 'Ask me', next: 'Automatic' },
      ],
      evidence: [{ label: 'Previous value', value: 'Not set' }],
      library: 'Fiction',
      occurred_at: 1,
      origin: 'human',
      state: 'verified',
      work_id: 'work-1',
      work_label: 'Recorded work',
    })
    const suspension = {
      connection: 'primary',
      destination: null,
      reason: 'connection_unavailable',
      scope: 'library',
      updated_at: 1,
    }
    const payload = activityResponse({
      owner_action_required: 2,
      configured_libraries: [
        {
          connection: 'primary',
          library: 'Fiction',
          prepared: { automatic: 1, human: 2, total: 3 },
        },
      ],
      counts: { verified: 1 },
      capabilities: [capability],
      waiting: {
        applying: 1,
        awaiting_permission: 2,
        held_for_recovery: 3,
        retrying: 4,
        scheduled: 5,
      },
      provider_recovery: [recovery],
      events: [event],
      ledger: {
        event_type: 'all',
        page: 1,
        page_count: 1,
        page_size: 25,
        query: null,
        range_days: 30,
        total: 1,
      },
      prepared: { automatic: 1, human: 2, total: 3 },
      suspensions: [suspension],
    })
    const activity = await readActivity(payload)

    expect(activity.configured_libraries[0]?.library).toBe('Fiction')
    expect(activity.capabilities[0]?.destinations[0]).toMatchObject({
      destination: 'title',
      consented: true,
      prepared: 4,
    })
    expect(activity.waiting).toEqual({
      applying: 1,
      awaiting_permission: 2,
      held_for_recovery: 3,
      retrying: 4,
      scheduled: 5,
    })
    expect(activity.provider_recovery[0]?.dispatch_reasons).toEqual([
      'missing_corroboration',
      'blanket_scan',
    ])
    expect(activity.provider_recovery[0]?.failure_reasons).toEqual([])
    const reasons = ['burst_throttled', 'quota_exhausted', 'transient']
    expect(
      (
        await readActivity({
          ...payload,
          provider_recovery: [{ ...recovery, failure_reasons: reasons }],
        })
      ).provider_recovery[0]?.failure_reasons,
    ).toEqual(reasons)
    const failureReasons = '/provider_recovery/0/failure_reasons'
    for (const [failure_reasons, failure] of [
      [['private'], `enum@${failureReasons}/0`],
      [['transient', 'transient'], `unique@${failureReasons}`],
      [[1], `type@${failureReasons}/0`],
      ['transient', `type@${failureReasons}`],
      [Array(8).fill('transient'), `max_items@${failureReasons}`],
      // An absent list is refused too: the decoder admits no default (T6).
      [undefined, `required@${failureReasons}`],
    ] as const)
      await expect(
        readActivity({ ...payload, provider_recovery: [{ ...recovery, failure_reasons }] }),
      ).rejects.toMatchObject(refused(failure))
    // The order is the one check the schema cannot state (ADR-0091).
    await expect(
      readActivity({
        ...payload,
        provider_recovery: [{ ...recovery, failure_reasons: ['transient', 'authentication'] }],
      }),
    ).rejects.toMatchObject(unnamed('invalid_provider_recovery_failure_reasons'))
    const dispatchReasons = '/provider_recovery/0/dispatch_reasons'
    for (const [dispatch_reasons, failure] of [
      [['private-detail'], `enum@${dispatchReasons}/0`],
      [['blanket_scan', 'blanket_scan'], `unique@${dispatchReasons}`],
      ['blanket_scan', `type@${dispatchReasons}`],
    ] as const)
      await expect(
        readActivity({ ...payload, provider_recovery: [{ ...recovery, dispatch_reasons }] }),
      ).rejects.toMatchObject(refused(failure))
    await expect(readActivity({ ...payload, waiting: { applying: 1 } })).rejects.toMatchObject(
      refused('required@/waiting/awaiting_permission'),
    )
    expect(activity.owner_action_required).toBe(2)
    for (const [owner_action_required, failure] of [
      [undefined, 'required@/owner_action_required'],
      [-1, 'min@/owner_action_required'],
      [1.5, 'type@/owner_action_required'],
      ['1', 'type@/owner_action_required'],
    ] as const)
      await expect(readActivity({ ...payload, owner_action_required })).rejects.toMatchObject(
        refused(failure),
      )
    expect(activity.events[0]).toMatchObject({
      library: 'Fiction',
      state: 'verified',
      origin: 'human',
    })
    expect(activity.events[0]).toMatchObject({
      work_label: 'Recorded work',
      category: 'owner',
      field_label: 'Series',
      change_detail: 'Set Series from Not set to Earthsea Cycle',
    })
    expect(activity.events[0]?.policy_changes[0]).toEqual({
      field: 'Title',
      setting: 'Setting',
      previous: 'Ask me',
      next: 'Automatic',
    })
    const degraded = await readActivity({
      ...payload,
      events: [
        {
          ...event,
          diagnostic: {
            event_class: 'automatic_decision',
            reason: 'automatic_authority_unavailable',
          },
        },
      ],
    })
    expect(degraded.events[0]?.diagnostic).toEqual({
      event_class: 'automatic_decision',
      reason: 'automatic_authority_unavailable',
    })
    await expect(
      readActivity({
        ...payload,
        events: [
          {
            ...event,
            diagnostic: { event_class: 'automatic_decision', reason: 'private-detail' },
          },
        ],
      }),
    ).rejects.toMatchObject(refused('const@/events/0/diagnostic/reason'))
    await expect(
      readActivity({ ...payload, events: [{ ...event, category: 'review' }] }),
    ).rejects.toMatchObject(refused('enum@/events/0/category'))
    expect(activity.suspensions[0]).toMatchObject({ scope: 'library', destination: null })
    // A hold reason outside the closed set has no owner words, so it is refused.
    await expect(
      readActivity({
        ...payload,
        suspensions: [{ ...suspension, reason: 'recovery' }],
      }),
    ).rejects.toMatchObject(refused('enum@/suspensions/0/reason'))
    // A library's write state and each destination's state are closed sets
    // too; a code outside either has no owner words (#1286 C6).
    await expect(
      readActivity({ ...payload, capabilities: [{ ...capability, state: 'partial' }] }),
    ).rejects.toMatchObject(refused('enum@/capabilities/0/state'))
    await expect(
      readActivity({
        ...payload,
        capabilities: [
          {
            ...capability,
            destinations: [{ ...capability.destinations[0], state: 'writable' }],
          },
        ],
      }),
    ).rejects.toMatchObject(refused('enum@/capabilities/0/destinations/0/state'))
    await expect(
      readActivity({
        ...payload,
        suspensions: [
          {
            connection: 'primary',
            destination: null,
            reason: 'connection_unavailable',
            scope: 'library',
            updated_at: 'later',
          },
        ],
      }),
    ).rejects.toMatchObject(refused('type@/suspensions/0/updated_at'))
    await expect(readActivity({ ...payload, example: { after: 'After' } })).rejects.toMatchObject(
      refused('type@/example'),
    )
  })

  it('offers Automatic only for an audiobook tranche a record qualifies', () => {
    // While no record is published the page must read exactly the rich
    // records, so the ASIN the ebook envelope leaves unqualified stays
    // unqualified. Publishing one makes the same category routable without a
    // further change here (#967).
    const rich = [
      {
        automatic_available: false,
        operation_scope: 'none',
        candidate_sha256: 'a'.repeat(64),
        disposition: 'insufficient_coverage',
        field: 'asin',
        option: 'preserve_exact',
      },
      {
        automatic_available: true,
        operation_scope: 'fill',
        candidate_sha256: 'a'.repeat(64),
        disposition: 'passing_automatic',
        field: 'series',
        option: 'primary_narrative',
      },
    ]
    const state = {
      schema_version: 'metadata-policy-notebook-v2',
      policy_id: 'policy-1',
      profile: {
        name: 'Active',
        options: {},
        destinations: {},
      },
      rich_qualification: rich,
      audiobook_qualification: [
        {
          automatic_available: false,
          destination: 'asin',
          field: 'asin',
          identity: 'asin:preserve_exact:audiobook_asin_fill',
          operation: 'fill',
          option: 'preserve_exact',
          record_sha256: null,
        },
        {
          automatic_available: false,
          destination: 'narrators',
          field: 'contributor_names',
          identity: 'contributor_names:canonical:audiobook_narrators_fill',
          operation: 'fill',
          option: 'canonical',
          record_sha256: null,
        },
      ],
    } as never
    expect(policyAutomaticQualification(state)).toEqual([
      ...(state as unknown as PolicyStateView).audiobook_qualification,
      ...rich,
    ])
    const qualified = {
      ...(state as unknown as PolicyStateView),
      audiobook_qualification: [
        {
          automatic_available: true,
          destination: 'asin',
          field: 'asin',
          identity: 'asin:preserve_exact:audiobook_asin_fill',
          operation: 'fill',
          option: 'preserve_exact',
          record_sha256: 'b'.repeat(64),
        },
        {
          automatic_available: false,
          destination: 'narrators',
          field: 'contributor_names',
          identity: 'contributor_names:canonical:audiobook_narrators_fill',
          operation: 'fill',
          option: 'canonical',
          record_sha256: null,
        },
      ],
    } as never
    const entries = policyAutomaticQualification(qualified)
    expect(entries.filter((entry) => entry.field === 'asin')).toEqual([
      (qualified as unknown as PolicyStateView).audiobook_qualification[0],
      rich[0],
    ])
    expect(entries).toHaveLength(rich.length + 2)
    expect(entries.filter((entry) => entry.field === 'series')).toEqual([rich[1]])
  })

  it('distinguishes matching active policy values from a changed local draft', () => {
    const defaults = { title_layout: { option: 'preserve', destination: 'ask' } }
    const pair = {
      first: { option: 'one', destination: 'ask' },
      second: { option: 'two', destination: 'ignore' },
    }
    const active = {
      schema_version: 'metadata-policy-notebook-v2',
      policy_id: 'policy-1',
      profile: {
        name: 'Active',
        options: { title_layout: 'preserve' },
        destinations: { title_layout: 'ask' },
      },
    } as never
    expect(
      derivePolicyDraftDisplay(
        active,
        {
          name: 'Active',
          options: { title_layout: 'preserve' },
          destinations: { title_layout: 'ask' },
        },
        defaults,
      ),
    ).toMatchObject({ changed: false, activeName: 'Active' })
    const reordered = {
      schema_version: 'metadata-policy-notebook-v2',
      policy_id: 'policy-1',
      profile: {
        name: 'Active',
        options: { first: 'one', second: 'two' },
        destinations: { first: 'ask', second: 'ignore' },
      },
    } as never
    expect(
      derivePolicyDraftDisplay(
        reordered,
        {
          name: 'Active',
          options: { second: 'two', first: 'one' },
          destinations: { second: 'ignore', first: 'ask' },
        },
        pair,
      ),
    ).toMatchObject({ changed: false })
    expect(
      derivePolicyDraftDisplay(
        active,
        {
          name: 'Draft',
          options: { title_layout: 'preserve' },
          destinations: { title_layout: 'ask' },
        },
        defaults,
      ),
    ).toMatchObject({ changed: true, draftName: 'Draft' })
  })

  it('reads an omitted field as the default it stands for, so an exact revert is not a change', () => {
    const defaults = {
      title_layout: { option: 'preserve', destination: 'ask' },
      tags: { option: 'off', destination: 'ask' },
    }
    const active = {
      schema_version: 'metadata-policy-notebook-v2',
      policy_id: 'policy-1',
      profile: {
        name: 'Active',
        options: { title_layout: 'preserve' },
        destinations: {},
      },
    } as never
    // The draft spells out what the active document leaves to the default; the
    // two describe the same policy, so nothing is offered for validation.
    expect(
      derivePolicyDraftDisplay(
        active,
        {
          name: 'Active',
          options: { title_layout: 'preserve', tags: 'off' },
          destinations: { title_layout: 'ask', tags: 'ask' },
        },
        defaults,
      ),
    ).toMatchObject({ changed: false })
    expect(
      derivePolicyDraftDisplay(
        active,
        {
          name: 'Active',
          options: { title_layout: 'preserve', tags: 'replace' },
          destinations: {},
        },
        defaults,
      ),
    ).toMatchObject({ changed: true })
  })

  const csrfChallenge = () => Response.json({ error: { code: 'csrf' } }, { status: 403 })

  it('renews CSRF once but requires an explicit retry of a request it cannot resend', async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(csrfChallenge())
      .mockResolvedValueOnce(new Response(null, { status: 204 }))
    const client = new ReviewApiClient({ csrf, fetch: fetchMock, buildIdentity: 'c'.repeat(64) })

    const refused = client.logout()
    await expect(refused).rejects.toBeInstanceOf(CsrfRenewedError)
    await expect(refused).rejects.toMatchObject({ status: 403, code: 'csrf_renewed' })
    expect(refusalCopy(await refused.catch((error: unknown) => error), 'fallback')).toBe(
      'Your session was refreshed. Press again.',
    )
    expect(refusalCopy(new ApiError(403, 'csrf_renewed'), 'fallback')).toBe('fallback')

    expect(csrf.renew).toHaveBeenCalledOnce()
    expect(fetchMock).toHaveBeenCalledOnce()
    csrf.current.mockReturnValueOnce(renewedToken)
    await client.logout()
    expect(new Headers(fetchMock.mock.calls[1]?.[1]?.headers).get('x-csrf-token')).toBe(
      renewedToken,
    )
  })

  it('resends an idempotency-keyed decision once with the renewed token', async () => {
    const { default: payload } = await import('./descriptionAdviceFixture.json')
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(csrfChallenge())
      .mockResolvedValueOnce(
        Response.json(
          decisionAcknowledgement({
            confirmation: { status: 'confirmed', case: { ...payload, review_case_id: 'case-1' } },
          }),
        ),
      )
    const client = new ReviewApiClient({ csrf, fetch: fetchMock, buildIdentity: 'c'.repeat(64) })

    const result = await client.recordDecision('case-1', {
      destination: 'title',
      outcome: 'keep_current',
      proposalId: 'proposal-1',
      targetFingerprint: 'fingerprint-1',
      offerDigest: 'offer-digest',
      idempotencyKey: 'same-decision',
    })

    expect(result.confirmation.status).toBe('confirmed')
    expect(csrf.renew).toHaveBeenCalledOnce()
    expect(fetchMock).toHaveBeenCalledTimes(2)
    const [first, second] = fetchMock.mock.calls.map(([, init]) => new Headers(init?.headers))
    expect(first?.get('x-csrf-token')).toBe(token)
    expect(second?.get('x-csrf-token')).toBe(renewedToken)
    expect(second?.get('idempotency-key')).toBe('same-decision')
    expect(first?.get('idempotency-key')).toBe('same-decision')
    expect(fetchMock.mock.calls[1]?.[1]?.body).toBe(fetchMock.mock.calls[0]?.[1]?.body)
  })

  const policyDraft: PolicyDraft = { name: 'Draft', options: {}, destinations: {} }
  // Preview and activation have their own typed methods; the rest are generic
  // policy commands.
  it.each([
    ['validate', (client: ReviewApiClient) => client.policyCommand('validate', policyDraft), 2],
    ['preview', (client: ReviewApiClient) => client.previewPolicy(policyDraft), 2],
    ['drafts', (client: ReviewApiClient) => client.policyCommand('drafts', policyDraft), 1],
    [
      'activate',
      (client: ReviewApiClient) =>
        client.activatePolicy({
          policy_id: 'policy-2',
          expected_active_policy_id: 'policy-1',
          expected_active_generation: 1,
        }),
      1,
    ],
    [
      'automatic-safety/pause',
      (client: ReviewApiClient) => client.policyCommand('automatic-safety/pause', { paused: true }),
      1,
    ],
  ] as const)('resends policy %s only when it is side-effect free', async (action, send, sends) => {
    // Only validate and preview are resent; each answers with its own document.
    const body = (action === 'preview' ? policyPreview : policyProfile)({ policy_id: 'policy-2' })
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(csrfChallenge())
      .mockResolvedValueOnce(Response.json(body))
    const client = new ReviewApiClient({ csrf, fetch: fetchMock, buildIdentity: 'c'.repeat(64) })

    const command = send(client)
    if (sends === 2) await expect(command).resolves.toEqual(body)
    else await expect(command).rejects.toBeInstanceOf(CsrfRenewedError)
    expect(csrf.renew).toHaveBeenCalledOnce()
    expect(fetchMock).toHaveBeenCalledTimes(sends)
  })

  it('renews and resends at most once when the resend is refused again', async () => {
    const fetchMock = vi.fn<typeof fetch>(async () => csrfChallenge())
    const client = new ReviewApiClient({ csrf, fetch: fetchMock, buildIdentity: 'c'.repeat(64) })

    await expect(client.policyCommand('validate', policyDraft)).rejects.toBeInstanceOf(
      CsrfRenewedError,
    )
    expect(csrf.renew).toHaveBeenCalledOnce()
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })

  // A lost resend is never sent a third time. A write that may have landed
  // stays ambiguous; validate writes nothing, so it is a lost network call.
  it.each([
    [
      'provider-recovery/retry',
      (client: ReviewApiClient) => client.retryProviderRecovery(7),
      AmbiguousMutationError,
    ],
    [
      'validate',
      (client: ReviewApiClient) => client.policyCommand('validate', policyDraft),
      NetworkUnavailableError,
    ],
  ] as const)('does not resend a lost %s resend again', async (_name, send, expected) => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(csrfChallenge())
      .mockRejectedValueOnce(new TypeError('network'))
    const client = new ReviewApiClient({ csrf, fetch: fetchMock, buildIdentity: 'c'.repeat(64) })

    await expect(send(client)).rejects.toBeInstanceOf(expected)
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })

  it.each([
    [Response.json({ error: { code: 'origin' } }, { status: 403 }), 'origin'],
    [new Response('not json', { status: 403 }), 'invalid_error_response'],
  ])('treats a non-csrf 403 as an ordinary refusal with no renewal', async (response, code) => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValueOnce(response)
    const client = new ReviewApiClient({ csrf, fetch: fetchMock, buildIdentity: 'c'.repeat(64) })

    const refused = client.policyCommand('validate', policyDraft)
    await expect(refused).rejects.toMatchObject({ status: 403, code })
    await expect(refused).rejects.not.toBeInstanceOf(CsrfRenewedError)
    expect(csrf.renew).not.toHaveBeenCalled()
    expect(fetchMock).toHaveBeenCalledOnce()
  })

  it('maps the server build-mismatch code to the controlled shell recovery', async () => {
    const mismatch = vi.fn()
    const client = new ReviewApiClient({
      csrf,
      buildIdentity: 'c'.repeat(64),
      fetch: vi.fn(async () =>
        Response.json({ error: { code: 'browser_build_mismatch' } }, { status: 409 }),
      ),
      onBuildMismatch: mismatch,
    })

    await expect(client.logout()).rejects.toMatchObject({ code: 'frontend_build_mismatch' })
    expect(mismatch).toHaveBeenCalledOnce()
  })

  it('rejects a read whose response names a different build and raises the shell recovery', async () => {
    const mismatch = vi.fn()
    const client = new ReviewApiClient({
      csrf,
      buildIdentity: 'c'.repeat(64),
      fetch: vi.fn(async () =>
        Response.json(
          { error: { code: 'unused' } },
          { status: 200, headers: { 'x-ml-build-identity': 'e'.repeat(64) } },
        ),
      ),
      onBuildMismatch: mismatch,
    })

    await expect(client.getLibrary({ lens: 'needs_attention' })).rejects.toBeInstanceOf(
      BuildMismatchError,
    )
    expect(mismatch).toHaveBeenCalledOnce()
  })

  it('reports session expiry and invokes document-navigation ownership', async () => {
    const expired = vi.fn()
    const client = new ReviewApiClient({
      csrf,
      fetch: vi.fn(async () => Response.json({ error: { code: 'session' } }, { status: 401 })),
      onSessionExpired: expired,
    })

    await expect(client.getLibrary({ lens: 'needs_attention' })).rejects.toBeInstanceOf(
      SessionExpiredError,
    )
    await vi.waitFor(() => expect(expired).toHaveBeenCalledOnce())
  })

  it('schedules one expiry hand-off without waiting for another request', async () => {
    const expired = vi.fn()
    let releaseSlowResponse: (response: Response) => void = () => undefined
    const slowResponse = new Promise<Response>((resolve) => {
      releaseSlowResponse = resolve
    })
    const fetchMock = vi.fn<typeof fetch>()
    fetchMock.mockImplementationOnce(async () => slowResponse)
    fetchMock.mockResolvedValueOnce(Response.json({ error: { code: 'session' } }, { status: 401 }))
    const client = new ReviewApiClient({
      csrf,
      fetch: fetchMock,
      onSessionExpired: expired,
    })

    const slowRequest = client.getAutomaticSafety()
    await expect(client.getLibrary({ lens: 'needs_attention' })).rejects.toBeInstanceOf(
      SessionExpiredError,
    )
    await vi.waitFor(() => expect(expired).toHaveBeenCalledOnce())
    releaseSlowResponse(Response.json({ error: { code: 'session' } }, { status: 401 }))
    await expect(slowRequest).rejects.toBeInstanceOf(SessionExpiredError)
    await new Promise<void>((resolve) => window.setTimeout(resolve, 0))
    expect(expired).toHaveBeenCalledOnce()
  })

  it('reports session expiry discovered while renewing CSRF', async () => {
    const expired = vi.fn()
    const renewalExpired = {
      current: () => token,
      renew: () => Promise.reject(new SessionExpiredError()),
    }
    const client = new ReviewApiClient({
      csrf: renewalExpired,
      buildIdentity: 'c'.repeat(64),
      fetch: vi.fn(async () => Response.json({ error: { code: 'csrf' } }, { status: 403 })),
      onSessionExpired: expired,
    })

    await expect(
      client.activatePolicy({
        policy_id: 'policy-2',
        expected_active_policy_id: 'policy-1',
        expected_active_generation: 1,
      }),
    ).rejects.toBeInstanceOf(SessionExpiredError)
    await vi.waitFor(() => expect(expired).toHaveBeenCalledOnce())
  })

  const sessionGone = () => Response.json({ error: { code: 'session' } }, { status: 401 })
  const flushExpiryHandOff = () => new Promise<void>((resolve) => window.setTimeout(resolve, 0))

  it('leaves the page to sign-out when a held read answers 401 after logout', async () => {
    const expired = vi.fn()
    let releaseHeldRead: (response: Response) => void = () => undefined
    const fetchMock = vi.fn<typeof fetch>()
    fetchMock.mockImplementationOnce(
      () => new Promise<Response>((resolve) => (releaseHeldRead = resolve)),
    )
    fetchMock.mockResolvedValueOnce(new Response(null, { status: 204 }))
    fetchMock.mockResolvedValueOnce(sessionGone())
    const client = new ReviewApiClient({
      csrf,
      fetch: fetchMock,
      buildIdentity: 'c'.repeat(64),
      onSessionExpired: expired,
    })

    const heldRead = client.getLibrary({ lens: 'needs_attention' })
    await client.logout()
    releaseHeldRead(sessionGone())
    await expect(heldRead).rejects.toBeInstanceOf(SessionExpiredError)
    await expect(client.getAttention({})).rejects.toBeInstanceOf(SessionExpiredError)
    await flushExpiryHandOff()
    expect(expired).not.toHaveBeenCalled()
  })

  it.each([
    ['answers 401', vi.fn(async () => sessionGone()), csrf],
    [
      'finds the session gone while renewing CSRF',
      vi.fn(async () => csrfChallenge()),
      { current: () => token, renew: () => Promise.reject(new SessionExpiredError()) },
    ],
  ])('rejects a logout that %s without the expiry hand-off', async (_, fetchMock, tokens) => {
    const expired = vi.fn()
    const client = new ReviewApiClient({
      csrf: tokens,
      fetch: fetchMock,
      buildIdentity: 'c'.repeat(64),
      onSessionExpired: expired,
    })

    await expect(client.logout()).rejects.toBeInstanceOf(SessionExpiredError)
    await flushExpiryHandOff()
    expect(expired).not.toHaveBeenCalled()
  })

  const unavailable = () => Response.json({ error: { code: 'unavailable' } }, { status: 503 })

  it('hands off a later expiry once when logout fails for another reason', async () => {
    const expired = vi.fn()
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValueOnce(unavailable())
    fetchMock.mockResolvedValue(sessionGone())
    const client = new ReviewApiClient({
      csrf,
      fetch: fetchMock,
      buildIdentity: 'c'.repeat(64),
      onSessionExpired: expired,
    })

    await expect(client.logout()).rejects.toMatchObject({ status: 503 })
    await expect(client.getLibrary({ lens: 'all' })).rejects.toBeInstanceOf(SessionExpiredError)
    await expect(client.getAttention({})).rejects.toBeInstanceOf(SessionExpiredError)
    await vi.waitFor(() => expect(expired).toHaveBeenCalledOnce())
    await flushExpiryHandOff()
    expect(expired).toHaveBeenCalledOnce()
  })

  it('replays an expiry held back during a logout that then fails', async () => {
    const expired = vi.fn()
    let releaseLogout: (response: Response) => void = () => undefined
    const fetchMock = vi.fn<typeof fetch>()
    fetchMock.mockImplementationOnce(
      () => new Promise<Response>((resolve) => (releaseLogout = resolve)),
    )
    fetchMock.mockResolvedValue(sessionGone())
    const client = new ReviewApiClient({
      csrf,
      fetch: fetchMock,
      buildIdentity: 'c'.repeat(64),
      onSessionExpired: expired,
    })

    const logout = client.logout()
    await expect(client.getAttention({})).rejects.toBeInstanceOf(SessionExpiredError)
    await flushExpiryHandOff()
    expect(expired).not.toHaveBeenCalled()
    releaseLogout(unavailable())
    await expect(logout).rejects.toMatchObject({ status: 503 })
    await flushExpiryHandOff()
    expect(expired).toHaveBeenCalledOnce()
    await expect(client.getLibrary({ lens: 'all' })).rejects.toBeInstanceOf(SessionExpiredError)
    await flushExpiryHandOff()
    expect(expired).toHaveBeenCalledOnce()
  })

  it('stays signed out when a second logout fails after one succeeded', async () => {
    const expired = vi.fn()
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(new Response(null, { status: 204 }))
      .mockResolvedValueOnce(unavailable())
      .mockResolvedValue(sessionGone())
    const client = new ReviewApiClient({
      csrf,
      fetch: fetchMock,
      buildIdentity: 'c'.repeat(64),
      onSessionExpired: expired,
    })

    await client.logout()
    await expect(client.logout()).rejects.toMatchObject({ status: 503 })
    await expect(client.getLibrary({ lens: 'all' })).rejects.toBeInstanceOf(SessionExpiredError)
    await flushExpiryHandOff()
    expect(expired).not.toHaveBeenCalled()
  })

  it('blocks mutations without a current build identity before transport', async () => {
    const fetchMock = vi.fn<typeof fetch>()
    const mismatch = vi.fn()
    const client = new ReviewApiClient({ csrf, fetch: fetchMock, onBuildMismatch: mismatch })

    await expect(client.logout()).rejects.toMatchObject({ code: 'frontend_build_mismatch' })
    expect(fetchMock).not.toHaveBeenCalled()
    expect(mismatch).toHaveBeenCalledOnce()
  })

  it('binds every mutation to the captured build identity', async () => {
    const fetchMock = vi.fn<typeof fetch>(async () => new Response(null, { status: 204 }))
    const buildIdentity = 'd'.repeat(64)
    const client = new ReviewApiClient({ csrf, fetch: fetchMock, buildIdentity })

    await client.logout()

    expect(new Headers(fetchMock.mock.calls[0]?.[1]?.headers).get('x-ml-build-identity')).toBe(
      buildIdentity,
    )
  })

  it('accepts scan progress acknowledgements independently from preparation controls', async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(
        Response.json(scanProgress({ scan_id: 'scan-1', state: 'queued' }), {
          status: 202,
        }),
      )
      .mockResolvedValueOnce(
        Response.json(
          preparation({
            schema_version: 'review-preparation-v2',
            recovery_required: false,
            paused: false,
          }),
        ),
      )
    const client = new ReviewApiClient({ csrf, fetch: fetchMock, buildIdentity: 'c'.repeat(64) })

    await expect(client.preparationAction('primary/Books/scan')).resolves.toMatchObject({
      scan_id: 'scan-1',
      state: 'queued',
    })
    await expect(client.preparationAction('pause')).resolves.toMatchObject({
      schema_version: 'review-preparation-v2',
      recovery_required: false,
      paused: false,
    })
  })

  it('reads only closed target-connection preparation conditions', async () => {
    const base = preparation({ active: true })
    for (const state of ['paused', 'checking', 'recovered']) {
      const view = await readPreparation({
        ...base,
        connections: [
          {
            connection: 'primary',
            code: 'unavailable',
            state,
            affected: state === 'recovered' ? 0 : 2,
            updated_at: 1,
          },
        ],
      })
      expect(view.connections[0]?.state).toBe(state)
    }
    for (const [invalid, failure] of [
      [{ ...base, connections: undefined }, 'required@/connections'],
      [
        {
          ...base,
          connections: [
            { connection: 'primary', code: 'unknown', state: 'paused', affected: 1, updated_at: 1 },
          ],
        },
        'enum@/connections/0/code',
      ],
      [
        {
          ...base,
          connections: [{ connection: 'primary', code: 'tls', state: 'paused', affected: 1 }],
        },
        'required@/connections/0/updated_at',
      ],
    ] as const)
      await expect(readPreparation(invalid)).rejects.toMatchObject(refused(failure))
  })

  it('reads only closed, bounded provider throttling (#1282)', async () => {
    const base: Record<string, unknown> = preparation()
    const entry = {
      library: 'Synthetic ebooks',
      adapter: 'google_books',
      state: 'waiting',
      reasons: ['burst_throttled'],
      deferred: 214,
      next_retry_at: 1_800_000_000,
      remedy: 'add_api_key',
      notice_at: 1_800_086_400,
      untried: false,
    }
    const throttling = {
      generation: 3,
      retry: 'available',
      entries: [entry],
      answered: [{ library: 'Synthetic audio', adapter: 'hardcover' }],
    }
    // An absent block was once read as none (T5); the schema requires it.
    const absent = { ...base }
    delete absent.provider_throttling
    await expect(readPreparation(absent)).rejects.toMatchObject(
      refused('required@/provider_throttling'),
    )
    expect(
      (await readPreparation({ ...base, provider_throttling: null })).provider_throttling,
    ).toBeNull()
    expect(
      (await readPreparation({ ...base, provider_throttling: throttling })).provider_throttling,
    ).toEqual(throttling)
    const unannounced = { ...throttling, entries: [{ ...entry, notice_at: null }] }
    expect(
      (await readPreparation({ ...base, provider_throttling: unannounced })).provider_throttling,
    ).toEqual(unannounced)
    const missing: Record<string, unknown> = { ...entry }
    delete missing.notice_at
    await expect(
      readPreparation({ ...base, provider_throttling: { ...throttling, entries: [missing] } }),
    ).rejects.toMatchObject(refused('required@/provider_throttling/entries/0/notice_at'))
    const unsaid: Record<string, unknown> = { ...entry }
    delete unsaid.untried
    await expect(
      readPreparation({ ...base, provider_throttling: { ...throttling, entries: [unsaid] } }),
    ).rejects.toMatchObject(refused('required@/provider_throttling/entries/0/untried'))
    const maximal = { ...throttling, entries: Array.from({ length: 256 }, () => entry) }
    expect(
      (await readPreparation({ ...base, provider_throttling: maximal })).provider_throttling
        ?.entries,
    ).toHaveLength(256)
    const at = '/provider_throttling'
    for (const [invalid, failure] of [
      [
        { ...throttling, entries: Array.from({ length: 257 }, () => entry) },
        `max_items@${at}/entries`,
      ],
      [
        { ...throttling, entries: [{ ...entry, adapter: 'unknown_source' }] },
        `enum@${at}/entries/0/adapter`,
      ],
      [{ ...throttling, entries: [{ ...entry, deferred: -1 }] }, `min@${at}/entries/0/deferred`],
      [{ ...throttling, entries: [{ ...entry, deferred: 1.5 }] }, `type@${at}/entries/0/deferred`],
      [
        { ...throttling, entries: [{ ...entry, state: 'recovered' }] },
        `enum@${at}/entries/0/state`,
      ],
      [
        { ...throttling, entries: [{ ...entry, reasons: ['unknown'] }] },
        `enum@${at}/entries/0/reasons/0`,
      ],
      [
        { ...throttling, entries: [{ ...entry, remedy: 'buy_more' }] },
        `enum@${at}/entries/0/remedy`,
      ],
      [{ ...throttling, entries: [{ ...entry, notice_at: -1 }] }, `min@${at}/entries/0/notice_at`],
      [
        { ...throttling, entries: [{ ...entry, notice_at: 8_640_000_000_001 }] },
        `max@${at}/entries/0/notice_at`,
      ],
      [
        { ...throttling, entries: [{ ...entry, library: '' }] },
        `min_length@${at}/entries/0/library`,
      ],
      [
        { ...throttling, entries: [{ ...entry, library: 'x'.repeat(65) }] },
        `max_length@${at}/entries/0/library`,
      ],
      [
        { ...throttling, entries: [{ ...entry, connection: 'primary-google' }] },
        `unknown_key@${at}/entries/0/connection`,
      ],
      [{ ...throttling, retry: 'later' }, `enum@${at}/retry`],
      [{ ...throttling, generation: 0 }, `min@${at}/generation`],
      [{ ...throttling, extra: true }, `unknown_key@${at}/extra`],
      [{ generation: 3, retry: 'available', entries: [entry] }, `required@${at}/answered`],
      [
        { ...throttling, answered: [{ library: 'Synthetic audio', adapter: 'unknown_source' }] },
        `enum@${at}/answered/0/adapter`,
      ],
    ] as const)
      await expect(
        readPreparation({ ...base, provider_throttling: invalid }),
      ).rejects.toMatchObject(refused(failure))
  })

  it("reads a library's absent languages as the empty list and refuses any but a list of strings", async () => {
    const { view } = await import('./settingsFixtures')
    const withLanguages = (languages: unknown) => {
      const value = view()
      return {
        ...value,
        document: {
          ...value.document,
          libraries: value.document.libraries.map((library) => ({ ...library, languages })),
        },
      }
    }
    const absent = Object.fromEntries(
      Object.entries(view().document.libraries[0] ?? {}).filter(([key]) => key !== 'languages'),
    )
    // The decoder fills the documented default (#1689).
    expect(
      (await readSettings({ ...view(), document: { ...view().document, libraries: [absent] } }))
        .document.libraries[0]?.languages,
    ).toEqual([])
    expect(
      (await readSettings(withLanguages(['de', 'en']))).document.libraries[0]?.languages,
    ).toEqual(['de', 'en'])
    const at = '/document/libraries/0/languages'
    for (const [invalid, failure] of [
      ['de', `type@${at}`],
      [[1], `type@${at}/0`],
      [[null], `type@${at}/0`],
      [null, `type@${at}`],
      [{ de: true }, `type@${at}`],
    ] as const)
      await expect(readSettings(withLanguages(invalid))).rejects.toMatchObject(refused(failure))
  })

  // The checks the settings mapper made by hand, each now the decoder's
  // (2b inventory E list for `mapSettings`, and the presence site at
  // d53effa `:2389`) (#1689).
  it('refuses a malformed settings answer at the decoder', async () => {
    const { view } = await import('./settingsFixtures')
    const base = view()
    const doc = base.document
    const library = doc.libraries[0]
    for (const [invalid, failure] of [
      [{ ...base, authority: 'deploy' }, 'enum@/authority'],
      [
        // The stored settings version stays on the server (#1847).
        { ...base, document: { ...doc, schema_version: 'any' } },
        'unknown_key@/document/schema_version',
      ],
      [
        {
          ...base,
          document: { ...doc, description_advice: { ...doc.description_advice, extra: true } },
        },
        'unknown_key@/document/description_advice/extra',
      ],
      [
        {
          ...base,
          document: {
            ...doc,
            description_advice: { ...doc.description_advice, remove_praise: 'yes' },
          },
        },
        'type@/document/description_advice/remove_praise',
      ],
      [{ ...base, document: { ...doc, connections: {} } }, 'type@/document/connections'],
      [{ ...base, document: { ...doc, providers: null } }, 'type@/document/providers'],
      [{ ...base, fields: {} }, 'type@/fields'],
      [{ ...base, secrets: null }, 'type@/secrets'],
      [
        {
          ...base,
          document: { ...doc, connections: [{ ...doc.connections[0], adapter: 'plex' }] },
        },
        'const@/document/connections/0/adapter',
      ],
      [
        { ...base, document: { ...doc, libraries: [{ ...library, library_id: 7 }] } },
        'type@/document/libraries/0/library_id',
      ],
      [
        {
          ...base,
          document: { ...doc, libraries: [{ ...library, provider_connections: 'hardcover' }] },
        },
        'type@/document/libraries/0/provider_connections',
      ],
      [
        { ...base, fields: [{ ...base.fields[0], apply_effect: 'restart' }] },
        'enum@/fields/0/apply_effect',
      ],
      [
        { ...base, fields: [{ ...base.fields[0], managed_by_file: 'no' }] },
        'type@/fields/0/managed_by_file',
      ],
      [{ ...base, secrets: [{ ...base.secrets[0], indicator: 7 }] }, 'type@/secrets/0/indicator'],
      [{ ...base, secrets: [{ ...base.secrets[0], configured: 1 }] }, 'type@/secrets/0/configured'],
    ] as const)
      await expect(readSettings(invalid)).rejects.toMatchObject(refused(failure))
  })

  it('posts Retry now with the expected generation and resends it once after CSRF renewal', async () => {
    const { view } = await import('./settingsFixtures')
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(csrfChallenge())
      .mockResolvedValueOnce(Response.json(view()))
    const client = new ReviewApiClient({ csrf, fetch: fetchMock, buildIdentity: 'c'.repeat(64) })

    await expect(client.retryProviderRecovery(7)).resolves.toMatchObject({ authority: 'store' })
    expect(fetchMock).toHaveBeenCalledTimes(2)
    const [url, init] = fetchMock.mock.calls[1] ?? []
    expect(url).toBe('/api/v1/provider-recovery/retry')
    expect(init?.method).toBe('POST')
    expect(init?.body).toBe(JSON.stringify({ expected_generation: 7 }))
    expect(new Headers(init?.headers).get('x-csrf-token')).toBe(renewedToken)
  })

  it("accepts only a closed count of the run's works as they stand now", async () => {
    const now = {
      ready: 1,
      recorded: 2,
      not_matched: 0,
      failed: 0,
      in_progress: 1,
      scan_ids: ['scan-1'],
    }
    const view = (value: unknown) =>
      readPreparation(
        preparation({ active: true, presentation: preparationPresentation({ now: value }) }),
      )
    expect((await view(now)).presentation?.now).toEqual(now)
    expect((await view(null)).presentation?.now).toBeNull()
    const at = '/presentation/now'
    for (const [malformed, failure] of [
      [{ ...now, extra: 0 }, `unknown_key@${at}/extra`],
      [
        { ready: 1, recorded: 2, not_matched: 0, failed: 0, in_progress: 1 },
        `required@${at}/scan_ids`,
      ],
      [[], `type@${at}`],
      [{ ...now, failed: -1 }, `min@${at}/failed`],
      [{ ...now, ready: true }, `type@${at}/ready`],
      [{ ...now, scan_ids: 'scan-1' }, `type@${at}/scan_ids`],
      [{ ...now, scan_ids: [1] }, `type@${at}/scan_ids/0`],
      [{ ...now, scan_ids: Array.from({ length: 129 }, () => 'scan') }, `max_items@${at}/scan_ids`],
    ] as const)
      await expect(view(malformed)).rejects.toMatchObject(refused(failure))
  })

  it('accepts only ordered and bounded preparation presentation content', async () => {
    const presentation = preparationPresentation({
      passage: { read: 4, searches_finished: 3, compared: 2, checked: 2, ready: 1, failed: 1 },
      active_books: [{ name: 'Private title', stage: 'Comparing sources' }],
      recent_completions: [{ name: 'Finished title', outcome: 'ready', completed_at: 101 }],
      throughput_per_minute: 1.2,
    })
    const base = preparation({ active: true, presentation })
    const read = (changes: Record<string, unknown>) =>
      readPreparation({ ...base, presentation: { ...presentation, ...changes } })
    expect((await readPreparation(base)).presentation?.passage.ready).toBe(1)
    const allTriggers = [
      'initial_setup',
      'manual_scan',
      'library_added',
      'title_recovery',
      'automatic_confirmed',
      'matching_changed',
    ].map((trigger) => ({ ...presentation.triggers[0], trigger }))
    expect((await read({ triggers: allTriggers })).presentation?.triggers).toHaveLength(6)
    expect((await read({ triggers: [allTriggers[3]] })).presentation?.triggers[0]?.trigger).toBe(
      'title_recovery',
    )
    expect((await read({ triggers: [allTriggers[4]] })).presentation?.triggers[0]?.trigger).toBe(
      'automatic_confirmed',
    )
    // Saving Book matching re-checks current works as its own cause (#1355).
    expect((await read({ triggers: [allTriggers[5]] })).presentation?.triggers[0]?.trigger).toBe(
      'matching_changed',
    )
    await expect(
      read({ triggers: [...allTriggers, { ...allTriggers[0], trigger: 'unknown' }] }),
    ).rejects.toMatchObject(refused('max_items@/presentation/triggers'))
    await expect(
      read({ triggers: [{ ...allTriggers[0], trigger: 'unknown' }] }),
    ).rejects.toMatchObject(refused('enum@/presentation/triggers/0/trigger'))
    // One entry per trigger family is a named check (ADR-0091), not the schema's.
    await expect(read({ triggers: [allTriggers[3], allTriggers[3]] })).rejects.toThrow(
      new ApiError(502, 'invalid_preparation_trigger'),
    )

    const maximumName = 'x'.repeat(2048)
    expect(
      (
        await read({
          active_books: [{ name: maximumName, stage: 'Reading' }],
          recent_completions: [{ name: maximumName, outcome: 'ready', completed_at: 101 }],
        })
      ).presentation?.active_books[0]?.name,
    ).toBe(maximumName)
    const maximumMultibyteName = `${'界'.repeat(682)}ab`
    expect(new TextEncoder().encode(maximumMultibyteName)).toHaveLength(2048)
    expect(
      (
        await read({
          active_books: [{ name: maximumMultibyteName, stage: 'Reading' }],
          recent_completions: [{ name: maximumMultibyteName, outcome: 'ready', completed_at: 101 }],
        })
      ).presentation?.recent_completions[0]?.name,
    ).toBe(maximumMultibyteName)
    await expect(
      read({ active_books: [{ name: `${maximumName}x`, stage: 'Reading' }] }),
    ).rejects.toMatchObject(refused('max_length@/presentation/active_books/0/name'))
    await expect(
      read({
        recent_completions: [{ name: `${maximumName}x`, outcome: 'ready', completed_at: 101 }],
      }),
    ).rejects.toMatchObject(refused('max_length@/presentation/recent_completions/0/name'))
    await expect(
      read({ passage: { ...presentation.passage, checked: 1, ready: 2 } }),
    ).rejects.toThrow(new ApiError(502, 'invalid_preparation_passage_order'))
    expect(
      (
        await read({
          passage: { read: 1, searches_finished: 0, compared: 0, checked: 0, ready: 0, failed: 1 },
        })
      ).presentation?.passage.failed,
    ).toBe(1)
    expect(
      (
        await read({
          passage: { read: 1, searches_finished: 1, compared: 1, checked: 1, ready: 1, failed: 1 },
          total: 1,
        })
      ).presentation?.passage,
    ).toMatchObject({ ready: 1, failed: 1 })
    const total = new ApiError(502, 'invalid_preparation_passage_total')
    await expect(read({ updated_at: 99 })).rejects.toThrow(total)
    await expect(read({ state: 'completed_clean' })).rejects.toThrow(total)
    await expect(read({ completed_at: 101 })).rejects.toThrow(total)
    await expect(read({ state: 'dismissed', completed_at: 99 })).rejects.toThrow(total)
    await expect(
      read({
        passage: { read: 0, searches_finished: 0, compared: 0, checked: 0, ready: 0, failed: 0 },
        active_overflow: 1,
        total: 1,
      }),
    ).rejects.toThrow(total)
    await expect(
      read({
        active_books: Array.from({ length: 6 }, () => ({
          name: 'Private title',
          stage: 'Reading',
        })),
      }),
    ).rejects.toMatchObject(refused('max_items@/presentation/active_books'))
    await expect(
      read({
        recent_completions: [{ name: 'Private title', outcome: 'invented', completed_at: 101 }],
      }),
    ).rejects.toMatchObject(refused('enum@/presentation/recent_completions/0/outcome'))
  })

  it('preserves structured server error codes and rejects malformed DTOs', async () => {
    const client = new ReviewApiClient({
      csrf,
      fetch: vi.fn(async () =>
        Response.json(
          { schema_version: 'review-library-v12', error: { code: 'busy' } },
          { status: 503 },
        ),
      ),
    })
    await expect(client.getLibrary({ lens: 'all' })).rejects.toMatchObject({
      status: 503,
      code: 'busy',
    })
    await expect(readLibrary(library({ lens: '__proto__' }))).rejects.toMatchObject(
      refused('enum@/lens'),
    )
    const work = libraryWork({ media_kind: 'm4b', detail_kind: 'reports' })
    const page = library({
      items: [work],
      counts: {
        needs_attention: 0,
        all: 1,
        no_attention_needed: 1,
        not_matched: 0,
        preparation_failed: 0,
        flagged: 0,
        new: 0,
      },
    })
    await expect(readLibrary(page)).rejects.toMatchObject(refused('enum@/items/0/detail_kind'))
    // `ebook` is not a format the server reports (#1286).
    await expect(
      readLibrary({ ...page, items: [{ ...work, media_kind: 'ebook', detail_kind: 'case' }] }),
    ).rejects.toMatchObject(refused('enum@/items/0/media_kind'))
    // A stopped preparation names its stage and status from closed sets, and
    // a code outside either is refused rather than shown raw (#1286 C6).
    const stopped = (preparation: Record<string, unknown>) => ({
      ...page,
      items: [
        {
          ...work,
          detail_kind: 'case',
          preparation: {
            attempt: 1,
            check_given_up: false,
            diagnostic_reference: 'diagnostic-1',
            media_health: [],
            provider: 'none',
            reason: 'The file could not be read.',
            recovery: 'Replace the file.',
            recovery_attempt: 0,
            stage: 'extraction',
            status: 'unreadable_media',
            ...preparation,
          },
        },
      ],
    })
    expect((await readLibrary(stopped({}))).works[0]?.preparation).toMatchObject({
      stage: 'extraction',
      status: 'unreadable_media',
    })
    await expect(readLibrary(stopped({ stage: 'reading' }))).rejects.toMatchObject(
      refused('enum@/items/0/preparation/stage'),
    )
    await expect(readLibrary(stopped({ status: 'broken' }))).rejects.toMatchObject(
      refused('enum@/items/0/preparation/status'),
    )
  })

  it('refuses a library document served under any token but its own', async () => {
    // The library gained a required media-health member and moved to v2. A
    // client that reads whatever token arrives would render a v1 document as
    // though the member were simply absent, which is the reading the server
    // moved the token to prevent.
    const page = library({ schema_version: 'review-library-v1' })
    await expect(readLibrary(page)).rejects.toMatchObject(refused('const@/schema_version'))
    // v3 added the Not matched count and lens (#1407); a v2 document lacks both.
    await expect(
      readLibrary({ ...page, schema_version: 'review-library-v2' }),
    ).rejects.toMatchObject(refused('const@/schema_version'))
    // v5 added each source's companion ISBN flag (#1723); a v4 document lacks it.
    await expect(
      readLibrary({ ...page, schema_version: 'review-library-v4' }),
    ).rejects.toMatchObject(refused('const@/schema_version'))
    // v6 added each work's pinned field count (#1346); a v5 document lacks it.
    await expect(
      readLibrary({ ...page, schema_version: 'review-library-v5' }),
    ).rejects.toMatchObject(refused('const@/schema_version'))
    // v7 added the Looks wrong count, lens and each work's findings (#1334);
    // a v6 document lacks them.
    await expect(
      readLibrary({ ...page, schema_version: 'review-library-v6' }),
    ).rejects.toMatchObject(refused('const@/schema_version'))
    await expect(
      readLibrary({ ...page, schema_version: 'review-library-v7' }),
    ).rejects.toMatchObject(refused('const@/schema_version'))
    // v8 added the New count and lens and how far the listing had got
    // (#1351); a v7 document lacks them.
    await expect(
      readLibrary({ ...page, schema_version: 'review-library-v8' }),
    ).rejects.toMatchObject(refused('const@/schema_version'))
    // v12 added a summary description's formatted copy (#1995); a v11
    // summary lacks it.
    await expect(
      readLibrary({ ...page, schema_version: 'review-library-v11' }),
    ).rejects.toMatchObject(refused('const@/schema_version'))
    await expect(
      readLibrary({ ...page, schema_version: 'review-library-v12' }),
    ).resolves.toBeDefined()
  })

  it('reads the Looks wrong lens, its count and each finding in rule order (#1334)', async () => {
    const flagged = library({
      lens: 'flagged',
      counts: {
        all: 2,
        flagged: 1,
        new: 0,
        needs_attention: 0,
        no_attention_needed: 2,
        not_matched: 0,
        preparation_failed: 0,
      },
      items: [
        libraryWork({
          title: 'THE EXAMPLE STORY',
          value_flags: [
            { destination: 'title', rule: 'title_all_capitals' },
            { destination: 'narrators', rule: 'credit_label' },
          ],
        }),
      ],
    })
    const mapped = await readLibrary(flagged)
    expect(mapped.lens).toBe('flagged')
    expect(mapped.counts.flagged).toBe(1)
    expect(mapped.works[0]?.valueFlags).toEqual([
      { destination: 'title', rule: 'title_all_capitals' },
      { destination: 'narrators', rule: 'credit_label' },
    ])
    const finding = (flag: Record<string, unknown>) => ({
      ...flagged,
      items: [libraryWork({ value_flags: [flag] })],
    })
    await expect(
      readLibrary(finding({ destination: 'title', rule: 'title_in_capitals' })),
    ).rejects.toMatchObject(refused('enum@/items/0/value_flags/0/rule'))
    await expect(
      readLibrary(finding({ destination: 'subtitle', rule: 'title_all_capitals' })),
    ).rejects.toMatchObject(refused('enum@/items/0/value_flags/0/destination'))
    // The schema cannot pair a credit label with a contributor list, so the
    // mapper refuses either crossing.
    await expect(
      readLibrary(finding({ destination: 'title', rule: 'credit_label' })),
    ).rejects.toMatchObject({ status: 502, code: 'invalid_value_flag' })
    await expect(
      readLibrary(finding({ destination: 'authors', rule: 'title_all_capitals' })),
    ).rejects.toMatchObject({ status: 502, code: 'invalid_value_flag' })
    const counts: Record<string, number> = { ...flagged.counts }
    delete counts.flagged
    await expect(readLibrary({ ...flagged, counts })).rejects.toMatchObject(
      refused('required@/counts/flagged'),
    )
    const items = [libraryWork()]
    delete (items[0] as Record<string, unknown>).value_flags
    await expect(readLibrary({ ...flagged, items })).rejects.toMatchObject(
      refused('required@/items/0/value_flags'),
    )
  })

  it('reads each work’s pinned field count (#1346)', async () => {
    const page = (count: number) => library({ items: [libraryWork({ pinned_count: count })] })
    expect((await readLibrary(page(2))).works[0]?.pinnedCount).toBe(2)
    await expect(readLibrary(page(-1))).rejects.toMatchObject(refused('min@/items/0/pinned_count'))
  })

  it('maps a library work cover and refuses a malformed one', async () => {
    const preview = 'a'.repeat(64)
    const cover = {
      height: 452,
      preview_sha256: preview,
      url: `/api/v1/library/cover?connection=primary&library=books&item=item%2F1&preview=${preview}`,
      width: 302,
    }
    const page = (value: unknown) => {
      const work: Record<string, unknown> = libraryWork({
        item: 'item/1',
        detail_url: '/api/v1/library/report?item=item%2F1',
        cover: value,
      })
      // The absent case removes the member.
      if (value === undefined) delete work.cover
      return library({
        items: [work],
        counts: {
          needs_attention: 0,
          all: 1,
          no_attention_needed: 1,
          not_matched: 0,
          preparation_failed: 0,
          flagged: 0,
          new: 0,
        },
      })
    }
    expect((await readLibrary(page(cover))).works[0]?.cover).toEqual({
      previewSha256: preview,
      width: 302,
      height: 452,
      url: cover.url,
    })
    // A server that says no cover leaves the work its spine mark; the member
    // itself is required, so its absence is a document this client refuses.
    expect((await readLibrary(page(null))).works[0]).not.toHaveProperty('cover')
    await expect(readLibrary(page(undefined))).rejects.toMatchObject(
      refused('required@/items/0/cover'),
    )
    for (const [malformed, failure] of [
      ['cover', 'type@/items/0/cover'],
      [{ ...cover, preview_sha256: 'A'.repeat(64) }, 'pattern@/items/0/cover/preview_sha256'],
      [{ ...cover, width: 0 }, 'min@/items/0/cover/width'],
      [{ ...cover, height: 1.5 }, 'type@/items/0/cover/height'],
      // The address must be the cover route, naming this preview.
      [{ ...cover, url: `/api/v1/review-cases/x/images/${preview}` }, 'pattern@/items/0/cover/url'],
      [
        { ...cover, url: `https://elsewhere.test/api/v1/library/cover?preview=${preview}` },
        'pattern@/items/0/cover/url',
      ],
    ] as const)
      await expect(readLibrary(page(malformed)), JSON.stringify(malformed)).rejects.toMatchObject(
        refused(failure),
      )
    // The decoder holds the route; the binding to this preview is the mapper's
    // own named check, which a read reports under its own code.
    await expect(
      readLibrary(page({ ...cover, url: `/api/v1/library/cover?preview=${'b'.repeat(64)}` })),
    ).rejects.toThrow(new ApiError(502, 'invalid_library_cover'))
  })

  it('reads the Not matched lens, its count and a closed reason state (#1407)', async () => {
    const work = (state: string) =>
      libraryWork({
        title: 'Unmatched work',
        attention_state: 'not_matched',
        detail_url: '/api/v1/library/report?connection=primary&library=Books&item=item-1',
        summary: { fields: [], reason: 'No review decision is recorded.', state },
      })
    const page = (state: string) =>
      library({
        items: [work(state)],
        counts: {
          needs_attention: 0,
          all: 3,
          no_attention_needed: 1,
          not_matched: 2,
          preparation_failed: 0,
          flagged: 0,
          new: 0,
        },
        lens: 'not_matched',
      })
    const mapped = await readLibrary(page('not_matched_unavailable_scheduled'))
    expect(mapped.lens).toBe('not_matched')
    expect(mapped.counts).toEqual({
      attention: 0,
      all: 3,
      clear: 1,
      notMatched: 2,
      failed: 0,
      flagged: 0,
      new: 0,
    })
    expect(mapped.works[0]?.summary?.state).toBe('not_matched_unavailable_scheduled')
    await expect(readLibrary(page('not_matched_somehow'))).rejects.toMatchObject(
      refused('enum@/items/0/summary/state'),
    )
    const stray = {
      ...page('not_matched_refused'),
      items: [{ ...work('not_matched_refused'), attention_state: 'unmatched' }],
    }
    await expect(readLibrary(stray)).rejects.toMatchObject(refused('enum@/items/0/attention_state'))
    await expect(
      readLibrary({ ...page('not_matched_refused'), lens: 'unmatched' }),
    ).rejects.toMatchObject(refused('enum@/lens'))
    const counts: Record<string, number> = { ...page('not_matched_refused').counts }
    delete counts.not_matched
    await expect(readLibrary({ ...page('not_matched_refused'), counts })).rejects.toMatchObject(
      refused('required@/counts/not_matched'),
    )
  })
})

describe('mapReportDetail', () => {
  const detail = reportDetail

  it('refuses an evidence kind, basis or state, or a cover surface, outside its closed set (#1286 C6)', async () => {
    const claim = {
      basis: 'sidecar',
      excerpt: 'Recorded Narrator',
      kind: 'contributor',
      role: 'narrator',
      state: 'valid',
      value: 'Recorded Narrator',
    }
    const mapped = await readReport(
      detail({ evidence: [claim], embedded_cover: { present: true, surface: 'mp4_ilst' } }),
    )
    expect(mapped.evidence[0]).toMatchObject({ kind: 'contributor', basis: 'sidecar' })
    for (const [override, failure] of [
      [{ kind: 'narrator' }, 'enum@/evidence/0/kind'],
      [{ basis: 'filename' }, 'enum@/evidence/0/basis'],
      [{ state: 'broken' }, 'enum@/evidence/0/state'],
    ] as const)
      await expect(
        readReport(detail({ evidence: [{ ...claim, ...override }] })),
      ).rejects.toMatchObject(refused(failure))
    await expect(
      readReport(detail({ embedded_cover: { present: true, surface: 'apic' } })),
    ).rejects.toMatchObject(refused('enum@/embedded_cover/surface'))
  })

  it('requires a closed nullable recording basis and maps its attribution', async () => {
    const basis = {
      basis: 'chapter_runtime_and_structure',
      provider: 'Audnexus',
      candidate: 'recording-1',
    }
    expect(
      (
        await readReport(
          detail({ recording_identity_basis: basis, audiobook_review_scope: 'recording' }),
        )
      ).recordingIdentityBasis,
    ).toEqual(basis)
    expect((await readReport(detail())).recordingIdentityBasis).toBeNull()
    for (const [value, failure] of [
      [undefined, 'required@/recording_identity_basis'],
      [{}, 'required@/recording_identity_basis/basis'],
      [{ ...basis, basis: 'runtime' }, 'const@/recording_identity_basis/basis'],
      [{ ...basis, extra: true }, 'unknown_key@/recording_identity_basis/extra'],
      [{ ...basis, provider: '' }, 'min_length@/recording_identity_basis/provider'],
      [{ ...basis, candidate: 'x'.repeat(129) }, 'max_length@/recording_identity_basis/candidate'],
      [{ ...basis, provider: 'bad\nsource' }, 'pattern@/recording_identity_basis/provider'],
    ] as const) {
      const document = detail({ recording_identity_basis: value })
      await expect(readReport(document), JSON.stringify(value)).rejects.toMatchObject(
        refused(failure),
      )
    }
  })

  it('requires a consistent closed audiobook review scope', async () => {
    expect(
      (await readReport(detail({ audiobook_review_scope: 'work' }))).audiobookReviewScope,
    ).toBe('work')
    expect(
      (await readReport(detail({ audiobook_review_scope: 'recording' }))).audiobookReviewScope,
    ).toBe('recording')
    for (const [value, failure] of [
      [undefined, 'required@/audiobook_review_scope'],
      ['', 'enum@/audiobook_review_scope'],
      ['edition', 'enum@/audiobook_review_scope'],
      [{}, 'type@/audiobook_review_scope'],
      [true, 'type@/audiobook_review_scope'],
    ] as const) {
      const document = detail({ audiobook_review_scope: value })
      await expect(readReport(document), JSON.stringify(value)).rejects.toMatchObject(
        refused(failure),
      )
    }
    // The scope against the media kind and the recording basis are the
    // helper's own named checks, which a read reports under their own code.
    const inconsistent = new ApiError(502, 'inconsistent_audiobook_review_scope')
    await expect(
      readReport(detail({ media_kind: 'epub', audiobook_review_scope: 'work' })),
    ).rejects.toThrow(inconsistent)
    await expect(
      readReport(
        detail({
          audiobook_review_scope: 'work',
          recording_admission_basis: null,
          recording_identity_basis: {
            basis: 'chapter_runtime_and_structure',
            provider: 'Audnexus',
            candidate: 'recording-1',
          },
        }),
      ),
    ).rejects.toThrow(inconsistent)
  })

  it('maps the media-health section and refuses an older report detail', async () => {
    const mapped = await readReport(detail())

    expect(mapped.mediaHealth).toEqual([
      {
        bound: 'The declared track sequence runs without a hole.',
        cited: ['track 1 declares sequence 1', 'track 2 declares sequence 3'],
        expected: null,
        kind: 'track_sequence_gap',
        observed: null,
        tracks: [1, 2],
      },
    ])
    // A v2 document has no media-health section at all, so accepting one would
    // draw an empty section the server never stated anything about.
    await expect(
      readReport(detail({ schema_version: 'review-report-detail-v2' })),
    ).rejects.toMatchObject(refused('const@/schema_version'))
  })

  it('refuses a media-health observation that is not the closed shape', async () => {
    await expect(readReport(detail({ media_health: {} }))).rejects.toMatchObject(
      refused('type@/media_health'),
    )
    await expect(readReport(detail({ media_health: ['gap'] }))).rejects.toMatchObject(
      refused('type@/media_health/0'),
    )
    const unbounded = { ...detail().media_health[0], bound: 'x'.repeat(257) }
    await expect(readReport(detail({ media_health: [unbounded] }))).rejects.toMatchObject(
      refused('max_length@/media_health/0/bound'),
    )
    const untyped = { ...detail().media_health[0], tracks: ['1'] }
    await expect(readReport(detail({ media_health: [untyped] }))).rejects.toMatchObject(
      refused('type@/media_health/0/tracks/0'),
    )
  })

  it('accepts every media-health kind and refuses one outside the vocabulary', async () => {
    expect(MEDIA_HEALTH_KINDS).toHaveLength(13)
    for (const kind of MEDIA_HEALTH_KINDS) {
      const mapped = await readReport(
        detail({ media_health: [{ ...detail().media_health[0], kind }] }),
      )
      expect(mapped.mediaHealth[0]?.kind).toBe(kind)
    }
    // A kind this build cannot render is a server it does not understand, not
    // a row to draw blank.
    const widened = { ...detail().media_health[0], kind: 'media_shredded' }
    await expect(readReport(detail({ media_health: [widened] }))).rejects.toMatchObject(
      refused('enum@/media_health/0/kind'),
    )
  })

  it('refuses a media-health observation with no track list', async () => {
    // `tracks` is required by the schema, so coercing an absent one to no
    // tracks would print an item-wide statement the server never made.
    const absent = {
      bound: 'The declared track sequence runs without a hole.',
      cited: ['track 1 declares sequence 1'],
      expected: null,
      kind: 'track_sequence_gap',
      observed: null,
    }
    await expect(readReport(detail({ media_health: [absent] }))).rejects.toMatchObject(
      refused('required@/media_health/0/tracks'),
    )
    const untracked = { ...detail().media_health[0], tracks: 2 }
    await expect(readReport(detail({ media_health: [untracked] }))).rejects.toMatchObject(
      refused('type@/media_health/0/tracks'),
    )
  })

  it('refuses a media-health observation that cites nothing', async () => {
    // Every observation names the evidence it read; an empty citation list is
    // a claim with nothing behind it, so the wire refuses it as the domain
    // type does.
    const uncited = { ...detail().media_health[0], cited: [] }
    await expect(readReport(detail({ media_health: [uncited] }))).rejects.toMatchObject(
      refused('min_items@/media_health/0/cited'),
    )
  })
})

describe('mapCaseDetail media health', () => {
  const observation = {
    bound: 'A complete recording runs within 1 min 30 s of the edition’s stated running time.',
    cited: ['local_abc123', 'audible-product-B0SYNE7P1K'],
    expected: '20 h stated by the matched edition',
    kind: 'runtime_short_of_provider',
    observed: '12 h of local audio, 8 h shorter than the matched edition',
    tracks: [],
  }
  const detail = async (media_health: unknown) => {
    const { default: payload } = await import('./descriptionAdviceFixture.json')
    return { ...payload, media_health }
  }

  it('maps the section the case view draws for an admitted audiobook', async () => {
    expect((await readCase(await detail([observation]))).mediaHealth).toEqual([observation])
    expect((await readCase(await detail([]))).mediaHealth).toEqual([])
  })

  it('refuses a case-detail media-health section that is not the closed shape', async () => {
    const absent = await detail(undefined)
    await expect(readCase(absent)).rejects.toMatchObject(refused('required@/media_health'))
    const untyped = await detail([{ ...observation, tracks: ['1'] }])
    await expect(readCase(untyped)).rejects.toMatchObject(refused('type@/media_health/0/tracks/0'))
    const widened = await detail([{ ...observation, kind: 'media_shredded' }])
    await expect(readCase(widened)).rejects.toMatchObject(refused('enum@/media_health/0/kind'))
  })

  it('refuses a case-detail observation that cites nothing', async () => {
    // An observation with no citation names a fault without saying what it
    // read, which is the one thing the domain type refuses to construct.
    const uncited = await detail([{ ...observation, cited: [] }])
    await expect(readCase(uncited)).rejects.toMatchObject(
      refused('min_items@/media_health/0/cited'),
    )
  })
})

describe('PageCsrfTokenStore', () => {
  it('accepts only bounded tokens from the renewed authenticated page', async () => {
    const store = new PageCsrfTokenStore(
      token,
      async () =>
        new Response(`<body data-csrf-token="${renewedToken}"></body>`, {
          status: 200,
          headers: { 'content-type': 'text/html' },
        }),
    )
    await expect(store.renew()).resolves.toBe(renewedToken)
    expect(store.current()).toBe(renewedToken)
  })
})

it('maps an actual persisted authored description separately from provider alternatives', async () => {
  const { default: payload } = await import('./descriptionAdviceFixture.json')
  const mapped = await readCase(payload)
  const description = mapped.fields.find((field) => field.destination === 'description')
  if (!description) throw new Error('description fixture requires its field')
  expect(description.descriptionAdvice?.candidate?.provenance).toBe('authored_content')
  expect(description.descriptionAdvice?.candidate?.advised).toBe(true)
  expect(description.descriptionAdvice?.candidate?.output.description).toBe(
    'Authored synthetic description.',
  )
  expect(description.descriptionAdvice?.current).toBe(true)
})

it('sends an ordinary chooser authored command only to the bound authored selection endpoint', async () => {
  const { default: payload } = await import('./descriptionAdviceFixture.json')
  const field = (await readCase(payload)).fields.find((item) => item.destination === 'description')
  if (!field?.descriptionAdvice?.candidate)
    throw new Error('description fixture requires its candidate')
  const advice = field.descriptionAdvice
  const candidate = field.descriptionAdvice.candidate
  // A rotated token is renewed and the keyed selection is sent once more (#1018).
  const fetchMock = vi
    .fn<typeof fetch>(async () =>
      Response.json({
        schema_version: 'description-selection-ack-v1',
        confirmation: { status: 'confirmed', case: { ...payload, review_case_id: 'case-1' } },
        decision_id: 'human_decision_' + 'a'.repeat(64),
        advised: true,
        invalidation_sequences: [],
      }),
    )
    .mockResolvedValueOnce(Response.json({ error: { code: 'csrf' } }, { status: 403 }))
  const client = new ReviewApiClient({ csrf, fetch: fetchMock, buildIdentity: 'c'.repeat(64) })
  await client.recordDecision('case-1', {
    destination: 'description',
    outcome: 'accept',
    proposalId: field.proposalId,
    targetFingerprint: field.targetFingerprint,
    offerDigest: field.offerDigest,
    selectedValue: { kind: 'text', value: candidate.output.description },
    idempotencyKey: 'owner-choice-idempotency',
    authoredDescription: { candidateId: candidate.candidate_id, binding: advice.binding },
  })
  expect(fetchMock).toHaveBeenCalledTimes(2)
  for (const call of fetchMock.mock.calls) {
    if (!call[1]) throw new Error('description request was not sent')
    const [url, options] = call
    expect(url).toBe('/api/v1/review-cases/case-1/description-advice/selection')
    expect(JSON.parse(options.body as string)).toEqual({
      candidate_id: candidate.candidate_id,
      binding: advice.binding,
    })
    expect(new Headers(options.headers).get('idempotency-key')).toBe('owner-choice-idempotency')
  }
})

it('binds a choice to the offer the field showed (#1355)', async () => {
  const { default: payload } = await import('./descriptionAdviceFixture.json')
  const served = payload.fields.find((item) => item.destination === 'series')
  const field = (await readCase(payload)).fields.find((item) => item.destination === 'series')
  if (!served || !field) throw new Error('fixture requires its series field')
  expect(field.offerDigest).toBe(served.offer_digest)
  const fetchMock = vi.fn<typeof fetch>(async () => Response.json({}, { status: 500 }))
  const client = new ReviewApiClient({ csrf, fetch: fetchMock, buildIdentity: 'c'.repeat(64) })
  await client
    .recordDecision('case-1', {
      destination: 'series',
      outcome: 'keep_current',
      proposalId: field.proposalId,
      targetFingerprint: field.targetFingerprint,
      offerDigest: field.offerDigest,
      idempotencyKey: 'offer-bound-choice',
    })
    .catch(() => undefined)
  const options = fetchMock.mock.calls[0]?.[1]
  if (!options) throw new Error('decision request was not sent')
  expect(JSON.parse(options.body as string)).toMatchObject({
    offered_alternatives_digest: served.offer_digest,
    target_fingerprint: served.target_fingerprint,
  })
})

it('reads a case waiting for its check and a value offered for title and author (#1355)', async () => {
  const { default: payload } = await import('./descriptionAdviceFixture.json')
  const changed = {
    ...payload,
    checking: true,
    check_given_up: true,
    fields: payload.fields.map((field) =>
      field.destination === 'series'
        ? {
            ...field,
            choice_contexts: field.choice_contexts.map((context) =>
              context.selection === 'proposed'
                ? { ...context, match_basis: 'title_and_author' }
                : context,
            ),
          }
        : field,
    ),
  }
  const read = await readCase(changed)
  expect(read.checking).toBe(true)
  expect(read.checkGivenUp).toBe(true)
  const series = read.fields.find((item) => item.destination === 'series')
  if (!series) throw new Error('fixture requires its series field')
  expect(series.choiceContexts.map((context) => context.matchBasis)).toEqual(
    series.choiceContexts.map((context) =>
      context.selection === 'proposed' ? 'title_and_author' : null,
    ),
  )
  expect((await readCase(payload)).checking).toBe(false)
})

it.each(['digest_mismatch', 'unreconciled_case'])(
  'carries why a choice was refused as offer_changed (%s)',
  async (reason) => {
    const fetchMock = vi.fn<typeof fetch>(async () =>
      Response.json(
        { schema_version: 'review-error-v1', error: { code: 'offer_changed', reason } },
        { status: 409 },
      ),
    )
    const client = new ReviewApiClient({ csrf, fetch: fetchMock, buildIdentity: 'c'.repeat(64) })
    const refused = await client
      .recordDecision('case-1', {
        destination: 'series',
        outcome: 'keep_current',
        proposalId: 'proposal-1',
        targetFingerprint: 'fingerprint-1',
        offerDigest: 'offer-digest',
        idempotencyKey: 'offer-changed-choice',
      })
      .then(
        () => null,
        (error: unknown) => error,
      )
    expect(refused).toBeInstanceOf(ApiError)
    expect(refused).toMatchObject({ status: 409, code: 'offer_changed', reason })
  },
)

it.each([undefined, 'applying', 'verified'])(
  'rejects an unbound or invalid description application outcome %s',
  async (outcome) => {
    const { default: payload } = await import('./descriptionAdviceFixture.json')
    const changed = {
      ...payload,
      fields: payload.fields.map((field) =>
        field.destination === 'description'
          ? {
              ...field,
              description_advice: { ...field.description_advice, application_outcome: outcome },
            }
          : field,
      ),
    }
    await expect(readCase(changed)).rejects.toThrow(ApiError)
  },
)

it.each([
  { failure: ['invalid_output'] },
  { failure: { failure: 'invalid_output' } },
  { failure: 1 },
])('rejects non-string description failure values without coercion: %j', async ({ failure }) => {
  const { default: payload } = await import('./descriptionAdviceFixture.json')
  const changed = {
    ...payload,
    fields: payload.fields.map((field) =>
      field.destination === 'description'
        ? { ...field, description_advice: { ...field.description_advice, failure } }
        : field,
    ),
  }
  await expect(readCase(changed)).rejects.toThrow(ApiError)
})

it.each([
  // X49: a candidate's description is not blank.
  [{ output: { description: '   ' } }, 'invalid_description_text'],
  // X50: the candidate binds the advice's attempt, engine and model.
  [{ model: 'another-model' }, 'invalid_description_candidate_binding'],
] as const)('refuses a description candidate the schema admits: %j', async (change, code) => {
  const { default: payload } = await import('./descriptionAdviceFixture.json')
  const changed = {
    ...payload,
    fields: payload.fields.map((field) => {
      if (field.destination !== 'description' || !field.description_advice?.candidate) return field
      const { candidate } = field.description_advice
      return {
        ...field,
        description_advice: {
          ...field.description_advice,
          candidate: {
            ...candidate,
            ...change,
            output: { ...candidate.output, ...('output' in change ? change.output : {}) },
          },
        },
      }
    }),
  }
  await expect(readCase(changed)).rejects.toThrow(new ApiError(502, code))
})

it.each(['selected', 'requested', 'running', 'failed', 'interrupted', 'succeeded'] as const)(
  'parses actual serialized retained selection during %s advice',
  async (state) => {
    const { default: base } = await import('./descriptionAdviceFixture.json')
    const { default: retries } = await import('./descriptionAdviceRetryFixture.json')
    const payload = {
      ...base,
      fields: base.fields.map((field) =>
        field.destination === 'description'
          ? { ...field, description_advice: retries[state] }
          : field,
      ),
    }
    const advice = (await readCase(payload)).fields.find(
      (field) => field.destination === 'description',
    )?.descriptionAdvice
    expect(advice?.selection?.candidate_id).toBe(retries.selected.candidate.candidate_id)
    expect(advice?.selected_advice?.current).toBe(true)
    expect(advice?.state).toBe(retries[state].state)
    if (state === 'succeeded')
      expect(advice?.candidate?.candidate_id).not.toBe(advice?.selection?.candidate_id)
  },
)

it.each([
  'missing-snapshot',
  'missing-selection',
  'candidate',
  'binding',
  'shape',
  'current',
  'outcome',
] as const)('rejects incoherent selected advice %s', async (fault) => {
  const { default: base } = await import('./descriptionAdviceFixture.json')
  const { default: retries } = await import('./descriptionAdviceRetryFixture.json')
  const original = retries.requested
  const selected = original.selected_advice
  const advice = {
    ...original,
    ...(fault === 'missing-snapshot' ? { selected_advice: null } : {}),
    ...(fault === 'missing-selection' ? { selection: null } : {}),
    ...(fault === 'candidate'
      ? {
          selected_advice: {
            ...selected,
            candidate: {
              ...selected.candidate,
              candidate_id: `description_candidate_${'f'.repeat(64)}`,
            },
          },
        }
      : {}),
    ...(fault === 'binding'
      ? { selected_advice: { ...selected, binding: { ...selected.binding, access_revision: 99 } } }
      : {}),
    ...(fault === 'shape' ? { selected_advice: { ...selected, extra: true } } : {}),
    ...(fault === 'current' ? { selected_advice: { ...selected, current: 'true' } } : {}),
    ...(fault === 'outcome'
      ? { selection: null, selected_advice: null, application_outcome: 'verified' }
      : {}),
  }
  await expect(
    readCase({
      ...base,
      fields: base.fields.map((field) =>
        field.destination === 'description' ? { ...field, description_advice: advice } : field,
      ),
    }),
  ).rejects.toThrow(ApiError)
})

it('validates the closed decision confirmation and maps its case exactly once', async () => {
  const { default: payload } = await import('./descriptionAdviceFixture.json')
  const command = {
    destination: 'title',
    outcome: 'keep_current' as const,
    proposalId: 'proposal-1',
    targetFingerprint: 'fingerprint-1',
    offerDigest: 'offer-digest',
    idempotencyKey: 'same-decision',
  }
  const casePayload = { ...payload, review_case_id: 'case-1', current: false }
  // An acknowledgement the browser cannot read is uncertain, never retryable:
  // a same-key Retry would replay the same answer (#1688).
  for (const [confirmation, failure] of [
    [{ status: 'other' }, 'discriminator@/confirmation/status'],
    [{ status: 'unavailable', case: casePayload }, 'unknown_key@/confirmation/case'],
    [{ status: 'confirmed' }, 'required@/confirmation/case'],
    [
      { status: 'confirmed', case: { ...casePayload, current: 'yes' } },
      'type@/confirmation/case/current',
    ],
    [
      { status: 'confirmed', case: { ...casePayload, review_case_id: 'another-case' } },
      'invariant@invalid_decision_confirmation',
    ],
    [{ status: 'confirmed', case: casePayload, extra: true }, 'unknown_key@/confirmation/extra'],
  ] as const) {
    const client = new ReviewApiClient({
      csrf,
      buildIdentity: 'c'.repeat(64),
      fetch: vi.fn(async () => Response.json(decisionAcknowledgement({ confirmation }))),
    })
    const refusal = await client.recordDecision('case-1', command).catch((e: unknown) => e)
    expect(refusal).toBeInstanceOf(UncertainMutationError)
    expect(refusal).toMatchObject(uncertain(failure))
  }
  const client = new ReviewApiClient({
    csrf,
    buildIdentity: 'c'.repeat(64),
    fetch: vi.fn(async () =>
      Response.json(
        decisionAcknowledgement({ confirmation: { status: 'confirmed', case: casePayload } }),
      ),
    ),
  })
  const result = await client.recordDecision('case-1', command)
  expect(result.confirmation).toEqual({ status: 'confirmed', case: await readCase(casePayload) })
  if (result.confirmation.status !== 'confirmed') throw new Error('confirmed response required')
  expect(result.confirmation.case.current).toBe(false)
})

// A write committed while the live channel is closed publishes nothing, so its
// acknowledgement names no position: the browser accepts the empty list, and
// keeps refusing 0, which no frame ever carries (#1674).
it.each(['decisions', 'description-advice/selection'])(
  'accepts an acknowledgement naming no position and refuses position 0 from %s',
  async (route) => {
    const { default: payload } = await import('./descriptionAdviceFixture.json')
    const field = (await readCase(payload)).fields.find(
      (item) => item.destination === 'description',
    )
    if (!field?.descriptionAdvice?.candidate)
      throw new Error('description fixture requires its candidate')
    const authored = route === 'description-advice/selection'
    const command = {
      destination: 'description',
      outcome: 'accept' as const,
      proposalId: field.proposalId,
      targetFingerprint: field.targetFingerprint,
      offerDigest: field.offerDigest,
      selectedValue: {
        kind: 'text' as const,
        value: field.descriptionAdvice.candidate.output.description,
      },
      idempotencyKey: 'closed-channel-idempotency',
      ...(authored
        ? {
            authoredDescription: {
              candidateId: field.descriptionAdvice.candidate.candidate_id,
              binding: field.descriptionAdvice.binding,
            },
          }
        : {}),
    }
    const casePayload = { ...payload, review_case_id: 'case-1' }
    const acknowledgement = (invalidation_sequences: number[]) =>
      authored
        ? {
            schema_version: 'description-selection-ack-v1',
            confirmation: { status: 'confirmed', case: casePayload },
            decision_id: 'human_decision_' + 'a'.repeat(64),
            advised: true,
            invalidation_sequences,
          }
        : decisionAcknowledgement({
            confirmation: { status: 'confirmed', case: casePayload },
            invalidation_sequences,
          })
    const client = (invalidation_sequences: number[]) => {
      const fetchMock = vi.fn<typeof fetch>(async () =>
        Response.json(acknowledgement(invalidation_sequences)),
      )
      return {
        fetchMock,
        api: new ReviewApiClient({ csrf, fetch: fetchMock, buildIdentity: 'c'.repeat(64) }),
      }
    }
    const accepted = client([])
    const result = await accepted.api.recordDecision('case-1', command)
    expect(accepted.fetchMock.mock.calls[0]?.[0]).toBe(`/api/v1/review-cases/case-1/${route}`)
    expect(result.invalidation_sequences).toEqual([])
    expect(result.confirmation.status).toBe('confirmed')
    for (const sequences of [[0], [1, 0]]) {
      const refused = client(sequences)
      const failure = await refused.api.recordDecision('case-1', command).catch((e: unknown) => e)
      // The schema's minimum refuses position 0, so the write is uncertain.
      expect(failure).toBeInstanceOf(UncertainMutationError)
      expect(failure).toMatchObject(
        uncertain(`min@/invalidation_sequences/${String(sequences.length - 1)}`),
      )
    }
  },
)

it('sends the actual read scheduling hint while ordinary reads remain foreground', async () => {
  const { default: payload } = await import('./descriptionAdviceFixture.json')
  const fetchMock = vi.fn<typeof fetch>(async () => Response.json(payload))
  const client = new ReviewApiClient({ csrf, fetch: fetchMock })
  await client.getCaseDetail(payload.review_case_id)
  await client.getCaseDetail(payload.review_case_id, undefined, 'background')
  expect(
    fetchMock.mock.calls.map((call) => new Headers(call[1]?.headers).get('X-ML-Work-Class')),
  ).toEqual(['foreground', 'background'])
})

it('requires an exact recovery flag in every preparation response', async () => {
  const response = preparation({ actionable: true })
  for (const recovery_required of [undefined, null, 0, 1, 'false', {}, []]) {
    await expect(readPreparation({ ...response, recovery_required })).rejects.toMatchObject(
      refused(
        recovery_required === undefined ? 'required@/recovery_required' : 'type@/recovery_required',
      ),
    )
  }
  expect((await readPreparation({ ...response, recovery_required: true })).recovery_required).toBe(
    true,
  )
})

it.each(['invalid JSON', 'interrupted body'])(
  'keeps a successful mutation with %s uncertain without resending',
  async (failure) => {
    const response = new Response('{', { status: 200 })
    if (failure === 'interrupted body')
      vi.spyOn(response, 'json').mockRejectedValue(new TypeError('body interrupted'))
    const fetchMock = vi.fn<typeof fetch>(async () => response)
    const client = new ReviewApiClient({ csrf, fetch: fetchMock, buildIdentity: 'c'.repeat(64) })
    await expect(
      client.recordDecision('case-1', {
        destination: 'cover',
        outcome: 'accept',
        proposalId: 'proposal-1',
        targetFingerprint: 'fingerprint-1',
        offerDigest: 'offer-digest',
        idempotencyKey: 'owner-choice-idempotency',
        selectedValue: { kind: 'cover', value: `cover_${'a'.repeat(64)}` },
        considerationId: `cover_consideration_${'b'.repeat(64)}`,
        coverConfirmation: { expected_selection_id: null, confirm_unverified_match: true },
      }),
    ).rejects.toBeInstanceOf(AmbiguousMutationError)
    expect(fetchMock).toHaveBeenCalledOnce()
    const readResponse = new Response('{', { status: 200 })
    if (failure === 'interrupted body')
      vi.spyOn(readResponse, 'json').mockRejectedValue(new TypeError('body interrupted'))
    fetchMock.mockResolvedValue(readResponse)
    await expect(client.getCaseDetail('case-1')).rejects.toMatchObject({
      code: 'invalid_json_response',
      status: 502,
    })
    expect(fetchMock).toHaveBeenCalledTimes(2)
  },
)

it.each([null, '0', '0.000001'])(
  'admits independent cover price %s bound to the saved model',
  async (price) => {
    const metadata = {
      model: 'example/model',
      observed_at: 1,
      prompt_price: price,
      completion_price: null,
      image_price: '0',
      request_price: null,
    }
    const value = reviewerAccessPayload({
      mapping: { engine: 'openrouter', model: 'example/model' },
      cover_metadata: metadata,
    })
    expect((await readReviewerAccess(value)).cover_metadata).toEqual(metadata)
    // The binding and the price grammar are named checks (ADR-0091).
    await expect(
      readReviewerAccess({ ...value, mapping: { engine: 'openrouter', model: 'example/other' } }),
    ).rejects.toMatchObject({ status: 502, code: 'invalid_cover_metadata' })
    await expect(
      readReviewerAccess({ ...value, cover_metadata: { ...metadata, image_price: 'NaN' } }),
    ).rejects.toMatchObject({ status: 502, code: 'invalid_cover_metadata' })
    await expect(
      readReviewerAccess({
        ...value,
        cost_evidence: {
          models: [],
          private: 'forbidden',
        },
      }),
    ).rejects.toMatchObject(refused('unknown_key@/cost_evidence/private'))
  },
)

describe('reviewer cost evidence', () => {
  const run = { source_head: 'a'.repeat(40), cohort_sha256: 'b'.repeat(64), observed_at: 1 }
  const row = {
    model: 'openai/gpt-6-luna',
    task: 'cover',
    requests_selected: 100,
    requests_attempted: 100,
    requests_completed: 100,
    requests_failed: 0,
    works: 100,
    distinct_images: 270,
    total_cost_usd: '0.063641500',
    per_1000_usd: '0.636415000',
    measured_run: run,
  }
  const evidence = (cost_evidence: unknown) =>
    readReviewerAccess(reviewerAccessPayload({ cost_evidence }))
  const valid = { models: [row] }

  it('admits per-row cost runs and refuses a missing row run or a top-level run', async () => {
    expect((await evidence(valid)).cost_evidence).toEqual(valid)
    const missing = Object.fromEntries(
      Object.entries(row).filter(([key]) => key !== 'measured_run'),
    )
    const at = '/cost_evidence'
    for (const [invalid, failure] of [
      [{ ...valid, models: [missing] }, `required@${at}/models/0/measured_run`],
      [{ ...valid, models: [{ ...row, measured_run: null }] }, `type@${at}/models/0/measured_run`],
      [
        { ...valid, models: [{ ...row, measured_run: { ...run, source_head: 'A'.repeat(40) } }] },
        `pattern@${at}/models/0/measured_run/source_head`,
      ],
      [{ ...valid, measured_run: run }, `unknown_key@${at}/measured_run`],
      [{ ...valid, schema_version: 'any' }, `unknown_key@${at}/schema_version`],
    ] as const)
      await expect(evidence(invalid)).rejects.toMatchObject(refused(failure))
  })

  // The accounting (X44), its prices (X45) and one row per model and task
  // (X46) stay with the mapper (ADR-0091).
  it.each([
    ['attempted differs from selected', [{ ...row, requests_attempted: 99 }]],
    ['completed and failed miss attempted', [{ ...row, requests_failed: 1 }]],
    ['more works than requests', [{ ...row, requests_selected: 100, works: 101 }]],
    ['description images', [{ ...row, task: 'description', distinct_images: 1 }]],
    ['an unbounded price', [{ ...row, total_cost_usd: '1e9', per_1000_usd: '1e10' }]],
    ['a per-1000 cost off its total', [{ ...row, per_1000_usd: '0.7' }]],
    ['a repeated model and task', [row, row]],
  ])('refuses %s with the named check', async (_label, models) => {
    await expect(evidence({ ...valid, models })).rejects.toMatchObject({
      status: 502,
      code: 'invalid_cost_evidence',
    })
  })
})

describe('preparation scan identity', () => {
  const active = scanProgress({
    scan_id: 'scan-1',
    library: 'books',
    library_id: 'books',
    state: 'discovering',
  })
  const idle = {
    ...active,
    scan_id: null,
    library: 'new-books',
    library_id: 'new-books',
    state: 'idle',
  }
  const envelope = preparation({ active: true, libraries: [active, idle] })

  it('retains an active scan beside a library that has never been requested', async () => {
    expect((await readPreparation(envelope)).libraries).toEqual([active, idle])
  })

  it('refuses a scan state outside the closed set (#1286 C6)', async () => {
    await expect(
      readPreparation({ ...envelope, libraries: [active, { ...idle, state: 'paused' }] }),
    ).rejects.toMatchObject(refused('enum@/libraries/1/state'))
  })

  it.each([
    ['missing', {}, 'required@/libraries/1/scan_id'],
    ['undefined', { scan_id: undefined }, 'required@/libraries/1/scan_id'],
    ['number', { scan_id: 1 }, 'type@/libraries/1/scan_id'],
    ['boolean', { scan_id: false }, 'type@/libraries/1/scan_id'],
    ['object', { scan_id: {} }, 'type@/libraries/1/scan_id'],
    ['array', { scan_id: [] }, 'type@/libraries/1/scan_id'],
  ])('refuses a %s scan identity at the decoder', async (_name, identity, failure) => {
    const withoutIdentity: Record<string, unknown> = { ...idle }
    delete withoutIdentity.scan_id
    await expect(
      readPreparation({
        ...envelope,
        libraries: [active, { ...withoutIdentity, ...identity }],
      }),
    ).rejects.toMatchObject(refused(failure))
  })
})

describe('Activity check group (#1281)', () => {
  const key = `report_${'c'.repeat(64)}`
  const withGroup = (value: unknown) => ({
    ...currentActivity,
    events: currentActivity.events.map((event) => {
      // The captured events carry the key; the absent case removes it.
      const copy: Record<string, unknown> = { ...event }
      delete copy.check_group
      return value === undefined ? copy : { ...copy, check_group: value }
    }),
  })
  it.each([
    ['null', null, null],
    ['a preparation key', key, key],
  ])('maps %s to the event view', async (_name, value, expected) => {
    const { events } = await readActivity(withGroup(value))
    expect(events.length).toBeGreaterThan(0)
    expect(events.every((event) => event.check_group === expected)).toBe(true)
  })
  // The decoder admits no default, so an absent key is refused rather than
  // read as null (T1, #1691).
  it.each([
    ['an absent key', undefined, 'required@/events/0/check_group'],
    ['a key that is not a string', 7, 'type@/events/0/check_group'],
  ])('refuses %s', async (_name, value, failure) => {
    await expect(readActivity(withGroup(value))).rejects.toMatchObject(refused(failure))
  })
  it('maps the captured fixture, whose events all carry the key', async () => {
    expect(currentActivity.events.every((event) => 'check_group' in event)).toBe(true)
    const { events } = await readActivity(currentActivity)
    expect(events.length).toBe(currentActivity.events.length)
  })
})

describe('write plan file (#1336)', () => {
  const encoder = new TextEncoder()
  const streamed = (chunks: string[], fault?: Error) =>
    new ReadableStream<Uint8Array>({
      start(controller) {
        for (const chunk of chunks) controller.enqueue(encoder.encode(chunk))
        if (fault) controller.error(fault)
        else controller.close()
      },
    })

  it('fetches the file once, counts rows as they arrive and keeps its bytes', async () => {
    const chunks = ['{"changes":[\n', '{"work":"A"}\n,{"work":"B"}\n', '],"listed":2}']
    const fetchMock = vi.fn<typeof fetch>(
      async () =>
        new Response(streamed(chunks), {
          status: 200,
          headers: { 'content-type': 'application/json' },
        }),
    )
    const rows: number[] = []

    const file = await new ReviewApiClient({ csrf, fetch: fetchMock }).downloadActivityWritePlan(
      'json',
      undefined,
      (count) => rows.push(count),
    )

    expect(fetchMock).toHaveBeenCalledOnce()
    const [path, init] = fetchMock.mock.calls[0] ?? []
    expect(path).toBe('/api/v1/activity/write-plan?format=json')
    expect(init).toMatchObject({ credentials: 'same-origin', cache: 'no-store' })
    expect(init?.method ?? 'GET').toBe('GET')
    expect(rows).toEqual([0, 2, 2])
    expect(file.filename).toBe('mechanical-librarian-write-plan.json')
    expect(file.blob.type).toBe('application/json')
    expect(await file.blob.text()).toBe(chunks.join(''))
  })

  it('reads the whole body when it cannot be streamed', async () => {
    const response = new Response('"Library"\r\n', { status: 200 })
    Object.defineProperty(response, 'body', { value: null })
    const file = await new ReviewApiClient({
      csrf,
      fetch: vi.fn<typeof fetch>(async () => response),
    }).downloadActivityWritePlan('csv')

    expect(file.filename).toBe('mechanical-librarian-write-plan.csv')
    expect(await file.blob.text()).toBe('"Library"\r\n')
  })

  it('never returns a file whose connection ended before its end', async () => {
    const client = new ReviewApiClient({
      csrf,
      fetch: vi.fn<typeof fetch>(
        async () => new Response(streamed(['"Library"\r\n'], new TypeError('network'))),
      ),
    })

    await expect(client.downloadActivityWritePlan('csv')).rejects.toBeInstanceOf(
      NetworkUnavailableError,
    )
  })

  it.each([
    [
      'session expiry',
      () => Response.json({ error: { code: 'session' } }, { status: 401 }),
      SessionExpiredError,
    ],
    [
      'an earlier build',
      () => new Response('x', { status: 200, headers: { 'x-ml-build-identity': 'e'.repeat(64) } }),
      BuildMismatchError,
    ],
    [
      'an unavailable plan',
      () => Response.json({ error: { code: 'unavailable' } }, { status: 503 }),
      ApiError,
    ],
  ])('refuses %s as every read does', async (_name, answer, refusal) => {
    const client = new ReviewApiClient({
      csrf,
      buildIdentity: 'c'.repeat(64),
      fetch: vi.fn<typeof fetch>(async () => answer()),
    })

    await expect(client.downloadActivityWritePlan('csv')).rejects.toBeInstanceOf(refusal)
  })
})

describe('deferred Activity permission contract', () => {
  it('refuses a genuine earlier producer and requires both nullable fields', async () => {
    await expect(readActivity(activityV2Predecessor)).rejects.toBeInstanceOf(ResponseDecodeError)
    expect((await readActivity(currentActivity)).backup_ready).toBeNull()
    expect((await readActivity(currentActivity)).example).toBeNull()
    for (const field of ['backup_ready', 'example']) {
      const missing = Object.fromEntries(
        Object.entries(currentActivity).filter(([key]) => key !== field),
      )
      await expect(readActivity(missing)).rejects.toMatchObject(refused(`required@/${field}`))
    }
  })
  it.each([
    ['backup_ready', true],
    ['backup_ready', false],
    [
      'example',
      {
        after: 'After',
        before: 'Before',
        destination: 'title',
        library: 'Fiction',
        work_id: 'work-1',
      },
    ],
  ])('rejects non-null history %s', async (field, value) => {
    await expect(readActivity({ ...currentActivity, [field]: value })).rejects.toMatchObject(
      refused(`type@/${field}`),
    )
  })
  it.each([true, false, null])(
    'reads only the narrow permission response with readiness %s',
    async (backup_ready) => {
      const response = {
        backup_ready,
        capabilities: [],
        configured_libraries: [],
        prepared: { automatic: 0, human: 1, total: 1 },
        plan: {
          total: 1,
          groups: [
            {
              library: 'Fiction',
              destination: 'title',
              kind: 'human',
              count: 1,
              rows: [
                {
                  work: 'Real Tigers',
                  authors: 'Mick Herron',
                  current: 'Not set',
                  new: 'Real Tigers',
                  reason: 'You chose this value',
                  sources: ['Hardcover'],
                },
              ],
            },
          ],
        },
      }
      expect(await readPermissionReadiness(response)).toEqual(response)
      // The plan is required, its kinds are closed and a row states no identity.
      await expect(readPermissionReadiness({ ...response, plan: undefined })).rejects.toMatchObject(
        refused('required@/plan'),
      )
      await expect(readPermissionReadiness({ ...response, example: null })).rejects.toMatchObject(
        refused('unknown_key@/example'),
      )
      const [group] = response.plan.groups as [(typeof response.plan.groups)[number]]
      await expect(
        readPermissionReadiness({
          ...response,
          plan: { ...response.plan, groups: [{ ...group, kind: 'policy' }] },
        }),
      ).rejects.toMatchObject({ name: 'ResponseDecodeError' })
      await expect(
        readPermissionReadiness({
          ...response,
          plan: {
            ...response.plan,
            groups: [{ ...group, rows: [{ ...group.rows[0], work_id: 'work-1' }] }],
          },
        }),
      ).rejects.toMatchObject({ name: 'ResponseDecodeError' })
      await expect(readPermissionReadiness({ ...response, events: [] })).rejects.toMatchObject(
        refused('unknown_key@/events'),
      )
      await expect(
        readPermissionReadiness({ ...response, backup_ready: undefined }),
      ).rejects.toMatchObject(refused('required@/backup_ready'))
    },
  )

  it('reads the narrow consent response as exactly what Activity carries (#1236)', async () => {
    // Distinct counts, so no reason can be dropped or swapped unseen.
    const waiting = {
      applying: 1,
      awaiting_permission: 2,
      held_for_recovery: 3,
      retrying: 4,
      scheduled: 5,
    }
    const { backup_ready, capabilities, configured_libraries } = currentActivity
    const lapsed_consents = [
      { connection: 'primary', library: 'Fiction', reason: 'destinations_changed' },
    ]
    const response = { backup_ready, capabilities, configured_libraries, lapsed_consents, waiting }
    const history = await readActivity({ ...currentActivity, lapsed_consents, waiting })
    expect(await readActivityConsent(response)).toEqual({
      backup_ready: history.backup_ready,
      capabilities: history.capabilities,
      configured_libraries: history.configured_libraries,
      lapsed_consents: history.lapsed_consents,
      waiting: history.waiting,
    })
    expect(history.lapsed_consents).toEqual(lapsed_consents)
    for (const key of Object.keys(response))
      await expect(
        readActivityConsent(
          Object.fromEntries(Object.entries(response).filter(([k]) => k !== key)),
        ),
      ).rejects.toMatchObject(refused(`required@/${key}`))
    for (const [invalid, failure] of [
      [{ ...response, events: [] }, 'unknown_key@/events'],
      [{ ...response, backup_ready: true }, 'type@/backup_ready'],
      [{ ...response, capabilities: {} }, 'type@/capabilities'],
      [
        { ...response, configured_libraries: [{ connection: 'primary' }] },
        'required@/configured_libraries/0/library',
      ],
      [{ ...response, waiting: { ...waiting, scheduled: -1 } }, 'min@/waiting/scheduled'],
      [{ ...response, waiting: { ...waiting, applying: undefined } }, 'required@/waiting/applying'],
      [{ ...response, waiting: null }, 'type@/waiting'],
      // A lapse is closed: three reasons, three keys, nothing else (#1370).
      [{ ...response, lapsed_consents: null }, 'type@/lapsed_consents'],
      [
        { ...response, lapsed_consents: [{ ...lapsed_consents[0], reason: 'expired' }] },
        'enum@/lapsed_consents/0/reason',
      ],
      [
        { ...response, lapsed_consents: [{ ...lapsed_consents[0], basis: 'owner_grant' }] },
        'unknown_key@/lapsed_consents/0/basis',
      ],
      [
        { ...response, lapsed_consents: [{ connection: 'primary', library: 'Fiction' }] },
        'required@/lapsed_consents/0/reason',
      ],
    ] as const)
      await expect(readActivityConsent(invalid)).rejects.toMatchObject(refused(failure))
    await expect(
      readActivity({ ...currentActivity, lapsed_consents: undefined }),
    ).rejects.toMatchObject(refused('required@/lapsed_consents'))
    // A waiting value that is not a record is refused as such by both reads.
    for (const waiting of [null, [], 'scheduled'])
      for (const read of [
        () => readActivity({ ...currentActivity, waiting }),
        () => readActivityConsent({ ...response, waiting }),
      ])
        await expect(read()).rejects.toMatchObject(refused('type@/waiting'))
    const fetchMock = vi.fn<typeof fetch>(async () => Response.json(response))
    const client = new ReviewApiClient({ csrf, fetch: fetchMock, buildIdentity: 'c'.repeat(64) })
    expect(await client.getActivityConsent()).toEqual(await readActivityConsent(response))
    expect(fetchMock.mock.calls[0]?.[0]).toBe('/api/v1/activity/consent-state')
  })
})

describe('signInLocation', () => {
  it('carries the current page and query as the return path (#1371)', () => {
    expect(signInLocation({ pathname: '/review/case-1', search: '?field=title' })).toBe(
      '/login?next=%2Freview%2Fcase-1%3Ffield%3Dtitle',
    )
    expect(signInLocation({ pathname: '/library', search: '' })).toBe('/login?next=%2Flibrary')
  })
})

describe('uncertainWriteOutcome', () => {
  it.each([
    ['a lost or unreadable response', new AmbiguousMutationError()],
    ["Starlette's text 500", new ApiError(500, 'invalid_error_response')],
    ['503 persistence', new ApiError(503, 'persistence')],
    ['503 unavailable', new ApiError(503, 'unavailable')],
    ['503 reviewer_access_unavailable', new ApiError(503, 'reviewer_access_unavailable')],
    ['a client mapper refusal after a 2xx write', new ApiError(502, 'invalid_x')],
  ])('treats %s as possibly committed', (_name, error) => {
    expect(uncertainWriteOutcome(error)).toBe(true)
  })

  it.each([
    ['the pre-call build refusal', new ApiError(503, 'react_assets_unavailable')],
    ['a renewed CSRF token', new CsrfRenewedError()],
    ['a stale refusal', new ApiError(409, 'stale')],
    ['a validation refusal', new ApiError(422, 'automatic_unavailable')],
    ['a plain error', new Error('network')],
  ])('treats %s as nothing changed', (_name, error) => {
    expect(uncertainWriteOutcome(error)).toBe(false)
  })

  it('allow-lists only the code the session dependency raises before a handler runs', () => {
    expect([...PRE_CALL_SERVER_CODES]).toEqual(['react_assets_unavailable'])
  })
})

describe('central response decoding (#1685)', () => {
  const answering = (body: unknown, status = 200) => {
    const fetchMock = vi.fn<typeof fetch>(async () =>
      body === undefined ? new Response(null, { status }) : Response.json(body, { status }),
    )
    return new ReviewApiClient({ csrf, fetch: fetchMock, buildIdentity: 'c'.repeat(64) })
  }
  const undecodable = preparation({ libraries: [{}] })
  const progressPaths: PreparationActionPath[] = ['primary/Books/scan', 'primary/Books/retry']
  const viewPaths: PreparationActionPath[] = [
    'first-boot/acknowledge',
    'pause',
    'resume',
    'primary/check-connection',
  ]
  const operation = (path: string) =>
    `POST /api/v1/review-preparation/${path
      .replace(/^primary\/Books\//u, '{connection}/{library_name}/')
      .replace(/^primary\//u, '{connection}/')}`

  it('reports an undecodable read as a 502 with its reason and pointer in properties', async () => {
    const failure = await answering(undecodable)
      .getPreparation()
      .catch((error: unknown) => error)
    expect(failure).toBeInstanceOf(ResponseDecodeError)
    expect(failure).toBeInstanceOf(ApiError)
    expect(failure).not.toBeInstanceOf(AmbiguousMutationError)
    expect(failure).toMatchObject({
      status: 502,
      code: 'invalid_response',
      operation: 'GET /api/v1/review-preparation',
      responseStatus: 200,
      reason: 'required',
      at: '/libraries/0/blocked',
    })
    expect((failure as Error).message).not.toContain('/')
    expect(uncertainWriteOutcome(failure)).toBe(true)
  })

  it.each([
    [
      'GET /api/v1/library',
      (client: ReviewApiClient) => client.getLibrary({ lens: 'all' }),
      library({ items: [libraryWork({ cover: undefined })] }),
      '/items/0/cover',
    ],
    [
      'GET /api/v1/library/report',
      (client: ReviewApiClient) =>
        client.getReportDetail('/api/v1/library/report?connection=primary&item=item-9'),
      reportDetail({ chapters_truncated: undefined }),
      '/chapters_truncated',
    ],
    [
      'GET /api/v1/attention',
      (client: ReviewApiClient) => client.getAttention({ q: 'dates', cursor: 'next' }),
      attention({ items: [attentionItem({ classification_reason: undefined })] }),
      '/items/0/classification_reason',
    ],
    [
      'GET /api/v1/review-cases/{review_case_id}',
      (client: ReviewApiClient) => client.getCaseDetail('case-1'),
      caseDetail({ writes_consented: undefined }),
      '/writes_consented',
    ],
    [
      'GET /api/v1/review-cases/{review_case_id}/evidence',
      (client: ReviewApiClient) => client.getEvidence('case-1'),
      {
        items: [],
        missing_lineage_count: 0,
        next_cursor: null,
        review_case_id: 'case-1',
        schema_version: 'review-case-evidence-v6',
      },
      '/source_ledger',
    ],
    [
      'GET /api/v1/review-cases/{review_case_id}/history',
      (client: ReviewApiClient) => client.getHistory('case-1'),
      caseHistory({ next_cursor: undefined }),
      '/next_cursor',
    ],
    [
      'GET /api/v1/review-cases/{review_case_id}/successor',
      (client: ReviewApiClient) => client.getCaseSuccessor('case-1'),
      {},
      '/review_case_id',
    ],
    [
      'GET /api/v1/settings',
      (client: ReviewApiClient) => client.getSettings(),
      { ...settingsView(), managed_path: undefined },
      '/managed_path',
    ],
    [
      'GET /api/v1/appearance',
      (client: ReviewApiClient) => client.getAppearance(),
      {},
      '/reduced_motion',
    ],
    [
      'GET /api/v1/reviewer-access',
      (client: ReviewApiClient) => client.getReviewerAccess(),
      reviewerAccessPayload({ cost_evidence: undefined }),
      '/cost_evidence',
    ],
    [
      'GET /api/v1/reviewer-access/codex/device',
      (client: ReviewApiClient) => client.getCodexDevice(),
      { verification_url: null, user_code: null, expires_in_seconds: null, failure: null },
      '/state',
    ],
    [
      'GET /api/v1/activity',
      (client: ReviewApiClient) =>
        client.getActivity({ eventType: 'all', rangeDays: 30, page: 1, pageSize: 25 }),
      activityResponse({ paused: undefined }),
      '/paused',
    ],
    [
      'GET /api/v1/activity/consent-state',
      (client: ReviewApiClient) => client.getActivityConsent(),
      {},
      '/backup_ready',
    ],
    [
      'GET /api/v1/activity/permission-readiness',
      (client: ReviewApiClient) => client.getActivityPermissionReadiness(),
      {},
      '/backup_ready',
    ],
    [
      'GET /api/v1/activity/{event_id}/transitions',
      (client: ReviewApiClient) => client.getActivityTransitions('event-1', 3),
      { events: [] },
      '/has_more',
    ],
    [
      'GET /api/v1/metadata-policy/state',
      (client: ReviewApiClient) => client.getPolicyState(),
      policyProfile({ policy_id: undefined }),
      '/policy_id',
    ],
    [
      'GET /api/v1/metadata-policy/preview-mode',
      (client: ReviewApiClient) => client.getPreviewPosture(),
      { schema_version: 'preview-posture-v1' },
      '/authority',
    ],
    [
      'GET /api/v1/metadata-policy/preview-mode/outcomes',
      (client: ReviewApiClient) => client.getPreviewSample(),
      { schema_version: 'preview-outcome-sample-v2' },
      '/authority',
    ],
    [
      'GET /api/v1/metadata-policy/transition',
      (client: ReviewApiClient) => client.getPolicyTransition(),
      { schema_version: 'metadata-policy-transition-v1' },
      '/acknowledged',
    ],
    [
      'GET /api/v1/metadata-policy/automatic-safety',
      (client: ReviewApiClient) => client.getAutomaticSafety(),
      {},
      '/category_suspensions',
    ],
  ] as const)(
    'reports an undecodable %s answer as a 502 with its reason and pointer (#1686-#1692)',
    async (operation, read, document, at) => {
      const failure = await read(answering(document)).catch((error: unknown) => error)
      expect(failure).toBeInstanceOf(ResponseDecodeError)
      expect(failure).not.toBeInstanceOf(AmbiguousMutationError)
      expect(failure).toMatchObject({
        status: 502,
        code: 'invalid_response',
        operation,
        responseStatus: 200,
        reason: 'required',
        at,
      })
      expect((failure as Error).message).not.toContain('/')
    },
  )

  it.each([
    '/api/v1/library?lens=all',
    '/api/v1/settings',
    '/review/case-1',
    'https://elsewhere.test/api/v1/library/report?item=item-9',
  ])('follows no report address but the report route (%s) (#1686)', async (address) => {
    const fetchMock = vi.fn<typeof fetch>(async () => Response.json(reportDetail()))
    const client = new ReviewApiClient({ csrf, fetch: fetchMock })
    const failure = await client.getReportDetail(address).catch((error: unknown) => error)
    expect(failure).toBeInstanceOf(ResponseDecodeError)
    expect(failure).toMatchObject({
      status: 502,
      code: 'invalid_response',
      operation: 'GET /api/v1/library/report',
      reason: 'unexpected_operation',
      at: '',
    })
    expect((failure as Error).message).not.toContain('/')
    expect(fetchMock).not.toHaveBeenCalled()
  })

  type Answer = readonly [
    path: PreparationActionPath,
    body: unknown,
    status: number,
    reason: string,
    at: string,
  ]
  it.each<Answer>([
    ...progressPaths.map((path): Answer => [
      path,
      scanProgress({ total: -1 }),
      202,
      'min',
      '/total',
    ]),
    ...viewPaths.map((path): Answer => [
      path,
      undecodable,
      200,
      'required',
      '/libraries/0/blocked',
    ]),
  ])('reports an undecodable %s answer as uncertain', async (path, body, status, reason, at) => {
    const failure = await answering(body, status)
      .preparationAction(path)
      .catch((error: unknown) => error)
    expect(failure).toBeInstanceOf(UncertainMutationError)
    expect(failure).toBeInstanceOf(AmbiguousMutationError)
    expect(failure).toMatchObject({
      status: 0,
      code: 'mutation_outcome_unknown',
      operation: operation(path),
      responseStatus: status,
      reason,
      at,
    })
    expect((failure as Error).message).not.toContain('/')
    expect(uncertainWriteOutcome(failure)).toBe(true)
  })

  it('reports an undecodable dismissal as uncertain', async () => {
    const failure = await answering(undecodable)
      .dismissPreparation(`preparation_run_${'a'.repeat(64)}`)
      .catch((error: unknown) => error)
    expect(failure).toBeInstanceOf(UncertainMutationError)
    expect(failure).toMatchObject({
      operation: 'POST /api/v1/review-preparation/dismiss',
      responseStatus: 200,
      reason: 'required',
      at: '/libraries/0/blocked',
    })
    expect((failure as Error).message).not.toContain('/')
  })

  it('reports a named check failed by an unsafe answer as uncertain', async () => {
    const disordered = preparation({
      active: true,
      presentation: preparationPresentation({
        passage: { read: 4, searches_finished: 3, compared: 2, checked: 1, ready: 2, failed: 0 },
      }),
    })
    for (const send of [
      (client: ReviewApiClient) => client.preparationAction('pause'),
      (client: ReviewApiClient) => client.dismissPreparation(`preparation_run_${'a'.repeat(64)}`),
    ])
      await expect(send(answering(disordered))).rejects.toMatchObject({
        name: 'UncertainMutationError',
        code: 'mutation_outcome_unknown',
        reason: 'invariant',
        at: 'invalid_preparation_passage_order',
      })
    // The same answer to a read keeps the check's own 502.
    await expect(answering(disordered).getPreparation()).rejects.toThrow(
      new ApiError(502, 'invalid_preparation_passage_order'),
    )
  })

  it('fails closed on a 2xx status the operation does not document', async () => {
    await expect(answering(preparation(), 203).getPreparation()).rejects.toMatchObject({
      name: 'ResponseDecodeError',
      operation: 'GET /api/v1/review-preparation',
      responseStatus: 203,
      reason: 'no_decoder',
      at: '',
    })
    await expect(
      answering(scanProgress(), 200).preparationAction('primary/Books/scan'),
    ).rejects.toMatchObject({
      name: 'UncertainMutationError',
      responseStatus: 200,
      reason: 'no_decoder',
    })
  })

  it('accepts a 204 answer from an operation that documents no JSON answer', async () => {
    await expect(
      answering(undefined, 204).returnToAutomatic('case-1', 'title'),
    ).resolves.toBeUndefined()
    await expect(answering(undefined, 204).logout()).resolves.toBeUndefined()
  })

  it('fails closed on a 204 answer to an operation that documents a JSON answer', async () => {
    await expect(answering(undefined, 204).getPreparation()).rejects.toMatchObject({
      name: 'ResponseDecodeError',
      status: 502,
      operation: 'GET /api/v1/review-preparation',
      responseStatus: 204,
      reason: 'no_decoder',
      at: '',
    })
    for (const send of [
      (client: ReviewApiClient) => client.preparationAction('pause'),
      (client: ReviewApiClient) => client.preparationAction('primary/check-connection'),
      (client: ReviewApiClient) => client.dismissPreparation(`preparation_run_${'a'.repeat(64)}`),
    ]) {
      const failure = await send(answering(undefined, 204)).catch((error: unknown) => error)
      expect(failure).toBeInstanceOf(UncertainMutationError)
      expect(failure).toMatchObject({ responseStatus: 204, reason: 'no_decoder', at: '' })
      expect(uncertainWriteOutcome(failure)).toBe(true)
    }
  })

  // The private command seam, so the proofs need no operation's own mapper.
  type Mutate = (
    path: string,
    body?: unknown,
    signal?: AbortSignal,
    extraHeaders?: HeadersInit,
    method?: string,
    sideEffectFree?: boolean,
    writesNothing?: boolean,
    map?: (value: unknown) => unknown,
  ) => Promise<unknown>
  const mutateOf = (client: ReviewApiClient) =>
    (client as unknown as { mutate: Mutate }).mutate.bind(client)

  it('lets a TypeError thrown by a command mapper propagate unchanged', async () => {
    const defect = new TypeError('mapper defect')
    const failure = await mutateOf(answering(preparation()))(
      '/api/v1/review-preparation/pause',
      undefined,
      undefined,
      undefined,
      'POST',
      false,
      false,
      () => {
        throw defect
      },
    ).catch((error: unknown) => error)
    expect(failure).toBe(defect)
    expect(failure).not.toBeInstanceOf(AmbiguousMutationError)
  })

  it('keeps a command the network lost ambiguous', async () => {
    const fetchMock = vi.fn<typeof fetch>().mockRejectedValue(new TypeError('network'))
    const client = new ReviewApiClient({ csrf, fetch: fetchMock, buildIdentity: 'c'.repeat(64) })
    const failure = await client.preparationAction('pause').catch((error: unknown) => error)
    expect(failure).toBeInstanceOf(AmbiguousMutationError)
    expect(failure).not.toBeInstanceOf(UncertainMutationError)
  })

  // #1754: only a command that may write is uncertain when its answer is lost.
  const policyDraft: PolicyDraft = { name: 'Draft', options: {}, destinations: {} }
  it.each([
    ['preview', (client: ReviewApiClient) => client.previewPolicy(policyDraft)],
    ['validate', (client: ReviewApiClient) => client.policyCommand('validate', policyDraft)],
    ['a read', (client: ReviewApiClient) => client.getPolicyState()],
  ] as const)('reports a lost %s answer as a network failure', async (_name, send) => {
    const fetchMock = vi.fn<typeof fetch>().mockRejectedValue(new TypeError('Failed to fetch'))
    const client = new ReviewApiClient({ csrf, fetch: fetchMock, buildIdentity: 'c'.repeat(64) })
    const failure = await send(client).catch((error: unknown) => error)
    expect(failure).toBeInstanceOf(NetworkUnavailableError)
    expect(failure).not.toBeInstanceOf(AmbiguousMutationError)
    expect(failure).not.toBeInstanceOf(ApiError)
    expect(uncertainWriteOutcome(failure)).toBe(false)
    expect(fetchMock).toHaveBeenCalledOnce()
  })

  it('reports a lost CSRF renewal on validate as a network failure', async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(Response.json({ error: { code: 'csrf' } }, { status: 403 }))
    const client = new ReviewApiClient({
      csrf: { current: () => token, renew: () => Promise.reject(new TypeError('Failed to fetch')) },
      fetch: fetchMock,
      buildIdentity: 'c'.repeat(64),
    })
    const failure = await client
      .policyCommand('validate', policyDraft)
      .catch((error: unknown) => error)
    expect(failure).toBeInstanceOf(NetworkUnavailableError)
    expect(uncertainWriteOutcome(failure)).toBe(false)
    expect(fetchMock).toHaveBeenCalledOnce()
  })

  it('reports a failure as a 502 only for a command that writes nothing', async () => {
    const send = (sideEffectFree: boolean, writesNothing: boolean, map?: () => never) =>
      mutateOf(answering(undecodable))(
        '/api/v1/review-preparation/pause',
        undefined,
        undefined,
        undefined,
        'POST',
        sideEffectFree,
        writesNothing,
        map,
      ).catch((error: unknown) => error)
    // Safe to resend is not the same as writing nothing: still uncertain.
    expect(await send(true, false)).toBeInstanceOf(UncertainMutationError)
    expect(await send(false, false)).toBeInstanceOf(UncertainMutationError)
    const refused = await send(true, true)
    expect(refused).toBeInstanceOf(ResponseDecodeError)
    expect(refused).toMatchObject({ reason: 'required', at: '/libraries/0/blocked' })
    const named = await mutateOf(answering(preparation()))(
      '/api/v1/review-preparation/pause',
      undefined,
      undefined,
      undefined,
      'POST',
      true,
      false,
      () => {
        throw new ApiError(502, 'invalid_named_check')
      },
    ).catch((error: unknown) => error)
    expect(named).toMatchObject({
      name: 'UncertainMutationError',
      reason: 'invariant',
      at: 'invalid_named_check',
    })
  })

  it('marks only policy validate and preview as writing nothing', async () => {
    const client = answering({ ok: true })
    const spy = vi.spyOn(client as unknown as { mutate: Mutate }, 'mutate')
    // Each answer is undecodable; only the flags each command sends matter here.
    const ignore = () => undefined
    const draft = { name: 'Draft', options: {}, destinations: {} }
    await client.policyCommand('validate', draft).catch(ignore)
    await client.previewPolicy(draft).catch(ignore)
    await client.policyCommand('drafts', draft).catch(ignore)
    await client
      .activatePolicy({
        policy_id: 'policy-2',
        expected_active_policy_id: 'policy-1',
        expected_active_generation: 1,
      })
      .catch(ignore)
    expect(spy.mock.calls.map((call) => [call[5], call[6]])).toEqual([
      [true, true],
      [true, true],
      [false, false],
      [false, false],
    ])
    spy.mockClear()
    spy.mockResolvedValueOnce({})
    await client.retryProviderRecovery(7).catch(() => undefined)
    expect(spy.mock.calls.map((call) => [call[5], call[6]])).toEqual([[true, false]])
  })

  // No gate is left (#1731): an operation with no decoder documents only 204,
  // so any other answer from it is one this client cannot read.
  it.each([
    ['a body', { unexpected: true }, 200],
    ['an empty 200', undefined, 200],
    ['a body with another 2xx status', { unexpected: true }, 202],
  ])(
    'refuses %s in answer to return to automatic, which documents only 204, as uncertain',
    async (_label, body, status) => {
      const failure = await answering(body, status)
        .returnToAutomatic('case-1', 'title')
        .catch((error: unknown) => error)
      expect(failure).toBeInstanceOf(UncertainMutationError)
      expect(failure).toMatchObject({
        ...uncertain('no_decoder@'),
        operation: 'POST /api/v1/review-cases/case-1/fields/title/return-to-automatic',
        responseStatus: status,
      })
      expect(uncertainWriteOutcome(failure)).toBe(true)
    },
  )

  it('refuses a body in answer to sign out, which documents only 204, as uncertain', async () => {
    const failure = await answering({ signed_out: true })
      .logout()
      .catch((error: unknown) => error)
    expect(failure).toBeInstanceOf(UncertainMutationError)
    expect(failure).toMatchObject({
      ...uncertain('no_decoder@'),
      operation: 'POST /api/v1/session/logout',
      responseStatus: 200,
    })
  })

  // The private read seam. No client method reads the operational status, so
  // this proves decoding needs no per-operation list naming it.
  type Read = (path: string, signal?: AbortSignal) => Promise<unknown>
  const readOf = (client: ReviewApiClient) =>
    (client as unknown as { read: Read }).read.bind(client)

  it('decodes an operation no family read', async () => {
    const failure = await readOf(answering({}))('/api/v1/operations/status').catch(
      (error: unknown) => error,
    )
    expect(failure).toBeInstanceOf(ResponseDecodeError)
    expect(failure).toMatchObject({
      status: 502,
      code: 'invalid_response',
      operation: 'GET /api/v1/operations/status',
      responseStatus: 200,
      reason: 'required',
    })
    expect((failure as ResponseDecodeError).at).toMatch(/^\/[a-z_]+$/u)
  })
})

describe('case-detail write decoding (#1688)', () => {
  const answering = (body: unknown) =>
    new ReviewApiClient({
      csrf,
      buildIdentity: 'c'.repeat(64),
      fetch: vi.fn<typeof fetch>(async () => Response.json(body)),
    })
  // The decision command for the fixture's description field, through the
  // authored selection endpoint when `authored` is set.
  const decide = async (client: ReviewApiClient, authored: boolean) => {
    const { default: payload } = await import('./descriptionAdviceFixture.json')
    const field = (await readCase(payload)).fields.find(
      (item) => item.destination === 'description',
    )
    const candidate = field?.descriptionAdvice?.candidate
    if (!field?.descriptionAdvice || !candidate)
      throw new Error('description fixture requires its candidate')
    return client.recordDecision('case-1', {
      destination: 'description',
      outcome: 'accept',
      proposalId: field.proposalId,
      targetFingerprint: field.targetFingerprint,
      offerDigest: field.offerDigest,
      selectedValue: { kind: 'text', value: candidate.output.description },
      idempotencyKey: 'decoded-decision',
      ...(authored
        ? {
            authoredDescription: {
              candidateId: candidate.candidate_id,
              binding: field.descriptionAdvice.binding,
            },
          }
        : {}),
    })
  }
  const selectionAcknowledgement = (overrides: Record<string, unknown> = {}) => ({
    schema_version: 'description-selection-ack-v1',
    confirmation: { status: 'confirmed', case: caseDetail() },
    decision_id: 'human_decision_' + 'a'.repeat(64),
    advised: true,
    invalidation_sequences: [],
    ...overrides,
  })

  type Write = readonly [
    operation: string,
    send: (client: ReviewApiClient) => Promise<unknown>,
    body: unknown,
    failure: string,
  ]
  it.each<Write>([
    [
      'POST /api/v1/review-cases/{review_case_id}/decisions',
      (client) => decide(client, false),
      decisionAcknowledgement({ decision_id: undefined }),
      'required@/decision_id',
    ],
    [
      'POST /api/v1/review-cases/{review_case_id}/description-advice/selection',
      (client) => decide(client, true),
      selectionAcknowledgement({ advised: undefined }),
      'required@/advised',
    ],
    [
      'POST /api/v1/review-cases/{review_case_id}/cover-considerations/retry',
      (client) => client.retryCoverConsideration('case-1'),
      coverConsideration({ ready: undefined }),
      'required@/ready',
    ],
    [
      'POST /api/v1/review-cases/{review_case_id}/description-advice/retry',
      (client) => client.retryDescriptionAdvice('case-1'),
      { queued: false },
      'const@/queued',
    ],
    [
      'POST /api/v1/review-cases/{review_case_id}/decisions',
      (client) => decide(client, false),
      decisionAcknowledgement({
        confirmation: { status: 'confirmed', case: caseDetail({ review_case_id: 'case-2' }) },
      }),
      'invariant@invalid_decision_confirmation',
    ],
    [
      'POST /api/v1/review-cases/{review_case_id}/description-advice/selection',
      (client) => decide(client, true),
      selectionAcknowledgement({
        confirmation: { status: 'confirmed', case: caseDetail({ review_case_id: 'case-2' }) },
      }),
      'invariant@invalid_decision_confirmation',
    ],
  ])(
    'reports an unreadable %s answer as uncertain (%#)',
    async (operation, send, body, failure) => {
      const refusal = await send(answering(body)).catch((error: unknown) => error)
      // The decision controller forgets the command and offers no same-key Retry.
      expect(classifyDecisionFailure(refusal)).toBe('uncertain')
      expect(refusal).toBeInstanceOf(UncertainMutationError)
      expect(refusal).toBeInstanceOf(AmbiguousMutationError)
      expect(refusal).toMatchObject({ ...uncertain(failure), operation, responseStatus: 200 })
      expect((refusal as Error).message).not.toContain('/')
      expect(uncertainWriteOutcome(refusal)).toBe(true)
    },
  )

  it('accepts the documented answers to the case-detail writes', async () => {
    await expect(
      answering({ queued: true }).retryDescriptionAdvice('case-1'),
    ).resolves.toBeUndefined()
    expect((await answering(coverConsideration()).retryCoverConsideration('case-1')).ready).toBe(
      false,
    )
    expect((await decide(answering(selectionAcknowledgement()), true)).confirmation.status).toBe(
      'confirmed',
    )
  })
})

describe('settings write decoding (#1689)', () => {
  const answering = (body: unknown) =>
    new ReviewApiClient({
      csrf,
      buildIdentity: 'c'.repeat(64),
      fetch: vi.fn<typeof fetch>(async () => Response.json(body)),
    })
  const write: SettingsWrite = {
    expected_revision: 3,
    document: settingsView().document,
    secrets: [],
  }
  type Write = readonly [
    operation: string,
    send: (client: ReviewApiClient) => Promise<unknown>,
    body: unknown,
    failure: string,
  ]
  it.each<Write>([
    [
      'PATCH /api/v1/settings',
      (client) => client.writeSettings(write),
      { ...settingsView(), fields: undefined },
      'required@/fields',
    ],
    [
      'PATCH /api/v1/settings',
      (client) => client.writeSettings(write),
      { ...settingsView(), authority: 'deploy' },
      'enum@/authority',
    ],
    [
      'POST /api/v1/provider-recovery/retry',
      (client) => client.retryProviderRecovery(1),
      { ...settingsView(), secrets: undefined },
      'required@/secrets',
    ],
    [
      'POST /api/v1/provider-recovery/retry',
      (client) => client.retryProviderRecovery(1),
      {
        ...settingsView(),
        document: { ...settingsView().document, provider_recovery_generation: 0 },
      },
      'min@/document/provider_recovery_generation',
    ],
  ])(
    'reports an unreadable %s answer as uncertain (%#)',
    async (operation, send, body, failure) => {
      const refusal = await send(answering(body)).catch((error: unknown) => error)
      expect(refusal).toBeInstanceOf(UncertainMutationError)
      expect(refusal).toBeInstanceOf(AmbiguousMutationError)
      expect(refusal).toMatchObject({ ...uncertain(failure), operation, responseStatus: 200 })
      expect((refusal as Error).message).not.toContain('/')
      expect(uncertainWriteOutcome(refusal)).toBe(true)
    },
  )

  it('accepts the documented answers to the settings writes', async () => {
    const saved = settingsView()
    const library = Object.fromEntries(
      Object.entries(saved.document.libraries[0] ?? {}).filter(([key]) => key !== 'languages'),
    )
    const answer = { ...saved, document: { ...saved.document, libraries: [library] } }
    // The decoder fills an omitted language list with its default.
    expect((await answering(answer).writeSettings(write)).document.libraries[0]?.languages).toEqual(
      [],
    )
    expect((await answering(saved).retryProviderRecovery(1)).authority).toBe('store')
  })
})

describe('reviewer-access write decoding (#1690)', () => {
  const answering = (body: unknown) =>
    new ReviewApiClient({
      csrf,
      buildIdentity: 'c'.repeat(64),
      fetch: vi.fn<typeof fetch>(async () => Response.json(body)),
    })
  const access = reviewerAccessPayload()
  const device = {
    state: 'issued',
    verification_url: 'https://auth.openai.com/codex/device',
    user_code: 'ABCD-1234',
    expires_in_seconds: 900,
    failure: null,
  }
  const metadata = {
    model: 'example/text',
    observed_at: 1,
    prompt_price: '0.000001',
    completion_price: '0.000002',
    request_price: null,
    text_input: true,
    text_output: true,
    structured_outputs: true,
  }
  const openRouter = { expected_revision: 3, api_key: 'synthetic', model: 'example/vision' }
  type Send = (client: ReviewApiClient) => Promise<unknown>
  const sends: readonly (readonly [operation: string, send: Send, answer: unknown])[] = [
    [
      'PUT /api/v1/reviewer-access/openrouter',
      (client) => client.configureOpenRouter(openRouter),
      access,
    ],
    ['DELETE /api/v1/reviewer-access/openrouter', (client) => client.clearOpenRouter(3), access],
    [
      'POST /api/v1/reviewer-access/openrouter/refresh',
      (client) => client.refreshOpenRouter(undefined, 'description_rewrite'),
      access,
    ],
    ['PUT /api/v1/reviewer-access/selection', (client) => client.selectReviewer(null, 3), access],
    ['DELETE /api/v1/reviewer-access/codex', (client) => client.clearCodex(3), access],
    [
      'POST /api/v1/reviewer-access/codex/device',
      (client) => client.startCodexDevice(3),
      { access, device },
    ],
    [
      'POST /api/v1/reviewer-access/codex/device/cancel',
      (client) => client.cancelCodexDevice(),
      {
        ...device,
        state: 'cancelled',
        verification_url: null,
        user_code: null,
        expires_in_seconds: null,
      },
    ],
    [
      'POST /api/v1/reviewer-access/codex/recheck',
      (client) => client.recheckCodex(),
      {
        ...device,
        state: 'usable',
        verification_url: null,
        user_code: null,
        expires_in_seconds: null,
      },
    ],
    // The test and the disclosure write nothing, but they are not marked as
    // writing nothing, so they read as uncertain too (stage-3 Q4).
    [
      'POST /api/v1/reviewer-access/description-model/test',
      (client) => client.testDescriptionModel(openRouter),
      metadata,
    ],
    [
      'POST /api/v1/reviewer-access/description-model/contained',
      (client) => client.describeContainedDescription(),
      { model: 'contained-model', profile_sha256: 'a'.repeat(64) },
    ],
  ]

  it.each(sends)('accepts the documented %s answer', async (_operation, send, answer) => {
    await expect(send(answering(answer))).resolves.toBeDefined()
  })

  it.each(sends)(
    'reports an unreadable %s answer as uncertain',
    async (operation, send, answer) => {
      const body = { ...(answer as Record<string, unknown>), invented: true }
      const refusal = await send(answering(body)).catch((error: unknown) => error)
      expect(refusal).toBeInstanceOf(UncertainMutationError)
      expect(refusal).toBeInstanceOf(AmbiguousMutationError)
      expect(refusal).toMatchObject({
        ...uncertain('unknown_key@/invented'),
        operation,
        responseStatus: 200,
      })
      expect((refusal as Error).message).not.toContain('/')
      expect(uncertainWriteOutcome(refusal)).toBe(true)
    },
  )

  // The contained disclosure's model and profile digest were a plain Error
  // (S81, S82); they are the decoder's now.
  it.each([
    [{ model: '', profile_sha256: 'a'.repeat(64) }, 'min_length@/model'],
    [{ model: 'contained-model', profile_sha256: 'A'.repeat(64) }, 'pattern@/profile_sha256'],
    [{ model: 'contained-model' }, 'required@/profile_sha256'],
  ])('reports a contained disclosure %o as uncertain', async (body, failure) => {
    await expect(answering(body).describeContainedDescription()).rejects.toMatchObject({
      ...uncertain(failure),
      operation: 'POST /api/v1/reviewer-access/description-model/contained',
    })
  })

  // A named check failed on a committing answer, or on the test, is uncertain
  // with the check's code (stage-3 Q12).
  it.each([
    [
      'DELETE /api/v1/reviewer-access/codex',
      (client: ReviewApiClient) => client.clearCodex(3),
      reviewerAccessPayload({ codex: { ...access.codex, model: null } }),
      'invalid_reviewer_access_response',
    ],
    [
      'PUT /api/v1/reviewer-access/selection',
      (client: ReviewApiClient) => client.selectReviewer(null, 3),
      reviewerAccessPayload({
        cost_evidence: {
          models: [
            {
              model: 'openai/gpt-6-luna',
              task: 'cover',
              requests_selected: 100,
              requests_attempted: 99,
              requests_completed: 99,
              requests_failed: 0,
              works: 99,
              distinct_images: 1,
              total_cost_usd: '0.099',
              per_1000_usd: '1',
              measured_run: {
                source_head: 'a'.repeat(40),
                cohort_sha256: 'b'.repeat(64),
                observed_at: 1,
              },
            },
          ],
        },
      }),
      'invalid_cost_evidence',
    ],
    [
      'POST /api/v1/reviewer-access/codex/device',
      (client: ReviewApiClient) => client.startCodexDevice(3),
      { access, device: { ...device, user_code: null } },
      'invalid_codex_device_response',
    ],
    [
      'POST /api/v1/reviewer-access/codex/recheck',
      (client: ReviewApiClient) => client.recheckCodex(),
      { ...device, state: 'ready' },
      'invalid_codex_device_response',
    ],
    [
      'POST /api/v1/reviewer-access/description-model/test',
      (client: ReviewApiClient) => client.testDescriptionModel(openRouter),
      { ...metadata, request_price: '-1' },
      'invalid_description_metadata',
    ],
  ] as const)(
    'reports %s failing a named check as uncertain (%#)',
    async (operation, send, body, code) => {
      const refusal = await send(answering(body)).catch((error: unknown) => error)
      expect(refusal).toBeInstanceOf(UncertainMutationError)
      expect(refusal).toMatchObject({
        ...uncertain(`invariant@${code}`),
        operation,
        responseStatus: 200,
      })
      expect(uncertainWriteOutcome(refusal)).toBe(true)
    },
  )
})

// The cover and choice-context checks named X55-X61 in ADR-0091, and the two
// conditions their named neighbours already imply (#1688). Each answer is to
// the cover retry, so a failed check reports the write as uncertain.
describe('named cover and choice-context checks (#1688)', () => {
  const proposed = (alternative_id: string | null) =>
    choiceContext({ alternative_id, selection: 'proposed', rationale: 'Retained cover choice.' })
  const second = (overrides: Record<string, unknown> = {}) =>
    coverCandidate({
      artifact_id: 'artifact-2',
      deterministic_rank: 2,
      deterministic_rank_key: [1, 0, -10000, -100, -100, 0, 'candidate-2'],
      ordinal: 1,
      reference: 'candidate-2',
      ...overrides,
    })
  const retained = (overrides: Record<string, unknown> = {}) =>
    coverCandidate({
      incumbent: true,
      manual_eligible: false,
      selectable: false,
      deterministic_rank: null,
      state: 'excluded',
      ...overrides,
    })
  const payload = (overrides: Record<string, unknown> = {}) =>
    coverConsideration({
      candidates: [coverCandidate()],
      choice_contexts: [choiceContext(), proposed('candidate-1')],
      policy: coverPolicyReport(),
      ready: true,
      reviewer_access_revision: 7,
      reviewer_engine: 'openrouter',
      reviewer_model: 'provider/model',
      reviewer_status: 'completed',
      ...overrides,
    })
  const twoAlternatives = (overrides: Record<string, unknown> = {}) =>
    payload({
      candidates: [coverCandidate(), second()],
      choice_contexts: [choiceContext(), proposed('candidate-1'), proposed('candidate-2')],
      policy: coverPolicyReport({
        acceptable_candidate_references: ['candidate-1', 'candidate-2'],
      }),
      ...overrides,
    })

  it('accepts the base answers', async () => {
    expect((await retryCover(payload())).choiceContexts).toHaveLength(2)
    expect((await retryCover(twoAlternatives())).candidates).toHaveLength(2)
  })

  it.each<readonly [string, Record<string, unknown>, string]>([
    // X55: the current context names no alternative.
    [
      'X55',
      {
        choice_contexts: [
          choiceContext({ alternative_id: 'candidate-1' }),
          proposed('candidate-1'),
        ],
      },
      'misaligned_choice_contexts',
    ],
    // X56: every later context is proposed and names an alternative.
    [
      'X56 selection',
      {
        choice_contexts: [
          choiceContext(),
          choiceContext({ alternative_id: 'candidate-1', selection: 'current' }),
        ],
      },
      'misaligned_choice_contexts',
    ],
    [
      'X56 identity',
      { choice_contexts: [choiceContext(), proposed(null)] },
      'misaligned_choice_contexts',
    ],
    // X57: proposed alternatives are distinct.
    [
      'X57',
      { choice_contexts: [choiceContext(), proposed('candidate-1'), proposed('candidate-1')] },
      'misaligned_choice_contexts',
    ],
    // X58: candidate artifacts are distinct.
    [
      'X58',
      { candidates: [coverCandidate(), second({ artifact_id: 'artifact-1' })] },
      'misaligned_cover_candidates',
    ],
    // X59: a confirmed incumbent is unselectable and unranked with a rank
    // key, and a confirmed alternative has a rank key.
    [
      'X59 incumbent',
      {
        candidates: [retained({ selectable: true })],
        choice_contexts: [choiceContext()],
        incumbent: { reference: 'candidate-1', state: 'available' },
      },
      'contradictory_cover_identity',
    ],
    [
      'X59 alternative',
      { candidates: [coverCandidate({ deterministic_rank: null, deterministic_rank_key: null })] },
      'contradictory_cover_identity',
    ],
    // match-reason: a matched-book reason names only a proposed candidate
    // whose identity is not a conflict (#1642).
    [
      'match-reason incumbent',
      {
        candidates: [retained({ match_reason: 'matched_book' })],
        choice_contexts: [choiceContext()],
        incumbent: { reference: 'candidate-1', state: 'available' },
      },
      'contradictory_cover_match_reason',
    ],
    [
      'match-reason conflict',
      {
        candidates: [
          coverCandidate({
            identity: coverIdentity({ outcome: 'conflict' }),
            manual_eligible: false,
            match_reason: 'matched_book',
            match_state: 'match_not_confirmed',
            selectable: false,
            deterministic_rank: null,
            deterministic_rank_key: null,
            state: 'excluded',
          }),
        ],
        choice_contexts: [choiceContext()],
        policy: coverPolicyReport({ acceptable_candidate_references: [] }),
      },
      'contradictory_cover_match_reason',
    ],
    // X60: proposed contexts follow the eligible alternatives in order.
    [
      'X60',
      { choice_contexts: [choiceContext(), proposed('candidate-2'), proposed('candidate-1')] },
      'misaligned_cover_choice_contexts',
    ],
    // X61: an unavailable reason needs the unavailable status.
    [
      'X61',
      { reviewer_unavailable_reason: 'profile contract requires process_limit=32' },
      'invalid_cover_reviewer_unavailable_reason',
    ],
    // Implied by ordinal-position: a repeated ordinal is out of position.
    [
      'repeated ordinal',
      { candidates: [coverCandidate(), second({ ordinal: 0 })] },
      'misaligned_cover_candidates',
    ],
    // Implied by the incumbent rule: no state admits two incumbents.
    [
      'two available incumbents',
      {
        candidates: [
          retained(),
          retained({
            artifact_id: 'artifact-2',
            ordinal: 1,
            reference: 'candidate-2',
            deterministic_rank_key: [1, 0, -10000, -100, -100, 0, 'candidate-2'],
          }),
        ],
        choice_contexts: [choiceContext()],
        incumbent: { reference: 'candidate-1', state: 'available' },
      },
      'misaligned_cover_incumbent',
    ],
    [
      'two incumbents with none stated',
      {
        candidates: [
          retained(),
          retained({
            artifact_id: 'artifact-2',
            ordinal: 1,
            reference: 'candidate-2',
            deterministic_rank_key: [1, 0, -10000, -100, -100, 0, 'candidate-2'],
          }),
        ],
        choice_contexts: [choiceContext()],
      },
      'misaligned_cover_incumbent',
    ],
  ])('refuses %s', async (_name, overrides, code) => {
    const base = 'candidates' in overrides && (overrides.candidates as unknown[]).length === 2
    const document = base ? twoAlternatives(overrides) : payload(overrides)
    await expect(retryCover(document)).rejects.toMatchObject(uncertain(`invariant@${code}`))
  })
})

describe('activity write decoding (#1691)', () => {
  const answering = (body: unknown, status = 200) => {
    const fetchMock = vi.fn<typeof fetch>(async () =>
      body === undefined ? new Response(null, { status }) : Response.json(body, { status }),
    )
    return {
      fetchMock,
      client: new ReviewApiClient({ csrf, buildIdentity: 'c'.repeat(64), fetch: fetchMock }),
    }
  }
  const consent = { grants: [{ library: 'Fiction', destinations: ['title'] }] }
  type Command = readonly [operation: string, path: ActivityCommandPath, body: unknown]
  const commands: readonly Command[] = [
    ['POST /api/v1/activity/consent', 'activity/consent', consent],
    [
      'POST /api/v1/activity/{connection}/{library}/revoke',
      'activity/primary/Fiction/revoke',
      undefined,
    ],
  ]
  const recovery = {
    state: 'waiting',
    affected_count: 1,
    next_eligible_at: null,
    dispatch_reasons: [],
    failure_reasons: [],
  }

  it.each<Command>(commands)(
    'accepts the documented answer to %s',
    async (_operation, path, body) => {
      const { client, fetchMock } = answering(activityResponse({ paused: true }))
      expect((await client.activityCommand(path, body))?.paused).toBe(true)
      expect(fetchMock.mock.calls[0]?.[0]).toBe(`/api/v1/${path}`)
    },
  )

  type Unreadable = readonly [Command[0], Command[1], Command[2], answer: unknown, failure: string]
  it.each<Unreadable>(
    commands.flatMap(([operation, path, body]): Unreadable[] => [
      [operation, path, body, activityResponse({ invented: true }), 'unknown_key@/invented'],
      [
        operation,
        path,
        body,
        activityResponse({ provider_recovery: [{ ...recovery, failure_reasons: undefined }] }),
        'required@/provider_recovery/0/failure_reasons',
      ],
    ]),
  )(
    'reports an unreadable %s answer as uncertain (%#)',
    async (operation, path, body, answer, failure) => {
      const refusal = await answering(answer)
        .client.activityCommand(path, body)
        .catch((error: unknown) => error)
      expect(refusal).toBeInstanceOf(UncertainMutationError)
      expect(refusal).toBeInstanceOf(AmbiguousMutationError)
      expect(refusal).toMatchObject({ ...uncertain(failure), operation, responseStatus: 200 })
      expect((refusal as Error).message).not.toContain('/')
      expect(uncertainWriteOutcome(refusal)).toBe(true)
    },
  )

  it.each<Command>(commands)(
    'reports a committed %s answer whose failure reasons are out of order as uncertain',
    async (operation, path, body) => {
      const answer = activityResponse({
        provider_recovery: [{ ...recovery, failure_reasons: ['transient', 'authentication'] }],
      })
      const refusal = await answering(answer)
        .client.activityCommand(path, body)
        .catch((error: unknown) => error)
      expect(refusal).toBeInstanceOf(UncertainMutationError)
      expect(refusal).toMatchObject({
        ...uncertain('invariant@invalid_provider_recovery_failure_reasons'),
        operation,
        responseStatus: 200,
      })
    },
  )

  it.each<ActivityCommandPath>(['field-effects/pause', 'field-effects/resume'])(
    'leaves the 204 answer to %s undecoded',
    async (path) => {
      const { client, fetchMock } = answering(undefined, 204)
      await expect(client.activityCommand(path)).resolves.toBeUndefined()
      expect(fetchMock.mock.calls[0]?.[0]).toBe(`/api/v1/${path}`)
    },
  )

  it.each<ActivityCommandPath>(['field-effects/pause', 'field-effects/resume'])(
    'reports a body in answer to %s, which documents only 204, as uncertain',
    async (path) => {
      const refusal = await answering(activityResponse())
        .client.activityCommand(path)
        .catch((error: unknown) => error)
      expect(refusal).toBeInstanceOf(UncertainMutationError)
      // `request()` refuses it before the Activity mapper runs (#1731).
      expect(refusal).toMatchObject({
        ...uncertain('no_decoder@'),
        operation: `POST /api/v1/${path}`,
        responseStatus: 200,
      })
    },
  )

  it('reads a documented transitions page and refuses one the schema does not allow', async () => {
    const transition = {
      event_id: 'event-1',
      failure: null,
      occurred_at: 1,
      sequence: 1,
      state: 'verified',
    }
    const read = (body: unknown) => answering(body).client.getActivityTransitions('event-1')
    expect(await read({ events: [transition], has_more: false })).toEqual({
      events: [transition],
      has_more: false,
    })
    for (const [body, failure] of [
      [{ events: Array(21).fill(transition), has_more: true }, 'max_items@/events'],
      [{ events: [{ ...transition, sequence: 0 }], has_more: false }, 'min@/events/0/sequence'],
      [{ events: [{ ...transition, event_id: 7 }], has_more: false }, 'type@/events/0/event_id'],
      [{ events: [{ ...transition, failure: 1 }], has_more: false }, 'type@/events/0/failure'],
    ] as const)
      await expect(read(body)).rejects.toMatchObject(refused(failure))
  })
})

// `policyCommand` is typed by its operation (#1729). Each path's body and answer
// come from the generated `paths`, and preview and activation, which carry
// checks the schema cannot state, are not among its paths. `tsc` checks these
// cases; the commands below are never sent.
describe('policy command types (#1729)', () => {
  type Request<Name extends keyof components['schemas']> = components['schemas'][Name]
  type Answer<Name extends keyof responseComponents['schemas']> =
    responseComponents['schemas'][Name]

  it('derives each command answer from its operation', () => {
    expectTypeOf<PolicyCommandAnswer<'validate'>>().toEqualTypeOf<PolicyStateView>()
    expectTypeOf<PolicyCommandAnswer<'drafts'>>().toEqualTypeOf<Answer<'_PolicyDraftResponse'>>()
    expectTypeOf<
      PolicyCommandAnswer<
        'automatic-safety/pause' | 'automatic-safety/recover' | 'automatic-safety/suspend'
      >
    >().toEqualTypeOf<AutomaticSafetyView>()
    expectTypeOf<PolicyCommandAnswer<'preview-mode/enter'>>().toEqualTypeOf<PreviewPostureView>()
    expectTypeOf<
      PolicyCommandAnswer<'preview-mode/confirm-automatic'>
    >().toEqualTypeOf<PreviewPostureView>()
    expectTypeOf<
      PolicyCommandAnswer<'transitions/transition-1/acknowledge'>
    >().toEqualTypeOf<PolicyTransitionView>()
    expectTypeOf<
      PolicyCommandAnswer<'transitions/transition-1/retry'>
    >().toEqualTypeOf<PolicyTransitionView>()
    // A union of paths answers the union of their answers.
    expectTypeOf<PolicyCommandAnswer<'validate' | 'preview-mode/enter'>>().toEqualTypeOf<
      PolicyStateView | PreviewPostureView
    >()
  })

  it('derives each command body from its operation', () => {
    expectTypeOf<PolicyCommandBody<'validate' | 'drafts'>>().toEqualTypeOf<PolicyDraft>()
    expectTypeOf<PolicyCommandBody<'automatic-safety/pause'>>().toEqualTypeOf<
      Request<'_AutomaticPauseRequest'>
    >()
    expectTypeOf<PolicyCommandBody<'automatic-safety/recover'>>().toEqualTypeOf<
      Request<'_AutomaticRecoveryRequest'>
    >()
    expectTypeOf<PolicyCommandBody<'automatic-safety/suspend'>>().toEqualTypeOf<
      Request<'_AutomaticSuspendRequest'>
    >()
    expectTypeOf<
      PolicyCommandBody<
        | 'preview-mode/enter'
        | 'preview-mode/confirm-automatic'
        | 'transitions/transition-1/acknowledge'
        | 'transitions/transition-1/retry'
      >
    >().toEqualTypeOf<undefined>()
  })

  it('sends only the generic policy commands, each with its own body', () => {
    const client = new ReviewApiClient({ csrf, buildIdentity: 'c'.repeat(64), fetch: vi.fn() })
    const draft: PolicyDraft = { name: 'Draft', options: {}, destinations: {} }
    const request = {
      policy_id: 'policy-2',
      expected_active_policy_id: 'policy-1',
      expected_active_generation: 1,
    }
    const typed = () => {
      expectTypeOf(
        client.policyCommand('validate', draft),
      ).resolves.toEqualTypeOf<PolicyStateView>()
      expectTypeOf(
        client.policyCommand('automatic-safety/pause', { paused: true }),
      ).resolves.toEqualTypeOf<AutomaticSafetyView>()
      expectTypeOf(
        client.policyCommand('transitions/transition-1/retry'),
      ).resolves.toEqualTypeOf<PolicyTransitionView>()
      expectTypeOf<'preview' | 'activate'>().not.toExtend<PolicyCommandPath>()
      // @ts-expect-error preview is sent only by `previewPolicy` (P-X1, P-X2)
      void client.policyCommand('preview', draft)
      // @ts-expect-error activation is sent only by `activatePolicy` (P-X3)
      void client.policyCommand('activate', request)
      // @ts-expect-error no such policy command
      void client.policyCommand('transitions/transition-1/cancel')
      // @ts-expect-error validate takes a complete draft
      void client.policyCommand('validate', {})
      // @ts-expect-error the automatic pause takes `{ paused }`, not a draft
      void client.policyCommand('automatic-safety/pause', draft)
    }
    expect(typed).toBeTypeOf('function')
  })
})

describe('answer types of read and mutate (#1730)', () => {
  it('does not let a caller choose the answer of a mutate without a map', () => {
    type Mutate = ReviewApiClient['mutate']
    const typed = (mutate: Mutate) => {
      // @ts-expect-error logout's answer is not the caller's expected type
      const wrong: Promise<{ totally: 'wrong' }> = mutate('/api/v1/session/logout')
      // @ts-expect-error a settings write answers settings, not a number
      const number: Promise<number> = mutate('/api/v1/settings', {}, undefined, undefined, 'PATCH')
      return [wrong, number]
    }
    expect(typed).toBeTypeOf('function')
  })
  type Answer<Name extends keyof responseComponents['schemas']> =
    responseComponents['schemas'][Name]
  type Method<K extends string> = K extends `${infer M} ${string}` ? M : never
  type Route<K extends string> = K extends `${string} ${infer R}` ? R : never
  // A route with each `{parameter}` filled by a hole, or by `x-1`.
  type Holes<R extends string> = R extends `${infer Head}{${string}}${infer Tail}`
    ? `${Head}${string}${Holes<Tail>}`
    : R
  type Filled<R extends string> = R extends `${infer Head}{${string}}${infer Tail}`
    ? `${Head}x-1${Filled<Tail>}`
    : R
  const filled = (route: string) => route.replaceAll(/\{[^}]+\}/gu, 'x-1')
  const keys = Object.keys(responseDecoders) as OperationKey[]

  it('resolves every operation from its own path, in types and at run time alike', () => {
    expectTypeOf<{
      [K in OperationKey]: OperationOf<Method<K>, Filled<Route<K>>>
    }>().toEqualTypeOf<{ [K in OperationKey]: K }>()
    expectTypeOf<{
      [K in OperationKey]: OperationOf<Method<K>, `${Filled<Route<K>>}?q=1`>
    }>().toEqualTypeOf<{ [K in OperationKey]: K }>()
    expectTypeOf<{
      [K in OperationKey]: OperationOf<Method<K>, Holes<Route<K>>>
    }>().toEqualTypeOf<{ [K in OperationKey]: K }>()
    expectTypeOf<{
      [K in OperationKey]: OperationOf<Method<K>, `${Holes<Route<K>>}?${string}`>
    }>().toEqualTypeOf<{ [K in OperationKey]: K }>()
    // The same filled paths, resolved by the run-time matcher `request()` uses.
    expect(keys.length).toBeGreaterThan(60)
    for (const key of keys) {
      const [method = '', route = ''] = key.split(' ')
      expect(matchOperation(method, filled(route))).toBe(key)
      expect(matchOperation(method, `${filled(route)}?q=1`)).toBe(key)
    }
  })

  it('answers what the operation decodes to', () => {
    expectTypeOf<RequestAnswer<'GET', '/api/v1/settings'>>().toEqualTypeOf<SettingsView>()
    expectTypeOf<RequestAnswer<'PATCH', '/api/v1/settings'>>().toEqualTypeOf<SettingsView>()
    expectTypeOf<
      RequestAnswer<'GET', `/api/v1/review-cases/${string}/evidence?${string}`>
    >().toEqualTypeOf<Answer<'_EvidenceResponse'>>()
    expectTypeOf<RequestAnswer<'GET', `/api/v1/review-cases/${string}`>>().toEqualTypeOf<
      Answer<'_CaseDetailResponse'>
    >()
    expectTypeOf<
      RequestAnswer<'GET', '/api/v1/metadata-policy/transition'>
    >().toEqualTypeOf<PolicyTransitionView | null>()
    expectTypeOf<
      RequestAnswer<'POST', `/api/v1/review-preparation/${PreparationActionPath}`>
    >().toEqualTypeOf<Answer<'_PreparationResponse'> | Answer<'_PreparationProgress'>>()
    expectTypeOf<RequestAnswer<'POST', `/api/v1/review-cases/${string}/decisions`>>().toEqualTypeOf<
      Answer<'_DecisionAcknowledgement'>
    >()
    // An operation with no decoder documents only 204, the only answer
    // `request()` accepts from it (#1731).
    expectTypeOf<RequestAnswer<'POST', '/api/v1/session/logout'>>().toEqualTypeOf<undefined>()
    expectTypeOf<
      RequestAnswer<'POST', '/api/v1/session/logout-others'>
    >().toEqualTypeOf<undefined>()
    expectTypeOf<RequestAnswer<'POST', `/api/v1/${ActivityCommandPath}`>>().toEqualTypeOf<
      Answer<'_ActivityResponse'> | undefined
    >()
  })

  it('names no operation for a path that matches none, or matches through a hole', () => {
    expectTypeOf<RequestAnswer<'GET', '/api/v1/nothing'>>().toEqualTypeOf<Unrouted>()
    expectTypeOf<RequestAnswer<'POST', '/api/v1/settings'>>().toEqualTypeOf<Unrouted>()
    // A hole is one segment, so it cannot stand for `x-1/evidence`.
    expectTypeOf<
      RequestAnswer<'GET', `/api/v1/review-cases/${string}/x/evidence`>
    >().toEqualTypeOf<Unrouted>()
    expectTypeOf<RequestAnswer<'GET', '/api/v1/review-cases/'>>().toEqualTypeOf<Unrouted>()
    // A hole fills only a parameter, never a named segment such as `device`.
    expectTypeOf<
      RequestAnswer<'GET', `/api/v1/reviewer-access/codex/${string}`>
    >().toEqualTypeOf<Unrouted>()
    expectTypeOf<Unrouted>().not.toExtend<Answer<'_CaseDetailResponse'>>()
    expectTypeOf<'/api/v1/settings'>().toExtend<RoutablePath<'GET'>>()
    expectTypeOf<'/api/v1/nothing'>().not.toExtend<RoutablePath<'GET'>>()
    expectTypeOf<`/api/v1/attention${string}`>().not.toExtend<RoutablePath<'GET'>>()
    expectTypeOf<'/api/v1/session/logout'>().toExtend<RoutablePath<'POST'>>()
    expectTypeOf<'/api/v1/session/logout-others'>().toExtend<RoutablePath<'POST'>>()
  })
})

describe('policy decoding (#1692)', () => {
  const answering = (body: unknown, status = 200) => {
    const fetchMock = vi.fn<typeof fetch>(async () => Response.json(body, { status }))
    return {
      fetchMock,
      client: new ReviewApiClient({ csrf, buildIdentity: 'c'.repeat(64), fetch: fetchMock }),
    }
  }
  const draft = { name: 'Draft', options: {}, destinations: {} }
  const capture = policyPreview()
  // One sampled work whose comparison describes the same field on both sides.
  const impact = {
    current_value: 'Old tag',
    desired_value: 'New tag',
    field: 'tags',
    narrowed_for_target: false,
    option: 'fill_empty',
    outcome: 'projected',
    qualified_value: 'New tag',
    reason: null,
    target_state: 'available',
  }
  const sampled = (comparison: Record<string, unknown> = {}, cases = 1) =>
    policyPreview({
      libraries: [
        {
          ...capture.libraries[0],
          affected_categories: ['tags'],
          cases: Array.from({ length: cases }, (_, index) => ({
            fields: [impact],
            proposal_set_id: `proposal-${String(index)}`,
            report_id: `report-${String(index)}`,
            resolved_policy_id: 'resolved-policy',
            work_label: `Work ${String(index)}`,
          })),
          cases_total: cases,
          comparisons: [
            {
              media_kind: 'ebook',
              fields: [
                {
                  active: { ...impact, desired_value: 'Old tag', qualified_value: 'Old tag' },
                  draft: impact,
                  active_destination: 'ask',
                  draft_destination: 'automatic',
                  destination: 'subtitle',
                  changed: true,
                  ...comparison,
                },
              ],
            },
          ],
        },
      ],
    })
  const activation = activationCapture.body
  const request = {
    policy_id: activation.policy_id,
    expected_active_policy_id: 'policy-active',
    expected_active_generation: 1,
  }

  it('reads an absent transition as none', async () => {
    await expect(answering(null).client.getPolicyTransition()).resolves.toBeNull()
  })

  it('accepts a preview whose comparisons match its sampled works', async () => {
    const { client, fetchMock } = answering(sampled())
    expect((await client.previewPolicy(draft)).libraries[0]?.cases).toHaveLength(1)
    expect(fetchMock.mock.calls[0]?.[0]).toBe('/api/v1/metadata-policy/preview')
  })

  // Validate and preview write nothing, so an answer they cannot read is a
  // 502, never an uncertain write (Q4).
  it.each([
    [
      'POST /api/v1/metadata-policy/validate',
      (client: ReviewApiClient) => client.policyCommand('validate', draft),
      'required@/audiobook_qualification',
    ],
    [
      'POST /api/v1/metadata-policy/preview',
      (client: ReviewApiClient) => client.previewPolicy(draft),
      'required@/active_generation',
    ],
  ] as const)(
    'reports an undecodable %s answer as a 502 (%#)',
    async (operation, send, failure) => {
      const refusal = await send(answering({}).client).catch((error: unknown) => error)
      expect(refusal).toBeInstanceOf(ResponseDecodeError)
      expect(refusal).not.toBeInstanceOf(AmbiguousMutationError)
      expect(refusal).toMatchObject({ ...refused(failure), operation, responseStatus: 200 })
      expect((refusal as Error).message).not.toContain('/')
    },
  )

  // Nor can an answer that is not JSON at all: it is the read's 502, not an
  // uncertain write (#1731). A command that may have written stays uncertain.
  it.each([
    ['validate', (client: ReviewApiClient) => client.policyCommand('validate', draft)],
    ['preview', (client: ReviewApiClient) => client.previewPolicy(draft)],
  ] as const)('reports a non-JSON answer to %s as a 502', async (_name, send) => {
    for (const response of [
      () => new Response('Saved', { status: 200, headers: { 'content-type': 'text/plain' } }),
      () => {
        const interrupted = new Response('{', { status: 200 })
        vi.spyOn(interrupted, 'json').mockRejectedValue(new TypeError('body interrupted'))
        return interrupted
      },
    ]) {
      const fetchMock = vi.fn<typeof fetch>(async () => response())
      const client = new ReviewApiClient({ csrf, buildIdentity: 'c'.repeat(64), fetch: fetchMock })
      const refusal = await send(client).catch((error: unknown) => error)
      expect(refusal).toEqual(new ApiError(502, 'invalid_json_response'))
      expect(refusal).not.toBeInstanceOf(AmbiguousMutationError)
      expect(fetchMock).toHaveBeenCalledOnce()
    }
  })

  it('keeps a non-JSON answer to a snapshot save uncertain', async () => {
    const fetchMock = vi.fn<typeof fetch>(
      async () => new Response('Saved', { status: 201, headers: { 'content-type': 'text/plain' } }),
    )
    const client = new ReviewApiClient({ csrf, buildIdentity: 'c'.repeat(64), fetch: fetchMock })
    await expect(client.policyCommand('drafts', draft)).rejects.toBeInstanceOf(
      AmbiguousMutationError,
    )
  })

  it.each([
    ['P-X1: a comparison per sampled work', sampled({}, 2), 'invalid_policy_preview_comparisons'],
    [
      'P-X2: both sides of a comparison name one field',
      sampled({ active: { ...impact, field: 'genres' } }),
      'invalid_policy_preview_field',
    ],
  ])('refuses a preview that fails %s as a 502', async (_check, answer, code) => {
    const refusal = await answering(answer)
      .client.previewPolicy(draft)
      .catch((error: unknown) => error)
    expect(refusal).toBeInstanceOf(ApiError)
    expect(refusal).not.toBeInstanceOf(AmbiguousMutationError)
    expect(refusal).toMatchObject({ status: 502, code })
  })

  it('accepts an activation of the requested policy', async () => {
    const { client, fetchMock } = answering(activation)
    expect(await client.activatePolicy(request)).toEqual(activation)
    expect(fetchMock.mock.calls[0]?.[0]).toBe('/api/v1/metadata-policy/activate')
    expect(fetchMock.mock.calls[0]?.[1]?.body).toBe(JSON.stringify(request))
  })

  it('reports an activation of another policy as uncertain (P-X3)', async () => {
    const refusal = await answering(activation)
      .client.activatePolicy({ ...request, policy_id: 'policy-other' })
      .catch((error: unknown) => error)
    expect(refusal).toBeInstanceOf(UncertainMutationError)
    expect(refusal).toMatchObject({
      ...uncertain('invariant@invalid_policy_activation_response'),
      operation: 'POST /api/v1/metadata-policy/activate',
      responseStatus: 200,
    })
    expect(uncertainWriteOutcome(refusal)).toBe(true)
  })

  type Unsafe = readonly [operation: string, send: (client: ReviewApiClient) => Promise<unknown>]
  const commands: readonly Unsafe[] = [
    ['POST /api/v1/metadata-policy/drafts', (client) => client.policyCommand('drafts', draft)],
    [
      'POST /api/v1/metadata-policy/automatic-safety/pause',
      (client) => client.policyCommand('automatic-safety/pause', { paused: true }),
    ],
    [
      'POST /api/v1/metadata-policy/automatic-safety/recover',
      (client) => client.policyCommand('automatic-safety/recover', { category: null }),
    ],
    [
      'POST /api/v1/metadata-policy/automatic-safety/suspend',
      (client) => client.policyCommand('automatic-safety/suspend', { reason: 'repeated_failure' }),
    ],
    [
      'POST /api/v1/metadata-policy/preview-mode/enter',
      (client) => client.policyCommand('preview-mode/enter'),
    ],
    [
      'POST /api/v1/metadata-policy/preview-mode/confirm-automatic',
      (client) => client.policyCommand('preview-mode/confirm-automatic'),
    ],
    [
      'POST /api/v1/metadata-policy/transitions/{transition_id}/acknowledge',
      (client) => client.policyCommand('transitions/transition-1/acknowledge'),
    ],
    [
      'POST /api/v1/metadata-policy/transitions/{transition_id}/retry',
      (client) => client.policyCommand('transitions/transition-1/retry'),
    ],
  ]
  it.each<Unsafe>([
    ...commands,
    ['POST /api/v1/metadata-policy/activate', (client) => client.activatePolicy(request)],
  ])('reports an undecodable %s answer as uncertain', async (operation, send) => {
    const status = operation.endsWith('/drafts') ? 201 : 200
    const { client, fetchMock } = answering({ invented: true }, status)
    const refusal = await send(client).catch((error: unknown) => error)
    expect(refusal).toBeInstanceOf(UncertainMutationError)
    expect(refusal).toBeInstanceOf(AmbiguousMutationError)
    expect(refusal).toMatchObject({
      ...uncertain('unknown_key@/invented'),
      operation,
      responseStatus: status,
    })
    expect((refusal as Error).message).not.toContain('/')
    expect(uncertainWriteOutcome(refusal)).toBe(true)
    expect(fetchMock).toHaveBeenCalledOnce()
  })
})

describe('the retained original as a cover candidate (#1363)', () => {
  const retained = (overrides: Record<string, unknown> = {}) =>
    coverCandidate({
      artifact_id: 'retained-artifact',
      candidate_kind: 'retained',
      deterministic_rank: null,
      deterministic_rank_key: null,
      identity: null,
      match_state: 'match_not_confirmed',
      ordinal: 0,
      reference: 'retained-1',
      source_kinds: ['retained'],
      state: 'available',
      ...overrides,
    })
  // The decoder admits the shape; the mapping owns the cross-field rules.
  const read = async (candidate: unknown) =>
    mapCoverConsideration(
      coverConsideration({
        // No reviewed cover, so no policy: the original alone is offered.
        candidates: [candidate],
        ready: (candidate as { selectable: boolean }).selectable,
        choice_contexts: [
          choiceContext(),
          choiceContext({ alternative_id: 'retained-1', selection: 'proposed' }),
        ],
      }) as Parameters<typeof mapCoverConsideration>[0],
    )

  it('maps the original with no identity, rank or match claim', async () => {
    const [mapped] = (await read(retained())).candidates
    expect(mapped).toMatchObject({
      candidateKind: 'retained',
      identity: null,
      deterministicRank: null,
      selectable: true,
      manualEligible: true,
      sourceKinds: ['retained'],
    })
  })

  it.each([
    ['an identity', { identity: coverCandidate().identity }],
    ['a rank', { deterministic_rank: 1 }],
    ['a confirmed match', { match_state: 'same_book' }],
    ['the current cover', { incumbent: true }],
    ['eligibility it cannot be chosen with', { selectable: false }],
    ['another source', { source_kinds: ['open_library'] }],
  ])('refuses an original with %s', async (_name, overrides) => {
    await expect(read(retained(overrides))).rejects.toThrow(ApiError)
  })

  // An original the decoder refused has no size or preview at all (#1362).
  const previewless = { height: null, width: null, preview_sha256: null, transport_sha256: null }

  it('maps an original the decoder refused with no size or preview', async () => {
    const [mapped] = (await read(retained(previewless))).candidates
    expect(mapped).toMatchObject({
      candidateKind: 'retained',
      height: null,
      previewSha256: null,
      width: null,
      selectable: true,
    })
  })

  it.each(Object.keys(previewless))('refuses an original missing only its %s', async (key) => {
    await expect(read(retained({ [key]: null }))).rejects.toThrow(
      expect.objectContaining({ code: 'contradictory_retained_cover' }),
    )
  })

  it.each(['candidate', 'larger_copy'])('refuses a %s with no preview', async (kind) => {
    expect(() =>
      mapCoverConsideration(
        coverConsideration({
          candidates: [coverCandidate({ candidate_kind: kind, ...previewless })],
          ready: true,
          choice_contexts: [
            choiceContext(),
            choiceContext({ alternative_id: 'candidate-1', selection: 'proposed' }),
          ],
          policy: coverPolicyReport(),
        }) as Parameters<typeof mapCoverConsideration>[0],
      ),
    ).toThrow(expect.objectContaining({ code: 'missing_cover_preview' }))
  })
})

describe('a larger copy of the current cover as a candidate (#1363)', () => {
  const read = (candidate: unknown) =>
    mapCoverConsideration(
      coverConsideration({
        candidates: [candidate],
        ready: true,
        choice_contexts: [
          choiceContext(),
          choiceContext({ alternative_id: 'candidate-1', selection: 'proposed' }),
        ],
        policy: coverPolicyReport(),
      }) as Parameters<typeof mapCoverConsideration>[0],
    )

  it('keeps its kind beside its confirmed identity and rank', () => {
    const [mapped] = read(coverCandidate({ candidate_kind: 'larger_copy' })).candidates
    expect(mapped).toMatchObject({
      candidateKind: 'larger_copy',
      matchState: 'same_book',
      deterministicRank: 1,
      selectable: true,
    })
  })

  it('refuses one with no confirmed match', () => {
    expect(() =>
      read(
        coverCandidate({
          candidate_kind: 'larger_copy',
          identity: coverIdentity({ outcome: 'unknown' }),
          match_state: 'match_not_confirmed',
          selectable: false,
          deterministic_rank: null,
          deterministic_rank_key: null,
          state: 'excluded',
        }),
      ),
    ).toThrow(expect.objectContaining({ code: 'contradictory_cover_identity' }))
  })
})

describe('a full comparison with a larger copy and a Put back (#1363, Codex R1)', () => {
  // Twelve reviewed previews, the larger copy and the retained original.
  const consideration = (cards: number) => {
    const card = (ordinal: number) => {
      const reference = `candidate-${String(ordinal)}`
      const kind =
        ordinal === cards - 1 ? 'retained' : ordinal === cards - 2 ? 'larger_copy' : 'candidate'
      return kind === 'retained'
        ? coverCandidate({
            artifact_id: `artifact-${String(ordinal)}`,
            candidate_kind: 'retained',
            deterministic_rank: null,
            deterministic_rank_key: null,
            identity: null,
            match_state: 'match_not_confirmed',
            ordinal,
            reference,
            source_kinds: ['retained'],
            state: 'available',
          })
        : coverCandidate({
            artifact_id: `artifact-${String(ordinal)}`,
            candidate_kind: kind,
            deterministic_rank: null,
            deterministic_rank_key: [1, 0, -10000, -100, -100, ordinal, reference],
            identity: coverIdentity({ candidate_reference: reference }),
            incumbent: ordinal === 0,
            manual_eligible: ordinal !== 0,
            ordinal,
            reference,
            selectable: ordinal !== 0,
            state: ordinal === 0 ? 'excluded' : 'available',
          })
    }
    return coverConsideration({
      candidates: Array.from({ length: cards }, (_, ordinal) => card(ordinal)),
      choice_contexts: [
        choiceContext(),
        ...Array.from({ length: cards - 1 }, (_, index) =>
          choiceContext({
            alternative_id: `candidate-${String(index + 1)}`,
            selection: 'proposed',
          }),
        ),
      ],
      incumbent: { reference: 'candidate-0', state: 'available' },
      policy: coverPolicyReport({ acceptable_candidate_references: [] }),
      ready: true,
    })
  }

  it('reads all fourteen cards', async () => {
    const read = await retryCover(consideration(14))
    expect(read.candidates.map((candidate) => candidate.candidateKind)).toEqual([
      ...Array<string>(12).fill('candidate'),
      'larger_copy',
      'retained',
    ])
  })

  it('refuses a fifteenth card', async () => {
    const refusal: unknown = await retryCover(consideration(15)).catch((error: unknown) => error)
    expect(refusal).toMatchObject({ at: '/candidates' })
  })
})

describe('a cover that contradicts the book (#1790)', () => {
  const unconfirmed = (
    identity: Record<string, unknown>,
    overrides: Record<string, unknown> = {},
  ) =>
    coverCandidate({
      identity: coverIdentity(identity),
      manual_eligible: false,
      match_state: 'match_not_confirmed',
      selectable: false,
      deterministic_rank: null,
      deterministic_rank_key: null,
      state: 'excluded',
      ...overrides,
    })
  const read = (candidate: unknown, overrides: Record<string, unknown> = {}) =>
    mapCoverConsideration(
      coverConsideration({
        candidates: [candidate],
        choice_contexts: [choiceContext()],
        policy: coverPolicyReport({ acceptable_candidate_references: [] }),
        ...overrides,
      }) as Parameters<typeof mapCoverConsideration>[0],
    )

  it.each([
    ['a different book', 'conflict', ['unrelated_identifier'], 'book'],
    ['a different work', 'conflict', ['edition_work_contradiction'], 'book'],
    ['a different edition', 'conflict', ['manifestation_publication_incompatible'], 'edition'],
    ['a different creator', 'unknown', ['retained_creator_or_contributor_disagreement'], 'creator'],
  ])('names %s', (_name, outcome, reasons, contradiction) => {
    const [mapped] = read(unconfirmed({ outcome, reasons })).candidates
    expect(mapped).toMatchObject({ contradiction, manualEligible: false })
  })

  it('leaves an unconfirmed cover with no contradiction unmarked', () => {
    const [mapped] = read(
      unconfirmed(
        { outcome: 'unknown', reasons: ['insufficient_identity_evidence'] },
        { manual_eligible: true },
      ),
      {
        ready: true,
        choice_contexts: [
          choiceContext(),
          choiceContext({ alternative_id: 'candidate-1', selection: 'proposed' }),
        ],
      },
    ).candidates
    expect(mapped?.manualEligible).toBe(true)
    expect(mapped).not.toHaveProperty('contradiction')
  })

  it('never marks the current cover', () => {
    const [mapped] = read(
      unconfirmed({ outcome: 'conflict', reasons: ['unrelated_identifier'] }, { incumbent: true }),
      { incumbent: { reference: 'candidate-1', state: 'available' }, ready: true },
    ).candidates
    expect(mapped?.incumbent).toBe(true)
    expect(mapped).not.toHaveProperty('contradiction')
  })

  it('never marks the retained original', () => {
    const [mapped] = read(
      coverCandidate({
        candidate_kind: 'retained',
        deterministic_rank: null,
        deterministic_rank_key: null,
        identity: null,
        match_state: 'match_not_confirmed',
        reference: 'retained-1',
        source_kinds: ['retained'],
        state: 'available',
      }),
      {
        policy: null,
        ready: true,
        choice_contexts: [
          choiceContext(),
          choiceContext({ alternative_id: 'retained-1', selection: 'proposed' }),
        ],
      },
    ).candidates
    expect(mapped?.candidateKind).toBe('retained')
    expect(mapped).not.toHaveProperty('contradiction')
  })

  it('refuses a contradicting cover the server says can be chosen', () => {
    expect(() =>
      read(
        unconfirmed(
          { outcome: 'conflict', reasons: ['unrelated_identifier'] },
          { manual_eligible: true },
        ),
      ),
    ).toThrow(expect.objectContaining({ code: 'contradictory_cover_manual_eligibility' }))
  })
})

// Covers → Taste profile's read (#1372): decoded whole, then the named checks.
const readTasteProfile = (document: unknown) =>
  new ReviewApiClient({
    csrf,
    fetch: vi.fn<typeof fetch>(async () => Response.json(document)),
  }).getTasteProfile()
const tasteDocument = {
  schema_version: 'review-taste-profile-v4',
  configured: true,
  chosen: 30,
  required: 25,
  daily_limit: 8,
  build: null,
  profile: {
    version: 2,
    kind: 'built',
    built_at: 20,
    decisions: 30,
    rules: [
      { number: 1, section: 'drawn_to', text: 'Bold art.', provenance: 'model' },
      { number: 2, section: 'avoid', text: 'Busy type.', provenance: 'owner' },
    ],
  },
  versions: [
    { version: 2, kind: 'built', created_at: 20, decisions: 30 },
    { version: 1, kind: 'built', created_at: 10, decisions: 25 },
  ],
  judge: { configured: true, daily_limit: 5000, stopped: null, retry_after: null, failed: 0 },
  confirmed: { confirmed: 0, suggested: 0 },
  automatic_available: false,
}
// An edit version keeps its build's facts and its rules' numbers, with gaps.
const editedTasteDocument = {
  ...tasteDocument,
  profile: {
    ...tasteDocument.profile,
    version: 3,
    kind: 'edited',
    rules: [
      { number: 4, section: 'drawn_to', text: 'Calm type.', provenance: 'owner' },
      { number: 2, section: 'avoid', text: 'Busy type!', provenance: 'edited' },
    ],
  },
  versions: [
    { version: 3, kind: 'edited', created_at: 30, decisions: 30 },
    ...tasteDocument.versions,
  ],
}
const failedBuild = (failure: string, retryAfter: number | null) => ({
  build_id: `taste_build_${'a'.repeat(32)}`,
  state: 'failed',
  failure,
  retry_after: retryAfter,
})

// Covers' picks (#1372 PR 3): the calibration set, decoded whole.
describe('cover calibration read (#1372 PR 3)', () => {
  const calibrationWork = (index: number) => ({
    authors: ['An Author'],
    choosable: true,
    cover: {
      height: 900,
      preview_sha256: 'a'.repeat(64),
      url: `/api/v1/library/cover?preview=${'a'.repeat(64)}`,
      width: 600,
    },
    group: 'kept',
    hold: null,
    media_kind: 'epub',
    review_case_id: `review_case_${String(index).padStart(64, '0')}`,
    suggestion: null,
    switched: null,
    title: `Work ${String(index)}`,
    unreadable_cover: false,
  })
  const readCalibration = (document: unknown) => {
    const fetcher = vi.fn<typeof fetch>(async () => Response.json(document))
    return {
      fetcher,
      read: new ReviewApiClient({ csrf, fetch: fetcher }).getCoverCalibration(),
    }
  }

  it('reads the set from its route as decoded', async () => {
    const document = {
      items: [calibrationWork(1), calibrationWork(2)],
      schema_version: 'review-cover-calibration-v3',
    }
    const { fetcher, read } = readCalibration(document)
    const view = await read
    expectTypeOf(view.items).toEqualTypeOf<CoverCalibrationView['items']>()
    expect(view).toEqual(document)
    expect(fetcher.mock.calls[0]?.[0]).toBe('/api/v1/covers/calibration')
  })

  it('refuses an extra member and more than 25 works', async () => {
    await expect(
      readCalibration({ items: [], schema_version: 'review-cover-calibration-v3', next: null })
        .read,
    ).rejects.toMatchObject({ code: 'invalid_response' })
    await expect(
      readCalibration({
        items: Array.from({ length: 26 }, (_, index) => calibrationWork(index)),
        schema_version: 'review-cover-calibration-v3',
      }).read,
    ).rejects.toMatchObject({ code: 'invalid_response' })
  })
})

describe('taste profile decoder (#1372)', () => {
  it('admits a profile, its versions and each build state', async () => {
    expect(await readTasteProfile(tasteDocument)).toEqual(tasteDocument)
    const building = {
      ...tasteDocument,
      build: { ...failedBuild('unavailable', null), state: 'building', failure: null },
    }
    expect((await readTasteProfile(building)).build?.state).toBe('building')
    for (const [failure, retryAfter] of [
      ['ceiling', 86_400],
      ['unknown_charge', 86_400],
      ['interrupted', null],
    ] as const)
      expect(
        (await readTasteProfile({ ...tasteDocument, build: failedBuild(failure, retryAfter) }))
          .build?.failure,
      ).toBe(failure)
  })

  it.each([
    ['a profile with no versions', { versions: [] }],
    ['versions with no profile', { profile: null }],
    ['a profile that is not the newest version', { versions: tasteDocument.versions.slice(1) }],
    [
      'versions out of order',
      {
        versions: [...tasteDocument.versions].reverse(),
        profile: { ...tasteDocument.profile, version: 1, built_at: 10 },
      },
    ],
    [
      'a built profile not built when its version was',
      { profile: { ...tasteDocument.profile, built_at: 19 } },
    ],
    [
      'a profile of another kind than its version',
      { profile: { ...tasteDocument.profile, kind: 'edited' } },
    ],
    [
      'a built profile not numbered from 1',
      {
        profile: {
          ...tasteDocument.profile,
          rules: [
            { number: 1, section: 'drawn_to', text: 'Bold art.', provenance: 'model' },
            { number: 3, section: 'avoid', text: 'Busy type.', provenance: 'model' },
          ],
        },
      },
    ],
    [
      'rules numbered out of order',
      {
        profile: {
          ...tasteDocument.profile,
          rules: [
            { number: 2, section: 'drawn_to', text: 'Bold art.', provenance: 'model' },
            { number: 1, section: 'avoid', text: 'Busy type.', provenance: 'model' },
          ],
        },
      },
    ],
    [
      'an edited profile repeating a number',
      {
        ...editedTasteDocument,
        profile: {
          ...editedTasteDocument.profile,
          rules: [
            { number: 2, section: 'drawn_to', text: 'Calm type.', provenance: 'owner' },
            { number: 2, section: 'avoid', text: 'Busy type!', provenance: 'edited' },
          ],
        },
      },
    ],
    [
      'rules out of section order',
      {
        profile: {
          ...tasteDocument.profile,
          rules: [
            { number: 1, section: 'avoid', text: 'Busy type.', provenance: 'model' },
            { number: 2, section: 'drawn_to', text: 'Bold art.', provenance: 'model' },
          ],
        },
      },
    ],
    ['a ceiling with no reset time', { build: failedBuild('ceiling', null) }],
    ['a reset time on another failure', { build: failedBuild('unavailable', 86_400) }],
    [
      'a running build with a failure',
      { build: { ...failedBuild('unavailable', null), state: 'building' } },
    ],
  ])('refuses %s', async (_name, change) => {
    await expect(readTasteProfile({ ...tasteDocument, ...change })).rejects.toMatchObject({
      status: 502,
      code: 'invalid_taste_profile',
    })
  })

  it('refuses a member the contract does not name', async () => {
    await expect(
      readTasteProfile({ ...tasteDocument, references: ['taste_image_0'] }),
    ).rejects.toBeInstanceOf(Error)
  })

  it('admits an edited profile whose rules keep their numbers with gaps', async () => {
    expect(await readTasteProfile(editedTasteDocument)).toEqual(editedTasteDocument)
  })

  it.each([
    [{ action: 'edit', number: 4, text: 'Calm serif type.' }],
    [{ action: 'add', section: 'trade_offs', text: 'Takes a sharper copy.' }],
    [{ action: 'remove', number: 2 }],
  ] as const)('posts one rule edit with the version shown: %j', async (edit) => {
    const fetchMock = vi.fn<typeof fetch>(async () => Response.json(editedTasteDocument))
    const client = new ReviewApiClient({ csrf, fetch: fetchMock, buildIdentity: 'c'.repeat(64) })
    await expect(client.editTasteRule(3, edit)).resolves.toEqual(editedTasteDocument)
    const [url, init] = fetchMock.mock.calls[0] ?? []
    expect(url).toBe('/api/v1/covers/taste/rules')
    expect(init?.method).toBe('POST')
    expect(JSON.parse(init?.body as string)).toEqual({ version: 3, ...edit })
  })

  it('treats an unreadable answer to a rule edit as an uncertain write', async () => {
    const client = new ReviewApiClient({
      csrf,
      fetch: vi.fn<typeof fetch>(async () =>
        Response.json({ ...editedTasteDocument, versions: [] }),
      ),
      buildIdentity: 'c'.repeat(64),
    })
    const error = await client
      .editTasteRule(3, { action: 'remove', number: 2 })
      .catch((e: unknown) => e)
    expect(uncertainWriteOutcome(error)).toBe(true)
  })

  it('sends a rule in the spelling the server admits', () => {
    expect(normalizeTasteRule('  Bold\u00a0 art\n\tnow  ')).toBe('Bold art now')
    expect(normalizeTasteRule('Cafe\u0301')).toBe('Caf\u00e9')
  })

  it.each([
    ['', 'rule_empty'],
    ['é'.repeat(401), 'rule_too_long'],
    ['😀'.repeat(400), null],
    ['See https://example.invalid', 'address'],
    ['Covers like www.example.invalid', 'address'],
    ['Covers in /books/covers/ here', 'address'],
    // The browser lets these through; the server refuses the second (S13).
    ['and/or both', null],
    ['password=hunter2 style', null],
  ] as const)('refuses %j as %s', (text, refusal) => {
    expect(tasteRuleRefusal(text, [], null)).toBe(refusal)
  })

  it('refuses words past the profile’s bound unless they replace a rule', () => {
    // 30 rules of 40 words is 1,200; ten more words refuses.
    const rules = Array.from({ length: 30 }, (_unused, index) => ({
      number: index + 1,
      text: Array.from({ length: 40 }, () => 'a').join(' '),
    }))
    const ten = Array.from({ length: 10 }, () => 'w').join(' ')
    expect(tasteRuleRefusal(ten, rules, null)).toBe('profile_full')
    expect(tasteRuleRefusal(ten, rules, 1)).toBeNull()
  })

  it('counts the profile’s characters in code points', () => {
    const rules = Array.from({ length: 20 }, (_unused, index) => ({
      number: index + 1,
      text: '😀'.repeat(400),
    }))
    expect(tasteRuleRefusal('😀'.repeat(192), rules, null)).toBeNull()
    expect(tasteRuleRefusal('😀'.repeat(193), rules, null)).toBe('profile_full')
  })

  it('binds taste prices to the saved taste profile model', async () => {
    const metadata = {
      model: 'example/vision',
      observed_at: 1,
      prompt_price: '0.000001',
      completion_price: null,
      image_price: '0',
      request_price: null,
    }
    const value = reviewerAccessPayload({
      taste_mapping: { engine: 'openrouter', model: 'example/vision' },
      taste_metadata: metadata,
    })
    expect((await readReviewerAccess(value)).taste_metadata).toEqual(metadata)
    await expect(
      readReviewerAccess({
        ...value,
        taste_mapping: { engine: 'openrouter', model: 'example/other' },
      }),
    ).rejects.toMatchObject({ status: 502, code: 'invalid_taste_metadata' })
    await expect(
      readReviewerAccess({ ...value, taste_metadata: { ...metadata, image_price: 'NaN' } }),
    ).rejects.toMatchObject({ status: 502, code: 'invalid_cover_metadata' })
    // Codex writes taste profiles too, and has no prices (#1372 C1).
    const codex = reviewerAccessPayload({ taste_mapping: { engine: 'codex' } })
    expect((await readReviewerAccess(codex)).taste_mapping).toEqual({ engine: 'codex' })
    await expect(readReviewerAccess({ ...codex, taste_metadata: metadata })).rejects.toMatchObject({
      status: 502,
      code: 'invalid_taste_metadata',
    })
    await expect(
      readReviewerAccess({
        ...codex,
        taste_mapping: { engine: 'codex', model: 'gpt-5.6-sol' },
      }),
    ).rejects.toBeInstanceOf(Error)
  })

  it('binds judge prices to the saved suggestion model (#1372 PR 4)', async () => {
    const metadata = {
      model: 'example/vision',
      observed_at: 1,
      prompt_price: '0.000001',
      completion_price: null,
      image_price: '0',
      request_price: null,
    }
    const value = reviewerAccessPayload({
      judge_mapping: { engine: 'openrouter', model: 'example/vision' },
      judge_metadata: metadata,
    })
    expect((await readReviewerAccess(value)).judge_metadata).toEqual(metadata)
    await expect(
      readReviewerAccess({
        ...value,
        judge_mapping: { engine: 'openrouter', model: 'example/other' },
      }),
    ).rejects.toMatchObject({ status: 502, code: 'invalid_taste_metadata' })
    const codex = reviewerAccessPayload({ judge_mapping: { engine: 'codex' } })
    expect((await readReviewerAccess(codex)).judge_mapping).toEqual({ engine: 'codex' })
    await expect(readReviewerAccess({ ...codex, judge_metadata: metadata })).rejects.toMatchObject({
      status: 502,
      code: 'invalid_taste_metadata',
    })
  })

  it('refuses a reviewer-access answer without the judge members', async () => {
    // Every answer carries them; a stub without them fails the read whole.
    const rest = Object.fromEntries(
      Object.entries(reviewerAccessPayload()).filter(([key]) => !key.startsWith('judge_')),
    )
    await expect(readReviewerAccess(rest)).rejects.toMatchObject({
      status: 502,
      code: 'invalid_response',
    })
  })
})

describe('Library progress (#1335)', () => {
  const answering = (response: Response) =>
    new ReviewApiClient({ csrf, fetch: vi.fn<typeof fetch>(async () => response) })

  it('reads the captured answer as Library counts per library and per field', async () => {
    const fetchMock = vi.fn<typeof fetch>(async () => Response.json(libraryProgressCapture.body))
    const view = await new ReviewApiClient({ csrf, fetch: fetchMock }).getLibraryProgress()
    expect(fetchMock.mock.calls[0]?.[0]).toBe('/api/v1/library/progress')
    expect(view).toEqual({
      totals: { all: 2, attention: 1, clear: 0, notMatched: 1, failed: 0, flagged: 0, new: 0 },
      libraries: [
        {
          connection: 'primary',
          library: 'audiobooks',
          libraryDisplayName: 'audiobooks',
          counts: { all: 1, attention: 0, clear: 0, notMatched: 1, failed: 0, flagged: 0, new: 0 },
        },
        {
          connection: 'primary',
          library: 'ebooks',
          libraryDisplayName: 'ebooks',
          counts: { all: 1, attention: 1, clear: 0, notMatched: 0, failed: 0, flagged: 0, new: 0 },
        },
      ],
      fields: [{ destination: 'title', needYou: 1 }],
    })
  })

  it('refuses a field outside the vocabulary or one nobody needs', async () => {
    const body = libraryProgressCapture.body
    await expect(
      answering(
        Response.json({ ...body, fields: [{ destination: 'shelf', need_you: 1 }] }),
      ).getLibraryProgress(),
    ).rejects.toMatchObject(refused('enum@/fields/0/destination'))
    await expect(
      answering(
        Response.json({ ...body, fields: [{ destination: 'title', need_you: 0 }] }),
      ).getLibraryProgress(),
    ).rejects.toMatchObject(refused('min@/fields/0/need_you'))
    await expect(
      answering(Response.json({ ...body, run_id: 'run-1' })).getLibraryProgress(),
    ).rejects.toBeInstanceOf(ResponseDecodeError)
  })

  it('hands an expired session to sign-in and keeps a storage fault as 503', async () => {
    const expired = vi.fn()
    await expect(
      new ReviewApiClient({
        csrf,
        fetch: vi.fn(async () => Response.json({ error: { code: 'session' } }, { status: 401 })),
        onSessionExpired: expired,
      }).getLibraryProgress(),
    ).rejects.toBeInstanceOf(SessionExpiredError)
    await vi.waitFor(() => expect(expired).toHaveBeenCalledOnce())
    await expect(
      answering(
        Response.json(
          { error: { code: 'unavailable' }, schema_version: 'review-error-v1' },
          { status: 503 },
        ),
      ).getLibraryProgress(),
    ).rejects.toMatchObject({ status: 503, code: 'unavailable' })
  })

  it('refuses an answer from another build and raises the shell recovery', async () => {
    const mismatch = vi.fn()
    await expect(
      new ReviewApiClient({
        csrf,
        buildIdentity: 'c'.repeat(64),
        fetch: vi.fn(async () =>
          Response.json(libraryProgressCapture.body, {
            headers: { 'x-ml-build-identity': 'e'.repeat(64) },
          }),
        ),
        onBuildMismatch: mismatch,
      }).getLibraryProgress(),
    ).rejects.toBeInstanceOf(BuildMismatchError)
    expect(mismatch).toHaveBeenCalledOnce()
  })
})

describe('changePassword', () => {
  const build = 'c'.repeat(64)
  const body = {
    current_password: 'old horse battery',
    new_password: 'correct horse battery staple',
    confirmation: 'correct horse battery staple',
  }
  const changed = () => new Response(null, { status: 204 })
  const gone = () => Response.json({ error: { code: 'unauthorized' } }, { status: 401 })
  const refused = (status: number, code: string) => Response.json({ error: { code } }, { status })
  const flush = () => new Promise<void>((resolve) => window.setTimeout(resolve, 0))
  const client = (
    fetchMock: typeof fetch,
    tokens: { current: () => string; renew: () => Promise<string> } = {
      current: () => token,
      renew: vi.fn(async () => renewedToken),
    },
  ) => {
    const expired = vi.fn()
    return {
      expired,
      tokens,
      client: new ReviewApiClient({
        csrf: tokens,
        fetch: fetchMock,
        buildIdentity: build,
        onSessionExpired: expired,
      }),
    }
  }
  // The change is held in flight while another request is answered.
  const holding = () => {
    let release: (response: Response) => void = () => undefined
    const fetchMock = vi.fn<typeof fetch>()
    fetchMock.mockImplementationOnce(() => new Promise<Response>((resolve) => (release = resolve)))
    fetchMock.mockResolvedValue(gone())
    return { fetchMock, release: (response: Response) => release(response) }
  }

  it('posts the three passwords with the build identity and CSRF token, then renews the token', async () => {
    const fetchMock = vi.fn<typeof fetch>(async () => changed())
    const { client: http, tokens } = client(fetchMock)

    await expect(http.changePassword(body)).resolves.toBeUndefined()

    const [path, init] = fetchMock.mock.calls[0] ?? []
    expect(path).toBe('/api/v1/session/password')
    expect(init?.method).toBe('POST')
    expect(JSON.parse(init?.body as string)).toEqual(body)
    const headers = new Headers(init?.headers)
    expect(headers.get('x-ml-build-identity')).toBe(build)
    expect(headers.get('x-csrf-token')).toBe(token)
    expect(headers.get('content-type')).toBe('application/json')
    expect(tokens.renew).toHaveBeenCalledOnce()
  })

  it('succeeds although the token renewal after the change fails', async () => {
    const renew = vi.fn(async () => {
      throw new SessionExpiredError()
    })
    const { client: http, expired } = client(
      vi.fn<typeof fetch>(async () => changed()),
      { current: () => token, renew },
    )

    await expect(http.changePassword(body)).resolves.toBeUndefined()
    expect(renew).toHaveBeenCalledOnce()
    await flush()
    expect(expired).not.toHaveBeenCalled()
  })

  it('never sends the change again after a CSRF challenge', async () => {
    const fetchMock = vi.fn<typeof fetch>(async () => refused(403, 'csrf'))
    const { client: http, tokens } = client(fetchMock)

    await expect(http.changePassword(body)).rejects.toBeInstanceOf(CsrfRenewedError)
    expect(fetchMock).toHaveBeenCalledOnce()
    expect(tokens.renew).toHaveBeenCalledOnce()
  })

  it('discards a 401 a parallel request got while the change succeeded', async () => {
    const { fetchMock, release } = holding()
    const { client: http, expired } = client(fetchMock)

    const change = http.changePassword(body)
    await expect(http.getAttention({})).rejects.toBeInstanceOf(SessionExpiredError)
    await flush()
    expect(expired).not.toHaveBeenCalled()
    release(changed())
    await change
    await flush()
    expect(expired).not.toHaveBeenCalled()
    // The mark clears: a later 401 goes to sign-in.
    await expect(http.getLibrary({ lens: 'all' })).rejects.toBeInstanceOf(SessionExpiredError)
    await vi.waitFor(() => expect(expired).toHaveBeenCalledOnce())
  })

  it('ignores a 401 answered after the change for a request that left before it', async () => {
    const answers: ((response: Response) => void)[] = []
    const fetchMock = vi.fn<typeof fetch>(
      () => new Promise<Response>((resolve) => answers.push(resolve)),
    )
    const { client: http, expired } = client(fetchMock)

    const change = http.changePassword(body)
    const earlier = http.getAttention({})
    answers[0]?.(changed())
    await change
    // The earlier request carried the replaced session's cookie.
    answers[1]?.(gone())
    await expect(earlier).rejects.toBeInstanceOf(SessionExpiredError)
    await flush()
    expect(expired).not.toHaveBeenCalled()
    // A request that leaves on the new session and answers 401 goes to sign-in.
    const later = http.getLibrary({ lens: 'all' })
    answers[2]?.(gone())
    await expect(later).rejects.toBeInstanceOf(SessionExpiredError)
    await vi.waitFor(() => expect(expired).toHaveBeenCalledOnce())
  })

  it.each([
    ['a refusal', refused(403, 'current_password_invalid'), { status: 403 }],
    ['a lost answer', undefined, { name: 'AmbiguousMutationError' }],
  ])('replays a 401 held back during the change after %s', async (_, answer, failure) => {
    const { fetchMock, release } = holding()
    const { client: http, expired } = client(fetchMock)

    const change = http.changePassword(body)
    await expect(http.getAttention({})).rejects.toBeInstanceOf(SessionExpiredError)
    await flush()
    expect(expired).not.toHaveBeenCalled()
    if (answer === undefined)
      release(Promise.reject(new TypeError('network')) as unknown as Response)
    else release(answer)
    await expect(change).rejects.toMatchObject(failure)
    await vi.waitFor(() => expect(expired).toHaveBeenCalledOnce())
  })

  it('goes to sign-in when the change itself answers 401', async () => {
    const { client: http, expired } = client(vi.fn<typeof fetch>(async () => gone()))

    await expect(http.changePassword(body)).rejects.toBeInstanceOf(SessionExpiredError)
    await vi.waitFor(() => expect(expired).toHaveBeenCalledOnce())
  })

  it('reports a lost answer as uncertain, and a refusal as itself', async () => {
    const lost = client(
      vi.fn<typeof fetch>(async () => {
        throw new TypeError('network')
      }),
    )
    await expect(lost.client.changePassword(body)).rejects.toBeInstanceOf(AmbiguousMutationError)
    expect(lost.tokens.renew).not.toHaveBeenCalled()
    const wrong = client(vi.fn<typeof fetch>(async () => refused(429, 'throttled')))
    await expect(wrong.client.changePassword(body)).rejects.toMatchObject({
      status: 429,
      code: 'throttled',
    })
    expect(wrong.tokens.renew).not.toHaveBeenCalled()
  })

  it('names a 204-only operation that answers nothing', () => {
    expectTypeOf<RequestAnswer<'POST', '/api/v1/session/password'>>().toEqualTypeOf<undefined>()
    expectTypeOf<'/api/v1/session/password'>().toExtend<RoutablePath<'POST'>>()
    expectTypeOf<Parameters<ReviewApiClient['changePassword']>[0]>().toEqualTypeOf<
      components['schemas']['_PasswordChangeRequest']
    >()
    expect(matchOperation('POST', '/api/v1/session/password')).toBeUndefined()
  })
})

describe('logoutOthers', () => {
  const build = 'c'.repeat(64)
  const route = '/api/v1/session/logout-others'
  const ended = () => new Response(null, { status: 204 })
  const gone = () => Response.json({ error: { code: 'unauthorized' } }, { status: 401 })
  const refused = (status: number, code: string) => Response.json({ error: { code } }, { status })
  const flush = () => new Promise<void>((resolve) => window.setTimeout(resolve, 0))
  const client = (fetchMock: typeof fetch, buildIdentity: string | null = build) => {
    const expired = vi.fn()
    const mismatch = vi.fn()
    const tokens = { current: () => token, renew: vi.fn(async () => renewedToken) }
    return {
      expired,
      mismatch,
      tokens,
      client: new ReviewApiClient({
        csrf: tokens,
        fetch: fetchMock,
        ...(buildIdentity === null ? {} : { buildIdentity }),
        onSessionExpired: expired,
        onBuildMismatch: mismatch,
      }),
    }
  }

  it('posts to the sign-out route with build identity and CSRF and no body', async () => {
    const fetchMock = vi.fn<typeof fetch>(async () => ended())
    const { client: http, tokens } = client(fetchMock)

    await expect(http.logoutOthers()).resolves.toBeUndefined()

    expect(fetchMock).toHaveBeenCalledOnce()
    const [path, init] = fetchMock.mock.calls[0] ?? []
    expect(path).toBe(route)
    expect(init?.method).toBe('POST')
    expect(init?.body).toBeUndefined()
    const headers = new Headers(init?.headers)
    expect(headers.get('x-ml-build-identity')).toBe(build)
    expect(headers.get('x-csrf-token')).toBe(token)
    expect(headers.get('content-type')).toBeNull()
    // This session and its token are unchanged, so nothing is renewed.
    expect(tokens.renew).not.toHaveBeenCalled()
  })

  it('never sends again after a CSRF challenge', async () => {
    const fetchMock = vi.fn<typeof fetch>(async () => refused(403, 'csrf'))
    const { client: http, tokens } = client(fetchMock)

    await expect(http.logoutOthers()).rejects.toBeInstanceOf(CsrfRenewedError)
    expect(fetchMock).toHaveBeenCalledOnce()
    expect(tokens.renew).toHaveBeenCalledOnce()
  })

  it('reports a lost answer as uncertain', async () => {
    const fetchMock = vi.fn<typeof fetch>(async () => {
      throw new TypeError('network')
    })
    const { client: http } = client(fetchMock)

    const failure = await http.logoutOthers().catch((error: unknown) => error)
    expect(failure).toBeInstanceOf(AmbiguousMutationError)
    expect(uncertainWriteOutcome(failure)).toBe(true)
    expect(fetchMock).toHaveBeenCalledOnce()
  })

  it('reports a body in a 204-only answer as uncertain', async () => {
    const fetchMock = vi.fn<typeof fetch>(async () => Response.json({ signed_out: 2 }))
    const { client: http } = client(fetchMock)

    const failure = await http.logoutOthers().catch((error: unknown) => error)
    expect(failure).toBeInstanceOf(UncertainMutationError)
    expect(failure).toMatchObject({
      ...uncertain('no_decoder@'),
      operation: `POST ${route}`,
      responseStatus: 200,
    })
    expect(fetchMock).toHaveBeenCalledOnce()
  })

  it('reads a 503 as uncertain and a pre-call 503 as a refusal', async () => {
    const lost = await client(vi.fn<typeof fetch>(async () => refused(503, 'persistence')))
      .client.logoutOthers()
      .catch((error: unknown) => error)
    expect(lost).toMatchObject({ status: 503, code: 'persistence' })
    expect(uncertainWriteOutcome(lost)).toBe(true)
    const recovering = await client(
      vi.fn<typeof fetch>(async () => refused(503, 'react_assets_unavailable')),
    )
      .client.logoutOthers()
      .catch((error: unknown) => error)
    expect(recovering).toMatchObject({ status: 503, code: 'react_assets_unavailable' })
    expect(uncertainWriteOutcome(recovering)).toBe(false)
  })

  it('hands a 401 to session expiry at once', async () => {
    const { client: http, expired } = client(vi.fn<typeof fetch>(async () => gone()))

    await expect(http.logoutOthers()).rejects.toBeInstanceOf(SessionExpiredError)
    await vi.waitFor(() => expect(expired).toHaveBeenCalledOnce())
  })

  it('holds back no 401 a parallel request gets while it is in flight', async () => {
    let release: (response: Response) => void = () => undefined
    const fetchMock = vi.fn<typeof fetch>()
    fetchMock.mockImplementationOnce(() => new Promise<Response>((resolve) => (release = resolve)))
    fetchMock.mockResolvedValue(gone())
    const { client: http, expired } = client(fetchMock)

    const signingOut = http.logoutOthers()
    await expect(http.getAttention({})).rejects.toBeInstanceOf(SessionExpiredError)
    await vi.waitFor(() => expect(expired).toHaveBeenCalledOnce())
    release(ended())
    await expect(signingOut).resolves.toBeUndefined()
    await flush()
    expect(expired).toHaveBeenCalledOnce()
  })

  it('refuses without a build identity before sending', async () => {
    const fetchMock = vi.fn<typeof fetch>()
    const { client: http, mismatch } = client(fetchMock, null)

    await expect(http.logoutOthers()).rejects.toBeInstanceOf(BuildMismatchError)
    expect(fetchMock).not.toHaveBeenCalled()
    expect(mismatch).toHaveBeenCalledOnce()
  })
})

describe('the book link wire (#1477)', () => {
  const address = { connection: 'local', library: 'Books', item: 'item-1' }
  const answering = (document: unknown, status = 200) => {
    const fetchMock = vi.fn<typeof fetch>(async () => Response.json(document, { status }))
    return {
      fetchMock,
      http: new ReviewApiClient({ csrf, fetch: fetchMock, buildIdentity: 'd'.repeat(64) }),
    }
  }
  const linkRow = `owner_link_${'a'.repeat(64)}`
  const current = (overrides: Record<string, unknown> = {}) => ({
    creators: ['Frank Herbert'],
    identifier: null,
    link_row_id: linkRow,
    paused: false,
    provider: 'hardcover',
    reason: null,
    retry_at: null,
    scope: 'work',
    source: 'search',
    state: 'linked',
    title: 'Dune',
    ...overrides,
  })
  const linkView = (overrides: Record<string, unknown> = {}) => ({
    link: null,
    offered: true,
    prefill: { asin: null, authors: ['Frank Herbert'], isbn: null, title: 'Dune' },
    schema_version: 'owner-link-view-v3',
    sources: [
      { lanes: ['title_authors', 'isbn'], marketplaces: null, provider: 'hardcover' },
      { lanes: ['title_authors', 'asin'], marketplaces: ['us', 'uk'], provider: 'audible_catalog' },
    ],
    ...overrides,
  })

  it('reads the link view at the work’s address only', async () => {
    const { fetchMock, http } = answering(linkView({ link: current() }))
    const view = await http.getOwnerLinkView({ ...address, title: 'Dune' } as typeof address)
    expect(view.link).toMatchObject({ state: 'linked', provider: 'hardcover' })
    expect(view.sources).toHaveLength(2)
    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      '/api/v1/library/link?connection=local&library=Books&item=item-1',
    )
  })

  it.each([
    ['a work link with an identifier', { link: current({ identifier: '9780441172719' }) }],
    ['an edition link with none', { link: current({ scope: 'edition' }) }],
    ['a retry outside waiting', { link: current({ retry_at: 10 }) }],
    ['a reason linked does not carry', { link: current({ reason: 'format' }) }],
    ['contradicted with no reason', { link: current({ state: 'contradicted' }) }],
    [
      'needs replacing for a contradiction',
      { link: current({ state: 'needs_replacing', reason: 'runtime' }) },
    ],
  ])('refuses %s', async (_name, overrides) => {
    await expect(
      answering(linkView(overrides)).http.getOwnerLinkView(address),
    ).rejects.toMatchObject({ status: 502, code: 'contradictory_owner_link' })
  })

  it.each([
    [
      'marketplaces on another source',
      [{ lanes: ['isbn'], marketplaces: ['us'], provider: 'hardcover' }],
    ],
    [
      'Audible with no marketplaces',
      [{ lanes: ['asin'], marketplaces: null, provider: 'audible_catalog' }],
    ],
    ['no lanes', [{ lanes: [], marketplaces: null, provider: 'hardcover' }]],
    ['an ASIN lane off Audible', [{ lanes: ['asin'], marketplaces: null, provider: 'hardcover' }]],
    ['a lane twice', [{ lanes: ['isbn', 'isbn'], marketplaces: null, provider: 'hardcover' }]],
    [
      'a source twice',
      [
        { lanes: ['isbn'], marketplaces: null, provider: 'hardcover' },
        { lanes: ['title_authors'], marketplaces: null, provider: 'hardcover' },
      ],
    ],
  ])('refuses a source with %s', async (_name, sources) => {
    await expect(
      answering(linkView({ sources })).http.getOwnerLinkView(address),
    ).rejects.toMatchObject({ status: 502, code: 'contradictory_owner_link_source' })
  })

  const searchCard = (overrides: Record<string, unknown> = {}) => ({
    asin: null,
    choosable: true,
    contradictions: [],
    creators: ['Frank Herbert'],
    isbn: '9780441172719',
    language: null,
    narrators: [],
    position: 0,
    preview: false,
    publisher: null,
    runtime_seconds: null,
    scopes: ['work', 'edition'],
    subtitle: null,
    title: 'Dune',
    withheld: [],
    year: 1965,
    ...overrides,
  })
  const search = (overrides: Record<string, unknown> = {}) => ({
    cards: [searchCard(), searchCard({ position: 1 })],
    dropped: 1,
    lane: 'title_authors',
    marketplace: null,
    more: true,
    provider: 'hardcover',
    schema_version: 'owner-search-v1',
    search_id: 'search-1',
    ...overrides,
  })
  const request = {
    ...address,
    asin: null,
    authors: ['Frank Herbert'],
    isbn: null,
    lane: 'title_authors' as const,
    marketplace: null,
    provider: 'hardcover',
    title: 'Dune',
  }

  it('reads a search that answers what was asked', async () => {
    const { fetchMock, http } = answering(search())
    const read = await http.searchOwnerLink(request)
    expect(read).toMatchObject({ search_id: 'search-1', dropped: 1, more: true })
    expect(read.cards.map((card) => card.position)).toEqual([0, 1])
    expect(fetchMock).toHaveBeenCalledOnce()
  })

  it.each([
    ['another source', { provider: 'open_library' }],
    ['another lane', { lane: 'isbn' }],
    ['a marketplace off Audible', { marketplace: 'us' }],
    ['cards out of order', { cards: [searchCard({ position: 1 }), searchCard()] }],
    ['a choosable card without This book', { cards: [searchCard({ scopes: ['edition'] })] }],
    [
      'an unchoosable card with a scope',
      { cards: [searchCard({ choosable: false, scopes: ['edition'] })] },
    ],
    [
      'a scope both offered and withheld',
      {
        cards: [searchCard({ withheld: [{ scope: 'edition', reason: 'format_print' }] })],
      },
    ],
  ])('refuses a search with %s', async (_name, overrides) => {
    const refusal: unknown = await answering(search(overrides))
      .http.searchOwnerLink(request)
      .catch((error: unknown) => error)
    expect(refusal).toMatchObject({ status: 502, code: 'contradictory_owner_search' })
  })

  it('carries a refusal’s retry time and kind', async () => {
    const refusal: unknown = await answering(
      { error: { code: 'source_busy', kind: 'rate_limited', retry_at: 1_800_000_000 } },
      429,
    )
      .http.searchOwnerLink(request)
      .catch((error: unknown) => error)
    expect(refusal).toBeInstanceOf(ApiError)
    expect(refusal).toMatchObject({
      status: 429,
      code: 'source_busy',
      kind: 'rate_limited',
      retryAt: 1_800_000_000,
    })
  })

  it('sends a choice and a withdrawal with their key and reads what they recorded', async () => {
    const change = (action: string) => ({
      action,
      link_row_id: linkRow,
      scan_requested: true,
      schema_version: 'owner-link-change-v1',
    })
    const chose = answering(change('link'))
    await expect(
      chose.http.chooseOwnerLink('search-1', { position: 0, scope: 'work' }, 'key-'.repeat(5)),
    ).resolves.toMatchObject({ action: 'link' })
    const [path, init] = chose.fetchMock.mock.calls[0] ?? []
    expect(path).toBe('/api/v1/library/find/searches/search-1/choice')
    expect(new Headers(init?.headers).get('idempotency-key')).toBe('key-'.repeat(5))
    expect(JSON.parse(init?.body as string)).toEqual({ position: 0, scope: 'work' })

    const withdrew = answering(change('withdraw'))
    await withdrew.http.withdrawOwnerLink(
      { ...address, title: 'Dune' } as typeof address,
      linkRow,
      'key-'.repeat(5),
    )
    expect(JSON.parse(withdrew.fetchMock.mock.calls[0]?.[1]?.body as string)).toEqual({
      ...address,
      link_row_id: linkRow,
    })
    const refusal: unknown = await answering(change('link'))
      .http.withdrawOwnerLink(address, linkRow, 'key-'.repeat(5))
      .catch((error: unknown) => error)
    // An unsafe write's contradictory answer leaves its outcome uncertain.
    expect(refusal).toMatchObject({ name: 'UncertainMutationError' })
  })

  it('reads a link event in a work’s History', async () => {
    const item = {
      identifier: 'B000000001',
      kind: 'link_changed',
      link_row_id: linkRow,
      occurred_at: 5,
      provider: 'audible_catalog',
      review_case_id: 'case-1',
      schema_version: 'owner-link-history-event-v2',
      scope: 'recording',
      source: 'search',
      title: 'Dune',
      with_import: false,
    }
    expect((await readHistory(caseHistory({ items: [item] }))).items).toEqual([
      {
        category: 'owner_link',
        occurredAt: 5,
        kind: 'link_changed',
        source: 'search',
        withImport: false,
        identifier: 'B000000001',
        provider: 'audible_catalog',
        scope: 'recording',
        title: 'Dune',
      },
    ])
  })

  it('reads a file link event, which names no source or title (#1478)', async () => {
    const item = {
      identifier: '9780441172719',
      kind: 'linked',
      link_row_id: linkRow,
      occurred_at: 5,
      provider: null,
      review_case_id: 'case-1',
      schema_version: 'owner-link-history-event-v2',
      scope: 'edition',
      source: 'file',
      title: null,
      with_import: true,
    }
    expect((await readHistory(caseHistory({ items: [item] }))).items).toEqual([
      {
        category: 'owner_link',
        occurredAt: 5,
        kind: 'linked',
        source: 'file',
        withImport: true,
        identifier: '9780441172719',
        provider: null,
        scope: 'edition',
        title: null,
      },
    ])
  })
})

describe('a cover from the linked book (#1477)', () => {
  const read = (candidate: unknown) =>
    mapCoverConsideration(
      coverConsideration({
        candidates: [candidate],
        choice_contexts: [
          choiceContext(),
          choiceContext({ alternative_id: 'candidate-1', selection: 'proposed' }),
        ],
        policy: coverPolicyReport({ acceptable_candidate_references: [] }),
        ready: true,
      }) as Parameters<typeof mapCoverConsideration>[0],
    )
  const fromLink = (overrides: Record<string, unknown> = {}) =>
    coverCandidate({
      identity: coverIdentity({ outcome: 'owner_link_match' }),
      match_reason: 'linked_book',
      match_state: 'same_book',
      selectable: false,
      deterministic_rank: null,
      deterministic_rank_key: null,
      state: 'excluded',
      ...overrides,
    })

  it('reads as the same book, never selectable or ranked', () => {
    const [mapped] = read(fromLink()).candidates
    expect(mapped).toMatchObject({
      matchState: 'same_book',
      matchReason: 'linked_book',
      selectable: false,
      deterministicRank: null,
      deterministicRankKey: null,
    })
  })

  it('reads a title-and-author match through the link as unconfirmed', () => {
    const [mapped] = read(
      fromLink({
        identity: coverIdentity({ outcome: 'owner_link_anchor_match' }),
        match_reason: 'linked_title_and_author',
        match_state: 'match_not_confirmed',
        state: 'excluded',
      }),
    ).candidates
    expect(mapped).toMatchObject({
      matchState: 'match_not_confirmed',
      matchReason: 'linked_title_and_author',
    })
  })

  it.each([
    ['selectable', { selectable: true }, 'contradictory_cover_identity'],
    ['ranked', { deterministic_rank: 1 }, 'contradictory_cover_identity'],
    [
      'with a rank key',
      { deterministic_rank_key: [1, 0, -10000, -100, -100, 1, 'candidate-1'] },
      'contradictory_cover_identity',
    ],
    ['with another reason', { match_reason: null }, 'contradictory_cover_match_reason'],
  ])('refuses one %s', (_name, overrides, code) => {
    expect(() => read(fromLink(overrides))).toThrow(expect.objectContaining({ code }))
  })

  it('reads the current cover from the linked book, unranked and with no reason', () => {
    const current = fromLink({ incumbent: true, manual_eligible: false, match_reason: null })
    const view = mapCoverConsideration(
      coverConsideration({
        candidates: [current],
        choice_contexts: [choiceContext()],
        incumbent: { state: 'available', reference: 'candidate-1' },
        policy: coverPolicyReport({ acceptable_candidate_references: [] }),
        ready: true,
      }) as Parameters<typeof mapCoverConsideration>[0],
    )
    expect(view.candidates[0]).toMatchObject({
      incumbent: true,
      matchState: 'same_book',
      selectable: false,
      deterministicRankKey: null,
    })
    expect(view.candidates[0]).not.toHaveProperty('matchReason')
  })

  it('refuses the linked-book reason on any other cover', () => {
    expect(() => read(coverCandidate({ match_reason: 'linked_book' }))).toThrow(
      expect.objectContaining({ code: 'contradictory_cover_match_reason' }),
    )
  })
})

// An owner's clear and names entry on the wire (#1523, ADR-0105): each member
// is decoded closed, and the empty kind only where an owner decision stands.
describe('owner clear and names on the wire', () => {
  const EMPTY = { kind: 'empty', value: null }
  const ownerControl = {
    entry: null,
    subset: null,
    names: { max_items: 32, max_characters: 512, previous: ['Old Writer'] },
    clear: false,
    current: ['Ada Example'],
    withdraw: null,
  }
  const readField = async (field: Record<string, unknown>) =>
    (await readCase(caseDetail({ fields: [reviewField(field)] }))).fields[0]

  it('maps names entry, the clear flag and the current value', async () => {
    expect((await readField({ owner_control: ownerControl }))?.ownerControl).toEqual({
      entry: null,
      subset: null,
      names: { maxItems: 32, maxCharacters: 512, previous: ['Old Writer'] },
      clear: false,
      current: ['Ada Example'],
      withdraw: null,
    })
    const text = { ...ownerControl, names: null, clear: true, current: 'A Quiet Subtitle' }
    expect((await readField({ owner_control: text }))?.ownerControl).toMatchObject({
      clear: true,
      current: 'A Quiet Subtitle',
    })
  })

  it.each([
    [{ ...ownerControl, names: { ...ownerControl.names, extra: 1 } }, 'unknown_key'],
    [{ ...ownerControl, names: { ...ownerControl.names, max_items: 33 } }, 'const'],
    [{ ...ownerControl, clear: undefined }, 'required'],
    [{ ...ownerControl, current: 7 }, 'no_branch'],
    [{ ...ownerControl, empty_previous: true }, 'unknown_key'],
  ])('refuses an owner control the contract does not state: %j', async (control, reason) => {
    await expect(readField({ owner_control: control })).rejects.toMatchObject({
      reason,
    })
  })

  it('reads a clear on an owner decision and refuses it on an automatic one', async () => {
    const owner = await readField({
      human_decision: decisionProjection({
        destination: 'title',
        outcome: 'owner_entered',
        selected_value: EMPTY,
      }),
    })
    expect(owner?.humanDecision?.selected_value).toEqual(EMPTY)
    await expect(
      readField({
        automatic_decision: {
          candidate_id: null,
          decision_id: 'automatic-1',
          destination: 'title',
          occurred_at: 3,
          outcome: 'candidate',
          proposal_id: 'proposal-1',
          review_case_id: 'case-1',
          selected_value: EMPTY,
          target_fingerprint: 'fingerprint-1',
        },
      }),
    ).rejects.toThrow(ApiError)
  })

  it('reads a clear in History only on an owner decision', async () => {
    const history = await readHistory(
      caseHistory({
        items: [
          historyDecisionItem({
            destination: 'subtitle',
            outcome: 'owner_entered',
            previous_value: { kind: 'text', value: 'A Quiet Subtitle' },
            selected_value: EMPTY,
          }),
        ],
      }),
    )
    expect(history.items[0]).toMatchObject({ category: 'owner', selectedValue: EMPTY })
    await expect(
      readHistory(
        caseHistory({
          items: [
            {
              decision_id: 'automatic-1',
              destination: 'subtitle',
              kind: 'automatic_decision_recorded',
              occurred_at: 1,
              origin: 'automatic_policy',
              outcome: 'candidate',
              previous_value: null,
              review_case_id: 'case-1',
              schema_version: 'automatic-decision-history-event-v1',
              selected_value: EMPTY,
            },
          ],
        }),
      ),
    ).rejects.toThrow(ApiError)
  })

  it('reads a v2 acknowledgement of a clear and refuses the v1 one', async () => {
    const command = {
      destination: 'subtitle',
      outcome: 'owner_entered' as const,
      proposalId: 'proposal-1',
      selectedValue: { kind: 'empty' as const, value: null },
      targetFingerprint: 'fingerprint-1',
      offerDigest: 'offer-digest',
      idempotencyKey: 'clear-subtitle',
    }
    const answer = (overrides: Record<string, unknown>) =>
      new ReviewApiClient({
        csrf,
        buildIdentity: 'c'.repeat(64),
        fetch: vi.fn<typeof fetch>(async () =>
          Response.json(
            decisionAcknowledgement({
              destination: 'subtitle',
              outcome: 'owner_entered',
              selected_value: EMPTY,
              confirmation: { status: 'unavailable' },
              ...overrides,
            }),
          ),
        ),
      }).recordDecision('case-1', command)
    expect(await answer({})).toMatchObject({
      schema_version: 'human-decision-ack-v2' as const,
      selected_value: EMPTY,
    })
    await expect(answer({ schema_version: 'human-decision-ack-v1' })).rejects.toMatchObject({
      reason: 'const',
    })
  })
})

describe('the identifiers file wire (#1478)', () => {
  const build = 'e'.repeat(64)
  const preview = (overrides: Record<string, unknown> = {}) => ({
    preview_id: 'preview-1',
    expires_at: 1900,
    rows: 2,
    counts: {
      will_link: 1,
      already_linked: 0,
      no_item: 1,
      ambiguous: 0,
      invalid_identifier: 0,
      wrong_medium: 0,
      repeated: 0,
      conflicting: 0,
      unreadable: 0,
    },
    will_link: [
      {
        row: 2,
        title: 'Title 1',
        authors: [],
        identifier_kind: 'isbn',
        identifier: '9780306406157',
      },
    ],
    refused: [{ row: 3, reason: 'no_item' }],
    schema_version: 'identifier-preview-v1',
    ...overrides,
  })
  const client = (...answers: Response[]) => {
    const fetchMock = vi.fn<typeof fetch>()
    for (const answer of answers) fetchMock.mockResolvedValueOnce(answer)
    const keys = { current: vi.fn(() => token), renew: vi.fn(async () => renewedToken) }
    return {
      fetchMock,
      keys,
      http: new ReviewApiClient({ csrf: keys, fetch: fetchMock, buildIdentity: build }),
    }
  }
  const challenge = () => Response.json({ error: { code: 'csrf' } }, { status: 403 })
  const file = new Blob(['path\tidentifier\nA/B\t9780306406157\n'], { type: 'text/plain' })

  it('sends the file as its own bytes, as plain text, with the build and CSRF token', async () => {
    const { fetchMock, http } = client(Response.json(preview()))
    await expect(http.previewIdentifiers('abs', 'my books', file)).resolves.toMatchObject({
      preview_id: 'preview-1',
    })
    const [path, init] = fetchMock.mock.calls[0] ?? []
    expect(path).toBe('/api/v1/libraries/abs/my%20books/identifier-previews')
    const headers = new Headers(init?.headers)
    expect(headers.get('content-type')).toBe('text/plain; charset=utf-8')
    expect(headers.get('x-ml-build-identity')).toBe(build)
    expect(headers.get('x-csrf-token')).toBe(token)
    expect(init?.body).toBe(file)
  })

  it('reads a row that blank lines pushed past line 5,001', async () => {
    const pushed = preview({ refused: [{ row: 1_048_577, reason: 'no_item' }] })
    await expect(
      client(Response.json(pushed)).http.previewIdentifiers('abs', 'books', file),
    ).resolves.toMatchObject({ refused: [{ row: 1_048_577 }] })
    await expect(
      client(
        Response.json(preview({ refused: [{ row: 1_048_578, reason: 'no_item' }] })),
      ).http.previewIdentifiers('abs', 'books', file),
    ).rejects.toMatchObject({ status: 502 })
  })

  it('sends a preview again once after a CSRF renewal, with the renewed token', async () => {
    const { fetchMock, http, keys } = client(challenge(), Response.json(preview()))
    await http.previewIdentifiers('abs', 'books', file)
    expect(keys.renew).toHaveBeenCalledOnce()
    expect(fetchMock).toHaveBeenCalledTimes(2)
    expect(new Headers(fetchMock.mock.calls[1]?.[1]?.headers).get('x-csrf-token')).toBe(
      renewedToken,
    )
    expect(fetchMock.mock.calls[1]?.[1]?.body).toBe(file)
  })

  it.each([
    ['counts that do not add up to the rows', { rows: 3 }],
    ['a will-link list that is not its count', { will_link: [] }],
    ['a refused list that is not the rest', { refused: [] }],
  ])('refuses a preview with %s', async (_name, overrides) => {
    await expect(
      client(Response.json(preview(overrides))).http.previewIdentifiers('abs', 'books', file),
    ).rejects.toMatchObject({ status: 502, code: 'contradictory_identifier_preview' })
  })

  it('sends an import again after a CSRF renewal only with its own key', async () => {
    const imported = {
      import_id: `identifier_import_${'1'.repeat(64)}`,
      linked: 1,
      scan_requested: true,
      schema_version: 'identifier-import-v1',
    }
    const { fetchMock, http } = client(challenge(), Response.json(imported))
    await expect(http.importIdentifiers('preview-1', 'key-'.repeat(5))).resolves.toEqual(imported)
    const [first, second] = fetchMock.mock.calls
    expect(first?.[0]).toBe('/api/v1/identifier-previews/preview-1/import')
    expect(new Headers(first?.[1]?.headers).get('idempotency-key')).toBe('key-'.repeat(5))
    expect(new Headers(second?.[1]?.headers).get('idempotency-key')).toBe('key-'.repeat(5))
    expect(first?.[1]?.body).toBeUndefined()
  })

  it('reports a lost import answer as uncertain', async () => {
    const fetchMock = vi.fn<typeof fetch>().mockRejectedValueOnce(new TypeError('lost'))
    const http = new ReviewApiClient({ csrf, fetch: fetchMock, buildIdentity: build })
    await expect(http.importIdentifiers('preview-1', 'key-'.repeat(5))).rejects.toBeInstanceOf(
      AmbiguousMutationError,
    )
  })

  it('reads a preview’s status, and refuses an import named outside imported', async () => {
    const status = (state: string, importId: string | null, linked: number | null) =>
      Response.json({
        state,
        import_id: importId,
        linked,
        schema_version: 'identifier-preview-status-v1',
      })
    const id = `identifier_import_${'1'.repeat(64)}`
    await expect(
      client(status('imported', id, 2)).http.getIdentifierPreviewStatus('preview-1'),
    ).resolves.toMatchObject({ state: 'imported', linked: 2 })
    await expect(
      client(status('open', id, 2)).http.getIdentifierPreviewStatus('preview-1'),
    ).rejects.toMatchObject({ code: 'contradictory_identifier_preview' })
    await expect(
      client(status('imported', null, null)).http.getIdentifierPreviewStatus('preview-1'),
    ).rejects.toMatchObject({ code: 'contradictory_identifier_preview' })
  })

  it('reads an import’s live state and withdraws it with a key', async () => {
    const id = `identifier_import_${'1'.repeat(64)}`
    const state = (still: number, withdrawn: boolean) =>
      Response.json({
        import_id: id,
        linked: 3,
        still_linked: still,
        withdrawn,
        schema_version: 'identifier-import-v1',
      })
    const read = client(state(2, false))
    await expect(read.http.getIdentifierImport(id)).resolves.toMatchObject({ still_linked: 2 })
    expect(read.fetchMock.mock.calls[0]?.[0]).toBe(`/api/v1/identifier-imports/${id}`)
    for (const [still, withdrawn] of [
      [4, false],
      [1, true],
    ] as const)
      await expect(
        client(state(still, withdrawn)).http.getIdentifierImport(id),
      ).rejects.toMatchObject({ code: 'contradictory_identifier_import' })
    const withdrew = client(
      Response.json({
        import_id: id,
        withdrawn: 2,
        scan_requested: true,
        schema_version: 'identifier-import-withdrawal-v1',
      }),
    )
    await expect(
      withdrew.http.withdrawIdentifierImport(id, 'key-'.repeat(5)),
    ).resolves.toMatchObject({ withdrawn: 2 })
    expect(withdrew.fetchMock.mock.calls[0]?.[0]).toBe(`/api/v1/identifier-imports/${id}/withdraw`)
  })

  it('refuses a file link that names a source, a title or the whole work', async () => {
    const view = (link: Record<string, unknown>) =>
      Response.json({
        link: {
          creators: [],
          identifier: '9780306406157',
          link_row_id: `owner_link_${'a'.repeat(64)}`,
          paused: false,
          provider: null,
          reason: null,
          retry_at: null,
          scope: 'edition',
          source: 'file',
          state: 'linked',
          title: null,
          ...link,
        },
        offered: true,
        prefill: { asin: null, authors: [], isbn: null, title: null },
        schema_version: 'owner-link-view-v3',
        sources: [],
      })
    const address = { connection: 'abs', library: 'books', item: 'item-1' }
    await expect(client(view({})).http.getOwnerLinkView(address)).resolves.toMatchObject({
      link: { source: 'file', provider: null },
    })
    for (const link of [
      { provider: 'hardcover' },
      { title: 'Dune' },
      { scope: 'work', identifier: null },
      { source: 'search' },
    ])
      await expect(client(view(link)).http.getOwnerLinkView(address)).rejects.toMatchObject({
        code: 'contradictory_owner_link',
      })
  })
})

// The taste preview's read (#1372 PR 5, K7): decoded whole, then its bounds.
const readTastePreview = (document: unknown) =>
  new ReviewApiClient({
    csrf,
    fetch: vi.fn<typeof fetch>(async () => Response.json(document)),
  }).getTastePreview('unprotected')
const previewItem = (n: number) => ({
  review_case_id: `review_case_${String(n).repeat(64)}`,
  title: 'Example Title',
  authors: ['Example Author'],
  media_kind: 'epub',
  current: null,
  new: null,
  reason: null,
  rules: [{ number: 1, section: 'drawn_to', text: 'Bold art.' }],
  answer: null,
})
const previewDocument = {
  schema_version: 'review-taste-preview-v1',
  version: 2,
  checked: 7,
  found: [previewItem(1)],
  state: 'checking',
  rerun: true,
}

describe('taste preview decoder (#1372 PR 5)', () => {
  it('asks for the protection and admits a preview', async () => {
    const read = vi.fn<typeof fetch>(async () => Response.json(previewDocument))
    await expect(
      new ReviewApiClient({ csrf, fetch: read }).getTastePreview('protected'),
    ).resolves.toEqual(previewDocument)
    expect(read.mock.calls[0]?.[0]).toBe('/api/v1/covers/taste/preview?protection=protected')
  })
  it.each([
    ['five found still checking', { found: [1, 2, 3, 4, 5].map(previewItem) }],
    ['100 checked still checking', { checked: 100, found: [], rerun: false }],
    ['more found than checked', { checked: 0 }],
    ['a rerun with nothing found', { found: [], rerun: true }],
    ['the same work twice', { found: [previewItem(1), previewItem(1)] }],
  ])('refuses %s', async (_name, change) => {
    await expect(readTastePreview({ ...previewDocument, ...change })).rejects.toMatchObject({
      status: 502,
      code: 'invalid_taste_preview',
    })
  })
})
