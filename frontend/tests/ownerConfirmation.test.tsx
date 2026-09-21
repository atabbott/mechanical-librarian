import { act, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { expect, it, vi } from 'vitest'
import type { CaseDetailView, ReviewHttp } from '../src/api/http'
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
    fields: [{ ...field, needsAttention: false, humanDecision: ack }],
  }
  let reads = 0
  let recorded = false
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
  http.recordDecision = vi.fn(async () => {
    recorded = true
    return ack
  })
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
  await waitFor(() => expect(reads).toBe(3))
  for (let sequence = 1; sequence <= 4; sequence++) {
    await act(async () => {
      updates.announce({
        topic: sequence % 2 ? 'case' : 'resync',
        subject: sequence % 2 ? 'case-1' : null,
        sequence,
      })
      await new Promise((resolve) => setTimeout(resolve, 140))
    })
  }
  expect(reads).toBe(3)
  expect(pendingSignal?.aborted).toBe(true)
  expect(confirmSignal?.aborted).toBe(false)
  await act(async () => finishConfirmation?.(settled))
  expect(await screen.findByRole('heading', { name: 'Review complete' })).toBeVisible()
  expect(screen.getByRole('heading', { level: 2, name: 'A Book' })).toBeVisible()
  expect(screen.queryByText('This work is not available in the current Library result.')).toBeNull()
  await waitFor(() => expect(reads).toBe(4))
  expect(http.recordDecision).toHaveBeenCalledTimes(1)
})
