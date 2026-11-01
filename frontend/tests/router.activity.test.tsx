import { act, fireEvent, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { focusManager } from '@tanstack/react-query'
import { describe, expect, it, vi } from 'vitest'

import {
  AmbiguousMutationError,
  ApiError,
  BuildMismatchError,
  CsrfRenewedError,
  mapActivity,
} from '../src/api/http'
import type {
  ActivitySearch,
  ActivityView,
  PreparationView,
  ProviderThrottleEntry,
  ReviewHttp,
} from '../src/api/http'
import descriptionAdviceActivityFixture from './descriptionAdviceActivityFixture.json'
import { activityRoute, normalizeActivitySearch } from '../src/router'
import { formatDay, formatWhen } from '../src/dates'
import { languageName } from '../src/labels'
import { document as settingsDocument, view as settingsView } from './settingsFixtures'
import {
  activity,
  consentOf,
  createHttp,
  createLiveStream,
  openActivityAuthority,
  permissionReadiness,
  renderRoute,
} from './fixtures'

// A row's disclosure ends with the row's own time, as the row shows it.
const rowAt = (seconds: number) => formatWhen(seconds, new Date(), { dayShown: true })

it('keeps navigation available during initial Activity failure and retries the real read', async () => {
  const user = userEvent.setup()
  const http = createHttp()
  let rejectRead!: (error: Error) => void
  http.getActivity = vi
    .fn<ReviewHttp['getActivity']>()
    .mockImplementationOnce(
      () =>
        new Promise((_resolve, reject) => {
          rejectRead = reject
        }),
    )
    .mockRejectedValueOnce(new ApiError(503, 'unavailable'))
    .mockResolvedValue(activity())
  renderRoute('/activity', http)
  expect(await screen.findByRole('heading', { name: 'Loading activity…' })).toBeVisible()
  expect(screen.getByRole('navigation', { name: 'Primary workspace' })).toBeVisible()
  await act(async () => rejectRead(new ApiError(503, 'unavailable')))
  expect(
    await screen.findByRole('heading', { name: 'Activity could not be loaded.' }),
  ).toBeVisible()
  expect(screen.getByRole('link', { name: 'Library' })).toBeVisible()
  await user.click(screen.getByRole('button', { name: 'Try again' }))
  await waitFor(() => {
    expect(http.getActivity).toHaveBeenCalledTimes(2)
    expect(screen.getByRole('heading', { name: 'Activity could not be loaded.' })).toBeVisible()
  })
  await user.click(screen.getByRole('button', { name: 'Try again' }))
  expect(await screen.findByRole('heading', { name: 'Activity and recovery' })).toBeVisible()
  expect(http.getActivity).toHaveBeenCalledTimes(3)
})

describe('Activity route', () => {
  it('labels real description preparation results without relabeling policy records', async () => {
    const http = createHttp()
    vi.mocked(http.getActivity).mockResolvedValue(mapActivity(descriptionAdviceActivityFixture))
    renderRoute('/activity', http)
    const failed = await screen.findByText('Description advice could not be prepared')
    const failedRow = failed.closest('article')
    const completedRow = screen
      .getByText('Description advice prepared for review')
      .closest('article')
    if (!failedRow || !completedRow) throw new Error('Expected preparation rows')
    expect(within(failedRow).getByText('Description advice failed safely')).toBeVisible()
    expect(within(completedRow).getByText('Description advice completed')).toBeVisible()
    expect(screen.getAllByText('Completed')).toHaveLength(2)
    expect(screen.queryByText('Cover advice completed')).toBeNull()
    expect(screen.queryByText('Cover advice failed safely')).toBeNull()
  })

  it('retains authored preparation provenance without offering another choice interface', async () => {
    const user = userEvent.setup()
    const http = createHttp()
    vi.mocked(http.getActivity).mockResolvedValue(
      activity({
        events: [
          {
            event_id: 'description-attempt-synthetic',
            destination: 'description',
            field_label: 'Description',
            state: 'succeeded',
            origin: 'adviser',
            event_type: 'metadata',
            category: 'automatic',
            change: 'Description advice prepared for review',
            change_detail: 'Description advice prepared for review',
            evidence: [
              {
                label: 'Application',
                value:
                  'Preparation grants no write authority. Review current Description choices in Library.',
              },
              { label: 'Provenance', value: 'Advised · Authored content' },
              { label: 'Adviser', value: 'codex · synthetic-model' },
            ],
          },
        ],
      }),
    )
    renderRoute('/activity', http)
    await screen.findByText('Description advice prepared for review')
    await user.click(screen.getByRole('button', { name: /^Details for /u }))
    expect(screen.getByText('Advised · Authored content')).toBeVisible()
    expect(screen.getByText('codex · synthetic-model')).toBeVisible()
    expect(screen.getByText(/Preparation grants no write authority/)).toBeVisible()
    expect(screen.queryByRole('button', { name: /Choose advised|Retry description/ })).toBeNull()
    expect(screen.queryByRole('radio', { name: /Choose advised/ })).toBeNull()
  })

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

  it('offers incremental consent from the status section, outside the authority disclosure', async () => {
    const user = userEvent.setup()
    const initial = activity({
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
    })
    const http: ReviewHttp = {
      ...createHttp(),
      getActivity: vi.fn(async () => initial),
      getActivityPermissionReadiness: vi.fn(async () => permissionReadiness(initial)),
    }
    renderRoute('/activity', http)

    const consentTrigger = await screen.findByRole('button', { name: 'Review write permissions' })
    expect(consentTrigger.closest('details')).toBeNull()
    expect(consentTrigger.closest('.cm-activity-status')).not.toBeNull()
    expect(consentTrigger).toHaveAttribute('aria-expanded', 'false')
    expect(consentTrigger).toHaveAttribute('aria-controls', 'activity-consent')
    await user.click(consentTrigger)
    expect(screen.getByRole('heading', { name: 'Enable guarded writes' })).toBeVisible()
    // Confirm is the filled primary beside a plain Cancel (#1289).
    const confirm = screen.getByRole('button', { name: 'Confirm write permissions' })
    expect(confirm).toBeDisabled()
    expect(confirm).toHaveClass('button-primary')
    expect(screen.getByRole('button', { name: 'Cancel permission review' })).not.toHaveClass(
      'button-primary',
    )
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
      getActivityPermissionReadiness: vi.fn(async () => permissionReadiness(current)),
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

  it('asks for a second press after a refreshed session without resending the pause', async () => {
    const user = userEvent.setup()
    const http: ReviewHttp = {
      ...createHttp(),
      getActivity: vi.fn(async () =>
        activity({ enabled_libraries: [{ connection: 'primary', library: 'fiction' }] }),
      ),
      activityCommand: vi.fn(async () => {
        throw new CsrfRenewedError()
      }),
    }
    renderRoute('/activity', http)
    await openActivityAuthority(user)
    await user.click(screen.getByRole('button', { name: 'Pause new work' }))

    const authority = screen.getByLabelText('Activity authority and recovery')
    await waitFor(() =>
      expect(within(authority).getByRole('alert')).toHaveTextContent(
        /^Your session was refreshed\. Press again\.$/,
      ),
    )
    expect(within(authority).getByRole('alert')).toHaveFocus()
    expect(screen.getAllByRole('alert')).toHaveLength(1)
    expect(http.activityCommand).toHaveBeenCalledOnce()
  })

  it('announces a successful pause through a status line already present beside the controls', async () => {
    const user = userEvent.setup()
    let release: (() => void) | undefined
    const http: ReviewHttp = {
      ...createHttp(),
      getActivity: vi.fn(async () =>
        activity({ enabled_libraries: [{ connection: 'primary', library: 'fiction' }] }),
      ),
      activityCommand: vi.fn(
        () =>
          new Promise<undefined>((resolve) => {
            release = () => resolve(undefined)
          }),
      ),
    }
    renderRoute('/activity', http)
    await openActivityAuthority(user)
    // Mounted from the first render, not created by the press it reports.
    const authority = screen.getByLabelText('Activity authority and recovery')
    const line = within(authority).getByRole('status')
    expect(line).toHaveClass('activity-command-status')
    expect(line).toBeEmptyDOMElement()
    await user.click(screen.getByRole('button', { name: 'Pause new work' }))

    expect(within(authority).getByRole('status')).toBe(line)
    expect(line).toBeEmptyDOMElement()
    if (!release) throw new Error('pause must be pending')
    release()
    await waitFor(() =>
      expect(line).toHaveTextContent('Activity state refreshed from the authoritative server.'),
    )
    expect(within(authority).getByRole('status')).toBe(line)
  })

  it('keeps the reviewed consent selection after a refreshed session so one more press confirms', async () => {
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
    const http: ReviewHttp = {
      ...createHttp(),
      getActivity: vi.fn(async () => initial),
      getActivityPermissionReadiness: vi.fn(async () => permissionReadiness(initial)),
      // The verification read on Confirm states the basis that was checked.
      getActivityConsent: vi.fn(async () => consentOf(initial)),
      activityCommand: vi
        .fn<ReviewHttp['activityCommand']>()
        .mockRejectedValueOnce(new CsrfRenewedError())
        .mockResolvedValueOnce(undefined),
    }
    renderRoute('/activity#activity-consent', http)
    const library = await screen.findByRole('checkbox')
    await user.click(library)
    const confirm = screen.getByRole('button', { name: 'Confirm write permissions' })
    await user.click(confirm)

    const consent = document.getElementById('activity-consent')
    if (!consent) throw new Error('consent review must stay open')
    await waitFor(() =>
      expect(within(consent).getByRole('alert')).toHaveTextContent(
        /^Your session was refreshed\. Press again\.$/,
      ),
    )
    expect(within(consent).getByRole('checkbox')).toBeChecked()
    expect(confirm).toBeEnabled()
    expect(http.getActivityPermissionReadiness).toHaveBeenCalledOnce()
    await user.click(confirm)
    await waitFor(() => expect(http.activityCommand).toHaveBeenCalledTimes(2))
    expect(http.activityCommand).toHaveBeenLastCalledWith('activity/consent', {
      grants: [{ library: 'fiction', destinations: ['title'] }],
    })
    expect(
      await screen.findByText('Activity state refreshed from the authoritative server.'),
    ).toBeInTheDocument()
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
      getActivityPermissionReadiness: vi.fn(async () => permissionReadiness(current)),
      // The read on Confirm sees the reviewed basis, so the ambiguous POST is reached.
      getActivityConsent: vi.fn(async () => consentOf(initial)),
      activityCommand: vi.fn(async () => {
        throw new AmbiguousMutationError()
      }),
    }
    const { router } = renderRoute('/activity#activity-consent', http)
    await user.click(await screen.findByRole('checkbox'))
    current = { ...initial, capabilities: [] }
    await user.click(screen.getByRole('button', { name: 'Confirm write permissions' }))

    await waitFor(() =>
      expect(screen.getByRole('alert')).toHaveTextContent(
        'The activity outcome could not be confirmed. No command was resent.',
      ),
    )
    expect(http.activityCommand).toHaveBeenCalledOnce()
    expect(router.state.location.hash).toBe('activity-consent')
    expect(screen.getByRole('button', { name: 'Cancel permission review' })).toBeVisible()
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
    const http: ReviewHttp = {
      ...createHttp(),
      getActivity: vi.fn(async () => current),
      getActivityPermissionReadiness: vi.fn(async () => permissionReadiness(current)),
    }
    const { queryClient } = renderRoute('/activity', http)
    await user.click(await screen.findByRole('button', { name: 'Review write permissions' }))
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
    await waitFor(() => expect(screen.queryByRole('checkbox')).toBeNull())
    expect(screen.getByRole('button', { name: 'Confirm write permissions' })).toBeDisabled()
    // The discard says why, in the panel's own status line, and focus that
    // fell with the checkbox lands on the control that starts the next check.
    const review = screen.getByRole('region', { name: 'Enable guarded writes' })
    expect(within(review).getByRole('status')).toHaveTextContent(
      /^Your library settings changed\. Review the permissions again\.$/,
    )
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Check write permissions' })).toHaveFocus(),
    )

    current = partial
    await queryClient.invalidateQueries({ queryKey: ['activity'] })
    await user.click(screen.getByRole('button', { name: 'Check write permissions' }))
    const restored = await screen.findByRole('checkbox')
    expect(restored).toBeEnabled()
    expect(restored).not.toBeChecked()
    expect(screen.getByRole('button', { name: 'Confirm write permissions' })).toBeDisabled()
  })

  it('keeps unavailable consent disabled in the protected authority view', async () => {
    const initial = activity({
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
    })
    const http: ReviewHttp = {
      ...createHttp(),
      getActivity: vi.fn(async () => initial),
      getActivityPermissionReadiness: vi.fn(async () => permissionReadiness(initial)),
    }
    renderRoute('/activity', http)

    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: 'Review write permissions' }))
    expect(await screen.findByRole('checkbox')).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Confirm write permissions' })).toBeDisabled()
  })

  it('bounds unknown ledger types neutrally', async () => {
    const unknown = 'unexpected_origin_' + 'x'.repeat(100)
    const http: ReviewHttp = {
      ...createHttp(),
      getActivity: vi.fn(async () =>
        activity({
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
      'Closed safely',
    ],
    [
      'item changed',
      {
        enabled_libraries: [{ connection: 'primary', library: 'fiction' }],
        events: [{ work_label: 'Changed work', state: 'stale', failure: 'target_changed' }],
      },
      'Item changed; closed safely',
    ],
    [
      'connection-waiting',
      {
        enabled_libraries: [{ connection: 'primary', library: 'fiction' }],
        events: [{ work_label: 'Unreached work', state: 'stale', failure: 'connection' }],
      },
      'Waiting safely',
    ],
    [
      'connection-exhausted',
      {
        enabled_libraries: [{ connection: 'primary', library: 'fiction' }],
        events: [{ work_label: 'Exhausted work', state: 'stale', failure: 'connection_exhausted' }],
      },
      'Closed safely',
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
      'owner-entered',
      { events: [{ work_label: 'Entered work', state: 'owner_entered', origin: 'human' }] },
      'Owner-entered',
    ],
    [
      'withdrawn',
      {
        events: [
          {
            work_label: 'Withdrawn work',
            state: 'withdraw',
            origin: 'human',
            change: 'Withdrawn',
          },
        ],
      },
      // The server's change already says Withdrawn; the result does not
      // repeat it (#1286).
      'Recorded',
    ],
    [
      'completed automatic cover advice',
      {
        events: [
          {
            work_label: 'Cover work',
            destination: 'cover',
            state: 'completed',
            origin: 'automatic_pipeline',
          },
        ],
      },
      'Cover advice completed',
    ],
    [
      'unavailable owner cover retry',
      {
        events: [
          {
            work_label: 'Cover work',
            destination: 'cover',
            state: 'unavailable',
            origin: 'owner_retry',
          },
        ],
      },
      'Cover advice unavailable',
    ],
    [
      'failed owner cover retry',
      {
        events: [
          {
            work_label: 'Cover work',
            destination: 'cover',
            state: 'failed',
            origin: 'owner_retry',
          },
        ],
      },
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

  it.each([
    [
      1,
      'A stopped change needs you',
      'Its “Prepared change closed” entry in the ledger says why it stopped. Nothing retries it on its own: follow that advice or choose the field again. If it isn’t listed, widen the range.',
    ],
    [
      2,
      '2 stopped changes need you',
      'Each “Prepared change closed” entry in the ledger says why it stopped. Nothing retries them on their own: follow that advice or choose the field again. If one isn’t listed, widen the range.',
    ],
  ])(
    'never reads calm while %i changes wait on the owner',
    async (owner_action_required, heading, detail) => {
      const http: ReviewHttp = {
        ...createHttp(),
        getActivity: vi.fn(async () => activity({ owner_action_required })),
      }
      renderRoute('/activity', http)

      expect(await screen.findByRole('heading', { name: heading })).toBeVisible()
      expect(screen.getByText(detail)).toBeVisible()
      expect(screen.getByText('Action needed')).toBeVisible()
      expect(screen.queryByText('No action required')).toBeNull()
      expect(screen.queryByRole('heading', { name: 'No intervention needed' })).toBeNull()
    },
  )

  it.each([
    ['no filter', '/activity', 'widen the range.'],
    ['a search', '/activity?q=title', 'widen the range or clear filters.'],
    ['an event type', '/activity?eventType=safety', 'widen the range or clear filters.'],
  ])('says how to find a stopped change hidden by %s (#1542)', async (_name, path, ending) => {
    const http: ReviewHttp = {
      ...createHttp(),
      getActivity: vi.fn(async () => activity({ owner_action_required: 1 })),
    }
    renderRoute(path, http)

    const detail = await screen.findByText(/^Its “Prepared change closed” entry/u)
    expect(detail.textContent.endsWith(`If it isn’t listed, ${ending}`)).toBe(true)
  })

  it('stays calm when nothing waits on the owner', async () => {
    const http: ReviewHttp = {
      ...createHttp(),
      getActivity: vi.fn(async () => activity({ owner_action_required: 0 })),
    }
    renderRoute('/activity', http)

    expect(await screen.findByRole('heading', { name: 'No intervention needed' })).toBeVisible()
    expect(screen.getByText('No action required')).toBeVisible()
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
    expect(
      screen.getAllByText(/Writes are held because an earlier write behaved unexpectedly\./u),
    ).toHaveLength(2)
    expect(screen.queryByRole('button', { name: /retry/i })).toBeNull()
    expect(screen.getByRole('button', { name: 'Pause new work' })).toBeVisible()
  })

  it('projects all durable provider-recovery phases with safe scope and work units', async () => {
    const http: ReviewHttp = {
      ...createHttp(),
      getActivity: vi.fn(async () =>
        activity({
          provider_recovery: [
            {
              state: 'waiting',
              failure_reasons: ['burst_throttled', 'quota_exhausted', 'transient'],
              affected_count: 2,
              next_eligible_at: 1,
              dispatch_reasons: ['missing_corroboration', 'blanket_scan'],
            },
            { state: 'checking', affected_count: 1, next_eligible_at: null },
            { state: 'fanout', affected_count: 3, next_eligible_at: null },
            {
              state: 'recovered',
              affected_count: 1,
              next_eligible_at: null,
              dispatch_reasons: [],
            },
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
    expect(screen.getByText('Waiting for a safe retry').parentElement).toHaveTextContent(
      'Provider burst limit; Provider quota exhausted; Provider temporarily unavailable',
    )
    expect(screen.getByText('Waiting for a safe retry').parentElement).toHaveTextContent(
      'next eligible',
    )
    expect(screen.getByText('Waiting for a safe retry').parentElement).toHaveTextContent(
      'Priority: needs independent evidence, general recovery scan',
    )
    expect(screen.getByText('Checking provider health').parentElement).toHaveTextContent(
      '1 affected scope',
    )
    expect(screen.getByText('Retrying affected work').parentElement).toHaveTextContent(
      '3 affected works',
    )
    expect(screen.getByText('Recovered').parentElement).toHaveTextContent('1 affected scope')
    expect(screen.getByText('Recovered').parentElement).not.toHaveTextContent('Priority:')
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
    const initial = activity({
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
          destinations: [{ destination: 'title', state: 'allowed', consented: false, prepared: 0 }],
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
    })
    const http: ReviewHttp = {
      ...createHttp(),
      getActivity: vi.fn(async () => initial),
      getActivityPermissionReadiness: vi.fn(async () => permissionReadiness(initial, false)),
    }
    renderRoute('/activity', http)

    expect(await screen.findByText('Recorded work')).toBeVisible()
    await user.click(await screen.findByRole('button', { name: 'Review write permissions' }))
    expect(await screen.findByRole('checkbox')).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Confirm write permissions' })).toBeDisabled()
  })

  it('does not invent a pause control after revocation', async () => {
    const user = userEvent.setup()
    const initial = activity({
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
          destinations: [{ destination: 'title', state: 'allowed', consented: false, prepared: 0 }],
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
    })
    const http: ReviewHttp = {
      ...createHttp(),
      getActivity: vi.fn(async () => initial),
      getActivityPermissionReadiness: vi.fn(async () => permissionReadiness(initial)),
    }
    renderRoute('/activity', http)

    expect(await screen.findByText('Recorded work')).toBeVisible()
    await user.click(await screen.findByRole('button', { name: 'Review write permissions' }))
    expect(await screen.findByRole('heading', { name: 'Enable guarded writes' })).toBeVisible()
    expect(await screen.findByRole('checkbox')).toBeDisabled()
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
    // The sentence is text in the cell, not a label on it (#1288).
    const announced = screen.getByText(
      'Set Title from Paper Moons: Larkspur to Paper Moons over Larkspur Station',
    ).parentElement
    expect(announced).toHaveClass('cm-activity-change')
    expect(announced).not.toHaveAttribute('aria-label')
    expect(announced?.querySelector('[aria-hidden="true"]')).toHaveTextContent(
      'Paper Moons: Larkspur → Paper Moons over Larkspur Station',
    )
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
      ).getByRole('button', { name: /^Details for /u })
    await user.click(disclosureFor('Paper Moons over Larkspur Station'))
    expect(screen.getByText('Confirmed in Audiobookshelf')).toBeVisible()
    expect(screen.queryByRole('region', { name: /exact policy changes/ })).toBeNull()
    await user.click(screen.getByRole('button', { name: /^Hide details for /u }))
    await user.click(disclosureFor('Library policy'))
    const table = screen.getByLabelText('2 exact policy changes')
    expect(table).toHaveTextContent('Title layout')
    expect(table).toHaveTextContent('Review mode')
    expect(table).toHaveTextContent('Ask me')
    expect(table).toHaveTextContent('Automatic')
    expect(table).toHaveTextContent('Visible Google text')
  })

  it('names a language row’s languages in its change and Details (#1490)', async () => {
    const user = userEvent.setup()
    const http: ReviewHttp = {
      ...createHttp(),
      getActivity: vi.fn(async () =>
        activity({
          events: [
            {
              event_id: 'owner-language',
              destination: 'language',
              field_label: 'Language',
              change: 'en → en-GB',
              change_detail: 'Set Language from en to en-GB',
              evidence: [
                { label: 'Previous value', value: 'en' },
                { label: 'New value', value: 'en-GB' },
                { label: 'Chosen by', value: 'Library owner' },
              ],
            },
          ],
        }),
      ),
    }
    renderRoute('/activity', http)

    await screen.findByRole('heading', { name: 'Activity and recovery' })
    const [english, british] = [languageName('en'), languageName('en-GB')]
    expect(english).toBe('English (en)')
    expect(screen.getByText(`${english} → ${british}`)).toBeInTheDocument()
    expect(screen.getByText(`Set Language from ${english} to ${british}`)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /^Details for /u }))
    expect(screen.getByText(english)).toBeVisible()
    expect(screen.getByText(british)).toBeVisible()
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
    const disclosure = screen.getByRole('button', { name: /^Details for /u })
    disclosure.focus()
    await user.keyboard('{Enter}')
    expect(disclosure).toHaveAttribute('aria-expanded', 'true')
    expect(disclosure).toHaveAccessibleName(
      `Hide details for ISBN, Paper Moons: Larkspur, ${rowAt(1785742465)}`,
    )
    expect(screen.getByText('Previous value')).toBeVisible()
    disclosure.focus()
    await user.keyboard('{Enter}')
    expect(disclosure).toHaveAttribute('aria-expanded', 'false')
    expect(disclosure).toHaveAccessibleName(
      `Details for ISBN, Paper Moons: Larkspur, ${rowAt(1785742465)}`,
    )
    expect(screen.queryByText('Previous value')).toBeNull()
  })

  it('offers a reload, not another try, when recorded transitions meet a newer build', async () => {
    const user = userEvent.setup()
    const http: ReviewHttp = {
      ...createHttp(),
      getActivity: vi.fn(async () =>
        activity({
          events: [{ event_id: 'lifecycle', transition_id: 'effect_plan_' + 'a'.repeat(64) }],
        }),
      ),
      getActivityTransitions: vi.fn(async () => {
        throw new BuildMismatchError()
      }),
    }
    renderRoute('/activity', http)

    await user.click(await screen.findByRole('button', { name: /^Details for /u }))
    expect(
      await screen.findByText(
        'Recorded transitions were not loaded. This tab is running an earlier build.',
        { exact: false },
      ),
    ).toBeVisible()
    expect(screen.getByRole('button', { name: 'Reload the current application' })).toBeVisible()
    expect(
      screen.queryByText('Recorded transitions could not be loaded.', { exact: false }),
    ).toBeNull()
    expect(screen.queryByRole('button', { name: 'Try again' })).toBeNull()
  })

  it('reaches every bounded lifecycle transition and recovers a disclosure read', async () => {
    const user = userEvent.setup()
    const transitions = Array.from({ length: 25 }, (_, index) => ({
      event_id: `transition-${String(index + 1)}`,
      occurred_at: index + 1,
      sequence: index + 1,
      state: index === 24 ? 'verified' : 'planned',
      failure: null,
    }))
    let failFirst = true
    const http: ReviewHttp = {
      ...createHttp(),
      getActivity: vi.fn(async () =>
        activity({
          events: [{ event_id: 'lifecycle', transition_id: 'effect_plan_' + 'a'.repeat(64) }],
        }),
      ),
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

    await user.click(await screen.findByRole('button', { name: /^Details for /u }))
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Recorded transitions could not be loaded.',
    )
    await user.click(screen.getByRole('button', { name: 'Try again' }))
    const lifecycle = await screen.findByRole('list', { name: 'Recorded lifecycle transitions' })
    expect(lifecycle.querySelectorAll('li')).toHaveLength(21)
    expect(lifecycle.querySelector('li')?.textContent).toMatch(/\u00a0· Planned$/u)
    await user.click(screen.getByRole('button', { name: 'Load more transitions' }))
    await waitFor(() => expect(lifecycle.querySelectorAll('li')).toHaveLength(25))
    expect(http.getActivityTransitions).toHaveBeenLastCalledWith(
      'effect_plan_' + 'a'.repeat(64),
      20,
    )
  })

  it('states each lifecycle transition in one flow with its advice kind', async () => {
    const user = userEvent.setup()
    const http: ReviewHttp = {
      ...createHttp(),
      getActivity: vi.fn(async () =>
        activity({
          events: [
            {
              event_id: 'cover-advice',
              destination: 'cover',
              origin: 'adviser',
              state: 'completed',
              transition_id: 'effect_plan_' + 'c'.repeat(64),
            },
          ],
        }),
      ),
      getActivityTransitions: vi.fn(async () => ({
        events: ['failed', 'completed'].map((state, index) => ({
          event_id: `transition-${state}`,
          occurred_at: 1786000000 + index,
          sequence: index + 1,
          state,
          failure: null,
        })),
        has_more: false,
      })),
    }
    renderRoute('/activity', http)

    await user.click(await screen.findByRole('button', { name: /^Details for /u }))
    const lifecycle = await screen.findByRole('list', { name: 'Recorded lifecycle transitions' })
    const lines = [...lifecycle.querySelectorAll('li')].map((item) => item.textContent)
    expect(lines).toHaveLength(2)
    // One recorded time, in the one format (#1286), then the state.
    expect(lines[0]).toMatch(/^[^·\s][^·]*\u00a0· Cover advice failed safely$/u)
    expect(lines[1]).toMatch(/^[^·\s][^·]*\u00a0· Cover advice completed$/u)
    for (const line of lines) expect(line).not.toMatch(/(?:^|[ \n])·/u)
  })

  it.each([
    ['connection', 'Waiting safely'],
    ['connection_exhausted', 'Closed safely'],
    ['target_changed', 'Item changed; closed safely'],
  ])('labels each lifecycle stale from its own %s failure', async (failure, latest) => {
    const user = userEvent.setup()
    const http: ReviewHttp = {
      ...createHttp(),
      getActivity: vi.fn(async () =>
        activity({
          events: [
            {
              event_id: 'connection-wait',
              state: 'stale',
              failure,
              transition_id: 'effect_plan_' + 'd'.repeat(64),
            },
          ],
        }),
      ),
      getActivityTransitions: vi.fn(async () => ({
        events: [
          ['planned', null],
          ['stale', 'connection'],
          ['planned', null],
          ['stale', failure],
        ].map(([state, transitionFailure], index) => ({
          event_id: `transition-${String(index)}`,
          occurred_at: 1786000000 + index,
          sequence: index + 1,
          state: state ?? '',
          failure: transitionFailure ?? null,
        })),
        has_more: false,
      })),
    }
    renderRoute('/activity', http)

    await user.click(await screen.findByRole('button', { name: /^Details for /u }))
    const lifecycle = await screen.findByRole('list', { name: 'Recorded lifecycle transitions' })
    const lines = [...lifecycle.querySelectorAll('li')].map((item) =>
      item.textContent.split('\u00a0· ').at(-1),
    )
    // Each recorded stale reads from the failure it was recorded with (#1402).
    expect(lines).toEqual(['Planned', 'Waiting safely', 'Planned', latest])
  })

  it('keeps an open lifecycle disclosure through a refreshed latest event', async () => {
    const user = userEvent.setup()
    const transitionId = 'effect_plan_' + 'b'.repeat(64)
    const transitions = (start: number, count: number) =>
      Array.from({ length: count }, (_, index) => ({
        event_id: `transition-${String(start + index)}`,
        occurred_at: start + index,
        sequence: start + index,
        state: 'planned',
        failure: null,
      }))
    let current = activity({
      events: [
        { change_id: 'stable-change', event_id: 'latest-event', transition_id: transitionId },
      ],
    })
    let continuationFailures = 0
    const http: ReviewHttp = {
      ...createHttp(),
      getActivity: vi.fn(async () => current),
      getActivityTransitions: vi.fn(async (_eventId: string, afterSequence?: number) => {
        if (afterSequence === undefined) return { events: transitions(1, 20), has_more: true }
        if (afterSequence === 20) return { events: transitions(21, 2), has_more: true }
        ++continuationFailures
        throw new ApiError(503, 'unavailable')
      }),
    }
    const { queryClient } = renderRoute('/activity', http)

    await user.click(await screen.findByRole('button', { name: /^Details for /u }))
    const lifecycle = await screen.findByRole('list', { name: 'Recorded lifecycle transitions' })
    await user.click(screen.getByRole('button', { name: 'Load more transitions' }))
    await waitFor(() => expect(lifecycle.querySelectorAll('li')).toHaveLength(23))
    await user.click(screen.getByRole('button', { name: 'Load more transitions' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'More recorded transitions could not be loaded.',
    )
    const disclosure = screen.getByRole('button', { name: /^Hide details for /u })
    disclosure.focus()

    current = activity({
      events: [
        { change_id: 'stable-change', event_id: 'new-latest-event', transition_id: transitionId },
      ],
    })
    await queryClient.invalidateQueries({ queryKey: ['activity'] })

    await waitFor(() =>
      expect(screen.getByRole('button', { name: /^Hide details for /u })).toHaveFocus(),
    )
    expect(lifecycle.querySelectorAll('li')).toHaveLength(23)
    expect(screen.getByRole('alert')).toHaveTextContent(
      'More recorded transitions could not be loaded.',
    )
    expect(screen.getByRole('button', { name: 'Try again' })).toBeVisible()
    expect(continuationFailures).toBe(1)
  })

  it('does not request lifecycle transitions for human, policy, or no-change rows', async () => {
    const user = userEvent.setup()
    const http: ReviewHttp = {
      ...createHttp(),
      getActivityTransitions: vi.fn(async () => ({ events: [], has_more: false })),
      getActivity: vi.fn(async () =>
        activity({
          events: [
            { event_id: 'human-row', origin: 'human' },
            { event_id: 'policy-row', category: 'policy', origin: 'policy' },
            { event_id: 'no-change-row', state: 'no_change_required' },
          ],
        }),
      ),
    }
    renderRoute('/activity', http)

    for (const button of await screen.findAllByRole('button', { name: /^Details for /u }))
      await user.click(button)

    expect(http.getActivityTransitions).not.toHaveBeenCalled()
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('never says no action is required while its own prepared work awaits consent', async () => {
    const user = userEvent.setup()
    const initial = activity({
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
          destinations: [{ destination: 'title', state: 'allowed', consented: false, prepared: 3 }],
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
    })
    const http: ReviewHttp = {
      ...createHttp(),
      getActivity: vi.fn(async () => initial),
      getActivityPermissionReadiness: vi.fn(async () => permissionReadiness(initial)),
      getActivityConsent: vi.fn(async () => consentOf(initial)),
    }
    renderRoute('/activity', http)

    // The offer and the status panel read the same consent state, so the page
    // cannot claim calm directly above the count that refutes it (#639).
    expect(
      await screen.findByText('Writes are off. Review permissions when you are ready.'),
    ).toBeVisible()
    expect(
      screen.queryByRole('heading', { name: 'Prepared work needs your permission' }),
    ).toBeNull()
    expect(screen.getAllByText('Awaiting permission')).toHaveLength(1)
    for (const calm of [
      'No intervention needed',
      'No action required',
      'No action is required.',
      'Guarded work continues automatically. Recovery controls appear only when your decision is required.',
    ])
      expect(screen.queryByText(calm)).toBeNull()
    expect(screen.getByText('Action needed')).toBeVisible()
    await user.click(
      within(
        screen.getByRole('region', {
          name: 'Writes are off. Review permissions when you are ready.',
        }),
      ).getByRole('button', { name: 'Review write permissions' }),
    )
    expect(screen.getByRole('heading', { name: 'Enable guarded writes' })).toBeVisible()
    expect(http.activityCommand).not.toHaveBeenCalled()
    await user.click(screen.getByRole('checkbox', { name: /fiction/ }))
    await user.click(screen.getByRole('button', { name: 'Cancel permission review' }))
    expect(screen.queryByRole('heading', { name: 'Enable guarded writes' })).toBeNull()
    expect(screen.getAllByText('Awaiting permission')).toHaveLength(1)
    expect(screen.queryByText('Prepared work needs your permission')).toBeNull()
    expect(http.activityCommand).not.toHaveBeenCalled()
    await user.click(
      within(
        screen.getByRole('region', {
          name: 'Writes are off. Review permissions when you are ready.',
        }),
      ).getByRole('button', { name: 'Review write permissions' }),
    )
    await user.click(await screen.findByRole('checkbox', { name: /fiction/ }))
    vi.mocked(http.activityCommand).mockRejectedValueOnce(new ApiError(409, 'capability'))
    await user.click(screen.getByRole('button', { name: 'Confirm write permissions' }))
    expect(
      await screen.findByText(
        'The destinations changed since you reviewed them. No permission was granted. Check write permissions again, then confirm.',
      ),
    ).toBeVisible()
    expect(http.activityCommand).toHaveBeenCalledTimes(1)
    const failure = screen.getByRole('alert')
    expect(failure).toHaveFocus()
    expect(failure.closest('#activity-consent')).not.toBeNull()
    expect(screen.getAllByRole('alert')).toHaveLength(1)
    await user.click(screen.getByRole('button', { name: 'Check write permissions' }))
    await user.click(await screen.findByRole('checkbox', { name: /fiction/ }))
    await user.click(screen.getByRole('button', { name: 'Confirm write permissions' }))
    await waitFor(() => expect(http.activityCommand).toHaveBeenCalledTimes(2))
    expect(http.activityCommand).toHaveBeenLastCalledWith('activity/consent', {
      grants: [{ library: 'fiction', destinations: ['title'] }],
    })
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

  it.each([
    [
      'a lapse',
      [{ connection: 'primary', library: 'fiction', reason: 'destinations_changed' as const }],
      'Nothing is written to your libraries until you give permission again.',
    ],
    [
      'no permission at all',
      [],
      'No library has write permission, so nothing is written to your libraries until you give it.',
    ],
  ])('never reports calm while no library holds permission: %s (#1370)', async (_, lapsed, why) => {
    const view = activity({
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
          destinations: [{ destination: 'title', state: 'allowed', consented: false, prepared: 0 }],
        },
      ],
      lapsed_consents: lapsed,
    })
    const http: ReviewHttp = {
      ...createHttp(),
      getActivity: vi.fn(async () => view),
      getActivityConsent: vi.fn(async () => consentOf(view)),
    }
    renderRoute('/activity', http)

    expect(await screen.findByRole('heading', { name: 'Writes are off' })).toBeVisible()
    expect(await screen.findByText(why)).toBeVisible()
    for (const calm of [
      'No intervention needed',
      'No action required',
      'Guarded work continues automatically. Recovery controls appear only when your decision is required.',
    ])
      expect(screen.queryByText(calm)).toBeNull()
    // A lapse opens the consent card, in its existing slot, with the reason;
    // the status below says something else rather than repeating it (#1370).
    const reason =
      'Write permission for fiction lapsed because this version of Mechanical Librarian can write a different set of fields.'
    if (lapsed.length) {
      expect(
        await screen.findByRole('region', {
          name: 'Writes are off. Review permissions when you are ready.',
        }),
      ).toHaveTextContent(reason)
      expect(screen.getAllByText(reason, { exact: false })).toHaveLength(1)
    }
  })

  it('states a lapse beside a library that kept its permission (#1370)', async () => {
    const view = activity({
      configured_libraries: ['fiction', 'history'].map((library) => ({
        connection: 'primary',
        library,
        prepared: { automatic: 0, human: 0, total: 0 },
      })),
      capabilities: ['fiction', 'history'].map((library) => ({
        connection: 'primary',
        library,
        state: 'allowed',
        destinations: [
          { destination: 'title', state: 'allowed', consented: library === 'fiction', prepared: 0 },
        ],
      })),
      consented_libraries: [{ connection: 'primary', library: 'fiction' }],
      enabled_libraries: [{ connection: 'primary', library: 'fiction' }],
      lapsed_consents: [{ connection: 'primary', library: 'history', reason: 'policy_changed' }],
    })
    const http: ReviewHttp = {
      ...createHttp(),
      getActivity: vi.fn(async () => view),
      getActivityConsent: vi.fn(async () => consentOf(view)),
    }
    renderRoute('/activity', http)

    expect(await screen.findByRole('heading', { name: 'Some writes are off' })).toBeVisible()
    expect(
      screen.getByText(
        'Write permission for history lapsed because it was given under a policy that is no longer active.',
      ),
    ).toBeVisible()
    expect(screen.getByText('Action needed')).toBeVisible()
    for (const calm of [
      'No intervention needed',
      'No action required',
      'Guarded work continues automatically. Recovery controls appear only when your decision is required.',
    ])
      expect(screen.queryByText(calm)).toBeNull()
  })

  it('never reads calm beside a lapse while Settings is still loading (#1370)', async () => {
    const view = activity({
      configured_libraries: ['fiction', 'history'].map((library) => ({
        connection: 'primary',
        library,
        prepared: { automatic: 0, human: 0, total: 0 },
      })),
      capabilities: ['fiction', 'history'].map((library) => ({
        connection: 'primary',
        library,
        state: 'allowed',
        destinations: [
          { destination: 'title', state: 'allowed', consented: library === 'fiction', prepared: 0 },
        ],
      })),
      consented_libraries: [{ connection: 'primary', library: 'fiction' }],
      enabled_libraries: [{ connection: 'primary', library: 'fiction' }],
      lapsed_consents: [{ connection: 'primary', library: 'history', reason: 'policy_changed' }],
    })
    const http: ReviewHttp = {
      ...createHttp(),
      getActivity: vi.fn(async () => view),
      getActivityConsent: vi.fn(async () => consentOf(view)),
      // The Settings read never settles, so the names are never ready.
      getSettings: vi.fn(() => new Promise<never>(() => undefined)),
    }
    renderRoute('/activity', http)

    expect(await screen.findByRole('heading', { name: 'Some writes are off' })).toBeVisible()
    await waitFor(() => expect(http.getSettings).toHaveBeenCalled())
    // The status is not a live region, so it states the lapse by its slug.
    expect(
      screen.getByText(
        'Write permission for history lapsed because it was given under a policy that is no longer active.',
      ),
    ).toBeVisible()
    for (const calm of [
      'No intervention needed',
      'Guarded work continues automatically. Recovery controls appear only when your decision is required.',
    ])
      expect(screen.queryByText(calm)).toBeNull()
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
    await user.click(screen.getByRole('button', { name: /^Details for /u }))
    expect(screen.getByText('Prepared value')).toBeVisible()
    expect(screen.getByText('The current value changed after this was prepared')).toBeVisible()
    expect(screen.queryByText('Durable evidence')).toBeNull()
    expect(screen.getByText('Current waiting work · Title update')).toBeVisible()
    await user.click(screen.getByRole('button', { name: /^Hide details for /u }))
    const pageInput = screen.getByRole('spinbutton', { name: 'Activity page number' })
    await user.clear(pageInput)
    await user.type(pageInput, '3')
    await user.click(screen.getByRole('button', { name: 'Policy changes' }))
    await waitFor(() =>
      expect(http.getActivity).toHaveBeenLastCalledWith(
        expect.objectContaining({ eventType: 'policy', page: 1 }),
        expect.anything(),
        'foreground',
      ),
    )
    expect(screen.getByRole('spinbutton', { name: 'Activity page number' })).toHaveValue(1)
    await user.click(screen.getByRole('button', { name: 'Next' }))
    await waitFor(() =>
      expect(http.getActivity).toHaveBeenLastCalledWith(
        expect.objectContaining({ eventType: 'policy', page: 2 }),
        expect.anything(),
        'foreground',
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
            'foreground',
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
        const inspect = screen.getByRole('button', { name: /^Details for /u })
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
        'foreground',
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
        'foreground',
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
        'foreground',
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
        'foreground',
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
      'foreground',
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

    const rows = screen.getByRole('combobox', { name: 'Events per activity page' })
    await user.click(rows)
    await user.click(screen.getByRole('option', { name: '50 per page' }))
    await waitFor(() =>
      expect(screen.getByRole('combobox', { name: 'Events per activity page' })).toHaveFocus(),
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
        'foreground',
      ),
    )
  })

  it('keeps Page jump focus when the current Activity date range is chosen', async () => {
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
    const { router } = renderRoute('/activity?rangeDays=90', http)

    await screen.findByRole('heading', { name: 'Activity and recovery' })
    const navigate = vi.spyOn(router, 'navigate')
    const requests = vi.mocked(http.getActivity).mock.calls.length
    const dateRange = screen.getByRole('combobox', { name: 'Activity date range' })
    await user.click(dateRange)
    await user.click(screen.getByRole('option', { name: 'Last 90 days' }))

    expect(dateRange).toHaveFocus()
    expect(navigate).not.toHaveBeenCalled()
    expect(http.getActivity).toHaveBeenCalledTimes(requests)

    const page = screen.getByRole('spinbutton', { name: 'Activity page number' })
    await user.click(page)
    await user.clear(page)
    await user.type(page, '2{Escape}')
    expect(page).toHaveValue(1)
    expect(page).toHaveFocus()
  })

  it('keeps Page jump focus when a search change lands after the owner moved into it', async () => {
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
    const { router } = renderRoute('/activity?page=2', http)

    await screen.findByRole('heading', { name: 'Activity and recovery' })
    const page = screen.getByRole('spinbutton', { name: 'Activity page number' })
    await waitFor(() => expect(page).toHaveValue(2))
    await waitFor(() =>
      expect(document.getElementById('main-content')).toHaveAttribute(
        'data-restored-path',
        '/activity',
      ),
    )
    await user.click(page)
    await user.clear(page)
    await user.type(page, '3')
    // A range chosen a moment earlier resolves only now (#1554).
    await act(() =>
      router.navigate({
        to: '/activity',
        search: { eventType: 'all', rangeDays: 90, page: 1, pageSize: 25 },
      }),
    )

    await waitFor(() =>
      expect(screen.getByRole('spinbutton', { name: 'Activity page number' })).toHaveValue(1),
    )
    expect(screen.getByRole('spinbutton', { name: 'Activity page number' })).toBe(page)
    expect(page).toHaveFocus()
  })

  it('lets no pending Activity search pull the owner back once another route is loading', async () => {
    const { router } = renderRoute('/activity', createHttp())
    const search = await screen.findByRole('searchbox', { name: 'Search activity' })
    vi.useFakeTimers()
    try {
      fireEvent.change(search, { target: { value: 'late' } })
      await act(async () => {
        const leaving = router.navigate({ to: '/settings', search: { group: 'connection' } })
        // Activity is still painted while the Settings route loads.
        vi.advanceTimersByTime(250)
        await leaving
      })
    } finally {
      vi.useRealTimers()
    }
    expect(router.state.location.pathname).toBe('/settings')
    expect(router.state.location.search).toEqual({ group: 'connection' })
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
        'foreground',
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

  it('preserves the next query debounce when the prior response is acknowledged', async () => {
    let resolveFirst!: (value: ActivityView) => void
    const view = (search: ActivitySearch) =>
      activity({
        ledger: {
          event_type: search.eventType,
          page: search.page,
          page_count: 1,
          page_size: search.pageSize,
          query: search.q ?? null,
          range_days: search.rangeDays,
          total: search.q === 'ab' ? 0 : 1,
        },
      })
    const getActivity = vi.fn((search: ActivitySearch) =>
      search.q === 'a'
        ? new Promise<ActivityView>((resolve) => {
            resolveFirst = resolve
          })
        : Promise.resolve(view(search)),
    )
    renderRoute('/activity', { ...createHttp(), getActivity })
    await screen.findByRole('heading', { name: 'Activity and recovery' })
    const query = screen.getByRole('searchbox', { name: 'Search activity' })
    query.focus()
    vi.useFakeTimers()
    try {
      fireEvent.change(query, { target: { value: 'a' } })
      await act(async () => {
        await vi.advanceTimersByTimeAsync(150)
      })
      expect(resolveFirst).toBeDefined()
      fireEvent.change(query, { target: { value: 'ab' } })
      await act(async () => {
        await vi.advanceTimersByTimeAsync(50)
        resolveFirst(view({ q: 'a', eventType: 'all', page: 1, pageSize: 25, rangeDays: 30 }))
        await vi.advanceTimersByTimeAsync(1)
      })
      expect(query).toHaveValue('ab')
      expect(query).toHaveFocus()
      expect(getActivity.mock.calls.some(([search]) => search.q === 'ab')).toBe(false)
      await act(async () => {
        await vi.advanceTimersByTimeAsync(100)
      })
      expect(getActivity).toHaveBeenLastCalledWith(
        expect.objectContaining({ q: 'ab', page: 1 }),
        expect.anything(),
        'foreground',
      )
      expect(query).toHaveValue('ab')
      expect(query).toHaveFocus()
    } finally {
      vi.useRealTimers()
    }
  })

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
    const rows = screen.getByRole('combobox', { name: 'Events per activity page' })
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
        'foreground',
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
    const rows = screen.getByRole('combobox', { name: 'Events per activity page' })
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
        'foreground',
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

it.each([
  'The source supplied no ISBN',
  'The ISBN source could not be reached',
  'The source ISBN was malformed or incomplete',
  'The sources supplied conflicting ISBNs',
  'The supplied ISBN was not sufficiently associated with this recording',
])('explains an unchanged ISBN without offering a write: %s', async (reason) => {
  const http = createHttp()
  vi.mocked(http.getActivity).mockResolvedValue(
    activity({
      events: [
        {
          event_id: 'shadow_outcome_set_synthetic:isbn',
          destination: 'isbn',
          field_label: 'ISBN',
          state: 'unchanged',
          origin: 'automatic_policy',
          event_type: 'metadata',
          category: 'automatic',
          change: 'Left ISBN unchanged',
          change_detail: 'Left ISBN unchanged',
          evidence: [
            { label: 'Why it stayed unchanged', value: reason },
            { label: 'Outcome', value: 'No ISBN change was prepared at this evaluation' },
          ],
        },
      ],
    }),
  )
  renderRoute('/activity', http)
  await screen.findByText('Left ISBN unchanged')
  await userEvent.setup().click(screen.getByRole('button', { name: /^Details for /u }))
  expect(screen.getByText(reason)).toBeVisible()
  expect(screen.getByText('No ISBN change was prepared at this evaluation')).toBeVisible()
  expect(screen.queryByRole('button', { name: /Apply ISBN|Retry ISBN/ })).toBeNull()
})

it('skips speculative Activity data work and starts its required navigation read in foreground', async () => {
  const reads: {
    signal: AbortSignal | undefined
    purpose: string | undefined
    resolve: (value: ActivityView) => void
  }[] = []
  const http = createHttp()
  http.getActivity = vi.fn<ReviewHttp['getActivity']>(
    (_search, signal, purpose) =>
      new Promise((resolve, reject) => {
        reads.push({ signal, purpose, resolve })
        signal?.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')))
      }),
  )
  const { router } = renderRoute('/library?lens=all', http)
  expect(await screen.findByRole('heading', { name: 'Works' })).toBeVisible()
  // Library's notice reads only the consent state, in the foreground (#1236).
  await waitFor(() => expect(http.getActivityConsent).toHaveBeenCalledTimes(1))
  expect(vi.mocked(http.getActivityConsent).mock.calls[0]?.[1]).toBe('foreground')
  expect(http.getActivity).not.toHaveBeenCalled()
  await router.preloadRoute({
    to: '/activity',
    search: { eventType: 'all', rangeDays: 30, page: 1, pageSize: 25 },
  })
  expect(reads).toHaveLength(0)
  let navigation: Promise<void> | undefined
  act(() => {
    navigation = router.navigate({
      to: '/activity',
      search: { eventType: 'all', rangeDays: 30, page: 1, pageSize: 25 },
    })
  })
  await waitFor(() => expect(reads).toHaveLength(1))
  expect(reads[0]?.purpose).toBe('foreground')
  expect(reads[0]?.signal?.aborted).toBe(false)
  await act(async () => {
    reads[0]?.resolve(activity())
    await navigation
  })
  expect(await screen.findByRole('heading', { name: 'Activity and recovery' })).toBeVisible()
  expect(http.getActivity).toHaveBeenCalledTimes(1)
})

it.each([
  ['cancel', 'activity-consent'],
  ['success', 'activity-consent'],
  ['toggle', 'activity-consent'],
  ['cancel', 'other-section'],
])('closes %s from #%s without reopening on remount', async (close, hash) => {
  const user = userEvent.setup()
  const http = createHttp()
  http.getActivity = vi.fn(async () =>
    activity({
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
          destinations: [{ destination: 'title', state: 'allowed', consented: false, prepared: 1 }],
        },
      ],
    }),
  )
  const ready = permissionReadiness(await http.getActivity())
  vi.mocked(http.getActivityConsent).mockResolvedValue(consentOf(await http.getActivity()))
  vi.mocked(http.getActivity).mockClear()
  vi.mocked(http.getActivityPermissionReadiness).mockResolvedValue(ready)
  const { router } = renderRoute(
    `/activity?eventType=policy&rangeDays=7&page=1&pageSize=50#${hash}`,
    http,
  )
  if (hash !== 'activity-consent') {
    await user.click(await screen.findByRole('button', { name: 'Review write permissions' }))
  }
  await screen.findByRole('button', { name: 'Cancel permission review' })
  if (close === 'success') {
    await user.click(await screen.findByRole('checkbox'))
    await user.click(screen.getByRole('button', { name: 'Confirm write permissions' }))
    await waitFor(() =>
      expect(http.activityCommand).toHaveBeenCalledExactlyOnceWith('activity/consent', {
        grants: [{ library: 'fiction', destinations: ['title'] }],
      }),
    )
  } else if (close === 'toggle') {
    await user.click(await screen.findByRole('button', { name: 'Review write permissions' }))
  } else await user.click(screen.getByRole('button', { name: 'Cancel permission review' }))
  const closedHash = hash === 'activity-consent' ? '' : hash
  await waitFor(() => expect(router.state.location.hash).toBe(closedHash))
  expect(router.state.location.search).toEqual({
    eventType: 'policy',
    rangeDays: 7,
    page: 1,
    pageSize: 50,
  })
  expect(screen.queryByRole('button', { name: 'Cancel permission review' })).toBeNull()
  const search = { eventType: 'policy', rangeDays: 7, page: 1, pageSize: 50 } as const
  await act(async () => {
    await router.navigate({ to: '/library', search: { lens: 'all' } })
  })
  await screen.findByRole('heading', { level: 2, name: 'Works' })
  await act(async () => {
    await router.navigate({ to: '/activity', search, hash: closedHash })
  })
  await screen.findByRole('heading', { name: 'Activity and recovery' })
  expect(screen.queryByRole('button', { name: 'Cancel permission review' })).toBeNull()
  if (close !== 'success') expect(http.activityCommand).not.toHaveBeenCalled()
})

const permissionHistory = activity({
  configured_libraries: [
    { connection: 'primary', library: 'fiction', prepared: { automatic: 1, human: 0, total: 1 } },
  ],
  capabilities: [
    {
      connection: 'primary',
      library: 'fiction',
      state: 'allowed',
      destinations: [{ destination: 'title', state: 'allowed', consented: false, prepared: 1 }],
    },
  ],
})

it.each([false, null, 'error'] as const)(
  'keeps %s readiness distinct and requires explicit retry',
  async (outcome) => {
    const user = userEvent.setup()
    const http = createHttp()
    vi.mocked(http.getActivity).mockResolvedValue(permissionHistory)
    if (outcome === 'error')
      vi.mocked(http.getActivityPermissionReadiness).mockRejectedValueOnce(
        new ApiError(503, 'unavailable'),
      )
    else
      vi.mocked(http.getActivityPermissionReadiness).mockResolvedValueOnce(
        permissionReadiness(permissionHistory, outcome),
      )
    vi.mocked(http.getActivityPermissionReadiness).mockResolvedValue(
      permissionReadiness(permissionHistory),
    )
    renderRoute('/activity', http)
    expect(http.getActivityPermissionReadiness).not.toHaveBeenCalled()
    await user.click(await screen.findByRole('button', { name: 'Review write permissions' }))
    expect(
      await screen.findByText(
        outcome === false
          ? 'Backup readiness could not be confirmed. Writes cannot be enabled.'
          : outcome === null
            ? 'Readiness could not be confirmed for the current library state. Check again before enabling writes.'
            : 'Permission readiness is unavailable. No permission was granted. Try again.',
      ),
    ).toBeVisible()
    expect(screen.getByRole('button', { name: 'Confirm write permissions' })).toBeDisabled()
    // A check that answered reads Check again; one that failed or could not
    // confirm the current state reads Retry (#1286).
    await user.click(
      screen.getByRole('button', {
        name: outcome === false ? 'Check again' : 'Retry permission check',
      }),
    )
    await waitFor(() => expect(screen.getByRole('checkbox')).toBeEnabled())
    expect(screen.getByRole('checkbox')).not.toBeChecked()
    expect(http.activityCommand).not.toHaveBeenCalled()
  },
)

it('keeps history usable during checking and discards a late result after close and reopen', async () => {
  const user = userEvent.setup()
  const http = createHttp()
  vi.mocked(http.getActivity).mockResolvedValue({
    ...permissionHistory,
    waiting: { ...permissionHistory.waiting, awaiting_permission: 1 },
  })
  let finish!: (value: ReturnType<typeof permissionReadiness>) => void
  let signal: AbortSignal | undefined
  vi.mocked(http.getActivityPermissionReadiness)
    .mockImplementationOnce((requestSignal) => {
      signal = requestSignal
      return new Promise((resolve) => {
        finish = resolve
      })
    })
    .mockResolvedValue(permissionReadiness(permissionHistory, false))
  renderRoute('/activity', http)
  const summary = await screen.findByRole('region', {
    name: 'Writes are off. Review permissions when you are ready.',
  })
  const trigger = within(summary).getByRole('button', { name: 'Review write permissions' })
  await user.click(trigger)
  expect(await screen.findByText('Checking backup readiness and write permissions…')).toBeVisible()
  expect(screen.getByRole('searchbox', { name: 'Search activity' })).toBeEnabled()
  expect(screen.getByRole('button', { name: 'Confirm write permissions' })).toHaveAttribute(
    'aria-disabled',
    'true',
  )
  expect(http.getActivity).toHaveBeenCalledOnce()
  await user.click(screen.getByRole('button', { name: 'Cancel permission review' }))
  await waitFor(() => expect(trigger).toHaveFocus())
  expect(signal?.aborted).toBe(true)
  await user.click(trigger)
  await screen.findByText('Backup readiness could not be confirmed. Writes cannot be enabled.')
  await act(async () => finish(permissionReadiness(permissionHistory)))
  expect(screen.getByRole('checkbox')).toBeDisabled()
  expect(http.getActivityPermissionReadiness).toHaveBeenCalledTimes(2)
})

const pausedConnection = (state: 'paused' | 'recovered'): PreparationView => ({
  schema_version: 'review-preparation-v2',
  recovery_required: false,
  active: false,
  actionable: false,
  paused: false,
  libraries: [],
  connections: [{ connection: 'primary', code: 'unavailable', state, affected: 1, updated_at: 1 }],
})

/** One Activity page with a hand-driven live stream and a checked, ticked draft. */
const renderDraft = async (history: ActivityView = permissionHistory) => {
  const user = userEvent.setup()
  const http = createHttp()
  vi.mocked(http.getActivity).mockResolvedValue(history)
  vi.mocked(http.getActivityPermissionReadiness).mockResolvedValue(permissionReadiness(history))
  vi.mocked(http.getActivityConsent).mockResolvedValue(consentOf(history))
  const stream = createLiveStream()
  const rendered = renderRoute('/activity', http, stream)
  await user.click(await screen.findByRole('button', { name: 'Review write permissions' }))
  await user.click(await screen.findByRole('checkbox'))
  expect(screen.getByRole('button', { name: 'Confirm write permissions' })).toBeEnabled()
  return { ...rendered, http, stream, user }
}

it.each([
  'activity',
  'case',
  'library',
  'preparation',
  'resync',
  'history',
  'visibility',
  'focus',
  'offline',
  'connection',
  'stream-state',
  'connection-condition',
  'filter-change',
  'same-basis retry',
] as const)('keeps a selected permission draft on %s', async (cause) => {
  const { http, queryClient, stream, user } = await renderDraft()
  const reads = vi.mocked(http.getActivity).mock.calls.length
  await act(async () => {
    if (cause === 'history') await queryClient.invalidateQueries({ queryKey: ['activity'] })
    else if (cause === 'visibility') document.dispatchEvent(new Event('visibilitychange'))
    else if (cause === 'focus') {
      focusManager.setFocused(false)
      focusManager.setFocused(undefined)
    } else if (cause === 'offline') {
      // A flap rather than a lasting loss, which would pause every later query.
      window.dispatchEvent(new Event('offline'))
      window.dispatchEvent(new Event('online'))
    } else if (cause === 'connection') stream.setState('degraded')
    else if (cause === 'stream-state') {
      stream.setState('degraded')
      stream.setState('live')
    } else if (cause === 'connection-condition') {
      vi.mocked(http.getPreparation).mockResolvedValue(pausedConnection('paused'))
      await queryClient.invalidateQueries({ queryKey: ['review-preparation'] })
      vi.mocked(http.getPreparation).mockResolvedValue(pausedConnection('recovered'))
      await queryClient.invalidateQueries({ queryKey: ['review-preparation'] })
    } else if (cause !== 'filter-change' && cause !== 'same-basis retry')
      stream.announce({ sequence: 1, subject: null, topic: cause })
  })
  if (cause === 'filter-change') {
    await user.click(screen.getByRole('button', { name: 'Owner choices' }))
    await waitFor(() => expect(http.getActivity).toHaveBeenCalledTimes(reads + 1))
  }
  if (cause === 'same-basis retry') {
    await user.click(screen.getByRole('button', { name: 'Check again' }))
    await waitFor(() => expect(http.getActivityPermissionReadiness).toHaveBeenCalledTimes(2))
  }
  if (cause === 'history' || cause === 'activity')
    await waitFor(() => expect(http.getActivity).toHaveBeenCalledTimes(reads + 1))
  await waitFor(() =>
    expect(screen.getByRole('button', { name: 'Confirm write permissions' })).toBeEnabled(),
  )
  expect(screen.getByRole('checkbox')).toBeChecked()
  expect(screen.queryByText(basisChanged)).toBeNull()
  // The surviving draft grants exactly what was ticked, and nothing more.
  await user.click(screen.getByRole('button', { name: 'Confirm write permissions' }))
  await waitFor(() =>
    expect(http.activityCommand).toHaveBeenCalledExactlyOnceWith('activity/consent', {
      grants: [{ library: 'fiction', destinations: ['title'] }],
    }),
  )
})

it('keeps keyboard focus on Check again while its check runs, and ignores a second press', async () => {
  const { http, user } = await renderDraft()
  let finish!: (value: ReturnType<typeof permissionReadiness>) => void
  vi.mocked(http.getActivityPermissionReadiness).mockImplementationOnce(
    () => new Promise((resolve) => (finish = resolve)),
  )
  const retry = screen.getByRole('button', { name: 'Check again' })
  retry.focus()
  await user.keyboard('{Enter}')
  await screen.findByText('Checking backup readiness and write permissions…')
  expect(retry).toHaveFocus()
  expect(retry).toHaveAttribute('aria-disabled', 'true')
  await user.keyboard('{Enter}')
  expect(http.getActivityPermissionReadiness).toHaveBeenCalledTimes(2)
  await act(async () => finish(permissionReadiness(permissionHistory)))
  await waitFor(() => expect(retry).not.toHaveAttribute('aria-disabled'))
  expect(retry).toHaveFocus()
  expect(screen.getByRole('checkbox')).toBeChecked()
})

it('holds the ticks behind a connection condition and frees them on recovery', async () => {
  const { http, queryClient } = await renderDraft()
  vi.mocked(http.getPreparation).mockResolvedValue(pausedConnection('paused'))
  await act(async () => {
    await queryClient.invalidateQueries({ queryKey: ['review-preparation'] })
  })
  await waitFor(() => expect(screen.getByRole('checkbox')).toBeDisabled())
  expect(screen.getByRole('checkbox')).toBeChecked()
  expect(screen.getByRole('button', { name: 'Confirm write permissions' })).toBeDisabled()
  vi.mocked(http.getPreparation).mockResolvedValue(pausedConnection('recovered'))
  await act(async () => {
    await queryClient.invalidateQueries({ queryKey: ['review-preparation'] })
  })
  await waitFor(() => expect(screen.getByRole('checkbox')).toBeEnabled())
  expect(screen.getByRole('checkbox')).toBeChecked()
  expect(screen.getByRole('button', { name: 'Confirm write permissions' })).toBeEnabled()
})

const basisChanged = 'Your library settings changed. Review the permissions again.'
const grantedHistory: ActivityView = {
  ...permissionHistory,
  capabilities: permissionHistory.capabilities.map((item) => ({
    ...item,
    destinations: item.destinations.map((entry) => ({ ...entry, consented: true })),
  })),
}

it.each(['consent', 'policy'] as const)(
  'discards the draft when Confirm finds the %s changed, and sends nothing',
  async (change) => {
    const { http, user } = await renderDraft()
    if (change === 'consent')
      vi.mocked(http.getActivityConsent).mockResolvedValue(consentOf(grantedHistory))
    else
      vi.mocked(http.getPolicyState).mockResolvedValue({
        ...(await createHttp().getPolicyState()),
        policy_id: 'policy-2',
      })
    const consentReads = vi.mocked(http.getActivityConsent).mock.calls.length
    await user.click(screen.getByRole('button', { name: 'Confirm write permissions' }))
    const review = screen.getByRole('region', { name: 'Enable guarded writes' })
    await waitFor(() => expect(within(review).getByRole('status')).toHaveTextContent(basisChanged))
    expect(http.getActivityConsent).toHaveBeenCalledTimes(consentReads + 1)
    expect(http.activityCommand).not.toHaveBeenCalled()
    expect(screen.queryByRole('checkbox')).toBeNull()
    expect(screen.getByRole('button', { name: 'Confirm write permissions' })).toBeDisabled()
    const check = screen.getByRole('button', { name: 'Check write permissions' })
    await waitFor(() => expect(check).toHaveFocus())
    // A new check starts a new draft; the discarded ticks are not carried over.
    await user.click(check)
    expect(await screen.findByRole('checkbox')).not.toBeChecked()
    expect(within(review).getByRole('status')).not.toHaveTextContent(basisChanged)
  },
)

it('keeps the draft when the verification read fails, so Confirm can be pressed again', async () => {
  const { http, user } = await renderDraft()
  vi.mocked(http.getActivityConsent).mockRejectedValueOnce(new ApiError(503, 'unavailable'))
  const confirm = screen.getByRole('button', { name: 'Confirm write permissions' })
  await user.click(confirm)
  const review = screen.getByRole('region', { name: 'Enable guarded writes' })
  await waitFor(() =>
    expect(within(review).getByRole('alert')).toHaveTextContent(
      /^Permission readiness is unavailable\. No permission was granted\. Try again\.$/,
    ),
  )
  expect(http.activityCommand).not.toHaveBeenCalled()
  expect(screen.getByRole('checkbox')).toBeChecked()
  expect(confirm).toBeEnabled()
  await user.click(confirm)
  await waitFor(() =>
    expect(http.activityCommand).toHaveBeenCalledExactlyOnceWith('activity/consent', {
      grants: [{ library: 'fiction', destinations: ['title'] }],
    }),
  )
})

it('states the requirement beside Confirm and the scope only over included destinations', async () => {
  const user = userEvent.setup()
  const http = createHttp()
  const excludedOnly: ActivityView = {
    ...permissionHistory,
    capabilities: permissionHistory.capabilities.map((item) => ({
      ...item,
      state: 'not_ready' as const,
      destinations: item.destinations.map((entry) => ({
        ...entry,
        state: 'insufficient_permission' as const,
      })),
    })),
  }
  let finish!: (value: ReturnType<typeof permissionReadiness>) => void
  vi.mocked(http.getActivity).mockResolvedValue(permissionHistory)
  vi.mocked(http.getActivityPermissionReadiness)
    .mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finish = resolve
        }),
    )
    .mockRejectedValueOnce(new ApiError(503, 'unavailable'))
    .mockResolvedValueOnce(permissionReadiness(excludedOnly))
    .mockResolvedValue(permissionReadiness(permissionHistory))
  renderRoute('/activity', http)
  const scope = /^Only the included destinations shown above are enabled\./
  await user.click(await screen.findByRole('button', { name: 'Review write permissions' }))
  const confirm = screen.getByRole('button', { name: 'Confirm write permissions' })
  expect(confirm).toHaveAccessibleDescription(
    'Select each library you want to include, then confirm.',
  )
  expect(confirm.previousElementSibling).toHaveTextContent(
    'Select each library you want to include, then confirm.',
  )
  expect(screen.getByText('Checking backup readiness and write permissions…')).toBeVisible()
  expect(screen.queryByText(scope)).toBeNull()
  await act(async () => finish(permissionReadiness(permissionHistory)))
  expect(await screen.findByText(scope)).toBeVisible()
  await user.click(screen.getByRole('button', { name: 'Check again' }))
  await screen.findByText(
    'Permission readiness is unavailable. No permission was granted. Try again.',
  )
  expect(screen.queryByText(scope)).toBeNull()
  await user.click(screen.getByRole('button', { name: 'Retry permission check' }))
  await screen.findByText('Excluded — the connection lacks permission for it')
  expect(screen.queryByText(scope)).toBeNull()
  expect(confirm).toHaveAccessibleDescription(
    'Select each library you want to include, then confirm.',
  )
})

it('announces success through a page status already mounted and moves focus to it', async () => {
  const { http, user } = await renderDraft({
    ...permissionHistory,
    waiting: { ...permissionHistory.waiting, awaiting_permission: 1 },
  })
  const page = document.querySelector('.cm-activity-route > .activity-command-status')
  expect(page).toBeInstanceOf(HTMLElement)
  expect(page).toHaveAttribute('role', 'status')
  expect(page).toBeEmptyDOMElement()
  vi.mocked(http.getActivity).mockResolvedValue({
    ...grantedHistory,
    enabled_libraries: [{ connection: 'primary', library: 'fiction' }],
    waiting: { ...permissionHistory.waiting, scheduled: 1 },
  })
  await user.click(screen.getByRole('button', { name: 'Confirm write permissions' }))
  await waitFor(() =>
    expect(page).toHaveTextContent('Activity state refreshed from the authoritative server.'),
  )
  expect(document.querySelector('.cm-activity-route > .activity-command-status')).toBe(page)
  await waitFor(() => expect(page).toHaveFocus())
  expect(screen.queryByRole('region', { name: 'Enable guarded writes' })).toBeNull()
  // It reads directly under the card, and the next Tab continues from there.
  expect(page?.previousElementSibling).toHaveClass('cm-write-consent')
  await user.tab()
  expect(document.activeElement?.closest('.cm-activity-status')).not.toBeNull()
})

it('keeps focus on a busy Confirm and sends one grant for a double press', async () => {
  const { http } = await renderDraft()
  let finish!: () => void
  vi.mocked(http.activityCommand).mockImplementation(
    () => new Promise<never>((resolve) => (finish = () => resolve(undefined as never))),
  )
  const confirm = screen.getByRole('button', { name: 'Confirm write permissions' })
  confirm.focus()
  // Both presses land before the pending state can render.
  act(() => {
    confirm.click()
    confirm.click()
  })
  await waitFor(() => expect(http.activityCommand).toHaveBeenCalledOnce())
  expect(confirm).toHaveAttribute('aria-disabled', 'true')
  expect(confirm).toBeEnabled()
  expect(confirm).toHaveFocus()
  await userEvent.setup().keyboard('{Enter}')
  expect(http.activityCommand).toHaveBeenCalledOnce()
  await act(async () => finish())
  expect(http.activityCommand).toHaveBeenCalledOnce()
})

it('says so when a retry finds another basis and clears the ticks', async () => {
  const { http, user } = await renderDraft()
  const changed = {
    ...permissionHistory,
    capabilities: permissionHistory.capabilities.map((item) => ({
      ...item,
      destinations: item.destinations.map((entry) => ({ ...entry, state: 'not_ready' as const })),
    })),
  }
  vi.mocked(http.getActivityPermissionReadiness).mockResolvedValue(permissionReadiness(changed))
  const retry = screen.getByRole('button', { name: 'Check again' })
  await user.click(retry)
  const review = screen.getByRole('region', { name: 'Enable guarded writes' })
  await waitFor(() =>
    expect(within(review).getAllByRole('status')[0]).toHaveTextContent(basisChanged),
  )
  expect(within(review).queryAllByRole('checkbox', { checked: true })).toHaveLength(0)
  expect(screen.getByRole('button', { name: 'Confirm write permissions' })).toBeDisabled()
  expect(retry).toHaveFocus()
})

const waitingFor = (waiting: Partial<ActivityView['waiting']>): ActivityView => ({
  ...permissionHistory,
  waiting: { ...permissionHistory.waiting, ...waiting },
})

it('leaves focus on a shell control when the entry moves', async () => {
  const http = createHttp()
  vi.mocked(http.getActivity).mockResolvedValue({
    ...waitingFor({ scheduled: 1 }),
    enabled_libraries: [{ connection: 'primary', library: 'fiction' }],
  })
  const { queryClient } = renderRoute('/activity', http)
  const status = await screen.findByRole('button', { name: 'Review write permissions' })
  status.focus()
  // The owner moves to a control in the shell, outside the Activity route.
  const shell = screen.getByRole('button', { name: /Use (dark|light) mode/ })
  expect(shell.closest('.cm-activity-route')).toBeNull()
  shell.focus()
  vi.mocked(http.getActivity).mockResolvedValue(waitingFor({ awaiting_permission: 1 }))
  await act(async () => {
    await queryClient.invalidateQueries({ queryKey: ['activity'] })
  })
  await waitFor(() => expect(status).not.toBeInTheDocument())
  await act(() => new Promise((resolve) => setTimeout(resolve, 50)))
  expect(shell).toHaveFocus()
})

it.each([
  ['off', waitingFor({ awaiting_permission: 1 }), 'card'],
  ['enabled', waitingFor({ scheduled: 1 }), 'status'],
  ['applying', waitingFor({ applying: 1, scheduled: 1 }), 'status'],
  ['complete without consent', permissionHistory, 'status'],
  ['connection condition', waitingFor({ awaiting_permission: 1 }), 'status'],
  ['no libraries', activity(), 'none'],
] as const)(
  'exposes one review entry outside any disclosure in state %s',
  async (name, history, owner) => {
    const http = createHttp()
    vi.mocked(http.getActivity).mockResolvedValue({
      ...history,
      enabled_libraries:
        name === 'enabled' || name === 'applying'
          ? [{ connection: 'primary', library: 'fiction' }]
          : [],
    })
    if (name === 'connection condition')
      vi.mocked(http.getPreparation).mockResolvedValue(pausedConnection('paused'))
    renderRoute('/activity', http)
    await screen.findByRole('heading', { name: 'Activity and recovery' })
    if (name === 'connection condition') await screen.findByText('Library connection is paused')
    // The card waits for the one Settings read that names its libraries (#1286).
    const entries =
      owner === 'none'
        ? screen.queryAllByRole('button', { name: 'Review write permissions' })
        : await screen.findAllByRole('button', { name: 'Review write permissions' })
    expect(entries).toHaveLength(owner === 'none' ? 0 : 1)
    expect(document.querySelectorAll('[data-consent-entry]')).toHaveLength(entries.length)
    for (const entry of entries) {
      expect(entry.closest('details')).toBeNull()
      expect(
        entry.closest(owner === 'card' ? '.cm-write-consent' : '.cm-activity-status'),
      ).not.toBeNull()
    }
    for (const disclosure of document.querySelectorAll('details'))
      expect(
        within(disclosure as HTMLElement).queryByRole('button', { name: /Review write/ }),
      ).toBeNull()
  },
)

it('keeps the card as the entry while writes are enabling', async () => {
  const { http, user } = await renderDraft(waitingFor({ awaiting_permission: 1 }))
  vi.mocked(http.activityCommand).mockImplementation(() => new Promise(() => undefined))
  await user.click(screen.getByRole('button', { name: 'Confirm write permissions' }))
  await waitFor(() => expect(http.activityCommand).toHaveBeenCalledOnce())
  const entries = document.querySelectorAll('[data-consent-entry]')
  expect(entries).toHaveLength(1)
  expect(entries[0]).toHaveTextContent('Enabling writes…')
  expect(entries[0]?.closest('.cm-write-consent')).not.toBeNull()
  expect(screen.queryAllByRole('button', { name: 'Review write permissions' })).toHaveLength(0)
})

it('moves focus to the new entry when the focused one leaves', async () => {
  const user = userEvent.setup()
  const http = createHttp()
  const enabled = {
    ...waitingFor({ scheduled: 1 }),
    enabled_libraries: [{ connection: 'primary', library: 'fiction' }],
  }
  vi.mocked(http.getActivity).mockResolvedValue(enabled)
  const { queryClient } = renderRoute('/activity', http)
  const status = await screen.findByRole('button', { name: 'Review write permissions' })
  expect(status.closest('.cm-activity-status')).not.toBeNull()
  expect(status).not.toHaveClass('button-primary')
  await user.click(status)
  await user.click(await screen.findByRole('button', { name: 'Cancel permission review' }))
  await waitFor(() => expect(status).toHaveFocus())
  vi.mocked(http.getActivity).mockResolvedValue(waitingFor({ awaiting_permission: 1 }))
  await act(async () => {
    await queryClient.invalidateQueries({ queryKey: ['activity'] })
  })
  await waitFor(() => expect(status).not.toBeInTheDocument())
  const card = screen.getByRole('button', { name: 'Review write permissions' })
  expect(card.closest('.cm-write-consent')).not.toBeNull()
  // The card's entry is the filled primary; the status section's is plain (#1289).
  expect(card).toHaveClass('button-primary')
  await waitFor(() => expect(card).toHaveFocus())
})

it.each([false, true])(
  'checks the Library handoff after entering history settles (empty=%s)',
  async (empty) => {
    const user = userEvent.setup()
    const http = createHttp()
    const history = {
      ...permissionHistory,
      waiting: { ...permissionHistory.waiting, awaiting_permission: 1 },
      events: empty ? [] : permissionHistory.events,
    }
    let finishHistory!: (value: ActivityView) => void
    let finishPermissions!: (value: ReturnType<typeof permissionReadiness>) => void
    let signal: AbortSignal | undefined
    vi.mocked(http.getActivityConsent).mockResolvedValue(consentOf(history))
    vi.mocked(http.getActivity)
      .mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            finishHistory = resolve
          }),
      )
      .mockResolvedValue(history)
    vi.mocked(http.getActivityPermissionReadiness).mockImplementation((requestSignal) => {
      signal = requestSignal
      return new Promise((resolve) => {
        finishPermissions = resolve
      })
    })
    const { queryClient } = renderRoute('/library', http)
    await user.click(await screen.findByRole('link', { name: 'Review write permissions' }))
    await waitFor(() => expect(http.getActivity).toHaveBeenCalledTimes(1))
    expect(http.getActivityPermissionReadiness).not.toHaveBeenCalled()
    await act(async () => finishHistory(history))
    const review = await screen.findByRole('region', { name: 'Enable guarded writes' })
    await waitFor(() => expect(review).toHaveFocus())
    await screen.findByText('Checking backup readiness and write permissions…')
    expect(http.getActivityPermissionReadiness).toHaveBeenCalledOnce()
    expect(signal?.aborted).toBe(false)
    // The busy check stays in the tab order, announced as unavailable, so a
    // keyboard owner who pressed it never loses focus to <body>.
    await user.tab()
    expect(within(review).getByRole('button', { name: 'Check again' })).toHaveAttribute(
      'aria-disabled',
      'true',
    )
    expect(within(review).getByRole('button', { name: 'Check again' })).toHaveFocus()
    await user.tab()
    expect(within(review).getByRole('button', { name: 'Cancel permission review' })).toHaveFocus()
    await act(async () => finishPermissions(permissionReadiness(history)))
    expect(await screen.findByRole('checkbox')).toBeEnabled()
    expect(screen.getByRole('checkbox')).not.toBeChecked()
    expect(http.getActivityPermissionReadiness).toHaveBeenCalledOnce()
    await user.click(screen.getByRole('checkbox'))
    // History settling during a retry leaves the basis alone, so the retry
    // runs to completion and the ticks survive it (#1279).
    await user.click(within(review).getByRole('button', { name: 'Check again' }))
    await act(async () => {
      await queryClient.invalidateQueries({ queryKey: ['activity'] })
    })
    expect(signal?.aborted).toBe(false)
    await act(async () => finishPermissions(permissionReadiness(history)))
    await waitFor(() =>
      expect(
        within(review).getByRole('button', { name: 'Confirm write permissions' }),
      ).toBeEnabled(),
    )
    expect(screen.getByRole('checkbox')).toBeChecked()
    expect(http.getActivityPermissionReadiness).toHaveBeenCalledTimes(2)
    expect(http.activityCommand).not.toHaveBeenCalled()
  },
)

describe('Activity checks rows and filter notice (#1281)', () => {
  const key = `report_${'c'.repeat(64)}`
  // Ledger day headings share the one date formatter (#1286).
  const dayOf = (seconds: number) => formatDay(seconds)
  const check = (
    id: string,
    destination: string,
    field: string,
    change: string,
    extra: Partial<ActivityView['events'][number]> = {},
  ): Partial<ActivityView['events'][number]> => ({
    event_id: id,
    check_group: key,
    category: 'automatic',
    event_type: 'metadata',
    origin: 'automatic_policy',
    destination,
    field_label: field,
    state: 'recorded',
    change,
    change_detail: change,
    occurred_at: 1786000000,
    work_label: 'Synthetic checked work',
    library: 'fiction',
    evidence: [
      { label: 'Chosen by', value: 'Automatic policy' },
      { label: 'Policy setting', value: 'Title layout: Leave as it is' },
    ],
    ...extra,
  })
  const checkedTitle = check('check-title', 'title', 'Title', 'Kept Secret History', {
    state: 'no_change_required',
  })
  const keptTitle = check('check-title-kept', 'title', 'Title', 'Kept Secret History')
  const keptIsbn = check('check-isbn', 'isbn', 'ISBN', 'Left ISBN unchanged')
  const keptGenres = check('check-genres', 'genres', 'Genres', 'Kept Mystery', {
    occurred_at: 1785800000,
  })
  const members = [checkedTitle, keptTitle, keptIsbn, keptGenres]
  const byId = (id: string) => {
    const node = document.getElementById(id)
    if (!node) throw new Error(`Expected #${id}`)
    return node
  }
  const change = {
    event_id: 'owner-write',
    field_label: 'Subtitle',
    destination: 'subtitle',
    change: 'Old → New',
    change_detail: 'Set Subtitle from Old to New',
    occurred_at: 1786000000,
  }
  const ownerKeep = {
    event_id: 'owner-keep',
    state: 'keep_current',
    field_label: 'Title',
    destination: 'title',
    change: 'Kept Secret History',
    change_detail: 'Kept Title as Secret History',
    occurred_at: 1786000000,
  }
  const ledger = () => {
    const events = document.querySelector('.cm-activity-events')
    if (!events) throw new Error('Expected the ledger')
    return events
  }
  const checksRow = () => {
    const row = ledger().querySelector('article.is-checks')
    if (!(row instanceof HTMLElement)) throw new Error('Expected a checks row')
    return row
  }

  it('gathers one preparation into a checks row that leaves every change in place', async () => {
    const user = userEvent.setup()
    const http = createHttp()
    vi.mocked(http.getActivity).mockResolvedValue(
      activity({ events: [checkedTitle, change, keptTitle, ownerKeep, keptIsbn, keptGenres] }),
    )
    renderRoute('/activity', http)
    await screen.findByText('Checked 3 fields, no changes')
    const row = checksRow()
    expect(within(row).getByText('Automatic choice')).toBeVisible()
    expect(within(row).getByText('Synthetic checked work')).toBeVisible()
    expect(within(row).getByText('fiction')).toBeVisible()
    expect(within(row).getByText('No change required')).toBeVisible()
    // The sentence is the row's own text in the accessibility tree, with no
    // label on a generic element: only the disclosure button carries a name,
    // and that name starts with its visible text (#1286).
    expect(within(row).getByText('Checked 3 fields, no changes').closest('[aria-label]')).toBeNull()
    expect(
      within(row).getByText('Checked 3 fields, no changes').closest('[aria-hidden]'),
    ).toBeNull()
    expect([...row.querySelectorAll('[aria-label]')].map((node) => node.tagName)).toEqual([
      'BUTTON',
    ])
    expect(within(row).getByRole('button', { name: /^Details for / })).toHaveAccessibleName(
      'Details for Synthetic checked work checks',
    )
    // The checks row leads at its newest member's place; the change and the
    // owner keep between members stay rows of their own, in order.
    const order = [...ledger().querySelectorAll(':scope > .cm-activity-event-group > article')].map(
      (article) => {
        // The change is announced as its whole sentence, as text (#1288).
        const change = article.querySelector('.cm-activity-change')
        return (change?.querySelector('.sr-only') ?? change)?.textContent
      },
    )
    expect(order).toEqual([
      'Checked 3 fields, no changes',
      'Set Subtitle from Old to New',
      'Kept Title as Secret History',
    ])
    expect(screen.getAllByRole('heading', { level: 3 }).map((node) => node.textContent)).toEqual([
      dayOf(1786000000),
    ])

    const toggle = within(row).getByRole('button', { name: /^Details for /u })
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
    expect(toggle).toHaveAttribute('aria-controls', `activity-checks-${key}`)
    expect(document.getElementById(`activity-checks-${key}`)).toBeNull()
    await user.click(toggle)
    expect(toggle).toHaveAttribute('aria-expanded', 'true')
    expect(toggle).toHaveTextContent('Hide details')
    const list = document.getElementById(`activity-checks-${key}`)
    if (!list) throw new Error('Expected the members')
    expect(list).toHaveClass('cm-activity-checks')
    expect(screen.getByRole('list', { name: 'Synthetic checked work checks' })).toBe(list)
    expect(list.previousElementSibling).toBe(row)
    expect(row.contains(list)).toBe(false)
    // One row per checked field (#1425).
    const memberRows = [...list.querySelectorAll(':scope > li > article')]
    expect(
      memberRows.map((article) => {
        const change = article.querySelector('.cm-activity-change')
        return (change?.querySelector('[aria-hidden]') ?? change)?.textContent
      }),
    ).toEqual(['Kept Secret History', 'Left ISBN unchanged', 'Kept Mystery'])
    expect(memberRows[2]?.querySelector('time')).toHaveTextContent(dayOf(1785800000))
    expect(memberRows[0]?.querySelector('time')).not.toHaveTextContent(dayOf(1786000000))

    const isbn = memberRows[1] as HTMLElement
    await user.click(within(isbn).getByRole('button', { name: /^Details for /u }))
    expect(within(isbn).getByText('Automatic policy')).toBeVisible()
    expect(within(isbn).getByText('Title layout: Leave as it is')).toBeVisible()
    await user.click(within(isbn).getByRole('button', { name: /^Hide details for /u }))
    expect(within(isbn).queryByText('Automatic policy')).toBeNull()

    // Every durable record is reachable exactly once, as a row, a member, or
    // inside its field's Details.
    await user.click(
      within(memberRows[0] as HTMLElement).getByRole('button', { name: /^Details for /u }),
    )
    const controls = [...ledger().querySelectorAll('[aria-controls^="activity-evidence-"]')].map(
      (control) => control.getAttribute('aria-controls')?.replace('activity-evidence-', ''),
    )
    expect(controls.sort()).toEqual([
      'check-genres',
      'check-isbn',
      'check-title',
      'check-title-kept',
      'owner-keep',
      'owner-write',
    ])

    await user.click(toggle)
    expect(toggle).toHaveFocus()
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
    expect(document.getElementById(`activity-checks-${key}`)).toBeNull()
  })

  it('opens onto one row per field, its paired decision inside that row’s Details (#1425)', async () => {
    const user = userEvent.setup()
    let current = activity({ events: [checkedTitle, change, keptTitle, keptIsbn, keptGenres] })
    const http: ReviewHttp = { ...createHttp(), getActivity: vi.fn(async () => current) }
    const { queryClient } = renderRoute('/activity', http)
    await screen.findByText('Checked 3 fields, no changes')
    const toggle = within(checksRow()).getByRole('button', { name: /^Details for /u })
    await user.click(toggle)
    const list = byId(`activity-checks-${key}`)
    const fields = [...list.querySelectorAll<HTMLElement>(':scope > li > article')]
    expect(fields).toHaveLength(3)
    expect(fields.map((row) => row.querySelector('.cm-activity-work > span')?.textContent)).toEqual(
      ['Title · fiction', 'ISBN · fiction', 'Genres · fiction'],
    )
    const [title] = fields
    if (!title) throw new Error('Expected the Title row')
    // The field leads with what the check found; its recorded decision is not
    // a row of its own until that field's Details opens.
    expect(within(title).getByText('No change required')).toBeVisible()
    expect(within(title).queryByText('Automatic decision')).toBeNull()
    expect(
      document.querySelector('[aria-controls="activity-evidence-check-title-kept"]'),
    ).toBeNull()

    const details = within(title).getByRole('button', { name: /^Details for /u })
    expect(details).toHaveAttribute('aria-controls', 'activity-evidence-check-title')
    await user.click(details)
    const evidence = byId('activity-evidence-check-title')
    const paired = within(evidence).getByRole('button', { name: /^Details for /u })
    expect(paired).toHaveAttribute('aria-controls', 'activity-evidence-check-title-kept')
    // The paired decision says what it is and how it relates to the check, so
    // the field is not read as listed twice (#1286).
    expect(within(evidence).getByText('Automatic decision')).toBeVisible()
    expect(
      within(evidence).getByText(
        'This check confirmed the automatic decision below; nothing was written.',
      ),
    ).toBeVisible()
    expect(paired).toHaveAccessibleName(/^Details for Title, .*, automatic decision, [^,]+$/u)
    expect(details).toHaveAccessibleName(/^Hide details for Title, /u)
    // The nested list names what it holds (#1288).
    expect(
      within(evidence).getByRole('list', { name: 'Decisions this Title check confirmed' }),
    ).toContainElement(paired)
    await user.click(paired)
    expect(paired).toHaveAttribute('aria-expanded', 'true')
    // Both open, the field and its decision still name apart (#1286 X4).
    expect(paired).toHaveAccessibleName(/^Hide details for Title, .*, automatic decision, [^,]+$/u)
    const opened = screen
      .getAllByRole('button', { name: /details for /iu })
      .map((button) => button.getAttribute('aria-label'))
    expect(new Set(opened).size).toBe(opened.length)
    expect(
      within(byId('activity-evidence-check-title-kept')).getByText('Title layout: Leave as it is'),
    ).toBeVisible()
    // Opening the decision keeps its field open; closing it returns to the field.
    expect(details).toHaveAttribute('aria-expanded', 'true')
    await user.click(paired)
    expect(document.getElementById('activity-evidence-check-title-kept')).toBeNull()
    expect(paired).toHaveFocus()
    expect(byId('activity-evidence-check-title')).toBeVisible()
    await user.click(details)
    expect(details).toHaveAttribute('aria-expanded', 'false')
    expect(document.getElementById('activity-evidence-check-title')).toBeNull()
    expect(screen.getAllByText('Checked 3 fields, no changes')).toHaveLength(1)

    // A focused decision a refetch removes hands focus to its group's toggle.
    await user.click(details)
    within(byId('activity-evidence-check-title'))
      .getByRole('button', { name: /^Details for /u })
      .focus()
    current = activity({ events: [checkedTitle, change, keptIsbn, keptGenres] })
    await act(async () => {
      await queryClient.invalidateQueries({ queryKey: ['activity'] })
    })
    await waitFor(() =>
      expect(within(byId('activity-evidence-check-title')).queryByRole('button')).toBeNull(),
    )
    await waitFor(() => expect(toggle).toHaveFocus())
  })

  it('leads a field with its check even when its decision is listed first (#1425)', async () => {
    const user = userEvent.setup()
    const http = createHttp()
    vi.mocked(http.getActivity).mockResolvedValue(
      activity({ events: [keptTitle, checkedTitle, keptIsbn, keptGenres] }),
    )
    renderRoute('/activity', http)
    await screen.findByText('Checked 3 fields, no changes')
    await user.click(within(checksRow()).getByRole('button', { name: /^Details for /u }))
    const [title] = byId(`activity-checks-${key}`).querySelectorAll<HTMLElement>(
      ':scope > li > article',
    )
    if (!title) throw new Error('Expected the Title row')
    expect(title.querySelector(':scope > b')).toHaveTextContent('No change required')
    const details = within(title).getByRole('button', { name: /^Details for /u })
    expect(details).toHaveAttribute('aria-controls', 'activity-evidence-check-title')
    await user.click(details)
    const paired = byId('activity-evidence-check-title').querySelector(':scope article')
    expect(paired?.querySelector(':scope > b')).toHaveTextContent('Automatic decision')
  })

  it('counts each page of a split preparation by its own members and closes on paging', async () => {
    const user = userEvent.setup()
    const others = (page: number, count: number) =>
      Array.from({ length: count }, (_, index) => ({
        event_id: `other-${String(page)}-${String(index)}`,
        occurred_at: 1786000000,
      }))
    const http: ReviewHttp = {
      ...createHttp(),
      getActivity: vi.fn(async (search: ActivitySearch) =>
        activity({
          ledger: {
            event_type: 'all',
            page: search.page,
            page_count: 2,
            page_size: 25,
            query: null,
            range_days: 30,
            total: 30,
          },
          events:
            search.page === 1
              ? [...others(1, 23), checkedTitle, keptIsbn]
              : [
                  keptTitle,
                  keptGenres,
                  check('check-tags', 'tags', 'Tags', 'Kept Spy'),
                  ...others(2, 2),
                ],
        }),
      ),
    }
    renderRoute('/activity', http)
    await screen.findByText('Checked 2 fields, no changes')
    expect(screen.getByText('of 30 events').previousElementSibling).toHaveTextContent('1–25')
    expect(screen.getByText('Events')).toBeVisible()
    await user.click(within(checksRow()).getByRole('button', { name: /^Details for /u }))
    await user.click(screen.getByRole('button', { name: 'Next' }))
    await screen.findByText('Checked 3 fields, no changes')
    expect(screen.getByText('of 30 events').previousElementSibling).toHaveTextContent('26–30')
    expect(within(checksRow()).getByRole('button', { name: /^Details for /u })).toHaveAttribute(
      'aria-expanded',
      'false',
    )
    expect(ledger().querySelectorAll('article.is-checks')).toHaveLength(1)
  })

  it('keeps focus through a refetch and moves it to the ledger heading when its row leaves', async () => {
    const user = userEvent.setup()
    let current = activity({ events: [change, ...members] })
    const http: ReviewHttp = { ...createHttp(), getActivity: vi.fn(async () => current) }
    const { queryClient } = renderRoute('/activity', http)
    await user.click(
      within(await waitFor(checksRow)).getByRole('button', { name: /^Details for /u }),
    )
    const [, member] = within(byId(`activity-checks-${key}`)).getAllByRole('button', {
      name: /^Details for /u,
    })
    if (!member) throw new Error('Expected a second member')
    member.focus()

    current = activity({
      events: [
        { ...change, event_id: 'newer-write', change_id: 'newer-write' },
        change,
        ...members,
      ],
    })
    await act(async () => {
      await queryClient.invalidateQueries({ queryKey: ['activity'] })
    })
    await waitFor(() =>
      expect(ledger().querySelectorAll(':scope > .cm-activity-event-group')).toHaveLength(3),
    )
    expect(member).toHaveFocus()
    expect(checksRow().querySelector('button')).toHaveAttribute('aria-expanded', 'true')

    current = activity({ events: [change] })
    await act(async () => {
      await queryClient.invalidateQueries({ queryKey: ['activity'] })
    })
    await waitFor(() => expect(ledger().querySelector('article.is-checks')).toBeNull())
    await waitFor(() => expect(document.getElementById('activity-ledger-title')).toHaveFocus())
    expect(document.activeElement).not.toBe(document.body)

    // A standalone row's Details falls back the same way.
    current = activity({ events: [change, ownerKeep] })
    await act(async () => {
      await queryClient.invalidateQueries({ queryKey: ['activity'] })
    })
    const keep = await screen.findByText('Kept Secret History')
    const keepDetails = within(keep.closest('article') ?? keep).getByRole('button', {
      name: /^Details for /u,
    })
    keepDetails.focus()
    current = activity({ events: [change] })
    await act(async () => {
      await queryClient.invalidateQueries({ queryKey: ['activity'] })
    })
    await waitFor(() => expect(screen.queryByText('Kept Secret History')).toBeNull())
    await waitFor(() => expect(document.getElementById('activity-ledger-title')).toHaveFocus())
  })

  it('still restores focus after the window blurred while a control kept it', async () => {
    const user = userEvent.setup()
    let current = activity({ events: [change, ...members] })
    const http: ReviewHttp = { ...createHttp(), getActivity: vi.fn(async () => current) }
    const { queryClient } = renderRoute('/activity', http)
    await user.click(
      within(await waitFor(checksRow)).getByRole('button', { name: /^Details for /u }),
    )
    const [member] = within(byId(`activity-checks-${key}`)).getAllByRole('button', {
      name: /^Details for /u,
    })
    if (!member) throw new Error('Expected a member')
    member.focus()
    // Switching tabs blurs the control while it stays the active element.
    await act(async () => {
      member.dispatchEvent(new FocusEvent('focusout', { bubbles: true, relatedTarget: null }))
      await Promise.resolve()
    })
    expect(member).toHaveFocus()

    current = activity({ events: [change] })
    await act(async () => {
      await queryClient.invalidateQueries({ queryKey: ['activity'] })
    })
    await waitFor(() => expect(ledger().querySelector('article.is-checks')).toBeNull())
    await waitFor(() => expect(document.getElementById('activity-ledger-title')).toHaveFocus())
    expect(document.activeElement).not.toBe(document.body)
  })

  it('moves focus to the group toggle when a refetch removes a focused member', async () => {
    const user = userEvent.setup()
    let current = activity({ events: [change, ...members] })
    const http: ReviewHttp = { ...createHttp(), getActivity: vi.fn(async () => current) }
    const { queryClient } = renderRoute('/activity', http)
    const toggle = within(await waitFor(checksRow)).getByRole('button', { name: /^Details for /u })
    await user.click(toggle)
    const [, member] = within(byId(`activity-checks-${key}`)).getAllByRole('button', {
      name: /^Details for /u,
    })
    if (!member) throw new Error('Expected a second member')
    member.focus()

    current = activity({ events: [change, checkedTitle, keptTitle, keptGenres] })
    await act(async () => {
      await queryClient.invalidateQueries({ queryKey: ['activity'] })
    })
    await waitFor(() => expect(member).not.toBeInTheDocument())
    expect(checksRow()).toBeInTheDocument()
    await waitFor(() => expect(toggle).toHaveFocus())
  })

  it('reaches the checks toggle and then its members by keyboard', async () => {
    const user = userEvent.setup()
    const http = createHttp()
    vi.mocked(http.getActivity).mockResolvedValue(activity({ events: members }))
    renderRoute('/activity', http)
    const toggle = within(await waitFor(checksRow)).getByRole('button', { name: /^Details for /u })
    toggle.focus()
    await user.keyboard('{Enter}')
    expect(toggle).toHaveAttribute('aria-expanded', 'true')
    await user.tab()
    const list = byId(`activity-checks-${key}`)
    expect(within(list).getAllByRole('button', { name: /^Details for /u })[0]).toHaveFocus()
    await user.tab()
    expect(within(list).getAllByRole('button', { name: /^Details for /u })[1]).toHaveFocus()
  })

  it.each([
    ['an unknown filter', { eventType: 'owner_choice' }, 'unrecognised'],
    ['a kept flag', { eventType: 'all', filter: 'unrecognised' }, 'unrecognised'],
    ['a known filter', { eventType: 'policy' }, undefined],
    ['all', { eventType: 'all' }, undefined],
    ['no filter', {}, undefined],
  ])('normalises %s once and for all', (_name, raw, flag) => {
    const once = normalizeActivitySearch(raw)
    expect(once.filter).toBe(flag)
    const again: Record<string, unknown> = { ...once }
    expect(normalizeActivitySearch(again)).toEqual(once)
    expect(once.eventType).toBe('eventType' in raw && raw.eventType === 'policy' ? 'policy' : 'all')
  })

  const notice = 'That activity filter is not recognised. Showing all events.'
  const watchNotice = () => {
    const seen: string[] = []
    const observer = new MutationObserver(() => {
      const region = document.querySelector('.cm-activity-filter-notice')
      if (region && seen.length === 0) seen.push(region.textContent)
    })
    observer.observe(document.body, { childList: true, subtree: true })
    return { seen, stop: () => observer.disconnect() }
  }
  const noticeRegion = () => {
    const region = document.querySelector('.cm-activity-filter-notice')
    if (!region) throw new Error('Expected the notice region')
    return region
  }
  const paged = (): ReviewHttp => ({
    ...createHttp(),
    getActivity: vi.fn(async (search: ActivitySearch) =>
      activity({
        ledger: {
          event_type: search.eventType,
          page: search.page,
          page_count: 2,
          page_size: search.pageSize,
          query: search.q ?? null,
          range_days: search.rangeDays,
          total: 30,
        },
        events: [change],
      }),
    ),
  })

  it('says an unknown filter shows all events, from a status region mounted first', async () => {
    const watched = watchNotice()
    const { router } = renderRoute('/activity?eventType=owner_choice', paged())
    await waitFor(() => expect(noticeRegion()).toHaveTextContent(notice))
    watched.stop()
    expect(watched.seen).toEqual([''])
    expect(noticeRegion()).toHaveAttribute('role', 'status')
    expect(noticeRegion().previousElementSibling).toHaveClass('cm-activity-tools')
    expect(screen.getByRole('button', { name: 'All' })).toHaveAttribute('aria-pressed', 'true')
    expect(router.state.location.search).toMatchObject({ eventType: 'all', filter: 'unrecognised' })
  })

  it.each(['policy', 'all', 'review'])('shows no notice for the known filter %s', async (value) => {
    renderRoute(`/activity?eventType=${value}`, paged())
    await screen.findByRole('heading', { name: 'Activity and recovery' })
    await new Promise((resolve) => requestAnimationFrame(resolve))
    expect(noticeRegion()).toHaveTextContent('')
  })

  it('keeps the notice on reload while the address carries the flag', async () => {
    renderRoute('/activity?eventType=all&filter=unrecognised', paged())
    await waitFor(() => expect(noticeRegion()).toHaveTextContent(notice))
  })

  it.each([
    [
      'a chip',
      async (user: ReturnType<typeof userEvent.setup>) => {
        await user.click(screen.getByRole('button', { name: 'Owner choices' }))
      },
    ],
    [
      'a search',
      async (user: ReturnType<typeof userEvent.setup>) => {
        await user.type(screen.getByRole('searchbox', { name: 'Search activity' }), 'moons')
      },
    ],
    [
      'a date range',
      async (user: ReturnType<typeof userEvent.setup>) => {
        await user.click(screen.getByRole('combobox', { name: 'Activity date range' }))
        await user.click(screen.getByRole('option', { name: 'Last 90 days' }))
      },
    ],
    [
      'a page size',
      async (user: ReturnType<typeof userEvent.setup>) => {
        await user.click(screen.getByRole('combobox', { name: 'Events per activity page' }))
        await user.click(screen.getByRole('option', { name: '50 per page' }))
      },
    ],
    [
      'a page',
      async (user: ReturnType<typeof userEvent.setup>) => {
        await user.click(screen.getByRole('button', { name: 'Next' }))
      },
    ],
  ])('clears the notice after %s', async (_name, navigate) => {
    const user = userEvent.setup()
    const { router } = renderRoute('/activity?eventType=owner_choice', paged())
    await waitFor(() => expect(noticeRegion()).toHaveTextContent(notice))
    await navigate(user)
    await waitFor(() => expect(router.state.location.search).not.toHaveProperty('filter'))
    await waitFor(() => expect(noticeRegion()).toHaveTextContent(''))
    expect(noticeRegion()).toBeInTheDocument()
  })

  it('names write-card fields the way the ledger names them', async () => {
    const user = userEvent.setup()
    const http = createHttp()
    const history = activity({
      configured_libraries: [
        {
          connection: 'primary',
          library: 'fiction',
          prepared: { automatic: 4, human: 0, total: 4 },
        },
      ],
      waiting: {
        applying: 0,
        awaiting_permission: 4,
        held_for_recovery: 0,
        retrying: 0,
        scheduled: 0,
      },
      capabilities: [
        {
          connection: 'primary',
          library: 'fiction',
          state: 'allowed',
          destinations: [
            { destination: 'isbn', state: 'allowed', consented: false, prepared: 3 },
            { destination: 'publishedYear', state: 'allowed', consented: false, prepared: 1 },
          ],
        },
      ],
    })
    vi.mocked(http.getActivity).mockResolvedValue(history)
    vi.mocked(http.getActivityPermissionReadiness).mockResolvedValue(permissionReadiness(history))
    renderRoute('/activity', http)
    await user.click(await screen.findByRole('button', { name: 'Review write permissions' }))
    const destinations = await screen.findByRole('list', {
      name: 'fiction included and excluded destinations',
    })
    expect(
      within(destinations)
        .getAllByRole('listitem')
        .map((item) => item.querySelector('span')?.textContent),
    ).toEqual(['ISBN', 'Publication year'])
  })
})

describe('owner language in Activity (#1286)', () => {
  const [shelf] = settingsDocument.libraries
  if (!shelf) throw new Error('the settings fixture configures a library')
  const settingsNamed = () => ({
    ...settingsView(),
    document: {
      ...settingsDocument,
      libraries: [
        { ...shelf, name: 'fiction', display_name: 'Fiction Shelf' },
        { ...shelf, name: 'history', display_name: 'History Room' },
      ],
    },
  })
  const libraries = (consented: readonly string[]) =>
    activity({
      configured_libraries: ['fiction', 'history'].map((library) => ({
        connection: 'primary',
        library,
        prepared: { automatic: 1, human: 0, total: 1 },
      })),
      capabilities: ['fiction', 'history'].map((library) => ({
        connection: 'primary',
        library,
        state: 'allowed' as const,
        destinations: [
          {
            destination: 'title' as const,
            state: 'allowed' as const,
            consented: consented.includes(library),
            prepared: 1,
          },
        ],
      })),
      consented_libraries: consented.map((library) => ({ connection: 'primary', library })),
      enabled_libraries: consented.map((library) => ({ connection: 'primary', library })),
      waiting: {
        applying: 0,
        awaiting_permission: 1,
        held_for_recovery: 0,
        retrying: 0,
        scheduled: 0,
      },
    })

  it('names libraries by their Settings name in consent, review and revoke', async () => {
    const user = userEvent.setup()
    const view = libraries(['fiction'])
    const http: ReviewHttp = {
      ...createHttp(),
      getSettings: vi.fn(async () => settingsNamed()),
      getActivity: vi.fn(async () => view),
      getActivityPermissionReadiness: vi.fn(async () => permissionReadiness(view)),
    }
    renderRoute('/activity', http)

    // Partial consent says which library holds permission and which waits.
    expect(
      await screen.findByText(
        'Writes are on for Fiction Shelf; History Room needs permission. Review permissions when you are ready.',
        { exact: false },
      ),
    ).toBeVisible()
    await user.click(await screen.findByRole('button', { name: 'Review write permissions' }))
    expect(
      await screen.findByRole('list', { name: 'History Room included and excluded destinations' }),
    ).toBeVisible()
    expect(
      screen.getByRole('list', { name: 'Fiction Shelf included and excluded destinations' }),
    ).toBeVisible()
    await user.click(screen.getByRole('button', { name: 'Cancel permission review' }))
    await openActivityAuthority(user)
    const remove = screen.getByRole('button', { name: 'Remove write permission for Fiction Shelf' })
    expect(remove).toBeVisible()
    expect(remove).toHaveClass('button-danger')
    // No library slug reaches the owner.
    for (const slug of ['fiction', 'history'])
      expect(screen.queryByText(new RegExp(`\\b${slug}\\b`, 'u'))).toBeNull()
  })

  it('says readiness once, and names a held library as held, not as needing permission (#1286 X11, X12)', async () => {
    const user = userEvent.setup()
    const base = libraries(['fiction'])
    const view = {
      ...base,
      configured_libraries: [
        ...base.configured_libraries,
        {
          connection: 'primary',
          library: 'research',
          prepared: { automatic: 1, human: 0, total: 1 },
        },
      ],
      capabilities: [
        ...base.capabilities,
        {
          connection: 'primary',
          library: 'research',
          state: 'not_ready' as const,
          destinations: [
            {
              destination: 'title' as const,
              state: 'unexpected_effect_behavior' as const,
              consented: false,
              prepared: 1,
            },
          ],
        },
      ],
    }
    const http: ReviewHttp = {
      ...createHttp(),
      getSettings: vi.fn(async () => ({
        ...settingsNamed(),
        document: {
          ...settingsNamed().document,
          libraries: [
            ...settingsNamed().document.libraries,
            { ...shelf, name: 'research', display_name: 'Research Shelf' },
          ],
        },
      })),
      getActivity: vi.fn(async () => view),
      getActivityPermissionReadiness: vi.fn(async () => permissionReadiness(view)),
    }
    renderRoute('/activity', http)
    const heading = await screen.findByText(
      'Writes are on for Fiction Shelf; History Room needs permission; writes to Research Shelf are held.',
      { exact: false },
    )
    expect(heading).not.toHaveTextContent(/Research Shelf needs/u)
    const card = heading.closest('section')
    if (!card) throw new Error('the consent card is a section')
    expect(within(card).getAllByText(/readiness/iu)).toHaveLength(1)
    // The review says the same: the held library is held, not waiting.
    await user.click(await screen.findByRole('button', { name: 'Review write permissions' }))
    await screen.findByRole('list', { name: 'Research Shelf included and excluded destinations' })
    const label = (name: string) => screen.getByText(name, { selector: 'strong' }).closest('label')
    expect(label('Research Shelf')).toHaveTextContent('1 prepared · Writes held')
    expect(label('Research Shelf')).not.toHaveTextContent('Not ready yet')
    expect(label('History Room')).toHaveTextContent('Ready for consent')
  })

  it('shows an already-consented library as writes enabled', async () => {
    const user = userEvent.setup()
    const view = libraries(['fiction'])
    const http: ReviewHttp = {
      ...createHttp(),
      getSettings: vi.fn(async () => settingsNamed()),
      getActivity: vi.fn(async () => view),
      getActivityPermissionReadiness: vi.fn(async () => permissionReadiness(view)),
    }
    renderRoute('/activity', http)
    await user.click(await screen.findByRole('button', { name: 'Review write permissions' }))
    await screen.findByRole('list', { name: 'Fiction Shelf included and excluded destinations' })
    const label = (name: string) => screen.getByText(name, { selector: 'strong' }).closest('label')
    expect(label('Fiction Shelf')).toHaveTextContent('Writes enabled')
    expect(label('Fiction Shelf')).not.toHaveTextContent('Ready for consent')
    expect(label('History Room')).toHaveTextContent('Ready for consent')
  })

  it('reads a library as writes enabled once every destination that can be is consented (#1286 X8)', async () => {
    const user = userEvent.setup()
    const base = libraries(['fiction'])
    const view = {
      ...base,
      capabilities: base.capabilities.map((item) =>
        item.library === 'fiction'
          ? {
              ...item,
              state: 'partially_allowed' as const,
              destinations: [
                {
                  destination: 'title' as const,
                  state: 'allowed' as const,
                  consented: true,
                  prepared: 2,
                },
                {
                  destination: 'genres' as const,
                  state: 'allowed' as const,
                  consented: true,
                  prepared: 1,
                },
                {
                  destination: 'cover' as const,
                  state: 'field_unavailable' as const,
                  consented: false,
                  prepared: 0,
                },
              ],
            }
          : item,
      ),
    }
    const http: ReviewHttp = {
      ...createHttp(),
      getSettings: vi.fn(async () => settingsNamed()),
      getActivity: vi.fn(async () => view),
      getActivityPermissionReadiness: vi.fn(async () => permissionReadiness(view)),
    }
    renderRoute('/activity', http)
    await user.click(await screen.findByRole('button', { name: 'Review write permissions' }))
    const list = await screen.findByRole('list', {
      name: 'Fiction Shelf included and excluded destinations',
    })
    const label = screen.getByText('Fiction Shelf', { selector: 'strong' }).closest('label')
    expect(label).toHaveTextContent('Writes enabled')
    expect(label).not.toHaveTextContent('Some destinations are ready')
    expect(within(list).getAllByText(/^Writes enabled · \d+ prepared$/u)).toHaveLength(2)
    expect(within(list).queryByText(/^Included/u)).toBeNull()
    expect(within(list).getByText(/^Excluded — /u)).toBeVisible()
  })

  it('never reads a consented but suspended destination as writes enabled (#1286 B1)', async () => {
    const user = userEvent.setup()
    const base = libraries(['fiction', 'history'])
    // Consent outlives a later suspension: Fiction keeps one writable
    // destination beside a consented one now held, and History's only
    // consented destination is held.
    const view = {
      ...base,
      capabilities: base.capabilities.map((item) =>
        item.library === 'fiction'
          ? {
              ...item,
              state: 'partially_allowed' as const,
              destinations: [
                {
                  destination: 'title' as const,
                  state: 'allowed' as const,
                  consented: true,
                  prepared: 2,
                },
                {
                  destination: 'genres' as const,
                  state: 'unexpected_effect_behavior' as const,
                  consented: true,
                  prepared: 1,
                },
              ],
            }
          : {
              ...item,
              state: 'not_ready' as const,
              destinations: [
                {
                  destination: 'title' as const,
                  state: 'unexpected_effect_behavior' as const,
                  consented: true,
                  prepared: 1,
                },
              ],
            },
      ),
    }
    const http: ReviewHttp = {
      ...createHttp(),
      getSettings: vi.fn(async () => settingsNamed()),
      getActivity: vi.fn(async () => view),
      getActivityPermissionReadiness: vi.fn(async () => permissionReadiness(view)),
    }
    renderRoute('/activity', http)
    await user.click(await screen.findByRole('button', { name: 'Review write permissions' }))
    const fiction = await screen.findByRole('list', {
      name: 'Fiction Shelf included and excluded destinations',
    })
    const history = screen.getByRole('list', {
      name: 'History Room included and excluded destinations',
    })
    expect(within(fiction).getAllByText(/^Writes enabled · \d+ prepared$/u)).toHaveLength(1)
    expect(
      within(fiction).getByText('Writes held — an earlier write behaved unexpectedly'),
    ).toBeVisible()
    expect(within(history).queryByText(/Writes enabled/u)).toBeNull()
    expect(
      within(history).getByText('Writes held — an earlier write behaved unexpectedly'),
    ).toBeVisible()
    const label = (name: string) => screen.getByText(name, { selector: 'strong' }).closest('label')
    expect(label('Fiction Shelf')).toHaveTextContent('Writes enabled')
    expect(label('History Room')).toHaveTextContent('1 prepared · Writes held')
    expect(label('History Room')).not.toHaveTextContent('Writes enabled')
  })

  it('says Check again after a successful check', async () => {
    const user = userEvent.setup()
    const http = createHttp()
    vi.mocked(http.getActivity).mockResolvedValue(permissionHistory)
    vi.mocked(http.getActivityPermissionReadiness).mockResolvedValue(
      permissionReadiness(permissionHistory),
    )
    renderRoute('/activity', http)
    await user.click(await screen.findByRole('button', { name: 'Review write permissions' }))
    expect(await screen.findByRole('button', { name: 'Check again' })).toBeVisible()
    expect(screen.queryByRole('button', { name: 'Retry permission check' })).toBeNull()
  })

  it('states the lapse exclusion reason for a destination with nothing prepared', async () => {
    const user = userEvent.setup()
    const view = activity({
      configured_libraries: [
        {
          connection: 'primary',
          library: 'history',
          prepared: { automatic: 0, human: 0, total: 0 },
        },
      ],
      capabilities: [
        {
          connection: 'primary',
          library: 'history',
          state: 'not_ready',
          destinations: [
            { destination: 'title', state: 'not_ready', consented: false, prepared: 0 },
          ],
        },
      ],
      lapsed_consents: [{ connection: 'primary', library: 'history', reason: 'policy_changed' }],
    })
    const http: ReviewHttp = {
      ...createHttp(),
      getActivity: vi.fn(async () => view),
      getActivityConsent: vi.fn(async () => consentOf(view)),
      getActivityPermissionReadiness: vi.fn(async () => permissionReadiness(view)),
    }
    renderRoute('/activity', http)
    await user.click(await screen.findByRole('button', { name: 'Review write permissions' }))
    const destinations = await screen.findByRole('list', {
      name: 'history included and excluded destinations',
    })
    expect(destinations).toHaveTextContent(
      'Excluded — nothing is prepared for it yet; you can give permission again once a change is prepared',
    )
    expect(destinations).not.toHaveTextContent('it is not ready yet')
  })

  it.each([
    [
      'waiting without a remedy',
      { state: 'waiting' as const, remedy: null },
      'A source is limited',
      'A metadata source is limiting lookups',
    ],
    [
      'needing an API key',
      { state: 'waiting' as const, remedy: 'add_api_key' as const },
      'Action needed',
      'A metadata source needs attention',
    ],
  ])('a first provider throttle is never calm: %s', async (_name, overrides, header, heading) => {
    const throttle: ProviderThrottleEntry = {
      library: 'fiction',
      adapter: 'google_books',
      reasons: ['burst_throttled'],
      deferred: 3,
      next_retry_at: null,
      ...overrides,
    }
    const http: ReviewHttp = {
      ...createHttp(),
      getActivity: vi.fn(async () => activity()),
      getPreparation: vi.fn(async (): Promise<PreparationView> => ({
        schema_version: 'review-preparation-v2',
        recovery_required: false,
        active: false,
        actionable: false,
        paused: false,
        libraries: [],
        provider_throttling: { generation: 1, retry: 'available', entries: [throttle] },
      })),
    }
    renderRoute('/activity', http)
    expect(await screen.findByRole('heading', { name: heading })).toBeVisible()
    expect(document.querySelector('.cm-activity-calm')).toHaveTextContent(header)
    expect(
      screen.getByRole('link', { name: 'Review metadata sources in Settings' }),
    ).toHaveAttribute('href', '/settings?group=providers')
    // The calm sentinels stay exactly as they are elsewhere.
    for (const calm of ['No intervention needed', 'No action required'])
      expect(screen.queryByText(calm)).toBeNull()
  })

  it('gives every disclosure in a page a distinct accessible name', async () => {
    const user = userEvent.setup()
    const http: ReviewHttp = {
      ...createHttp(),
      getActivity: vi.fn(async () =>
        activity({
          events: [
            { event_id: 'isbn-a', work_label: 'First Book' },
            { event_id: 'isbn-b', work_label: 'Second Book' },
            {
              event_id: 'title-a',
              work_label: 'First Book',
              destination: 'title',
              field_label: 'Title',
            },
          ],
        }),
      ),
    }
    renderRoute('/activity', http)
    const disclosures = await screen.findAllByRole('button', { name: /^Details for /u })
    const names = disclosures.map((button) => button.getAttribute('aria-label'))
    expect(names).toEqual([
      `Details for ISBN, First Book, ${rowAt(1785742465)}`,
      `Details for ISBN, Second Book, ${rowAt(1785742465)}`,
      `Details for Title, First Book, ${rowAt(1785742465)}`,
    ])
    expect(new Set(names).size).toBe(names.length)
    // The visible text stays inside the name (WCAG 2.5.3).
    for (const button of disclosures) expect(button).toHaveTextContent('Details')
    const [first] = disclosures
    if (!first) throw new Error('Expected a disclosure')
    await user.click(first)
    expect(first).toHaveAccessibleName(`Hide details for ISBN, First Book, ${rowAt(1785742465)}`)
  })

  it('names two policy events on one work distinctly', async () => {
    const policyEvent = (
      eventId: string,
      occurredAt: number,
    ): Partial<ActivityView['events'][number]> => ({
      event_id: eventId,
      category: 'policy',
      event_type: 'policy',
      field_label: 'Policy',
      library: 'Library policy',
      destination: 'policy',
      state: 'completed',
      origin: 'policy',
      occurred_at: occurredAt,
      work_label: 'Library policy',
      change: '1 change activated',
      change_detail: '1 change activated',
    })
    const http: ReviewHttp = {
      ...createHttp(),
      getActivity: vi.fn(async () =>
        activity({
          events: [policyEvent('policy-late', 1785742465), policyEvent('policy-early', 1785735265)],
        }),
      ),
    }
    renderRoute('/activity', http)
    const disclosures = await screen.findAllByRole('button', { name: /^Details for Policy/u })
    const names = disclosures.map((button) => button.getAttribute('aria-label'))
    expect(names).toEqual([
      `Details for Policy, Library policy, ${rowAt(1785742465)}`,
      `Details for Policy, Library policy, ${rowAt(1785735265)}`,
    ])
    expect(new Set(names).size).toBe(2)
  })

  it.each([
    [1, '1 recovery item needs review'],
    [2, '2 recovery items need review'],
  ])('agrees the recovery verb with %i held item', async (count, summary) => {
    const http: ReviewHttp = {
      ...createHttp(),
      getActivity: vi.fn(async () =>
        activity({
          enabled_libraries: [{ connection: 'primary', library: 'fiction' }],
          suspensions: (['title', 'authors'] as const).slice(0, count).map((destination) => ({
            connection: 'primary',
            destination,
            reason: 'unexpected_effect_behavior' as const,
            scope: 'destination' as const,
            updated_at: 1,
          })),
        }),
      ),
    }
    renderRoute('/activity', http)
    expect(await screen.findByText(summary)).toBeVisible()
  })
})
