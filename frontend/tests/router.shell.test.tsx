import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { RouterProvider, createMemoryHistory } from '@tanstack/react-router'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { PreparationView, ReviewHttp } from '../src/api/http'
import type { LiveStreamState, LiveUpdate, LiveUpdateStream } from '../src/liveUpdates'
import { createApplicationRouter } from '../src/router'
import { createHttp, renderRoute } from './fixtures'

const reducedMotion = () => ({
  matches: true,
  media: '(prefers-reduced-motion: reduce)',
  onchange: null,
  addEventListener: vi.fn(),
  removeEventListener: vi.fn(),
  addListener: vi.fn(),
  removeListener: vi.fn(),
  dispatchEvent: vi.fn(() => true),
})

afterEach(() => vi.unstubAllGlobals())

describe('Application shell', () => {
  it('hands over at the first ready work while preparation keeps streaming', async () => {
    vi.stubGlobal('matchMedia', vi.fn(reducedMotion))
    HTMLElement.prototype.scrollIntoView = vi.fn()
    const http: ReviewHttp = {
      ...createHttp(),
      getPreparation: vi.fn(async (): Promise<PreparationView> => ({
        schema_version: 'review-preparation-v1',
        active: true,
        actionable: false,
        paused: false,
        first_boot: true,
        first_boot_complete: false,
        first_boot_works: null,
        libraries: [
          {
            blocked: 0,
            checked: 1,
            connection: 'primary',
            library: 'Books',
            library_id: 'books',
            ready: 1,
            rescan_requested: false,
            scan_id: 'scan-1',
            state: 'processing',
            total: 7,
          },
        ],
      })),
    }
    renderRoute('/library?lens=all', http)

    expect(await screen.findByRole('heading', { name: 'Your work is ready' })).toHaveFocus()
    expect(screen.queryByRole('link', { name: 'Open workspace' })).toBeNull()
    expect(screen.getByText('Preparation continues here as more work becomes ready.')).toBeVisible()
    expect(screen.getByText('1 ready · 1 of 7 checked')).toBeVisible()
    expect(screen.queryByText(/Preparation complete/u)).toBeNull()
  })

  it('shows the unique-work measurement only at terminal first-boot preparation', async () => {
    vi.stubGlobal('matchMedia', vi.fn(reducedMotion))
    HTMLElement.prototype.scrollIntoView = vi.fn()
    const http: ReviewHttp = {
      ...createHttp(),
      getPreparation: vi.fn(async (): Promise<PreparationView> => ({
        schema_version: 'review-preparation-v1',
        active: false,
        actionable: true,
        paused: false,
        first_boot: true,
        first_boot_complete: true,
        first_boot_works: 7,
        libraries: [
          {
            blocked: 2,
            checked: 7,
            connection: 'primary',
            library: 'Books',
            library_id: 'books',
            ready: 5,
            rescan_requested: false,
            scan_id: 'scan-1',
            state: 'completed_with_failures',
            total: 7,
          },
        ],
      })),
    }
    renderRoute('/library?lens=all', http)

    expect(
      await screen.findByText(
        'Preparation complete · 7 unique works checked.',
      ),
    ).toBeVisible()
    expect(
      screen.getByText('Some work could not finish. Review the failures and recovery below.'),
    ).toBeVisible()
    expect(screen.getByText('Preparation is complete. Review your ready work below.')).toBeVisible()
    expect(screen.queryByText(/Preparation continues here/u)).toBeNull()
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
          libraries: [
            {
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
            },
          ],
        })),
      }
      const { queryClient } = renderRoute('/library?lens=all', http)
      const invalidations = vi.spyOn(queryClient, 'invalidateQueries')
      await vi.waitFor(() => expect(http.getPreparation).toHaveBeenCalledOnce())

      await act(async () => {
        await vi.advanceTimersByTimeAsync(1_600)
      })

      await vi.waitFor(() => expect(invalidations).toHaveBeenCalledWith({ queryKey: ['library'] }))
      expect(invalidations).toHaveBeenCalledWith({ queryKey: ['attention'] })
    } finally {
      vi.useRealTimers()
    }
  })

  it('returns focus to the preparation trigger when its activity panel closes', async () => {
    const user = userEvent.setup()
    const http: ReviewHttp = {
      ...createHttp(),
      getPreparation: vi.fn(async (): Promise<PreparationView> => ({
        schema_version: 'review-preparation-v1',
        active: true,
        actionable: false,
        paused: false,
        libraries: [
          {
            blocked: 0,
            checked: 1,
            connection: 'primary',
            library: 'Books',
            library_id: 'books',
            ready: 0,
            rescan_requested: false,
            scan_id: 'scan-1',
            state: 'scanning',
            total: 2,
          },
        ],
      })),
    }
    renderRoute('/library?lens=all', http)

    const trigger = await screen.findByRole('button', { name: 'Preparing libraries' })
    await user.click(trigger)
    await user.click(screen.getByRole('button', { name: 'Close' }))
    await waitFor(() => expect(trigger).toHaveFocus())
  })

  it.each([
    ['paused', 'Library connection needs attention', 'Check connection again', false],
    ['checking', 'Checking library connection', 'Checking connection…', true],
  ] as const)(
    'renders %s connection recovery through Library, full Detail, and Activity without replacing route state',
    async (state, title, action, disabled) => {
      const preparation: PreparationView = {
        schema_version: 'review-preparation-v1',
        active: true,
        actionable: false,
        paused: false,
        libraries: [
          {
            blocked: 1,
            checked: 2,
            connection: 'primary',
            library: 'Books',
            library_id: 'books',
            ready: 1,
            rescan_requested: false,
            scan_id: 'scan-1',
            state: 'processing',
            total: 3,
          },
        ],
        connections: [
          {
            connection: 'primary',
            code: 'unavailable',
            state,
            affected: 2,
            updated_at: 1,
          },
        ],
      }
      const http: ReviewHttp = { ...createHttp(), getPreparation: vi.fn(async () => preparation) }
      const { router } = renderRoute('/library?lens=needs_attention&q=A%20Book', http)
      expect(
        (await screen.findAllByText(title)).some(
          (element) => getComputedStyle(element).display !== 'none',
        ),
      ).toBe(true)
      expect(screen.getByText('Ready')).toBeVisible()
      expect(screen.getByRole('button', { name: action })).toHaveAttribute(
        'aria-disabled',
        String(disabled),
      )
      expect(screen.getByDisplayValue('A Book')).toBeVisible()
      await router.navigate({
        to: '/review/$reviewCaseId',
        params: { reviewCaseId: 'case-1' },
        search: { field: 'title', lens: 'all', subview: 'evidence' },
      })
      expect(await screen.findByRole('heading', { name: 'A Book' })).toBeVisible()
      expect(router.state.location.pathname).toBe('/review/case-1')
      await router.navigate({
        to: '/activity',
        search: { eventType: 'all', rangeDays: 30, page: 1, pageSize: 25 },
      })
      await screen.findByRole('heading', { name: 'Activity and recovery' })
      expect(
        (await screen.findAllByText(title)).some(
          (element) => getComputedStyle(element).display !== 'none',
        ),
      ).toBe(true)
      const activityTitle = state === 'paused' ? 'Library connection is paused' : title
      expect(screen.getByRole('heading', { name: activityTitle })).toBeVisible()
      expect(screen.getByText('Recovery needed').parentElement).toHaveTextContent('2')
      expect(screen.queryByRole('heading', { name: 'No intervention needed' })).toBeNull()
      expect(screen.queryByText('No action required')).toBeNull()
    },
  )

  it('does not restore a completed connection incident from retained recovered state', async () => {
    const recovered: PreparationView = {
      schema_version: 'review-preparation-v1',
      active: true,
      actionable: false,
      paused: false,
      libraries: [],
      connections: [
        {
          connection: 'primary',
          code: 'unavailable',
          state: 'recovered',
          affected: 0,
          updated_at: 2,
        },
      ],
    }
    const http: ReviewHttp = { ...createHttp(), getPreparation: vi.fn(async () => recovered) }
    const { router } = renderRoute('/library?lens=all', http)

    await screen.findByRole('heading', { name: 'Works' })
    expect(screen.queryByLabelText('Shared library connection status')).toBeNull()
    await router.navigate({
      to: '/activity',
      search: { eventType: 'all', rangeDays: 30, page: 1, pageSize: 25 },
    })
    expect(await screen.findByRole('heading', { name: 'No intervention needed' })).toBeVisible()
    expect(screen.getByText('No action required')).toBeVisible()
    expect(screen.queryByText('Library connection recovered')).toBeNull()
  })

  it('sums simultaneous connection holds while keeping one focused recovery control', async () => {
    const preparation: PreparationView = {
      schema_version: 'review-preparation-v1',
      active: true,
      actionable: false,
      paused: false,
      libraries: [],
      connections: [
        { connection: 'primary', code: 'unavailable', state: 'paused', affected: 2, updated_at: 1 },
        {
          connection: 'secondary',
          code: 'authorization',
          state: 'checking',
          affected: 3,
          updated_at: 2,
        },
      ],
    }
    const http: ReviewHttp = { ...createHttp(), getPreparation: vi.fn(async () => preparation) }
    renderRoute('/activity', http)

    expect(
      await screen.findByRole('heading', { name: 'Library connection is paused' }),
    ).toBeVisible()
    expect(screen.getByText('Recovery needed').parentElement).toHaveTextContent('5')
  })

  it.each([
    ['tls', 'secure connection settings'],
    ['authentication', 'sign-in settings'],
    ['authorization', 'access permission'],
    ['unavailable', 'connection is unavailable'],
  ] as const)(
    'uses safe owner-readable %s connection copy without raw connection values',
    async (code, expected) => {
      const preparation: PreparationView = {
        schema_version: 'review-preparation-v1',
        active: true,
        actionable: false,
        paused: false,
        libraries: [],
        connections: [
          {
            connection: 'https://private.example/token',
            code,
            state: 'paused',
            affected: 1,
            updated_at: 1,
          },
        ],
      }
      const http: ReviewHttp = { ...createHttp(), getPreparation: vi.fn(async () => preparation) }
      renderRoute('/library?lens=all', http)

      const notice = await screen.findByLabelText('Shared library connection status')
      expect(notice).toHaveTextContent(expected)
      expect(notice).not.toHaveTextContent('private.example')
      expect(notice).not.toHaveTextContent('token')
    },
  )

  it('announces checking immediately and settles the retained route authoritatively', async () => {
    const user = userEvent.setup()
    const connection = {
      connection: 'primary',
      code: 'unavailable' as const,
      state: 'paused' as const,
      affected: 2,
      updated_at: 1,
    }
    const paused: PreparationView = {
      schema_version: 'review-preparation-v1',
      active: true,
      actionable: false,
      paused: false,
      libraries: [],
      connections: [connection],
    }
    const recovered: PreparationView = {
      ...paused,
      connections: [{ ...connection, state: 'recovered', affected: 0, updated_at: 2 }],
    }
    let current = paused
    let settle: ((value: PreparationView) => void) | undefined
    const pending = new Promise<PreparationView>((resolve) => {
      settle = resolve
    })
    const http: ReviewHttp = {
      ...createHttp(),
      getPreparation: vi.fn(async () => current),
      preparationAction: vi.fn(() => pending),
    }
    const { router } = renderRoute('/library?lens=needs_attention&q=A%20Book', http)

    const check = await screen.findByRole('button', { name: 'Check connection again' })
    await user.click(check)
    expect(
      (await screen.findAllByText('Checking library connection')).some(
        (element) => getComputedStyle(element).display !== 'none',
      ),
    ).toBe(true)
    expect(check).toHaveFocus()
    expect(screen.getByRole('button', { name: 'Checking connection…' })).toHaveFocus()
    expect(screen.getByDisplayValue('A Book')).toBeVisible()

    await router.navigate({
      to: '/review/$reviewCaseId',
      params: { reviewCaseId: 'case-1' },
      search: { field: 'title', lens: 'all', subview: 'evidence' },
    })
    expect(await screen.findByRole('heading', { name: 'A Book' })).toBeVisible()
    expect(router.state.location.pathname).toBe('/review/case-1')
    current = recovered
    settle?.(recovered)
    expect(await screen.findByRole('heading', { name: 'A Book' })).toBeVisible()
  })

  it('acknowledges a successful check once, then clears it and exposes a new incident', async () => {
    vi.useFakeTimers()
    try {
      const connection = {
        connection: 'primary',
        code: 'unavailable' as const,
        state: 'paused' as const,
        affected: 2,
        updated_at: 1,
      }
      const paused: PreparationView = {
        schema_version: 'review-preparation-v1',
        active: true,
        actionable: false,
        paused: false,
        libraries: [],
        connections: [connection],
      }
      const recovered: PreparationView = {
        ...paused,
        connections: [{ ...connection, state: 'recovered', affected: 0, updated_at: 2 }],
      }
      let current = paused
      const http: ReviewHttp = {
        ...createHttp(),
        getPreparation: vi.fn(async () => current),
        preparationAction: vi.fn(async () => {
          current = recovered
          return recovered
        }),
      }
      const { queryClient } = renderRoute('/library?lens=all', http)

      await vi.waitFor(
        () => expect(screen.getByRole('button', { name: 'Check connection again' })).toBeVisible(),
        { interval: 0 },
      )
      const initialCheck = screen.getByRole('button', { name: 'Check connection again' })
      initialCheck.focus()
      expect(initialCheck).toHaveFocus()
      fireEvent.click(initialCheck)
      await vi.waitFor(
        () => expect(screen.getByText('Library connection recovered')).toBeVisible(),
        { interval: 0 },
      )
      const dismiss = screen.getByRole('button', { name: 'Dismiss' })
      expect(dismiss).toHaveFocus()
      fireEvent.click(dismiss)
      expect(screen.queryByLabelText('Shared library connection status')).toBeNull()
      await act(async () => {
        await vi.advanceTimersByTimeAsync(16)
      })
      expect(screen.getByLabelText('Library controls')).toHaveFocus()

      current = paused
      await act(async () => {
        await queryClient.invalidateQueries({ queryKey: ['review-preparation'] })
      })
      await vi.waitFor(
        () => expect(screen.getByText('Library connection needs attention')).toBeVisible(),
        { interval: 0 },
      )
      const repeatCheck = screen.getByRole('button', { name: 'Check connection again' })
      repeatCheck.focus()
      expect(repeatCheck).toHaveFocus()
      fireEvent.click(repeatCheck)
      await vi.waitFor(
        () => expect(screen.getByText('Library connection recovered')).toBeVisible(),
        { interval: 0 },
      )

      await act(async () => {
        await vi.advanceTimersByTimeAsync(7_999)
      })
      expect(screen.getByLabelText('Shared library connection status')).toBeVisible()
      await act(async () => {
        await vi.advanceTimersByTimeAsync(1)
      })
      expect(screen.queryByLabelText('Shared library connection status')).toBeNull()
      await act(async () => {
        await vi.advanceTimersByTimeAsync(16)
      })
      expect(screen.getByLabelText('Library controls')).toHaveFocus()
    } finally {
      vi.useRealTimers()
    }
  })

  it('uses the successful check response when its follow-up refetch fails', async () => {
    const user = userEvent.setup()
    const connection = {
      connection: 'primary',
      code: 'unavailable' as const,
      state: 'paused' as const,
      affected: 2,
      updated_at: 1,
    }
    const paused: PreparationView = {
      schema_version: 'review-preparation-v1',
      active: true,
      actionable: false,
      paused: false,
      libraries: [],
      connections: [connection],
    }
    const recovered: PreparationView = {
      ...paused,
      connections: [{ ...connection, state: 'recovered', affected: 0, updated_at: 2 }],
    }
    let reads = 0
    const http: ReviewHttp = {
      ...createHttp(),
      getPreparation: vi.fn(async () => {
        if (reads++ > 0) throw new Error('refetch failed')
        return paused
      }),
      preparationAction: vi.fn(async () => recovered),
    }
    renderRoute('/library?lens=all', http)

    await user.click(await screen.findByRole('button', { name: 'Check connection again' }))
    expect(await screen.findByText('Library connection recovered')).toBeVisible()
    expect(screen.queryByText('Checking library connection')).toBeNull()
  })

  it('does not revive an acknowledgement after a newer incident has replaced it', async () => {
    const user = userEvent.setup()
    const base = {
      connection: 'primary',
      code: 'unavailable' as const,
      affected: 2,
    }
    const view = (state: 'paused' | 'recovered', updated_at: number): PreparationView => ({
      schema_version: 'review-preparation-v1',
      active: true,
      actionable: false,
      paused: false,
      libraries: [],
      connections: [{ ...base, state, affected: state === 'recovered' ? 0 : 2, updated_at }],
    })
    let current = view('paused', 2)
    const firstRecovery = view('recovered', 2)
    const http: ReviewHttp = {
      ...createHttp(),
      getPreparation: vi.fn(async () => current),
      preparationAction: vi.fn(async () => {
        current = firstRecovery
        return firstRecovery
      }),
    }
    const { queryClient } = renderRoute('/library?lens=all', http)

    await user.click(await screen.findByRole('button', { name: 'Check connection again' }))
    expect(await screen.findByText('Library connection recovered')).toBeVisible()

    current = view('paused', 2)
    await act(async () => {
      await queryClient.invalidateQueries({ queryKey: ['review-preparation'] })
    })
    expect(await screen.findByText('Library connection needs attention')).toBeVisible()
    expect(screen.getByRole('button', { name: 'Check connection again' })).toHaveFocus()

    current = view('recovered', 2)
    await act(async () => {
      await queryClient.invalidateQueries({ queryKey: ['review-preparation'] })
    })
    await waitFor(() =>
      expect(screen.queryByLabelText('Shared library connection status')).toBeNull(),
    )
    expect(screen.getByLabelText('Library controls')).toHaveFocus()
  })

  it.each(['pointer', 'keyboard'] as const)(
    'keeps the focused connection control stable for %s activation',
    async (activation) => {
      const user = userEvent.setup()
      const connection = {
        connection: 'primary',
        code: 'unavailable' as const,
        state: 'paused' as const,
        affected: 2,
        updated_at: 1,
      }
      const paused: PreparationView = {
        schema_version: 'review-preparation-v1',
        active: true,
        actionable: false,
        paused: false,
        libraries: [],
        connections: [connection],
      }
      let settle: ((value: PreparationView) => void) | undefined
      const http: ReviewHttp = {
        ...createHttp(),
        getPreparation: vi.fn(async () => paused),
        preparationAction: vi.fn(
          () =>
            new Promise<PreparationView>((resolve) => {
              settle = resolve
            }),
        ),
      }
      renderRoute('/library?lens=all', http)

      const controls = await screen.findByLabelText('Library controls')
      await waitFor(() => expect(controls).toHaveFocus())
      const check = await screen.findByRole('button', { name: 'Check connection again' })
      check.focus()
      expect(check).toHaveFocus()
      if (activation === 'pointer') await user.click(check)
      else await user.keyboard('{Enter}')

      expect(http.preparationAction).toHaveBeenCalledOnce()
      expect(document.getElementById('main-content')).toHaveAttribute(
        'data-route-focus-identity',
        '/:all:::::::',
      )
      const checking = screen.getByRole('button', { name: 'Checking connection…' })
      expect(checking).toBe(check)
      expect(checking).toHaveFocus()
      expect(checking).toHaveAttribute('aria-disabled', 'true')
      expect(checking).toHaveAttribute('aria-busy', 'true')
      settle?.(paused)
    },
  )

  it('restores the actionable paused connection notice when mutation and refetch fail', async () => {
    const user = userEvent.setup()
    const connection = {
      connection: 'primary',
      code: 'unavailable' as const,
      state: 'paused' as const,
      affected: 2,
      updated_at: 1,
    }
    const paused: PreparationView = {
      schema_version: 'review-preparation-v1',
      active: true,
      actionable: false,
      paused: false,
      libraries: [],
      connections: [connection],
    }
    let calls = 0
    const http: ReviewHttp = {
      ...createHttp(),
      getPreparation: vi.fn(async () => {
        calls += 1
        if (calls > 1) throw new Error('refetch failed')
        return paused
      }),
      preparationAction: vi.fn(async () => {
        throw new Error('mutation failed')
      }),
    }
    renderRoute('/library?lens=all', http)
    const check = await screen.findByRole('button', { name: 'Check connection again' })
    await user.click(check)

    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Check connection again' })).toHaveFocus(),
    )
    expect(screen.getByText('Safely paused').parentElement).toHaveTextContent('2')
    expect(screen.getByRole('alert')).toHaveTextContent(
      'Connection checking could not be completed',
    )
  })

  it('moves focus to the persistent main landmark when polling removes preparation activity', async () => {
    const user = userEvent.setup()
    const active: PreparationView = {
      schema_version: 'review-preparation-v1',
      active: true,
      actionable: false,
      paused: false,
      libraries: [],
    }
    const inactive: PreparationView = {
      schema_version: 'review-preparation-v1',
      active: false,
      actionable: false,
      paused: false,
      libraries: [],
    }
    let current = active
    const http: ReviewHttp = { ...createHttp(), getPreparation: vi.fn(async () => current) }
    const { queryClient } = renderRoute('/library?lens=all', http)
    const trigger = await screen.findByRole('button', { name: 'Preparing libraries' })
    await user.click(trigger)
    current = inactive
    await queryClient.invalidateQueries({ queryKey: ['review-preparation'] })

    await waitFor(() =>
      expect(screen.queryByRole('button', { name: 'Preparing libraries' })).toBeNull(),
    )
    expect(document.getElementById('main-content')).toHaveFocus()
  })

  it('keeps an unknown direct entry inside a safe shell fallback', async () => {
    renderRoute('/not-a-review-route')

    expect(
      await screen.findByRole('heading', { name: 'This review route does not exist.' }),
    ).toBeVisible()
    expect(screen.getByRole('link', { name: 'Open review library' })).toHaveAttribute(
      'href',
      '/library?lens=all',
    )
  })

  it('looks again where an event points, without rebuilding the page or reloading it', async () => {
    const listeners = new Set<(update: LiveUpdate) => void>()
    const stateListeners = new Set<(state: LiveStreamState) => void>()
    const updates: LiveUpdateStream = {
      state: 'live',
      subscribe(listener) {
        listeners.add(listener)
        return () => listeners.delete(listener)
      },
      observeState(listener) {
        stateListeners.add(listener)
        return () => stateListeners.delete(listener)
      },
      close: () => undefined,
    }
    const announce = (update: LiveUpdate) => {
      for (const listener of [...listeners]) listener(update)
    }
    const http = createHttp()
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const router = createApplicationRouter(
      http,
      queryClient,
      createMemoryHistory({ initialEntries: ['/library?lens=all'] }),
      updates,
    )
    render(
      <QueryClientProvider client={queryClient}>
        <RouterProvider router={router} />
      </QueryClientProvider>,
    )
    await screen.findByRole('heading', { level: 2, name: 'Works' })
    const before = vi.mocked(http.getLibrary).mock.calls.length
    const href = router.state.location.href

    // A case changing is not a reason to rebuild the Library page: the event
    // names one identity, and only the query holding that identity is asked
    // again. Nothing here is a whole-Library projection.
    await act(async () => {
      announce({ sequence: 1, subject: `review_case_${'a'.repeat(64)}`, topic: 'case' })
      await new Promise((resolve) => setTimeout(resolve, 200))
    })
    expect(http.getLibrary).toHaveBeenCalledTimes(before)

    // The bounded page the owner is looking at is refetched exactly once for a
    // burst of Library announcements, at the same cursor and lens.
    await act(async () => {
      for (let index = 0; index < 20; index += 1)
        announce({ sequence: index + 2, subject: null, topic: 'library' })
      await new Promise((resolve) => setTimeout(resolve, 200))
    })
    expect(http.getLibrary).toHaveBeenCalledTimes(before + 1)
    expect(vi.mocked(http.getLibrary).mock.calls.at(-1)?.[0]).toEqual({ lens: 'all' })
    // And no navigation happened: a live update is a refetch, never a reload.
    expect(router.state.location.href).toBe(href)
  })

  it('says the stream is down only once it has stayed down', async () => {
    vi.useFakeTimers()
    try {
      const stateListeners = new Set<(state: LiveStreamState) => void>()
      const stream: { state: LiveStreamState } = { state: 'degraded' }
      const updates: LiveUpdateStream = {
        get state() {
          return stream.state
        },
        subscribe: () => () => undefined,
        observeState(listener) {
          stateListeners.add(listener)
          return () => stateListeners.delete(listener)
        },
        close: () => undefined,
      }
      const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
      const router = createApplicationRouter(
        createHttp(),
        queryClient,
        createMemoryHistory({ initialEntries: ['/library?lens=all'] }),
        updates,
      )
      render(
        <QueryClientProvider client={queryClient}>
          <RouterProvider router={router} />
        </QueryClientProvider>,
      )
      await act(async () => {
        await vi.advanceTimersByTimeAsync(200)
      })

      // A momentary reconnect is not news, so the first second is silent.
      expect(screen.queryByText('Reconnecting · checking for updates')).toBeNull()
      await act(async () => {
        await vi.advanceTimersByTimeAsync(1_200)
      })
      expect(screen.getByText('Reconnecting · checking for updates')).toBeVisible()

      await act(async () => {
        stream.state = 'live'
        for (const listener of [...stateListeners]) listener(stream.state)
        await vi.advanceTimersByTimeAsync(200)
      })
      expect(screen.queryByText('Reconnecting · checking for updates')).toBeNull()
    } finally {
      vi.useRealTimers()
    }
  })
})
