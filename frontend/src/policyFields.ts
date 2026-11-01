// The policy notebook's field catalogue: each field's chapter, meaning and
// representations, what each representation does, and where Automatic may
// decide. Moved out of the Policy route under the route-size rule (#1571).
import { TARGET_DESTINATIONS } from './api/http'
import type { PolicyFieldDefault, PolicySection } from './api/http'
import { destinationLabel } from './labels'

export const audiobookOperationLabels = {
  fill: 'Fill an empty field',
  preserve: 'Keep the current value',
  normalize: 'Normalize the representation',
  add: 'Add supported members',
  replace: 'Replace the current value',
  replace_invalid: 'Replace an invalid ASIN',
  replace_recording: 'Replace a valid ASIN',
} as const

// How a scope or an operation reads beside a qualified field.
export const operationScopeLabels = {
  none: 'No automatic effect',
  fill: 'Fill empty only',
  fill_or_replace: 'Fill empty or replace',
  fill_or_respell: 'Fill empty or respell the same language',
  preserve: 'Keep the current value',
  add: 'Add only',
  replace: 'Replace',
} as const
export const operationWords = {
  fill: 'fill empty',
  preserve: 'keep current',
  normalize: 'normalize',
  add: 'add',
  replace: 'replace',
  replace_invalid: 'replace invalid',
  replace_recording: 'replace recording',
} as const
export const policyDestinationLabels = {
  ask: 'Ask me',
  automatic: 'Automatic',
  ignore: 'Ignore',
  unsupported: 'Not supported',
} as const

export interface FieldDefinition {
  key: string
  label: string
  meaning: string
  options: readonly string[]
  optionLabels: Readonly<Record<string, string>>
  chapter: PolicySection
  unsupported?: boolean
}

interface RepresentationGuidance {
  admitted: string
  transformation: string
  effect: string
  fallback: string
}

