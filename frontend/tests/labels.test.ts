import { describe, expect, it } from 'vitest'

import {
  AUDIO_SURFACES,
  CLAIM_BASES,
  CLAIM_KINDS,
  COVER_HISTORY_OUTCOMES,
  COVER_SOURCE_KINDS,
  DESCRIPTION_STATES,
  FIELD_STATES,
  MEDIA_HEALTH_KINDS,
  MEDIA_KINDS,
  PREPARATION_FAILURE_STAGES,
  PREPARATION_FAILURE_STATUSES,
  PROVIDER_QUERY_KINDS,
  PROVIDER_STATUSES,
  SCAN_STATES,
  SHADOW_DISPOSITIONS,
  SUSPENSION_REASONS,
  TARGET_DESTINATIONS,
  TRANSITION_OUTCOMES,
} from '../src/api/http'
import type { TargetDestination } from '../src/api/http'
import {
  audioSurfaceLabels,
  candidateReference,
  choiceRequestReasons,
  claimBasisLabels,
  claimKindLabels,
  coverHistoryLabels,
  coverNoteLabel,
  coverSourceLabels,
  destinationLabel,
  destinationPhrase,
  fieldStateLabels,
  languageName,
  observationText,
  lookupLabels,
  materialComparisonReasons,
  materialReasonLabel,
  mediaHealthLabels,
  mediaKindLabels,
  preparationStageLabels,
  preparationStatusLabels,
  scanStateLabels,
  shadowDispositionLabels,
  sourceStatusLabels,
  transitionLabels,
  writeHoldReasons,
  writeStateLabels,
} from '../src/labels'

// Each closed code family beside the map that names it for the owner.
const families: readonly (readonly [
  string,
  readonly string[],
  Readonly<Record<string, string>>,
])[] = [
  [
    'destination',
    TARGET_DESTINATIONS,
    Object.fromEntries(TARGET_DESTINATIONS.map((code) => [code, destinationLabel(code)])),
  ],
  ['media kind', MEDIA_KINDS, mediaKindLabels],
  ['media health', MEDIA_HEALTH_KINDS, mediaHealthLabels],
  ['lookup', PROVIDER_QUERY_KINDS, lookupLabels],
  ['source status', PROVIDER_STATUSES, sourceStatusLabels],
  ['claim kind', CLAIM_KINDS, claimKindLabels],
  ['claim basis', CLAIM_BASES, claimBasisLabels],
  ['field state', FIELD_STATES, fieldStateLabels],
  ['audio surface', AUDIO_SURFACES, audioSurfaceLabels],
  ['preparation status', PREPARATION_FAILURE_STATUSES, preparationStatusLabels],
  ['preparation stage', PREPARATION_FAILURE_STAGES, preparationStageLabels],
  ['write hold', SUSPENSION_REASONS, writeHoldReasons],
  ['scan state', SCAN_STATES, scanStateLabels],
  ['write state', DESCRIPTION_STATES, writeStateLabels],
  ['preview disposition', SHADOW_DISPOSITIONS, shadowDispositionLabels],
  ['transition', TRANSITION_OUTCOMES, transitionLabels],
  ['cover history', COVER_HISTORY_OUTCOMES, coverHistoryLabels],
  ['cover source', COVER_SOURCE_KINDS, coverSourceLabels],
]

// What the removed fallbacks printed: `publishedYear` → `Published year`,
// `no_match` → `No match`.
const mechanical = (code: string) => {
  const words = code
    .replace(/([a-z])([A-Z])/gu, '$1 $2')
    .replaceAll('_', ' ')
    .toLowerCase()
  return `${words.slice(0, 1).toUpperCase()}${words.slice(1)}`
}

