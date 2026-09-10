import { describe, expect, it } from 'vitest'

import { AmbiguousMutationError, ApiError, mapCaseDetail } from '../src/api/http'
import {
  actionable,
  choiceAvailable,
  classifyDecisionFailure,
  retryableDecisionFailure,
} from '../src/reviewDecisionController'
import { detail } from './fixtures'
import descriptionAdviceFixture from './descriptionAdviceFixture.json'

describe('review decision failure contract', () => {
  it('retries only the closed transient server outcomes', () => {
    for (const code of ['persistence', 'unavailable']) {
      const error = new ApiError(503, code)
      expect(classifyDecisionFailure(error)).toBe('retryable')
      expect(retryableDecisionFailure(error)).toBe(true)
    }
    for (const error of [
      new ApiError(422, 'unsupported'),
      new ApiError(422, 'invalid_selection'),
      new ApiError(409, 'idempotency_conflict'),
      new ApiError(503, 'other'),
    ]) {
      expect(classifyDecisionFailure(error)).toBe('permanent')
      expect(retryableDecisionFailure(error)).toBe(false)
    }
  })

  it('keeps stale and uncertain responses on their reconciliation paths', () => {
    expect(classifyDecisionFailure(new ApiError(409, 'stale'))).toBe('stale')
    expect(classifyDecisionFailure(new AmbiguousMutationError())).toBe('uncertain')
  })

  it('does not treat an attention context as a decision without exact actions', () => {
    const field = detail.fields[0]
    if (!field) throw new Error('decision fixture requires one field')
    expect(actionable(field)).toBe(true)
    expect(actionable({ ...field, decisionActions: [], needsAttention: true })).toBe(false)
    expect(
      actionable({
        ...field,
        destination: 'cover',
        decisionActions: [],
        editable: true,
        needsAttention: true,
        coverConsideration: null,
      }),
    ).toBe(true)
    expect(choiceAvailable({ ...field, needsAttention: false })).toBe(true)
    expect(choiceAvailable({ ...field, editable: false, needsAttention: false })).toBe(false)
  })
})

it('keeps a current authored choice available without treating it as a provider action', () => {
  const field = mapCaseDetail(descriptionAdviceFixture).fields.find(
    (item) => item.destination === 'description',
  )!
  const authored = { ...field, decisionActions: [], needsAttention: false }
  expect(choiceAvailable(authored)).toBe(true)
  expect(actionable(authored)).toBe(false)
  expect(
    choiceAvailable({
      ...authored,
      descriptionAdvice: { ...authored.descriptionAdvice!, current: false },
    }),
  ).toBe(false)
  expect(choiceAvailable({ ...authored, editable: false })).toBe(false)
})
