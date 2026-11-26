import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { beforeAll, describe, expect, it, vi } from 'vitest'

import { ApiError, CsrfRenewedError } from '../src/api/http'
import type { ReviewHttp, UpgradeNotice as Notice, UpgradeNoticeView } from '../src/api/http'
import { SHOW_ALL_KEPT_NOTICE, UpgradeNotice } from '../src/components/UpgradeNotice'
import { createHttp, loadRouteModules, renderRoute } from './fixtures'

beforeAll(loadRouteModules)

const view = (notice: Notice | null): UpgradeNoticeView => ({
  schema_version: 'upgrade-notice-v1',
  notice,
})
const notice = (kept: number, asked: number, preparing = false): Notice => ({
  regeneration_id: 'regeneration_1',
  preparing,
  kept,
  asked_again: asked,
})
const httpWith = (current: Notice | null) => {
  const http = createHttp()
  http.getUpgradeNotice = vi.fn(async () => view(current))
  return http
}
const renderNotice = (http: ReviewHttp, showAllKept?: boolean) => {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return {
    queryClient,
    ...render(
      <QueryClientProvider client={queryClient}>
        <UpgradeNotice http={http} {...(showAllKept === undefined ? {} : { showAllKept })} />
      </QueryClientProvider>,
    ),
  }
}
// The notice's sentences, or null once the read settled with nothing to say.
const noticeText = async (current: Notice | null, showAllKept?: boolean) => {
  const { container, queryClient } = renderNotice(httpWith(current), showAllKept)
  await waitFor(() => expect(queryClient.getQueryState(['upgrade-notice'])?.status).toBe('success'))
  // React Query notifies its observers on the next task, so let that run.
  await act(async () => new Promise((resolve) => setTimeout(resolve, 0)))
  return container.querySelector('.cm-upgrade-notice p')?.textContent ?? null
}

describe('upgrade notice copy', () => {
  it.each([
    [
      notice(12, 3),
      '3 of your answers are asked again after the upgrade. Each one says why. The other 12 were kept.',
    ],
    [notice(12, 1), '1 of your answers is asked again after the upgrade. The other 12 were kept.'],
    [
      notice(1, 3),
      '3 of your answers are asked again after the upgrade. Each one says why. The other one was kept.',
    ],
    [notice(1, 1), '1 of your answers is asked again after the upgrade. The other one was kept.'],
    [notice(0, 1), 'Your 1 answer is asked again after the upgrade.'],
    [notice(0, 3), 'All 3 of your answers are asked again after the upgrade. Each one says why.'],
    [notice(1, 0), 'After the upgrade, your 1 answer was kept.'],
    [notice(12, 0), 'After the upgrade, all 12 of your answers were kept.'],
    [notice(1_234, 0), 'After the upgrade, all 1,234 of your answers were kept.'],
    [notice(0, 0), null],
    [notice(0, 0, true), 'Preparing your library after the upgrade.'],
    [
      notice(1, 1, true),
      'Preparing your library after the upgrade. So far 1 of your answers was kept and 1 was asked again.',
    ],
    [
      notice(12, 3, true),
      'Preparing your library after the upgrade. So far 12 of your answers were kept and 3 were asked again.',
    ],
    [
      notice(0, 2, true),
      'Preparing your library after the upgrade. So far 2 of your answers were asked again.',
    ],
    [
      notice(1, 0, true),
      'Preparing your library after the upgrade. So far 1 of your answers was kept.',
    ],
    [
      notice(0, 1, true),
      'Preparing your library after the upgrade. So far 1 of your answers was asked again.',
    ],
    [null, null],
  ])('says %o as %s', async (current, expected) => {
    expect(await noticeText(current)).toBe(expected)
  })

  it('says an all-kept regeneration only while the switch is on', async () => {
    expect(SHOW_ALL_KEPT_NOTICE).toBe(true)
    expect(await noticeText(notice(12, 0), false)).toBeNull()
    expect(await noticeText(notice(12, 3), false)).toBe(
      '3 of your answers are asked again after the upgrade. Each one says why. The other 12 were kept.',
    )
    expect(await noticeText(notice(2, 0, true), false)).toBe(
      'Preparing your library after the upgrade. So far 2 of your answers were kept.',
    )
  })
})

