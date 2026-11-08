// The policy preview's projections: how each previewed field reads as an
// owner-facing impact. Moved out of the Policy route under the route-size rule
// (#1571). The preview and activation answers are decoded, and their named
// checks made, by the HTTP client (#1692).
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
    return `title ${value.title} and subtitle ${value.subtitle}`
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
    | 'Needs you'
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
// A reason after "remains …" starts its own sentence (#1760).
const reasonSentence = (reason: string) => `${reason.charAt(0).toUpperCase()}${reason.slice(1)}.`
const humanReason = (reason: string | null) => {
  if (reason === 'ambiguous_evidence') return 'the sources do not agree on one value'
  if (reason === 'policy_ignored') return 'Ignore leaves this field unchanged'
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
          : `${proposal ? 'would propose changing' : 'changes'} ${current} to ${previewValue(field.desired_value, language)}`
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
      title: 'Needs you',
      detail: 'Current value is unavailable or unreadable. Inspect the source before deciding.',
      technicalDetail,
    }
  if (field.outcome === 'abstain')
    return {
      changed: false,
      title: field.reason === 'ambiguous_evidence' ? 'Needs you' : 'No change',
      detail:
        field.reason === 'policy_ignored'
          ? `remains ${current}. ${reasonSentence(humanReason(field.reason))}`
          : (automaticRefusalReasons[field.reason ?? ''] ??
            'No value would be chosen for you, and no more specific reason is known.'),
      technicalDetail,
    }
  if (field.outcome === 'no_op' || sameMetadataValue(field.current_value, field.desired_value))
    return {
      changed: false,
      title: 'No change',
      // With no specific reason, the line ends at the value (#1760).
      detail:
        !field.reason || field.reason === 'target_preserved' || field.reason === 'unchanged'
          ? `remains ${current}.`
          : `remains ${current}. ${reasonSentence(humanReason(field.reason))}`,
      technicalDetail,
    }
  if (!currentIsDisplayable && !desiredIsDisplayable)
    return {
      changed: false,
      title: 'No change',
      detail: 'remains empty. No supported value found.',
      technicalDetail,
    }
  return {
    changed: false,
    title: 'No supported value found',
    detail: `${current} remains. ${reasonSentence(humanReason(field.reason))}`,
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
