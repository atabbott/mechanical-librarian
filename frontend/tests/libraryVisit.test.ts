import { act, render } from '@testing-library/react'
import { createElement } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { LibraryListing, LibraryView, ReviewHttp } from '../src/api/http'
import {
  type ShowLibraryListing,
  VISIT_HEARTBEAT_MS,
  useLibraryListing,
  useLibraryVisit,
} from '../src/libraryVisit'
import { createHttp, library } from './fixtures'

// The browser's side of the visit (#1351): when a tab says Library is in use,
// and that only a rendered New lens says how far it listed. The visit sits at
// the Library route and Works beneath it, as in the router.
const Works = ({ show, data }: { show: ShowLibraryListing; data: LibraryView | undefined }) => {
  useLibraryListing(show, data)
  return null
}
const Library = ({
  http,
  data,
}: {
  http: ReviewHttp
  data: LibraryView | undefined | 'covers'
}) => {
  const show = useLibraryVisit(http)
  return data === 'covers' ? null : createElement(Works, { show, data })
}

describe('useLibraryVisit', () => {
  const listed = (packages: number): LibraryListing => ({ packages, reports: 9, works: 2 })
  const fresh = (packages: number): LibraryView => ({
    ...library,
    lens: 'new',
    listing: listed(packages),
  })
  const all: LibraryView = { ...library, listing: listed(40) }
  let visible = true
  const mount = (
    first: LibraryView | undefined | 'covers',
    record = vi.fn(async () => undefined),
  ) => {
    const http: ReviewHttp = { ...createHttp(), recordLibraryVisit: record }
    const view = render(createElement(Library, { http, data: first }))
    return {
      record,
      show: (data: LibraryView | undefined | 'covers') =>
        view.rerender(createElement(Library, { http, data })),
    }
  }
  const settle = () => act(async () => vi.advanceTimersByTimeAsync(0))
  const press = () => act(() => void window.dispatchEvent(new Event('pointerdown')))
  const type = () => act(() => void window.dispatchEvent(new Event('keydown')))

  beforeEach(() => {
    vi.useFakeTimers()
    visible = true
    vi.spyOn(document, 'visibilityState', 'get').mockImplementation(() =>
      visible ? 'visible' : 'hidden',
    )
  })
  afterEach(() => {
    vi.useRealTimers()
    vi.restoreAllMocks()
  })

  it('posts once when Library first renders, with no listing outside New', async () => {
    const { record, show } = mount(undefined)
    await settle()
    expect(record.mock.calls).toEqual([[null]])
    show(all)
    await settle()
    expect(record).toHaveBeenCalledTimes(1)
    // A later read of any other lens says nothing, whatever its listing.
    show({ ...all, lens: 'flagged', listing: listed(41) })
    await settle()
    expect(record).toHaveBeenCalledTimes(1)
  })

  it('sends how far a rendered New lens listed, and again only when that moves', async () => {
    const { record, show } = mount(all)
    await settle()
    show(fresh(40))
    await settle()
    expect(record.mock.calls).toEqual([[null], [listed(40)]])
    show({ ...fresh(40), works: [] })
    await settle()
    expect(record).toHaveBeenCalledTimes(2)
    show(fresh(41))
    await settle()
    expect(record).toHaveBeenLastCalledWith(listed(41))
    expect(record).toHaveBeenCalledTimes(3)
  })

  it('opens straight into New with its listing in the first post', async () => {
    const { record } = mount(fresh(40))
    await settle()
    expect(record.mock.calls).toEqual([[listed(40)]])
  })

  it('posts on input only once the heartbeat has passed since the last post', async () => {
    const { record, show } = mount(all)
    await settle()
    await vi.advanceTimersByTimeAsync(VISIT_HEARTBEAT_MS - 1)
    press()
    type()
    await settle()
    expect(record).toHaveBeenCalledTimes(1)
    // Time alone posts nothing: an open tab nobody touches is not a visit.
    await vi.advanceTimersByTimeAsync(VISIT_HEARTBEAT_MS)
    expect(record).toHaveBeenCalledTimes(1)
    type()
    await settle()
    expect(record.mock.calls).toEqual([[null], [null]])
    // From New the heartbeat carries the rendered listing.
    show(fresh(40))
    await settle()
    await vi.advanceTimersByTimeAsync(VISIT_HEARTBEAT_MS)
    press()
    await settle()
    expect(record.mock.calls.slice(2)).toEqual([[listed(40)], [listed(40)]])
  })

  it('keeps the visit going from Covers, where no listing is sent', async () => {
    const { record, show } = mount(fresh(40))
    await settle()
    show('covers')
    await vi.advanceTimersByTimeAsync(VISIT_HEARTBEAT_MS)
    press()
    await settle()
    expect(record.mock.calls).toEqual([[listed(40)], [null]])
    // Back on New with the same listing: nothing further to say.
    show(fresh(40))
    await settle()
    expect(record).toHaveBeenCalledTimes(2)
  })

  it('posts nothing while the page is hidden, and catches up when it shows', async () => {
    visible = false
    const { record } = mount(fresh(40))
    press()
    await settle()
    expect(record).not.toHaveBeenCalled()
    visible = true
    act(() => void document.dispatchEvent(new Event('visibilitychange')))
    await settle()
    expect(record.mock.calls).toEqual([[listed(40)]])
  })

  it('keeps one post in flight, then sends a listing that moved meanwhile', async () => {
    const answers: (() => void)[] = []
    const record = vi.fn(
      () => new Promise<undefined>((resolve) => answers.push(() => resolve(undefined))),
    )
    const { show } = mount(fresh(40), record)
    await settle()
    show(fresh(41))
    show(fresh(42))
    await vi.advanceTimersByTimeAsync(VISIT_HEARTBEAT_MS)
    press()
    await settle()
    expect(record).toHaveBeenCalledTimes(1)
    answers[0]?.()
    await settle()
    expect(record.mock.calls).toEqual([[listed(40)], [listed(42)]])
  })

  it('leaves a failed post silent until the next heartbeat', async () => {
    const record = vi.fn(async () => {
      throw new Error('unavailable')
    })
    const { show } = mount(fresh(40), record)
    await settle()
    show(fresh(40))
    press()
    await settle()
    expect(record).toHaveBeenCalledTimes(1)
    await vi.advanceTimersByTimeAsync(VISIT_HEARTBEAT_MS)
    press()
    await settle()
    expect(record).toHaveBeenCalledTimes(2)
  })
})
