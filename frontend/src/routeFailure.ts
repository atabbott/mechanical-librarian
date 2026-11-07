import {
  ApiError,
  NetworkUnavailableError,
  ResponseDecodeError,
  SessionExpiredError,
} from './api/http'

// What a page that could not load tells the owner (#1756). The message says
// in plain words what happened and what to do; a status and code are kept for
// "Technical detail" only. A decoder's pointer and reason, and a JavaScript
// message, never reach the page (ADR-0091 rule 1).
export interface RouteFailureCopy {
  message: string
  detail: string | null
  action: 'retry' | 'sign-in'
}

const copy = {
  session: 'Your session has ended. Sign in again to continue.',
  network:
    'The server could not be reached. Check that Mechanical Librarian is running and this device is connected, then try again.',
  unreadable:
    'The server sent an answer this page could not read safely, so nothing from it is shown. Try again; if it keeps happening, reload the page.',
  server:
    'The server could not answer just now. Try again in a moment; if it keeps happening, check that the Mechanical Librarian service is running normally.',
  refused:
    'The server would not show this page. Try again; if it keeps happening, reload the page.',
  unexpected:
    'Something went wrong while showing this page. Try again; if it keeps happening, reload the page.',
} as const

const apiDetail = (error: ApiError) => `${String(error.status)} · ${error.code}`

export const routeFailureCopy = (error: unknown): RouteFailureCopy => {
  if (error instanceof SessionExpiredError)
    return { message: copy.session, detail: apiDetail(error), action: 'sign-in' }
  if (error instanceof NetworkUnavailableError)
    return { message: copy.network, detail: 'network_unavailable', action: 'retry' }
  if (
    error instanceof ResponseDecodeError ||
    (error instanceof ApiError && error.status === 502 && error.code.startsWith('invalid_'))
  )
    return { message: copy.unreadable, detail: apiDetail(error), action: 'retry' }
  if (error instanceof ApiError)
    return {
      message: error.status >= 500 ? copy.server : copy.refused,
      detail: apiDetail(error),
      action: 'retry',
    }
  return {
    message: copy.unexpected,
    detail: error instanceof Error ? error.name : null,
    action: 'retry',
  }
}
