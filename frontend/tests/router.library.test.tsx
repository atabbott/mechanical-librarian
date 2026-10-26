import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { RouterProvider, createMemoryHistory } from '@tanstack/react-router'
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, onTestFinished, vi } from 'vitest'

import { ApiError, CsrfRenewedError, mapCaseDetail } from '../src/api/http'
import descriptionAdviceFixture from './descriptionAdviceFixture.json'
import { coalesceMilliseconds, libraryRefreshMilliseconds } from '../src/liveInvalidation'
import { ownerChoiceCount } from '../src/reviewChoiceCounts'
import { reconciliationKey } from '../src/reviewDecisionController'
import { sourceStatusLabel } from '../src/routes/LibraryPage'
import { advancePresentationSettled } from '../src/reviewAdvancePresentation'
import type {
  AttentionSearch,
  CaseDetailView,
  DecisionCommand,
  LibrarySearch,
  LibraryView,
  PreparationView,
  ProviderThrottleEntry,
  ProviderThrottlingView,
  ReviewHttp,
  ReviewerAccessView,
} from '../src/api/http'
import { createApplicationRouter, normalizeLibrarySearch } from '../src/router'
import {
  activity,
  consentOf,
  createHttp,
  createLiveStream,
  detail,
  library,
  recordedAcknowledgement,
  renderRoute,
  wholeText,
} from './fixtures'
import { view as settingsView } from './settingsFixtures'

// jsdom has no native dialog methods; real focus trapping/Escape are browser proof.
Object.defineProperties(HTMLDialogElement.prototype, {
  showModal: {
    configurable: true,
    value(this: HTMLDialogElement) {
      this.setAttribute('open', '')
    },
  },
  close: {
    configurable: true,
    value(this: HTMLDialogElement) {
      this.removeAttribute('open')
    },
  },
})

it('keeps the active search caret through debounced Library URL changes', async () => {
  const user = userEvent.setup()
  const { router } = renderRoute('/library?lens=all', createHttp())
  const search = await screen.findByRole('searchbox')
  await user.click(search)
  await user.keyboard('synthetic')
  await waitFor(() => expect(router.state.location.search.q).toBe('synthetic'))
  expect(search).toHaveFocus()
  await user.keyboard('{ArrowLeft}{Backspace}')
  await waitFor(() => expect(router.state.location.search.q).toBe('synthetc'))
  expect(search).toHaveFocus()
  expect(search).toHaveProperty('selectionStart', 7)
  expect(search).toHaveProperty('selectionEnd', 7)
  await user.clear(search)
  await user.type(search, 'replacement')
  await waitFor(() => expect(router.state.location.search.q).toBe('replacement'))
  await user.click(screen.getByRole('button', { name: 'Clear' }))
  expect(search).toHaveFocus()
  await waitFor(() => expect(router.state.location.search.q).toBeUndefined())
  expect(screen.getByText('1 work', { selector: '[role=status]' })).toHaveAttribute(
    'role',
    'status',
  )
  expect(screen.getByText('1 work', { selector: '[role=status]' })).toHaveAttribute(
    'aria-live',
    'polite',
  )
})

it('keeps resync debt with its exact collection while search and cursor replace old requests', async () => {
  const user = userEvent.setup()
  const reads: {
    resolve: (value: LibraryView) => void
    signal: AbortSignal | undefined
    search: LibrarySearch
  }[] = []
  const http = createHttp()
  let first = true
  http.getLibrary = vi.fn<ReviewHttp['getLibrary']>(async (search, signal) => {
    if (first) {
      first = false
      return library
    }
    return new Promise((resolve) => reads.push({ resolve, signal, search }))
  })
  const updates = createLiveStream()
  const { router, queryClient } = renderRoute('/library?lens=all&preview=case-1', http, updates)
  expect(await screen.findByRole('heading', { name: 'A Book' })).toBeVisible()
  await act(async () => {
    void queryClient.invalidateQueries({ queryKey: ['library'] })
  })
  await waitFor(() => expect(reads).toHaveLength(1))
  const resync = async () =>
    act(async () => {
      updates.announce({ sequence: 0, subject: null, topic: 'resync' })
      await new Promise((resolve) => setTimeout(resolve, coalesceMilliseconds + 20))
    })
  await resync()
  expect(reads).toHaveLength(1)
  expect(reads[0]?.signal?.aborted).toBe(false)
  const search = screen.getByRole('searchbox')
  await user.click(search)
  await user.keyboard('later')
  await waitFor(() => expect(reads).toHaveLength(2))
  // The unsearched read is the library summary, which the toolbar still needs.
  expect(reads[0]?.search.q).toBeUndefined()
  expect(reads[0]?.signal?.aborted).toBe(false)
  expect(reads[1]?.search.q).toBe('later')
  expect(search).toHaveFocus()
  await resync()
  expect(reads).toHaveLength(2)
  expect(reads[1]?.signal?.aborted).toBe(false)
  await act(async () =>
    router.navigate({
      to: '/library',
      search: { lens: 'all', preview: 'case-1', q: 'later', cursor: 'second' },
    }),
  )
  await waitFor(() => expect(reads).toHaveLength(3))
  expect(reads[1]?.signal?.aborted).toBe(true)
  await resync()
  const work = library.works[0]
  if (!work) throw new Error('public work fixture required')
  const result = { ...library, works: [{ ...work, title: 'Current result' }] }
  await act(async () => {
    reads[0]?.resolve({ ...library, works: [{ ...work, title: 'Obsolete result' }] })
    reads[1]?.resolve({ ...library, works: [{ ...work, title: 'Obsolete result' }] })
    reads[2]?.resolve(result)
  })
  expect(await screen.findByRole('heading', { name: 'Current result' })).toBeVisible()
  expect(screen.queryByText('Obsolete result')).toBeNull()
  // Each collection settles its own resync debt once: the summary and this page.
  await waitFor(() => expect(reads).toHaveLength(5))
  expect(reads.slice(3).map((read) => [read.search.q, read.search.cursor])).toEqual([
    [undefined, undefined],
    ['later', 'second'],
  ])
  await act(async () => {
    reads[3]?.resolve(library)
    reads[4]?.resolve(result)
  })
  expect(router.state.location.search).toMatchObject({
    q: 'later',
    cursor: 'second',
    preview: 'case-1',
  })
  expect(http.getLibrary).toHaveBeenCalledTimes(6)
})

it('retries unavailable Works while the Library shell remains usable', async () => {
  const user = userEvent.setup()
  const getLibrary = vi
    .fn()
    .mockRejectedValueOnce(new Error('temporary Library failure'))
    .mockResolvedValue(library)
  renderRoute('/library?lens=all', { ...createHttp(), getLibrary })
  expect(await screen.findByText('Works are unavailable.')).toBeVisible()
  const failedReads = getLibrary.mock.calls.length
  await user.click(screen.getByRole('button', { name: 'Try again' }))
  expect(await screen.findByRole('heading', { name: 'Library' })).toBeVisible()
  expect(getLibrary.mock.calls.length).toBeGreaterThan(failedReads)
  await waitFor(() => expect(screen.queryByText('Works are unavailable.')).toBeNull())
})

const terminalPreparation = (
  runId: string,
  updatedAt = 101,
): PreparationView & { presentation: NonNullable<PreparationView['presentation']> } => ({
  schema_version: 'review-preparation-v2',
  recovery_required: false,
  active: false,
  actionable: true,
  paused: false,
  libraries: [],
  connections: [],
  presentation: {
    run_id: runId,
    state: 'completed_with_failures',
    triggers: [
      {
        trigger: 'manual_scan',
        accepted_count: 1,
        first_accepted_at: 100,
        last_accepted_at: 100,
      },
    ],
    passage: {
      read: 1,
      searches_finished: 1,
      compared: 1,
      checked: 1,
      ready: 1,
      failed: 1,
    },
    active_books: [],
    active_overflow: 0,
    recent_completions: [{ name: 'Finished title', outcome: 'failed', completed_at: 101 }],
    throughput_per_minute: 1,
    started_at: 100,
    updated_at: updatedAt,
    completed_at: 101,
    total: 1,
    dismissible: true,
    now: { ready: 1, recorded: 0, not_matched: 0, failed: 1, in_progress: 0, scan_ids: [] },
  },
})

it('keeps Attention in the Library search scope with its own cursor', async () => {
  const user = userEvent.setup()
  const http = createHttp()
  const item = {
    id: 'case-1',
    destination: 'title',
    title: 'Scoped work',
    authors: [],
    library: 'Books',
    mediaKind: 'ebook',
    notices: [],
    provenance: [],
    workAttentionCount: 1,
  }
  http.getAttention = vi.fn<ReviewHttp['getAttention']>(async ({ q, cursor }) => ({
    total: q === 'none' ? 0 : 1,
    items:
      q === 'none' ? [] : [cursor ? { ...item, id: 'case-2', title: 'Later scoped work' } : item],
    ...(cursor ? {} : { nextCursor: 'attention-page-2' }),
  }))
  const { router } = renderRoute(
    '/library?lens=all&q=needle&cursor=library-page&attention=open',
    http,
  )
  expect(
    await screen.findByRole('button', { name: /^Title/u, description: 'Scoped work' }),
  ).toBeVisible()
  expect(http.getAttention).toHaveBeenCalledWith(
    { q: 'needle', cursor: undefined },
    expect.any(AbortSignal),
    'foreground',
  )
  await user.click(screen.getByRole('button', { name: 'Load more attention' }))
  expect(
    await screen.findByRole('button', { name: /^Title/u, description: 'Later scoped work' }),
  ).toBeVisible()
  expect(http.getAttention).toHaveBeenCalledWith(
    { q: 'needle', cursor: 'attention-page-2' },
    expect.any(AbortSignal),
    'foreground',
  )
  await user.click(screen.getByRole('button', { name: 'Close' }))
  await user.clear(screen.getByRole('searchbox'))
  await user.type(screen.getByRole('searchbox'), 'none')
  await waitFor(() => expect(router.state.location.search.q).toBe('none'))
  expect(router.state.location.search.cursor).toBeUndefined()
  expect(router.state.location.search.attentionCursor).toBeUndefined()
  await user.click(screen.getByRole('button', { name: /^Attention /u }))
  await waitFor(() =>
    expect(http.getAttention).toHaveBeenCalledWith(
      { q: 'none', cursor: undefined },
      expect.any(AbortSignal),
      'foreground',
    ),
  )
  expect(screen.queryByRole('button', { name: /^Title/u, description: 'Scoped work' })).toBeNull()
  expect(
    screen.queryByRole('button', { name: /^Title/u, description: 'Later scoped work' }),
  ).toBeNull()
})

describe('Library route', () => {
  it('retains a setup handoff until the first authoritative preparation snapshot selects it', async () => {
    const runId = `preparation_run_${'9'.repeat(64)}`
    const confirmed = terminalPreparation(runId)
    let release!: (value: PreparationView) => void
    const http = createHttp()
    http.getPreparation = vi.fn<ReviewHttp['getPreparation']>(
      () =>
        new Promise<PreparationView>((resolve) => {
          release = resolve
        }),
    )
    const { router } = renderRoute(`/library?lens=all&preparation=${runId}`, http)

    await waitFor(() => expect(release).toBeTypeOf('function'))
    expect(router.state.location.search).toMatchObject({ preparation: runId })
    expect(
      screen.queryByRole('heading', { name: 'Preparation completed with failures' }),
    ).toBeNull()

    await act(async () => release(confirmed))
    expect(
      await screen.findByText(wholeText('Finished · 1 ready to browse · 1 could not finish'), {
        selector: '.preparation-run-state',
      }),
    ).toBeVisible()
    expect(router.state.location.search).toMatchObject({ preparation: runId })
    await waitFor(() =>
      expect(
        screen.getByRole('heading', { name: 'Preparation completed with failures' }),
      ).toHaveFocus(),
    )
    expect(http.getPreparation).toHaveBeenCalledTimes(1)
  })

  it('retains a failed cold handoff and normalizes only after authoritative absence', async () => {
    const runId = `preparation_run_${'8'.repeat(64)}`
    const http = createHttp()
    http.getPreparation = vi
      .fn<ReviewHttp['getPreparation']>()
      .mockRejectedValueOnce(new ApiError(503, 'unavailable'))
      .mockResolvedValueOnce({
        ...terminalPreparation(runId),
        presentation: null,
      })
    const { queryClient, router } = renderRoute(`/library?lens=all&preparation=${runId}`, http)

    await waitFor(() => expect(http.getPreparation).toHaveBeenCalledTimes(1))
    expect(router.state.location.search).toMatchObject({ preparation: runId })

    await act(async () => {
      await queryClient.refetchQueries({ queryKey: ['review-preparation'] })
    })
    await waitFor(() => expect(router.state.location.search).toEqual({ lens: 'all' }))
    expect(http.getPreparation).toHaveBeenCalledTimes(2)
  })

  it('hands one preparation run from the Works summary into shared detail and restores work selection', async () => {
    const user = userEvent.setup()
    const http = createHttp()
    const updates = createLiveStream()
    const confirmed: PreparationView = {
      schema_version: 'review-preparation-v2' as const,
      recovery_required: false,
      active: true,
      actionable: false,
      paused: false,
      libraries: [],
      connections: [],
      presentation: {
        run_id: `preparation_run_${'a'.repeat(64)}`,
        state: 'running' as const,
        triggers: [
          {
            trigger: 'initial_setup' as const,
            accepted_count: 1,
            first_accepted_at: 100,
            last_accepted_at: 100,
          },
        ],
        passage: { read: 4, searches_finished: 3, compared: 2, checked: 2, ready: 1, failed: 1 },
        active_books: [{ name: 'Private title', stage: 'Comparing candidates' }],
        active_overflow: 2,
        recent_completions: [
          { name: 'Finished title', outcome: 'failed' as const, completed_at: 101 },
        ],
        throughput_per_minute: 1.2,
        started_at: 100,
        updated_at: 101,
        completed_at: null,
        total: 4,
        dismissible: false,
      },
    }
    const confirmedPresentation = confirmed.presentation
    if (!confirmedPresentation) throw new Error('preparation fixture requires a presentation')
    http.getPreparation = vi.fn(async () => confirmed)
    http.getAttention = vi.fn(async () => ({
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
    }))
    const { queryClient, router } = renderRoute('/library?lens=all', http, updates)

    const summary = await screen.findByRole('button', { name: /Preparing your library/u })
    expect(summary).toBeVisible()
    expect(screen.getByRole('heading', { name: 'Works' })).toBeVisible()
    await user.click(summary)
    expect(await screen.findByRole('heading', { name: 'Now preparing' })).toBeVisible()
    const passage = screen.getAllByRole('progressbar')
    expect(passage).toHaveLength(4)
    expect(
      passage.map((item) => [item.getAttribute('aria-label'), item.getAttribute('value')]),
    ).toEqual([
      ['Read from library: 4 of 4', '4'],
      ['Searches finished: 3 of 4', '3'],
      ['Candidates compared: 2 of 4', '2'],
      ['Metadata checked: 2 of 4', '2'],
    ])
    for (const item of passage) {
      expect(item).toHaveAttribute('max', '4')
      expect(item).not.toHaveAttribute('style')
    }
    expect(document.querySelector('.preparation-run-state')).not.toHaveClass('reported-change')
    expect(screen.getByText('Private title')).toBeVisible()
    expect(screen.getByText('1.2 books/min over the last five minutes')).toBeVisible()
    expect(screen.getByText('Finished title')).toBeVisible()
    let releaseOlderFetch!: (value: Awaited<ReturnType<typeof http.getPreparation>>) => void
    vi.mocked(http.getPreparation).mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          releaseOlderFetch = resolve
        }),
    )
    act(() => {
      void queryClient.refetchQueries({ queryKey: ['review-preparation'] })
    })
    await waitFor(() => expect(releaseOlderFetch).toBeTypeOf('function'))
    act(() => updates.setState('degraded'))
    expect(
      await screen.findByText(/Reconnecting · showing the last confirmed state/u),
    ).toBeVisible()
    expect(screen.getByText('Private title')).toBeVisible()
    const changedDuringOutage = {
      ...confirmed,
      presentation: {
        ...confirmedPresentation,
        active_books: [{ name: 'Unconfirmed title', stage: 'Checking metadata' }],
        updated_at: 102,
      },
    }
    act(() => {
      releaseOlderFetch(changedDuringOutage)
    })
    expect(queryClient.getQueryData(['review-preparation'])).toBe(confirmed)
    expect(screen.getByText('Private title')).toBeVisible()
    expect(screen.queryByText('Unconfirmed title')).toBeNull()
    await act(async () => new Promise((resolve) => window.setTimeout(resolve, 1_600)))
    expect(http.getPreparation).toHaveBeenCalledTimes(2)
    let releaseRefresh!: (value: Awaited<ReturnType<typeof http.getPreparation>>) => void
    vi.mocked(http.getPreparation).mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          releaseRefresh = resolve
        }),
    )
    act(() => updates.setState('live'))
    expect(screen.getByText(/Reconnecting · showing the last confirmed state/u)).toBeVisible()
    await waitFor(() => expect(releaseRefresh).toBeTypeOf('function'))
    expect(screen.getByText(/Reconnecting · showing the last confirmed state/u)).toBeVisible()
    act(() => updates.setState('degraded'))
    await act(async () => releaseRefresh(changedDuringOutage))
    expect(queryClient.getQueryData(['review-preparation'])).toBe(confirmed)
    expect(screen.getByText('Private title')).toBeVisible()
    expect(screen.queryByText('Unconfirmed title')).toBeNull()
    act(() => updates.setState('live'))
    await waitFor(() =>
      expect(screen.queryByText(/Reconnecting · showing the last confirmed state/u)).toBeNull(),
    )
    expect(screen.getByText('Private title')).toBeVisible()
    expect(document.querySelector('.preparation-run-state')).not.toHaveClass('reported-change')
    expect(http.getPreparation).toHaveBeenCalledTimes(4)

    await user.click(await screen.findByRole('button', { name: /^A Book\./u }))
    expect(await screen.findByRole('heading', { name: 'A Book' })).toBeVisible()
    expect(screen.queryByRole('heading', { name: 'Now preparing' })).toBeNull()
    await user.click(screen.getByRole('button', { name: /Preparing your library/u }))
    expect(await screen.findByRole('heading', { name: 'Now preparing' })).toBeVisible()
    expect(document.querySelector('.preparation-run-state')).not.toHaveClass('reported-change')

    act(() => {
      queryClient.setQueryData(['review-preparation'], {
        ...confirmed,
        presentation: { ...confirmedPresentation, updated_at: 102 },
      })
    })
    expect(document.querySelector('.preparation-run-state')).not.toHaveClass('reported-change')
    act(() => {
      queryClient.setQueryData(['review-preparation'], {
        ...confirmed,
        presentation: {
          ...confirmedPresentation,
          throughput_per_minute: 9.9,
          updated_at: 103,
        },
      })
    })
    await waitFor(() =>
      expect(screen.getByText('9.9 books/min over the last five minutes')).toBeVisible(),
    )
    expect(document.querySelector('.preparation-run-state')).not.toHaveClass('reported-change')
    const semanticChange = {
      ...confirmed,
      presentation: {
        ...confirmedPresentation,
        passage: { ...confirmedPresentation.passage, ready: 2 },
        updated_at: 104,
      },
    }
    act(() => {
      queryClient.setQueryData(['review-preparation'], semanticChange)
    })
    await waitFor(() =>
      expect(document.querySelector('.preparation-run-state')).toHaveClass('reported-change'),
    )
    await waitFor(
      () =>
        expect(document.querySelector('.preparation-run-state')).not.toHaveClass('reported-change'),
      { timeout: 1_000 },
    )
    act(() => {
      queryClient.setQueryData(['review-preparation'], semanticChange)
    })
    expect(document.querySelector('.preparation-run-state')).not.toHaveClass('reported-change')

    await user.click(screen.getByRole('button', { name: 'Attention 1 work' }))
    const attentionRail = await screen.findByLabelText('Attention queue')
    await user.click(within(attentionRail).getByRole('button', { description: 'A Book' }))
    const selectedHeading = await screen.findByRole('heading', { name: 'A Book' })
    await waitFor(() => expect(selectedHeading.closest('[data-route-focus]')).toHaveFocus())
    expect(screen.queryByRole('heading', { name: 'Now preparing' })).toBeNull()
    expect(screen.getByRole('button', { name: /Choose current value/u })).toBeEnabled()
    expect(router.state.location.search).toEqual({
      lens: 'all',
      preview: 'case-1',
      field: 'title',
    })
  })

  it('summarizes preparation states truthfully while Works remains usable', async () => {
    const runId = `preparation_run_${'7'.repeat(64)}`
    const running = terminalPreparation(runId)
    const presentation = running.presentation
    const runningPresentation = {
      ...presentation,
      state: 'running' as const,
      passage: { ...presentation.passage, ready: 1, failed: 0 },
      active_books: [{ name: 'Active title', stage: 'Checking metadata' }],
      completed_at: null,
      dismissible: false,
      now: { ready: 1, recorded: 0, not_matched: 0, failed: 0, in_progress: 0, scan_ids: [] },
    }
    const current: PreparationView = {
      ...running,
      active: true,
      actionable: false,
      presentation: runningPresentation,
    }
    const http = createHttp()
    http.getPreparation = vi.fn(async () => current)
    const { queryClient } = renderRoute('/library?lens=all', http)

    let summary = await screen.findByRole('button', { name: /Preparing your library/u })
    expect(
      within(summary).getByText(wholeText('This run: 1 ready to browse · 1 active')),
    ).toBeVisible()
    expect(screen.getByRole('button', { name: /^A Book\./u })).toBeEnabled()

    act(() => {
      queryClient.setQueryData(['review-preparation'], {
        ...current,
        presentation: {
          ...runningPresentation,
          state: 'starting',
          passage: { read: 0, searches_finished: 0, compared: 0, checked: 0, ready: 0, failed: 0 },
          active_books: [],
          updated_at: 101,
          now: { ready: 0, recorded: 0, not_matched: 0, failed: 0, in_progress: 0, scan_ids: [] },
        },
      })
    })
    summary = await screen.findByRole('button', { name: /Preparation is starting/u })
    expect(
      within(summary).getByText(wholeText('This run: 0 ready to browse · 0 active')),
    ).toBeVisible()
    expect(screen.getByRole('button', { name: /^A Book\./u })).toBeEnabled()

    act(() => {
      queryClient.setQueryData(['review-preparation'], {
        ...current,
        paused: true,
        presentation: { ...runningPresentation, state: 'blocked', updated_at: 102 },
      })
    })
    summary = await screen.findByRole('button', { name: /Preparation needs attention/u })
    expect(
      within(summary).getByText(wholeText('This run: 1 ready to browse · 1 active')),
    ).toBeVisible()
    expect(screen.getByRole('button', { name: /^A Book\./u })).toBeEnabled()

    act(() => {
      queryClient.setQueryData(['review-preparation'], {
        ...current,
        active: false,
        actionable: true,
        presentation: {
          ...runningPresentation,
          state: 'completed_clean',
          active_books: [],
          updated_at: 103,
          completed_at: 103,
          dismissible: true,
        },
      })
    })
    summary = await screen.findByRole('button', { name: /Preparation complete/u })
    expect(within(summary).getByText(wholeText('This run: 1 ready to browse'))).toBeVisible()
    expect(screen.getByRole('button', { name: /^A Book\./u })).toBeEnabled()

    act(() => {
      queryClient.setQueryData(['review-preparation'], terminalPreparation(runId, 104))
    })
    summary = await screen.findByRole('button', { name: /Preparation completed with failures/u })
    expect(
      within(summary).getByText(wholeText('This run: 1 ready to browse · 1 could not finish')),
    ).toBeVisible()
    expect(screen.getByRole('button', { name: /^A Book\./u })).toBeEnabled()
  })

  it.each([
    [
      'starting',
      'Preparation is starting',
      'The run is open and waiting for preparation work to begin or resume.',
      'Preparation is starting',
    ],
    [
      'running',
      'Preparing your library',
      'Preparation continues here while prepared works remain selectable.',
      'Preparation continues',
    ],
    [
      'blocked',
      'Preparation needs attention',
      'Preparation is paused until the reported issue is resolved.',
      'Paused until the reported connection issue is resolved',
    ],
    [
      'completed_clean',
      'Preparation complete',
      'This run finished without a reported failure.',
      'Finished · 1 ready to browse',
    ],
    [
      'completed_with_failures',
      'Preparation completed with failures',
      'This run finished with reported failures; prepared books remain available and the failed outcomes stay visible.',
      'Finished · 1 ready to browse · 1 could not finish',
    ],
  ] as const)(
    'describes the %s preparation detail truthfully',
    async (state, heading, introduction, runState) => {
      const runId = `preparation_run_${'8'.repeat(64)}`
      const base = terminalPreparation(runId)
      const terminal = state === 'completed_clean' || state === 'completed_with_failures'
      const presentation = {
        ...base.presentation,
        state,
        passage: {
          ...base.presentation.passage,
          failed: state === 'completed_with_failures' ? 1 : 0,
        },
        now: {
          ready: 1,
          recorded: 0,
          not_matched: 0,
          failed: state === 'completed_with_failures' ? 1 : 0,
          in_progress: 0,
          scan_ids: [],
        },
        active_books: terminal ? [] : base.presentation.active_books,
        completed_at: terminal ? 104 : null,
        dismissible: terminal,
      }
      const current: PreparationView = {
        ...base,
        active: !terminal,
        actionable: terminal,
        paused: state === 'blocked',
        presentation,
      }
      const http = createHttp()
      http.getPreparation = vi.fn(async () => current)
      renderRoute(`/library?lens=all&preparation=${runId}`, http)

      const detailHeading = await screen.findByRole('heading', { name: heading })
      expect(screen.getByText(introduction)).toBeVisible()
      expect(
        screen.getByText(wholeText(runState), { selector: '.preparation-run-state' }),
      ).toBeVisible()
      await waitFor(() => expect(detailHeading).toHaveFocus())
    },
  )

  it('routes every completed book into Works and states what a failed one becomes', async () => {
    const runId = `preparation_run_${'c'.repeat(64)}`
    const base = terminalPreparation(runId)
    const runningPresentation = {
      ...base.presentation,
      state: 'running' as const,
      passage: { read: 2, searches_finished: 2, compared: 1, checked: 1, ready: 1, failed: 0 },
      active_books: [{ name: 'Active title', stage: 'Comparing candidates' }],
      recent_completions: [{ name: 'Ready title', outcome: 'ready' as const, completed_at: 101 }],
      total: 3,
      completed_at: null,
      dismissible: false,
      now: { ready: 1, recorded: 0, not_matched: 0, failed: 0, in_progress: 2, scan_ids: [] },
    }
    const running: PreparationView = {
      ...base,
      active: true,
      actionable: false,
      presentation: runningPresentation,
    }
    const http = createHttp()
    http.getPreparation = vi.fn(async () => running)
    const { queryClient } = renderRoute('/library?lens=all', http)

    fireEvent.click(await screen.findByRole('button', { name: /Preparing your library/u }))
    await screen.findByRole('heading', { name: 'Preparing your library' })
    const readyLink = screen.getByRole('link', { name: 'Find in Works' })
    expect(readyLink).toHaveAttribute('href', '/library?lens=all&q=Ready+title')
    expect(screen.getByText('A book appears in Works once it is prepared.')).toBeVisible()
    expect(document.querySelectorAll('.preparation-active-books a')).toHaveLength(0)
    expect(screen.queryByText(/Preparation unavailable/u)).toBeNull()
    // The reconciliation reads once, in the outcomes row; the header keeps the
    // short count it always carried rather than repeating it.
    expect(screen.getByText(wholeText('1 ready to browse · 2 in progress'))).toBeVisible()
    // Each number and its first word are one inline unit with an ordinary
    // space, so they move to the next line together and break only when wider
    // than a line; no line starts with a separator (#1013, #1366).
    const scope = document.querySelector('.preparation-outcomes strong')
    expect(
      [...(scope?.querySelectorAll(':scope > .count-pair') ?? [])].map((node) => node.textContent),
    ).toEqual(['1 ready', '2 in'])
    expect(scope?.textContent).toBe('1 ready to browse\u00a0· 2 in progress')
    expect(document.querySelector('.preparation-detail > header > strong')?.textContent).toBe(
      '1 ready to browse in this run',
    )
    expect(screen.getByText('Started by an owner scan')).toBeVisible()
    act(() => {
      queryClient.setQueryData(['review-preparation'], {
        ...running,
        presentation: {
          ...runningPresentation,
          triggers: [
            ...runningPresentation.triggers,
            {
              trigger: 'title_recovery',
              accepted_count: 1,
              first_accepted_at: 101,
              last_accepted_at: 101,
            },
          ],
          passage: { ...runningPresentation.passage, failed: 1 },
          now: { ...runningPresentation.now, failed: 1, in_progress: 1 },
          recent_completions: [
            { name: 'Unrelated unfinished title', outcome: 'failed', completed_at: 101 },
          ],
        },
      })
    })
    expect(
      await screen.findByText('Started by an owner scan · Title recovery requested'),
    ).toBeVisible()
    expect(screen.getByText('Active title')).toBeVisible()
    expect(screen.getByText('Unrelated unfinished title')).toBeVisible()
    expect(
      screen.getByText(wholeText('1 ready to browse · 1 could not finish · 1 in progress')),
    ).toBeVisible()

    act(() => {
      queryClient.setQueryData(['review-preparation'], {
        ...running,
        active: false,
        actionable: true,
        presentation: {
          ...runningPresentation,
          state: 'completed_with_failures',
          passage: { ...runningPresentation.passage, checked: 2, failed: 1 },
          now: { ...runningPresentation.now, failed: 1, in_progress: 1 },
          active_books: [],
          recent_completions: [
            { name: 'Ready title', outcome: 'ready', completed_at: 101 },
            { name: 'Unfinished title', outcome: 'failed', completed_at: 102 },
          ],
          updated_at: 105,
          completed_at: 105,
          dismissible: true,
        },
      })
    })

    const failureSentence = await screen.findByText(
      'A book that could not finish is listed in Works under its library title, with its stage, reason, next step and reference. If its library record has since changed, it is listed as Preparation unavailable.',
    )
    expect(failureSentence).toBeVisible()
    const links = screen.getAllByRole('link', { name: 'Find in Works' })
    expect(links.map((link) => link.getAttribute('href'))).toEqual([
      '/library?lens=all&q=Ready+title',
      '/library?lens=all&q=Unfinished+title',
    ])
    expect(
      screen.getByText(wholeText('1 ready to browse · 1 could not finish · 1 in progress')),
    ).toBeVisible()

    act(() => {
      queryClient.setQueryData(['review-preparation'], {
        ...running,
        presentation: {
          ...runningPresentation,
          run_id: `preparation_run_${'d'.repeat(64)}`,
          state: 'running',
          triggers: [
            {
              trigger: 'manual_scan' as const,
              accepted_count: 3,
              first_accepted_at: 200,
              last_accepted_at: 220,
            },
          ],
          passage: { read: 1, searches_finished: 0, compared: 0, checked: 0, ready: 0, failed: 0 },
          recent_completions: [],
          updated_at: 221,
        },
      })
    })

    fireEvent.click(await screen.findByRole('button', { name: /Preparing your library/u }))
    expect(await screen.findByText('Owner scan requested 3 times')).toBeVisible()
    expect(screen.queryByText(/Preparation unavailable/u)).toBeNull()
    expect(screen.queryByRole('link', { name: 'Find in Works' })).toBeNull()
  })

  it('states every passage numerator and only a current throughput', async () => {
    const runId = `preparation_run_${'e'.repeat(64)}`
    const base = terminalPreparation(runId)
    const runningPresentation = {
      ...base.presentation,
      state: 'running' as const,
      passage: { read: 3, searches_finished: 2, compared: 1, checked: 1, ready: 1, failed: 0 },
      active_books: [{ name: 'Active title', stage: 'Comparing candidates' }],
      recent_completions: [],
      throughput_per_minute: 0,
      total: 4,
      completed_at: null,
      dismissible: false,
    }
    const running: PreparationView = {
      ...base,
      active: true,
      actionable: false,
      presentation: runningPresentation,
    }
    const http = createHttp()
    http.getPreparation = vi.fn(async () => running)
    const { queryClient } = renderRoute(`/library?lens=all&preparation=${runId}`, http)

    await screen.findByRole('heading', { name: 'Preparing your library' })
    expect(
      screen.getByText(
        'Each stage counts the books that first reached it in this run, and a book that could not finish stops at its last stage; In this run counts them as they stand now.',
      ),
    ).toBeVisible()
    expect(
      screen
        .getAllByRole('progressbar')
        .map((bar) => bar.parentElement?.querySelector('strong')?.textContent.trim()),
    ).toEqual(['3 of 4', '2 of 4', '1 of 4', '1 of 4'])
    expect(screen.getByText('No book finished in the last five minutes')).toBeVisible()

    act(() => {
      queryClient.setQueryData(['review-preparation'], {
        ...running,
        presentation: { ...runningPresentation, throughput_per_minute: 2.5, updated_at: 106 },
      })
    })
    expect(await screen.findByText('2.5 books/min over the last five minutes')).toBeVisible()

    act(() => {
      queryClient.setQueryData(['review-preparation'], {
        ...running,
        active: false,
        actionable: true,
        presentation: {
          ...runningPresentation,
          state: 'completed_with_failures',
          passage: { ...runningPresentation.passage, failed: 1 },
          active_books: [],
          throughput_per_minute: 7,
          updated_at: 107,
          completed_at: 107,
          dismissible: true,
        },
      })
    })
    await screen.findByText(wholeText('Finished · 1 ready to browse · 1 could not finish'), {
      selector: '.preparation-run-state',
    })
    expect(screen.queryByText(/books\/min/u)).toBeNull()
    expect(screen.queryByText('No book finished in the last five minutes')).toBeNull()
  })

  // The route bounds `q` by UTF-16 units and the backend by characters, so a
  // name is cut to at most 128 units -- never through an astral pair, and never
  // to a length the route would drop.
  it.each([
    // 127 ASCII plus one astral character is 128 characters but 129 units:
    // bounding by characters makes the route drop `q` entirely.
    ['an astral character past the unit bound', `${'a'.repeat(127)}\u{1f4d8}tail`, 'a'.repeat(127)],
    // 64 astral characters are exactly 128 units, so all of them survive.
    ['a name that is entirely astral characters', '\u{1f4d8}'.repeat(80), '\u{1f4d8}'.repeat(64)],
    [
      'a multibyte name inside both bounds',
      '\u63a8\u7406\u5c0f\u8aac'.repeat(20),
      '\u63a8\u7406\u5c0f\u8aac'.repeat(20),
    ],
  ])('bounds a completion search with %s', async (_case, name, expected) => {
    const runId = `preparation_run_${'e'.repeat(64)}`
    const http = createHttp()
    http.getPreparation = vi.fn(async () => {
      const view = terminalPreparation(runId)
      return {
        ...view,
        presentation: {
          ...view.presentation,
          recent_completions: [{ name, outcome: 'ready' as const, completed_at: 120 }],
        },
      }
    })
    renderRoute(`/library?lens=all&preparation=${runId}`, http)

    const href = (await screen.findByRole('link', { name: 'Find in Works' })).getAttribute('href')
    const query = new URLSearchParams(href?.split('?')[1] ?? '').get('q')
    expect(query).toBe(expected)
    expect(query?.length).toBeLessThanOrEqual(128)
    expect(query).not.toContain('\ufffd')
    expect(Array.from(query ?? '').join('')).toBe(query)
  })

  it('gives preparation exclusive left selection and restores the retained work', async () => {
    const user = userEvent.setup()
    const runId = `preparation_run_${'9'.repeat(64)}`
    const http = createHttp()
    http.getPreparation = vi.fn(async () => terminalPreparation(runId))
    renderRoute('/library?lens=all&preview=case-1&field=title', http)

    const work = await screen.findByRole('button', { name: /^A Book\./u })
    expect(work).toHaveAttribute('aria-current', 'true')
    const summary = await screen.findByRole('button', {
      name: /Preparation completed with failures/u,
    })
    await user.click(summary)
    expect(summary).toHaveAttribute('aria-current', 'true')
    expect(work).not.toHaveAttribute('aria-current')
    expect(work).not.toHaveClass('active')
    expect(
      document.querySelectorAll(
        '.preparation-summary[aria-current="true"],.library-work-row[aria-current="true"]',
      ),
    ).toHaveLength(1)

    await user.click(work)
    expect(await screen.findByRole('heading', { name: 'A Book' })).toBeVisible()
    expect(work).toHaveAttribute('aria-current', 'true')
    expect(summary).not.toHaveAttribute('aria-current')
    expect(
      screen.queryByRole('heading', { name: 'Preparation completed with failures' }),
    ).toBeNull()
  })

  it('keeps preparation selected while changing the Library lens', async () => {
    const user = userEvent.setup()
    const runId = `preparation_run_${'9'.repeat(64)}`
    const http = createHttp()
    http.getPreparation = vi.fn(async () => terminalPreparation(runId))
    renderRoute(`/library?lens=all&preview=case-1&field=title&preparation=${runId}`, http)

    const summary = await screen.findByRole('button', {
      name: /Preparation completed with failures/u,
    })
    const work = screen.getByRole('button', { name: /^A Book\./u })
    expect(summary).toHaveAttribute('aria-current', 'true')
    await user.click(screen.getByRole('link', { name: 'Needs attention' }))

    expect(summary).toHaveAttribute('aria-current', 'true')
    expect(work).not.toHaveAttribute('aria-current')
    expect(
      await screen.findByRole('heading', { name: 'Preparation completed with failures' }),
    ).toBeVisible()
    await user.click(await screen.findByRole('button', { name: /^A Book\./u }))
    expect(await screen.findByRole('heading', { name: 'A Book' })).toBeVisible()
  })

  it.each(['pointer', 'Enter', 'Space'])(
    'adopts a reconnecting dismissal by %s and restores the selected work',
    async (interaction) => {
      const user = userEvent.setup()
      const http = createHttp()
      const updates = createLiveStream()
      const runId = `preparation_run_${'d'.repeat(64)}`
      const confirmed = terminalPreparation(runId)
      const dismissed: PreparationView = {
        ...confirmed,
        presentation: null,
      }
      http.getPreparation = vi
        .fn<ReviewHttp['getPreparation']>()
        .mockResolvedValueOnce(confirmed)
        .mockResolvedValue(dismissed)
      http.dismissPreparation = vi.fn(async () => dismissed)
      const { queryClient, router } = renderRoute(
        '/library?lens=all&preview=case-1&field=title',
        http,
        updates,
      )

      expect(await screen.findByRole('heading', { name: 'A Book' })).toBeVisible()
      await waitFor(() =>
        expect(
          screen.getByRole('heading', { name: 'A Book' }).closest('[data-route-focus]'),
        ).toHaveFocus(),
      )
      const queued = new Map<number, FrameRequestCallback>()
      let nextFrame = 0
      vi.spyOn(window, 'requestAnimationFrame').mockImplementation((callback) => {
        queued.set(++nextFrame, callback)
        return nextFrame
      })
      vi.spyOn(window, 'cancelAnimationFrame').mockImplementation((id) => {
        queued.delete(id)
      })
      await user.click(screen.getByRole('button', { name: /Preparation completed with failures/u }))
      expect(
        await screen.findByText(wholeText('Finished · 1 ready to browse · 1 could not finish'), {
          selector: '.preparation-run-state',
        }),
      ).toBeVisible()
      act(() => updates.setState('degraded'))
      expect(
        await screen.findByText(/Reconnecting · showing the last confirmed state/u),
      ).toBeVisible()
      const invalidateQueries = vi.spyOn(queryClient, 'invalidateQueries')

      const dismissButton = screen.getByRole('button', { name: 'Dismiss preparation' })
      if (interaction === 'pointer') await user.click(dismissButton)
      else {
        dismissButton.focus()
        expect(dismissButton).toHaveFocus()
        fireEvent.keyDown(dismissButton, { key: interaction === 'Enter' ? 'Enter' : ' ' })
        fireEvent.click(dismissButton, { detail: 0 })
        fireEvent.keyUp(dismissButton, { key: interaction === 'Enter' ? 'Enter' : ' ' })
      }

      await waitFor(() => expect(http.dismissPreparation).toHaveBeenCalledTimes(1))
      expect(queryClient.getQueryData(['review-preparation'])).toBe(dismissed)
      await waitFor(() =>
        expect(router.state.location.search).toEqual({
          lens: 'all',
          preview: 'case-1',
          field: 'title',
        }),
      )
      const workHeading = await screen.findByRole('heading', { name: 'A Book' })
      await act(async () => {
        for (const callback of queued.values()) callback(0)
        queued.clear()
      })
      if (interaction === 'pointer')
        await waitFor(() => expect(workHeading.closest('[data-route-focus]')).toHaveFocus())
      expect(screen.getByRole('button', { name: /Choose current value/u })).toBeEnabled()
      expect(invalidateQueries).toHaveBeenCalledWith({
        queryKey: ['review-preparation'],
        exact: true,
        refetchType: 'none',
      })
      expect(document.querySelector('.reported-change')).toBeNull()
    },
  )

  it('adopts a deferred dismissal without overriding later route ownership', async () => {
    const user = userEvent.setup()
    const http = createHttp()
    const runId = `preparation_run_${'d'.repeat(64)}`
    const confirmed = terminalPreparation(runId)
    const dismissed: PreparationView = {
      ...confirmed,
      presentation: null,
    }
    let releaseDismissal!: (value: PreparationView) => void
    http.getPreparation = vi
      .fn<ReviewHttp['getPreparation']>()
      .mockResolvedValueOnce(confirmed)
      .mockResolvedValue(dismissed)
    http.dismissPreparation = vi.fn<ReviewHttp['dismissPreparation']>(
      () =>
        new Promise<PreparationView>((resolve) => {
          releaseDismissal = resolve
        }),
    )
    const { queryClient, router } = renderRoute(
      '/library?lens=all&preview=case-1&field=title',
      http,
    )
    await user.click(
      await screen.findByRole('button', { name: /Preparation completed with failures/u }),
    )
    expect(
      await screen.findByText(wholeText('Finished · 1 ready to browse · 1 could not finish'), {
        selector: '.preparation-run-state',
      }),
    ).toBeVisible()
    await user.click(screen.getByRole('button', { name: 'Dismiss preparation' }))
    await waitFor(() => expect(releaseDismissal).toBeTypeOf('function'))

    await router.navigate({
      to: '/activity',
      search: { eventType: 'all', rangeDays: 30, page: 1, pageSize: 25 },
    })
    expect(await screen.findByRole('heading', { name: 'Activity and recovery' })).toBeVisible()
    await act(async () => releaseDismissal(dismissed))
    await waitFor(() => expect(queryClient.getQueryData(['review-preparation'])).toBe(dismissed))
    expect(router.state.location.pathname).toBe('/activity')
    expect(http.dismissPreparation).toHaveBeenCalledTimes(1)
  })

  it('does not clean up a newer same-route search after deferred dismissal', async () => {
    const user = userEvent.setup()
    const http = createHttp()
    const runId = `preparation_run_${'d'.repeat(64)}`
    const confirmed = terminalPreparation(runId)
    const dismissed: PreparationView = {
      ...confirmed,
      presentation: null,
    }
    let releaseDismissal!: (value: PreparationView) => void
    http.getPreparation = vi
      .fn<ReviewHttp['getPreparation']>()
      .mockResolvedValueOnce(confirmed)
      .mockResolvedValue(dismissed)
    http.dismissPreparation = vi.fn<ReviewHttp['dismissPreparation']>(
      () =>
        new Promise<PreparationView>((resolve) => {
          releaseDismissal = resolve
        }),
    )
    const { queryClient, router } = renderRoute(
      '/library?lens=all&preview=case-1&field=title',
      http,
    )
    await user.click(
      await screen.findByRole('button', { name: /Preparation completed with failures/u }),
    )
    expect(
      await screen.findByText(wholeText('Finished · 1 ready to browse · 1 could not finish'), {
        selector: '.preparation-run-state',
      }),
    ).toBeVisible()
    await user.click(screen.getByRole('button', { name: 'Dismiss preparation' }))
    await waitFor(() => expect(releaseDismissal).toBeTypeOf('function'))

    const laterSearch = {
      lens: 'needs_attention' as const,
      preview: 'case-1',
      field: 'authors',
      q: 'later choice',
    }
    await router.navigate({ to: '/library', search: laterSearch })
    await act(async () => releaseDismissal(dismissed))
    await waitFor(() => expect(queryClient.getQueryData(['review-preparation'])).toBe(dismissed))
    expect(router.state.location.search).toEqual(laterSearch)
    expect(http.dismissPreparation).toHaveBeenCalledTimes(1)
  })

  it('does not let dismissal normalization cancel a newer held Library navigation', async () => {
    const user = userEvent.setup()
    const http = createHttp()
    const runId = `preparation_run_${'d'.repeat(64)}`
    const confirmed = terminalPreparation(runId)
    const dismissed: PreparationView = {
      ...confirmed,
      presentation: null,
    }
    let releaseDismissal!: (value: PreparationView) => void
    let releaseLibrary!: (value: LibraryView) => void
    http.getPreparation = vi
      .fn<ReviewHttp['getPreparation']>()
      .mockResolvedValueOnce(confirmed)
      .mockResolvedValue(dismissed)
    http.dismissPreparation = vi.fn<ReviewHttp['dismissPreparation']>(
      () =>
        new Promise<PreparationView>((resolve) => {
          releaseDismissal = resolve
        }),
    )
    http.getLibrary = vi.fn<ReviewHttp['getLibrary']>((search) =>
      search.q === 'held destination'
        ? new Promise((resolve) => {
            releaseLibrary = resolve
          })
        : Promise.resolve(library),
    )
    const { queryClient, router } = renderRoute(
      '/library?lens=all&preview=case-1&field=title',
      http,
    )
    await user.click(
      await screen.findByRole('button', { name: /Preparation completed with failures/u }),
    )
    await user.click(screen.getByRole('button', { name: 'Dismiss preparation' }))
    await waitFor(() => expect(releaseDismissal).toBeTypeOf('function'))

    const laterSearch = {
      lens: 'needs_attention' as const,
      preview: 'case-1',
      field: 'authors',
      q: 'held destination',
    }
    const navigation = router.navigate({ to: '/library', search: laterSearch })
    await waitFor(() => expect(releaseLibrary).toBeTypeOf('function'))
    await act(async () => releaseDismissal(dismissed))
    await waitFor(() => expect(queryClient.getQueryData(['review-preparation'])).toBe(dismissed))

    await act(async () => releaseLibrary(library))
    await navigation
    expect(router.state.location.search).toEqual(laterSearch)
    expect(http.dismissPreparation).toHaveBeenCalledTimes(1)
    expect(
      vi.mocked(http.getLibrary).mock.calls.filter(([search]) => search.q === 'held destination'),
    ).toHaveLength(1)
  })

  it('leaves dismissal cache, reconnect debt, and URL unchanged on errors and stale responses', async () => {
    const user = userEvent.setup()
    const http = createHttp()
    const runId = `preparation_run_${'e'.repeat(64)}`
    const confirmed = terminalPreparation(runId)
    const dismissed: PreparationView = {
      ...confirmed,
      presentation: null,
    }
    const other = terminalPreparation(`preparation_run_${'f'.repeat(64)}`)
    const otherDismissed: PreparationView = {
      ...other,
      presentation: { ...other.presentation, state: 'dismissed', dismissible: false },
    }
    let releaseOlder!: (value: PreparationView) => void
    http.getPreparation = vi.fn(async () => confirmed)
    http.dismissPreparation = vi
      .fn<ReviewHttp['dismissPreparation']>()
      .mockRejectedValueOnce(new ApiError(409, 'stale_preparation'))
      .mockResolvedValueOnce(otherDismissed)
      .mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            releaseOlder = resolve
          }),
      )
    const { queryClient, router } = renderRoute(
      '/library?lens=all&preview=case-1&field=title',
      http,
    )
    await user.click(
      await screen.findByRole('button', { name: /Preparation completed with failures/u }),
    )
    expect(
      await screen.findByText(wholeText('Finished · 1 ready to browse · 1 could not finish'), {
        selector: '.preparation-run-state',
      }),
    ).toBeVisible()
    const readsBeforeDismissal = vi.mocked(http.getPreparation).mock.calls.length

    await user.click(screen.getByRole('button', { name: 'Dismiss preparation' }))
    await waitFor(() => expect(http.dismissPreparation).toHaveBeenCalledTimes(1))
    expect(queryClient.getQueryData(['review-preparation'])).toBe(confirmed)
    expect(router.state.location.search).toMatchObject({ preparation: runId })

    await user.click(screen.getByRole('button', { name: 'Dismiss preparation' }))
    await waitFor(() => expect(http.dismissPreparation).toHaveBeenCalledTimes(2))
    expect(queryClient.getQueryData(['review-preparation'])).toBe(confirmed)
    expect(router.state.location.search).toMatchObject({ preparation: runId })

    await user.click(screen.getByRole('button', { name: 'Dismiss preparation' }))
    await waitFor(() => expect(releaseOlder).toBeTypeOf('function'))
    const later = terminalPreparation(`preparation_run_${'f'.repeat(64)}`, 102)
    act(() => {
      queryClient.setQueryData(['review-preparation'], later)
      releaseOlder(dismissed)
    })
    await waitFor(() => expect(http.dismissPreparation).toHaveBeenCalledTimes(3))
    expect(queryClient.getQueryData(['review-preparation'])).toBe(later)
    expect(router.state.location.search).toEqual({
      lens: 'all',
      preview: 'case-1',
      field: 'title',
    })
    expect(http.getPreparation).toHaveBeenCalledTimes(readsBeforeDismissal)
  })

  it('retries a failed live confirmation until authoritative HTTP succeeds', async () => {
    const http = createHttp()
    const updates = createLiveStream()
    const confirmed: PreparationView = {
      schema_version: 'review-preparation-v2',
      recovery_required: false,
      active: true,
      actionable: false,
      paused: false,
      libraries: [],
      connections: [],
      presentation: {
        run_id: `preparation_run_${'b'.repeat(64)}`,
        state: 'starting',
        triggers: [
          {
            trigger: 'manual_scan',
            accepted_count: 1,
            first_accepted_at: 100,
            last_accepted_at: 100,
          },
        ],
        passage: { read: 0, searches_finished: 0, compared: 0, checked: 0, ready: 0, failed: 0 },
        active_books: [],
        active_overflow: 0,
        recent_completions: [],
        throughput_per_minute: 0,
        started_at: 100,
        updated_at: 100,
        completed_at: null,
        total: 0,
        dismissible: false,
      },
    }
    http.getPreparation = vi
      .fn<ReviewHttp['getPreparation']>()
      .mockResolvedValueOnce(confirmed)
      .mockRejectedValueOnce(new ApiError(503, 'unavailable'))
      .mockResolvedValue(confirmed)
    const { queryClient } = renderRoute('/library?lens=all', http, updates)

    const summary = await screen.findByRole('button', { name: /Preparation is starting/u })
    expect(summary).toBeVisible()
    await userEvent.click(summary)
    expect(await screen.findByRole('heading', { name: 'Now preparing' })).toBeVisible()
    // Mount the actual panel before controlling the fallback sampling cadence.
    vi.useFakeTimers()
    onTestFinished(() => {
      vi.useRealTimers()
    })
    act(() => updates.setState('degraded'))
    expect(screen.getByText(/Reconnecting · showing the last confirmed state/u)).toBeVisible()
    act(() => updates.setState('live'))

    await act(() => vi.advanceTimersByTimeAsync(0))
    expect(http.getPreparation).toHaveBeenCalledTimes(2)
    expect(screen.getByText(/Reconnecting · showing the last confirmed state/u)).toBeVisible()
    await act(() => vi.advanceTimersByTimeAsync(1_499))
    expect(http.getPreparation).toHaveBeenCalledTimes(2)
    expect(screen.getByText(/Reconnecting · showing the last confirmed state/u)).toBeVisible()
    await act(() => vi.advanceTimersByTimeAsync(1))
    expect(http.getPreparation).toHaveBeenCalledTimes(3)
    expect(queryClient.getQueryState(['review-preparation'])?.status).toBe('success')
    expect(queryClient.getQueryData(['review-preparation'])).toBe(confirmed)
    expect(queryClient.getQueryData(['review-preparation-invalidation-debt'])).toBe(false)
    // Query publishes to React on its separate zero-delay notification turn.
    await act(() => vi.advanceTimersByTimeAsync(1))
    expect(http.getPreparation).toHaveBeenCalledTimes(3)
    expect(screen.queryByText(/Reconnecting · showing the last confirmed state/u)).toBeNull()
  })

  it('shows the terminal first-boot measurement without a Library self-action', async () => {
    const previousMatchMedia = window.matchMedia
    vi.stubGlobal(
      'matchMedia',
      vi.fn(() => ({ matches: true, addEventListener: vi.fn(), removeEventListener: vi.fn() })),
    )
    renderRoute('/library?lens=all', {
      ...createHttp(),
      getPreparation: vi.fn(async () => ({
        schema_version: 'review-preparation-v2' as const,
        recovery_required: false,
        active: false,
        actionable: false,
        paused: false,
        first_boot: true,
        first_boot_complete: true,
        first_boot_works: 1,
        presentation: {
          run_id: `preparation_run_${'a'.repeat(64)}`,
          state: 'completed_with_failures' as const,
          triggers: [
            {
              trigger: 'initial_setup' as const,
              accepted_count: 1,
              first_accepted_at: 100,
              last_accepted_at: 100,
            },
          ],
          passage: { read: 1, searches_finished: 1, compared: 1, checked: 1, ready: 0, failed: 1 },
          active_books: [],
          active_overflow: 0,
          recent_completions: [
            { name: 'Unfinished work', outcome: 'failed' as const, completed_at: 101 },
          ],
          throughput_per_minute: 1,
          started_at: 100,
          updated_at: 101,
          completed_at: 101,
          total: 1,
          dismissible: true,
          now: { ready: 0, recorded: 0, not_matched: 0, failed: 1, in_progress: 0, scan_ids: [] },
        },
        libraries: [
          {
            blocked: 1,
            recorded: 0,
            not_matched: 0,
            checked: 1,
            connection: 'primary',
            library: 'books',
            library_id: 'books',
            ready: 0,
            rescan_requested: false,
            scan_id: null,
            state: 'complete' as const,
            total: 1,
          },
        ],
      })),
    })

    const summary = await screen.findByRole('button', {
      name: /Preparation completed with failures/u,
    })
    expect(screen.getByRole('heading', { name: 'Works' })).toBeVisible()
    fireEvent.click(summary)
    expect(
      await screen.findByText(wholeText('Finished · 0 ready to browse · 1 could not finish'), {
        selector: '.preparation-run-state',
      }),
    ).toBeVisible()
    // A completion is first-finish history, so its label is never a phrase the
    // run line uses for the books as they stand now (#1366).
    const completions = [...document.querySelectorAll('.preparation-completions strong')].map(
      (label) => label.textContent,
    )
    expect(completions).toContain('Stopped before finishing')
    for (const label of completions)
      expect(label).not.toMatch(
        /ready to browse|recorded, nothing to decide|not matched|could not finish/iu,
      )
    expect(screen.getByText('Unfinished work')).toBeVisible()
    expect(screen.getByRole('button', { name: 'Dismiss preparation' })).toBeVisible()
    vi.stubGlobal('matchMedia', previousMatchMedia)
  })

  it('keeps a target route pending until its selected field commits', () => {
    const presentation = {
      destination: 'authors',
      sourceRevision: 'source-revision',
      target: 'target-search',
    }

    expect(
      advancePresentationSettled(presentation, 'target-search', 'target-revision', 'title'),
    ).toBe(false)
    expect(
      advancePresentationSettled(presentation, 'target-search', 'target-revision', 'authors'),
    ).toBe(true)
    expect(
      advancePresentationSettled(presentation, 'source-search', 'source-replacement', 'title'),
    ).toBe(true)
  })

  it('bounds and normalizes URL-owned library search state', () => {
    expect(
      normalizeLibrarySearch({ lens: 'all', q: 'title', cursor: 'next', preview: 'case-1' }),
    ).toEqual({
      lens: 'all',
      q: 'title',
      cursor: 'next',
      preview: 'case-1',
    })
    expect(normalizeLibrarySearch({ lens: 'hostile', q: 'x'.repeat(129) })).toEqual({ lens: 'all' })
  })

  it.each(['needs_attention', 'no_attention_needed'] as const)(
    'keeps primary Library navigation active for the %s lens',
    async (lens) => {
      renderRoute(`/library?lens=${lens}`)
      expect(await screen.findByRole('heading', { level: 2, name: 'Works' })).toBeVisible()
      expect(screen.queryByText('Every work, with exceptions first')).toBeNull()
      expect(screen.getByRole('link', { name: 'Library' })).toHaveAttribute('aria-current', 'page')
    },
  )

  it('does not query successor lineage without a preview selection', async () => {
    const getCaseSuccessor = vi.fn<ReviewHttp['getCaseSuccessor']>()
    renderRoute('/library?lens=all', { ...createHttp(), getCaseSuccessor })

    expect(await screen.findByRole('heading', { level: 2, name: 'Works' })).toBeVisible()
    expect(getCaseSuccessor).not.toHaveBeenCalled()
  })

  it('keeps the selected-work header as the only Library route-focus target', async () => {
    renderRoute('/library?lens=all&preview=case-1&field=title')
    expect(await screen.findByRole('heading', { level: 2, name: 'A Book' })).toBeVisible()
    const targets = document.querySelectorAll('[data-route-focus]')
    expect(targets).toHaveLength(1)
    expect(targets[0]).toHaveClass('selected-work-header')
  })

  it('puts compact selected-work navigation below the masthead and before the work', async () => {
    renderRoute('/library?lens=all&preview=case-1&field=title')
    const heading = await screen.findByRole('heading', { level: 2, name: 'A Book' })
    const back = screen.getByRole('button', { name: 'Back to works' })
    const attention = screen.getByRole('button', { name: 'Attention 1 work' })
    const masthead = document.querySelector('.masthead')
    if (!masthead) throw new Error('application fixture requires the masthead')

    expect(masthead.compareDocumentPosition(back) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(back.compareDocumentPosition(heading) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(
      attention.compareDocumentPosition(heading) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy()
    expect(screen.getByRole('heading', { level: 1, name: 'Library' })).toBeInTheDocument()
    // One Attention control, in the toolbar; Back to works leads the workspace (#1284).
    expect(screen.getAllByRole('button', { name: /^Attention/u })).toEqual([attention])
    expect(attention.closest('header[aria-label="Library controls"]')).not.toBeNull()
    expect(back).toHaveClass('compact-workspace-back')
    expect(back.parentElement?.firstElementChild).toBe(back)
    expect(back.parentElement).toHaveClass('library-workspace')
  })

  it('loads the critical library query and progressively reveals detail', async () => {
    const user = userEvent.setup()
    const http: ReviewHttp = {
      ...createHttp(),
      getCaseDetail: vi.fn(async () => ({
        ...detail,
        title: 'item-1',
        subtitle: 'internal identity',
      })),
    }
    renderRoute('/library?lens=all&q=title', http)

    expect(await screen.findByRole('heading', { level: 2, name: 'Works' })).toBeVisible()
    expect(http.getLibrary).toHaveBeenCalledWith(
      { lens: 'all', q: 'title' },
      expect.any(AbortSignal),
      'foreground',
    )
    await user.click(await screen.findByRole('button', { name: /^A Book\./u }))
    expect(await screen.findByRole('heading', { level: 2, name: 'A Book' })).toBeVisible()
    expect(screen.queryByText('item-1')).toBeNull()
    expect(screen.getByText('Author details unavailable · Books · ebook')).toBeVisible()
    expect(http.getCaseDetail).toHaveBeenCalledWith('case-1', expect.any(AbortSignal), 'foreground')
    expect(screen.getByRole('link', { name: 'Evidence' })).toHaveAttribute(
      'href',
      '/review/case-1?field=title&lens=all&subview=evidence&q=title',
    )
    expect(screen.getByRole('link', { name: 'History' })).toHaveAttribute(
      'href',
      '/review/case-1?field=title&lens=all&subview=history&q=title',
    )
  })

  it('opens a current summary and its source ledger for a work with no review case', async () => {
    const firstWork = library.works[0]
    if (!firstWork) throw new Error('library fixture requires one work')
    const baseWork = { ...firstWork }
    delete baseWork.reviewCaseId
    const failed = {
      ...baseWork,
      id: 'failed-work',
      title: 'Preparation unavailable',
      attentionCount: 0,
      attention: 'no_attention_needed',
      // Neither a case nor a report: there is nothing here to open.
      detailUrl: null,
      detailKind: null,
      preparation: {
        attempt: 1,
        diagnosticReference: 'prep-0123456789abcdef',
        mediaHealth: [],
        provider: 'hardcover-main',
        reason: 'Provider credentials need owner attention.',
        recovery: 'Update the configured provider credentials, then scan this library again.',
        recoveryAttempt: 0,
        stage: 'provider',
        status: 'authentication',
      },
      preparationOutcomes: [
        {
          providerRequests: 1,
          laneKind: 'work',
          provider: 'hardcover-main',
          reason: 'Refused the credentials it was given; nothing was retained.',
          status: 'authentication',
        },
      ],
      summary: {
        state: 'preparation_failed' as const,
        reason: 'Provider credentials need owner attention.',
        fields: [],
      },
    }
    const http: ReviewHttp = {
      ...createHttp(),
      getLibrary: vi.fn(async () => ({
        lens: 'all' as const,
        page: 1,
        counts: { attention: 0, all: 1, clear: 1, notMatched: 0 },
        works: [failed],
      })),
    }
    renderRoute('/library?lens=all', http)

    expect(await screen.findByText('Preparation failed safely')).toBeVisible()
    const terminal = document.querySelector('.library-work-row.is-terminal')
    expect(terminal?.tagName).toBe('ARTICLE')
    expect(terminal).toHaveAttribute('aria-label', 'Terminal preparation failure')
    expect(terminal?.querySelector('button, a, details, [tabindex]')).toBeNull()
    expect(screen.queryByRole('button', { name: /^Preparation unavailable\./u })).toBeNull()
    expect(terminal).toHaveTextContent(
      'Update the configured provider credentials, then scan this library again.',
    )
    expect(screen.getByText('prep-0123456789abcdef')).toBeVisible()
  })

  it.each([
    { chapterBasis: false, entry: 'click' },
    { chapterBasis: true, entry: 'click' },
    { chapterBasis: false, entry: 'desktop' },
    { chapterBasis: false, entry: 'resize' },
    { chapterBasis: false, entry: 'conflicting-preview' },
  ])(
    'opens recorded audiobook evidence with $chapterBasis attribution via $entry',
    async ({ chapterBasis, entry }) => {
      const user = userEvent.setup()
      const firstWork = library.works[0]
      if (!firstWork) throw new Error('library fixture requires one work')
      const baseWork = { ...firstWork }
      delete baseWork.reviewCaseId
      const detailUrl = '/api/v1/library/report?connection=primary&library=audio&item=item-9'
      const recorded = {
        ...baseWork,
        id: 'library_work_audio',
        workId: 'library_work_audio',
        title: 'Recorded work',
        mediaKind: 'm4b',
        attentionCount: 0,
        attention: 'no_attention_needed',
        detailUrl,
        detailKind: 'report' as const,
        summary: {
          state: 'no_decision_recorded' as const,
          reason: 'No review decision is recorded.',
          fields: [],
        },
        // An audiobook whose providers admitted no work identity completes on
        // the recorded outcome, so it reports no preparation failure at all.
        // The row must not read as a failure, and must open (#966).
        preparation: null,
      }
      const getReportDetail = vi.fn(async () => ({
        chapters: [
          { endMilliseconds: 1_800_000, ordinal: 1, startMilliseconds: 0, title: 'Chapter One' },
        ],
        chaptersTruncated: false,
        coverPresent: true,
        coverSurface: 'container_stream',
        evidence: [
          {
            basis: 'sidecar_libation',
            excerpt: 'B0CW1KL2QD',
            kind: 'asin',
            role: null,
            state: 'valid',
            value: 'B0CW1KL2QD',
          },
          {
            basis: 'sidecar_libation',
            excerpt: 'Recorded Narrator',
            kind: 'contributor',
            role: 'narrator',
            state: 'valid',
            value: 'Recorded Narrator',
          },
          {
            basis: 'container_format',
            excerpt: '43200.000',
            kind: 'duration',
            role: null,
            state: 'valid',
            value: '43200.000',
          },
          {
            basis: 'path_file',
            excerpt: 'B0XXXXXXXX',
            kind: 'asin',
            role: null,
            state: 'malformed',
            value: 'B0XXXXXXXX',
          },
        ],
        evidenceTruncated: false,
        item: 'item-9',
        library: 'audio',
        mediaHealth: [
          {
            bound:
              'The local running time is within 90 seconds of the running time the matched edition states.',
            cited: ['local_abc123', 'audible-product-B08G9PRS1K'],
            expected: '72000 seconds stated by the matched edition',
            kind: 'runtime_short_of_provider' as const,
            observed: '43200 seconds of local audio',
            tracks: [],
          },
        ],
        mediaKind: 'm4b',
        providerSources: [
          {
            providerRequests: 1,
            laneKind: 'asin',
            provider: 'Audnexus',
            reason: 'The provider had nothing matching this work.',
            status: 'no_match',
          },
        ],
        providerEvidence: [],
        providerEvidenceTruncated: false,
        audiobookReviewScope: chapterBasis ? ('recording' as const) : null,
        recordingIdentityBasis: chapterBasis
          ? {
              basis: 'chapter_runtime_and_structure' as const,
              provider: 'Audnexus',
              candidate: 'recording-1',
            }
          : null,
        reportId: 'report_' + 'a'.repeat(64),
        title: 'Recorded work',
      }))
      const http: ReviewHttp = {
        ...createHttp(),
        getReportDetail,
        getLibrary: vi.fn(async () => ({
          lens: 'all' as const,
          page: 1,
          counts: { attention: 0, all: 1, clear: 1, notMatched: 0 },
          works: [...library.works, recorded],
        })),
      }
      const previousMatchMedia = window.matchMedia
      const changes = new Set<() => void>()
      const media = {
        matches: entry !== 'resize',
        addEventListener: (_type: string, listener: () => void) => changes.add(listener),
        removeEventListener: (_type: string, listener: () => void) => changes.delete(listener),
      }
      vi.stubGlobal(
        'matchMedia',
        vi.fn(() => media),
      )
      const route =
        entry === 'click'
          ? '/library?lens=all'
          : `/library?lens=all&work=library_work_audio${entry === 'conflicting-preview' ? '&preview=case-1' : ''}`
      const { router } = renderRoute(route, http)

      const row = await screen.findByRole('button', { name: /^Recorded work\./u })
      expect(row).not.toHaveClass('is-terminal')
      expect(document.querySelector('.library-work-row.is-terminal')).toBeNull()
      expect(row).toHaveTextContent('Recorded evidence available')

      if (entry === 'click') {
        await waitFor(() => expect(router.state.location.search.preview).toBe('case-1'))
        await user.click(row)
      }
      if (entry === 'resize') {
        act(() => {
          media.matches = true
          changes.forEach((listener) => listener())
        })
      }
      await waitFor(() => expect(row).toHaveAttribute('aria-current', 'true'))
      expect(document.querySelectorAll('.library-work-row[aria-current="true"]')).toHaveLength(1)
      expect(router.state.location.search.work).toBe('library_work_audio')
      if (entry !== 'conflicting-preview')
        expect(router.state.location.search.preview).toBeUndefined()

      const evidence = await screen.findByRole('region', { name: 'Recorded evidence' })
      if (chapterBasis) {
        expect(
          within(evidence).getByRole('region', { name: 'Recording identity evidence' }),
        ).toHaveTextContent(
          'Complete chapter timing and structure corroborate this recording where overall runtime is unavailable.',
        )
      } else {
        expect(
          within(evidence).queryByRole('region', { name: 'Recording identity evidence' }),
        ).toBeNull()
      }
      expect(getReportDetail).toHaveBeenCalledWith(detailUrl, expect.anything(), 'foreground')
      expect(within(evidence).getByText('B0CW1KL2QD')).toBeVisible()
      expect(within(evidence).getByText('Recorded Narrator')).toBeVisible()
      expect(within(evidence).getByText('43200.000')).toBeVisible()
      // A claim the extraction could not trust must not read as sound evidence.
      expect(within(evidence).getByText(/Malformed/u)).toBeVisible()
      expect(within(evidence).getByText(/Embedded cover art is present/u)).toBeVisible()
      // What is wrong with the media, stated as its own section: the observation,
      // the rule it broke, and what it was read from -- with the opaque local
      // reference standing for the evidence it points at rather than shown.
      const health = within(evidence).getByRole('region', { name: 'Media health' })
      expect(within(health).getByText('Runtime short of provider')).toBeVisible()
      expect(
        within(health).getByText(/43200 seconds of local audio · expected 72000 seconds/u),
      ).toBeVisible()
      expect(
        within(health).getByText(
          'The local running time is within 90 seconds of the running time the matched edition states.',
        ),
      ).toBeVisible()
      expect(
        within(health).getByText('Read from local evidence, audible-product-B08G9PRS1K'),
      ).toBeVisible()
      // Chapters are a bounded structured section, disclosed like the source
      // ledger beside them rather than filling the panel.
      await user.click(within(evidence).getByText(/Chapters \(1\)/u))
      expect(within(evidence).getByText('Chapter One')).toBeVisible()
      expect(within(evidence).getByText('00:00:00 – 00:30:00')).toBeVisible()
      vi.stubGlobal('matchMedia', previousMatchMedia)
    },
  )

  it('does not let a retained report hide the latest real preparation failure', async () => {
    const firstWork = library.works[0]
    if (!firstWork) throw new Error('library fixture requires one work')
    const baseWork = { ...firstWork }
    delete baseWork.reviewCaseId
    const masked = {
      ...baseWork,
      id: 'library_work_masked',
      workId: 'library_work_masked',
      title: 'Retained work',
      attentionCount: 0,
      attention: 'no_attention_needed',
      // Its earlier report is still browsable, but the latest attempt failed
      // for a real reason the owner has to act on.
      detailUrl: '/api/v1/library/report?connection=primary&library=ebooks&item=item-7',
      detailKind: 'report' as const,
      preparation: {
        attempt: 1,
        diagnosticReference: 'prep-masked-0123456789',
        mediaHealth: [],
        provider: 'hardcover-main',
        reason: 'Provider credentials need owner attention.',
        recovery: 'Update the configured provider credentials, then scan this library again.',
        recoveryAttempt: 0,
        stage: 'provider',
        status: 'authentication',
      },
    }
    const http: ReviewHttp = {
      ...createHttp(),
      getLibrary: vi.fn(async () => ({
        lens: 'all' as const,
        page: 1,
        counts: { attention: 0, all: 1, clear: 1, notMatched: 0 },
        works: [masked],
      })),
    }
    renderRoute('/library?lens=all', http)

    const row = await screen.findByRole('button', { name: /^Retained work\./u })
    // Openable, because the evidence is real -- but not reported as a success.
    expect(row).toHaveTextContent('Preparation failed safely')
    expect(row).not.toHaveTextContent('Recorded evidence available')
    expect(screen.getByRole('heading', { name: 'Preparation needs attention' })).toBeVisible()
    expect(screen.queryByRole('heading', { name: 'No attention needed' })).toBeNull()
    expect(row.querySelector('.needs-attention')).not.toBeNull()
  })

  it('lists works no provider matched under their own lens, each with one reason (#1407)', async () => {
    const user = userEvent.setup()
    const firstWork = library.works[0]
    if (!firstWork) throw new Error('library fixture requires one work')
    const baseWork = { ...firstWork }
    delete baseWork.reviewCaseId
    const unmatched = (
      key: string,
      state:
        | 'not_matched_refused'
        | 'not_matched_unavailable_scheduled'
        | 'not_matched_unavailable'
        | 'not_matched_no_match',
    ) => ({
      ...baseWork,
      id: `library_work_${key}`,
      workId: `library_work_${key}`,
      title: `Unmatched ${key}`,
      attentionCount: 0,
      attention: 'not_matched',
      detailUrl: `/api/v1/library/report?connection=primary&library=ebooks&item=${key}`,
      detailKind: 'report' as const,
      preparation: null,
      preparationOutcomes: [],
      summary: { fields: [], reason: 'No review decision is recorded for this work.', state },
    })
    const works = [
      unmatched('refused', 'not_matched_refused'),
      unmatched('scheduled', 'not_matched_unavailable_scheduled'),
      unmatched('unavailable', 'not_matched_unavailable'),
      unmatched('nothing', 'not_matched_no_match'),
    ]
    const getLibrary = vi.fn(async (search: LibrarySearch) => ({
      lens: search.lens,
      page: 1,
      counts: { attention: 0, all: 4, clear: 0, notMatched: 4 },
      works,
    }))
    renderRoute('/library?lens=all', { ...createHttp(), getLibrary })

    const lens = await screen.findByRole('link', { name: 'Not matched' })
    // The count sits beside the Works count, in the same words.
    expect(await screen.findByText('4 works · 4 not matched')).toBeVisible()
    expect(
      within(screen.getByRole('navigation', { name: 'Library lenses' })).getAllByRole('link'),
    ).toHaveLength(4)
    expect(screen.getByRole('heading', { name: 'Not matched' })).toBeVisible()
    for (const [key, reason] of [
      ['refused', 'Found, but not this work'],
      ['scheduled', 'Providers unavailable; will be checked again'],
      ['unavailable', 'Providers unavailable'],
      ['nothing', 'No provider matched'],
    ] as const) {
      const row = screen.getByRole('button', { name: new RegExp(`^Unmatched ${key}\\.`, 'u') })
      const status = row.querySelector('.library-work-status > span')
      expect(status).toHaveTextContent(new RegExp(`^${reason}$`, 'u'))
      expect(status).toHaveClass('handled')
      expect(row).not.toHaveTextContent('Recorded evidence available')
    }

    await user.click(lens)
    await waitFor(() =>
      expect(getLibrary).toHaveBeenCalledWith(
        expect.objectContaining({ lens: 'not_matched' }),
        expect.anything(),
        expect.anything(),
      ),
    )
    expect(await screen.findByRole('link', { name: 'Not matched' })).toHaveAttribute(
      'aria-current',
      'page',
    )
    // Inside its own lens the rows need no group heading.
    expect(screen.queryByRole('heading', { name: 'Not matched' })).toBeNull()
  })

  it('names the Not matched lens without a count it could not read', async () => {
    renderRoute('/library?lens=all', {
      ...createHttp(),
      getLibrary: vi.fn(async () => {
        throw new ApiError(503, 'unavailable')
      }),
    })
    expect(await screen.findByRole('alert')).toHaveTextContent('Works are unavailable.')
    expect(screen.getByRole('link', { name: 'Not matched' })).toBeVisible()
  })

  it('adds Not matched to the Works count only when there is one to state (#1407)', async () => {
    const page = (search: LibrarySearch, notMatched: number) => ({
      lens: search.lens,
      page: 1,
      counts: { attention: 0, all: 2, clear: 2 - notMatched, notMatched },
      works: [],
    })
    const counted = async (getLibrary: ReviewHttp['getLibrary']) => {
      cleanup()
      renderRoute('/library?lens=not_matched', { ...createHttp(), getLibrary })
      const header = (await screen.findByRole('heading', { name: 'Works' })).parentElement
      if (!header) throw new Error('the Works heading has a header')
      return within(header).getByRole('status')
    }
    let status = await counted(vi.fn(async (search: LibrarySearch) => page(search, 0)))
    await waitFor(() => expect(status).toHaveTextContent(/^2 works$/u))
    status = await counted(
      vi.fn(async (search: LibrarySearch) =>
        search.lens === 'all' ? new Promise<never>(() => undefined) : page(search, 1),
      ),
    )
    await waitFor(() => expect(status).toHaveTextContent(/^2 works · … not matched$/u))
    status = await counted(
      vi.fn(async (search: LibrarySearch) => {
        if (search.lens === 'all') throw new ApiError(503, 'unavailable')
        return page(search, 1)
      }),
    )
    await waitFor(() => expect(status).toHaveTextContent(/^2 works$/u))
  })

  it('keeps an unknown lens out of the URL it reads (#1407)', () => {
    expect(normalizeLibrarySearch({ lens: 'not_matched' })).toEqual({ lens: 'not_matched' })
    expect(normalizeLibrarySearch({ lens: 'not_matched_refused' })).toEqual({ lens: 'all' })
  })

  it('states media health on an openable row whose latest preparation could not read a file', async () => {
    const firstWork = library.works[0]
    if (!firstWork) throw new Error('library fixture requires one work')
    const baseWork = { ...firstWork }
    delete baseWork.reviewCaseId
    const retained = {
      ...baseWork,
      id: 'library_work_unreadable',
      workId: 'library_work_unreadable',
      title: 'Retained audiobook',
      attentionCount: 0,
      attention: 'no_attention_needed',
      detailUrl: '/api/v1/library/report?connection=primary&library=audio&item=item-8',
      detailKind: 'report' as const,
      preparation: {
        attempt: 1,
        diagnosticReference: 'prep-unreadable-0123456789',
        mediaHealth: [
          {
            bound: 'Every declared track is readable by the installed decoder.',
            cited: ['track_4'],
            expected: null,
            kind: 'media_unreadable' as const,
            observed: null,
            tracks: [4],
          },
        ],
        provider: 'audiobookshelf-main',
        reason: 'One file in this work could not be read.',
        recovery: 'Repair or replace the unreadable file, then scan this library again.',
        recoveryAttempt: 0,
        stage: 'media',
        status: 'unreadable_media',
      },
    }
    const http: ReviewHttp = {
      ...createHttp(),
      getLibrary: vi.fn(async () => ({
        lens: 'all' as const,
        page: 1,
        counts: { attention: 0, all: 1, clear: 1, notMatched: 0 },
        works: [retained],
      })),
    }
    renderRoute('/library?lens=all', http)

    // The row stays openable because the earlier evidence is real, and the
    // observation the failure produced travels with it instead of being
    // dropped with the terminal panel it used to live in.
    const row = await screen.findByRole('button', { name: /^Retained audiobook\./u })
    expect(row).toHaveTextContent('Preparation failed safely')
    const health = screen.getByRole('region', { name: 'Media health' })
    expect(within(health).getByText('Media unreadable')).toBeVisible()
    expect(within(health).getByText('Read from track_4')).toBeVisible()
    expect(row).not.toContainElement(health)
  })

  it('keeps a current case selectable while exposing its terminal preparation diagnostic', async () => {
    const user = userEvent.setup()
    const firstWork = library.works[0]
    if (!firstWork) throw new Error('library fixture requires one work')
    const current = {
      ...firstWork,
      preparation: {
        attempt: 1,
        diagnosticReference: 'prep-safe-0123456789abcdef',
        mediaHealth: [],
        provider: 'hardcover-main',
        reason: 'A <script> hostile diagnostic remains safe.',
        recovery: 'Scan again only after the source condition is corrected.',
        recoveryAttempt: 0,
        stage: 'provider',
        status: 'failed',
      },
    }
    const http: ReviewHttp = {
      ...createHttp(),
      getLibrary: vi.fn(async () => ({
        lens: 'all' as const,
        page: 1,
        counts: { attention: 1, all: 1, clear: 0, notMatched: 0 },
        works: [current],
      })),
    }
    renderRoute('/library?lens=all', http)

    const card = await screen.findByRole('button', { name: /^A Book./u })
    expect(card).not.toHaveClass('is-terminal')
    expect(card).toHaveTextContent('A <script> hostile diagnostic remains safe.')
    expect(card).toHaveAccessibleDescription(
      /provider: A <script> hostile diagnostic remains safe\. Scan again only after the source condition is corrected\. Reference: prep-safe-0123456789abcdef/u,
    )
    expect(screen.getByText('prep-safe-0123456789abcdef')).toBeVisible()
    expect(screen.queryByRole('button', { name: /retry/i })).toBeNull()
    card.focus()
    fireEvent.keyDown(card, { key: 'Enter' })
    await user.click(card)
    expect(await screen.findByRole('heading', { level: 2, name: 'A Book' })).toBeVisible()
  })

  it('keeps an open case-less work open when a search hides its card', async () => {
    const user = userEvent.setup()
    const firstWork = library.works[0]
    if (!firstWork) throw new Error('library fixture requires one work')
    const orphan = { ...firstWork }
    delete orphan.reviewCaseId
    const listed = {
      ...orphan,
      id: 'orphan-work',
      workId: 'work-orphan',
      title: 'Unfiled observation',
      attentionCount: 0,
      summary: {
        state: 'no_decision_recorded' as const,
        reason: 'No review decision is recorded for this work.',
        fields: [{ destination: 'title', state: 'value', value: 'Unfiled observation' }],
      },
    }
    const getLibrary = vi.fn(async (search: { q?: string }) => ({
      lens: 'all' as const,
      page: 1,
      counts: { attention: 0, all: 1, clear: 1, notMatched: 0 },
      works: search.q ? [] : [listed],
    }))
    renderRoute('/library?lens=all', { ...createHttp(), getLibrary })

    await user.click(await screen.findByRole('button', { name: /^Unfiled observation\./u }))
    expect(
      await screen.findByRole('heading', { level: 2, name: 'Unfiled observation' }),
    ).toBeVisible()
    expect(screen.queryByRole('link', { name: 'Evidence' })).toBeNull()
    expect(screen.queryByRole('link', { name: 'History' })).toBeNull()
    await user.type(
      screen.getByLabelText('Search title, author, identifier, library, or format'),
      'nothing matches',
    )

    // The card is gone from the narrowed result, but the open work stays open.
    await screen.findByText('No exact-current works match this search and lens.')
    expect(screen.getByRole('heading', { level: 2, name: 'Unfiled observation' })).toBeVisible()
    await user.click(screen.getByRole('button', { name: 'Back to works' }))
    expect(screen.queryByRole('heading', { level: 2, name: 'Unfiled observation' })).toBeNull()
  })

  it('keeps the latest case-less selection when an older navigation settles last', async () => {
    const user = userEvent.setup()
    const firstWork = library.works[0]
    if (!firstWork) throw new Error('library fixture requires one work')
    const caseLess = (id: string, title: string) => {
      const work = { ...firstWork }
      delete work.reviewCaseId
      return {
        ...work,
        id,
        workId: `work-${id}`,
        title,
        attentionCount: 0,
        summary: {
          state: 'no_decision_recorded' as const,
          reason: 'No review decision is recorded for this work.',
          fields: [{ destination: 'title', state: 'value' as const, value: title }],
        },
      }
    }
    const first = caseLess('first-observation', 'First observation')
    const second = caseLess('second-observation', 'Second observation')
    const getLibrary = vi.fn(async (search: { q?: string }) => ({
      lens: 'all' as const,
      page: 1,
      counts: { attention: 0, all: 2, clear: 2, notMatched: 0 },
      works: search.q ? [] : [first, second],
    }))
    const { router } = renderRoute('/library?lens=all', { ...createHttp(), getLibrary })
    const firstRow = await screen.findByRole('button', { name: /^First observation\./u })
    const secondRow = screen.getByRole('button', { name: /^Second observation\./u })
    const navigate = router.navigate.bind(router)
    let releaseFirstNavigation: () => void = () => undefined
    const firstNavigationHeld = new Promise<void>((resolve) => {
      releaseFirstNavigation = resolve
    })
    let selectionNavigations = 0
    vi.spyOn(router, 'navigate').mockImplementation(async (options) => {
      selectionNavigations += 1
      const invocation = selectionNavigations
      const result = await navigate(options)
      if (invocation === 1) await firstNavigationHeld
      return result
    })

    fireEvent.click(firstRow)
    fireEvent.click(secondRow)
    await waitFor(() =>
      expect(router.state.location.search).toMatchObject({
        lens: 'all',
        work: 'work-second-observation',
      }),
    )
    await act(async () => {
      releaseFirstNavigation()
      await firstNavigationHeld
    })
    await user.type(
      screen.getByLabelText('Search title, author, identifier, library, or format'),
      'hide both',
    )
    await screen.findByText('No exact-current works match this search and lens.')
    expect(
      await screen.findByRole('heading', { level: 2, name: 'Second observation' }),
    ).toBeVisible()
    await waitFor(() =>
      expect(router.state.location.search).toMatchObject({
        lens: 'all',
        q: 'hide both',
        work: 'work-second-observation',
      }),
    )
  })

  it('normalises an unresolvable work selection out of the URL', async () => {
    const http: ReviewHttp = { ...createHttp(), getLibrary: vi.fn(async () => library) }
    const { router } = renderRoute('/library?lens=all&work=work-that-is-not-on-this-page', http)

    // The URL asserted a selection nothing can render, so it stops asserting it
    // rather than leaving the view and the address bar disagreeing.
    await waitFor(() => expect(router.state.location.search).toEqual({ lens: 'all' }))
    expect(screen.getByRole('heading', { name: 'Choose a work' })).toBeVisible()
  })

  it.each([
    ['candidates', 'Candidates found'],
    ['no_match', 'No match'],
    ['rate_limited', 'Rate limited'],
    ['unavailable', 'Unavailable'],
    ['candidate_limit_exceeded', 'Too many candidates'],
    ['authentication', 'Authentication failed'],
    ['malformed', 'Malformed evidence'],
    ['a_status_this_build_has_never_seen', 'Provider failure'],
  ])('names the %s source status for an owner as %s', (status, label) => {
    expect(sourceStatusLabel(status)).toBe(label)
  })

  it('keeps the whole work card as the single selection target', async () => {
    const user = userEvent.setup()
    const { router } = renderRoute('/library?lens=all')

    const card = await screen.findByRole('button', { name: /^A Book\./u })
    expect(card).toHaveClass('library-work-row')
    // No nested title-only target competes with the card.
    expect(within(card).queryByRole('button')).toBeNull()
    expect(within(card).queryByRole('link')).toBeNull()
    expect(card).toHaveTextContent('A Book')
    expect(card).toHaveTextContent('Author details unavailable')
    expect(card).toHaveTextContent('1 field needs attention')

    await user.click(card)
    await waitFor(() =>
      expect(router.state.location.search).toEqual({ lens: 'all', preview: 'case-1' }),
    )
    expect(screen.getByRole('button', { name: /^A Book\./u })).toHaveAttribute(
      'aria-current',
      'true',
    )
  })

  it('offers both pagination directions with readable page context', async () => {
    const user = userEvent.setup()
    const paged = { ...library, page: 2, nextCursor: 'page-3', previousCursor: 'page-1' }
    const http: ReviewHttp = { ...createHttp(), getLibrary: vi.fn(async () => paged) }
    const { router } = renderRoute('/library?lens=all&q=book&cursor=page-2', http)

    expect(await screen.findByText('Page 2')).toBeVisible()
    await user.click(screen.getByRole('button', { name: '← Previous' }))
    await waitFor(() =>
      expect(router.state.location.search).toEqual({ lens: 'all', q: 'book', cursor: 'page-1' }),
    )
    await user.click(screen.getByRole('button', { name: 'Next →' }))
    await waitFor(() =>
      expect(router.state.location.search).toEqual({ lens: 'all', q: 'book', cursor: 'page-3' }),
    )
  })

  describe('retention and intent prefetch', () => {
    const work = library.works[0]
    if (!work) throw new Error('library fixture requires one work')
    const page = (title: string, cursors: Partial<LibraryView> = {}): LibraryView => ({
      ...library,
      ...cursors,
      works: [{ ...work, id: title, title }],
    })
    interface Deferred {
      resolve: (value: LibraryView) => void
      reject: (error: Error) => void
    }
    const deferredLibrary = (first: LibraryView) => {
      const reads = new Map<string, Deferred>()
      const getLibrary = vi.fn<ReviewHttp['getLibrary']>(async (search) => {
        const key = search.q ? `q:${search.q}` : (search.cursor ?? '')
        if (!key) return first
        return new Promise<LibraryView>((resolve, reject) => reads.set(key, { resolve, reject }))
      })
      return { reads, getLibrary }
    }

    it('keeps the previous page busy in place until the newer page settles', async () => {
      const user = userEvent.setup()
      const { reads, getLibrary } = deferredLibrary(page('First page', { nextCursor: 'page-2' }))
      const http = { ...createHttp(), getLibrary }
      const { router } = renderRoute('/library?lens=all', http)
      const next = await screen.findByRole('button', { name: 'Next →' })
      next.focus()
      await user.keyboard('{Enter}')
      await waitFor(() => expect(router.state.location.search.cursor).toBe('page-2'))
      const list = document.querySelector<HTMLElement>('.library-work-list')
      expect(list).toHaveAttribute('aria-busy', 'true')
      expect(screen.getByRole('button', { name: /^First page\./u })).toBeVisible()
      expect(screen.queryByText('Loading works…')).toBeNull()
      expect(screen.getByText('1 work', { selector: '[role=status]' })).toHaveAttribute(
        'aria-busy',
        'true',
      )
      const announced = document.querySelector('.navigation-status')
      expect(announced).toHaveTextContent('Loading Library')
      expect(next).toHaveAttribute('aria-disabled', 'true')
      // The retained page's cursor belongs to the key the URL just left.
      await user.click(next)
      expect(router.state.location.search.cursor).toBe('page-2')
      // So may its rows, which the settled page may not contain.
      const retainedRow = screen.getByRole('button', { name: /^First page\./u })
      expect(retainedRow).toHaveAttribute('aria-disabled', 'true')
      await user.click(retainedRow)
      expect(router.state.location.search).toEqual({ lens: 'all', cursor: 'page-2' })
      fireEvent.pointerEnter(retainedRow)
      retainedRow.focus()
      await new Promise((resolve) => setTimeout(resolve, 100))
      expect(http.getCaseDetail).not.toHaveBeenCalled()
      await act(async () =>
        reads
          .get('page-2')
          ?.resolve(
            page('Second page', { page: 2, previousCursor: 'page-1', nextCursor: 'page-3' }),
          ),
      )
      expect(await screen.findByRole('button', { name: /^Second page\./u })).toBeVisible()
      expect(screen.queryByRole('button', { name: /^First page\./u })).toBeNull()
      expect(list).not.toHaveAttribute('aria-busy')
      expect(next).not.toHaveAttribute('aria-disabled')
      expect(announced).toHaveTextContent('Library loaded')
    })

    it('keeps the retained rows grouped as their own lens until the newer lens settles', async () => {
      let settle: (view: LibraryView) => void = () => undefined
      const getLibrary = vi.fn<ReviewHttp['getLibrary']>(async (search) =>
        search.lens === 'all'
          ? page('Grouped work')
          : new Promise<LibraryView>((resolve) => (settle = resolve)),
      )
      const { router } = renderRoute('/library?lens=all', { ...createHttp(), getLibrary })
      await screen.findByRole('button', { name: /^Grouped work\./u })
      const groups = () =>
        [...document.querySelectorAll('.library-provider-group h3')].map((h) => h.textContent)
      const shown = groups()
      expect(shown).toEqual(['Provider evidence complete'])
      await act(async () =>
        router.navigate({ to: '/library', search: { lens: 'no_attention_needed' } }),
      )
      expect(document.querySelector('.library-work-list')).toHaveAttribute('aria-busy', 'true')
      expect(groups()).toEqual(shown)
      await act(async () => settle({ ...page('Clear work'), lens: 'no_attention_needed' }))
      await screen.findByRole('button', { name: /^Clear work\./u })
      expect(groups()).toEqual([])
    })

    it('lets no late Library navigation pull the owner back once another route is loading', async () => {
      const { getLibrary } = deferredLibrary(page('First page', { nextCursor: 'page-2' }))
      const { router } = renderRoute('/library?lens=all', { ...createHttp(), getLibrary })
      const next = await screen.findByRole('button', { name: 'Next →' })
      await act(async () => {
        const leaving = router.navigate({ to: '/settings', search: { group: 'connection' } })
        // Library is still painted while the Settings route loads.
        fireEvent.click(next)
        await leaving
      })
      expect(router.state.location.pathname).toBe('/settings')
      expect(router.state.location.search).toEqual({ group: 'connection' })
    })

    it('never lets an older page response replace a newer page or the typed draft', async () => {
      const user = userEvent.setup()
      const { reads, getLibrary } = deferredLibrary(page('First page', { nextCursor: 'page-2' }))
      const { router } = renderRoute('/library?lens=all', { ...createHttp(), getLibrary })
      await screen.findByRole('button', { name: /^First page\./u })
      await act(async () =>
        router.navigate({ to: '/library', search: { lens: 'all', cursor: 'page-2' } }),
      )
      await act(async () =>
        router.navigate({ to: '/library', search: { lens: 'all', cursor: 'page-3' } }),
      )
      await act(async () => reads.get('page-3')?.resolve(page('Third page', { page: 3 })))
      expect(await screen.findByRole('button', { name: /^Third page\./u })).toBeVisible()
      await act(async () => reads.get('page-2')?.resolve(page('Second page', { page: 2 })))
      expect(screen.queryByRole('button', { name: /^Second page\./u })).toBeNull()
      expect(screen.getByRole('button', { name: /^Third page\./u })).toBeVisible()

      await act(async () =>
        router.navigate({ to: '/library', search: { lens: 'all', cursor: 'page-4' } }),
      )
      const box = screen.getByRole('searchbox')
      await user.click(box)
      await user.type(box, 'draft')
      await act(async () => reads.get('page-4')?.resolve(page('Older page', { page: 4 })))
      expect(box).toHaveValue('draft')
      expect(box).toHaveFocus()
      expect(box).toHaveProperty('selectionStart', 5)
      await waitFor(() => expect(router.state.location.search.q).toBe('draft'))
      expect(screen.getByRole('button', { name: /^Older page\./u })).toBeVisible()
      expect(document.querySelector('.library-work-list')).toHaveAttribute('aria-busy', 'true')
      await act(async () => reads.get('q:draft')?.resolve(page('Draft result')))
      expect(await screen.findByRole('button', { name: /^Draft result\./u })).toBeVisible()
      expect(screen.queryByRole('button', { name: /^Older page\./u })).toBeNull()
      expect(box).toHaveValue('draft')
      expect(box).toHaveFocus()
    })

    it('keeps the loaded Attention queue busy in place while a newer search reads', async () => {
      const user = userEvent.setup()
      const item = {
        id: 'case-1',
        destination: 'title',
        title: 'Retained queue work',
        authors: [],
        library: 'Books',
        mediaKind: 'ebook',
        notices: [],
        provenance: [],
        workAttentionCount: 1,
      }
      type Page = Awaited<ReturnType<ReviewHttp['getAttention']>>
      let settle: ((value: Page) => void) | undefined
      const http = createHttp()
      http.getAttention = vi.fn<ReviewHttp['getAttention']>(async ({ q }) =>
        q
          ? new Promise<Page>((resolve) => {
              settle = resolve
            })
          : { total: 1, items: [item], nextCursor: 'later-page' },
      )
      const { router } = renderRoute('/library?lens=all&attention=open', http)
      const row = await screen.findByRole('button', { description: 'Retained queue work' })
      await act(async () =>
        router.navigate({ to: '/library', search: { lens: 'all', attention: 'open', q: 'later' } }),
      )
      const rail = screen.getByLabelText('Attention queue')
      await waitFor(() => expect(rail).toHaveAttribute('aria-busy', 'true'))
      // The works read has settled, but the route is not loaded while its rail is retained.
      expect(screen.getByText('Matching: 1 of 1 work')).not.toHaveAttribute('aria-busy')
      const announced = document.querySelector('.navigation-status')
      expect(announced).toHaveTextContent('Loading Library')
      expect(row).toBeVisible()
      expect(row).toHaveAttribute('aria-disabled', 'true')
      expect(screen.queryByRole('button', { name: 'Load more attention' })).toBeNull()
      await user.click(row)
      expect(router.state.location.search.preview).toBeUndefined()
      await act(async () =>
        settle?.({ total: 1, items: [{ ...item, id: 'case-2', title: 'Later queue work' }] }),
      )
      expect(await screen.findByRole('button', { description: 'Later queue work' })).toBeVisible()
      expect(screen.queryByRole('button', { description: 'Retained queue work' })).toBeNull()
      expect(rail).not.toHaveAttribute('aria-busy')
      expect(announced).toHaveTextContent('Library loaded')
    })

    it('prefetches the adjacent page, other lenses and a work on intent as background reads', async () => {
      const user = userEvent.setup()
      const second = { ...work, id: 'case-2', reviewCaseId: 'case-2', title: 'Second work' }
      const http = createHttp()
      http.getLibrary = vi.fn<ReviewHttp['getLibrary']>(async (search) =>
        search.cursor
          ? page('Prefetched page', { page: 2, previousCursor: 'page-1' })
          : { ...library, nextCursor: 'page-2', works: [work, second] },
      )
      const { router } = renderRoute('/library?lens=all', http)
      const next = await screen.findByRole('button', { name: 'Next →' })
      const href = router.state.location.href
      const reads = (match: Partial<LibrarySearch>) =>
        vi
          .mocked(http.getLibrary)
          .mock.calls.filter(([search]) =>
            Object.entries(match).every(
              ([key, value]) => search[key as keyof LibrarySearch] === value,
            ),
          )

      await user.hover(next)
      await waitFor(() => expect(reads({ cursor: 'page-2' })).toHaveLength(1))
      expect(reads({ cursor: 'page-2' })[0]?.[2]).toBe('background')
      await user.unhover(next)
      await user.hover(next)
      await new Promise((resolve) => setTimeout(resolve, 100))
      expect(reads({ cursor: 'page-2' })).toHaveLength(1)

      act(() => screen.getByRole('link', { name: 'Needs attention' }).focus())
      await waitFor(() => expect(reads({ lens: 'needs_attention' })).toHaveLength(1))
      expect(reads({ lens: 'needs_attention' })[0]?.[2]).toBe('background')

      await user.hover(screen.getByRole('button', { name: /^Second work\./u }))
      await waitFor(() =>
        expect(http.getCaseDetail).toHaveBeenCalledWith(
          'case-2',
          expect.any(AbortSignal),
          'background',
        ),
      )
      expect(router.state.location.href).toBe(href)

      await user.click(next)
      expect(await screen.findByRole('button', { name: /^Prefetched page\./u })).toBeVisible()
      expect(reads({ cursor: 'page-2' })).toHaveLength(1)
    })
  })

  it('filters progressively while typing, resets on clear, and keeps the lens and selection', async () => {
    const user = userEvent.setup()
    const { router } = renderRoute('/library?lens=needs_attention&preview=case-1&cursor=page-2')

    // Wait for the route to finish arriving before taking the keyboard. The
    // arrival re-renders the panel, and a box grabbed before that is a
    // detached node every keystroke goes into and nothing comes out of --
    // which passes whenever the machine is quick and fails whenever the rest
    // of the suite is competing for it.
    await waitFor(() => expect(document.querySelector('[data-route-focus]')).toHaveFocus())
    const box = screen.getByLabelText('Search title, author, identifier, library, or format')
    await user.type(box, 'clock')
    await waitFor(() =>
      expect(router.state.location.search).toEqual({
        lens: 'needs_attention',
        preview: 'case-1',
        q: 'clock',
      }),
    )
    await user.click(screen.getByRole('button', { name: 'Clear' }))
    await waitFor(() =>
      expect(router.state.location.search).toEqual({ lens: 'needs_attention', preview: 'case-1' }),
    )
    expect(box).toHaveValue('')
  })

  it.each([
    [1, '1 field needs attention'],
    [2, '2 fields need attention'],
  ] as const)(
    'agrees the work-card attention noun and verb for %i field(s)',
    async (attentionCount, copy) => {
      const firstWork = library.works[0]
      if (!firstWork) throw new Error('library fixture requires one work')
      const http: ReviewHttp = {
        ...createHttp(),
        getLibrary: vi.fn(async () => ({ ...library, works: [{ ...firstWork, attentionCount }] })),
      }
      renderRoute('/library?lens=all', http)

      expect(await screen.findByText(copy)).toBeVisible()
    },
  )

  it('shows bounded provider absence once at the selected-work comparison boundary', async () => {
    const http: ReviewHttp = {
      ...createHttp(),
      getCaseDetail: vi.fn(async () => ({
        ...detail,
        comparisonNotices: [
          'Provider limitation: Open Library — Provider returned no matching metadata.',
        ],
      })),
    }
    renderRoute('/library?lens=all&preview=case-1&field=title', http)

    const condition = await screen.findByRole('complementary', {
      name: 'Provider comparison condition',
    })
    expect(condition).toHaveTextContent('Open Library')
    expect(condition).toHaveTextContent('Provider returned no matching metadata.')
    expect(within(condition).getAllByText(/Provider limitation/u)).toHaveLength(1)
  })

  it('keeps Variant M field, automatic, evidence, and apply context in the responsive Library workspace', async () => {
    const user = userEvent.setup()
    const titleField = detail.fields[0]
    if (!titleField) throw new Error('workspace fixture requires a title field')
    const automaticField = {
      ...titleField,
      destination: 'series',
      currentValue: 'Atlas Cycle 2.5',
      currentRaw: { name: 'Atlas Cycle', sequence: '2.5' },
      proposedValues: [],
      proposalId: 'proposal-series',
      targetFingerprint: 'fingerprint-series',
      classificationReason:
        'Automatic handling recorded this field; you may inspect or override it.',
      needsAttention: false,
      automaticDecision: {
        candidate_id: null,
        decision_id: 'automatic-series',
        destination: 'series' as const,
        occurred_at: 1,
        outcome: 'keep_current' as const,
        proposal_id: 'proposal-series',
        review_case_id: 'case-1',
        selected_value: null,
        target_fingerprint: 'fingerprint-series',
      },
      notices: ['Series already agrees with the deterministic policy.'],
    }
    const http: ReviewHttp = {
      ...createHttp(),
      getCaseDetail: vi.fn(async () => ({
        ...detail,
        fields: [
          {
            ...titleField,
            effectCapability: 'allowed',
            notices: ['competing_evidence'],
            choiceContexts: [
              {
                alternativeId: null,
                selection: 'current',
                rationale: 'Keep the value currently held by the local library.',
                recommendation: 'no_recommendation',
                recommendationReason:
                  'No deterministic recommendation is retained for the current value.',
                limitations: [],
                evidence: [
                  {
                    source: 'Local library record',
                    observation: 'Title: Old title',
                    role: 'local_presence',
                  },
                ],
              },
              {
                alternativeId: 'alternative-title-1',
                selection: 'proposed',
                rationale: 'This retained alternative is eligible for owner selection.',
                recommendation: 'no_recommendation',
                recommendationReason:
                  'No deterministic recommendation is retained for this alternative.',
                limitations: [],
                evidence: [
                  {
                    source: 'Open Library',
                    observation: 'Title: New title',
                    role: 'external_corroboration',
                  },
                ],
              },
              {
                alternativeId: 'alternative-title-preserves-target',
                selection: 'proposed',
                rationale: 'This retained alternative preserves the local value.',
                recommendation: 'no_recommendation',
                recommendationReason:
                  'No deterministic recommendation is retained for this alternative.',
                limitations: [],
                evidence: [
                  {
                    source: 'Preserved catalogue record',
                    observation: 'Title: Old title',
                    role: 'external_corroboration',
                  },
                ],
              },
            ] as const,
          },
          automaticField,
        ],
      })),
    }
    renderRoute('/library?lens=all&preview=case-1&field=title', http)

    expect(
      await screen.findByRole('button', { name: 'Choose current value: Old title' }),
    ).toBeVisible()
    expect(screen.getByText('Eligible for a library update')).toBeVisible()
    const evidenceButtons = screen.getAllByRole('button', { name: /Evidence for/u })
    const currentEvidence = evidenceButtons[0]
    const proposedEvidence = evidenceButtons[1]
    if (!currentEvidence || !proposedEvidence)
      throw new Error('choice evidence controls are missing')
    expect(currentEvidence).toHaveAccessibleName('Evidence for Current value: Old title')
    expect(proposedEvidence).toHaveAccessibleName('Evidence for Alternative 1: New title')
    await user.click(currentEvidence)
    expect(
      screen.getByRole('button', { name: 'Evidence for Current value: Old title' }),
    ).toHaveAttribute('aria-expanded', 'true')
    expect(
      screen.getByRole('button', { name: 'Evidence for Current value: Old title' }),
    ).toHaveTextContent('Hide evidence')
    expect(await screen.findByText('Local library record')).toBeVisible()
    expect(screen.getByText('Title: Old title')).toBeVisible()
    expect(screen.getByText('Local presence')).toBeVisible()
    expect(screen.getAllByText('No recommendation retained').length).toBeGreaterThan(0)
    expect(screen.queryByText('Source ledger')).not.toBeInTheDocument()
    await user.click(proposedEvidence)
    expect(await screen.findByText('Open Library')).toBeVisible()
    expect(screen.getByText('Title: New title')).toBeVisible()
    expect(screen.getByText('External corroboration')).toBeVisible()
    expect(screen.queryByText('Preserved catalogue record')).not.toBeInTheDocument()
    expect(screen.getAllByText('Competing evidence').length).toBeGreaterThan(0)
    expect(screen.queryByText('Different source tag lists')).toBeNull()
    const fieldRail = screen.getByRole('navigation', { name: 'Review fields' })
    expect(
      within(fieldRail).getByRole('button', { name: /Series.*Resolved automatically/u }),
    ).toBeVisible()
  })

  it.each(
    [
      { sources: ['Hardcover'], current: null, human: false, explained: true },
      { sources: ['Hardcover', 'Open Library'], current: [], human: false, explained: true },
      { sources: [], current: null, human: false, explained: false },
      { sources: ['Hardcover'], current: ['Owner series'], human: false, explained: false },
      { sources: ['Hardcover'], current: null, human: true, explained: false },
    ].flatMap((scenario) =>
      [false, true].flatMap((editable) =>
        ['2', null].map((sequence) => ({ ...scenario, editable, sequence })),
      ),
    ),
  )(
    'explains only the recorded automatic empty-series fill: %j',
    async ({ sources, current, human, explained, editable, sequence }) => {
      const titleField = detail.fields[0]
      if (!titleField) throw new Error('workspace fixture requires a title field')
      const candidate = { kind: 'series' as const, value: [{ name: 'New series', sequence }] }
      const series = {
        ...titleField,
        automaticDecision: {
          candidate_id: 'alternative-series-1',
          decision_id: 'automatic-series',
          destination: 'series' as const,
          occurred_at: 1,
          outcome: 'candidate' as const,
          proposal_id: 'proposal-series',
          review_case_id: 'case-1',
          selected_value: candidate,
          target_fingerprint: 'fingerprint-series',
        },
        choiceContexts: [
          {
            alternativeId: null,
            selection: 'current' as const,
            rationale: 'Current series evidence.',
            recommendation: 'no_recommendation' as const,
            recommendationReason: 'Current series evidence.',
            limitations: [],
            evidence: [
              { source: 'Current source', observation: 'Old series', role: 'local_presence' },
            ],
          },
          {
            alternativeId: 'alternative-series-1',
            selection: 'proposed' as const,
            rationale: 'Selected automatic series evidence.',
            recommendation: 'recommended' as const,
            recommendationReason: 'Selected automatic series evidence.',
            limitations: [],
            evidence: [
              {
                source: 'Candidate source',
                observation: 'New series',
                role: 'external_corroboration',
              },
            ],
          },
        ] as const,
        automaticSelectionSources: sources,
        humanDecision: human
          ? {
              actor: 'owner',
              application: 'recorded_only' as const,
              decision_id: 'human-series',
              destination: 'series' as const,
              occurred_at: 2,
              outcome: 'accept' as const,
              proposal_id: 'proposal-series',
              review_case_id: 'case-1',
              selected_value: candidate,
              target_fingerprint: 'fingerprint-series',
            }
          : null,
        currentValue: current?.length ? 'Owner series' : '',
        currentRaw: current,
        decisionActions: editable ? ['keep_current' as const, 'accept' as const] : [],
        editable,
        destination: 'series',
        needsAttention: false,
        navigationRole: 'resolved' as const,
        proposedValues: [candidate],
        proposalId: 'proposal-series',
        targetFingerprint: 'fingerprint-series',
      }
      renderRoute('/library?lens=all&preview=case-1&field=series', {
        ...createHttp(),
        getCaseDetail: vi.fn(async () => ({ ...detail, fields: [series] })),
      })

      expect(await screen.findByRole('heading', { name: 'Series' })).toBeVisible()
      if (!editable) expect(screen.getByText('Chosen value')).toBeVisible()
      expect(screen.getAllByText(/New series/u).length).toBeGreaterThan(0)
      if (explained) {
        expect(
          screen.getByText(
            `Selected from ${sources.join(' and ')} to fill an empty Series field under a qualified automatic policy. ${sequence === null ? 'No series number was supplied.' : 'Numbering is supplied by the provider and is not independently verified for this book.'}`,
          ),
        ).toBeVisible()
      } else {
        expect(screen.queryByText(/under a qualified automatic policy/u)).toBeNull()
      }
      if (!editable) expect(screen.queryByText('Current value')).toBeNull()
      await userEvent.setup().click(
        screen.getByRole('button', {
          name: editable ? /Evidence for Alternative 1/u : 'Evidence',
        }),
      )
      expect(await screen.findByText('Candidate source')).toBeVisible()
      expect(screen.queryByText('Current source')).toBeNull()
      if (!editable) expect(screen.queryByRole('button', { name: /Choose /u })).toBeNull()
    },
  )

  it.each([
    ['m4b', true],
    ['m4a', true],
    ['mp3', true],
    ['epub', true],
    ['unknown', true],
    ['m4b', false],
  ] as const)(
    'uses neutral description alternatives only for a validated audiobook selection (%s, %s)',
    async (mediaKind, selected) => {
      const base = detail.fields[0]
      if (!base) throw new Error('workspace fixture requires a field')
      const candidate = { kind: 'text' as const, value: 'A safe regional synopsis.' }
      const field = {
        ...base,
        destination: 'description' as const,
        humanDecision: null,
        notices: ['competing_evidence'],
        currentRaw: null,
        currentValue: '',
        automaticDecision: selected
          ? {
              candidate_id: 'description-1',
              decision_id: 'automatic-description',
              destination: 'description' as const,
              occurred_at: 1,
              outcome: 'candidate' as const,
              proposal_id: base.proposalId,
              review_case_id: 'case-1',
              selected_value: candidate,
              target_fingerprint: base.targetFingerprint,
            }
          : null,
        proposedValues: [
          candidate,
          { kind: 'text' as const, value: 'Another regional description.' },
        ],
      }
      renderRoute('/library?lens=all&preview=case-1&field=description', {
        ...createHttp(),
        getCaseDetail: vi.fn(async () => ({
          ...detail,
          target: { ...detail.target, mediaKind },
          fields: [field],
        })),
      })
      const neutral = selected && ['m4b', 'm4a', 'mp3'].includes(mediaKind)
      expect(
        await screen.findByText(neutral ? 'Different source descriptions' : 'Competing evidence'),
      ).toBeVisible()
      expect(
        screen.queryByText(neutral ? 'Competing evidence' : 'Different source descriptions'),
      ).toBeNull()
    },
  )

  it('does not infer a quality check when only one of two descriptions is applicable', async () => {
    const titleField = detail.fields[0]
    if (!titleField) throw new Error('workspace fixture requires a title field')
    const candidate = {
      kind: 'text' as const,
      value: 'One short but otherwise valid description.',
    }
    const lowerAuthorityCandidate = {
      kind: 'text' as const,
      value:
        'A different complete description with enough bounded plain text to satisfy the automatic quality floor.',
    }
    const description = {
      ...titleField,
      automaticSelectionSources: ['Hardcover'],
      automaticDecision: {
        candidate_id: 'alternative-description-1',
        decision_id: 'automatic-description',
        destination: 'description' as const,
        occurred_at: 1,
        outcome: 'candidate' as const,
        proposal_id: 'proposal-description',
        review_case_id: 'case-1',
        selected_value: candidate,
        target_fingerprint: 'fingerprint-description',
      },
      choiceContexts: [
        {
          alternativeId: 'alternative-description-1',
          selection: 'proposed' as const,
          rationale: 'Selected automatic description evidence.',
          recommendation: 'recommended' as const,
          recommendationReason: 'Selected automatic description evidence.',
          limitations: [],
          evidence: [
            {
              source: 'Hardcover',
              observation: candidate.value,
              role: 'external_corroboration' as const,
            },
            {
              source: 'Google Books',
              observation: 'Retained but not decisive evidence.',
              role: 'external_corroboration' as const,
            },
          ],
        },
      ],
      currentValue: '',
      currentRaw: '',
      decisionActions: [],
      editable: false,
      destination: 'description' as const,
      needsAttention: false,
      navigationRole: 'resolved' as const,
      proposedValues: [candidate, lowerAuthorityCandidate],
      proposalId: 'proposal-description',
      targetFingerprint: 'fingerprint-description',
    }
    renderRoute('/library?lens=all&preview=case-1&field=description', {
      ...createHttp(),
      getCaseDetail: vi.fn(async () => ({ ...detail, fields: [description] })),
    })

    expect(
      await screen.findByText(
        'Selected automatically from Hardcover after matching the target work.',
      ),
    ).toBeVisible()
    expect(screen.queryByText(/description quality checks/u)).toBeNull()
  })

  it('does not claim the multi-description quality floor for a single candidate', async () => {
    const titleField = detail.fields[0]
    if (!titleField) throw new Error('workspace fixture requires a title field')
    const candidate = {
      kind: 'text' as const,
      value: 'One short but otherwise valid description.',
    }
    const description = {
      ...titleField,
      automaticDecision: {
        candidate_id: 'alternative-description-1',
        decision_id: 'automatic-description',
        destination: 'description' as const,
        occurred_at: 1,
        outcome: 'candidate' as const,
        proposal_id: 'proposal-description',
        review_case_id: 'case-1',
        selected_value: candidate,
        target_fingerprint: 'fingerprint-description',
      },
      automaticSelectionSources: ['Hardcover'],
      currentValue: '',
      currentRaw: '',
      decisionActions: [],
      editable: false,
      destination: 'description' as const,
      needsAttention: false,
      navigationRole: 'resolved' as const,
      proposedValues: [candidate],
      proposalId: 'proposal-description',
      targetFingerprint: 'fingerprint-description',
    }
    renderRoute('/library?lens=all&preview=case-1&field=description', {
      ...createHttp(),
      getCaseDetail: vi.fn(async () => ({ ...detail, fields: [description] })),
    })

    expect((await screen.findAllByText('Resolved automatically')).at(-1)).toBeVisible()
    expect(screen.queryByText(/after matching the target work/u)).toBeNull()
  })

  it('shows the provider-consensus title winner and matched pattern beside its selected value', async () => {
    const titleField = detail.fields[0]
    if (!titleField) throw new Error('workspace fixture requires a title field')
    const candidate = titleField.proposedValues[0]
    if (!candidate) throw new Error('workspace fixture requires a proposed title')
    const title = {
      ...titleField,
      automaticDecision: {
        candidate_id: 'alternative-title-1',
        decision_id: 'automatic-title',
        destination: 'title' as const,
        occurred_at: 1,
        outcome: 'candidate' as const,
        proposal_id: titleField.proposalId,
        review_case_id: 'case-1',
        selected_value: candidate,
        target_fingerprint: titleField.targetFingerprint,
      },
      classification: 'automatic_recorded' as const,
      decisionActions: [],
      editable: false,
      needsAttention: false,
      navigationRole: 'resolved' as const,
      notices: ['provider_consensus_title_bracket_source_tag'],
    }
    renderRoute('/library?lens=all&preview=case-1&field=title', {
      ...createHttp(),
      getCaseDetail: vi.fn(async () => ({ ...detail, fields: [title] })),
    })

    const notice = await screen.findByRole('complementary', {
      name: 'Comparison condition',
    })
    expect(notice).toHaveTextContent(
      'Independent providers agree after removing the bracketed source tag',
    )
    const canvas = notice.closest<HTMLElement>('.decision-canvas')
    if (!canvas) throw new Error('provider-consensus notice is outside its selected field')
    expect(within(canvas).getByText('New title')).toBeVisible()
  })

  it('states the decoration rule that tidied the title to its plain form', async () => {
    const titleField = detail.fields[0]
    if (!titleField) throw new Error('workspace fixture requires a title field')
    const candidate = titleField.proposedValues[0]
    if (!candidate) throw new Error('workspace fixture requires a proposed title')
    const title = {
      ...titleField,
      automaticDecision: {
        candidate_id: 'alternative-title-1',
        decision_id: 'automatic-title',
        destination: 'title' as const,
        occurred_at: 1,
        outcome: 'candidate' as const,
        proposal_id: titleField.proposalId,
        review_case_id: 'case-1',
        selected_value: candidate,
        target_fingerprint: titleField.targetFingerprint,
      },
      classification: 'automatic_recorded' as const,
      decisionActions: [],
      editable: false,
      needsAttention: false,
      navigationRole: 'resolved' as const,
      notices: ['decoration_tidy_bracket_source_tag'],
    }
    renderRoute('/library?lens=all&preview=case-1&field=title', {
      ...createHttp(),
      getCaseDetail: vi.fn(async () => ({ ...detail, fields: [title] })),
    })

    const notice = await screen.findByRole('complementary', {
      name: 'Comparison condition',
    })
    expect(notice).toHaveTextContent(
      'Providers agree once the bracketed source tag is removed; the current title is tidied to the plain form',
    )
  })

  it('states the decoration rule that kept an equivalent title unchanged', async () => {
    const titleField = detail.fields[0]
    if (!titleField) throw new Error('workspace fixture requires a title field')
    const title = {
      ...titleField,
      automaticDecision: {
        candidate_id: null,
        decision_id: 'automatic-title-keep',
        destination: 'title' as const,
        occurred_at: 1,
        outcome: 'keep_current' as const,
        proposal_id: titleField.proposalId,
        review_case_id: 'case-1',
        selected_value: null,
        target_fingerprint: titleField.targetFingerprint,
      },
      classification: 'automatic_recorded' as const,
      decisionActions: [],
      editable: false,
      needsAttention: false,
      navigationRole: 'resolved' as const,
      notices: ['decoration_equivalent_subtitle_segment'],
    }
    renderRoute('/library?lens=all&preview=case-1&field=title', {
      ...createHttp(),
      getCaseDetail: vi.fn(async () => ({ ...detail, fields: [title] })),
    })

    const notice = await screen.findByRole('complementary', {
      name: 'Comparison condition',
    })
    expect(notice).toHaveTextContent(
      'Providers agree once the colon subtitle is removed; the current title is kept',
    )
  })

  it('states the segment guard that withheld an automatic subtitle fill', async () => {
    const titleField = detail.fields[0]
    if (!titleField) throw new Error('workspace fixture requires a title field')
    const subtitle = {
      ...titleField,
      destination: 'subtitle',
      proposalId: 'proposal-subtitle',
      targetFingerprint: 'fingerprint-subtitle',
      notices: ['subtitle_segment_provider_dissent', 'subtitle_segment_decorated_consensus'],
    }
    renderRoute('/library?lens=all&preview=case-1&field=subtitle', {
      ...createHttp(),
      getCaseDetail: vi.fn(async () => ({ ...detail, fields: [subtitle] })),
    })

    const notice = await screen.findByRole('complementary', {
      name: 'Comparison condition',
    })
    expect(notice).toHaveTextContent(
      'Another provider states a different subtitle; choose the subtitle yourself',
    )
    expect(notice).toHaveTextContent(
      'The colon segment reads as title text: no provider carries it, or no more providers state the plain title than carry the decorated form',
    )
  })

  it.each([null, 'keep_current', 'reject'] as const)(
    'states the provider-chosen contributor display only without an owner outcome: %s',
    async (outcome) => {
      const base = detail.fields[0]
      if (!base) throw new Error('workspace fixture requires a field')
      const candidate = { kind: 'list' as const, value: ['Ada Example'] }
      const authors = {
        ...base,
        automaticDecision: {
          candidate_id: 'alternative-authors-1',
          decision_id: 'automatic-authors',
          destination: 'authors' as const,
          occurred_at: 1,
          outcome: 'candidate' as const,
          proposal_id: base.proposalId,
          review_case_id: 'case-1',
          selected_value: candidate,
          target_fingerprint: base.targetFingerprint,
        },
        currentValue: 'Example, Ada',
        destination: 'authors' as const,
        humanDecision:
          outcome === null
            ? null
            : {
                actor: 'owner',
                application: 'apply' as const,
                decision_id: 'owner-authors',
                destination: 'authors',
                occurred_at: 2,
                outcome,
                proposal_id: base.proposalId,
                review_case_id: 'case-1',
                selected_value: null,
                target_fingerprint: base.targetFingerprint,
              },
        decisionActions: [],
        editable: false,
        needsAttention: false,
        navigationRole: 'resolved' as const,
        notices: ['provider_consensus_contributor_display'],
        proposedValues: [candidate],
      }
      renderRoute('/library?lens=all&preview=case-1&field=authors', {
        ...createHttp(),
        getCaseDetail: vi.fn(async () => ({ ...detail, fields: [authors] })),
      })

      const recorded = await screen.findByRole('region', { name: 'Authors recorded outcome' })
      if (outcome === null) {
        expect(recorded).toHaveTextContent(
          'Providers chose Ada Example; normalized from Example, Ada.',
        )
        expect(recorded).toHaveTextContent('Chosen value')
      } else {
        expect(recorded).not.toHaveTextContent('Providers chose')
        expect(recorded.querySelector('dd')).toHaveTextContent('Example, Ada')
      }
    },
  )

  it.each([
    { ownerTags: [], mediaKind: 'm4b' },
    { ownerTags: ['Owner tag'], mediaKind: 'm4b' },
    { ownerTags: ['Owner tag'], mediaKind: 'm4a' },
    { ownerTags: ['Owner tag'], mediaKind: 'mp3' },
    { ownerTags: ['Owner tag'], mediaKind: 'ebook' },
    { ownerTags: ['Owner tag'], mediaKind: 'epub' },
    { ownerTags: ['Owner tag'], mediaKind: 'unknown' },
    { ownerTags: ['Owner tag'], mediaKind: 'audiobook' },
  ])(
    'shows combined tags only for $mediaKind with $ownerTags',
    async ({ ownerTags, mediaKind }) => {
      const base = detail.fields[0]
      if (!base) throw new Error('workspace fixture requires a field')
      const sourceLists = [
        ['First', 'Shared'],
        ['Shared', 'Second'],
      ]
      const field = {
        ...base,
        destination: 'tags' as const,
        notices: ['competing_evidence'],
        currentValue: ownerTags.join(', ') || 'Not set',
        currentRaw: ownerTags,
        automaticDecision: {
          candidate_id: 'derived-tags',
          decision_id: 'automatic-tags',
          destination: 'tags' as const,
          occurred_at: 1,
          outcome: 'candidate' as const,
          proposal_id: base.proposalId,
          review_case_id: 'case-1',
          selected_value: {
            kind: 'list' as const,
            value: [...ownerTags, 'First', 'Shared', 'Second'],
          },
          target_fingerprint: base.targetFingerprint,
        },
        humanDecision: null,
        decisionActions: ['keep_current', 'accept'] as const,
        editable: true,
        needsAttention: false,
        navigationRole: 'resolved' as const,
        proposedValues: sourceLists.map((value) => ({ kind: 'list' as const, value })),
        choiceContexts: sourceLists.map((value, index) => ({
          alternativeId: `source-tags-${String(index)}`,
          evidence: value.map((tag) => ({
            observation: `Metadata: ${tag}`,
            role: 'external_corroboration' as const,
            source: `Source record ${String(index + 1)}`,
          })),
          limitations: [],
          rationale: 'Complete source tag list.',
          recommendation: 'no_recommendation' as const,
          recommendationReason: 'Source alternative retained.',
          selection: 'proposed' as const,
        })),
      }
      renderRoute('/library?lens=all&preview=case-1&field=tags', {
        ...createHttp(),
        getCaseDetail: vi.fn(async () => ({
          ...detail,
          target: { ...detail.target, mediaKind },
          fields: [field],
        })),
      })
      await screen.findByRole('region', { name: 'Tags recorded choices' })
      expect(
        screen.getByRole('button', { name: 'Choose proposed value: First · Shared' }),
      ).toBeVisible()
      expect(
        screen.getByRole('button', { name: 'Choose proposed value: Shared · Second' }),
      ).toBeVisible()
      if (['ebook', 'epub', 'unknown', 'audiobook'].includes(mediaKind)) {
        expect(screen.queryByRole('region', { name: 'Tags recorded outcome' })).toBeNull()
        expect(screen.getByText('Competing evidence')).toBeVisible()
        expect(screen.queryByText('Different source tag lists')).toBeNull()
        expect(
          screen.queryByText('Compatible source tags were combined, retaining any existing tags.'),
        ).toBeNull()
        return
      }
      const recorded = await screen.findByRole('region', { name: 'Tags recorded outcome' })
      expect(recorded).toHaveTextContent('First · Shared · Second')
      if (ownerTags.length) expect(recorded).toHaveTextContent('Owner tag')
      expect(recorded).toHaveTextContent('Compatible source tags were combined')
      expect(screen.getByText('Different source tag lists')).toBeVisible()
      expect(screen.queryByText('Competing evidence')).toBeNull()
      await userEvent.click(within(recorded).getByRole('button', { name: 'Evidence' }))
      expect(recorded).toHaveTextContent('Source record 1')
      expect(recorded).toHaveTextContent('Source record 2')
      expect(recorded).toHaveTextContent('Metadata: First')
      expect(recorded).toHaveTextContent('Metadata: Second')
    },
  )

  it('keeps the provider-consensus contributor statement visible while revisiting choices', async () => {
    const base = detail.fields[0]
    if (!base) throw new Error('workspace fixture requires a field')
    const candidate = { kind: 'list' as const, value: ['Ada Example'] }
    const authors = {
      ...base,
      automaticDecision: {
        candidate_id: 'alternative-authors-1',
        decision_id: 'automatic-authors',
        destination: 'authors' as const,
        occurred_at: 1,
        outcome: 'candidate' as const,
        proposal_id: base.proposalId,
        review_case_id: 'case-1',
        selected_value: candidate,
        target_fingerprint: base.targetFingerprint,
      },
      currentValue: 'Example, Ada',
      destination: 'authors' as const,
      decisionActions: ['keep_current', 'accept'] as const,
      editable: true,
      needsAttention: false,
      navigationRole: 'resolved' as const,
      notices: ['provider_consensus_contributor_display'],
      proposedValues: [candidate],
    }
    renderRoute('/library?lens=all&preview=case-1&field=authors', {
      ...createHttp(),
      getCaseDetail: vi.fn(async () => ({ ...detail, fields: [authors] })),
    })
    expect(
      await screen.findByText('Providers chose Ada Example; normalized from Example, Ada.'),
    ).toBeVisible()
    expect(screen.getByRole('button', { name: /Choose current value/u })).toBeVisible()
  })

  it('explains the deterministic identity evidence for a recorded ISBN outcome', async () => {
    const base = detail.fields[0]
    if (!base) throw new Error('workspace fixture requires a field')
    const candidate = { kind: 'text' as const, value: '9780306406157' }
    const isbn = {
      ...base,
      automaticDecision: {
        candidate_id: 'alternative-isbn-1',
        decision_id: 'automatic-isbn',
        destination: 'isbn' as const,
        occurred_at: 1,
        outcome: 'candidate' as const,
        proposal_id: base.proposalId,
        review_case_id: 'case-1',
        selected_value: candidate,
        target_fingerprint: base.targetFingerprint,
      },
      destination: 'isbn' as const,
      decisionActions: [],
      editable: false,
      needsAttention: false,
      navigationRole: 'resolved' as const,
      notices: ['verified_manifestation_isbn'],
      proposedValues: [candidate],
    }
    renderRoute('/library?lens=all&preview=case-1&field=isbn', {
      ...createHttp(),
      getCaseDetail: vi.fn(async () => ({ ...detail, fields: [isbn] })),
    })

    const recorded = await screen.findByRole('region', { name: 'Isbn recorded outcome' })
    expect(recorded).toHaveTextContent(
      "Deterministic identity evidence bound this ISBN to this file's work.",
    )
    expect(recorded).toHaveTextContent('Chosen value')
  })

  it('does not present automatic ISBN authority beside an owner-final override', async () => {
    const base = detail.fields[0]
    if (!base) throw new Error('workspace fixture requires a field')
    const automaticValue = { kind: 'text' as const, value: '9780306406157' }
    const ownerValue = { kind: 'text' as const, value: '9780000000002' }
    const isbn = {
      ...base,
      automaticDecision: {
        candidate_id: 'alternative-isbn-1',
        decision_id: 'automatic-isbn',
        destination: 'isbn' as const,
        occurred_at: 1,
        outcome: 'candidate' as const,
        proposal_id: base.proposalId,
        review_case_id: 'case-1',
        selected_value: automaticValue,
        target_fingerprint: base.targetFingerprint,
      },
      destination: 'isbn' as const,
      decisionActions: [],
      editable: false,
      humanDecision: {
        actor: 'owner',
        application: 'apply' as const,
        decision_id: 'decision-isbn',
        destination: 'isbn' as const,
        occurred_at: 2,
        outcome: 'accept' as const,
        proposal_id: base.proposalId,
        review_case_id: 'case-1',
        selected_value: ownerValue,
        target_fingerprint: base.targetFingerprint,
      },
      needsAttention: false,
      navigationRole: 'resolved' as const,
      notices: ['verified_manifestation_isbn'],
      proposedValues: [automaticValue, ownerValue],
    }
    renderRoute('/library?lens=all&preview=case-1&field=isbn', {
      ...createHttp(),
      getCaseDetail: vi.fn(async () => ({ ...detail, fields: [isbn] })),
    })

    const recorded = await screen.findByRole('region', { name: 'Isbn recorded outcome' })
    expect(recorded).toHaveTextContent('Chosen value')
    expect(recorded).toHaveTextContent('9780000000002')
    expect(recorded).not.toHaveTextContent(
      "Deterministic identity evidence bound this ISBN to this file's work.",
    )
  })

  it.each([
    { outcome: 'keep_current' as const, currentValue: 'Unavailable', currentRaw: null },
    { outcome: 'reject' as const, currentValue: 'Unavailable', currentRaw: null },
    { outcome: 'keep_current' as const, currentValue: 'Old title', currentRaw: 'Old title' },
    { outcome: 'reject' as const, currentValue: 'Old title', currentRaw: 'Old title' },
  ])(
    'keeps the nullable owner outcome final when automatic authority remains in history: %j',
    async ({ outcome, currentValue, currentRaw }) => {
      const base = detail.fields[0]
      if (!base) throw new Error('owner-final fixture requires a field')
      const field = {
        ...base,
        automaticDecision: {
          candidate_id: 'alternative-title-1',
          decision_id: 'automatic-title',
          destination: 'title' as const,
          occurred_at: 1,
          outcome: 'candidate' as const,
          proposal_id: base.proposalId,
          review_case_id: 'case-1',
          selected_value: base.proposedValues[0] ?? null,
          target_fingerprint: base.targetFingerprint,
        },
        decisionActions: [],
        editable: false,
        currentValue,
        currentRaw,
        humanDecision: {
          actor: 'owner',
          application: 'apply' as const,
          decision_id: 'decision-title',
          destination: 'title',
          occurred_at: 2,
          outcome,
          proposal_id: base.proposalId,
          review_case_id: 'case-1',
          selected_value: null,
          target_fingerprint: base.targetFingerprint,
        },
        needsAttention: false,
        navigationRole: 'resolved' as const,
      }
      renderRoute('/library?lens=all&preview=case-1&field=title', {
        ...createHttp(),
        getCaseDetail: vi.fn(async () => ({ ...detail, fields: [field] })),
      })

      const recorded = await screen.findByRole('region', { name: 'Title recorded outcome' })
      expect(recorded).toHaveTextContent(
        outcome === 'keep_current' ? 'Current value retained' : 'No action recorded',
      )
      expect(recorded.querySelector('dd')).toHaveTextContent(currentValue)
      expect(recorded).not.toHaveTextContent('New title')
      expect(recorded).not.toHaveTextContent('Resolved automatically')
      const automaticCount = screen.getByText('Resolved automatically').closest('div')
      expect(automaticCount).toHaveTextContent('0')
    },
  )

  it('keeps empty series in completion and revisited choices after an automatic candidate is overridden', async () => {
    const user = userEvent.setup()
    const base = detail.fields[0]
    if (!base) throw new Error('owner override fixture requires a field')
    const candidate = {
      kind: 'series' as const,
      value: [{ name: 'Reading Order', sequence: null }],
    }
    const series = {
      ...base,
      destination: 'series',
      currentRaw: null,
      currentValue: 'Unavailable',
      automaticSelectionSources: ['Hardcover'],
      proposedValues: [candidate],
      decisionActions: ['keep_current' as const, 'accept' as const],
      classification: 'automatic_recorded' as const,
      needsAttention: false,
      navigationRole: 'resolved' as const,
      editable: true,
      automaticDecision: {
        candidate_id: 'alternative-series-1',
        decision_id: 'automatic-series',
        destination: 'series' as const,
        occurred_at: 1,
        outcome: 'candidate' as const,
        proposal_id: base.proposalId,
        review_case_id: 'case-1',
        selected_value: candidate,
        target_fingerprint: base.targetFingerprint,
      },
    }
    const owner = {
      actor: 'owner',
      application: 'apply' as const,
      decision_id: 'owner-series',
      destination: 'series',
      occurred_at: 2,
      outcome: 'keep_current' as const,
      proposal_id: base.proposalId,
      review_case_id: 'case-1',
      selected_value: null,
      target_fingerprint: base.targetFingerprint,
    }
    let current: CaseDetailView = { ...detail, fields: [series] }
    const http: ReviewHttp = {
      ...createHttp(),
      getCaseDetail: vi.fn(async () => current),
      recordDecision: vi.fn(async () => {
        current = { ...detail, fields: [{ ...series, humanDecision: owner }] }
        return {
          confirmation: { status: 'confirmed' as const, case: current },
          ...owner,
          schema_version: 'review-decision-v1',
          invalidation_sequences: [],
        }
      }),
    }
    renderRoute('/library?lens=all&preview=case-1&field=series', http)
    expect(await screen.findByText(/No series number was supplied/u)).toBeVisible()
    await user.click(screen.getByRole('button', { name: 'Choose current value: Unavailable' }))
    await screen.findByRole('heading', { name: 'Review complete' })
    const completed = screen.getByRole('button', { name: /Series · Current value retained/u })
    expect(completed).toHaveTextContent('Unavailable')
    expect(completed).not.toHaveTextContent('Reading Order')
    await user.click(completed)
    expect(
      screen.getByRole('button', { name: 'Choose current value: Unavailable' }),
    ).toHaveAttribute('aria-pressed', 'true')
    expect(screen.queryByText(/No series number was supplied/u)).toBeNull()
    await user.click(screen.getByRole('button', { name: '← Back to completion' }))
    expect(
      screen.getByRole('button', { name: /Series · Current value retained/u }),
    ).toHaveTextContent('Unavailable')
  })

  it('opens a long description without recording a choice', async () => {
    const user = userEvent.setup()
    const description =
      'A complete retained paragraph.\r\n\r\nEscaped <text> & a long word: '.repeat(8) +
      'unbroken'.repeat(50)
    const titleField = detail.fields[0]
    if (!titleField) throw new Error('description fixture requires a field')
    const field = { ...titleField, destination: 'description', currentValue: description }
    const http = {
      ...createHttp(),
      getCaseDetail: vi.fn(async () => ({ ...detail, fields: [field] })),
    }
    renderRoute('/library?lens=all&preview=case-1&field=description', http)

    const disclosure = await screen.findByText('Read full description')
    await user.click(disclosure)
    expect(screen.getAllByText((_, node) => node?.textContent === description)).toHaveLength(1)
    disclosure.focus()
    await user.keyboard('{Enter}')
    expect(http.recordDecision).not.toHaveBeenCalled()

    const fullValue = screen.getByRole('button', { name: 'Choose current description' })
    await user.click(fullValue)
    await waitFor(() => expect(http.recordDecision).toHaveBeenCalledOnce())
  })

  it.each(['epub', 'm4b'])('retains %s cover checks', async (mediaKind) => {
    const user = userEvent.setup()
    const base = detail.fields[0]
    if (!base) throw new Error('cover fixture requires a field')
    const reference = (index: number) => `cover_transport_ref_${String(index).repeat(64)}`
    const observation = (index: number) => ({
      candidateReference: reference(index),
      composition: 'minimal' as const,
      coverTextLanguage: 'english' as const,
      explicitImagery: 'absent' as const,
      formatBranding: 'absent' as const,
      medium: 'illustrated' as const,
      overlayMarking: 'absent' as const,
      placeholder: 'absent' as const,
      previewSha256: String(index).repeat(64),
      previewVariant: 'cover-preview-v1',
      promotionalText: 'absent' as const,
      reproduction: 'clean' as const,
      seriesStyling: 'standalone' as const,
      thumbnailLegibility: 'legible' as const,
      tieInBranding: 'absent' as const,
    })
    const candidate = (index: number, incumbent = false) => ({
      artifactId: `cover_${String(index).repeat(64)}`,
      deterministicCitations: [`citation-${String(index)}`],
      deterministicRank: incumbent ? null : index - 1,
      deterministicRankKey: [1, 0, -960000, -800, -1200, index, reference(index)],
      height: 1200,
      identity: { outcome: 'verified_work_match' },
      incumbent,
      matchState: 'same_book' as const,
      observation: observation(index),
      originalSha256: String(index).repeat(64),
      ordinal: index,
      previewSha256: String(index).repeat(64),
      reference: reference(index),
      selectable: !incumbent,
      manualEligible: !incumbent,
      sourceKinds: [incumbent ? 'audiobookshelf_current' : 'provider'],
      state: incumbent ? 'incumbent' : 'acceptable',
      width: 800,
    })
    const consideration = {
      id: `cover_consideration_${'c'.repeat(64)}`,
      choiceContexts: [
        {
          alternativeId: null,
          selection: 'current',
          evidence: [],
          limitations: [],
          rationale: 'Keep the local cover.',
          recommendation: 'no_recommendation',
          recommendationReason: 'No recommendation retained.',
        },
        {
          alternativeId: reference(2),
          selection: 'proposed',
          evidence: [
            {
              source: 'Bound provider candidate',
              role: 'external_corroboration',
              observation: 'Confirmed selectable cover evidence.',
            },
          ],
          limitations: [],
          rationale: 'Selectable retained cover.',
          recommendation: 'no_recommendation',
          recommendationReason: 'No recommendation retained.',
        },
        {
          alternativeId: reference(3),
          selection: 'proposed',
          evidence: [],
          limitations: [],
          rationale: 'Selectable retained cover.',
          recommendation: 'recommended',
          recommendationReason: 'Advisory context recommends this candidate.',
        },
        {
          alternativeId: reference(4),
          selection: 'proposed',
          evidence: [],
          limitations: [],
          rationale: 'Selectable retained cover.',
          recommendation: 'no_recommendation',
          recommendationReason: 'No recommendation retained.',
        },
        {
          alternativeId: reference(5),
          selection: 'proposed',
          evidence: [
            {
              source: 'Unconfirmed provider candidate',
              role: 'external_corroboration',
              observation: 'Retained evidence without a confirmed target match.',
            },
          ],
          limitations: ['insufficient_identity_evidence'],
          rationale: 'This retained candidate requires explicit owner confirmation.',
          recommendation: 'no_recommendation',
          recommendationReason: 'No recommendation retained.',
        },
      ],
      current: true,
      candidates: [
        candidate(1, true),
        candidate(2),
        candidate(3),
        candidate(4),
        {
          ...candidate(5),
          deterministicRank: null,
          deterministicRankKey: null,
          matchState: 'match_not_confirmed' as const,
          selectable: false,
          manualEligible: true,
          identity: { outcome: 'unknown', reasons: ['insufficient_identity_evidence'] },
          observation: {
            ...observation(5),
            composition: 'dense' as const,
            formatBranding: 'audiobook' as const,
            medium: 'photographic' as const,
          },
        },
        {
          ...candidate(6),
          deterministicRank: null,
          deterministicRankKey: null,
          matchState: 'match_not_confirmed' as const,
          selectable: false,
          manualEligible: false,
          identity: { outcome: 'conflict', reasons: ['identity_conflict'] },
          observation: {
            ...observation(6),
            composition: 'dense' as const,
            formatBranding: 'text_edition' as const,
            medium: 'photographic' as const,
            overlayMarking: 'present' as const,
            promotionalText: 'present' as const,
            reproduction: 'degraded' as const,
          },
        },
      ],
      incumbent: { reference: reference(1), state: 'available' as const },
      omissions: [{ reason: 'preview_unavailable', reference: 'retained-unavailable-source' }],
      policy: {
        recommended_candidate_reference: reference(3),
        acceptable_candidate_references: [reference(2), reference(3), reference(4)],
        status: 'abstained',
        reasons: ['observations_ambiguous'],
      },
      policyPreferences: {
        avoidPromotionalText: true,
        avoidTieInBranding: false,
        preferMinimalComposition: false,
      },
      policyReference: `cover_policy_${'3'.repeat(64)}`,
      providerFetching: true,
      ready: false,
      reviewerAccessRevision: null,
      reviewerEngine: null,
      reviewerModel: null,
      reviewerStatus: 'completed',
      reviewerUnavailableReason: null,
      targetFingerprint: 'fingerprint-cover',
    } as const
    const coverField = {
      ...base,
      destination: 'cover',
      decisionActions: [],
      editable: false,
      needsAttention: false,
      currentValue: `cover_${'1'.repeat(64)}`,
      currentRaw: `cover_${'1'.repeat(64)}`,
      proposalId: 'proposal-cover',
      targetFingerprint: 'fingerprint-cover',
      proposedValues: [{ kind: 'cover', value: `cover_${'2'.repeat(64)}` }],
      coverConsideration: consideration,
    }
    const http: ReviewHttp = {
      ...createHttp(),
      getCaseDetail: vi.fn(async () => ({
        ...detail,
        target: { ...detail.target, mediaKind },
        fields: [coverField],
      })),
    }
    renderRoute('/library?lens=all&preview=case-1&field=cover', http)

    const comparison = await screen.findByLabelText('Choose a cover')
    expect(
      within(comparison).getByText(
        'Retained covers remain visible. Choices become available when fetching pauses.',
      ),
    ).toBeVisible()
    expect(within(comparison).queryByText(/Recommended/u)).not.toBeInTheDocument()
    expect(comparison.querySelectorAll('.library-cover-card:disabled')).toHaveLength(6)
    expect(screen.queryByText(/1 remaining/u)).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Cover Needs review' })).not.toBeInTheDocument()
    expect(http.recordDecision).not.toHaveBeenCalled()
    const cleanChecks = within(comparison).getByLabelText('Current cover visual checks')
    expect(within(cleanChecks).getByText('No visual defects flagged')).toBeVisible()
    await user.click(within(cleanChecks).getByText('Visual observation details'))
    const defectHeading = within(cleanChecks).getByText('Defect checks')
    const preferenceHeading = within(cleanChecks).getByText('Style and advisory notes')
    expect(
      defectHeading.compareDocumentPosition(preferenceHeading) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy()
    expect(within(cleanChecks).getByText('Small-preview title')).toBeVisible()
    const singleFlagChecks = within(comparison).getByLabelText('Alternative 4 cover visual checks')
    const singleFlagSummary = singleFlagChecks.querySelector<HTMLElement>('.cover-defect-summary')
    if (!singleFlagSummary) throw new Error('single-flag cover summary is missing')
    if (mediaKind === 'epub') {
      expect(within(singleFlagSummary).getByText('1 visual issue flagged')).toBeVisible()
      expect(within(singleFlagSummary).getByText('Audiobook branding')).toBeVisible()
    } else {
      expect(within(singleFlagSummary).getByText('No visual defects flagged')).toBeVisible()
      expect(within(singleFlagSummary).queryByText('Audiobook branding')).toBeNull()
    }
    const flaggedChecks = within(comparison).getByLabelText('Alternative 5 cover visual checks')
    const flaggedSummary = flaggedChecks.querySelector<HTMLElement>('.cover-defect-summary')
    if (!flaggedSummary) throw new Error('flagged cover summary is missing')
    expect(
      within(flaggedSummary).getByText(
        mediaKind === 'epub' ? '2 visual issues flagged' : '3 visual issues flagged',
      ),
    ).toBeVisible()
    expect(within(flaggedSummary).getByText('Overlay or marking')).toBeVisible()
    expect(within(flaggedSummary).getByText('Degraded reproduction')).toBeVisible()
    if (mediaKind === 'epub') {
      expect(within(flaggedSummary).queryByText('Text-edition branding')).not.toBeInTheDocument()
    } else {
      expect(within(flaggedSummary).getByText('Text-edition branding')).toBeVisible()
    }
    await user.click(within(flaggedChecks).getByText('Visual observation details'))
    const observationDetails = flaggedChecks.querySelector('details')
    if (!observationDetails) throw new Error('cover observation details are missing')
    expect(within(observationDetails).getByText('Text-edition branding')).toBeVisible()
    await user.click(within(comparison).getByText('Reasons retained'))
    expect(within(comparison).getByText('Preview unavailable')).toBeVisible()
    expect(
      comparison.querySelectorAll('.cover-choice.is-unconfirmed .library-cover-card:disabled'),
    ).toHaveLength(2)
    expect(comparison.querySelector('.library-cover-pinned')).not.toBeNull()
    expect(comparison.querySelectorAll('.library-cover-gallery .library-cover-card')).toHaveLength(
      5,
    )

    cleanup()
    const recordDecision = vi.fn<ReviewHttp['recordDecision']>(() => new Promise(() => undefined))
    renderRoute('/library?lens=all&preview=case-1&field=cover', {
      ...createHttp(),
      recordDecision,
      getCaseDetail: vi.fn(async () => ({
        ...detail,
        target: { ...detail.target, mediaKind },
        fields: [
          {
            ...coverField,
            decisionActions: ['keep_current', 'accept'] as const,
            editable: true,
            needsAttention: true,
            coverConsideration: { ...consideration, providerFetching: false, ready: true },
          },
        ],
      })),
    })
    const mixedComparison = await screen.findByLabelText('Choose a cover')
    const unknownChoice = within(mixedComparison).getByRole('button', {
      name: /^Choose Alternative 4 cover/u,
    })
    await user.click(unknownChoice)
    expect(screen.getByRole('dialog')).toHaveTextContent('Match not confirmed.')
    await user.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(unknownChoice).toHaveFocus()
    expect(recordDecision).not.toHaveBeenCalled()
    await user.click(unknownChoice)
    fireEvent(screen.getByRole('dialog'), new Event('cancel', { cancelable: true }))
    expect(unknownChoice).toHaveFocus()
    expect(recordDecision).not.toHaveBeenCalled()
    const pinnedTrack = mixedComparison.querySelector<HTMLElement>('.library-cover-pinned')
    const currentCard = within(mixedComparison).getByRole('button', {
      name: 'Choose Current cover',
    })
    const forwardCard = within(mixedComparison).getByRole('button', {
      name: /Alternative 3 cover/u,
    })
    const forwardChoice = forwardCard.closest<HTMLElement>('.cover-choice')
    const currentChoice = currentCard.closest<HTMLElement>('.cover-choice')
    if (!pinnedTrack || !forwardChoice || !currentChoice) {
      throw new Error('expanded cover track is missing')
    }
    Object.defineProperty(pinnedTrack, 'scrollLeft', {
      configurable: true,
      value: 0,
      writable: true,
    })
    vi.spyOn(pinnedTrack, 'getBoundingClientRect').mockReturnValue({
      left: 0,
      right: 250,
      width: 250,
    } as DOMRect)
    vi.spyOn(forwardChoice, 'getBoundingClientRect').mockReturnValue({
      left: 310,
      right: 500,
      width: 190,
    } as DOMRect)
    forwardCard.focus()
    expect(pinnedTrack.scrollLeft).toBe(310)
    vi.spyOn(currentChoice, 'getBoundingClientRect').mockReturnValue({
      left: -120,
      right: 70,
      width: 190,
    } as DOMRect)
    currentCard.focus()
    expect(pinnedTrack.scrollLeft).toBe(190)

    await user.click(
      within(mixedComparison).getByRole('button', { name: 'Evidence for Alternative 4 cover' }),
    )
    const ineligibleEvidence = within(mixedComparison).getByLabelText(
      'Alternative 4 cover evidence',
    )
    await user.click(ineligibleEvidence)
    fireEvent.keyDown(ineligibleEvidence, { key: 'Enter' })
    fireEvent.keyDown(ineligibleEvidence, { key: ' ' })
    expect(recordDecision).not.toHaveBeenCalled()

    await user.click(
      within(mixedComparison).getByRole('button', { name: 'Evidence for Alternative 1 cover' }),
    )
    await user.click(within(mixedComparison).getByLabelText('Alternative 1 cover evidence'))
    expect(recordDecision).not.toHaveBeenCalled()
    await user.click(
      within(mixedComparison).getByRole('button', { name: /^Choose Alternative 1 cover/u }),
    )
    expect(recordDecision).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: 'Confirm cover choice' }))
    expect(recordDecision).toHaveBeenCalledOnce()
    expect(screen.getByRole('button', { name: 'Recording…' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Close' })).toBeDisabled()

    cleanup()
    const revision = vi.fn<ReviewHttp['recordDecision']>(() => new Promise(() => undefined))
    renderRoute('/library?lens=all&preview=case-1&field=cover', {
      ...createHttp(),
      recordDecision: revision,
      getCaseDetail: vi.fn(async () => ({
        ...detail,
        fields: [
          {
            ...coverField,
            editable: true,
            needsAttention: false,
            decisionActions: ['keep_current', 'accept'] as const,
            currentCoverSelectionId: `cover_selection_${'a'.repeat(64)}`,
            currentCoverSelectionArtifactId: `cover_${'2'.repeat(64)}`,
            coverConsideration: { ...consideration, providerFetching: false, ready: true },
          },
        ],
      })),
    })
    await user.click(await screen.findByRole('button', { name: 'Choose Alternative 4 cover' }))
    expect(screen.getByRole('heading', { name: 'Confirm replacement cover' })).toBeVisible()
    expect(revision).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: 'Confirm cover choice' }))
    expect(revision).toHaveBeenCalledOnce()
    expect(revision.mock.calls[0]?.[1].coverConfirmation).toEqual({
      expected_selection_id: `cover_selection_${'a'.repeat(64)}`,
      confirm_unverified_match: true,
    })

    cleanup()
    renderRoute('/library?lens=all&preview=case-1&field=cover', {
      ...createHttp(),
      getCaseDetail: vi.fn(async () => ({
        ...detail,
        fields: [
          {
            ...coverField,
            humanDecision: {
              actor: 'owner' as const,
              application: 'apply' as const,
              decision_id: 'recorded-cover',
              destination: 'cover',
              occurred_at: 1,
              outcome: 'accept' as const,
              proposal_id: coverField.proposalId,
              review_case_id: 'case-1',
              target_fingerprint: coverField.targetFingerprint,
              selected_value: { kind: 'cover', value: `cover_${'2'.repeat(64)}` },
            },
            coverConsideration: { ...consideration, providerFetching: false, ready: true },
          },
        ],
      })),
    })
    expect(
      await screen.findByText(/This cover choice is not currently available for revision/u),
    ).toBeVisible()
    expect(
      screen.getByLabelText('Choose a cover').querySelectorAll('.library-cover-card:disabled'),
    ).toHaveLength(6)

    cleanup()
    const recommended = {
      ...consideration,
      providerFetching: false,
      ready: true,
      policy: {
        ...consideration.policy,
        status: 'recommended' as const,
      },
    }
    renderRoute('/library?lens=all&preview=case-1&field=cover', {
      ...createHttp(),
      getCaseDetail: vi.fn(async () => ({
        ...detail,
        target: { ...detail.target, mediaKind },
        fields: [{ ...coverField, coverConsideration: recommended }],
      })),
    })
    const recommendedComparison = await screen.findByLabelText('Choose a cover')
    expect(within(recommendedComparison).getByText('Recommended cover ready')).toBeVisible()
    expect(
      within(recommendedComparison).getByText('Alternative 2 cover · Recommended'),
    ).toBeVisible()

    for (const [reason, explanation] of [
      [
        'actual_target_unverified',
        'The current cover could not be verified, so automatic replacement is withheld.',
      ],
      [
        'confirmed_incumbent_not_improved',
        'No confirmed alternative improves the current cover, so automatic replacement is withheld.',
      ],
    ] as const) {
      cleanup()
      const ownerReady = {
        ...consideration,
        providerFetching: false,
        ready: true,
        policy: {
          ...consideration.policy,
          recommended_candidate_reference: null,
          status: 'abstained' as const,
          reasons: [reason],
        },
      }
      renderRoute('/library?lens=all&preview=case-1&field=cover', {
        ...createHttp(),
        getCaseDetail: vi.fn(async () => ({
          ...detail,
          target: { ...detail.target, mediaKind },
          fields: [{ ...coverField, coverConsideration: ownerReady }],
        })),
      })
      const ownerReadyComparison = await screen.findByLabelText('Choose a cover')
      expect(within(ownerReadyComparison).getByText('Cover choices ready')).toBeVisible()
      expect(within(ownerReadyComparison).getByText(explanation)).toBeVisible()
      expect(within(ownerReadyComparison).queryByText(/· Recommended/u)).not.toBeInTheDocument()
    }
  })

  it.each([
    ['memory_bytes mismatch', 'profile contract requires memory_bytes=1073741824', 'unavailable'],
    ['process_limit mismatch', 'profile contract requires process_limit=32', 'unavailable'],
    ['reviewer failure', null, 'failed'],
  ] as const)(
    'keeps manual cover decisions usable for a %s',
    async (_state, reviewerUnavailableReason, reviewerStatus) => {
      const user = userEvent.setup()
      const base = detail.fields[0]
      if (!base) throw new Error('cover fixture requires a field')
      const consideration = {
        id: `cover_consideration_${'u'.repeat(64)}`,
        choiceContexts: [
          {
            alternativeId: null,
            selection: 'current',
            evidence: [
              {
                source: 'Local library record',
                role: 'local_presence',
                observation: 'Current cover preview is unavailable; local presence is retained.',
              },
            ],
            limitations: ['preview_unavailable'],
            rationale: 'Keep the retained local cover.',
            recommendation: 'no_recommendation',
            recommendationReason: 'No recommendation retained.',
          },
          {
            alternativeId: `cover_transport_ref_${'a'.repeat(64)}`,
            selection: 'proposed',
            evidence: [],
            limitations: [],
            rationale: 'Selectable retained cover.',
            recommendation: 'no_recommendation',
            recommendationReason: 'No recommendation retained.',
          },
        ],
        current: true,
        candidates: [
          {
            artifactId: `cover_${'a'.repeat(64)}`,
            deterministicCitations: ['citation-a'],
            deterministicRank: 1,
            deterministicRankKey: [
              1,
              0,
              -960000,
              -800,
              -1200,
              1,
              `cover_transport_ref_${'a'.repeat(64)}`,
            ],
            height: 1200,
            identity: { outcome: 'verified_work_match' },
            incumbent: false,
            matchState: 'same_book' as const,
            observation: null,
            originalSha256: 'a'.repeat(64),
            ordinal: 1,
            previewSha256: 'a'.repeat(64),
            reference: `cover_transport_ref_${'a'.repeat(64)}`,
            selectable: true,
            manualEligible: true,
            sourceKinds: ['provider'],
            state: 'acceptable',
            width: 800,
          },
        ],
        incumbent: {
          reference: `cover_transport_ref_${'b'.repeat(64)}`,
          state: 'unavailable' as const,
        },
        omissions: [],
        policy: null,
        policyPreferences: {
          avoidPromotionalText: false,
          avoidTieInBranding: false,
          preferMinimalComposition: false,
        },
        policyReference: `cover_policy_${'u'.repeat(64)}`,
        providerFetching: false,
        ready: true,
        reviewerAccessRevision: null,
        reviewerEngine: null,
        reviewerModel: null,
        reviewerStatus,
        reviewerUnavailableReason,
        targetFingerprint: 'fingerprint-cover',
      } as const
      const coverField = {
        ...base,
        destination: 'cover',
        proposedValues: [{ kind: 'cover', value: `cover_${'a'.repeat(64)}` }],
        coverConsideration: consideration,
      }
      renderRoute('/library?lens=all&preview=case-1&field=cover', {
        ...createHttp(),
        getCaseDetail: vi.fn(async () => ({ ...detail, fields: [coverField] })),
      })

      const currentCover = await screen.findByRole('button', {
        name: 'Keep current cover',
      })
      expect(currentCover).toBeVisible()
      expect(currentCover.closest('.library-cover-layout')?.firstElementChild).toBe(
        currentCover.closest('.cover-choice'),
      )
      expect(screen.getByText('Visual checks unavailable')).toBeVisible()
      expect(screen.queryByText('No visual defects flagged')).not.toBeInTheDocument()
      if (reviewerUnavailableReason === null) {
        expect(screen.queryByRole('button', { name: 'Retry advisory review' })).toBeNull()
        expect(screen.queryByText(/Regenerate the profile for the current contract/u)).toBeNull()
      } else {
        expect(
          screen.getByText(
            reviewerUnavailableReason === 'profile contract requires memory_bytes=1073741824'
              ? /must set memory_bytes to 1073741824/u
              : /must set process_limit to 32/u,
          ),
        ).toBeVisible()
        expect(screen.getByText(/Regenerate the profile for the current contract/u)).toHaveClass(
          'reviewer-unavailable-detail',
        )
      }
      const evidence = screen.getByRole('button', { name: 'Evidence for Current cover' })
      await user.click(evidence)
      expect(screen.getByRole('button', { name: 'Evidence for Current cover' })).toHaveAttribute(
        'aria-expanded',
        'true',
      )
      expect(screen.getByRole('button', { name: 'Evidence for Current cover' })).toHaveTextContent(
        'Hide evidence',
      )
      expect(screen.getByText('Local presence')).toBeVisible()
      expect(screen.getByText('Preview unavailable')).toBeVisible()
      expect(screen.getByRole('button', { name: 'Keep current cover' })).toHaveAttribute(
        'aria-pressed',
        'false',
      )
      expect(screen.getByText('Choices').nextElementSibling).toHaveTextContent('2')
      const compactTrack = currentCover.closest<HTMLElement>('.library-cover-layout')
      const alternativeCover = screen.getByRole('button', { name: /Alternative 1 cover/u })
      const alternativeChoice = alternativeCover.closest<HTMLElement>('.cover-choice')
      const compactCurrentChoice = currentCover.closest<HTMLElement>('.cover-choice')
      if (!compactTrack || !alternativeChoice || !compactCurrentChoice) {
        throw new Error('compact cover track is missing')
      }
      Object.defineProperty(compactTrack, 'scrollLeft', {
        configurable: true,
        value: 0,
        writable: true,
      })
      vi.spyOn(compactTrack, 'getBoundingClientRect').mockReturnValue({
        left: 0,
        right: 250,
        width: 250,
      } as DOMRect)
      vi.spyOn(alternativeChoice, 'getBoundingClientRect').mockReturnValue({
        left: 280,
        right: 450,
        width: 170,
      } as DOMRect)
      alternativeCover.focus()
      expect(compactTrack.scrollLeft).toBe(280)
      vi.spyOn(compactCurrentChoice, 'getBoundingClientRect').mockReturnValue({
        left: -80,
        right: 90,
        width: 170,
      } as DOMRect)
      currentCover.focus()
      expect(compactTrack.scrollLeft).toBe(200)

      cleanup()
      const acceptOnlyHttp = {
        ...createHttp(),
        getCaseDetail: vi.fn(async () => ({
          ...detail,
          fields: [{ ...coverField, decisionActions: ['accept' as const] }],
        })),
      }
      renderRoute('/library?lens=all&preview=case-1&field=cover', acceptOnlyHttp)
      expect(await screen.findByRole('button', { name: 'Keep current cover' })).toBeDisabled()
      expect(screen.getByRole('button', { name: /Alternative 1 cover/u })).toBeEnabled()
      await user.click(screen.getByRole('button', { name: /Evidence for Current cover/u }))
      expect(screen.queryByRole('button', { name: 'Choose Current cover' })).not.toBeInTheDocument()
      expect(acceptOnlyHttp.recordDecision).not.toHaveBeenCalled()
      if (reviewerStatus === 'failed') {
        for (const capability of [
          'configured',
          'null',
          'unconfigured',
          'changed-engine',
        ] as const) {
          cleanup()
          const access: ReviewerAccessView = {
            schema_version: 'reviewer-access-view-v2',
            cover_metadata: null,
            cost_evidence: {
              schema_version: 'reviewer-cost-evidence-v2',
              models: [],
            },
            revision: 9,
            mapping:
              capability === 'null'
                ? null
                : capability === 'changed-engine'
                  ? { engine: 'openrouter', model: 'synthetic-model' }
                  : { engine: 'codex' },
            description_mapping: null,
            description_metadata: null,
            description: null,
            openrouter_key_configured: false,
            codex: {
              engine: 'codex',
              state: capability === 'unconfigured' ? 'unconfigured' : 'failed',
              reason: null,
              model: null,
              tier: null,
              host_headroom_bytes: null,
            },
            openrouter: {
              engine: 'openrouter',
              state: 'unconfigured',
              reason: null,
              model: null,
              tier: null,
              host_headroom_bytes: null,
            },
            recommendations: [],
            candidates: [],
            free_tier_caveat: '',
            data_use_caveat: '',
          }
          const capabilityHttp = {
            ...createHttp(),
            getCaseDetail: vi.fn(async () => ({ ...detail, fields: [coverField] })),
            getReviewerAccess: vi.fn(async () => access),
            retryCoverConsideration: vi.fn<ReviewHttp['retryCoverConsideration']>(async () => {
              throw new ApiError(409, 'stale_cover')
            }),
          }
          renderRoute('/library?lens=all&preview=case-1&field=cover', capabilityHttp)
          await screen.findByRole('button', { name: /Alternative 1 cover/u })
          await waitFor(() => expect(capabilityHttp.getReviewerAccess).toHaveBeenCalledOnce())
          if (capability === 'configured') {
            await user.click(await screen.findByRole('button', { name: 'Retry advisory review' }))
            expect(await screen.findByText(/The advisory retry failed safely/u)).toBeVisible()
            expect(capabilityHttp.retryCoverConsideration).toHaveBeenCalledOnce()
          } else {
            expect(screen.queryByRole('button', { name: 'Retry advisory review' })).toBeNull()
            expect(capabilityHttp.retryCoverConsideration).not.toHaveBeenCalled()
          }
          expect(capabilityHttp.recordDecision).not.toHaveBeenCalled()
        }
      }
    },
  )

  it('closes cached cover evidence after client-side A to B to A work navigation', async () => {
    const user = userEvent.setup()
    const baseField = detail.fields[0]
    const baseWork = library.works[0]
    if (!baseField || !baseWork) throw new Error('cover reset fixture requires a work and field')
    const cover = (id: string): CaseDetailView => ({
      ...detail,
      id,
      fields: [
        {
          ...baseField,
          destination: 'cover',
          proposedValues: [{ kind: 'cover', value: 'cover-alt' }],
          coverConsideration: {
            id: `consideration-${id}`,
            current: true,
            candidates: [
              {
                artifactId: 'cover-current',
                deterministicCitations: ['citation-current'],
                deterministicRank: null,
                deterministicRankKey: [1, 0, -10000, -100, -100, 0, 'current'],
                height: 100,
                identity: { outcome: 'verified_work_match' },
                incumbent: true,
                matchState: 'same_book',
                observation: null,
                originalSha256: 'a'.repeat(64),
                ordinal: 0,
                previewSha256: 'b'.repeat(64),
                reference: 'current',
                selectable: false,
                manualEligible: false,
                sourceKinds: [],
                state: 'available',
                width: 100,
              },
              {
                artifactId: 'cover-alt',
                deterministicCitations: ['citation-alternative'],
                deterministicRank: 1,
                deterministicRankKey: [1, 0, -10000, -100, -100, 1, 'alternative'],
                height: 100,
                identity: { outcome: 'verified_work_match' },
                incumbent: false,
                matchState: 'same_book',
                observation: null,
                originalSha256: 'c'.repeat(64),
                ordinal: 1,
                previewSha256: 'd'.repeat(64),
                reference: 'alternative',
                selectable: true,
                manualEligible: true,
                sourceKinds: [],
                state: 'available',
                width: 100,
              },
            ],
            choiceContexts: [
              {
                alternativeId: null,
                selection: 'current',
                evidence: [
                  {
                    source: 'Local library record',
                    role: 'local_presence',
                    observation: 'Local cover retained.',
                  },
                ],
                limitations: [],
                rationale: 'Current cover.',
                recommendation: 'no_recommendation',
                recommendationReason: 'No recommendation.',
              },
              {
                alternativeId: 'alternative',
                selection: 'proposed',
                evidence: [],
                limitations: [],
                rationale: 'Alternative cover.',
                recommendation: 'no_recommendation',
                recommendationReason: 'No recommendation.',
              },
            ],
            incumbent: { reference: 'current', state: 'available' },
            omissions: [],
            policy: {
              status: 'abstained',
              recommended_candidate_reference: null,
              acceptable_candidate_references: [],
            },
            policyPreferences: {
              avoidPromotionalText: false,
              avoidTieInBranding: false,
              preferMinimalComposition: false,
            },
            policyReference: 'policy',
            providerFetching: false,
            ready: true,
            reviewerAccessRevision: null,
            reviewerEngine: null,
            reviewerModel: null,
            reviewerStatus: 'completed',
            reviewerUnavailableReason: null,
            targetFingerprint: 'fingerprint',
          },
        },
      ],
    })
    const twoWorks: LibraryView = {
      ...library,
      works: [
        { ...baseWork, title: 'Work A' },
        { ...baseWork, id: 'case-2', reviewCaseId: 'case-2', title: 'Work B' },
      ],
    }
    const http = {
      ...createHttp(),
      getLibrary: vi.fn(async () => twoWorks),
      getCaseDetail: vi.fn(async (id: string) => cover(id)),
    }
    renderRoute('/library?lens=all&preview=case-1&field=cover', http)
    const evidence = await screen.findByRole('button', { name: 'Evidence for Current cover' })
    await user.click(evidence)
    expect(screen.getByLabelText('Current cover evidence')).toBeVisible()
    await user.click(screen.getByRole('button', { name: /^Work B\./u }))
    await screen.findByRole('button', { name: 'Evidence for Current cover' })
    expect(screen.queryByLabelText('Current cover evidence')).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /^Work A\./u }))
    await screen.findByRole('button', { name: 'Evidence for Current cover' })
    expect(screen.queryByLabelText('Current cover evidence')).not.toBeInTheDocument()
    expect(http.recordDecision).not.toHaveBeenCalled()
  })

  it('prepares a missing cover comparison inside the Library workflow', async () => {
    const user = userEvent.setup()
    const base = detail.fields[0]
    if (!base) throw new Error('cover fixture requires a field')
    const consideration = {
      id: `cover_consideration_${'p'.repeat(64)}`,
      current: true,
      choiceContexts: [
        {
          alternativeId: null,
          selection: 'current',
          evidence: [],
          limitations: [],
          rationale: 'Keep the local cover.',
          recommendation: 'no_recommendation',
          recommendationReason: 'No recommendation retained.',
        },
        {
          alternativeId: `cover_transport_ref_${'a'.repeat(64)}`,
          selection: 'proposed',
          evidence: [],
          limitations: [],
          rationale: 'Selectable retained cover.',
          recommendation: 'no_recommendation',
          recommendationReason: 'No recommendation retained.',
        },
      ],
      candidates: [
        {
          artifactId: `cover_${'a'.repeat(64)}`,
          deterministicCitations: ['citation-a'],
          deterministicRank: 1,
          deterministicRankKey: [
            1,
            0,
            -960000,
            -800,
            -1200,
            1,
            `cover_transport_ref_${'a'.repeat(64)}`,
          ],
          height: 1200,
          identity: { outcome: 'verified_work_match' },
          incumbent: false,
          matchState: 'same_book' as const,
          observation: null,
          originalSha256: 'a'.repeat(64),
          ordinal: 1,
          previewSha256: 'a'.repeat(64),
          reference: `cover_transport_ref_${'a'.repeat(64)}`,
          selectable: true,
          manualEligible: true,
          sourceKinds: ['provider'],
          state: 'acceptable',
          width: 800,
        },
      ],
      incumbent: {
        reference: `cover_transport_ref_${'b'.repeat(64)}`,
        state: 'unavailable' as const,
      },
      omissions: [],
      policy: null,
      policyPreferences: {
        avoidPromotionalText: false,
        avoidTieInBranding: false,
        preferMinimalComposition: false,
      },
      policyReference: `cover_policy_${'p'.repeat(64)}`,
      providerFetching: false,
      ready: true,
      reviewerAccessRevision: null,
      reviewerEngine: null,
      reviewerModel: null,
      reviewerStatus: 'completed',
      reviewerUnavailableReason: null,
      targetFingerprint: 'fingerprint-cover',
    } as const
    const absent: CaseDetailView['fields'][number] = {
      ...base,
      destination: 'cover',
      proposedValues: [{ kind: 'cover', value: `cover_${'a'.repeat(64)}` }],
      coverConsideration: null,
    }
    const prepared: CaseDetailView['fields'][number] = {
      ...absent,
      coverConsideration: consideration,
    }
    let field: CaseDetailView['fields'][number] = absent
    let preparationAttempts = 0
    const retryCoverConsideration = vi.fn(async () => {
      preparationAttempts += 1
      if (preparationAttempts === 1) throw new Error('temporary preparation failure')
      field = prepared
      return consideration
    })
    const http: ReviewHttp = {
      ...createHttp(),
      getCaseDetail: vi.fn(async () => ({ ...detail, fields: [field] })),
      retryCoverConsideration,
    }
    renderRoute('/library?lens=all&preview=case-1&field=cover', http)

    const prepare = await screen.findByRole('button', { name: 'Prepare cover comparison' })
    expect(screen.getByText('Choices').nextElementSibling).toHaveTextContent('1')
    await user.click(prepare)
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'The comparison could not be prepared. The existing cover remains unchanged.',
    )
    expect(prepare).toBeEnabled()
    await user.click(prepare)
    await waitFor(() => expect(retryCoverConsideration).toHaveBeenCalledTimes(2))
    expect(retryCoverConsideration).toHaveBeenLastCalledWith('case-1')
    expect(await screen.findByLabelText('Choose a cover')).toBeVisible()
    expect(screen.getByText('Choices').nextElementSibling).toHaveTextContent('2')
  })

  it('offers only preparation for an authenticated historical cover comparison', async () => {
    const base = detail.fields[0]
    if (!base) throw new Error('cover fixture requires a field')
    const historical: CaseDetailView['fields'][number] = {
      ...base,
      destination: 'cover',
      decisionActions: [],
      editable: true,
      needsAttention: true,
      coverConsideration: {
        id: `cover_consideration_${'h'.repeat(64)}`,
        current: false,
        choiceContexts: [],
        candidates: [],
        incumbent: { reference: null, state: 'absent' },
        omissions: [],
        policy: null,
        policyPreferences: {
          avoidPromotionalText: false,
          avoidTieInBranding: false,
          preferMinimalComposition: false,
        },
        policyReference: `cover_policy_${'h'.repeat(64)}`,
        providerFetching: false,
        ready: true,
        reviewerAccessRevision: null,
        reviewerEngine: null,
        reviewerModel: null,
        reviewerStatus: 'completed',
        reviewerUnavailableReason: null,
        targetFingerprint: 'fingerprint-1',
      },
    }
    const http: ReviewHttp = {
      ...createHttp(),
      getCaseDetail: vi.fn(async () => ({ ...detail, fields: [historical] })),
    }
    renderRoute('/library?lens=all&preview=case-1&field=cover', http)

    expect(await screen.findByRole('button', { name: 'Prepare cover comparison' })).toBeEnabled()
    expect(screen.queryByRole('button', { name: 'Keep current cover' })).toBeNull()
    expect(screen.queryByLabelText('Choose a cover')).toBeNull()
    expect(http.recordDecision).not.toHaveBeenCalled()
  })

  it('keeps retained per-choice provenance when additional observations fail', async () => {
    const user = userEvent.setup()
    const titleField = detail.fields[0]
    if (!titleField) throw new Error('workspace fixture requires a title field')
    const http: ReviewHttp = {
      ...createHttp(),
      getCaseDetail: vi.fn(async () => ({
        ...detail,
        fields: [
          {
            ...titleField,
            choiceContexts: [
              {
                alternativeId: null,
                selection: 'current',
                rationale: 'Keep the value currently held by the local library.',
                recommendation: 'no_recommendation',
                recommendationReason:
                  'No deterministic recommendation is retained for the current value.',
                limitations: [],
                evidence: [
                  {
                    source: 'Local library record',
                    observation: 'Title: retained current value',
                    role: 'local_presence',
                  },
                ],
              },
              {
                alternativeId: 'alternative-title-1',
                selection: 'proposed',
                rationale: 'This retained alternative is eligible for owner selection.',
                recommendation: 'no_recommendation',
                recommendationReason:
                  'No deterministic recommendation is retained for this alternative.',
                limitations: [],
                evidence: [
                  {
                    source: 'Open Library',
                    observation: 'Title: New title',
                    role: 'external_corroboration',
                  },
                ],
              },
            ] as const,
          },
        ],
      })),
      getEvidence: vi.fn(async () => {
        throw new Error('diagnostic evidence unavailable')
      }),
    }
    renderRoute('/library?lens=all&preview=case-1&field=title', http)

    const evidenceButton = (await screen.findAllByRole('button', { name: /Evidence for/u }))[0]
    if (!evidenceButton) throw new Error('current choice evidence control is missing')
    await user.click(evidenceButton)
    expect(screen.getByText('Local library record')).toBeVisible()
    expect(screen.getByText('Title: retained current value')).toBeVisible()
    expect(http.getEvidence).toHaveBeenCalledTimes(1)
    expect(screen.getByRole('button', { name: 'Retry observations' })).toBeVisible()
    expect(screen.queryByLabelText('Comparison condition')).not.toBeInTheDocument()
  })

  it('keeps frozen field positions after an earlier field is decided', async () => {
    const user = userEvent.setup()
    const titleField = detail.fields[0]
    if (!titleField) throw new Error('workspace fixture requires a title field')
    const authorsField = {
      ...titleField,
      destination: 'authors',
      currentValue: 'Ada Example',
      proposalId: 'proposal-authors',
      targetFingerprint: 'fingerprint-authors',
    }
    const retainedAttentionContext = {
      ...titleField,
      destination: 'subtitle',
      decisionActions: [],
      editable: false,
      needsAttention: true,
      proposedValues: [],
      proposalId: 'proposal-subtitle',
      targetFingerprint: 'fingerprint-subtitle',
    }
    const contextualField = {
      ...titleField,
      destination: 'publishedYear',
      inventoryRole: 'contextual' as const,
      navigationRole: 'inspect' as const,
      needsAttention: false,
    }
    const initial = {
      ...detail,
      fields: [titleField, retainedAttentionContext, authorsField, contextualField],
    }
    const settled = {
      ...detail,
      fields: [
        authorsField,
        retainedAttentionContext,
        contextualField,
        {
          ...titleField,
          needsAttention: false,
          humanDecision: {
            actor: 'owner',
            application: 'apply' as const,
            decision_id: 'decision-title',
            destination: 'title',
            occurred_at: 1,
            outcome: 'keep_current' as const,
            proposal_id: titleField.proposalId,
            review_case_id: 'case-1',
            selected_value: null,
            target_fingerprint: titleField.targetFingerprint,
          },
        },
      ],
    }
    let current: CaseDetailView = initial
    const http: ReviewHttp = {
      ...createHttp(),
      getCaseDetail: vi.fn(async () => current),
      recordDecision: vi.fn(async () => {
        current = settled
        return {
          confirmation: { status: 'confirmed' as const, case: current },
          invalidation_sequences: [],
          schema_version: 'review-decision-v1' as const,
          actor: 'owner' as const,
          application: 'apply' as const,
          decision_id: 'decision-title',
          destination: 'title',
          occurred_at: 1,
          outcome: 'keep_current' as const,
          proposal_id: titleField.proposalId,
          review_case_id: 'case-1',
          selected_value: null,
          target_fingerprint: titleField.targetFingerprint,
        }
      }),
    }
    renderRoute('/library?lens=all&preview=case-1&field=title', http)

    expect(await screen.findByRole('button', { name: /Authors.*Needs review/u })).toBeVisible()
    await user.click(screen.getByRole('button', { name: 'Choose current value: Old title' }))
    await waitFor(() =>
      expect(screen.getByRole('button', { name: /Authors.*Needs review/u })).toHaveAttribute(
        'aria-current',
        'true',
      ),
    )
    // Advancement leaves the next field ready for the keyboard without moving
    // the document to centre the rail cell. The retained attention context in
    // between has no exact action, so it cannot become a phantom next choice.
    expect(screen.getByRole('button', { name: 'Choose current value: Ada Example' })).toHaveFocus()
    expect(screen.getByRole('button', { name: /Title.*Decision recorded/u })).toBeVisible()
    expect(screen.queryByText('Decision recorded from the authoritative case.')).toBeNull()
    expect(
      within(screen.getByRole('navigation', { name: 'Review fields' }))
        .getAllByRole('button')
        .map((button) => button.textContent),
    ).toEqual(['Title✓', 'Subtitle✓', 'Authors•'])
  })

  it('omits an unsupported previous value for a blocked recorded replacement', async () => {
    const user = userEvent.setup()
    const source = detail.fields[0]
    if (!source) throw new Error('workspace fixture requires a title field')
    const title = { ...source, decisionActions: [], needsAttention: false, proposedValues: [] }
    const mistborn = { kind: 'text' as const, value: 'Mistborn' }
    const series = {
      ...source,
      currentValue: 'Not set',
      destination: 'series',
      proposalId: 'proposal-series',
      proposedValues: [mistborn],
      targetFingerprint: 'fingerprint-series',
    }
    const recordedSeries = {
      ...series,
      decisionActions: ['keep_current', 'accept'] as const,
      editable: false,
      needsAttention: false,
      humanDecision: {
        actor: 'owner' as const,
        application: 'apply' as const,
        decision_id: 'decision-series',
        destination: 'series',
        occurred_at: 1,
        outcome: 'accept' as const,
        proposal_id: series.proposalId,
        review_case_id: 'case-1',
        selected_value: mistborn,
        target_fingerprint: series.targetFingerprint,
      },
    }
    let current: CaseDetailView = { ...detail, fields: [title, series] }
    const http: ReviewHttp = {
      ...createHttp(),
      getCaseDetail: vi.fn(async () => current),
      recordDecision: vi.fn(async () => {
        current = { ...detail, fields: [title, recordedSeries] }
        return {
          confirmation: { status: 'confirmed' as const, case: current },
          invalidation_sequences: [],
          schema_version: 'review-decision-v1' as const,
          actor: 'owner' as const,
          application: 'apply' as const,
          decision_id: 'decision-series',
          destination: 'series',
          occurred_at: 1,
          outcome: 'accept' as const,
          proposal_id: series.proposalId,
          review_case_id: 'case-1',
          selected_value: mistborn,
          target_fingerprint: series.targetFingerprint,
        }
      }),
    }
    renderRoute('/library?lens=all&preview=case-1&field=series', http)

    await user.click(await screen.findByRole('button', { name: 'Choose proposed value: Mistborn' }))
    await screen.findByRole('heading', { name: 'Review complete' })
    // The preserved title is inspectable, but no decision was recorded for it.
    expect(screen.getByText('1 recorded · work complete')).toBeVisible()
    expect(screen.getByText('1 recorded field outcome. No library data changed.')).toBeVisible()
    const rail = screen.getByRole('navigation', { name: 'Review fields' })
    await user.click(within(rail).getByRole('button', { name: /^Title/u }))
    expect(await screen.findByRole('heading', { name: 'Title' })).toBeVisible()
    expect(screen.getByLabelText('Title current value')).toHaveTextContent('Old title')
    expect(screen.queryByLabelText('Title recorded outcome')).toBeNull()
    expect(screen.queryByText('Decision recorded from the authoritative case.')).toBeNull()

    await user.click(within(rail).getByRole('button', { name: /^Series/u }))
    expect(await screen.findByLabelText('Series recorded outcome')).toHaveTextContent(
      'Proposed value selectedChosen valueMistborn',
    )
    expect(screen.getByLabelText('Series recorded outcome')).not.toHaveTextContent('Previous value')
    expect(screen.queryByText('Decision recorded from the authoritative case.')).toBeNull()
  })

  it('advances on the authoritative case before delayed aggregates converge', async () => {
    const user = userEvent.setup()
    const source = detail.fields[0]
    const work = library.works[0]
    if (!source || !work) throw new Error('workspace fixture requires a field and work')
    const series = {
      ...source,
      currentValue: 'Not set',
      destination: 'series',
      proposalId: 'proposal-series',
      proposedValues: [{ kind: 'text' as const, value: 'Mistborn' }],
      targetFingerprint: 'fingerprint-series',
    }
    const recorded = {
      ...series,
      decisionActions: [],
      needsAttention: false,
      humanDecision: {
        actor: 'owner' as const,
        application: 'apply' as const,
        decision_id: 'decision-series',
        destination: 'series',
        occurred_at: 1,
        outcome: 'accept' as const,
        proposal_id: series.proposalId,
        review_case_id: 'case-1',
        selected_value: series.proposedValues[0] ?? null,
        target_fingerprint: series.targetFingerprint,
      },
    }
    const initialLibrary = { ...library, counts: { all: 1, attention: 1, clear: 0, notMatched: 0 } }
    const settledLibrary = {
      ...initialLibrary,
      counts: { all: 1, attention: 0, clear: 1, notMatched: 0 },
      works: [{ ...work, attention: 'no_attention_needed' as const, attentionCount: 0 }],
    }
    let currentDetail: CaseDetailView = { ...detail, fields: [series] }
    let currentLibrary: LibraryView = initialLibrary
    let releaseAggregates: (() => void) | undefined
    const aggregateRefresh = new Promise<void>((resolve) => {
      releaseAggregates = resolve
    })
    const waitForAggregateRefresh = async () => {
      if (currentLibrary === settledLibrary) await aggregateRefresh
    }
    const getAttention = vi.fn<ReviewHttp['getAttention']>(async () => {
      await waitForAggregateRefresh()
      return { total: currentLibrary.counts.attention, items: [] }
    })
    const http: ReviewHttp = {
      ...createHttp(),
      getAttention,
      getCaseDetail: vi.fn(async () => currentDetail),
      getLibrary: vi.fn(async () => {
        await waitForAggregateRefresh()
        return currentLibrary
      }),
      recordDecision: vi.fn(async () => {
        currentDetail = { ...detail, fields: [recorded] }
        currentLibrary = settledLibrary
        return {
          confirmation: { status: 'confirmed' as const, case: currentDetail },
          invalidation_sequences: [],
          schema_version: 'review-decision-v1' as const,
          actor: 'owner' as const,
          application: 'apply' as const,
          decision_id: 'decision-series',
          destination: 'series',
          occurred_at: 1,
          outcome: 'accept' as const,
          proposal_id: series.proposalId,
          review_case_id: 'case-1',
          selected_value: series.proposedValues[0] ?? null,
          target_fingerprint: series.targetFingerprint,
        }
      }),
    }
    renderRoute('/library?lens=all&preview=case-1&field=series', http)

    expect(await screen.findByRole('button', { name: 'Attention 1 work' })).toBeVisible()
    expect(screen.getByRole('button', { name: /A Book.*1 field needs attention/u })).toBeVisible()
    await user.click(await screen.findByRole('button', { name: 'Choose proposed value: Mistborn' }))

    expect(await screen.findByRole('heading', { name: 'Review complete' })).toBeVisible()
    expect(screen.getByText('Needs review').nextElementSibling).toHaveTextContent('0')
    expect(screen.getByRole('button', { name: 'Attention 1 work' })).toBeVisible()
    await waitFor(() => expect(http.getLibrary).toHaveBeenCalledTimes(2))
    await waitFor(() => expect(getAttention).toHaveBeenCalledTimes(2))
    await act(async () => {
      releaseAggregates?.()
    })
    expect(await screen.findByRole('button', { name: 'Attention 0 works' })).toBeVisible()
    expect(screen.getByRole('button', { name: /A Book.*No attention needed/u })).toBeVisible()
    expect(
      screen.getAllByRole('button', { name: /Series.*Proposed value selected.*Mistborn/u }),
    ).toHaveLength(1)
  })

  it('keeps a recorded decision successful when its next-field navigation rejects', async () => {
    const user = userEvent.setup()
    const title = detail.fields[0]
    if (!title) throw new Error('workspace fixture requires a title field')
    const authors = {
      ...title,
      currentValue: 'Ada Example',
      destination: 'authors',
      proposalId: 'proposal-authors',
      targetFingerprint: 'fingerprint-authors',
    }
    const recorded = {
      ...title,
      needsAttention: false,
      humanDecision: {
        actor: 'owner' as const,
        application: 'apply' as const,
        decision_id: 'decision-title',
        destination: 'title',
        occurred_at: 1,
        outcome: 'accept' as const,
        proposal_id: title.proposalId,
        review_case_id: 'case-1',
        selected_value: title.proposedValues[0] ?? null,
        target_fingerprint: title.targetFingerprint,
      },
    }
    let current: CaseDetailView = { ...detail, fields: [title, authors] }
    const http: ReviewHttp = {
      ...createHttp(),
      getCaseDetail: vi.fn(async () => current),
      getLibrary: vi.fn(async () => library),
      recordDecision: vi.fn(async () => {
        current = { ...detail, fields: [recorded, authors] }
        return {
          confirmation: { status: 'confirmed' as const, case: current },
          invalidation_sequences: [],
          schema_version: 'review-decision-v1' as const,
          actor: 'owner' as const,
          application: 'apply' as const,
          decision_id: 'decision-title',
          destination: 'title',
          occurred_at: 1,
          outcome: 'accept' as const,
          proposal_id: title.proposalId,
          review_case_id: 'case-1',
          selected_value: title.proposedValues[0] ?? null,
          target_fingerprint: title.targetFingerprint,
        }
      }),
    }
    const { router } = renderRoute('/library?lens=all&preview=case-1&field=title', http)
    await screen.findByRole('button', { name: 'Choose proposed value: New title' })
    vi.spyOn(router, 'navigate').mockRejectedValueOnce(new Error('navigation unavailable'))

    await user.click(screen.getByRole('button', { name: 'Choose proposed value: New title' }))

    await waitFor(() => expect(http.recordDecision).toHaveBeenCalledOnce())
    await waitFor(() => expect(http.getLibrary).toHaveBeenCalledTimes(2))
    expect(
      screen.queryByText('The decision was not recorded. Your choice is unchanged.'),
    ).toBeNull()
    expect(screen.queryByRole('button', { name: 'Try the same decision again' })).toBeNull()
  })

  it('settles aggregate convergence when unmount interrupts a rejected owner advance', async () => {
    const user = userEvent.setup()
    const title = detail.fields[0]
    if (!title) throw new Error('workspace fixture requires a title field')
    const authors = {
      ...title,
      currentValue: 'Ada Example',
      destination: 'authors',
      proposalId: 'proposal-authors',
      targetFingerprint: 'fingerprint-authors',
    }
    const recorded = {
      ...title,
      needsAttention: false,
      humanDecision: {
        actor: 'owner' as const,
        application: 'apply' as const,
        decision_id: 'decision-title',
        destination: 'title',
        occurred_at: 1,
        outcome: 'accept' as const,
        proposal_id: title.proposalId,
        review_case_id: 'case-1',
        selected_value: title.proposedValues[0] ?? null,
        target_fingerprint: title.targetFingerprint,
      },
    }
    let current: CaseDetailView = { ...detail, fields: [title, authors] }
    const http: ReviewHttp = {
      ...createHttp(),
      getCaseDetail: vi.fn(async () => current),
      recordDecision: vi.fn(async () => {
        current = { ...detail, fields: [recorded, authors] }
        return {
          confirmation: { status: 'confirmed' as const, case: current },
          invalidation_sequences: [],
          schema_version: 'review-decision-v1' as const,
          actor: 'owner' as const,
          application: 'apply' as const,
          decision_id: 'decision-title',
          destination: 'title',
          occurred_at: 1,
          outcome: 'accept' as const,
          proposal_id: title.proposalId,
          review_case_id: 'case-1',
          selected_value: title.proposedValues[0] ?? null,
          target_fingerprint: title.targetFingerprint,
        }
      }),
    }
    const { queryClient, router } = renderRoute(
      '/library?lens=all&preview=case-1&field=title',
      http,
    )
    let rejectNavigation: ((reason: Error) => void) | undefined
    const pendingNavigation = new Promise<void>((_resolve, reject) => {
      rejectNavigation = reject
    })
    vi.spyOn(router, 'navigate').mockImplementationOnce(async () => await pendingNavigation)
    const invalidations = vi.spyOn(queryClient, 'invalidateQueries')

    await user.click(
      await screen.findByRole('button', { name: 'Choose proposed value: New title' }),
    )
    await waitFor(() => expect(http.recordDecision).toHaveBeenCalledOnce())
    cleanup()
    await act(async () => {
      rejectNavigation?.(new Error('navigation unavailable after unmount'))
    })

    await waitFor(() =>
      expect(invalidations).toHaveBeenCalledWith({ queryKey: ['library'], refetchType: 'none' }),
    )
  })

  it('settles an owner advance when an exact-source route supersedes its held target', async () => {
    const user = userEvent.setup()
    const title = detail.fields[0]
    if (!title) throw new Error('workspace fixture requires a title field')
    const authors = {
      ...title,
      currentValue: 'Ada Example',
      destination: 'authors',
      proposalId: 'proposal-authors',
      targetFingerprint: 'fingerprint-authors',
    }
    const recorded = {
      ...title,
      needsAttention: false,
      humanDecision: {
        actor: 'owner' as const,
        application: 'apply' as const,
        decision_id: 'decision-title',
        destination: 'title',
        occurred_at: 1,
        outcome: 'accept' as const,
        proposal_id: title.proposalId,
        review_case_id: 'case-1',
        selected_value: title.proposedValues[0] ?? null,
        target_fingerprint: title.targetFingerprint,
      },
    }
    let current: CaseDetailView = { ...detail, fields: [title, authors] }
    let reads = 0
    let releaseRefresh: ((value: CaseDetailView) => void) | undefined
    const refresh = new Promise<CaseDetailView>((resolve) => {
      releaseRefresh = resolve
    })
    const http: ReviewHttp = {
      ...createHttp(),
      getCaseDetail: vi.fn(async () => {
        reads += 1
        return reads === 2 ? refresh : current
      }),
      getLibrary: vi.fn(async () => library),
      recordDecision: vi.fn(async () => {
        current = { ...detail, fields: [recorded, authors] }
        return {
          confirmation: { status: 'confirmed' as const, case: await refresh },
          invalidation_sequences: [],
          schema_version: 'review-decision-v1' as const,
          actor: 'owner' as const,
          application: 'apply' as const,
          decision_id: 'decision-title',
          destination: 'title',
          occurred_at: 1,
          outcome: 'accept' as const,
          proposal_id: title.proposalId,
          review_case_id: 'case-1',
          selected_value: title.proposedValues[0] ?? null,
          target_fingerprint: title.targetFingerprint,
        }
      }),
    }
    const { router } = renderRoute('/library?lens=all&preview=case-1&field=title', http)
    const sourceRevision = router.state.location.state.__TSR_key
    let releaseTarget: (() => void) | undefined
    const targetNavigation = new Promise<void>((resolve) => {
      releaseTarget = resolve
    })
    const target = vi.spyOn(router, 'navigate').mockImplementationOnce(async (options) => {
      if (typeof options.search === 'function') options.search(router.state.location.search)
      await targetNavigation
    })
    await user.click(
      await screen.findByRole('button', { name: 'Choose proposed value: New title' }),
    )
    await waitFor(() => expect(http.recordDecision).toHaveBeenCalledOnce())
    expect(reads).toBe(1)
    await act(async () => {
      releaseRefresh?.(current)
    })
    await waitFor(() => expect(target).toHaveBeenCalledOnce())
    await act(async () => {
      router.history.push('/library?lens=all&preview=case-1&field=title')
      releaseTarget?.()
    })

    expect(router.state.location.state.__TSR_key).not.toBe(sourceRevision)
    await waitFor(() => expect(http.getLibrary).toHaveBeenCalledTimes(2))
    expect(router.state.location.search).toMatchObject({
      lens: 'all',
      preview: 'case-1',
    })
    expect(
      screen.queryByText('The decision was not recorded. Your choice is unchanged.'),
    ).toBeNull()
  })

  it('activates a focused choice once with Enter or Space while recording in place', async () => {
    const user = userEvent.setup()
    const deferred = new Promise<never>(() => undefined)
    const http: ReviewHttp = {
      ...createHttp(),
      recordDecision: vi.fn(() => deferred),
    }
    renderRoute('/library?lens=all&preview=case-1&field=title', http)

    const choice = await screen.findByRole('button', { name: 'Choose proposed value: New title' })
    await waitFor(() => expect(document.querySelector('[data-route-focus]')).toHaveFocus())
    choice.focus()
    expect(choice).toHaveFocus()
    await user.keyboard('{Enter}')
    await user.keyboard(' ')
    await waitFor(() => expect(http.recordDecision).toHaveBeenCalledTimes(1))
    expect(http.recordDecision).toHaveBeenCalledWith(
      'case-1',
      expect.objectContaining({
        destination: 'title',
        outcome: 'accept',
        selectedValue: { kind: 'text', value: 'New title' },
      }),
    )
    expect(choice).toHaveFocus()
    expect(screen.getByRole('heading', { name: 'Which title should this work use?' })).toBeVisible()
  })

  it('renders a current-only field as readable context without owner controls', async () => {
    const user = userEvent.setup()
    const titleField = detail.fields[0]
    if (!titleField) throw new Error('workspace fixture requires a title field')
    const contextOnly = {
      ...titleField,
      decisionActions: [],
      needsAttention: false,
      proposedValues: [],
    }
    const subtitleContext = {
      ...contextOnly,
      destination: 'subtitle',
      currentValue: 'Unavailable',
      proposalId: 'proposal-subtitle',
      targetFingerprint: 'fingerprint-subtitle',
    }
    const contextualField = {
      ...contextOnly,
      destination: 'authors',
      inventoryRole: 'contextual' as const,
      proposalId: 'proposal-authors',
      targetFingerprint: 'fingerprint-authors',
    }
    renderRoute('/library?lens=all&preview=case-1&field=title', {
      ...createHttp(),
      getLibrary: vi.fn(async () => ({
        ...library,
        counts: { all: 1, attention: 0, clear: 1, notMatched: 0 },
        works: library.works.map((work) => ({
          ...work,
          workAttentionCount: 0,
          preparationOutcomes: [
            {
              providerRequests: 1,
              laneKind: 'work',
              provider: 'synthetic-catalogue',
              reason: 'No eligible alternative was retained.',
              status: 'no_match',
            },
          ],
        })),
      })),
      getAttention: vi.fn(async () => ({ total: 0, items: [] })),
      getCaseDetail: vi.fn(async () => ({
        ...detail,
        fields: [contextOnly, subtitleContext, contextualField],
      })),
    })

    expect(await screen.findByRole('heading', { name: 'Title' })).toBeVisible()
    expect(screen.getAllByLabelText('No eligible change')).toHaveLength(2)
    expect(screen.getByRole('button', { name: 'Attention 0 works' })).toBeVisible()
    expect(screen.getByText('There’s no other value to choose from.')).toBeVisible()
    expect(screen.getByLabelText('Title current value')).toHaveTextContent('Current valueOld title')
    const evidence = screen.getByRole('button', { name: 'Evidence' })
    await user.click(evidence)
    expect(screen.getByText('Retained evidence is unavailable for this choice.')).toBeVisible()
    const hideEvidence = screen.getByRole('button', { name: 'Hide evidence' })
    expect(hideEvidence).toHaveFocus()
    await user.click(hideEvidence)
    expect(screen.getByRole('button', { name: 'Evidence' })).toHaveFocus()
    expect(screen.queryByText('Retained evidence is unavailable for this choice.')).toBeNull()
    expect(screen.queryByText('Which title should this work use?')).toBeNull()
    expect(screen.queryByText('Queue position')).toBeNull()
    expect(screen.queryByText('Choices')).toBeNull()
    expect(screen.queryByRole('button', { name: /Choose current value/u })).toBeNull()
    expect(screen.queryByRole('button', { name: /Choose proposed value/u })).toBeNull()
    expect(screen.queryByRole('button', { name: /Take no action|Reject/u })).toBeNull()
    expect(screen.queryByText('This field needs your review.')).toBeNull()
    expect(screen.queryByText(/needs your review/u)).toBeNull()
    expect(screen.queryByText(/Which .* should this work use/u)).toBeNull()
    expect(screen.queryByLabelText('External update status')).toBeNull()
    expect(
      screen.queryByText(
        'The decision can still be recorded; no external update capability is configured.',
      ),
    ).toBeNull()
    expect(screen.getByText('Sources')).toBeVisible()
    expect(screen.getByText('1 lookup recorded for this work')).toBeVisible()
    expect(screen.getByText('1 field has context')).toBeVisible()
    expect(screen.queryByText('field with contexts')).toBeNull()
    await user.click(screen.getByRole('button', { name: /Subtitle.*No eligible change/u }))
    expect(await screen.findByRole('heading', { name: 'Subtitle' })).toBeVisible()
    expect(screen.queryByText('This field needs your review.')).toBeNull()
    expect(screen.queryByText('Which subtitle should this work use?')).toBeNull()
    expect(screen.queryByText('Queue position')).toBeNull()
    expect(screen.queryByText('Choices')).toBeNull()
    expect(screen.getByLabelText('Subtitle current value')).toHaveTextContent(
      'Current valueUnavailable',
    )
    expect(screen.getByText('Sources')).toBeVisible()
  })

  it('keeps needs-review and recordability wording for an actionable field', async () => {
    renderRoute('/library?lens=all&preview=case-1&field=title', createHttp())

    expect(await screen.findByText(/This field needs your review\./u)).toBeVisible()
    expect(
      screen.getByText(
        'Selecting an outcome records this field only. Audiobookshelf is unchanged until writes are enabled.',
      ),
    ).toBeVisible()
  })

  it.each([
    [new ApiError(503, 'persistence'), 'The decision was not recorded. Your choice is unchanged.'],
    [new CsrfRenewedError(), 'Your session was refreshed. Press again.'],
  ])(
    'retries the embedded Library decision after %s with the exact preserved command and idempotency key',
    async (failure, copy) => {
      const user = userEvent.setup()
      const titleField = detail.fields[0]
      if (!titleField) throw new Error('workspace fixture requires a title field')
      const settled: CaseDetailView = {
        ...detail,
        fields: [
          {
            ...titleField,
            needsAttention: false,
            humanDecision: {
              actor: 'owner',
              application: 'apply' as const,
              decision_id: 'decision-retried',
              destination: 'title',
              occurred_at: 1,
              outcome: 'keep_current',
              proposal_id: titleField.proposalId,
              review_case_id: 'case-1',
              selected_value: null,
              target_fingerprint: titleField.targetFingerprint,
            },
          },
        ],
      }
      let current = detail
      let attempts = 0
      const recordDecision = vi.fn<ReviewHttp['recordDecision']>(async () => {
        attempts += 1
        if (attempts === 1) throw failure
        current = settled
        return {
          confirmation: { status: 'confirmed' as const, case: current },
          invalidation_sequences: [],
          schema_version: 'review-decision-v1' as const,
          actor: 'owner' as const,
          application: 'apply' as const,
          decision_id: 'decision-retried',
          destination: 'title',
          occurred_at: 1,
          outcome: 'keep_current' as const,
          proposal_id: titleField.proposalId,
          review_case_id: 'case-1',
          selected_value: null,
          target_fingerprint: titleField.targetFingerprint,
        }
      })
      const http: ReviewHttp = {
        ...createHttp(),
        getCaseDetail: vi.fn(async () => current),
        recordDecision,
      }
      renderRoute('/library?lens=all&preview=case-1&field=title', http)

      await user.click(
        await screen.findByRole('button', { name: 'Choose current value: Old title' }),
      )
      expect(await screen.findByRole('alert')).toHaveTextContent(copy)
      expect(
        screen.getByRole('button', { name: 'Choose current value: Old title' }),
      ).not.toHaveTextContent('Recording…')
      expect(
        screen.getByRole('button', { name: 'Choose current value: Old title' }),
      ).toHaveAttribute('aria-pressed', 'false')
      expect(screen.queryByText('Choice recorded.')).toBeNull()
      expect(document.body.textContent).not.toContain('Recording accept…')
      expect(document.body.textContent).not.toContain('Recording choice…')
      expect(screen.getByText('1 remaining · field 1 of 1')).toBeVisible()
      expect(screen.getByRole('button', { name: /Title.*Needs review/u })).toBeVisible()
      const retry = screen.getByRole('button', { name: 'Try the same decision again' })
      expect(retry).toHaveFocus()
      const firstCommand = recordDecision.mock.calls[0]?.[1]
      await user.click(retry)

      await waitFor(() => expect(recordDecision).toHaveBeenCalledTimes(2))
      expect(recordDecision.mock.calls[1]?.[1]).toEqual(firstCommand)
      expect(recordDecision.mock.calls[1]?.[1].idempotencyKey).toBe(firstCommand?.idempotencyKey)
      expect(await screen.findByText('Review complete')).toBeVisible()
      expect(
        screen.getByRole('button', { name: /Title.*Current value retained.*Old title/u }),
      ).toBeVisible()
      expect(document.activeElement).toHaveClass('review-complete')
    },
  )

  describe("a recording's optional choice (#1249)", () => {
    const title = detail.fields[0]
    if (!title) throw new Error('optional choice fixture requires a title field')
    const asin = {
      ...title,
      destination: 'asin',
      classification: 'owner_choosable' as const,
      classificationReason:
        'No review is needed. You can still keep the current value or choose a proposed one.',
      needsAttention: false,
      navigationRole: 'inspect' as const,
      editable: true,
      currentValue: 'Unavailable',
      currentRaw: null,
      proposedValues: [{ kind: 'text' as const, value: 'B0CW1KL2QD' }],
      proposalId: 'proposal-asin',
    }
    const recording: CaseDetailView = { ...detail, fields: [title, asin] }
    const settle = (field: typeof title, outcome: 'accept' | 'keep_current') => {
      const decision = {
        actor: 'owner',
        application: 'apply' as const,
        decision_id: `decision-${field.destination}`,
        destination: field.destination,
        occurred_at: 1,
        outcome,
        proposal_id: field.proposalId,
        review_case_id: 'case-1',
        selected_value: outcome === 'accept' ? (field.proposedValues[0] ?? null) : null,
        target_fingerprint: field.targetFingerprint,
      }
      const settled: CaseDetailView = {
        ...recording,
        fields: recording.fields.map((value) =>
          value.destination === field.destination
            ? {
                ...value,
                classification: 'human_recorded' as const,
                needsAttention: false,
                navigationRole: 'inspect' as const,
                humanDecision: decision,
              }
            : value,
        ),
      }
      return { decision, settled }
    }

    it('is offered without counting it or naming it for review', async () => {
      const user = userEvent.setup()
      const { decision, settled } = settle(asin, 'accept')
      let current = recording
      const recordDecision = vi.fn<ReviewHttp['recordDecision']>(async () => {
        current = settled
        return {
          confirmation: { status: 'confirmed' as const, case: current },
          invalidation_sequences: [],
          schema_version: 'review-decision-v1' as const,
          ...decision,
        }
      })
      renderRoute('/library?lens=all&preview=case-1', {
        ...createHttp(),
        getCaseDetail: vi.fn(async () => current),
        recordDecision,
      })

      expect(
        await screen.findByRole('button', { name: 'Choose current value: Old title' }),
      ).toBeVisible()
      expect(screen.getByText('Needs review').nextElementSibling).toHaveTextContent('1')
      expect(screen.getByText('1 remaining · field 1 of 2')).toBeVisible()
      const rail = screen.getByRole('button', { name: /^Asin/u })
      const glyph = within(rail).getByLabelText('Optional choice')
      expect(glyph).toHaveTextContent('✓')

      await user.click(rail)
      const choices = await screen.findByRole('region', { name: 'Asin choices' })
      expect(
        within(choices).getByRole('button', { name: 'Choose current value: Unavailable' }),
      ).toBeEnabled()
      const accept = within(choices).getByRole('button', {
        name: 'Choose proposed value: B0CW1KL2QD',
      })
      expect(accept).toBeEnabled()
      expect(screen.queryByText('Queue position')).toBeNull()
      const canvas = document.querySelector('.decision-canvas')
      expect(canvas?.textContent).not.toMatch(/needs (?:your )?review/iu)
      expect(
        within(canvas as HTMLElement).getByText(
          'No review is needed. You can still keep the current value or choose a proposed one.',
        ),
      ).toBeVisible()

      await user.click(accept)
      await waitFor(() => expect(recordDecision).toHaveBeenCalledTimes(1))
      expect(recordDecision.mock.calls[0]?.[1].destination).toBe('asin')
    })

    it('is skipped when the field that needed review is decided', async () => {
      const user = userEvent.setup()
      const { decision, settled } = settle(title, 'keep_current')
      let current = recording
      renderRoute('/library?lens=all&preview=case-1', {
        ...createHttp(),
        getCaseDetail: vi.fn(async () => current),
        recordDecision: vi.fn<ReviewHttp['recordDecision']>(async () => {
          current = settled
          return {
            confirmation: { status: 'confirmed' as const, case: current },
            invalidation_sequences: [],
            schema_version: 'review-decision-v1' as const,
            ...decision,
          }
        }),
      })

      await user.click(
        await screen.findByRole('button', { name: 'Choose current value: Old title' }),
      )
      expect(await screen.findByRole('heading', { name: 'Review complete' })).toBeVisible()
      expect(screen.queryByRole('region', { name: 'Asin choices' })).toBeNull()
    })
  })

  it('clears a stale attempt before reopening its actionable current field', async () => {
    const user = userEvent.setup()
    const recordDecision = vi.fn<ReviewHttp['recordDecision']>(async () => {
      throw new ApiError(409, 'stale')
    })
    renderRoute('/library?lens=all&preview=case-1&field=title', {
      ...createHttp(),
      recordDecision,
    })

    await user.click(
      await screen.findByRole('button', { name: 'Choose proposed value: New title' }),
    )
    await waitFor(() =>
      expect(screen.getByRole('heading', { level: 2, name: 'Works' })).toBeVisible(),
    )
    await user.click(
      screen.getByRole('button', {
        name: 'A Book. Author details unavailable. 1 field needs attention',
      }),
    )

    const proposed = await screen.findByRole('button', { name: 'Choose proposed value: New title' })
    expect(recordDecision).toHaveBeenCalledOnce()
    expect(document.body.textContent).not.toContain('Recording choice…')
    expect(proposed).toHaveAttribute('aria-pressed', 'false')
    expect(proposed).not.toHaveTextContent('Recording…')
    expect(screen.getByText('1 remaining · field 1 of 1')).toBeVisible()
    expect(screen.getByRole('button', { name: /Title.*Needs review/u })).toBeVisible()
  })

  it.each([
    ['a direct replacement choice on the same case', null, false],
    ['a direct replacement choice beside another unresolved field', null, true],
    ['a direct replacement whose recorded case refresh fails', null, false],
    ['an explicit replacement recovery on the same case', null, false],
    ['an explicit recovery with the same consideration', null, false],
    ['an explicit successor-case recovery', 'case-2', false],
    ['an explicit retired-case recovery', 'case-2', false],
    ['an explicit retired-case recovery with unavailable detail', 'case-2', false],
  ])('retains a stale cover choice and recovers through %s', async (label, successorId, other) => {
    const user = userEvent.setup()
    const directChoice = label.startsWith('a direct')
    const failRecordedRefresh = label.includes('recorded case refresh fails')
    const base = detail.fields[0]
    if (!base) throw new Error('cover stale fixture requires a field')
    const candidate = (suffix: string, incumbent: boolean) => ({
      artifactId: `cover_${suffix.repeat(64)}`,
      deterministicCitations: [`citation-${suffix}`],
      deterministicRank: incumbent ? null : 0,
      deterministicRankKey: [1, 0, -960000, -800, -1200, incumbent ? 0 : 1, suffix],
      height: 1200,
      identity: { outcome: 'verified_work_match' as const },
      incumbent,
      matchState: 'same_book' as const,
      observation: null,
      originalSha256: suffix.repeat(64),
      ordinal: incumbent ? 0 : 1,
      previewSha256: suffix.repeat(64),
      reference: `cover_transport_ref_${suffix.repeat(64)}`,
      selectable: !incumbent,
      manualEligible: !incumbent,
      sourceKinds: [incumbent ? 'epub' : 'provider'],
      state: incumbent ? ('incumbent' as const) : ('acceptable' as const),
      width: 800,
    })
    const coverDetail = (id: string, alternative: string): CaseDetailView => ({
      ...detail,
      id,
      fields: [
        {
          ...base,
          destination: 'cover',
          currentValue: `cover_${'1'.repeat(64)}`,
          currentRaw: `cover_${'1'.repeat(64)}`,
          proposedValues: [{ kind: 'cover', value: `cover_${alternative.repeat(64)}` }],
          proposalId: `proposal-${id}`,
          targetFingerprint: `fingerprint-${id}`,
          coverConsideration: {
            id: `cover_consideration_${alternative.repeat(64)}`,
            choiceContexts: [],
            current: true,
            candidates: [candidate('1', true), candidate(alternative, false)],
            incumbent: {
              reference: `cover_transport_ref_${'1'.repeat(64)}`,
              state: 'available',
            },
            omissions: [],
            policy: {
              recommended_candidate_reference: `cover_transport_ref_${alternative.repeat(64)}`,
              acceptable_candidate_references: [`cover_transport_ref_${alternative.repeat(64)}`],
              status: 'recommended',
              reasons: [],
            },
            policyPreferences: {
              avoidPromotionalText: false,
              avoidTieInBranding: false,
              preferMinimalComposition: false,
            },
            policyReference: `cover_policy_${'3'.repeat(64)}`,
            providerFetching: false,
            ready: true,
            reviewerAccessRevision: null,
            reviewerEngine: null,
            reviewerModel: null,
            reviewerStatus: 'unavailable',
            reviewerUnavailableReason: null,
            targetFingerprint: `fingerprint-${id}`,
          },
        },
        ...(other ? [base] : []),
      ],
    })
    const initial = coverDetail('case-1', '2')
    const current = coverDetail(successorId ?? 'case-1', '3')
    let retainSameConsideration = label.includes('same consideration')
    let stale = false
    let failCurrentLookup = true
    let successfulDecisions = 0
    let recordedRefreshAvailable = false
    const defaultHttp = createHttp()
    const recordDecision = vi.fn<ReviewHttp['recordDecision']>(async (...args) => {
      if (!stale) {
        stale = true
        throw new ApiError(409, 'stale')
      }
      const acknowledgement = await defaultHttp.recordDecision(...args)
      successfulDecisions += 1
      if (!('actor' in acknowledgement)) throw new Error('ordinary cover decision required')
      if (failRecordedRefresh && !recordedRefreshAvailable)
        return { ...acknowledgement, confirmation: { status: 'unavailable' } }
      return recordedAcknowledgement(current, acknowledgement)
    })
    const http: ReviewHttp = {
      ...createHttp(),
      recordDecision,
      getLibrary: vi.fn(async () =>
        label.includes('retired-case') && stale
          ? { ...library, works: library.works.filter((work) => work.reviewCaseId !== initial.id) }
          : library,
      ),
      getCaseDetail: vi.fn(async (id) => {
        if (!stale || retainSameConsideration) return initial
        if (label.includes('unavailable detail') && id === initial.id)
          throw new Error('retired case unavailable')
        if (failRecordedRefresh && successfulDecisions === 1 && !recordedRefreshAvailable) {
          throw new Error('recorded current case refresh unavailable')
        }
        if (id === current.id) return current
        return { ...initial, current: false }
      }),
      getCaseSuccessor: vi.fn(async () => {
        if (failCurrentLookup) {
          failCurrentLookup = false
          throw new Error('current comparison unavailable')
        }
        return successorId
      }),
    }
    const { queryClient } = renderRoute('/library?lens=all&preview=case-1&field=cover', http)

    await user.click(await screen.findByRole('button', { name: /Alternative 1 cover/u }))
    await user.click(screen.getByRole('button', { name: 'Confirm cover choice' }))
    if (retainSameConsideration) {
      expect(await screen.findByText(/Confirmation was not completed/u)).toBeVisible()
      expect(recordDecision).toHaveBeenCalledOnce()
      await user.click(screen.getByRole('button', { name: 'Close' }))
      expect(screen.getByRole('button', { name: /Alternative 1 cover/u })).toHaveFocus()
      retainSameConsideration = false
    }
    expect(
      await screen.findByText(
        'This cover comparison changed before your choice was recorded. No cover was changed.',
      ),
    ).toBeVisible()
    expect(screen.getByRole('heading', { level: 2, name: 'A Book' })).toBeVisible()
    if (label.includes('retired-case')) {
      await queryClient.invalidateQueries({ queryKey: ['library'] })
      await waitFor(() =>
        expect(screen.getByRole('button', { name: 'Review current comparison' })).toHaveFocus(),
      )
      expect(screen.getAllByRole('heading', { level: 2, name: 'A Book' })).toHaveLength(1)
    }
    expect(document.body.textContent).not.toContain('Recording…')
    expect(recordDecision).toHaveBeenCalledOnce()

    await user.click(screen.getByRole('button', { name: 'Review current comparison' }))
    expect(
      await screen.findByText(
        'The current comparison could not be loaded. Your prior choice remains unrecorded.',
      ),
    ).toBeVisible()
    expect(screen.getByRole('button', { name: 'Review current comparison' })).toBeEnabled()

    if (directChoice) {
      const currentAlternative = screen.getByRole('button', {
        name: /Alternative 1 cover/u,
      })
      expect(within(currentAlternative).getByRole('img')).toHaveAttribute(
        'src',
        expect.stringContaining(`cover_transport_ref_${'3'.repeat(64)}`),
      )
      await user.click(currentAlternative)
      await user.click(screen.getByRole('button', { name: 'Confirm cover choice' }))
      await waitFor(() => expect(recordDecision).toHaveBeenCalledTimes(2))
      expect(
        screen.queryByText(
          'This cover comparison changed before your choice was recorded. No cover was changed.',
        ),
      ).toBeNull()
      expect(screen.queryByRole('button', { name: 'Review current comparison' })).toBeNull()
      if (failRecordedRefresh) {
        expect(
          await screen.findByText(
            'Decision recorded, but the current case could not be refreshed. Reload before reviewing another value.',
          ),
        ).toBeVisible()
        expect(screen.queryByRole('button', { name: /Alternative 1 cover/u })).toBeNull()
        const reconciliation = queryClient.getQueryData<{
          destination: string
          message: string
          detailUpdatedAt: number
        }>(reconciliationKey('case-1'))
        expect(reconciliation?.destination).toBe('cover')
        expect(reconciliation?.message).toBe(
          'Decision recorded, but the current case could not be refreshed. Reload before reviewing another value.',
        )
        expect(typeof reconciliation?.detailUpdatedAt).toBe('number')
        expect(recordDecision).toHaveBeenCalledTimes(2)
        expect(successfulDecisions).toBe(1)
        recordedRefreshAvailable = true
        await user.click(screen.getByRole('button', { name: 'Reload current case' }))
        expect(await screen.findByRole('button', { name: /Alternative 1 cover/u })).toBeVisible()
        await waitFor(() =>
          expect(queryClient.getQueryData(reconciliationKey('case-1'))).toBeNull(),
        )
        return
      }
      if (other) {
        expect(await screen.findByRole('button', { name: /Title.*Needs review/u })).toBeVisible()
      } else {
        expect(await screen.findByText('Review complete')).toBeVisible()
      }
      return
    }

    await user.click(screen.getByRole('button', { name: 'Review current comparison' }))
    const currentAlternative = await screen.findByRole('button', {
      name: /Alternative 1 cover/u,
    })
    expect(within(currentAlternative).getByRole('img')).toHaveAttribute(
      'src',
      expect.stringContaining(`cover_transport_ref_${'3'.repeat(64)}`),
    )
    expect(
      screen.queryByText(
        'This cover comparison changed before your choice was recorded. No cover was changed.',
      ),
    ).toBeNull()
    expect(recordDecision).toHaveBeenCalledOnce()
  })

  it.each(['pointer', 'keyboard'])(
    'does not carry failed same-field advancement into a manual field change via %s',
    async (input) => {
      const user = userEvent.setup()
      const titleField = detail.fields[0]
      if (!titleField) throw new Error('workspace fixture requires a title field')
      const authorsField = {
        ...titleField,
        destination: 'authors',
        currentValue: 'Ada Example',
        proposalId: 'proposal-authors',
        targetFingerprint: 'fingerprint-authors',
      }
      const http: ReviewHttp = {
        ...createHttp(),
        getCaseDetail: vi.fn(async () => ({ ...detail, fields: [titleField, authorsField] })),
        recordDecision: vi.fn(async () => {
          throw new ApiError(503, 'unavailable')
        }),
      }
      const scrollTo = vi.mocked(window.scrollTo)
      scrollTo.mockClear()
      const { router } = renderRoute('/library?lens=all&preview=case-1&field=title', http)
      const navigate = vi.spyOn(router, 'navigate')

      await user.click(
        await screen.findByRole('button', { name: 'Choose current value: Old title' }),
      )
      expect(
        await screen.findByText('The decision was not recorded. Your choice is unchanged.'),
      ).toBeVisible()
      scrollTo.mockClear()
      const authors = screen.getByRole('button', { name: /Authors.*Needs review/u })
      if (input === 'pointer') await user.click(authors)
      else {
        authors.focus()
        await user.keyboard('{Enter}')
      }

      await waitFor(() => expect(authors).toHaveAttribute('aria-current', 'true'))
      await act(
        async () => await new Promise<void>((resolve) => requestAnimationFrame(() => resolve())),
      )
      expect(navigate).toHaveBeenLastCalledWith(expect.objectContaining({ resetScroll: false }))
      expect(authors).toHaveFocus()
      expect(scrollTo).not.toHaveBeenCalledWith({ top: 0 })
    },
  )

  it('preserves final completion when the reviewer moves on while a decision settles', async () => {
    const user = userEvent.setup()
    const titleField = detail.fields[0]
    if (!titleField) throw new Error('workspace fixture requires a title field')
    const recordedAuthors = {
      ...titleField,
      destination: 'authors',
      needsAttention: false,
      proposalId: 'proposal-authors',
      targetFingerprint: 'fingerprint-authors',
      humanDecision: {
        actor: 'owner',
        application: 'apply' as const,
        decision_id: 'decision-authors',
        destination: 'authors',
        occurred_at: 1,
        outcome: 'keep_current' as const,
        proposal_id: 'proposal-authors',
        review_case_id: 'case-1',
        selected_value: null,
        target_fingerprint: 'fingerprint-authors',
      },
    }
    let current: CaseDetailView = { ...detail, fields: [titleField, recordedAuthors] }
    const http: ReviewHttp = {
      ...createHttp(),
      getCaseDetail: vi.fn(async () => current),
      recordDecision: vi.fn(
        // eslint-disable-next-line @typescript-eslint/no-unused-vars -- ReviewHttp includes cancellation unused by this scenario.
        async (_reviewCaseId: string, command: DecisionCommand, _signal?: AbortSignal) => {
          if (command.outcome === 'accept' && command.selectedValue === undefined) {
            throw new Error('accept command requires its selected value')
          }
          const selectedValue = command.selectedValue ?? null
          current = {
            ...detail,
            fields: [
              {
                ...titleField,
                needsAttention: false,
                humanDecision: {
                  actor: 'owner',
                  application: 'apply' as const,
                  decision_id: `decision-title-${command.outcome}`,
                  destination: 'title',
                  occurred_at: 1,
                  outcome: command.outcome,
                  proposal_id: titleField.proposalId,
                  review_case_id: 'case-1',
                  selected_value: selectedValue,
                  target_fingerprint: titleField.targetFingerprint,
                },
              },
              recordedAuthors,
            ],
          }
          return {
            confirmation: { status: 'confirmed' as const, case: current },
            invalidation_sequences: [],
            schema_version: 'review-decision-v1',
            actor: 'owner',
            application: 'apply' as const,
            decision_id: `decision-title-${command.outcome}`,
            destination: 'title',
            occurred_at: 1,
            outcome: command.outcome,
            proposal_id: titleField.proposalId,
            review_case_id: 'case-1',
            selected_value: selectedValue,
            target_fingerprint: titleField.targetFingerprint,
          }
        },
      ),
    }
    renderRoute('/library?lens=all&preview=case-1&field=title', http)

    await user.click(await screen.findByRole('button', { name: 'Choose current value: Old title' }))
    expect(await screen.findByText('Review complete')).toBeVisible()
    // The same exact current field remains voluntarily revisitable, without
    // returning to the Attention queue. A second choice is a new command.
    await user.click(screen.getByRole('button', { name: /Title.*Decision recorded/u }))
    const replacement = await screen.findByRole('button', {
      name: 'Choose proposed value: New title',
    })
    await user.click(replacement)
    await waitFor(() => expect(http.recordDecision).toHaveBeenCalledTimes(2))
    const [first, second] = vi.mocked(http.recordDecision).mock.calls.map(([, command]) => command)
    expect(first?.idempotencyKey).not.toBe(second?.idempotencyKey)
    await user.click(screen.getByRole('button', { name: /Title.*Decision recorded/u }))
    expect(
      screen.getByRole('button', { name: 'Choose proposed value: New title' }),
    ).toHaveAttribute('aria-pressed', 'true')
    expect(screen.queryByText('Queue position')).toBeNull()
  })

  it('keeps visible route focus while an uncached selected detail loads', async () => {
    const user = userEvent.setup()
    let release: ((value: CaseDetailView) => void) | undefined
    const pendingDetail = new Promise<CaseDetailView>((resolve) => {
      release = resolve
    })
    renderRoute('/library?lens=all', {
      ...createHttp(),
      getCaseDetail: vi.fn(async () => pendingDetail),
    })

    await user.click(await screen.findByRole('button', { name: /^A Book\./u }))
    expect(await screen.findByText('Loading selected work…')).toBeVisible()
    const loadingRegion = screen.getByText('Loading selected work…').closest('section')
    expect(loadingRegion).toHaveClass('workspace-detail')
    expect(loadingRegion).toHaveAttribute('data-route-focus', 'true')
    await waitFor(() => expect(loadingRegion).toHaveFocus())
    await act(async () => {
      release?.(detail)
    })
    expect(
      await screen.findByRole('button', { name: 'Choose current value: Old title' }),
    ).toBeVisible()
    await waitFor(() => expect(document.querySelector('[data-route-focus]')).toHaveFocus())
  })

  it('keeps a persistent control focused when selected detail becomes ready', async () => {
    const user = userEvent.setup()
    let release: ((value: CaseDetailView) => void) | undefined
    const pendingDetail = new Promise<CaseDetailView>((resolve) => {
      release = resolve
    })
    renderRoute('/library?lens=all', {
      ...createHttp(),
      getCaseDetail: vi.fn(async () => pendingDetail),
    })

    await user.click(await screen.findByRole('button', { name: /^A Book\./u }))
    const loadingRegion = screen.getByText('Loading selected work…').closest('section')
    if (!loadingRegion) throw new Error('loading detail requires a workspace region')
    await waitFor(() => expect(loadingRegion).toHaveFocus())
    const back = screen.getByRole('button', { name: 'Back to works' })
    back.focus()
    expect(back).toHaveFocus()
    await act(async () => {
      release?.(detail)
    })
    expect(
      await screen.findByRole('button', { name: 'Choose current value: Old title' }),
    ).toBeVisible()
    await act(
      async () => await new Promise<void>((resolve) => requestAnimationFrame(() => resolve())),
    )
    expect(back).toHaveFocus()
  })

  it('restores selected detail focus after its retry control is replaced', async () => {
    const user = userEvent.setup()
    let release: ((value: CaseDetailView) => void) | undefined
    const pendingDetail = new Promise<CaseDetailView>((resolve) => {
      release = resolve
    })
    let detailReads = 0
    renderRoute('/library?lens=all&preview=case-1&field=title', {
      ...createHttp(),
      getCaseDetail: vi.fn(async () => {
        detailReads += 1
        if (detailReads === 1) throw new Error('temporary selected detail failure')
        return pendingDetail
      }),
    })

    const retry = await screen.findByRole('button', { name: 'Try again' })
    retry.focus()
    expect(retry).toHaveFocus()
    await user.click(retry)
    expect(await screen.findByText('Loading selected work…')).toBeVisible()
    expect(document.body).toHaveFocus()
    await act(async () => {
      release?.(detail)
    })
    expect(
      await screen.findByRole('button', { name: 'Choose current value: Old title' }),
    ).toBeVisible()
    await waitFor(() => expect(document.querySelector('[data-route-focus]')).toHaveFocus())
  })

  it('restores retry focus when selected detail retry fails', async () => {
    const user = userEvent.setup()
    let rejectRetry: ((error: Error) => void) | undefined
    const pendingRetry = new Promise<CaseDetailView>((_resolve, reject) => {
      rejectRetry = reject
    })
    let detailReads = 0
    renderRoute('/library?lens=all&preview=case-1&field=title', {
      ...createHttp(),
      getCaseDetail: vi.fn(async () => {
        detailReads += 1
        if (detailReads === 1) throw new Error('initial selected detail failure')
        return pendingRetry
      }),
    })

    const retry = await screen.findByRole('button', { name: 'Try again' })
    retry.focus()
    await user.click(retry)
    expect(await screen.findByText('Loading selected work…')).toBeVisible()
    expect(document.body).toHaveFocus()
    await act(async () => {
      rejectRetry?.(new Error('retry selected detail failure'))
    })
    const remountedRetry = await screen.findByRole('button', { name: 'Try again' })
    expect(remountedRetry).toHaveFocus()
  })

  it('keeps a persistent control focused during selected detail retry', async () => {
    const user = userEvent.setup()
    let release: ((value: CaseDetailView) => void) | undefined
    const pendingDetail = new Promise<CaseDetailView>((resolve) => {
      release = resolve
    })
    let detailReads = 0
    renderRoute('/library?lens=all&preview=case-1&field=title', {
      ...createHttp(),
      getCaseDetail: vi.fn(async () => {
        detailReads += 1
        if (detailReads === 1) throw new Error('temporary selected detail failure')
        return pendingDetail
      }),
    })

    await user.click(await screen.findByRole('button', { name: 'Try again' }))
    expect(await screen.findByText('Loading selected work…')).toBeVisible()
    const back = screen.getByRole('button', { name: 'Back to works' })
    back.focus()
    expect(back).toHaveFocus()
    await act(async () => {
      release?.(detail)
    })
    expect(
      await screen.findByRole('button', { name: 'Choose current value: Old title' }),
    ).toBeVisible()
    await act(
      async () => await new Promise<void>((resolve) => requestAnimationFrame(() => resolve())),
    )
    expect(back).toHaveFocus()
  })

  it('keeps a persistent control focused when selected detail retry fails', async () => {
    const user = userEvent.setup()
    let rejectRetry: ((error: Error) => void) | undefined
    const pendingRetry = new Promise<CaseDetailView>((_resolve, reject) => {
      rejectRetry = reject
    })
    let detailReads = 0
    renderRoute('/library?lens=all&preview=case-1&field=title', {
      ...createHttp(),
      getCaseDetail: vi.fn(async () => {
        detailReads += 1
        if (detailReads === 1) throw new Error('initial selected detail failure')
        return pendingRetry
      }),
    })

    await user.click(await screen.findByRole('button', { name: 'Try again' }))
    expect(await screen.findByText('Loading selected work…')).toBeVisible()
    const back = screen.getByRole('button', { name: 'Back to works' })
    back.focus()
    expect(back).toHaveFocus()
    await act(async () => {
      rejectRetry?.(new Error('retry selected detail failure'))
    })
    await screen.findByRole('button', { name: 'Try again' })
    expect(back).toHaveFocus()
  })

  it('suppresses workspace decisions for a superseded case', async () => {
    const http: ReviewHttp = {
      ...createHttp(),
      getCaseDetail: vi.fn(async () => ({ ...detail, current: false })),
    }
    renderRoute('/library?lens=all&preview=case-1', http)

    expect(
      await screen.findByText(
        'This review case is no longer current. Reopen its current replacement from Library.',
      ),
    ).toHaveAttribute('role', 'alert')
    expect(screen.queryByRole('button', { name: /Current value/ })).toBeNull()
    expect(screen.queryByRole('button', { name: /Specific proposed value/ })).toBeNull()
  })

  it('preserves the selected field in Library and clears it when another work is selected', async () => {
    const user = userEvent.setup()
    const titleField = detail.fields[0]
    const firstWork = library.works[0]
    if (!titleField || !firstWork) throw new Error('workspace fixture requires a field and work')
    const authorsField = {
      ...titleField,
      destination: 'authors',
      proposalId: 'proposal-authors',
      targetFingerprint: 'fingerprint-authors',
    }
    const secondWork = { ...firstWork, id: 'work-2', reviewCaseId: 'case-2', title: 'Second work' }
    const http: ReviewHttp = {
      ...createHttp(),
      getLibrary: vi.fn(async () => ({
        ...library,
        counts: { ...library.counts, all: 2 },
        works: [firstWork, secondWork],
      })),
      getCaseDetail: vi.fn(async (id: string) => ({
        ...detail,
        id,
        title: id === 'case-2' ? 'Second work' : detail.title,
        fields: [titleField, authorsField],
      })),
    }
    const { router } = renderRoute('/library?lens=all&preview=case-1&field=authors', http)

    expect(screen.queryByRole('link', { name: /Open full review|Open all evidence/u })).toBeNull()
    await user.click(await screen.findByRole('button', { name: /^Second work\./u }))
    await waitFor(() =>
      expect(router.state.location.search).toEqual({ lens: 'all', preview: 'case-2' }),
    )
  })

  it.each([undefined, 'starting-page'])(
    'carries the actual later Attention page through a fresh diagnostic return from %s',
    async (start) => {
      const user = userEvent.setup()
      const title = detail.fields[0]
      if (!title) throw new Error('title fixture required')
      const item = {
        id: 'case-1',
        destination: 'title',
        title: 'A Book',
        authors: [],
        library: 'Books',
        mediaKind: 'ebook',
        notices: [],
        provenance: [],
        workAttentionCount: 1,
      }
      const target = {
        ...item,
        id: 'case-2',
        destination: 'authors',
        title: 'Later attention target',
      }
      const http = createHttp()
      http.getAttention = vi.fn<ReviewHttp['getAttention']>(async ({ cursor }) =>
        cursor === 'later-page'
          ? { total: 2, items: [target] }
          : { total: 2, items: [item], nextCursor: 'later-page' },
      )
      http.getCaseDetail = vi.fn<ReviewHttp['getCaseDetail']>(async (id) =>
        id === 'case-2'
          ? {
              ...detail,
              id,
              title: target.title,
              fields: [
                {
                  ...title,
                  destination: 'authors',
                  currentValue: 'Later author',
                  currentRaw: 'Later author',
                },
              ],
            }
          : detail,
      )
      expect(
        (await http.getLibrary({ lens: 'all' })).works.some(
          (work) => work.reviewCaseId === target.id,
        ),
      ).toBe(false)
      expect(
        (await http.getAttention(start ? { cursor: start } : {})).items.some(
          (row) => row.id === target.id,
        ),
      ).toBe(false)
      const initial = `/library?lens=all&q=needle&cursor=library-page${start ? '&attentionCursor=starting-page' : ''}`
      const first = renderRoute(initial, http)
      await user.click(await screen.findByRole('button', { name: /^Attention/u }))
      await user.click(await screen.findByRole('button', { name: 'Load more attention' }))
      await user.click(await screen.findByRole('button', { description: 'Later attention target' }))
      await waitFor(() =>
        expect(first.router.state.location.search).toMatchObject({
          attentionCursor: 'later-page',
          preview: 'case-2',
          field: 'authors',
          q: 'needle',
          cursor: 'library-page',
          lens: 'all',
        }),
      )
      expect(await screen.findByRole('heading', { name: target.title })).toBeVisible()
      await user.click(screen.getByRole('link', { name: 'Evidence' }))
      await user.click(await screen.findByRole('link', { name: 'History' }))
      const emitted = first.router.state.location.href
      cleanup()
      const fresh = renderRoute(emitted, http)
      expect(fresh.queryClient).not.toBe(first.queryClient)
      await user.click(await screen.findByRole('link', { name: 'Back to work' }))
      expect(await screen.findByRole('heading', { name: target.title })).toBeVisible()
      expect(
        await screen.findByRole('button', { name: 'Choose current value: Later author' }),
      ).toBeVisible()
      expect(fresh.router.state.location.search).toMatchObject({
        attentionCursor: 'later-page',
        field: 'authors',
        preview: 'case-2',
        q: 'needle',
        cursor: 'library-page',
      })
      cleanup()
      // Without the Attention page the case read still names the work (#1365).
      renderRoute('/library?lens=all&preview=case-2&field=authors', http)
      expect(await screen.findByRole('heading', { name: target.title })).toBeVisible()
      expect(
        await screen.findByRole('button', { name: 'Choose current value: Later author' }),
      ).toBeVisible()
      expect(
        screen.queryByText('This work is not available in the current Library result.'),
      ).toBeNull()
    },
  )

  it.each([undefined, 'starting-page'])(
    'retains field-specific Attention page provenance from %s and preserves ordinary field changes',
    async (start) => {
      const user = userEvent.setup()
      const title = detail.fields[0]
      if (!title) throw new Error('title fixture required')
      const item = {
        id: 'case-2',
        destination: 'title',
        title: 'Split field work',
        authors: [],
        library: 'Books',
        mediaKind: 'ebook',
        notices: [],
        provenance: [],
        workAttentionCount: 2,
      }
      const http = createHttp()
      http.getAttention = vi.fn<ReviewHttp['getAttention']>(async ({ cursor }) =>
        cursor === 'later-page'
          ? { total: 2, items: [{ ...item, destination: 'authors' }] }
          : { total: 2, items: [item], nextCursor: 'later-page' },
      )
      http.getCaseDetail = vi.fn<ReviewHttp['getCaseDetail']>(async (id) => ({
        ...detail,
        id,
        fields: [title, { ...title, destination: 'authors' }],
      }))
      const { router } = renderRoute(
        `/library?lens=all&attention=open${start ? '&attentionCursor=starting-page' : ''}`,
        http,
      )
      await user.click(
        await screen.findByRole('button', { name: /^Title/u, description: 'Split field work' }),
      )
      await waitFor(() => expect(router.state.location.search.attentionCursor).toBe(start))
      expect(router.state.location.search.attention).toBeUndefined()
      await act(async () => router.history.back())
      expect(await screen.findByLabelText('Attention queue')).toBeVisible()
      await user.click(await screen.findByRole('button', { name: 'Load more attention' }))
      await user.click(
        await screen.findByRole('button', { name: /^Authors/u, description: 'Split field work' }),
      )
      await waitFor(() =>
        expect(router.state.location.search).toMatchObject({
          attentionCursor: 'later-page',
          field: 'authors',
        }),
      )
      await user.click(await screen.findByRole('button', { name: /^Title/u }))
      await waitFor(() =>
        expect(router.state.location.search).toMatchObject({
          attentionCursor: 'later-page',
          field: 'title',
        }),
      )
    },
  )

  it.each(['ready', 'absent', 'failed', 'persistent'] as const)(
    'settles the newly anchored work from its case read whatever its Attention page says: %s',
    async (outcome) => {
      const user = userEvent.setup()
      const title = detail.fields[0]
      if (!title) throw new Error('title fixture required')
      const firstItem = {
        id: 'case-1',
        destination: 'title',
        title: 'A Book',
        authors: [],
        library: 'Books',
        mediaKind: 'ebook',
        notices: [],
        provenance: [],
        workAttentionCount: 1,
      }
      const target = { ...firstItem, id: 'case-2', title: 'Anchored target' }
      type Page = Awaited<ReturnType<ReviewHttp['getAttention']>>
      let settle: ((value: Page) => void) | undefined
      let fail: ((error: Error) => void) | undefined
      let releaseDetail: (() => void) | undefined
      const detailRead = new Promise<void>((resolve) => (releaseDetail = resolve))
      let laterReads = 0
      const http = createHttp()
      http.getAttention = vi.fn<ReviewHttp['getAttention']>(async ({ cursor }) => {
        if (cursor !== 'later-page')
          return { total: 2, items: [firstItem], nextCursor: 'later-page' }
        laterReads += 1
        if (laterReads === 1) return { total: 2, items: [target] }
        return new Promise<Page>((resolve, reject) => {
          settle = resolve
          fail = reject
        })
      })
      http.getCaseDetail = vi.fn<ReviewHttp['getCaseDetail']>(async (id) => {
        if (id !== 'case-2') return detail
        await detailRead
        return {
          ...detail,
          id,
          title: target.title,
          fields: [{ ...title, currentValue: 'Anchored choice' }],
        }
      })
      renderRoute('/library?lens=all&attention=open', http)
      await waitFor(() => expect(screen.getByLabelText('Attention queue')).toHaveFocus())
      await user.click(await screen.findByRole('button', { name: 'Load more attention' }))
      const row = await screen.findByRole('button', { description: 'Anchored target' })
      row.focus()
      await user.keyboard('{Enter}')
      const loading = await screen.findByText('Loading selected work…')
      const workspace = loading.closest('section')
      await waitFor(() => expect(workspace).toHaveFocus())
      const search = screen.getByRole('searchbox')
      if (outcome === 'persistent') search.focus()
      await act(async () => {
        if (outcome === 'failed') fail?.(new Error('anchored identity unavailable'))
        else settle?.({ total: 2, items: outcome === 'absent' ? [] : [target] })
      })
      // Until the case read settles, the work is loading, never absent (#1365).
      expect(screen.getByText('Loading selected work…')).toBeVisible()
      expect(
        screen.queryByText('This work is not available in the current Library result.'),
      ).toBeNull()
      expect(screen.queryByRole('button', { name: 'Try again' })).toBeNull()
      expect(
        screen.queryByRole('button', { name: 'Choose current value: Anchored choice' }),
      ).toBeNull()
      await act(async () => releaseDetail?.())
      const heading = await screen.findByRole('heading', { name: target.title })
      expect(
        await screen.findByRole('button', { name: 'Choose current value: Anchored choice' }),
      ).toBeVisible()
      if (outcome === 'persistent') expect(search).toHaveFocus()
      else await waitFor(() => expect(heading.closest('[data-route-focus]')).toHaveFocus())
    },
  )

  it('settles the newly anchored work after Try again reads its failed case and Attention again: retry', async () => {
    const user = userEvent.setup()
    const title = detail.fields[0]
    if (!title) throw new Error('title fixture required')
    const firstItem = {
      id: 'case-1',
      destination: 'title',
      title: 'A Book',
      authors: [],
      library: 'Books',
      mediaKind: 'ebook',
      notices: [],
      provenance: [],
      workAttentionCount: 1,
    }
    const target = { ...firstItem, id: 'case-2', title: 'Anchored target' }
    type Page = Awaited<ReturnType<ReviewHttp['getAttention']>>
    let settle: ((value: Page) => void) | undefined
    let fail: ((error: Error) => void) | undefined
    let laterReads = 0
    let caseReads = 0
    const http = createHttp()
    http.getAttention = vi.fn<ReviewHttp['getAttention']>(async ({ cursor }) => {
      if (cursor !== 'later-page') return { total: 2, items: [firstItem], nextCursor: 'later-page' }
      laterReads += 1
      if (laterReads === 1) return { total: 2, items: [target] }
      return new Promise<Page>((resolve, reject) => {
        settle = resolve
        fail = reject
      })
    })
    // The anchored case's own read fails twice, then recovers.
    http.getCaseDetail = vi.fn<ReviewHttp['getCaseDetail']>(async (id) => {
      if (id !== 'case-2') return detail
      caseReads += 1
      if (caseReads < 3) throw new ApiError(503, 'unavailable')
      return {
        ...detail,
        id,
        title: target.title,
        fields: [{ ...title, currentValue: 'Anchored choice' }],
      }
    })
    renderRoute('/library?lens=all&attention=open', http)
    await waitFor(() => expect(screen.getByLabelText('Attention queue')).toHaveFocus())
    await user.click(await screen.findByRole('button', { name: 'Load more attention' }))
    const row = await screen.findByRole('button', { description: 'Anchored target' })
    row.focus()
    await user.keyboard('{Enter}')
    await screen.findByText('Loading selected work…')
    await act(async () => fail?.(new Error('anchored identity unavailable')))
    const retry = await screen.findByRole('button', { name: 'Try again' })
    retry.focus()
    await user.click(retry)
    await waitFor(() => expect(laterReads).toBe(3))
    await waitFor(() => expect(caseReads).toBe(2))
    await act(async () => fail?.(new Error('still unavailable')))
    await waitFor(() => expect(screen.getByRole('button', { name: 'Try again' })).toHaveFocus())
    await user.keyboard('{Enter}')
    await waitFor(() => expect(laterReads).toBe(4))
    await act(async () => settle?.({ total: 2, items: [target] }))
    const heading = await screen.findByRole('heading', { name: target.title })
    expect(
      await screen.findByRole('button', { name: 'Choose current value: Anchored choice' }),
    ).toBeVisible()
    expect(caseReads).toBe(3)
    await waitFor(() => expect(heading.closest('[data-route-focus]')).toHaveFocus())
  })

  it.each(['not_found', 'pending'])(
    'calls an off-page work unavailable only once its case read fails: %s',
    async (state) => {
      const http = createHttp()
      http.getCaseDetail = vi.fn<ReviewHttp['getCaseDetail']>(async () => {
        if (state === 'pending') return new Promise<CaseDetailView>(() => undefined)
        throw new ApiError(404, 'not_found')
      })
      renderRoute('/library?lens=all&preview=missing-case', http)
      if (state === 'pending') {
        // The Library result has settled without it, but its own read has not (#1365).
        expect(await screen.findByRole('button', { name: /^A Book\./u })).toBeVisible()
        expect(await screen.findByText('Loading selected work…')).toBeVisible()
        expect(screen.queryByText(/not available in the current Library result/u)).toBeNull()
      } else
        expect(
          await screen.findByText('This work is not available in the current Library result.'),
        ).toBeVisible()
      expect(screen.queryByRole('button', { name: 'Try again' })).toBeNull()
      expect(screen.queryByText('Case superseded')).toBeNull()
      expect(http.getCaseDetail).toHaveBeenCalledWith(
        'missing-case',
        expect.any(AbortSignal),
        'foreground',
      )
    },
  )

  it.each(['before', 'after'] as const)(
    'recovers and paginates the contextual attention queue with the open-focus frames %s the retry',
    async (frames) => {
      const user = userEvent.setup()
      let calls = 0
      const item = {
        id: 'case-1',
        destination: 'title',
        title: 'A Book',
        authors: [],
        library: 'Books',
        mediaKind: 'ebook',
        classificationReason: 'Canonical library attention reason',
        notices: ['internal-library-notice'],
        provenance: [],
        workAttentionCount: 1,
      }
      const http: ReviewHttp = {
        ...createHttp(),
        getAttention: vi.fn(async (search: AttentionSearch) => {
          calls += 1
          if (calls === 1) throw new Error('temporary attention failure')
          return search.cursor
            ? { total: 2, items: [{ ...item, id: 'case-2', title: 'Second queued work' }] }
            : { total: 2, nextCursor: 'next', items: [item] }
        }),
      }
      renderRoute('/library?lens=all', http)

      const open = await screen.findByRole('button', { name: /Attention/ })
      // The rail takes focus two frames after opening; hold those frames so
      // the retry lands on a chosen side of them (#1399).
      const queued = new Map<number, FrameRequestCallback>()
      let nextFrame = 0
      vi.spyOn(window, 'requestAnimationFrame').mockImplementation((callback) => {
        queued.set(++nextFrame, callback)
        return nextFrame
      })
      vi.spyOn(window, 'cancelAnimationFrame').mockImplementation((id) => {
        queued.delete(id)
      })
      const runFrames = async () =>
        await act(async () => {
          for (const callback of queued.values()) callback(0)
          queued.clear()
        })
      await user.click(open)
      const retry = await screen.findByRole('button', { name: 'Try again' })
      const rail = screen.getByLabelText('Attention queue')
      if (frames === 'before') {
        await runFrames()
        expect(rail).toHaveFocus()
      }
      await user.click(retry)
      expect(await within(rail).findByText('A Book')).toBeVisible()
      if (frames === 'after') await runFrames()
      await waitFor(() => expect(rail).toHaveFocus())
      expect(within(rail).getByText(/Canonical library attention reason/u)).toBeVisible()
      expect(within(rail).queryByText('internal-library-notice')).toBeNull()
      await user.click(screen.getByRole('button', { name: 'Load more attention' }))
      expect(await screen.findByText('Second queued work')).toBeVisible()
      expect(http.getAttention).toHaveBeenLastCalledWith(
        { cursor: 'next' },
        expect.any(AbortSignal),
        'foreground',
      )
    },
  )

  it('uses the review-case identity for workspace selection and advances through the frozen attention cohort', async () => {
    const user = userEvent.setup()
    const titleField = detail.fields[0]
    const firstWork = library.works[0]
    if (!titleField) throw new Error('detail fixture requires a title field')
    if (!firstWork) throw new Error('library fixture requires a work')
    const selectedLibrary: LibraryView = {
      ...library,
      works: [{ ...firstWork, id: 'work-identity-is-not-case-identity' }],
    }
    const settled: CaseDetailView = {
      ...detail,
      fields: [
        {
          ...titleField,
          needsAttention: false,
          humanDecision: {
            actor: 'owner',
            application: 'apply' as const,
            decision_id: 'decision-identity',
            destination: 'title',
            occurred_at: 1,
            outcome: 'keep_current',
            proposal_id: titleField.proposalId,
            review_case_id: 'case-1',
            selected_value: null,
            target_fingerprint: titleField.targetFingerprint,
          },
        },
      ],
    }
    let current = detail
    const http: ReviewHttp = {
      ...createHttp(),
      getLibrary: vi.fn(async () => selectedLibrary),
      getAttention: vi.fn(async () => ({
        total: 2,
        items: [
          {
            id: 'case-1',
            destination: 'title',
            title: 'A Book',
            authors: [],
            library: 'Books',
            mediaKind: 'ebook',
            notices: [],
            provenance: ['Local catalogue'],
            workAttentionCount: 1,
          },
          {
            id: 'case-2',
            destination: 'authors',
            title: 'Off-page successor',
            authors: ['Ada Example'],
            library: 'Project Gutenberg',
            mediaKind: 'audiobook',
            notices: [],
            provenance: ['Project Gutenberg record'],
            workAttentionCount: 1,
          },
        ],
      })),
      getCaseDetail: vi.fn(async (id: string) =>
        id === 'case-2' ? { ...detail, id, title: 'Off-page successor' } : current,
      ),
      recordDecision: vi.fn(async () => {
        current = settled
        return {
          confirmation: { status: 'confirmed' as const, case: current },
          invalidation_sequences: [],
          schema_version: 'review-decision-v1',
          actor: 'owner',
          application: 'apply' as const,
          decision_id: 'decision-identity',
          destination: 'title',
          occurred_at: 1,
          outcome: 'keep_current',
          proposal_id: titleField.proposalId,
          review_case_id: 'case-1',
          selected_value: null,
          target_fingerprint: titleField.targetFingerprint,
        }
      }),
    }
    const { router } = renderRoute('/library?lens=all', http)

    expect(screen.queryByRole('link', { name: 'Attention' })).toBeNull()
    await user.click(await screen.findByRole('button', { name: /^A Book\./u }))
    const currentValue = (await screen.findByText('Old title')).closest('button')
    if (!currentValue)
      throw new Error('workspace fixture requires a current-value decision control')
    await user.click(currentValue)

    expect(await screen.findByText('Review complete')).toBeVisible()
    await user.click(screen.getByRole('button', { name: 'Review next work →' }))
    await waitFor(() =>
      expect(router.state.location.search).toEqual({
        lens: 'all',
        preview: 'case-2',
        field: 'authors',
      }),
    )
    expect(await screen.findByRole('heading', { name: 'Off-page successor' })).toBeVisible()
    expect(http.getCaseDetail).toHaveBeenCalledWith('case-1', expect.any(AbortSignal), 'foreground')
    expect(http.getCaseDetail).toHaveBeenCalledWith('case-2', expect.any(AbortSignal), 'foreground')
  })

  it('retains completion identity and a return path after the completed work leaves its lens', async () => {
    const user = userEvent.setup()
    const titleField = detail.fields[0]
    const firstWork = library.works[0]
    if (!titleField || !firstWork)
      throw new Error('completion fixture requires a work and title field')
    const settled: CaseDetailView = {
      ...detail,
      fields: [
        {
          ...titleField,
          needsAttention: false,
          humanDecision: {
            actor: 'owner',
            application: 'apply' as const,
            decision_id: 'decision-complete',
            destination: 'title',
            occurred_at: 1,
            outcome: 'keep_current',
            proposal_id: titleField.proposalId,
            review_case_id: 'case-1',
            selected_value: null,
            target_fingerprint: titleField.targetFingerprint,
          },
        },
      ],
    }
    const successor = {
      id: 'case-2',
      destination: 'authors',
      title: 'Next work',
      authors: [],
      library: 'Books',
      mediaKind: 'ebook',
      notices: [],
      provenance: [],
      workAttentionCount: 1,
    }
    let current = detail
    let libraryReads = 0
    let summaryReads = 0
    let attentionReads = 0
    const http: ReviewHttp = {
      ...createHttp(),
      // The lens=all read is the library summary; it is counted on its own.
      getLibrary: vi.fn(async (search: LibrarySearch) =>
        search.lens === 'all'
          ? (summaryReads++, library)
          : ++libraryReads === 1
            ? { ...library, lens: 'needs_attention' as const }
            : {
                ...library,
                lens: 'needs_attention' as const,
                counts: { attention: 1, all: 1, clear: 0, notMatched: 0 },
                works: [],
              },
      ),
      getAttention: vi.fn(async () =>
        ++attentionReads === 1
          ? {
              total: 2,
              items: [
                { ...successor, id: 'case-1', destination: 'title', title: firstWork.title },
                successor,
              ],
            }
          : { total: 1, items: [successor] },
      ),
      getCaseDetail: vi.fn(async () => current),
      recordDecision: vi.fn(async () => {
        current = settled
        return {
          confirmation: { status: 'confirmed' as const, case: current },
          invalidation_sequences: [90, 91, 92],
          schema_version: 'review-decision-v1',
          actor: 'owner',
          application: 'apply' as const,
          decision_id: 'decision-complete',
          destination: 'title',
          occurred_at: 1,
          outcome: 'keep_current',
          proposal_id: titleField.proposalId,
          review_case_id: 'case-1',
          selected_value: null,
          target_fingerprint: titleField.targetFingerprint,
        }
      }),
    }
    const updates = createLiveStream()
    renderRoute('/library?lens=needs_attention&preview=case-1&field=title', http, updates)

    await user.click(await screen.findByRole('button', { name: 'Choose current value: Old title' }))
    const complete = (await screen.findByText('Review complete')).closest('article')
    if (!complete) throw new Error('completion boundary is required')
    expect(within(complete).getByRole('heading', { name: 'Review complete' })).toBeVisible()
    expect(screen.getByRole('heading', { level: 2, name: 'A Book' })).toBeVisible()
    expect(screen.getByRole('button', { name: 'Review next work →' })).toBeVisible()

    await user.click(screen.getByRole('button', { name: /Title.*Old title/u }))
    expect(await screen.findByRole('button', { name: '← Back to completion' })).toBeVisible()
    // A recorded exact-current field is no longer Attention work, but retains
    // the same eligible choices for a voluntary append-only replacement.
    expect(screen.getByRole('button', { name: 'Choose current value: Old title' })).toHaveAttribute(
      'aria-pressed',
      'true',
    )
    expect(screen.getByRole('button', { name: 'Choose proposed value: New title' })).toBeVisible()
    expect(screen.queryByText('Queue position')).toBeNull()
    expect(screen.queryByText('This field needs your review.')).toBeNull()
    await user.click(screen.getByRole('button', { name: '← Back to completion' }))
    expect(await screen.findByText('Review complete')).toBeVisible()
    // The authoritative decision refreshes its visible aggregates once. Its
    // own later announcement is still recognised rather than causing a second
    // read.
    await act(async () => {
      updates.announce({ sequence: 91, subject: 'case-1', topic: 'library' })
    })
    expect([libraryReads, summaryReads, attentionReads]).toEqual([2, 2, 2])
    // The stream echo is still settled on exit after the immediate aggregate
    // refresh, and the completed work is gone from the lens.
    await user.click(screen.getByRole('button', { name: 'Back to works' }))
    expect(
      await screen.findByText('No exact-current works match this search and lens.'),
    ).toBeVisible()
    await waitFor(() => expect([libraryReads, summaryReads, attentionReads]).toEqual([3, 3, 3]))
  })

  it('never lets a retained rail prune the next search queue when a review completes', async () => {
    const user = userEvent.setup()
    const titleField = detail.fields[0]
    if (!titleField) throw new Error('completion fixture requires a title field')
    const decision = {
      actor: 'owner',
      application: 'apply' as const,
      decision_id: 'decision-complete',
      destination: 'title',
      occurred_at: 1,
      outcome: 'keep_current',
      proposal_id: titleField.proposalId,
      review_case_id: 'case-1',
      selected_value: null,
      target_fingerprint: titleField.targetFingerprint,
    }
    const settled: CaseDetailView = {
      ...detail,
      fields: [{ ...titleField, needsAttention: false, humanDecision: decision }],
    }
    const item = (id: string, destination: string, title: string) => ({
      id,
      destination,
      title,
      authors: [],
      library: 'Books',
      mediaKind: 'ebook',
      notices: [],
      provenance: [],
      workAttentionCount: 1,
    })
    const earlier = item('case-2', 'authors', 'Earlier queue work')
    type Page = Awaited<ReturnType<ReviewHttp['getAttention']>>
    const searched: ((page: Page) => void)[] = []
    let current = detail
    const http: ReviewHttp = {
      ...createHttp(),
      getAttention: vi.fn<ReviewHttp['getAttention']>(async ({ q }) =>
        q
          ? new Promise<Page>((resolve) => searched.push(resolve))
          : { total: 2, items: [earlier, item('case-1', 'title', 'A Book')] },
      ),
      getCaseDetail: vi.fn(async () => current),
      recordDecision: vi.fn(async () => {
        current = settled
        return {
          ...decision,
          confirmation: { status: 'confirmed' as const, case: current },
          invalidation_sequences: [90],
          schema_version: 'review-decision-v1',
        }
      }),
    }
    const { router } = renderRoute(
      '/library?lens=all&attention=open&preview=case-1&field=title',
      http,
    )
    const choose = await screen.findByRole('button', { name: 'Choose current value: Old title' })
    await screen.findByRole('button', { description: 'Earlier queue work' })
    await act(async () =>
      router.navigate({
        to: '/library',
        search: {
          lens: 'all',
          attention: 'open',
          preview: 'case-1',
          field: 'title',
          q: 'later',
        },
      }),
    )
    await waitFor(() =>
      expect(screen.getByLabelText('Attention queue')).toHaveAttribute('aria-busy', 'true'),
    )
    // The retained rail ranks the earlier search's work ahead of this one.
    await user.click(choose)
    expect(await screen.findByText('Review complete')).toBeVisible()
    await act(async () => searched.forEach((resolve) => resolve({ total: 1, items: [earlier] })))
    expect(await screen.findByRole('button', { name: 'Review next work →' })).toBeVisible()
  })

  const renderLaterPageProviderRetry = () => {
    const predecessor = { ...detail, id: 'case-1', current: true }
    const successor: CaseDetailView = {
      ...detail,
      id: 'case-2',
      current: true,
      title: 'Recovered work',
      fields: detail.fields.map((field) => ({ ...field, needsAttention: false })),
    }
    const unrelated: CaseDetailView = {
      ...detail,
      id: 'case-3',
      target: { ...detail.target, item: 'Other work' },
      title: 'Other work',
    }
    let libraryReads = 0
    const http: ReviewHttp = {
      ...createHttp(),
      // The lens=all read is the library summary, not this route's page.
      getLibrary: vi.fn(async (search: LibrarySearch) =>
        search.lens === 'all'
          ? library
          : ++libraryReads === 1
            ? {
                ...library,
                lens: 'needs_attention' as const,
                works: library.works.map((work) => ({
                  ...work,
                  preparationOutcomes: [
                    {
                      providerRequests: 1,
                      laneKind: 'work',
                      provider: 'catalogue',
                      reason: 'Provider retry is awaiting its next permitted request.',
                      status: 'rate_limited',
                    },
                  ],
                })),
              }
            : {
                ...library,
                lens: 'needs_attention' as const,
                counts: { attention: 0, all: 1, clear: 1, notMatched: 0 },
                works: [],
              },
      ),
      getCaseDetail: vi.fn(async (id: string) =>
        id === 'case-2'
          ? successor
          : id === 'case-3'
            ? unrelated
            : libraryReads > 1
              ? { ...predecessor, current: false }
              : predecessor,
      ),
    }
    const updates = createLiveStream()
    const { router } = renderRoute(
      '/library?lens=needs_attention&cursor=later-page&preview=case-1&field=title',
      http,
      updates,
    )
    return { http, libraryReads: () => libraryReads, router, updates }
  }

  it('follows an off-page successor named by a null-subject invalidation', async () => {
    const predecessor = { ...detail, id: 'case-1', current: true }
    const successor = { ...detail, id: 'case-2', current: true, title: 'Null-subject successor' }
    let stale = false
    const updates = createLiveStream()
    const http: ReviewHttp = {
      ...createHttp(),
      getLibrary: vi.fn(async () => (stale ? { ...library, works: [] } : library)),
      getCaseDetail: vi.fn(async (id: string) =>
        id === 'case-2' ? successor : { ...predecessor, current: !stale },
      ),
      getCaseSuccessor: vi.fn(async () => (stale ? 'case-2' : null)),
    }
    const { router } = renderRoute('/library?lens=all&preview=case-1&field=title', http, updates)
    expect(await screen.findByRole('heading', { name: 'A Book' })).toBeVisible()

    stale = true
    await act(async () => {
      updates.announce({ sequence: 76, subject: null, topic: 'library' })
      await new Promise((resolve) => setTimeout(resolve, coalesceMilliseconds + 20))
    })

    expect(http.getCaseSuccessor).toHaveBeenCalledTimes(1)
    expect(await screen.findByRole('heading', { name: 'Null-subject successor' })).toBeVisible()
    expect(http.getCaseSuccessor).toHaveBeenCalledWith(
      'case-1',
      expect.any(AbortSignal),
      'foreground',
    )
    expect(router.state.location.search.preview).toBe('case-2')
  })

  it('retries a failed null-subject lineage lookup with the invalidated Library query', async () => {
    const predecessor = { ...detail, id: 'case-1', current: true }
    const successor = { ...detail, id: 'case-2', current: true, title: 'Retried successor' }
    let stale = false
    const updates = createLiveStream()
    const getCaseSuccessor = vi
      .fn<ReviewHttp['getCaseSuccessor']>()
      .mockRejectedValueOnce(new Error('synthetic transport failure'))
      .mockResolvedValueOnce('case-2')
    const http: ReviewHttp = {
      ...createHttp(),
      getLibrary: vi.fn(async () => (stale ? { ...library, works: [] } : library)),
      getCaseDetail: vi.fn(async (id: string) =>
        id === 'case-2' ? successor : { ...predecessor, current: !stale },
      ),
      getCaseSuccessor,
    }
    const { router } = renderRoute('/library?lens=all&preview=case-1&field=title', http, updates)
    expect(await screen.findByRole('heading', { name: 'A Book' })).toBeVisible()

    stale = true
    await act(async () => {
      updates.announce({ sequence: 78, subject: null, topic: 'library' })
      await new Promise((resolve) => setTimeout(resolve, coalesceMilliseconds + 20))
    })

    expect(await screen.findByRole('heading', { name: 'Retried successor' })).toBeVisible()
    await waitFor(() => expect(getCaseSuccessor).toHaveBeenCalledTimes(2))
    expect(router.state.location.search.preview).toBe('case-2')
  })

  it('clears a null-subject reconciliation after an authoritative no-successor result', async () => {
    const updates = createLiveStream()
    const getCaseSuccessor = vi.fn<ReviewHttp['getCaseSuccessor']>().mockResolvedValue(null)
    const http: ReviewHttp = {
      ...createHttp(),
      getLibrary: vi.fn(async () => ({ ...library })),
      getCaseSuccessor,
    }
    renderRoute('/library?lens=all&preview=case-1&field=title', http, updates)
    expect(await screen.findByRole('heading', { name: 'A Book' })).toBeVisible()

    await act(async () => {
      updates.announce({ sequence: 79, subject: null, topic: 'library' })
      await new Promise((resolve) => setTimeout(resolve, coalesceMilliseconds * 2))
      window.dispatchEvent(new Event('focus'))
      window.dispatchEvent(new Event('online'))
      await new Promise((resolve) => setTimeout(resolve, coalesceMilliseconds + 20))
    })

    expect(getCaseSuccessor).toHaveBeenCalledTimes(1)
  })

  it('retires an exhausted null-subject cycle across focus, reconnect, and selection re-enable', async () => {
    const first = { ...detail, id: 'case-1', current: true }
    const second = { ...detail, id: 'case-2', current: true, title: 'New selection' }
    const updates = createLiveStream()
    const getCaseSuccessor = vi
      .fn<ReviewHttp['getCaseSuccessor']>()
      .mockRejectedValueOnce(new Error('first synthetic transport failure'))
      .mockRejectedValueOnce(new Error('second synthetic transport failure'))
    const http: ReviewHttp = {
      ...createHttp(),
      getCaseDetail: vi.fn(async (id: string) => (id === 'case-2' ? second : first)),
      getCaseSuccessor,
    }
    const { queryClient, router } = renderRoute(
      '/library?lens=all&preview=case-1&field=title',
      http,
      updates,
    )
    expect(await screen.findByRole('heading', { name: 'A Book' })).toBeVisible()

    await act(async () => {
      updates.announce({ sequence: 80, subject: null, topic: 'library' })
      await new Promise((resolve) => setTimeout(resolve, coalesceMilliseconds * 2 + 40))
    })
    await waitFor(() => expect(getCaseSuccessor).toHaveBeenCalledTimes(2))

    await act(async () => {
      window.dispatchEvent(new Event('focus'))
      window.dispatchEvent(new Event('online'))
      await router.navigate({
        to: '/library',
        search: { lens: 'all', preview: 'case-2', field: 'title' },
      })
      await router.navigate({
        to: '/library',
        search: { lens: 'all', preview: 'case-1', field: 'title' },
      })
      await new Promise((resolve) => setTimeout(resolve, coalesceMilliseconds * 2 + 40))
    })

    expect(await screen.findByRole('heading', { name: 'A Book' })).toBeVisible()
    await waitFor(() => expect(getCaseSuccessor).toHaveBeenCalledTimes(2))

    cleanup()
    render(
      <QueryClientProvider client={queryClient}>
        <RouterProvider router={router} />
      </QueryClientProvider>,
    )
    await new Promise((resolve) => setTimeout(resolve, coalesceMilliseconds + 20))
    expect(getCaseSuccessor).toHaveBeenCalledTimes(2)
  })

  it('retires a mid-flight null-subject generation when its selection leaves and returns', async () => {
    const first = { ...detail, id: 'case-1', current: true }
    const second = { ...detail, id: 'case-2', current: true, title: 'New selection' }
    const updates = createLiveStream()
    let rejectLookup: ((reason: Error) => void) | undefined
    const getCaseSuccessor = vi
      .fn<ReviewHttp['getCaseSuccessor']>()
      .mockImplementationOnce(
        () =>
          new Promise<string | null>((_resolve, reject) => {
            rejectLookup = reject
          }),
      )
      .mockResolvedValueOnce('case-2')
    const http: ReviewHttp = {
      ...createHttp(),
      getCaseDetail: vi.fn(async (id: string) => (id === 'case-2' ? second : first)),
      getCaseSuccessor,
    }
    const { router } = renderRoute('/library?lens=all&preview=case-1&field=title', http, updates)
    expect(await screen.findByRole('heading', { name: 'A Book' })).toBeVisible()

    updates.announce({ sequence: 83, subject: null, topic: 'library' })
    await waitFor(() => expect(getCaseSuccessor).toHaveBeenCalledTimes(1))
    await act(async () => {
      await router.navigate({
        to: '/library',
        search: { lens: 'all', preview: 'case-2', field: 'title' },
      })
      await router.navigate({
        to: '/library',
        search: { lens: 'all', preview: 'case-1', field: 'title' },
      })
      rejectLookup?.(new Error('stale transport failure'))
      await new Promise((resolve) => setTimeout(resolve, coalesceMilliseconds * 2 + 40))
    })

    expect(router.state.location.search.preview).toBe('case-1')
    expect(getCaseSuccessor).toHaveBeenCalledTimes(1)
  })

  it('starts a fresh bounded cycle when a later null-subject invalidation repeats its predecessor', async () => {
    const successor = { ...detail, id: 'case-2', current: true, title: 'Later successor' }
    let stale = false
    const updates = createLiveStream()
    const getCaseSuccessor = vi
      .fn<ReviewHttp['getCaseSuccessor']>()
      .mockRejectedValueOnce(new Error('first synthetic transport failure'))
      .mockRejectedValueOnce(new Error('second synthetic transport failure'))
      .mockRejectedValueOnce(new Error('third synthetic transport failure'))
      .mockResolvedValueOnce('case-2')
    const http: ReviewHttp = {
      ...createHttp(),
      getLibrary: vi.fn(async () => (stale ? { ...library, works: [] } : library)),
      getCaseDetail: vi.fn(async (id: string) =>
        id === 'case-2' ? successor : { ...detail, id: 'case-1', current: !stale },
      ),
      getCaseSuccessor,
    }
    const { router } = renderRoute('/library?lens=all&preview=case-1&field=title', http, updates)
    expect(await screen.findByRole('heading', { name: 'A Book' })).toBeVisible()

    await act(async () => {
      updates.announce({ sequence: 81, subject: null, topic: 'library' })
      await new Promise((resolve) => setTimeout(resolve, coalesceMilliseconds * 2 + 40))
    })
    await waitFor(() => expect(getCaseSuccessor).toHaveBeenCalledTimes(2))
    // A later preparation frame, past the Library refresh bound.
    await act(() => new Promise((resolve) => setTimeout(resolve, libraryRefreshMilliseconds)))

    stale = true
    await act(async () => {
      updates.announce({ sequence: 82, subject: null, topic: 'library' })
      await new Promise((resolve) => setTimeout(resolve, coalesceMilliseconds + 20))
    })

    expect(await screen.findByRole('heading', { name: 'Later successor' })).toBeVisible()
    expect(getCaseSuccessor).toHaveBeenCalledTimes(4)
    expect(router.state.location.search.preview).toBe('case-2')
  }, 15_000)

  it('reconciles a buffered successor after deferred detail proves its predecessor historical', async () => {
    const predecessor = { ...detail, id: 'case-1', current: false }
    const successor = { ...detail, id: 'case-2', current: true, title: 'Deferred successor' }
    let stale = false
    let resolveInitial: ((value: typeof predecessor) => void) | undefined
    const initial = new Promise<typeof predecessor>((resolve) => {
      resolveInitial = resolve
    })
    let firstDetail = true
    const updates = createLiveStream()
    const http: ReviewHttp = {
      ...createHttp(),
      getLibrary: vi.fn(async () => (stale ? { ...library, works: [] } : library)),
      getCaseDetail: vi.fn(async (id: string) => {
        if (id === 'case-2') return successor
        if (firstDetail) {
          firstDetail = false
          return initial
        }
        return predecessor
      }),
      getCaseSuccessor: vi.fn(async () => (stale ? 'case-2' : null)),
    }
    const { router } = renderRoute('/library?lens=all&preview=case-1&field=title', http, updates)
    await waitFor(() => expect(http.getCaseDetail).toHaveBeenCalled())

    stale = true
    await act(async () => {
      updates.announce({ sequence: 77, subject: null, topic: 'library' })
      await new Promise((resolve) => setTimeout(resolve, coalesceMilliseconds + 20))
    })
    resolveInitial?.(predecessor)

    expect(await screen.findByRole('heading', { name: 'Deferred successor' })).toBeVisible()
    expect(router.state.location.search.preview).toBe('case-2')
  })

  it('buffers a named successor until deferred detail establishes the selected predecessor', async () => {
    const predecessor = { ...detail, id: 'case-1', current: false }
    const successor = { ...detail, id: 'case-2', current: true, title: 'Buffered successor' }
    let stale = false
    let resolveInitial: ((value: typeof predecessor) => void) | undefined
    const initial = new Promise<typeof predecessor>((resolve) => {
      resolveInitial = resolve
    })
    let firstDetail = true
    const updates = createLiveStream()
    const getCaseSuccessor = vi.fn(async () => null)
    const http: ReviewHttp = {
      ...createHttp(),
      getLibrary: vi.fn(async () => (stale ? { ...library, works: [] } : library)),
      getCaseDetail: vi.fn(async (id: string) => {
        if (id === 'case-2') return successor
        if (firstDetail) {
          firstDetail = false
          return initial
        }
        return predecessor
      }),
      getCaseSuccessor,
    }
    const { router } = renderRoute('/library?lens=all&preview=case-1&field=title', http, updates)
    await waitFor(() =>
      expect(http.getCaseDetail).toHaveBeenCalledWith(
        'case-1',
        expect.any(AbortSignal),
        'foreground',
      ),
    )

    stale = true
    await act(async () => {
      updates.announce({ sequence: 78, subject: 'case-2', topic: 'library' })
      await new Promise((resolve) => setTimeout(resolve, coalesceMilliseconds + 20))
    })
    resolveInitial?.(predecessor)

    expect(await screen.findByRole('heading', { name: 'Buffered successor' })).toBeVisible()
    expect(router.state.location.search.preview).toBe('case-2')
    expect(getCaseSuccessor).not.toHaveBeenCalled()
  })

  it('follows a rightful later-page provider-retry successor when an unrelated frame arrives last', async () => {
    const { http, libraryReads, router, updates } = renderLaterPageProviderRetry()

    expect(await screen.findByRole('heading', { level: 2, name: 'A Book' })).toBeVisible()
    await act(async () => {
      updates.announce({ sequence: 76, subject: 'case-2', topic: 'library' })
      updates.announce({ sequence: 77, subject: 'case-3', topic: 'library' })
    })

    await waitFor(() =>
      expect(router.state.location.search).toEqual({
        lens: 'needs_attention',
        cursor: 'later-page',
        preview: 'case-2',
        field: 'title',
      }),
    )
    expect(await screen.findByRole('heading', { level: 2, name: 'Recovered work' })).toBeVisible()
    expect(http.getCaseDetail).toHaveBeenCalledWith('case-2')
    expect(http.getCaseDetail).not.toHaveBeenCalledWith('case-3')
    await waitFor(() => expect(libraryReads()).toBe(2))
  })

  it('inspects every coalesced later-page provider-retry candidate until it finds the rightful successor', async () => {
    const { http, libraryReads, router, updates } = renderLaterPageProviderRetry()

    expect(await screen.findByRole('heading', { level: 2, name: 'A Book' })).toBeVisible()
    await act(async () => {
      updates.announce({ sequence: 76, subject: 'case-3', topic: 'library' })
      updates.announce({ sequence: 77, subject: 'case-2', topic: 'library' })
    })

    await waitFor(() =>
      expect(router.state.location.search).toEqual({
        lens: 'needs_attention',
        cursor: 'later-page',
        preview: 'case-2',
        field: 'title',
      }),
    )
    expect(await screen.findByRole('heading', { level: 2, name: 'Recovered work' })).toBeVisible()
    expect(http.getCaseDetail).toHaveBeenCalledWith('case-3')
    expect(http.getCaseDetail).toHaveBeenCalledWith('case-2')
    await waitFor(() => expect(libraryReads()).toBe(2))
  })

  it('keeps a later-page provider-retry selection when no coalesced candidate has its target', async () => {
    const { http, libraryReads, router, updates } = renderLaterPageProviderRetry()

    expect(await screen.findByRole('heading', { level: 2, name: 'A Book' })).toBeVisible()
    await act(async () => {
      updates.announce({ sequence: 76, subject: 'case-3', topic: 'library' })
    })

    await waitFor(() => expect(libraryReads()).toBe(2))
    await waitFor(() => expect(http.getCaseDetail).toHaveBeenCalledWith('case-3'))
    expect(router.state.location.search.preview).toBe('case-1')
    expect(http.getCaseDetail).not.toHaveBeenCalledWith('case-2')
  })

  it('reconciles a later provider-retry successor after an unchanged no-match batch without looping', async () => {
    const { http, libraryReads, router, updates } = renderLaterPageProviderRetry()
    const detailReads = (id: string) =>
      vi.mocked(http.getCaseDetail).mock.calls.filter(([requested]) => requested === id).length

    expect(await screen.findByRole('heading', { level: 2, name: 'A Book' })).toBeVisible()
    await act(async () => {
      updates.announce({ sequence: 76, subject: 'case-3', topic: 'library' })
    })
    await waitFor(() => expect(libraryReads()).toBe(2))
    await waitFor(() => expect(detailReads('case-3')).toBe(1))
    // The move's own read, then the selected-case query read again because
    // the move ended nowhere and the retained read is no longer current (#1365).
    await waitFor(() => expect(detailReads('case-1')).toBe(3))
    // No loop, and the next frame arrives past the Library refresh bound.
    await new Promise((resolve) => setTimeout(resolve, libraryRefreshMilliseconds + 20))
    expect(libraryReads()).toBe(2)
    expect(detailReads('case-1')).toBe(3)
    expect(detailReads('case-3')).toBe(1)

    await act(async () => {
      updates.announce({ sequence: 77, subject: 'case-2', topic: 'library' })
    })
    await waitFor(() =>
      expect(router.state.location.search).toEqual({
        lens: 'needs_attention',
        cursor: 'later-page',
        preview: 'case-2',
        field: 'title',
      }),
    )
    expect(await screen.findByRole('heading', { level: 2, name: 'Recovered work' })).toBeVisible()
    await waitFor(() => expect(libraryReads()).toBe(3))
    expect(detailReads('case-1')).toBe(4)
    expect(detailReads('case-3')).toBe(1)
    // One read proves target identity before navigation; the other is the
    // ordinary selected-case query for the replacement URL.
    expect(detailReads('case-2')).toBe(2)
  }, 15_000)

  it('acts on every Library announcement except the echo of its own decision', async () => {
    const user = userEvent.setup()
    const titleField = detail.fields[0]
    if (!titleField) throw new Error('workspace fixture requires a title field')
    const settled: CaseDetailView = {
      ...detail,
      fields: [
        {
          ...titleField,
          needsAttention: false,
          humanDecision: {
            actor: 'owner',
            application: 'apply' as const,
            decision_id: 'd',
            destination: 'title',
            occurred_at: 1,
            outcome: 'keep_current',
            proposal_id: titleField.proposalId,
            review_case_id: 'case-1',
            selected_value: null,
            target_fingerprint: titleField.targetFingerprint,
          },
        },
      ],
    }
    let current = detail
    let libraryReads = 0
    let attentionReads = 0
    const http: ReviewHttp = {
      ...createHttp(),
      getLibrary: vi.fn(async () => {
        libraryReads += 1
        return library
      }),
      getAttention: vi.fn(async () => {
        attentionReads += 1
        return { total: 0, items: [] }
      }),
      getCaseDetail: vi.fn(async () => current),
      // The positions the server says this decision caused. 41 is its Library
      // row, which is the only one this tab may recognise.
      recordDecision: vi.fn(async () => {
        current = settled
        return {
          confirmation: { status: 'confirmed' as const, case: current },
          invalidation_sequences: [40, 41, 42],
          schema_version: 'review-decision-v1' as const,
          actor: 'owner' as const,
          application: 'apply' as const,
          decision_id: 'decision-echo',
          destination: 'title',
          occurred_at: 1,
          outcome: 'keep_current' as const,
          proposal_id: titleField.proposalId,
          review_case_id: 'case-1',
          selected_value: null,
          target_fingerprint: titleField.targetFingerprint,
        }
      }),
    }
    const updates = createLiveStream()
    renderRoute('/library?lens=all&preview=case-1&field=title', http, updates)
    expect(await screen.findByRole('heading', { level: 2, name: 'A Book' })).toBeVisible()
    await waitFor(() => expect([libraryReads, attentionReads]).toEqual([1, 1]))

    const announce = async (update: Parameters<typeof updates.announce>[0]) => {
      await act(async () => {
        updates.announce(update)
        await new Promise((resolve) => setTimeout(resolve, coalesceMilliseconds + 20))
      })
    }
    const crossLibraryBound = () =>
      act(() => new Promise((resolve) => setTimeout(resolve, libraryRefreshMilliseconds)))

    await user.click(screen.getByRole('button', { name: 'Choose current value: Old title' }))
    expect(await screen.findByText('Review complete')).toBeVisible()
    // The authoritative result refreshes the displayed aggregates exactly
    // once, before the stream echo is handled.
    expect([libraryReads, attentionReads]).toEqual([2, 2])

    // The echo of this tab's own decision. Recognised by position, so it is
    // settled rather than acted on.
    await announce({ sequence: 41, subject: 'case-1', topic: 'library' })
    expect([libraryReads, attentionReads]).toEqual([2, 2])

    // Everything else still streams in while the owner keeps recording. A work
    // finishing preparation names its own case, which is not this one.
    await announce({ sequence: 55, subject: 'case-2', topic: 'library' })
    await waitFor(() => expect([libraryReads, attentionReads]).toEqual([3, 3]))

    // A work that could not finish names no case at all. Each later frame
    // arrives past the Library refresh bound, so each is its own read.
    await crossLibraryBound()
    await announce({ sequence: 56, subject: null, topic: 'library' })
    await waitFor(() => expect([libraryReads, attentionReads]).toEqual([4, 4]))

    // A subjectless frame is what the channel emits when a subscription
    // outgrows its bound and collapses to topic-wide, keeping the highest
    // position it replaced -- which can be one this tab caused. Acting on it is
    // one redundant read; recognising it would discard every work collapsed
    // into it, so position alone is never enough.
    await crossLibraryBound()
    await announce({ sequence: 42, subject: null, topic: 'library' })
    await waitFor(() => expect([libraryReads, attentionReads]).toEqual([5, 5]))

    // The positions this decision caused on other topics are never treated as
    // Library echoes, whatever they carry.
    await announce({ sequence: 40, subject: 'case-1', topic: 'case' })
    expect([libraryReads, attentionReads]).toEqual([5, 5])

    // A read taken for another reason discharges what the echo deferred, so
    // leaving costs nothing more.
    await user.click(screen.getByRole('button', { name: 'Back to works' }))
    expect(await screen.findByRole('heading', { level: 2, name: 'Works' })).toBeVisible()
    expect([libraryReads, attentionReads]).toEqual([5, 5])
  }, 15_000)

  it('settles a recognised echo on the way out when nothing else read the Library', async () => {
    const user = userEvent.setup()
    const titleField = detail.fields[0]
    if (!titleField) throw new Error('workspace fixture requires a title field')
    const settled: CaseDetailView = {
      ...detail,
      fields: [
        {
          ...titleField,
          needsAttention: false,
          humanDecision: {
            actor: 'owner',
            application: 'apply' as const,
            decision_id: 'd',
            destination: 'title',
            occurred_at: 1,
            outcome: 'keep_current',
            proposal_id: titleField.proposalId,
            review_case_id: 'case-1',
            selected_value: null,
            target_fingerprint: titleField.targetFingerprint,
          },
        },
      ],
    }
    let current = detail
    let libraryReads = 0
    let attentionReads = 0
    const http: ReviewHttp = {
      ...createHttp(),
      getLibrary: vi.fn(async () => {
        libraryReads += 1
        return library
      }),
      getAttention: vi.fn(async () => {
        attentionReads += 1
        return { total: 0, items: [] }
      }),
      getCaseDetail: vi.fn(async () => current),
      recordDecision: vi.fn(async () => {
        current = settled
        return {
          confirmation: { status: 'confirmed' as const, case: current },
          invalidation_sequences: [70, 71, 72],
          schema_version: 'review-decision-v1' as const,
          actor: 'owner' as const,
          application: 'apply' as const,
          decision_id: 'decision-settle',
          destination: 'title',
          occurred_at: 1,
          outcome: 'keep_current' as const,
          proposal_id: titleField.proposalId,
          review_case_id: 'case-1',
          selected_value: null,
          target_fingerprint: titleField.targetFingerprint,
        }
      }),
    }
    const updates = createLiveStream()
    renderRoute('/library?lens=all&preview=case-1&field=title', http, updates)
    expect(await screen.findByRole('heading', { level: 2, name: 'A Book' })).toBeVisible()
    await waitFor(() => expect([libraryReads, attentionReads]).toEqual([1, 1]))

    await user.click(screen.getByRole('button', { name: 'Choose current value: Old title' }))
    expect(await screen.findByText('Review complete')).toBeVisible()
    await act(async () => {
      updates.announce({ sequence: 71, subject: 'case-1', topic: 'library' })
      await new Promise((resolve) => setTimeout(resolve, coalesceMilliseconds + 20))
    })
    expect([libraryReads, attentionReads]).toEqual([2, 2])

    // The acknowledged result refreshed the displayed aggregates; its deferred
    // stream echo is still settled once as this case is left.
    await user.click(screen.getByRole('button', { name: 'Back to works' }))
    await waitFor(() => expect([libraryReads, attentionReads]).toEqual([3, 3]))

    // And leaving a case that recognised nothing costs nothing.
    await user.click(await screen.findByRole('button', { name: /^A Book\./u }))
    expect(await screen.findByRole('heading', { level: 2, name: 'A Book' })).toBeVisible()
    await user.click(screen.getByRole('button', { name: 'Back to works' }))
    expect(await screen.findByRole('heading', { level: 2, name: 'Works' })).toBeVisible()
    expect([libraryReads, attentionReads]).toEqual([3, 3])
  })

  it('holds a Library frame that outruns the response naming it, and releases what the response disowns', async () => {
    const user = userEvent.setup()
    const titleField = detail.fields[0]
    if (!titleField) throw new Error('workspace fixture requires a title field')
    const settled: CaseDetailView = {
      ...detail,
      fields: [
        {
          ...titleField,
          needsAttention: false,
          humanDecision: {
            actor: 'owner',
            application: 'apply' as const,
            decision_id: 'd',
            destination: 'title',
            occurred_at: 1,
            outcome: 'keep_current',
            proposal_id: titleField.proposalId,
            review_case_id: 'case-1',
            selected_value: null,
            target_fingerprint: titleField.targetFingerprint,
          },
        },
      ],
    }
    let current = detail
    let libraryReads = 0
    let release: (() => void) | undefined
    const answered = new Promise<void>((resolve) => {
      release = resolve
    })
    const http: ReviewHttp = {
      ...createHttp(),
      getLibrary: vi.fn(async () => {
        libraryReads += 1
        return library
      }),
      getCaseDetail: vi.fn(async () => current),
      recordDecision: vi.fn(async () => {
        await answered
        current = settled
        return {
          confirmation: { status: 'confirmed' as const, case: current },
          invalidation_sequences: [80, 81, 82],
          schema_version: 'review-decision-v1' as const,
          actor: 'owner' as const,
          application: 'apply' as const,
          decision_id: 'decision-outrun',
          destination: 'title',
          occurred_at: 1,
          outcome: 'keep_current' as const,
          proposal_id: titleField.proposalId,
          review_case_id: 'case-1',
          selected_value: null,
          target_fingerprint: titleField.targetFingerprint,
        }
      }),
    }
    const updates = createLiveStream()
    renderRoute('/library?lens=all&preview=case-1&field=title', http, updates)
    expect(await screen.findByRole('heading', { level: 2, name: 'A Book' })).toBeVisible()
    await waitFor(() => expect(libraryReads).toBe(1))

    await user.click(screen.getByRole('button', { name: 'Choose current value: Old title' }))
    // The server publishes the echo before it writes the response that names
    // it, so this is the real order, not a contrived one. The frame arrives
    // with nothing yet able to explain it.
    await act(async () => {
      updates.announce({ sequence: 81, subject: 'case-1', topic: 'library' })
      updates.announce({ sequence: 99, subject: 'case-1', topic: 'library' })
      await new Promise((resolve) => setTimeout(resolve, coalesceMilliseconds + 20))
    })
    // Held, not acted on: a read here would be the whole-Library read on the
    // decision path that #635 removed.
    expect(libraryReads).toBe(1)

    // The response arrives and claims 81. It does not claim 99, which was
    // therefore somebody else's news all along and is acted on now.
    await act(async () => {
      release?.()
      await answered
    })
    await waitFor(() => expect(libraryReads).toBe(3))
    expect(await screen.findByText('Review complete')).toBeVisible()

    // 81 stays owed, and leaving settles it once.
    await user.click(screen.getByRole('button', { name: 'Back to works' }))
    await waitFor(() => expect(libraryReads).toBe(4))
  })

  it("a tab that recorded nothing acts on every announcement, including another tab's decision", async () => {
    let libraryReads = 0
    let attentionReads = 0
    const http: ReviewHttp = {
      ...createHttp(),
      getLibrary: vi.fn(async () => {
        libraryReads += 1
        return library
      }),
      getAttention: vi.fn(async () => {
        attentionReads += 1
        return { total: 0, items: [] }
      }),
    }
    const updates = createLiveStream()
    renderRoute('/library?lens=all&preview=case-1&field=title', http, updates)
    expect(await screen.findByRole('heading', { level: 2, name: 'A Book' })).toBeVisible()
    await waitFor(() => expect([libraryReads, attentionReads]).toEqual([1, 1]))

    // The same frame the recording tab recognises. This one caused nothing, so
    // it holds no positions and acts on all of them.
    await act(async () => {
      updates.announce({ sequence: 41, subject: 'case-1', topic: 'library' })
      await new Promise((resolve) => setTimeout(resolve, coalesceMilliseconds + 20))
    })
    await waitFor(() => expect([libraryReads, attentionReads]).toEqual([2, 2]))
  })

  it('keeps a choice on its exact field until acknowledgement refreshes the case', async () => {
    const user = userEvent.setup()
    const titleField = detail.fields[0]
    if (!titleField) throw new Error('workspace fixture requires a title field')
    const authorsField = {
      ...titleField,
      destination: 'authors',
      proposalId: 'proposal-authors',
      targetFingerprint: 'fingerprint-authors',
    }
    let release: (() => void) | undefined
    const deferred = new Promise<void>((resolve) => {
      release = resolve
    })
    const http: ReviewHttp = {
      ...createHttp(),
      getCaseDetail: vi.fn(async () => ({ ...detail, fields: [titleField, authorsField] })),
      recordDecision: vi.fn(async () => {
        await deferred
        return recordedAcknowledgement(
          { ...detail, fields: [titleField, authorsField] },
          {
            invalidation_sequences: [],
            schema_version: 'review-decision-v1',
            actor: 'owner',
            application: 'apply' as const,
            decision_id: 'decision-title',
            destination: 'title',
            occurred_at: 1,
            outcome: 'keep_current',
            proposal_id: titleField.proposalId,
            review_case_id: 'case-1',
            selected_value: null,
            target_fingerprint: titleField.targetFingerprint,
          },
        )
      }),
    }
    renderRoute('/library?lens=all&preview=case-1&field=title', http)

    const choice = await screen.findByRole('button', { name: 'Choose current value: Old title' })
    const readsBefore = vi.mocked(http.getLibrary).mock.calls.length
    const caseReadsBefore = vi.mocked(http.getCaseDetail).mock.calls.length
    await user.click(choice)

    expect(screen.getByRole('button', { name: /Title.*Needs review/u })).toHaveAttribute(
      'aria-current',
      'true',
    )
    expect(vi.mocked(http.recordDecision)).toHaveBeenCalledTimes(1)
    expect(vi.mocked(http.getLibrary).mock.calls.length).toBe(readsBefore)
    expect(vi.mocked(http.getCaseDetail).mock.calls.length).toBe(caseReadsBefore)

    const chosen = await screen.findByRole('button', { name: 'Choose current value: Old title' })
    expect(chosen).toHaveAttribute('aria-pressed', 'true')
    expect(chosen).toHaveTextContent('Recording…')
    expect(screen.getByRole('button', { name: 'Choose proposed value: New title' })).toBeEnabled()
    await act(async () => {
      release?.()
    })
    await waitFor(() =>
      expect(screen.getByRole('button', { name: /Authors.*Needs review/u })).toHaveAttribute(
        'aria-current',
        'true',
      ),
    )
  })

  it('records one command per field however often its choices are pressed', async () => {
    const user = userEvent.setup()
    const titleField = detail.fields[0]
    if (!titleField) throw new Error('workspace fixture requires a title field')
    const authorsField = {
      ...titleField,
      destination: 'authors',
      proposalId: 'proposal-authors',
      targetFingerprint: 'fingerprint-authors',
    }
    let release: (() => void) | undefined
    const deferred = new Promise<void>((resolve) => {
      release = resolve
    })
    const recordDecision = vi.fn<ReviewHttp['recordDecision']>(async (_id, command) => {
      await deferred
      return recordedAcknowledgement(
        { ...detail, fields: [titleField, authorsField] },
        {
          invalidation_sequences: [],
          schema_version: 'review-decision-v1' as const,
          actor: 'owner' as const,
          application: 'apply' as const,
          decision_id: `decision-${command.destination}`,
          destination: command.destination,
          occurred_at: 1,
          outcome: 'keep_current' as const,
          proposal_id: command.proposalId,
          review_case_id: 'case-1',
          selected_value: null,
          target_fingerprint: command.targetFingerprint,
        },
      )
    })
    const http: ReviewHttp = {
      ...createHttp(),
      getCaseDetail: vi.fn(async () => ({ ...detail, fields: [titleField, authorsField] })),
      recordDecision,
    }
    renderRoute('/library?lens=all&preview=case-1&field=title', http)

    await user.click(await screen.findByRole('button', { name: 'Choose current value: Old title' }))
    expect(screen.getByRole('button', { name: /Title.*Needs review/u })).toHaveAttribute(
      'aria-current',
      'true',
    )
    await user.click(
      await screen.findByRole('button', { name: 'Choose proposed value: New title' }),
    )

    expect(recordDecision).toHaveBeenCalledTimes(1)
    expect(recordDecision.mock.calls[0]?.[1].destination).toBe('title')
    await act(async () => {
      release?.()
    })
  })

  it('keeps another field guarded while a failed choice is reconciled', async () => {
    const user = userEvent.setup()
    const titleField = detail.fields[0]
    if (!titleField) throw new Error('workspace fixture requires a title field')
    const authorsField = {
      ...titleField,
      destination: 'authors',
      proposalId: 'proposal-authors',
      targetFingerprint: 'fingerprint-authors',
    }
    let rejectTitle: ((error: Error) => void) | undefined
    const titlePending = new Promise<never>((_resolve, reject) => {
      rejectTitle = reject
    })
    let releaseAuthors: (() => void) | undefined
    const authorsPending = new Promise<void>((resolve) => {
      releaseAuthors = resolve
    })
    const recordDecision = vi.fn<ReviewHttp['recordDecision']>(async (_id, command) => {
      if (command.destination === 'title') return await titlePending
      await authorsPending
      return recordedAcknowledgement(
        { ...detail, fields: [titleField, authorsField] },
        {
          invalidation_sequences: [],
          schema_version: 'review-decision-v1' as const,
          actor: 'owner' as const,
          application: 'apply' as const,
          decision_id: 'decision-authors',
          destination: 'authors',
          occurred_at: 1,
          outcome: 'keep_current' as const,
          proposal_id: command.proposalId,
          review_case_id: 'case-1',
          selected_value: null,
          target_fingerprint: command.targetFingerprint,
        },
      )
    })
    const http: ReviewHttp = {
      ...createHttp(),
      getCaseDetail: vi.fn(async () => ({ ...detail, fields: [titleField, authorsField] })),
      recordDecision,
    }
    renderRoute('/library?lens=all&preview=case-1&field=title', http)

    await user.click(await screen.findByRole('button', { name: 'Choose current value: Old title' }))
    await user.click(screen.getByRole('button', { name: /Authors.*Needs review/u }))
    await user.click(await screen.findByRole('button', { name: 'Choose current value: Old title' }))
    expect(recordDecision).toHaveBeenCalledTimes(1)

    await act(async () => {
      rejectTitle?.(new ApiError(422, 'unsupported'))
    })
    await waitFor(() => expect(recordDecision).toHaveBeenCalledTimes(2))
    const authorsKey = recordDecision.mock.calls[1]?.[1].idempotencyKey
    await user.click(screen.getByRole('button', { name: /Authors.*Needs review/u }))
    await user.click(screen.getByRole('button', { name: 'Choose current value: Old title' }))
    expect(recordDecision).toHaveBeenCalledTimes(2)
    expect(recordDecision.mock.calls[1]?.[1].idempotencyKey).toBe(authorsKey)

    await act(async () => {
      releaseAuthors?.()
    })
  })

  it('does not submit a second field until the first acknowledgement advances there', async () => {
    const user = userEvent.setup()
    const titleField = detail.fields[0]
    if (!titleField) throw new Error('workspace fixture requires a title field')
    const authorsField = {
      ...titleField,
      destination: 'authors',
      proposalId: 'proposal-authors',
      targetFingerprint: 'fingerprint-authors',
    }
    let release: (() => void) | undefined
    const deferred = new Promise<void>((resolve) => {
      release = resolve
    })
    const sent: string[] = []
    const recordDecision = vi.fn<ReviewHttp['recordDecision']>(async (_id, command) => {
      sent.push(command.destination)
      if (command.destination === 'title') await deferred
      return recordedAcknowledgement(
        { ...detail, fields: [titleField, authorsField] },
        {
          invalidation_sequences: [],
          schema_version: 'review-decision-v1' as const,
          actor: 'owner' as const,
          application: 'apply' as const,
          decision_id: `decision-${command.destination}`,
          destination: command.destination,
          occurred_at: 1,
          outcome: 'keep_current' as const,
          proposal_id: command.proposalId,
          review_case_id: 'case-1',
          selected_value: null,
          target_fingerprint: command.targetFingerprint,
        },
      )
    })
    const http: ReviewHttp = {
      ...createHttp(),
      getCaseDetail: vi.fn(async () => ({ ...detail, fields: [titleField, authorsField] })),
      recordDecision,
    }
    renderRoute('/library?lens=all&preview=case-1&field=title', http)

    await user.click(await screen.findByRole('button', { name: 'Choose current value: Old title' }))
    expect(screen.getByRole('button', { name: /Title.*Needs review/u })).toHaveAttribute(
      'aria-current',
      'true',
    )
    expect(sent).toEqual(['title'])

    await act(async () => {
      release?.()
    })
    await waitFor(() =>
      expect(screen.getByRole('button', { name: /Authors.*Needs review/u })).toHaveAttribute(
        'aria-current',
        'true',
      ),
    )
    await act(
      async () => await new Promise<void>((resolve) => requestAnimationFrame(() => resolve())),
    )
    expect(screen.getByRole('button', { name: 'Choose current value: Old title' })).toHaveFocus()
    expect(sent).toEqual(['title'])
    await user.click(await screen.findByRole('button', { name: 'Choose current value: Old title' }))
    await waitFor(() => expect(sent).toEqual(['title', 'authors']))
  })

  it('states what write consent makes true when the work is complete', async () => {
    const titleField = detail.fields[0]
    if (!titleField) throw new Error('workspace fixture requires a title field')
    const settled = (writesConsented: boolean): CaseDetailView => ({
      ...detail,
      writesConsented,
      fields: [
        {
          ...titleField,
          needsAttention: false,
          humanDecision: {
            actor: 'owner',
            application: 'apply' as const,
            decision_id: 'decision-title',
            destination: 'title',
            occurred_at: 1,
            outcome: 'keep_current',
            proposal_id: titleField.proposalId,
            review_case_id: 'case-1',
            selected_value: null,
            target_fingerprint: titleField.targetFingerprint,
          },
        },
      ],
    })
    for (const [writesConsented, expected] of [
      [false, 'Nothing is written yet — applying happens separately, behind your consent.'],
      [
        true,
        'Every write is logged in Activity, and read-back verification remains a separate guarded stage.',
      ],
    ] as const) {
      const user = userEvent.setup()
      let current: CaseDetailView = { ...detail, writesConsented }
      const http: ReviewHttp = {
        ...createHttp(),
        getCaseDetail: vi.fn(async () => current),
        recordDecision: vi.fn(async () => {
          current = settled(writesConsented)
          return {
            confirmation: { status: 'confirmed' as const, case: current },
            invalidation_sequences: [],
            schema_version: 'review-decision-v1',
            actor: 'owner',
            application: 'apply' as const,
            decision_id: 'decision-title',
            destination: 'title',
            occurred_at: 1,
            outcome: 'keep_current',
            proposal_id: titleField.proposalId,
            review_case_id: 'case-1',
            selected_value: null,
            target_fingerprint: titleField.targetFingerprint,
          }
        }),
      }
      renderRoute('/library?lens=all&preview=case-1&field=title', http)
      const choices = await screen.findAllByRole('button', {
        name: 'Choose current value: Old title',
      })
      const latest = choices[choices.length - 1]
      if (!latest) throw new Error('the current route rendered no choice')
      await user.click(latest)
      expect(await screen.findByText(expected)).toBeVisible()
      expect(
        screen.queryByText(
          writesConsented
            ? 'Nothing is written yet — applying happens separately, behind your consent.'
            : 'Every write is logged in Activity, and read-back verification remains a separate guarded stage.',
        ),
      ).toBeNull()
      cleanup()
    }
  })

  it('gives the review comparison a one-line evidence footer and the summary its own', async () => {
    const first = library.works[0]
    if (!first) throw new Error('library fixture requires a work')
    const consulted = {
      ...first,
      preparationOutcomes: [
        {
          providerRequests: 1,
          laneKind: 'work',
          provider: 'hardcover-main',
          reason: 'Answered with one candidate.',
          status: 'candidates',
        },
      ],
    }
    const http: ReviewHttp = {
      ...createHttp(),
      getLibrary: vi.fn(async () => ({ ...library, works: [consulted] })),
    }
    renderRoute('/library?lens=all&preview=case-1&field=title', http)
    // The protected footer: one line, no subtitle, and it says what it is
    // evidence for. The work summary is not a comparison and keeps its own.
    expect(await screen.findByText('1 lookup recorded for this work')).toBeVisible()
    expect(screen.queryByText(/consulted for this work$/u)).toBeNull()
  })

  it('names every field in the rail and carries the count in the field-map header', async () => {
    const titleField = detail.fields[0]
    if (!titleField) throw new Error('workspace fixture requires a title field')
    const authorsField = {
      ...titleField,
      destination: 'authors',
      proposalId: 'proposal-authors',
      targetFingerprint: 'fingerprint-authors',
    }
    const http: ReviewHttp = {
      ...createHttp(),
      getCaseDetail: vi.fn(async () => ({ ...detail, fields: [titleField, authorsField] })),
    }
    renderRoute('/library?lens=all&preview=case-1&field=title', http)

    const rail = await screen.findByRole('navigation', { name: 'Review fields' })
    for (const cell of within(rail).getAllByRole('button')) {
      expect(cell.textContent).toMatch(/[A-Za-z]/u)
      expect(cell.textContent).not.toMatch(/^\s*\d/u)
    }
    expect(within(rail).getByRole('button', { name: /^Title/u })).toBeVisible()
    expect(within(rail).getByRole('button', { name: /^Authors/u })).toBeVisible()
    expect(screen.getByText('2 remaining · field 1 of 2')).toBeVisible()
    // The teaching line is offered once, on the first field.
    expect(screen.getByText('One tap records and opens the next field')).toBeVisible()
    await userEvent.setup().click(within(rail).getByRole('button', { name: /^Authors/u }))
    expect(await screen.findByText('2 remaining · field 2 of 2')).toBeVisible()
    expect(screen.queryByText('One tap records and opens the next field')).toBeNull()
  })

  it('hides unavailable unproposed primary fields until the owner reveals their named order', async () => {
    const titleField = detail.fields[0]
    if (!titleField) throw new Error('workspace fixture requires a title field')
    const emptySubtitle = {
      ...titleField,
      classification: 'unsupported',
      classificationReason: 'This field is unavailable for the current service capability.',
      currentValue: 'Unavailable',
      destination: 'subtitle',
      decisionActions: [],
      editable: false,
      currentRaw: null,
      needsAttention: false,
      navigationRole: 'inspect',
      proposedValues: [],
    }
    const emptySeries = {
      ...emptySubtitle,
      destination: 'series',
      currentRaw: '',
    }
    const emptyGenres = {
      ...emptySubtitle,
      destination: 'genres',
      currentRaw: [],
    }
    const currentOnly = {
      ...titleField,
      classification: 'unsupported',
      classificationReason: 'This field is unavailable for the current service capability.',
      currentValue: 'An Author',
      destination: 'authors',
      decisionActions: [],
      editable: false,
      currentRaw: ['An Author'],
      needsAttention: false,
      navigationRole: 'inspect',
      proposedValues: [],
    }
    const proposalOnly = {
      ...titleField,
      destination: 'description',
      currentRaw: null,
      proposedValues: [{ kind: 'text' as const, value: 'A description' }],
    }
    const http: ReviewHttp = {
      ...createHttp(),
      getCaseDetail: vi.fn(async () => ({
        ...detail,
        fields: [emptySubtitle, currentOnly, emptySeries, proposalOnly, emptyGenres],
      })),
    }
    renderRoute('/library?lens=all&preview=case-1&field=authors', http)

    const rail = await screen.findByRole('navigation', { name: 'Review fields' })
    expect(within(rail).getByRole('button', { name: /^Authors/u })).toBeVisible()
    expect(within(rail).getByRole('button', { name: /^Description/u })).toBeVisible()
    expect(within(rail).queryByRole('button', { name: /^Subtitle/u })).toBeNull()
    expect(within(rail).getByRole('button', { name: 'Show 3 additional fields' })).toBeVisible()

    await userEvent
      .setup()
      .click(within(rail).getByRole('button', { name: 'Show 3 additional fields' }))
    const names = within(rail)
      .getAllByRole('button')
      .map((button) => button.querySelector('strong')?.textContent)
    expect(names).toEqual(['Subtitle', 'Authors', 'Series', 'Description', 'Genres'])
    expect(within(rail).getByRole('button', { name: /^Subtitle/u })).toHaveFocus()
  })

  it('has no phantom all-empty selection and reveals canonical named fields on request', async () => {
    const titleField = detail.fields[0]
    if (!titleField) throw new Error('workspace fixture requires a title field')
    const emptyTitle = {
      ...titleField,
      classification: 'unsupported',
      classificationReason: 'This field is unavailable for the current service capability.',
      currentValue: 'Unavailable',
      decisionActions: [],
      editable: false,
      currentRaw: null,
      needsAttention: false,
      navigationRole: 'inspect',
      proposedValues: [],
    }
    const emptySubtitle = {
      ...emptyTitle,
      destination: 'subtitle',
      currentRaw: '',
    }
    const http: ReviewHttp = {
      ...createHttp(),
      getCaseDetail: vi.fn(async () => ({ ...detail, fields: [emptyTitle, emptySubtitle] })),
    }
    renderRoute('/library?lens=all&preview=case-1', http)

    const rail = await screen.findByRole('navigation', { name: 'Review fields' })
    expect(screen.getByText('0 remaining · no field selected')).toBeVisible()
    expect(within(rail).getByRole('button', { name: 'Show 2 additional fields' })).toBeVisible()
    expect(within(rail).queryByRole('button', { name: /^Title/u })).toBeNull()

    await userEvent
      .setup()
      .click(within(rail).getByRole('button', { name: 'Show 2 additional fields' }))
    expect(within(rail).getByRole('button', { name: /^Title/u })).toHaveFocus()
  })

  it('synchronously reveals and selects an additional field named in the URL', async () => {
    const titleField = detail.fields[0]
    if (!titleField) throw new Error('workspace fixture requires a title field')
    const emptySubtitle = {
      ...titleField,
      classification: 'unsupported',
      classificationReason: 'This field is unavailable for the current service capability.',
      currentValue: 'Unavailable',
      destination: 'subtitle',
      decisionActions: [],
      editable: false,
      currentRaw: '',
      needsAttention: false,
      navigationRole: 'inspect',
      proposedValues: [],
    }
    const http: ReviewHttp = {
      ...createHttp(),
      getCaseDetail: vi.fn(async () => ({ ...detail, fields: [titleField, emptySubtitle] })),
    }
    renderRoute('/library?lens=all&preview=case-1&field=subtitle', http)

    const rail = await screen.findByRole('navigation', { name: 'Review fields' })
    expect(within(rail).getByRole('button', { name: /^Subtitle/u })).toHaveAttribute(
      'aria-current',
      'true',
    )
    expect(screen.getByRole('heading', { name: 'Subtitle' })).toBeVisible()
    expect(screen.queryByRole('button', { name: /Choose current value/u })).toBeNull()
  })

  it('immediately restores collapsed fields when authoritative detail gains a current value or proposal', async () => {
    const titleField = detail.fields[0]
    if (!titleField) throw new Error('workspace fixture requires a title field')
    const emptyTitle = {
      ...titleField,
      classification: 'unsupported',
      classificationReason: 'This field is unavailable for the current service capability.',
      currentValue: 'Unavailable',
      decisionActions: [],
      editable: false,
      currentRaw: null,
      needsAttention: false,
      navigationRole: 'inspect',
      proposedValues: [],
    }
    const emptySubtitle = {
      ...emptyTitle,
      destination: 'subtitle',
      currentRaw: '',
    }
    let authoritative = false
    const http: ReviewHttp = {
      ...createHttp(),
      getCaseDetail: vi.fn(async () =>
        authoritative
          ? {
              ...detail,
              fields: [
                {
                  ...emptyTitle,
                  currentRaw: 'Current title',
                  currentValue: 'Current title',
                },
                {
                  ...emptySubtitle,
                  proposedValues: [{ kind: 'text' as const, value: 'A subtitle' }],
                },
              ],
            }
          : { ...detail, fields: [emptyTitle, emptySubtitle] },
      ),
    }
    const { queryClient } = renderRoute('/library?lens=all&preview=case-1', http)

    const rail = await screen.findByRole('navigation', { name: 'Review fields' })
    expect(within(rail).getByRole('button', { name: 'Show 2 additional fields' })).toBeVisible()
    authoritative = true
    await act(async () => {
      await queryClient.refetchQueries({ queryKey: ['review-case', 'case-1'] })
    })
    await waitFor(() => expect(within(rail).getByRole('button', { name: /^Title/u })).toBeVisible())
    expect(within(rail).getByRole('button', { name: /^Subtitle/u })).toBeVisible()
    expect(within(rail).queryByRole('button', { name: /Show .* additional field/u })).toBeNull()
  })

  it('keeps applicable unavailable fields inspectable without making them actionable', async () => {
    const titleField = detail.fields[0]
    if (!titleField) throw new Error('workspace fixture requires a title field')
    const unavailableField = {
      ...titleField,
      classification: 'unsupported' as const,
      classificationReason: 'This field is unavailable for the current service capability.',
      destination: 'subtitle',
      editable: false,
      inventoryRole: 'contextual' as const,
      needsAttention: false,
      navigationRole: 'inspect' as const,
      proposalId: 'proposal-subtitle',
      proposedValues: [],
      targetFingerprint: 'fingerprint-subtitle',
    }
    const http: ReviewHttp = {
      ...createHttp(),
      getCaseDetail: vi.fn(async () => ({ ...detail, fields: [titleField, unavailableField] })),
    }
    renderRoute('/library?lens=all&preview=case-1&field=title', http)

    const rail = await screen.findByRole('navigation', { name: 'Review fields' })
    expect(within(rail).getByRole('button', { name: /^Title/u })).toBeVisible()
    expect(within(rail).queryByRole('button', { name: /^Subtitle/u })).toBeNull()
    expect(screen.getByText('1 field has context')).toBeVisible()
    await userEvent.setup().click(screen.getByText('1 field has context'))
    expect(
      screen.getByText('This field is unavailable for the current service capability.'),
    ).toBeVisible()
  })

  it('presents a date and year as one publication field', async () => {
    const titleField = detail.fields[0]
    if (!titleField) throw new Error('workspace fixture requires a title field')
    const publicationDate = {
      ...titleField,
      destination: 'publishedDate',
      proposalId: 'proposal-publication-date',
      targetFingerprint: 'fingerprint-publication-date',
    }
    const publicationYear = {
      ...titleField,
      destination: 'publishedYear',
      inventoryRole: 'contextual' as const,
      proposalId: 'proposal-publication-year',
      targetFingerprint: 'fingerprint-publication-year',
    }
    const http: ReviewHttp = {
      ...createHttp(),
      getCaseDetail: vi.fn(async () => ({ ...detail, fields: [publicationDate, publicationYear] })),
    }
    renderRoute('/library?lens=all&preview=case-1&field=publishedYear', http)

    const rail = await screen.findByRole('navigation', { name: 'Review fields' })
    expect(within(rail).getByRole('button', { name: /^Publication date/u })).toBeVisible()
    expect(within(rail).queryByRole('button', { name: /^PublishedYear/u })).toBeNull()
  })

  it('uses non-animated rail movement when reduced motion is requested', async () => {
    const originalScrollTo = HTMLElement.prototype.scrollTo
    const scrollTo = vi.fn()
    Object.defineProperty(HTMLElement.prototype, 'scrollTo', {
      configurable: true,
      value: scrollTo,
    })
    const original = window.matchMedia
    window.matchMedia = vi.fn((query: string) => ({
      matches: query === '(prefers-reduced-motion: reduce)',
      media: query,
      onchange: null,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    }))
    try {
      renderRoute('/library?lens=all&preview=case-1&field=title')
      await screen.findByRole('button', { name: 'Choose current value: Old title' })
      expect(scrollTo).toHaveBeenCalledWith(expect.objectContaining({ behavior: 'auto' }))
    } finally {
      window.matchMedia = original
      Object.defineProperty(HTMLElement.prototype, 'scrollTo', {
        configurable: true,
        value: originalScrollTo,
      })
    }
  })

  it('centres a positioned rail from viewport coordinates and its existing scroll', async () => {
    const originalScrollTo = HTMLElement.prototype.scrollTo
    const originalRect = HTMLElement.prototype.getBoundingClientRect
    const scrollLeft = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'scrollLeft')
    const clientWidth = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'clientWidth')
    const scrollTo = vi.fn()
    Object.defineProperty(HTMLElement.prototype, 'scrollTo', {
      configurable: true,
      value: scrollTo,
    })
    Object.defineProperty(HTMLElement.prototype, 'scrollLeft', {
      configurable: true,
      get(this: HTMLElement) {
        return this.classList.contains('field-filmstrip')
          ? 40
          : Number(scrollLeft?.get?.call(this) ?? 0)
      },
    })
    Object.defineProperty(HTMLElement.prototype, 'clientWidth', {
      configurable: true,
      get(this: HTMLElement) {
        return this.classList.contains('field-filmstrip')
          ? 300
          : Number(clientWidth?.get?.call(this) ?? 0)
      },
    })
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (
      this: HTMLElement,
    ) {
      if (this.classList.contains('field-filmstrip')) return { left: 240, width: 300 } as DOMRect
      if (this.getAttribute('aria-current') === 'true') return { left: 540, width: 100 } as DOMRect
      return originalRect.call(this)
    })
    try {
      renderRoute('/library?lens=all&preview=case-1&field=title')
      await screen.findByRole('button', { name: 'Choose current value: Old title' })
      expect(scrollTo).toHaveBeenCalledWith(expect.objectContaining({ left: 240 }))
    } finally {
      vi.restoreAllMocks()
      Object.defineProperty(HTMLElement.prototype, 'scrollTo', {
        configurable: true,
        value: originalScrollTo,
      })
      if (scrollLeft) Object.defineProperty(HTMLElement.prototype, 'scrollLeft', scrollLeft)
      if (clientWidth) Object.defineProperty(HTMLElement.prototype, 'clientWidth', clientWidth)
    }
  })

  it('loads later attention pages until completion finds a deliberate next-work choice', async () => {
    const user = userEvent.setup()
    const titleField = detail.fields[0]
    if (!titleField) throw new Error('detail fixture requires a title field')
    const settled: CaseDetailView = {
      ...detail,
      fields: [
        {
          ...titleField,
          needsAttention: false,
          humanDecision: {
            actor: 'owner',
            application: 'apply' as const,
            decision_id: 'decision-page',
            destination: 'title',
            occurred_at: 1,
            outcome: 'keep_current',
            proposal_id: titleField.proposalId,
            review_case_id: 'case-1',
            selected_value: null,
            target_fingerprint: titleField.targetFingerprint,
          },
        },
      ],
    }
    let current = detail
    const getAttention = vi.fn<ReviewHttp['getAttention']>(async (search) =>
      search.cursor
        ? {
            total: 2,
            items: [
              {
                id: 'case-2',
                destination: 'authors',
                title: 'Later page work',
                authors: [],
                library: 'Books',
                mediaKind: 'ebook',
                notices: [],
                provenance: [],
                workAttentionCount: 1,
              },
            ],
          }
        : {
            total: 2,
            nextCursor: 'later',
            items: [
              {
                id: 'case-1',
                destination: 'title',
                title: 'A Book',
                authors: [],
                library: 'Books',
                mediaKind: 'ebook',
                notices: [],
                provenance: [],
                workAttentionCount: 1,
              },
            ],
          },
    )
    const http: ReviewHttp = {
      ...createHttp(),
      getAttention,
      getCaseDetail: vi.fn(async (id: string) =>
        id === 'case-2'
          ? {
              ...detail,
              id,
              title: 'Later page work',
              fields: [{ ...titleField, destination: 'authors', currentValue: 'Later author' }],
            }
          : current,
      ),
      recordDecision: vi.fn(async () => {
        current = settled
        return {
          confirmation: { status: 'confirmed' as const, case: current },
          invalidation_sequences: [],
          schema_version: 'review-decision-v1',
          actor: 'owner',
          application: 'apply' as const,
          decision_id: 'decision-page',
          destination: 'title',
          occurred_at: 1,
          outcome: 'keep_current',
          proposal_id: titleField.proposalId,
          review_case_id: 'case-1',
          selected_value: null,
          target_fingerprint: titleField.targetFingerprint,
        }
      }),
    }
    const first = renderRoute('/library?lens=all&preview=case-1&field=title', http)

    await user.click(await screen.findByRole('button', { name: 'Choose current value: Old title' }))
    expect(await screen.findByText('Review complete')).toBeVisible()
    expect(await screen.findByRole('button', { name: 'Review next work →' })).toBeVisible()
    expect(getAttention).toHaveBeenCalledWith(
      { cursor: 'later' },
      expect.any(AbortSignal),
      'foreground',
    )
    await user.click(screen.getByRole('button', { name: 'Review next work →' }))
    await waitFor(() =>
      expect(first.router.state.location.search).toMatchObject({
        preview: 'case-2',
        field: 'authors',
        attentionCursor: 'later',
      }),
    )
    await user.click(await screen.findByRole('link', { name: 'Evidence' }))
    const emitted = first.router.state.location.href
    cleanup()
    const fresh = renderRoute(emitted, http)
    expect(fresh.queryClient).not.toBe(first.queryClient)
    await user.click(await screen.findByRole('link', { name: 'Back to work' }))
    expect(await screen.findByRole('heading', { name: 'Later page work' })).toBeVisible()
    expect(
      await screen.findByRole('button', { name: 'Choose current value: Later author' }),
    ).toBeVisible()
  })

  it('clears the cursor but keeps the open work when the Library search query changes', async () => {
    const user = userEvent.setup()
    const { router } = renderRoute('/library?lens=all&q=first&cursor=next&preview=case-1')

    expect(await screen.findByRole('heading', { level: 2, name: 'A Book' })).toBeVisible()
    await user.clear(screen.getByLabelText('Search title, author, identifier, library, or format'))
    await user.type(
      screen.getByLabelText('Search title, author, identifier, library, or format'),
      'second',
    )

    // A new page of results starts at the first page, but the work under review
    // stays open even when the search no longer lists its card.
    await waitFor(() =>
      expect(router.state.location.search).toEqual({ lens: 'all', preview: 'case-1', q: 'second' }),
    )
    expect(screen.getByRole('heading', { level: 2, name: 'A Book' })).toBeVisible()
  })

  it.each(['absent', 'error'] as const)(
    'keeps the selected work through a search that settles %s and clears only its rows',
    async (result) => {
      let settle: ((value: LibraryView) => void) | undefined
      let reject: ((error: Error) => void) | undefined
      const getLibrary = vi.fn<ReviewHttp['getLibrary']>(async (search) => {
        if (!search.q) return library
        return new Promise<LibraryView>((resolve, refuse) => {
          settle = resolve
          reject = refuse
        })
      })
      const { router } = renderRoute('/library?lens=all&preview=case-1', {
        ...createHttp(),
        getLibrary,
      })
      expect(await screen.findByRole('heading', { level: 2, name: 'A Book' })).toBeVisible()
      await act(async () => {
        await router.navigate({
          to: '/library',
          search: { lens: 'all', preview: 'case-1', q: 'pending' },
        })
      })
      // The previous rows stay in place, busy, until the URL's own read settles.
      const list = document.querySelector<HTMLElement>('.library-work-list')
      await waitFor(() => expect(list).toHaveAttribute('aria-busy', 'true'))
      expect(screen.getByRole('button', { name: /^A Book\./u })).toBeVisible()
      expect(screen.queryByText('Loading works…')).toBeNull()
      expect(screen.getByRole('heading', { level: 2, name: 'A Book' })).toBeVisible()
      await act(async () => {
        if (result === 'absent')
          settle?.({
            ...library,
            works: [],
            counts: { attention: 0, all: 0, clear: 0, notMatched: 0 },
          })
        else reject?.(new Error('Collection unavailable'))
      })
      // Search scopes the result list; the case read keeps the selection (#1365).
      await waitFor(() => expect(screen.queryByRole('button', { name: /^A Book\./u })).toBeNull())
      expect(screen.getByRole('heading', { level: 2, name: 'A Book' })).toBeVisible()
      expect(screen.queryByText(/not available in the current Library result/u)).toBeNull()
      expect(screen.queryByText('Selected work could not be loaded.')).toBeNull()
      expect(list).not.toHaveAttribute('aria-busy')
      await act(async () => {
        await router.navigate({
          to: '/library',
          search: { lens: 'all', preview: 'case-1', q: 'later' },
        })
      })
      expect(screen.getByRole('heading', { level: 2, name: 'A Book' })).toBeVisible()
      // Rows a settled read already cleared are never revived for a later read.
      expect(screen.queryByRole('button', { name: /^A Book\./u })).toBeNull()
    },
  )

  it('keeps the latest Library query when a prior replacement finishes late', async () => {
    const user = userEvent.setup()
    const { router } = renderRoute('/library?lens=all&q=first&cursor=next&preview=case-1')
    await screen.findByRole('heading', { level: 2, name: 'A Book' })
    const originalNavigate = router.navigate.bind(router)
    let releaseClear: (() => void) | undefined
    let replacements = 0
    const delayedNavigate = vi.spyOn(router, 'navigate').mockImplementation((options) => {
      replacements += 1
      if (replacements !== 1) return originalNavigate(options)
      return new Promise((resolve, reject) => {
        releaseClear = () => {
          void originalNavigate(options).then(resolve, reject)
        }
      })
    })
    const query = screen.getByLabelText('Search title, author, identifier, library, or format')

    await user.clear(query)
    await waitFor(() => expect(replacements).toBe(1))
    await user.type(query, 'second')
    expect(query).toHaveValue('second')
    act(() => releaseClear?.())

    await waitFor(() =>
      expect(router.state.location.search).toEqual({ lens: 'all', preview: 'case-1', q: 'second' }),
    )
    expect(replacements).toBe(2)
    delayedNavigate.mockRestore()
  })

  it('coalesces a cleared Library query before its route load begins', async () => {
    const getLibrary = vi.fn<ReviewHttp['getLibrary']>(async () => library)
    const { router } = renderRoute('/library?lens=all&q=first&cursor=next&preview=case-1', {
      ...createHttp(),
      getLibrary,
    })
    await screen.findByRole('heading', { level: 2, name: 'A Book' })
    const query = screen.getByLabelText('Search title, author, identifier, library, or format')
    vi.useFakeTimers()
    try {
      fireEvent.change(query, { target: { value: '' } })
      fireEvent.change(query, { target: { value: 'second' } })
      expect(query).toHaveValue('second')
      expect(getLibrary.mock.calls.map(([search]) => search)).not.toContainEqual({
        lens: 'all',
        preview: 'case-1',
      })
      await act(async () => {
        await vi.advanceTimersByTimeAsync(150)
      })
      vi.useRealTimers()
      await waitFor(() =>
        expect(router.state.location.search).toEqual({
          lens: 'all',
          preview: 'case-1',
          q: 'second',
        }),
      )

      const searches = getLibrary.mock.calls.map(([search]) => search)
      expect(searches.filter((search) => search.q === 'second')).toHaveLength(1)
      expect(searches).not.toContainEqual({ lens: 'all', preview: 'case-1' })
    } finally {
      vi.useRealTimers()
    }
  })

  it('relinquishes a pending Library query to a same-route URL change', async () => {
    const getLibrary = vi.fn<ReviewHttp['getLibrary']>(async () => library)
    const { router } = renderRoute('/library?lens=all&q=first&cursor=next&preview=case-1', {
      ...createHttp(),
      getLibrary,
    })
    await screen.findByRole('heading', { level: 2, name: 'A Book' })
    const query = screen.getByLabelText('Search title, author, identifier, library, or format')
    vi.useFakeTimers()
    try {
      fireEvent.change(query, { target: { value: 'typed' } })
      await act(async () => {
        await router.navigate({
          to: '/library',
          search: { lens: 'needs_attention', preview: 'case-1', q: 'first' },
        })
        await Promise.resolve()
      })
      await act(async () => {
        await vi.advanceTimersByTimeAsync(150)
      })
      vi.useRealTimers()

      expect(router.state.location.search).toEqual({
        lens: 'needs_attention',
        preview: 'case-1',
        q: 'first',
      })
      expect(getLibrary.mock.calls.map(([search]) => search)).not.toContainEqual({
        lens: 'all',
        preview: 'case-1',
        q: 'typed',
      })
    } finally {
      vi.useRealTimers()
    }
  })

  it('starts a later owner query after invalidating an active Library replacement', async () => {
    const { router } = renderRoute('/library?lens=all&q=first&cursor=next&preview=case-1')
    await screen.findByRole('heading', { level: 2, name: 'A Book' })
    const originalNavigate = router.navigate.bind(router)
    let releaseFirst: (() => void) | undefined
    let libraryReplacements = 0
    const delayedNavigate = vi.spyOn(router, 'navigate').mockImplementation((options) => {
      if (options.from !== '/library') return originalNavigate(options)
      libraryReplacements += 1
      if (libraryReplacements !== 1) return originalNavigate(options)
      if (typeof options.search === 'function') options.search(router.state.location.search)
      return new Promise((resolve) => {
        releaseFirst = resolve
      })
    })
    const query = screen.getByLabelText('Search title, author, identifier, library, or format')
    vi.useFakeTimers()
    try {
      fireEvent.change(query, { target: { value: 'first ' } })
      await act(async () => {
        await vi.advanceTimersByTimeAsync(150)
      })
      expect(libraryReplacements).toBe(1)
      await act(async () => {
        await originalNavigate({
          to: '/library',
          search: { lens: 'needs_attention', preview: 'case-1', q: 'first' },
        })
        await Promise.resolve()
      })
      fireEvent.change(query, { target: { value: 'latest' } })
      await act(async () => {
        await vi.advanceTimersByTimeAsync(150)
        releaseFirst?.()
        await Promise.resolve()
        await vi.advanceTimersByTimeAsync(150)
      })
      vi.useRealTimers()

      await waitFor(() =>
        expect(router.state.location.search).toEqual({
          lens: 'needs_attention',
          preview: 'case-1',
          q: 'latest',
        }),
      )
      expect(libraryReplacements).toBe(2)
    } finally {
      vi.useRealTimers()
      delayedNavigate.mockRestore()
    }
  })

  it('starts a fresh Library search after a rejected replacement', async () => {
    const user = userEvent.setup()
    const { router } = renderRoute('/library?lens=all&q=first&cursor=next&preview=case-1')
    await screen.findByRole('heading', { level: 2, name: 'A Book' })
    const failedNavigate = vi
      .spyOn(router, 'navigate')
      .mockRejectedValueOnce(new Error('route unavailable'))
    const query = screen.getByLabelText('Search title, author, identifier, library, or format')

    await user.clear(query)
    await waitFor(() => expect(failedNavigate).toHaveBeenCalledTimes(1))
    await user.clear(query)
    await user.type(query, 'second')

    await waitFor(() =>
      expect(router.state.location.search).toEqual({ lens: 'all', preview: 'case-1', q: 'second' }),
    )
    failedNavigate.mockRestore()
  })

  it('does not revive queued Library search after leaving the route', async () => {
    const user = userEvent.setup()
    let releaseFirst: (() => void) | undefined
    const { router } = renderRoute('/library?lens=all&q=first&cursor=next&preview=case-1')
    await screen.findByRole('heading', { level: 2, name: 'A Book' })
    const originalNavigate = router.navigate.bind(router)
    let libraryReplacements = 0
    const delayedNavigate = vi.spyOn(router, 'navigate').mockImplementation((options) => {
      if (options.from !== '/library') return originalNavigate(options)
      libraryReplacements += 1
      if (libraryReplacements !== 1) return originalNavigate(options)
      return new Promise((resolve) => {
        releaseFirst = resolve
      })
    })
    const query = screen.getByLabelText('Search title, author, identifier, library, or format')

    await user.clear(query)
    await waitFor(() => expect(releaseFirst).toBeTypeOf('function'))
    await user.type(query, 'second')
    await originalNavigate({
      to: '/activity',
      search: { eventType: 'all', rangeDays: 30, page: 1, pageSize: 25 },
    })
    await screen.findByRole('heading', { name: 'Activity and recovery' })
    await originalNavigate({ to: '/policy', search: { section: 'identity' } })
    await screen.findByRole('heading', { name: 'Library policy' })
    await act(async () => {
      releaseFirst?.()
      await Promise.resolve()
    })

    expect(router.state.location.pathname).toBe('/policy')
    expect(libraryReplacements).toBe(1)
    delayedNavigate.mockRestore()
  })

  it('shows an off-page preview deep link from its case read without detail-only identity', async () => {
    const emptyLibrary: LibraryView = {
      lens: 'all',
      page: 1,
      counts: { attention: 0, all: 0, clear: 0, notMatched: 0 },
      works: [],
    }
    const http: ReviewHttp = {
      ...createHttp(),
      getLibrary: vi.fn(async () => emptyLibrary),
      getCaseDetail: vi.fn(async () => ({ ...detail, subtitle: 'internal identity' })),
    }
    renderRoute('/library?lens=all&preview=case-1', http)

    // The case read carries the same readable title, authors, library and
    // format a Library row does (#1365).
    expect(await screen.findByRole('heading', { level: 2, name: 'A Book' })).toBeVisible()
    expect(
      screen.queryByText('Readable work identity is not available on this Library page.'),
    ).toBeNull()
    expect(screen.queryByText('internal identity')).toBeNull()
    expect(screen.queryByRole('link', { name: /Open full review|Open all evidence/u })).toBeNull()
  })

  it('keeps Evidence as a read-only diagnostic instead of a Library decision surface', async () => {
    const { router } = renderRoute('/review/case-1?subview=evidence&field=title', createHttp())

    expect(await screen.findByRole('heading', { name: 'A Book' })).toBeVisible()
    expect(router.state.location.pathname).toBe('/review/case-1')
    expect(screen.getByRole('heading', { name: 'Evidence' })).toBeVisible()
    expect(screen.queryByRole('button', { name: /Choose current value/u })).toBeNull()
  })

  it('aborts a superseded critical load and renders only the current route result', async () => {
    const signals = new Map<LibrarySearch['lens'], AbortSignal>()
    let resolveFirst: ((value: LibraryView) => void) | undefined
    const withTitle = (title: string): LibraryView => ({
      ...library,
      works: library.works.map((work, index) => (index === 0 ? { ...work, title } : work)),
    })
    const current = withTitle('Current result')
    const http: ReviewHttp = {
      ...createHttp(),
      getLibrary: vi.fn((search: LibrarySearch, signal?: AbortSignal): Promise<LibraryView> => {
        if (signal) signals.set(search.lens, signal)
        if (search.lens === 'no_attention_needed') return Promise.resolve(current)
        // The lens=all read is the library summary, which every lens keeps.
        if (search.lens === 'all') return Promise.resolve(library)
        return new Promise<LibraryView>((resolve) => {
          resolveFirst = resolve
        })
      }),
    }
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const router = createApplicationRouter(
      http,
      queryClient,
      createMemoryHistory({ initialEntries: ['/library?lens=needs_attention'] }),
    )
    render(
      <QueryClientProvider client={queryClient}>
        <RouterProvider router={router} />
      </QueryClientProvider>,
    )
    await waitFor(() => expect(http.getLibrary).toHaveBeenCalledTimes(2))
    await router.navigate({ to: '/library', search: { lens: 'no_attention_needed' } })
    resolveFirst?.(withTitle('Stale result'))

    expect(await screen.findByRole('button', { name: /^Current result\./u })).toBeVisible()
    expect(screen.queryByRole('button', { name: /^Stale result\./u })).toBeNull()
    expect(signals.get('needs_attention')?.aborted).toBe(true)
    expect(signals.get('all')?.aborted).toBe(false)
    expect(http.getLibrary).toHaveBeenCalledTimes(3)
  })

  it('offers the completion handoff inside Library with one action for every eligible destination', async () => {
    const user = userEvent.setup()
    const view = activity({
      configured_libraries: [
        {
          connection: 'primary',
          library: 'fiction',
          prepared: { automatic: 4, human: 6, total: 10 },
        },
      ],
      capabilities: [
        {
          connection: 'primary',
          library: 'fiction',
          state: 'partially_allowed',
          destinations: [
            { consented: false, destination: 'title', prepared: 4, state: 'allowed' },
            {
              consented: false,
              destination: 'cover',
              prepared: 6,
              state: 'insufficient_permission',
            },
          ],
        },
      ],
      waiting: {
        applying: 0,
        awaiting_permission: 4,
        held_for_recovery: 0,
        retrying: 0,
        scheduled: 0,
      },
    })
    const http: ReviewHttp = {
      ...createHttp(),
      getPreparation: vi.fn(async () => ({
        schema_version: 'review-preparation-v2' as const,
        recovery_required: false,
        active: true,
        actionable: false,
        paused: false,
        libraries: [
          {
            scan_id: 'scan-1',
            connection: 'primary',
            library: 'fiction',
            library_id: 'library-1',
            state: 'running',
            checked: 9,
            total: 12,
            ready: 9,
            blocked: 0,
            recorded: 0,
            not_matched: 0,
            rescan_requested: false,
          },
        ],
      })),
      getActivity: vi.fn(async () => view),
      getActivityConsent: vi.fn(async () => consentOf(view)),
      // The heading counts works in Library, not the scan's 9 ready cases (#1284).
      getLibrary: vi.fn(async () => ({ ...library, counts: { ...library.counts, all: 12 } })),
    }
    vi.mocked(http.getActivityPermissionReadiness).mockResolvedValue({
      backup_ready: true,
      configured_libraries: view.configured_libraries,
      capabilities: view.capabilities,
      prepared: view.prepared,
      example: view.example,
    })
    renderRoute('/library?lens=all', http)

    // One compact notice inside the existing Library flow: what is in Library,
    // what the write state is, and the single action that changes it (#641, #639).
    expect(
      await screen.findByRole('heading', { name: '12 works are in Library. Writes are off.' }),
    ).toBeVisible()
    // The notice reads the narrow consent read, never Activity history (#1236).
    expect(http.getActivity).not.toHaveBeenCalled()
    // Preparation still running does not withdraw the action.
    expect(screen.getByText(/while preparation continues/u)).toBeVisible()
    // The Attention decision and status share one toolbar, toggle first, so
    // focus order matches the visual order at every width (#1013).
    const controls = document.querySelector('header[aria-label="Library controls"]')
    const notices = document.querySelectorAll('.cm-library-handoff')
    expect(notices).toHaveLength(1)
    expect(controls?.firstElementChild).toBe(screen.getByRole('button', { name: /^Attention/u }))
    expect(controls?.lastElementChild).toBe(notices[0])
    await user.click(screen.getByRole('link', { name: 'Review write permissions' }))
    expect(await screen.findByRole('heading', { name: 'Enable guarded writes' })).toBeVisible()
    expect(http.activityCommand).not.toHaveBeenCalled()
    await user.click(await screen.findByRole('checkbox', { name: /fiction/u }))
    await user.click(screen.getByRole('button', { name: 'Confirm write permissions' }))

    // Every eligible destination, and only those, after explicit review.
    expect(http.activityCommand).toHaveBeenCalledWith('activity/consent', {
      grants: [{ library: 'fiction', destinations: ['title'] }],
    })

    // Selected work keeps the same toolbar, in the same order, with one notice;
    // Back to works leads the workspace instead (#1284).
    cleanup()
    renderRoute('/library?lens=all&preview=case-1', http)
    expect(
      await screen.findByRole('heading', { name: '12 works are in Library. Writes are off.' }),
    ).toBeVisible()
    const selectedNotices = document.querySelectorAll('.cm-library-handoff')
    const selectedControls = document.querySelector('header[aria-label="Library controls"]')
    expect(selectedNotices).toHaveLength(1)
    expect(document.querySelector('.compact-workspace-controls')).toBeNull()
    expect([...(selectedControls?.children ?? [])].map((child) => child.className)).toEqual([
      'attention-toggle',
      selectedNotices[0]?.className,
    ])
    expect(document.querySelector('.library-workspace')?.firstElementChild).toBe(
      screen.getByRole('button', { name: 'Back to works' }),
    )
  })

  it('refreshes the notice on an activity invalidation and polls only while the stream is degraded', async () => {
    const off = activity({
      configured_libraries: [{ connection: 'primary', library: 'fiction' }],
      capabilities: [
        {
          connection: 'primary',
          library: 'fiction',
          state: 'allowed',
          destinations: [{ consented: false, destination: 'title', prepared: 2, state: 'allowed' }],
        },
      ],
      waiting: {
        applying: 0,
        awaiting_permission: 2,
        held_for_recovery: 0,
        retrying: 0,
        scheduled: 0,
      },
    })
    const enabled = { ...off, waiting: { ...off.waiting, awaiting_permission: 0, scheduled: 2 } }
    const http: ReviewHttp = {
      ...createHttp(),
      getPreparation: vi.fn(async () => ({
        schema_version: 'review-preparation-v2' as const,
        recovery_required: false,
        active: false,
        actionable: false,
        paused: false,
        libraries: [],
      })),
      getActivityConsent: vi
        .fn<ReviewHttp['getActivityConsent']>()
        .mockResolvedValueOnce(consentOf(off))
        .mockResolvedValue(consentOf(enabled)),
    }
    const updates = createLiveStream()
    renderRoute('/library?lens=all', http, updates)
    expect(
      await screen.findByRole('heading', { name: '1 work is in Library. Writes are off.' }),
    ).toBeVisible()

    // While live, the `activity` frame is what brings the notice up to date.
    await act(async () => {
      updates.announce({ sequence: 1, subject: null, topic: 'activity' })
      await new Promise((resolve) => setTimeout(resolve, coalesceMilliseconds + 20))
    })
    expect(
      await screen.findByRole('heading', {
        name: '1 work is in Library. Writes are enabled.',
      }),
    ).toBeVisible()
    expect(http.getActivityConsent).toHaveBeenCalledTimes(2)
    expect(http.getActivity).not.toHaveBeenCalled()

    vi.useFakeTimers()
    onTestFinished(() => {
      vi.useRealTimers()
    })
    await act(async () => vi.advanceTimersByTimeAsync(31_000))
    expect(http.getActivityConsent).toHaveBeenCalledTimes(2)
    // Degraded, the 15 s cadence is the fallback, and only then.
    act(() => updates.setState('degraded'))
    await act(async () => vi.advanceTimersByTimeAsync(15_100))
    expect(http.getActivityConsent).toHaveBeenCalledTimes(3)
    await act(async () => vi.advanceTimersByTimeAsync(15_000))
    expect(http.getActivityConsent).toHaveBeenCalledTimes(4)
    expect(http.getActivity).not.toHaveBeenCalled()
  })
})

const providerRailWork = library.works[0]
const providerRailField = detail.fields[0]
if (!providerRailWork || !providerRailField) throw new Error('Provider rail fixtures are missing')

describe('Provider evidence rail', () => {
  it('groups a failed preparation separately from complete zero-attention work', async () => {
    const failed = {
      ...providerRailWork,
      id: 'failed',
      reviewCaseId: undefined,
      detailUrl: null,
      detailKind: null,
      attentionCount: 0,
      preparationOutcomes: [],
      preparation: {
        attempt: 3,
        diagnosticReference: 'prep-transient',
        mediaHealth: [],
        provider: null,
        reason: 'Bounded automatic recovery attempts were exhausted.',
        recovery:
          'When this library finishes with failures, open Background activity and choose Retry.',
        recoveryAttempt: 0,
        stage: 'provider',
        status: 'transient',
      },
    }
    const http = Object.assign(createHttp(), {
      getLibrary: vi.fn(async () => ({ ...library, works: [failed] })),
    })
    renderRoute('/library?lens=all', http)
    expect(
      await screen.findByRole('heading', { name: 'Preparation needs attention' }),
    ).toBeVisible()
    expect(screen.queryByRole('heading', { name: 'No attention needed' })).toBeNull()
    expect(screen.getByRole('article', { name: 'Terminal preparation failure' })).toHaveTextContent(
      'open Background activity and choose Retry',
    )
  })

  it.each(['all', 'needs_attention'])(
    'shows server-ordered page-local boundaries in %s',
    async (lens) => {
      const works = [
        {
          ...providerRailWork,
          id: 'complete',
          reviewCaseId: 'case-1',
          title: 'Zulu complete',
          preparationOutcomes: [
            {
              provider: 'Catalogue',
              laneKind: 'isbn',
              status: 'no_match',
              reason: 'No match',
              providerRequests: 1,
            },
          ],
        },
        {
          ...providerRailWork,
          id: 'limited',
          reviewCaseId: 'case-2',
          title: 'Alpha limited',
          preparationOutcomes: [
            {
              provider: 'Catalogue',
              laneKind: 'isbn',
              status: 'unavailable',
              reason: 'Unavailable',
              providerRequests: 1,
            },
          ],
        },
        ...(lens === 'all'
          ? [
              {
                ...providerRailWork,
                id: 'clear',
                reviewCaseId: 'case-3',
                title: 'Clear work',
                attentionCount: 0,
              },
            ]
          : []),
      ]
      const http = Object.assign(createHttp(), {
        getLibrary: vi.fn(async () => ({ ...library, works })),
      })
      renderRoute(`/library?lens=${lens}`, http)
      const complete = await screen.findByRole('heading', { name: 'Provider evidence complete' })
      const limited = screen.getByRole('heading', { name: 'Provider evidence limited' })
      expect(
        complete.compareDocumentPosition(limited) & Node.DOCUMENT_POSITION_FOLLOWING,
      ).toBeTruthy()
      expect(screen.getByRole('button', { name: /Zulu complete/ })).toHaveAccessibleDescription(
        'No match',
      )
      expect(screen.getByRole('button', { name: /Alpha limited/ })).toHaveAccessibleDescription(
        'Unavailable',
      )
      expect(screen.queryByText('Retry scheduled')).toBeNull()
      if (lens === 'all')
        expect(screen.getByRole('heading', { name: 'No attention needed' })).toBeVisible()
    },
  )

  it.each([
    ['candidates', 'Candidates found', 'complete'],
    ['no_match', 'No match', 'complete'],
    ['disabled', 'Not enabled', 'complete'],
    ['insufficient_input', 'Not enough identity', 'complete'],
    ['authentication', 'Authentication failed', 'limited'],
    ['authorization', 'Access denied', 'limited'],
    ['rate_limited', 'Rate limited', 'limited'],
    ['unavailable', 'Unavailable', 'limited'],
    ['incomplete', 'Incomplete evidence', 'limited'],
    ['malformed', 'Malformed evidence', 'limited'],
    ['oversized', 'Response too large', 'limited'],
    ['candidate_limit_exceeded', 'Too many candidates', 'limited'],
    ['query_limit_exceeded', 'Query allowance reached', 'limited'],
  ])(
    'names %s visibly and accessibly on a page starting within a group',
    async (status, label, group) => {
      const http = Object.assign(createHttp(), {
        getLibrary: vi.fn(async () => ({
          ...library,
          page: 2,
          works: [
            {
              ...providerRailWork,
              preparationOutcomes: [
                {
                  provider: 'Catalogue',
                  laneKind: 'discovery',
                  status,
                  reason: 'Bounded status',
                  providerRequests: 0,
                },
              ],
            },
          ],
        })),
      })
      renderRoute('/library?lens=all', http)
      const row = await screen.findByRole('button', { name: /A Book\./ })
      expect(row).toHaveAccessibleDescription(label)
      expect(within(row).getByText(label)).toBeVisible()
      expect(screen.getByRole('heading', { name: `Provider evidence ${group}` })).toBeVisible()
    },
  )

  it('shows a recorded refusal for an actionless contextual field', async () => {
    const reason = 'No eligible value was found in the retained evidence.'
    const http = createHttp()
    http.getCaseDetail = vi.fn(async () => ({
      ...detail,
      fields: [
        {
          ...providerRailField,
          needsAttention: false,
          classification: 'context_only' as const,
          proposedValues: [],
          automaticWithheldReason: reason,
        },
      ],
    }))
    renderRoute('/library?lens=all&preview=case-1&field=title', http)
    expect(await screen.findByText(reason)).toBeVisible()
    expect(screen.queryByRole('button', { name: /New title/ })).toBeNull()
  })

  it('shows the field-local reason while keeping direct choices available', async () => {
    const reason =
      'Automatic decision withheld: a contributing provider lookup did not supply usable evidence. You can review this field.'
    const http = createHttp()
    http.getCaseDetail = vi.fn(async () => ({
      ...detail,
      fields: [{ ...providerRailField, automaticWithheldReason: reason }],
    }))
    renderRoute('/library?lens=all&preview=case-1', http)
    expect(await screen.findByText(reason)).toBeVisible()
    expect(screen.getByRole('button', { name: /New title/ })).toBeEnabled()
  })
})

it('keeps clear and empty lenses free of provider headers and bounds many lane hints', async () => {
  const http = createHttp()
  http.getLibrary = vi.fn(async () => ({
    ...library,
    works: [
      {
        ...providerRailWork,
        attentionCount: 0,
        preparationOutcomes: Array.from({ length: 40 }, (_, index) => ({
          provider: `Source ${String(index)}`,
          laneKind: 'isbn',
          status: 'no_match',
          reason: 'No match',
          providerRequests: 1,
        })),
      },
    ],
  }))
  renderRoute('/library?lens=no_attention_needed', http)
  const row = await screen.findByRole('button', { name: /A Book\./ })
  expect(row).toHaveAccessibleDescription('No match × 40')
  expect(screen.queryByRole('heading', { name: /Provider evidence/ })).toBeNull()
})

it.each(['Evidence', 'History'] as const)(
  'returns from %s to the exact search page and selected field',
  async (departure) => {
    const user = userEvent.setup()
    const http = createHttp()
    http.getLibrary = vi.fn<ReviewHttp['getLibrary']>(async (search) => ({
      ...library,
      lens: search.lens,
      page: 2,
      works:
        search.q === 'needle' && search.cursor === 'page-two' && search.lens === 'needs_attention'
          ? library.works
          : [],
    }))
    expect((await http.getLibrary({ lens: 'needs_attention' })).works).toEqual([])
    const { router } = renderRoute(
      '/library?lens=needs_attention&q=needle&cursor=page-two&preview=case-1&field=title',
      http,
    )
    await screen.findByRole('heading', { name: 'Which title should this work use?' })
    await user.click(screen.getByRole('link', { name: departure }))
    await screen.findByRole('heading', { name: departure })
    await user.click(
      screen.getByRole('link', {
        name: departure === 'Evidence' ? 'History' : 'Evidence',
      }),
    )
    await user.click(screen.getByRole('link', { name: 'Back to work' }))
    expect(
      await screen.findByRole('heading', { name: 'Which title should this work use?' }),
    ).toBeVisible()
    expect(router.state.location.search).toMatchObject({
      lens: 'needs_attention',
      q: 'needle',
      cursor: 'page-two',
      preview: 'case-1',
      field: 'title',
    })
    expect(screen.getByRole('button', { name: /A Book.*1 field needs attention/ })).toHaveAttribute(
      'aria-current',
      'true',
    )
  },
)

it('offers advised text in the ordinary Description chooser with its immutable authored binding', async () => {
  const authored = mapCaseDetail(descriptionAdviceFixture)
  const field = authored.fields.find((item) => item.destination === 'description')
  if (!field) throw new Error('description fixture requires its field')
  const advice = field.descriptionAdvice
  if (!advice?.candidate) throw new Error('description fixture requires its candidate')
  const candidate = advice.candidate
  const proposed = field.proposedValues[0]
  if (!proposed) throw new Error('description fixture requires proposed text')
  // Retain the earlier ordinary choice when the authored selection supersedes it.
  field.humanDecision = {
    actor: 'owner',
    application: 'apply',
    decision_id: 'prior-description',
    destination: 'description',
    occurred_at: 1,
    outcome: 'accept',
    proposal_id: field.proposalId,
    review_case_id: 'case-1',
    selected_value: proposed,
    target_fingerprint: field.targetFingerprint,
  }
  const http = createHttp()
  http.getCaseDetail = vi.fn(async () => ({ ...authored, id: 'case-1', fields: [field] }))
  http.recordDecision = vi.fn<ReviewHttp['recordDecision']>(async () => ({
    schema_version: 'description-selection-ack-v1',
    decision_id: `human_decision_${'a'.repeat(64)}`,
    advised: true,
    invalidation_sequences: [],
    confirmation: {
      status: 'confirmed',
      case: {
        ...authored,
        id: 'case-1',
        fields: [
          {
            ...field,
            needsAttention: false,
            descriptionAdvice: {
              ...advice,
              selected_advice: { candidate, binding: advice.binding, current: true },
              selection: {
                schema_version: 'authored-description-decision-v1',
                decision_id: `human_decision_${'a'.repeat(64)}`,
                candidate_id: candidate.candidate_id,
                binding: advice.binding,
                origin: 'human',
                resolved_policy_id: `resolved_policy_${'b'.repeat(64)}`,
                qualification_manifest_sha256: 'c'.repeat(64),
                idempotency_digest: 'd'.repeat(64),
                occurred_at: 10,
                destination: 'description',
                outcome: 'accept',
                application: 'guarded',
              },
            },
          },
        ],
      },
    },
  }))
  renderRoute('/library?lens=all&preview=case-1&field=description', http)
  const option = await screen.findByRole('button', {
    name: 'Choose advised value: Authored synthetic description.',
  })
  expect(option).toHaveTextContent('Advised · Authored content')
  expect(option).toHaveTextContent('codex · synthetic-model')
  const chooser = option.closest('section.direct-choice')
  if (!(chooser instanceof HTMLElement)) throw new Error('description choice area is missing')
  expect(
    within(chooser).getByRole('button', {
      name: 'Choose current value: Existing description.',
    }),
  ).toBeVisible()
  expect(
    within(chooser).getByRole('button', {
      name: 'Choose proposed value: A qualified description.',
    }),
  ).toBeVisible()
  expect(screen.queryByRole('article', { name: 'Advised description' })).toBeNull()
  expect(
    screen.getByRole('button', { name: 'Choose proposed value: A qualified description.' }),
  ).toHaveAttribute('aria-pressed', 'true')
  await userEvent.setup().click(option)
  await waitFor(() => expect(http.recordDecision).toHaveBeenCalled())
  const call = vi.mocked(http.recordDecision).mock.calls[0]
  if (!call) throw new Error('description choice was not recorded')
  const command = call[1]
  expect(command.authoredDescription).toEqual({
    candidateId: advice.candidate.candidate_id,
    binding: advice.binding,
  })
  expect(command.idempotencyKey).toBeTruthy()
  await screen.findByRole('heading', { name: 'Review complete' })
  const completed = screen.getByRole('button', {
    name: 'Description · Advised description selectedAuthored synthetic description.',
  })
  expect(completed).not.toHaveTextContent('A qualified description.')
  await userEvent.setup().click(
    within(screen.getByRole('navigation', { name: 'Review fields' })).getByRole('button', {
      name: /^Description/,
    }),
  )
  expect(await screen.findByText('Choice recorded.')).toBeVisible()
  expect(
    screen.getByRole('button', { name: 'Choose advised value: Authored synthetic description.' }),
  ).toHaveAttribute('aria-pressed', 'true')
  expect(
    screen.getByRole('button', { name: 'Choose proposed value: A qualified description.' }),
  ).toHaveAttribute('aria-pressed', 'false')
  expect(screen.queryByText('Recorded. Opening the next field…')).toBeNull()
})

it('renders hostile multiline advice as text and refuses stale selection while ordinary choices remain', async () => {
  const authored = mapCaseDetail(descriptionAdviceFixture)
  const field = authored.fields.find((item) => item.destination === 'description')
  if (!field) throw new Error('description fixture requires its field')
  const advice = field.descriptionAdvice
  if (!advice?.candidate) throw new Error('description fixture requires its candidate')
  field.descriptionAdvice = {
    ...advice,
    current: false,
    candidate: {
      ...advice.candidate,
      output: { description: '<img src=x onerror=alert(1)>\nSecond line', disposition: 'ready' },
    },
  }
  renderRoute('/library?lens=all&preview=case-1&field=description', {
    ...createHttp(),
    getCaseDetail: vi.fn(async () => ({ ...authored, id: 'case-1', fields: [field] })),
  })
  const option = await screen.findByRole('button', {
    name: /Choose advised value: <img src=x onerror=alert\(1\)>/,
  })
  expect(option.querySelector('img')).toBeNull()
  expect(option).toBeDisabled()
  expect(option).toHaveTextContent('Stale advice')
  expect(
    screen.getByRole('button', { name: 'Choose current value: Existing description.' }),
  ).toBeEnabled()
  expect(
    screen.getByRole('button', { name: 'Choose proposed value: A qualified description.' }),
  ).toBeEnabled()
})

it('retries an advised selection through the ordinary controller with the same authored binding and idempotency key', async () => {
  const authored = mapCaseDetail(descriptionAdviceFixture)
  const field = authored.fields.find((item) => item.destination === 'description')
  if (!field) throw new Error('description fixture requires its field')
  const advice = field.descriptionAdvice
  if (!advice?.candidate) throw new Error('description fixture requires its candidate')
  const http = createHttp()
  http.getCaseDetail = vi.fn(async () => ({ ...authored, id: 'case-1', fields: [field] }))
  let release: (() => void) | undefined
  const pending = new Promise<void>((resolve) => {
    release = resolve
  })
  http.recordDecision = vi.fn(async () => {
    await pending
    throw new ApiError(503, 'persistence')
  })
  renderRoute('/library?lens=all&preview=case-1&field=description', http)
  const user = userEvent.setup()
  const option = await screen.findByRole('button', {
    name: 'Choose advised value: Authored synthetic description.',
  })
  option.focus()
  await user.keyboard('{Enter}')
  await waitFor(() => expect(http.recordDecision).toHaveBeenCalledTimes(1))
  expect(screen.getByLabelText('External update status')).not.toHaveTextContent('Choice recorded')
  expect(screen.getByLabelText('External update status')).not.toHaveTextContent(
    'Applied and verified',
  )
  if (!release) throw new Error('pending decision fixture requires release')
  release()
  const retry = await screen.findByRole('button', { name: 'Try the same decision again' })
  expect(screen.getByLabelText('External update status')).not.toHaveTextContent('Choice recorded')
  await user.click(retry)
  await waitFor(() => expect(http.recordDecision).toHaveBeenCalledTimes(2))
  const calls = vi.mocked(http.recordDecision).mock.calls
  const [first, retried] = calls
  if (!first || !retried) throw new Error('description retry was not recorded')
  expect(retried[1]).toEqual(first[1])
  expect(first[1].authoredDescription?.binding).toEqual(advice.binding)
})

it.each(['verified', 'no_change_required'] as const)(
  'shows the exact authored %s outcome without renewing a stale binding',
  async (outcome) => {
    const authored = mapCaseDetail(descriptionAdviceFixture)
    const field = authored.fields.find((item) => item.destination === 'description')
    if (!field) throw new Error('description fixture requires its field')
    const advice = field.descriptionAdvice
    if (!advice?.candidate) throw new Error('description fixture requires its candidate')
    field.descriptionAdvice = {
      ...advice,
      current: outcome === 'no_change_required',
      application_outcome: outcome,
      selected_advice: {
        candidate: advice.candidate,
        binding: advice.binding,
        current: outcome === 'no_change_required',
      },
      selection: {
        schema_version: 'authored-description-decision-v1',
        decision_id: `automatic_decision_${'a'.repeat(64)}`,
        candidate_id: advice.candidate.candidate_id,
        binding: advice.binding,
        origin: 'automatic_policy',
        resolved_policy_id: `resolved_policy_${'b'.repeat(64)}`,
        qualification_manifest_sha256: 'c'.repeat(64),
        idempotency_digest: 'd'.repeat(64),
        occurred_at: 10,
        destination: 'description',
        outcome: 'accept',
        application: 'guarded',
      },
    }
    renderRoute('/library?lens=all&preview=case-1&field=description', {
      ...createHttp(),
      getCaseDetail: vi.fn(async () => ({ ...authored, id: 'case-1', fields: [field] })),
    })
    const option = await screen.findByRole('button', { name: /^Choose advised value:/ })
    expect(option).toHaveAttribute('aria-pressed', 'true')
    expect(option).toHaveTextContent(
      outcome === 'verified' ? 'Applied and verified' : 'No change needed',
    )
    expect(option).not.toHaveTextContent('Stale advice')
    if (outcome === 'verified') expect(option).toBeDisabled()
    expect(
      screen.getByRole('button', { name: 'Choose current value: Existing description.' }),
    ).toHaveAttribute('aria-pressed', 'false')
    expect(
      screen.getByRole('button', { name: 'Choose proposed value: A qualified description.' }),
    ).toHaveAttribute('aria-pressed', 'false')
  },
)

it.each([
  { origin: 'human', current: true, outcome: null, recorded: true },
  { origin: 'human', current: false, outcome: null, recorded: false },
  { origin: 'human', current: false, outcome: 'verified', recorded: true },
  { origin: 'human', current: true, outcome: 'no_change_required', recorded: true },
  { origin: 'automatic_policy', current: true, outcome: null, recorded: false },
] as const)(
  'labels only valid authored owner selections as recorded: %j',
  async ({ origin, current, outcome, recorded }) => {
    const authored = mapCaseDetail(descriptionAdviceFixture)
    const field = authored.fields.find((item) => item.destination === 'description')
    if (!field) throw new Error('description fixture requires its field')
    const advice = field.descriptionAdvice
    if (!advice?.candidate) throw new Error('description fixture requires its candidate')
    field.descriptionAdvice = {
      ...advice,
      current,
      application_outcome: outcome,
      selected_advice: { candidate: advice.candidate, binding: advice.binding, current },
      selection: {
        schema_version: 'authored-description-decision-v1',
        decision_id: `${origin === 'human' ? 'human' : 'automatic'}_decision_${'a'.repeat(64)}`,
        candidate_id: advice.candidate.candidate_id,
        binding: advice.binding,
        origin,
        resolved_policy_id: `resolved_policy_${'b'.repeat(64)}`,
        qualification_manifest_sha256: 'c'.repeat(64),
        idempotency_digest: 'd'.repeat(64),
        occurred_at: 10,
        destination: 'description',
        outcome: 'accept',
        application: 'guarded',
      },
    }
    const http = {
      ...createHttp(),
      getCaseDetail: vi.fn(async () => ({ ...authored, id: 'case-1', fields: [field] })),
    }
    const url = '/library?lens=all&preview=case-1&field=description'
    const first = renderRoute(url, http)
    await screen.findByRole('button', { name: /^Choose advised value:/ })
    const rail = screen.getByRole('navigation', { name: 'Review fields' })
    expect(
      within(rail)
        .getByRole('button', { name: /^Description/ })
        .querySelector('span'),
    ).toHaveAttribute(
      'aria-label',
      recorded ? 'Decision recorded' : 'Handled automatically; inspect or override',
    )
    expect(Boolean(screen.queryByText('Choice recorded.'))).toBe(recorded)
    const user = userEvent.setup()
    for (const departure of ['Evidence', 'History']) {
      await user.click(screen.getByRole('link', { name: departure }))
      await screen.findByRole('heading', { name: departure })
      await user.click(screen.getByRole('link', { name: 'Back to work' }))
      await screen.findByRole('button', { name: /^Choose advised value:/ })
      expect(Boolean(screen.queryByText('Choice recorded.'))).toBe(recorded)
    }
    cleanup()
    const fresh = renderRoute(url, http)
    expect(fresh.queryClient).not.toBe(first.queryClient)
    await screen.findByRole('button', { name: /^Choose advised value:/ })
    expect(Boolean(screen.queryByText('Choice recorded.'))).toBe(recorded)
  },
)

it.each(['human', 'automatic_policy'] as const)(
  'describes persisted authored external outcomes truthfully for %s',
  async (origin) => {
    for (const scenario of [
      {
        current: true,
        outcome: null,
        capability: 'allowed',
        heading: 'Choice recorded',
        detail:
          "Activity shows this change's progress; safety checks run before any update is sent.",
      },
      {
        current: true,
        outcome: null,
        capability: 'unavailable',
        heading: 'Choice recorded · External update unavailable',
        detail: 'No external update capability is configured.',
      },
      {
        current: true,
        outcome: null,
        capability: 'backup_required',
        heading: 'Choice recorded · External update guarded',
        detail: 'currently prevents an external update.',
      },
      {
        current: false,
        outcome: null,
        capability: 'allowed',
        heading: 'Earlier choice retained',
        detail: 'This choice is no longer current.',
      },
      {
        current: false,
        outcome: 'verified',
        capability: 'unavailable',
        heading: 'Applied and verified',
        detail: 'This authored description was applied and verified in the library.',
      },
      {
        current: false,
        outcome: 'no_change_required',
        capability: 'backup_required',
        heading: 'No change needed',
        detail: 'The library already held the selected description.',
      },
    ] as const) {
      const authored = mapCaseDetail(descriptionAdviceFixture)
      const field = authored.fields.find((item) => item.destination === 'description')
      if (!field?.descriptionAdvice?.candidate)
        throw new Error('description fixture requires candidate')
      const advice = field.descriptionAdvice
      field.effectCapability = scenario.capability
      field.descriptionAdvice = {
        ...advice,
        current: scenario.current,
        application_outcome: scenario.outcome,
        selected_advice: {
          candidate: field.descriptionAdvice.candidate,
          binding: advice.binding,
          current: scenario.current,
        },
        selection: {
          schema_version: 'authored-description-decision-v1',
          decision_id: `${origin === 'human' ? 'human' : 'automatic'}_decision_${'a'.repeat(64)}`,
          candidate_id: field.descriptionAdvice.candidate.candidate_id,
          binding: advice.binding,
          origin,
          resolved_policy_id: `resolved_policy_${'b'.repeat(64)}`,
          qualification_manifest_sha256: 'c'.repeat(64),
          idempotency_digest: 'd'.repeat(64),
          occurred_at: 10,
          destination: 'description',
          outcome: 'accept',
          application: 'guarded',
        },
      }
      renderRoute('/library?lens=all&preview=case-1&field=description', {
        ...createHttp(),
        getCaseDetail: vi.fn(async () => ({ ...authored, id: 'case-1', fields: [field] })),
      })
      const panel = await screen.findByLabelText('External update status')
      expect(panel).toHaveTextContent(scenario.heading)
      expect(panel).toHaveTextContent(scenario.detail)
      expect(panel).not.toHaveTextContent('queues nothing')
      cleanup()
    }
  },
)

it.each([true, false])(
  'marks a held automatic authored description as chosen only after Resume (held: %s)',
  async (held) => {
    const authored = mapCaseDetail(descriptionAdviceFixture)
    const field = authored.fields.find((item) => item.destination === 'description')
    if (!field?.descriptionAdvice?.candidate)
      throw new Error('description fixture requires candidate')
    const advice = field.descriptionAdvice
    const candidate = field.descriptionAdvice.candidate
    field.effectCapability = 'unavailable'
    field.humanDecision = null
    field.automaticDecision = null
    field.manualHold = held
    field.descriptionAdvice = {
      ...advice,
      current: true,
      application_outcome: null,
      selected_advice: { candidate, binding: advice.binding, current: true },
      selection: {
        schema_version: 'authored-description-decision-v1',
        decision_id: `automatic_decision_${'a'.repeat(64)}`,
        candidate_id: candidate.candidate_id,
        binding: advice.binding,
        origin: 'automatic_policy',
        resolved_policy_id: `resolved_policy_${'b'.repeat(64)}`,
        qualification_manifest_sha256: 'c'.repeat(64),
        idempotency_digest: 'd'.repeat(64),
        occurred_at: 10,
        destination: 'description',
        outcome: 'accept',
        application: 'guarded',
      },
    }
    renderRoute('/library?lens=all&preview=case-1&field=description', {
      ...createHttp(),
      getCaseDetail: vi.fn(async () => ({ ...authored, id: 'case-1', fields: [field] })),
    })
    const choices = await screen.findAllByRole('button', { name: /^Choose advised value:/ })
    const panel = screen.getByLabelText('External update status')
    const [choice] = choices
    expect(choices).toHaveLength(1)
    expect(choice).toHaveAttribute('aria-pressed', String(!held))
    expect(choice?.closest('article')?.classList.contains('is-selected')).toBe(!held)
    expect(Boolean(screen.queryByText(/^Selected\. Activity shows/))).toBe(!held)
    expect(panel.textContent.includes('Choice recorded')).toBe(!held)
    expect(Boolean(screen.queryByRole('button', { name: 'Resume automatic handling' }))).toBe(held)
    if (held)
      expect(
        screen.getByText('Your earlier decision still holds this field.', { exact: false }),
      ).toBeVisible()
  },
)

it.each(['ready', 'needs_review', 'stale', 'absent'] as const)(
  'does not infer external application from unselected %s advice',
  async (state) => {
    const authored = mapCaseDetail(descriptionAdviceFixture)
    const field = authored.fields.find((item) => item.destination === 'description')
    if (!field?.descriptionAdvice?.candidate)
      throw new Error('description fixture requires candidate')
    field.effectCapability = 'allowed'
    if (state === 'absent') field.descriptionAdvice = null
    else
      field.descriptionAdvice = {
        ...field.descriptionAdvice,
        current: state !== 'stale',
        selection: null,
        candidate: {
          ...field.descriptionAdvice.candidate,
          output: {
            ...field.descriptionAdvice.candidate.output,
            disposition: state === 'needs_review' ? 'needs_review' : 'ready',
          },
        },
      }
    renderRoute('/library?lens=all&preview=case-1&field=description', {
      ...createHttp(),
      getCaseDetail: vi.fn(async () => ({ ...authored, id: 'case-1', fields: [field] })),
    })
    const panel = await screen.findByLabelText('External update status')
    expect(panel).toHaveTextContent('Eligible for a library update')
    expect(panel).toHaveTextContent(
      'Being eligible sends nothing. Activity checks any recorded change before it reaches the library.',
    )
    expect(panel).not.toHaveTextContent('queues nothing')
    expect(panel).not.toHaveTextContent('Choice recorded')
    expect(panel).not.toHaveTextContent('Applied and verified')
  },
)

it.each(['requested', 'running', 'failed', 'interrupted', 'succeeded'] as const)(
  'retains the recorded authored choice across %s retry and fresh route load',
  async (state) => {
    const { default: retries } = await import('./descriptionAdviceRetryFixture.json')
    const payload = {
      ...descriptionAdviceFixture,
      fields: descriptionAdviceFixture.fields.map((field) =>
        field.destination === 'description'
          ? { ...field, description_advice: retries[state] }
          : field,
      ),
    }
    const authored = mapCaseDetail(payload)
    const field = authored.fields.find((item) => item.destination === 'description')
    if (!field?.descriptionAdvice?.selected_advice)
      throw new Error('selected retry fixture required')
    const http = {
      ...createHttp(),
      getCaseDetail: vi.fn(async () => ({ ...authored, id: 'case-1', fields: [field] })),
    }
    for (let load = 0; load < 2; load++) {
      renderRoute('/library?lens=all&preview=case-1&field=description', http)
      const choices = await screen.findAllByRole('button', { name: /^Choose advised value:/ })
      expect(choices).toHaveLength(state === 'succeeded' ? 2 : 1)
      expect(choices[0]).toHaveAttribute('aria-pressed', 'true')
      expect(choices[0]).toBeDisabled()
      expect(screen.getByText('Choice recorded.')).toBeVisible()
      expect(field.effectCapability).toBe('unavailable')
      expect(screen.getByLabelText('External update status')).toHaveTextContent(
        'Choice recorded · External update unavailable',
      )
      expect(screen.getByLabelText('External update status')).toHaveTextContent(
        'Activity shows any recorded application progress.',
      )
      if (state === 'requested' || state === 'running')
        expect(screen.getByText(/Preparing description advice/)).toBeVisible()
      if (state === 'failed')
        expect(screen.getByText(/Description adviser unavailable/)).toBeVisible()
      if (state === 'succeeded') {
        expect(choices[1]).toBeEnabled()
        expect(choices[1]).toHaveAttribute('aria-pressed', 'false')
      }
      cleanup()
    }
  },
)

it('shows retained automatic advice during preservation-only retry without adding owner actions', async () => {
  const { default: payload } = await import('./descriptionAdvicePreservationFixture.json')
  const authored = mapCaseDetail(payload)
  const field = authored.fields.find((item) => item.destination === 'description')
  if (!field?.descriptionAdvice?.selected_advice)
    throw new Error('Selected preservation fixture required')
  expect(field.preservationOnly).toBe(true)
  expect(field.editable).toBe(false)
  expect(field.decisionActions).toEqual([])
  expect(field.proposedValues).toEqual([])
  expect(ownerChoiceCount(field)).toBe(0)
  expect(field.descriptionAdvice.selection?.origin).toBe('automatic_policy')
  expect(field.descriptionAdvice.candidate).toBeNull()
  expect(field.descriptionAdvice.application_outcome).toBeNull()
  renderRoute('/library?lens=all&preview=case-1&field=description', {
    ...createHttp(),
    getCaseDetail: vi.fn(async () => ({ ...authored, id: 'case-1', fields: [field] })),
  })
  const retained = await screen.findByRole('button', {
    name: 'Choose advised value: Authored synthetic description.',
  })
  expect(retained).toBeDisabled()
  expect(retained).toHaveAttribute('aria-pressed', 'true')
  expect(retained).toHaveTextContent('Advised · Authored content · Recorded choice')
  expect(screen.getByText(/Preparing description advice/)).toBeVisible()
  expect(screen.queryByText('Choice recorded.')).toBeNull()
  expect(screen.queryByRole('button', { name: 'Description Decision recorded' })).toBeNull()
  expect(screen.getByLabelText('External update status')).toHaveTextContent(
    'Activity shows any recorded application progress.',
  )
})

it.each([
  ['header-open', '', false],
  ['compact-open', '&preview=case-1', false],
  ['header-close', '&attention=open', true],
  ['rail-close', '&preview=case-1&attention=open', true],
] as const)(
  'composes the latest Library draft with explicit Attention %s',
  async (kind, extra, closing) => {
    const { router } = renderRoute(
      '/library?lens=all&q=before&cursor=library-page&attentionCursor=attention-page' + extra,
    )
    const query = await screen.findByRole('searchbox')
    await screen.findByRole('heading', { name: extra.includes('preview') ? 'A Book' : 'Works' })
    const user = userEvent.setup()
    if (closing)
      await waitFor(() =>
        expect(screen.getByRole('complementary', { name: 'Attention queue' })).toHaveFocus(),
      )
    await user.clear(query)
    await user.type(query, 'after ')
    const button =
      kind === 'rail-close'
        ? within(screen.getByRole('complementary', { name: 'Attention queue' })).getByRole(
            'button',
            { name: 'Close' },
          )
        : screen.getByRole('button', { name: 'Attention 1 work' })
    await user.click(button)
    await waitFor(() => {
      expect(router.state.location.search.q).toBe('after')
      expect(router.state.location.search.attention).toBe(closing ? undefined : 'open')
    })
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 200))
    })
    expect(query).toHaveValue('after ')
    expect(router.state.location.search.cursor).toBeUndefined()
    expect(router.state.location.search.attentionCursor).toBeUndefined()
  },
)

it('retains independent Library and Attention cursors for a drawer-only intent', async () => {
  const { router } = renderRoute(
    '/library?lens=all&q=before&cursor=library-page&attentionCursor=attention-page',
  )
  await screen.findByRole('heading', { name: 'Works' })
  await userEvent.setup().click(screen.getByRole('button', { name: /^Attention / }))
  await waitFor(() => expect(router.state.location.search.attention).toBe('open'))
  expect(router.state.location.search).toEqual({
    lens: 'all',
    q: 'before',
    cursor: 'library-page',
    attentionCursor: 'attention-page',
    attention: 'open',
  })
})

it('composes Attention after an already submitted search and retains newer rapid typing', async () => {
  const { router } = renderRoute('/library?lens=all&q=before&preview=case-1')
  await screen.findByRole('heading', { name: 'A Book' })
  const query = screen.getByRole('searchbox')
  const original = router.navigate.bind(router)
  let release!: () => void
  let replacements = 0
  const delayed = vi.spyOn(router, 'navigate').mockImplementation((options) => {
    if (options.from !== '/library' || ++replacements !== 1) return original(options)
    return new Promise((resolve, reject) => {
      release = () => {
        void original(options).then(resolve, reject)
      }
    })
  })
  fireEvent.change(query, { target: { value: 'submitted' } })
  await waitFor(() => expect(replacements).toBe(1))
  fireEvent.change(query, { target: { value: 'latest' } })
  await userEvent.setup().click(screen.getByRole('button', { name: 'Attention 1 work' }))
  fireEvent.change(query, { target: { value: 'newest' } })
  act(() => release())
  await waitFor(() =>
    expect(router.state.location.search).toEqual({
      lens: 'all',
      preview: 'case-1',
      q: 'newest',
      attention: 'open',
    }),
  )
  expect(query).toHaveValue('newest')
  delayed.mockRestore()
})

it('restores the combined search and drawer through real Back and Forward', async () => {
  const { router } = renderRoute('/library?lens=all&q=before')
  await screen.findByRole('heading', { name: 'Works' })
  const query = screen.getByRole('searchbox')
  fireEvent.change(query, { target: { value: 'after' } })
  await userEvent.setup().click(screen.getByRole('button', { name: /^Attention / }))
  await waitFor(() =>
    expect(router.state.location.search).toEqual({ lens: 'all', q: 'after', attention: 'open' }),
  )
  act(() => router.history.back())
  await waitFor(() => expect(router.state.location.search).toEqual({ lens: 'all', q: 'before' }))
  expect(query).toHaveValue('before')
  act(() => router.history.forward())
  await waitFor(() =>
    expect(router.state.location.search).toEqual({ lens: 'all', q: 'after', attention: 'open' }),
  )
  expect(query).toHaveValue('after')
})

it('abandons composed Attention and search when external same-route navigation wins', async () => {
  const { router } = renderRoute('/library?lens=all&q=before&preview=case-1')
  await screen.findByRole('heading', { name: 'A Book' })
  const query = screen.getByRole('searchbox')
  const original = router.navigate.bind(router)
  let release!: () => void
  let replacements = 0
  const delayed = vi.spyOn(router, 'navigate').mockImplementation((options) => {
    if (options.from !== '/library' || ++replacements !== 1) return original(options)
    return new Promise((resolve, reject) => {
      release = () => {
        void original(options).then(resolve, reject)
      }
    })
  })
  fireEvent.change(query, { target: { value: 'obsolete' } })
  await waitFor(() => expect(replacements).toBe(1))
  fireEvent.change(query, { target: { value: 'queued' } })
  await userEvent.setup().click(screen.getByRole('button', { name: 'Attention 1 work' }))
  await act(async () => {
    await original({
      to: '/library',
      search: { lens: 'needs_attention', q: 'external', preview: 'case-1' },
    })
  })
  await waitFor(() => expect(query).toHaveValue('external'))
  act(() => release())
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 200))
  })
  expect(router.state.location.search).toEqual({
    lens: 'needs_attention',
    q: 'external',
    preview: 'case-1',
  })
  expect(query).toHaveValue('external')
  delayed.mockRestore()
})

it('commits an explicitly cleared search together with Attention', async () => {
  const { router } = renderRoute('/library?lens=all&q=before&cursor=old&attentionCursor=older')
  await screen.findByRole('heading', { name: 'Works' })
  const query = screen.getByRole('searchbox')
  fireEvent.change(query, { target: { value: '' } })
  await userEvent.setup().click(screen.getByRole('button', { name: /^Attention / }))
  await waitFor(() =>
    expect(router.state.location.search).toEqual({ lens: 'all', attention: 'open' }),
  )
  expect(query).toHaveValue('')
})

it.each([
  ['', 'Current title', 'default'],
  ['before', '', 'default'],
  ['', 'Current title', 'selection'],
  ['', 'Current title', 'back'],
] as const)(
  'preserves search ownership %s to %s with %s when the first wide page arrives',
  async (initialQuery, draft, action) => {
    const previousMatchMedia = window.matchMedia
    vi.stubGlobal('matchMedia', (query: string) => ({
      matches: query === '(min-width: 761px)',
      media: query,
      onchange: null,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(() => true),
    }))
    onTestFinished(() => {
      vi.stubGlobal('matchMedia', previousMatchMedia)
    })
    let releaseInitial: (value: LibraryView) => void = () => undefined
    const http = createHttp()
    const work = library.works[0]
    if (!work) throw new Error('public work fixture required')
    const filtered = {
      ...library,
      works: [{ ...work, reviewCaseId: 'filtered-case', title: 'Filtered work' }],
    }
    let first = true
    // Only the initial URL's first page arrives late; with a search, the
    // library summary is a separate read and is not that page.
    http.getLibrary = vi.fn<ReviewHttp['getLibrary']>((search) => {
      if (!first || (search.q ?? '') !== initialQuery) return Promise.resolve(filtered)
      first = false
      return new Promise((resolve) => {
        releaseInitial = resolve
      })
    })
    http.getCaseDetail = vi.fn<ReviewHttp['getCaseDetail']>(async (id) => ({ ...detail, id }))
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const router = createApplicationRouter(
      http,
      queryClient,
      createMemoryHistory({
        initialEntries: [
          '/library?lens=all&q=prior&preview=prior-case',
          '/library?lens=all' + (initialQuery ? '&q=' + initialQuery : ''),
        ],
      }),
    )
    render(
      <QueryClientProvider client={queryClient}>
        <RouterProvider router={router} />
      </QueryClientProvider>,
    )
    const selected: (string | undefined)[] = []
    const unsubscribe = router.subscribe('onResolved', () => {
      selected.push(router.state.location.search.preview)
    })
    onTestFinished(unsubscribe)
    const query = await screen.findByRole('searchbox')
    expect(screen.getByText('Loading works…')).toBeVisible()
    if (action === 'default') {
      const user = userEvent.setup()
      if (draft) await user.type(query, draft)
      else await user.clear(query)
      expect(query).toHaveValue(draft)
      await act(async () => releaseInitial(library))
      await waitFor(() => expect(router.state.location.search.q).toBe(draft || undefined))
      expect(query).toHaveValue(draft)
      expect(query).toHaveFocus()
      expect(query).toHaveProperty('selectionStart', draft.length)
      await waitFor(() => expect(router.state.location.search.preview).toBe('filtered-case'))
      expect(selected).not.toContain('case-1')
      return
    }
    vi.useFakeTimers()
    try {
      fireEvent.change(query, { target: { value: draft } })
      expect(query).toHaveValue(draft)
      await act(async () => releaseInitial(library))
      await vi.waitFor(
        () => expect(screen.getByRole('button', { name: /^A Book\./ })).toBeVisible(),
        { interval: 0 },
      )
      // Selection and Back must precede the pending draft's debounce deadline.
      expect(router.state.location.search.q).toBeUndefined()
      expect(router.state.location.search.preview).toBeUndefined()
      if (action === 'selection') fireEvent.click(screen.getByRole('button', { name: /^A Book\./ }))
      else act(() => router.history.back())
      await vi.waitFor(
        () =>
          expect(router.state.location.search.preview).toBe(
            action === 'selection' ? 'case-1' : 'prior-case',
          ),
        { interval: 0 },
      )
      await act(() => vi.advanceTimersByTimeAsync(150))
      expect(router.state.location.search.q).toBe(action === 'selection' ? undefined : 'prior')
      expect(query).toHaveValue(action === 'selection' ? '' : 'prior')
      expect(router.state.location.search.preview).toBe(
        action === 'selection' ? 'case-1' : 'prior-case',
      )
    } finally {
      vi.useRealTimers()
    }
  },
)

it('pages selected-field observations and retries without creating a choice', async () => {
  const user = userEvent.setup()
  const http = createHttp()
  http.getEvidence = vi
    .fn<ReviewHttp['getEvidence']>()
    .mockResolvedValueOnce({
      items: [
        {
          destinations: ['isbn'],
          observation: 'ISBN: 9780000000002',
          role: 'external_observation',
          source: 'Open Library',
        },
      ],
      missingLineageCount: 0,
      sourceLedger: [],
      nextCursor: 'page-two',
    })
    .mockRejectedValueOnce(new Error('temporary read failure'))
    .mockResolvedValueOnce({
      items: [
        {
          destinations: ['title'],
          observation: 'Title: contradicted retained fact',
          role: 'external_observation',
          source: 'Open Library',
        },
      ],
      missingLineageCount: 0,
      sourceLedger: [],
    })
  renderRoute('/library?lens=all&preview=case-1&field=title', http)
  const region = await screen.findByRole('region', { name: 'Recorded observations' })
  await user.click(await within(region).findByRole('button', { name: 'Load more observations' }))
  await user.click(await within(region).findByRole('button', { name: 'Retry observations' }))
  expect(await within(region).findByText('Title: contradicted retained fact')).toBeVisible()
  expect(within(region).queryByText('ISBN: 9780000000002')).toBeNull()
  expect(within(region).queryByRole('button')).toBeNull()
  expect(http.getEvidence).toHaveBeenLastCalledWith(
    'case-1',
    'page-two',
    expect.any(AbortSignal),
    expect.anything(),
  )
  expect(http.recordDecision).not.toHaveBeenCalled()
})

it('counts linked corroborating authorities once and keeps observations separate', async () => {
  const user = userEvent.setup()
  const field = detail.fields[0]
  if (!field) throw new Error('title field required')
  const http = createHttp()
  http.getCaseDetail = vi.fn<ReviewHttp['getCaseDetail']>(async () => ({
    ...detail,
    fields: [
      {
        ...field,
        choiceContexts: [
          {
            alternativeId: null,
            selection: 'current',
            limitations: [],
            rationale: 'Retained current title.',
            recommendation: 'no_recommendation',
            recommendationReason: 'No recommendation.',
            evidence: [
              {
                source: 'Open Library',
                observation: 'Title: Old title',
                role: 'external_corroboration',
              },
              {
                source: 'Open Library',
                observation: 'Edition title: Old title',
                role: 'external_corroboration',
              },
              {
                source: 'Retained observation (provider name unavailable)',
                observation: 'Title: Other title',
                role: 'external_observation',
              },
              {
                source: 'Local library record',
                observation: 'Title: Old title',
                role: 'local_presence',
              },
            ],
          },
        ],
      },
    ],
  }))
  renderRoute('/library?lens=all&preview=case-1&field=title', http)
  await user.click(await screen.findByRole('button', { name: /^Evidence for Current value:/u }))
  expect(screen.getByText('1 source corroborates this choice.')).toBeVisible()
  expect(screen.getByText('Title: Other title')).toBeVisible()
  expect(screen.getByText('External observation')).toBeVisible()
  expect(http.recordDecision).not.toHaveBeenCalled()
})

describe('library provider throttling notice (#1282)', () => {
  const noon = new Date(2026, 9, 23, 12, 0)
  const seconds = (date: Date) => Math.floor(date.getTime() / 1000)
  // The time and its meridiem never wrap apart (#1282 R3 F8).
  const clock = (date: Date) =>
    new Intl.DateTimeFormat(undefined, { hour: 'numeric', minute: '2-digit' })
      .format(date)
      .replace(/\s/g, '\u00a0')
  const entry = (overrides: Partial<ProviderThrottleEntry> = {}): ProviderThrottleEntry => ({
    library: 'Synthetic ebooks',
    adapter: 'google_books',
    state: 'waiting',
    reasons: ['burst_throttled'],
    deferred: 214,
    next_retry_at: null,
    remedy: null,
    ...overrides,
  })
  const throttled = (
    entries: ProviderThrottleEntry[],
    retry: ProviderThrottlingView['retry'] = 'available',
  ): PreparationView => ({
    schema_version: 'review-preparation-v2',
    recovery_required: false,
    active: false,
    actionable: false,
    paused: false,
    libraries: [],
    provider_throttling: { generation: 4, retry, entries },
  })
  const notice = () => screen.findByRole('region', { name: 'Provider lookups waiting to retry' })
  const container = () => document.querySelector<HTMLElement>('.provider-throttle-notice')
  const freezeNoon = () => {
    vi.useFakeTimers({ toFake: ['Date'], now: noon })
    onTestFinished(() => {
      vi.useRealTimers()
    })
  }

  it('states each library and source once with its reason, count, timing and remedy', async () => {
    freezeNoon()
    const today = new Date(2026, 9, 23, 13, 30)
    const tomorrow = new Date(2026, 9, 24, 9, 0)
    const http: ReviewHttp = {
      ...createHttp(),
      getPreparation: vi.fn(async () =>
        throttled([
          entry({ next_retry_at: seconds(today), remedy: 'add_api_key' }),
          entry({
            adapter: 'hardcover',
            state: 'dormant',
            reasons: ['authentication'],
            deferred: 1,
            remedy: 'check_api_key',
          }),
          entry({
            library: 'Synthetic audiobooks with a long library name',
            adapter: 'open_library',
            state: 'exhausted',
            reasons: ['transient'],
            deferred: 10_000,
          }),
          entry({
            library: 'Second shelf',
            reasons: ['quota_exhausted'],
            deferred: 3,
            next_retry_at: seconds(tomorrow),
          }),
          entry({ adapter: 'itunes_search', state: 'checking', reasons: ['rate_limited'] }),
          entry({ adapter: 'audnexus', reasons: [], next_retry_at: seconds(noon) - 60 }),
          entry({ adapter: 'inventaire', reasons: ['configuration'], deferred: 2 }),
        ]),
      ),
    }
    renderRoute('/library?lens=all', http)
    const items = await within(await notice()).findAllByRole('listitem')
    const day = new Intl.DateTimeFormat(undefined, {
      weekday: 'short',
      day: 'numeric',
      month: 'short',
    }).format(tomorrow)
    expect(items.map((item) => item.querySelector('span')?.textContent)).toEqual([
      `Google Books · Synthetic ebooks: It is limiting requests. 214 deferred lookups. Next automatic retry ${clock(today)}.`,
      'Hardcover · Synthetic ebooks: It refused the configured API key. 1 deferred lookup. Waits for Retry now after the source is fixed.',
      `Open Library · Synthetic audiobooks with a long library name: It did not answer. ${(10_000).toLocaleString()} deferred lookups. No automatic retry remains.`,
      `Google Books · Second shelf: Its request quota is used up. 3 deferred lookups. Next automatic retry ${day}, ${clock(tomorrow)}.`,
      'iTunes · Synthetic ebooks: It is limiting requests. 214 deferred lookups. Retrying now.',
      'Audnexus · Synthetic ebooks: 214 deferred lookups. Retry due now.',
      'Inventaire · Synthetic ebooks: Its settings need checking. 2 deferred lookups. Retry due now.',
    ])
    // Only the first two entries carry a remedy, each one link to Metadata sources.
    expect(
      items.map((item) =>
        within(item)
          .queryAllByRole('link')
          .map((link) => [link.textContent, link.getAttribute('href')]),
      ),
    ).toEqual([
      [['Add a Google Books API key', '/settings?group=providers']],
      [['Check the source\u2019s API key', '/settings?group=providers']],
      [],
      [],
      [],
      [],
      [],
    ])
    expect(screen.getByRole('button', { name: 'Retry now' })).toBeVisible()
  })

  it.each([
    [
      'managed_by_file',
      'Settings are managed by /data/config.json: raise provider_recovery_generation by one there and restart the service.',
    ],
    ['unavailable', null],
  ] as const)('offers no Retry now when retry is %s', async (retry, instruction) => {
    const http: ReviewHttp = {
      ...createHttp(),
      getPreparation: vi.fn(async () => throttled([entry()], retry)),
    }
    renderRoute('/library?lens=all', http)
    const region = await notice()
    await within(region).findByRole('listitem')
    expect(within(region).queryByRole('button')).toBeNull()
    if (instruction) expect(within(region).getByText(instruction)).toBeVisible()
    else expect(region.querySelector('.provider-throttle-file')).toBeNull()
  })

  it('keeps an empty notice mounted without content or a region, and its live regions only with a projection', async () => {
    let current: PreparationView = { ...throttled([]), provider_throttling: null }
    const http: ReviewHttp = { ...createHttp(), getPreparation: vi.fn(async () => current) }
    const { queryClient } = renderRoute('/library?lens=all', http)
    await waitFor(() => expect(http.getPreparation).toHaveBeenCalled())
    await waitFor(() => expect(container()).not.toBeNull())
    const empty = container()
    if (!empty) throw new Error('the notice container is not mounted')
    expect(empty).toHaveClass('is-empty')
    expect(empty).toBeEmptyDOMElement()
    // Empty, it is no landmark: no region role and no label (#1282 R3 F4).
    expect(empty).not.toHaveAttribute('aria-label')
    expect(screen.queryByRole('region', { name: 'Provider lookups waiting to retry' })).toBeNull()
    current = throttled([])
    await act(() => queryClient.invalidateQueries({ queryKey: ['review-preparation'] }))
    await waitFor(() => expect(within(empty).getByRole('status')).toBeEmptyDOMElement())
    expect(within(empty).getByRole('alert')).toBeEmptyDOMElement()
    expect(empty).toHaveClass('is-empty')
    expect(empty).not.toHaveAttribute('aria-label')
    expect(within(empty).queryByRole('list')).toBeNull()
    // The label returns with the first entry, on the same element.
    current = throttled([entry()])
    await act(() => queryClient.invalidateQueries({ queryKey: ['review-preparation'] }))
    expect(await notice()).toBe(empty)
  })

  it('re-renders a passed wait as due without another preparation read, on one timer (#1282 R3 F1)', async () => {
    const real = { setTimeout: globalThis.setTimeout, clearTimeout: globalThis.clearTimeout }
    vi.useFakeTimers({
      toFake: ['Date', 'setTimeout', 'clearTimeout'],
      now: noon,
      shouldAdvanceTime: true,
    })
    // Recorded by hand: the configured mock restoration would reinstate a
    // spied fake timer for every later test.
    const fake = { setTimeout: globalThis.setTimeout, clearTimeout: globalThis.clearTimeout }
    const scheduled: (readonly [number, unknown])[] = []
    const cleared: unknown[] = []
    globalThis.setTimeout = ((...args: Parameters<typeof setTimeout>) => {
      const id = fake.setTimeout(...args)
      scheduled.push([args[1] ?? 0, id])
      return id
    }) as typeof setTimeout
    globalThis.clearTimeout = (id: Parameters<typeof clearTimeout>[0]) => {
      cleared.push(id)
      fake.clearTimeout(id)
    }
    onTestFinished(() => {
      vi.useRealTimers()
      Object.assign(globalThis, real)
    })
    // jest-dom compares normalised text, where the no-break space is a space.
    const spoken = (date: Date) => clock(date).replace(/\u00a0/g, ' ')
    const soon = new Date(noon.getTime() + 90_000)
    const later = new Date(noon.getTime() + 600_000)
    let current = throttled([
      entry({ next_retry_at: seconds(soon) }),
      entry({ adapter: 'inventaire', next_retry_at: seconds(later) }),
    ])
    const http: ReviewHttp = { ...createHttp(), getPreparation: vi.fn(async () => current) }
    const { queryClient } = renderRoute('/library?lens=all', http)
    const items = await within(await notice()).findAllByRole('listitem')
    expect(items[0]).toHaveTextContent(`Next automatic retry ${spoken(soon)}.`)
    // One timer, to the earliest wait still ahead; the query cache's own
    // five-minute collection timers are the only other long ones.
    const timers = () => scheduled.filter(([delay]) => delay > 60_000 && delay !== 300_000)
    expect(timers().map(([delay]) => delay > 80_000 && delay <= 90_000)).toEqual([true])
    const reads = vi.mocked(http.getPreparation).mock.calls.length
    await act(() => vi.advanceTimersByTimeAsync(91_000))
    expect(items[0]).toHaveTextContent('214 deferred lookups. Retry due now.')
    expect(items[1]).toHaveTextContent(`Next automatic retry ${spoken(later)}.`)
    expect(vi.mocked(http.getPreparation).mock.calls.length).toBe(reads)
    // The next timer runs to the next wait; a change replaces it.
    const [, second] = timers().at(-1) ?? []
    current = throttled([entry({ adapter: 'inventaire', next_retry_at: seconds(later) + 600 })])
    await act(() => queryClient.invalidateQueries({ queryKey: ['review-preparation'] }))
    await waitFor(() => expect(cleared).toContain(second))
    const [, third] = timers().at(-1) ?? []
    expect(third).not.toBe(second)
    cleanup()
    expect(cleared).toContain(third)
  })

  it('hands the Retry now settings to the Settings cache from Library (#1282 R3 F2)', async () => {
    const user = userEvent.setup()
    const saved = settingsView()
    const raised = {
      ...saved,
      document: { ...saved.document, revision: 9, provider_recovery_generation: 5 },
    }
    const http: ReviewHttp = {
      ...createHttp(),
      getPreparation: vi.fn(async () => throttled([entry()])),
      retryProviderRecovery: vi.fn(async () => raised),
    }
    const { queryClient } = renderRoute('/library?lens=all', http)
    await user.click(await within(await notice()).findByRole('button', { name: 'Retry now' }))
    await waitFor(() => expect(queryClient.getQueryData(['settings'])).toEqual(raised))
  })

  it('answers Retry now beside its control, success and refusal marked apart (#1282 R3 F6)', async () => {
    const user = userEvent.setup()
    const http: ReviewHttp = {
      ...createHttp(),
      getPreparation: vi.fn(async () => throttled([entry()])),
      retryProviderRecovery: vi
        .fn<ReviewHttp['retryProviderRecovery']>()
        .mockResolvedValueOnce(settingsView())
        .mockRejectedValueOnce(new ApiError(409, 'provider_recovery_stale')),
    }
    renderRoute('/library?lens=all', http)
    const region = await notice()
    const button = await within(region).findByRole('button', { name: 'Retry now' })
    const status = within(region).getByRole('status')
    const alert = within(region).getByRole('alert')
    // Both are mounted, empty, in the Retry now block and after the control.
    const block = button.closest('.provider-throttle-retry')
    for (const outcome of [status, alert]) {
      expect(outcome).toBeEmptyDOMElement()
      expect(outcome.closest('.provider-throttle-retry')).toBe(block)
      expect(
        button.compareDocumentPosition(outcome) & Node.DOCUMENT_POSITION_FOLLOWING,
      ).toBeTruthy()
    }
    expect(within(region).getByRole('list').compareDocumentPosition(status)).toBe(
      Node.DOCUMENT_POSITION_FOLLOWING,
    )
    await user.click(button)
    await waitFor(() => expect(status).toHaveTextContent('Retry now accepted.'))
    expect(status).toHaveClass('provider-throttle-outcome', 'provider-throttle-status')
    await user.click(button)
    await waitFor(() => expect(alert).toHaveTextContent('Retry now was already pressed elsewhere.'))
    expect(alert).toHaveClass('provider-throttle-outcome', 'provider-throttle-refusal')
    expect(status).toBeEmptyDOMElement()
  })

  it('sends one Retry now per press while pending and announces success in the mounted region', async () => {
    const user = userEvent.setup()
    let release: (value: ReturnType<typeof settingsView>) => void = () => undefined
    const http: ReviewHttp = {
      ...createHttp(),
      getPreparation: vi.fn(async () => throttled([entry()])),
      retryProviderRecovery: vi.fn(
        () =>
          new Promise<ReturnType<typeof settingsView>>((resolve) => {
            release = resolve
          }),
      ),
    }
    renderRoute('/library?lens=all', http)
    const region = await notice()
    const status = await within(region).findByRole('status')
    const button = await within(region).findByRole('button', { name: 'Retry now' })
    await user.click(button)
    expect(button).toHaveAttribute('aria-disabled', 'true')
    expect(button).toHaveAttribute('aria-busy', 'true')
    expect(button).toHaveTextContent('Retrying…')
    await user.click(button)
    await user.keyboard('{Enter}')
    expect(http.retryProviderRecovery).toHaveBeenCalledOnce()
    expect(http.retryProviderRecovery).toHaveBeenCalledWith(4)
    const reads = vi.mocked(http.getPreparation).mock.calls.length
    await act(async () => release(settingsView()))
    await waitFor(() =>
      expect(status).toHaveTextContent('Retry now accepted. Every deferred lookup will retry now.'),
    )
    expect(within(region).getByRole('status')).toBe(status)
    await waitFor(() =>
      expect(vi.mocked(http.getPreparation).mock.calls.length).toBeGreaterThan(reads),
    )
    expect(button).not.toHaveAttribute('aria-disabled')
    expect(button).toHaveFocus()
  })

  it.each([
    [
      new ApiError(409, 'provider_recovery_stale'),
      'Retry now was already pressed elsewhere. The notice now shows the current state.',
    ],
    [
      new ApiError(409, 'settings_managed_by_file'),
      'Settings are managed by /data/config.json: raise provider_recovery_generation by one there and restart the service.',
    ],
    [
      new ApiError(409, 'settings_stale'),
      'Settings changed elsewhere. Check the notice, then press Retry now again.',
    ],
    [
      new ApiError(503, 'settings_unavailable'),
      'Retry now could not be confirmed. Check the notice before pressing it again.',
    ],
  ])('alerts a refused Retry now (%s) and refreshes the notice', async (error, copy) => {
    const user = userEvent.setup()
    const http: ReviewHttp = {
      ...createHttp(),
      getPreparation: vi.fn(async () => throttled([entry()])),
      retryProviderRecovery: vi.fn(async () => {
        throw error
      }),
    }
    renderRoute('/library?lens=all', http)
    const region = await notice()
    const alert = await within(region).findByRole('alert')
    const reads = () => vi.mocked(http.getPreparation).mock.calls.length
    await waitFor(() => expect(reads()).toBeGreaterThan(0))
    const before = reads()
    await user.click(await within(region).findByRole('button', { name: 'Retry now' }))
    await waitFor(() => expect(alert).toHaveTextContent(copy))
    expect(within(region).getByRole('status')).toBeEmptyDOMElement()
    await waitFor(() => expect(reads()).toBeGreaterThan(before))
  })

  it('moves focus to the route, never the body, when the notice empties under the pressed control', async () => {
    const user = userEvent.setup()
    let current = throttled([entry()])
    const http: ReviewHttp = {
      ...createHttp(),
      getPreparation: vi.fn(async () => current),
      retryProviderRecovery: vi.fn(async () => {
        current = throttled([])
        return settingsView()
      }),
    }
    renderRoute('/library?lens=all', http)
    const region = await notice()
    const button = await within(region).findByRole('button', { name: 'Retry now' })
    await user.click(button)
    await waitFor(() => expect(within(region).queryByRole('button')).toBeNull())
    expect(document.activeElement).not.toBe(document.body)
    const main = document.getElementById('main-content')
    expect(document.activeElement).toBe(main?.querySelector('[data-route-focus]') ?? main)
    expect(within(region).getByRole('status')).toHaveTextContent('Retry now accepted.')
  })
})

describe('Library counts, search scope and grouped Attention (#1284)', () => {
  const counted = (attention: number, all: number): LibraryView => ({
    ...library,
    counts: { attention, all, clear: all - attention, notMatched: 0 },
  })
  const badge = () => document.querySelector('.attention-toggle strong')
  const worksStatus = () => document.querySelector('.library-works-heading [role="status"]')
  const railItem = (
    id: string,
    destination: string,
    title: string,
    workAttentionCount: number,
  ) => ({
    id,
    destination,
    title,
    authors: ['Synthetic Writer'],
    library: 'Books',
    mediaKind: 'ebook',
    notices: [],
    provenance: [],
    workAttentionCount,
  })
  const writesOff = () =>
    activity({
      configured_libraries: [{ connection: 'primary', library: 'fiction' }],
      capabilities: [
        {
          connection: 'primary',
          library: 'fiction',
          state: 'allowed',
          destinations: [{ consented: false, destination: 'title', prepared: 2, state: 'allowed' }],
        },
      ],
      waiting: {
        applying: 0,
        awaiting_permission: 2,
        held_for_recovery: 0,
        retrying: 0,
        scheduled: 0,
      },
    })

  it('V1: a search narrows only the list and the rail, never the badge or its denominators', async () => {
    const user = userEvent.setup()
    const http = createHttp()
    http.getLibrary = vi.fn<ReviewHttp['getLibrary']>(async (search) =>
      search.q ? counted(1, 1) : counted(3, 5),
    )
    const { router } = renderRoute('/library?lens=needs_attention&attention=open', http)
    await waitFor(() => expect(worksStatus()).toHaveTextContent(/^5 works$/u))
    expect(badge()).toHaveTextContent(/^3 works$/u)
    expect(screen.getByRole('button', { name: 'Attention 3 works' })).toBeVisible()
    const rail = screen.getByRole('complementary', { name: 'Attention queue' })
    expect(within(rail).getByText('3 works need a decision')).toBeVisible()
    // Opening the rail moves focus to it; type only once it has settled there.
    await waitFor(() => expect(rail).toHaveFocus())

    await user.type(screen.getByRole('searchbox'), 'needle')
    await waitFor(() => expect(router.state.location.search.q).toBe('needle'))
    await waitFor(() => expect(worksStatus()).toHaveTextContent(/^Matching: 1 of 5 works$/u))
    expect(badge()).toHaveTextContent(/^3 works$/u)
    expect(
      within(screen.getByRole('complementary', { name: 'Attention queue' })).getByText(
        'Matching: 1 of 3 works needing a decision',
      ),
    ).toBeVisible()

    await user.clear(screen.getByRole('searchbox'))
    await waitFor(() => expect(router.state.location.search.q).toBeUndefined())
    await waitFor(() => expect(worksStatus()).toHaveTextContent(/^5 works$/u))
    expect(badge()).toHaveTextContent(/^3 works$/u)
    // The summary is the unsearched lens=all read, read once and never with q.
    const summaryCalls = vi
      .mocked(http.getLibrary)
      .mock.calls.filter(([search]) => search.lens === 'all')
    expect(summaryCalls.map(([search]) => search)).toEqual([{ lens: 'all' }])
  })

  it('V1: singular forms name one work', async () => {
    const http = createHttp()
    http.getLibrary = vi.fn<ReviewHttp['getLibrary']>(async () => counted(1, 1))
    renderRoute('/library?lens=all&attention=open', http)
    await waitFor(() => expect(worksStatus()).toHaveTextContent(/^1 work$/u))
    expect(badge()).toHaveTextContent(/^1 work$/u)
    expect(screen.getByText('1 work needs a decision')).toBeVisible()
  })

  it('V2: Attention lists each work once with its fields as rows, and Load more continues a work', async () => {
    const user = userEvent.setup()
    const http = createHttp()
    const workA = railItem('case-1', 'title', 'Work A', 2)
    http.getAttention = vi.fn<ReviewHttp['getAttention']>(async ({ cursor }) =>
      cursor === 'attention-page-2'
        ? {
            total: 5,
            items: [
              railItem('case-2', 'authors', 'Work B', 2),
              railItem('case-3', 'title', 'Work C', 1),
            ],
          }
        : {
            total: 5,
            items: [
              workA,
              { ...workA, destination: 'description' },
              railItem('case-2', 'title', 'Work B', 2),
            ],
            nextCursor: 'attention-page-2',
          },
    )
    renderRoute('/library?lens=all&attention=open', http)
    const rail = await screen.findByRole('complementary', { name: 'Attention queue' })
    await waitFor(() => expect(rail.querySelectorAll('.attention-work-group')).toHaveLength(2))
    const groups = [...rail.querySelectorAll<HTMLElement>('.attention-work-group')]
    expect(rail.querySelectorAll('button.attention-queue-item')).toHaveLength(3)
    expect(within(rail).getAllByText('Work A')).toHaveLength(1)
    expect(within(rail).getAllByText('Synthetic Writer')).toHaveLength(2)
    expect(groups.map((group) => group.querySelector('.attention-position')?.textContent)).toEqual([
      '1',
      '2',
    ])
    expect(
      groups.map((group) => group.querySelector('.attention-work-count')?.textContent),
    ).toEqual(['2 fields', '2 fields'])
    const rows = within(groups[0] ?? rail).getAllByRole('button')
    expect(rows.map((row) => row.querySelector('.attention-queue-copy b')?.textContent)).toEqual([
      'Title · Needs your decision',
      'Description · Needs your decision',
    ])
    for (const row of rows) expect(row).toHaveAccessibleDescription('Work A')

    await user.click(screen.getByRole('button', { name: 'Load more attention' }))
    await waitFor(() => expect(rail.querySelectorAll('.attention-work-group')).toHaveLength(3))
    const workB = within(rail).getAllByRole('button', { description: 'Work B' })
    expect(workB.map((row) => row.querySelector('b')?.textContent)).toEqual([
      'Title · Needs your decision',
      'Authors · Needs your decision',
    ])
    expect(workB[0]?.closest('.attention-work-group')).toBe(
      workB[1]?.closest('.attention-work-group'),
    )
    expect(within(rail).getAllByText('Work B')).toHaveLength(1)
    expect(rail.querySelectorAll('button.attention-queue-item')).toHaveLength(5)
  })

  it('M2: Load more moves focus to the first row it added, even inside an existing work', async () => {
    const user = userEvent.setup()
    const http = createHttp()
    let release: () => void = () => undefined
    http.getAttention = vi.fn<ReviewHttp['getAttention']>(async ({ cursor }) =>
      cursor === 'attention-page-2'
        ? new Promise((resolve) => {
            release = () => {
              resolve({
                total: 3,
                items: [
                  railItem('case-2', 'authors', 'Work B', 2),
                  railItem('case-3', 'title', 'Work C', 1),
                ],
              })
            }
          })
        : {
            total: 3,
            items: [
              railItem('case-1', 'title', 'Work A', 1),
              railItem('case-2', 'title', 'Work B', 2),
            ],
            nextCursor: 'attention-page-2',
          },
    )
    renderRoute('/library?lens=all&attention=open', http)
    const rail = await screen.findByRole('complementary', { name: 'Attention queue' })
    const more = await within(rail).findByRole('button', { name: 'Load more attention' })
    // Opening the rail focuses it a frame later; the owner reaches Load more after that.
    await waitFor(() => expect(rail).toHaveFocus())
    more.focus()
    await user.keyboard('{Enter}')
    // While the page loads the control keeps focus, and a second press is ignored.
    await waitFor(() => expect(more).toHaveAttribute('aria-disabled', 'true'))
    expect(more).toHaveFocus()
    await user.keyboard('{Enter}')
    release()
    const added = await within(rail).findByRole('button', { name: /^Authors/u })
    await waitFor(() => expect(added).toHaveFocus())
    expect(added).toHaveAccessibleDescription('Work B')
    expect(
      vi
        .mocked(http.getAttention)
        .mock.calls.filter(([read]) => read.cursor === 'attention-page-2'),
    ).toHaveLength(1)
    await user.tab()
    expect(document.activeElement).not.toBe(document.body)
    expect(document.activeElement).toBe(
      within(rail).getByRole('button', { name: /^Title/u, description: 'Work C' }),
    )
  })

  it('CR-b: Load more still loads the next page when each page lands at once', async () => {
    const user = userEvent.setup()
    const http = createHttp()
    const page = (name: string, nextCursor?: string) => ({
      total: 3,
      items: [railItem(`case-${name}`, 'title' as const, `Work ${name}`, 1)],
      ...(nextCursor ? { nextCursor } : {}),
    })
    http.getAttention = vi.fn<ReviewHttp['getAttention']>(({ cursor }) =>
      Promise.resolve(
        cursor === 'attention-page-3'
          ? page('C')
          : cursor === 'attention-page-2'
            ? page('B', 'attention-page-3')
            : page('A', 'attention-page-2'),
      ),
    )
    renderRoute('/library?lens=all&attention=open', http)
    const rail = await screen.findByRole('complementary', { name: 'Attention queue' })
    await waitFor(() => expect(rail).toHaveFocus())
    await user.click(await within(rail).findByRole('button', { name: 'Load more attention' }))
    await within(rail).findByRole('button', { name: /^Title/u, description: 'Work B' })
    await user.click(within(rail).getByRole('button', { name: 'Load more attention' }))
    await within(rail).findByRole('button', { name: /^Title/u, description: 'Work C' })
    expect(vi.mocked(http.getAttention).mock.calls.map(([read]) => read.cursor)).toEqual([
      undefined,
      'attention-page-2',
      'attention-page-3',
    ])
  })

  it('CR-d: Load more still loads the next page after a refetch cancels it', async () => {
    const user = userEvent.setup()
    const http = createHttp()
    let secondPages = 0
    http.getAttention = vi.fn<ReviewHttp['getAttention']>(async ({ cursor }) => {
      if (cursor !== 'attention-page-2')
        return {
          total: 2,
          items: [railItem('case-1', 'title', 'Work A', 1)],
          nextCursor: 'attention-page-2',
        }
      secondPages += 1
      // The first request for page 2 is still loading when the refetch cancels it.
      if (secondPages === 1) return new Promise(() => undefined)
      return { total: 2, items: [railItem('case-2', 'title', 'Work B', 1)] }
    })
    const { queryClient } = renderRoute('/library?lens=all&attention=open', http)
    const rail = await screen.findByRole('complementary', { name: 'Attention queue' })
    await waitFor(() => expect(rail).toHaveFocus())
    const more = within(rail).getByRole('button', { name: 'Load more attention' })
    await user.click(more)
    await waitFor(() => expect(more).toHaveAttribute('aria-disabled', 'true'))
    await queryClient.invalidateQueries({ queryKey: ['attention'] })
    await waitFor(() => expect(more).not.toHaveAttribute('aria-disabled'))
    await user.click(more)
    await within(rail).findByRole('button', { name: /^Title/u, description: 'Work B' })
    expect(secondPages).toBe(2)
  })

  it('CR-a: Load more leaves focus in the search field the owner moved to', async () => {
    const user = userEvent.setup()
    const http = createHttp()
    let release: () => void = () => undefined
    http.getAttention = vi.fn<ReviewHttp['getAttention']>(async ({ cursor }) =>
      cursor === 'attention-page-2'
        ? new Promise((resolve) => {
            release = () => {
              resolve({ total: 2, items: [railItem('case-2', 'title', 'Work B', 1)] })
            }
          })
        : {
            total: 2,
            items: [railItem('case-1', 'title', 'Work A', 1)],
            nextCursor: 'attention-page-2',
          },
    )
    renderRoute('/library?lens=all&attention=open', http)
    const rail = await screen.findByRole('complementary', { name: 'Attention queue' })
    await waitFor(() => expect(rail).toHaveFocus())
    await user.click(within(rail).getByRole('button', { name: 'Load more attention' }))
    const search = screen.getByRole('searchbox')
    await user.click(search)
    await user.keyboard('Work')
    release()
    await within(rail).findByRole('button', { name: /^Title/u, description: 'Work B' })
    expect(search).toHaveFocus()
  })

  it.each([
    ['with a later page', true, 'Work 1 of 2 loaded'],
    ['fully loaded', false, 'Work 1 of 2'],
  ] as const)('V2: Queue position counts loaded works %s', async (_name, more, position) => {
    const user = userEvent.setup()
    const http = createHttp()
    http.getAttention = vi.fn<ReviewHttp['getAttention']>(async () => ({
      total: 2,
      items: [railItem('case-1', 'title', 'A Book', 1), railItem('case-2', 'title', 'Work B', 1)],
      ...(more ? { nextCursor: 'attention-page-2' } : {}),
    }))
    renderRoute('/library?lens=all&attention=open', http)
    await user.click(await screen.findByRole('button', { name: /^Title/u, description: 'A Book' }))
    const label = await screen.findByText('Queue position')
    expect(label.nextElementSibling).toHaveTextContent(new RegExp(`^${position}$`, 'u'))
    await user.click(screen.getByRole('button', { name: /^Attention/u }))
    expect(
      await screen.findByRole('button', { name: /^Title/u, description: 'A Book' }),
    ).toHaveAttribute('aria-current', 'true')
    expect(screen.getByRole('button', { name: /^Title/u, description: 'Work B' })).toHaveAttribute(
      'aria-current',
      'false',
    )
  })

  it('V3: no count reads zero before its source resolves', async () => {
    let release: ((value: LibraryView) => void) | undefined
    const view = writesOff()
    const http: ReviewHttp = {
      ...createHttp(),
      getActivityConsent: vi.fn(async () => consentOf(view)),
      getLibrary: vi.fn(
        () =>
          new Promise<LibraryView>((resolve) => {
            release = resolve
          }),
      ),
    }
    renderRoute('/library?lens=all&attention=open', http)
    expect(
      await screen.findByRole('heading', { name: 'Counting works in Library… Writes are off.' }),
    ).toBeVisible()
    expect(badge()).toHaveTextContent(/^…$/u)
    expect(worksStatus()).toHaveTextContent(/^Loading count…$/u)
    expect(screen.getByText('Counting works…')).toBeVisible()
    expect(document.body.textContent).not.toMatch(/\b0 works?\b/u)
    await act(async () => release?.(counted(2, 5)))
    expect(
      await screen.findByRole('heading', { name: '5 works are in Library. Writes are off.' }),
    ).toBeVisible()
    expect(badge()).toHaveTextContent(/^2 works$/u)
  })

  it('V3: a failed summary omits its figure rather than printing zero', async () => {
    const view = writesOff()
    const http: ReviewHttp = {
      ...createHttp(),
      getActivityConsent: vi.fn(async () => consentOf(view)),
      getLibrary: vi.fn(async () => Promise.reject(new Error('synthetic library failure'))),
    }
    renderRoute('/library?lens=all', http)
    expect(await screen.findByRole('heading', { name: 'Writes are off.' })).toBeVisible()
    expect(screen.getByRole('button', { name: 'Attention' })).toBeVisible()
    expect(badge()).toBeNull()
    expect(worksStatus()).toHaveTextContent(/^Count unavailable$/u)
  })

  it('states a lapse in the write notice even with nothing prepared (#1370)', async () => {
    const lapsed = activity({
      configured_libraries: [{ connection: 'primary', library: 'fiction' }],
      capabilities: [
        {
          connection: 'primary',
          library: 'fiction',
          state: 'allowed',
          destinations: [{ consented: false, destination: 'title', prepared: 0, state: 'allowed' }],
        },
      ],
      lapsed_consents: [
        { connection: 'primary', library: 'fiction', reason: 'library_unresolved' },
      ],
    })
    const http: ReviewHttp = {
      ...createHttp(),
      getActivityConsent: vi.fn(async () => consentOf(lapsed)),
      getLibrary: vi.fn(async () => counted(0, 5)),
      // The lapse names the library as Settings does, not by its slug.
      getSettings: vi.fn(async () => ({
        ...settingsView(),
        document: {
          ...settingsView().document,
          libraries: settingsView().document.libraries.map((library) => ({
            ...library,
            name: 'fiction',
            display_name: 'Fiction Shelf',
          })),
        },
      })),
    }
    renderRoute('/library?lens=all', http)

    const heading = await screen.findByRole('heading', {
      name: '5 works are in Library. Writes are off.',
    })
    await waitFor(() =>
      expect(heading.closest('.cm-library-handoff')).toHaveTextContent(
        'Write permission for Fiction Shelf lapsed because the active policy does not cover that library.',
      ),
    )
    expect(screen.getByRole('link', { name: 'Review write permissions' })).toBeVisible()
  })

  it('states a lapse beside a library that kept its permission (#1370)', async () => {
    const view = activity({
      configured_libraries: ['fiction', 'history'].map((library) => ({
        connection: 'primary',
        library,
      })),
      capabilities: ['fiction', 'history'].map((library) => ({
        connection: 'primary',
        library,
        state: 'allowed',
        destinations: [
          { consented: library === 'fiction', destination: 'title', prepared: 0, state: 'allowed' },
        ],
      })),
      lapsed_consents: [{ connection: 'primary', library: 'history', reason: 'policy_changed' }],
    })
    const http: ReviewHttp = {
      ...createHttp(),
      getActivityConsent: vi.fn(async () => consentOf(view)),
      getLibrary: vi.fn(async () => counted(0, 5)),
    }
    renderRoute('/library?lens=all', http)

    // Writes are enabled only for the library that kept permission (#1370).
    const heading = await screen.findByRole('heading', {
      name: '5 works are in Library. Writes are enabled for fiction.',
    })
    expect(heading.closest('.cm-library-handoff')).toHaveTextContent(
      'Write permission for history lapsed because it was given under a policy that is no longer active. Review current write permissions in Activity.',
    )
    expect(screen.getByRole('link', { name: 'Review write permissions' })).toBeVisible()
  })

  it('announces a lapse once, by its Settings name (#1370)', async () => {
    const view = activity({
      configured_libraries: [{ connection: 'primary', library: 'fiction' }],
      capabilities: [
        {
          connection: 'primary',
          library: 'fiction',
          state: 'allowed',
          destinations: [{ consented: false, destination: 'title', prepared: 0, state: 'allowed' }],
        },
      ],
      lapsed_consents: [{ connection: 'primary', library: 'fiction', reason: 'policy_changed' }],
    })
    let settle: (value: Awaited<ReturnType<ReviewHttp['getSettings']>>) => void = () => undefined
    const http: ReviewHttp = {
      ...createHttp(),
      getActivityConsent: vi.fn(async () => consentOf(view)),
      getLibrary: vi.fn(async () => counted(0, 5)),
      getSettings: vi.fn(
        () =>
          new Promise<Awaited<ReturnType<ReviewHttp['getSettings']>>>((resolve) => {
            settle = resolve
          }),
      ),
    }
    // Every text the polite live region ever carries, in order.
    const said: string[] = []
    const observer = new MutationObserver(() => {
      const text = document.querySelector('.cm-library-handoff')?.textContent ?? ''
      if (text.includes('lapsed') && said.at(-1) !== text) said.push(text)
    })
    observer.observe(document.body, { subtree: true, childList: true, characterData: true })
    renderRoute('/library?lens=all', http)

    await waitFor(() => expect(http.getSettings).toHaveBeenCalled())
    expect(document.querySelector('.cm-library-handoff')).toBeNull()
    act(() =>
      settle({
        ...settingsView(),
        document: {
          ...settingsView().document,
          libraries: settingsView().document.libraries.map((library) => ({
            ...library,
            name: 'fiction',
            display_name: 'Fiction Shelf',
          })),
        },
      }),
    )
    await screen.findByText(/Write permission for Fiction Shelf lapsed/)
    observer.disconnect()
    expect(said).toHaveLength(1)
    expect(said[0]).not.toContain('for fiction lapsed')
  })

  it.each([
    ['', 'Count unavailable'],
    ['&q=needle', 'Matching: 1 work needing a decision'],
  ] as const)('V3: a failed summary never leaves the rail counting (%s)', async (query, copy) => {
    const http = createHttp()
    http.getLibrary = vi.fn<ReviewHttp['getLibrary']>(async (search) =>
      search.q ? counted(1, 1) : Promise.reject(new Error('synthetic summary failure')),
    )
    renderRoute(`/library?lens=all&attention=open${query}`, http)
    const rail = await screen.findByRole('complementary', { name: 'Attention queue' })
    await waitFor(() => expect(rail.querySelector('header p')).toHaveTextContent(copy))
    expect(badge()).toBeNull()
  })

  const settledTitle = (): CaseDetailView => {
    const titleField = detail.fields[0]
    if (!titleField) throw new Error('completion fixture requires a title field')
    return {
      ...detail,
      fields: [
        {
          ...titleField,
          needsAttention: false,
          humanDecision: {
            actor: 'owner',
            application: 'apply' as const,
            decision_id: 'decision-filtered',
            destination: 'title',
            occurred_at: 1,
            outcome: 'keep_current',
            proposal_id: titleField.proposalId,
            review_case_id: 'case-1',
            selected_value: null,
            target_fingerprint: titleField.targetFingerprint,
          },
        },
      ],
    }
  }
  const completeUnderSearch = async (
    entry: string,
    summary: (decided: boolean) => Promise<LibraryView>,
  ) => {
    const user = userEvent.setup()
    let decided = false
    let current = detail
    const titleField = detail.fields[0]
    if (!titleField) throw new Error('completion fixture requires a title field')
    const http: ReviewHttp = {
      ...createHttp(),
      getLibrary: vi.fn(async (search: LibrarySearch) =>
        search.q ? counted(decided ? 0 : 1, 1) : summary(decided),
      ),
      getAttention: vi.fn(async () =>
        decided
          ? { total: 0, items: [] }
          : { total: 1, items: [railItem('case-1', 'title', 'A Book', 1)] },
      ),
      getCaseDetail: vi.fn(async () => current),
      recordDecision: vi.fn(async () => {
        decided = true
        current = settledTitle()
        return {
          confirmation: { status: 'confirmed' as const, case: current },
          invalidation_sequences: [],
          schema_version: 'review-decision-v1' as const,
          actor: 'owner' as const,
          application: 'apply' as const,
          decision_id: 'decision-filtered',
          destination: 'title',
          occurred_at: 1,
          outcome: 'keep_current' as const,
          proposal_id: titleField.proposalId,
          review_case_id: 'case-1',
          selected_value: null,
          target_fingerprint: titleField.targetFingerprint,
        }
      }),
    }
    renderRoute(entry, http)
    await user.click(await screen.findByRole('button', { name: 'Choose current value: Old title' }))
    const complete = (await screen.findByRole('heading', { name: 'Review complete' })).closest(
      'article',
    )
    if (!complete) throw new Error('completion boundary is required')
    return complete.querySelector('footer')
  }

  it.each([
    [
      3,
      'All review work matching this search is complete. 2 more works need a decision outside this search.',
    ],
    [
      2,
      'All review work matching this search is complete. 1 more work needs a decision outside this search.',
    ],
    [1, 'All review work matching this search is complete. No other work needs a decision.'],
  ] as const)(
    'V4: completing a search with %i library-wide works states what remains outside it',
    async (before, copy) => {
      const footer = await completeUnderSearch(
        '/library?lens=all&q=needle&preview=case-1&field=title',
        async (decided) => counted(decided ? before - 1 : before, 4),
      )
      await waitFor(() => expect(footer).toHaveTextContent(new RegExp(`^${copy}$`, 'u')))
    },
  )

  it('V4: a remainder skewed below zero is clamped', async () => {
    const footer = await completeUnderSearch(
      '/library?lens=all&q=needle&preview=case-1&field=title',
      async () => counted(0, 4),
    )
    await waitFor(() =>
      expect(footer).toHaveTextContent(
        /^All review work matching this search is complete\. No other work needs a decision\.$/u,
      ),
    )
  })

  it('V4: a pending summary leaves the remainder unstated', async () => {
    const footer = await completeUnderSearch(
      '/library?lens=all&q=needle&preview=case-1&field=title',
      () => new Promise<LibraryView>(() => undefined),
    )
    await waitFor(() =>
      expect(footer).toHaveTextContent(/^All review work matching this search is complete\.$/u),
    )
  })

  it('V4: without a search the completion footer is unchanged', async () => {
    const footer = await completeUnderSearch(
      '/library?lens=needs_attention&preview=case-1&field=title',
      async (decided) => counted(decided ? 2 : 3, 4),
    )
    await waitFor(() =>
      expect(footer).toHaveTextContent(/^All visible review work is complete\.$/u),
    )
  })

  it('V5: the default landing and the summary share one request', async () => {
    const user = userEvent.setup()
    const http = createHttp()
    renderRoute('/library?lens=all', http)
    await waitFor(() => expect(worksStatus()).toHaveTextContent(/^1 work$/u))
    expect(http.getLibrary).toHaveBeenCalledOnce()
    cleanup()

    const lensed = createHttp()
    const { router } = renderRoute('/library?lens=needs_attention', lensed)
    await waitFor(() => expect(worksStatus()).toHaveTextContent(/^1 work$/u))
    expect(vi.mocked(lensed.getLibrary).mock.calls.map(([search]) => search)).toEqual(
      expect.arrayContaining([
        { lens: 'all' },
        expect.objectContaining({ lens: 'needs_attention' }),
      ]),
    )
    expect(lensed.getLibrary).toHaveBeenCalledTimes(2)
    await user.type(screen.getByRole('searchbox'), 'needle')
    await waitFor(() => expect(router.state.location.search.q).toBe('needle'))
    await waitFor(() => expect(lensed.getLibrary).toHaveBeenCalledTimes(3))
    expect(vi.mocked(lensed.getLibrary).mock.calls[2]?.[0]).toMatchObject({
      lens: 'needs_attention',
      q: 'needle',
    })
  })

  it('V7: one toolbar, with the same children, whether or not a work is selected', async () => {
    const user = userEvent.setup()
    const view = writesOff()
    const http: ReviewHttp = {
      ...createHttp(),
      getActivityConsent: vi.fn(async () => consentOf(view)),
    }
    renderRoute('/library?lens=all', http)
    await screen.findByRole('heading', { name: '1 work is in Library. Writes are off.' })
    const header = document.querySelector('header[aria-label="Library controls"]')
    const toggle = screen.getByRole('button', { name: 'Attention 1 work' })
    const children = () => [...(header?.children ?? [])].map((child) => child.className)
    const before = children()
    expect(before).toEqual(['attention-toggle', expect.stringContaining('cm-library-handoff')])
    expect(header).toHaveAttribute('data-route-focus', 'true')
    expect(document.querySelector('.compact-workspace-back')).toBeNull()

    await user.click(screen.getByRole('button', { name: /^A Book\./u }))
    await screen.findByRole('heading', { level: 2, name: 'A Book' })
    expect(document.querySelector('header[aria-label="Library controls"]')).toBe(header)
    expect(children()).toEqual(before)
    expect(screen.getByRole('button', { name: 'Attention 1 work' })).toBe(toggle)
    expect(header).not.toHaveAttribute('data-route-focus')
    expect(document.querySelector('.library-workspace')?.firstElementChild).toBe(
      screen.getByRole('button', { name: 'Back to works' }),
    )

    await user.click(toggle)
    const rail = await screen.findByRole('complementary', { name: 'Attention queue' })
    expect(toggle).toBeInTheDocument()
    expect(toggle).toHaveAttribute('aria-expanded', 'true')
    expect(screen.queryByRole('button', { name: 'Back to works' })).toBeNull()
    expect(children()).toEqual(before)
    await user.click(within(rail).getByRole('button', { name: 'Close' }))
    await waitFor(() => expect(toggle).toHaveAttribute('aria-expanded', 'false'))
    await waitFor(() => expect(document.activeElement).not.toBe(document.body))
    expect(screen.getByRole('button', { name: 'Back to works' })).toBeVisible()
  })

  it.each([
    [2, 'fields match this search'],
    [1, 'field matches this search'],
  ] as const)('V8: /attention under a search reads %i %s', async (total, copy) => {
    const http = createHttp()
    http.getAttention = vi.fn<ReviewHttp['getAttention']>(async () => ({
      total,
      items: [railItem('case-1', 'title', 'A Book', total)],
    }))
    renderRoute('/attention?q=needle', http)
    const count = await screen.findByText(copy)
    expect(count.previousElementSibling).toHaveTextContent(String(total))
    cleanup()
    renderRoute('/attention', http)
    expect(
      await screen.findByText(
        total === 1 ? 'field across current works' : 'fields across current works',
      ),
    ).toBeVisible()
  })
})
