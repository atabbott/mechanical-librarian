import { QueryClient, QueryClientProvider, useQuery } from '@tanstack/react-query'
import {
  Outlet,
  RouterProvider,
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
} from '@tanstack/react-router'
import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, onTestFinished, vi } from 'vitest'

import { ApiError } from '../src/api/http'
import type {
  PreparationView,
  ProviderThrottleEntry,
  ProviderThrottlingView,
  ReviewHttp,
} from '../src/api/http'
import { BackgroundActivity } from '../src/components/BackgroundActivity'
import { StatusIcon } from '../src/components/MastheadIcons'
import { ThemeControl } from '../src/components/ThemeControl'
import { providerRetryPressKey, useProviderRetry } from '../src/providerThrottling'
import { createHttp, idlePreparation } from './fixtures'
import { view as settingsView } from './settingsFixtures'

const entry = (overrides: Partial<ProviderThrottleEntry> = {}): ProviderThrottleEntry => ({
  library: 'Synthetic ebooks',
  adapter: 'google_books',
  state: 'waiting',
  reasons: ['burst_throttled'],
  deferred: 214,
  next_retry_at: null,
  remedy: null,
  notice_at: null,
  untried: false,
  ...overrides,
})
const library = (
  overrides: Partial<PreparationView['libraries'][number]> = {},
): PreparationView['libraries'][number] => ({
  scan_id: null,
  connection: 'primary',
  library: 'Synthetic ebooks',
  library_id: 'library-1',
  state: 'processing',
  checked: 1_250,
  total: 10_000,
  ready: 3,
  blocked: 1,
  recorded: 2,
  not_matched: 0,
  rescan_requested: false,
  discovery_failure: null,
  stages: [{ stage: 'providers', count: 7 }],
  ...overrides,
})
const preparation = (
  entries: ProviderThrottleEntry[] | null,
  overrides: Partial<PreparationView> = {},
  generation = 4,
  retry: ProviderThrottlingView['retry'] = 'available',
  answered: ProviderThrottlingView['answered'] = [],
): PreparationView => ({
  ...idlePreparation,
  libraries: [library()],
  provider_throttling: entries === null ? null : { generation, retry, entries, answered },
  ...overrides,
})

// The default entry's source, as one whose lookups a provider answered.
const google = [{ library: 'Synthetic ebooks', adapter: 'google_books' } as const]

// A page notice stand-in: the second place Retry now lives, on the same command.
const NoticeRetry = ({ http, data }: { http: ReviewHttp; data: PreparationView | undefined }) => {
  const queryClient = useQueryClientFromContext()
  const retry = useProviderRetry(http, queryClient, data?.provider_throttling ?? null, 'notice')
  return (
    <section aria-label="Notice stand-in">
      <button type="button" aria-busy={retry.busy || undefined} onClick={retry.start}>
        Notice retry
      </button>
      {retry.press?.origin === 'notice' ? <p role="status">{retry.status}</p> : <p role="status" />}
    </section>
  )
}
let contextClient: QueryClient | null = null
const useQueryClientFromContext = () => {
  if (contextClient === null) throw new Error('no client')
  return contextClient
}

const renderActivity = (http: ReviewHttp, { notice = false, start = '/' } = {}) => {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  contextClient = queryClient
  const Harness = () => {
    const { data } = useQuery({
      queryKey: ['review-preparation'],
      queryFn: () => http.getPreparation(),
    })
    return (
      <>
        <BackgroundActivity data={data} http={http} queryClient={queryClient} />
        {notice ? <NoticeRetry http={http} data={data} /> : null}
        <main id="main-content" tabIndex={-1}>
          <Outlet />
        </main>
      </>
    )
  }
  const root = createRootRoute({ component: Harness })
  const routeTree = root.addChildren([
    createRoute({ getParentRoute: () => root, path: '/', component: () => <p>Library</p> }),
    createRoute({
      getParentRoute: () => root,
      path: '/settings',
      component: () => (
        <h1 tabIndex={-1} data-route-focus>
          Settings
        </h1>
      ),
    }),
  ])
  const router = createRouter({
    routeTree,
    history: createMemoryHistory({ initialEntries: [start] }),
  })
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  )
  return { queryClient, router }
}
const trigger = () => screen.findByRole('button', { expanded: false })
const openPopover = async (user: ReturnType<typeof userEvent.setup>) => {
  await user.click(await trigger())
  return screen.findByRole('region', { name: 'Background activity' })
}

