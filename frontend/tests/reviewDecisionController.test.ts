import { describe, expect, it } from 'vitest'

import { AmbiguousMutationError, ApiError } from '../src/api/http'
import {
  actionable,
  choiceAvailable,
  classifyDecisionFailure,
  retryableDecisionFailure,
} from '../src/reviewDecisionController'
import { detail } from './fixtures'

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
