// The policy notebook's field catalogue: each field's chapter, meaning and
// representations, what each representation does, and where Automatic may
// decide. Moved out of the Policy route under the route-size rule (#1571).
import { TARGET_DESTINATIONS } from './api/http'
import type { PolicyFieldDefault, PolicySection } from './api/http'
import { destinationLabel } from './labels'

export const audiobookOperationLabels = {
  fill: 'Fill an empty field',
  preserve: 'Keep the current value',
  normalize: 'Normalise the current value',
  add: 'Add supported members',
  replace: 'Replace the current value',
  replace_invalid: 'Replace an unreadable ASIN',
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
  normalize: 'normalise',
  add: 'add',
  replace: 'replace',
  replace_invalid: 'replace unreadable',
  replace_recording: 'replace valid',
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
    meaning: 'Decide the title and subtitle together.',
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
    meaning: 'Keep the order of names and choose how they are written.',
    options: ['preserve', 'canonical'],
    optionLabels: { preserve: 'Preserve supported form', canonical: 'Use canonical names' },
    chapter: 'identity',
  },
  {
    key: 'language_rendering',
    label: 'Language',
    meaning: 'Write each language by name, such as English.',
    options: ['display_name'],
    optionLabels: {
      display_name: 'Use language display name',
    },
    chapter: 'identity',
  },
  {
    key: 'subtitle',
    label: 'Subtitle',
    meaning:
      'Use a descriptive subtitle, or a title and subtitle pair confirmed by another source.',
    options: ['fill_empty', 'replace_descriptive', 'corroborated_split'],
    optionLabels: {
      fill_empty: 'Fill only when empty',
      replace_descriptive: 'Replace a promotional subtitle',
      corroborated_split: 'Use a confirmed split',
    },
    chapter: 'identity',
  },
  {
    key: 'date_precision',
    label: 'Publication date and year',
    meaning: "Audiobooks use the date the work was first published, and ebooks the edition's date.",
    options: ['year', 'full_date'],
    optionLabels: { year: 'Year only', full_date: 'Keep source date precision' },
    chapter: 'publication',
  },
  {
    key: 'publisher_preference',
    label: 'Publisher',
    meaning: 'Choose the publisher or imprint for the work or edition.',
    options: ['publisher', 'imprint'],
    optionLabels: { publisher: 'Prefer publisher', imprint: 'Prefer imprint' },
    chapter: 'publication',
  },
  {
    key: 'isbn_form',
    label: 'ISBN',
    meaning:
      'Use the ISBN of the audiobook recording or the ebook edition. An ISBN used only to search is not enough.',
    options: ['preserve_exact', 'prefer_isbn13'],
    optionLabels: { preserve_exact: 'Preserve exact ISBN', prefer_isbn13: 'Prefer ISBN-13' },
    chapter: 'publication',
  },
  {
    key: 'series',
    label: 'Series memberships',
    meaning: 'Fill an empty Series, add supported memberships, or replace the current list.',
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
    meaning: "Use one source's genre list as it is.",
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
      'Tags start Off and are never asked about while Off. The other options take them from sources.',
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
    meaning: "Use one source's description. Audiobooks keep useful text they already have.",
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
      'Use a confirmed cover. Audiobooks replace a small current cover only with a clearly larger one.',
    options: ['fixed_source_order', 'replace_if_strictly_better'],
    optionLabels: {
      fixed_source_order: 'Fill from confirmed covers',
      replace_if_strictly_better: 'Replace with a clearly larger cover',
    },
    chapter: 'curation',
  },
  {
    key: 'asin',
    label: 'ASIN',
    meaning: 'Use the ASIN of the audiobook recording, or of a confirmed ebook edition.',
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
    meaning: 'An adult-product source can turn the flag on for an audiobook.',
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
    effect: 'Automatic records the selected title and leaves the subtitle as it is',
    fallback: 'makes no change when Automatic cannot decide',
  },
  'title_layout:title_and_subtitle': {
    admitted: 'the current candidate value and its policy/audit context',
    transformation: 'does not split the produced candidate',
    effect: 'Automatic records the selected title and leaves the subtitle as it is',
    fallback: 'makes no change when Automatic cannot decide',
  },
  'contributor_names:preserve': {
    admitted: 'the already-produced exact candidate and its policy/audit context',
    transformation: 'records the option without transforming the candidate',
    effect: 'Automatic writes only the selected names',
    fallback: 'makes no change when Automatic cannot decide',
  },
  'contributor_names:canonical': {
    admitted:
      'the already-produced exact candidate from provider-consensus display evidence and its retained local provenance',
    transformation:
      'replaces only an equivalent local contributor display form without reconciling contributor identity',
    effect: 'Automatic writes only the selected names',
    fallback: 'makes no change when Automatic cannot decide',
  },
  'language_rendering:display_name': {
    admitted: 'the already-produced exact candidate and its policy/audit context',
    transformation:
      'writes the display name of the language the evidence names, such as English; keeps a current value already spelled that way',
    effect:
      "Automatic writes the language's name into an empty or unknown field, and respells the same language written another way",
    fallback:
      'keeps the current form when a language has no name, and never writes a different or unknown language',
  },
  'subtitle:fill_empty': {
    admitted: "one supported subtitle attested by the book's own metadata",
    transformation: 'uses the exact admitted text',
    effect: 'fills an empty Subtitle only',
    fallback: "makes no change when Subtitle has a value or the book's own details give none",
  },
  'subtitle:corroborated_split': {
    admitted: 'one corroborated title and subtitle pair',
    transformation: 'splits only an admitted exact joined title',
    effect: 'sets Title and Subtitle together',
    fallback:
      'needs both fields set to Automatic, with write permission. If either is held back, neither changes',
  },
  'subtitle:replace_descriptive': {
    admitted: 'a supported descriptive subtitle for this audiobook work',
    transformation: 'replaces only a recognized whole-field promotion',
    effect: 'fills an empty Subtitle or replaces an award, bestseller or adaptation announcement',
    fallback: 'keeps real descriptive text, and never clears a subtitle because sources lack one',
  },
  'date_precision:year': {
    admitted: 'the already-produced exact candidate and its policy/audit context',
    transformation: 'records the option without reducing the candidate',
    effect: 'Automatic writes only the selected publication date or year',
    fallback:
      "audiobooks need a confirmed original publication date. A recording's release date is not used",
  },
  'date_precision:full_date': {
    admitted: 'the already-produced exact candidate and its policy/audit context',
    transformation: 'records the option without changing candidate precision',
    effect: 'Automatic writes only the selected publication date or year',
    fallback:
      'audiobooks keep the year, month or day precision the source gives. No date is invented',
  },
  'publisher_preference:publisher': {
    admitted: 'the already-produced exact candidate and its policy/audit context',
    transformation: 'records the option without selecting a publisher value',
    effect: 'Automatic writes only the selected publisher',
    fallback: 'makes no change when Automatic cannot decide',
  },
  'publisher_preference:imprint': {
    admitted: 'the already-produced exact Publisher preview candidate and its policy/audit context',
    transformation: 'records the option without selecting an imprint value',
    effect: 'the preview shows the selected publisher. Automatic does not write an imprint',
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
    effect: 'shows every membership, but Automatic fills an empty Series field with only the first',
    fallback: 'makes no change when no series membership is confirmed',
  },
  'series:add_qualified': {
    admitted: 'supported series and sequence associations for this work',
    transformation: 'retains current memberships and adds missing associations',
    effect: 'extends Series only where Automatic supports adding for this media',
    fallback: 'asks you when Automatic cannot add, or the sources do not agree',
  },
  'series:replace_qualified': {
    admitted: 'supported series and sequence associations for this work',
    transformation: 'uses the complete selected list',
    effect: 'replaces Series only where Automatic supports replacing for this media',
    fallback: 'asks you when Automatic cannot replace, or the sources do not agree',
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
    fallback: 'never asks you about tags',
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
    fallback: 'makes no change when a list is unreadable or incomplete, or the lists disagree',
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
    effect: 'fills an empty Description and keeps existing text',
    fallback:
      'makes no change when the source text is unsafe or incomplete, or the sources disagree about the work',
  },
  'description_fill:replace_safe_plain_text': {
    admitted: 'safe complete synopsis text from an applicable source',
    transformation:
      'keeps the original preferred source text; equal audiobook sources use stable source-record order',
    effect:
      'for audiobooks, fills an empty Description or replaces a placeholder, such as a lone chapter number or "No synopsis". Other text is kept. For ebooks, fills or replaces it with source text',
    fallback:
      'makes no change when the text is unsafe or incomplete, or the sources disagree. The placeholder rule does not judge how well a description is written',
  },
  'description_fill:google_visible_text': {
    admitted: 'eligible Google Books visible text only',
    transformation: 'turns bounded Google HTML into inert visible text',
    effect: 'fills an empty Description only',
    fallback: 'makes no change without Google Books text, and never uses Hardcover instead',
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
      'replaces a current cover under 300 pixels on its shorter edge with one at least 500 pixels and twice as large on that edge',
    fallback:
      'keeps a current cover that is large enough or of unknown size. The new cover must be portrait or square',
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
    fallback: 'nothing changes until you choose the other option and a setting',
  },
  'explicit:adult_product': {
    admitted: 'positive adult-product evidence for the applicable audiobook',
    transformation: 'sets only true, never false',
    effect: 'can turn the flag on, once you give write permission',
    fallback:
      'keeps the current flag when a source says no or nothing. An unset flag does not mean the book is clean',
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

export const chapters: readonly { key: PolicySection; label: string }[] = [
  { key: 'identity', label: 'Identity' },
  { key: 'publication', label: 'Publication' },
  { key: 'curation', label: 'Curation' },
  { key: 'unsupported', label: 'Additional fields' },
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
    ? 'Nothing is recorded for this option.'
    : `${guidance.effect.slice(0, 1).toUpperCase()}${guidance.effect.slice(1)}. ${guidance.fallback.slice(0, 1).toUpperCase()}${guidance.fallback.slice(1)}.`
