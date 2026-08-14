import type { EvidenceView } from '../api/http'

const labels: Readonly<Record<string, string>> = {
  hardcover: 'Hardcover',
  open_library: 'Open Library',
  'open-library': 'Open Library',
  google_books: 'Google Books',
}

export const creatorCorroborationLabels = (
  notices: readonly string[],
  provenance: readonly string[],
  evidence: readonly EvidenceView['items'][number][],
): readonly string[] => {
  if (!notices.includes('explicit_author_corroborates_untyped_creator')) return []
  return provenance.flatMap((reference) => {
    const provider = evidence.find((item) => item.reference === reference)?.provider
    return provider !== null && provider !== undefined && labels[provider]
      ? [labels[provider]]
      : []
  })
}
