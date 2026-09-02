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
