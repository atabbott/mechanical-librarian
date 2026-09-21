import { QueryClient, QueryClientProvider, QueryObserver } from '@tanstack/react-query'
import { RouterProvider, createMemoryHistory } from '@tanstack/react-router'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { ApiError } from '../src/api/http'
import type { LibraryView, PreparationView, ReviewHttp } from '../src/api/http'
import type { LiveStreamState, LiveUpdate, LiveUpdateStream } from '../src/liveUpdates'
import { coalesceMilliseconds } from '../src/liveInvalidation'
import { createApplicationRouter, preparationQuery } from '../src/router'
import { createHttp, createLiveStream, library, renderRoute } from './fixtures'

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

it('keeps initial collection requests during shell resync and confirms with one follow-up', async () => {
  const reads: { resolve: (value: LibraryView) => void; signal: AbortSignal | undefined }[] = []
  const http = createHttp()
  http.getLibrary = vi.fn<ReviewHttp['getLibrary']>(
    (_search, signal) =>
      new Promise((resolve) => {
        reads.push({ resolve, signal })
      }),
  )
  const updates = createLiveStream()
  renderRoute('/library?lens=all', http, updates)
  expect(await screen.findByRole('heading', { name: 'Works' })).toBeVisible()
  expect(screen.getByText('Loading works…')).toBeVisible()
  await act(async () => {
    updates.announce({ sequence: 0, subject: null, topic: 'resync' })
    await new Promise((resolve) => setTimeout(resolve, coalesceMilliseconds + 20))
  })
  expect(reads).toHaveLength(1)
  expect(reads[0]?.signal?.aborted).toBe(false)
  await act(async () => reads[0]?.resolve(library))
  await waitFor(() => expect(reads).toHaveLength(2))
  expect(screen.queryByText('Loading works…')).toBeNull()
  await act(async () => reads[1]?.resolve(library))
  expect(http.getLibrary).toHaveBeenCalledTimes(2)
})