describe('BackgroundActivity popover (#1494 acceptance 5)', () => {
  it('counts works per library and drops the stage buckets and outcome tail', async () => {
    const user = userEvent.setup()
    const http = { ...createHttp(), getPreparation: vi.fn(async () => preparation(null)) }
    renderActivity(http)
    const popover = await openPopover(user)
    expect(within(popover).getByText('1,250 of 10,000 works checked')).toBeInTheDocument()
    expect(popover.querySelector('.preparation-stages')).toBeNull()
    expect(within(popover).queryByText(/Asking sources/)).toBeNull()
    expect(popover).not.toHaveTextContent(/\b3 ready\b/)
    expect(within(popover).getByRole('button', { name: 'Scan now' })).toBeInTheDocument()
    expect(within(popover).queryByRole('heading', { name: 'Source lookups' })).toBeNull()
    expect(within(popover).getByRole('button', { name: 'Pause' })).toBeInTheDocument()
    expect(within(popover).getByRole('button', { name: 'Close' })).toBeInTheDocument()
  })

  it('lists every waiting source, those that need you first, with the remedy and Retry now', async () => {
    const user = userEvent.setup()
    const http = {
      ...createHttp(),
      getPreparation: vi.fn(async () =>
        preparation([
          entry({ adapter: 'inventaire', reasons: ['rate_limited'], deferred: 3 }),
          entry({ remedy: 'add_api_key' }),
        ]),
      ),
    }
    renderActivity(http)
    const popover = await openPopover(user)
    const section = within(popover).getByRole('region', { name: 'Source lookups' })
    expect(within(section).getByRole('heading', { level: 3 })).toHaveTextContent('Source lookups')
    const [first, second] = within(section).getAllByRole('listitem')
    if (!first || !second) throw new Error('two entries expected')
    const items = [first, second]
    expect(items.map((item) => item.querySelector('strong')?.textContent)).toEqual([
      'Google Books · Synthetic ebooks',
      'Inventaire · Synthetic ebooks',
    ])
    expect(first).toHaveTextContent('It is limiting requests. 214 lookups waiting. Retry due now.')
    expect(within(first).getByRole('link', { name: 'Add a Google Books API key' })).toHaveAttribute(
      'href',
      '/settings?group=providers',
    )
    expect(within(second).queryByRole('link')).toBeNull()
    expect(within(section).getByRole('button', { name: 'Retry now' })).toBeInTheDocument()
    // The popover's Retry now has no hint line.
    expect(within(section).queryByText(/Retries the waiting lookups/)).toBeNull()
    // The order the page asks for: libraries, Scan note, Source lookups, Pause, Close.
    const order = [...popover.querySelectorAll('article, small, .source-lookups, button')].map(
      (element) => element.textContent.slice(0, 12),
    )
    expect(order.indexOf('Source looku')).toBeGreaterThan(order.indexOf('Scan now pre'))
    expect(order.indexOf('Pause')).toBeGreaterThan(order.indexOf('Source looku'))
  })

  it('gives the file instruction in place of Retry now when retries are managed by file', async () => {
    const user = userEvent.setup()
    const http = {
      ...createHttp(),
      getPreparation: vi.fn(async () => preparation([entry()], {}, 4, 'managed_by_file')),
    }
    renderActivity(http)
    const section = within(await openPopover(user)).getByRole('region', { name: 'Source lookups' })
    expect(within(section).queryByRole('button', { name: 'Retry now' })).toBeNull()
    expect(section).toHaveTextContent(
      'To retry, raise provider_recovery_generation by one in /data/config.json and restart the service.',
    )
  })

  it('closes on following the key link, so focus is not left on the page body', async () => {
    const user = userEvent.setup()
    const http = {
      ...createHttp(),
      getPreparation: vi.fn(async () => preparation([entry({ remedy: 'check_api_key' })])),
    }
    const { router } = renderActivity(http)
    const popover = await openPopover(user)
    await user.click(within(popover).getByRole('link', { name: 'Check the source’s API key' }))
    await waitFor(() => expect(router.state.location.pathname).toBe('/settings'))
    expect(screen.queryByRole('region', { name: 'Background activity' })).toBeNull()
  })

  it('hands focus to the route when the key link leads to the Settings already shown', async () => {
    const user = userEvent.setup()
    const http = {
      ...createHttp(),
      getPreparation: vi.fn(async () => preparation([entry({ remedy: 'add_api_key' })])),
    }
    const { router } = renderActivity(http, { start: '/settings?group=connection' })
    const heading = await screen.findByRole('heading', { name: 'Settings' })
    const popover = await openPopover(user)
    await user.click(within(popover).getByRole('link', { name: 'Add a Google Books API key' }))
    await waitFor(() => expect(router.state.location.search).toEqual({ group: 'providers' }))
    // The route did not remount, so nothing else would move focus off the body.
    expect(screen.getByRole('heading', { name: 'Settings' })).toBe(heading)
    await waitFor(() => expect(heading).toHaveFocus())
  })

  it('is hidden when there are no libraries', async () => {
    const http = {
      ...createHttp(),
      getPreparation: vi.fn(async () => preparation([entry()], { libraries: [] })),
    }
    renderActivity(http)
    await screen.findByText('Library')
    await waitFor(() => expect(http.getPreparation).toHaveBeenCalled())
    expect(screen.queryByRole('button', { expanded: false })).toBeNull()
  })
})