describe('upgrade notice dismissal', () => {
  it('sends the regeneration and hides the notice', async () => {
    const user = userEvent.setup()
    const http = httpWith(notice(12, 3))
    renderNotice(http)
    await user.click(await screen.findByRole('button', { name: 'Dismiss' }))
    expect(http.dismissUpgradeNotice).toHaveBeenCalledWith('regeneration_1')
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Dismiss' })).toBeNull())
  })

  it('is no live region, so the counts are not announced again on each read', async () => {
    const http = httpWith(notice(12, 3, true))
    const { container } = renderNotice(http)
    await screen.findByRole('button', { name: 'Dismiss' })
    const section = container.querySelector('.cm-upgrade-notice')
    expect(section).not.toHaveAttribute('aria-live')
    expect(section?.closest('[aria-live]')).toBeNull()
    expect(within(section as HTMLElement).queryByRole('status')).toBeNull()
    expect(within(section as HTMLElement).queryByRole('alert')).toBeNull()
  })

  it('reads a stale notice again and says nothing more', async () => {
    const user = userEvent.setup()
    const http = createHttp()
    http.getUpgradeNotice = vi
      .fn<ReviewHttp['getUpgradeNotice']>()
      .mockResolvedValueOnce(view(notice(12, 3)))
      .mockResolvedValue(view({ ...notice(5, 1), regeneration_id: 'regeneration_2' }))
    http.dismissUpgradeNotice = vi.fn(async () => {
      throw new ApiError(409, 'stale')
    })
    renderNotice(http)
    await user.click(await screen.findByRole('button', { name: 'Dismiss' }))
    expect(
      await screen.findByText(
        '1 of your answers is asked again after the upgrade. The other 5 were kept.',
      ),
    ).toBeVisible()
    expect(http.getUpgradeNotice).toHaveBeenCalledTimes(2)
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it.each([
    [new ApiError(422, 'request'), 'Nothing was changed. Try again.'],
    [new CsrfRenewedError(), 'Your session was refreshed. Press again.'],
    [
      new ApiError(503, 'persistence'),
      'No answer came back. Check whether the change took effect before pressing again.',
    ],
  ])('keeps the notice and says why after %o', async (error, copy) => {
    const user = userEvent.setup()
    const http = httpWith(notice(12, 3))
    http.dismissUpgradeNotice = vi.fn(async () => {
      throw error
    })
    renderNotice(http)
    await user.click(await screen.findByRole('button', { name: 'Dismiss' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(copy)
    expect(screen.getByRole('button', { name: 'Dismiss' })).toBeVisible()
  })
})

describe('upgrade notice placement', () => {
  const sentence =
    '3 of your answers are asked again after the upgrade. Each one says why. The other 12 were kept.'

  it('reads directly under the Activity heading', async () => {
    renderRoute('/activity', httpWith(notice(12, 3)))
    const text = await screen.findByText(sentence)
    const section = text.closest('section')
    expect(section).toHaveClass('cm-upgrade-notice')
    const heading = section?.previousElementSibling
    expect(heading?.matches('header.cm-page-heading')).toBe(true)
    expect(within(heading as HTMLElement).getByRole('heading', { level: 1 })).toHaveTextContent(
      'Activity',
    )
  })

  it('reads in the Attention rail under its header, and nowhere else on Library', async () => {
    const http = httpWith(notice(12, 3))
    const { router } = renderRoute('/library?lens=all', http)
    expect(await screen.findByRole('heading', { level: 1 })).toBeVisible()
    expect(screen.queryByText(sentence)).toBeNull()
    await act(async () =>
      router.navigate({ to: '/library', search: { lens: 'all', attention: 'open' } }),
    )
    const rail = await screen.findByLabelText('Attention queue')
    const text = await within(rail).findByText(sentence)
    expect(text.closest('section')?.previousElementSibling?.tagName).toBe('HEADER')
    expect(screen.getAllByText(sentence)).toHaveLength(1)
  })

  it('a dismissal on Activity hides it in the Attention rail', async () => {
    const user = userEvent.setup()
    let current: Notice | null = notice(12, 3)
    const http = createHttp()
    http.getUpgradeNotice = vi.fn(async () => view(current))
    // The server says nothing more once dismissed; the cache is cleared first.
    http.dismissUpgradeNotice = vi.fn(async () => {
      current = null
    })
    const { router, queryClient } = renderRoute('/activity', http)
    await screen.findByText(sentence)
    await user.click(screen.getByRole('button', { name: 'Dismiss' }))
    await waitFor(() => expect(screen.queryByText(sentence)).toBeNull())
    expect(queryClient.getQueryData(['upgrade-notice'])).toEqual(view(null))
    await act(async () =>
      router.navigate({ to: '/library', search: { lens: 'all', attention: 'open' } }),
    )
    const rail = await screen.findByLabelText('Attention queue')
    await act(async () => Promise.resolve())
    expect(within(rail).queryByText(sentence)).toBeNull()
    expect(within(rail).queryByRole('button', { name: 'Dismiss' })).toBeNull()
    expect(http.dismissUpgradeNotice).toHaveBeenCalledTimes(1)
  })

  it('a dismissal on Activity moves focus to the Activity heading', async () => {
    const user = userEvent.setup()
    renderRoute('/activity', httpWith(notice(12, 3)))
    await screen.findByText(sentence)
    await user.click(screen.getByRole('button', { name: 'Dismiss' }))
    await waitFor(() => expect(screen.queryByText(sentence)).toBeNull())
    const heading = screen.getByRole('heading', { level: 1, name: 'Activity' })
    expect(document.activeElement).toBe(heading.closest('header'))
    expect(document.activeElement).toHaveAttribute('data-route-focus')
  })

  it('a dismissal in the Attention rail moves focus to the Attention heading', async () => {
    const user = userEvent.setup()
    renderRoute('/library?lens=all&attention=open', httpWith(notice(12, 3)))
    const rail = await screen.findByLabelText('Attention queue')
    await within(rail).findByText(sentence)
    await user.click(within(rail).getByRole('button', { name: 'Dismiss' }))
    await waitFor(() => expect(within(rail).queryByText(sentence)).toBeNull())
    expect(document.activeElement).toBe(within(rail).getByRole('heading', { name: 'Attention' }))
  })

  it('a dismissal leaves focus the owner moved elsewhere while it ran', async () => {
    const user = userEvent.setup()
    const http = httpWith(notice(12, 3))
    let answer: (() => void) | undefined
    http.dismissUpgradeNotice = vi.fn(
      () => new Promise<void>((resolve) => (answer = () => resolve())),
    )
    renderRoute('/activity', http)
    await screen.findByText(sentence)
    await user.click(screen.getByRole('button', { name: 'Dismiss' }))
    const [elsewhere] = screen.getAllByRole('link')
    elsewhere?.focus()
    await act(async () => answer?.())
    await waitFor(() => expect(screen.queryByText(sentence)).toBeNull())
    expect(document.activeElement).toBe(elsewhere)
  })

  it('a dismissal in the Attention rail hides it on Activity', async () => {
    const user = userEvent.setup()
    // The read keeps answering the notice, so only the shared query can hide it.
    const http = httpWith(notice(12, 3))
    const { router, queryClient } = renderRoute('/library?lens=all&attention=open', http)
    const rail = await screen.findByLabelText('Attention queue')
    await within(rail).findByText(sentence)
    await user.click(within(rail).getByRole('button', { name: 'Dismiss' }))
    await waitFor(() => expect(within(rail).queryByText(sentence)).toBeNull())
    queryClient.setDefaultOptions({ queries: { retry: false, staleTime: Infinity } })
    await act(async () =>
      router.navigate({
        to: '/activity',
        search: { eventType: 'all', rangeDays: 30, page: 1, pageSize: 25 },
      }),
    )
    expect(await screen.findByRole('heading', { level: 1, name: 'Activity' })).toBeVisible()
    expect(screen.queryByText(sentence)).toBeNull()
    expect(http.dismissUpgradeNotice).toHaveBeenCalledWith('regeneration_1')
  })
})
