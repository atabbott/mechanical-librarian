import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { RouterProvider, createMemoryHistory } from '@tanstack/react-router'
import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { AmbiguousMutationError, ApiError } from '../src/api/http'
import type { ActivityView, CaseDetailView, LibrarySearch, LibraryView, PolicyStateView, PreparationView, ReviewHttp, ShadowPostureView, ShadowSampleView } from '../src/api/http'
import { createApplicationRouter, normalizeLibrarySearch } from '../src/router'

const library: LibraryView = {
  lens: 'all',
  counts: { attention: 1, all: 1, clear: 0 },
  works: [{
    id: 'case-1',
    title: 'A Book',
    subtitle: 'Books · item-1',
    attention: 'needs_attention',
    dispositions: ['review'],
  }],
}

const detail: CaseDetailView = {
  id: 'case-1',
  current: true,
  title: 'A Book',
  subtitle: 'Books · local',
  fields: [{
    destination: 'title',
    disposition: 'review',
    effectCapability: 'manual',
    needsAttention: true,
    manualHold: false,
    currentValue: 'Old title',
    currentRaw: 'Old title',
    proposedValues: [{ kind: 'text', value: 'New title' }],
    proposalId: 'proposal-1',
    targetFingerprint: 'fingerprint-1',
    humanDecision: null,
    automaticDecision: null,
    notices: [],
    provenance: [],
  }],
}

const createHttp = (): ReviewHttp => ({
  getLibrary: vi.fn(async () => library),
  getAttention: vi.fn(async () => ({ total: 0, items: [] })),
  getCaseDetail: vi.fn(async () => detail),
  getEvidence: vi.fn(async () => ({ schema_version: 'review-case-evidence-v2', review_case_id: 'case-1', items: [], next_cursor: null })),
  getHistory: vi.fn(async () => ({ schema_version: 'review-case-history-v1', review_case_id: 'case-1', items: [], next_cursor: null })),
  getPreparation: vi.fn(async (): Promise<PreparationView> => ({ schema_version: 'review-preparation-v1', active: false, actionable: false, paused: false, libraries: [] })),
  preparationAction: vi.fn(async (): Promise<PreparationView> => ({ schema_version: 'review-preparation-v1', active: false, actionable: false, paused: false, libraries: [] })),
  recordDecision: vi.fn(async () => ({ schema_version: 'review-decision-v1', actor: 'owner', decision_id: 'decision-1', destination: 'title', occurred_at: 1, outcome: 'keep_current', proposal_id: 'proposal-1', review_case_id: 'case-1', selected_value: null, target_fingerprint: 'fingerprint-1' })),
  returnToAutomatic: vi.fn(async () => undefined),
  getPolicyState: vi.fn(async (): Promise<PolicyStateView> => ({ schema_version: 'metadata-policy-notebook-v1', policy_id: 'policy-1', profile: { schema_version: 'metadata-policy-profile-v1', name: 'Default', options: {}, destinations: {} } })),
  getShadowPosture: vi.fn(async (): Promise<ShadowPostureView> => ({ schema_version: 'shadow-posture-v1', posture: 'review', authority: 'none', can_leave_preview: false })),
  getAutomaticSafety: vi.fn(async () => ({ paused: false, global_suspension: null, category_suspensions: {} })),
  getShadowSample: vi.fn(async (): Promise<ShadowSampleView> => ({ schema_version: 'shadow-outcome-sample-v1', authority: 'none', items: [], next_cursor: null })),
  getPolicyTransition: vi.fn(async () => null),
  policyCommand: async <T,>() => ({} as T),
  getActivity: vi.fn(async () => ({})),
  activityCommand: vi.fn(async () => undefined),
  logout: vi.fn(async () => undefined),
})

const renderRoute = (entry: string, http = createHttp()) => {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const router = createApplicationRouter(http, queryClient, createMemoryHistory({ initialEntries: [entry] }))
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  )
  return { http, queryClient, router }
}

