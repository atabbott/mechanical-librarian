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
  coverAutomaticDecision,
  coverCandidate,
  coverConsideration,
  coverIdentity,
  coverPolicyReport,
  decisionAcknowledgement,
  decisionProjection,
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
  targetIdentity,
} from './wireFixtures'
import { describe, expect, it, vi } from 'vitest'

import {
  signInLocation,
  ApiError,
  AmbiguousMutationError,
  BuildMismatchError,
  CsrfRenewedError,
  MEDIA_HEALTH_KINDS,
  PageCsrfTokenStore,
  ReviewApiClient,
  mapCaseDetail,
  mapActivity,
  mapActivityConsent,
  mapActivityPermissionReadiness,
  derivePolicyDraftDisplay,
  policyAutomaticQualification,
  SessionExpiredError,
  refusalCopy,
  PRE_CALL_SERVER_CODES,
  uncertainWriteOutcome,
  mapEvidence,
  mapHistory,
  ResponseDecodeError,
  UncertainMutationError,
  mapCoverConsideration,
  mapCodexDevice,
  mapReviewerAccess,
  mapSettings,
} from '../src/api/http'
import type { PolicyStateView } from '../src/api/http'

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

const reviewerAccessPayload = (overrides: Record<string, unknown> = {}) => ({
  schema_version: 'reviewer-access-view-v2',
  cover_metadata: null,
  cost_evidence: { schema_version: 'reviewer-cost-evidence-v2', models: [] },
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

describe('reviewer access transport vocabulary', () => {
  it.each(['profile_not_provisioned', 'profile_unreadable', 'profile_contract_mismatch'])(
    'admits provisioning reason %s only in its exact state',
    (reason) => {
      const codex = {
        ...reviewerAccessPayload().codex,
        state: 'not_provisioned',
        reason,
        model: null,
        host_headroom_bytes: null,
      }
      expect(mapReviewerAccess(reviewerAccessPayload({ codex })).codex.reason).toBe(reason)
      for (const invalid of [
        { ...codex, state: 'ready' },
        { ...codex, model: 'gpt-5.6-sol' },
        { ...codex, reason: 'host_evidence_unavailable' },
      ]) {
        expect(() => mapReviewerAccess(reviewerAccessPayload({ codex: invalid }))).toThrow()
      }
    },
  )
  it('rejects the prior view and malformed contained model grammar', () => {
    expect(() =>
      mapReviewerAccess(reviewerAccessPayload({ schema_version: 'reviewer-access-view-v1' })),
    ).toThrow()
    for (const model of [null, '', 'model\nsecret', 'provider/model', 'x'.repeat(129)]) {
      expect(() =>
        mapReviewerAccess(
          reviewerAccessPayload({ codex: { ...reviewerAccessPayload().codex, model } }),
        ),
      ).toThrow()
    }
  })

  it.each(['openai/gpt-6-luna'])('preserves the exact cover recommendation %s', (model) => {
    expect(
      mapReviewerAccess(reviewerAccessPayload({ recommendations: [model] })).recommendations,
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
  ] as const)('preserves the closed %s refusal', (state) => {
    const mapped = mapReviewerAccess(
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

  it.each([
    [
      'cross-engine reason',
      {
        codex: {
          engine: 'codex',
          state: 'failed',
          reason: 'invalid_key',
          model: 'gpt-5.6-sol',
          tier: null,
          host_headroom_bytes: 1,
        },
      },
    ],
    [
      'hostile model',
      { mapping: { engine: 'openrouter', model: 'provider/model\nBearer secret' } },
    ],
    ['hostile recommendation', { recommendations: ['provider/model\nBearer secret'] }],
    ['extra mapping field', { mapping: { engine: 'codex', model: 'invented' } }],
    [
      'non-integer headroom',
      {
        codex: {
          engine: 'codex',
          state: 'ready',
          reason: null,
          model: 'gpt-5.6-sol',
          tier: null,
          host_headroom_bytes: '1',
        },
      },
    ],
    [
      'array candidate model',
      {
        candidates: [
          {
            model: ['provider/model'],
            tier: 'free',
            qualified: false,
            evidence: 'catalogue_capability_only',
            requests_per_minute: null,
            requests_per_day: null,
          },
        ],
      },
    ],
    [
      'array candidate tier',
      {
        candidates: [
          {
            model: 'provider/model',
            tier: ['free'],
            qualified: false,
            evidence: 'catalogue_capability_only',
            requests_per_minute: null,
            requests_per_day: null,
          },
        ],
      },
    ],
    [
      'qualified candidate',
      {
        candidates: [
          {
            model: 'provider/model',
            tier: 'paid',
            qualified: true,
            evidence: 'catalogue_capability_only',
            requests_per_minute: null,
            requests_per_day: null,
          },
        ],
      },
    ],
    ['extra top-level field', { invented: true }],
  ])('rejects %s', (_label, overrides) => {
    expect(() => mapReviewerAccess(reviewerAccessPayload(overrides))).toThrow(
      'invalid_reviewer_access_response',
    )
  })

  it.each([
    'permission_or_profile',
    'permission_or_process',
    'protocol_or_process',
    'restart_interrupted',
  ] as const)('preserves the closed device failure %s', (failure) => {
    expect(
      mapCodexDevice({
        state: failure === 'restart_interrupted' ? 'interrupted' : 'failed',
        verification_url: null,
        user_code: null,
        expires_in_seconds: null,
        failure,
      }).failure,
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
      mapReviewerAccess(reviewerAccessPayload({ description_metadata: metadata }))
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
    for (const invalid of [
      { ...metadata, prompt_price: null },
      { ...metadata, request_price: '-1' },
      { ...metadata, request_price: '' },
    ]) {
      expect(() =>
        mapReviewerAccess(reviewerAccessPayload({ description_metadata: invalid })),
      ).toThrow('invalid_description_metadata')
    }
  })

  it('accepts only the fixed issued prompt shape', () => {
    const issued = {
      state: 'issued',
      verification_url: 'https://auth.openai.com/codex/device',
      user_code: 'ABCD-1234',
      expires_in_seconds: 900,
      failure: null,
    }
    expect(mapCodexDevice(issued).state).toBe('issued')
    expect(mapCodexDevice({ ...issued, user_code: 'ABCD-EFGHJ' }).user_code).toBe('ABCD-EFGHJ')
    for (const user_code of ['Bearer secret', 'ABC-1234', 'ABCD-123456789']) {
      expect(() => mapCodexDevice({ ...issued, user_code })).toThrow(
        'invalid_codex_device_response',
      )
    }
    expect(() => mapCodexDevice({ ...issued, invented: true })).toThrow(
      'invalid_codex_device_response',
    )
  })
})

describe('ReviewApiClient', () => {
  it('fails closed on cover candidate, policy, incumbent, and recommendation relationship contradictions', () => {
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

    expect(mapCoverConsideration(payload())?.choiceContexts).toHaveLength(2)
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
    expect(mapCoverConsideration(retainedOnly)?.ready).toBe(true)
    expect(() => mapCoverConsideration({ ...retainedOnly, ready: false })).toThrow(ApiError)
    expect(
      mapCoverConsideration({ ...retainedOnly, provider_fetching: true, ready: false })?.ready,
    ).toBe(false)

    expect(
      mapCoverConsideration(
        payload({
          candidates: [],
          choice_contexts: [context(null, 'current')],
          policy: null,
          ready: false,
        }),
      ),
    ).not.toBeNull()
    expect(
      mapCoverConsideration(
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
      )?.choiceContexts[1]?.recommendation,
    ).toBe('recommended')
    expect(
      mapCoverConsideration(payload({ incumbent: { state: 'unavailable', reference: null } }))
        ?.incumbent.state,
    ).toBe('unavailable')
    expect(
      mapCoverConsideration(payload({ candidates: [candidate({ observation: observation() })] }))
        ?.candidates[0]?.observation?.reproduction,
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
      expect(() => mapCoverConsideration(payload(overrides)), label).toThrow(ApiError)
    // A cover's sources name closed kinds; one outside them is refused (#1286 C6).
    expect(
      mapCoverConsideration(
        payload({ candidates: [candidate({ source_kinds: ['open_library'] })] }),
      )?.candidates[0]?.sourceKinds,
    ).toEqual(['open_library'])
    expect(() =>
      mapCoverConsideration(payload({ candidates: [candidate({ source_kinds: ['scraped'] })] })),
    ).toThrow(new ApiError(502, 'invalid_cover_source_kind'))
  })
  it('maps only the fixed unavailable reviewer-contract reasons', () => {
    const payload = (reviewer_unavailable_reason: unknown) =>
      coverConsideration({ reviewer_status: 'unavailable', reviewer_unavailable_reason })

    expect(mapCoverConsideration(payload(null))?.reviewerUnavailableReason).toBeNull()
    expect(
      mapCoverConsideration(payload('profile contract requires memory_bytes=1073741824'))
        ?.reviewerUnavailableReason,
    ).toBe('profile contract requires memory_bytes=1073741824')
    expect(
      mapCoverConsideration(payload('profile contract requires process_limit=32'))
        ?.reviewerUnavailableReason,
    ).toBe('profile contract requires process_limit=32')
    expect(() => mapCoverConsideration(payload('/private/profile token=secret'))).toThrow(ApiError)
  })
  it('maps only a closed reviewer failure code on a failed review', () => {
    const payload = (reviewer_status: string, reviewer_failure_code?: unknown) => {
      const body: Record<string, unknown> = coverConsideration({
        reviewer_access_revision: 3,
        reviewer_engine: 'openrouter',
        reviewer_model: 'provider/model',
        reviewer_status,
        reviewer_failure_code,
      })
      // The mapper reads an absent code as no code; the absent case removes it.
      if (reviewer_failure_code === undefined) delete body.reviewer_failure_code
      return body
    }

    for (const code of [
      'timed_out',
      'response_too_large',
      'data_policy_refused',
      'image_limit_exceeded',
    ] as const)
      expect(mapCoverConsideration(payload('failed', code))?.reviewerFailureCode).toBe(code)
    expect(mapCoverConsideration(payload('failed'))).not.toHaveProperty('reviewerFailureCode')
    expect(mapCoverConsideration(payload('failed', null))).not.toHaveProperty('reviewerFailureCode')
    expect(() => mapCoverConsideration(payload('failed', 'transport'))).toThrow(
      new ApiError(502, 'invalid_cover_reviewer_failure_code'),
    )
    expect(() => mapCoverConsideration(payload('completed', 'timed_out'))).toThrow(
      new ApiError(502, 'invalid_cover_reviewer_failure_code'),
    )
  })
  it('preserves present and absent series sequences in the mapped review value', () => {
    const detail = (
      series: readonly Record<string, unknown>[],
      choiceContexts: unknown = [choiceContext()],
    ) =>
      mapCaseDetail(
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

    expect(detail([{ name: 'Archive', sequence: '2.5' }]).fields[0]?.currentValue).toBe(
      'Archive · 2.5',
    )
    expect(detail([{ name: 'Standalone' }]).fields[0]?.currentValue).toBe('Standalone')
    expect(() => detail([], [])).toThrow(new ApiError(502, 'misaligned_choice_contexts'))
  })

  it('requires exact creator corroboration source lists in case-detail responses', () => {
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

    expect(() => mapCaseDetail(response(undefined))).toThrow(ApiError)
    expect(() => mapCaseDetail(response('Hardcover'))).toThrow(ApiError)
    expect(
      mapCaseDetail(response(['Hardcover', 'Open Library'])).fields[0]?.corroborationSources,
    ).toEqual(['Hardcover', 'Open Library'])
  })

  // The transport closes the code families the backend closes, so an owner
  // surface never meets a code it has no words for (#1286, R1).
  const closedDetail = (
    field: Record<string, unknown> = {},
    target: Record<string, unknown> = {},
  ) => caseDetail({ fields: [reviewField(field)], target: targetIdentity(target) })

  it.each([
    ['destination', { destination: 'publication_date' }, {}, 'invalid_destination'],
    ['disposition', { deterministic_disposition: 'review' }, {}, 'invalid_disposition'],
    ['write capability', { effect_capability: 'manual' }, {}, 'invalid_effect_capability'],
    [
      'current state',
      { current: targetField({ state: 'absent', value: null }) },
      {},
      'invalid_current_state',
    ],
    ['media kind', {}, { media_kind: 'ebook' }, 'invalid_target_media_kind'],
  ])('refuses an unknown %s code in a case detail', (_name, field, target, code) => {
    expect(mapCaseDetail(closedDetail()).fields[0]?.destination).toBe('title')
    expect(() => mapCaseDetail(closedDetail(field, target))).toThrow(new ApiError(502, code))
  })

  it.each([
    ['value', 'A Book', 'A Book'],
    ['missing', null, 'Not set in Audiobookshelf'],
    ['null', null, 'Not set in Audiobookshelf'],
    ['empty', '', 'Not set in Audiobookshelf'],
    ['unloaded', null, 'Couldn’t be read from Audiobookshelf'],
    ['malformed', null, 'Couldn’t be read from Audiobookshelf'],
    ['oversized', null, 'Couldn’t be read from Audiobookshelf'],
  ])('reads a %s current value in Audiobookshelf’s terms', (state, value, shown) => {
    expect(
      mapCaseDetail(closedDetail({ current: targetField({ state, value }) })).fields[0]
        ?.currentValue,
    ).toBe(shown)
  })

  it('uses Unicode code point bounds for readable case identity', () => {
    const astral = '📚'
    const response = (target: Record<string, unknown>) =>
      caseDetail({ fields: [], target: targetIdentity(target) })

    const exact = astral.repeat(512)
    expect(mapCaseDetail(response({ title: exact, authors: [exact] }))).toMatchObject({
      title: exact,
      authors: [exact],
    })
    expect(() => mapCaseDetail(response({ title: astral.repeat(513) }))).toThrow(ApiError)
    expect(() => mapCaseDetail(response({ authors: [astral.repeat(513)] }))).toThrow(ApiError)
  })

  it('rejects case-detail fields that omit the objects the view dereferences', () => {
    const field = reviewField
    const response = (fields: readonly unknown[]) => caseDetail({ fields })

    expect(mapCaseDetail(response([field()])).fields[0]?.currentValue).toBe('A Book')
    expect(mapCaseDetail(response([field()])).fields[0]?.automaticWithheldReason).toBeNull()
    // The earlier value is one more suggestion and one more tagged genre; a
    // restore control is not part of the contract (ADR-0085).
    const ownerControl = {
      entry: { max_characters: 512, suggestions: [{ rule: 'previous', value: 'Old Title' }] },
      subset: {
        max_items: 32,
        options: [{ value: 'Drama', current: false, proposed: false, previous: true }],
      },
      withdraw: null,
    }
    expect(
      mapCaseDetail(response([field({ owner_control: ownerControl })])).fields[0]?.ownerControl,
    ).toEqual({
      entry: { maxCharacters: 512, suggestions: [{ rule: 'previous', value: 'Old Title' }] },
      subset: {
        maxItems: 32,
        options: [{ value: 'Drama', current: false, proposed: false, previous: true }],
      },
      withdraw: null,
    })
    expect(() =>
      mapCaseDetail(response([field({ owner_control: { ...ownerControl, revert: null } })])),
    ).toThrow(ApiError)
    // A recording's optional choice maps; a state the server never states does not.
    expect(
      mapCaseDetail(
        response([
          field({
            classification: 'owner_choosable',
            needs_attention: false,
            navigation_role: 'inspect',
          }),
        ]),
      ).fields[0]?.classification,
    ).toBe('owner_choosable')
    expect(() => mapCaseDetail(response([field({ classification: 'queued' })]))).toThrow(ApiError)
    expect(
      mapCaseDetail(response([field({ automatic_withheld_reason: null })])).fields[0]
        ?.automaticWithheldReason,
    ).toBeNull()
    expect(
      mapCaseDetail(
        response([field({ automatic_withheld_reason: 'Contributing lookup unavailable.' })]),
      ).fields[0]?.automaticWithheldReason,
    ).toBe('Contributing lookup unavailable.')
    expect(() => mapCaseDetail(response([field({ automatic_withheld_reason: 1 })]))).toThrow(
      ApiError,
    )
    expect(() =>
      mapCaseDetail(response([field({ automatic_withheld_reason: 'x'.repeat(513) })])),
    ).toThrow(ApiError)

    const retainedEvidence = field({
      decision_actions: ['keep_current', 'accept'],
      proposed_values: [{ kind: 'text', value: 'Alternative' }],
      choice_contexts: [
        choiceContext({ rationale: 'Current evidence.' }),
        choiceContext({
          alternative_id: 'eligible',
          rationale: 'Eligible evidence.',
          selection: 'proposed',
        }),
        choiceContext({
          alternative_id: 'preserves-target',
          rationale: 'Retained non-selectable evidence.',
          selection: 'proposed',
        }),
      ],
    })
    const retainedContexts = mapCaseDetail(response([retainedEvidence])).fields[0]?.choiceContexts
    expect(retainedContexts).toHaveLength(3)
    expect(retainedContexts?.[1]?.alternativeId).toBe('eligible')
    expect(retainedContexts?.[2]?.alternativeId).toBe('preserves-target')
    for (const invalid of [
      null,
      'field',
      field({ current: undefined }),
      field({ current: 'A Book' }),
      field({ proposed_values: undefined }),
      field({ proposed_values: {} }),
    ])
      expect(() => mapCaseDetail(response([invalid]))).toThrow(
        new ApiError(502, 'invalid_case_detail_field'),
      )
    for (const actions of [
      ['keep_current'],
      ['accept'],
      ['accept', 'keep_current'],
      ['keep_current', 'accept', 'reject'],
      ['keep_current', 'keep_current'],
      ['reject'],
      ['unknown'],
    ])
      expect(() => mapCaseDetail(response([field({ decision_actions: actions })]))).toThrow(
        ApiError,
      )
    expect(
      mapCaseDetail(response([field({ decision_actions: ['keep_current', 'accept'] })])).fields[0]
        ?.decisionActions,
    ).toEqual(['keep_current', 'accept'])
  })

  it('maps only bounded readable diagnostic evidence', () => {
    const payload = {
      schema_version: 'review-case-evidence-v4',
      review_case_id: 'case-1',
      missing_lineage_count: 1,
      next_cursor: 'next',
      items: [
        {
          destinations: ['title'],
          observation: 'Title: A Book',
          role: 'external_corroboration',
          source: 'Hardcover',
        },
      ],
      source_ledger: [
        {
          provider_requests: 1,
          lane_kind: 'work',
          provider: 'Hardcover',
          reason: 'Returned matching metadata for this work.',
          status: 'candidates',
        },
      ],
    }
    expect(() =>
      mapEvidence({
        ...payload,
        source_ledger: [{ ...payload.source_ledger[0], lane_kind: 'identity' }],
      }),
    ).toThrow(new ApiError(502, 'invalid_provider_outcome_0_lane'))
    expect(mapEvidence(payload)).toEqual({
      items: [
        {
          destinations: ['title'],
          observation: 'Title: A Book',
          role: 'external_corroboration',
          source: 'Hardcover',
        },
      ],
      missingLineageCount: 1,
      nextCursor: 'next',
      sourceLedger: [
        {
          providerRequests: 1,
          laneKind: 'work',
          provider: 'Hardcover',
          reason: 'Returned matching metadata for this work.',
          status: 'candidates',
        },
      ],
    })
    expect(
      mapEvidence({ ...payload, items: [{ ...payload.items[0], role: 'external_observation' }] })
        .items[0]?.role,
    ).toBe('external_observation')
    for (const invalid of [
      { ...payload, schema_version: 'review-case-evidence-v3' },
      { ...payload, items: [{ fact: '{}', kind: 'raw', reference: 'evidence-1' }] },
      { ...payload, items: [{ ...payload.items[0], destinations: [] }] },
      { ...payload, items: [{ ...payload.items[0], destinations: ['title', 'title'] }] },
      { ...payload, items: [{ ...payload.items[0], role: 'unknown' }] },
      { ...payload, items: [{ ...payload.items[0], observation: 'x'.repeat(1025) }] },
      { ...payload, source_ledger: [{ provider: 'Hardcover' }] },
    ])
      expect(() => mapEvidence(invalid)).toThrow(ApiError)
  })

  it('maps history without exposing retained identities and rejects malformed values', () => {
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
    expect(mapHistory(payload)).toEqual({
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
      mapHistory({
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
      }).items[0],
    ).toMatchObject({ reviewerModel: 'mistralai/ministral-3b-2512' })
    expect(mapHistory({ ...payload, items: [cover] })).toEqual({
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
      mapHistory({
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
      }).items[0],
    ).toMatchObject({ category: 'cover', ownerOutcome: 'accept', ownerMatchUnconfirmed: true })
    for (const outcome of ['keep_current', 'reject']) {
      const mapped = mapHistory({
        ...payload,
        items: [
          {
            ...cover,
            kind: outcome === 'keep_current' ? 'kept_incumbent' : 'no_action',
            decision: decisionProjection({ outcome, selected_value: null }),
            consideration: {
              ...cover.consideration,
              candidates: [coverCandidate({ incumbent: true, match_state: 'match_not_confirmed' })],
            },
          },
        ],
      }).items[0]
      expect(mapped).toMatchObject({ category: 'cover', ownerOutcome: outcome })
      if (!mapped) throw new Error('Expected retained cover history item')
      expect('ownerMatchUnconfirmed' in mapped).toBe(outcome === 'keep_current')
    }
    expect(mapHistory({ ...payload, items: [{ ...cover, kind: 'superseded' }] })).toEqual({
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
      mapHistory({
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
              candidates: Array.from({ length: 13 }, () => coverCandidate()),
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
      expect(() => mapHistory(invalid)).toThrow(ApiError)
    // Every closed history code is refused by name outside its set (#1286 C6).
    const [human, automatic, transition] = items
    const preview = historyShadowItem({ occurred_at: 4, dispositions: { abstention: 1 } })
    expect(mapHistory({ ...payload, items: [preview] }).items[0]).toMatchObject({
      category: 'preview',
      dispositions: [{ count: 1, outcome: 'abstention' }],
    })
    for (const [item, code] of [
      [{ ...human, outcome: 'approve' }, 'invalid_history_item_0_outcome'],
      [{ ...automatic, outcome: 'accept' }, 'invalid_history_item_0_outcome'],
      [{ ...preview, dispositions: { approve: 1 } }, 'invalid_history_item_0_disposition_name'],
      [{ ...transition, kind: 'invented' }, 'invalid_history_item_0_kind'],
    ] as const)
      expect(() => mapHistory({ ...payload, items: [item] })).toThrow(new ApiError(502, code))
  })

  it('accepts domain-bounded history values by Unicode code points', () => {
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
    expect(mapHistory(history({ kind: 'text', value: description })).items[0]).toMatchObject({
      category: 'owner',
      applicationReason: null,
      selectedValue: { kind: 'text', value: description },
    })
    expect(() => mapHistory(history({ kind: 'text', value: astral.repeat(16_385) }))).toThrow(
      ApiError,
    )
    expect(mapHistory(history({ kind: 'list', value: [astral.repeat(512)] })).items).toHaveLength(1)
    expect(() => mapHistory(history({ kind: 'list', value: [astral.repeat(513)] }))).toThrow(
      ApiError,
    )
    expect(
      mapHistory(history({ kind: 'series', value: [{ name: astral.repeat(512), sequence: null }] }))
        .items[0],
    ).toMatchObject({
      selectedValue: { kind: 'series', value: [{ name: astral.repeat(512), sequence: null }] },
    })
    expect(
      mapHistory(
        history({ kind: 'series', value: [{ name: 'Archive', sequence: astral.repeat(512) }] }),
      ).items,
    ).toHaveLength(1)
    expect(() =>
      mapHistory(
        history({ kind: 'series', value: [{ name: astral.repeat(513), sequence: null }] }),
      ),
    ).toThrow(ApiError)
    expect(() => mapHistory(history({ kind: 'series', value: [{ name: 'Archive' }] }))).toThrow(
      ApiError,
    )
    expect(() =>
      mapHistory(
        history({ kind: 'series', value: [{ name: 'Archive', sequence: astral.repeat(513) }] }),
      ),
    ).toThrow(ApiError)

    const cover = (reason: string) =>
      historyCoverItem({
        consideration: coverConsideration({
          policy: coverPolicyReport({ reasons: [reason], status: 'abstained' }),
          reviewer_status: 'completed',
        }),
      })
    expect(mapHistory({ ...history(null), items: [cover(astral.repeat(128))] }).items).toHaveLength(
      1,
    )
    expect(() => mapHistory({ ...history(null), items: [cover(astral.repeat(129))] })).toThrow(
      ApiError,
    )
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
              attention_count: 0,
              attention_state: 'no_attention_needed',
              detail_url: null,
              detail_kind: null,
              dispositions: [],
              work_id: 'library_work_2',
              preparation: null,
              preparation_outcomes: [
                {
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
                  { destination: 'title', state: 'value', value: 'Catalogue-only work' },
                  { destination: 'subtitle', state: 'missing', value: null },
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
      attention: 'needs_attention',
      detailUrl: '/review/case-1',
      detailKind: 'case',
      preparation: null,
      preparationOutcomes: [],
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
      attention: 'no_attention_needed',
      detailUrl: null,
      detailKind: null,
      preparation: null,
      preparationOutcomes: [
        {
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
          { destination: 'title', state: 'value', value: 'Catalogue-only work' },
          { destination: 'subtitle', state: 'missing', value: 'Not set in Audiobookshelf' },
        ],
      },
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

  it('rejects a malformed activity display projection before it reaches the workspace', () => {
    expect(() =>
      mapActivity(activityResponse({ schema_version: 'continuous-field-activity-v1' })),
    ).toThrow(ApiError)
  })

  it('maps bounded activity labels and grouped state into the display seam', () => {
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
        { field: 'Title', setting: 'Review mode', previous: 'Ask me', next: 'Automatic' },
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
    const activity = mapActivity(payload)

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
    expect(activity.provider_recovery?.[0]?.dispatch_reasons).toEqual([
      'missing_corroboration',
      'blanket_scan',
    ])
    expect(activity.provider_recovery?.[0]?.failure_reasons).toEqual([])
    const reasons = ['burst_throttled', 'quota_exhausted', 'transient']
    expect(
      mapActivity({
        ...payload,
        provider_recovery: [{ ...recovery, failure_reasons: reasons }],
      }).provider_recovery?.[0]?.failure_reasons,
    ).toEqual(reasons)
    for (const failure_reasons of [
      ['private'],
      ['transient', 'transient'],
      ['transient', 'authentication'],
      [1],
      'transient',
      Array(8).fill('transient'),
    ]) {
      expect(() =>
        mapActivity({
          ...payload,
          provider_recovery: [{ ...recovery, failure_reasons }],
        }),
      ).toThrow(ApiError)
    }
    for (const dispatch_reasons of [
      ['private-detail'],
      ['blanket_scan', 'blanket_scan'],
      'blanket_scan',
    ])
      expect(() =>
        mapActivity({
          ...payload,
          provider_recovery: [{ ...recovery, dispatch_reasons }],
        }),
      ).toThrow(ApiError)
    // The mapper reads absent failure reasons as none; this recovery leaves them out.
    const terminalRecovery = mapActivity({
      ...payload,
      provider_recovery: [
        {
          state: 'recovered',
          affected_count: 1,
          next_eligible_at: null,
          dispatch_reasons: [],
        },
      ],
    })
    expect(terminalRecovery.provider_recovery?.[0]).toEqual({
      failure_reasons: [],
      state: 'recovered',
      affected_count: 1,
      next_eligible_at: null,
      dispatch_reasons: [],
    })
    expect(() => mapActivity({ ...payload, waiting: { applying: 1 } })).toThrow(ApiError)
    expect(activity.owner_action_required).toBe(2)
    for (const owner_action_required of [undefined, -1, 1.5, '1'])
      expect(() => mapActivity({ ...payload, owner_action_required })).toThrow(ApiError)
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
      setting: 'Review mode',
      previous: 'Ask me',
      next: 'Automatic',
    })
    const degraded = mapActivity({
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
    expect(() =>
      mapActivity({
        ...payload,
        events: [
          {
            ...event,
            diagnostic: { event_class: 'automatic_decision', reason: 'private-detail' },
          },
        ],
      }),
    ).toThrow(ApiError)
    expect(() => mapActivity({ ...payload, events: [{ ...event, category: 'review' }] })).toThrow(
      ApiError,
    )
    expect(activity.suspensions[0]).toMatchObject({ scope: 'library', destination: null })
    // A hold reason outside the closed set has no owner words, so it is refused.
    expect(() =>
      mapActivity({
        ...payload,
        suspensions: [{ ...suspension, reason: 'recovery' }],
      }),
    ).toThrow(new ApiError(502, 'invalid_activity_suspension_reason'))
    // A library's write state and each destination's state are closed sets
    // too; a code outside either has no owner words (#1286 C6).
    expect(() =>
      mapActivity({ ...payload, capabilities: [{ ...capability, state: 'partial' }] }),
    ).toThrow(new ApiError(502, 'invalid_activity_state'))
    expect(() =>
      mapActivity({
        ...payload,
        capabilities: [
          {
            ...capability,
            destinations: [{ ...capability.destinations[0], state: 'writable' }],
          },
        ],
      }),
    ).toThrow(new ApiError(502, 'invalid_activity_destination_state'))
    expect(() =>
      mapActivity({
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
    ).toThrow(ApiError)
    expect(() => mapActivity({ ...payload, example: { after: 'After' } })).toThrow(ApiError)
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
      schema_version: 'metadata-policy-notebook-v1',
      policy_id: 'policy-1',
      profile: {
        schema_version: 'metadata-policy-profile-v3',
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
      schema_version: 'metadata-policy-notebook-v1',
      policy_id: 'policy-1',
      profile: {
        schema_version: 'metadata-policy-profile-v3',
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
      schema_version: 'metadata-policy-notebook-v1',
      policy_id: 'policy-1',
      profile: {
        schema_version: 'metadata-policy-profile-v3',
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
      schema_version: 'metadata-policy-notebook-v1',
      policy_id: 'policy-1',
      profile: {
        schema_version: 'metadata-policy-profile-v3',
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

  it.each([
    ['validate', 2],
    ['preview', 2],
    ['drafts', 1],
    ['activate', 1],
    ['automatic-safety/pause', 1],
  ])('resends policy %s only when it is side-effect free', async (action, sends) => {
    // Only validate and preview are resent; each answers with its own document.
    const body = (action === 'preview' ? policyPreview : policyProfile)({ policy_id: 'policy-2' })
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(csrfChallenge())
      .mockResolvedValueOnce(Response.json(body))
    const client = new ReviewApiClient({ csrf, fetch: fetchMock, buildIdentity: 'c'.repeat(64) })

    const command = client.policyCommand(action, { name: 'Draft' })
    if (sends === 2) await expect(command).resolves.toEqual(body)
    else await expect(command).rejects.toBeInstanceOf(CsrfRenewedError)
    expect(csrf.renew).toHaveBeenCalledOnce()
    expect(fetchMock).toHaveBeenCalledTimes(sends)
  })

  it('renews and resends at most once when the resend is refused again', async () => {
    const fetchMock = vi.fn<typeof fetch>(async () => csrfChallenge())
    const client = new ReviewApiClient({ csrf, fetch: fetchMock, buildIdentity: 'c'.repeat(64) })

    await expect(client.policyCommand('validate', {})).rejects.toBeInstanceOf(CsrfRenewedError)
    expect(csrf.renew).toHaveBeenCalledOnce()
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })

  it('keeps a lost resend ambiguous rather than resending again', async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(csrfChallenge())
      .mockRejectedValueOnce(new TypeError('network'))
    const client = new ReviewApiClient({ csrf, fetch: fetchMock, buildIdentity: 'c'.repeat(64) })

    await expect(client.policyCommand('validate', {})).rejects.toBeInstanceOf(
      AmbiguousMutationError,
    )
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })

  it.each([
    [Response.json({ error: { code: 'origin' } }, { status: 403 }), 'origin'],
    [new Response('not json', { status: 403 }), 'invalid_error_response'],
  ])('treats a non-csrf 403 as an ordinary refusal with no renewal', async (response, code) => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValueOnce(response)
    const client = new ReviewApiClient({ csrf, fetch: fetchMock, buildIdentity: 'c'.repeat(64) })

    const refused = client.policyCommand('validate', {})
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

    await expect(client.policyCommand('activate', {})).rejects.toBeInstanceOf(SessionExpiredError)
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
    }
    const throttling = { generation: 3, retry: 'available', entries: [entry] }
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
    ] as const)
      await expect(
        readPreparation({ ...base, provider_throttling: invalid }),
      ).rejects.toMatchObject(refused(failure))
  })

  it("accepts a library's languages only when absent or a list of strings", async () => {
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
    expect(
      mapSettings({ ...view(), document: { ...view().document, libraries: [absent] } }).document
        .libraries[0]?.languages,
    ).toEqual([])
    expect(mapSettings(withLanguages(['de', 'en'])).document.libraries[0]?.languages).toEqual([
      'de',
      'en',
    ])
    for (const invalid of ['de', [1], [null], null, { de: true }])
      expect(() => mapSettings(withLanguages(invalid))).toThrow(
        expect.objectContaining({ status: 502 }),
      )
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
      active_books: [{ name: 'Private title', stage: 'Comparing candidates' }],
      recent_completions: [{ name: 'Finished title', outcome: 'ready', completed_at: 101 }],
      throughput_per_minute: 1.2,
    })
    const base = preparation({ active: true, presentation })
    const read = (changes: Record<string, unknown>) =>
      readPreparation({ ...base, presentation: { ...presentation, ...changes } })
    expect((await readPreparation(base)).presentation?.passage.ready).toBe(1)
    const fourTriggers = ['initial_setup', 'manual_scan', 'library_added', 'title_recovery'].map(
      (trigger) => ({ ...presentation.triggers[0], trigger }),
    )
    expect((await read({ triggers: fourTriggers })).presentation?.triggers).toHaveLength(4)
    expect((await read({ triggers: [fourTriggers[3]] })).presentation?.triggers[0]?.trigger).toBe(
      'title_recovery',
    )
    await expect(
      read({ triggers: [...fourTriggers, { ...fourTriggers[0], trigger: 'unknown' }] }),
    ).rejects.toMatchObject(refused('max_items@/presentation/triggers'))
    await expect(
      read({ triggers: [{ ...fourTriggers[0], trigger: 'unknown' }] }),
    ).rejects.toMatchObject(refused('enum@/presentation/triggers/0/trigger'))
    // One entry per trigger family is a named check (ADR-0091), not the schema's.
    await expect(read({ triggers: [fourTriggers[3], fourTriggers[3]] })).rejects.toThrow(
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
          { schema_version: 'review-library-v4', error: { code: 'busy' } },
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
    await expect(
      readLibrary({ ...page, schema_version: 'review-library-v4' }),
    ).resolves.toBeDefined()
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
        },
        lens: 'not_matched',
      })
    const mapped = await readLibrary(page('not_matched_unavailable_scheduled'))
    expect(mapped.lens).toBe('not_matched')
    expect(mapped.counts).toEqual({ attention: 0, all: 3, clear: 1, notMatched: 2, failed: 0 })
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
    bound: "A complete recording runs within 1 min 30 s of the edition's stated running time.",
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
    expect(mapCaseDetail(await detail([observation])).mediaHealth).toEqual([observation])
    expect(mapCaseDetail(await detail([])).mediaHealth).toEqual([])
  })

  it('refuses a case-detail media-health section that is not the closed shape', async () => {
    const absent = await detail(undefined)
    expect(() => mapCaseDetail(absent)).toThrow(new ApiError(502, 'invalid_case_media_health'))
    const untyped = await detail([{ ...observation, tracks: ['1'] }])
    expect(() => mapCaseDetail(untyped)).toThrow(
      new ApiError(502, 'invalid_case_media_health_0_track_0'),
    )
    const widened = await detail([{ ...observation, kind: 'media_shredded' }])
    expect(() => mapCaseDetail(widened)).toThrow(
      new ApiError(502, 'invalid_case_media_health_0_kind'),
    )
  })

  it('refuses a case-detail observation that cites nothing', async () => {
    // An observation with no citation names a fault without saying what it
    // read, which is the one thing the domain type refuses to construct.
    const uncited = await detail([{ ...observation, cited: [] }])
    expect(() => mapCaseDetail(uncited)).toThrow(
      new ApiError(502, 'invalid_case_media_health_0_cited'),
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
  const mapped = mapCaseDetail(payload)
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
  const field = mapCaseDetail(payload).fields.find((item) => item.destination === 'description')
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
    expect(() => mapCaseDetail(changed)).toThrow(ApiError)
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
  expect(() => mapCaseDetail(changed)).toThrow(ApiError)
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
    const advice = mapCaseDetail(payload).fields.find(
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
  expect(() =>
    mapCaseDetail({
      ...base,
      fields: base.fields.map((field) =>
        field.destination === 'description' ? { ...field, description_advice: advice } : field,
      ),
    }),
  ).toThrow(ApiError)
})

it('validates the closed decision confirmation and maps its case exactly once', async () => {
  const { default: payload } = await import('./descriptionAdviceFixture.json')
  const command = {
    destination: 'title',
    outcome: 'keep_current' as const,
    proposalId: 'proposal-1',
    targetFingerprint: 'fingerprint-1',
    idempotencyKey: 'same-decision',
  }
  const casePayload = { ...payload, review_case_id: 'case-1', current: false }
  for (const confirmation of [
    { status: 'other' },
    { status: 'unavailable', case: casePayload },
    { status: 'confirmed' },
    { status: 'confirmed', case: { ...casePayload, current: 'yes' } },
    { status: 'confirmed', case: { ...casePayload, review_case_id: 'another-case' } },
    { status: 'confirmed', case: casePayload, extra: true },
  ]) {
    const client = new ReviewApiClient({
      csrf,
      buildIdentity: 'c'.repeat(64),
      fetch: vi.fn(async () => Response.json(decisionAcknowledgement({ confirmation }))),
    })
    await expect(client.recordDecision('case-1', command)).rejects.toBeInstanceOf(ApiError)
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
  expect(result.confirmation).toEqual({ status: 'confirmed', case: mapCaseDetail(casePayload) })
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
    const field = mapCaseDetail(payload).fields.find((item) => item.destination === 'description')
    if (!field?.descriptionAdvice?.candidate)
      throw new Error('description fixture requires its candidate')
    const authored = route === 'description-advice/selection'
    const command = {
      destination: 'description',
      outcome: 'accept' as const,
      proposalId: field.proposalId,
      targetFingerprint: field.targetFingerprint,
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
      expect(failure).toBeInstanceOf(ApiError)
      expect(failure).toMatchObject({ status: 502, code: 'invalid_decision_confirmation' })
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
  (price) => {
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
    expect(mapReviewerAccess(value).cover_metadata).toEqual(metadata)
    expect(() =>
      mapReviewerAccess({ ...value, mapping: { engine: 'openrouter', model: 'example/other' } }),
    ).toThrow()
    expect(() =>
      mapReviewerAccess({ ...value, cover_metadata: { ...metadata, image_price: 'NaN' } }),
    ).toThrow()
    expect(() =>
      mapReviewerAccess({
        ...value,
        cost_evidence: {
          schema_version: 'reviewer-cost-evidence-v2',
          models: [],
          private: 'forbidden',
        },
      }),
    ).toThrow()
  },
)

it('admits per-row cost runs and refuses a missing row run or a top-level run', () => {
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
    mapReviewerAccess(reviewerAccessPayload({ cost_evidence }))
  const valid = { schema_version: 'reviewer-cost-evidence-v2', models: [row] }
  expect(evidence(valid).cost_evidence).toEqual(valid)
  const missing = Object.fromEntries(Object.entries(row).filter(([key]) => key !== 'measured_run'))
  for (const invalid of [
    { ...valid, models: [missing] },
    { ...valid, models: [{ ...row, measured_run: null }] },
    { ...valid, models: [{ ...row, measured_run: { ...run, source_head: 'A'.repeat(40) } }] },
    { ...valid, measured_run: run },
    { ...valid, schema_version: 'reviewer-cost-evidence-v1' },
  ])
    expect(() => evidence(invalid)).toThrow(new ApiError(502, 'invalid_cost_evidence'))
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
    ['absent', undefined, null],
    ['null', null, null],
    ['a preparation key', key, key],
  ])('maps %s to the event view', (_name, value, expected) => {
    const events = mapActivity(withGroup(value)).events
    expect(events.length).toBeGreaterThan(0)
    expect(events.every((event) => event.check_group === expected)).toBe(true)
  })
  it('refuses a key that is not a string', () => {
    expect(() => mapActivity(withGroup(7))).toThrow(
      new ApiError(502, 'invalid_activity_check_group'),
    )
  })
  it('maps the captured fixture, whose events all carry the key', () => {
    expect(currentActivity.events.every((event) => 'check_group' in event)).toBe(true)
    const events = mapActivity(currentActivity).events
    expect(events.length).toBe(currentActivity.events.length)
  })
})

describe('deferred Activity permission contract', () => {
  it('refuses a genuine earlier producer and requires both nullable fields', () => {
    expect(() => mapActivity(activityV2Predecessor)).toThrow(ApiError)
    expect(mapActivity(currentActivity).backup_ready).toBeNull()
    expect(mapActivity(currentActivity).example).toBeNull()
    for (const field of ['backup_ready', 'example']) {
      const missing = Object.fromEntries(
        Object.entries(currentActivity).filter(([key]) => key !== field),
      )
      expect(() => mapActivity(missing)).toThrow(ApiError)
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
  ])('rejects non-null history %s', (field, value) => {
    expect(() => mapActivity({ ...currentActivity, [field]: value })).toThrow(ApiError)
  })
  it.each([true, false, null])(
    'reads only the narrow permission response with readiness %s',
    async (backup_ready) => {
      const response = {
        backup_ready,
        capabilities: [],
        configured_libraries: [],
        prepared: { automatic: 0, human: 0, total: 0 },
        example: {
          after: 'After',
          before: 'Before',
          destination: 'title',
          library: 'Fiction',
          work_id: 'work-1',
        },
      }
      expect(mapActivityPermissionReadiness(response)).toEqual({
        ...response,
        example: { ...response.example, work_label: 'Work details unavailable' },
      })
      expect(mapActivityPermissionReadiness({ ...response, example: null }).example).toBeNull()
      expect(() => mapActivityPermissionReadiness({ ...response, events: [] })).toThrow(ApiError)
      expect(() =>
        mapActivityPermissionReadiness({ ...response, backup_ready: undefined }),
      ).toThrow(ApiError)
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
    const history = mapActivity({ ...currentActivity, lapsed_consents, waiting })
    expect(mapActivityConsent(response)).toEqual({
      backup_ready: history.backup_ready,
      capabilities: history.capabilities,
      configured_libraries: history.configured_libraries,
      lapsed_consents: history.lapsed_consents,
      waiting: history.waiting,
    })
    expect(history.lapsed_consents).toEqual(lapsed_consents)
    for (const key of Object.keys(response))
      expect(() =>
        mapActivityConsent(Object.fromEntries(Object.entries(response).filter(([k]) => k !== key))),
      ).toThrow(ApiError)
    for (const invalid of [
      { ...response, events: [] },
      { ...response, backup_ready: true },
      { ...response, capabilities: {} },
      { ...response, configured_libraries: [{ connection: 'primary' }] },
      { ...response, waiting: { ...waiting, scheduled: -1 } },
      { ...response, waiting: { ...waiting, applying: undefined } },
      { ...response, waiting: null },
      // A lapse is closed: three reasons, three keys, nothing else (#1370).
      { ...response, lapsed_consents: null },
      { ...response, lapsed_consents: [{ ...lapsed_consents[0], reason: 'expired' }] },
      { ...response, lapsed_consents: [{ ...lapsed_consents[0], basis: 'owner_grant' }] },
      { ...response, lapsed_consents: [{ connection: 'primary', library: 'Fiction' }] },
    ])
      expect(() => mapActivityConsent(invalid)).toThrow(ApiError)
    expect(() => mapActivity({ ...currentActivity, lapsed_consents: undefined })).toThrow(
      expect.objectContaining({ code: 'invalid_activity_lapsed_consents' }),
    )
    // A waiting value that is not a record is refused as such by both reads.
    for (const waiting of [null, [], 'scheduled'])
      for (const read of [
        () => mapActivity({ ...currentActivity, waiting }),
        () => mapActivityConsent({ ...response, waiting }),
      ])
        expect(read).toThrow(expect.objectContaining({ code: 'invalid_activity_waiting' }))
    const fetchMock = vi.fn<typeof fetch>(async () => Response.json(response))
    const client = new ReviewApiClient({ csrf, fetch: fetchMock, buildIdentity: 'c'.repeat(64) })
    expect(await client.getActivityConsent()).toEqual(mapActivityConsent(response))
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
  const progressPaths = ['primary/Books/scan', 'primary/Books/retry']
  const viewPaths = ['first-boot/acknowledge', 'pause', 'resume', 'primary/check-connection']
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
  ] as const)(
    'reports an undecodable %s answer as a 502 with its reason and pointer (#1686, #1687)',
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

  type Answer = readonly [path: string, body: unknown, status: number, reason: string, at: string]
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

  it('does not decode a 204 answer to an operation outside the gate', async () => {
    await expect(
      answering(undefined, 204).returnToAutomatic('case-1', 'title'),
    ).resolves.toBeUndefined()
  })

  it('fails closed on a 204 answer to a gated operation', async () => {
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

  // The private command seam, so the proofs need no ungated operation's mapper.
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
    await client.policyCommand('validate', {})
    await client.policyCommand('preview', {})
    await client.policyCommand('drafts', {})
    expect(spy.mock.calls.map((call) => [call[5], call[6]])).toEqual([
      [true, true],
      [true, true],
      [false, false],
    ])
    spy.mockClear()
    spy.mockResolvedValueOnce({})
    await client.retryProviderRecovery(7).catch(() => undefined)
    expect(spy.mock.calls.map((call) => [call[5], call[6]])).toEqual([[true, false]])
  })

  it('passes an operation outside the decode gate through unchanged', async () => {
    await expect(answering({ unexpected: true }, 201).policyCommand('drafts', {})).resolves.toEqual(
      { unexpected: true },
    )
  })
})
