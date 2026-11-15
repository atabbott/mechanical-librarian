import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import {
  Outlet,
  RouterProvider,
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
} from '@tanstack/react-router'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { BuildMismatchError } from '../src/api/http'
import type { LibraryCounts, LibraryProgressView, ReviewHttp } from '../src/api/http'
import { LibraryProgressLine } from '../src/components/LibraryProgressLine'
import { ActivityLibraryProgress } from '../src/routes/ActivityLibraryProgress'
import { createHttp, libraryProgress } from './fixtures'

// The Library progress line and Activity card on their own (#1335), in a
// router with only the routes their links name. Their places in the run's box
// and on Activity are proved through the application router.

const counts = (value: Partial<LibraryCounts>): LibraryCounts => ({
  all: 0,
  attention: 0,
  clear: 0,
  notMatched: 0,
  failed: 0,
  ...value,
})

const mount = (
  entry: string,
  {
    line,
    http = createHttp(),
  }: { line?: { counts: LibraryCounts | undefined; linked: boolean }; http?: ReviewHttp },
) => {
  const root = createRootRoute({ component: () => <Outlet /> })
  const routes = [
    createRoute({
      getParentRoute: () => root,
      path: '/line',
      component: () => (line ? <LibraryProgressLine {...line} /> : null),
    }),
    createRoute({
      getParentRoute: () => root,
      path: '/activity',
      component: () => (
        <main>
          <h1>Activity</h1>
          <ActivityLibraryProgress http={http} />
        </main>
      ),
    }),
    createRoute({
      getParentRoute: () => root,
      path: '/library',
      component: () => <h1>Library</h1>,
    }),
  ]
  const router = createRouter({
    routeTree: root.addChildren(routes),
    history: createMemoryHistory({ initialEntries: [entry] }),
  })
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  )
  return { http, queryClient, router }
}

const lineText = async () => {
  const line = await waitFor(() => {
    const found = document.querySelector('.library-progress-line')
    if (!found) throw new Error('no line yet')
    return found as HTMLElement
  })
  return line
}

describe('LibraryProgressLine', () => {
  it('groups each number and links every nonzero one to its lens', async () => {
    mount('/line', {
      line: {
        counts: counts({ all: 2026, clear: 1840, attention: 174, failed: 3, notMatched: 9 }),
        linked: true,
      },
    })
    const line = await lineText()
    expect(line).toHaveTextContent(
      '1,840 of 2,026 works settled · 174 need you · 3 could not finish · 9 not matched',
    )
    const links = within(line).getAllByRole('link')
    expect(links.map((link) => [link.textContent, link.getAttribute('href')])).toEqual([
      ['1,840', '/library?lens=no_attention_needed'],
      ['2,026', '/library?lens=all'],
      ['174', '/library?lens=needs_attention'],
      ['3', '/library?lens=preparation_failed'],
      ['9', '/library?lens=not_matched'],
    ])
  })

  it('reads a single work in the singular and leaves a zero unlinked', async () => {
    mount('/line', { line: { counts: counts({ all: 1, clear: 1 }), linked: true } })
    const line = await lineText()
    // No tail for what is zero, and "0 need you" is plain text.
    expect(line).toHaveTextContent(/^1 of 1 work settled · 0 need you$/u)
    expect(
      within(line)
        .getAllByRole('link')
        .map((link) => link.textContent),
    ).toEqual(['1', '1'])
  })

  it('says "needs" for one work needing you', async () => {
    mount('/line', {
      line: { counts: counts({ all: 3, clear: 2, attention: 1 }), linked: false },
    })
    const line = await lineText()
    expect(line).toHaveTextContent(/^2 of 3 works settled · 1 needs you$/u)
    expect(within(line).queryAllByRole('link')).toEqual([])
  })

  it('shows an ellipsis, never zero, for counts not yet known', async () => {
    mount('/line', { line: { counts: undefined, linked: true } })
    const line = await lineText()
    expect(line).toHaveTextContent(/^… of … works settled · … need you$/u)
    expect(line).not.toHaveTextContent('0')
    expect(within(line).queryAllByRole('link')).toEqual([])
  })
})

const card = () => screen.findByRole('region', { name: 'Library progress' })

