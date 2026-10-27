import { act, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { expect, it, vi } from 'vitest'
import type { CaseDetailView, DecisionOutcome, LibraryView, ReviewHttp } from '../src/api/http'
import { hasBackgroundDebt } from '../src/queryWork'
import { createHttp, createLiveStream, detail, library, renderRoute } from './fixtures'
it('confirms an off-page decision despite a pre-ack read and repeated case/resync frames', async () => {
  const field = detail.fields[0]
  if (!field) throw new Error('field required')
  const http = createHttp()
  const ack = await http.recordDecision('case-1', {
    destination: 'title',
    outcome: 'keep_current',
    proposalId: field.proposalId,
    selectedValue: null,
    targetFingerprint: field.targetFingerprint,
    idempotencyKey: 'diagnostic',
  })
  if (!('actor' in ack)) throw new Error('Title fixture requires a field decision acknowledgement')
  const settled: CaseDetailView = {
    ...detail,
    fields: [
      {
        ...field,
        needsAttention: false,
        humanDecision: { ...ack, outcome: ack.outcome as DecisionOutcome },
      },
    ],
  }
  let reads = 0
  let recorded = false
  let finishCommand: ((value: typeof ack) => void) | undefined
  let pendingSignal: AbortSignal | undefined
  let confirmSignal: AbortSignal | undefined
  let finishConfirmation: ((value: CaseDetailView) => void) | undefined
  http.getCaseDetail = vi.fn<ReviewHttp['getCaseDetail']>(async (_id, signal) => {
    reads += 1
    if (reads === 2) {
      pendingSignal = signal
      return new Promise(() => undefined)
    }
    if (reads === 3) {
      confirmSignal = signal
      return new Promise((resolve) => {
        finishConfirmation = resolve
      })
    }
    return recorded ? settled : detail
  })
  http.recordDecision = vi.fn<ReviewHttp['recordDecision']>(
    () =>
      new Promise((resolve) => {
        finishCommand = resolve
      }),
  )
  http.getLibrary = vi.fn(async () => (recorded ? { ...library, works: [] } : library))
  const updates = createLiveStream()
  const { queryClient } = renderRoute('/library?lens=all&preview=case-1&field=title', http, updates)
  const button = await screen.findByRole('button', { name: 'Choose current value: Old title' })
  await act(async () => {
    void queryClient.invalidateQueries({ queryKey: ['review-case', 'case-1'], exact: true })
  })
  await waitFor(() => expect(reads).toBe(2))
  await userEvent.setup().click(button)
  await waitFor(() => expect(http.recordDecision).toHaveBeenCalledTimes(1))
  expect(pendingSignal?.aborted).toBe(true)
  const query = queryClient
    .getQueryCache()
    .find({ queryKey: ['review-case', 'case-1'], exact: true })
  if (!query) throw new Error('Mounted case query required')
  const invalidate = vi.spyOn(query, 'invalidate')
  await act(async () => {
    updates.announce({ topic: 'library', subject: 'case-1', sequence: 1 })
    updates.announce({ topic: 'library', subject: 'case-1', sequence: 2 })
    updates.announce({ topic: 'case', subject: 'case-1', sequence: 3 })
    updates.announce({ topic: 'resync', subject: null, sequence: 4 })
  })
  await waitFor(() => expect(hasBackgroundDebt(queryClient, query)).toBe(true))
  expect(reads).toBe(2)
  expect(screen.queryByRole('heading', { name: 'Review complete' })).toBeNull()
  expect(http.recordDecision).toHaveBeenCalledTimes(1)
  await act(async () => {
    recorded = true
    finishCommand?.({
      ...ack,
      invalidation_sequences: [1],
      confirmation: { status: 'confirmed', case: settled },
    })
  })
  expect(await screen.findByRole('heading', { name: 'Review complete' })).toBeVisible()
  await waitFor(() => expect(reads).toBe(3))
  expect(query.state.data).toEqual(settled)
  await waitFor(() =>
    expect(
      queryClient
        .getQueriesData<LibraryView>({ queryKey: ['library'] })
        .some(([, value]) => value?.works.length === 0),
    ).toBe(true),
  )
  for (let sequence = 5; sequence <= 8; sequence++) {
    const before = invalidate.mock.calls.length
    await act(async () => {
      updates.announce({
        topic: sequence % 2 ? 'case' : 'resync',
        subject: sequence % 2 ? 'case-1' : null,
        sequence,
      })
    })
    await waitFor(() => expect(invalidate.mock.calls.length).toBeGreaterThan(before))
    expect(reads).toBe(3)
    expect(confirmSignal?.aborted).toBe(false)
    expect(hasBackgroundDebt(queryClient, query)).toBe(true)
  }
  await act(async () => finishConfirmation?.(settled))
  await waitFor(() => expect(reads).toBe(4))
  await waitFor(() => expect(hasBackgroundDebt(queryClient, query)).toBe(false))
  expect(await screen.findByRole('heading', { name: 'Review complete' })).toBeVisible()
  expect(screen.getByRole('heading', { level: 2, name: 'A Book' })).toBeVisible()
  expect(screen.queryByText('This work is not available in the current Library result.')).toBeNull()
  expect(http.recordDecision).toHaveBeenCalledTimes(1)
})