describe('Retry now outcome (#1494 acceptance 6)', () => {
  it('says the pending line, then the outcome once the raised generation arrives, once', async () => {
    const user = userEvent.setup()
    let current = preparation([
      entry(),
      entry({ adapter: 'hardcover', reasons: ['authentication'], state: 'dormant', deferred: 31 }),
    ])
    const http: ReviewHttp = {
      ...createHttp(),
      getPreparation: vi.fn(async () => current),
      retryProviderRecovery: vi.fn(async () => settingsView()),
    }
    const { queryClient } = renderActivity(http)
    const popover = await openPopover(user)
    const status = within(popover).getByRole('status')
    expect(status).toBeEmptyDOMElement()
    // The raised generation is not read yet: still retrying.
    current = preparation([entry({ state: 'checking' })], {}, 5)
    await user.click(within(popover).getByRole('button', { name: 'Retry now' }))
    await waitFor(() => expect(status).toHaveTextContent('Every waiting lookup is retrying now.'))
    // Only the outcome that every lookup got an answer is ticked (#1494).
    expect(status).not.toHaveAttribute('data-outcome')
    expect(http.retryProviderRecovery).toHaveBeenCalledWith(4)
    current = preparation(
      [
        entry({
          state: 'dormant',
          reasons: ['authentication'],
          adapter: 'hardcover',
          deferred: 31,
        }),
      ],
      {},
      5,
    )
    await act(() => queryClient.invalidateQueries({ queryKey: ['review-preparation'] }))
    const outcome = 'Retried. Hardcover refused the API key again, so 31 lookups did not run.'
    await waitFor(() => expect(status).toHaveTextContent(outcome))
    expect(status).not.toHaveAttribute('data-outcome')
    // A later refetch with another state does not rewrite it; the region is the same.
    current = preparation([], {}, 5)
    await act(() => queryClient.invalidateQueries({ queryKey: ['review-preparation'] }))
    expect(within(popover).getByRole('status')).toBe(status)
    expect(status).toHaveTextContent(outcome)
    // Closing the popover after it showed the outcome clears it.
    await user.click(within(popover).getByRole('button', { name: 'Close' }))
    await user.click(await trigger())
    const reopened = await screen.findByRole('region', { name: 'Background activity' })
    expect(within(reopened).queryByRole('region', { name: 'Source lookups' })).toBeNull()
  })

  it('shares one pending command between the popover and the notice, live only where pressed', async () => {
    const user = userEvent.setup()
    let release: (value: ReturnType<typeof settingsView>) => void = () => undefined
    let current = preparation([entry()])
    const http: ReviewHttp = {
      ...createHttp(),
      getPreparation: vi.fn(async () => current),
      retryProviderRecovery: vi.fn(
        () => new Promise<ReturnType<typeof settingsView>>((resolve) => (release = resolve)),
      ),
    }
    const { queryClient } = renderActivity(http, { notice: true })
    const notice = await screen.findByRole('region', { name: 'Notice stand-in' })
    await user.click(await within(notice).findByRole('button', { name: 'Notice retry' }))
    const popover = await openPopover(user)
    const popoverRetry = within(popover).getByRole('button', { name: 'Retrying…' })
    expect(popoverRetry).toHaveAttribute('aria-busy', 'true')
    await user.click(popoverRetry)
    expect(http.retryProviderRecovery).toHaveBeenCalledTimes(1)
    current = preparation([], {}, 5, 'available', google)
    await act(async () => release(settingsView()))
    await act(() => queryClient.invalidateQueries({ queryKey: ['review-preparation'] }))
    const outcome = 'Retried. Every waiting lookup got an answer.'
    await waitFor(() => expect(within(notice).getByRole('status')).toHaveTextContent(outcome))
    // The popover says it too, as plain text: only the notice announces.
    const copy = within(popover).getByText(outcome)
    expect(copy).not.toHaveAttribute('role')
  })

  it('keeps a second press when the popover closes before its outcome', async () => {
    const user = userEvent.setup()
    let current = preparation([entry()])
    let release: (value: ReturnType<typeof settingsView>) => void = () => undefined
    let calls = 0
    const http: ReviewHttp = {
      ...createHttp(),
      getPreparation: vi.fn(async () => current),
      retryProviderRecovery: vi.fn(() => {
        calls += 1
        if (calls === 1) return Promise.resolve(settingsView())
        return new Promise<ReturnType<typeof settingsView>>((resolve) => (release = resolve))
      }),
    }
    const { queryClient } = renderActivity(http)
    const popover = await openPopover(user)
    current = preparation([entry()], {}, 5)
    await user.click(within(popover).getByRole('button', { name: 'Retry now' }))
    const first =
      'Retried. Google Books is still limiting requests, so 214 lookups are waiting again.'
    await waitFor(() => expect(within(popover).getByRole('status')).toHaveTextContent(first))
    // A second press, then the popover closes before the server answers.
    await user.click(within(popover).getByRole('button', { name: 'Retry now' }))
    await user.click(within(popover).getByRole('button', { name: 'Close' }))
    current = preparation([], {}, 6, 'available', google)
    await act(async () => release(settingsView()))
    await act(() => queryClient.invalidateQueries({ queryKey: ['review-preparation'] }))
    await user.click(await trigger())
    const reopened = await screen.findByRole('region', { name: 'Background activity' })
    await waitFor(() =>
      expect(reopened).toHaveTextContent('Retried. Every waiting lookup got an answer.'),
    )
  })

  it('keeps a press made on the notice when the popover closes after showing an outcome', async () => {
    const user = userEvent.setup()
    let current = preparation([entry()])
    let release: (value: ReturnType<typeof settingsView>) => void = () => undefined
    let calls = 0
    const http: ReviewHttp = {
      ...createHttp(),
      getPreparation: vi.fn(async () => current),
      retryProviderRecovery: vi.fn(() => {
        calls += 1
        if (calls === 1) return Promise.resolve(settingsView())
        return new Promise<ReturnType<typeof settingsView>>((resolve) => (release = resolve))
      }),
    }
    const { queryClient } = renderActivity(http, { notice: true })
    const popover = await openPopover(user)
    current = preparation([entry()], {}, 5)
    await user.click(within(popover).getByRole('button', { name: 'Retry now' }))
    await waitFor(() =>
      expect(within(popover).getByRole('status')).toHaveTextContent('are waiting again.'),
    )
    const notice = screen.getByRole('region', { name: 'Notice stand-in' })
    await user.click(within(notice).getByRole('button', { name: 'Notice retry' }))
    await user.click(within(popover).getByRole('button', { name: 'Close' }))
    current = preparation([], {}, 6, 'available', google)
    await act(async () => release(settingsView()))
    await act(() => queryClient.invalidateQueries({ queryKey: ['review-preparation'] }))
    await waitFor(() =>
      expect(within(notice).getByRole('status')).toHaveTextContent(
        'Retried. Every waiting lookup got an answer.',
      ),
    )
  })

  it('says without a tick that nothing is waiting when no source answered', async () => {
    const user = userEvent.setup()
    let current = preparation([entry()])
    const http: ReviewHttp = {
      ...createHttp(),
      getPreparation: vi.fn(async () => current),
      retryProviderRecovery: vi.fn(async () => settingsView()),
    }
    const { queryClient } = renderActivity(http)
    const popover = await openPopover(user)
    await user.click(within(popover).getByRole('button', { name: 'Retry now' }))
    // The waiting lookups went stale: the source left with no answer (#1494).
    current = preparation([], {}, 5)
    await act(() => queryClient.invalidateQueries({ queryKey: ['review-preparation'] }))
    const status = within(popover).getByRole('status')
    await waitFor(() => expect(status).toHaveTextContent('Retried. No lookups are waiting.'))
    expect(status).not.toHaveAttribute('data-outcome')
  })

  it('ends the pending line when another press raised the generation again first', async () => {
    const user = userEvent.setup()
    let current = preparation([entry()])
    const http: ReviewHttp = {
      ...createHttp(),
      getPreparation: vi.fn(async () => current),
      retryProviderRecovery: vi.fn(async () => settingsView()),
    }
    const { queryClient } = renderActivity(http)
    const popover = await openPopover(user)
    const status = within(popover).getByRole('status')
    // The other press is still retrying when this one's answer is read.
    current = preparation([entry({ state: 'checking' })], {}, 6)
    await user.click(within(popover).getByRole('button', { name: 'Retry now' }))
    await waitFor(() => expect(status).toHaveTextContent('Every waiting lookup is retrying now.'))
    // Generation 5, the one this press raised, is never read.
    current = preparation([entry()], {}, 6)
    await act(() => queryClient.invalidateQueries({ queryKey: ['review-preparation'] }))
    await waitFor(() =>
      expect(status).toHaveTextContent(
        'Retried. Google Books is still limiting requests, so 214 lookups are waiting again.',
      ),
    )
  })

  it('keeps the pending line while one of a source’s connections is untried', async () => {
    const user = userEvent.setup()
    let current = preparation([entry({ reasons: ['transient'], deferred: 3 })])
    const http: ReviewHttp = {
      ...createHttp(),
      getPreparation: vi.fn(async () => current),
      retryProviderRecovery: vi.fn(async () => settingsView()),
    }
    const { queryClient } = renderActivity(http)
    const popover = await openPopover(user)
    const status = within(popover).getByRole('status')
    // One connection failed; the other still awaits its first retry.
    current = preparation([entry({ reasons: ['transient'], deferred: 2, untried: true })], {}, 5)
    await user.click(within(popover).getByRole('button', { name: 'Retry now' }))
    await waitFor(() => expect(status).toHaveTextContent('Every waiting lookup is retrying now.'))
    await waitFor(() => expect(within(popover).getByText(/2 lookups waiting/)).toBeInTheDocument())
    // The raised generation is read and no outcome is saved for the press.
    expect(queryClient.getQueryData(providerRetryPressKey)).toMatchObject({
      accepted: true,
      outcome: null,
    })
    expect(status).toHaveTextContent('Every waiting lookup is retrying now.')
    // The other connection is answered: one lookup waits again.
    current = preparation([entry({ reasons: ['transient'], deferred: 1 })], {}, 5)
    await act(() => queryClient.invalidateQueries({ queryKey: ['review-preparation'] }))
    await waitFor(() =>
      expect(status).toHaveTextContent(
        'Retried. Google Books still did not answer, so 1 lookup is waiting again.',
      ),
    )
  })

  it('moves focus to the outcome line when Retry now leaves while focused', async () => {
    const user = userEvent.setup()
    let current = preparation([entry()])
    const http: ReviewHttp = {
      ...createHttp(),
      getPreparation: vi.fn(async () => current),
      retryProviderRecovery: vi.fn(async () => settingsView()),
    }
    const { queryClient } = renderActivity(http)
    const popover = await openPopover(user)
    const command = within(popover).getByRole('button', { name: 'Retry now' })
    await user.click(command)
    await waitFor(() =>
      expect(within(popover).getByRole('status')).toHaveTextContent('retrying now'),
    )
    expect(command).toHaveFocus()
    current = preparation([], {}, 5, 'available', google)
    await act(() => queryClient.invalidateQueries({ queryKey: ['review-preparation'] }))
    const status = within(popover).getByRole('status')
    await waitFor(() =>
      expect(status).toHaveTextContent('Retried. Every waiting lookup got an answer.'),
    )
    expect(within(popover).queryByRole('button', { name: /Retry now|Retrying/ })).toBeNull()
    expect(status).toHaveFocus()
    expect(status).toHaveAttribute('data-outcome', 'recovered')
    // Focus is inside the popover, so Escape still closes it onto the trigger.
    await user.keyboard('{Escape}')
    expect(screen.queryByRole('region', { name: 'Background activity' })).toBeNull()
    expect(screen.getByRole('button', { expanded: false })).toHaveFocus()
  })

  it('says a refusal where Retry now was pressed', async () => {
    const user = userEvent.setup()
    const http: ReviewHttp = {
      ...createHttp(),
      getPreparation: vi.fn(async () => preparation([entry()])),
      retryProviderRecovery: vi.fn(async () => {
        throw new ApiError(409, 'provider_recovery_stale')
      }),
    }
    renderActivity(http)
    const popover = await openPopover(user)
    await user.click(within(popover).getByRole('button', { name: 'Retry now' }))
    expect(await within(popover).findByRole('alert')).toHaveTextContent(
      'Retry now was already pressed elsewhere.',
    )
  })
})