describe('ActivityLibraryProgress', () => {
  it('counts works while loading, then shows totals, libraries and fields', async () => {
    let answer: (view: LibraryProgressView) => void = () => undefined
    const http = createHttp()
    http.getLibraryProgress = vi.fn(
      () =>
        new Promise<LibraryProgressView>((resolve) => {
          answer = resolve
        }),
    )
    mount('/activity', { http })
    const region = await card()
    expect(within(region).getByRole('status')).toHaveTextContent('Counting works…')
    expect(region).toHaveTextContent('… of … works settled · … need you')
    answer({
      totals: counts({ all: 2026, clear: 1840, attention: 174, failed: 3, notMatched: 9 }),
      libraries: [
        {
          connection: 'primary',
          library: 'ebooks',
          libraryDisplayName: 'Reading Room',
          counts: counts({ all: 26, clear: 20, attention: 6 }),
        },
        {
          connection: 'primary',
          library: 'audiobooks',
          libraryDisplayName: 'audiobooks',
          counts: counts({ all: 2000, clear: 1820, attention: 168, failed: 3, notMatched: 9 }),
        },
      ],
      fields: [
        { destination: 'cover', needYou: 120 },
        { destination: 'isbn', needYou: 1 },
      ],
    })
    await within(region).findByText('By library')
    expect(within(region).queryByRole('status')).toBeNull()
    const [totals, ...rows] = Array.from(
      region.querySelectorAll<HTMLElement>('.library-progress-line'),
    )
    if (totals === undefined) throw new Error('no totals line')
    expect(totals).toHaveTextContent(
      '1,840 of 2,026 works settled · 174 need you · 3 could not finish · 9 not matched',
    )
    expect(within(totals).getAllByRole('link')).toHaveLength(5)
    // Each library's line, in the order the server gave, with no links.
    const [libraries, fields] = within(region).getAllByRole('list')
    if (libraries === undefined || fields === undefined) throw new Error('no lists')
    expect(
      within(libraries)
        .getAllByRole('listitem')
        .map((item) => item.textContent),
    ).toEqual([
      'Reading Room 20 of 26 works settled · 6 need you',
      'audiobooks 1,820 of 2,000 works settled · 168 need you · 3 could not finish · 9 not matched',
    ])
    for (const row of rows) expect(within(row).queryAllByRole('link')).toEqual([])
    expect(within(region).getByRole('heading', { name: 'Fields that need you' })).toBeVisible()
    expect(
      within(fields)
        .getAllByRole('listitem')
        .map((item) => item.textContent),
    ).toEqual(['Cover 120 works', 'ISBN 1 work'])
  })

  it('lists no libraries for one, and no fields when none needs you', async () => {
    const http = createHttp()
    http.getLibraryProgress = vi.fn(async () => ({
      ...libraryProgress,
      totals: counts({ all: 4, clear: 4 }),
      libraries: libraryProgress.libraries.map((library) => ({
        ...library,
        counts: counts({ all: 4, clear: 4 }),
      })),
      fields: [],
    }))
    mount('/activity', { http })
    const region = await card()
    await waitFor(() => expect(region).toHaveTextContent('4 of 4 works settled · 0 need you'))
    expect(within(region).queryByText('By library')).toBeNull()
    expect(within(region).queryByText('Fields that need you')).toBeNull()
    expect(within(region).queryAllByRole('list')).toEqual([])
  })

  it('is absent while Library has no works', async () => {
    const http = createHttp()
    http.getLibraryProgress = vi.fn(async () => ({
      totals: counts({}),
      libraries: [],
      fields: [],
    }))
    mount('/activity', { http })
    await waitFor(() => expect(http.getLibraryProgress).toHaveBeenCalled())
    await waitFor(() =>
      expect(screen.queryByRole('region', { name: 'Library progress' })).toBeNull(),
    )
  })

  it('offers Try again after a failed read, and keeps focus with the card', async () => {
    const user = userEvent.setup()
    const http = createHttp()
    http.getLibraryProgress = vi
      .fn<ReviewHttp['getLibraryProgress']>()
      .mockRejectedValueOnce(new Error('unavailable'))
      .mockResolvedValue(libraryProgress)
    mount('/activity', { http })
    const region = await card()
    const alert = await within(region).findByRole('alert')
    expect(alert).toHaveTextContent('Library progress could not be loaded.')
    await user.click(within(alert).getByRole('button', { name: 'Try again' }))
    await waitFor(() => expect(region).toHaveTextContent('0 of 1 work settled · 1 needs you'))
    expect(http.getLibraryProgress).toHaveBeenCalledTimes(2)
    await waitFor(() =>
      expect(document.activeElement).toBe(
        within(region).getByRole('heading', { name: 'Library progress' }),
      ),
    )
  })

  it('offers Reload when the tab is on an earlier build', async () => {
    const http = createHttp()
    http.getLibraryProgress = vi.fn(async () => {
      throw new BuildMismatchError()
    })
    mount('/activity', { http })
    const region = await card()
    expect(await within(region).findByText('Library progress was not loaded.')).toBeVisible()
    expect(within(region).getByRole('button', { name: 'Reload' })).toBeVisible()
    expect(within(region).queryByRole('alert')).toBeNull()
  })

  it('takes focus once when Activity is opened at its hash', async () => {
    mount('/activity#activity-library-progress', {})
    const region = await card()
    await waitFor(() => expect(region).toHaveTextContent('0 of 1 work settled'))
    await waitFor(() => expect(document.activeElement).toBe(region))
    expect(region.id).toBe('activity-library-progress')
  })

  it('leaves focus alone when Activity is opened without its hash', async () => {
    mount('/activity', {})
    const region = await card()
    await waitFor(() => expect(region).toHaveTextContent('0 of 1 work settled'))
    expect(document.activeElement).not.toBe(region)
  })

  it('reads again when Library is invalidated, under Library’s own key', async () => {
    const { http, queryClient } = mount('/activity', {})
    await card()
    await waitFor(() => expect(http.getLibraryProgress).toHaveBeenCalledOnce())
    expect(queryClient.getQueryCache().find({ queryKey: ['library', 'progress'] })).toBeDefined()
    await queryClient.invalidateQueries({ queryKey: ['library'] })
    await waitFor(() => expect(http.getLibraryProgress).toHaveBeenCalledTimes(2))
  })
})
