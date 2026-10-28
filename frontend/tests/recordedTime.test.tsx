import { act, render } from '@testing-library/react'
import { describe, expect, it, onTestFinished, vi } from 'vitest'

import { RecordedTime } from '../src/components/RecordedTime'

describe('RecordedTime (#1286 C3)', () => {
  it('re-reads a recent time as it ages, so what it shows stays true', async () => {
    const start = new Date(2026, 9, 28, 14, 5, 0)
    vi.useFakeTimers({ toFake: ['Date', 'setTimeout', 'clearTimeout'], now: start })
    onTestFinished(() => {
      vi.useRealTimers()
    })
    const { container } = render(<RecordedTime seconds={start.getTime() / 1000 - 30} />)
    expect(container).toHaveTextContent('now')
    await act(() => vi.advanceTimersByTimeAsync(30_000))
    expect(container).toHaveTextContent('1 minute ago')
    await act(() => vi.advanceTimersByTimeAsync(10 * 60_000))
    expect(container).toHaveTextContent('11 minutes ago')
    await act(() => vi.advanceTimersByTimeAsync(60 * 60_000))
    expect(container).toHaveTextContent(/^14:04$/u)
  })
})
