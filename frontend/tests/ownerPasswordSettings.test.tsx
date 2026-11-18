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
import type { PasswordChange, ReviewHttp } from '../src/api/http'
import { OwnerPasswordSettings } from '../src/components/OwnerPasswordSettings'
import { useOwnerPasswordForm } from '../src/ownerPasswordForm'
import { createHttp } from './fixtures'
import { view } from './settingsFixtures'

// Settings' Owner password row in store mode: closed until asked, three fields
// that keep what was typed after a refusal, and an outcome the browser cannot
// know read again once, never sent again.

const current = 'old horse battery'
const next = 'correct horse battery staple'

const setup = (http: ReviewHttp = createHttp()) => {
  const user = userEvent.setup()
  render(<OwnerPasswordSettings http={http} />)
  return { user, http }
}
const field = (name: string) => screen.getByLabelText(name, { selector: 'input' })
const open = async (user: ReturnType<typeof userEvent.setup>) => {
  await user.click(screen.getByRole('button', { name: 'Change password' }))
}
const fill = async (
  user: ReturnType<typeof userEvent.setup>,
  values: { current?: string; next?: string; confirmation?: string },
) => {
  if (values.current) await user.type(field('Current password'), values.current)
  if (values.next) await user.type(field('New password'), values.next)
  if (values.confirmation) await user.type(field('Confirm new password'), values.confirmation)
}
const send = (user: ReturnType<typeof userEvent.setup>) =>
  user.click(screen.getByRole('button', { name: 'Change password' }))
const refusing = (error: unknown) => {
  const http = createHttp()
  http.changePassword = vi.fn(async () => {
    throw error
  })
  return http
}
const describedBy = (element: HTMLElement) =>
  (element.getAttribute('aria-describedby') ?? '')
    .split(' ')
    .map((id) => window.document.getElementById(id)?.textContent)

