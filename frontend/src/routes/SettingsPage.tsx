import { useMutation, useQuery } from '@tanstack/react-query'
import { useLayoutEffect, useMemo, useRef, useState } from 'react'
import type { ComponentPropsWithoutRef, ReactNode } from 'react'

import {
  AmbiguousMutationError,
  ApiError,
  settingsEndpointValid,
  settingsIntegerValid,
  settingsOpenLibraryEmailValid,
  settingsProviderKeyRequired,
  settingsSecretValid,
  settingsTextValid,
  settingsValidationCopy,
} from '../api/http'
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
interface FieldIssue {
  fieldId: string
  message: string
}

const groupById: Readonly<Record<Group, { id: Group; label: string; description: string }>> = {
  connection: { id: 'connection', label: 'Connection', description: 'Your Audiobookshelf service' },
  providers: { id: 'providers', label: 'Providers', description: 'Metadata sources' },
  libraries: {
    id: 'libraries',
    label: 'Libraries',
    description: 'Included collections and mounts',
  },
  automation: {
    id: 'automation',
    label: 'Automation & limits',
    description: 'Background work and limits',
  },
  reviewer: { id: 'reviewer', label: 'Reviewer', description: 'AI reviewer' },
  server: { id: 'server', label: 'Server', description: 'Sessions, polling and shutdown' },
  appearance: {
    id: 'appearance',
    label: 'Appearance',
    description: 'Motion preferences',
  },
}
const groups = Object.values(groupById)
const isGroup = (value: string): value is Group => Object.hasOwn(groupById, value)

const clone = (value: SettingsView['document'] | Document): Document =>
  structuredClone(value) as Document
const effectCopy = (effect: string) =>
  effect === 'deploy_managed' ? 'Managed by your deployment' : null

const validationErrors = (
  document: Document,
  secrets: Record<string, SecretWrite>,
  view: SettingsView,
) => {
  const errors: FieldIssue[] = []
  const ranges = [
    [
      'limits.target_response_bytes',
      document.limits.target_response_bytes,
      1024,
      4_194_304,
      'Target response limit',
    ],
    [
      'limits.provider_response_bytes',
      document.limits.provider_response_bytes,
      1024,
      262_144,
      'Provider response limit',
    ],
    [
      'limits.source_archive_bytes',
      document.limits.source_archive_bytes,
      1024,
      268_435_456,
      'Source archive limit',
    ],
    ['limits.timeout_seconds', document.limits.timeout_seconds, 1, 60, 'Request timeout'],
    [
      'server.session_lifetime_seconds',
      document.server.session_lifetime_seconds,
      300,
      2_592_000,
      'Fixed owner session lifetime',
    ],
    [
      'server.library_polling_interval_seconds',
      document.server.library_polling_interval_seconds,
      60,
      86_400,
      'Polling interval',
    ],
    [
      'server.graceful_shutdown_seconds',
      document.server.graceful_shutdown_seconds,
      1,
      300,
      'Shutdown budget',
    ],
  ] as const
  for (const [fieldId, value, minimum, maximum, label] of ranges)
    if (!settingsIntegerValid(value, minimum, maximum))
      errors.push({
        fieldId,
        message: `${label} must be from ${minimum.toLocaleString()} to ${maximum.toLocaleString()}.`,
      })
  if (!settingsIntegerValid(document.provider_recovery_generation, 1))
    errors.push({
      fieldId: 'provider_recovery_generation',
      message: 'Provider recovery generation must be a whole number of at least 1.',
    })
  for (const entry of Object.values(secrets))
    if (entry.operation !== 'clear' && (!entry.value || !settingsSecretValid(entry.value)))
      errors.push({
        fieldId: entry.field_id,
        message: settingsValidationCopy.secret,
      })
  for (const connection of document.connections) {
    if (!settingsEndpointValid(connection.endpoint))
      errors.push({
        fieldId: `connection.${connection.name}.endpoint`,
        message: `${connection.name} endpoint must contain 1–2,048 characters.`,
      })
    const fieldId = `connection.${connection.name}.key`
    const existing = view.secrets.find((item) => item.field_id === fieldId)?.configured === true
    const pending = secrets[fieldId]
    if ((!existing && !pending?.value) || pending?.operation === 'clear')
      errors.push({ fieldId, message: `${connection.name} requires an access key.` })
  }
  for (const provider of document.providers) {
    if (provider.adapter === 'open_library' && provider.enabled) {
      const fieldId = `provider.${provider.name}.contact_email`
      if (!provider.contact_email)
        errors.push({ fieldId, message: `${provider.name} needs a contact email while enabled.` })
      else if (!settingsOpenLibraryEmailValid(provider.contact_email))
        errors.push({
          fieldId,
          message: settingsValidationCopy.open_library_email,
        })
    }
    if (settingsProviderKeyRequired(provider.adapter) && provider.enabled) {
      const fieldId = `provider.${provider.name}.key`
      const existing = view.secrets.find((item) => item.field_id === fieldId)?.configured === true
      const pending = secrets[fieldId]
      if ((!existing && !pending?.value) || pending?.operation === 'clear')
        errors.push({ fieldId, message: settingsValidationCopy.provider_key })
    }
  }
  for (const library of document.libraries)
    if (!settingsTextValid(library.library_id, 1, 128))
      errors.push({
        fieldId: `library.${library.name}.library_id`,
        message: `${library.name} identifier must contain 1–128 characters.`,
      })
  return errors
}

