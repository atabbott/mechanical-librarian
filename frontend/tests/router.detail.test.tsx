import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import type { AttentionView, CaseDetailView } from '../src/api/http'
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
    expect(screen.queryByRole('button', { name: /Evidence for Current value/u })).toBeNull()
    await user.click(screen.getByRole('button', { name: 'Current value evidence' }))
    await waitFor(() =>
      expect(screen.queryByRole('button', { name: /Evidence for Current value/u })).toBeNull(),
    )
  })

  it('renders recorded and automatic fields as safe Library context rather than a retired review surface', async () => {
    const field = detail.fields[0]
    if (!field) throw new Error('title fixture required')
    const recorded = {
      ...field,
      needsAttention: false,
      classification: 'automatic_recorded' as const,
      classificationReason: '<script>untrusted</script>',
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
    expect(document.body.textContent).toContain('<script>untrusted</script>')
    expect(screen.queryByText('Current review case')).toBeNull()
  })

  it('preserves a diagnostic direct entry through Back and Forward', async () => {
    const { router } = renderRoute('/library?lens=all')
    await router.navigate({
      to: '/review/$reviewCaseId',
      params: { reviewCaseId: 'case-1' },
      search: { field: 'title', subview: 'evidence' },
    })
    await screen.findByRole('heading', { name: 'Evidence and source ledger' })
    await router.history.back()
    await waitFor(() => expect(router.state.location.pathname).toBe('/library'))
    await router.history.forward()
    await waitFor(() =>
      expect(router.state.location.search).toEqual({ field: 'title', subview: 'evidence' }),
    )
  })
})
