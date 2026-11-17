// Shared Find this book builders for the dialog and link tests (#1477).
import { vi } from 'vitest'

import type { OwnerLinkCurrent, OwnerLinkView, OwnerSearchCard, ReviewHttp } from '../src/api/http'
import { createHttp, library } from './fixtures'

export const card = (overrides: Partial<OwnerSearchCard> = {}): OwnerSearchCard => ({
  position: 0,
  title: 'Dune',
  subtitle: null,
  creators: ['Frank Herbert'],
  narrators: [],
  year: 1965,
  publisher: 'Chilton',
  language: null,
  isbn: '9780441172719',
  asin: null,
  runtime_seconds: null,
  choosable: true,
  scopes: ['work', 'edition'],
  withheld: [],
  contradictions: [],
  preview: false,
  ...overrides,
})
export const linked = (overrides: Partial<OwnerLinkCurrent> = {}): OwnerLinkCurrent => ({
  link_row_id: `owner_link_${'a'.repeat(64)}`,
  scope: 'work',
  source: 'search',
  provider: 'hardcover',
  title: 'Dune',
  creators: ['Frank Herbert'],
  identifier: null,
  state: 'linked',
  reason: null,
  retry_at: null,
  paused: false,
  ...overrides,
})

// A double whose link view is `view` and whose Library row agrees with it, as
// the server's row and view do: the header shows its entry from the row and
// reads the view only to open the dialog (#1477).
export const withLinkView = (view: OwnerLinkView, more: Partial<ReviewHttp> = {}): ReviewHttp => ({
  ...createHttp(),
  getOwnerLinkView: vi.fn(async () => view),
  getLibrary: vi.fn(async () => ({
    ...library,
    works: library.works.map((work) => ({
      ...work,
      findOffered: view.offered,
      link:
        view.link === null
          ? null
          : { state: view.link.state, source: view.link.source, provider: view.link.provider },
    })),
  })),
  ...more,
})
