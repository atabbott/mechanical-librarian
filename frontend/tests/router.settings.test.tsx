import { act, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { ApiError } from '../src/api/http'
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
  pending_restart: ['providers'],
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
      apply_effect: 'restart_required',
      field_id: 'connections',
      group: 'connections',
      managed_by_file: authority === 'file',
      managed_path: authority === 'file' ? '/data/config.json' : null,
      pending_restart: false,
    },
    {
      apply_effect: 'restart_required',
      field_id: 'providers',
      group: 'providers',
      managed_by_file: authority === 'file',
      managed_path: authority === 'file' ? '/data/config.json' : null,
      pending_restart: true,
    },
    {
      apply_effect: 'applied_live',
      field_id: 'reduced_motion',
      group: 'appearance',
      managed_by_file: authority === 'file',
      managed_path: authority === 'file' ? '/data/config.json' : null,
      pending_restart: false,
    },
  ],
  managed_path: authority === 'file' ? '/data/config.json' : null,
  pending_restart: ['providers'],
  secrets: [
    {
      configured: true,
      field_id: 'connection.home.key',
      indicator: 'Configured · ····home',
    },
    { configured: true, field_id: 'provider.hardcover.key', indicator: 'Configured · ····abcd' },
  ],
})

describe('owner settings route', () => {
  it('keeps the primary shell intact and saves a redacted replacement with revision CAS', async () => {
    const user = userEvent.setup()
    const http = createHttp()
    http.getSettings = vi.fn(async () => view())
    http.writeSettings = vi.fn(async () => ({ ...view(), document: { ...document, revision: 4 } }))
    renderRoute('/settings', http)

    expect(await screen.findByRole('heading', { level: 1, name: 'Settings' })).toBeVisible()
    for (const name of ['Library', 'Policy', 'Activity'])
      expect(screen.getByRole('link', { name })).toBeVisible()
    expect(screen.getByText('Restart required for saved changes')).toBeVisible()
    await user.click(screen.getByRole('button', { name: /Providers/ }))
    expect(screen.getByText('Configured · ····abcd')).toBeVisible()
    await user.click(screen.getByRole('button', { name: 'Replace' }))
    await user.type(screen.getByLabelText('Replacement secret'), 'new-secret-abcd')
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
    expect(await screen.findByRole('alert')).toHaveTextContent('Settings changed elsewhere')
    expect(screen.queryByText('https://other.invalid')).not.toBeInTheDocument()
  })
})