describe('Status icon (#1494 acceptance 7)', () => {
  it('is named per state, with the tooltip saying the same words', async () => {
    const http = {
      ...createHttp(),
      getPreparation: vi.fn(async () =>
        preparation([entry({ state: 'dormant' }), entry({ adapter: 'inventaire' })], {
          active: true,
        }),
      ),
    }
    renderActivity(http)
    const button = await screen.findByRole('button', {
      name: 'Preparing libraries. A source needs you. Source lookups are waiting to retry.',
    })
    expect(button.querySelector('.masthead-tooltip')).toHaveAttribute('aria-hidden', 'true')
    expect(button.querySelector('.masthead-tooltip')).toHaveTextContent(
      'Preparing libraries. A source needs you. Source lookups are waiting to retry.',
    )
    expect(button.querySelector('.status-icon-turn')).not.toBeNull()
  })

  it('raises a day-long wait on one timer, without another preparation read', async () => {
    const real = { setTimeout: globalThis.setTimeout, clearTimeout: globalThis.clearTimeout }
    const start = new Date('2026-11-15T12:00:00Z')
    vi.useFakeTimers({
      toFake: ['Date', 'setTimeout', 'clearTimeout'],
      now: start,
      shouldAdvanceTime: true,
    })
    onTestFinished(() => {
      vi.useRealTimers()
      Object.assign(globalThis, real)
    })
    const due = start.getTime() / 1000 + 90
    const http = {
      ...createHttp(),
      getPreparation: vi.fn(async () => preparation([entry({ notice_at: due })])),
    }
    renderActivity(http)
    await screen.findByRole('button', {
      name: 'Preparation idle. Source lookups are waiting to retry.',
    })
    const reads = http.getPreparation.mock.calls.length
    await act(() => vi.advanceTimersByTimeAsync(89_000))
    expect(screen.getByRole('button', { expanded: false })).toHaveAccessibleName(
      'Preparation idle. Source lookups are waiting to retry.',
    )
    await act(() => vi.advanceTimersByTimeAsync(2_000))
    expect(screen.getByRole('button', { expanded: false })).toHaveAccessibleName(
      'Preparation idle. A source needs you.',
    )
    expect(http.getPreparation.mock.calls.length).toBe(reads)
  })

  it.each([
    ['stopped', 'rect'],
    ['needs_you', 'path'],
    ['paused', 'path'],
    ['idle', 'path'],
  ] as const)('draws the %s centre mark', (centre, element) => {
    const { container } = render(<StatusIcon centre={centre} active={false} fill={0.5} />)
    expect(container.querySelector(`svg > ${element}, svg ${element}`)).not.toBeNull()
    expect(container.querySelector('.status-icon-turn')).toBeNull()
  })

  it('fills the turning ring by the clamped share, and draws no mark while preparing', () => {
    const { container } = render(<StatusIcon centre="none" active fill={0.5} />)
    const arc = container.querySelector('.status-icon-turn circle')
    const circumference = 2 * Math.PI * 9.6
    expect(arc?.getAttribute('stroke-dasharray')).toBe(
      `${String(0.5 * circumference)} ${String((1 - 0.5) * circumference)}`,
    )
    expect(container.querySelectorAll('svg > circle')).toHaveLength(1)
  })
})