const controlId = (fieldId: string) => `settings-${fieldId.replaceAll('.', '-')}`
const descriptionId = (fieldId: string) => `${controlId(fieldId)}-description`
const errorId = (fieldId: string) => `${controlId(fieldId)}-error`

const FieldInput = ({
  fieldId,
  error,
  ...properties
}: ComponentPropsWithoutRef<'input'> & { fieldId: string; error?: string | undefined }) => (
  <input
    {...properties}
    id={controlId(fieldId)}
    aria-invalid={error ? true : undefined}
    aria-describedby={`${descriptionId(fieldId)}${error ? ` ${errorId(fieldId)}` : ''}`}
  />
)

const Row = ({
  title,
  description,
  effect,
  locked,
  fieldId,
  error,
  group = false,
  children,
}: {
  title: string
  description: string
  effect: string
  locked: boolean
  fieldId?: string | undefined
  error?: string | undefined
  group?: boolean
  children: ReactNode
}) => (
  <div className="settings-field-row">
    <div>
      {effectCopy(effect) ? <span className="settings-effect">{effectCopy(effect)}</span> : null}
      {fieldId && !locked && !group ? (
        <label htmlFor={controlId(fieldId)}>
          <strong>{title}</strong>
        </label>
      ) : (
        <strong>{title}</strong>
      )}
      <p id={fieldId ? descriptionId(fieldId) : undefined}>{description}</p>
    </div>
    <div className="settings-field-control">
      {locked ? <span className="settings-lock">Locked</span> : null}
      {children}
      {fieldId && error ? (
        <p className="settings-field-error" id={errorId(fieldId)}>
          {error}
        </p>
      ) : null}
    </div>
  </div>
)

