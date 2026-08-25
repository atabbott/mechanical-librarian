import { useMutation, useQuery } from '@tanstack/react-query'
import { useMemo, useRef, useState } from 'react'
import type { ReactNode } from 'react'

import { ApiError } from '../api/http'
import type { SettingsView, SettingsWrite } from '../api/http'
import { settingsRoute, useReviewContext } from '../router'
import type { SettingsGroup } from '../router'

type Mutable<T> = T extends readonly (infer Item)[]
  ? Mutable<Item>[]
  : T extends object
    ? { -readonly [Key in keyof T]: Mutable<T[Key]> }
    : T
type Document = Mutable<SettingsView['document']>
type SecretWrite = SettingsWrite['secrets'][number]
type Group = SettingsGroup

const groupById: Readonly<Record<Group, { id: Group; label: string; description: string }>> = {
  connection: { id: 'connection', label: 'Connection', description: 'Your Audiobookshelf service' },
  providers: { id: 'providers', label: 'Providers', description: 'Metadata evidence sources' },
  libraries: {
    id: 'libraries',
    label: 'Libraries',
    description: 'Included collections and mounts',
  },
  automation: {
    id: 'automation',
    label: 'Automation & limits',
    description: 'Bounded work and responses',
  },
  reviewer: { id: 'reviewer', label: 'Reviewer', description: 'Contained review posture' },
  server: { id: 'server', label: 'Server', description: 'Sessions, polling and shutdown' },
  appearance: {
    id: 'appearance',
    label: 'Appearance',
    description: 'Accessible motion preference',
  },
}
const groups = Object.values(groupById)
const isGroup = (value: string): value is Group => Object.hasOwn(groupById, value)

const clone = (value: SettingsView['document'] | Document): Document =>
  structuredClone(value) as Document
const effectCopy = (effect: string) =>
  effect === 'applied_live'
    ? 'Applies immediately'
    : effect === 'deploy_managed'
      ? 'Deployment managed'
      : 'Applies on restart'

const secretValid = (value: string) =>
  value.length >= 1 &&
  value.length <= 2048 &&
  /^[\x21-\x7e]+$/u.test(value) &&
  !value.toLowerCase().startsWith('bearer')

const endpointValid = (value: string) => {
  try {
    const endpoint = new URL(value)
    return (endpoint.protocol === 'http:' || endpoint.protocol === 'https:') && value.length <= 2048
  } catch {
    return false
  }
}

const validationErrors = (
  document: Document,
  secrets: Record<string, SecretWrite>,
  view: SettingsView,
) => {
  const errors: string[] = []
  const ranges = [
    [document.limits.target_response_bytes, 1024, 4_194_304, 'Target response limit'],
    [document.limits.provider_response_bytes, 1024, 262_144, 'Provider response limit'],
    [document.limits.source_archive_bytes, 1024, 268_435_456, 'Source archive limit'],
    [document.limits.timeout_seconds, 1, 300, 'Request timeout'],
    [document.server.session_lifetime_seconds, 300, 86_400, 'Session lifetime'],
    [document.server.library_polling_interval_seconds, 60, 86_400, 'Polling interval'],
    [document.server.graceful_shutdown_seconds, 1, 300, 'Shutdown budget'],
  ] as const
  for (const [value, minimum, maximum, label] of ranges)
    if (!Number.isSafeInteger(value) || value < minimum || value > maximum)
      errors.push(
        `${label} must be from ${minimum.toLocaleString()} to ${maximum.toLocaleString()}.`,
      )
  for (const entry of Object.values(secrets))
    if (entry.operation !== 'clear' && (!entry.value || !secretValid(entry.value)))
      errors.push(
        'Secrets must be 1–2,048 printable ASCII characters with no whitespace or Bearer prefix.',
      )
  for (const connection of document.connections) {
    if (!endpointValid(connection.endpoint))
      errors.push(`${connection.name} needs a valid HTTP or HTTPS endpoint.`)
    const fieldId = `connection.${connection.name}.key`
    const existing = view.secrets.find((item) => item.field_id === fieldId)?.configured === true
    const pending = secrets[fieldId]
    if ((!existing && !pending?.value) || pending?.operation === 'clear')
      errors.push(`${connection.name} requires an access key.`)
  }
  for (const provider of document.providers) {
    if (provider.adapter === 'open_library' && provider.enabled && !provider.contact_email)
      errors.push(`${provider.name} needs a contact email while enabled.`)
    if (provider.adapter === 'hardcover' && provider.enabled) {
      const fieldId = `provider.${provider.name}.key`
      const existing = view.secrets.find((item) => item.field_id === fieldId)?.configured === true
      const pending = secrets[fieldId]
      if ((!existing && !pending?.value) || pending?.operation === 'clear')
        errors.push(`${provider.name} requires an API key while enabled.`)
    }
  }
  return errors
}

