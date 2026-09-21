import {
  invalidateBackgroundQueries,
  hasBackgroundDebt,
  mountBackgroundWork,
} from '../src/queryWork'
import { QueryClient, QueryObserver } from '@tanstack/react-query'
import { expect, it, onTestFinished } from 'vitest'
import { submitDecisionCase } from '../src/decisionEcho'
import { detail, recordedAcknowledgement } from './fixtures'
import type { CaseDetailView, DecisionAcknowledgementView } from '../src/api/http'

const acknowledgement = (id: string, current = true): DecisionAcknowledgementView =>
  recordedAcknowledgement(
    { ...detail, current },
    {
      schema_version: 'review-decision-v1',
      actor: 'owner',
      application: 'apply',
      decision_id: id,
      destination: 'title',
      occurred_at: 1,
      outcome: 'keep_current',
      proposal_id: 'proposal-1',
      review_case_id: detail.id,
      selected_value: null,
      target_fingerprint: 'fingerprint-1',
      invalidation_sequences: [],
    },
  )
const harness = () => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const key = ['review-case', detail.id]
  const stopBackground = mountBackgroundWork(client)
  const observer = new QueryObserver(client, {
    queryKey: key,
    initialData: detail,
    staleTime: Infinity,
  })
  const stop = observer.subscribe(() => undefined)
  const query = client.getQueryCache().find({ queryKey: key, exact: true })
  if (!query) throw new Error('case query required')
  onTestFinished(() => {
    stop()
    stopBackground()
    client.clear()
  })
  return { client, query, key, stop }
}

it('serializes same-case commands and adopts each actual response before starting the next', async () => {
  const { client, query, key } = harness()
  const sent: string[] = []
  let release!: (value: DecisionAcknowledgementView) => void
  const first = submitDecisionCase(client, detail.id, () => {
    sent.push('first')
    return new Promise((resolve) => {
      release = resolve
    })
  })
  const second = submitDecisionCase(client, detail.id, async () => {
    sent.push('second')
    expect(client.getQueryData<CaseDetailView>(key)?.fields[0]?.humanDecision?.decision_id).toBe(
      'first',
    )
    return acknowledgement('second')
  })
  await new Promise((resolve) => setTimeout(resolve, 0))
  expect(sent).toEqual(['first'])
  for (let i = 0; i < 10; i += 1)
    await invalidateBackgroundQueries(client, { queryKey: key, exact: true })
  expect(hasBackgroundDebt(client, query)).toBe(true)
  release(acknowledgement('first'))
  await first
  await second
  expect(sent).toEqual(['first', 'second'])
  expect(client.getQueryData<CaseDetailView>(key)?.fields[0]?.humanDecision?.decision_id).toBe(
    'second',
  )
  expect(hasBackgroundDebt(client, query)).toBe(true)
})

it.each(['unmount', 'remove', 'replace'] as const)(
  'preserves the submitted write across %s without adopting another query',
  async (action) => {
    const { client, query, key, stop } = harness()
    let release!: (value: DecisionAcknowledgementView) => void
    const pending = submitDecisionCase(
      client,
      detail.id,
      () =>
        new Promise((resolve) => {
          release = resolve
        }),
    )
    await new Promise((resolve) => setTimeout(resolve, 0))
    stop()
    if (action !== 'unmount') client.removeQueries({ queryKey: key, exact: true })
    if (action === 'replace') client.setQueryData(key, detail)
    release(acknowledgement('committed'))
    const result = await pending
    expect('decision_id' in result.acknowledgement && result.acknowledgement.decision_id).toBe(
      'committed',
    )
    if (action === 'unmount') {
      expect(result.confirmed?.fields[0]?.humanDecision?.decision_id).toBe('committed')
      expect(query.getObserversCount()).toBe(0)
    } else {
      expect(result.confirmed).toBeUndefined()
      expect(client.getQueryData<CaseDetailView>(key)?.fields[0]?.humanDecision).not.toMatchObject({
        decision_id: 'committed',
      })
    }
  },
)

it('retains superseded currentness and durable unavailable acknowledgement without a background retry loop', async () => {
  const { client, query } = harness()
  const superseded = await submitDecisionCase(client, detail.id, async () =>
    acknowledgement('old-case', false),
  )
  expect(superseded.confirmed?.current).toBe(false)
  const unavailable = {
    ...acknowledgement('durable'),
    confirmation: { status: 'unavailable' as const },
  }
  let release!: (value: DecisionAcknowledgementView) => void
  const pending = submitDecisionCase(
    client,
    detail.id,
    () =>
      new Promise((resolve) => {
        release = resolve
      }),
  )
  await new Promise((resolve) => setTimeout(resolve, 0))
  await invalidateBackgroundQueries(client, { queryKey: query.queryKey, exact: true })
  release(unavailable)
  expect((await pending).confirmed).toBeUndefined()
  expect(hasBackgroundDebt(client, query)).toBe(false)
  expect(query.state.isInvalidated).toBe(true)
  expect(query.state.fetchStatus).toBe('idle')
})
