import { render } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useRouteReadyFocus } from '../src/focus'

const Route = ({
  identity,
  preserveFocusedSearch = false,
}: {
  identity: string
  preserveFocusedSearch?: boolean
}) => {
  useRouteReadyFocus(identity, preserveFocusedSearch)
  return (
    <main id="main-content">
      <section data-route-focus tabIndex={-1}>
        <button type="button">Choose proposed value</button>
        <input type="search" aria-label="Search works" />
      </section>
    </main>
  )
}

let frames: FrameRequestCallback[] = []

beforeEach(() => {
  frames = []
  vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => {
    frames.push(callback)
    return frames.length
  })
  vi.stubGlobal('cancelAnimationFrame', () => undefined)
})

afterEach(() => {
  vi.unstubAllGlobals()
})

const runFrames = () => {
  const pending = frames
  frames = []
  for (const frame of pending) frame(0)
}

describe('useRouteReadyFocus', () => {
  it('preserves an edited Library search caret across a debounced URL update', () => {
    const view = render(<Route identity="library:q=" preserveFocusedSearch />)
    runFrames()
    const search = document.querySelector<HTMLInputElement>('input')
    if (!search) throw new Error('search fixture required')
    search.value = 'synthetic'
    search.focus()
    search.setSelectionRange(4, 4)
    view.rerender(<Route identity="library:q=synthetic" preserveFocusedSearch />)
    runFrames()
    expect(search).toHaveFocus()
    expect(search.selectionStart).toBe(4)
    expect(search.selectionEnd).toBe(4)
    document.querySelector('button')?.focus()
    view.rerender(<Route identity="library:q=synthetic:page=2" preserveFocusedSearch />)
    runFrames()
    expect(document.querySelector('[data-route-focus]')).toHaveFocus()
  })

  it('moves focus to the route target once the route is ready', () => {
    render(<Route identity="review:title" />)
    runFrames()
    expect(document.querySelector('[data-route-focus]')).toHaveFocus()
    expect(document.getElementById('main-content')).toHaveAttribute(
      'data-route-focus-identity',
      '/:review:title',
    )
  })

  it('leaves focus where an interaction placed it before the frame ran', () => {
    render(<Route identity="review:title" />)
    const control = document.querySelector('button')
    control?.focus()
    runFrames()
    // The deferred move must not take focus back: the next key would otherwise
    // reach the container instead of the control, losing the activation (#760).
    expect(control).toHaveFocus()
    expect(document.getElementById('main-content')).not.toHaveAttribute('data-route-focus-identity')
  })
})

it('records readiness when shell restoration already focused the same target', () => {
  render(<Route identity="activity" />)
  const target = document.querySelector<HTMLElement>('[data-route-focus]')
  if (!target) throw new Error('route target fixture required')
  target.focus()
  const focus = vi.spyOn(target, 'focus')
  runFrames()
  expect(target).toHaveFocus()
  expect(focus).not.toHaveBeenCalled()
  expect(document.getElementById('main-content')).toHaveAttribute(
    'data-route-focus-identity',
    '/:activity',
  )
})

it.each([false, true])(
  'renews readiness after an intervening identity was cancelled, owner moved=%s',
  (ownerMoved) => {
    const queued = new Map<number, FrameRequestCallback>()
    let nextFrame = 0
    vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => {
      queued.set(++nextFrame, callback)
      return nextFrame
    })
    vi.stubGlobal('cancelAnimationFrame', (id: number) => queued.delete(id))
    const flush = () => {
      const batch = [...queued.values()]
      queued.clear()
      for (const callback of batch) callback(0)
    }
    const view = render(<Route identity="work" />)
    flush()
    const target = document.querySelector<HTMLElement>('[data-route-focus]')
    const main = document.getElementById('main-content')
    if (!target || !main) throw new Error('Mounted route required')
    expect(target).toHaveFocus()
    expect(main.dataset.routeFocusIdentity).toBe('/:work')
    target.blur()
    view.rerender(<Route identity="preparation" />)
    view.rerender(<Route identity="work" />)
    expect(queued.size).toBe(1)
    const control = document.querySelector<HTMLButtonElement>('button')
    if (!control) throw new Error('Owner control required')
    if (ownerMoved) control.focus()
    flush()
    if (ownerMoved) {
      expect(control).toHaveFocus()
      expect(main).not.toHaveAttribute('data-route-focus-identity')
    } else {
      expect(target).toHaveFocus()
      expect(main.dataset.routeFocusIdentity).toBe('/:work')
    }
  },
)

it('retains same-identity readiness and honors readiness installed by the shell before the frame', () => {
  const view = render(<Route identity="work" />)
  runFrames()
  const main = document.getElementById('main-content')
  const control = document.querySelector<HTMLButtonElement>('button')
  if (!main || !control) throw new Error('Mounted route and control required')
  control.focus()
  view.rerender(<Route identity="work" preserveFocusedSearch />)
  expect(main.dataset.routeFocusIdentity).toBe('/:work')
  runFrames()
  expect(control).toHaveFocus()
  view.rerender(<Route identity="work-next" preserveFocusedSearch />)
  expect(main).not.toHaveAttribute('data-route-focus-identity')
  main.dataset.routeFocusIdentity = '/:work-next'
  runFrames()
  expect(control).toHaveFocus()
  expect(main.dataset.routeFocusIdentity).toBe('/:work-next')
})