export const fields: readonly FieldDefinition[] = [
  {
    key: 'title_layout',
    label: 'Title and subtitle',
    meaning: 'Keep a title and subtitle together as one coherent identity.',
    options: ['preserve', 'title_and_subtitle'],
    optionLabels: {
      preserve: 'Preserve current layout',
      title_and_subtitle: 'Separate title and subtitle',
    },
    chapter: 'identity',
  },
  {
    key: 'contributor_names',
    label: 'Authors and narrators',
    meaning: 'Preserve contributor order while choosing how supported names appear.',
    options: ['preserve', 'canonical'],
    optionLabels: { preserve: 'Preserve supported form', canonical: 'Use canonical names' },
    chapter: 'identity',
  },
  {
    key: 'language_rendering',
    label: 'Language',
    meaning:
      'Write every language as one display name, such as English; a current value of the same language spelled another way is respelled, and another language is never written.',
    options: ['display_name'],
    optionLabels: {
      display_name: 'Use language display name',
    },
    chapter: 'identity',
  },
  {
    key: 'subtitle',
    label: 'Subtitle',
    meaning: 'Use a supported descriptive subtitle, or a corroborated title and subtitle pair.',
    options: ['fill_empty', 'replace_descriptive', 'corroborated_split'],
    optionLabels: {
      fill_empty: 'Fill only when empty',
      replace_descriptive: 'Replace a promotional subtitle',
      corroborated_split: 'Use corroborated split',
    },
    chapter: 'identity',
  },
  {
    key: 'date_precision',
    label: 'Publication date and year',
    meaning:
      'Audiobooks use original work publication; ebooks retain edition publication. Recording release dates stay separate.',
    options: ['year', 'full_date'],
    optionLabels: { year: 'Year only', full_date: 'Keep source date precision' },
    chapter: 'publication',
  },
  {
    key: 'publisher_preference',
    label: 'Publisher',
    meaning: 'Choose the supported publisher or imprint for the applicable work or edition.',
    options: ['publisher', 'imprint'],
    optionLabels: { publisher: 'Prefer publisher', imprint: 'Prefer imprint' },
    chapter: 'publication',
  },
  {
    key: 'isbn_form',
    label: 'ISBN',
    meaning:
      'Use an ISBN associated with the selected audiobook recording or ebook edition; a lookup hint alone is not enough.',
    options: ['preserve_exact', 'prefer_isbn13'],
    optionLabels: { preserve_exact: 'Preserve exact ISBN', prefer_isbn13: 'Prefer ISBN-13' },
    chapter: 'publication',
  },
  {
    key: 'series',
    label: 'Series memberships',
    meaning:
      'Fill empty Series, add supported memberships, or explicitly replace the current list.',
    options: [
      'primary_narrative',
      'source_order',
      'all_qualified',
      'add_qualified',
      'replace_qualified',
    ],
    optionLabels: {
      primary_narrative: 'Primary narrative',
      source_order: 'First source membership',
      all_qualified: 'All supported memberships',
      add_qualified: 'Add supported memberships',
      replace_qualified: 'Replace with supported memberships',
    },
    chapter: 'publication',
  },
  {
    key: 'genres',
    label: 'Genres',
    meaning: 'Use one exact eligible source list.',
    options: ['fill_empty', 'add', 'replace'],
    optionLabels: {
      fill_empty: 'Fill only when empty',
      add: 'Add missing values',
      replace: 'Replace with source list',
    },
    chapter: 'curation',
  },
  {
    key: 'tags',
    label: 'Tags',
    meaning:
      'Tags are off by default. When enabled, use compatible source tags or review them yourself.',
    options: ['off', 'fill_empty', 'add', 'replace'],
    optionLabels: {
      off: 'Off',
      fill_empty: 'Fill only when empty',
      add: 'Add missing values',
      replace: 'Replace with source list',
    },
    chapter: 'curation',
  },
  {
    key: 'description_fill',
    label: 'Description',
    meaning:
      'Deterministic selection retains one source description. Audiobooks preserve useful existing prose; optional advice can separately rewrite or combine text.',
    options: ['safe_plain_text', 'google_visible_text', 'replace_safe_plain_text'],
    optionLabels: {
      safe_plain_text: 'Fill empty with safe plain text',
      google_visible_text: 'Visible Google text',
      replace_safe_plain_text: 'Fill empty or replace safe text',
    },
    chapter: 'curation',
  },
  {
    key: 'cover_order',
    label: 'Cover',
    meaning:
      'Use a confirmed cover. Audiobooks keep an adequate cover and replace only a small incumbent with a materially larger match.',
    options: ['fixed_source_order', 'replace_if_strictly_better'],
    optionLabels: {
      fixed_source_order: 'Fill from confirmed order',
      replace_if_strictly_better: 'Replace with a material improvement',
    },
    chapter: 'curation',
  },
  {
    key: 'asin',
    label: 'ASIN',
    meaning:
      'Use an ASIN associated with the audiobook recording, or the ASIN of a confirmed ebook edition.',
    options: ['preserve_exact', 'ebook_manifestation'],
    optionLabels: {
      preserve_exact: 'Preserve exact ASIN',
      ebook_manifestation: 'Ebook edition ASIN',
    },
    chapter: 'curation',
  },
  {
    key: 'explicit',
    label: 'Explicit-content flag',
    meaning:
      'An adult-product source may set an audiobook flag to true. Missing or false evidence never clears it or certifies clean content.',
    options: ['unsupported', 'adult_product'],
    optionLabels: {
      unsupported: 'Leave flag unchanged',
      adult_product: 'Set from adult-product evidence',
    },
    chapter: 'unsupported',
  },
]

