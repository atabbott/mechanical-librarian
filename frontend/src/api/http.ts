import type { components } from '../generated/review-api'

type LibraryDto = components['schemas']['_LibraryResponse']
type CaseDetailDto = components['schemas']['_CaseDetailResponse']

export type LibraryLens = 'needs_attention' | 'all' | 'no_attention_needed'

export interface LibrarySearch {
  lens: LibraryLens
  q?: string
  cursor?: string
  preview?: string
}

export interface LibraryWorkView {
  id: string
  title: string
  subtitle: string
  attention: string
  dispositions: readonly string[]
}

export interface LibraryView {
  lens: LibraryLens
  query?: string
  nextCursor?: string
  counts: { attention: number; all: number; clear: number }
  works: readonly LibraryWorkView[]
}

export interface ReviewFieldView {
  destination: string
  disposition: string
  needsAttention: boolean
  currentValue: string
}

export interface CaseDetailView {
  id: string
  current: boolean
  title: string
  subtitle: string
  fields: readonly ReviewFieldView[]
}

export interface ReviewHttp {
  getLibrary(search: LibrarySearch, signal?: AbortSignal): Promise<LibraryView>
  getCaseDetail(reviewCaseId: string, signal?: AbortSignal): Promise<CaseDetailView>
  logout(signal?: AbortSignal): Promise<void>
}

export interface CsrfTokenStore {
  current(): string | undefined
  renew(signal?: AbortSignal): Promise<string>
}

export interface HttpClientOptions {
  csrf: CsrfTokenStore
  fetch?: typeof globalThis.fetch
  onSessionExpired?: () => void
}

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
  ) {
    super(`Review API request failed (${String(status)}: ${code})`)
    this.name = 'ApiError'
  }
}

export class SessionExpiredError extends ApiError {
  constructor() {
    super(401, 'session_expired')
    this.name = 'SessionExpiredError'
  }
}

const csrfPattern = /^[A-Za-z0-9_-]{32,128}$/

export class PageCsrfTokenStore implements CsrfTokenStore {
  private token?: string

  constructor(
    initialToken: string | undefined,
    private readonly fetchPage: (signal?: AbortSignal) => Promise<Response> = async (signal) =>
      fetch(window.location.href, {
        cache: 'no-store',
        credentials: 'same-origin',
        ...(signal ? { signal } : {}),
      }),
  ) {
    if (initialToken && csrfPattern.test(initialToken)) this.token = initialToken
  }

  current(): string | undefined {
    return this.token
  }

  async renew(signal?: AbortSignal): Promise<string> {
    const response = await this.fetchPage(signal)
    if (response.status === 401) throw new SessionExpiredError()
    if (!response.ok) throw new ApiError(response.status, 'csrf_renewal_failed')
    const page = new DOMParser().parseFromString(await response.text(), 'text/html')
    const token = page.body.dataset.csrfToken
    page.body.removeAttribute('data-csrf-token')
    if (!token || !csrfPattern.test(token)) throw new ApiError(502, 'invalid_csrf_renewal')
    this.token = token
    return token
  }
}

export class ReviewApiClient implements ReviewHttp {
  private readonly fetch: typeof globalThis.fetch
  private readonly onSessionExpired: () => void

  constructor(private readonly options: HttpClientOptions) {
    this.fetch = options.fetch ?? globalThis.fetch.bind(globalThis)
    this.onSessionExpired = options.onSessionExpired ?? (() => window.location.assign('/login'))
  }

  async getLibrary(search: LibrarySearch, signal?: AbortSignal): Promise<LibraryView> {
    const parameters = new URLSearchParams({ lens: search.lens })
    if (search.q) parameters.set('q', search.q)
    if (search.cursor) parameters.set('cursor', search.cursor)
    return mapLibrary(
      await this.request(`/api/v1/library?${parameters.toString()}`, signal ? { signal } : {}),
    )
  }

  async getCaseDetail(reviewCaseId: string, signal?: AbortSignal): Promise<CaseDetailView> {
    return mapCaseDetail(
      await this.request(
        `/api/v1/review-cases/${encodeURIComponent(reviewCaseId)}`,
        signal ? { signal } : {},
      ),
    )
  }

  async logout(signal?: AbortSignal): Promise<void> {
    await this.request('/api/v1/session/logout', { method: 'POST', ...(signal ? { signal } : {}) }, true)
  }

