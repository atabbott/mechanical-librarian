import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { describe, expect, it, vi } from 'vitest'

import {
  AmbiguousMutationError,
  ApiError,
  BuildMismatchError,
  CsrfRenewedError,
  SessionExpiredError,
  UncertainMutationError,
} from '../src/api/http'
import type { ReviewHttp } from '../src/api/http'
import { OtherBrowsersSettings } from '../src/components/OtherBrowsersSettings'
import type { OtherBrowsersState } from '../src/components/OtherBrowsersSettings'
import { createHttp } from './fixtures'

// Settings' Other browsers row: one button that ends every other session, one
// request at a time, and a line for each outcome. An outcome the browser
// cannot know is never sent again and has nothing to read again.

const Row = ({ http }: { http: ReviewHttp }) => {
  const state = useState<OtherBrowsersState>({ kind: 'idle' })
  return <OtherBrowsersSettings http={http} state={state} />
}
const setup = (http: ReviewHttp = createHttp()) => {
  const user = userEvent.setup()
  render(<Row http={http} />)
  return { user, http }
}
const button = () => screen.getByRole('button', { name: 'Sign out other browsers' })
const refusing = (error: unknown) => {
  const http = createHttp()
  http.logoutOthers = vi.fn(async () => {
    throw error
  })
  return http
}
const deferred = () => {
  let finish: (error?: Error) => void = () => undefined
  const http = createHttp()
  http.logoutOthers = vi.fn(
    () =>
      new Promise<void>((resolve, reject) => {
        finish = (error?: Error) => (error === undefined ? resolve() : reject(error))
      }),
  )
  return { http, finish: (error?: Error) => finish(error) }
}
const notSignedOut = 'The other browsers were not signed out. Try again.'
const noAnswer = 'No answer came back. Try again.'

