import { describe, expect, it } from 'vitest'

import {
  policyImpactDisplay,
  policyImpactTotals,
  policyRepresentationGuidance,
} from '../src/routes/PolicyPage'

describe('policy impact projections', () => {
  it('labels both associated values in a paired subtitle preview', () => {
    expect(
      policyImpactDisplay({
        current_value: null,
        desired_value: { title: 'Synthetic Journey', subtitle: 'A Tale of Courage' },
        field: 'subtitle',
        outcome: 'projected',
        reason: null,
        target_state: 'missing',
      }).detail,
    ).toBe('fills the empty field with Title: Synthetic Journey; Subtitle: A Tale of Courage')
  })

  it('reads an Ask me change as what the review would propose (#1286)', () => {
    const field = {
      current_value: null,
      desired_value: 'A bounded subtitle',
      field: 'subtitle',
      outcome: 'projected',
      reason: null,
      target_state: 'missing',
    } as const
    expect(policyImpactDisplay(field, true).detail).toBe(
      'would propose filling the empty field with A bounded subtitle',
    )
    expect(
      policyImpactDisplay({ ...field, current_value: 'Old', desired_value: null }, true).detail,
    ).toBe('would propose clearing Old')
    expect(
      policyImpactDisplay({ ...field, current_value: 'Old', desired_value: 'New' }, true).detail,
    ).toBe('would propose Old → New')
    // A language value reads by its name.
    expect(
      policyImpactDisplay({
        ...field,
        field: 'language_rendering',
        current_value: 'en',
        desired_value: 'English',
      }).detail,
    ).toBe('English (en) → English')
  })

  it('projects bounded policy impact language without promoting transport enums', () => {
    expect(
      policyImpactDisplay({
        current_value: 'Old title',
        desired_value: 'New title',
        field: 'title_layout',
        outcome: 'projected',
        reason: null,
        target_state: 'value',
      }),
    ).toMatchObject({ changed: true, title: 'Change', detail: 'Old title → New title' })
    expect(
      policyImpactDisplay({
        current_value: null,
        desired_value: 'A bounded subtitle',
        field: 'subtitle',
        outcome: 'projected',
        reason: null,
        target_state: 'missing',
      }),
    ).toMatchObject({
      changed: true,
      title: 'Change',
      detail: 'fills the empty field with A bounded subtitle',
    })
    expect(
      policyImpactDisplay({
        current_value: null,
        desired_value: '9780306406157',
        field: 'isbn_form',
        outcome: 'projected',
        reason: 'verified_manifestation_isbn',
        target_state: 'missing',
      }),
    ).toMatchObject({
      changed: true,
      title: 'Change',
      detail:
        'fills the empty field with 9780306406157 because deterministic identity evidence binds it to this file’s work',
    })
    expect(
      policyImpactDisplay({
        current_value: 'Old subtitle',
        desired_value: null,
        field: 'subtitle',
        outcome: 'projected',
        reason: null,
        target_state: 'missing',
      }),
    ).toMatchObject({ changed: true, title: 'Change', detail: 'clears Old subtitle' })
    expect(
      policyImpactDisplay({
        current_value: null,
        desired_value: '',
        field: 'subtitle',
        outcome: 'projected',
        reason: null,
        target_state: 'missing',
      }),
    ).toMatchObject({
      changed: false,
      title: 'No change',
      detail: 'remains empty — no supported value found',
    })
    expect(
      policyImpactDisplay({
        current_value: 'Current title',
        desired_value: 'Current title',
        field: 'title_layout',
        outcome: 'projected',
        reason: null,
        target_state: 'value',
      }),
    ).toMatchObject({
      changed: false,
      title: 'No change',
      detail: 'remains Current title — the exact current value is preserved',
    })
    expect(
      policyImpactDisplay({
        current_value: null,
        desired_value: null,
        field: 'subtitle',
        outcome: 'abstain',
        reason: 'unrecognized_retained_reason',
        target_state: 'missing',
      }),
    ).toMatchObject({
      changed: false,
      title: 'No change',
      detail:
        'The preview did not select an automatic action; no more specific reason is available.',
    })
    expect(
      policyImpactDisplay({
        current_value: null,
        desired_value: null,
        field: 'title_layout',
        outcome: 'no_op',
        reason: 'target_state_unusable',
        target_state: 'malformed',
      }),
    ).toMatchObject({
      changed: false,
      title: 'Needs review',
      detail: 'Current value is unavailable or unreadable — inspect the source before deciding',
    })
    expect(
      policyImpactDisplay({
        current_value: null,
        desired_value: null,
        field: 'tags',
        outcome: 'abstain',
        reason: 'policy_ignored',
        target_state: 'missing',
      }),
    ).toMatchObject({
      changed: false,
      title: 'No change',
      detail: 'remains empty — this policy leaves the field unchanged',
    })
    expect(
      policyImpactDisplay({
        current_value: null,
        desired_value: null,
        field: 'explicit',
        outcome: 'abstain',
        reason: 'explicit_unsupported',
        target_state: 'missing',
      }),
    ).toMatchObject({
      changed: false,
      title: 'No change',
      detail: 'Automatic explicit-content handling is unavailable for this option.',
    })
    expect(
      policyImpactDisplay({
        current_value: 'Current title',
        desired_value: 'Current title',
        field: 'title_layout',
        outcome: 'no_op',
        reason: 'unchanged',
        target_state: 'value',
      }),
    ).toMatchObject({
      changed: false,
      title: 'No change',
      detail: 'remains Current title — the exact current value is preserved',
    })
    expect(
      policyImpactDisplay({
        current_value: 'Current title',
        desired_value: null,
        field: 'title_layout',
        outcome: 'abstain',
        reason: 'ambiguous_evidence',
        target_state: 'value',
      }),
    ).toMatchObject({
      changed: false,
      title: 'Needs review',
      detail: 'The retained evidence does not select one value for automatic handling.',
    })
    expect(
      policyImpactDisplay({
        current_value: null,
        desired_value: null,
        field: 'genres',
        outcome: 'abstain',
        reason: 'target_work_contradicted',
        target_state: 'empty',
      }),
    ).toMatchObject({
      changed: false,
      title: 'No change',
      detail: 'The retained evidence contradicts applicability to this work.',
    })
    expect(
      policyImpactDisplay({
        current_value: 'Current title',
        desired_value: null,
        field: 'title_layout',
        outcome: 'abstain',
        reason: 'policy_ignored',
        target_state: 'value',
      }),
    ).toMatchObject({
      changed: false,
      title: 'No change',
      detail: 'remains Current title — this policy leaves the field unchanged',
    })
    expect(
      policyImpactDisplay({
        current_value: 'Current title',
        desired_value: null,
        field: 'explicit',
        outcome: 'abstain',
        reason: 'explicit_unsupported',
        target_state: 'value',
      }),
    ).toMatchObject({
      changed: false,
      title: 'No change',
      detail: 'Automatic explicit-content handling is unavailable for this option.',
    })
    expect(
      policyImpactTotals([
        {
          caseId: 'work-a',
          impact: policyImpactDisplay({
            current_value: 'Old',
            desired_value: 'New',
            field: 'title_layout',
            outcome: 'projected',
            reason: null,
            target_state: 'value',
          }),
        },
        {
          caseId: 'work-a',
          impact: policyImpactDisplay({
            current_value: 'Current',
            desired_value: 'Current',
            field: 'subtitle',
            outcome: 'no_op',
            reason: 'target_preserved',
            target_state: 'value',
          }),
        },
        {
          caseId: 'work-a',
          impact: policyImpactDisplay({
            current_value: 'Current',
            desired_value: null,
            field: 'tags',
            outcome: 'abstain',
            reason: 'policy_ignored',
            target_state: 'value',
          }),
        },
        {
          caseId: 'work-b',
          impact: policyImpactDisplay({
            current_value: 'Current',
            desired_value: null,
            field: 'title_layout',
            outcome: 'abstain',
            reason: 'ambiguous_evidence',
            target_state: 'value',
          }),
        },
      ]),
    ).toEqual({ changedWorks: 1, materialChanges: 1 })
  })

  it('keeps every supported closed Policy representation context-complete', () => {
    const expected = [
      'title_layout:preserve',
      'title_layout:title_and_subtitle',
      'contributor_names:preserve',
      'contributor_names:canonical',
      'language_rendering:preserve',
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
      'cover_order:replace_if_strictly_better',
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
          "audiobooks need a confirmed original publication date; a recording's release date is not used",
        )
      } else if (key === 'date_precision:full_date') {
        expect(guidance.fallback).toBe(
          'audiobooks retain the supplied year, month or day precision without inventing a date; Automatic must support the change',
        )
      } else {
        expect(guidance.fallback).toBe('makes no change when Automatic cannot decide safely')
      }
    }
    for (const [option, preferred, fallback] of [
      ['preserve', 'an observed code', 'uses another recorded form when no code is present'],
      ['display_name', 'an observed English name', 'uses a recorded code when no name is present'],
    ] as const) {
      const guidance = policyRepresentationGuidance[`language_rendering:${option}`]
      if (!guidance) throw new Error(`missing language guidance: ${option}`)
      expect(guidance.admitted).toContain('already-produced exact candidate')
      expect(guidance.transformation).toBe(
        `keeps an equivalent current value; when filling, prefers ${preferred}`,
      )
      expect(guidance.effect).toBe('Automatic writes only a form present in the selected evidence')
      expect(guidance.fallback).toBe(`${fallback}; an undetermined language is never written`)
    }
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
      expect(`${guidance.effect}; ${guidance.fallback}`).not.toMatch(
        /abstain|qualif|admitted|non-authoritative|projected|destination value/iu,
      )
    }
  })
})

