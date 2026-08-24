import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import type { AttentionView, CaseDetailView, ReviewHttp } from '../src/api/http'
import { createHttp, detail, renderRoute } from './fixtures'

describe('Library review ownership', () => {
  it('opens direct Evidence diagnostics and returns to the selected Library field', async () => {
    const { router } = renderRoute('/review/case-1?field=title&subview=evidence')
    expect(await screen.findByRole('heading', { name: 'A Book' })).toBeVisible()
    expect(router.state.location.pathname).toBe('/review/case-1')
    expect(screen.getByRole('heading', { name: 'Evidence and source ledger' })).toBeVisible()
    await userEvent.setup().click(screen.getByRole('link', { name: 'Back to work' }))
    await waitFor(() =>
      expect(router.state.location.search).toEqual({
        lens: 'all',
        preview: 'case-1',
        field: 'title',
      }),
    )
  })

  it('keeps History diagnostic-only and navigable from Evidence', async () => {
    const { router } = renderRoute('/review/case-1?field=title&subview=history')
    expect(await screen.findByRole('heading', { name: 'Current-case history' })).toBeVisible()
    expect(screen.queryByRole('button', { name: /Choose current value/u })).toBeNull()
    await userEvent.setup().click(screen.getByRole('button', { name: 'Evidence' }))
    await waitFor(() =>
      expect(router.state.location.search).toEqual({ field: 'title', subview: 'evidence' }),
    )
  })

  it('renders typed owner-history values as text, including retained outcomes', async () => {
    const getHistory = vi.fn<ReviewHttp['getHistory']>(async () => ({
      schema_version: 'review-case-history-v1',
      review_case_id: 'case-1',
      next_cursor: null,
      items: [
        {
          actor: 'owner',
          decision_id: 'decision-accept',
          destination: 'title',
          event_id: 'event-accept',
          kind: 'human_decision_recorded',
          occurred_at: 2,
          outcome: 'accept',
          previous_value: { kind: 'text', value: '<script>previous</script>' },
          review_case_id: 'case-1',
          schema_version: 'human-decision-event-v1',
          selected_value: { kind: 'text', value: '<script>chosen</script>' },
        },
        {
          actor: 'owner',
          decision_id: 'decision-keep',
          destination: 'subtitle',
          event_id: 'event-keep',
          kind: 'human_decision_recorded',
          occurred_at: 1,
          outcome: 'keep_current',
          previous_value: { kind: 'text', value: 'Existing subtitle' },
          review_case_id: 'case-1',
          schema_version: 'human-decision-event-v1',
          selected_value: { kind: 'text', value: 'Existing subtitle' },
        },
      ],
    }))
    renderRoute('/review/case-1?field=title&subview=history', { ...createHttp(), getHistory })

    expect(await screen.findByText('Proposed value selected.')).toBeVisible()
    expect(screen.getByText('Previous: <script>previous</script>')).toBeVisible()
    expect(screen.getByText('Chosen: <script>chosen</script>')).toBeVisible()
    expect(screen.getByText('Current value retained.')).toBeVisible()
    expect(screen.getByText('Retained: Existing subtitle')).toBeVisible()
    expect(document.querySelector('script')).toBeNull()
  })

  it.each(['evidence', 'history'] as const)(
    'guards a superseded %s diagnostic and returns to Library authority',
    async (subview) => {
      const evidence = vi.fn()
      const history = vi.fn()
      const user = userEvent.setup()
      const { router } = renderRoute(`/review/case-1?field=title&subview=${subview}`, {
        ...createHttp(),
        getCaseDetail: vi.fn(async () => ({ ...detail, current: false })),
        getEvidence: evidence,
        getHistory: history,
      })

      expect(
        await screen.findByRole('heading', {
          name: 'This diagnostic case is no longer current.',
        }),
      ).toBeVisible()
      expect(screen.queryByRole('heading', { name: 'Evidence and source ledger' })).toBeNull()
      expect(screen.queryByRole('heading', { name: 'Current-case history' })).toBeNull()
      expect(evidence).not.toHaveBeenCalled()
      expect(history).not.toHaveBeenCalled()
      await user.click(screen.getByRole('link', { name: 'Back to work' }))
      await waitFor(() => expect(router.state.location.search).toEqual({ lens: 'all' }))
    },
  )

  it('opens an Attention field directly in its selected Library preview', async () => {
    const attention: AttentionView = {
      total: 1,
      items: [
        {
          id: 'case-1',
          destination: 'title',
          title: 'A Book',
          authors: [],
          library: 'Books',
          mediaKind: 'ebook',
          classificationReason: 'This field needs your review.',
          notices: [],
          provenance: [],
          workAttentionCount: 1,
        },
      ],
    }
    const user = userEvent.setup()
    const { router } = renderRoute('/attention', {
      ...createHttp(),
      getAttention: vi.fn(async () => attention),
    })
    await user.click(await screen.findByRole('link', { name: /Open exact field/u }))
    expect(await screen.findByRole('heading', { name: 'A Book' })).toBeVisible()
    expect(router.state.location.pathname).toBe('/library')
    expect(router.state.location.search).toEqual({ lens: 'all', preview: 'case-1', field: 'title' })
  })

  it('keeps a later Attention page identity through its exact Library field', async () => {
    const later: AttentionView = {
      total: 2,
      items: [
        {
          id: 'case-later',
          destination: 'authors',
          title: 'Later attention work',
          authors: ['Later owner'],
          library: 'Books',
          mediaKind: 'ebook',
          classificationReason: 'This later field needs your review.',
          notices: [],
          provenance: [],
          workAttentionCount: 1,
        },
      ],
    }
    const field = detail.fields[0]
    if (!field) throw new Error('detail field fixture required')
    const user = userEvent.setup()
    const http = {
      ...createHttp(),
      getAttention: vi.fn(async () => later),
      getCaseDetail: vi.fn(async (id: string) => ({
        ...detail,
        id,
        title: 'Later attention work',
        fields: [{ ...field, destination: 'authors' }],
      })),
    }
    const { router } = renderRoute('/attention?cursor=later-page', http)

    await user.click(await screen.findByRole('link', { name: /Open exact field/u }))
    expect(await screen.findByRole('heading', { name: 'Later attention work' })).toBeVisible()
    expect(router.state.location.search).toEqual({
      lens: 'all',
      attentionCursor: 'later-page',
      preview: 'case-later',
      field: 'authors',
    })
    expect(http.getAttention).toHaveBeenLastCalledWith(
      { cursor: 'later-page' },
      expect.any(AbortSignal),
    )
  })

  it('records the exact selected scalar choice and refreshes the Library read model', async () => {
    const user = userEvent.setup()
    const field = detail.fields[0]
    if (!field) throw new Error('title fixture required')
    const settled: CaseDetailView = {
      ...detail,
      fields: [
        {
          ...field,
          needsAttention: false,
          humanDecision: {
            actor: 'owner',
            decision_id: 'decision-1',
            destination: field.destination,
            occurred_at: 1,
            outcome: 'accept',
            proposal_id: field.proposalId,
            review_case_id: 'case-1',
            selected_value: field.proposedValues[0] ?? null,
            target_fingerprint: field.targetFingerprint,
          },
        },
      ],
    }
    let current = detail
    const recordDecision = vi.fn(async () => {
      current = settled
      return {
        schema_version: 'review-decision-v1' as const,
        actor: 'owner' as const,
        decision_id: 'decision-1',
        destination: field.destination,
        occurred_at: 1,
        outcome: 'accept' as const,
        proposal_id: field.proposalId,
        review_case_id: 'case-1',
        selected_value: field.proposedValues[0] ?? null,
        target_fingerprint: field.targetFingerprint,
        invalidation_sequences: [],
      }
    })
    renderRoute('/library?lens=all&preview=case-1&field=title', {
      ...createHttp(),
      getCaseDetail: vi.fn(async () => current),
      recordDecision,
    })
    await user.click(await screen.findByRole('button', { name: /Choose proposed value/u }))
    await waitFor(() =>
      expect(recordDecision).toHaveBeenCalledWith(
        'case-1',
        expect.objectContaining({
          destination: 'title',
          outcome: 'accept',
          selectedValue: field.proposedValues[0],
        }),
      ),
    )
    expect(await screen.findByRole('heading', { name: 'Review complete' })).toBeVisible()
  })

  it('keeps a current-only field selectable without fabricating an alternative', async () => {
    const field = detail.fields[0]
    if (!field) throw new Error('title fixture required')
    renderRoute('/library?lens=all&preview=case-1&field=title', {
      ...createHttp(),
      getCaseDetail: vi.fn(async () => ({
        ...detail,
        fields: [{ ...field, preservationOnly: true, proposedValues: [] }],
      })),
    })
    expect(await screen.findByRole('button', { name: /Choose current value/u })).toBeVisible()
    expect(screen.queryByText('Choose a new value')).toBeNull()
  })

  it('keeps named retained evidence optional, expandable, sticky, and part of its whole choice', async () => {
    const user = userEvent.setup()
    const field = detail.fields[0]
    if (!field) throw new Error('title fixture required')
    const contextual = {
      alternativeId: null,
      selection: 'current' as const,
      evidence: [
        {
          source: 'Hardcover',
          role: 'external_corroboration' as const,
          observation: 'Title: Old title',
        },
      ],
      limitations: [],
      rationale: 'Retained rationale.',
      recommendation: 'recommended' as const,
      recommendationReason: 'Retained recommendation.',
    }
    renderRoute('/library?lens=all&preview=case-1&field=title', {
      ...createHttp(),
      getCaseDetail: vi.fn(async () => ({
        ...detail,
        fields: [{ ...field, choiceContexts: [contextual] }],
      })),
    })
    const evidence = await screen.findByRole('button', { name: /Evidence for Current value/u })
    await user.click(evidence)
    expect(screen.getByText('Hardcover')).toBeVisible()
    expect(screen.getAllByText('Retained recommendation.')).not.toHaveLength(0)
    expect(evidence).toHaveAttribute('aria-expanded', 'true')
    expect(evidence).toHaveTextContent('Hide evidence')
    await user.click(evidence)
    expect(evidence).toHaveFocus()
    await waitFor(() =>
      expect(screen.getByRole('button', { name: /Evidence for Current value/u })).toHaveAttribute(
        'aria-expanded',
        'false',
      ),
    )
  })

  it('renders recorded fields as safely escaped voluntary revisits outside Attention', async () => {
    const field = detail.fields[0]
    if (!field) throw new Error('title fixture required')
    const recorded = {
      ...field,
      needsAttention: false,
      classification: 'automatic_recorded' as const,
      classificationReason: '<script>untrusted</script>',
      currentValue: '<script>untrusted</script>',
      currentRaw: '<script>untrusted</script>',
      automaticDecision: {
        candidate_id: null,
        decision_id: 'automatic-1',
        destination: field.destination,
        occurred_at: 1,
        outcome: 'keep_current' as const,
        proposal_id: field.proposalId,
        review_case_id: 'case-1',
        selected_value: null,
        target_fingerprint: field.targetFingerprint,
      },
    }
    renderRoute('/library?lens=all&preview=case-1&field=title', {
      ...createHttp(),
      getCaseDetail: vi.fn(async () => ({ ...detail, fields: [recorded] })),
    })
    await screen.findByRole('heading', { name: 'A Book' })
    expect(screen.getByText('<script>untrusted</script>')).toBeVisible()
    expect(document.querySelector('script')).toBeNull()
    expect(
      screen.getByText('The recorded outcome is marked below. You can select another eligible value.'),
    ).toBeVisible()
    expect(screen.getByRole('button', { name: /Choose current value/u })).toBeVisible()
    expect(screen.queryByText('Current review case')).toBeNull()
  })

  it('requests fresh checks only after the Library manual-handling confirmation', async () => {
    const user = userEvent.setup()
    const field = detail.fields[0]
    if (!field) throw new Error('title fixture required')
    let settleRequest: (() => void) | undefined
    const returnToAutomatic = vi.fn(
      () =>
        new Promise<void>((resolve) => {
          settleRequest = resolve
        }),
    )
    renderRoute('/library?lens=all&preview=case-1&field=title', {
      ...createHttp(),
      getCaseDetail: vi.fn(async () => ({ ...detail, fields: [{ ...field, manualHold: true }] })),
      returnToAutomatic,
    })

    await screen.findByRole('button', { name: 'Resume automatic handling' })
    await user.click(screen.getByRole('button', { name: 'Resume automatic handling' }))
    const cancel = await screen.findByRole('button', { name: 'Keep manual handling' })
    expect(cancel).toHaveFocus()
    await user.keyboard('{Escape}')
    expect(screen.getByRole('button', { name: 'Resume automatic handling' })).toHaveFocus()
    expect(returnToAutomatic).not.toHaveBeenCalled()

    await user.click(screen.getByRole('button', { name: 'Resume automatic handling' }))
    await user.click(screen.getByRole('button', { name: 'Resume after fresh checks' }))
    expect(screen.getByRole('button', { name: 'Requesting fresh checks…' })).toBeDisabled()
    expect(returnToAutomatic).toHaveBeenCalledOnce()
    settleRequest?.()
    expect(
      await screen.findByText(
        'Fresh checks requested. Manual handling remains until they complete.',
      ),
    ).toBeVisible()
    await user.click(screen.getByRole('button', { name: 'Keep manual handling' }))
    expect(screen.getByRole('button', { name: 'Fresh checks requested' })).toHaveAttribute(
      'aria-disabled',
      'true',
    )
    expect(screen.getByRole('button', { name: 'Fresh checks requested' })).toHaveFocus()
    await user.click(screen.getByRole('button', { name: 'Fresh checks requested' }))
    expect(returnToAutomatic).toHaveBeenCalledOnce()
  })

  it('keeps manual handling unchanged when fresh-check request fails', async () => {
    const user = userEvent.setup()
    const field = detail.fields[0]
    if (!field) throw new Error('title fixture required')
    const returnToAutomatic = vi.fn(async () => Promise.reject(new Error('offline')))
    renderRoute('/library?lens=all&preview=case-1&field=title', {
      ...createHttp(),
      getCaseDetail: vi.fn(async () => ({ ...detail, fields: [{ ...field, manualHold: true }] })),
      returnToAutomatic,
    })

    await user.click(await screen.findByRole('button', { name: 'Resume automatic handling' }))
    await user.click(screen.getByRole('button', { name: 'Resume after fresh checks' }))
    expect(
      await screen.findByText('Fresh checks could not be requested. Manual handling is unchanged.'),
    ).toBeVisible()
    expect(returnToAutomatic).toHaveBeenCalledOnce()
    expect(screen.getByRole('button', { name: 'Keep manual handling' })).toBeEnabled()
  })

  it('keeps a fresh-check success with the field that requested it after navigation', async () => {
    const user = userEvent.setup()
    const title = detail.fields[0]
    if (!title) throw new Error('title fixture required')
    const authors = {
      ...title,
      destination: 'authors',
      targetFingerprint: 'fingerprint-authors',
      manualHold: true,
    }
    let resolveRequest: (() => void) | undefined
    const returnToAutomatic = vi.fn(
      () =>
        new Promise<void>((resolve) => {
          resolveRequest = resolve
        }),
    )
    renderRoute('/library?lens=all&preview=case-1&field=title', {
      ...createHttp(),
      getCaseDetail: vi.fn(async () => ({
        ...detail,
        fields: [{ ...title, manualHold: true }, authors],
      })),
      returnToAutomatic,
    })

    await user.click(await screen.findByRole('button', { name: 'Resume automatic handling' }))
    await user.click(screen.getByRole('button', { name: 'Resume after fresh checks' }))
    await user.click(screen.getByRole('button', { name: /^Authors/u }))
    await screen.findByRole('heading', { name: 'Which authors should this work use?' })
    await user.click(screen.getByRole('button', { name: 'Resume automatic handling' }))
    expect(
      screen.queryByText('Fresh checks requested. Manual handling remains until they complete.'),
    ).toBeNull()
    resolveRequest?.()
    await waitFor(() => expect(returnToAutomatic).toHaveBeenCalledOnce())
    expect(
      screen.queryByText('Fresh checks requested. Manual handling remains until they complete.'),
    ).toBeNull()
    await user.click(screen.getByRole('button', { name: /^Title/u }))
    await screen.findByRole('heading', { name: 'Which title should this work use?' })
    expect(screen.getByRole('button', { name: 'Fresh checks requested' })).toHaveAttribute(
      'aria-disabled',
      'true',
    )
  })

  it('keeps a fresh-check failure with the field that requested it after navigation', async () => {
    const user = userEvent.setup()
    const title = detail.fields[0]
    if (!title) throw new Error('title fixture required')
    const authors = {
      ...title,
      destination: 'authors',
      targetFingerprint: 'fingerprint-authors',
      manualHold: true,
    }
    let rejectRequest: ((error: Error) => void) | undefined
    const returnToAutomatic = vi.fn(
      () =>
        new Promise<void>((_resolve, reject) => {
          rejectRequest = reject
        }),
    )
    renderRoute('/library?lens=all&preview=case-1&field=title', {
      ...createHttp(),
      getCaseDetail: vi.fn(async () => ({
        ...detail,
        fields: [{ ...title, manualHold: true }, authors],
      })),
      returnToAutomatic,
    })

    await user.click(await screen.findByRole('button', { name: 'Resume automatic handling' }))
    await user.click(screen.getByRole('button', { name: 'Resume after fresh checks' }))
    await user.click(screen.getByRole('button', { name: /^Authors/u }))
    await screen.findByRole('heading', { name: 'Which authors should this work use?' })
    await user.click(screen.getByRole('button', { name: 'Resume automatic handling' }))
    rejectRequest?.(new Error('offline'))
    await waitFor(() => expect(returnToAutomatic).toHaveBeenCalledOnce())
    expect(
      screen.queryByText('Fresh checks could not be requested. Manual handling is unchanged.'),
    ).toBeNull()
    await user.click(screen.getByRole('button', { name: /^Title/u }))
    await screen.findByRole('heading', { name: 'Which title should this work use?' })
    await user.click(screen.getByRole('button', { name: 'Resume automatic handling' }))
    expect(
      await screen.findByText('Fresh checks could not be requested. Manual handling is unchanged.'),
    ).toBeVisible()
    expect(screen.getByRole('button', { name: 'Keep manual handling' })).toHaveFocus()
  })

  it('preserves a diagnostic direct entry through Back and Forward', async () => {
    const { router } = renderRoute('/library?lens=all')
    await router.navigate({
      to: '/review/$reviewCaseId',
      params: { reviewCaseId: 'case-1' },
      search: { field: 'title', subview: 'evidence' },
    })
    await screen.findByRole('heading', { name: 'Evidence and source ledger' })
    router.history.back()
    await waitFor(() => expect(router.state.location.pathname).toBe('/library'))
    router.history.forward()
    await waitFor(() =>
      expect(router.state.location.search).toEqual({ field: 'title', subview: 'evidence' }),
    )
  })
})
