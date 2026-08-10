import { describe, expect, it, vi } from 'vitest'

import {
  ApiError,
  PageCsrfTokenStore,
  ReviewApiClient,
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
    const client = new ReviewApiClient({ csrf, fetch: fetchMock })

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

  it('renews CSRF once before retrying an unsafe request', async () => {
    const fetchMock = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(Response.json({ error: { code: 'csrf' } }, { status: 403 }))
      .mockResolvedValueOnce(new Response(null, { status: 204 }))
    const client = new ReviewApiClient({ csrf, fetch: fetchMock })

    await client.logout()

    expect(csrf.renew).toHaveBeenCalledOnce()
    expect(new Headers(fetchMock.mock.calls[1]?.[1]?.headers).get('x-csrf-token')).toBe(renewedToken)
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
      fetch: vi.fn(async () => Response.json({ error: { code: 'csrf' } }, { status: 403 })),
      onSessionExpired: expired,
    })

    await expect(client.logout()).rejects.toBeInstanceOf(SessionExpiredError)
    expect(expired).toHaveBeenCalledOnce()
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