describe('typed review routes', () => {
  it('bounds and normalizes URL-owned library search state', () => {
    expect(normalizeLibrarySearch({ lens: 'all', q: 'title', cursor: 'next', preview: 'case-1' })).toEqual({
      lens: 'all', q: 'title', cursor: 'next', preview: 'case-1',
    })
    expect(normalizeLibrarySearch({ lens: 'hostile', q: 'x'.repeat(129) })).toEqual({ lens: 'needs_attention' })
  })

  it('loads the critical library query and progressively reveals detail', async () => {
    const user = userEvent.setup()
    const { http } = renderRoute('/library?lens=all&q=title')

    expect(await screen.findByRole('heading', { name: 'Review library' })).toBeVisible()
    expect(http.getLibrary).toHaveBeenCalledWith({ lens: 'all', q: 'title' }, expect.any(AbortSignal))
    await user.click(screen.getByRole('button', { name: 'Preview' }))
    expect(await screen.findByText('Progressive detail')).toBeVisible()
    expect(http.getCaseDetail).toHaveBeenCalledWith('case-1', expect.any(AbortSignal))
  })

  it('clears preview and cursor when the Library search query changes', async () => {
    const user = userEvent.setup()
    const { router } = renderRoute('/library?lens=all&q=first&cursor=next&preview=case-1')

    expect(await screen.findByText('Progressive detail')).toBeVisible()
    await user.clear(screen.getByLabelText('Search title, library, or item'))
    await user.type(screen.getByLabelText('Search title, library, or item'), 'second')
    await user.click(screen.getByRole('button', { name: 'Search' }))

    await waitFor(() => expect(router.state.location.search).toEqual({ lens: 'all', q: 'second' }))
    expect(screen.queryByText('Progressive detail')).toBeNull()
  })

  it('supports direct detail entry and focuses the settled main landmark', async () => {
    const { router } = renderRoute('/review/case-1')

    expect(await screen.findByRole('heading', { name: 'Current review case', level: 1 })).toBeVisible()
    await waitFor(() => expect(screen.getByRole('main')).toHaveFocus())
    expect(screen.getByText('Review case loaded')).toHaveAttribute('role', 'status')
    await waitFor(() => expect(router.state.location.search).toEqual({ field: 'title', subview: 'review' }))
  })

  it('records the exact selected proposal and refreshes authoritative detail', async () => {
    const user = userEvent.setup()
    const second = { kind: 'text', value: 'Another title' } as const
    const titleField = detail.fields[0]
    if (!titleField) throw new Error('detail fixture requires a title field')
    const initial: CaseDetailView = {
      ...detail,
      fields: [{ ...titleField, proposedValues: [...titleField.proposedValues, second] }],
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
      }],
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
    renderRoute('/review/case-1?field=title&subview=review', http)

    await user.click(await screen.findByRole('button', { name: 'Accept proposed value 2: Another title' }))

    await waitFor(() => expect(http.recordDecision).toHaveBeenCalledWith('case-1', expect.objectContaining({ selectedValue: second })))
    expect(await screen.findByText('Human decision: accept by owner.')).toBeVisible()
    expect(screen.queryByRole('button', { name: 'Keep current value' })).toBeNull()
    expect(http.getCaseDetail).toHaveBeenCalledTimes(2)
  })

  it('preserves a human override for an automatically decided field', async () => {
    const user = userEvent.setup()
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

    await user.click(await screen.findByRole('button', { name: 'Choose human override' }))
    await user.click(screen.getByRole('button', { name: 'Keep current value' }))

    await waitFor(() => expect(http.recordDecision).toHaveBeenCalledWith(
      'case-1',
      expect.objectContaining({ destination: 'title', outcome: 'keep_current' }),
    ))
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

    await user.click(await screen.findByRole('button', { name: 'Keep current value' }))

    expect(await screen.findByText('Human decision: keep current by owner.')).toBeVisible()
    expect(screen.getByText('The response was lost. The current case was reloaded before any retry.')).toBeVisible()
    expect(ambiguous).toHaveBeenCalledOnce()

    const staleHttp: ReviewHttp = { ...createHttp(), recordDecision: vi.fn(async () => { throw new ApiError(409, 'stale') }) }
    const stale = renderRoute('/review/case-1?field=title&subview=review', staleHttp)
    const keep = (await screen.findAllByRole('button', { name: 'Keep current value' })).at(-1)
    if (!keep) throw new Error('stale fixture requires a decision control')
    await user.click(keep)
    await waitFor(() => expect(stale.router.state.location.pathname).toBe('/library'))
  })

  it('replaces invalid detail state with the resolved canonical field and subview', async () => {
    const { router } = renderRoute('/review/case-1?field=missing&subview=hostile')

    expect(await screen.findByRole('heading', { name: 'Current review case' })).toBeVisible()
    await waitFor(() => expect(router.state.location.search).toEqual({ field: 'title', subview: 'review' }))
  })

  it('aborts a superseded critical load and renders only the current route result', async () => {
    const signals: AbortSignal[] = []
    let resolveFirst: ((value: LibraryView) => void) | undefined
    const withTitle = (title: string): LibraryView => ({
      ...library,
      works: library.works.map((work, index) => index === 0 ? { ...work, title } : work),
    })
    const current = withTitle('Current result')
    const http: ReviewHttp = {
      ...createHttp(),
      getLibrary: vi.fn((search: LibrarySearch, signal?: AbortSignal): Promise<LibraryView> => {
        if (signal) signals.push(signal)
        if (search.lens === 'no_attention_needed') return Promise.resolve(current)
        return new Promise<LibraryView>((resolve) => { resolveFirst = resolve })
      }),
    }
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const router = createApplicationRouter(
      http,
      queryClient,
      createMemoryHistory({ initialEntries: ['/library?lens=all'] }),
    )
    render(
      <QueryClientProvider client={queryClient}>
        <RouterProvider router={router} />
      </QueryClientProvider>,
    )
    await waitFor(() => expect(http.getLibrary).toHaveBeenCalledOnce())
    await router.navigate({ to: '/library', search: { lens: 'no_attention_needed' } })
    resolveFirst?.(withTitle('Stale result'))

    expect(await screen.findByRole('heading', { name: 'Current result' })).toBeVisible()
    expect(screen.queryByRole('heading', { name: 'Stale result' })).toBeNull()
    expect(signals[0]?.aborted).toBe(true)
  })

  it('refreshes active projections when passive preparation progress changes', async () => {
    vi.useFakeTimers()
    try {
      let checked = 0
      const http: ReviewHttp = {
        ...createHttp(),
        getPreparation: vi.fn(async (): Promise<PreparationView> => ({
          schema_version: 'review-preparation-v1',
          active: true,
          actionable: false,
          paused: false,
          libraries: [{
            blocked: 0,
            checked: checked++,
            connection: 'primary',
            library: 'Books',
            library_id: 'books',
            ready: 0,
            rescan_requested: false,
            scan_id: 'scan-1',
            state: 'discovering',
            total: 2,
          }],
        })),
      }
      const { queryClient } = renderRoute('/library?lens=all', http)
      const invalidations = vi.spyOn(queryClient, 'invalidateQueries')
      await vi.waitFor(() => expect(http.getPreparation).toHaveBeenCalledOnce())

      await act(async () => { await vi.advanceTimersByTimeAsync(1_600) })

      await vi.waitFor(() => expect(invalidations).toHaveBeenCalledWith({ queryKey: ['library'] }))
      expect(invalidations).toHaveBeenCalledWith({ queryKey: ['attention'] })
    } finally {
      vi.useRealTimers()
    }
  })

  it('polls idle Activity while mounted and tears the poll down after navigation', async () => {
    vi.useFakeTimers()
    try {
      const http = createHttp()
      const { router } = renderRoute('/activity', http)
      await vi.waitFor(() => expect(http.getActivity).toHaveBeenCalledOnce())

      await act(async () => { await vi.advanceTimersByTimeAsync(5_100) })
      await vi.waitFor(() => expect(http.getActivity).toHaveBeenCalledTimes(2))
      await act(async () => { await router.navigate({ to: '/library', search: { lens: 'all' } }) })
      const callsAfterNavigation = vi.mocked(http.getActivity).mock.calls.length

      await act(async () => { await vi.advanceTimersByTimeAsync(5_100) })

      expect(http.getActivity).toHaveBeenCalledTimes(callsAfterNavigation)
    } finally {
      vi.useRealTimers()
    }
  })

  it('discloses the exact destination subset and offers incremental consent', async () => {
    const user = userEvent.setup()
    const http: ReviewHttp = {
      ...createHttp(),
      getActivity: vi.fn(async () => ({
        backup_ready: true,
        configured_libraries: [{ connection: 'primary', library: 'fiction', prepared: { automatic: 1, human: 1, total: 2 } }],
        enabled_libraries: [{ connection: 'primary', library: 'fiction' }],
        capabilities: [{
          connection: 'primary', library: 'fiction', state: 'partially_allowed',
          destinations: [
            { destination: 'authors', state: 'allowed', consented: false },
            { destination: 'subtitle', state: 'field_unavailable', consented: false },
            { destination: 'title', state: 'allowed', consented: true },
          ],
        }],
        events: [], paused: false,
      })),
    }
    renderRoute('/activity', http)

    const disclosure = await screen.findByRole('list', { name: 'fiction destination consent' })
    expect(disclosure).toHaveTextContent('authors: Included · consent requested')
    expect(disclosure).toHaveTextContent('subtitle: Excluded · field unavailable')
    expect(disclosure).toHaveTextContent('title: Included · already consented')
    const choice = screen.getByRole('checkbox', { name: /fiction · 2 prepared/ })
    await user.click(choice)
    await user.click(screen.getByRole('button', { name: 'Enable selected libraries' }))

    await waitFor(() => expect(http.activityCommand).toHaveBeenCalledWith(
      'activity/consent',
      { grants: [{ library: 'fiction', destinations: ['authors', 'title'] }] },
    ))
    await waitFor(() => expect(choice).not.toBeChecked())
    expect(screen.getByRole('button', { name: 'Enable selected libraries' })).toBeDisabled()
  })

  it('prunes a selection when polling removes and later restores eligibility', async () => {
    const user = userEvent.setup()
    const partial: ActivityView = {
      backup_ready: true,
      configured_libraries: [{ connection: 'primary', library: 'fiction', prepared: { automatic: 1, human: 0, total: 1 } }],
      enabled_libraries: [],
      capabilities: [{ connection: 'primary', library: 'fiction', state: 'allowed', destinations: [{ destination: 'title', state: 'allowed', consented: false }] }],
      events: [], paused: false,
    }
    let current = partial
    const http: ReviewHttp = { ...createHttp(), getActivity: vi.fn(async () => current) }
    const { queryClient } = renderRoute('/activity', http)
    const choice = await screen.findByRole('checkbox', { name: /fiction · 1 prepared/ })
    await user.click(choice)
    expect(choice).toBeChecked()

    current = { ...partial, enabled_libraries: [{ connection: 'primary', library: 'fiction' }], capabilities: [{ connection: 'primary', library: 'fiction', state: 'allowed', destinations: [{ destination: 'title', state: 'allowed', consented: true }] }] }
    await queryClient.invalidateQueries({ queryKey: ['activity'] })
    await waitFor(() => expect(choice).toBeDisabled())
    expect(choice).not.toBeChecked()

    current = partial
    await queryClient.invalidateQueries({ queryKey: ['activity'] })
    await waitFor(() => expect(choice).toBeEnabled())
    expect(choice).not.toBeChecked()
    expect(screen.getByRole('button', { name: 'Enable guarded writes' })).toBeDisabled()
  })

  it('keeps a fully connection-blocked prepared library visible but unselectable', async () => {
    const http: ReviewHttp = {
      ...createHttp(),
      getActivity: vi.fn(async () => ({
        backup_ready: true,
        configured_libraries: [{ connection: 'primary', library: 'blocked', prepared: { automatic: 2, human: 0, total: 2 } }],
        enabled_libraries: [],
        capabilities: [{
          connection: 'primary', library: 'blocked', state: 'connection_unavailable',
          destinations: [
            { destination: 'authors', state: 'connection_unavailable', consented: false },
            { destination: 'title', state: 'connection_unavailable', consented: false },
          ],
        }],
        events: [], paused: false,
      })),
    }
    renderRoute('/activity', http)

    const disclosure = await screen.findByRole('list', { name: 'blocked destination consent' })
    expect(disclosure).toHaveTextContent('authors: Excluded · connection unavailable')
    expect(disclosure).toHaveTextContent('title: Excluded · connection unavailable')
    expect(screen.getByRole('checkbox', { name: /blocked · 2 prepared/ })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Enable guarded writes' })).toBeDisabled()
  })

  it('labels qualified automatic activity and bounds unknown origins neutrally', async () => {
    const unknown = 'unexpected_origin_' + 'x'.repeat(100)
    const http: ReviewHttp = {
      ...createHttp(),
      getActivity: vi.fn(async () => ({
        backup_ready: true, configured_libraries: [], enabled_libraries: [], capabilities: [], paused: false,
        events: [
          { work_id: 'book-1', library: 'fiction', destination: 'title', state: 'verified', origin: 'automatic_policy' },
          { work_id: 'book-2', library: 'fiction', destination: 'subtitle', state: 'stale', origin: unknown },
        ],
      })),
    }
    renderRoute('/activity', http)

    expect(await screen.findByText(/Qualified automatic/)).toBeVisible()
    const neutral = screen.getByText(/Recorded source: unexpected origin/)
    expect(neutral).toBeVisible()
    expect(neutral.textContent).not.toContain(unknown)
    expect(neutral.textContent).not.toContain('automatic')
  })

  it('keeps an unknown direct entry inside a safe shell fallback', async () => {
    renderRoute('/not-a-review-route')

    expect(await screen.findByRole('heading', { name: 'This review route does not exist.' })).toBeVisible()
    expect(screen.getByRole('link', { name: 'Open review library' })).toHaveAttribute('href', '/library?lens=needs_attention')
  })
})