const Row = ({
  title,
  description,
  effect,
  locked,
  children,
}: {
  title: string
  description: string
  effect: string
  locked: boolean
  children: ReactNode
}) => (
  <div className="settings-field-row">
    <div>
      <span className="settings-effect">{effectCopy(effect)}</span>
      <strong>{title}</strong>
      <p>{description}</p>
    </div>
    <div className="settings-field-control">
      {locked ? <span className="settings-lock">Locked</span> : null}
      {children}
    </div>
  </div>
)

const Secret = ({
  fieldId,
  view,
  pending,
  onChange,
}: {
  fieldId: string
  view: SettingsView
  pending: SecretWrite | undefined
  onChange: (value: SecretWrite | undefined) => void
}) => {
  const presence = view.secrets.find((item) => item.field_id === fieldId)
  const [editing, setEditing] = useState(!presence?.configured)
  const [confirmingClear, setConfirmingClear] = useState(false)
  if (pending?.operation === 'clear')
    return (
      <div className="settings-secret-state">
        <strong>Will be cleared</strong>
        <button type="button" onClick={() => onChange(undefined)}>
          Keep configured secret
        </button>
      </div>
    )
  if (!editing && presence?.configured)
    return (
      <div className="settings-secret-state">
        <strong>{presence.indicator ?? 'Configured'}</strong>
        {confirmingClear ? (
          <div role="alert" className="settings-clear-confirmation">
            <span>Confirm removal. This integration may stop working after restart.</span>
            <button
              type="button"
              onClick={() => {
                onChange({ field_id: fieldId, operation: 'clear' })
                setConfirmingClear(false)
              }}
            >
              Confirm clear
            </button>
            <button type="button" onClick={() => setConfirmingClear(false)}>
              Keep secret
            </button>
          </div>
        ) : (
          <span>
            <button type="button" onClick={() => setEditing(true)}>
              Replace
            </button>
            <button type="button" onClick={() => setConfirmingClear(true)}>
              Clear
            </button>
          </span>
        )}
      </div>
    )
  return (
    <div className="settings-secret-editor">
      <input
        type="password"
        autoComplete="new-password"
        maxLength={2048}
        aria-label={presence?.configured ? 'Replacement secret' : 'New secret'}
        value={pending?.value ?? ''}
        onChange={(event) =>
          onChange({
            field_id: fieldId,
            operation: presence?.configured ? 'replace' : 'set',
            value: event.currentTarget.value,
          })
        }
      />
      <small>The value is sent once and never shown again.</small>
      {presence?.configured ? (
        <button
          type="button"
          onClick={() => {
            onChange(undefined)
            setEditing(false)
          }}
        >
          Cancel replacement
        </button>
      ) : null}
    </div>
  )
}

