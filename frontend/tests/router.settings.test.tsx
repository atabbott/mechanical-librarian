import { act, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import {
  AmbiguousMutationError,
  ApiError,
  settingsEndpointValid,
  settingsIntegerValid,
  settingsOpenLibraryEmailValid,
  settingsLocalLibraryNameValid,
  settingsProviderKeyRequired,
  settingsSecretValid,
  settingsTextValid,
} from '../src/api/http'
import type { SettingsView } from '../src/api/http'
import { createHttp, renderRoute } from './fixtures'

const document: SettingsView['document'] = {
  schema_version: 'owner-settings-v1',
  revision: 3,
  connections: [{ adapter: 'audiobookshelf', endpoint: 'https://library.invalid', name: 'home' }],
  providers: [
    { adapter: 'hardcover', cover_enabled: true, enabled: true, name: 'hardcover' },
    {
      adapter: 'open_library',
      contact_email: 'owner@example.invalid',
      cover_enabled: true,
      enabled: true,
      name: 'open-library',
    },
  ],
  libraries: [
    {
      connection: 'home',
      library_id: 'books',
      media_kind: 'ebook',
      mount: 'ebooks',
      name: 'Books',
      policy_override: null,
      provider_connections: ['hardcover'],
    },
  ],
  limits: {
    provider_response_bytes: 65_536,
    source_archive_bytes: 268_435_456,
    target_response_bytes: 1_048_576,
    timeout_seconds: 30,
  },
  pending_restart: [],
  provider_recovery_generation: 1,
  reduced_motion: 'follow_system',
  server: {
    diagnostic_timings_enabled: false,
    graceful_shutdown_seconds: 30,
    library_polling_interval_seconds: 900,
    reviewer_enabled: false,
    session_lifetime_seconds: 3600,
  },
  cover_preferences: {
    avoid_promotional_text: false,
    avoid_tie_in_branding: false,
    prefer_minimal_composition: false,
  },
}

const view = (authority: 'file' | 'store' = 'store'): SettingsView => ({
  authority,
  document,
  fields: [
    {
      apply_effect: 'applied_live',
      field_id: 'connections',
      group: 'connections',
      managed_by_file: authority === 'file',
      managed_path: authority === 'file' ? '/data/config.json' : null,
    },
    {
      apply_effect: 'applied_live',
      field_id: 'providers',
      group: 'providers',
      managed_by_file: authority === 'file',
      managed_path: authority === 'file' ? '/data/config.json' : null,
    },
    {
      apply_effect: 'applied_live',
      field_id: 'reduced_motion',
      group: 'appearance',
      managed_by_file: authority === 'file',
      managed_path: authority === 'file' ? '/data/config.json' : null,
    },
  ],
  managed_path: authority === 'file' ? '/data/config.json' : null,
  provider_capabilities: [],
  secrets: [
    {
      configured: true,
      field_id: 'connection.home.key',
      indicator: 'Configured · ····home',
    },
    { configured: true, field_id: 'provider.hardcover.key', indicator: 'Configured · ····abcd' },
  ],
})

describe('settings API field validation vocabulary', () => {
  it('matches the generated boundary at exact and unusual values', () => {
    expect(settingsEndpointValid('not a URL but API-valid')).toBe(true)
    expect(settingsEndpointValid('x'.repeat(2048))).toBe(true)
    expect(settingsEndpointValid('😀'.repeat(2048))).toBe(true)
    expect(settingsEndpointValid('')).toBe(false)
    expect(settingsEndpointValid('x'.repeat(2049))).toBe(false)
    expect(settingsEndpointValid('😀'.repeat(2049))).toBe(false)
    expect(settingsIntegerValid(60, 1, 60)).toBe(true)
    expect(settingsIntegerValid(61, 1, 60)).toBe(false)
    expect(settingsTextValid('x', 1, 128)).toBe(true)
    expect(settingsTextValid('', 1, 128)).toBe(false)
    expect(settingsSecretValid('printable-secret')).toBe(true)
    expect(settingsSecretValid('Bearer secret')).toBe(false)
    expect(settingsOpenLibraryEmailValid('owner@example.invalid')).toBe(true)
    expect(settingsOpenLibraryEmailValid('not-an-email')).toBe(false)
    expect(settingsLocalLibraryNameValid('books_1')).toBe(true)
    expect(settingsLocalLibraryNameValid('Not valid')).toBe(false)
    expect(settingsProviderKeyRequired('hardcover')).toBe(true)
    expect(settingsProviderKeyRequired('google_books')).toBe(false)
  })
})

describe('owner settings route', () => {
  it('keeps absent supported providers configurable from an empty document', async () => {
    const user = userEvent.setup()
    const http = createHttp()
    http.getSettings = vi.fn(async () => ({
      ...view(),
      document: { ...document, providers: [] },
      provider_capabilities: [
        {
          adapter: 'open_library' as const,
          credential: 'contact_email' as const,
          label: 'Open Library',
          name: 'open_library',
        },
      ],
    }))
    renderRoute('/settings?group=providers', http)

    await user.click(await screen.findByRole('button', { name: 'Add Open Library' }))
    expect(screen.getByRole('checkbox', { name: 'Provider state Enable open library' })).toBeVisible()
    expect(screen.getByRole('textbox', { name: 'Open Library contact email' })).toBeVisible()
  })

  it('keeps the primary shell intact and saves a redacted replacement with revision CAS', async () => {
    const user = userEvent.setup()
    const http = createHttp()
    http.getSettings = vi.fn(async () => view())
    http.writeSettings = vi.fn(async () => ({ ...view(), document: { ...document, revision: 4 } }))
    renderRoute('/settings', http)

    expect(await screen.findByRole('heading', { level: 1, name: 'Settings' })).toBeVisible()
    for (const name of ['Library', 'Policy', 'Activity'])
      expect(screen.getByRole('link', { name })).toBeVisible()
    expect(screen.queryByText('Restart required for saved changes')).not.toBeInTheDocument()
    expect(screen.queryByText('Applies on restart')).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /Providers/ }))
    expect(screen.getByRole('checkbox', { name: 'Provider state Enable hardcover' })).toBeVisible()
    expect(screen.getByRole('link', { name: 'Get a Hardcover key' })).toBeVisible()
    expect(screen.getByText('Configured · ····abcd')).toBeVisible()
    expect(
      screen.getByRole('textbox', { name: 'Open Library contact email' }),
    ).toHaveAccessibleDescription(
      /Sent only to Open Library; never shared with this project or retained in reports/,
    )
    expect(screen.queryByText('Cover evidence')).not.toBeInTheDocument()
    expect(screen.queryByRole('checkbox', { name: /cover evidence/i })).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Replace hardcover API key' }))
    await user.type(screen.getByLabelText('Replacement hardcover API key'), 'new-secret-abcd')
    await user.click(screen.getByRole('button', { name: 'Save settings' }))

    await waitFor(() => expect(http.writeSettings).toHaveBeenCalledOnce())
    expect(http.writeSettings).toHaveBeenCalledWith(
      expect.objectContaining({
        expected_revision: 3,
        secrets: [
          { field_id: 'provider.hardcover.key', operation: 'replace', value: 'new-secret-abcd' },
        ],
      }),
    )
    expect(screen.queryByDisplayValue('new-secret-abcd')).not.toBeInTheDocument()
    expect(await screen.findByText(/Server-authoritative settings are current/)).toBeVisible()
  })

  it('renders file authority without covered edit affordances in the DOM', async () => {
    const user = userEvent.setup()
    const http = createHttp()
    http.getSettings = vi.fn(async () => view('file'))
    renderRoute('/settings', http)
    expect(await screen.findByText('Managed by /data/config.json')).toBeVisible()
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Save settings' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Replace|Clear/ })).not.toBeInTheDocument()
    for (const name of [
      /Providers/,
      /Libraries/,
      /Automation & limits/,
      /Reviewer/,
      /Server/,
      /Appearance/,
    ]) {
      await user.click(screen.getByRole('button', { name }))
      expect(window.document.querySelector('.settings-sheet input')).toBeNull()
      expect(screen.queryByRole('button', { name: 'Save settings' })).not.toBeInTheDocument()
    }
  })

  it('owns group navigation in the URL and restores it with browser history', async () => {
    const user = userEvent.setup()
    const http = createHttp()
    http.getSettings = vi.fn(async () => view())
    const { router } = renderRoute('/settings?group=connection', http)
    await screen.findByRole('heading', { level: 2, name: 'Connection' })
    await user.click(screen.getByRole('button', { name: /Libraries/ }))
    expect(await screen.findByRole('heading', { level: 2, name: 'Libraries' })).toBeVisible()
    expect(router.state.location.search).toEqual({ group: 'libraries' })
    await user.click(screen.getByRole('button', { name: /Appearance/ }))
    expect(await screen.findByRole('heading', { level: 2, name: 'Appearance' })).toBeVisible()
    act(() => router.history.back())
    expect(await screen.findByRole('heading', { level: 2, name: 'Libraries' })).toBeVisible()
  })

  it('announces unavailable and stale recovery states without leaking a value', async () => {
    const user = userEvent.setup()
    const http = createHttp()
    http.getSettings = vi
      .fn()
      .mockRejectedValueOnce(new ApiError(503, 'settings_unavailable'))
      .mockResolvedValue(view())
    http.writeSettings = vi.fn(async () => {
      throw new ApiError(409, 'settings_revision_stale')
    })
    renderRoute('/settings', http)
    expect(await screen.findByRole('alert')).toHaveTextContent('could not safely read')
    await user.click(screen.getByRole('button', { name: 'Try again' }))
    expect(await screen.findByRole('heading', { level: 1, name: 'Settings' })).toBeVisible()
    await user.clear(screen.getByDisplayValue('https://library.invalid'))
    await user.type(screen.getByRole('textbox'), 'https://other.invalid')
    await user.click(screen.getByRole('button', { name: 'Save settings' }))
    const staleAlert = await screen.findByRole('alert')
    expect(staleAlert).toHaveTextContent('Settings changed elsewhere')
    await waitFor(() => expect(staleAlert).toHaveFocus())
    expect(screen.queryByText('https://other.invalid')).not.toBeInTheDocument()
  })

  it.each([new AmbiguousMutationError(), new ApiError(502, 'invalid_settings_response')])(
    'reconciles an uncertain save before confirming authoritative state',
    async (failure) => {
      const user = userEvent.setup()
      const http = createHttp()
      const reconciled: SettingsView = {
        ...view(),
        document: {
          ...document,
          revision: 4,
          connections: [
            { adapter: 'audiobookshelf', endpoint: 'https://authoritative.invalid', name: 'home' },
          ],
        },
      }
      http.getSettings = vi.fn().mockResolvedValueOnce(view()).mockResolvedValueOnce(reconciled)
      http.writeSettings = vi.fn(async () => {
        throw failure
      })
      renderRoute('/settings?group=connection', http)
      const endpoint = await screen.findByRole('textbox', { name: 'Audiobookshelf endpoint' })
      await waitFor(() =>
        expect(globalThis.document.getElementById('main-content')).toHaveAttribute(
          'data-restored-path',
          '/settings',
        ),
      )
      await user.click(endpoint)
      await user.clear(endpoint)
      await user.type(endpoint, 'https://submitted.invalid')
      await user.click(screen.getByRole('button', { name: 'Save settings' }))

      const recovered = await screen.findByText(
        'Authoritative Settings reloaded. Review the current values before saving again.',
      )
      expect(recovered.closest('[role="alert"]')).toHaveFocus()
      expect(screen.getByRole('textbox', { name: 'Audiobookshelf endpoint' })).toHaveValue(
        'https://authoritative.invalid',
      )
      expect(screen.getByRole('button', { name: 'Save settings' })).toBeDisabled()
      expect(screen.queryByText(/Nothing changed/)).not.toBeInTheDocument()
      expect(http.getSettings).toHaveBeenCalledTimes(2)
      expect(http.writeSettings).toHaveBeenCalledOnce()
    },
  )

  it('blocks repeat mutation when uncertain reconciliation cannot reload', async () => {
    const user = userEvent.setup()
    const http = createHttp()
    http.getSettings = vi
      .fn()
      .mockResolvedValueOnce(view())
      .mockRejectedValue(new ApiError(503, 'settings_unavailable'))
    http.writeSettings = vi.fn(async () => {
      throw new ApiError(503, 'settings_unavailable')
    })
    renderRoute('/settings?group=connection', http)
    const endpoint = await screen.findByRole('textbox', { name: 'Audiobookshelf endpoint' })
    await waitFor(() =>
      expect(globalThis.document.getElementById('main-content')).toHaveAttribute(
        'data-restored-path',
        '/settings',
      ),
    )
    await user.clear(endpoint)
    await user.type(endpoint, 'https://submitted.invalid')
    await user.click(screen.getByRole('button', { name: 'Save settings' }))

    const uncertain = await screen.findByText(/Save outcome is uncertain and Settings could not/)
    expect(uncertain.closest('[role="alert"]')).toHaveFocus()
    expect(screen.getByRole('button', { name: 'Save settings' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Retry authoritative reload' })).toBeVisible()
    expect(screen.queryByText(/Nothing changed/)).not.toBeInTheDocument()
    expect(http.writeSettings).toHaveBeenCalledOnce()
  })

  it('associates every editable validation family with its named field', async () => {
    const user = userEvent.setup()
    const http = createHttp()
    http.getSettings = vi.fn(async () => view())
    renderRoute('/settings?group=connection', http)

    const endpoint = await screen.findByRole('textbox', { name: 'Audiobookshelf endpoint' })
    await waitFor(() =>
      expect(globalThis.document.getElementById('main-content')).toHaveAttribute(
        'data-restored-path',
        '/settings',
      ),
    )
    await user.click(endpoint)
    await user.clear(endpoint)
    expect(endpoint).toHaveFocus()
    expect(endpoint).toHaveAttribute('aria-invalid', 'true')
    expect(endpoint).toHaveAccessibleDescription(/endpoint must contain 1–2,048 characters/)
    expect(screen.getByRole('alert')).toHaveTextContent('endpoint must contain')

    await user.click(screen.getByRole('button', { name: 'Replace home access key' }))
    const targetSecret = screen.getByLabelText('Replacement home access key')
    await user.type(targetSecret, 'Bearer secret')
    expect(targetSecret).toHaveAttribute('aria-invalid', 'true')
    expect(targetSecret).toHaveAccessibleDescription(/Secrets must be 1–2,048/)

    await user.click(screen.getByRole('button', { name: /Providers/ }))
    const email = screen.getByRole('textbox', { name: 'Open Library contact email' })
    await user.clear(email)
    expect(email).toHaveAttribute('aria-invalid', 'true')
    expect(email).toHaveAccessibleDescription(/needs a contact email while enabled/)
    const openLibraryEnabled = screen.getByRole('checkbox', {
      name: 'Provider state Enable open-library',
    })
    await user.click(openLibraryEnabled)
    expect(email).not.toHaveAttribute('aria-invalid')
    await user.click(openLibraryEnabled)
    expect(email).toHaveAttribute('aria-invalid', 'true')

    await user.click(screen.getByRole('button', { name: 'Clear hardcover API key' }))
    await user.click(screen.getByRole('button', { name: 'Confirm clear hardcover API key' }))
    const requiredKey = screen.getByRole('button', {
      name: 'Keep configured hardcover API key',
    })
    expect(requiredKey).toHaveAttribute('aria-invalid', 'true')
    expect(requiredKey).toHaveAccessibleDescription(/requires an API key while enabled/)

    await user.click(screen.getByRole('button', { name: /Libraries/ }))
    const libraryId = screen.getByRole('textbox', { name: 'Library identifier' })
    await user.clear(libraryId)
    expect(libraryId).toHaveAttribute('aria-invalid', 'true')
    expect(libraryId).toHaveAccessibleDescription(/identifier must contain 1–128 characters/)

    await user.click(screen.getByRole('button', { name: /Automation & limits/ }))
    const timeout = screen.getByRole('spinbutton', { name: 'Request timeout' })
    await user.clear(timeout)
    await user.type(timeout, '0')
    expect(timeout).toHaveAttribute('aria-invalid', 'true')
    expect(timeout).toHaveAccessibleDescription(/Request timeout must be from 1 to 60/)
    expect(screen.getByRole('button', { name: 'Save settings' })).toBeDisabled()
  })
})
