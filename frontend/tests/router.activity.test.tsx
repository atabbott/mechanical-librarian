import { act, fireEvent, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { AmbiguousMutationError, ApiError, BuildMismatchError } from '../src/api/http'
import type { ActivitySearch, ActivityView, ReviewHttp } from '../src/api/http'
import { activityRoute } from '../src/router'
import { activity, createHttp, openActivityAuthority, renderRoute } from './fixtures'

describe('Activity route', () => {
  it('polls idle Activity while mounted and tears the poll down after navigation', async () => {
    vi.useFakeTimers()
    try {
      const http = createHttp()
      const { router, queryClient } = renderRoute('/activity', http)
      await vi.waitFor(() => expect(http.getActivity).toHaveBeenCalledOnce())

      await act(async () => {
        await vi.advanceTimersByTimeAsync(5_100)
      })
      await vi.waitFor(() => expect(http.getActivity).toHaveBeenCalledTimes(2))
      await act(async () => {
        await router.navigate({ to: '/library', search: { lens: 'all' } })
      })
      // Awaiting `navigate` resolves the router, not React's passive-effect
      // cleanup, and it is that cleanup which unsubscribes the Activity query
      // observer and clears its poll interval. Wait for the observer to go
      // rather than assuming the navigation await was enough: on a cold module
      // it is not, and the count then measures a route that is still polling.
      await vi.waitFor(() => {
        const entries = queryClient
          .getQueryCache()
          .getAll()
          .filter((entry) => entry.queryKey[0] === 'activity')
        // The Activity route's own ledger query -- the one carrying its search
        // in the key -- must have no observers left. Library keeps a second,
        // compact activity read for its write handoff, which is a different
        // query on a slower cadence and is not what this measures.
        expect(
          entries
            .filter((entry) => entry.queryKey.length > 2)
            .every((entry) => entry.getObserversCount() === 0),
        ).toBe(true)
        // That compact read has also settled, so the count captured next
        // measures a cadence rather than someone's first fetch.
        expect(entries.every((entry) => entry.state.fetchStatus === 'idle')).toBe(true)
      })
      const callsAfterNavigation = vi.mocked(http.getActivity).mock.calls.length

      await act(async () => {
        await vi.advanceTimersByTimeAsync(5_100)
      })

      expect(http.getActivity).toHaveBeenCalledTimes(callsAfterNavigation)
    } finally {
      vi.useRealTimers()
    }
  })

  it('keeps incremental consent behind the protected authority control', async () => {
    const user = userEvent.setup()
    const http: ReviewHttp = {
      ...createHttp(),
      getActivity: vi.fn(async () =>
        activity({
          prepared: { automatic: 1, human: 1, total: 2 },
          configured_libraries: [
            {
              connection: 'primary',
              library: 'fiction',
              prepared: { automatic: 1, human: 1, total: 2 },
            },
          ],
          enabled_libraries: [{ connection: 'primary', library: 'fiction' }],
          capabilities: [
            {
              connection: 'primary',
              library: 'fiction',
              state: 'partially_allowed',
              destinations: [
                { destination: 'authors', state: 'allowed', consented: false, prepared: 0 },
                {
                  destination: 'subtitle',
                  state: 'field_unavailable',
                  consented: false,
                  prepared: 0,
                },
                { destination: 'title', state: 'allowed', consented: true, prepared: 0 },
              ],
            },
          ],
          events: [],
          paused: false,
        }),
      ),
    }
    renderRoute('/activity', http)

    await openActivityAuthority(user)
    const consentTrigger = screen.getByRole('button', { name: 'Review write permissions' })
    expect(consentTrigger).toHaveAttribute('aria-expanded', 'false')
    expect(consentTrigger).toHaveAttribute('aria-controls', 'activity-consent')
    await user.click(consentTrigger)
    expect(screen.getByRole('heading', { name: 'Enable guarded writes' })).toBeVisible()
    expect(screen.getByRole('button', { name: 'Enable selected libraries' })).toBeDisabled()
  })

  it.each([
    ['a build mismatch', new BuildMismatchError()],
    ['a gateway failure', new ApiError(504, 'gateway_timeout')],
    ['an invalid authoritative response', new ApiError(502, 'invalid_activity_response')],
  ])(
    'reconciles %s after a committed pause without claiming it failed safely',
    async (_name, failure) => {
      const user = userEvent.setup()
      const initial: ActivityView = activity({
        enabled_libraries: [{ connection: 'primary', library: 'fiction' }],
      })
      let current = initial
      const http: ReviewHttp = {
        ...createHttp(),
        getActivity: vi.fn(async () => current),
        activityCommand: vi.fn(async () => {
          throw failure
        }),
      }
      renderRoute('/activity', http)
      await openActivityAuthority(user)
      current = { ...initial, paused: true }
      await user.click(screen.getByRole('button', { name: 'Pause new work' }))

      await waitFor(() =>
        expect(screen.getByRole('alert')).toHaveTextContent(
          'The requested activity change was applied, confirmed by the authoritative server.',
        ),
      )
      expect(screen.getByRole('alert')).not.toHaveTextContent('failed safely')
      expect(http.activityCommand).toHaveBeenCalledOnce()
      expect(http.getActivity).toHaveBeenCalledTimes(2)
    },
  )

  it.each([
    [
      'applied',
      true,
      'The requested activity change was applied, confirmed by the authoritative server.',
    ],
    ['unconfirmed', false, 'The activity outcome could not be confirmed. No command was resent.'],
  ])('reconciles an ambiguous pause as %s without resending it', async (_name, paused, message) => {
    const user = userEvent.setup()
    const initial: ActivityView = activity({
      enabled_libraries: [{ connection: 'primary', library: 'fiction' }],
    })
    let current = initial
    const http: ReviewHttp = {
      ...createHttp(),
      getActivity: vi.fn(async () => current),
      activityCommand: vi.fn(async () => {
        throw new AmbiguousMutationError()
      }),
    }
    renderRoute('/activity', http)
    await openActivityAuthority(user)
    current = { ...initial, paused }
    await user.click(screen.getByRole('button', { name: 'Pause new work' }))

    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent(message))
    expect(http.activityCommand).toHaveBeenCalledOnce()
    expect(http.getActivity).toHaveBeenCalledTimes(2)
  })

  it('reports an unresolved ambiguous pause without calling it safe or resending it', async () => {
    const user = userEvent.setup()
    const initial: ActivityView = activity({
      enabled_libraries: [{ connection: 'primary', library: 'fiction' }],
    })
    let available = true
    const http: ReviewHttp = {
      ...createHttp(),
      getActivity: vi.fn(async () => {
        if (!available) throw new Error('offline')
        return initial
      }),
      activityCommand: vi.fn(async () => {
        throw new AmbiguousMutationError()
      }),
    }
    renderRoute('/activity', http)
    await openActivityAuthority(user)
    available = false
    await user.click(screen.getByRole('button', { name: 'Pause new work' }))

    await waitFor(() =>
      expect(screen.getByRole('alert')).toHaveTextContent(
        'The activity outcome could not be confirmed. No command was resent.',
      ),
    )
    expect(http.activityCommand).toHaveBeenCalledOnce()
  })

  it('keeps ambiguous consent unconfirmed without resending', async () => {
    const user = userEvent.setup()
    const initial: ActivityView = activity({
      configured_libraries: [
        {
          connection: 'primary',
          library: 'fiction',
          prepared: { automatic: 0, human: 0, total: 1 },
        },
      ],
      capabilities: [
        {
          connection: 'primary',
          library: 'fiction',
          state: 'allowed',
          destinations: [{ destination: 'title', state: 'allowed', consented: false, prepared: 0 }],
        },
      ],
    })
    let current = initial
    const http: ReviewHttp = {
      ...createHttp(),
      getActivity: vi.fn(async () => current),
      activityCommand: vi.fn(async () => {
        throw new AmbiguousMutationError()
      }),
    }
    renderRoute('/activity', http)
    await openActivityAuthority(user)
    await user.click(screen.getByRole('button', { name: 'Review write permissions' }))
    await user.click(screen.getByRole('checkbox'))
    current = { ...initial, capabilities: [] }
    await user.click(screen.getByRole('button', { name: 'Enable selected libraries' }))

    await waitFor(() =>
      expect(screen.getByRole('alert')).toHaveTextContent(
        'The activity outcome could not be confirmed. No command was resent.',
      ),
    )
    expect(http.activityCommand).toHaveBeenCalledOnce()
  })

  it('clears protected consent selection when current eligibility changes', async () => {
    const user = userEvent.setup()
    const partial: ActivityView = activity({
      configured_libraries: [
        {
          connection: 'primary',
          library: 'fiction',
          prepared: { automatic: 1, human: 0, total: 1 },
        },
      ],
      enabled_libraries: [],
      capabilities: [
        {
          connection: 'primary',
          library: 'fiction',
          state: 'allowed',
          destinations: [{ destination: 'title', state: 'allowed', consented: false, prepared: 0 }],
        },
      ],
    })
    let current = partial
    const http: ReviewHttp = { ...createHttp(), getActivity: vi.fn(async () => current) }
    const { queryClient } = renderRoute('/activity', http)
    await openActivityAuthority(user)
    await user.click(screen.getByRole('button', { name: 'Review write permissions' }))
    const choice = await screen.findByRole('checkbox')
    await user.click(choice)
    expect(choice).toBeChecked()

    current = {
      ...partial,
      enabled_libraries: [{ connection: 'primary', library: 'fiction' }],
      capabilities: [
        {
          connection: 'primary',
          library: 'fiction',
          state: 'allowed',
          destinations: [{ destination: 'title', state: 'allowed', consented: true, prepared: 0 }],
        },
      ],
    }
    await queryClient.invalidateQueries({ queryKey: ['activity'] })
    await waitFor(() => expect(screen.getByRole('checkbox')).toBeDisabled())
    expect(screen.getByRole('button', { name: 'Enable selected libraries' })).toBeDisabled()

    current = partial
    await queryClient.invalidateQueries({ queryKey: ['activity'] })
    const restored = await screen.findByRole('checkbox')
    expect(restored).toBeEnabled()
    expect(restored).not.toBeChecked()
    expect(screen.getByRole('button', { name: 'Enable selected libraries' })).toBeDisabled()
  })

  it('keeps unavailable consent disabled in the protected authority view', async () => {
    const http: ReviewHttp = {
      ...createHttp(),
      getActivity: vi.fn(async () =>
        activity({
          configured_libraries: [
            {
              connection: 'primary',
              library: 'blocked',
              prepared: { automatic: 2, human: 0, total: 2 },
            },
          ],
          enabled_libraries: [],
          capabilities: [
            {
              connection: 'primary',
              library: 'blocked',
              state: 'connection_unavailable',
              destinations: [
                {
                  destination: 'authors',
                  state: 'connection_unavailable',
                  consented: false,
                  prepared: 0,
                },
                {
                  destination: 'title',
                  state: 'connection_unavailable',
                  consented: false,
                  prepared: 0,
                },
              ],
            },
          ],
          events: [],
          paused: false,
        }),
      ),
    }
    renderRoute('/activity', http)

    const user = userEvent.setup()
    await openActivityAuthority(user)
    await user.click(screen.getByRole('button', { name: 'Review write permissions' }))
    expect(screen.getByRole('checkbox')).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Enable selected libraries' })).toBeDisabled()
  })

  it('bounds unknown ledger types neutrally', async () => {
    const unknown = 'unexpected_origin_' + 'x'.repeat(100)
    const http: ReviewHttp = {
      ...createHttp(),
      getActivity: vi.fn(async () =>
        activity({
          backup_ready: true,
          configured_libraries: [],
          enabled_libraries: [],
          capabilities: [],
          paused: false,
          events: [
            {
              work_id: 'book-1',
              library: 'fiction',
              destination: 'title',
              state: 'verified',
              origin: 'automatic_policy',
              occurred_at: null,
              failure: null,
              work_label: 'Work details unavailable',
            },
            {
              event_type: 'unknown',
              category: 'recovery',
              change: 'Recorded',
              change_detail: 'Recorded',
              work_id: 'book-2',
              library: 'fiction',
              destination: 'subtitle',
              state: 'stale',
              origin: unknown,
              occurred_at: null,
              failure: null,
              work_label: 'Work details unavailable',
            },
          ],
        }),
      ),
    }
    renderRoute('/activity', http)

    // An unclassifiable record reads as Recovery rather than as the internal
    // origin token it arrived with, which is never shown at all.
    const rows = await screen.findAllByText('Recovery')
    expect(rows.some((node) => node.className.includes('cm-activity-category'))).toBe(true)
    expect(document.body.textContent).not.toContain(unknown)
  })

  it.each([
    [
      'prepared',
      {
        enabled_libraries: [{ connection: 'primary', library: 'fiction' }],
        prepared: { automatic: 1, human: 0, total: 1 },
        events: [{ work_label: 'Prepared work', state: 'planned' }],
      },
      'Planned',
    ],
    [
      'applying',
      {
        enabled_libraries: [{ connection: 'primary', library: 'fiction' }],
        events: [{ work_label: 'Applying work', state: 'applying' }],
      },
      'Applying',
    ],
    [
      'waiting',
      {
        enabled_libraries: [{ connection: 'primary', library: 'fiction' }],
        events: [{ work_label: 'Waiting work', state: 'retryable' }],
      },
      'Waiting safely',
    ],
    [
      'stale',
      {
        enabled_libraries: [{ connection: 'primary', library: 'fiction' }],
        events: [{ work_label: 'Stale work', state: 'stale' }],
      },
      'Stale',
    ],
    [
      'stopped',
      {
        enabled_libraries: [{ connection: 'primary', library: 'fiction' }],
        events: [{ work_label: 'Stopped work', state: 'abandoned' }],
      },
      'Stopped safely',
    ],
    [
      'verified',
      {
        enabled_libraries: [{ connection: 'primary', library: 'fiction' }],
        events: [{ work_label: 'Verified work', state: 'verified' }],
      },
      'Verified',
    ],
    [
      'no change',
      {
        events: [
          { work_label: 'Unchanged work', state: 'no_change_required', origin: 'automatic_policy' },
        ],
      },
      'No change required',
    ],
    [
      'accepted',
      { events: [{ work_label: 'Accepted work', state: 'accept', origin: 'human' }] },
      'Accepted',
    ],
    [
      'kept current',
      { events: [{ work_label: 'Kept work', state: 'keep_current', origin: 'human' }] },
      'Kept current',
    ],
    [
      'rejected',
      { events: [{ work_label: 'Rejected work', state: 'reject', origin: 'human' }] },
      'Rejected',
    ],
    [
      'completed automatic cover advice',
      { events: [{ work_label: 'Cover work', state: 'completed', origin: 'automatic_pipeline' }] },
      'Cover advice completed',
    ],
    [
      'unavailable owner cover retry',
      { events: [{ work_label: 'Cover work', state: 'unavailable', origin: 'owner_retry' }] },
      'Cover advice unavailable',
    ],
    [
      'failed owner cover retry',
      { events: [{ work_label: 'Cover work', state: 'failed', origin: 'owner_retry' }] },
      'Cover advice failed safely',
    ],
  ])('keeps the protected ledger row truthful for %s work', async (_name, current, rowStatus) => {
    const http: ReviewHttp = {
      ...createHttp(),
      getActivity: vi.fn(async () => activity(current)),
    }
    renderRoute('/activity', http)

    expect(await screen.findByRole('heading', { name: 'No intervention needed' })).toBeVisible()
    expect(screen.getAllByText(rowStatus).at(-1)).toBeVisible()
  })

  it('keeps failed outcomes bounded in ledger rows', async () => {
    const failures = [
      'target_changed',
      'target_failure',
      'backup',
      'authority_superseded',
      'readback',
      'retry_exhausted',
    ]
    const http: ReviewHttp = {
      ...createHttp(),
      getActivity: vi.fn(async () =>
        activity({
          events: failures.map((failure, index) => ({
            work_id: `book-${String(index)}`,
            library: 'fiction',
            destination: 'title',
            state: 'abandoned',
            origin: 'human',
            occurred_at: null,
            failure,
            work_label: `Work ${String(index)}`,
          })),
        }),
      ),
    }
    renderRoute('/activity', http)

    expect(await screen.findAllByText('Stopped safely')).toHaveLength(failures.length)
    for (const failure of failures) expect(document.body.textContent).not.toContain(failure)
  })

  it('renders duplicate activity events without duplicate React keys', async () => {
    const duplicate = {
      work_id: 'book-1',
      library: 'fiction',
      destination: 'title',
      state: 'verified',
      occurred_at: '2026-08-10T00:00:00Z',
    }
    const error = vi.spyOn(console, 'error').mockImplementation(() => undefined)
    const http: ReviewHttp = {
      ...createHttp(),
      getActivity: vi.fn(async () =>
        activity({
          events: [duplicate, duplicate].map((event) => ({
            ...event,
            occurred_at: null,
            origin: '',
            failure: null,
            work_label: 'Work details unavailable',
          })),
        }),
      ),
    }
    renderRoute('/activity', http)

    await screen.findAllByText('Work details unavailable')
    await screen.findAllByText('Verified')
    expect(error).not.toHaveBeenCalledWith(expect.stringContaining('same key'), expect.anything())
    error.mockRestore()
  })

  it('keeps recovery visible without retry authority', async () => {
    const user = userEvent.setup()
    const http: ReviewHttp = {
      ...createHttp(),
      getActivity: vi.fn(async () =>
        activity({
          enabled_libraries: [{ connection: 'primary', library: 'fiction' }],
          suspensions: [
            {
              connection: 'primary',
              destination: 'title',
              reason: 'unexpected_effect_behavior',
              scope: 'destination',
              updated_at: 1,
            },
            {
              connection: 'primary',
              destination: 'authors',
              reason: 'unexpected_effect_behavior',
              scope: 'destination',
              updated_at: 1,
            },
          ],
        }),
      ),
    }
    renderRoute('/activity', http)

    expect(await screen.findByRole('heading', { name: 'Recovery needs attention' })).toBeVisible()
    await openActivityAuthority(user)
    expect(screen.getAllByText(/Unexpected effect behavior/)).toHaveLength(2)
    expect(screen.queryByRole('button', { name: /retry/i })).toBeNull()
    expect(screen.getByRole('button', { name: 'Pause new work' })).toBeVisible()
  })

  it('projects all durable provider-recovery phases with safe scope and work units', async () => {
    const http: ReviewHttp = {
      ...createHttp(),
      getActivity: vi.fn(async () =>
        activity({
          provider_recovery: [
            { state: 'waiting', affected_count: 2, next_eligible_at: 1 },
            { state: 'checking', affected_count: 1, next_eligible_at: null },
            { state: 'fanout', affected_count: 3, next_eligible_at: null },
            { state: 'recovered', affected_count: 1, next_eligible_at: null },
            { state: 'dormant', affected_count: 2, next_eligible_at: null },
            { state: 'exhausted', affected_count: 1, next_eligible_at: null },
          ],
        }),
      ),
    }
    renderRoute('/activity', http)

    expect(await screen.findByRole('heading', { name: 'Provider recovery' })).toBeVisible()
    expect(screen.getByText('Waiting for a safe retry').parentElement).toHaveTextContent(
      '2 affected scopes',
    )
    expect(screen.getByText('Checking provider health').parentElement).toHaveTextContent(
      '1 affected scope',
    )
    expect(screen.getByText('Retrying affected work').parentElement).toHaveTextContent(
      '3 affected works',
    )
    expect(screen.getByText('Recovered').parentElement).toHaveTextContent('1 affected scope')
    expect(screen.getByText('Needs a configuration change').parentElement).toHaveTextContent(
      '2 affected scopes',
    )
    expect(screen.getByText('No further retry scheduled').parentElement).toHaveTextContent(
      '1 affected scope',
    )
    expect(screen.queryByRole('button', { name: /provider.*retry/i })).toBeNull()
  })

  it('retains distinct recovery facts without a completion estimate', async () => {
    const user = userEvent.setup()
    const http: ReviewHttp = {
      ...createHttp(),
      getActivity: vi.fn(async () =>
        activity({
          enabled_libraries: [{ connection: 'primary', library: 'fiction' }],
          suspensions: [
            {
              connection: 'primary',
              destination: 'title',
              reason: 'unexpected_effect_behavior',
              scope: 'destination',
              updated_at: 1,
            },
            {
              connection: 'primary',
              destination: 'subtitle',
              reason: 'field_unavailable',
              scope: 'destination',
              updated_at: 1,
            },
            {
              connection: 'secondary',
              destination: null,
              reason: 'connection_unavailable',
              scope: 'connection',
              updated_at: 1,
            },
          ],
        }),
      ),
    }
    renderRoute('/activity', http)

    await openActivityAuthority(user)
    expect(await screen.findAllByText(/^Recovery:/)).toHaveLength(3)
    expect(screen.queryByRole('progressbar')).toBeNull()
  })

  it('retains backup-disabled consent beside existing history', async () => {
    const user = userEvent.setup()
    const http: ReviewHttp = {
      ...createHttp(),
      getActivity: vi.fn(async () =>
        activity({
          backup_ready: false,
          prepared: { automatic: 1, human: 0, total: 1 },
          configured_libraries: [
            {
              connection: 'primary',
              library: 'fiction',
              prepared: { automatic: 1, human: 0, total: 1 },
            },
          ],
          capabilities: [
            {
              connection: 'primary',
              library: 'fiction',
              state: 'allowed',
              destinations: [
                { destination: 'title', state: 'allowed', consented: false, prepared: 0 },
              ],
            },
          ],
          events: [
            {
              work_id: 'book-1',
              library: 'fiction',
              destination: 'title',
              state: 'verified',
              origin: 'human',
              occurred_at: null,
              failure: null,
              work_label: 'Recorded work',
            },
          ],
        }),
      ),
    }
    renderRoute('/activity', http)

    expect(await screen.findByText('Recorded work')).toBeVisible()
    await openActivityAuthority(user)
    await user.click(screen.getByRole('button', { name: 'Review write permissions' }))
    expect(await screen.findByRole('checkbox')).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Enable selected libraries' })).toBeDisabled()
  })

  it('does not invent a pause control after revocation', async () => {
    const user = userEvent.setup()
    const http: ReviewHttp = {
      ...createHttp(),
      getActivity: vi.fn(async () =>
        activity({
          configured_libraries: [
            {
              connection: 'primary',
              library: 'fiction',
              prepared: { automatic: 0, human: 0, total: 0 },
            },
          ],
          capabilities: [
            {
              connection: 'primary',
              library: 'fiction',
              state: 'allowed',
              destinations: [
                { destination: 'title', state: 'allowed', consented: false, prepared: 0 },
              ],
            },
          ],
          events: [
            {
              work_id: 'book-1',
              library: 'fiction',
              destination: 'title',
              state: 'verified',
              origin: 'human',
              occurred_at: null,
              failure: null,
              work_label: 'Recorded work',
            },
          ],
        }),
      ),
    }
    renderRoute('/activity', http)

    expect(await screen.findByText('Recorded work')).toBeVisible()
    await openActivityAuthority(user)
    await user.click(screen.getByRole('button', { name: 'Review write permissions' }))
    expect(await screen.findByRole('heading', { name: 'Enable guarded writes' })).toBeVisible()
    expect(screen.getByRole('checkbox')).toBeDisabled()
    expect(screen.queryByRole('button', { name: /pause new work|resume new work/i })).toBeNull()
  })

  it('requires an explicit resume for current waiting work while paused', async () => {
    const user = userEvent.setup()
    const http: ReviewHttp = {
      ...createHttp(),
      getActivity: vi.fn(async () =>
        activity({
          paused: true,
          enabled_libraries: [{ connection: 'primary', library: 'fiction' }],
          current_waiting: {
            destination: 'title',
            library: 'fiction',
            state: 'retryable',
            work_id: 'current-work',
            work_label: 'Current waiting work',
          },
        }),
      ),
    }
    renderRoute('/activity', http)

    expect(await screen.findByText('New work paused')).toBeVisible()
    expect(
      screen.getByText(
        'Resume new work when you are ready. No automatic check will start while new work is paused.',
      ),
    ).toBeVisible()
    expect(
      screen.queryByText('Next exact-current check runs automatically. No action is required.'),
    ).toBeNull()
    await openActivityAuthority(user)
    expect(screen.getByRole('button', { name: 'Resume new work' })).toBeVisible()
  })

  it('reads work, exact change, and result in order, and announces the change as a sentence', async () => {
    const user = userEvent.setup()
    const http: ReviewHttp = {
      ...createHttp(),
      getActivity: vi.fn(async () =>
        activity({
          events: [
            {
              event_id: 'owner-set',
              category: 'owner',
              event_type: 'review',
              field_label: 'Title',
              library: 'fiction',
              destination: 'title',
              state: 'verified',
              origin: 'human',
              occurred_at: 1,
              work_label: 'Paper Moons over Larkspur Station',
              change: 'Paper Moons: Larkspur → Paper Moons over Larkspur Station',
              change_detail:
                'Set Title from Paper Moons: Larkspur to Paper Moons over Larkspur Station',
              evidence: [
                { label: 'Previous value', value: 'Paper Moons: Larkspur' },
                { label: 'New value', value: 'Paper Moons over Larkspur Station' },
                { label: 'Chosen by', value: 'Library owner' },
                {
                  label: 'Confirmed in Audiobookshelf',
                  value: 'Confirmed as Paper Moons over Larkspur Station',
                },
              ],
            },
            {
              event_id: 'policy-activated',
              category: 'policy',
              event_type: 'policy',
              field_label: 'Policy',
              library: 'Library policy',
              destination: 'policy',
              state: 'completed',
              origin: 'policy',
              occurred_at: 1,
              work_label: 'Library policy',
              change: '2 changes activated',
              change_detail: '2 changes activated',
              policy_changes: [
                {
                  field: 'Title layout',
                  setting: 'Review mode',
                  previous: 'Ask me',
                  next: 'Automatic',
                },
                {
                  field: 'Description fill',
                  setting: 'Representation',
                  previous: 'Safe plain text',
                  next: 'Visible Google text',
                },
              ],
              evidence: [
                { label: 'Changed by', value: 'Library owner' },
                { label: 'Active policy', value: 'Updated with all 2 changes' },
              ],
            },
          ],
        }),
      ),
    }
    renderRoute('/activity', http)

    await screen.findByRole('heading', { name: 'Activity and recovery' })
    // The row is read in the order the ticket names: work, field, exact
    // change, result. The arrow the column shows is hidden from the
    // announcement, which carries the same change as a whole sentence.
    expect(screen.getByText('Title · fiction')).toBeVisible()
    const announced = screen.getByLabelText(
      'Set Title from Paper Moons: Larkspur to Paper Moons over Larkspur Station',
    )
    expect(announced).toHaveTextContent('Paper Moons: Larkspur → Paper Moons over Larkspur Station')
    expect(announced.querySelector('[aria-hidden="true"]')).not.toBeNull()
    expect(screen.getByText('Verified')).toBeVisible()

    // A verified write whose authority was human is an Owner choice, not a
    // Review, and no row repeats its own lifecycle back at it.
    const categories = screen
      .getAllByText(/^(Owner choice|Policy change)$/)
      .filter((node) => node.className.includes('cm-activity-category'))
    expect(categories.map((node) => node.textContent)).toEqual(['Owner choice', 'Policy change'])

    // Only the Policy record carries the exact From/To; the owner change keeps
    // the compact detail density beside it.
    const disclosureFor = (work: string) =>
      within(
        screen.getByText(work).closest('article') ?? document.createElement('article'),
      ).getByRole('button', { name: 'Details' })
    await user.click(disclosureFor('Paper Moons over Larkspur Station'))
    expect(screen.getByText('Confirmed in Audiobookshelf')).toBeVisible()
    expect(screen.queryByRole('region', { name: /exact policy changes/ })).toBeNull()
    await user.click(screen.getByRole('button', { name: 'Hide details' }))
    await user.click(disclosureFor('Library policy'))
    const table = screen.getByLabelText('2 exact policy changes')
    expect(table).toHaveTextContent('Title layout')
    expect(table).toHaveTextContent('Review mode')
    expect(table).toHaveTextContent('Ask me')
    expect(table).toHaveTextContent('Automatic')
    expect(table).toHaveTextContent('Visible Google text')
  })

  it('opens and closes a record from the keyboard with a truthful disclosure label', async () => {
    const user = userEvent.setup()
    const http: ReviewHttp = {
      ...createHttp(),
      getActivity: vi.fn(async () =>
        activity({
          events: [
            {
              event_id: 'keyboard',
              change: 'Not set → Earthsea Cycle',
              evidence: [{ label: 'Previous value', value: 'Not set' }],
            },
          ],
        }),
      ),
    }
    renderRoute('/activity', http)

    await screen.findByRole('heading', { name: 'Activity and recovery' })
    // The route moves focus to its own heading on arrival. Wait for that to
    // settle before taking the keyboard, or the press lands on the heading.
    await waitFor(() => expect(document.querySelector('[data-route-focus]')).toHaveFocus())
    const disclosure = screen.getByRole('button', { name: 'Details' })
    disclosure.focus()
    await user.keyboard('{Enter}')
    expect(disclosure).toHaveAttribute('aria-expanded', 'true')
    expect(disclosure).toHaveAccessibleName('Hide details')
    expect(screen.getByText('Previous value')).toBeVisible()
    disclosure.focus()
    await user.keyboard('{Enter}')
    expect(disclosure).toHaveAttribute('aria-expanded', 'false')
    expect(disclosure).toHaveAccessibleName('Details')
    expect(screen.queryByText('Previous value')).toBeNull()
  })

  it('reaches every bounded lifecycle transition and recovers a disclosure read', async () => {
    const user = userEvent.setup()
    const transitions = Array.from({ length: 25 }, (_, index) => ({
      event_id: `transition-${String(index + 1)}`,
      occurred_at: index + 1,
      sequence: index + 1,
      state: index === 24 ? 'verified' : 'planned',
    }))
    let failFirst = true
    const http: ReviewHttp = {
      ...createHttp(),
      getActivity: vi.fn(async () => activity({ events: [{ event_id: 'lifecycle' }] })),
      getActivityTransitions: vi.fn(async (_eventId: string, afterSequence?: number) => {
        if (failFirst) {
          failFirst = false
          throw new ApiError(503, 'unavailable')
        }
        const start = afterSequence ?? 0
        const events = transitions.slice(start, start + 20)
        return { events, has_more: start + events.length < transitions.length }
      }),
    }
    renderRoute('/activity', http)

    await user.click(await screen.findByRole('button', { name: 'Details' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Recorded transitions could not be loaded.',
    )
    await user.click(screen.getByRole('button', { name: 'Try again' }))
    const lifecycle = await screen.findByRole('list', { name: 'Recorded lifecycle transitions' })
    expect(lifecycle.querySelectorAll('li')).toHaveLength(21)
    await user.click(screen.getByRole('button', { name: 'Load more transitions' }))
    await waitFor(() => expect(lifecycle.querySelectorAll('li')).toHaveLength(25))
    expect(http.getActivityTransitions).toHaveBeenLastCalledWith('lifecycle', 20)
  })

  it('never says no action is required while its own prepared work awaits consent', async () => {
    const http: ReviewHttp = {
      ...createHttp(),
      getActivity: vi.fn(async () =>
        activity({
          backup_ready: true,
          configured_libraries: [
            {
              connection: 'primary',
              library: 'fiction',
              prepared: { automatic: 2, human: 1, total: 3 },
            },
          ],
          capabilities: [
            {
              connection: 'primary',
              library: 'fiction',
              state: 'allowed',
              destinations: [
                { destination: 'title', state: 'allowed', consented: false, prepared: 3 },
              ],
            },
          ],
          waiting: {
            applying: 0,
            awaiting_permission: 3,
            held_for_recovery: 0,
            retrying: 0,
            scheduled: 0,
          },
          current_waiting: {
            destination: 'title',
            library: 'fiction',
            state: 'planned',
            work_id: 'book-1',
            work_label: 'The Lathe of Heaven',
          },
        }),
      ),
    }
    renderRoute('/activity', http)

    // The offer and the status panel read the same consent state, so the page
    // cannot claim calm directly above the count that refutes it (#639).
    expect(await screen.findByText('3 prepared changes are ready. Writes are off.')).toBeVisible()
    expect(
      screen.getByRole('heading', { name: 'Prepared work needs your permission' }),
    ).toBeVisible()
    expect(screen.getByText('Awaiting permission')).toBeVisible()
    for (const calm of [
      'No intervention needed',
      'No action required',
      'No action is required.',
      'Guarded work continues automatically. Recovery controls appear only when your decision is required.',
    ])
      expect(screen.queryByText(calm)).toBeNull()
    expect(screen.getByText('Action needed')).toBeVisible()
  })

  it('returns to a calm status once every prepared change has permission', async () => {
    const http: ReviewHttp = {
      ...createHttp(),
      getActivity: vi.fn(async () =>
        activity({
          enabled_libraries: [{ connection: 'primary', library: 'fiction' }],
          configured_libraries: [
            {
              connection: 'primary',
              library: 'fiction',
              prepared: { automatic: 2, human: 1, total: 3 },
            },
          ],
          capabilities: [
            {
              connection: 'primary',
              library: 'fiction',
              state: 'allowed',
              destinations: [
                { destination: 'title', state: 'allowed', consented: true, prepared: 3 },
              ],
            },
          ],
          waiting: {
            applying: 0,
            awaiting_permission: 0,
            held_for_recovery: 0,
            retrying: 0,
            scheduled: 3,
          },
        }),
      ),
    }
    renderRoute('/activity', http)

    expect(await screen.findByRole('heading', { name: 'No intervention needed' })).toBeVisible()
    expect(
      screen.queryByRole('heading', { name: 'Prepared work needs your permission' }),
    ).toBeNull()
  })

  it('keeps the protected ledger query, evidence, and owner controls bounded', async () => {
    const user = userEvent.setup()
    const http: ReviewHttp = {
      ...createHttp(),
      getActivity: vi.fn(async () =>
        activity({
          enabled_libraries: [{ connection: 'primary', library: 'fiction' }],
          ledger: {
            event_type: 'all',
            page: 1,
            page_count: 3,
            page_size: 25,
            query: null,
            range_days: 30,
            total: 51,
          },
          current_waiting: {
            destination: 'title',
            library: 'fiction',
            state: 'retryable',
            work_id: 'current-work',
            work_label: 'Current waiting work',
          },
          events: [
            {
              event_id: 'event-proof',
              event_type: 'safety',
              category: 'recovery',
              change: 'Prepared change paused',
              change_detail: 'Prepared change paused',
              field_label: 'Title',
              evidence: [
                { label: 'Prepared value', value: 'A Wizard of Earthsea' },
                {
                  label: 'Why it stopped',
                  value: 'The current value changed after this was prepared',
                },
              ],
              work_id: 'book-1',
              library: 'fiction',
              destination: 'title',
              state: 'verified',
              origin: 'human',
              occurred_at: 1,
              failure: null,
              work_label: 'Recorded work',
            },
          ],
        }),
      ),
    }
    renderRoute('/activity', http)

    expect(await screen.findByRole('heading', { name: 'Activity and recovery' })).toBeVisible()
    expect(screen.getByRole('heading', { name: 'No intervention needed' })).toBeVisible()
    await openActivityAuthority(user)
    expect(screen.getByRole('button', { name: 'Pause new work' })).toBeVisible()
    // The single ambiguous number is gone: waiting is now stated as the four
    // different next steps an owner has (#639).
    expect(screen.queryByText('Waiting safely')).toBeNull()
    for (const reason of ['Awaiting permission', 'Scheduled', 'Retrying', 'Held for recovery'])
      expect(screen.getByText(reason)).toBeVisible()
    // The row reads work, field, exact change, result -- and the disclosure
    // says which of its two states it is in (#636).
    expect(screen.getByText('Prepared change paused')).toBeVisible()
    expect(screen.getByText('Title · fiction')).toBeVisible()
    await user.click(screen.getByRole('button', { name: 'Details' }))
    expect(screen.getByText('Prepared value')).toBeVisible()
    expect(screen.getByText('The current value changed after this was prepared')).toBeVisible()
    expect(screen.queryByText('Durable evidence')).toBeNull()
    expect(screen.getByText('Current waiting work · Title update')).toBeVisible()
    await user.click(screen.getByRole('button', { name: 'Hide details' }))
    const pageInput = screen.getByRole('spinbutton', { name: 'Activity page number' })
    await user.clear(pageInput)
    await user.type(pageInput, '3')
    await user.click(screen.getByRole('button', { name: 'Policy changes' }))
    await waitFor(() =>
      expect(http.getActivity).toHaveBeenLastCalledWith(
        expect.objectContaining({ eventType: 'policy', page: 1 }),
        expect.anything(),
      ),
    )
    expect(screen.getByRole('spinbutton', { name: 'Activity page number' })).toHaveValue(1)
    await user.click(screen.getByRole('button', { name: 'Next' }))
    await waitFor(() =>
      expect(http.getActivity).toHaveBeenLastCalledWith(
        expect.objectContaining({ eventType: 'policy', page: 2 }),
        expect.anything(),
      ),
    )
  })

  it.each([
    ['after the stale restore is queued', true],
    ['before the final navigation resolves', false],
  ])(
    'keeps Inspect focused when Activity All navigation completes %s',
    async (_ordering, restoreQueued) => {
      const user = userEvent.setup()
      const frames: FrameRequestCallback[] = []
      const requestFrame = vi
        .spyOn(window, 'requestAnimationFrame')
        .mockImplementation((callback) => {
          frames.push(callback)
          return frames.length
        })
      const cancelFrame = vi
        .spyOn(window, 'cancelAnimationFrame')
        .mockImplementation(() => undefined)
      let releaseNavigation: (() => void) | undefined
      let restoreNavigation: (() => void) | undefined
      const view = (search: ActivitySearch) =>
        activity({
          ledger: {
            event_type: search.eventType,
            page: search.page,
            page_count: 1,
            page_size: search.pageSize,
            query: search.q ?? null,
            range_days: search.rangeDays,
            total: 1,
          },
          events: [
            {
              evidence: [{ label: 'Previous value', value: 'Not set' }],
              work_label: 'Focused activity',
            },
          ],
        })
      const http: ReviewHttp = {
        ...createHttp(),
        getActivity: vi.fn((search: ActivitySearch) => Promise.resolve(view(search))),
      }
      try {
        const { router } = renderRoute('/activity', http)
        await screen.findByRole('heading', { name: 'Activity and recovery' })
        frames.length = 0
        await user.click(screen.getByRole('button', { name: 'Owner choices' }))
        await waitFor(() =>
          expect(http.getActivity).toHaveBeenLastCalledWith(
            expect.objectContaining({ eventType: 'review' }),
            expect.anything(),
          ),
        )
        const originalNavigate = router.navigate.bind(router)
        const delayedNavigate = vi.spyOn(router, 'navigate').mockImplementation(async (options) => {
          const result = await originalNavigate(options)
          return await new Promise<typeof result>((resolve) => {
            releaseNavigation = () => resolve(result)
          })
        })
        restoreNavigation = () => delayedNavigate.mockRestore()
        await user.click(screen.getByRole('button', { name: 'All' }))
        await waitFor(() => expect(releaseNavigation).toBeTypeOf('function'))
        if (restoreQueued) {
          act(() => {
            releaseNavigation?.()
          })
          await waitFor(() => expect(frames).not.toHaveLength(0))
          act(() => {
            frames.shift()?.(0)
          })
        }
        const inspect = screen.getByRole('button', { name: 'Details' })
        act(() => {
          inspect.focus()
        })
        expect(inspect).toHaveFocus()
        if (!restoreQueued)
          act(() => {
            releaseNavigation?.()
          })
        await act(async () => {
          await Promise.resolve()
        })
        act(() => {
          while (frames.length) frames.shift()?.(0)
        })
        expect(inspect).toHaveFocus()
        await user.keyboard('{Enter}')
        expect(inspect).toHaveAttribute('aria-expanded', 'true')
        expect(screen.getByText('Previous value')).toBeVisible()
      } finally {
        requestFrame.mockRestore()
        cancelFrame.mockRestore()
        restoreNavigation?.()
      }
    },
  )

  it('preserves Activity drafts and control focus across interleaved route completion', async () => {
    const user = userEvent.setup()
    const http: ReviewHttp = {
      ...createHttp(),
      getActivity: vi.fn(async (search: ActivitySearch) =>
        activity({
          ledger: {
            event_type: search.eventType,
            page: search.page,
            page_count: 3,
            page_size: search.pageSize,
            query: search.q ?? null,
            range_days: search.rangeDays,
            total: 75,
          },
        }),
      ),
    }
    renderRoute('/activity', http)

    await screen.findByRole('heading', { name: 'Activity and recovery' })
    await waitFor(() =>
      expect(document.getElementById('main-content')).toHaveAttribute('data-route-focus-identity'),
    )
    const search = screen.getByRole('searchbox', { name: 'Search activity' })
    await user.click(search)
    vi.useFakeTimers()
    try {
      fireEvent.change(search, { target: { value: 'a' } })
      fireEvent.change(search, { target: { value: 'ab' } })
      await act(async () => {
        await vi.advanceTimersByTimeAsync(250)
      })
      expect(search).toHaveValue('ab')
      expect(http.getActivity).toHaveBeenLastCalledWith(
        expect.objectContaining({ q: 'ab', page: 1 }),
        expect.anything(),
      )
      expect(screen.getByRole('searchbox', { name: 'Search activity' })).toHaveFocus()

      fireEvent.change(search, { target: { value: '' } })
      fireEvent.change(search, { target: { value: 'desktop' } })
      await act(async () => {
        await vi.advanceTimersByTimeAsync(250)
      })
      expect(http.getActivity).toHaveBeenLastCalledWith(
        expect.objectContaining({ q: 'desktop', page: 1 }),
        expect.anything(),
      )
      expect(search).toHaveValue('desktop')
      expect(search).toHaveFocus()
    } finally {
      vi.useRealTimers()
    }

    const next = screen.getByRole('button', { name: 'Next' })
    await user.click(next)
    await waitFor(() =>
      expect(http.getActivity).toHaveBeenLastCalledWith(
        expect.objectContaining({ q: 'desktop', page: 2 }),
        expect.anything(),
      ),
    )
    await waitFor(() => expect(screen.getByRole('button', { name: 'Next' })).toHaveFocus())

    const page = screen.getByRole('spinbutton', { name: 'Activity page number' })
    await user.clear(page)
    await user.type(page, '99{Enter}')
    await waitFor(() =>
      expect(http.getActivity).toHaveBeenLastCalledWith(
        expect.objectContaining({ page: 3 }),
        expect.anything(),
      ),
    )
    await waitFor(() =>
      expect(screen.getByRole('spinbutton', { name: 'Activity page number' })).toHaveFocus(),
    )

    const currentPage = screen.getByRole('spinbutton', { name: 'Activity page number' })
    await user.clear(currentPage)
    await user.type(currentPage, '2{Escape}')
    expect(http.getActivity).toHaveBeenLastCalledWith(
      expect.objectContaining({ page: 3 }),
      expect.anything(),
    )
    await waitFor(() =>
      expect(screen.getByRole('spinbutton', { name: 'Activity page number' })).toHaveValue(3),
    )
    await waitFor(() =>
      expect(screen.getByRole('spinbutton', { name: 'Activity page number' })).toHaveFocus(),
    )

    const dateRange = screen.getByRole('combobox', { name: 'Activity date range' })
    await user.click(dateRange)
    await user.click(screen.getByRole('option', { name: 'Last 90 days' }))
    await waitFor(() =>
      expect(screen.getByRole('combobox', { name: 'Activity date range' })).toHaveFocus(),
    )

    const escapedPage = screen.getByRole('spinbutton', { name: 'Activity page number' })
    await user.clear(escapedPage)
    await user.type(escapedPage, '2{Escape}')
    await waitFor(() =>
      expect(screen.getByRole('spinbutton', { name: 'Activity page number' })).toHaveFocus(),
    )

    const rows = screen.getByRole('combobox', { name: 'Rows per activity page' })
    await user.click(rows)
    await user.click(screen.getByRole('option', { name: '50 per page' }))
    await waitFor(() =>
      expect(screen.getByRole('combobox', { name: 'Rows per activity page' })).toHaveFocus(),
    )

    await user.click(screen.getByRole('button', { name: 'Last' }))
    await waitFor(() =>
      expect(screen.getByRole('spinbutton', { name: 'Activity page number' })).toHaveValue(3),
    )
    const previous = screen.getByRole('button', { name: 'Previous' })
    act(() => {
      previous.click()
      previous.click()
    })
    await waitFor(() =>
      expect(screen.getByRole('spinbutton', { name: 'Activity page number' })).toHaveValue(1),
    )
    await waitFor(() =>
      expect(screen.getByRole('spinbutton', { name: 'Activity page number' })).toHaveFocus(),
    )

    await user.click(screen.getByRole('button', { name: 'Last' }))
    await waitFor(() =>
      expect(screen.getByRole('spinbutton', { name: 'Activity page number' })).toHaveValue(3),
    )
    await waitFor(() =>
      expect(screen.getByRole('spinbutton', { name: 'Activity page number' })).toHaveFocus(),
    )

    await waitFor(() =>
      expect(http.getActivity).toHaveBeenLastCalledWith(
        expect.objectContaining({ page: 3, pageSize: 50 }),
        expect.anything(),
      ),
    )
  })

  it('does not let an unmounted Activity focus callback target a fresh Activity route', async () => {
    const frames = new Map<number, FrameRequestCallback>()
    let nextFrame = 1
    const requestFrame = vi
      .spyOn(window, 'requestAnimationFrame')
      .mockImplementation((callback) => {
        const frame = nextFrame++
        frames.set(frame, callback)
        return frame
      })
    const cancelFrame = vi.spyOn(window, 'cancelAnimationFrame').mockImplementation((frame) => {
      frames.delete(frame)
    })
    try {
      const getActivity = vi.fn(async (search: ActivitySearch) =>
        activity({
          ledger: {
            event_type: search.eventType,
            page: search.page,
            page_count: 3,
            page_size: search.pageSize,
            query: search.q ?? null,
            range_days: search.rangeDays,
            total: 75,
          },
        }),
      )
      const { router } = renderRoute('/activity', { ...createHttp(), getActivity })
      await screen.findByRole('heading', { name: 'Activity and recovery' })

      fireEvent.click(screen.getByRole('button', { name: 'Next' }))
      await waitFor(() =>
        expect(screen.getByRole('spinbutton', { name: 'Activity page number' })).toHaveValue(2),
      )
      const outer = [...frames.entries()].at(-1)
      expect(outer).toBeDefined()
      await act(async () => {
        outer?.[1](0)
      })
      const staleInner = [...frames.entries()].at(-1)
      expect(staleInner).toBeDefined()

      await act(async () => {
        await router.navigate({ to: '/library', search: { lens: 'all' } })
      })
      await screen.findByRole('heading', { level: 2, name: 'Works' })
      await act(async () => {
        await router.navigate({
          to: '/activity',
          search: { eventType: 'all', rangeDays: 30, page: 1, pageSize: 25 },
        })
      })
      await screen.findByRole('heading', { name: 'Activity and recovery' })
      const freshSearch = screen.getByRole('searchbox', { name: 'Search activity' })
      freshSearch.focus()

      await act(async () => {
        staleInner?.[1](0)
      })
      expect(freshSearch).toHaveFocus()
    } finally {
      requestFrame.mockRestore()
      cancelFrame.mockRestore()
    }
  })

  it('commits the next page edit once after an Escape cancellation', async () => {
    const user = userEvent.setup()
    const getActivity = vi.fn(async (search: ActivitySearch) =>
      activity({
        ledger: {
          event_type: search.eventType,
          page: search.page,
          page_count: 3,
          page_size: search.pageSize,
          query: search.q ?? null,
          range_days: search.rangeDays,
          total: 75,
        },
      }),
    )
    renderRoute('/activity', { ...createHttp(), getActivity })

    const heading = await screen.findByRole('heading', { name: 'Activity and recovery' })
    const focusTarget = heading.closest<HTMLElement>('[data-route-focus]')
    expect(focusTarget).not.toBeNull()
    await waitFor(() => expect(focusTarget).toHaveFocus())
    const page = screen.getByRole('spinbutton', { name: 'Activity page number' })
    const callsBeforeEscape = getActivity.mock.calls.length
    await user.clear(page)
    await user.type(page, '2{Escape}')
    expect(page).toHaveValue(1)
    expect(page).toHaveFocus()
    expect(getActivity).toHaveBeenCalledTimes(callsBeforeEscape)

    await user.clear(page)
    await user.type(page, '2')
    await user.click(screen.getByRole('searchbox', { name: 'Search activity' }))
    await waitFor(() =>
      expect(getActivity).toHaveBeenLastCalledWith(
        expect.objectContaining({ page: 2 }),
        expect.anything(),
      ),
    )
    expect(getActivity.mock.calls.filter(([search]) => search.page === 2)).toHaveLength(1)
  })

  it.each([
    ['a', 'ab'],
    ['ab', 'a'],
  ] as const)(
    'keeps the latest Activity query draft when deferred replacements complete %s then %s',
    async (first, second) => {
      const deferred = new Map<string, { resolve: (value: ActivityView) => void }>()
      const http: ReviewHttp = {
        ...createHttp(),
        getActivity: vi.fn((search: ActivitySearch) => {
          if (search.q !== 'a' && search.q !== 'ab')
            return Promise.resolve(
              activity({
                ledger: {
                  event_type: search.eventType,
                  page: search.page,
                  page_count: 3,
                  page_size: search.pageSize,
                  query: search.q ?? null,
                  range_days: search.rangeDays,
                  total: 75,
                },
              }),
            )
          return new Promise<ActivityView>((resolve) => {
            deferred.set(search.q ?? '', { resolve })
          })
        }),
      }
      renderRoute('/activity', http)
      await screen.findByRole('heading', { name: 'Activity and recovery' })
      vi.useFakeTimers()
      try {
        const query = screen.getByRole('searchbox', { name: 'Search activity' })
        fireEvent.change(query, { target: { value: 'a' } })
        await act(async () => {
          await vi.advanceTimersByTimeAsync(150)
        })
        expect(deferred.has('a')).toBe(true)
        fireEvent.change(query, { target: { value: 'ab' } })
        await act(async () => {
          await vi.advanceTimersByTimeAsync(150)
        })
        expect(deferred.has('ab')).toBe(true)

        act(() => {
          deferred.get(first)?.resolve(
            activity({
              ledger: {
                event_type: 'all',
                page: 1,
                page_count: 3,
                page_size: 25,
                query: first,
                range_days: 30,
                total: 75,
              },
            }),
          )
        })
        act(() => {
          deferred.get(second)?.resolve(
            activity({
              ledger: {
                event_type: 'all',
                page: 1,
                page_count: 3,
                page_size: 25,
                query: second,
                range_days: 30,
                total: 75,
              },
            }),
          )
        })

        await act(async () => {
          await vi.advanceTimersByTimeAsync(1)
        })
        expect(screen.getByRole('searchbox', { name: 'Search activity' })).toHaveValue('ab')
      } finally {
        vi.useRealTimers()
      }
    },
  )

  it('keeps slow sequential search editing focused when a prior dropdown replacement settles late', async () => {
    const deferred = new Map<string, { resolve: (value: ActivityView) => void }>()
    const http: ReviewHttp = {
      ...createHttp(),
      getActivity: vi.fn((search: ActivitySearch) => {
        const key = search.q ?? `rows-${String(search.pageSize)}`
        if (search.pageSize !== 50 && !search.q?.startsWith('desktop'))
          return Promise.resolve(
            activity({
              ledger: {
                event_type: search.eventType,
                page: search.page,
                page_count: 3,
                page_size: search.pageSize,
                query: search.q ?? null,
                range_days: search.rangeDays,
                total: 75,
              },
            }),
          )
        return new Promise<ActivityView>((resolve) => {
          deferred.set(key, { resolve })
        })
      }),
    }
    const user = userEvent.setup({ delay: 175 })
    renderRoute('/activity', http)

    await screen.findByRole('heading', { name: 'Activity and recovery' })
    const rows = screen.getByRole('combobox', { name: 'Rows per activity page' })
    await user.click(rows)
    await user.click(screen.getByRole('option', { name: '50 per page' }))
    await waitFor(() => expect(deferred.has('rows-50')).toBe(true))

    const search = screen.getByRole('searchbox', { name: 'Search activity' })
    await user.click(search)
    await user.type(search, 'desktop')
    await waitFor(() => expect(deferred.has('desktop')).toBe(true))
    expect(screen.getByRole('searchbox', { name: 'Search activity' })).toHaveValue('desktop')
    expect(screen.getByRole('searchbox', { name: 'Search activity' })).toHaveFocus()

    act(() => {
      deferred.get('rows-50')?.resolve(
        activity({
          ledger: {
            event_type: 'all',
            page: 1,
            page_count: 3,
            page_size: 50,
            query: null,
            range_days: 30,
            total: 75,
          },
        }),
      )
    })
    await waitFor(() =>
      expect(screen.getByRole('searchbox', { name: 'Search activity' })).toHaveValue('desktop'),
    )
    expect(screen.getByRole('searchbox', { name: 'Search activity' })).toHaveFocus()

    act(() => {
      deferred.get('desktop')?.resolve(
        activity({
          ledger: {
            event_type: 'all',
            page: 1,
            page_count: 3,
            page_size: 50,
            query: 'desktop',
            range_days: 30,
            total: 75,
          },
        }),
      )
    })
    await waitFor(() =>
      expect(http.getActivity).toHaveBeenLastCalledWith(
        expect.objectContaining({ q: 'desktop' }),
        expect.anything(),
      ),
    )
  }, 10_000)

  it('retains the Activity search draft through a pending same-query page-size replacement', async () => {
    expect(activityRoute.options.pendingMs).toBe(Infinity)
    let resolveRows: ((value: ActivityView) => void) | undefined
    const http: ReviewHttp = {
      ...createHttp(),
      getActivity: vi.fn((search: ActivitySearch) => {
        if (search.q === 'ab' && search.pageSize === 50)
          return new Promise<ActivityView>((resolve) => {
            resolveRows = resolve
          })
        return Promise.resolve(
          activity({
            ledger: {
              event_type: search.eventType,
              page: search.page,
              page_count: 3,
              page_size: search.pageSize,
              query: search.q ?? null,
              range_days: search.rangeDays,
              total: 75,
            },
          }),
        )
      }),
    }
    const user = userEvent.setup()
    renderRoute('/activity?q=ab', http)

    const search = await screen.findByRole('searchbox', { name: 'Search activity' })
    const rows = screen.getByRole('combobox', { name: 'Rows per activity page' })
    await user.click(rows)
    await user.click(screen.getByRole('option', { name: '50 per page' }))
    await waitFor(() => expect(resolveRows).toBeTypeOf('function'))
    search.focus()
    await user.clear(search)
    expect(screen.getByRole('searchbox', { name: 'Search activity' })).toBe(search)
    expect(search).toHaveValue('')
    expect(search).toHaveFocus()

    act(() => {
      resolveRows?.(
        activity({
          ledger: {
            event_type: 'all',
            page: 1,
            page_count: 3,
            page_size: 50,
            query: 'ab',
            range_days: 30,
            total: 75,
          },
        }),
      )
    })
    await waitFor(() => expect(search).toHaveValue(''))
    expect(screen.getByRole('searchbox', { name: 'Search activity' })).toBe(search)
    expect(search).toHaveFocus()

    await user.type(search, 'desktop')
    expect(search).toHaveValue('desktop')
    expect(search).toHaveFocus()
    await waitFor(() =>
      expect(http.getActivity).toHaveBeenLastCalledWith(
        expect.objectContaining({ q: 'desktop' }),
        expect.anything(),
      ),
    )
  })

  it('uses memory-history traversal, rather than synthetic popstate, as Activity query and page authority', async () => {
    const http: ReviewHttp = {
      ...createHttp(),
      getActivity: vi.fn(async (search: ActivitySearch) =>
        activity({
          ledger: {
            event_type: search.eventType,
            page: search.page,
            page_count: 3,
            page_size: search.pageSize,
            query: search.q ?? null,
            range_days: search.rangeDays,
            total: 75,
          },
        }),
      ),
    }
    const user = userEvent.setup()
    const { router } = renderRoute('/activity?q=first&page=1', http)

    await screen.findByRole('heading', { name: 'Activity and recovery' })
    await router.navigate({
      to: '/activity',
      search: { eventType: 'all', q: 'second', rangeDays: 30, page: 2, pageSize: 25 },
    })
    await waitFor(() =>
      expect(screen.getByRole('searchbox', { name: 'Search activity' })).toHaveValue('second'),
    )
    await waitFor(() =>
      expect(screen.getByRole('spinbutton', { name: 'Activity page number' })).toHaveValue(2),
    )

    const editingSearch = screen.getByRole('searchbox', { name: 'Search activity' })
    await user.clear(editingSearch)
    await user.type(editingSearch, 'editing')
    act(() => router.history.back())
    await waitFor(() =>
      expect(screen.getByRole('searchbox', { name: 'Search activity' })).toHaveValue('first'),
    )
    await waitFor(() =>
      expect(screen.getByRole('spinbutton', { name: 'Activity page number' })).toHaveValue(1),
    )

    act(() => router.history.forward())
    await waitFor(() =>
      expect(screen.getByRole('searchbox', { name: 'Search activity' })).toHaveValue('second'),
    )
    await waitFor(() =>
      expect(screen.getByRole('spinbutton', { name: 'Activity page number' })).toHaveValue(2),
    )

    await router.navigate({
      to: '/activity',
      search: { eventType: 'all', q: 'second', rangeDays: 30, page: 3, pageSize: 25 },
    })
    await waitFor(() =>
      expect(screen.getByRole('spinbutton', { name: 'Activity page number' })).toHaveValue(3),
    )
    act(() => router.history.back())
    await waitFor(() =>
      expect(screen.getByRole('searchbox', { name: 'Search activity' })).toHaveValue('second'),
    )
    await waitFor(() =>
      expect(screen.getByRole('spinbutton', { name: 'Activity page number' })).toHaveValue(2),
    )

    fireEvent.popState(window)
    expect(screen.getByRole('searchbox', { name: 'Search activity' })).toHaveValue('second')
    expect(screen.getByRole('spinbutton', { name: 'Activity page number' })).toHaveValue(2)
  })

  it('gives a traversal target authority over an abandoned local page identity', async () => {
    const deferred = new Map<number, (value: ActivityView) => void>()
    const getActivity = vi.fn((search: ActivitySearch) => {
      if (search.page === 1)
        return Promise.resolve(
          activity({
            ledger: {
              event_type: search.eventType,
              page: 1,
              page_count: 3,
              page_size: search.pageSize,
              query: search.q ?? null,
              range_days: search.rangeDays,
              total: 75,
            },
          }),
        )
      return new Promise<ActivityView>((resolve) => {
        deferred.set(search.page, resolve)
      })
    })
    const http: ReviewHttp = {
      ...createHttp(),
      getActivity,
    }
    const user = userEvent.setup()
    const { router } = renderRoute('/activity?q=canonical&page=1', http)

    await screen.findByRole('heading', { name: 'Activity and recovery' })
    await user.click(screen.getByRole('button', { name: 'Next' }))
    await waitFor(() => expect(deferred.has(2)).toBe(true))
    await user.click(screen.getByRole('button', { name: 'Next' }))
    await waitFor(() => expect(deferred.has(3)).toBe(true))
    act(() => {
      deferred.get(3)?.(
        activity({
          ledger: {
            event_type: 'all',
            page: 3,
            page_count: 3,
            page_size: 25,
            query: 'canonical',
            range_days: 30,
            total: 75,
          },
        }),
      )
    })
    await waitFor(() =>
      expect(screen.getByRole('spinbutton', { name: 'Activity page number' })).toHaveValue(3),
    )

    vi.useFakeTimers()
    try {
      fireEvent.change(screen.getByRole('searchbox', { name: 'Search activity' }), {
        target: { value: 'draft' },
      })
      act(() => router.history.back())
      await act(async () => {
        await vi.advanceTimersByTimeAsync(151)
      })
      expect(getActivity.mock.calls.some(([search]) => search.q === 'draft')).toBe(false)
      expect(router.state.location.search).toMatchObject({ q: 'canonical', page: 2 })
      act(() => {
        deferred.get(2)?.(
          activity({
            ledger: {
              event_type: 'all',
              page: 2,
              page_count: 3,
              page_size: 25,
              query: 'canonical',
              range_days: 30,
              total: 75,
            },
          }),
        )
      })
      await act(async () => {
        await Promise.resolve()
      })
      expect(screen.getByRole('searchbox', { name: 'Search activity' })).toHaveValue('canonical')
      expect(screen.getByRole('spinbutton', { name: 'Activity page number' })).toHaveValue(2)
      expect(router.state.location.search).toMatchObject({ q: 'canonical', page: 2 })
    } finally {
      vi.useRealTimers()
    }
  })
})
