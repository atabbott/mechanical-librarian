import { describe, expect, it } from 'vitest'

import {
  ApiError,
  NetworkUnavailableError,
  ResponseDecodeError,
  SessionExpiredError,
} from '../src/api/http'
import { routeFailureCopy } from '../src/routeFailure'

const network =
  'The server could not be reached. Check that Mechanical Librarian is running and this device is connected, then try again.'
const unreadable =
  'The server sent an answer this page could not read safely, so nothing from it is shown. Try again; if it keeps happening, reload the page.'
const server =
  'The server could not answer just now. Try again in a moment; if it keeps happening, check that the Mechanical Librarian service is running normally.'
const refused =
  'The server would not show this page. Try again; if it keeps happening, reload the page.'
const unexpected =
  'Something went wrong while showing this page. Try again; if it keeps happening, reload the page.'

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
        message: 'Your session has ended. Sign in again to continue.',
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
      copy: { message: refused, detail: '404 · not_found', action: 'retry' },
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
