import type { EvidenceRole } from './api/http'

const ownerFieldLabels: Record<string, string> = {
  isbn_creator_supported_title: 'ISBN and author evidence support this title',
  isbn_title_material_conflict:
    'The sources disagree about the work or title; choose the title yourself',
  publishedDate: 'Publication date',
  provider_consensus_title_bracket_source_tag:
    'Independent providers agree after removing the bracketed source tag',
  provider_consensus_title_explicit_identifier:
    'Independent providers agree after removing the labelled ISBN',
  provider_consensus_title_parenthesized_bare_number:
    'Independent providers agree after removing the parenthesized number',
  provider_consensus_title_parenthesized_series_name:
    'Independent providers agree after preserving the structured series name',
  provider_consensus_title_parenthesized_year:
    'Independent providers agree after removing the parenthesized year',
  provider_consensus_title_subtitle_segment:
    'Independent providers agree after preserving the colon subtitle',
  provider_consensus_title_colon_unused:
    'The colon subtitle is local corroboration only; choose the title yourself',
  provider_consensus_title_series_unpreserved:
    'The parenthesized series name is not preserved as structured series evidence',
  decoration_equivalent_bracket_source_tag:
    'Providers agree once the bracketed source tag is removed; the current title is kept',
  decoration_equivalent_explicit_identifier:
    'Providers agree once the labelled ISBN is removed; the current title is kept',
  decoration_equivalent_parenthesized_bare_number:
    'Providers agree once the parenthesized number is removed; the current title is kept',
  decoration_equivalent_parenthesized_series_name:
    'Providers agree once the parenthesized series name is removed; the current title is kept',
  decoration_equivalent_parenthesized_year:
    'Providers agree once the parenthesized year is removed; the current title is kept',
  decoration_equivalent_subtitle_segment:
    'Providers agree once the colon subtitle is removed; the current title is kept',
  decoration_tidy_bracket_source_tag:
    'Providers agree once the bracketed source tag is removed; the current title is tidied to the plain form',
  decoration_tidy_explicit_identifier:
    'Providers agree once the labelled ISBN is removed; the current title is tidied to the plain form',
  decoration_tidy_parenthesized_bare_number:
    'Providers agree once the parenthesized number is removed; the current title is tidied to the plain form',
  decoration_tidy_parenthesized_series_name:
    'Providers agree once the parenthesized series name is removed after preserving the structured series name; the current title is tidied to the plain form',
  decoration_tidy_parenthesized_year:
    'Providers agree once the parenthesized year is removed; the current title is tidied to the plain form',
  decoration_tidy_subtitle_segment:
    'Providers agree once the colon subtitle is removed; the current title is tidied to the plain form',
  decoration_tidy_series_unpreserved:
    'The parenthesized series name is not preserved as structured series evidence; choose the title yourself',
  decoration_equivalence_provider_dissent:
    'Another provider states a different work title; choose the title yourself',
  decoration_equivalence_decorated_consensus:
    'More providers state the decorated title than the plain one; choose the title yourself',
  subtitle_segment_uncorroborated:
    'A colon segment in a title matches no independent subtitle evidence',
  subtitle_segment_provider_dissent:
    'Another provider states a different subtitle; choose the subtitle yourself',
  subtitle_segment_decorated_consensus:
    'The colon segment reads as title text: no provider carries it, or no more providers state the plain title than carry the decorated form',
  provider_consensus_contributor_display:
    'Independent providers agree on the contributor display form',
  target_work_contradicted: 'The retained provider evidence names a different work',
}

// Sentence case is the one convention for every server-named field, enum,
// destination and status the owner reads: underscores become spaces and only
// the first character is capitalised, so `media_kind` is `Media kind` on every
// surface. Three of the four route formatters this replaces already used it;
// title case made a server enum look like a proper noun, and leaving the first
// letter alone made a heading look unfinished.
export const fieldLabel = (value: string) =>
  ownerFieldLabels[value] ??
  `${labelWords(value).slice(0, 1).toUpperCase()}${labelWords(value).slice(1)}`
// The Activity ledger's own names for the write destinations whose generic
// label differs, so the write card and the rows it writes name one field one
// way: `3 ISBN changes`, not `3 isbn changes` (#1281).
const writeFieldLabels: Record<string, string> = {
  asin: 'ASIN',
  isbn: 'ISBN',
  publishedDate: 'Published date',
  publishedYear: 'Published year',
}
export const writeFieldLabel = (destination: string) =>
  writeFieldLabels[destination] ?? fieldLabel(destination)