const Secret = ({
  fieldId,
  label,
  error,
  view,
  pending,
  onChange,
}: {
  fieldId: string
  label: string
  error?: string | undefined
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
        <button
          type="button"
          id={controlId(fieldId)}
          aria-invalid={error ? true : undefined}
          aria-describedby={`${descriptionId(fieldId)}${error ? ` ${errorId(fieldId)}` : ''}`}
          onClick={() => onChange(undefined)}
        >
          Keep configured {label}
        </button>
      </div>
    )
  if (!editing && presence?.configured)
    return (
      <div className="settings-secret-state">
        <strong>{presence.indicator ?? 'Configured'}</strong>
        {confirmingClear ? (
          <div role="alert" className="settings-clear-confirmation">
            <span>Confirm removal. This integration will not be used for later work.</span>
            <button
              type="button"
              onClick={() => {
                onChange({ field_id: fieldId, operation: 'clear' })
                setConfirmingClear(false)
              }}
            >
              Confirm clear {label}
            </button>
            <button type="button" onClick={() => setConfirmingClear(false)}>
              Keep {label}
            </button>
          </div>
        ) : (
          <span>
            <button type="button" onClick={() => setEditing(true)}>
              Replace {label}
            </button>
            <button type="button" onClick={() => setConfirmingClear(true)}>
              Clear {label}
            </button>
          </span>
        )}
      </div>
    )
  return (
    <div className="settings-secret-editor">
      <FieldInput
        fieldId={fieldId}
        error={error}
        type="password"
        autoComplete="new-password"
        maxLength={2048}
        aria-label={presence?.configured ? `Replacement ${label}` : `New ${label}`}
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
          Cancel replacement {label}
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
  const [reconciliation, setReconciliation] = useState<'idle' | 'checking' | 'failed' | 'checked'>(
    'idle',
  )
  const [recoveryGeneration, setRecoveryGeneration] = useState(0)
  const staleRecoveryTarget = useRef<HTMLParagraphElement>(null)
  const reconciliationTarget = useRef<HTMLDivElement>(null)
  useLayoutEffect(() => {
    const target = staleRecovery ? staleRecoveryTarget.current : reconciliationTarget.current
    target?.focus()
  }, [reconciliation, staleRecovery])
  const reconcileUncertainSave = async () => {
    setSaved(false)
    setStaleRecovery(false)
    setReconciliation('checking')
    const result = await query.refetch()
    if (result.isSuccess) {
      setRecoveryGeneration((value) => value + 1)
      setReconciliation('checked')
    } else setReconciliation('failed')
  }
  if (query.isPending)
    return (
      <section className="settings-state" aria-busy="true" data-route-focus tabIndex={-1}>
        <h1>Loading settings…</h1>
        <p>Reading saved settings.</p>
      </section>
    )
  if (query.isError && query.data === undefined)
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
        <p className="settings-stale-recovery" role="alert" tabIndex={-1} ref={staleRecoveryTarget}>
          Settings changed elsewhere. The latest saved values replaced this draft; review them
          before saving again.
        </p>
      ) : null}
      {saved ? (
        <p className="settings-saved" role="status">
          Settings saved and in use.
        </p>
      ) : null}
      {reconciliation !== 'idle' ? (
        <div
          className="settings-stale-recovery"
          role="alert"
          tabIndex={-1}
          ref={reconciliationTarget}
        >
          {reconciliation === 'checking' ? (
            <p>Save outcome is uncertain. Reloading saved settings before another save.</p>
          ) : reconciliation === 'failed' ? (
            <>
              <p>
                Save outcome is uncertain and Settings could not be reloaded. Do not save again
                until the current settings are available.
              </p>
              <button type="button" onClick={() => void reconcileUncertainSave()}>
                Retry settings reload
              </button>
            </>
          ) : (
            <p>Current settings reloaded. Review the values before saving again.</p>
          )}
        </div>
      ) : null}
      <SettingsEditor
        key={`${query.data.authority}:${query.data.document.revision.toString()}:${recoveryGeneration.toString()}`}
        view={query.data}
        saveBlocked={reconciliation === 'checking' || reconciliation === 'failed'}
        onSaved={(value) => {
          queryClient.setQueryData(['settings'], value)
          queryClient.setQueryData(['appearance'], {
            reduced_motion: value.document.reduced_motion,
          })
          setStaleRecovery(false)
          setReconciliation('idle')
          setSaved(true)
        }}
        onStale={() => {
          setStaleRecovery(true)
          setSaved(false)
          void query.refetch()
        }}
        onUncertain={() => void reconcileUncertainSave()}
      />
    </>
  )
}

