// The policy preview's projections: how each previewed field reads as an
// owner-facing impact. Moved out of the Policy route under the route-size rule
// (#1571). The preview and activation answers are decoded, and their named
// checks made, by the HTTP client (#1692).
import type { PolicyPreviewView } from './api/http'
import type { GroupedRow } from './components/RowGroups'
import { automaticRefusalReasons, coverNoteLabel, languageName } from './labels'
import { readableCategory } from './policyFields'

// A language value reads by its name, `English (en)` (#1286).
// A preview value as Activity states one: a list joined, a series as
// `Name (sequence)`, never as raw JSON (#1286).
const previewValue = (value: unknown, language = false): string => {
  if (value === null || value === undefined || value === '') return 'Not set'
  if (typeof value === 'string') return language ? languageName(value) : value
  if (typeof value === 'boolean') return value ? 'Yes' : 'No'
  if (typeof value === 'number') return value.toLocaleString()
  if (Array.isArray(value)) {
    if (!value.length) return 'Not set'
    if (value.every((item) => typeof item === 'string'))
      return value.map((item) => (language ? languageName(item) : item)).join(', ')
    const series = value.map((item: unknown) =>
      typeof item === 'object' &&
      item !== null &&
      'name' in item &&
      typeof item.name === 'string' &&
      (!('sequence' in item) || item.sequence === null || typeof item.sequence === 'string')
        ? 'sequence' in item && typeof item.sequence === 'string' && item.sequence
          ? `${item.name} (${item.sequence})`
          : item.name
        : null,
    )
    if (series.every((item) => item !== null)) return series.join(', ')
    return 'a value this preview cannot show'
  }
  if (
    typeof value === 'object' &&
    Object.keys(value).length === 2 &&
    'title' in value &&
    'subtitle' in value &&
    typeof value.title === 'string' &&
    typeof value.subtitle === 'string'
  )
    return `title ${value.title} and subtitle ${value.subtitle}`
  return 'a value this preview cannot show'
}

interface PolicyPreviewField {
  readonly current_value: unknown
  readonly desired_value: unknown
  readonly field: string
  readonly outcome: string
  readonly reason: string | null
}

/** What one policy does to a field: the cell's words, and what happens. */
export interface PolicyCell {
  readonly effect: 'write' | 'ask' | 'none' | 'unpreviewed'
  readonly value: string
  readonly note?: string | undefined
}

export interface PolicyImpactRow {
  readonly caseId: string
  readonly changed: boolean
}

const sameMetadataValue = (left: unknown, right: unknown) =>
  JSON.stringify(left) === JSON.stringify(right)
const hasMetadataValue = (value: unknown) => value !== null && value !== undefined && value !== ''
// A reason after "remains …" starts its own sentence (#1760).
const reasonSentence = (reason: string) => `${reason.charAt(0).toUpperCase()}${reason.slice(1)}.`
const humanReason = (reason: string | null) => {
  if (reason === 'ambiguous_evidence') return 'the sources do not agree on one value'
  if (reason === 'policy_off') return 'tag handling is off'
  if (reason === 'explicit_unsupported')
    return 'Not supported leaves the explicit-content flag unchanged'
  if (reason === 'source_value_absent') return 'no source supplied an ISBN'
  if (reason === 'source_unavailable') return 'the sources could not be read'
  if (reason === 'source_value_malformed_or_incomplete')
    return 'the sources give an unreadable or incomplete ISBN'
  if (reason === 'source_values_conflict') return 'the sources give different ISBNs'
  if (reason === 'source_value_insufficient_association')
    return 'the sources do not tie this ISBN closely enough to this recording'
  if (reason === 'verified_manifestation_isbn') return 'it is verified for this edition'
  if (reason === 'target_work_contradicted')
    return 'the sources suggest this value belongs to a different work'
  if (reason === 'target_preserved' || reason === 'unchanged') return 'the current value is kept'
  return 'no supported value found'
}

// The group and the cell already say nothing changes; the note says why.
const ignored = 'Set to Ignore.'
const coverUnpreviewed: Readonly<Record<string, string>> = {
  cover_not_compared: "No scan has compared this work's covers yet.",
  provider_fetching: 'Sources are still being fetched.',
  cover_compared_next_scan: 'The next scan compares the covers again.',
}

