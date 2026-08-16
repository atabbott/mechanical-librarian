// Sentence case is the one convention for every server-named field, enum,
// destination and status the owner reads: underscores become spaces and only
// the first character is capitalised, so `media_kind` is `Media kind` on every
// surface. Three of the four route formatters this replaces already used it;
// title case made a server enum look like a proper noun, and leaving the first
// letter alone made a heading look unfinished.
export const fieldLabel = (value: string) =>
  `${labelWords(value).slice(0, 1).toUpperCase()}${labelWords(value).slice(1)}`
// The same words without the capital, for a label set inside a sentence
// (`Choose a media kind value`) rather than standing on its own.
export const labelWords = (value: string) => value.replaceAll('_', ' ')
// Server identifiers with no meaning for a reader; never shown as a label.
export const opaqueSource = /^(?:local|evidence|provider)_[0-9a-f]+$/iu