describe('owner labels (#1286)', () => {
  it.each(families)('names every closed %s code, and only those', (_family, codes, labels) => {
    expect(Object.keys(labels).sort()).toEqual([...codes].sort())
    for (const code of codes) expect(labels[code]?.trim()).not.toBe('')
  })

  it.each(families)('never reads a %s code as the code itself', (_family, codes, labels) => {
    for (const code of codes.filter((value) => /[_A-Z]/u.test(value)))
      expect(labels[code]).not.toBe(code)
    for (const label of Object.values(labels)) expect(label).not.toMatch(/_/u)
  })

  it('replaces every mechanical misreading the removed fallbacks produced', () => {
    // Many codes are plain English once spaced (`no_match` is `No match`), so
    // the rule is not that a label differs from its sentence-cased code; it is
    // that no code is turned into words mechanically. These are the
    // misreadings that reached the owner: acronyms and camel-cased fields.
    for (const code of ['isbn', 'asin', 'publishedYear', 'publishedDate'] as const)
      expect(destinationLabel(code)).not.toBe(mechanical(code))
    expect(claimBasisLabels.package_metadata).not.toBe(mechanical('package_metadata'))
    expect(mediaKindLabels.epub).not.toBe(mechanical('epub'))
    expect(sourceStatusLabels.insufficient_input).not.toBe(mechanical('insufficient_input'))
    expect(writeHoldReasons.unexpected_effect_behavior).not.toBe(
      mechanical('unexpected_effect_behavior'),
    )
  })

  it('fails the type check for a map that misses a code', () => {
    const missing = {
      title: 'Title',
      subtitle: 'Subtitle',
      authors: 'Authors',
      narrators: 'Narrators',
      series: 'Series',
      genres: 'Genres',
      publishedYear: 'Publication year',
      publishedDate: 'Publication date',
      publisher: 'Publisher',
      description: 'Description',
      isbn: 'ISBN',
      asin: 'ASIN',
      language: 'Language',
      explicit: 'Explicit content',
      tags: 'Tags',
      // @ts-expect-error a destination map without `cover` does not satisfy the closed set
    } satisfies Record<TargetDestination, string>
    expect(Object.keys(missing)).not.toContain('cover')
  })

  it('keeps acronyms in capitals inside a sentence and lowers other names', () => {
    expect(destinationLabel('isbn')).toBe('ISBN')
    expect(destinationLabel('publishedYear')).toBe('Publication year')
    expect(destinationPhrase('isbn')).toBe('ISBN')
    expect(destinationPhrase('asin')).toBe('ASIN')
    expect(destinationPhrase('publishedDate')).toBe('publication date')
    expect(`Which ${destinationPhrase('isbn')} should this work use?`).toBe(
      'Which ISBN should this work use?',
    )
  })

  it('names a provider reference by its provider and leaves other text as it is', () => {
    expect(candidateReference('audible-product-B0SYNE7P1K')).toBe('Audible product B0SYNE7P1K')
    expect(candidateReference('open-library-work-OL1W')).toBe('Open Library work OL1W')
    expect(candidateReference('recording-1')).toBe('recording-1')
    expect(candidateReference(`hardcover-work-${'x'.repeat(200)}`)).toHaveLength(158)
  })

  it('reads an unknown cover note as one neutral sentence, never as its code', () => {
    expect(coverNoteLabel('source_timeout')).toBe('The source did not answer in time')
    expect(coverNoteLabel('a_future_reason')).toBe('Another cover safeguard applies')
  })

  it.each([
    ['en', 'English (en)'],
    ['en-GB', 'British English (en-GB)'],
    ['eng', 'English (eng)'],
    // Already a name, not a tag: shown as it is.
    ['English', 'English'],
    // Tag-shaped but not a language the runtime can name.
    ['xx-invalid', 'xx-invalid'],
    ['', ''],
  ])('renders the language value %j through Intl.DisplayNames as %j', (value, shown) => {
    expect(languageName(value)).toBe(shown)
  })

  // One language tag reads one way on every page, region and script included
  // (#1286 L1); the server's observation text stays literal.
  it.each([
    ['Language: en', 'Language: English (en)'],
    ['Language: pt-BR', 'Language: Brazilian Portuguese (pt-BR)'],
    ['Language: en-GB', 'Language: British English (en-GB)'],
    ['Language: zh-Hant', 'Language: Traditional Chinese (zh-Hant)'],
    [
      'Language: fr. Retained for review only; not an eligible choice.',
      'Language: French (fr). Retained for review only; not an eligible choice.',
    ],
    // Undetermined keeps its tag; the observation already says so.
    ['Language: und', 'Language: und'],
    [
      'Language: UND. Undetermined language; not an eligible choice.',
      'Language: UND. Undetermined language; not an eligible choice.',
    ],
    // Not a tag, or not a language observation: shown as it is.
    ['Language: English', 'Language: English'],
    ['Language: not a tag', 'Language: not a tag'],
    ['Title: en', 'Title: en'],
  ])('reads the observation %j as %j', (text, shown) => {
    expect(observationText(text)).toBe(shown)
    // The page names the tag exactly as the field value beside it does.
    const tag = /^Language: ([A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*)(?=\.|$)/u.exec(text)?.[1]
    if (tag && !/^und$/iu.test(tag)) expect(shown).toContain(languageName(tag))
  })
})

describe('optional choice conditions (#1557)', () => {
  it('leaves off exactly the conditions that ask the owner to choose', () => {
    const asking = materialComparisonReasons.filter((code) =>
      /yourself|needs you/u.test(materialReasonLabel(code) ?? ''),
    )
    expect([...choiceRequestReasons].sort()).toEqual([...asking].sort())
  })
})
