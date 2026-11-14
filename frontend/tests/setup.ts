import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach, vi } from 'vitest'

afterEach(cleanup)
vi.stubGlobal('scrollTo', vi.fn())
vi.stubGlobal('scrollBy', vi.fn())
Element.prototype.scrollIntoView = vi.fn()

// jsdom 31 drives animation frames and focus fixup from one shared interval,
// created on demand. If a test first creates it while fake timers are
// installed, it is a fake interval that never ticks after useRealTimers(), so
// every later requestAnimationFrame in that file never runs. While fake timers
// are installed, one real frame callback stays pending so the interval jsdom
// uses is a real one.
const realRequestAnimationFrame = window.requestAnimationFrame.bind(window)
const realCancelAnimationFrame = window.cancelAnimationFrame.bind(window)
let realFrame: number | null = null
const holdRealFrames = () => {
  realFrame = realRequestAnimationFrame(holdRealFrames)
}
const installFakeTimers = vi.useFakeTimers.bind(vi)
const restoreRealTimers = vi.useRealTimers.bind(vi)
vi.useFakeTimers = (...args: Parameters<typeof vi.useFakeTimers>) => {
  if (realFrame === null) holdRealFrames()
  return installFakeTimers(...args)
}
vi.useRealTimers = () => {
  const utils = restoreRealTimers()
  if (realFrame !== null) realCancelAnimationFrame(realFrame)
  realFrame = null
  return utils
}