describe('Other browsers', () => {
  it('shows the row title, description and one enabled button', () => {
    setup()
    expect(screen.getByText('Other browsers')).toBeVisible()
    expect(screen.getByText('This browser stays signed in.')).toBeVisible()
    expect(screen.getAllByRole('button')).toHaveLength(1)
    expect(button()).toBeEnabled()
    // The row's line is read with the button, not only seen beside it.
    expect(button()).toHaveAccessibleDescription('This browser stays signed in.')
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('says Signing out… and keeps focus on the unavailable button while sending', async () => {
    const { http, finish } = deferred()
    const { user } = setup(http)
    await user.click(button())

    const sending = screen.getByRole('button', { name: 'Signing out…' })
    expect(sending).toHaveAttribute('aria-disabled', 'true')
    // Not `disabled`, which would drop focus to the page while it waits.
    expect(sending).toHaveFocus()
    await user.click(sending)
    await user.keyboard('{Enter}')
    expect(http.logoutOthers).toHaveBeenCalledOnce()
    await act(async () => {
      finish()
      await Promise.resolve()
    })
    expect(button()).toBeEnabled()
  })

  it('sends one request when pressed twice before it answers', async () => {
    const { http, finish } = deferred()
    setup(http)
    // Two presses in one tick, before the disabled state renders.
    act(() => {
      fireEvent.click(button())
      fireEvent.click(button())
    })
    await screen.findByRole('button', { name: 'Signing out…' })
    expect(http.logoutOthers).toHaveBeenCalledOnce()
    await act(async () => {
      finish()
      await Promise.resolve()
    })
    expect(http.logoutOthers).toHaveBeenCalledOnce()
  })

  it('announces the outcome in a status and moves focus to it', async () => {
    const { user, http } = setup()
    await user.click(button())

    const status = await screen.findByRole('status')
    expect(status).toHaveTextContent('Every other browser was signed out.')
    await waitFor(() => expect(status).toHaveFocus())
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(button()).toBeEnabled()
    expect(http.logoutOthers).toHaveBeenCalledOnce()
  })

  it('keeps the done line when the row remounts', async () => {
    const Page = () => {
      const state = useState<OtherBrowsersState>({ kind: 'idle' })
      const [generation, setGeneration] = useState(0)
      return (
        <>
          <button type="button" onClick={() => setGeneration((value) => value + 1)}>
            Remount
          </button>
          <OtherBrowsersSettings key={generation} http={createHttp()} state={state} />
        </>
      )
    }
    const user = userEvent.setup()
    render(<Page />)
    await user.click(button())
    await screen.findByRole('status')
    await user.click(screen.getByRole('button', { name: 'Remount' }))

    expect(screen.getByRole('status')).toHaveTextContent('Every other browser was signed out.')
    expect(button()).toBeEnabled()
  })

  it('asks to press again after a CSRF renewal and does not send again', async () => {
    const http = refusing(new CsrfRenewedError())
    const { user } = setup(http)
    await user.click(button())

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Your session was refreshed. Press again.',
    )
    await waitFor(() => expect(button()).toHaveFocus())
    expect(http.logoutOthers).toHaveBeenCalledOnce()
  })

  it.each([
    ['an invalid request', new ApiError(422, 'invalid')],
    ['assets in recovery', new ApiError(503, 'react_assets_unavailable')],
    ['a stale token', new ApiError(403, 'csrf')],
  ])('says the other browsers were not signed out after %s', async (_, error) => {
    const http = refusing(error)
    const { user } = setup(http)
    await user.click(button())

    expect(await screen.findByRole('alert')).toHaveTextContent(notSignedOut)
    await waitFor(() => expect(button()).toHaveFocus())
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
    expect(http.logoutOthers).toHaveBeenCalledOnce()
  })

  it.each([
    ['a lost answer', new AmbiguousMutationError()],
    [
      'an unreadable answer',
      new UncertainMutationError({
        operation: 'POST /api/v1/session/logout-others',
        responseStatus: 200,
        at: '',
        reason: 'no_decoder',
      }),
    ],
  ])('says No answer came back after %s, with no Reload', async (_, error) => {
    const http = refusing(error)
    const { user } = setup(http)
    await user.click(button())

    expect(await screen.findByRole('alert')).toHaveTextContent(noAnswer)
    expect(screen.queryByText(notSignedOut)).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Reload' })).not.toBeInTheDocument()
    await waitFor(() => expect(button()).toHaveFocus())
    expect(http.getSettings).not.toHaveBeenCalled()
    expect(http.logoutOthers).toHaveBeenCalledOnce()
  })

  it.each([
    ['a store failure', new ApiError(503, 'persistence')],
    ['a server fault', new ApiError(500, 'runtime')],
  ])('reports %s with the unconfirmed line', async (_, error) => {
    // The failure may follow the commit, so it is never reported as refused.
    const http = refusing(error)
    const { user } = setup(http)
    await user.click(button())

    expect(await screen.findByRole('alert')).toHaveTextContent(noAnswer)
    expect(screen.queryByText(notSignedOut)).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Reload' })).not.toBeInTheDocument()
    expect(http.getSettings).not.toHaveBeenCalled()
    expect(http.logoutOthers).toHaveBeenCalledOnce()
  })

  it('names the sign-out as not made on a newer build, beside Reload', async () => {
    const http = refusing(new BuildMismatchError())
    const { user } = setup(http)
    await user.click(button())

    const reload = await screen.findByRole('button', { name: 'Reload' })
    expect(reload.closest('.stale-build-read')).toHaveTextContent(
      'The other browsers were not signed out.',
    )
    expect(screen.queryByText(notSignedOut)).not.toBeInTheDocument()
    await waitFor(() => expect(reload).toHaveFocus())
    expect(http.logoutOthers).toHaveBeenCalledOnce()
  })

  it('shows nothing in the row when the session expired', async () => {
    const http = refusing(new SessionExpiredError())
    const { user } = setup(http)
    await user.click(button())

    await waitFor(() => expect(button()).toBeEnabled())
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Reload' })).not.toBeInTheDocument()
  })

  it('clears the previous line when pressed again', async () => {
    const { http, finish } = deferred()
    const { user } = setup(http)
    await user.click(button())
    await act(async () => {
      finish(new ApiError(422, 'invalid'))
      await Promise.resolve()
    })
    const first = await screen.findByRole('alert')
    expect(first).toHaveTextContent(notSignedOut)

    await user.click(button())
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Signing out…' })).toHaveAttribute(
      'aria-disabled',
      'true',
    )
    await act(async () => {
      finish(new ApiError(422, 'invalid'))
      await Promise.resolve()
    })
    // The same refusal again is a new alert, so it is read out again.
    const second = await screen.findByRole('alert')
    expect(second).toHaveTextContent(notSignedOut)
    expect(second).not.toBe(first)

    await user.click(button())
    await act(async () => {
      finish()
      await Promise.resolve()
    })
    expect(await screen.findByRole('status')).toHaveTextContent(
      'Every other browser was signed out.',
    )
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(http.logoutOthers).toHaveBeenCalledTimes(3)
  })
})
