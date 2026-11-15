import { act, fireEvent, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { ReviewHttp, WritePlanFile, WritePlanView } from '../src/api/http'
import { BuildMismatchError } from '../src/api/http'
import { WritePlanField } from '../src/components/WritePlanGroups'
import { ActivityWritePlan, SAVED_URL_LIFETIME_MS } from '../src/routes/ActivityWritePlan'

const context: { http: Partial<ReviewHttp> } = vi.hoisted(() => ({ http: {} }))
vi.mock('../src/router', () => ({ useReviewContext: () => context }))

const row = (
  work: string,
  overrides: Partial<WritePlanView['groups'][number]['rows'][number]> = {},
) => ({
  work,
  authors: 'Mick Herron',
  current: 'Old title',
  new: 'New title',
  reason: 'You chose this value',
  sources: ['Hardcover', 'Open Library'],
  ...overrides,
})

const plan = (total: number, groups: WritePlanView['groups']): WritePlanView => ({ total, groups })

describe('WritePlanField: one library and field of the plan (#1336)', () => {
  const small = plan(4, [
    {
      library: 'books',
      destination: 'title',
      kind: 'human',
      count: 2,
      rows: [row('Real Tigers'), row('Slow Horses', { sources: [] })],
    },
    {
      library: 'books',
      destination: 'title',
      kind: 'automatic',
      count: 1,
      rows: [
        row('Dead Lions', {
          current: 'Not set',
          reason: 'Title and subtitle: Separate title and subtitle',
          sources: ['Hardcover'],
        }),
      ],
    },
    {
      library: 'books',
      destination: 'cover',
      kind: 'automatic',
      count: 1,
      rows: [
        row('Spook Street', {
          current: 'Current cover',
          new: 'Selected cover',
          reason: 'Cover: Fill from confirmed covers',
          sources: ['Audible'],
        }),
      ],
    },
  ])

  it('shows every change of a small plan, open, owner choices first', () => {
    const { container } = render(
      <WritePlanField plan={small} library="books" destination="title" />,
    )

    const groups = container.querySelectorAll('details')
    expect(groups).toHaveLength(2)
    expect([...groups].map((group) => group.open)).toEqual([true, true])
    const [mine, automatic] = [...groups] as [HTMLDetailsElement, HTMLDetailsElement]
    expect(within(mine).getByText('Chosen by you')).toBeVisible()
    expect(within(mine).getByText('2 changes')).toBeVisible()
    expect(within(automatic).getByText('Chosen automatically')).toBeVisible()
    expect(within(automatic).getByText('1 change')).toBeVisible()
    // A row: the title in bold, its authors, current beside new, and why.
    const [first, second] = within(mine).getAllByRole('listitem') as [HTMLElement, HTMLElement]
    expect(within(first).getByText('Real Tigers').tagName).toBe('STRONG')
    expect(within(first).getByText('Mick Herron')).toBeVisible()
    expect(within(first).getByText('Current').tagName).toBe('DT')
    expect(within(first).getByText('Old title').tagName).toBe('DD')
    expect(within(first).getByText('New').tagName).toBe('DT')
    expect(within(first).getByText('New title').tagName).toBe('DD')
    expect(within(first).getByText('You chose this value · Hardcover, Open Library')).toBeVisible()
    // No sources: the reason stands alone, with no separator and no labels.
    expect(within(second).getByText('You chose this value')).toBeVisible()
    expect(container).not.toHaveTextContent(/Reason|Sources|more in the file/)
    expect(within(automatic).getByText('Not set')).toBeVisible()
  })

  it('names a cover in words', () => {
    render(<WritePlanField plan={small} library="books" destination="cover" />)

    expect(screen.getByText('Current cover')).toBeVisible()
    expect(screen.getByText('Selected cover')).toBeVisible()
    expect(screen.getByText('Cover: Fill from confirmed covers · Audible')).toBeVisible()
  })

  it('renders nothing for a field or library the plan does not change', () => {
    const { container, rerender } = render(
      <WritePlanField plan={small} library="books" destination="authors" />,
    )
    expect(container).toBeEmptyDOMElement()
    rerender(<WritePlanField plan={small} library="audio" destination="title" />)
    expect(container).toBeEmptyDOMElement()
    rerender(<WritePlanField plan={undefined} library="books" destination="title" />)
    expect(container).toBeEmptyDOMElement()
  })

  it('never sends a row it could not read to the file when every row is shown', () => {
    // Three changes, one unreadable: the inline plan lists the other two.
    const unreadable = plan(3, [
      {
        library: 'books',
        destination: 'title',
        kind: 'human',
        count: 3,
        rows: [row('Real Tigers'), row('Slow Horses')],
      },
    ])
    const { container } = render(
      <WritePlanField plan={unreadable} library="books" destination="title" />,
    )

    expect(within(container).getByText('3 changes')).toBeVisible()
    expect(within(container).getAllByRole('listitem')).toHaveLength(2)
    expect(container).not.toHaveTextContent(/more in the file/)
  })

  it('starts a large plan closed, with examples and the rest in the file', async () => {
    const user = userEvent.setup()
    const large = plan(1_240, [
      {
        library: 'books',
        destination: 'title',
        kind: 'human',
        count: 1_234,
        rows: [row('Real Tigers'), row('Slow Horses')],
      },
      { library: 'books', destination: 'title', kind: 'automatic', count: 6, rows: [] },
    ])
    const { container } = render(
      <WritePlanField plan={large} library="books" destination="title" />,
    )

    const [mine, automatic] = [...container.querySelectorAll('details')] as [
      HTMLDetailsElement,
      HTMLDetailsElement,
    ]
    expect(mine.open).toBe(false)
    expect(within(mine).getByText('1,234 changes')).toBeInTheDocument()
    expect(within(mine).getByText('1,232 more in the file')).toBeInTheDocument()
    // A count-only group lists no rows and says where they are.
    expect(within(automatic).queryByRole('list')).toBeNull()
    expect(within(automatic).getByText('6 more in the file')).toBeInTheDocument()
    await user.click(within(mine).getByText('Chosen by you'))
    expect(mine.open).toBe(true)
  })
})

describe('ActivityWritePlan: the lead and the two files (#1336)', () => {
  const download = vi.fn<ReviewHttp['downloadActivityWritePlan']>()
  const saved: { href: string; download: string }[] = []

  beforeEach(() => {
    download.mockReset()
    saved.length = 0
    context.http = { downloadActivityWritePlan: download }
    vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:plan')
    vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => undefined)
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (
      this: HTMLAnchorElement,
    ) {
      saved.push({ href: this.href, download: this.download })
    })
  })
  afterEach(() => {
    vi.restoreAllMocks()
    vi.useRealTimers()
  })

  const file: WritePlanFile = {
    blob: new Blob(['"Library"\r\n'], { type: 'text/csv;charset=utf-8' }),
    filename: 'mechanical-librarian-write-plan.csv',
  }

  it('says how many changes are prepared and that permission covers later ones', () => {
    const { rerender } = render(<ActivityWritePlan plan={plan(1, [])} />)
    expect(
      screen.getByText(
        '1 change is prepared so far. Permission also covers changes prepared after you allow writes.',
      ),
    ).toBeVisible()
    rerender(<ActivityWritePlan plan={plan(1_234, [])} />)
    expect(
      screen.getByText(
        '1,234 changes are prepared so far. Permission also covers changes prepared after you allow writes.',
      ),
    ).toBeVisible()
    expect(screen.queryByText(/The file lists the first/)).toBeNull()
    rerender(<ActivityWritePlan plan={plan(20_001, [])} />)
    expect(screen.getByText('The file lists the first 20,000 changes.')).toBeVisible()
  })

  it('renders nothing when nothing is prepared', () => {
    const { container } = render(<ActivityWritePlan plan={plan(0, [])} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('saves the file it fetched, staying focusable and busy meanwhile', async () => {
    const user = userEvent.setup()
    let finish: (value: WritePlanFile) => void = () => undefined
    download.mockImplementation(
      () =>
        new Promise((resolve) => {
          finish = resolve
        }),
    )
    render(<ActivityWritePlan plan={plan(3, [])} />)

    const csv = screen.getByRole('button', { name: 'Download CSV' })
    await user.click(csv)

    expect(download).toHaveBeenCalledOnce()
    expect(download.mock.calls[0]?.[0]).toBe('csv')
    expect(csv).toHaveFocus()
    expect(csv).toHaveAttribute('aria-disabled', 'true')
    expect(screen.getByRole('button', { name: 'Download JSON' })).toHaveAttribute(
      'aria-disabled',
      'true',
    )
    expect(screen.getByRole('status')).toHaveTextContent('Preparing the file…')
    // A press while busy starts nothing.
    await user.click(csv)
    expect(download).toHaveBeenCalledOnce()

    await act(async () => finish(file))

    expect(saved).toEqual([{ href: 'blob:plan', download: 'mechanical-librarian-write-plan.csv' }])
    // Not let go at once: some browsers would drop the download.
    expect(URL.revokeObjectURL).not.toHaveBeenCalled()
    expect(csv).not.toHaveAttribute('aria-disabled')
    expect(screen.getByRole('status')).toHaveTextContent('')
  })

  it('lets a saved file go a minute later, or when the panel ends', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    download.mockResolvedValue(file)
    const { unmount } = render(<ActivityWritePlan plan={plan(3, [])} />)

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Download CSV' }))
    })
    expect(saved).toHaveLength(1)
    act(() => {
      vi.advanceTimersByTime(SAVED_URL_LIFETIME_MS - 1)
    })
    expect(URL.revokeObjectURL).not.toHaveBeenCalled()
    act(() => {
      vi.advanceTimersByTime(1)
    })
    expect(URL.revokeObjectURL).toHaveBeenCalledExactlyOnceWith('blob:plan')

    vi.mocked(URL.createObjectURL).mockReturnValue('blob:second')
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Download JSON' }))
    })
    expect(saved).toHaveLength(2)
    unmount()
    expect(URL.revokeObjectURL).toHaveBeenLastCalledWith('blob:second')
    act(() => {
      vi.advanceTimersByTime(SAVED_URL_LIFETIME_MS)
    })
    expect(URL.revokeObjectURL).toHaveBeenCalledTimes(2)
  })

  it('counts received changes at most once a second, up to the file limit', async () => {
    vi.useFakeTimers({ now: 10_000, toFake: ['Date'] })
    const user = userEvent.setup()
    let report: (rows: number) => void = () => undefined
    let finish: (value: WritePlanFile) => void = () => undefined
    download.mockImplementation(
      (_format, _signal, onRows) =>
        new Promise((resolve) => {
          report = onRows ?? report
          finish = resolve
        }),
    )
    render(<ActivityWritePlan plan={plan(25_000, [])} />)

    await user.click(screen.getByRole('button', { name: 'Download JSON' }))
    const status = screen.getByRole('status')
    act(() => report(0))
    expect(status).toHaveTextContent(/^Preparing the file…$/)
    act(() => report(100))
    expect(status).toHaveTextContent('Preparing the file… 100 of 20,000 changes')
    act(() => report(250))
    expect(status).toHaveTextContent('Preparing the file… 100 of 20,000 changes')
    vi.setSystemTime(11_000)
    act(() => report(4_321))
    expect(status).toHaveTextContent('Preparing the file… 4,321 of 20,000 changes')

    await act(async () => finish(file))
    expect(status).toHaveTextContent('')
  })

  it('says the file was not downloaded when it fails, and tries again', async () => {
    const user = userEvent.setup()
    download.mockRejectedValueOnce(new Error('network')).mockResolvedValueOnce(file)
    render(<ActivityWritePlan plan={plan(3, [])} />)

    await user.click(screen.getByRole('button', { name: 'Download CSV' }))
    expect(await screen.findByText('The file was not downloaded. Try again.')).toBeVisible()
    expect(saved).toEqual([])

    await user.click(screen.getByRole('button', { name: 'Download CSV' }))
    expect(saved).toHaveLength(1)
    expect(screen.getByRole('status')).toHaveTextContent('')
  })

  it('offers only a reload once the tab is on an earlier build', async () => {
    const user = userEvent.setup()
    download.mockRejectedValueOnce(new BuildMismatchError())
    render(<ActivityWritePlan plan={plan(3, [])} />)

    await user.click(screen.getByRole('button', { name: 'Download JSON' }))

    expect(await screen.findByText('The file was not downloaded.')).toBeVisible()
    expect(screen.getByRole('button', { name: 'Reload' })).toBeVisible()
    expect(screen.queryByRole('button', { name: /Download/ })).toBeNull()
  })

  it('abandons a download in flight when the panel closes', async () => {
    const user = userEvent.setup()
    let signal: AbortSignal | undefined
    let finish: (value: WritePlanFile) => void = () => undefined
    download.mockImplementation(
      (_format, abort) =>
        new Promise((resolve) => {
          signal = abort
          finish = resolve
        }),
    )
    const { unmount } = render(<ActivityWritePlan plan={plan(3, [])} />)

    await user.click(screen.getByRole('button', { name: 'Download CSV' }))
    unmount()
    await act(async () => finish(file))

    expect(signal?.aborted).toBe(true)
    expect(saved).toEqual([])
  })
})
