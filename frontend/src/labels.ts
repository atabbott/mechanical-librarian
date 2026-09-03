const ownerFieldLabels: Record<string, string> = {
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
// The same words without the capital, for a label set inside a sentence
// (`Choose a media kind value`) rather than standing on its own.
export const labelWords = (value: string) => value.replaceAll('_', ' ')
// Server identifiers with no meaning for a reader; never shown as a label.
export const opaqueSource = /^(?:local|evidence|provider)_[0-9a-f]+$/iu
