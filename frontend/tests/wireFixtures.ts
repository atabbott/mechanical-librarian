// Base wire builders shared by the mapper tests and the fixture decode test,
// which proves each one is a complete server response (#1576).

// `GET /api/v1/library/report` 200.
export const reportDetail = (overrides: Record<string, unknown> = {}) => ({
  chapters: [],
  chapters_truncated: false,
  connection: 'primary',
  embedded_cover: { present: false, surface: null },
  evidence: [],
  evidence_truncated: false,
  item: 'item-9',
  library: 'audio',
  library_display_name: 'Audiobooks',
  media_health: [
    {
      bound: 'The declared track sequence runs without a hole.',
      cited: ['track 1 declares sequence 1', 'track 2 declares sequence 3'],
      expected: null,
      kind: 'track_sequence_gap',
      observed: null,
      tracks: [1, 2],
    },
  ],
  media_kind: 'm4b',
  provider_sources: [],
  provider_evidence: [],
  provider_evidence_truncated: false,
  report_id: `report_${'a'.repeat(64)}`,
  schema_version: 'review-report-detail-v5',
  audiobook_review_scope: null,
  recording_identity_basis: null,
  title: 'Recorded work',
  ...overrides,
})