const SettingsEditor = ({
  view,
  saveBlocked,
  onSaved,
  onStale,
  onUncertain,
}: {
  view: SettingsView
  saveBlocked: boolean
  onSaved: (value: SettingsView) => void
  onStale: () => void
  onUncertain: () => void
}) => {
  const { http } = useReviewContext()
  const { group: active } = settingsRoute.useSearch()
  const navigate = settingsRoute.useNavigate()
  const [draft, setDraft] = useState<Document>(() => clone(view.document))
  const [secrets, setSecrets] = useState<Record<string, SecretWrite>>({})
  const [status, setStatus] = useState<'idle' | 'saved' | 'failed' | 'uncertain'>('idle')
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
      else if (
        error instanceof AmbiguousMutationError ||
        (error instanceof ApiError && (error.status === 0 || error.status >= 500))
      ) {
        setStatus('uncertain')
        onUncertain()
      } else {
        setStatus('failed')
        requestAnimationFrame(() => statusTarget.current?.focus())
      }
    },
  })
  const errors = useMemo(() => validationErrors(draft, secrets, view), [draft, secrets, view])
  const fieldError = (fieldId: string) => errors.find((item) => item.fieldId === fieldId)?.message
  const changed = useMemo(
    () =>
      JSON.stringify(view.document) !== JSON.stringify(draft) || Object.keys(secrets).length > 0,
    [draft, secrets, view.document],
  )
  const libraryPageCount = Math.max(1, Math.ceil(draft.libraries.length / 20))
  const visibleLibraries = draft.libraries
    .map((library, index) => ({ library, index }))
    .slice(libraryPage * 20, libraryPage * 20 + 20)
  const availableProviderCapabilities = view.provider_capabilities.filter(
    (capability) => !draft.providers.some((provider) => provider.adapter === capability.adapter),
  )
  const locked = view.authority === 'file'
  const effect = (fieldId: string) =>
    view.fields.find((item) => item.field_id === fieldId)?.apply_effect ?? 'applied_live'
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
  const addProvider = (capability: SettingsView['provider_capabilities'][number]) => {
    setDraft((current) => {
      if (
        current.providers.some(
          (provider) =>
            provider.adapter === capability.adapter || provider.name === capability.name,
        )
      )
        return current
      const value = clone(current)
      const provider =
        capability.adapter === 'open_library'
          ? {
              adapter: 'open_library' as const,
              contact_email: null,
              cover_enabled: false,
              enabled: false,
              name: capability.name,
            }
          : capability.adapter === 'hardcover'
            ? {
                adapter: 'hardcover' as const,
                cover_enabled: false,
                enabled: false,
                name: capability.name,
              }
            : {
                adapter: 'google_books' as const,
                cover_enabled: false as const,
                enabled: false,
                name: capability.name,
              }
      value.providers.push(provider)
      value.providers.sort((left, right) => left.name.localeCompare(right.name))
      return value
    })
    setStatus('idle')
  }

  return (
    <section className="settings-route" data-route-focus tabIndex={-1}>
      <header className="settings-heading">
        <div>
          <h1>Settings</h1>
        </div>
      </header>
      {locked ? (
        <section className="settings-file-banner" role="status">
          <strong>Managed by /data/config.json</strong>
          <p>
            These values are read-only while this deployment is managed by its configuration file.
          </p>
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
                      fieldId={`connection.${connection.name}.endpoint`}
                      error={fieldError(`connection.${connection.name}.endpoint`)}
                      title="Audiobookshelf endpoint"
                      description="The exact service origin used for catalogue and metadata work."
                      effect={effect('connections')}
                      locked={locked}
                    >
                      {locked ? (
                        <span>{connection.endpoint}</span>
                      ) : (
                        <FieldInput
                          fieldId={`connection.${connection.name}.endpoint`}
                          error={fieldError(`connection.${connection.name}.endpoint`)}
                          type="text"
                          inputMode="url"
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
                      fieldId={`connection.${connection.name}.key`}
                      error={fieldError(`connection.${connection.name}.key`)}
                      group={!locked}
                      title="Access key"
                      description="Used to connect to Audiobookshelf. It is never shown again on this page."
                      effect={effect('connections')}
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
                          label={`${connection.name} access key`}
                          error={fieldError('connection.' + connection.name + '.key')}
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
              <>
                {draft.providers.length === 0 ? (
                  <div className="settings-empty">
                    <h3>Choose metadata providers</h3>
                    <p>
                      More configured sources generally give review work better evidence. They
                      remain optional.
                    </p>
                  </div>
                ) : null}
                {draft.providers.map((provider, index) => {
                  const secretId = 'provider.' + provider.name + '.key'
                  return (
                    <div className="settings-record" key={provider.name}>
                      <h3>{provider.name.replaceAll('_', ' ')}</h3>
                      <Row
                        fieldId={`provider.${provider.name}.enabled`}
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
                            <FieldInput
                              fieldId={`provider.${provider.name}.enabled`}
                              type="checkbox"
                              checked={provider.enabled}
                              onChange={(event) =>
                                update((value) => {
                                  const item = value.providers.at(index)
                                  if (item) {
                                    item.enabled = event.currentTarget.checked
                                    if (!item.enabled) {
                                      item.cover_enabled = false
                                      for (const library of value.libraries)
                                        library.provider_connections =
                                          library.provider_connections.filter(
                                            (name) => name !== item.name,
                                          )
                                    }
                                  }
                                })
                              }
                            />
                            Enable {provider.name.replaceAll('_', ' ')}
                          </label>
                        )}
                      </Row>
                      {'contact_email' in provider ? (
                        <Row
                          fieldId={`provider.${provider.name}.contact_email`}
                          error={fieldError(`provider.${provider.name}.contact_email`)}
                          title="Open Library contact email"
                          description="Sent only to Open Library; never shared with this project or retained in reports."
                          effect={effect('providers')}
                          locked={locked}
                        >
                          {locked ? (
                            <span>{provider.contact_email ?? 'Not configured'}</span>
                          ) : (
                            <FieldInput
                              fieldId={`provider.${provider.name}.contact_email`}
                              error={fieldError(`provider.${provider.name}.contact_email`)}
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
                          fieldId={secretId}
                          error={fieldError(secretId)}
                          group={!locked}
                          title="API key"
                          description="Used to connect to this provider. Get it from your provider account."
                          effect={effect('providers')}
                          locked={locked}
                        >
                          {locked ? (
                            <strong>
                              {view.secrets.find((item) => item.field_id === secretId)?.indicator ??
                                'Not configured'}
                            </strong>
                          ) : (
                            <Secret
                              fieldId={secretId}
                              label={`${provider.name.replaceAll('_', ' ')} API key`}
                              error={fieldError(secretId)}
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
                })}
                {availableProviderCapabilities.length ? (
                  <section
                    className="settings-capabilities"
                    aria-label="Available metadata providers"
                  >
                    <h3>Add a metadata provider</h3>
                    <p>
                      Each provider is optional. Add it here, then enable it when its details are
                      ready.
                    </p>
                    <div>
                      {availableProviderCapabilities.map((capability) => (
                        <button
                          type="button"
                          key={capability.name}
                          disabled={locked}
                          onClick={() => addProvider(capability)}
                        >
                          Add {capability.label}
                        </button>
                      ))}
                    </div>
                  </section>
                ) : null}
              </>
            ) : null}
            {active === 'libraries' ? (
              <>
                {visibleLibraries.map(({ library, index }) => (
                  <div className="settings-record" key={library.name}>
                    <h3>{library.display_name ?? library.name}</h3>
                    <p className="settings-record-meta">
                      Local identifier: <code>{library.name}</code>
                      <span aria-hidden="true"> · </span>
                      Audiobookshelf identifier: <code>{library.library_id}</code>
                    </p>
                    <Row
                      fieldId={`library.${library.name}.library_id`}
                      error={fieldError(`library.${library.name}.library_id`)}
                      title="Library identifier"
                      description="The exact Audiobookshelf library selected for this collection."
                      effect={effect('libraries')}
                      locked={locked}
                    >
                      {locked ? (
                        <span>{library.library_id}</span>
                      ) : (
                        <FieldInput
                          fieldId={`library.${library.name}.library_id`}
                          error={fieldError(`library.${library.name}.library_id`)}
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
                      fieldId={`library.${library.name}.provider_connections`}
                      group
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
                      ) : draft.providers.some((provider) => provider.enabled) ? (
                        <fieldset
                          className="settings-choice"
                          id={controlId(`library.${library.name}.provider_connections`)}
                          aria-describedby={descriptionId(
                            `library.${library.name}.provider_connections`,
                          )}
                        >
                          <legend>Providers for {library.display_name ?? library.name}</legend>
                          {draft.providers
                            .filter((provider) => provider.enabled)
                            .map((provider) => (
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
                      ) : (
                        <p className="settings-empty-associations">
                          No enabled metadata providers are available to associate.
                        </p>
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
                  fieldId="limits.timeout_seconds"
                  error={fieldError('limits.timeout_seconds')}
                  title="Request timeout"
                  description="From 1 to 60 seconds; applies to later target and provider work."
                  effect={effect('limits')}
                  locked={locked}
                >
                  {locked ? (
                    <strong>{String(draft.limits.timeout_seconds)} seconds</strong>
                  ) : (
                    <FieldInput
                      fieldId="limits.timeout_seconds"
                      error={fieldError('limits.timeout_seconds')}
                      type="number"
                      min={1}
                      max={60}
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
                      fieldId={`limits.${key}`}
                      error={fieldError(`limits.${key}`)}
                      title={label}
                      description={`From 1,024 to ${maximum.toLocaleString()} bytes; applies to the next operation.`}
                      effect={effect('limits')}
                      locked={locked}
                    >
                      {locked ? (
                        <strong>{String(draft.limits[key])} bytes</strong>
                      ) : (
                        <FieldInput
                          fieldId={`limits.${key}`}
                          error={fieldError(`limits.${key}`)}
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
                  fieldId="provider_recovery_generation"
                  error={fieldError('provider_recovery_generation')}
                  title="Provider recovery generation"
                  description="Increment after correcting provider configuration or credentials."
                  effect={effect('provider_recovery_generation')}
                  locked={locked}
                >
                  {locked ? (
                    <strong>{String(draft.provider_recovery_generation)}</strong>
                  ) : (
                    <FieldInput
                      fieldId="provider_recovery_generation"
                      error={fieldError('provider_recovery_generation')}
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
                fieldId="server.reviewer_enabled"
                title="AI reviewer"
                description="The sign-in details and private profile stay on the server and never enter the browser."
                effect={effect('server.reviewer_enabled')}
                locked={locked}
              >
                {locked ? (
                  <strong>{draft.server.reviewer_enabled ? 'Enabled' : 'Disabled'}</strong>
                ) : (
                  <label className="settings-check">
                    <FieldInput
                      fieldId="server.reviewer_enabled"
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
                    fieldId={`server.${key}`}
                    error={fieldError(`server.${key}`)}
                    title={label}
                    description="Controls the next applicable server operation."
                    effect={effect('server.' + key)}
                    locked={locked}
                  >
                    {locked ? (
                      <strong>{String(draft.server[key])} seconds</strong>
                    ) : (
                      <FieldInput
                        fieldId={`server.${key}`}
                        error={fieldError(`server.${key}`)}
                        type="number"
                        min={
                          key === 'session_lifetime_seconds'
                            ? 300
                            : key === 'library_polling_interval_seconds'
                              ? 60
                              : 1
                        }
                        max={
                          key === 'session_lifetime_seconds'
                            ? 2_592_000
                            : key === 'graceful_shutdown_seconds'
                              ? 300
                              : 86_400
                        }
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
                  fieldId="server.diagnostic_timings_enabled"
                  title="Diagnostic timings"
                  description="Record stage timings for support diagnostics."
                  effect={effect('server.diagnostic_timings_enabled')}
                  locked={locked}
                >
                  {locked ? (
                    <strong>
                      {draft.server.diagnostic_timings_enabled ? 'Enabled' : 'Disabled'}
                    </strong>
                  ) : (
                    <label className="settings-check">
                      <FieldInput
                        fieldId="server.diagnostic_timings_enabled"
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
                fieldId="reduced_motion"
                group
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
                  <fieldset
                    className="settings-choice"
                    id={controlId('reduced_motion')}
                    aria-describedby={descriptionId('reduced_motion')}
                  >
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
        <footer
          className={`settings-save-tray${!changed && status === 'idle' && !errors.length ? ' quiet' : ''}`}
        >
          {changed || status !== 'idle' || errors.length ? (
            <p
              ref={statusTarget}
              tabIndex={-1}
              role={
                status === 'failed' || status === 'uncertain' || errors.length ? 'alert' : 'status'
              }
              aria-live="polite"
            >
              {mutation.isPending
                ? 'Saving settings…'
                : status === 'saved'
                  ? 'Settings saved and in use.'
                  : status === 'uncertain'
                    ? 'Save outcome uncertain. Waiting for current settings.'
                    : status === 'failed'
                      ? 'The server rejected these values. Nothing was saved.'
                      : (errors[0]?.message ?? (changed ? 'Unsaved settings changes.' : ''))}
            </p>
          ) : null}
          <button
            type="button"
            disabled={!changed || mutation.isPending || saveBlocked || errors.length > 0}
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