describe('ThemeControl (#1494 acceptance 8)', () => {
  it('shows a moon in the light theme and a sun in the dark one, with no visible word', async () => {
    const user = userEvent.setup()
    document.cookie = 'ml_theme=light; Path=/'
    onTestFinished(() => {
      document.cookie = 'ml_theme=; Path=/; Max-Age=0'
    })
    render(<ThemeControl />)
    const button = screen.getByRole('button', { name: 'Use dark mode' })
    expect(button).toHaveAttribute('aria-pressed', 'false')
    expect(button.querySelector('svg')).not.toBeNull()
    expect(button).not.toHaveTextContent(/Light|Dark/)
    await user.click(button)
    expect(screen.getByRole('button', { name: 'Use light mode' })).toHaveAttribute(
      'aria-pressed',
      'true',
    )
    expect(document.documentElement.dataset.theme).toBe('dark')
  })

  it('hides a tooltip shown by the pointer on Escape, wherever focus is', async () => {
    const user = userEvent.setup()
    render(
      <>
        <ThemeControl />
        <input aria-label="Elsewhere" />
      </>,
    )
    const button = screen.getByRole('button', { name: /Use (dark|light) mode/ })
    await user.click(screen.getByRole('textbox', { name: 'Elsewhere' }))
    await user.hover(button)
    await user.keyboard('{Escape}')
    expect(button).not.toHaveFocus()
    expect(button).toHaveAttribute('data-tooltip', 'dismissed')
    await user.unhover(button)
    expect(button).not.toHaveAttribute('data-tooltip')
  })

  it('hides its tooltip on Escape until focus leaves', async () => {
    const user = userEvent.setup()
    render(<ThemeControl />)
    const button = screen.getByRole('button', { name: /Use (dark|light) mode/ })
    await user.tab()
    expect(button).toHaveFocus()
    await user.keyboard('{Escape}')
    expect(button).toHaveAttribute('data-tooltip', 'dismissed')
    await user.tab()
    expect(button).not.toHaveAttribute('data-tooltip')
  })
})

