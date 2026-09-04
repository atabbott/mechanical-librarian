import { describe, expect, it, vi } from 'vitest'

import {
  ApiError,
  PageCsrfTokenStore,
  ReviewApiClient,
  mapCaseDetail,
  mapActivity,
  mapAttention,
  derivePolicyDraftDisplay,
  SessionExpiredError,
  mapLibrary,
  mapEvidence,
  mapHistory,
  mapPreparation,
  mapCoverConsideration,
  mapCodexDevice,
  mapReviewerAccess,
} from '../src/api/http'

const token = 'a'.repeat(32)
const renewedToken = 'b'.repeat(32)

const csrf = {
  current: vi.fn(() => token),
  renew: vi.fn(async () => renewedToken),
}

const reviewerAccessPayload = (overrides: Record<string, unknown> = {}) => ({
  schema_version: 'reviewer-access-view-v1',
  revision: 3,
  mapping: null,
  openrouter_key_configured: false,
  codex: {
    engine: 'codex',
    state: 'ready',
    reason: null,
    model: null,
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
  it('preserves a closed provider-local recommendation', () => {
    expect(
      mapReviewerAccess(reviewerAccessPayload({ recommendations: ['openai/gpt-4o-mini'] }))
        .recommendations,
    ).toEqual(['openai/gpt-4o-mini'])
  })

  it.each([
    'invalid_key',
    'quota_exhausted',
    'rate_limited',
    'model_unavailable',
    'model_text_only',
    'malformed_response',
    'unreachable',
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
          model: null,
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
          model: null,
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

  it('accepts only the fixed issued prompt shape', () => {
    const issued = {
      state: 'issued',
      verification_url: 'https://auth.openai.com/codex/device',
      user_code: 'ABCD-1234',
      expires_in_seconds: 900,
      failure: null,
    }
    expect(mapCodexDevice(issued).state).toBe('issued')
    expect(() => mapCodexDevice({ ...issued, user_code: 'Bearer secret' })).toThrow(
      'invalid_codex_device_response',
    )
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
    ) => ({
      alternative_id,
      selection,
      recommendation,
      recommendation_reason:
        recommendation === 'recommended'
          ? 'Authoritative policy recommendation.'
          : 'No recommendation retained.',
      rationale: 'Retained cover choice.',
      limitations: [],
      evidence: [],
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
    const candidate = (overrides: Record<string, unknown> = {}) => ({
      artifact_id: 'artifact-1',
      deterministic_citations: ['citation-1'],
      deterministic_rank: 1,
      deterministic_rank_key: [1, 0, -10000, -100, -100, 0, 'candidate-1'],
      height: 100,
      identity: { outcome: 'verified_work_match' },
      incumbent: false,
      match_state: 'same_book',
      observation: null,
      original_sha256: 'a'.repeat(64),
      ordinal: 0,
      preview_sha256: 'b'.repeat(64),
      reference: 'candidate-1',
      selectable: true,
      source_kinds: [],
      state: 'acceptable',
      width: 100,
      ...overrides,
    })
    const policy = (overrides: Record<string, unknown> = {}) => ({
      status: 'abstained',
      recommended_candidate_reference: null,
      acceptable_candidate_references: ['candidate-1'],
      ...overrides,
    })
    const payload = (overrides: Record<string, unknown> = {}) => ({
      schema_version: 'cover-consideration-v3',
      consideration_id: 'consideration',
      current: true,
      candidates: [candidate()],
      choice_contexts: [context(null, 'current'), context('candidate-1', 'proposed')],
      incumbent: { state: 'absent', reference: null },
      omissions: [],
      policy: policy(),
      policy_preferences: {
        avoid_promotional_text: false,
        avoid_tie_in_branding: false,
        prefer_minimal_composition: false,
      },
      policy_reference: 'policy',
      provider_fetching: true,
      ready: false,
      reviewer_access_revision: 7,
      reviewer_engine: 'openrouter',
      reviewer_model: 'provider/model',
      reviewer_status: 'completed',
      reviewer_unavailable_reason: null,
      supersedes_id: null,
      target_fingerprint: 'fingerprint',
      ...overrides,
    })

    expect(mapCoverConsideration(payload())?.choiceContexts).toHaveLength(2)
    expect(
      mapCoverConsideration(
        payload({ candidates: [], choice_contexts: [context(null, 'current')], policy: null }),
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
            candidate({ identity: { outcome: 'unknown' }, match_state: 'match_not_confirmed' }),
          ],
        },
      ],
      ['conflict-same-book', { candidates: [candidate({ identity: { outcome: 'conflict' } })] }],
      [
        'manifestation-wrong-label',
        { candidates: [candidate({ identity: { outcome: 'verified_manifestation_match' } })] },
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
  })
  it('maps only the fixed unavailable reviewer-contract reasons', () => {
    const payload = (reviewer_unavailable_reason: unknown) => ({
      schema_version: 'cover-consideration-v3',
      consideration_id: 'consideration',
      current: true,
      candidates: [],
      choice_contexts: [
        {
          alternative_id: null,
          selection: 'current',
          recommendation: 'no_recommendation',
          recommendation_reason: 'No recommendation retained.',
          rationale: 'Retained cover choice.',
          limitations: [],
          evidence: [],
        },
      ],
      incumbent: { state: 'absent', reference: null },
      omissions: [],
      policy: null,
      policy_preferences: {
        avoid_promotional_text: false,
        avoid_tie_in_branding: false,
        prefer_minimal_composition: false,
      },
      policy_reference: 'policy',
      provider_fetching: false,
      ready: false,
      reviewer_access_revision: null,
      reviewer_engine: null,
      reviewer_model: null,
      reviewer_status: 'unavailable',
      reviewer_unavailable_reason,
      supersedes_id: null,
      target_fingerprint: 'fingerprint',
    })

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
  it('preserves present and absent series sequences in the mapped review value', () => {
    const detail = (
      series: readonly Record<string, unknown>[],
      choiceContexts: unknown = [
        {
          alternative_id: null,
          evidence: [],
          limitations: [],
          rationale: 'The local library has no retained value for this field.',
          recommendation: 'no_recommendation',
          recommendation_reason:
            'No deterministic recommendation is retained for the current value.',
          selection: 'current',
        },
      ],
    ) =>
      mapCaseDetail({
        schema_version: 'review-case-detail-v2',
        review_case_id: 'case-1',
        current: true,
        comparison_notices: [],
        writes_consented: false,
        target: {
          item: 'internal-item-1',
          title: 'A Book',
          library: 'Books',
          connection: 'primary',
          media_kind: 'ebook',
          authors: ['Ada Example'],
        },
        fields: [
          {
            classification: 'needs_attention',
            classification_reason: 'This field needs your review.',
            editable: true,
            inventory_role: 'primary',
            navigation_role: 'attention',
            preservation_only: false,
            decision_actions: [],
            destination: 'series',
            deterministic_disposition: 'review',
            effect_capability: 'manual',
            needs_attention: true,
            manual_hold: false,
            current: { value: series },
            proposed_values: [],
            choice_contexts: choiceContexts,
            proposal_id: 'proposal-1',
            target_fingerprint: 'fingerprint-1',
            human_decision: null,
            automatic_decision: null,
            automatic_selection_sources: [],
            notices: [],
            provenance: [],
            corroboration_sources: [],
          },
        ],
      })

    expect(detail([{ name: 'Archive', sequence: '2.5' }]).fields[0]?.currentValue).toBe(
      'Archive · 2.5',
    )
    expect(detail([{ name: 'Standalone' }]).fields[0]?.currentValue).toBe('Standalone')
    expect(() => detail([], [])).toThrow(new ApiError(502, 'misaligned_choice_contexts'))
  })

  it('requires exact creator corroboration source lists in case-detail responses', () => {
    const response = (corroboration_sources: unknown) => ({
      schema_version: 'review-case-detail-v2',
      review_case_id: 'case-1',
      current: true,
      comparison_notices: [],
      writes_consented: false,
      target: {
        item: 'internal-item-1',
        title: 'A Book',
        library: 'Books',
        connection: 'primary',
        media_kind: 'ebook',
        authors: ['Ada Example'],
      },
      fields: [
        {
          classification: 'needs_attention',
          classification_reason: 'This field needs your review.',
          editable: true,
          inventory_role: 'primary',
          navigation_role: 'attention',
          preservation_only: false,
          decision_actions: [],
          destination: 'authors',
          deterministic_disposition: 'review',
          effect_capability: 'manual',
          needs_attention: true,
          manual_hold: false,
          current: { value: ['Ada Example'] },
          proposed_values: [],
          choice_contexts: [
            {
              alternative_id: null,
              evidence: [],
              limitations: [],
              rationale: 'The local library has no retained value for this field.',
              recommendation: 'no_recommendation',
              recommendation_reason:
                'No deterministic recommendation is retained for the current value.',
              selection: 'current',
            },
          ],
          proposal_id: 'proposal-1',
          target_fingerprint: 'fingerprint-1',
          human_decision: null,
          automatic_decision: null,
          automatic_selection_sources: [],
          notices: [],
          provenance: [],
          corroboration_sources,
        },
      ],
    })

    expect(() => mapCaseDetail(response(undefined))).toThrow(ApiError)
    expect(() => mapCaseDetail(response('Hardcover'))).toThrow(ApiError)
    expect(
      mapCaseDetail(response(['Hardcover', 'Open Library'])).fields[0]?.corroborationSources,
    ).toEqual(['Hardcover', 'Open Library'])
  })

  it('uses Unicode code point bounds for readable case identity', () => {
    const astral = '📚'
    const response = (target: Record<string, unknown>) => ({
      schema_version: 'review-case-detail-v2',
      review_case_id: 'case-1',
      current: true,
      comparison_notices: [],
      writes_consented: false,
      target: {
        item: 'internal-item-1',
        title: 'A Book',
        library: 'Books',
        connection: 'primary',
        media_kind: 'ebook',
        authors: ['Ada Example'],
        ...target,
      },
      fields: [],
    })

    const exact = astral.repeat(512)
    expect(mapCaseDetail(response({ title: exact, authors: [exact] }))).toMatchObject({
      title: exact,
      authors: [exact],
    })
    expect(() => mapCaseDetail(response({ title: astral.repeat(513) }))).toThrow(ApiError)
    expect(() => mapCaseDetail(response({ authors: [astral.repeat(513)] }))).toThrow(ApiError)
  })

  it('rejects case-detail fields that omit the objects the view dereferences', () => {
    const field = (overrides: Record<string, unknown> = {}) => ({
      classification: 'needs_attention',
      classification_reason: 'This field needs your review.',
      editable: true,
      inventory_role: 'primary',
      navigation_role: 'attention',
      preservation_only: false,
      decision_actions: [],
      destination: 'title',
      deterministic_disposition: 'review',
      effect_capability: 'manual',
      needs_attention: true,
      manual_hold: false,
      current: { value: 'A Book' },
      proposed_values: [],
      choice_contexts: [
        {
          alternative_id: null,
          evidence: [],
          limitations: [],
          rationale: 'The local library has no retained value for this field.',
          recommendation: 'no_recommendation',
          recommendation_reason:
            'No deterministic recommendation is retained for the current value.',
          selection: 'current',
        },
      ],
      proposal_id: 'proposal-1',
      target_fingerprint: 'fingerprint-1',
      human_decision: null,
      automatic_decision: null,
      automatic_selection_sources: [],
      notices: [],
      provenance: [],
      corroboration_sources: [],
      ...overrides,
    })
    const response = (fields: readonly unknown[]) => ({
      schema_version: 'review-case-detail-v2',
      review_case_id: 'case-1',
      current: true,
      comparison_notices: [],
      writes_consented: false,
      target: {
        item: 'internal-item-1',
        title: 'A Book',
        library: 'Books',
        connection: 'primary',
        media_kind: 'ebook',
        authors: ['Ada Example'],
      },
      fields,
    })

    expect(mapCaseDetail(response([field()])).fields[0]?.currentValue).toBe('A Book')
    const retainedEvidence = field({
      decision_actions: ['keep_current', 'accept'],
      proposed_values: [{ kind: 'text', value: 'Alternative' }],
      choice_contexts: [
        {
          alternative_id: null,
          evidence: [],
          limitations: [],
          rationale: 'Current evidence.',
          recommendation: 'no_recommendation',
          recommendation_reason: 'No recommendation retained.',
          selection: 'current',
        },
        {
          alternative_id: 'eligible',
          evidence: [],
          limitations: [],
          rationale: 'Eligible evidence.',
          recommendation: 'no_recommendation',
          recommendation_reason: 'No recommendation retained.',
          selection: 'proposed',
        },
        {
          alternative_id: 'preserves-target',
          evidence: [],
          limitations: [],
          rationale: 'Retained non-selectable evidence.',
          recommendation: 'no_recommendation',
          recommendation_reason: 'No recommendation retained.',
          selection: 'proposed',
        },
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
          lane_kind: 'identity',
          provider: 'Hardcover',
          reason: 'Returned matching metadata for this work.',
          status: 'candidates',
        },
      ],
    }
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
          laneKind: 'identity',
          provider: 'Hardcover',
          reason: 'Returned matching metadata for this work.',
          status: 'candidates',
        },
      ],
    })
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
    const payload = {
      schema_version: 'review-case-history-v2',
      review_case_id: 'case-1',
      next_cursor: null,
      items: [
        {
          actor: 'owner',
          application: 'apply' as const,
          decision_id: 'decision-secret',
          destination: 'title',
          event_id: 'event-secret',
          kind: 'human_decision_recorded',
          occurred_at: 2,
          outcome: 'accept',
          previous_value: { kind: 'text', value: 'Old' },
          review_case_id: 'case-1',
          schema_version: 'human-decision-event-v1',
          selected_value: { kind: 'text', value: 'New' },
        },
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
      ],
    }
    expect(mapHistory(payload)).toEqual({
      items: [
        {
          category: 'owner',
          applicationReason: null,
          destination: 'title',
          outcome: 'accept',
          previousValue: { kind: 'text', value: 'Old' },
          selectedValue: { kind: 'text', value: 'New' },
        },
        {
          category: 'automatic',
          destination: 'authors',
          outcome: 'ignore',
          previousValue: null,
          selectedValue: null,
        },
        { category: 'transition', outcome: 'admitted', reason: '<script>actionable</script>' },
        {
          category: 'transition',
          outcome: 'superseded',
          reason: 'A newer review replaced this one.',
        },
      ],
    })
    const cover = {
      consideration_id: 'cover-secret',
      consideration: {
        candidates: [{ reference: 'candidate-secret' }],
        policy: { reasons: ['policy_satisfied'], status: 'recommended' },
        reviewer_status: 'completed',
      },
      decision: { outcome: 'accept' },
      decision_id: 'decision-secret',
      kind: 'selected',
      occurred_at: 3,
      review_case_id: 'case-1',
      schema_version: 'cover-consideration-event-v1',
    }
    expect(mapHistory({ ...payload, items: [cover] })).toEqual({
      items: [
        {
          category: 'cover',
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
    expect(mapHistory({ ...payload, items: [{ ...cover, kind: 'superseded' }] })).toEqual({
      items: [
        {
          category: 'cover',
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
            decision: { outcome: 'candidate', supersedes_decision_id: 'automatic-prior' },
          },
        ],
      }),
    ).toEqual({
      items: [
        {
          automaticOutcome: 'candidate',
          category: 'cover',
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
      { ...payload, items: [{ ...payload.items[0], outcome: 'unknown' }] },
      { ...payload, items: [{ ...payload.items[0], selected_value: { kind: 'text', value: {} } }] },
      {
        ...payload,
        items: [
          { ...payload.items[0], selected_value: { kind: 'text', value: 'x'.repeat(16_385) } },
        ],
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
              candidates: Array.from({ length: 13 }, () => ({})),
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
  })

  it('accepts domain-bounded history values by Unicode code points', () => {
    const astral = '📚'
    const decision = (selected_value: unknown) => ({
      actor: 'owner',
      application: 'apply' as const,
      decision_id: 'decision-secret',
      destination: 'description',
      event_id: 'event-secret',
      kind: 'human_decision_recorded',
      occurred_at: 2,
      outcome: 'accept',
      previous_value: null,
      review_case_id: 'case-1',
      schema_version: 'human-decision-event-v1',
      selected_value,
    })
    const history = (selected_value: unknown) => ({
      schema_version: 'review-case-history-v2',
      review_case_id: 'case-1',
      next_cursor: null,
      items: [decision(selected_value)],
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

    const cover = (reason: string) => ({
      consideration: {
        candidates: [],
        policy: { reasons: [reason], status: 'abstained' },
        reviewer_status: 'completed',
      },
      decision: null,
      kind: 'no_action',
      occurred_at: 3,
      schema_version: 'cover-consideration-event-v1',
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
      Response.json({
        schema_version: 'review.v1',
        lens: 'all',
        query: null,
        next_cursor: null,
        previous_cursor: 'page-1',
        page: 2,
        counts: { needs_attention: 1, all: 2, no_attention_needed: 1 },
        items: [
          {
            review_case_id: 'case-1',
            title: 'A Book',
            authors: ['Ada Example'],
            library: 'Books',
            media_kind: 'ebook',
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
          },
          {
            review_case_id: null,
            title: 'Catalogue-only work',
            authors: ['Grace Example'],
            library: 'Books',
            media_kind: 'ebook',
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
                reason: 'Returned no matching metadata, so nothing was retained.',
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
          },
        ],
      }),
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
      mediaKind: 'ebook',
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
      mediaKind: 'ebook',
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
          reason: 'Returned no matching metadata, so nothing was retained.',
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
          { destination: 'subtitle', state: 'missing', value: 'Unavailable' },
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

  it('maps field-level attention context without promoting internal target keys', () => {
    const view = mapAttention({
      schema_version: 'attention-v1',
      query: null,
      next_cursor: null,
      total: 1,
      items: [
        {
          review_case_id: 'case-1',
          destination: 'publication_date',
          title: 'A Book',
          authors: ['Ada Example'],
          library: 'Books',
          media_kind: 'ebook',
          item: 'internal-item-1',
          detail_url: '/review/case-1?field=publication_date',
          classification_reason: 'The available dates disagree.',
          notices: ['The available dates disagree.'],
          provenance: ['Selected EPUB supports a complete date.'],
          work_attention_count: 2,
        },
      ],
    })

    expect(view.items[0]).toEqual({
      id: 'case-1',
      destination: 'publication_date',
      title: 'A Book',
      authors: ['Ada Example'],
      library: 'Books',
      mediaKind: 'ebook',
      classificationReason: 'The available dates disagree.',
      notices: ['The available dates disagree.'],
      provenance: ['Selected EPUB supports a complete date.'],
      workAttentionCount: 2,
    })
    expect(JSON.stringify(view)).not.toContain('internal-item-1')
  })

  it('rejects a malformed activity display projection before it reaches the workspace', () => {
    expect(() =>
      mapActivity({
        schema_version: 'continuous-field-activity-v1',
        backup_ready: true,
        paused: false,
      }),
    ).toThrow(ApiError)
  })

  it('maps bounded activity labels and grouped state into the display seam', () => {
    const activity = mapActivity({
      schema_version: 'continuous-field-activity-v2',
      backup_ready: true,
      paused: false,
      configured_libraries: [
        {
          connection: 'primary',
          library: 'Fiction',
          prepared: { automatic: 1, human: 2, total: 3 },
        },
      ],
      current_waiting: null,
      consented_libraries: [],
      enabled_libraries: [],
      counts: { verified: 1 },
      capabilities: [
        {
          connection: 'primary',
          library: 'Fiction',
          state: 'allowed',
          destinations: [{ destination: 'title', state: 'allowed', consented: true, prepared: 4 }],
        },
      ],
      waiting: {
        applying: 1,
        awaiting_permission: 2,
        held_for_recovery: 3,
        retrying: 4,
        scheduled: 5,
      },
      provider_recovery: [
        {
          state: 'waiting',
          affected_count: 2,
          next_eligible_at: 2,
          dispatch_reasons: ['missing_corroboration', 'blanket_scan'],
        },
      ],
      events: [
        {
          destination: 'title',
          event_id: 'event-1',
          event_type: 'review',
          category: 'owner',
          applicationReason: null,
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
        },
      ],
      ledger: {
        event_type: 'all',
        page: 1,
        page_count: 1,
        page_size: 25,
        query: null,
        range_days: 30,
        total: 1,
      },
      example: {
        after: 'After',
        before: 'Before',
        destination: 'title',
        library: 'Fiction',
        work_id: 'work-1',
      },
      last_failure: null,
      prepared: { automatic: 1, human: 2, total: 3 },
      suspensions: [
        {
          connection: 'primary',
          destination: null,
          reason: 'recovery',
          scope: 'library',
          updated_at: 1,
        },
      ],
    })

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
    for (const dispatch_reasons of [
      ['private-detail'],
      ['blanket_scan', 'blanket_scan'],
      'blanket_scan',
    ])
      expect(() =>
        mapActivity({
          ...activity,
          provider_recovery: [{ ...activity.provider_recovery?.[0], dispatch_reasons }],
        }),
      ).toThrow(ApiError)
    const terminalRecovery = mapActivity({
      ...activity,
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
      state: 'recovered',
      affected_count: 1,
      next_eligible_at: null,
      dispatch_reasons: [],
    })
    expect(() => mapActivity({ ...activity, waiting: { applying: 1 } })).toThrow(ApiError)
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
      ...activity,
      events: [
        {
          ...activity.events[0],
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
        ...activity,
        events: [
          {
            ...activity.events[0],
            diagnostic: { event_class: 'automatic_decision', reason: 'private-detail' },
          },
        ],
      }),
    ).toThrow(ApiError)
    expect(() =>
      mapActivity({ ...activity, events: [{ ...activity.events[0], category: 'review' }] }),
    ).toThrow(ApiError)
    expect(activity.suspensions[0]).toMatchObject({ scope: 'library', destination: null })
    expect(() =>
      mapActivity({
        ...activity,
        suspensions: [
          {
            connection: 'primary',
            destination: null,
            reason: 'recovery',
            scope: 'library',
            updated_at: 'later',
          },
        ],
      }),
    ).toThrow(ApiError)
    expect(() => mapActivity({ ...activity, example: { after: 'After' } })).toThrow(ApiError)
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
        schema_version: 'metadata-policy-profile-v2',
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
        schema_version: 'metadata-policy-profile-v2',
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
        schema_version: 'metadata-policy-profile-v2',
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

  it('renews CSRF once but requires an explicit retry of an unsafe request', async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(Response.json({ error: { code: 'csrf' } }, { status: 403 }))
      .mockResolvedValueOnce(new Response(null, { status: 204 }))
    const client = new ReviewApiClient({ csrf, fetch: fetchMock, buildIdentity: 'c'.repeat(64) })

    await expect(client.logout()).rejects.toMatchObject({ status: 403, code: 'csrf_renewed' })

    expect(csrf.renew).toHaveBeenCalledOnce()
    expect(fetchMock).toHaveBeenCalledOnce()
    csrf.current.mockReturnValueOnce(renewedToken)
    await client.logout()
    expect(new Headers(fetchMock.mock.calls[1]?.[1]?.headers).get('x-csrf-token')).toBe(
      renewedToken,
    )
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

    await expect(client.logout()).rejects.toBeInstanceOf(SessionExpiredError)
    await vi.waitFor(() => expect(expired).toHaveBeenCalledOnce())
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
        Response.json({
          scan_id: 'scan-1',
          connection: 'primary',
          library: 'Books',
          library_id: 'books',
          state: 'queued',
          checked: 0,
          total: 2,
          ready: 0,
          blocked: 0,
          recorded: 0,
          rescan_requested: false,
        }),
      )
      .mockResolvedValueOnce(
        Response.json({
          schema_version: 'review-preparation-v2',
          active: false,
          actionable: false,
          paused: false,
          libraries: [],
          connections: [],
        }),
      )
    const client = new ReviewApiClient({ csrf, fetch: fetchMock, buildIdentity: 'c'.repeat(64) })

    await expect(client.preparationAction('primary/Books/scan')).resolves.toMatchObject({
      scan_id: 'scan-1',
      state: 'queued',
    })
    await expect(client.preparationAction('pause')).resolves.toMatchObject({
      schema_version: 'review-preparation-v2',
      paused: false,
    })
  })

  it('maps only closed target-connection preparation conditions', () => {
    const base = {
      schema_version: 'review-preparation-v2',
      active: true,
      actionable: false,
      paused: false,
      libraries: [],
    }
    for (const state of ['paused', 'checking', 'recovered']) {
      expect(
        mapPreparation({
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
        }).connections?.[0]?.state,
      ).toBe(state)
    }
    for (const invalid of [
      base,
      {
        ...base,
        connections: [
          { connection: 'primary', code: 'unknown', state: 'paused', affected: 1, updated_at: 1 },
        ],
      },
      {
        ...base,
        connections: [{ connection: 'primary', code: 'tls', state: 'paused', affected: 1 }],
      },
    ])
      expect(() => mapPreparation(invalid)).toThrow(ApiError)
  })

  it('accepts only ordered and bounded preparation presentation content', () => {
    const presentation = {
      run_id: `preparation_run_${'a'.repeat(64)}`,
      state: 'running',
      triggers: [
        {
          trigger: 'manual_scan',
          accepted_count: 1,
          first_accepted_at: 100,
          last_accepted_at: 100,
        },
      ],
      passage: { read: 4, searches_finished: 3, compared: 2, checked: 2, ready: 1, failed: 1 },
      active_books: [{ name: 'Private title', stage: 'Comparing candidates' }],
      active_overflow: 0,
      recent_completions: [{ name: 'Finished title', outcome: 'ready', completed_at: 101 }],
      throughput_per_minute: 1.2,
      started_at: 100,
      updated_at: 101,
      completed_at: null,
      total: 4,
      dismissible: false,
    }
    const base = {
      schema_version: 'review-preparation-v2',
      active: true,
      actionable: false,
      paused: false,
      libraries: [],
      connections: [],
      presentation,
    }
    expect(mapPreparation(base).presentation?.passage.ready).toBe(1)
    const maximumName = 'x'.repeat(2048)
    expect(
      mapPreparation({
        ...base,
        presentation: {
          ...presentation,
          active_books: [{ name: maximumName, stage: 'Reading' }],
          recent_completions: [{ name: maximumName, outcome: 'ready', completed_at: 101 }],
        },
      }).presentation?.active_books[0]?.name,
    ).toBe(maximumName)
    const maximumMultibyteName = `${'界'.repeat(682)}ab`
    expect(new TextEncoder().encode(maximumMultibyteName)).toHaveLength(2048)
    expect(
      mapPreparation({
        ...base,
        presentation: {
          ...presentation,
          active_books: [{ name: maximumMultibyteName, stage: 'Reading' }],
          recent_completions: [{ name: maximumMultibyteName, outcome: 'ready', completed_at: 101 }],
        },
      }).presentation?.recent_completions[0]?.name,
    ).toBe(maximumMultibyteName)
    expect(() =>
      mapPreparation({
        ...base,
        presentation: {
          ...presentation,
          active_books: [{ name: `${maximumName}x`, stage: 'Reading' }],
        },
      }),
    ).toThrow('invalid_preparation_active_name')
    expect(() =>
      mapPreparation({
        ...base,
        presentation: {
          ...presentation,
          recent_completions: [{ name: `${maximumName}x`, outcome: 'ready', completed_at: 101 }],
        },
      }),
    ).toThrow('invalid_preparation_completion_name')
    expect(() =>
      mapPreparation({
        ...base,
        presentation: {
          ...presentation,
          passage: { ...presentation.passage, checked: 1, ready: 2 },
        },
      }),
    ).toThrow('invalid_preparation_passage_order')
    expect(
      mapPreparation({
        ...base,
        presentation: {
          ...presentation,
          passage: { read: 1, searches_finished: 0, compared: 0, checked: 0, ready: 0, failed: 1 },
        },
      }).presentation?.passage.failed,
    ).toBe(1)
    expect(
      mapPreparation({
        ...base,
        presentation: {
          ...presentation,
          passage: { read: 1, searches_finished: 1, compared: 1, checked: 1, ready: 1, failed: 1 },
          total: 1,
        },
      }).presentation?.passage,
    ).toMatchObject({ ready: 1, failed: 1 })
    expect(() =>
      mapPreparation({ ...base, presentation: { ...presentation, updated_at: 99 } }),
    ).toThrow('invalid_preparation_passage_total')
    expect(() =>
      mapPreparation({ ...base, presentation: { ...presentation, state: 'completed_clean' } }),
    ).toThrow('invalid_preparation_passage_total')
    expect(() =>
      mapPreparation({ ...base, presentation: { ...presentation, completed_at: 101 } }),
    ).toThrow('invalid_preparation_passage_total')
    expect(() =>
      mapPreparation({
        ...base,
        presentation: { ...presentation, state: 'dismissed', completed_at: 99 },
      }),
    ).toThrow('invalid_preparation_passage_total')
    expect(() =>
      mapPreparation({
        ...base,
        presentation: {
          ...presentation,
          passage: { read: 0, searches_finished: 0, compared: 0, checked: 0, ready: 0, failed: 0 },
          active_overflow: 1,
          total: 1,
        },
      }),
    ).toThrow('invalid_preparation_passage_total')
    expect(() =>
      mapPreparation({
        ...base,
        presentation: {
          ...presentation,
          active_books: Array.from({ length: 6 }, () => ({
            name: 'Private title',
            stage: 'Reading',
          })),
        },
      }),
    ).toThrow('invalid_preparation_presentation')
    expect(() =>
      mapPreparation({
        ...base,
        presentation: {
          ...presentation,
          recent_completions: [{ name: 'Private title', outcome: 'invented', completed_at: 101 }],
        },
      }),
    ).toThrow('invalid_preparation_completion')
  })

  it('preserves structured server error codes and rejects malformed DTOs', async () => {
    const client = new ReviewApiClient({
      csrf,
      fetch: vi.fn(async () =>
        Response.json({ schema_version: 'review.v1', error: { code: 'busy' } }, { status: 503 }),
      ),
    })
    await expect(client.getLibrary({ lens: 'all' })).rejects.toMatchObject({
      status: 503,
      code: 'busy',
    })
    expect(() =>
      mapLibrary({ items: [], counts: {}, lens: '__proto__', schema_version: 'review.v1' }),
    ).toThrow(ApiError)
    const work = {
      review_case_id: null,
      title: 'A Book',
      authors: [],
      library: 'Books',
      media_kind: 'audiobook',
      item: 'item-1',
      attention_count: 0,
      attention_state: 'no_attention_needed',
      detail_url: '/api/v1/library/report?item=item-1',
      detail_kind: 'reports',
      dispositions: [],
      work_id: 'library_work_1',
      preparation: null,
      preparation_outcomes: [],
      summary: null,
    }
    const page = {
      items: [work],
      counts: { needs_attention: 0, all: 1, no_attention_needed: 1 },
      lens: 'all',
      page: 1,
      schema_version: 'review.v1',
    }
    expect(() => mapLibrary(page)).toThrow(new ApiError(502, 'invalid_detail_kind'))
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
