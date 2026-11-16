import { describe, expect, it } from 'vitest'

import { policyRepresentationGuidance } from '../src/policyFields'
import { policyCell, policyImpactTotals } from '../src/policyImpact'

// One side of a compared field, as the preview answers it.
const field = (
  values: Partial<Parameters<typeof policyCell>[0]> = {},
): Parameters<typeof policyCell>[0] => ({
  current_value: null,
  desired_value: null,
  field: 'subtitle',
  outcome: 'projected',
  reason: null,
  ...values,
})

describe('policy impact projections', () => {
  it('labels both associated values in a paired subtitle preview', () => {
    expect(
      policyCell(
        field({ desired_value: { title: 'Synthetic Journey', subtitle: 'A Tale of Courage' } }),
        'automatic',
      ),
    ).toEqual({
      effect: 'write',
      value: 'title Synthetic Journey and subtitle A Tale of Courage',
      note: undefined,
    })
  })

  it('reads a list or series as Activity does, never as raw JSON (#1286 X6)', () => {
    const series = policyCell(
      field({
        current_value: [{ name: 'Old order', sequence: '1' }],
        desired_value: [
          { name: 'Reading order', sequence: '2' },
          { name: 'Companion set', sequence: null },
        ],
        field: 'series',
      }),
      'automatic',
    ).value
    expect(series).toBe('Reading order (2), Companion set')
    const genres = policyCell(
      field({
        current_value: ['Fantasy'],
        desired_value: ['Fantasy', 'Adventure'],
        field: 'genres',
      }),
      'automatic',
    ).value
    expect(genres).toBe('Fantasy, Adventure')
    expect(`${series} ${genres}`).not.toMatch(/[[\]{}"]/u)
  })

  it('reads an Ask me change as a value left to the owner (#1286)', () => {
    const subtitle = field({ desired_value: 'A bounded subtitle' })
    // Under Ask me nothing is written: the value is what the review would propose.
    expect(policyCell(subtitle, 'ask')).toEqual({
      effect: 'ask',
      value: 'A bounded subtitle',
      note: 'Left to you',
    })
    expect(policyCell({ ...subtitle, current_value: 'Old', desired_value: null }, 'ask')).toEqual({
      effect: 'ask',
      value: 'Not set',
      note: 'Left to you',
    })
    expect(policyCell({ ...subtitle, current_value: 'Old', desired_value: 'New' }, 'ask')).toEqual({
      effect: 'ask',
      value: 'New',
      note: 'Left to you',
    })
    // The outcome compiler says a value is left to the owner under any setting.
    expect(policyCell({ ...subtitle, outcome: 'suggested' }, 'automatic')).toEqual({
      effect: 'ask',
      value: 'A bounded subtitle',
      note: 'Left to you',
    })
    // A language value reads by its name.
    expect(
      policyCell(
        field({ field: 'language_rendering', current_value: 'en', desired_value: 'English' }),
        'automatic',
      ).value,
    ).toBe('English')
  })

  it('sets the not-offered sentence under the value it leaves to you (#1524)', () => {
    const sentence = 'Automatic is not offered for this kind of change, so it is left to you.'
    expect(
      policyCell(
        field({
          field: 'contributor_names',
          outcome: 'suggested',
          desired_value: ['Ilsa Brandt'],
          reason: 'automatic_unqualified',
        }),
        'automatic',
      ),
    ).toEqual({ effect: 'ask', value: 'Ilsa Brandt', note: sentence })
    // With no value proposed, the sentence stands alone.
    expect(
      policyCell(field({ outcome: 'abstain', reason: 'automatic_unqualified' }), 'automatic'),
    ).toEqual({ effect: 'ask', value: sentence })
  })

  it('reads a cover as the words the application built, with its rule (#1524)', () => {
    const cover = (values: Parameters<typeof field>[0], audiobook = false) =>
      policyCell(field({ field: 'cover_order', ...values }), 'automatic', audiobook)
    // A fill carries no rule sentence: it would be the same on every row.
    expect(cover({ desired_value: 'Fills the empty cover' })).toEqual({
      effect: 'write',
      value: 'Fills the empty cover',
      note: undefined,
    })
    expect(cover({ outcome: 'suggested', desired_value: 'Suggests a confirmed cover' })).toEqual({
      effect: 'ask',
      value: 'Suggests a confirmed cover',
      note: undefined,
    })
    expect(
      cover({
        outcome: 'suggested',
        desired_value: 'Suggests a replacement',
        reason: 'below_floor',
      }),
    ).toEqual({ effect: 'ask', value: 'Suggests a replacement', note: 'Under 300 px' })
    // The shape rule names the work's medium.
    expect(
      cover(
        {
          outcome: 'suggested',
          desired_value: 'Suggests a replacement',
          reason: 'wrong_shape',
        },
        true,
      ).note,
    ).toBe('Book shape on an audiobook')
    expect(
      cover({
        outcome: 'no_op',
        desired_value: 'Current cover kept',
        reason: 'current_cover_kept',
      }),
    ).toEqual({
      effect: 'none',
      value: 'Current cover kept',
      note: 'The current cover has no fault',
    })
    expect(cover({ outcome: 'no_op', desired_value: 'Current cover kept' })).toEqual({
      effect: 'none',
      value: 'Current cover kept',
      note: undefined,
    })
    expect(cover({ outcome: 'no_op', reason: 'no_confirmed_candidates' })).toEqual({
      effect: 'none',
      value: 'No change',
      note: 'No cover is confirmed for this book',
    })
    expect(cover({ outcome: 'no_op', reason: 'policy_ignored' })).toEqual({
      effect: 'none',
      value: 'No change',
      note: 'Set to Ignore.',
    })
  })

  it.each([
    ['cover_not_compared', "No scan has compared this work's covers yet."],
    ['provider_fetching', 'Sources are still being fetched.'],
    ['cover_compared_next_scan', 'The next scan compares the covers again.'],
    // A reason this build does not know reads as the most general one.
    ['another_reason', 'The next scan compares the covers again.'],
  ])('says why a cover row is not previewed: %s (#1524)', (reason, note) => {
    expect(
      policyCell(field({ field: 'cover_order', outcome: 'not_previewed', reason }), 'automatic'),
    ).toEqual({ effect: 'unpreviewed', value: 'Not previewed.', note })
  })

  it('projects bounded policy impact language without promoting transport enums', () => {
    const none = (note?: string) => ({ effect: 'none', value: 'No change', note })
    expect(
      policyCell(
        field({ current_value: 'Old title', desired_value: 'New title', field: 'title_layout' }),
        'automatic',
      ),
    ).toEqual({ effect: 'write', value: 'New title', note: undefined })
    expect(policyCell(field({ desired_value: 'A bounded subtitle' }), 'automatic')).toEqual({
      effect: 'write',
      value: 'A bounded subtitle',
      note: undefined,
    })
    expect(
      policyCell(
        field({
          desired_value: '9780306406157',
          field: 'isbn_form',
          reason: 'verified_manifestation_isbn',
        }),
        'automatic',
      ),
    ).toEqual({
      effect: 'write',
      value: '9780306406157',
      note: 'It is verified for this edition.',
    })
    // A clear writes nothing in the field's place.
    expect(policyCell(field({ current_value: 'Old subtitle' }), 'automatic')).toEqual({
      effect: 'write',
      value: 'Not set',
      note: undefined,
    })
    expect(policyCell(field({ desired_value: '' }), 'automatic')).toEqual(
      none('No supported value found.'),
    )
    expect(
      policyCell(
        field({
          current_value: 'Current title',
          desired_value: 'Current title',
          field: 'title_layout',
        }),
        'automatic',
      ),
    ).toEqual(none())
    expect(
      policyCell(
        field({ outcome: 'abstain', reason: 'unrecognized_retained_reason' }),
        'automatic',
      ),
    ).toEqual(none('No value would be chosen for you, and no more specific reason is known.'))
    // An unreadable current value is left to the owner, in one sentence.
    expect(
      policyCell(
        field({ field: 'title_layout', outcome: 'no_op', reason: 'target_state_unusable' }),
        'automatic',
      ),
    ).toEqual({
      effect: 'ask',
      value: 'Current value is unavailable or unreadable. Inspect the source before deciding.',
    })
    expect(
      policyCell(field({ field: 'tags', outcome: 'abstain', reason: 'policy_ignored' }), 'ignore'),
    ).toEqual(none('Set to Ignore.'))
    expect(
      policyCell(
        field({ field: 'explicit', outcome: 'abstain', reason: 'explicit_unsupported' }),
        'unsupported',
      ),
    ).toEqual(none('Explicit content is never marked for you with this setting.'))
    expect(
      policyCell(
        field({
          current_value: 'Current title',
          desired_value: 'Current title',
          field: 'title_layout',
          outcome: 'no_op',
          reason: 'unchanged',
        }),
        'automatic',
      ),
    ).toEqual(none())
    // Sources that disagree leave the field to the owner.
    expect(
      policyCell(
        field({
          current_value: 'Current title',
          field: 'title_layout',
          outcome: 'abstain',
          reason: 'ambiguous_evidence',
        }),
        'automatic',
      ),
    ).toEqual({ effect: 'ask', value: 'The sources do not agree on one value.' })
    expect(
      policyCell(
        field({ field: 'genres', outcome: 'abstain', reason: 'target_work_contradicted' }),
        'automatic',
      ),
    ).toEqual(none('The sources suggest this value belongs to a different work.'))
    expect(
      policyCell(
        field({
          current_value: 'Current title',
          field: 'title_layout',
          outcome: 'abstain',
          reason: 'policy_ignored',
        }),
        'ignore',
      ),
    ).toEqual(none('Set to Ignore.'))
    expect(
      policyCell(
        field({
          current_value: 'Current title',
          field: 'explicit',
          outcome: 'abstain',
          reason: 'explicit_unsupported',
        }),
        'unsupported',
      ),
    ).toEqual(none('Explicit content is never marked for you with this setting.'))
    expect(
      policyImpactTotals([
        { caseId: 'work-a', changed: true },
        { caseId: 'work-a', changed: false },
        { caseId: 'work-a', changed: false },
        { caseId: 'work-b', changed: false },
      ]),
    ).toEqual({ changedWorks: 1, materialChanges: 1 })
  })

  it('keeps every supported closed Policy representation context-complete', () => {
    const expected = [
      'title_layout:preserve',
      'title_layout:title_and_subtitle',
      'contributor_names:preserve',
      'contributor_names:canonical',
      'language_rendering:display_name',
      'subtitle:fill_empty',
      'subtitle:corroborated_split',
      'subtitle:replace_descriptive',
      'date_precision:year',
      'date_precision:full_date',
      'publisher_preference:publisher',
      'publisher_preference:imprint',
      'isbn_form:preserve_exact',
      'isbn_form:prefer_isbn13',
      'series:primary_narrative',
      'series:source_order',
      'series:all_qualified',
      'series:add_qualified',
      'series:replace_qualified',
      'genres:fill_empty',
      'genres:add',
      'genres:replace',
      'tags:off',
      'tags:fill_empty',
      'tags:add',
      'tags:replace',
      'description_fill:safe_plain_text',
      'description_fill:replace_safe_plain_text',
      'description_fill:google_visible_text',
      'cover_order:fixed_source_order',
      'asin:preserve_exact',
      'asin:ebook_manifestation',
      'explicit:unsupported',
      'explicit:adult_product',
    ]
    expect(Object.keys(policyRepresentationGuidance).sort()).toEqual(expected.sort())
    for (const guidance of Object.values(policyRepresentationGuidance)) {
      expect(guidance.admitted).not.toHaveLength(0)
      expect(guidance.transformation).not.toHaveLength(0)
      expect(guidance.effect).not.toHaveLength(0)
      expect(guidance.fallback).not.toHaveLength(0)
    }
    for (const key of [
      'contributor_names:preserve',
      'contributor_names:canonical',
      'date_precision:year',
      'date_precision:full_date',
      'publisher_preference:publisher',
    ]) {
      const guidance = policyRepresentationGuidance[key]
      if (!guidance) throw new Error(`missing guidance: ${key}`)
      expect(guidance.admitted).toContain('already-produced exact candidate')
      expect(guidance.transformation).toContain('without')
      expect(guidance.effect).toMatch(/^Automatic writes only the selected /)
      if (key === 'date_precision:year') {
        expect(guidance.fallback).toBe(
          "audiobooks need a confirmed original publication date. A recording's release date is not used",
        )
      } else if (key === 'date_precision:full_date') {
        expect(guidance.fallback).toBe(
          'audiobooks keep the year, month or day precision the source gives. No date is invented',
        )
      } else {
        expect(guidance.fallback).toBe('makes no change when Automatic cannot decide')
      }
    }
    const language = policyRepresentationGuidance['language_rendering:display_name']
    if (!language) throw new Error('missing language guidance')
    expect(language.admitted).toContain('already-produced exact candidate')
    expect(language.transformation).toBe(
      'writes the display name of the language the evidence names, such as English; keeps a current value already spelled that way',
    )
    expect(language.effect).toBe(
      "Automatic writes the language's name into an empty or unknown field, and respells the same language written another way",
    )
    expect(language.fallback).toBe(
      'keeps the current form when a language has no name, and never writes a different or unknown language',
    )
    expect(policyRepresentationGuidance['language_rendering:preserve']).toBeUndefined()
    expect(policyRepresentationGuidance['date_precision:year']?.effect).toBe(
      'Automatic writes only the selected publication date or year',
    )
    expect(policyRepresentationGuidance['date_precision:full_date']?.effect).toBe(
      'Automatic writes only the selected publication date or year',
    )
    expect(policyRepresentationGuidance['publisher_preference:imprint']?.effect).toContain(
      'Automatic does not write an imprint',
    )
    // The owner reads plain words: none of the audit vocabulary the guidance
    // used to speak in reaches a consequence (#1286).
    for (const guidance of Object.values(policyRepresentationGuidance)) {
      expect(`${guidance.effect}. ${guidance.fallback}`).not.toMatch(
        /abstain|qualif|admitted|non-authoritative|projected|destination value|retained|incumbent|corroborat|safely/iu,
      )
    }
  })
})

it.each([
  ['source_value_absent', 'No source supplied an ISBN.'],
  ['source_unavailable', 'The sources could not be read.'],
  ['source_value_malformed_or_incomplete', 'The sources give an unreadable or incomplete ISBN.'],
  ['source_values_conflict', 'The sources give different ISBNs.'],
  [
    'source_value_insufficient_association',
    'The sources do not tie this ISBN closely enough to this recording.',
  ],
])('keeps the specific ISBN refusal visible: %s', (reason, explanation) => {
  expect(
    policyCell(field({ field: 'isbn_form', outcome: 'abstain', reason }), 'automatic'),
  ).toEqual({ effect: 'none', value: 'No change', note: explanation })
})

describe('ebook synopsis refusals', () => {
  it.each([
    [
      'description_quality_floor_not_met',
      'No source description meets the rules for choosing one for you.',
      'none',
    ],
    [
      'target_work_applicability_unproved',
      'do not show that this value describes this work',
      'none',
    ],
    // Sources that disagree leave the field to the owner: the sentence alone.
    ['ambiguous_evidence', 'do not agree on one value', 'ask'],
  ])('explains %s without claiming no retained source', (reason, text, effect) => {
    const cell = policyCell(
      field({ field: 'description_fill', outcome: 'abstain', reason }),
      'automatic',
    )
    const said = cell.note ?? cell.value
    expect(said).toContain(text)
    expect(cell.effect).toBe(effect)
    expect(said).not.toContain('explicit choice')
  })
})

it.each([
  ['manifestation_verification_required', 'The sources do not confirm this ISBN for this edition.'],
  ['series_provider_failure_work', 'A title and author lookup did not return a usable answer.'],
  [
    'target_work_applicability_unproved',
    'The sources do not show that this value describes this work.',
  ],
])('preserves specific empty-value refusal before equality: %s', (reason, detail) => {
  expect(
    policyCell(field({ field: 'isbn_form', outcome: 'abstain', reason }), 'automatic'),
  ).toEqual({ effect: 'none', value: 'No change', note: detail })
})
