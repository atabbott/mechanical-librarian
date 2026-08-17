import { render } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useRouteReadyFocus } from '../src/focus'

const Route = ({ identity }: { identity: string }) => {
  useRouteReadyFocus(identity)
  return (
    <main id="main-content">
      <section data-route-focus tabIndex={-1}>
        <button type="button">Choose proposed value</button>
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