// This static closed catalogue is imported by the component proof.
export const policyRepresentationGuidance: Readonly<Record<string, RepresentationGuidance>> = {
  'title_layout:preserve': {
    admitted: 'the current candidate value and its policy/audit context',
    transformation: 'does not transform the produced candidate',
    effect: 'Automatic records the selected title; the subtitle is context only',
    fallback: 'makes no change when Automatic cannot decide safely',
  },
  'title_layout:title_and_subtitle': {
    admitted: 'the current candidate value and its policy/audit context',
    transformation: 'does not split the produced candidate',
    effect: 'Automatic records the selected title; the subtitle is context only',
    fallback: 'makes no change when Automatic cannot decide safely',
  },
  'contributor_names:preserve': {
    admitted: 'the already-produced exact candidate and its policy/audit context',
    transformation: 'records the option without transforming the candidate',
    effect: 'Automatic writes only the selected contributor value',
    fallback: 'makes no change when Automatic cannot decide safely',
  },
  'contributor_names:canonical': {
    admitted:
      'the already-produced exact candidate from provider-consensus display evidence and its retained local provenance',
    transformation:
      'replaces only an equivalent local contributor display form without reconciling contributor identity',
    effect: 'Automatic writes only the selected contributor value',
    fallback: 'makes no change when Automatic cannot decide safely',
  },
  'language_rendering:display_name': {
    admitted: 'the already-produced exact candidate and its policy/audit context',
    transformation:
      'writes the display name of the language the evidence names, such as English; keeps a current value already spelled that way',
    effect:
      'Automatic fills an empty or undetermined language and respells a current value of the same language to that name; a different language is never written',
    fallback:
      'keeps the recorded form when a language has no display name; an undetermined language is never written',
  },
  'subtitle:fill_empty': {
    admitted: "one supported subtitle attested by the book's own metadata",
    transformation: 'uses the exact admitted text',
    effect: 'fills an empty Subtitle only',
    fallback:
      "makes no change when Subtitle already has a value or the book's own metadata does not state it",
  },
  'subtitle:corroborated_split': {
    admitted: 'one corroborated title and subtitle pair',
    transformation: 'splits only an admitted exact joined title',
    effect: 'sets Title and Subtitle together',
    fallback:
      'requires both fields to be Automatic with write permission; if either is blocked, neither changes',
  },
  'subtitle:replace_descriptive': {
    admitted: 'a supported descriptive subtitle for this audiobook work',
    transformation: 'replaces only a recognized whole-field promotion',
    effect: 'fills an empty Subtitle or replaces an award, bestseller or adaptation announcement',
    fallback: 'keeps genuine descriptive text and never clears a subtitle from missing evidence',
  },
  'date_precision:year': {
    admitted: 'the already-produced exact candidate and its policy/audit context',
    transformation: 'records the option without reducing the candidate',
    effect: 'Automatic writes only the selected publication date or year',
    fallback:
      "audiobooks need a confirmed original publication date; a recording's release date is not used",
  },
  'date_precision:full_date': {
    admitted: 'the already-produced exact candidate and its policy/audit context',
    transformation: 'records the option without changing candidate precision',
    effect: 'Automatic writes only the selected publication date or year',
    fallback:
      'audiobooks retain the supplied year, month or day precision without inventing a date; Automatic must support the change',
  },
  'publisher_preference:publisher': {
    admitted: 'the already-produced exact candidate and its policy/audit context',
    transformation: 'records the option without selecting a publisher value',
    effect: 'Automatic writes only the selected publisher',
    fallback: 'makes no change when Automatic cannot decide safely',
  },
  'publisher_preference:imprint': {
    admitted: 'the already-produced exact Publisher preview candidate and its policy/audit context',
    transformation: 'records the option without selecting an imprint value',
    effect: 'the preview shows the selected publisher; Automatic does not write an imprint',
    fallback: 'makes no change when no publisher is found',
  },
  'isbn_form:preserve_exact': {
    admitted: 'an already admitted exact ISBN',
    transformation: 'keeps its observed form',
    effect: 'sets ISBN',
    fallback: 'makes no change when no exact ISBN is recorded',
  },
  'isbn_form:prefer_isbn13': {
    admitted: 'an already admitted ISBN-13 representation',
    transformation: 'selects that exact existing form without conversion',
    effect: 'sets ISBN',
    fallback: 'makes no change when no ISBN-13 is recorded',
  },
  'series:primary_narrative': {
    admitted: 'provider-backed memberships for this work',
    transformation: 'selects the primary narrative membership',
    effect: 'Automatic fills an empty Series field only',
    fallback: 'makes no change when no series membership is confirmed',
  },
  'series:source_order': {
    admitted: 'provider-backed memberships for this work',
    transformation: 'selects the first admitted source membership',
    effect: 'Automatic fills an empty Series field only',
    fallback: 'makes no change when no series membership is confirmed',
  },
  'series:all_qualified': {
    admitted: 'all provider-backed qualified memberships for this work',
    transformation: 'keeps them visible in admitted order',
    effect:
      'keeps the full context while Automatic fills an empty Series field with only its first membership',
    fallback: 'makes no change when no series membership is confirmed',
  },
  'series:add_qualified': {
    admitted: 'supported series and sequence associations for this work',
    transformation: 'retains current memberships and adds missing associations',
    effect: 'extends Series only where Automatic supports adding for this media',
    fallback: 'asks you when Automatic cannot add safely or the evidence is ambiguous',
  },
  'series:replace_qualified': {
    admitted: 'supported series and sequence associations for this work',
    transformation: 'uses the complete selected list',
    effect: 'replaces Series only where Automatic supports replacing for this media',
    fallback: 'asks you when Automatic cannot replace safely or the evidence is ambiguous',
  },
  'genres:fill_empty': {
    admitted: 'one exact eligible source list',
    transformation: 'keeps the list unchanged',
    effect: 'fills empty Genres only',
    fallback: 'makes no change when Genres already has values or no list is eligible',
  },
  'genres:add': {
    admitted: 'one exact eligible source list',
    transformation: 'adds only values absent from Genres',
    effect: 'extends Genres',
    fallback: 'makes no change when no list is eligible',
  },
  'genres:replace': {
    admitted: 'one exact eligible source list',
    transformation: 'uses that list without combining providers',
    effect: 'replaces Genres',
    fallback: 'makes no change when no list is eligible',
  },
  'tags:off': {
    admitted: 'no source value',
    transformation: 'does not transform metadata',
    effect: 'leaves Tags unchanged',
    fallback: 'never changes tags',
  },
  'tags:fill_empty': {
    admitted: 'compatible explicit tag lists from applicable sources',
    transformation: 'audiobooks combine supported members in stable source order',
    effect: 'fills empty Tags only',
    fallback: 'makes no change when Tags already has values or no list is eligible',
  },
  'tags:add': {
    admitted: 'compatible explicit tag lists from applicable sources',
    transformation: 'keeps owner tags and adds supported audiobook members in stable order',
    effect: 'extends Tags',
    fallback: 'makes no change when the lists are malformed, incomplete or conflicting',
  },
  'tags:replace': {
    admitted: 'one exact eligible source list',
    transformation: 'uses that list without combining providers',
    effect: 'replaces Tags',
    fallback: 'makes no change when no list is eligible',
  },
  'description_fill:safe_plain_text': {
    admitted:
      'safe complete text from an applicable source; catalogue placeholders are excluded from automatic ebook fills and retained for explicit choice',
    transformation:
      'uses the preferred applicable source; equal sources use stable source-record order, never text length',
    effect: 'fills an empty Description; preserves populated prose',
    fallback:
      'makes no change for unsafe or incomplete source text, conflicting work identity, or when Automatic is unavailable',
  },
  'description_fill:replace_safe_plain_text': {
    admitted: 'safe complete synopsis text from an applicable source',
    transformation:
      'keeps the original preferred source text; equal audiobook sources use stable source-record order',
    effect:
      'for audiobooks, fills an empty Description or replaces only whole chapter/track number and no-description/no-synopsis placeholders, preserving other useful prose; for ebooks, fills or replaces a Description with eligible source text',
    fallback:
      'makes no change for unsafe, incomplete or conflicting evidence; the audiobook placeholder rule does not judge writing quality',
  },
  'description_fill:google_visible_text': {
    admitted: 'eligible Google Books visible text only',
    transformation: 'turns bounded Google HTML into inert visible text',
    effect: 'fills an empty Description only',
    fallback: 'makes no change without it and never falls back to Hardcover',
  },
  'cover_order:fixed_source_order': {
    admitted: 'identity-confirmed non-current covers after provider fetching is quiescent',
    transformation: 'uses the frozen identity, aspect, size and packet tie-break order',
    effect: 'fills an empty Cover',
    fallback: 'preserves an existing Cover',
  },
  'cover_order:replace_if_strictly_better': {
    admitted: 'confirmed audiobook covers with a known small incumbent',
    transformation: 'selects the first eligible cover in the existing confirmed order',
    effect:
      'replaces an incumbent below 300 pixels on its shorter edge with a candidate at least 500 pixels and twice as large on that edge',
    fallback:
      'keeps a current cover that is large enough or of unknown size; the new cover must be portrait or square, and Automatic must support the change',
  },
  'asin:preserve_exact': {
    admitted: 'an already admitted exact ASIN',
    transformation: 'keeps its observed form',
    effect: 'sets ASIN',
    fallback: 'makes no change when no exact ASIN is recorded',
  },
  'asin:ebook_manifestation': {
    admitted: 'an admitted ebook-manifestation ASIN',
    transformation: 'selects that exact manifestation value',
    effect: 'sets ASIN',
    fallback: 'makes no change when no ebook edition ASIN is recorded',
  },
  'explicit:unsupported': {
    admitted: 'no automatic source value',
    transformation: 'does not interpret content',
    effect: 'leaves the explicit-content flag unchanged',
    fallback: 'stays off until you choose an eligible option and review mode',
  },
  'explicit:adult_product': {
    admitted: 'positive adult-product evidence for the applicable audiobook',
    transformation: 'sets only true, never false',
    effect: 'may set the flag to true after separate application consent',
    fallback:
      'preserves the current flag for false or missing evidence; this does not certify clean content',
  },
}