describe('Application shell', () => {
  it('mounts navigation and selected detail independently of a pending collection and cancels it on departure', async () => {
    let librarySignal: AbortSignal | undefined
    const http: ReviewHttp = {
      ...createHttp(),
      getAttention: vi.fn(async () => ({
        total: 1,
        items: [
          {
            id: 'case-1',
            destination: 'title',
            title: 'A Book',
            authors: [],
            library: 'Books',
            mediaKind: 'ebook',
            classificationReason: 'Title needs a decision',
            notices: [],
            provenance: [],
            workAttentionCount: 1,
          },
        ],
      })),
      getLibrary: vi.fn<ReviewHttp['getLibrary']>((_search, signal) => {
        librarySignal = signal
        return new Promise((_resolve, reject) => {
          signal?.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')))
        })
      }),
    }
    const { router } = renderRoute('/library?lens=all&preview=case-1&field=title', http)
    expect(await screen.findByRole('link', { name: 'Policy' })).toBeVisible()
    expect(await screen.findByText('Loading works…')).toBeVisible()
    expect(
      await screen.findByRole('button', { name: 'Choose current value: Old title' }),
    ).toBeVisible()
    expect(http.getPreparation).toHaveBeenCalled()
    expect(http.getAttention).toHaveBeenCalled()
    expect(http.getCaseDetail).toHaveBeenCalledWith('case-1', expect.any(AbortSignal))
    expect(screen.queryByText('0 total')).toBeNull()
    expect(screen.queryByText('No exact-current works match this search and lens.')).toBeNull()
    await act(async () => {
      await router.navigate({ to: '/policy', search: { section: 'identity' } })
    })
    await waitFor(() => expect(librarySignal?.aborted).toBe(true))
    expect(http.getPolicyState).toHaveBeenCalled()
    expect(http.getShadowPosture).toHaveBeenCalled()
    expect(http.getAutomaticSafety).toHaveBeenCalled()
  })

  it('hands the setup run into the selectable Library preparation summary', async () => {
    vi.stubGlobal('matchMedia', vi.fn(reducedMotion))
    HTMLElement.prototype.scrollIntoView = vi.fn()
    const http: ReviewHttp = {
      ...createHttp(),
      getPreparation: vi.fn(async (): Promise<PreparationView> => ({
        schema_version: 'review-preparation-v2',
        active: true,
        actionable: false,
        paused: false,
        first_boot: true,
        first_boot_complete: false,
        first_boot_works: null,
        presentation: {
          run_id: `preparation_run_${'a'.repeat(64)}`,
          state: 'running',
          triggers: [
            {
              trigger: 'initial_setup',
              accepted_count: 1,
              first_accepted_at: 100,
              last_accepted_at: 100,
            },
          ],
          passage: {
            read: 7,
            searches_finished: 4,
            compared: 2,
            checked: 1,
            ready: 1,
            failed: 0,
          },
          active_books: [{ name: 'A Book', stage: 'Comparing candidates' }],
          active_overflow: 0,
          recent_completions: [],
          throughput_per_minute: 1,
          started_at: 100,
          updated_at: 101,
          completed_at: null,
          total: 7,
          dismissible: false,
        },
        libraries: [
          {
            blocked: 0,
            recorded: 0,
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

    const summary = await screen.findByRole('button', { name: /Preparing your library/u })
    expect(summary).toBeVisible()
    expect(screen.getByText('1 ready · 0 could not finish · 1 active')).toBeVisible()
    fireEvent.click(summary)
    expect(await screen.findByRole('heading', { name: 'Now preparing' })).toBeVisible()
    expect(screen.getByText('Preparation continues')).toBeVisible()
  })

  it('shows terminal preparation outcomes in the same Library state model', async () => {
    vi.stubGlobal('matchMedia', vi.fn(reducedMotion))
    HTMLElement.prototype.scrollIntoView = vi.fn()
    const http: ReviewHttp = {
      ...createHttp(),
      getPreparation: vi.fn(async (): Promise<PreparationView> => ({
        schema_version: 'review-preparation-v2',
        active: false,
        actionable: true,
        paused: false,
        first_boot: true,
        first_boot_complete: true,
        first_boot_works: 7,
        presentation: {
          run_id: `preparation_run_${'b'.repeat(64)}`,
          state: 'completed_with_failures',
          triggers: [
            {
              trigger: 'initial_setup',
              accepted_count: 1,
              first_accepted_at: 100,
              last_accepted_at: 100,
            },
          ],
          passage: {
            read: 7,
            searches_finished: 7,
            compared: 7,
            checked: 7,
            ready: 5,
            failed: 2,
          },
          active_books: [],
          active_overflow: 0,
          recent_completions: [{ name: 'Unfinished work', outcome: 'failed', completed_at: 101 }],
          throughput_per_minute: 7,
          started_at: 100,
          updated_at: 101,
          completed_at: 101,
          total: 7,
          dismissible: true,
        },
        libraries: [
          {
            blocked: 2,
            recorded: 0,
            checked: 7,
            connection: 'primary',
            library: 'Books',
            library_id: 'books',
            ready: 5,
            rescan_requested: false,
            scan_id: 'scan-1',
            state: 'completed_with_failures',
            stages: [
              { stage: 'queued', count: 0 },
              { stage: 'extracting', count: 0 },
              { stage: 'ready', count: 5 },
              { stage: 'failed', count: 2 },
            ],
            total: 7,
          },
        ],
      })),
    }
    renderRoute('/library?lens=all', http)

    const summary = await screen.findByRole('button', {
      name: /Preparation completed with failures/u,
    })
    fireEvent.click(summary)
    expect(await screen.findByText('Preparation completed with failures')).toBeVisible()
    expect(screen.getByText('Unfinished work')).toBeVisible()
    expect(screen.getByRole('button', { name: 'Dismiss preparation' })).toBeVisible()

    fireEvent.click(screen.getByRole('button', { name: 'Preparation needs attention' }))
    expect(await screen.findByText('Complete · some books could not finish')).toBeVisible()
    expect(screen.getByText('7 of 7 checked · 5 ready · 2 could not finish')).toBeVisible()
    // A finished scan lists the buckets books actually reached, not a wall of
    // zeros for the ones nothing did.
    expect(
      [...document.querySelectorAll('.preparation-stages dt')].map((term) => term.textContent),
    ).toEqual(['Ready to browse', 'Could not finish'])
    expect(screen.queryByText('Not started')).toBeNull()
    expect(screen.queryByText('Reading your files')).toBeNull()
  })

  it('refreshes active projections when passive preparation progress changes', async () => {
    vi.useFakeTimers()
    try {
      let checked = 0
      const http: ReviewHttp = {
        ...createHttp(),
        getPreparation: vi.fn(async (): Promise<PreparationView> => ({
          schema_version: 'review-preparation-v2',
          active: true,
          actionable: false,
          paused: false,
          libraries: [
            {
              blocked: 0,
              recorded: 0,
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
        schema_version: 'review-preparation-v2',
        active: true,
        actionable: false,
        paused: false,
        libraries: [
          {
            blocked: 0,
            recorded: 0,
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
    // A state the label table does not name still reads as a label, never as
    // the store's identifier.
    expect(screen.getByText('Scanning')).toBeVisible()
    expect(screen.getByText('1 of 2 checked · 0 ready')).toBeVisible()
    expect(
      screen.getByText(
        'Scan now \u2014 and Retry after a run with failures \u2014 start a new preparation cycle for this library; books that could not finish are prepared again; provider lookups that hit limits recover automatically on the documented schedule; a failure before a report has no automatic retry.',
      ),
    ).toBeVisible()
    await user.click(screen.getByRole('button', { name: 'Close' }))
    await waitFor(() => expect(trigger).toHaveFocus())

    await user.click(trigger)
    const scan = await screen.findByRole('button', { name: 'Scan now' })
    scan.focus()
    await user.keyboard('{Escape}')
    await waitFor(() => expect(trigger).toHaveFocus())
    expect(screen.queryByRole('heading', { name: 'Background activity' })).toBeNull()

    // Escape from the trigger itself closes too, and returns nothing anywhere
    // else: focus was already where it belongs.
    await user.click(trigger)
    trigger.focus()
    await user.keyboard('{Escape}')
    await waitFor(() => expect(trigger).toHaveFocus())
    expect(screen.queryByRole('heading', { name: 'Background activity' })).toBeNull()
  })

  it('closes the preparation panel when focus tabs off either end of it', async () => {
    const user = userEvent.setup()
    const http: ReviewHttp = {
      ...createHttp(),
      getPreparation: vi.fn(async (): Promise<PreparationView> => ({
        schema_version: 'review-preparation-v2',
        active: true,
        actionable: false,
        paused: false,
        libraries: [
          {
            blocked: 0,
            recorded: 0,
            checked: 1,
            connection: 'primary',
            library: 'Books',
            library_id: 'books',
            ready: 0,
            rescan_requested: false,
            scan_id: 'scan-1',
            state: 'processing',
            total: 2,
          },
        ],
      })),
    }
    renderRoute('/library?lens=all', http)

    const trigger = await screen.findByRole('button', { name: 'Preparing libraries' })
    await user.click(trigger)
    // Tabbing off the last control closes the panel where it stands and lets
    // the browser carry on: focus lands on the next masthead control, never
    // outside a panel that is still open.
    screen.getByRole('button', { name: 'Close' }).focus()
    await user.tab()
    await waitFor(() =>
      expect(screen.queryByRole('heading', { name: 'Background activity' })).toBeNull(),
    )
    // Focus is not pulled back to the trigger: the browser continues the tab
    // order from where it was. jsdom restarts that order once the panel
    // unmounts, so the landing control itself is proved in the browser.
    expect(trigger).not.toHaveFocus()

    await user.click(trigger)
    // Shift+Tab off the first control closes it the same way.
    screen.getByRole('button', { name: 'Scan now' }).focus()
    await user.tab({ shift: true })
    await waitFor(() =>
      expect(screen.queryByRole('heading', { name: 'Background activity' })).toBeNull(),
    )
    expect(trigger).not.toHaveFocus()
  })

  it.each([
    ['paused', 'Library connection needs attention', 'Check connection again', false],
    ['checking', 'Checking library connection', 'Checking connection…', true],
  ] as const)(
    'renders %s connection recovery through Library, full Detail, and Activity without replacing route state',
    async (state, title, action, disabled) => {
      const preparation: PreparationView = {
        schema_version: 'review-preparation-v2',
        active: true,
        actionable: false,
        paused: false,
        libraries: [
          {
            blocked: 1,
            recorded: 0,
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
      schema_version: 'review-preparation-v2',
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
      schema_version: 'review-preparation-v2',
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
        schema_version: 'review-preparation-v2',
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
      schema_version: 'review-preparation-v2',
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
        schema_version: 'review-preparation-v2',
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
      schema_version: 'review-preparation-v2',
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
      schema_version: 'review-preparation-v2',
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
        schema_version: 'review-preparation-v2',
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
        '/:all::::::::',
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
      schema_version: 'review-preparation-v2',
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
      schema_version: 'review-preparation-v2',
      active: true,
      actionable: false,
      paused: false,
      libraries: [],
    }
    const inactive: PreparationView = {
      schema_version: 'review-preparation-v2',
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

  it('bounds the captured active-preparation cadence and resyncs after foreground recovery', async () => {
    vi.useFakeTimers()
    try {
      let visibility: DocumentVisibilityState = 'visible'
      Object.defineProperty(document, 'visibilityState', {
        configurable: true,
        get: () => visibility,
      })
      const listeners = new Set<(update: LiveUpdate) => void>()
      const stateListeners = new Set<(state: LiveStreamState) => void>()
      let streamState: LiveStreamState = 'connecting'
      let hasBeenLive = false
      const updates: LiveUpdateStream = {
        get state() {
          return streamState
        },
        get hasBeenLive() {
          return hasBeenLive
        },
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
      const active: PreparationView = {
        schema_version: 'review-preparation-v2',
        active: true,
        actionable: false,
        paused: false,
        libraries: [],
      }
      const preparationReads: number[] = []
      const http: ReviewHttp = {
        ...createHttp(),
        getPreparation: vi.fn(async () => {
          preparationReads.push(Date.now())
          return active
        }),
      }
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
      await act(async () => {
        await vi.waitFor(
          () => expect(screen.getByRole('heading', { level: 2, name: 'Works' })).toBeVisible(),
          { interval: 0 },
        )
      })
      expect(screen.getByRole('heading', { level: 2, name: 'Works' })).toBeVisible()
      const before = {
        attention: vi.mocked(http.getAttention).mock.calls.length,
        library: vi.mocked(http.getLibrary).mock.calls.length,
        preparation: vi.mocked(http.getPreparation).mock.calls.length,
      }
      expect(before).toEqual({ attention: 1, library: 1, preparation: 1 })
      await act(async () => {
        streamState = 'live'
        hasBeenLive = true
        for (const listener of [...stateListeners]) listener(streamState)
      })

      // Replay the fixed baseline's 98 preparation and 69 Library refresh
      // causes uniformly rather than in their original bursts. Uniform spacing
      // is the harder case for coalescing and would still cost the old bridge
      // 236 requests during this untouched 42-minute foreground session.
      const probeMilliseconds = 42 * 60 * 1_000
      const frames = [
        ...Array.from({ length: 98 }, (_, index) => ({
          at: Math.floor(((index + 1) * probeMilliseconds) / 99),
          topic: 'preparation' as const,
        })),
        ...Array.from({ length: 69 }, (_, index) => ({
          at: Math.floor(((index + 1) * probeMilliseconds) / 70),
          topic: 'library' as const,
        })),
      ].sort((left, right) => left.at - right.at)
      for (const [index, frame] of frames.entries())
        setTimeout(
          () => announce({ sequence: index + 1, subject: null, topic: frame.topic }),
          frame.at,
        )
      await act(async () => {
        await vi.advanceTimersByTimeAsync(probeMilliseconds + coalesceMilliseconds)
      })
      expect(vi.mocked(http.getAttention)).toHaveBeenCalledTimes(before.attention)
      expect(vi.mocked(http.getLibrary)).toHaveBeenCalledTimes(before.library)
      const afterIdle = {
        attention: vi.mocked(http.getAttention).mock.calls.length,
        library: vi.mocked(http.getLibrary).mock.calls.length,
        preparation: vi.mocked(http.getPreparation).mock.calls.length,
      }
      const idleRequests =
        afterIdle.attention -
        before.attention +
        (afterIdle.library - before.library) +
        (afterIdle.preparation - before.preparation)
      expect(idleRequests).toBe(20)
      const idlePreparationReads = preparationReads.slice(before.preparation)
      expect(idlePreparationReads).toHaveLength(20)
      expect(
        idlePreparationReads
          .slice(1)
          .map((at, index) => at - (idlePreparationReads[index] ?? Number.NaN)),
      ).toEqual(Array.from({ length: 19 }, () => 126_000))
      expect(afterIdle).toEqual({ attention: 1, library: 1, preparation: 21 })

      visibility = 'hidden'
      document.dispatchEvent(new Event('visibilitychange'))
      announce({ sequence: 1, subject: null, topic: 'library' })
      await act(async () => {
        await vi.advanceTimersByTimeAsync(200)
      })
      expect(vi.mocked(http.getLibrary)).toHaveBeenCalledTimes(afterIdle.library)

      visibility = 'visible'
      document.dispatchEvent(new Event('visibilitychange'))
      await act(async () => {
        await vi.advanceTimersByTimeAsync(200)
      })
      expect(vi.mocked(http.getAttention)).toHaveBeenCalledTimes(afterIdle.attention + 1)
      expect(vi.mocked(http.getLibrary)).toHaveBeenCalledTimes(afterIdle.library + 1)
      expect(vi.mocked(http.getPreparation)).toHaveBeenCalledTimes(afterIdle.preparation + 1)

      await act(async () => {
        streamState = 'degraded'
        for (const listener of [...stateListeners]) listener(streamState)
      })
      await act(async () => {
        await vi.advanceTimersByTimeAsync(1_600)
      })
      expect(vi.mocked(http.getPreparation)).toHaveBeenCalledTimes(afterIdle.preparation + 1)
    } finally {
      vi.useRealTimers()
    }
  })

  it('does not refetch preparation when a delayed route observer joins the shell query', async () => {
    let now = 0
    const clock = vi.spyOn(Date, 'now').mockImplementation(() => now)
    const http: ReviewHttp = {
      ...createHttp(),
      getPreparation: vi.fn(async (): Promise<PreparationView> => ({
        schema_version: 'review-preparation-v2',
        active: true,
        actionable: false,
        paused: false,
        libraries: [],
      })),
    }
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const shellObserver = new QueryObserver(queryClient, preparationQuery(http, true))
    const stopShell = shellObserver.subscribe(() => undefined)
    let stopRoute: () => void = () => undefined
    try {
      await waitFor(() => expect(http.getPreparation).toHaveBeenCalledOnce())

      now = 1_001
      const routeObserver = new QueryObserver(queryClient, preparationQuery(http, true))
      stopRoute = routeObserver.subscribe(() => undefined)

      await waitFor(() => expect(http.getPreparation).toHaveBeenCalledOnce())
    } finally {
      stopRoute()
      stopShell()
      clock.mockRestore()
    }
  })

  it('lets TanStack cancellation abort one held preparation read', async () => {
    let aborted = false
    const http: ReviewHttp = {
      ...createHttp(),
      getPreparation: vi.fn(
        (signal?: AbortSignal) =>
          new Promise<PreparationView>((_resolve, reject) => {
            signal?.addEventListener('abort', () => {
              aborted = true
              reject(new DOMException('aborted', 'AbortError'))
            })
          }),
      ),
    }
    const { queryClient } = renderRoute('/library?lens=all', http)
    await waitFor(() => expect(http.getPreparation).toHaveBeenCalledOnce())

    await queryClient.cancelQueries({ queryKey: ['review-preparation'], exact: true })

    expect(aborted).toBe(true)
  })

  it('takes a distinct authoritative read for an event arriving during a slow refresh', async () => {
    const updates = createLiveStream()
    const initial: PreparationView = {
      schema_version: 'review-preparation-v2',
      active: true,
      actionable: false,
      paused: false,
      libraries: [],
    }
    const afterFirstEvent = { ...initial, paused: true }
    const afterSecondEvent = { ...initial, actionable: true }
    let releaseFirstEvent: (value: PreparationView) => void = () => undefined
    let firstEventAborted = false
    const http: ReviewHttp = {
      ...createHttp(),
      getPreparation: vi.fn((signal?: AbortSignal) => {
        const request = vi.mocked(http.getPreparation).mock.calls.length
        if (request === 1) return Promise.resolve(initial)
        if (request === 2)
          return new Promise<PreparationView>((resolve) => {
            releaseFirstEvent = resolve
            signal?.addEventListener('abort', () => {
              firstEventAborted = true
            })
          })
        return Promise.resolve(afterSecondEvent)
      }),
    }
    const { queryClient } = renderRoute('/library?lens=all', http, updates)
    await waitFor(() => expect(queryClient.getQueryData(['review-preparation'])).toBe(initial))

    act(() => updates.announce({ sequence: 1, subject: null, topic: 'preparation' }))
    await waitFor(() => expect(http.getPreparation).toHaveBeenCalledTimes(2))
    act(() => updates.announce({ sequence: 2, subject: null, topic: 'preparation' }))

    await waitFor(() => expect(http.getPreparation).toHaveBeenCalledTimes(3))
    expect(firstEventAborted).toBe(true)
    expect(queryClient.getQueryData(['review-preparation'])).toBe(afterSecondEvent)
    await act(async () => releaseFirstEvent(afterFirstEvent))
    expect(queryClient.getQueryData(['review-preparation'])).toBe(afterSecondEvent)
  })

  it('keeps reconnect debt through cancellation and a failed superseding read', async () => {
    const updates = createLiveStream()
    const runId = `preparation_run_${'c'.repeat(64)}`
    const initialPresentation: NonNullable<PreparationView['presentation']> = {
      run_id: runId,
      state: 'running',
      triggers: [
        {
          trigger: 'manual_scan',
          accepted_count: 1,
          first_accepted_at: 100,
          last_accepted_at: 100,
        },
      ],
      passage: { read: 1, searches_finished: 0, compared: 0, checked: 0, ready: 0, failed: 0 },
      active_books: [{ name: 'Confirmed title', stage: 'Reading library record' }],
      active_overflow: 0,
      recent_completions: [],
      throughput_per_minute: 0,
      started_at: 100,
      updated_at: 100,
      completed_at: null,
      total: 1,
      dismissible: false,
    }
    const initial: PreparationView = {
      schema_version: 'review-preparation-v2',
      active: true,
      actionable: false,
      paused: false,
      libraries: [],
      presentation: initialPresentation,
    }
    const confirmed: PreparationView = {
      ...initial,
      presentation: { ...initialPresentation, updated_at: 101 },
    }
    let heldAborted = false
    const http: ReviewHttp = {
      ...createHttp(),
      getPreparation: vi.fn((signal?: AbortSignal) => {
        const request = vi.mocked(http.getPreparation).mock.calls.length
        if (request === 1) return Promise.resolve(initial)
        if (request === 2)
          return new Promise<PreparationView>((_resolve, reject) => {
            signal?.addEventListener('abort', () => {
              heldAborted = true
              reject(new DOMException('aborted', 'AbortError'))
            })
          })
        if (request === 3) return Promise.reject(new ApiError(503, 'unavailable'))
        return Promise.resolve(confirmed)
      }),
    }
    const { queryClient } = renderRoute('/library?lens=all', http, updates)
    await waitFor(() => expect(queryClient.getQueryData(['review-preparation'])).toBe(initial))
    await screen.findByRole('button', { name: /Preparing your library/u })
    const debtHistory: boolean[] = []
    const preparationActions: string[] = []
    const stopObservingDebt = queryClient.getQueryCache().subscribe((event) => {
      if (event.query.queryHash === '["review-preparation-invalidation-debt"]')
        debtHistory.push(
          queryClient.getQueryData(['review-preparation-invalidation-debt']) === true,
        )
      if (event.query.queryHash === '["review-preparation"]' && event.type === 'updated')
        preparationActions.push(
          `${event.action.type}:${'manual' in event.action ? String(event.action.manual) : '-'}:${event.query.state.fetchStatus}`,
        )
    })

    act(() => updates.setState('degraded'))
    act(() => updates.setState('live'))
    await waitFor(() => expect(http.getPreparation).toHaveBeenCalledTimes(2))
    act(() => updates.announce({ sequence: 1, subject: null, topic: 'preparation' }))

    await waitFor(() => expect(http.getPreparation).toHaveBeenCalledTimes(3))
    expect(heldAborted).toBe(true)
    expect(queryClient.getQueryData(['review-preparation-invalidation-debt'])).toBe(true)
    expect(screen.getByText(/Reconnecting · last confirmed progress shown/u)).toBeVisible()
    expect(queryClient.getQueryData(['review-preparation'])).toBe(initial)
    expect(debtHistory).not.toContain(false)

    await waitFor(() => expect(http.getPreparation).toHaveBeenCalledTimes(4), { timeout: 2_500 })
    await waitFor(() => expect(queryClient.getQueryData(['review-preparation'])).toBe(confirmed))
    expect(preparationActions.at(-1)).toBe('success:undefined:idle')
    await waitFor(() =>
      expect(queryClient.getQueryData(['review-preparation-invalidation-debt'])).toBe(false),
    )
    expect(queryClient.getQueryData(['review-preparation'])).toBe(confirmed)
    stopObservingDebt()
  })

  it('does not discharge reconnect debt from a cancellation state restoration', async () => {
    const updates = createLiveStream()
    const initial: PreparationView = {
      schema_version: 'review-preparation-v2',
      active: true,
      actionable: false,
      paused: false,
      libraries: [],
    }
    const http: ReviewHttp = {
      ...createHttp(),
      getPreparation: vi
        .fn<ReviewHttp['getPreparation']>()
        .mockResolvedValueOnce(initial)
        .mockImplementation(() => new Promise<PreparationView>(() => undefined)),
    }
    const { queryClient } = renderRoute('/library?lens=all', http, updates)
    await waitFor(() => expect(queryClient.getQueryData(['review-preparation'])).toBe(initial))

    act(() => updates.setState('degraded'))
    act(() => updates.setState('live'))
    await waitFor(() => expect(http.getPreparation).toHaveBeenCalledTimes(2))
    act(() => {
      const preparation = queryClient
        .getQueryCache()
        .find<PreparationView>({ queryKey: ['review-preparation'] })
      if (!preparation) throw new Error('preparation query must exist')
      preparation.setState({ ...preparation.state, fetchStatus: 'idle', status: 'success' })
    })

    expect(queryClient.getQueryData(['review-preparation-invalidation-debt'])).toBe(true)
  })

  it('supersedes a held degraded poll with a distinct live confirmation read', async () => {
    const stateListeners = new Set<(state: LiveStreamState) => void>()
    let streamState: LiveStreamState = 'degraded'
    let hasBeenLive = false
    const updates: LiveUpdateStream & { setState: (state: LiveStreamState) => void } = {
      get state() {
        return streamState
      },
      get hasBeenLive() {
        return hasBeenLive
      },
      subscribe: () => () => undefined,
      observeState: (listener) => {
        stateListeners.add(listener)
        return () => stateListeners.delete(listener)
      },
      close: () => undefined,
      setState: (state) => {
        streamState = state
        if (state === 'live') hasBeenLive = true
        for (const listener of [...stateListeners]) listener(state)
      },
    }
    const degraded: PreparationView = {
      schema_version: 'review-preparation-v2',
      active: true,
      actionable: false,
      paused: true,
      libraries: [],
    }
    const confirmedLive = { ...degraded, actionable: true, paused: false }
    let releaseDegraded: (value: PreparationView) => void = () => undefined
    let degradedAborted = false
    const http: ReviewHttp = {
      ...createHttp(),
      getPreparation: vi.fn((signal?: AbortSignal) => {
        const request = vi.mocked(http.getPreparation).mock.calls.length
        if (request === 1)
          return new Promise<PreparationView>((resolve) => {
            releaseDegraded = resolve
            signal?.addEventListener('abort', () => {
              degradedAborted = true
            })
          })
        return Promise.resolve(confirmedLive)
      }),
    }
    const { queryClient } = renderRoute('/library?lens=all', http, updates)
    await waitFor(() => expect(http.getPreparation).toHaveBeenCalledOnce())
    act(() => updates.setState('live'))

    await waitFor(() => expect(http.getPreparation).toHaveBeenCalledTimes(2))
    expect(degradedAborted).toBe(true)
    expect(queryClient.getQueryData(['review-preparation'])).toBe(confirmedLive)
    await act(async () => releaseDegraded(degraded))
    expect(queryClient.getQueryData(['review-preparation'])).toBe(confirmedLive)
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
