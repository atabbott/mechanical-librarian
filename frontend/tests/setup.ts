import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach, vi } from 'vitest'

afterEach(cleanup)
vi.stubGlobal('scrollTo', vi.fn())
vi.stubGlobal('scrollBy', vi.fn())
Element.prototype.scrollIntoView = vi.fn()

// jsdom 31 drives animation frames and focus fixup from one shared interval,
// created on demand. If it is first created while a test has fake timers
// installed, it is a fake interval that never ticks after useRealTimers(), so
// every later requestAnimationFrame in that file never runs. Keeping one real
// frame callback pending holds a real interval open across fake-timer windows.
const realRequestAnimationFrame = window.requestAnimationFrame.bind(window)
const keepFramesRunning = () => {
  realRequestAnimationFrame(keepFramesRunning)
}
keepFramesRunning()
