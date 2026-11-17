import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, renderHook, waitFor } from '@testing-library/react'
import { createElement } from 'react'
import type { ReactNode } from 'react'
import { describe, expect, it, vi } from 'vitest'

import {
  AmbiguousMutationError,
  ApiError,
  CsrfRenewedError,
  UncertainMutationError,
} from '../src/api/http'
import type { CaseDetailView, DecisionCommand, ReviewHttp } from '../src/api/http'
import {
  actionable,
  choiceAvailable,
  classifyDecisionFailure,
  offerChangedCopy,
  ownerRefusalCopy,
  retryableDecisionFailure,
  useReviewDecisionController,
} from '../src/reviewDecisionController'
import { detailQuery } from '../src/router'
import type * as Router from '../src/router'
import { createHttp, detail, readCaseDetail, recordedAcknowledgement } from './fixtures'
import descriptionAdviceFixture from './descriptionAdviceFixture.json'

const context = vi.hoisted((): { value: unknown } => ({ value: null }))
vi.mock('../src/router', async (original) => ({
  ...(await original<typeof Router>()),
  useReviewContext: () => context.value,
}))

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

// One press of a value's pin (#1346, ADR-0085 Amendment 2): the value command,
// then the pin, and the owner stays on the field.
describe('a pin after a value command', () => {
  type Field = CaseDetailView['fields'][number]
  const title = detail.fields[0]
  if (!title) throw new Error('decision fixture requires one field')
  const offered: Field = { ...title, pinControl: { pin: 'available', unpin: false } }
  const pinned = (state: 'in_force' | 'waiting'): Field['pin'] => ({
    state,
    value: { destination: 'title', state: 'value', value: 'Old title' } as never,
  })
  const command = (outcome: DecisionCommand['outcome'], key: string): DecisionCommand => ({
    destination: 'title',
    outcome,
    proposalId: offered.proposalId,
    selectedValue: outcome === 'accept' ? { kind: 'text', value: 'New title' } : null,
    targetFingerprint: offered.targetFingerprint,
    offerDigest: offered.offerDigest,
    idempotencyKey: key,
  })
  // A store of one field: each command leaves the case the next read returns.
  const store = (
    after: (field: Field, sent: DecisionCommand) => Field,
    refuse: (sent: DecisionCommand, attempt: number) => unknown = () => undefined,
  ) => {
    const state = { field: offered, current: true, unavailable: false }
    const record = vi.fn<ReviewHttp['recordDecision']>(async (caseId, sent) => {
      const refusal = await refuse(sent, record.mock.calls.length)
      if (refusal !== undefined) throw refusal as Error
      state.field = after(state.field, sent)
      const acknowledgement = recordedAcknowledgement(
        { ...detail, id: caseId },
        {
          invalidation_sequences: [],
          schema_version: 'human-decision-ack-v2' as const,
          actor: 'owner',
          application: 'apply',
          application_reason: null,
          decision_id: `decision-${sent.idempotencyKey}`,
          destination: sent.destination,
          occurred_at: 2,
          outcome: sent.outcome,
          proposal_id: sent.proposalId,
          review_case_id: caseId,
          selected_value: sent.selectedValue ?? null,
          target_fingerprint: sent.targetFingerprint,
        },
      )
      if (state.unavailable) return { ...acknowledgement, confirmation: { status: 'unavailable' } }
      return {
        ...acknowledgement,
        confirmation: {
          status: 'confirmed',
          case: { ...detail, id: caseId, current: state.current, fields: [state.field] },
        },
      }
    })
    const http: ReviewHttp = {
      ...createHttp(),
      recordDecision: record,
      getCaseDetail: vi.fn(async (id: string) => ({ ...detail, id, fields: [state.field] })),
    }
    return { http, record, state }
  }
  const ordinary = (field: Field, sent: DecisionCommand): Field =>
    sent.outcome === 'pin'
      ? { ...field, pin: pinned('waiting'), pinControl: { pin: 'available', unpin: true } }
      : sent.outcome === 'unpin'
        ? { ...field, pin: null, pinControl: { pin: 'available', unpin: false } }
        : { ...field, needsAttention: false }
  const mount = async (
    http: ReviewHttp,
    caseId = 'case-1',
    shown: { fields: Field[]; active: () => boolean } = { fields: [offered], active: () => true },
  ) => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    context.value = { http, queryClient }
    // The page has read each case it shows before a command can leave it.
    for (const id of ['case-1', 'case-2']) await queryClient.query(detailQuery(http, id))
    const moves = { onAdvance: vi.fn(), onComplete: vi.fn(), onStale: vi.fn() }
    const hook = renderHook(
      ({ id }: { id: string }) =>
        useReviewDecisionController({
          reviewCaseId: id,
          ...shown,
          ...moves,
        }),
      {
        initialProps: { id: caseId },
        wrapper: ({ children }: { children: ReactNode }) =>
          createElement(QueryClientProvider, { client: queryClient }, children),
      },
    )
    return { hook, moves }
  }
  const press = (
    hook: Awaited<ReturnType<typeof mount>>['hook'],
    outcome: DecisionCommand['outcome'],
    key = 'value-key',
  ) =>
    act(() => {
      hook.result.current.submit(command(outcome, key))
      if (outcome === 'accept' || outcome === 'keep_current') hook.result.current.pinAfter(key)
    })
  const sent = (record: ReturnType<typeof store>['record']) =>
    record.mock.calls.map(([, value]) => value)

  it('posts the value, then the pin, under two keys and one binding', async () => {
    const { http, record } = store(ordinary)
    const { hook, moves } = await mount(http)
    press(hook, 'accept')
    expect(hook.result.current.statusFor('title')).toBe('Pinning…')
    await waitFor(() => expect(hook.result.current.statusFor('title')).toBe('Pinned.'))
    const [value, pin] = sent(record)
    expect(record).toHaveBeenCalledTimes(2)
    expect(record.mock.calls.map(([id]) => id)).toEqual(['case-1', 'case-1'])
    expect(value).toMatchObject({ outcome: 'accept', idempotencyKey: 'value-key' })
    expect(pin).toMatchObject({
      outcome: 'pin',
      selectedValue: null,
      destination: 'title',
      proposalId: offered.proposalId,
      targetFingerprint: offered.targetFingerprint,
      offerDigest: offered.offerDigest,
    })
    expect(pin?.idempotencyKey).not.toBe('value-key')
    expect(hook.result.current.alertFor('title')).toBe(false)
    // The owner stays on the field: nothing advances and nothing completes.
    expect(moves.onAdvance).not.toHaveBeenCalled()
    expect(moves.onComplete).not.toHaveBeenCalled()
  })

  it.each([
    ['pin', 'Pinning…', 'Pinned.'],
    ['unpin', 'Unpinning…', 'Unpinned.'],
  ] as const)('settles a lone %s in place', async (outcome, progress, done) => {
    const { http, record } = store(ordinary)
    const { hook, moves } = await mount(http)
    press(hook, outcome)
    expect(hook.result.current.statusFor('title')).toBe(progress)
    await waitFor(() => expect(hook.result.current.statusFor('title')).toBe(done))
    expect(sent(record).map((value) => value.outcome)).toEqual([outcome])
    expect(moves.onAdvance).not.toHaveBeenCalled()
    expect(moves.onComplete).not.toHaveBeenCalled()
  })

  it('registers no pin for a command the controller did not take', async () => {
    const { http, record } = store(ordinary)
    const { hook, moves } = await mount(http)
    act(() => {
      hook.result.current.submit(command('accept', 'value-key'))
      hook.result.current.pinAfter('another-key')
    })
    await waitFor(() =>
      expect(hook.result.current.statusFor('title')).toBe('Your choice is recorded.'),
    )
    expect(record).toHaveBeenCalledTimes(1)
    expect(moves.onComplete).toHaveBeenCalledTimes(1)
  })

  it('says the choice stands when the refreshed field offers no pin', async () => {
    const { http, record } = store((field) => ({
      ...field,
      needsAttention: false,
      pinControl: { pin: 'change_waiting', unpin: false },
    }))
    const { hook, moves } = await mount(http)
    press(hook, 'keep_current')
    await waitFor(() =>
      expect(hook.result.current.statusFor('title')).toBe(
        'Your choice is recorded, but the pin was not.',
      ),
    )
    expect(hook.result.current.alertFor('title')).toBe(true)
    expect(sent(record).map((value) => value.outcome)).toEqual(['keep_current'])
    expect(hook.result.current.phase('title')?.phase).toBe('confirmed')
    expect(moves.onAdvance).not.toHaveBeenCalled()
    expect(moves.onComplete).not.toHaveBeenCalled()
  })

  it('sends no pin to another work the owner selected meanwhile', async () => {
    const gate: { open?: () => void } = {}
    const { http, record } = store(ordinary, async (value) => {
      // Refuses nothing: the value command only waits until the owner has moved.
      if (value.outcome === 'accept')
        await new Promise<void>((resolve) => {
          gate.open = resolve
        })
      return undefined
    })
    const { hook, moves } = await mount(http)
    press(hook, 'accept')
    await waitFor(() => expect(gate.open).toBeDefined())
    hook.rerender({ id: 'case-2' })
    await act(async () => {
      gate.open?.()
      // Give a wrongly sent pin every chance to leave.
      await new Promise((resolve) => setTimeout(resolve, 20))
    })
    expect(sent(record).map((value) => value.outcome)).toEqual(['accept'])
    expect(record.mock.calls.map(([id]) => id)).toEqual(['case-1'])
    // The work the choice was made on says so when the owner returns to it.
    hook.rerender({ id: 'case-1' })
    expect(hook.result.current.statusFor('title')).toBe(
      'Your choice is recorded, but the pin was not. Press the pin again.',
    )
    expect(moves.onComplete).not.toHaveBeenCalled()
  })

  it('keeps the choice and says so when the pin is refused', async () => {
    const { http, record } = store(ordinary, (value) =>
      value.outcome === 'pin' ? new ApiError(503, 'persistence') : undefined,
    )
    const { hook, moves } = await mount(http)
    press(hook, 'accept')
    await waitFor(() =>
      expect(hook.result.current.statusFor('title')).toBe(
        'Your choice is recorded, but the pin was not. Press the pin again.',
      ),
    )
    expect(hook.result.current.alertFor('title')).toBe(true)
    expect(sent(record).map((value) => value.outcome)).toEqual(['accept', 'pin'])
    // A pin is never offered for the same-key retry: pressing it again sends a new one.
    expect(hook.result.current.canRetry).toBe(false)
    expect(hook.result.current.phase('title')).toBeNull()
    expect(moves.onAdvance).not.toHaveBeenCalled()
    expect(moves.onComplete).not.toHaveBeenCalled()
  })

  it('sends the pin after a retry of the value command', async () => {
    const { http, record } = store(ordinary, (value, attempt) =>
      value.outcome === 'accept' && attempt === 1 ? new ApiError(503, 'persistence') : undefined,
    )
    const { hook } = await mount(http)
    press(hook, 'accept')
    await waitFor(() => expect(hook.result.current.canRetry).toBe(true))
    expect(record).toHaveBeenCalledTimes(1)
    act(() => hook.result.current.retry())
    await waitFor(() => expect(hook.result.current.statusFor('title')).toBe('Pinned.'))
    expect(
      sent(record).map((value) => [value.outcome, value.idempotencyKey === 'value-key']),
    ).toEqual([
      ['accept', true],
      ['accept', true],
      ['pin', false],
    ])
  })

  it('drops the pin with a value command that is refused for good', async () => {
    const { http, record } = store(ordinary, (value) =>
      value.outcome === 'accept' ? new ApiError(422, 'invalid_selection') : undefined,
    )
    const { hook } = await mount(http)
    press(hook, 'accept')
    await waitFor(() =>
      expect(hook.result.current.statusFor('title')).toBe(
        'Your choice was not recorded. Choose again.',
      ),
    )
    expect(record).toHaveBeenCalledTimes(1)
    expect(hook.result.current.canRetry).toBe(false)
  })

  it('reads a failed pin or unpin from the refreshed case', async () => {
    // The store already holds the pin: the refusal of a repeat reads as done.
    const held = store(
      (field) => field,
      () => new ApiError(409, 'already_pinned'),
    )
    held.state.field = { ...offered, pin: pinned('in_force') }
    const first = await mount(held.http)
    press(first.hook, 'pin')
    await waitFor(() => expect(first.hook.result.current.statusFor('title')).toBe('Pinned.'))
    expect(first.hook.result.current.alertFor('title')).toBe(false)
    first.hook.unmount()

    const gone = store(
      (field) => field,
      () => new ApiError(409, 'not_pinned'),
    )
    const second = await mount(gone.http)
    press(second.hook, 'unpin')
    await waitFor(() => expect(second.hook.result.current.statusFor('title')).toBe('Unpinned.'))
    second.hook.unmount()

    for (const [outcome, field, words] of [
      ['pin', offered, 'The pin was not recorded. Press the pin again.'],
      [
        'pin',
        { ...offered, pinControl: { pin: 'change_waiting', unpin: false } },
        'The pin was not recorded.',
      ],
      // A changed pin is not the pin the press asked for.
      [
        'pin',
        { ...offered, pin: { ...pinned('in_force'), state: 'changed' } },
        'The pin was not recorded. Press the pin again.',
      ],
      [
        'unpin',
        { ...offered, pin: pinned('in_force') },
        'This field is still pinned. Press the pin again.',
      ],
    ] as const) {
      const refused = store(
        (value) => value,
        () => new AmbiguousMutationError(),
      )
      refused.state.field = field as Field
      const { hook, moves } = await mount(refused.http)
      press(hook, outcome)
      await waitFor(() => expect(hook.result.current.statusFor('title')).toBe(words))
      expect(hook.result.current.alertFor('title')).toBe(true)
      expect(hook.result.current.canRetry).toBe(false)
      expect(moves.onAdvance).not.toHaveBeenCalled()
      hook.unmount()
    }
  })

  it('hands a replaced case to the stale path with the pin that was not recorded', async () => {
    const { http, state } = store(ordinary)
    state.current = false
    const { hook, moves } = await mount(http)
    press(hook, 'accept')
    await waitFor(() => expect(moves.onStale).toHaveBeenCalledTimes(1))
    expect(moves.onStale.mock.calls[0]?.[0]).toMatchObject({ outcome: 'pin', destination: 'title' })
    expect(http.recordDecision).toHaveBeenCalledTimes(1)
  })

  it('hands a value command refused as stale to the stale path with its pin', async () => {
    const { http } = store(ordinary, () => new ApiError(409, 'stale'))
    const { hook, moves } = await mount(http)
    press(hook, 'accept')
    await waitFor(() => expect(moves.onStale).toHaveBeenCalledTimes(1))
    // The pin, not the choice, is what the owner pressed and is told was not recorded.
    expect(moves.onStale.mock.calls[0]?.[0]).toMatchObject({ outcome: 'pin', destination: 'title' })
    expect(http.recordDecision).toHaveBeenCalledTimes(1)
  })

  it('says the choice stands when its pin fails on a field that no longer takes a choice', async () => {
    const { http } = store(
      (field, value) =>
        value.outcome === 'accept' ? { ...field, needsAttention: false, editable: false } : field,
      (value) => (value.outcome === 'pin' ? new ApiError(503, 'persistence') : undefined),
    )
    const { hook } = await mount(http)
    press(hook, 'accept')
    await waitFor(() =>
      expect(hook.result.current.statusFor('title')).toBe(
        'Your choice is recorded, but the pin was not. Press the pin again.',
      ),
    )
    expect(hook.result.current.alertFor('title')).toBe(true)
  })

  it('names the pin that was not sent once a case that could not be reloaded is read again', async () => {
    const { http, record, state } = store(ordinary)
    state.unavailable = true
    const { hook } = await mount(http)
    press(hook, 'accept')
    await waitFor(() => expect(hook.result.current.reloadRequired).toBe(true))
    expect(record).toHaveBeenCalledTimes(1)
    expect(hook.result.current.statusFor('title')).toBe('')
    await act(async () => {
      await hook.result.current.settle()
    })
    expect(hook.result.current.statusFor('title')).toBe(
      'Your choice is recorded, but the pin was not. Press the pin again.',
    )
    expect(hook.result.current.alertFor('title')).toBe(true)
    // A plain choice on such a case names no pin after the reload.
    const plain = store(ordinary)
    plain.state.unavailable = true
    const second = await mount(plain.http)
    act(() => {
      second.hook.result.current.submit(command('accept', 'plain-key'))
    })
    await waitFor(() => expect(second.hook.result.current.reloadRequired).toBe(true))
    await act(async () => {
      await second.hook.result.current.settle()
    })
    expect(second.hook.result.current.statusFor('title')).toBe('')
  })

  const lockedAfterChoice = async () => {
    const { http, record, state } = store(ordinary)
    state.unavailable = true
    const { hook } = await mount(http)
    press(hook, 'accept')
    await waitFor(() => expect(hook.result.current.reloadRequired).toBe(true))
    state.unavailable = false
    return { hook, record }
  }
  const unsentWords = 'Your choice is recorded, but the pin was not. Press the pin again.'

  it('never names a pin that was not sent on another work, or after a later command', async () => {
    const { hook } = await lockedAfterChoice()
    // Another work is opened without Reload: its field of the same name is told nothing.
    hook.rerender({ id: 'case-2' })
    await act(async () => {
      await hook.result.current.settle()
    })
    expect(hook.result.current.statusFor('title')).toBe('')
    expect(hook.result.current.alertFor('title')).toBe(false)
    // Back on the first work, the pin is still named once it is read again.
    hook.rerender({ id: 'case-1' })
    await act(async () => {
      await hook.result.current.settle()
    })
    expect(hook.result.current.statusFor('title')).toBe(unsentWords)

    // A command taken since is the later moment: the earlier pin is not named over it.
    const later = await lockedAfterChoice()
    later.hook.rerender({ id: 'case-2' })
    act(() => {
      later.hook.result.current.submit(command('unpin', 'unpin-on-case-2'))
    })
    await waitFor(() => expect(later.hook.result.current.statusFor('title')).toBe('Unpinned.'))
    later.hook.rerender({ id: 'case-1' })
    await act(async () => {
      await later.hook.result.current.settle()
    })
    await waitFor(() => {
      later.hook.rerender({ id: 'case-1' })
      expect(later.hook.result.current.reloadRequired).toBe(false)
    })
    expect(later.hook.result.current.statusFor('title')).toBe('')
  })

  it('names the pin that was not sent when a later read lifts the lock without Reload', async () => {
    const { hook, record } = await lockedAfterChoice()
    const { http, queryClient } = context.value as { http: ReviewHttp; queryClient: QueryClient }
    // A read of the case in the background, not the Reload button.
    await act(async () => {
      await queryClient.query({ ...detailQuery(http, 'case-1'), staleTime: 0 })
    })
    hook.rerender({ id: 'case-1' })
    await waitFor(() => expect(hook.result.current.reloadRequired).toBe(false))
    expect(hook.result.current.statusFor('title')).toBe(unsentWords)
    expect(hook.result.current.alertFor('title')).toBe(true)
    // The owner pins and unpins the field: a later reload says nothing over that.
    press(hook, 'pin', 'pin-key')
    await waitFor(() => expect(hook.result.current.statusFor('title')).toBe('Pinned.'))
    press(hook, 'unpin', 'unpin-key')
    await waitFor(() => expect(hook.result.current.statusFor('title')).toBe('Unpinned.'))
    await act(async () => {
      await hook.result.current.settle()
    })
    expect(hook.result.current.statusFor('title')).toBe('Unpinned.')
    expect(hook.result.current.alertFor('title')).toBe(false)
    expect(sent(record).map((value) => value.outcome)).toEqual(['accept', 'pin', 'unpin'])
  })

  // A field in the No change panel has no tab: its pin never moves the owner.
  describe('on a field with nothing to decide', () => {
    const contextual: Field = { ...offered, inventoryRole: 'contextual' }
    const shown = { fields: [contextual], active: () => false }
    const pressPin = async (
      refuse?: Parameters<typeof store>[1],
      prepare?: (made: ReturnType<typeof store>) => void,
    ) => {
      const made = store(ordinary, refuse)
      made.state.field = contextual
      const { hook, moves } = await mount(made.http, 'case-1', shown)
      prepare?.(made)
      press(hook, 'pin', 'pin-key')
      return { hook, moves, ...made }
    }
    const still = (moves: Awaited<ReturnType<typeof mount>>['moves']) => {
      expect(moves.onAdvance).not.toHaveBeenCalled()
      expect(moves.onComplete).not.toHaveBeenCalled()
    }

    it('stays put when the pin is recorded', async () => {
      const { hook, moves } = await pressPin()
      await waitFor(() => expect(hook.result.current.statusFor('title')).toBe('Pinned.'))
      still(moves)
    })

    it.each([
      [new ApiError(503, 'persistence'), 'The pin was not recorded. Press the pin again.'],
      [
        new ApiError(409, 'offer_changed', 'digest_mismatch'),
        'Not recorded: Book matching changed what this field offers. Choose again.',
      ],
    ])('stays put when the pin fails with %s', async (error, words) => {
      const { hook, moves } = await pressPin(() => error)
      await waitFor(() => expect(hook.result.current.statusFor('title')).toBe(words))
      expect(hook.result.current.alertFor('title')).toBe(true)
      still(moves)
    })

    it('stays put when the work cannot be reloaded after the pin, recorded or not', async () => {
      const recorded = await pressPin(undefined, ({ state }) => {
        state.unavailable = true
      })
      await waitFor(() => expect(recorded.hook.result.current.reloadRequired).toBe(true))
      still(recorded.moves)
      const failed = await pressPin(
        () => new ApiError(503, 'persistence'),
        ({ http }) => {
          vi.mocked(http.getCaseDetail).mockRejectedValue(new ApiError(503, 'unavailable'))
        },
      )
      await waitFor(() => expect(failed.hook.result.current.reloadRequired).toBe(true))
      expect(failed.hook.result.current.messageFor('title')).toBe(
        'This work could not be reloaded after your choice. Choose Reload current case before choosing again.',
      )
      // The reloaded case says whether it stands; nothing stays recording to
      // keep the pins busy once Reload lifts the lock.
      expect(failed.hook.result.current.recording).toBe(false)
      still(failed.moves)
    })
  })

  it.each([
    [new ApiError(422, 'invalid_selection'), 'Your choice was not recorded. Choose again.'],
    [
      new ApiError(503, 'persistence'),
      'Your choice could not be confirmed. Choose Try the same decision again to send it once more.',
    ],
    [
      new ApiError(409, 'offer_changed', 'digest_mismatch'),
      'Not recorded: Book matching changed what this field offers. Choose again.',
    ],
    [
      new ApiError(409, 'already_decided'),
      'This field was already decided. The page shows that decision.',
    ],
  ])('says every failure of a choice as an alert (%#)', async (error, words) => {
    const { http } = store(ordinary, () => error)
    const { hook } = await mount(http)
    act(() => {
      hook.result.current.submit(command('accept', 'value-key'))
    })
    await waitFor(() => expect(hook.result.current.statusFor('title')).toBe(words))
    expect(hook.result.current.alertFor('title')).toBe(true)
  })

  it('says a withdrawal refused over a pin in the owner’s words', () => {
    expect(ownerRefusalCopy(new ApiError(409, 'pinned'), offered)).toBe(
      'This field is pinned. Unpin it to withdraw the decision.',
    )
  })
})
