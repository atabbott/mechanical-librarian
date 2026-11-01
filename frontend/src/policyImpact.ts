// The policy preview's projections: the payload checks and how each previewed
// field reads as an owner-facing impact. Moved out of the Policy route under
// the route-size rule (#1571).
import type { PolicyActivationView, PolicyPreviewView, PolicyTransitionView } from './api/http'
import { automaticRefusalReasons, languageName } from './labels'

// A language value reads by its name, `English (en)` (#1286).
// A preview value as Activity states one: a list joined, a series as
// `Name (sequence)`, never as raw JSON (#1286).
const previewValue = (value: unknown, language = false): string => {
  if (value === null || value === undefined || value === '') return 'empty'
  if (typeof value === 'string') return language ? languageName(value) : value
  if (typeof value === 'boolean') return value ? 'Yes' : 'No'
  if (typeof value === 'number') return value.toLocaleString()
  if (Array.isArray(value)) {
    if (!value.length) return 'empty'
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
    return `Title: ${value.title}; Subtitle: ${value.subtitle}`
  return 'a value this preview cannot show'
}

interface PolicyPreviewField {
  readonly current_value: unknown
  readonly desired_value: unknown
  readonly field: string
  readonly outcome: string
  readonly reason: string | null
  readonly target_state: string
}

export interface PolicyImpactDisplay {
  readonly changed: boolean
  readonly detail: string
  readonly technicalDetail: string
  readonly title:
    | 'Change'
    | 'Needs review'
    | 'No change'
    | 'No supported value found'
    | 'Policy change'
    | 'No policy change'
}

export interface PolicyImpactRow {
  readonly caseId: string
  readonly impact: PolicyImpactDisplay
}

const sameMetadataValue = (left: unknown, right: unknown) =>
  JSON.stringify(left) === JSON.stringify(right)
const hasMetadataValue = (value: unknown) => value !== null && value !== undefined && value !== ''
const humanReason = (reason: string | null) => {
  if (reason === 'ambiguous_evidence') return 'evidence is ambiguous'
  if (reason === 'policy_ignored' || reason === 'policy_off')
    return 'this policy leaves the field unchanged'
  if (reason === 'explicit_unsupported')
    return 'this policy leaves the explicit-content flag unchanged'
  if (reason === 'source_value_absent') return 'the source supplied no ISBN'
  if (reason === 'source_unavailable') return 'the ISBN source could not be reached'
  if (reason === 'source_value_malformed_or_incomplete')
    return 'source ISBN data is malformed or incomplete'
  if (reason === 'source_values_conflict') return 'sources supply competing ISBNs'
  if (reason === 'source_value_insufficient_association')
    return 'the supplied ISBN is not sufficiently linked to this recording'
  if (reason === 'verified_manifestation_isbn')
    return 'deterministic identity evidence binds it to this file’s work'
  if (reason === 'target_work_contradicted')
    return 'the retained provider evidence names a different work'
  if (reason === 'target_preserved' || reason === 'unchanged')
    return 'the exact current value is preserved'
  return 'no supported value found'
}

// The server keeps its closed outcome and reason codes for audit detail. This
// projection deliberately turns them into owner-facing impact language.
// Under Ask me nothing is written; the change is what the review would propose.
export const policyImpactDisplay = (
  field: PolicyPreviewField,
  proposal = false,
): PolicyImpactDisplay => {
  const language = field.field === 'language_rendering'
  const current = previewValue(field.current_value, language)
  const currentIsDisplayable = hasMetadataValue(field.current_value)
  const desiredIsDisplayable = hasMetadataValue(field.desired_value)
  const materialChange =
    field.outcome === 'projected' &&
    !sameMetadataValue(field.current_value, field.desired_value) &&
    (currentIsDisplayable || desiredIsDisplayable)
  const technicalDetail = `outcome: ${field.outcome}; reason: ${field.reason ?? 'none'}; target state: ${field.target_state}`
  if (materialChange) {
    const detail =
      !currentIsDisplayable && desiredIsDisplayable
        ? `${proposal ? 'would propose filling' : 'fills'} the empty field with ${previewValue(field.desired_value, language)}`
        : currentIsDisplayable && !desiredIsDisplayable
          ? `${proposal ? 'would propose clearing' : 'clears'} ${current}`
          : `${proposal ? 'would propose ' : ''}${current} → ${previewValue(field.desired_value, language)}`
    return {
      changed: true,
      title: 'Change',
      detail:
        field.reason === 'verified_manifestation_isbn'
          ? `${detail} because ${humanReason(field.reason)}`
          : detail,
      technicalDetail,
    }
  }
  if (field.reason === 'target_state_unusable')
    return {
      changed: false,
      title: 'Needs review',
      detail: 'Current value is unavailable or unreadable — inspect the source before deciding',
      technicalDetail,
    }
  if (field.outcome === 'abstain')
    return {
      changed: false,
      title: field.reason === 'ambiguous_evidence' ? 'Needs review' : 'No change',
      detail:
        field.reason === 'policy_ignored'
          ? `remains ${current} — ${humanReason(field.reason)}`
          : (automaticRefusalReasons[field.reason ?? ''] ??
            'The preview did not select an automatic action; no more specific reason is available.'),
      technicalDetail,
    }
  if (field.outcome === 'no_op' || sameMetadataValue(field.current_value, field.desired_value))
    return {
      changed: false,
      title: 'No change',
      detail: `remains ${current} — ${field.reason ? humanReason(field.reason) : 'the exact current value is preserved'}`,
      technicalDetail,
    }
  if (!currentIsDisplayable && !desiredIsDisplayable)
    return {
      changed: false,
      title: 'No change',
      detail: 'remains empty — no supported value found',
      technicalDetail,
    }
  return {
    changed: false,
    title: 'No supported value found',
    detail: `${current} remains — ${humanReason(field.reason)}`,
    technicalDetail,
  }
}
export const policyImpactTotals = (rows: readonly PolicyImpactRow[]) => {
  const changes = rows.filter((row) => row.impact.changed)
  return {
    changedWorks: new Set(changes.map((row) => row.caseId)).size,
    materialChanges: changes.length,
  }
}
const previewFieldShape = (value: unknown): value is PolicyPreviewField => {
  if (typeof value !== 'object' || value === null) return false
  return (
    'field' in value &&
    typeof value.field === 'string' &&
    'outcome' in value &&
    typeof value.outcome === 'string' &&
    'reason' in value &&
    (value.reason === null || typeof value.reason === 'string') &&
    'target_state' in value &&
    typeof value.target_state === 'string' &&
    'current_value' in value &&
    'desired_value' in value
  )
}
export const isPolicyPreview = (value: unknown): value is PolicyPreviewView =>
  typeof value === 'object' &&
  value !== null &&
  'schema_version' in value &&
  value.schema_version === 'metadata-policy-preview-v1' &&
  'active_policy_id' in value &&
  typeof value.active_policy_id === 'string' &&
  'active_generation' in value &&
  typeof value.active_generation === 'number' &&
  Number.isSafeInteger(value.active_generation) &&
  value.active_generation > 0 &&
  'libraries' in value &&
  Array.isArray(value.libraries) &&
  value.libraries.every((library: unknown) => {
    if (
      typeof library !== 'object' ||
      library === null ||
      !('cases' in library) ||
      !Array.isArray(library.cases) ||
      !('comparisons' in library) ||
      !Array.isArray(library.comparisons) ||
      library.comparisons.length !== library.cases.length
    )
      return false
    return library.comparisons.every(
      (comparison: unknown) =>
        typeof comparison === 'object' &&
        comparison !== null &&
        'media_kind' in comparison &&
        ['ebook', 'audiobook'].includes(String(comparison.media_kind)) &&
        'fields' in comparison &&
        Array.isArray(comparison.fields) &&
        comparison.fields.every(
          (field: unknown) =>
            typeof field === 'object' &&
            field !== null &&
            'active' in field &&
            previewFieldShape(field.active) &&
            'draft' in field &&
            previewFieldShape(field.draft) &&
            field.active.field === field.draft.field &&
            'changed' in field &&
            typeof field.changed === 'boolean' &&
            'active_destination' in field &&
            ['ask', 'automatic', 'ignore', 'unsupported'].includes(
              String(field.active_destination),
            ) &&
            'draft_destination' in field &&
            ['ask', 'automatic', 'ignore', 'unsupported'].includes(String(field.draft_destination)),
        ),
    )
  })
const isPolicyTransition = (value: unknown): value is PolicyTransitionView =>
  typeof value === 'object' &&
  value !== null &&
  'schema_version' in value &&
  value.schema_version === 'metadata-policy-transition-v1' &&
  'transition_id' in value &&
  typeof value.transition_id === 'string' &&
  'from_policy_id' in value &&
  typeof value.from_policy_id === 'string' &&
  'to_policy_id' in value &&
  typeof value.to_policy_id === 'string' &&
  'state' in value &&
  typeof value.state === 'string' &&
  'generation' in value &&
  typeof value.generation === 'number' &&
  'completed' in value &&
  typeof value.completed === 'number' &&
  'failed' in value &&
  typeof value.failed === 'number' &&
  'total' in value &&
  typeof value.total === 'number' &&
  'acknowledged' in value &&
  typeof value.acknowledged === 'boolean'
export const isPolicyActivation = (
  value: unknown,
  policyId: string,
): value is PolicyActivationView =>
  typeof value === 'object' &&
  value !== null &&
  'policy_id' in value &&
  value.policy_id === policyId &&
  'schema_version' in value &&
  value.schema_version === 'metadata-policy-activation-v1' &&
  'transition' in value &&
  (value.transition === null || isPolicyTransition(value.transition))
