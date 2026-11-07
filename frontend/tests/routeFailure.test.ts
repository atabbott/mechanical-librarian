import { describe, expect, it } from 'vitest'

import {
  ApiError,
  NetworkUnavailableError,
  ResponseDecodeError,
  SessionExpiredError,
} from '../src/api/http'
import { routeFailureCopy } from '../src/routeFailure'

const network =
  "Couldn't reach the server. Check that Mechanical Librarian is running, then try again."
const unreadable = "The server sent an answer this page couldn't read. Try again."
const server = "The server couldn't answer. Try again in a moment."
const refused = "This page isn't available."
const unexpected = "This page couldn't be shown. Try again."

const decodeFailure = new ResponseDecodeError({
  operation: 'GET /api/v1/metadata-policy/state',
  responseStatus: 200,
  at: '/secret_pointer',
  reason: 'unexpected_property',
})

// #1756: one row per reason a route can fail, each pinning the whole copy.
describe('routeFailureCopy', () => {
  it.each([
    {
      id: 'session expired',
      error: new SessionExpiredError(),
      copy: {
        message: 'Your session ended.',
        detail: '401 · session_expired',
        action: 'sign-in',
      },
    },
    {
      id: 'lost read',
      error: new NetworkUnavailableError(),
      copy: { message: network, detail: 'network_unavailable', action: 'retry' },
    },
    {
      id: 'undecodable answer',
      error: decodeFailure,
      copy: { message: unreadable, detail: '502 · invalid_response', action: 'retry' },
    },
    {
      id: 'answer that is not JSON',
      error: new ApiError(502, 'invalid_json_response'),
      copy: { message: unreadable, detail: '502 · invalid_json_response', action: 'retry' },
    },
    {
      id: 'unreadable error answer',
      error: new ApiError(502, 'invalid_error_response'),
      copy: { message: unreadable, detail: '502 · invalid_error_response', action: 'retry' },
    },
    {
      id: 'other 502',
      error: new ApiError(502, 'bad_gateway'),
      copy: { message: server, detail: '502 · bad_gateway', action: 'retry' },
    },
    {
      id: 'server unavailable',
      error: new ApiError(503, 'unavailable'),
      copy: { message: server, detail: '503 · unavailable', action: 'retry' },
    },
    {
      id: 'persistence failure',
      error: new ApiError(503, 'persistence'),
      copy: { message: server, detail: '503 · persistence', action: 'retry' },
    },
    {
      id: 'refused',
      error: new ApiError(404, 'not_found'),
      copy: { message: refused, detail: '404 · not_found', action: 'none' },
    },
    {
      id: 'render defect',
      error: new TypeError("Cannot read properties of undefined (reading 'fields')"),
      copy: { message: unexpected, detail: 'TypeError', action: 'retry' },
    },
    {
      id: 'thrown non-error',
      error: 'boom',
      copy: { message: unexpected, detail: null, action: 'retry' },
    },
  ])('$id', ({ error, copy }) => {
    expect(routeFailureCopy(error)).toEqual(copy)
  })

  it("never shows a decoder's pointer, reason or operation", () => {
    const text = JSON.stringify(routeFailureCopy(decodeFailure))
    expect(text).not.toContain('secret_pointer')
    expect(text).not.toContain('unexpected_property')
    expect(text).not.toContain('/api/v1')
    expect(text).not.toContain('Review API request failed')
  })
})
