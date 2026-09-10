import type { SettingsView } from '../src/api/http'

export const document: SettingsView['document'] = {
  schema_version: 'owner-settings-v4',
  revision: 3,
  description_advice: {
    remove_marketing: true,
    remove_sales_boasts: true,
    remove_praise: true,
    automatic_application: false,
  },
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
      name: 'a-very_long-library-name',
      display_name: 'Ebooks & Audiobooks: Collected Editions',
      policy_override: null,
      provider_connections: ['hardcover'],
    },
  ],
  limits: {
    audio_source_bytes: 8_589_934_592,
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
    session_lifetime_seconds: 2_592_000,
  },
  cover_preferences: {
    avoid_promotional_text: false,
    avoid_tie_in_branding: false,
    prefer_minimal_composition: false,
  },
}

export const view = (authority: 'file' | 'store' = 'store'): SettingsView => ({
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
