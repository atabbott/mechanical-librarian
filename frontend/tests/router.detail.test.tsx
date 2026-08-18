import { act, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { AmbiguousMutationError, ApiError } from '../src/api/http'
import type { AttentionView, CaseDetailView, HistoryView, ReviewHttp } from '../src/api/http'
import { detailQuery } from '../src/router'
import { createHttp, detail, renderRoute } from './fixtures'

describe('Detail route', () => {
  it('loads evidence only after the owner opens the Evidence subview', async () => {
    const user = userEvent.setup()
    const getEvidence = vi.fn(async () => ({ schema_version: 'review-case-evidence-v2', review_case_id: 'case-1', items: [], next_cursor: null }))
    const http: ReviewHttp = { ...createHttp(), getEvidence }
    renderRoute('/review/case-1?field=title&subview=review', http)

    expect(await screen.findByRole('heading', { name: 'Current review case' })).toBeVisible()
    expect(getEvidence).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: 'Evidence' }))
    await waitFor(() => expect(getEvidence).toHaveBeenCalledTimes(1))
  })

  it('shows bound incumbent and manual alternatives and retries only failed advice', async () => {
    const user = userEvent.setup()
    const base = detail.fields[0]
    if (!base) throw new Error('cover fixture requires a field')
    const consideration = {
      id: `cover_consideration_${'c'.repeat(64)}`,
      choiceContexts: [{ alternativeId: null, selection: 'current', evidence: [], limitations: [], rationale: 'Keep the local cover.', recommendation: 'no_recommendation', recommendationReason: 'No recommendation retained.' }, { alternativeId: `cover_transport_ref_${'2'.repeat(64)}`, selection: 'proposed', evidence: [{ source: 'Open Library', role: 'external_corroboration', observation: 'Retained catalogue cover supports this choice.' }], limitations: [], rationale: 'Selectable retained cover.', recommendation: 'no_recommendation', recommendationReason: 'No recommendation retained.' }],
      current: true,
      candidates: [
        { artifactId: `cover_${'a'.repeat(64)}`, height: 1200, identity: { outcome: 'verified_manifestation_match' }, incumbent: true, observation: null, originalSha256: 'a'.repeat(64), ordinal: 0, previewSha256: '1'.repeat(64), reference: `cover_transport_ref_${'1'.repeat(64)}`, selectable: false, sourceKinds: ['audiobookshelf_current'], state: 'incumbent', width: 800 },
        { artifactId: `cover_${'b'.repeat(64)}`, height: 1200, identity: { outcome: 'plausible_match' }, incumbent: false, observation: null, originalSha256: 'b'.repeat(64), ordinal: 1, previewSha256: '2'.repeat(64), reference: `cover_transport_ref_${'2'.repeat(64)}`, selectable: true, sourceKinds: ['provider'], state: 'acceptable', width: 800 },
      ],
      incumbent: { reference: `cover_transport_ref_${'1'.repeat(64)}`, state: 'available' as const },
      omissions: [], policy: null,
      policyPreferences: { avoidPromotionalText: false, avoidTieInBranding: false, preferMinimalComposition: false },
      policyReference: `cover_policy_${'3'.repeat(64)}`,
      reviewerStatus: 'failed', targetFingerprint: 'fingerprint-cover',
    } as const
    const coverField = { ...base, destination: 'cover', currentValue: `cover_${'a'.repeat(64)}`, currentRaw: `cover_${'a'.repeat(64)}`, proposalId: 'proposal-cover', targetFingerprint: 'fingerprint-cover', proposedValues: [{ kind: 'cover', value: `cover_${'b'.repeat(64)}` }], coverConsideration: consideration }
    const retryCoverConsideration = vi.fn(async () => { throw new Error('retry failed') })
    const recordDecision = vi.fn(async () => ({ schema_version: 'review-decision-v1', actor: 'owner', decision_id: 'decision-cover', destination: 'cover', occurred_at: 1, outcome: 'accept' as const, proposal_id: 'proposal-cover', review_case_id: 'case-1', selected_value: { kind: 'cover', value: `cover_${'b'.repeat(64)}` }, target_fingerprint: 'fingerprint-cover' }))
    const getHistory = vi.fn(async () => ({ schema_version: 'review-case-history-v1' as const, review_case_id: 'case-1', items: [], next_cursor: null }))
    const http: ReviewHttp = { ...createHttp(), getCaseDetail: vi.fn(async () => ({ ...detail, fields: [coverField] })), getHistory, retryCoverConsideration, recordDecision }
    renderRoute('/review/case-1?field=cover&subview=review', http)

    expect(await screen.findByRole('heading', { name: 'Incumbent' })).toBeVisible()
    await waitFor(() => expect(getHistory).toHaveBeenCalledOnce())
    expect(screen.getByRole('heading', { name: 'Acceptable' })).toBeVisible()
    expect(screen.getByText(/Observation or recommendation is unavailable/u)).toBeVisible()
    await user.click(screen.getByRole('button', { name: 'Retry advisory review' }))
    await waitFor(() => expect(retryCoverConsideration).toHaveBeenCalledWith('case-1'))
    expect(await screen.findByRole('alert')).toHaveTextContent('The advisory retry failed safely. The existing cover and manual choices are unchanged.')
    await user.click(screen.getByRole('button', { name: 'Choose this cover' }))
    await waitFor(() => expect(recordDecision).toHaveBeenCalledWith('case-1', expect.objectContaining({ considerationId: consideration.id, outcome: 'accept', selectedValue: { kind: 'cover', value: `cover_${'b'.repeat(64)}` } })))
    await waitFor(() => expect(getHistory).toHaveBeenCalledTimes(2))
  })

  it('shows immutable historical cover evidence and the exact owner choice', async () => {
    const reference = `cover_transport_ref_${'1'.repeat(64)}`
    const history: HistoryView = {
      schema_version: 'review-case-history-v1', review_case_id: 'case-1', next_cursor: null,
      items: [{
        schema_version: 'cover-consideration-event-v1', kind: 'selected', occurred_at: 2,
        consideration_id: `cover_consideration_${'c'.repeat(64)}`, review_case_id: 'case-1', decision_id: 'decision-cover',
        decision: { actor: 'owner', decision_id: 'decision-cover', destination: 'cover', occurred_at: 2, outcome: 'accept', proposal_id: 'proposal-cover', review_case_id: 'case-1', selected_value: { kind: 'cover', value: `cover_${'a'.repeat(64)}` }, target_fingerprint: 'fingerprint-cover' },
        consideration: {
          schema_version: 'cover-consideration-v1', consideration_id: `cover_consideration_${'c'.repeat(64)}`, current: false,
          choice_contexts: [],
          candidates: [{ artifact_id: `cover_${'a'.repeat(64)}`, height: 1200, identity: { candidate_reference: reference, evidence_references: [], outcome: 'verified_work_match', preview_reference: `cover_preview_${'2'.repeat(64)}`, reasons: [] }, incumbent: false, observation: { candidate_reference: reference, composition: 'minimal', medium: 'illustration', preview_sha256: '3'.repeat(64), preview_variant: 'normalized', promotional_text: 'absent', tie_in_branding: 'absent' }, original_sha256: '4'.repeat(64), ordinal: 0, preview_sha256: '3'.repeat(64), reference, selectable: true, source_kinds: ['epub'], state: 'acceptable', transport_sha256: '5'.repeat(64), width: 800 }],
          incumbent: { reference: null, state: 'absent' }, omissions: [], policy_preferences: { avoid_promotional_text: true, avoid_tie_in_branding: false, prefer_minimal_composition: true, schema_version: 'cover-policy-v1' }, policy_reference: `cover_policy_${'6'.repeat(64)}`, reviewer_status: 'completed', supersedes_id: null, target_fingerprint: 'fingerprint-cover',
          policy: { acceptable_candidate_references: [reference], cover_packet_reference: `cover_packet_${'7'.repeat(64)}`, identity_classifier_reference: `cover_identity_${'8'.repeat(64)}`, identity_report_sha256: '9'.repeat(64), observation_report_sha256: 'a'.repeat(64), observation_taxonomy_reference: `cover_taxonomy_${'b'.repeat(64)}`, policy_reference: `cover_policy_${'6'.repeat(64)}`, reasons: ['minimal_composition_preferred'], recommended_candidate_reference: reference, schema_version: 'cover-policy-report-v1', status: 'recommended' },
        },
      }],
    }
    const http: ReviewHttp = { ...createHttp(), getHistory: vi.fn(async () => history) }
    renderRoute('/review/case-1?field=title&subview=history', http)

    expect(await screen.findByRole('heading', { name: 'Cover selected' })).toBeVisible()
    expect(screen.getByText(/Historical; it grants no current authority/u)).toBeVisible()
    expect(screen.getByText(/Owner outcome: accept/u)).toHaveTextContent(`cover_${'a'.repeat(64)}`)
    expect(screen.getByText(/recommendation:/u)).toHaveTextContent(reference)
    expect(screen.getByRole('list', { name: 'Historical cover candidates' })).toHaveTextContent('composition minimal')
  })

  it('settles a deferred Detail decision without reviving recording status after Evidence and Library traversal', async () => {
    const user = userEvent.setup()
    const field = detail.fields[0]
    if (!field) throw new Error('detail fixture requires a field')
    let resolveDecision: (() => void) | undefined
    const deferred = new Promise<void>((resolve) => { resolveDecision = resolve })
    const settled: CaseDetailView = { ...detail, fields: [{ ...field, needsAttention: false, humanDecision: { actor: 'owner', decision_id: 'settled', destination: 'title', occurred_at: 1, outcome: 'keep_current', proposal_id: field.proposalId, review_case_id: 'case-1', selected_value: null, target_fingerprint: field.targetFingerprint } }] }
    let current = detail
    const http: ReviewHttp = { ...createHttp(), getCaseDetail: vi.fn(async () => current), recordDecision: vi.fn(async () => { await deferred; current = settled; return { schema_version: 'review-decision-v1', actor: 'owner', decision_id: 'settled', destination: 'title', occurred_at: 1, outcome: 'keep_current', proposal_id: field.proposalId, review_case_id: 'case-1', selected_value: null, target_fingerprint: field.targetFingerprint } }) }
    const { router } = renderRoute('/review/case-1?field=title&subview=review', http)

    await user.click(await screen.findByRole('button', { name: 'Choose current value: Old title' }))
    await user.click(screen.getByRole('button', { name: 'Evidence' }))
    await act(async () => { resolveDecision?.() })
    await waitFor(() => expect(router.state.location.search).toEqual({ field: 'title', subview: 'evidence' }))
    expect(await screen.findByText('Decision recorded from the authoritative case.')).toBeVisible()
    expect(screen.queryByText(/Recording keep current/u)).toBeNull()

    await user.click(screen.getByRole('link', { name: 'Library' }))
    await user.click(await screen.findByRole('button', { name: /^A Book\./u }))
    await user.click(await screen.findByRole('link', { name: 'Open full review' }))
    expect(await screen.findByText('Decision recorded from the authoritative case.')).toBeVisible()
    expect(screen.queryByText(/Recording keep current/u)).toBeNull()
  })

  it('settles durable decision status after leaving the submitting route', async () => {
    const user = userEvent.setup()
    const field = detail.fields[0]
    if (!field) throw new Error('detail fixture requires a field')
    let resolveDecision: (() => void) | undefined
    const deferred = new Promise<void>((resolve) => { resolveDecision = resolve })
    const settled = { ...detail, fields: [{ ...field, needsAttention: false, humanDecision: { actor: 'owner', decision_id: 'settled-after-leave', destination: 'title', occurred_at: 1, outcome: 'keep_current' as const, proposal_id: field.proposalId, review_case_id: 'case-1', selected_value: null, target_fingerprint: field.targetFingerprint } }] }
    let current = detail
    const http: ReviewHttp = { ...createHttp(), getCaseDetail: vi.fn(async () => current), recordDecision: vi.fn(async () => { await deferred; current = settled; return { schema_version: 'review-decision-v1', actor: 'owner', decision_id: 'settled-after-leave', destination: 'title', occurred_at: 1, outcome: 'keep_current', proposal_id: field.proposalId, review_case_id: 'case-1', selected_value: null, target_fingerprint: field.targetFingerprint } }) }
    renderRoute('/review/case-1?field=title&subview=review', http)

    await user.click(await screen.findByRole('button', { name: 'Choose current value: Old title' }))
    await user.click(screen.getByRole('link', { name: 'Library' }))
    await act(async () => { resolveDecision?.() })
    await user.click(await screen.findByRole('button', { name: /^A Book\./u }))
    await user.click(await screen.findByRole('link', { name: 'Open full review' }))
    expect(await screen.findByText('Decision recorded from the authoritative case.')).toBeVisible()
    expect(screen.queryByText(/Recording keep current/u)).toBeNull()
  })

  it('groups human-beneficial fields by work and links each exact destination', async () => {
    const attention: AttentionView = {
      total: 2,
      items: [
        { id: 'case-1', destination: 'title', title: 'A Book', authors: ['Ada Example'], library: 'Books', mediaKind: 'ebook', classificationReason: 'Canonical standalone attention reason', notices: ['internal-standalone-notice'], provenance: ['opaque-source-1'], workAttentionCount: 3 },
        { id: 'case-1', destination: 'authors', title: 'A Book', authors: ['Ada Example'], library: 'Books', mediaKind: 'ebook', classificationReason: 'This field needs your review.', notices: [], provenance: [], workAttentionCount: 3 },
      ],
    }
    const http: ReviewHttp = { ...createHttp(), getAttention: vi.fn(async () => attention) }
    renderRoute('/attention', http)

    expect(await screen.findByRole('heading', { name: 'A Book' })).toBeVisible()
    expect(screen.getByText('3 fields')).toBeVisible()
    expect(screen.getByText('Canonical standalone attention reason')).toBeVisible()
    expect(screen.queryByText('internal-standalone-notice')).toBeNull()
    expect(screen.queryByText('opaque-source-1')).toBeNull()
    expect(screen.getByText(/1 evidence record available/u)).toBeVisible()
    expect(screen.getByText(/Inspect the current value and available authors choices/u)).toBeVisible()
    expect(screen.getByRole('link', { name: /Title/u })).toHaveAttribute('href', '/review/case-1?field=title&subview=review')
    expect(screen.getByRole('link', { name: /Authors/u })).toHaveAttribute('href', '/review/case-1?field=authors&subview=review')
  })

  it('supports direct detail entry and focuses the settled main landmark', async () => {
    const { router } = renderRoute('/review/case-1')

    expect(await screen.findByRole('heading', { name: 'Current review case', level: 1 })).toBeVisible()
    await waitFor(() => expect(document.querySelector('#field-title')).toHaveFocus())
    expect(screen.getByText('Review case loaded')).toHaveAttribute('role', 'status')
    await waitFor(() => expect(router.state.location.search).toEqual({ field: 'title', subview: 'review' }))
  })

  it('records the exact selected proposal and refreshes authoritative detail', async () => {
    const user = userEvent.setup()
    const second = { kind: 'text', value: 'Another title' } as const
    const titleField = detail.fields[0]
    if (!titleField) throw new Error('detail fixture requires a title field')
    const staleNextField = { ...titleField, destination: 'subtitle', proposalId: 'proposal-2', targetFingerprint: 'fingerprint-2' }
    const actualNextField = { ...titleField, destination: 'authors', proposalId: 'proposal-3', targetFingerprint: 'fingerprint-3' }
    const initial: CaseDetailView = {
      ...detail,
      fields: [{ ...titleField, proposedValues: [...titleField.proposedValues, second] }, staleNextField, actualNextField],
    }
    const initialField = initial.fields[0]
    if (!initialField) throw new Error('detail fixture requires an initial title field')
    const decided: CaseDetailView = {
      ...initial,
      fields: [{
        ...initialField,
        needsAttention: false,
        humanDecision: {
          actor: 'owner', decision_id: 'decision-1', destination: 'title', occurred_at: 1,
          outcome: 'accept', proposal_id: 'proposal-1', review_case_id: 'case-1',
          selected_value: second, target_fingerprint: 'fingerprint-1',
        },
      }, {
        ...staleNextField,
        needsAttention: false,
        automaticDecision: {
          candidate_id: null, decision_id: 'automatic-decision-2', destination: 'subtitle', occurred_at: 1,
          outcome: 'accept', proposal_id: 'proposal-2', review_case_id: 'case-1',
          selected_value: staleNextField.proposedValues[0] ?? null, target_fingerprint: 'fingerprint-2',
        },
      }, actualNextField],
    }
    let current = initial
    const http: ReviewHttp = {
      ...createHttp(),
      getCaseDetail: vi.fn(async () => current),
      recordDecision: vi.fn(async () => {
        current = decided
        return { schema_version: 'review-decision-v1', actor: 'owner', decision_id: 'decision-1', destination: 'title', occurred_at: 1, outcome: 'accept', proposal_id: 'proposal-1', review_case_id: 'case-1', selected_value: second, target_fingerprint: 'fingerprint-1' }
      }),
    }
    const { router } = renderRoute('/review/case-1?field=title&subview=review', http)

    expect((await screen.findAllByLabelText('Reviewer facts')).at(0)).toHaveTextContent('Available choices4')
    await user.click(await screen.findByRole('button', { name: 'Choose proposed value 2: Another title' }))

    // The next field arrives from the field list already in hand; only the
    // exact case is refetched behind it, and never the Library.
    await waitFor(() => expect(router.state.location.search).toEqual({ field: 'authors', subview: 'review' }))
    expect(await screen.findByRole('heading', { name: 'Authors' })).toBeVisible()
    await waitFor(() => expect(http.recordDecision).toHaveBeenCalledWith('case-1', expect.objectContaining({ selectedValue: second })))
    expect(await screen.findByText('Decision recorded from the authoritative case.')).toBeVisible()
    expect(http.getCaseDetail).toHaveBeenCalledTimes(2)
    expect(http.getLibrary).not.toHaveBeenCalled()
  })

  it('offers only the current-value choice when an unresolved field has no proposal', async () => {
    const user = userEvent.setup()
    const titleField = detail.fields[0]
    if (!titleField) throw new Error('detail fixture requires a title field')
    const currentOnly: CaseDetailView = { ...detail, fields: [{ ...titleField, proposedValues: [] }] }
    const http: ReviewHttp = { ...createHttp(), getCaseDetail: vi.fn(async () => currentOnly) }
    renderRoute('/review/case-1?field=title&subview=review', http)

    expect(await screen.findByRole('button', { name: 'Choose current value: Old title' })).toBeVisible()
    expect(screen.getAllByLabelText('Reviewer facts').at(0)).toHaveTextContent('Available choices2')
    expect(screen.queryByRole('button', { name: /Choose proposed value/ })).toBeNull()
    await user.click(screen.getByRole('button', { name: 'Choose current value: Old title' }))
    await waitFor(() => expect(http.recordDecision).toHaveBeenCalledWith('case-1', expect.objectContaining({
      destination: 'title', outcome: 'keep_current', selectedValue: null,
    })))
  })

  it('halts the acknowledged field when a successful decision cannot refresh current detail', async () => {
    const user = userEvent.setup()
    const titleField = detail.fields[0]
    if (!titleField) throw new Error('detail fixture requires a title field')
    const failedRefresh = { ...detail, fields: [titleField, { ...titleField, destination: 'series', proposalId: 'proposal-2', targetFingerprint: 'fingerprint-2' }] }
    const http: ReviewHttp = {
      ...createHttp(),
      getCaseDetail: vi.fn().mockResolvedValueOnce(failedRefresh).mockRejectedValue(new Error('refresh unavailable')),
    }
    const { router } = renderRoute('/review/case-1?field=title&subview=review', http)

    await user.click(await screen.findByRole('button', { name: 'Choose current value: Old title' }))

    expect(await screen.findByText('Decision recorded, but the current case could not be refreshed. Reload before reviewing another value.')).toBeVisible()
    // The optimistic advance is withdrawn onto the field whose outcome is in doubt.
    await waitFor(() => expect(router.state.location.search).toEqual({ field: 'title', subview: 'review' }))
    expect(screen.queryByRole('button', { name: /Choose current value/ })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Try again' })).toBeNull()
    expect(screen.getAllByLabelText('Reviewer facts').at(0)).toHaveTextContent('Available choices0')
    await user.click(screen.getByRole('button', { name: 'Series' }))
    expect(screen.queryByRole('button', { name: /Choose current value/ })).toBeNull()
    expect(screen.getAllByLabelText('Reviewer facts').at(1)).toHaveTextContent('Available choices0')
  })

  it('advances in the displayed pre-decision order after refreshed fields are reordered', async () => {
    const user = userEvent.setup()
    const titleField = detail.fields[0]
    if (!titleField) throw new Error('detail fixture requires a title field')
    const authors = { ...titleField, destination: 'authors', proposalId: 'proposal-2', targetFingerprint: 'fingerprint-2' }
    const series = { ...titleField, destination: 'series', proposalId: 'proposal-3', targetFingerprint: 'fingerprint-3' }
    const initial = { ...detail, fields: [titleField, authors, series] }
    const refreshed = { ...initial, fields: [titleField, series, { ...authors, needsAttention: false, humanDecision: { actor: 'owner', decision_id: 'decision-2', destination: 'authors', occurred_at: 1, outcome: 'keep_current' as const, proposal_id: 'proposal-2', review_case_id: 'case-1', selected_value: null, target_fingerprint: 'fingerprint-2' } }] }
    let current = initial
    const http: ReviewHttp = { ...createHttp(), getCaseDetail: vi.fn(async () => current), recordDecision: vi.fn(async () => { current = refreshed; return { schema_version: 'review-decision-v1', actor: 'owner', decision_id: 'decision-2', destination: 'authors', occurred_at: 1, outcome: 'keep_current', proposal_id: 'proposal-2', review_case_id: 'case-1', selected_value: null, target_fingerprint: 'fingerprint-2' } }) }
    const { router } = renderRoute('/review/case-1?field=authors&subview=review', http)

    await user.click(await screen.findByRole('button', { name: 'Choose current value: Old title' }))

    await waitFor(() => expect(router.state.location.search).toEqual({ field: 'series', subview: 'review' }))
  })

  it('preserves masthead navigation made while a decision is settling', async () => {
    const user = userEvent.setup()
    let resolveDecision: (() => void) | undefined
    const completion = new Promise<void>((resolve) => { resolveDecision = resolve })
    const titleField = detail.fields[0]
    if (!titleField) throw new Error('detail fixture requires a title field')
    const resolved = { ...detail, fields: [{ ...titleField, needsAttention: false, humanDecision: { actor: 'owner', decision_id: 'decision-3', destination: 'title', occurred_at: 1, outcome: 'keep_current' as const, proposal_id: 'proposal-1', review_case_id: 'case-1', selected_value: null, target_fingerprint: 'fingerprint-1' } }] }
    let current = detail
    const http: ReviewHttp = { ...createHttp(), getCaseDetail: vi.fn(async () => current), recordDecision: vi.fn(async () => { await completion; current = resolved; return { schema_version: 'review-decision-v1', actor: 'owner', decision_id: 'decision-3', destination: 'title', occurred_at: 1, outcome: 'keep_current', proposal_id: 'proposal-1', review_case_id: 'case-1', selected_value: null, target_fingerprint: 'fingerprint-1' } }) }
    const { router } = renderRoute('/review/case-1?field=title&subview=review', http)

    await user.click(await screen.findByRole('button', { name: 'Choose current value: Old title' }))
    await user.click(screen.getByRole('link', { name: 'Library' }))
    await act(async () => { resolveDecision?.() })

    await waitFor(() => expect(router.state.location.pathname).toBe('/library'))
    expect(router.state.location.pathname).not.toContain('undefined')
  })

  it('replaces opaque provenance references with distinct bounded reviewer facts', async () => {
    const titleField = detail.fields[0]
    if (!titleField) throw new Error('detail fixture requires a title field')
    const opaque = `provider_${'a'.repeat(64)}`
    const longHostile = `<img src=x>${'evidence '.repeat(100)}`
    const http: ReviewHttp = { ...createHttp(), getCaseDetail: vi.fn(async () => ({ ...detail, fields: [{ ...titleField, provenance: [opaque, longHostile] }] })) }
    renderRoute('/review/case-1?field=title&subview=review', http)

    expect(await screen.findByText('Evidence record 1')).toBeVisible()
    expect(screen.getByText(/Reviewed source 2/u)).toBeVisible()
    expect(screen.queryByText(opaque)).toBeNull()
    expect(screen.queryByRole('img')).toBeNull()
  })

  it('keeps an automatically decided field inspectable and owner-editable', async () => {
    const titleField = detail.fields[0]
    if (!titleField) throw new Error('detail fixture requires a title field')
    const automatic: CaseDetailView = {
      ...detail,
      fields: [{
        ...titleField,
        needsAttention: false,
        automaticDecision: {
          candidate_id: null,
          decision_id: 'automatic-decision-1',
          destination: 'title',
          occurred_at: 1,
          outcome: 'accept',
          proposal_id: 'proposal-1',
          review_case_id: 'case-1',
          selected_value: titleField.proposedValues[0] ?? null,
          target_fingerprint: 'fingerprint-1',
        },
      }],
    }
    const http: ReviewHttp = { ...createHttp(), getCaseDetail: vi.fn(async () => automatic) }
    renderRoute('/review/case-1?field=title&subview=review', http)

    expect(await screen.findByText('Handled automatically: accept · policy origin.')).toBeVisible()
    expect(screen.getByRole('button', { name: /Choose current value/ })).toBeVisible()
    expect(screen.getByRole('button', { name: /Choose proposed value/ })).toBeVisible()
  })

  it('shows the exact accepted recorded value without interpreting hostile content', async () => {
    const titleField = detail.fields[0]
    if (!titleField) throw new Error('detail fixture requires a title field')
    const recordedValue = { kind: 'text', value: `<img src=x>${'selected '.repeat(80)}` } as const
    const decided: CaseDetailView = {
      ...detail,
      fields: [{
        ...titleField,
        needsAttention: false,
        humanDecision: {
          actor: 'owner', decision_id: 'decision-accepted', destination: 'title', occurred_at: 1,
          outcome: 'accept', proposal_id: 'proposal-1', review_case_id: 'case-1',
          selected_value: recordedValue, target_fingerprint: 'fingerprint-1',
        },
      }],
    }
    renderRoute('/review/case-1?field=title&subview=review', { ...createHttp(), getCaseDetail: vi.fn(async () => decided) })

    expect(await screen.findByText(`Human decision: accept by owner. Recorded value: ${recordedValue.value}.`)).toBeVisible()
    expect(screen.queryByRole('img')).toBeNull()
  })

  it('reconciles an ambiguous decision without retrying and routes stale cases safely', async () => {
    const user = userEvent.setup()
    const titleField = detail.fields[0]
    if (!titleField) throw new Error('detail fixture requires a title field')
    const reconciled: CaseDetailView = {
      ...detail,
      fields: [{
        ...titleField,
        needsAttention: false,
        humanDecision: {
          actor: 'owner', decision_id: 'decision-1', destination: 'title', occurred_at: 1,
          outcome: 'keep_current', proposal_id: 'proposal-1', review_case_id: 'case-1',
          selected_value: null, target_fingerprint: 'fingerprint-1',
        },
      }],
    }
    let reads = 0
    const ambiguous = vi.fn(async () => { throw new AmbiguousMutationError() })
    const http: ReviewHttp = {
      ...createHttp(),
      getCaseDetail: vi.fn(async () => ++reads === 1 ? detail : reconciled),
      recordDecision: ambiguous,
    }
    renderRoute('/review/case-1?field=title&subview=review', http)

    await user.click(await screen.findByRole('button', { name: 'Choose current value: Old title' }))

    expect(await screen.findByText('Human decision: keep current by owner. Recorded value: Old title.')).toBeVisible()
    expect(screen.getAllByLabelText('Reviewer facts').at(0)).toHaveTextContent('Deterministic dispositionReview')
    expect(screen.queryByText('Review state')).toBeNull()
    expect(screen.getByText('The response was lost. The current case was reloaded before any retry.')).toBeVisible()
    expect(screen.queryByRole('button', { name: 'Try again' })).toBeNull()
    // Reconciled to a recorded outcome, the field keeps every eligible option
    // and marks the one that is recorded, so the owner can still change it.
    expect(screen.getByRole('button', { name: 'Choose current value: Old title' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('button', { name: 'Choose proposed value 1: New title' })).toHaveAttribute('aria-pressed', 'false')
    expect(ambiguous).toHaveBeenCalledOnce()
  })

  it('routes a superseded case back to Library instead of advancing it', async () => {
    const user = userEvent.setup()
    const staleHttp: ReviewHttp = { ...createHttp(), recordDecision: vi.fn(async () => { throw new ApiError(409, 'stale') }) }
    const stale = renderRoute('/review/case-1?field=title&subview=review', staleHttp)
    await user.click(await screen.findByRole('button', { name: 'Choose current value: Old title' }))
    await waitFor(() => expect(stale.router.state.location.pathname).toBe('/library'))
  })

  it.each([
    {
      name: 'ambiguous response',
      error: new AmbiguousMutationError(),
      status: 'The decision outcome is uncertain and the current case could not be refreshed. Reload to reconcile it before reviewing another value.',
    },
    {
      name: 'ordinary failure',
      error: new ApiError(500, 'decision_failed'),
      status: 'The current decision state could not be reconciled because the case could not be refreshed. Reload before reviewing another value.',
    },
  ])('locks every field when an $name cannot be reconciled', async ({ error, status }) => {
    const user = userEvent.setup()
    const titleField = detail.fields[0]
    if (!titleField) throw new Error('detail fixture requires a title field')
    const authorsField = { ...titleField, destination: 'authors', proposalId: 'proposal-2', targetFingerprint: 'fingerprint-2' }
    const unresolved = { ...detail, fields: [titleField, authorsField] }
    const reconciled = { ...unresolved, fields: [{
      ...titleField,
      needsAttention: false,
      humanDecision: {
        actor: 'owner', decision_id: 'decision-reconciled', destination: 'title', occurred_at: 1,
        outcome: 'keep_current' as const, proposal_id: 'proposal-1', review_case_id: 'case-1',
        selected_value: null, target_fingerprint: 'fingerprint-1',
      },
    }, authorsField] }
    let reads = 0
    let refreshAvailable = false
    const recordDecision = vi.fn(async () => { throw error })
    const http: ReviewHttp = {
      ...createHttp(),
      getCaseDetail: vi.fn(async () => {
        reads += 1
        if (reads === 1) return unresolved
        if (!refreshAvailable) throw new Error('refresh unavailable')
        return reconciled
      }),
      recordDecision,
    }
    renderRoute('/review/case-1?field=title&subview=review', http)

    await user.click(await screen.findByRole('button', { name: 'Choose current value: Old title' }))

    expect(await screen.findByText(status)).toBeVisible()
    expect(screen.queryByText('The response was lost. The current case was reloaded before any retry.')).toBeNull()
    expect(screen.queryByText('The decision was not recorded. Your choice is unchanged.')).toBeNull()
    expect(screen.queryByRole('button', { name: /Choose current value/ })).toBeNull()
    expect(screen.queryByRole('button', { name: /Choose proposed value/ })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Try again' })).toBeNull()
    expect(screen.getAllByLabelText('Reviewer facts').at(0)).toHaveTextContent('Available choices0')

    await user.click(screen.getByRole('button', { name: 'Authors' }))

    expect(screen.queryByRole('button', { name: /Choose current value/ })).toBeNull()
    expect(screen.queryByRole('button', { name: /Choose proposed value/ })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Try again' })).toBeNull()
    expect(screen.getAllByLabelText('Reviewer facts').at(1)).toHaveTextContent('Available choices0')
    expect(recordDecision).toHaveBeenCalledOnce()

    await user.click(screen.getByRole('link', { name: 'Library' }))
    await user.click(await screen.findByRole('button', { name: /^A Book\./u }))
    await user.click(await screen.findByRole('link', { name: 'Open full review' }))
    await waitFor(() => expect(http.getCaseDetail).toHaveBeenCalledTimes(4))

    expect(screen.getByText(status)).toHaveAttribute('role', 'alert')
    expect(screen.queryByRole('button', { name: /Choose current value/ })).toBeNull()
    expect(screen.queryByRole('button', { name: /Choose proposed value/ })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Try again' })).toBeNull()
    expect(screen.getAllByLabelText('Reviewer facts').at(0)).toHaveTextContent('Available choices0')
    await user.click(screen.getByRole('button', { name: 'Authors' }))
    expect(screen.getAllByLabelText('Reviewer facts').at(1)).toHaveTextContent('Available choices0')

    refreshAvailable = true
    await user.click(screen.getByRole('link', { name: 'Library' }))
    await user.click(await screen.findByRole('button', { name: /^A Book\./u }))
    await user.click(await screen.findByRole('link', { name: 'Open full review' }))

    expect(await screen.findByRole('button', { name: 'Choose current value: Old title' })).toBeVisible()
    await user.click(screen.getByRole('button', { name: 'Title' }))
    expect(screen.getByText('Human decision: keep current by owner. Recorded value: Old title.')).toBeVisible()
    await user.click(screen.getByRole('button', { name: 'Authors' }))
    expect(screen.getByRole('button', { name: 'Choose current value: Old title' })).toBeVisible()
    expect(screen.getByRole('button', { name: 'Choose proposed value 1: New title' })).toBeVisible()
    expect(screen.getAllByLabelText('Reviewer facts').at(1)).toHaveTextContent('Available choices3')
  })

  it('binds an ambiguous reconciliation lock after a pending background detail refresh', async () => {
    const user = userEvent.setup()
    const titleField = detail.fields[0]
    if (!titleField) throw new Error('detail fixture requires a title field')
    const authorsField = { ...titleField, destination: 'authors', proposalId: 'proposal-2', targetFingerprint: 'fingerprint-2' }
    const unresolved = { ...detail, fields: [titleField, authorsField] }
    const background = { ...unresolved, title: 'Background authoritative detail' }
    let rejectDecision: ((error: unknown) => void) | undefined
    const decisionResult = new Promise<never>((_resolve, reject) => { rejectDecision = reject })
    let reads = 0
    let postLockRefreshAvailable = false
    const http: ReviewHttp = {
      ...createHttp(),
      getCaseDetail: vi.fn(async () => {
        reads += 1
        if (reads === 1) return unresolved
        if (reads === 2) return background
        if (postLockRefreshAvailable) return unresolved
        throw new Error('refresh unavailable')
      }),
      recordDecision: vi.fn(() => decisionResult),
    }
    const { queryClient } = renderRoute('/review/case-1?field=title&subview=review', http)

    await user.click(await screen.findByRole('button', { name: 'Choose current value: Old title' }))
    await act(async () => { await queryClient.fetchQuery({ ...detailQuery(http, 'case-1'), staleTime: 0 }) })
    expect(await screen.findByText('Background authoritative detail')).toBeVisible()
    await act(async () => { rejectDecision?.(new AmbiguousMutationError()) })

    const locked = 'The decision outcome is uncertain and the current case could not be refreshed. Reload to reconcile it before reviewing another value.'
    expect(await screen.findByText(locked)).toHaveAttribute('role', 'alert')
    expect(screen.queryByRole('button', { name: /Choose current value/ })).toBeNull()
    expect(screen.queryByRole('button', { name: /Choose proposed value/ })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Try again' })).toBeNull()

    await user.click(screen.getByRole('link', { name: 'Library' }))
    await user.click(await screen.findByRole('button', { name: /^A Book\./u }))
    await user.click(await screen.findByRole('link', { name: 'Open full review' }))
    await waitFor(() => expect(http.getCaseDetail).toHaveBeenCalledTimes(5))
    expect(screen.getByText(locked)).toHaveAttribute('role', 'alert')
    expect(screen.queryByRole('button', { name: /Choose current value/ })).toBeNull()
    await user.click(screen.getByRole('button', { name: 'Authors' }))
    expect(screen.queryByRole('button', { name: /Choose proposed value/ })).toBeNull()

    postLockRefreshAvailable = true
    await user.click(screen.getByRole('link', { name: 'Library' }))
    await user.click(await screen.findByRole('button', { name: /^A Book\./u }))
    await user.click(await screen.findByRole('link', { name: 'Open full review' }))
    await user.click(await screen.findByRole('button', { name: 'Authors' }))
    expect(screen.getByRole('button', { name: 'Choose current value: Old title' })).toBeVisible()
    expect(screen.getByRole('button', { name: 'Choose proposed value 1: New title' })).toBeVisible()
    expect(screen.queryByText(locked)).toBeNull()
  })

  it('replaces invalid detail state with the resolved canonical field and subview', async () => {
    const { router } = renderRoute('/review/case-1?field=missing&subview=hostile')

    expect(await screen.findByRole('heading', { name: 'Current review case' })).toBeVisible()
    await waitFor(() => expect(router.state.location.search).toEqual({ field: 'title', subview: 'review' }))
  })
})
