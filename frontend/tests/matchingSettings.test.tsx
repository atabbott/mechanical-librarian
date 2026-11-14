import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import type { ReviewHttp, SettingsView } from '../src/api/http'
import { createHttp, renderRoute } from './fixtures'
import { savedDocument, view } from './settingsFixtures'

// Book matching in Policy, under Curation (#1355, Behaviour test 12): three
// levels, saved on its own apart from the policy draft, and a line saying how
// far the re-check of current cover comparisons has come.

type Check = NonNullable<SettingsView['matching_check']>
const check = (current_works: number, checking: number, failed: number): Check => ({
  current_works,
  checking,
  failed,
})
const withMatching = (
  base: SettingsView,
  level: SettingsView['document']['matching']['work_identity'],
  matching_check: Check | null,
): SettingsView => ({
  ...base,
  document: { ...base.document, matching: { work_identity: level } },
  matching_check,
})

const region = () => screen.findByRole('region', { name: 'Book matching' })
const option = (scope: HTMLElement, name: string) =>
  within(within(scope).getByRole('group', { name: 'Book matching' })).getByRole('button', {
    name,
  })

describe('Book matching', () => {
  it('offers the three levels, shows the saved one, and saves on its own', async () => {
    const user = userEvent.setup()
    const http = createHttp()
    const policyCommand = vi.fn(http.policyCommand)
    http.policyCommand = policyCommand
    const initial = withMatching(view(), 'identifier', check(0, 0, 0))
    http.getSettings = vi.fn(async () => initial)
    http.writeSettings = vi.fn<ReviewHttp['writeSettings']>(async (write) => ({
      ...initial,
      document: savedDocument(write.document),
      matching_check: check(2, 2, 0),
    }))
    renderRoute('/policy?section=curation', http)
    const scope = await region()
    await within(scope).findByRole('group', { name: 'Book matching' })
    expect(
      within(scope).getByText(
        'Which source records count as the same book when you review. Edition details, such as ISBN, publisher and narrators, always need a confirmed match.',
      ),
    ).toBeVisible()
    for (const name of [
      'Confirmed matches only',
      'Same title and every author',
      'Same title and any author',
    ])
      expect(option(scope, name)).toBeVisible()
    expect(option(scope, 'Confirmed matches only')).toHaveAttribute('aria-pressed', 'true')
    expect(
      within(scope).queryByText(
        'A field that needs you also offers values from these records, and covers from them say “Same title and author”. Mechanical Librarian does not choose them for you.',
      ),
    ).toBeNull()
    const save = within(scope).getByRole('button', { name: 'Save book matching' })
    expect(save).toHaveAttribute('aria-disabled', 'true')

    await user.click(option(scope, 'Same title and every author'))
    expect(
      within(scope).getByText(
        'A field that needs you also offers values from these records, and covers from them say “Same title and author”. Mechanical Librarian does not choose them for you.',
      ),
    ).toBeVisible()
    expect(
      within(scope).getByText('Unsaved. Activating the policy does not save it.'),
    ).toBeVisible()
    await user.click(save)

    await waitFor(() => expect(http.writeSettings).toHaveBeenCalledTimes(1))
    const written = vi.mocked(http.writeSettings).mock.calls[0]?.[0]
    expect(written?.expected_revision).toBe(initial.document.revision)
    expect(written?.document.matching).toEqual({ work_identity: 'title_and_creator_exact' })
    expect(await within(scope).findByText('Book matching saved.')).toBeVisible()
    expect(
      within(scope).getByText('Works are being checked against the new setting.'),
    ).toBeVisible()
    // Saving the setting is not a policy change: nothing was activated.
    expect(policyCommand).not.toHaveBeenCalled()
  })

  it('says when a level-changing save found no works to check', async () => {
    const user = userEvent.setup()
    const http = createHttp()
    const initial = withMatching(view(), 'identifier', check(0, 0, 0))
    http.getSettings = vi.fn(async () => initial)
    http.writeSettings = vi.fn<ReviewHttp['writeSettings']>(async (write) => ({
      ...initial,
      document: savedDocument(write.document),
    }))
    renderRoute('/policy?section=curation', http)
    const scope = await region()
    await within(scope).findByRole('group', { name: 'Book matching' })
    expect(within(scope).queryByText('No works needed checking.')).toBeNull()
    await user.click(option(scope, 'Same title and any author'))
    await user.click(within(scope).getByRole('button', { name: 'Save book matching' }))
    expect(await within(scope).findByText('No works needed checking.')).toBeVisible()
  })

  it.each([
    [
      1,
      '1 work could not be checked against the new setting. Its changes wait for that check. Save the setting again to retry.',
    ],
    [
      3,
      '3 works could not be checked against the new setting. Their changes wait for those checks. Save the setting again to retry.',
    ],
  ])(
    'names %i work(s) whose check gave up and retries with the same value',
    async (failed, line) => {
      const user = userEvent.setup()
      const http = createHttp()
      const initial = withMatching(view(), 'title_and_creator', check(4, 0, failed))
      http.getSettings = vi.fn(async () => initial)
      http.writeSettings = vi.fn<ReviewHttp['writeSettings']>(async (write) => ({
        ...initial,
        document: savedDocument(write.document),
        matching_check: check(4, failed, 0),
      }))
      renderRoute('/policy?section=curation', http)
      const scope = await region()
      expect(await within(scope).findByText(line)).toBeVisible()
      const save = within(scope).getByRole('button', { name: 'Save book matching' })
      // Unchanged, yet Save is offered so the gave-up works are retried.
      expect(save).not.toHaveAttribute('aria-disabled')
      await user.click(save)
      await waitFor(() => expect(http.writeSettings).toHaveBeenCalledTimes(1))
      expect(vi.mocked(http.writeSettings).mock.calls[0]?.[0].document.matching).toEqual({
        work_identity: 'title_and_creator',
      })
      expect(
        await within(scope).findByText('Works are being checked against the new setting.'),
      ).toBeVisible()
    },
  )

  it('is read only when the deployment configuration sets it', async () => {
    const http = createHttp()
    http.getSettings = vi.fn(async () =>
      withMatching(view('file'), 'title_and_creator_exact', check(0, 0, 0)),
    )
    renderRoute('/policy?section=curation', http)
    const scope = await region()
    expect(
      await within(scope).findByText('Book matching is set in the deployment configuration.'),
    ).toBeVisible()
    expect(option(scope, 'Same title and every author')).toHaveAttribute('aria-pressed', 'true')
    expect(option(scope, 'Confirmed matches only')).toBeDisabled()
    expect(within(scope).getByRole('button', { name: 'Save book matching' })).toBeDisabled()
  })

  it.each([
    [
      1,
      '1 work could not be checked against the new setting. Its changes wait for that check. Restart the Mechanical Librarian service to retry.',
    ],
    [
      2,
      '2 works could not be checked against the new setting. Their changes wait for those checks. Restart the Mechanical Librarian service to retry.',
    ],
  ])('shows %i stuck work(s) in file mode, retried by a restart (#1355)', async (failed, line) => {
    const http = createHttp()
    http.getSettings = vi.fn(async () =>
      withMatching(view('file'), 'title_and_creator', check(4, 0, failed)),
    )
    renderRoute('/policy?section=curation', http)
    const scope = await region()
    expect(await within(scope).findByText(line)).toBeVisible()
    expect(within(scope).queryByText(/Save the setting again/u)).toBeNull()
    expect(within(scope).getByRole('button', { name: 'Save book matching' })).toBeDisabled()
    expect(
      within(scope).getByText(
        'A field that needs you also offers values from these records, and covers from them say “Same title and author”. Mechanical Librarian does not choose them for you.',
      ),
    ).toBeVisible()
  })

  it('offers a reload after a refused save', async () => {
    const user = userEvent.setup()
    const http = createHttp()
    const initial = withMatching(view(), 'identifier', check(0, 0, 0))
    http.getSettings = vi.fn(async () => initial)
    http.writeSettings = vi.fn<ReviewHttp['writeSettings']>(async () => {
      throw new Error('refused')
    })
    renderRoute('/policy?section=curation', http)
    const scope = await region()
    await within(scope).findByRole('group', { name: 'Book matching' })
    await user.click(option(scope, 'Same title and any author'))
    await user.click(within(scope).getByRole('button', { name: 'Save book matching' }))
    expect(await within(scope).findByRole('button', { name: 'Reload book matching' })).toBeVisible()
  })
})
