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