describe('BackgroundActivity library reason (#2013)', () => {
  const reasons = [
    [
      'library_unavailable',
      'Audiobookshelf no longer has this library. Check it in Settings → Libraries.',
    ],
    [
      'library_too_large',
      'This library would take the catalogue past 10,000 books. Remove a library in Settings → Libraries.',
    ],
    [
      'response_too_large',
      'An answer from Audiobookshelf was over the Audiobookshelf response limit. Raise it in Settings → Background work.',
    ],
    [
      'response_unreadable',
      'Audiobookshelf’s book list for this library could not be read. Scan now to try again.',
    ],
    [
      'catalogue_unavailable',
      'Mechanical Librarian could not record this library’s books. Scan now to try again.',
    ],
    [
      'persistence',
      'Mechanical Librarian could not record this library’s books. Scan now to try again.',
    ],
    [
      'unknown',
      'Mechanical Librarian could not record this library’s books. Scan now to try again.',
    ],
  ] as const

  it.each(reasons)('says why %s stopped the library and keeps Scan now', async (code, line) => {
    const user = userEvent.setup()
    const failed = library({
      state: 'completed_with_failures',
      checked: 0,
      total: 0,
      discovery_failure: code,
    })
    const http = {
      ...createHttp(),
      getPreparation: vi.fn(async () => preparation(null, { libraries: [failed] })),
    }
    renderActivity(http)
    const popover = await openPopover(user)
    const article = within(popover).getByRole('article')
    expect(article).toHaveTextContent(line)
    expect(article).not.toHaveTextContent('Complete · some works could not finish')
    expect(article).not.toHaveTextContent('works checked')
    expect(within(article).getByRole('button', { name: 'Scan now' })).toBeInTheDocument()
    expect(within(article).getByRole('button', { name: 'Retry' })).toBeInTheDocument()
  })

  it('links the settings a reason names and closes on following it', async () => {
    const user = userEvent.setup()
    const failed = library({
      state: 'completed_with_failures',
      discovery_failure: 'response_too_large',
    })
    const http = {
      ...createHttp(),
      getPreparation: vi.fn(async () => preparation(null, { libraries: [failed] })),
    }
    const { router } = renderActivity(http)
    const popover = await openPopover(user)
    await user.click(within(popover).getByRole('link', { name: 'Settings → Background work' }))
    await waitFor(() => expect(router.state.location.search).toEqual({ group: 'background' }))
    expect(screen.queryByRole('region', { name: 'Background activity' })).toBeNull()
  })

  it('keeps the state line for a library whose works could not finish', async () => {
    const user = userEvent.setup()
    const http = {
      ...createHttp(),
      getPreparation: vi.fn(async () =>
        preparation(null, { libraries: [library({ state: 'completed_with_failures' })] }),
      ),
    }
    renderActivity(http)
    const popover = await openPopover(user)
    expect(within(popover).getByRole('article')).toHaveTextContent(
      'Complete · some works could not finish',
    )
    expect(within(popover).getByRole('article')).toHaveTextContent('works checked')
  })
})