// The one resolution of "what this field says when the document is silent".
// The dirty comparison and the chapter badges both read it, so they cannot
// disagree about whether a draft matches the active policy.
export const fieldDefaults: Readonly<Record<string, PolicyFieldDefault>> = Object.fromEntries(
  fields.map((field) => [
    field.key,
    {
      destination: field.unsupported || field.key === 'explicit' ? 'unsupported' : 'ask',
      option: field.options[0] ?? '',
    },
  ]),
)

export const chapters: readonly { key: PolicySection; label: string; description: string }[] = [
  {
    key: 'identity',
    label: 'Identity',
    description: 'How a work names itself and its contributors.',
  },
  {
    key: 'publication',
    label: 'Publication',
    description: 'How edition facts and memberships are represented.',
  },
  {
    key: 'curation',
    label: 'Curation',
    description: 'Fields that remain intentionally owner-directed.',
  },
  {
    key: 'unsupported',
    label: 'Additional fields',
    description: 'Fields with limited support that start off until you enable them.',
  },
]

// A suspension category is a policy field or, for a provider-consensus
// title, the destination itself: `Recover Title suspension` (#1286).
export const readableCategory = (key: string) => {
  const destination = TARGET_DESTINATIONS.find((candidate) => candidate === key)
  return (
    fields.find((field) => field.key === key)?.label ??
    (destination ? destinationLabel(destination) : 'Another policy field')
  )
}
export const automaticQualified = (
  field: FieldDefinition,
  option: string,
  qualification: readonly {
    readonly identity?: string
    readonly automatic_available: boolean
    readonly field: string
    readonly option: string
  }[],
) => {
  const entries = qualification.filter(
    (entry) => entry.field === field.key && entry.option === option,
  )
  return entries.some((entry) => entry.automatic_available)
}
// The disclosure lists one consequence per representation. The catalogue's
// admitted/transformation halves are qualification mechanics; the owner is
// choosing between outcomes, so the effect and what happens when it cannot
// apply are the two sentences that belong on the line.
export const representationConsequence = (guidance: RepresentationGuidance | undefined) =>
  guidance === undefined
    ? 'No owner-readable consequence is recorded for this representation.'
    : `${guidance.effect.slice(0, 1).toUpperCase()}${guidance.effect.slice(1)}; ${guidance.fallback}.`
