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
  mapPreparation,
} from '../src/api/http'

const token = 'a'.repeat(32)
const renewedToken = 'b'.repeat(32)

const csrf = {
  current: vi.fn(() => token),
  renew: vi.fn(async () => renewedToken),
}

describe('ReviewApiClient', () => {
  it('preserves present and absent series sequences in the mapped review value', () => {
    const detail = (series: readonly Record<string, unknown>[], choiceContexts: unknown = [{ alternative_id: null, evidence: [], limitations: [], rationale: 'The local library has no retained value for this field.', recommendation: 'no_recommendation', recommendation_reason: 'No deterministic recommendation is retained for the current value.', selection: 'current' }]) => mapCaseDetail({
      schema_version: 'review-case-detail-v1', review_case_id: 'case-1', current: true,
      target: { item: 'A Book', library: 'Books', connection: 'primary', media_kind: 'ebook', authors: ['Ada Example'] },
      fields: [{
        classification: 'needs_attention', classification_reason: 'This field needs your review.', editable: true, navigation_role: 'attention', preservation_only: false,
        destination: 'series', deterministic_disposition: 'review', effect_capability: 'manual',
        needs_attention: true, manual_hold: false, current: { value: series },
        proposed_values: [], choice_contexts: choiceContexts, proposal_id: 'proposal-1', target_fingerprint: 'fingerprint-1',
        human_decision: null, automatic_decision: null, notices: [], provenance: [],
        corroboration_sources: [],
      }],
    })

    expect(detail([{ name: 'Archive', sequence: '2.5' }]).fields[0]?.currentValue).toBe('Archive · 2.5')
    expect(detail([{ name: 'Standalone' }]).fields[0]?.currentValue).toBe('Standalone')
    expect(() => detail([], [])).toThrow(new ApiError(502, 'misaligned_choice_contexts'))
  })

  it('requires exact creator corroboration source lists in case-detail responses', () => {
    const response = (corroboration_sources: unknown) => ({
      schema_version: 'review-case-detail-v1', review_case_id: 'case-1', current: true,
      target: { item: 'A Book', library: 'Books', connection: 'primary', media_kind: 'ebook', authors: ['Ada Example'] },
      fields: [{
        classification: 'needs_attention', classification_reason: 'This field needs your review.', editable: true, navigation_role: 'attention', preservation_only: false,
        destination: 'authors', deterministic_disposition: 'review', effect_capability: 'manual',
        needs_attention: true, manual_hold: false, current: { value: ['Ada Example'] },
        proposed_values: [], choice_contexts: [{ alternative_id: null, evidence: [], limitations: [], rationale: 'The local library has no retained value for this field.', recommendation: 'no_recommendation', recommendation_reason: 'No deterministic recommendation is retained for the current value.', selection: 'current' }], proposal_id: 'proposal-1', target_fingerprint: 'fingerprint-1',
        human_decision: null, automatic_decision: null, notices: [], provenance: [],
        corroboration_sources,
      }],
    })

    expect(() => mapCaseDetail(response(undefined))).toThrow(ApiError)
    expect(() => mapCaseDetail(response('Hardcover'))).toThrow(ApiError)
    expect(mapCaseDetail(response(['Hardcover', 'Open Library'])).fields[0]?.corroborationSources)
      .toEqual(['Hardcover', 'Open Library'])
  })

  it('maps exact library DTOs into presentation-safe view models', async () => {
    const fetchMock = vi.fn<typeof fetch>(async () =>
      Response.json({
        schema_version: 'review.v1',
        lens: 'all',
        query: null,
        next_cursor: null,
        counts: { needs_attention: 1, all: 2, no_attention_needed: 1 },
        items: [{
          review_case_id: 'case-1',
          title: 'A Book',
          authors: ['Ada Example'],
          library: 'Books',
          media_kind: 'ebook',
          item: 'item-1',
          attention_count: 1,
          attention_state: 'needs_attention',
          detail_url: '/review/case-1',
          dispositions: ['unresolved_choice'],
          work_id: 'library_work_1',
          preparation: null,
          preparation_outcomes: [],
        }, {
          review_case_id: null,
          title: 'Catalogue-only work',
          authors: ['Grace Example'],
          library: 'Books',
          media_kind: 'ebook',
          item: 'internal-item-2',
          attention_count: 0,
          attention_state: 'no_attention_needed',
          detail_url: null,
          dispositions: [],
          work_id: 'library_work_2',
          preparation: null,
          preparation_outcomes: [{ provider_requests: 2, lane_kind: 'isbn', provider: 'google-books', reason: 'Provider returned no matching metadata.', status: 'no_match' }],
        }],
      }),
    )
    const client = new ReviewApiClient({ csrf, fetch: fetchMock, buildIdentity: 'c'.repeat(64) })

    const controller = new AbortController()
    const view = await client.getLibrary({ lens: 'all' }, controller.signal)

    expect(view.works[0]).toEqual({
      id: 'case-1',
      reviewCaseId: 'case-1',
      title: 'A Book',
      authors: ['Ada Example'],
      library: 'Books',
      mediaKind: 'ebook',
      attentionCount: 1,
      attention: 'needs_attention',
      preparation: null,
      preparationOutcomes: [],
    })
    expect(view.works[1]).toEqual({
      id: 'library_work_2',
      title: 'Catalogue-only work',
      authors: ['Grace Example'],
      library: 'Books',
      mediaKind: 'ebook',
      attentionCount: 0,
      attention: 'no_attention_needed',
      preparation: null,
      preparationOutcomes: [{ providerRequests: 2, laneKind: 'isbn', provider: 'google-books', reason: 'Provider returned no matching metadata.', status: 'no_match' }],
    })
    expect(JSON.stringify(view)).not.toContain('internal-item-2')
    expect(fetchMock).toHaveBeenCalledWith('/api/v1/library?lens=all', expect.objectContaining({
      cache: 'no-store',
      credentials: 'same-origin',
      signal: controller.signal,
    }))
  })

  it('maps field-level attention context without promoting internal target keys', () => {
    const view = mapAttention({
      schema_version: 'attention-v1', query: null, next_cursor: null, total: 1,
      items: [{
        review_case_id: 'case-1', destination: 'publication_date', title: 'A Book',
        authors: ['Ada Example'], library: 'Books', media_kind: 'ebook', item: 'internal-item-1',
        detail_url: '/review/case-1?field=publication_date', classification_reason: 'The available dates disagree.', notices: ['The available dates disagree.'],
        provenance: ['Selected EPUB supports a complete date.'], work_attention_count: 2,
      }],
    })

    expect(view.items[0]).toEqual({
      id: 'case-1', destination: 'publication_date', title: 'A Book', authors: ['Ada Example'],
      library: 'Books', mediaKind: 'ebook', classificationReason: 'The available dates disagree.', notices: ['The available dates disagree.'],
      provenance: ['Selected EPUB supports a complete date.'], workAttentionCount: 2,
    })
    expect(JSON.stringify(view)).not.toContain('internal-item-1')
  })

  it('rejects a malformed activity display projection before it reaches the workspace', () => {
    expect(() => mapActivity({ schema_version: 'continuous-field-activity-v1', backup_ready: true, paused: false })).toThrow(ApiError)
  })

  it('maps bounded activity labels and grouped state into the display seam', () => {
    const activity = mapActivity({
      schema_version: 'continuous-field-activity-v2', backup_ready: true, paused: false,
      configured_libraries: [{ connection: 'primary', library: 'Fiction', prepared: { automatic: 1, human: 2, total: 3 } }], current_waiting: null,
      consented_libraries: [], enabled_libraries: [], counts: { verified: 1 },
      capabilities: [{ connection: 'primary', library: 'Fiction', state: 'allowed', destinations: [{ destination: 'title', state: 'allowed', consented: true }] }],
      events: [{ destination: 'title', event_id: 'event-1', event_type: 'review', evidence: [{ label: 'Decision', value: 'Owner choice recorded' }], library: 'Fiction', occurred_at: 1, origin: 'human', state: 'verified', work_id: 'work-1' }],
      ledger: { event_type: 'all', page: 1, page_count: 1, page_size: 25, query: null, range_days: 30, total: 1 },
      example: { after: 'After', before: 'Before', destination: 'title', library: 'Fiction', work_id: 'work-1' }, last_failure: null, prepared: { automatic: 1, human: 2, total: 3 }, suspensions: [{ connection: 'primary', destination: null, reason: 'recovery', scope: 'library', updated_at: 1 }],
    })

    expect(activity.configured_libraries[0]?.library).toBe('Fiction')
    expect(activity.capabilities[0]?.destinations[0]).toMatchObject({ destination: 'title', consented: true })
    expect(activity.events[0]).toMatchObject({ library: 'Fiction', state: 'verified', origin: 'human' })
    expect(activity.events[0]).toMatchObject({ work_label: 'Recorded work' })
    expect(activity.suspensions[0]).toMatchObject({ scope: 'library', destination: null })
    expect(() => mapActivity({ ...activity, suspensions: [{ connection: 'primary', destination: null, reason: 'recovery', scope: 'library', updated_at: 'later' }] })).toThrow(ApiError)
    expect(() => mapActivity({ ...activity, example: { after: 'After' } })).toThrow(ApiError)
  })

  it('distinguishes matching active policy values from a changed local draft', () => {
    const active = { schema_version: 'metadata-policy-notebook-v1', policy_id: 'policy-1', profile: { schema_version: 'metadata-policy-profile-v2', name: 'Active', options: { title_layout: 'preserve' }, destinations: { title_layout: 'ask' } } } as never
    expect(derivePolicyDraftDisplay(active, { name: 'Active', options: { title_layout: 'preserve' }, destinations: { title_layout: 'ask' } })).toMatchObject({ changed: false, activeName: 'Active' })
    const reordered = { schema_version: 'metadata-policy-notebook-v1', policy_id: 'policy-1', profile: { schema_version: 'metadata-policy-profile-v2', name: 'Active', options: { first: 'one', second: 'two' }, destinations: { first: 'ask', second: 'ignore' } } } as never
    expect(derivePolicyDraftDisplay(reordered, { name: 'Active', options: { second: 'two', first: 'one' }, destinations: { second: 'ignore', first: 'ask' } })).toMatchObject({ changed: false })
    expect(derivePolicyDraftDisplay(active, { name: 'Draft', options: { title_layout: 'preserve' }, destinations: { title_layout: 'ask' } })).toMatchObject({ changed: true, draftName: 'Draft' })
  })

  it('renews CSRF once but requires an explicit retry of an unsafe request', async () => {
    const fetchMock = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(Response.json({ error: { code: 'csrf' } }, { status: 403 }))
      .mockResolvedValueOnce(new Response(null, { status: 204 }))
    const client = new ReviewApiClient({ csrf, fetch: fetchMock, buildIdentity: 'c'.repeat(64) })

    await expect(client.logout()).rejects.toMatchObject({ status: 403, code: 'csrf_renewed' })

    expect(csrf.renew).toHaveBeenCalledOnce()
    expect(fetchMock).toHaveBeenCalledOnce()
    csrf.current.mockReturnValueOnce(renewedToken)
    await client.logout()
    expect(new Headers(fetchMock.mock.calls[1]?.[1]?.headers).get('x-csrf-token')).toBe(renewedToken)
  })

  it('maps the server build-mismatch code to the controlled shell recovery', async () => {
    const mismatch = vi.fn()
    const client = new ReviewApiClient({
      csrf,
      buildIdentity: 'c'.repeat(64),
      fetch: vi.fn(async () => Response.json({ error: { code: 'browser_build_mismatch' } }, { status: 409 })),
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

    await expect(client.getLibrary({ lens: 'needs_attention' })).rejects.toBeInstanceOf(SessionExpiredError)
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
    expect(expired).toHaveBeenCalledOnce()
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

    expect(new Headers(fetchMock.mock.calls[0]?.[1]?.headers).get('x-ml-build-identity')).toBe(buildIdentity)
  })

  it('accepts scan progress acknowledgements independently from preparation controls', async () => {
    const fetchMock = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(Response.json({ scan_id: 'scan-1', connection: 'primary', library: 'Books', library_id: 'books', state: 'queued', checked: 0, total: 2, ready: 0, blocked: 0, rescan_requested: false }))
      .mockResolvedValueOnce(Response.json({ schema_version: 'review-preparation-v1', active: false, actionable: false, paused: false, libraries: [], connections: [] }))
    const client = new ReviewApiClient({ csrf, fetch: fetchMock, buildIdentity: 'c'.repeat(64) })

    await expect(client.preparationAction('primary/Books/scan')).resolves.toMatchObject({ scan_id: 'scan-1', state: 'queued' })
    await expect(client.preparationAction('pause')).resolves.toMatchObject({ schema_version: 'review-preparation-v1', paused: false })
  })

  it('maps only closed target-connection preparation conditions', () => {
    const base = { schema_version: 'review-preparation-v1', active: true, actionable: false, paused: false, libraries: [] }
    for (const state of ['paused', 'checking', 'recovered']) {
      expect(mapPreparation({ ...base, connections: [{ connection: 'primary', code: 'unavailable', state, affected: state === 'recovered' ? 0 : 2, updated_at: 1 }] }).connections?.[0]?.state).toBe(state)
    }
    for (const invalid of [
      base,
      { ...base, connections: [{ connection: 'primary', code: 'unknown', state: 'paused', affected: 1, updated_at: 1 }] },
      { ...base, connections: [{ connection: 'primary', code: 'tls', state: 'paused', affected: 1 }] },
    ]) expect(() => mapPreparation(invalid)).toThrow(ApiError)
  })

  it('preserves structured server error codes and rejects malformed DTOs', async () => {
    const client = new ReviewApiClient({
      csrf,
      fetch: vi.fn(async () => Response.json({ schema_version: 'review.v1', error: { code: 'busy' } }, { status: 503 })),
    })
    await expect(client.getLibrary({ lens: 'all' })).rejects.toMatchObject({ status: 503, code: 'busy' })
    expect(() => mapLibrary({ items: [], counts: {}, lens: '__proto__', schema_version: 'review.v1' })).toThrow(ApiError)
  })

})

describe('PageCsrfTokenStore', () => {
  it('accepts only bounded tokens from the renewed authenticated page', async () => {
    const store = new PageCsrfTokenStore(token, async () =>
      new Response(`<body data-csrf-token="${renewedToken}"></body>`, {
        status: 200,
        headers: { 'content-type': 'text/html' },
      }),
    )
    await expect(store.renew()).resolves.toBe(renewedToken)
    expect(store.current()).toBe(renewedToken)
  })
})