export default function SettingsPage() {
  const { http, queryClient } = useReviewContext()
  const query = useQuery({
    queryKey: ['settings'] as const,
    queryFn: ({ signal }) => http.getSettings(signal),
    staleTime: 15_000,
    retry: false,
  })
  const [staleRecovery, setStaleRecovery] = useState(false)
  const [saved, setSaved] = useState(false)
  if (query.isPending)
    return (
      <section className="settings-state" aria-busy="true" data-route-focus tabIndex={-1}>
        <h1>Loading settings…</h1>
        <p>Reading the owner-authoritative configuration.</p>
      </section>
    )
  if (query.isError)
    return (
      <section className="settings-state" data-route-focus tabIndex={-1}>
        <h1>Settings are unavailable.</h1>
        <p role="alert">The server could not safely read the current settings.</p>
        <button type="button" onClick={() => void query.refetch()}>
          Try again
        </button>
      </section>
    )
  return (
    <>
      {staleRecovery ? (
        <p
          className="settings-stale-recovery"
          role="alert"
          tabIndex={-1}
          ref={(element) => element?.focus()}
        >
          Settings changed elsewhere. The authoritative revision replaced this draft; review it
          before saving again.
        </p>
      ) : null}
      {saved ? (
        <p className="settings-saved" role="status">
          Saved. Server-authoritative settings are current.
        </p>
      ) : null}
      <SettingsEditor
        key={`${query.data.authority}:${query.data.document.revision.toString()}`}
        view={query.data}
        onSaved={(value) => {
          queryClient.setQueryData(['settings'], value)
          queryClient.setQueryData(['appearance'], {
            reduced_motion: value.document.reduced_motion,
          })
          setStaleRecovery(false)
          setSaved(true)
        }}
        onStale={() => {
          setStaleRecovery(true)
          setSaved(false)
          void query.refetch()
        }}
      />
    </>
  )
}

