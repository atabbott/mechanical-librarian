import { describe, expect, it, vi } from 'vitest'

import {
  ApiError,
  PageCsrfTokenStore,
  ReviewApiClient,
  mapCaseDetail,
  SessionExpiredError,
  mapLibrary,
} from '../src/api/http'

const token = 'a'.repeat(32)
const renewedToken = 'b'.repeat(32)

const csrf = {
  current: vi.fn(() => token),
  renew: vi.fn(async () => renewedToken),
}

describe('ReviewApiClient', () => {
  it('preserves present and absent series sequences in the mapped review value', () => {
    const detail = (series: readonly Record<string, unknown>[]) => mapCaseDetail({
      schema_version: 'review-case-detail-v1', review_case_id: 'case-1', current: true,
      target: { item: 'A Book', library: 'Books', connection: 'primary' },
      fields: [{
        destination: 'series', deterministic_disposition: 'review', effect_capability: 'manual',
        needs_attention: true, manual_hold: false, current: { value: series },
        proposed_values: [], proposal_id: 'proposal-1', target_fingerprint: 'fingerprint-1',
        human_decision: null, automatic_decision: null, notices: [], provenance: [],
      }],
    })

    expect(detail([{ name: 'Archive', sequence: '2.5' }]).fields[0]?.currentValue).toBe('Archive · 2.5')
    expect(detail([{ name: 'Standalone' }]).fields[0]?.currentValue).toBe('Standalone')
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
          library: 'Books',
          item: 'item-1',
          attention_count: 1,
          attention_state: 'needs_attention',
          detail_url: '/review/case-1',
          dispositions: ['review'],
        }],
      }),
    )
    const client = new ReviewApiClient({ csrf, fetch: fetchMock, buildIdentity: 'c'.repeat(64) })

    const controller = new AbortController()
    const view = await client.getLibrary({ lens: 'all' }, controller.signal)

    expect(view.works[0]).toEqual({
      id: 'case-1',
      title: 'A Book',
      subtitle: 'Books · item-1',
      attention: 'needs_attention',
      dispositions: ['review'],
    })
    expect(fetchMock).toHaveBeenCalledWith('/api/v1/library?lens=all', expect.objectContaining({
      cache: 'no-store',
      credentials: 'same-origin',
      signal: controller.signal,
    }))
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
