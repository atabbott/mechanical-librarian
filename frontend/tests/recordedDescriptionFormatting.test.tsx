import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import type { CaseDetailView } from '../src/api/http'
import { RecordedOutcome } from '../src/routes/FieldDecisionOutcome'
import descriptionAdviceFixture from './descriptionAdviceFixture.json'
import { readCaseDetail } from './fixtures'

// A recorded description shows the formatted copy it is written with, as its
// choice card did, while names and sentences keep the visible text (#1995).
type Field = CaseDetailView['fields'][number]

const describedField = (): Field => {
  const field = readCaseDetail(descriptionAdviceFixture).fields.find(
    (item) => item.destination === 'description',
  )
  if (!field) throw new Error('description fixture requires its field')
  return field
}

const decision = (field: Field, outcome: string, value: string) => ({
  decision_id: 'recorded-description',
  destination: 'description' as const,
  occurred_at: 1,
  outcome,
  proposal_id: field.proposalId,
  review_case_id: 'case-1',
  selected_value: { kind: 'text' as const, value },
  target_fingerprint: field.targetFingerprint,
})

const shown = (field: Field) => {
  render(
    <RecordedOutcome
      field={field}
      mediaKind="audiobook"
      expanded={false}
      toggle={() => undefined}
    />,
  )
  const region = screen.getByRole('region', { name: 'Description recorded outcome' })
  return region.querySelector('.description-formatted')?.innerHTML ?? null
}

describe('a recorded description', () => {
  it('shows the chosen advice’s formatted copy', () => {
    const field = describedField()
    const advice = field.descriptionAdvice
    if (!advice?.candidate) throw new Error('description fixture requires its candidate')
    const copy = '<p>An <b>authored</b> line.</p><ul><li>One</li></ul>'
    const visible = 'An authored line. • One'
    field.humanDecision = decision(field, 'accept', visible) as Field['humanDecision']
    field.descriptionAdvice = {
      ...advice,
      selected_advice: {
        candidate: {
          ...advice.candidate,
          output: { ...advice.candidate.output, description: copy },
        },
        binding: advice.binding,
        current: true,
        formatted: copy,
        visible_text: visible,
      },
      selection: {
        decision_id: 'recorded-description',
        candidate_id: advice.candidate.candidate_id,
        binding: advice.binding,
        origin: 'human' as const,
        resolved_policy_id: `resolved_policy_${'b'.repeat(64)}`,
        qualification_manifest_sha256: 'c'.repeat(64),
        idempotency_digest: 'd'.repeat(64),
        occurred_at: 10,
        destination: 'description' as const,
        outcome: 'accept' as const,
        application: 'guarded' as const,
      },
    }
    expect(shown(field)).toBe('<p>An <strong>authored</strong> line.</p><ul><li>One</li></ul>')
  })

  it('shows a chosen proposed value’s formatted copy', () => {
    const field = describedField()
    const proposed = field.proposedValues[0]
    if (proposed?.kind !== 'text' || typeof proposed.value !== 'string')
      throw new Error('description fixture requires text')
    field.proposedFormatted = [`<p><i>${proposed.value}</i></p>`]
    field.humanDecision = decision(field, 'accept', proposed.value) as Field['humanDecision']
    expect(shown(field)).toBe(`<p><em>${proposed.value}</em></p>`)
  })

  it('shows a kept value’s formatted copy', () => {
    const field = describedField()
    field.currentFormatted = `<p><b>${field.currentValue}</b></p>`
    field.humanDecision = decision(
      field,
      'keep_current',
      field.currentValue,
    ) as Field['humanDecision']
    expect(shown(field)).toBe(`<p><strong>${field.currentValue}</strong></p>`)
  })
})