describe('Owner password', () => {
  it('stays closed until asked, then opens three empty fields at Current password', async () => {
    const { user } = setup()
    expect(screen.getByText('Owner password')).toBeVisible()
    expect(screen.getByText('Changing it signs out every other browser.')).toBeVisible()
    expect(screen.queryByLabelText('Current password')).not.toBeInTheDocument()
    // The row's line is read with the button, not only seen beside it.
    expect(screen.getByRole('button', { name: 'Change password' })).toHaveAccessibleDescription(
      'Changing it signs out every other browser.',
    )

    await open(user)
    expect(field('Current password')).toHaveFocus()
    expect(field('Current password')).toHaveAttribute('type', 'password')
    expect(field('Current password')).toHaveAttribute('autocomplete', 'current-password')
    expect(field('New password')).toHaveAttribute('autocomplete', 'new-password')
    expect(field('Confirm new password')).toHaveAttribute('autocomplete', 'new-password')
    for (const name of ['Current password', 'New password', 'Confirm new password'])
      expect(field(name)).toHaveValue('')
    expect(field('New password')).toHaveAccessibleDescription('At least 12 characters.')
    expect(field('New password')).not.toHaveAttribute('aria-invalid')
    expect(screen.getByRole('button', { name: 'Cancel' })).toBeVisible()
  })

  it('shows and hides all three passwords with one pressed toggle', async () => {
    const { user } = setup()
    await open(user)
    const toggle = screen.getByRole('button', { name: 'Show passwords' })
    expect(toggle).toHaveAttribute('aria-pressed', 'false')
    const controlled = (toggle.getAttribute('aria-controls') ?? '').split(' ')
    expect(controlled).toEqual(
      ['Current password', 'New password', 'Confirm new password'].map((name) => field(name).id),
    )

    await user.click(toggle)
    expect(toggle).toHaveTextContent('Hide passwords')
    expect(toggle).toHaveAttribute('aria-pressed', 'true')
    for (const name of ['Current password', 'New password', 'Confirm new password'])
      expect(field(name)).toHaveAttribute('type', 'text')
    await user.click(toggle)
    expect(toggle).toHaveTextContent('Show passwords')
    expect(field('New password')).toHaveAttribute('type', 'password')
  })

  it('closes on Cancel, clears what was typed and returns focus to the row', async () => {
    const { user, http } = setup()
    await open(user)
    await fill(user, { current, next, confirmation: next })
    await user.click(screen.getByRole('button', { name: 'Show passwords' }))
    await user.click(screen.getByRole('button', { name: 'Cancel' }))

    expect(screen.queryByLabelText('Current password')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Change password' })).toHaveFocus()
    await open(user)
    for (const name of ['Current password', 'New password', 'Confirm new password'])
      expect(field(name)).toHaveValue('')
    expect(field('Current password')).toHaveAttribute('type', 'password')
    expect(http.changePassword).not.toHaveBeenCalled()
  })

  it('asks for each missing password beside its field without sending', async () => {
    const { user, http } = setup()
    await open(user)
    await send(user)

    expect(field('Current password')).toHaveFocus()
    expect(field('Current password')).toHaveAttribute('aria-invalid', 'true')
    expect(field('Current password')).toHaveAccessibleDescription('Enter your current password.')
    expect(field('New password')).toHaveAttribute('aria-invalid', 'true')
    expect(describedBy(field('New password'))).toEqual([
      'At least 12 characters.',
      'Enter a new password.',
    ])
    // Each line is an alert: focus may already be in its field.
    expect(screen.getAllByRole('alert').map((line) => line.textContent)).toEqual([
      'Enter your current password.',
      'Enter a new password.',
    ])
    expect(http.changePassword).not.toHaveBeenCalled()

    // Typing in a field clears its own line only.
    await user.type(field('Current password'), 'x')
    expect(screen.queryByText('Enter your current password.')).not.toBeInTheDocument()
    expect(field('Current password')).not.toHaveAttribute('aria-invalid')
    expect(screen.getByText('Enter a new password.')).toBeVisible()
  })

  it('marks the hint instead of adding a line for a short password, until typed in again', async () => {
    const { user, http } = setup()
    await open(user)
    // Eleven emoji are twenty-two UTF-16 units but eleven characters.
    const short = '\u{1F4D6}'.repeat(11)
    await fill(user, { current, next: short, confirmation: short })
    await send(user)

    const input = field('New password')
    const hint = screen.getByText('At least 12 characters.')
    expect(input).toHaveFocus()
    expect(input).toHaveAttribute('aria-invalid', 'true')
    expect(input).toHaveAccessibleDescription('At least 12 characters.')
    expect(hint).toHaveClass('is-refused')
    expect(hint).toHaveAttribute('role', 'alert')
    expect(window.document.querySelector('.settings-field-error')).toBeNull()
    expect(window.document.body).not.toHaveTextContent('Use at least 12 characters.')
    // A short password is not also called a mismatch.
    expect(screen.queryByText(/do not match/u)).not.toBeInTheDocument()
    expect(input).toHaveValue(short)
    expect(http.changePassword).not.toHaveBeenCalled()

    await user.type(input, 'x')
    expect(screen.getByText('At least 12 characters.')).not.toHaveClass('is-refused')
    expect(screen.getByText('At least 12 characters.')).not.toHaveAttribute('role')
    expect(input).not.toHaveAttribute('aria-invalid')
  })

  it('counts characters for the minimum and UTF-8 bytes for the maximum', async () => {
    const { user, http } = setup()
    await open(user)
    const twelve = '\u{1F4D6}'.repeat(12)
    await fill(user, { current, next: twelve, confirmation: twelve })
    await send(user)
    await waitFor(() => expect(http.changePassword).toHaveBeenCalledOnce())
    expect(http.changePassword).toHaveBeenCalledWith({
      current_password: current,
      new_password: twelve,
      confirmation: twelve,
    })
  })

  it('refuses a password over 1,024 UTF-8 bytes beside New password', async () => {
    const { user, http } = setup()
    await open(user)
    // 513 two-byte characters: 513 characters, 1,026 bytes.
    const long = 'é'.repeat(513)
    await user.click(field('Current password'))
    await user.paste(current)
    await user.click(field('New password'))
    await user.paste(long)
    await user.click(field('Confirm new password'))
    await user.paste(long)
    await send(user)

    expect(field('New password')).toHaveFocus()
    expect(describedBy(field('New password'))).toEqual([
      'At least 12 characters.',
      'That password is too long. Use a shorter one.',
    ])
    expect(screen.getByText('At least 12 characters.')).not.toHaveClass('is-refused')
    expect(http.changePassword).not.toHaveBeenCalled()

    // 512 of them are exactly 1,024 bytes, which the server admits.
    await user.clear(field('New password'))
    await user.click(field('New password'))
    await user.paste(long.slice(1))
    await user.clear(field('Confirm new password'))
    await user.click(field('Confirm new password'))
    await user.paste(long.slice(1))
    await send(user)
    await waitFor(() => expect(http.changePassword).toHaveBeenCalledOnce())
  })

  it('asks to retype a confirmation that differs, beside Confirm new password', async () => {
    const { user, http } = setup()
    await open(user)
    await fill(user, { current, next, confirmation: `${next}!` })
    await send(user)

    const confirmation = field('Confirm new password')
    expect(confirmation).toHaveFocus()
    expect(confirmation).toHaveAttribute('aria-invalid', 'true')
    expect(confirmation).toHaveAccessibleDescription(
      'The two passwords do not match. Retype the confirmation.',
    )
    expect(field('New password')).not.toHaveAttribute('aria-invalid')
    expect(confirmation).toHaveValue(`${next}!`)
    expect(http.changePassword).not.toHaveBeenCalled()
  })

  it('announces a refusal again when Enter is pressed in the refused field', async () => {
    const { user } = setup()
    await open(user)
    await fill(user, { current, next, confirmation: `${next}!` })
    await user.keyboard('{Enter}')
    const first = screen.getByRole('alert')
    expect(first).toHaveTextContent('The two passwords do not match. Retype the confirmation.')
    expect(field('Confirm new password')).toHaveFocus()

    // Focus never left the field, so only a new alert is read out.
    await user.keyboard('{Enter}')
    const second = screen.getByRole('alert')
    expect(second).toHaveTextContent('The two passwords do not match. Retype the confirmation.')
    expect(second).not.toBe(first)
  })

  it('sends one change when the form is submitted twice before it answers', async () => {
    const http = createHttp()
    http.changePassword = vi.fn(() => new Promise<undefined>(() => undefined))
    const { user } = setup(http)
    await open(user)
    await fill(user, { current, next, confirmation: next })
    const form = field('Current password').closest('form')
    if (form === null) throw new Error('the open row has no form')
    // Two submits in one tick, before the disabled state renders.
    act(() => {
      fireEvent.submit(form)
      fireEvent.submit(form)
    })
    await screen.findByRole('button', { name: 'Changing…' })
    expect(http.changePassword).toHaveBeenCalledOnce()
  })

  it('sends once when the page remounts the row while it sends', async () => {
    const http = createHttp()
    http.changePassword = vi.fn(() => new Promise<undefined>(() => undefined))
    const Page = () => {
      const form = useOwnerPasswordForm()
      const [generation, setGeneration] = useState(0)
      return (
        <>
          <button type="button" onClick={() => setGeneration((value) => value + 1)}>
            Remount
          </button>
          <OwnerPasswordSettings key={generation} http={http} form={form} />
        </>
      )
    }
    const user = userEvent.setup()
    render(<Page />)
    await open(user)
    await fill(user, { current, next, confirmation: next })
    await user.click(screen.getByRole('button', { name: 'Change password' }))
    await user.click(screen.getByRole('button', { name: 'Remount' }))
    await user.click(screen.getByRole('button', { name: 'Changing…' }))

    expect(http.changePassword).toHaveBeenCalledOnce()
  })

  it('keeps the open form when the page remounts the row', async () => {
    const Page = () => {
      const form = useOwnerPasswordForm()
      const [generation, setGeneration] = useState(0)
      return (
        <>
          <button type="button" onClick={() => setGeneration((value) => value + 1)}>
            Remount
          </button>
          <OwnerPasswordSettings key={generation} http={createHttp()} form={form} />
        </>
      )
    }
    const user = userEvent.setup()
    render(<Page />)
    await open(user)
    await fill(user, { current, next, confirmation: `${next}!` })
    await send(user)
    await user.click(screen.getByRole('button', { name: 'Remount' }))

    expect(field('Current password')).toHaveValue(current)
    expect(field('New password')).toHaveValue(next)
    expect(field('Confirm new password')).toHaveValue(`${next}!`)
    expect(field('Confirm new password')).toHaveAccessibleDescription(
      'The two passwords do not match. Retype the confirmation.',
    )
  })

  it('keeps focus on Change password while it sends, and sends once', async () => {
    let finish: () => void = () => undefined
    const http = createHttp()
    http.changePassword = vi.fn(
      () =>
        new Promise<undefined>((resolve) => {
          finish = () => resolve(undefined)
        }),
    )
    const { user } = setup(http)
    await open(user)
    await fill(user, { current, next, confirmation: next })
    await user.click(screen.getByRole('button', { name: 'Change password' }))

    // Not `disabled`, which would drop focus to the page while it waits.
    const sending = screen.getByRole('button', { name: 'Changing…' })
    expect(sending).toHaveAttribute('aria-disabled', 'true')
    expect(sending).toHaveFocus()
    await user.click(sending)
    await user.keyboard('{Enter}')
    expect(http.changePassword).toHaveBeenCalledOnce()
    finish()
    await waitFor(() => expect(screen.getByRole('status')).toHaveFocus())
  })

  it('disables the form while sending, then closes it and announces the change', async () => {
    let finish: () => void = () => undefined
    const http = createHttp()
    http.changePassword = vi.fn(
      () =>
        new Promise<undefined>((resolve) => {
          finish = () => resolve(undefined)
        }),
    )
    const { user } = setup(http)
    await open(user)
    await fill(user, { current, next, confirmation: next })
    await user.keyboard('{Enter}')

    const sending = screen.getByRole('button', { name: 'Changing…' })
    expect(sending).toHaveAttribute('aria-disabled', 'true')
    expect(screen.getByRole('button', { name: 'Cancel' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Show passwords' })).toBeDisabled()
    for (const name of ['Current password', 'New password', 'Confirm new password'])
      expect(field(name)).toBeDisabled()
    expect(http.changePassword).toHaveBeenCalledWith({
      current_password: current,
      new_password: next,
      confirmation: next,
    } satisfies PasswordChange)

    finish()
    const status = await screen.findByRole('status')
    expect(status).toHaveTextContent('Password changed. Every other browser was signed out.')
    await waitFor(() => expect(status).toHaveFocus())
    expect(screen.queryByLabelText('Current password')).not.toBeInTheDocument()

    // Opening the form again clears the line and starts empty.
    await open(user)
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
    expect(field('Current password')).toHaveValue('')
  })

  it('says a wrong current password beside it and keeps every value', async () => {
    const { user } = setup(refusing(new ApiError(403, 'current_password_invalid')))
    await open(user)
    await fill(user, { current, next, confirmation: next })
    await send(user)

    const input = await screen.findByLabelText('Current password', { selector: 'input' })
    await waitFor(() => expect(input).toHaveFocus())
    expect(input).toHaveAttribute('aria-invalid', 'true')
    expect(input).toHaveAccessibleDescription(
      'That password was not accepted. Check it and try again.',
    )
    expect(input).toHaveValue(current)
    expect(field('New password')).toHaveValue(next)
    expect(field('Confirm new password')).toHaveValue(next)
    expect(screen.getByRole('button', { name: 'Change password' })).toBeEnabled()
  })

  it.each([
    [
      'too many attempts',
      new ApiError(429, 'throttled'),
      'Too many password attempts from this device. Wait five minutes, then try again.',
    ],
    [
      'a rule it cannot place',
      new ApiError(422, 'invalid_password'),
      'The password was not changed. Try again.',
    ],
    [
      'file mode after all',
      new ApiError(409, 'settings_managed_by_file'),
      'The password was not changed. Try again.',
    ],
    ['a renewed session', new CsrfRenewedError(), 'Your session was refreshed. Press again.'],
  ])('shows %s as one line and keeps every value', async (_, error, line) => {
    const http = refusing(error)
    const { user } = setup(http)
    await open(user)
    await fill(user, { current, next, confirmation: next })
    await send(user)

    expect(await screen.findByRole('alert')).toHaveTextContent(line)
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Change password' })).toHaveFocus(),
    )
    expect(field('Current password')).toHaveValue(current)
    expect(field('New password')).toHaveValue(next)
    expect(field('Confirm new password')).toHaveValue(next)
    expect(screen.getByText('At least 12 characters.')).not.toHaveClass('is-refused')
    // Never sent again, and nothing read again: the answer was certain.
    expect(http.changePassword).toHaveBeenCalledOnce()
    expect(http.getSettings).not.toHaveBeenCalled()
  })

  it('names the change as not made on a newer build, beside Reload', async () => {
    const http = refusing(new BuildMismatchError())
    const { user } = setup(http)
    await open(user)
    await fill(user, { current, next, confirmation: next })
    await send(user)

    const reload = await screen.findByRole('button', { name: 'Reload' })
    expect(reload.closest('.stale-build-read')).toHaveTextContent('The password was not changed.')
    await waitFor(() => expect(reload).toHaveFocus())
    expect(http.getSettings).not.toHaveBeenCalled()
  })

  it.each([
    [
      'no answer',
      new AmbiguousMutationError(),
      'No answer came back, and the password was not changed. Try again.',
    ],
    [
      'a server failure',
      new ApiError(503, 'persistence'),
      'The password was not changed. Try again.',
    ],
    ['a server fault', new ApiError(500, 'runtime'), 'The password was not changed. Try again.'],
  ])(
    'reads Settings again after %s and says the password was not changed',
    async (_, error, line) => {
      const http = refusing(error)
      http.getSettings = vi.fn(async () => view())
      const { user } = setup(http)
      await open(user)
      await fill(user, { current, next, confirmation: next })
      await send(user)

      expect(await screen.findByRole('alert')).toHaveTextContent(line)
      expect(http.getSettings).toHaveBeenCalledOnce()
      expect(http.changePassword).toHaveBeenCalledOnce()
      expect(field('Current password')).toHaveValue(current)
      expect(field('New password')).toHaveValue(next)
    },
  )

  it('offers Reload after an unreadable answer, although Settings can be read', async () => {
    // The answer may have carried the new session, so a read that succeeds
    // does not show the password unchanged.
    const http = refusing(
      new UncertainMutationError({
        operation: 'POST /api/v1/session/password',
        responseStatus: 200,
        at: '',
        reason: 'no_decoder',
      }),
    )
    http.getSettings = vi.fn(async () => view())
    const { user } = setup(http)
    await open(user)
    await fill(user, { current, next, confirmation: next })
    await send(user)

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent(
      'No answer came back, and the page could not check whether the password changed.',
    )
    expect(alert).toContainElement(screen.getByRole('button', { name: 'Reload' }))
    expect(screen.queryByText(/the password was not changed/u)).not.toBeInTheDocument()
    expect(http.getSettings).toHaveBeenCalledOnce()
    expect(http.changePassword).toHaveBeenCalledOnce()
    expect(field('New password')).toHaveValue(next)
  })

  it('offers Reload when the read after a lost answer fails too', async () => {
    const http = refusing(new AmbiguousMutationError())
    http.getSettings = vi.fn(async () => {
      throw new ApiError(503, 'unavailable')
    })
    const { user } = setup(http)
    await open(user)
    await fill(user, { current, next, confirmation: next })
    await send(user)

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent(
      'No answer came back, and the page could not check whether the password changed.',
    )
    const reload = screen.getByRole('button', { name: 'Reload' })
    expect(alert).toContainElement(reload)
    await waitFor(() => expect(reload).toHaveFocus())
    expect(http.changePassword).toHaveBeenCalledOnce()
  })

  it.each([
    ['the change itself', refusing(new SessionExpiredError()), false],
    ['the read after a lost answer', refusing(new AmbiguousMutationError()), true],
  ])('leaves sign-in to the client when %s answers 401', async (_, http, reread) => {
    if (reread)
      http.getSettings = vi.fn(async () => {
        throw new SessionExpiredError()
      })
    const { user } = setup(http)
    await open(user)
    await fill(user, { current, next, confirmation: next })
    await send(user)

    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Change password' })).toBeEnabled(),
    )
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
    expect(http.getSettings).toHaveBeenCalledTimes(reread ? 1 : 0)
  })
})