// The same words without the capital, for a label set inside a sentence
// (`Choose a media kind value`) rather than standing on its own.
export const labelWords = (value: string) => value.replaceAll('_', ' ')
// One vocabulary for every preparation count, in the masthead and the run line
// alike (#1366); a count not yet known reads as an ellipsis, never 0. Only the
// ready count stands at zero, so a quiet run is not a wall of zeros (#641).
// Each phrase keeps ordinary spaces and is rendered by CountPhrases, which keeps
// the number with its first word whenever the two fit on a line (#1013, #1366).
export const preparationOutcomes = (
  counts: {
    readonly ready: number
    readonly recorded: number
    readonly not_matched: number
    readonly failed: number
  } | null,
) =>
  [
    [counts?.ready, 'ready to browse'] as const,
    [counts?.recorded, 'recorded, nothing to decide'] as const,
    [counts?.not_matched, 'not matched'] as const,
    [counts?.failed, 'could not finish'] as const,
  ]
    .filter(([count], index) => index === 0 || count !== 0)
    .map(([count, words]) => `${count === undefined ? '…' : String(count)} ${words}`)
// Server identifiers with no meaning for a reader; never shown as a label.
export const opaqueSource = /^(?:local|evidence|provider)_[0-9a-f]+$/iu

export const automaticRefusalReasons: Readonly<Record<string, string>> = {
  no_candidate: 'No eligible value was found in the retained evidence.',
  ambiguous_evidence: 'The retained evidence does not select one value for automatic handling.',
  target_work_contradicted: 'The retained evidence contradicts applicability to this work.',
  target_work_applicability_unproved:
    'The retained evidence does not establish that this value describes this work.',
  target_state_unusable:
    'The current field value cannot be used safely for this automatic operation.',
  automatic_unqualified: 'This policy option is not qualified for automatic handling.',
  manifestation_verification_required:
    'The retained evidence does not verify the ISBN for this edition.',
  description_quality_floor_not_met:
    'Retained source text does not meet the automatic synopsis rules.',
  series_membership_incomplete:
    'Series membership or position evidence is incomplete or malformed.',
  series_conflicting_membership:
    'The retained evidence does not resolve series membership for automatic handling.',
  series_provider_evidence_required: 'Provider evidence supporting this series value is required.',
  subtitle_local_corroboration_required: 'Independent evidence does not corroborate this subtitle.',
  subtitle_segment_provider_dissent:
    'Provider subtitle evidence disagrees with the proposed title split.',
  subtitle_segment_decorated_consensus:
    'The retained title evidence does not support separating this text into a subtitle.',
  atomic_bundle_unavailable: 'The required linked field selection is unavailable.',
  corroborated_split_unavailable:
    'The corroborated title and subtitle do not match the current combined title.',
  option_evidence_unavailable: 'Evidence required by the selected representation is unavailable.',
  policy_option_unavailable: 'The selected policy option is unavailable for this projection.',
  target_projection_oversized: 'The resulting field would exceed the supported size limit.',
  policy_off: 'Tag handling is off.',
  explicit_unsupported: 'Automatic explicit-content handling is unavailable for this option.',
  source_value_malformed_or_incomplete: 'ISBN evidence is malformed or incomplete.',
  source_values_conflict: 'Conflicting ISBN values remain.',
  source_value_insufficient_association: 'Association with this recording is insufficient.',
  source_unavailable: 'Source evidence is unavailable.',
  source_value_absent: 'No ISBN value was supplied.',
  series_provider_failure_isbn: 'A contributing ISBN lookup did not supply usable evidence.',
  series_provider_failure_work: 'A contributing work lookup did not supply usable evidence.',
  series_provider_failure_asin: 'A contributing ASIN lookup did not supply usable evidence.',
}

export const evidenceRoleLabel = (role: EvidenceRole) =>
  ({
    local_presence: 'Local presence',
    external_corroboration: 'External corroboration',
    external_observation: 'External observation',
  })[role]

const coverReviewerNames: Record<string, string> = {
  'openai/gpt-6-luna': 'GPT-6 Luna',
  'xiaomi/mimo-v2.6-flash': 'MiMo 2.6 Flash',
  'qwen/qwen3.8-flash': 'Qwen 3.8 Flash',
  'mistralai/ministral-3b-2512': 'Ministral 3B',
  'openai/gpt-5.6-luna': 'GPT-5.6 Luna',
  'mistralai/mistral-small-2603': 'Mistral Small 4',
}

export const coverReviewerLabel = (model: string) =>
  coverReviewerNames[model] ? `${coverReviewerNames[model]} (${model})` : model