  private async request(path: string, init: RequestInit, csrfRetry = false): Promise<unknown> {
    const send = async (token?: string): Promise<Response> => {
      const headers = new Headers(init.headers)
      if (token) headers.set('x-csrf-token', token)
      return this.fetch(path, {
        ...init,
        headers,
        credentials: 'same-origin',
        cache: 'no-store',
      })
    }

    let response = await send(csrfRetry ? this.options.csrf.current() : undefined)
    if (csrfRetry && response.status === 403) {
      let renewed: string
      try {
        renewed = await this.options.csrf.renew(init.signal ?? undefined)
      } catch (error) {
        if (error instanceof SessionExpiredError) this.onSessionExpired()
        throw error
      }
      response = await send(renewed)
    }
    if (response.status === 401) {
      this.onSessionExpired()
      throw new SessionExpiredError()
    }
    if (!response.ok) throw await structuredError(response)
    if (response.status === 204) return undefined
    return response.json() as Promise<unknown>
  }
}

const record = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value)

const stringValue = (value: unknown, field: string): string => {
  if (typeof value !== 'string') throw new ApiError(502, `invalid_${field}`)
  return value
}

const integerValue = (value: unknown, field: string): number => {
  if (!Number.isSafeInteger(value) || (value as number) < 0) throw new ApiError(502, `invalid_${field}`)
  return value as number
}

const optionalString = (value: unknown, field: string): string | undefined => {
  if (value === null || value === undefined) return undefined
  return stringValue(value, field)
}

const stringArray = (value: unknown, field: string): readonly string[] => {
  if (!Array.isArray(value) || !value.every((item) => typeof item === 'string')) {
    throw new ApiError(502, `invalid_${field}`)
  }
  return value
}

const displayValue = (value: unknown): string => {
  if (value === null) return 'Not set'
  if (typeof value === 'string' || typeof value === 'boolean') return String(value)
  if (Array.isArray(value)) return value.map(displayValue).join(' · ')
  if (record(value) && typeof value.name === 'string') return value.name
  return 'Unavailable'
}

export const mapLibrary = (value: unknown): LibraryView => {
  if (!record(value) || !record(value.counts) || !Array.isArray(value.items)) {
    throw new ApiError(502, 'invalid_library_response')
  }
  const lens = stringValue(value.lens, 'library_lens')
  if (lens !== 'needs_attention' && lens !== 'all' && lens !== 'no_attention_needed') {
    throw new ApiError(502, 'invalid_library_lens')
  }
  stringValue(value.schema_version, 'schema_version')
  const dto = value as LibraryDto
  const query = optionalString(dto.query, 'library_query')
  const nextCursor = optionalString(dto.next_cursor, 'library_cursor')
  return {
    lens,
    ...(query ? { query } : {}),
    ...(nextCursor ? { nextCursor } : {}),
    counts: {
      attention: integerValue(dto.counts.needs_attention, 'attention_count'),
      all: integerValue(dto.counts.all, 'all_count'),
      clear: integerValue(dto.counts.no_attention_needed, 'clear_count'),
    },
    works: dto.items.map((item, index) => {
      if (!record(item)) throw new ApiError(502, `invalid_library_item_${String(index)}`)
      return {
        id: stringValue(item.review_case_id, 'review_case_id'),
        title: stringValue(item.title, 'title'),
        subtitle: `${stringValue(item.library, 'library')} · ${stringValue(item.item, 'item')}`,
        attention: stringValue(item.attention_state, 'attention_state'),
        dispositions: stringArray(item.dispositions, 'dispositions'),
      }
    }),
  }
}

export const mapCaseDetail = (value: unknown): CaseDetailView => {
  if (!record(value) || !record(value.target) || !Array.isArray(value.fields)) {
    throw new ApiError(502, 'invalid_case_detail_response')
  }
  stringValue(value.schema_version, 'schema_version')
  if (typeof value.current !== 'boolean') throw new ApiError(502, 'invalid_case_currentness')
  const dto = value as CaseDetailDto
  return {
    id: stringValue(dto.review_case_id, 'review_case_id'),
    current: dto.current,
    title: stringValue(dto.target.item, 'target_item'),
    subtitle: `${stringValue(dto.target.library, 'target_library')} · ${stringValue(dto.target.connection, 'target_connection')}`,
    fields: dto.fields.map((field, index) => {
      if (!record(field) || !record(field.current)) {
        throw new ApiError(502, `invalid_review_field_${String(index)}`)
      }
      return {
        destination: stringValue(field.destination, 'destination'),
        disposition: stringValue(field.deterministic_disposition, 'disposition'),
        needsAttention:
          typeof field.needs_attention === 'boolean'
            ? field.needs_attention
            : (() => {
                throw new ApiError(502, 'invalid_attention_state')
              })(),
        currentValue: displayValue(field.current.value),
      }
    }),
  }
}

const structuredError = async (response: Response): Promise<ApiError> => {
  let value: unknown
  try {
    value = await response.json()
  } catch {
    return new ApiError(response.status, 'invalid_error_response')
  }
  if (!record(value) || !record(value.error) || typeof value.error.code !== 'string') {
    return new ApiError(response.status, 'invalid_error_response')
  }
  return new ApiError(response.status, value.error.code)
}
