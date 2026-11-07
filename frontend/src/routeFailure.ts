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
  // A refusal is not fixed by trying again, so it offers no action.
  action: 'retry' | 'sign-in' | 'none'
}

const copy = {
  session: 'Your session ended.',
  network: "Couldn't reach the server. Check that Mechanical Librarian is running, then try again.",
  unreadable: "The server sent an answer this page couldn't read. Try again.",
  server: "The server couldn't answer. Try again in a moment.",
  refused: "This page isn't available.",
  unexpected: "This page couldn't be shown. Try again.",
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
    return error.status >= 500
      ? { message: copy.server, detail: apiDetail(error), action: 'retry' }
      : { message: copy.refused, detail: apiDetail(error), action: 'none' }
  return {
    message: copy.unexpected,
    detail: error instanceof Error ? error.name : null,
    action: 'retry',
  }
}
