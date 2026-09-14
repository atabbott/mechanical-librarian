import { describe, expect, it } from 'vitest'

import {
  policyImpactDisplay,
  policyImpactTotals,
  policyRepresentationGuidance,
} from '../src/routes/PolicyPage'

describe('policy impact projections', () => {
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
        reason: 'no_supported_evidence',
        target_state: 'missing',
      }),
    ).toMatchObject({
      changed: false,
      title: 'No change',
      detail: 'remains empty — no supported value found',
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
      detail: 'empty remains — this policy leaves the explicit-content flag unchanged',
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
      detail: 'Current title remains — evidence is ambiguous',
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
      title: 'No supported value found',
      detail: 'remains empty — the retained provider evidence names a different work',
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
      detail: 'Current title remains — this policy leaves the explicit-content flag unchanged',
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
      'language_rendering:preserve',
      'language_rendering:display_name',
      'date_precision:year',
      'date_precision:full_date',
      'publisher_preference:publisher',
    ]) {
      const guidance = policyRepresentationGuidance[key]
      if (!guidance) throw new Error(`missing guidance: ${key}`)
      expect(guidance.admitted).toContain('already-produced exact candidate')
      expect(guidance.transformation).toContain('without')
      expect(guidance.effect).toContain('only that exact selected')
      if (key === 'date_precision:year') {
        expect(guidance.fallback).toBe(
          'requires a qualified original-work date for audiobooks; recording release dates do not qualify',
        )
      } else if (key === 'date_precision:full_date') {
        expect(guidance.fallback).toBe(
          'audiobooks retain the supplied year, month or day precision without inventing a date; the operation must be qualified',
        )
      } else {
        expect(guidance.fallback).toContain('no decision is qualified')
      }
    }
    expect(policyRepresentationGuidance['date_precision:year']?.effect).toContain(
      'Publication date or Publication year destination value',
    )
    expect(policyRepresentationGuidance['date_precision:full_date']?.effect).toContain(
      'Publication date or Publication year destination value',
    )
    expect(policyRepresentationGuidance['publisher_preference:imprint']?.effect).toContain(
      'no Automatic decision is qualified',
    )
  })
})

it.each([
  ['source_value_absent', 'the source supplied no ISBN'],
  ['source_unavailable', 'the ISBN source could not be reached'],
  ['source_value_malformed_or_incomplete', 'source ISBN data is malformed or incomplete'],
  ['source_values_conflict', 'sources supply competing ISBNs'],
  [
    'source_value_insufficient_association',
    'the supplied ISBN is not sufficiently linked to this recording',
  ],
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
  ).toMatchObject({ changed: false, title: 'No change', detail: `remains empty — ${explanation}` })
})