const SettingsEditor = ({
  view,
  onSaved,
  onStale,
}: {
  view: SettingsView
  onSaved: (value: SettingsView) => void
  onStale: () => void
}) => {
  const { http } = useReviewContext()
  const { group: active } = settingsRoute.useSearch()
  const navigate = settingsRoute.useNavigate()
  const [draft, setDraft] = useState<Document>(() => clone(view.document))
  const [secrets, setSecrets] = useState<Record<string, SecretWrite>>({})
  const [status, setStatus] = useState<'idle' | 'saved' | 'failed'>('idle')
  const [libraryPage, setLibraryPage] = useState(0)
  const statusTarget = useRef<HTMLParagraphElement>(null)
  const mutation = useMutation({
    mutationFn: (value: SettingsWrite) => http.writeSettings(value),
    retry: false,
    onSuccess: (value) => {
      setStatus('saved')
      onSaved(value)
    },
    onError: (error) => {
      if (error instanceof ApiError && error.status === 409) onStale()
      else {
        setStatus('failed')
        requestAnimationFrame(() => statusTarget.current?.focus())
      }
    },
  })
  const errors = useMemo(() => validationErrors(draft, secrets, view), [draft, secrets, view])
  const changed = useMemo(
    () =>
      JSON.stringify(view.document) !== JSON.stringify(draft) || Object.keys(secrets).length > 0,
    [draft, secrets, view.document],
  )
  const libraryPageCount = Math.max(1, Math.ceil(draft.libraries.length / 20))
  const visibleLibraries = draft.libraries
    .map((library, index) => ({ library, index }))
    .slice(libraryPage * 20, libraryPage * 20 + 20)
  const locked = view.authority === 'file'
  const effect = (fieldId: string) =>
    view.fields.find((item) => item.field_id === fieldId)?.apply_effect ?? 'restart_required'
  const update = (change: (value: Document) => void) => {
    const next = clone(draft)
    change(next)
    setDraft(next)
    setStatus('idle')
  }
  const setSecret = (fieldId: string, value: SecretWrite | undefined) => {
    setSecrets((current) => {
      if (value) return { ...current, [fieldId]: value }
      return Object.fromEntries(Object.entries(current).filter(([key]) => key !== fieldId))
    })
    setStatus('idle')
  }
  const currentGroup = groupById[active]

  return (
    <section className="settings-route" data-route-focus tabIndex={-1}>
      <header className="settings-heading">
        <div>
          <h1>Settings</h1>
          <p>Owner-managed configuration, with every consequence named before it is saved.</p>
        </div>
        <span>{locked ? 'File authority' : 'In-app authority'}</span>
      </header>
      {locked ? (
        <section className="settings-file-banner" role="status">
          <strong>Managed by /data/config.json</strong>
          <p>
            These values are read-only here. Use the supported Import and retire file management
            flow when it becomes available; do not delete the file manually.
          </p>
        </section>
      ) : null}
      {view.pending_restart.length ? (
        <section className="settings-restart" aria-label="Pending restart settings">
          <strong>Restart required for saved changes</strong>
          <ul>
            {view.pending_restart.map((item) => (
              <li key={item}>{item.replaceAll('_', ' ').replaceAll('.', ' · ')}</li>
            ))}
          </ul>
        </section>
      ) : null}
      <label className="settings-mobile-group">
        Settings group
        <select
          value={active}
          onChange={(event) => {
            const group = event.currentTarget.value
            if (isGroup(group)) void navigate({ search: { group } })
          }}
        >
          {groups.map((group) => (
            <option key={group.id} value={group.id}>
              {group.label}
            </option>
          ))}
        </select>
      </label>
      <div className="settings-editor">
        <aside className="settings-sidebar" aria-label="Settings groups">
          <header>
            <strong>Configuration</strong>
            <small>{String(groups.length)} focused groups</small>
          </header>
          <nav>
            {groups.map((group) => (
              <button
                type="button"
                key={group.id}
                aria-current={active === group.id ? 'page' : undefined}
                onClick={() => void navigate({ search: { group: group.id } })}
              >
                <span>
                  <strong>{group.label}</strong>
                  <small>{group.description}</small>
                </span>
              </button>
            ))}
          </nav>
        </aside>
        <div className="settings-sheet">
          <header>
            <h2>{currentGroup.label}</h2>
            <p>{currentGroup.description}</p>
          </header>
          <div className="settings-fields">
            {active === 'connection'
              ? draft.connections.map((connection, index) => (
                  <div className="settings-record" key={connection.name}>
                    <h3>{connection.name}</h3>
                    <Row
                      title="Audiobookshelf endpoint"
                      description="The exact service origin used for catalogue and metadata work."
                      effect={effect('connections')}
                      locked={locked}
                    >
                      {locked ? (
                        <span>{connection.endpoint}</span>
                      ) : (
                        <input
                          type="url"
                          maxLength={2048}
                          value={connection.endpoint}
                          onChange={(event) =>
                            update((value) => {
                              const item = value.connections.at(index)
                              if (item) item.endpoint = event.currentTarget.value
                            })
                          }
                        />
                      )}
                    </Row>
                    <Row
                      title="Access key"
                      description="Write-only target authority. It is never returned to this page."
                      effect="restart_required"
                      locked={locked}
                    >
                      {locked ? (
                        <strong>
                          {view.secrets.find(
                            (item) => item.field_id === 'connection.' + connection.name + '.key',
                          )?.indicator ?? 'Not configured'}
                        </strong>
                      ) : (
                        <Secret
                          fieldId={'connection.' + connection.name + '.key'}
                          view={view}
                          pending={secrets['connection.' + connection.name + '.key']}
                          onChange={(value) =>
                            setSecret('connection.' + connection.name + '.key', value)
                          }
                        />
                      )}
                    </Row>
                  </div>
                ))
              : null}
            {active === 'providers' ? (
              draft.providers.length === 0 ? (
                <div className="settings-empty">
                  <h3>No metadata providers configured</h3>
                  <p>Target evidence remains available.</p>
                </div>
              ) : (
                draft.providers.map((provider, index) => {
                  const secretId = 'provider.' + provider.name + '.key'
                  return (
                    <div className="settings-record" key={provider.name}>
                      <h3>{provider.name.replaceAll('_', ' ')}</h3>
                      <Row
                        title="Provider state"
                        description={
                          provider.adapter === 'open_library'
                            ? 'The contact email goes only to Open Library.'
                            : 'Enable this source for libraries that reference it.'
                        }
                        effect={effect('providers')}
                        locked={locked}
                      >
                        {locked ? (
                          <strong>{provider.enabled ? 'Enabled' : 'Disabled'}</strong>
                        ) : (
                          <label className="settings-check">
                            <input
                              type="checkbox"
                              checked={provider.enabled}
                              onChange={(event) =>
                                update((value) => {
                                  const item = value.providers.at(index)
                                  if (item) {
                                    item.enabled = event.currentTarget.checked
                                    if (!item.enabled) item.cover_enabled = false
                                  }
                                })
                              }
                            />
                            Enabled
                          </label>
                        )}
                      </Row>
                      <Row
                        title="Cover evidence"
                        description="Allow this provider to supply cover candidates for associated libraries."
                        effect={effect('providers')}
                        locked={locked}
                      >
                        {locked ? (
                          <strong>{provider.cover_enabled ? 'Enabled' : 'Disabled'}</strong>
                        ) : (
                          <label className="settings-check">
                            <input
                              type="checkbox"
                              checked={provider.cover_enabled}
                              disabled={!provider.enabled}
                              onChange={(event) =>
                                update((value) => {
                                  const item = value.providers.at(index)
                                  if (item) item.cover_enabled = event.currentTarget.checked
                                })
                              }
                            />
                            Use for cover evidence
                          </label>
                        )}
                      </Row>
                      {'contact_email' in provider ? (
                        <Row
                          title="Open Library contact email"
                          description="Sent only to Open Library; never shared with this project or retained in reports."
                          effect={effect('providers')}
                          locked={locked}
                        >
                          {locked ? (
                            <span>{provider.contact_email ?? 'Not configured'}</span>
                          ) : (
                            <input
                              type="email"
                              maxLength={254}
                              value={provider.contact_email ?? ''}
                              onChange={(event) =>
                                update((value) => {
                                  const item = value.providers[index]
                                  if (item && 'contact_email' in item)
                                    item.contact_email = event.currentTarget.value || null
                                })
                              }
                            />
                          )}
                        </Row>
                      ) : (
                        <Row
                          title="API key"
                          description="Write-only provider authority. Acquire it from the provider's own account."
                          effect="restart_required"
                          locked={locked}
                        >
                          {locked ? (
                            <strong>
                              {view.secrets.find((item) => item.field_id === secretId)?.indicator ??
                                'Not configured'}
                            </strong>
                          ) : !provider.enabled ? (
                            <strong>Enable this provider before adding a key.</strong>
                          ) : (
                            <Secret
                              fieldId={secretId}
                              view={view}
                              pending={secrets[secretId]}
                              onChange={(value) => setSecret(secretId, value)}
                            />
                          )}
                          <a
                            href={
                              provider.adapter === 'google_books'
                                ? 'https://developers.google.com/books/docs/v1/using'
                                : 'https://hardcover.app/account/api'
                            }
                            target="_blank"
                            rel="noreferrer"
                          >
                            Get a{' '}
                            {provider.adapter === 'google_books' ? 'Google Books' : 'Hardcover'} key
                          </a>
                        </Row>
                      )}
                    </div>
                  )
                })
              )
            ) : null}
            {active === 'libraries' ? (
              <>
                {visibleLibraries.map(({ library, index }) => (
                  <div className="settings-record" key={library.name}>
                    <h3>{library.name.replaceAll('_', ' ')}</h3>
                    <Row
                      title="Library identifier"
                      description="The exact Audiobookshelf library selected for this collection."
                      effect={effect('libraries')}
                      locked={locked}
                    >
                      {locked ? (
                        <span>{library.library_id}</span>
                      ) : (
                        <input
                          maxLength={128}
                          value={library.library_id}
                          onChange={(event) =>
                            update((value) => {
                              const item = value.libraries.at(index)
                              if (item) item.library_id = event.currentTarget.value
                            })
                          }
                        />
                      )}
                    </Row>
                    <Row
                      title="Connection and mount"
                      description="The mount association is deployment-managed and shown for inspection only."
                      effect="deploy_managed"
                      locked={locked}
                    >
                      <span>
                        {library.connection} · {library.mount ?? 'No media mount'}
                      </span>
                    </Row>
                    <Row
                      title="Provider associations"
                      description="Only selected providers contribute evidence for this library."
                      effect={effect('libraries')}
                      locked={locked}
                    >
                      {locked ? (
                        <strong>
                          {library.provider_connections.length
                            ? library.provider_connections.join(', ')
                            : 'Target evidence only'}
                        </strong>
                      ) : (
                        <fieldset className="settings-choice">
                          <legend>Providers for {library.name}</legend>
                          {draft.providers.map((provider) => (
                            <label key={provider.name}>
                              <input
                                type="checkbox"
                                checked={library.provider_connections.includes(provider.name)}
                                onChange={(event) =>
                                  update((value) => {
                                    const item = value.libraries.at(index)
                                    if (!item) return
                                    item.provider_connections = event.currentTarget.checked
                                      ? [...item.provider_connections, provider.name].sort()
                                      : item.provider_connections.filter(
                                          (name) => name !== provider.name,
                                        )
                                  })
                                }
                              />
                              {provider.name.replaceAll('_', ' ')}
                            </label>
                          ))}
                        </fieldset>
                      )}
                    </Row>
                  </div>
                ))}
                {libraryPageCount > 1 ? (
                  <nav className="settings-pagination" aria-label="Library settings pages">
                    <button
                      type="button"
                      disabled={libraryPage === 0}
                      onClick={() => setLibraryPage((value) => Math.max(0, value - 1))}
                    >
                      Previous libraries
                    </button>
                    <span>
                      Page {libraryPage + 1} of {libraryPageCount}
                    </span>
                    <button
                      type="button"
                      disabled={libraryPage + 1 === libraryPageCount}
                      onClick={() =>
                        setLibraryPage((value) => Math.min(libraryPageCount - 1, value + 1))
                      }
                    >
                      Next libraries
                    </button>
                  </nav>
                ) : null}
              </>
            ) : null}
            {active === 'automation' ? (
              <>
                <Row
                  title="Request timeout"
                  description="From 1 to 300 seconds; applies to later target and provider work."
                  effect={effect('limits')}
                  locked={locked}
                >
                  {locked ? (
                    <strong>{String(draft.limits.timeout_seconds)} seconds</strong>
                  ) : (
                    <input
                      type="number"
                      min={1}
                      max={300}
                      value={draft.limits.timeout_seconds}
                      onChange={(event) =>
                        update((value) => {
                          value.limits.timeout_seconds = Number(event.currentTarget.value)
                        })
                      }
                    />
                  )}
                </Row>
                <details className="settings-advanced">
                  <summary>Advanced response and archive limits</summary>
                  {(
                    [
                      ['target_response_bytes', 'Target response limit', 4_194_304],
                      ['provider_response_bytes', 'Provider response limit', 262_144],
                      ['source_archive_bytes', 'Source archive limit', 268_435_456],
                    ] as const
                  ).map(([key, label, maximum]) => (
                    <Row
                      key={key}
                      title={label}
                      description={`From 1,024 to ${maximum.toLocaleString()} bytes; applies after restart.`}
                      effect={effect('limits')}
                      locked={locked}
                    >
                      {locked ? (
                        <strong>{String(draft.limits[key])} bytes</strong>
                      ) : (
                        <input
                          type="number"
                          min={1024}
                          max={maximum}
                          value={draft.limits[key]}
                          onChange={(event) =>
                            update((value) => {
                              value.limits[key] = Number(event.currentTarget.value)
                            })
                          }
                        />
                      )}
                    </Row>
                  ))}
                </details>
                <Row
                  title="Provider recovery generation"
                  description="Increment after correcting provider configuration or credentials."
                  effect={effect('provider_recovery_generation')}
                  locked={locked}
                >
                  {locked ? (
                    <strong>{String(draft.provider_recovery_generation)}</strong>
                  ) : (
                    <input
                      type="number"
                      min={1}
                      value={draft.provider_recovery_generation}
                      onChange={(event) =>
                        update((value) => {
                          value.provider_recovery_generation = Number(event.currentTarget.value)
                        })
                      }
                    />
                  )}
                </Row>
              </>
            ) : null}
            {active === 'reviewer' ? (
              <Row
                title="Contained reviewer"
                description="The credential and private profile path remain deployment-side and never enter the browser."
                effect={effect('server.reviewer_enabled')}
                locked={locked}
              >
                {locked ? (
                  <strong>{draft.server.reviewer_enabled ? 'Enabled' : 'Disabled'}</strong>
                ) : (
                  <label className="settings-check">
                    <input
                      type="checkbox"
                      checked={draft.server.reviewer_enabled}
                      onChange={(event) =>
                        update((value) => {
                          value.server.reviewer_enabled = event.currentTarget.checked
                        })
                      }
                    />
                    Use contained review for eligible cover work
                  </label>
                )}
              </Row>
            ) : null}
            {active === 'server' ? (
              <>
                {(
                  [
                    ['session_lifetime_seconds', 'Owner session lifetime'],
                    ['library_polling_interval_seconds', 'Library polling interval'],
                    ['graceful_shutdown_seconds', 'Graceful shutdown budget'],
                  ] as const
                ).map(([key, label]) => (
                  <Row
                    key={key}
                    title={label}
                    description="A bounded server behavior applied when the process next starts."
                    effect={effect('server.' + key)}
                    locked={locked}
                  >
                    {locked ? (
                      <strong>{String(draft.server[key])} seconds</strong>
                    ) : (
                      <input
                        type="number"
                        min={
                          key === 'session_lifetime_seconds'
                            ? 300
                            : key === 'library_polling_interval_seconds'
                              ? 60
                              : 1
                        }
                        max={key === 'graceful_shutdown_seconds' ? 300 : 86_400}
                        value={draft.server[key]}
                        onChange={(event) =>
                          update((value) => {
                            value.server[key] = Number(event.currentTarget.value)
                          })
                        }
                      />
                    )}
                  </Row>
                ))}
                <Row
                  title="Diagnostic timings"
                  description="Record bounded stage timings for support diagnostics; values apply after restart."
                  effect={effect('server.diagnostic_timings_enabled')}
                  locked={locked}
                >
                  {locked ? (
                    <strong>
                      {draft.server.diagnostic_timings_enabled ? 'Enabled' : 'Disabled'}
                    </strong>
                  ) : (
                    <label className="settings-check">
                      <input
                        type="checkbox"
                        checked={draft.server.diagnostic_timings_enabled}
                        onChange={(event) =>
                          update((value) => {
                            value.server.diagnostic_timings_enabled = event.currentTarget.checked
                          })
                        }
                      />
                      Record diagnostic timings
                    </label>
                  )}
                </Row>
              </>
            ) : null}
            {active === 'appearance' ? (
              <Row
                title="Motion"
                description="Reduced when either this setting or the operating system requests it."
                effect={effect('reduced_motion')}
                locked={locked}
              >
                {locked ? (
                  <strong>
                    {draft.reduced_motion === 'always_reduced' ? 'Always reduced' : 'Follow system'}
                  </strong>
                ) : (
                  <fieldset className="settings-choice">
                    <legend>Motion preference</legend>
                    <label>
                      <input
                        type="radio"
                        name="reduced-motion"
                        checked={draft.reduced_motion === 'follow_system'}
                        onChange={() =>
                          update((value) => {
                            value.reduced_motion = 'follow_system'
                          })
                        }
                      />
                      Follow system
                    </label>
                    <label>
                      <input
                        type="radio"
                        name="reduced-motion"
                        checked={draft.reduced_motion === 'always_reduced'}
                        onChange={() =>
                          update((value) => {
                            value.reduced_motion = 'always_reduced'
                          })
                        }
                      />
                      Always reduced
                    </label>
                  </fieldset>
                )}
              </Row>
            ) : null}
          </div>
        </div>
      </div>
      {!locked ? (
        <footer className="settings-save-tray">
          <p
            ref={statusTarget}
            tabIndex={-1}
            role={status === 'failed' || errors.length ? 'alert' : 'status'}
            aria-live="polite"
          >
            {mutation.isPending
              ? 'Saving settings…'
              : status === 'saved'
                ? 'Saved. Server-authoritative settings are current.'
                : status === 'failed'
                  ? 'Nothing changed. Correct the values or try again.'
                  : (errors[0] ??
                    (changed ? 'Unsaved settings changes.' : 'Settings match the server.'))}
          </p>
          <button
            type="button"
            disabled={!changed || mutation.isPending || errors.length > 0}
            onClick={() =>
              mutation.mutate({
                expected_revision: view.document.revision,
                document: { ...draft, revision: view.document.revision + 1 },
                secrets: Object.values(secrets).filter(
                  (item) => item.operation === 'clear' || (item.value?.length ?? 0) > 0,
                ),
              })
            }
          >
            {mutation.isPending ? 'Saving…' : 'Save settings'}
          </button>
        </footer>
      ) : null}
    </section>
  )
}