it.each([
  ['source_value_absent', 'No ISBN value was supplied.'],
  ['source_unavailable', 'Source evidence is unavailable.'],
  ['source_value_malformed_or_incomplete', 'ISBN evidence is malformed or incomplete.'],
  ['source_values_conflict', 'Conflicting ISBN values remain.'],
  ['source_value_insufficient_association', 'Association with this recording is insufficient.'],
])('keeps the specific ISBN refusal visible: %s', (reason, explanation) => {
  expect(
    policyImpactDisplay({
      current_value: null,
      desired_value: null,
      field: 'isbn_form',
      outcome: 'abstain',
      reason,
      target_state: 'missing',
    }),
  ).toMatchObject({ changed: false, title: 'No change', detail: explanation })
})

describe('ebook synopsis refusals', () => {
  it.each([
    [
      'description_quality_floor_not_met',
      'Retained source text does not meet the automatic synopsis rules.',
      'No change',
    ],
    [
      'target_work_applicability_unproved',
      'does not establish that this value describes this work',
      'No change',
    ],
    ['ambiguous_evidence', 'does not select one value for automatic handling', 'Needs review'],
  ])('explains %s without claiming no retained source', (reason, text, title) => {
    const display = policyImpactDisplay({
      field: 'description_fill',
      outcome: 'abstain',
      reason,
      current_value: null,
      desired_value: null,
      target_state: 'missing',
    })
    expect(display.detail).toContain(text)
    expect(display.title).toBe(title)
    expect(display.detail).not.toContain('explicit choice')
    expect(display.changed).toBe(false)
  })
})

it.each([
  [
    'manifestation_verification_required',
    'The retained evidence does not verify the ISBN for this edition.',
  ],
  ['series_provider_failure_work', 'A contributing work lookup did not supply usable evidence.'],
  [
    'target_work_applicability_unproved',
    'The retained evidence does not establish that this value describes this work.',
  ],
])('preserves specific empty-value refusal before equality: %s', (reason, detail) => {
  expect(
    policyImpactDisplay({
      current_value: null,
      desired_value: null,
      field: 'isbn_form',
      outcome: 'abstain',
      reason,
      target_state: 'missing',
    }),
  ).toMatchObject({ changed: false, detail })
})
