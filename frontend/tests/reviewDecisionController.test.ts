import { describe, expect, it } from 'vitest'

import {
  AmbiguousMutationError,
  ApiError,
  CsrfRenewedError,
  UncertainMutationError,
} from '../src/api/http'
import {
  actionable,
  choiceAvailable,
  classifyDecisionFailure,
  offerChangedCopy,
  retryableDecisionFailure,
} from '../src/reviewDecisionController'
import { detail, readCaseDetail } from './fixtures'
import descriptionAdviceFixture from './descriptionAdviceFixture.json'

describe('review decision failure contract', () => {
  it('retries any server failure the call may have followed', () => {
    for (const error of [
      new ApiError(503, 'persistence'),
      new ApiError(503, 'unavailable'),
      new ApiError(503, 'other'),
      new ApiError(500, 'invalid_error_response'),
      new CsrfRenewedError(),
    ]) {
      expect(classifyDecisionFailure(error)).toBe('retryable')
      expect(retryableDecisionFailure(error)).toBe(true)
    }
    for (const error of [
      new ApiError(403, 'csrf_renewed'),
      new ApiError(403, 'origin'),
      new ApiError(422, 'unsupported'),
      new ApiError(422, 'invalid_selection'),
      new ApiError(409, 'idempotency_conflict'),
      new ApiError(503, 'react_assets_unavailable'),
    ]) {
      expect(classifyDecisionFailure(error)).toBe('permanent')
      expect(retryableDecisionFailure(error)).toBe(false)
    }
  })

  it('keeps stale and uncertain responses on their reconciliation paths', () => {
    expect(classifyDecisionFailure(new ApiError(409, 'stale'))).toBe('stale')
    expect(classifyDecisionFailure(new AmbiguousMutationError())).toBe('uncertain')
  })

  it('reports an acknowledgement the browser cannot read as uncertain, never retryable', () => {
    // A same-key Retry would replay the same unreadable acknowledgement (#1688).
    for (const [reason, at] of [
      ['invariant', 'invalid_decision_confirmation'],
      ['required', '/decision_id'],
    ] as const) {
      const unreadable = new UncertainMutationError({
        operation: 'POST /api/v1/review-cases/{review_case_id}/decisions',
        responseStatus: 200,
        reason,
        at,
      })
      expect(classifyDecisionFailure(unreadable)).toBe('uncertain')
      expect(retryableDecisionFailure(unreadable)).toBe(false)
    }
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
  const field = readCaseDetail(descriptionAdviceFixture).fields.find(
    (item) => item.destination === 'description',
  )
  if (!field?.descriptionAdvice) throw new Error('description fixture requires advice')
  const authored = { ...field, decisionActions: [], needsAttention: false }
  expect(choiceAvailable(authored)).toBe(true)
  expect(actionable(authored)).toBe(false)
  expect(
    choiceAvailable({
      ...authored,
      descriptionAdvice: { ...field.descriptionAdvice, current: false },
    }),
  ).toBe(false)
  expect(choiceAvailable({ ...authored, editable: false })).toBe(false)
})

it('answers a choice refused because Book matching changed without a generic refusal (#1355)', () => {
  expect(offerChangedCopy(new ApiError(409, 'offer_changed', 'digest_mismatch'))).toBe(
    'Not recorded: Book matching changed what this field offers. Choose again.',
  )
  // A work waiting for its check says so on its case line; the field adds nothing.
  expect(offerChangedCopy(new ApiError(409, 'offer_changed', 'unreconciled_case'))).toBe('')
  expect(offerChangedCopy(new ApiError(409, 'stale'))).toBeUndefined()
  expect(offerChangedCopy(new Error('offer_changed'))).toBeUndefined()
  // Neither reason is retried: the same choice would be refused again.
  expect(classifyDecisionFailure(new ApiError(409, 'offer_changed', 'digest_mismatch'))).toBe(
    'permanent',
  )
})