// The server keeps its closed outcome and reason codes for audit detail. This
// projection deliberately turns them into owner-facing impact language.
// Under Ask me nothing is written; the value is what the review would propose.
export const policyCell = (
  field: PolicyPreviewField,
  destination: string,
  audiobook = false,
): PolicyCell => {
  if (field.outcome === 'not_previewed')
    return {
      effect: 'unpreviewed',
      value: 'Not previewed.',
      note: coverUnpreviewed[field.reason ?? ''] ?? coverUnpreviewed.cover_compared_next_scan,
    }
  // A cover's value is words the application built from the stored comparison.
  if (field.field === 'cover_order')
    return {
      effect:
        field.outcome === 'projected' ? 'write' : field.outcome === 'suggested' ? 'ask' : 'none',
      value: typeof field.desired_value === 'string' ? field.desired_value : 'No change',
      note:
        field.reason === 'policy_ignored'
          ? ignored
          : field.reason
            ? coverNoteLabel(field.reason, audiobook)
            : undefined,
    }
  const refusal =
    automaticRefusalReasons[field.reason ?? ''] ??
    'No value would be chosen for you, and no more specific reason is known.'
  const language = field.field === 'language_rendering'
  // The sentence that says the field is left to the owner sits under the value
  // it leaves, and stands alone only where no value is proposed.
  if (field.reason === 'automatic_unqualified')
    return hasMetadataValue(field.desired_value)
      ? { effect: 'ask', value: previewValue(field.desired_value, language), note: refusal }
      : { effect: 'ask', value: refusal }
  if (
    (field.outcome === 'projected' || field.outcome === 'suggested') &&
    !sameMetadataValue(field.current_value, field.desired_value) &&
    (hasMetadataValue(field.current_value) || hasMetadataValue(field.desired_value))
  ) {
    const ask = field.outcome === 'suggested' || destination === 'ask'
    return {
      effect: ask ? 'ask' : 'write',
      value: previewValue(field.desired_value, language),
      note: ask
        ? 'Left to you'
        : field.reason === 'verified_manifestation_isbn'
          ? reasonSentence(humanReason(field.reason))
          : undefined,
    }
  }
  if (field.reason === 'target_state_unusable')
    return {
      effect: 'ask',
      value: 'Current value is unavailable or unreadable. Inspect the source before deciding.',
    }
  if (field.outcome === 'abstain' && field.reason !== 'policy_ignored')
    return field.reason === 'ambiguous_evidence'
      ? { effect: 'ask', value: refusal }
      : { effect: 'none', value: 'No change', note: refusal }
  return {
    effect: 'none',
    value: 'No change',
    // With no specific reason, the cell ends at its value (#1760).
    note:
      (field.outcome === 'no_op' || sameMetadataValue(field.current_value, field.desired_value)) &&
      (!field.reason || field.reason === 'target_preserved' || field.reason === 'unchanged')
        ? undefined
        : field.reason === 'policy_ignored'
          ? ignored
          : reasonSentence(humanReason(field.reason)),
  }
}

/** What happens under the draft, in the order the groups are listed. */
export const policyGroups = [
  'Filled',
  'Changed',
  'Left to you',
  'No change',
  'Not previewed',
] as const

export interface PolicyPreviewRow extends PolicyImpactRow, GroupedRow {
  readonly id: string
  readonly library: string
  readonly media: 'ebook' | 'audiobook'
  readonly field: string
  readonly group: (typeof policyGroups)[number]
  /** The draft's result on one line, for the unchanged outcomes. */
  readonly summary: string
}

// One policy field governs these library fields, so a row is named for its own.
const namedByDestination = new Set(['title_layout', 'contributor_names', 'date_precision'])

/** Every compared field of every sampled work, as one row of three values. */
export const policyPreviewRows = (
  libraries: PolicyPreviewView['libraries'],
): readonly PolicyPreviewRow[] =>
  libraries.flatMap((library) =>
    library.cases.flatMap((item, caseIndex) => {
      const comparison = library.comparisons[caseIndex]
      if (!comparison) return []
      const audiobook = comparison.media_kind === 'audiobook'
      return comparison.fields.map((field) => {
        const active = policyCell(field.active, field.active_destination, audiobook)
        const draft = policyCell(field.draft, field.draft_destination, audiobook)
        // A sentence that is the same under both policies is said once.
        const shared = active.note !== undefined && active.note === draft.note
        const current = field.draft.current_value
        return {
          caseId: item.report_id,
          id: `${item.report_id}:${field.destination}`,
          library: library.library,
          media: comparison.media_kind,
          field: readableCategory(
            namedByDestination.has(field.draft.field) ? field.destination : field.draft.field,
          ),
          changed: field.changed,
          group:
            draft.effect === 'unpreviewed'
              ? 'Not previewed'
              : draft.effect === 'write'
                ? hasMetadataValue(current)
                  ? 'Changed'
                  : 'Filled'
                : draft.effect === 'ask'
                  ? 'Left to you'
                  : 'No change',
          summary: draft.note
            ? `${draft.value.replace(/[^.]$/u, '$&.')} ${draft.note}`
            : draft.value,
          work: item.work_label,
          aside: audiobook ? 'Audiobook' : 'Ebook',
          // A row that was not previewed is its work and the reason alone.
          values:
            draft.effect === 'unpreviewed'
              ? []
              : [
                  {
                    label: 'Current',
                    value: previewValue(current, field.draft.field === 'language_rendering'),
                  },
                  {
                    label: 'Active policy',
                    value: active.value,
                    note: shared ? undefined : active.note,
                  },
                  { label: 'Draft', value: draft.value, note: shared ? undefined : draft.note },
                ],
          why: draft.effect === 'unpreviewed' ? draft.note : shared ? active.note : undefined,
        }
      })
    }),
  )

export const policyImpactTotals = (rows: readonly PolicyImpactRow[]) => {
  const changes = rows.filter((row) => row.changed)
  return {
    changedWorks: new Set(changes.map((row) => row.caseId)).size,
    materialChanges: changes.length,
  }
}
