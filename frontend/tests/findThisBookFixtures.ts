// Shared Find this book builders for the dialog and link tests (#1477).
import type { OwnerLinkCurrent, OwnerSearchCard } from '../src/api/http'

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
