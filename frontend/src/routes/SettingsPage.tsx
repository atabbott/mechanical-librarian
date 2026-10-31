import { queryWorkClass, useRequiredQuery as useQuery } from '../queryWork'
import { useMutation } from '@tanstack/react-query'
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import type { ReactNode, RefObject } from 'react'

import {
  AmbiguousMutationError,
  ApiError,
  refusalCopy,
  settingsAudibleMarketplaceOrder,
  settingsEndpointValid,
  settingsIntegerValid,
  settingsOpenLibraryEmailValid,
  settingsProviderKeyRequired,
  settingsProviderKeyless,
  settingsSecretValid,
  settingsTextValid,
  settingsValidationCopy,
} from '../api/http'
import type { SettingsView, SettingsWrite } from '../api/http'
import settingsVocabulary from '../../../src/mechanical_librarian/interfaces/web/settings_vocabulary.json'
import { Button } from '../components/Button'
import { ChangeCount } from '../components/ChangeCount'
import { Disclosure } from '../components/Disclosure'
import { Dropdown } from '../components/Dropdown'
import { FieldInput, FieldRow } from '../components/FieldRow'
import { FinishTray } from '../components/FinishTray'
import { ReviewerAccessPanel } from '../components/ReviewerAccessPanel'
import { SegmentedControl } from '../components/SegmentedControl'
import { controlId, descriptionId, errorId, offOn, unitId } from '../fieldControls'
import { ProviderThrottleNotice, settingsRoute, useReviewContext } from '../router'
import type { SettingsGroup } from '../router'

type Mutable<T> = T extends readonly (infer Item)[]
  ? Mutable<Item>[]
  : T extends object
    ? { -readonly [Key in keyof T]: Mutable<T[Key]> }
    : T
type Document = Mutable<SettingsView['document']>
type AudibleMarketplace = Extract<
  Document['providers'][number],
  { adapter: 'audible_catalog' }
>['marketplaces'][number]
type Provider = Document['providers'][number]
// Only the two adapters that state a cover asset of their own carry the
// switch. The JSON list is the one place that set is written; the `Extract`
// below is its compile-time restatement, and a Python test holds the two
// together against the settings contract that accepts the field.
type CoverSwitchProvider = Extract<Provider, { adapter: 'audible_catalog' | 'itunes_search' }>
const coverSwitchAdapters: readonly string[] = settingsVocabulary.cover_switch_provider_adapters
const hasCoverSwitch = (provider: Provider): provider is CoverSwitchProvider =>
  coverSwitchAdapters.includes(provider.adapter)
const providerLabel = (adapter: Provider['adapter']): string =>
  settingsVocabulary.provider_labels[adapter]
type SecretWrite = SettingsWrite['secrets'][number]
type Group = SettingsGroup
interface FieldIssue {
  fieldId: string
  message: string
}
interface Change {
  fieldId: string
  label: string
  before: string
  after: string
}

const sections: readonly { id: Group; label: string; description: string }[] = [
  { id: 'connection', label: 'Audiobookshelf', description: 'Service address and access key' },
  { id: 'libraries', label: 'Libraries', description: 'Collections and their sources' },
  { id: 'providers', label: 'Metadata sources', description: 'Where evidence comes from' },
  { id: 'reviewer', label: 'Reviewer', description: 'Optional AI advice' },
  { id: 'background', label: 'Background work', description: 'Requests, limits and polling' },
  { id: 'access', label: 'Access and diagnostics', description: 'Sign-in length and timings' },
  { id: 'appearance', label: 'Appearance', description: 'Motion' },
]
const sectionLabel = (id: Group) => sections.find((section) => section.id === id)?.label ?? id
const sectionOf = (fieldId: string): Group =>
  fieldId.startsWith('connection.')
    ? 'connection'
    : fieldId.startsWith('provider.')
      ? 'providers'
      : fieldId.startsWith('library.')
        ? 'libraries'
        : fieldId === 'reduced_motion'
          ? 'appearance'
          : ['server.session_lifetime_seconds', 'server.diagnostic_timings_enabled'].includes(
                fieldId,
              )
            ? 'access'
            : 'background'

// Every whole-number setting, in the order each section shows it. One entry
// supplies the row, its range validation and its change-review wording.
interface NumberField {
  fieldId: string
  label: string
  description: string
  minimum: number
  maximum?: number
  /**
   * The unit the owner reads and types. The document keeps whole seconds and
   * bytes; a minute is 60 seconds and an MB is 1,048,576 bytes (#1286).
   */
  unit: 'seconds' | 'minutes' | 'MB'
  advanced?: boolean
}
const unitFactor = { seconds: 1, minutes: 60, MB: 1_048_576 } as const
// The shortest decimal of `value / factor` that reads back to exactly
// `value` whole units, so a shown number is the stored one: 65,536 bytes are
// 0.0625 MB and 128,975 bytes 0.123 MB; a value that is no whole number of
// units reads unrounded.
const exactly = (value: number, factor: number) => {
  for (let digits = 0; digits <= 7; digits += 1) {
    const shown = Number((value / factor).toFixed(digits))
    if (Math.round(shown * factor) === value) return shown
  }
  return value / factor
}
// A stored value in the field's unit, as the field shows and the owner types
// it; what is shown is what is saved (#1286).
const shownNumber = (field: NumberField, value: number) => exactly(value, unitFactor[field.unit])
const grouped = (value: number) => value.toLocaleString(undefined, { maximumFractionDigits: 20 })
// Beside a size, the same size in the unit a person reads without arithmetic:
// KB below one MB, GB from 1,024 MB. A GB figure is read, not typed, so it
// stops at two decimals; the MB figure beside it stays exact (#1286).
const sizeAside = (field: NumberField, value: number) =>
  field.unit !== 'MB' || !Number.isFinite(value) || value <= 0
    ? null
    : value < unitFactor.MB
      ? `${grouped(exactly(value, 1024))} KB`
      : value >= 1024 * unitFactor.MB
        ? `${(value / (1024 * unitFactor.MB)).toLocaleString(undefined, { maximumFractionDigits: 2 })} GB`
        : null
const unitWord = (field: NumberField, shown: number) =>
  field.unit === 'MB' ? 'MB' : shown === 1 ? field.unit.slice(0, -1) : field.unit
const numberFields: readonly NumberField[] = [
  {
    fieldId: 'limits.timeout_seconds',
    label: 'Request timeout',
    description: 'How long one Audiobookshelf or source request may take.',
    minimum: 1,
    maximum: 60,
    unit: 'seconds',
  },
  {
    fieldId: 'server.library_polling_interval_seconds',
    label: 'Library polling interval',
    description: 'How often libraries are checked for new or changed books.',
    minimum: 60,
    maximum: 86_400,
    unit: 'minutes',
  },
  {
    fieldId: 'server.graceful_shutdown_seconds',
    label: 'Shutdown budget',
    description: 'How long running work may finish when the service stops.',
    minimum: 1,
    maximum: 300,
    unit: 'seconds',
  },
  {
    fieldId: 'limits.target_response_bytes',
    label: 'Audiobookshelf response limit',
    description: 'Largest Audiobookshelf response that is read.',
    minimum: 1024,
    maximum: 4_194_304,
    unit: 'MB',
    advanced: true,
  },
  {
    fieldId: 'limits.provider_response_bytes',
    label: 'Source response limit',
    description: 'Largest metadata source response that is read.',
    minimum: 1024,
    maximum: 262_144,
    unit: 'MB',
    advanced: true,
  },
  {
    fieldId: 'limits.source_archive_bytes',
    label: 'Source archive limit',
    description: 'Largest ebook file opened for evidence.',
    minimum: 1024,
    maximum: 268_435_456,
    unit: 'MB',
    advanced: true,
  },
  {
    // An audiobook is read by header rather than whole and is routinely larger
    // than any EPUB, so it carries its own ceiling rather than the archive one.
    fieldId: 'limits.audio_source_bytes',
    label: 'Audiobook source limit',
    description: 'Largest audiobook file whose header is read.',
    minimum: 1024,
    maximum: 17_179_869_184,
    unit: 'MB',
    advanced: true,
  },
  {
    fieldId: 'server.session_lifetime_seconds',
    label: 'Owner session lifetime',
    description: 'How long a sign-in lasts before you sign in again.',
    minimum: 300,
    maximum: 2_592_000,
    unit: 'minutes',
  },
]
const readNumber = (document: Document, fieldId: string) =>
  fieldId
    .split('.')
    .reduce<unknown>((value, key) => (value as Record<string, unknown>)[key], document) as number
const writeNumber = (document: Document, fieldId: string, value: number) => {
  const keys = fieldId.split('.')
  const parent = keys
    .slice(0, -1)
    .reduce<Record<string, unknown>>(
      (item, key) => item[key] as Record<string, unknown>,
      document as unknown as Record<string, unknown>,
    )
  parent[keys.at(-1) ?? fieldId] = value
}
// A value in words, in the field's own unit, with a size's KB or GB beside it:
// `0.0625 MB (64 KB)`.
const numberWords = (field: NumberField, value: number) => {
  if (!Number.isFinite(value)) return 'Not a number'
  const shown = shownNumber(field, value)
  const aside = sizeAside(field, value)
  return `${grouped(shown)} ${unitWord(field, shown)}${aside === null ? '' : ` (${aside})`}`
}
const onOffWord = (value: boolean) => (value ? 'On' : 'Off')
const motionWord = (value: Document['reduced_motion']) =>
  value === 'always_reduced' ? 'Always reduced' : 'Follow system'

const clone = (value: SettingsView['document'] | Document): Document =>
  structuredClone(value) as Document

const validationErrors = (
  document: Document,
  secrets: Record<string, SecretWrite>,
  view: SettingsView,
) => {
  const errors: FieldIssue[] = []
  for (const field of numberFields)
    if (!settingsIntegerValid(readNumber(document, field.fieldId), field.minimum, field.maximum))
      errors.push({
        fieldId: field.fieldId,
        message:
          field.maximum === undefined
            ? `${field.label} must be at least ${numberWords(field, field.minimum)}.`
            : `${field.label} must be from ${field.unit === 'MB' ? numberWords(field, field.minimum) : grouped(shownNumber(field, field.minimum))} to ${numberWords(field, field.maximum)}.`,
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
  for (const entry of Object.values(secrets))
    if (entry.operation !== 'clear' && (!entry.value || !settingsSecretValid(entry.value)))
      errors.push({
        fieldId: entry.field_id,
        message: settingsValidationCopy.secret,
      })
  return errors.sort(
    (left, right) =>
      sections.findIndex((section) => section.id === sectionOf(left.fieldId)) -
      sections.findIndex((section) => section.id === sectionOf(right.fieldId)),
  )
}

// What Save would change, as the owner reads it. Secret values never appear:
// only whether a key will be set, replaced or cleared. A library's sources can
// change indirectly when a source is turned off, so that is named too.
const describeChanges = (
  saved: Document,
  draft: Document,
  secrets: Record<string, SecretWrite>,
  view: SettingsView,
): Change[] => {
  const changes: Change[] = []
  const add = (fieldId: string, label: string, before: string, after: string) => {
    if (before !== after) changes.push({ fieldId, label, before, after })
  }
  const sourceName = (name: string) => {
    const provider = draft.providers.find((item) => item.name === name)
    return provider ? providerLabel(provider.adapter) : name.replaceAll('_', ' ')
  }
  const sourceList = (names: readonly string[]) =>
    names.length ? names.map(sourceName).join(', ') : 'No sources'
  for (const connection of draft.connections)
    add(
      `connection.${connection.name}.endpoint`,
      'Audiobookshelf endpoint',
      saved.connections.find((item) => item.name === connection.name)?.endpoint ?? 'Not set',
      connection.endpoint,
    )
  for (const provider of draft.providers) {
    const prior = saved.providers.find((item) => item.name === provider.name)
    const name = providerLabel(provider.adapter)
    add(
      `provider.${provider.name}.enabled`,
      name,
      prior ? onOffWord(prior.enabled) : 'Not added',
      prior ? onOffWord(provider.enabled) : `Added, ${onOffWord(provider.enabled).toLowerCase()}`,
    )
    if (hasCoverSwitch(provider))
      add(
        `provider.${provider.name}.cover_enabled`,
        `${name} cover evidence`,
        onOffWord(prior !== undefined && hasCoverSwitch(prior) && prior.cover_enabled),
        onOffWord(provider.cover_enabled),
      )
    if ('contact_email' in provider)
      add(
        `provider.${provider.name}.contact_email`,
        `${name} contact email`,
        (prior && 'contact_email' in prior ? prior.contact_email : null) ?? 'Not set',
        provider.contact_email ?? 'Not set',
      )
  }
  for (const entry of Object.values(secrets)) {
    if (entry.operation !== 'clear' && !entry.value) continue
    const configured = view.secrets.find((item) => item.field_id === entry.field_id)?.configured
    const provider = draft.providers.find((item) => `provider.${item.name}.key` === entry.field_id)
    add(
      entry.field_id,
      provider ? `${providerLabel(provider.adapter)} API key` : 'Audiobookshelf access key',
      configured ? 'Configured' : 'Not configured',
      entry.operation === 'clear'
        ? 'Will be cleared'
        : configured
          ? 'Will be replaced'
          : 'Will be set',
    )
  }
  for (const library of draft.libraries) {
    const prior = saved.libraries.find((item) => item.name === library.name)
    const title = library.display_name ?? library.name
    const turnedOff = (prior?.provider_connections ?? []).some(
      (name) =>
        !library.provider_connections.includes(name) &&
        draft.providers.some((provider) => provider.name === name && !provider.enabled),
    )
    add(
      `library.${library.name}.library_id`,
      `${title}: Audiobookshelf library ID`,
      prior?.library_id ?? 'Not set',
      library.library_id,
    )
    add(
      `library.${library.name}.provider_connections`,
      `${title}: sources${turnedOff ? ' (a source was turned off)' : ''}`,
      sourceList(prior?.provider_connections ?? []),
      sourceList(library.provider_connections),
    )
  }
  for (const field of numberFields)
    add(
      field.fieldId,
      field.label,
      numberWords(field, readNumber(saved, field.fieldId)),
      numberWords(field, readNumber(draft, field.fieldId)),
    )
  add(
    'server.diagnostic_timings_enabled',
    'Diagnostic timings',
    onOffWord(saved.server.diagnostic_timings_enabled),
    onOffWord(draft.server.diagnostic_timings_enabled),
  )
  add(
    'reduced_motion',
    'Motion',
    motionWord(saved.reduced_motion),
    motionWord(draft.reduced_motion),
  )
  if (!changes.length && JSON.stringify(saved) !== JSON.stringify(draft))
    changes.push({ fieldId: '', label: 'Other settings', before: 'Saved', after: 'Edited' })
  return changes.sort(
    (left, right) =>
      sections.findIndex((section) => section.id === sectionOf(left.fieldId)) -
      sections.findIndex((section) => section.id === sectionOf(right.fieldId)),
  )
}

// Three-way merge of the owner's edits (base to draft) onto newer saved
// values. A value edited here and unchanged elsewhere keeps the edit; a value
// edited in both places differently takes the saved value and is counted, so
// a revision change never silently drops or overwrites anyone's work.
const same = (left: unknown, right: unknown) => JSON.stringify(left) === JSON.stringify(right)
const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value)
const isNamedList = (value: unknown): value is Record<string, unknown>[] =>
  Array.isArray(value) && value.every((item) => isRecord(item) && typeof item.name === 'string')
const rebase = (base: unknown, draft: unknown, latest: unknown): [unknown, number] => {
  if (same(draft, base) || same(draft, latest)) return [latest, 0]
  if (same(latest, base)) return [draft, 0]
  if (isRecord(base) && isRecord(draft) && isRecord(latest)) {
    let conflicts = 0
    const value = { ...latest }
    for (const key of Object.keys(draft)) {
      const [merged, count] = rebase(base[key], draft[key], latest[key])
      value[key] = merged
      conflicts += count
    }
    return [value, conflicts]
  }
  if (isNamedList(base) && isNamedList(draft) && isNamedList(latest)) {
    let conflicts = 0
    const find = (list: Record<string, unknown>[], name: unknown) =>
      list.find((item) => item.name === name)
    const value = latest.map((item) => {
      const [merged, count] = rebase(find(base, item.name), find(draft, item.name), item)
      conflicts += count
      return merged
    })
    for (const item of draft)
      if (!find(latest, item.name)) {
        if (!find(base, item.name)) value.push(item)
        else if (!same(item, find(base, item.name))) conflicts += 1
      }
    return [value, conflicts]
  }
  return [latest, 1]
}

// Credential-bearing capabilities start disabled. Inventaire is keyless and
// #883 defaults both its evidence lanes on, including after owner re-enable.
// The four audiobook adapters are keyless (ADR-0061), and Audible's
// marketplace order stays with the configuration file, so neither appears.
const newProvider = (
  capability: SettingsView['provider_capabilities'][number],
): Document['providers'][number] => {
  const base = { cover_enabled: false as const, enabled: false, name: capability.name }
  switch (capability.adapter) {
    case 'open_library':
      return { ...base, adapter: 'open_library', contact_email: null }
    case 'hardcover':
      return { ...base, adapter: 'hardcover' }
    case 'google_books':
      return { ...base, adapter: 'google_books' }
    case 'inventaire':
      return { ...base, adapter: 'inventaire', cover_enabled: true, enabled: true }
    case 'audible_catalog':
      return {
        ...base,
        adapter: 'audible_catalog',
        marketplaces: [...settingsAudibleMarketplaceOrder] as AudibleMarketplace[],
      }
    case 'audnexus':
      return { ...base, adapter: 'audnexus' }
    case 'audiosilo':
      return { ...base, adapter: 'audiosilo' }
    case 'itunes_search':
      return { ...base, adapter: 'itunes_search' }
  }
}

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
  // A pending replacement reopens its editor when the row remounts, so a review
  // link back to it lands on the value that will be sent.
  const [editing, setEditing] = useState(!presence?.configured || pending?.operation === 'replace')
  const [confirmingClear, setConfirmingClear] = useState(false)
  if (pending?.operation === 'clear')
    return (
      <div className="settings-secret-state">
        <strong>Will be cleared when you save</strong>
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
      <small>Sent once when you save and never shown again.</small>
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

// The saved notice floats above the bottom edge. While it is shown it
// publishes its height, re-measured on each render and resize (zoom included),
// so a focused control scrolls clear of it as it does of the finish tray.
const SavedNotice = ({
  ref,
  focusable = false,
  children,
}: {
  ref?: RefObject<HTMLParagraphElement | null>
  focusable?: boolean
  children: ReactNode
}) => {
  const own = useRef<HTMLParagraphElement>(null)
  const notice = ref ?? own
  useLayoutEffect(() => {
    const root = document.documentElement
    const measure = () => {
      if (notice.current)
        root.style.setProperty(
          '--settings-saved-height',
          `${String(notice.current.offsetHeight)}px`,
        )
    }
    measure()
    window.addEventListener('resize', measure)
    return () => {
      window.removeEventListener('resize', measure)
      root.style.removeProperty('--settings-saved-height')
    }
  }, [notice, children])
  return (
    <p className="settings-saved" role="status" tabIndex={focusable ? -1 : undefined} ref={notice}>
      {children}
    </p>
  )
}

export default function SettingsPage() {
  const { http, queryClient } = useReviewContext()
  const query = useQuery({
    queryKey: ['settings'] as const,
    queryFn: (queryContext) => http.getSettings(queryContext.signal, queryWorkClass(queryContext)),
    staleTime: 15_000,
    retry: false,
  })
  const [saved, setSaved] = useState(false)
  const [reconciliation, setReconciliation] = useState<'idle' | 'checking' | 'failed' | 'checked'>(
    'idle',
  )
  const [recoveryGeneration, setRecoveryGeneration] = useState(0)
  const [savedGeneration, setSavedGeneration] = useState(0)
  const reconciliationTarget = useRef<HTMLDivElement>(null)
  useLayoutEffect(() => {
    reconciliationTarget.current?.focus()
  }, [reconciliation])
  const reconcileUncertainSave = async () => {
    setSaved(false)
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
      <ProviderThrottleNotice />
      {saved ? <SavedNotice>Settings saved and in use.</SavedNotice> : null}
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
        key={`${query.data.authority}:${String(recoveryGeneration)}:${String(savedGeneration)}`}
        view={query.data}
        saveBlocked={reconciliation === 'checking' || reconciliation === 'failed'}
        onSaved={(value) => {
          queryClient.setQueryData(['settings'], value)
          queryClient.setQueryData(['appearance'], {
            reduced_motion: value.document.reduced_motion,
          })
          setReconciliation('idle')
          setSavedGeneration((generation) => generation + 1)
          setSaved(true)
        }}
        onEdited={() => setSaved(false)}
        onStale={() => {
          setSaved(false)
          return query.refetch()
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
  onEdited,
  onStale,
  onUncertain,
}: {
  view: SettingsView
  saveBlocked: boolean
  onSaved: (value: SettingsView) => void
  onEdited: () => void
  onStale: () => Promise<unknown>
  onUncertain: () => void
}) => {
  const { http } = useReviewContext()
  const { group: active } = settingsRoute.useSearch()
  const navigate = settingsRoute.useNavigate()
  const [base, setBase] = useState<Document>(() => clone(view.document))
  const [draft, setDraft] = useState<Document>(() => clone(view.document))
  const [secrets, setSecrets] = useState<Record<string, SecretWrite>>({})
  const [status, setStatus] = useState<
    'idle' | 'failed' | 'uncertain' | 'stale' | 'unconfirmed' | 'invalid' | 'rebased'
  >('idle')
  const [conflicts, setConflicts] = useState(0)
  const [announcement, setAnnouncement] = useState('')
  const [reviewing, setReviewing] = useState(false)
  const [advancedOpen, setAdvancedOpen] = useState(false)
  const [discardGeneration, setDiscardGeneration] = useState(0)
  const [libraryPage, setLibraryPage] = useState(0)
  const [focusTarget, setFocusTarget] = useState<string | null>(null)
  const statusTarget = useRef<HTMLParagraphElement>(null)
  const routeSection = useRef<HTMLElement>(null)
  // The loading state that held route focus has just unmounted; keep focus in
  // the route rather than leave it on the document body.
  useLayoutEffect(() => {
    if (document.activeElement === null || document.activeElement === document.body)
      routeSection.current?.focus({ preventScroll: true })
  }, [])
  // Newer saved values (another tab, or the separate Policy description save)
  // are merged under the draft instead of replacing it.
  if (view.document.revision !== base.revision) {
    const [merged, count] = rebase(base, draft, view.document)
    const kept = !same(draft, base)
    // A pending secret follows the key's newer presence: a value becomes a set
    // or a replacement to match, and a clear of an absent key is dropped.
    const pending = Object.fromEntries(
      Object.entries(secrets).flatMap(([fieldId, item]): [string, SecretWrite][] => {
        const configured = view.secrets.some(
          (entry) => entry.field_id === fieldId && entry.configured,
        )
        if (item.operation === 'clear') return configured ? [[fieldId, item]] : []
        return [[fieldId, { ...item, operation: configured ? 'replace' : 'set' }]]
      }),
    )
    setBase(clone(view.document))
    setDraft(clone(merged as Document))
    setSecrets(pending)
    setConflicts(count)
    setStatus(kept || Object.keys(pending).length ? 'rebased' : 'idle')
  }
  const mutation = useMutation({
    mutationFn: (value: SettingsWrite) => http.writeSettings(value),
    retry: false,
    onSuccess: (value) => onSaved(value),
    onError: (error) => {
      if (error instanceof ApiError && error.status === 409) {
        setStatus('stale')
        void onStale().then(() =>
          setStatus((current) => (current === 'stale' ? 'unconfirmed' : current)),
        )
      } else if (
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
  const changes = useMemo(
    () => describeChanges(base, draft, secrets, view),
    [base, draft, secrets, view],
  )
  const changed = changes.length > 0
  const changedFields = new Set(changes.map((item) => item.fieldId))
  const changeCount = (group: Group) =>
    changes.filter((item) => sectionOf(item.fieldId) === group).length
  const changedSections = sections.filter((section) => changeCount(section.id) > 0)
  const libraryPageCount = Math.max(1, Math.ceil(draft.libraries.length / 20))
  const visibleLibraries = draft.libraries
    .map((library, index) => ({ library, index }))
    .slice(libraryPage * 20, libraryPage * 20 + 20)
  const availableProviderCapabilities = view.provider_capabilities.filter(
    (capability) => !draft.providers.some((provider) => provider.adapter === capability.adapter),
  )
  const locked = view.authority === 'file'
  const update = (change: (value: Document) => void) => {
    const next = clone(draft)
    change(next)
    setDraft(next)
    setStatus('idle')
    onEdited()
  }
  const setSecret = (fieldId: string, value: SecretWrite | undefined) => {
    setSecrets((current) => {
      if (value) return { ...current, [fieldId]: value }
      return Object.fromEntries(Object.entries(current).filter(([key]) => key !== fieldId))
    })
    setStatus('idle')
    onEdited()
  }
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
      value.providers.push(newProvider(capability))
      value.providers.sort((left, right) => left.name.localeCompare(right.name))
      return value
    })
    setStatus('idle')
    onEdited()
  }
  // Opens the section, page and disclosure holding a field, then focuses it.
  const goTo = (fieldId: string) => {
    const group = sectionOf(fieldId)
    const library = draft.libraries.findIndex((item) => fieldId.startsWith(`library.${item.name}.`))
    if (library >= 0) setLibraryPage(Math.floor(library / 20))
    if (numberFields.some((field) => field.advanced && field.fieldId === fieldId))
      setAdvancedOpen(true)
    if (group !== active) void navigate({ search: { group } })
    setFocusTarget(fieldId)
  }
  useEffect(() => {
    if (focusTarget === null) return undefined
    const frame = requestAnimationFrame(() => {
      const target = document.getElementById(controlId(focusTarget))
      const control = target?.matches('input, button, select, textarea')
        ? target
        : target?.querySelector<HTMLElement>('[aria-pressed="true"], button, input')
      control?.focus()
      setFocusTarget(null)
    })
    return () => cancelAnimationFrame(frame)
  }, [focusTarget, active, libraryPage])
  const save = () => {
    const first = errors[0]
    if (first) {
      setStatus('invalid')
      goTo(first.fieldId)
      return
    }
    mutation.mutate({
      expected_revision: base.revision,
      document: { ...draft, revision: base.revision + 1 },
      secrets: Object.values(secrets).filter(
        (item) => item.operation === 'clear' || (item.value?.length ?? 0) > 0,
      ),
    })
  }
  const discard = () => {
    setDraft(clone(base))
    setSecrets({})
    setStatus('idle')
    setReviewing(false)
    setDiscardGeneration((generation) => generation + 1)
    setAnnouncement('All unsaved settings changes were discarded.')
  }
  // A rebase that took the newer value for every edit leaves nothing to save,
  // so its notice sits on the page instead of in an otherwise empty tray.
  const settled = status === 'rebased' && !changed && Object.keys(secrets).length === 0
  const statusCopy = mutation.isPending
    ? 'Saving settings…'
    : status === 'uncertain'
      ? 'Save outcome uncertain. Waiting for current settings.'
      : status === 'failed'
        ? refusalCopy(
            mutation.error,
            'The server rejected these values. Nothing was saved; your changes are still here.',
          )
        : status === 'stale'
          ? 'Settings changed elsewhere. Loading the newer saved values under your changes.'
          : status === 'unconfirmed'
            ? 'Settings changed elsewhere, but the newer values could not be confirmed. Your changes are still here; save again to retry.'
            : status === 'rebased' && settled
              ? 'Settings changed elsewhere, and your edits now show the newer saved values. Nothing is left to save.'
              : status === 'rebased'
                ? conflicts
                  ? `Settings changed elsewhere. Your other changes were kept; ${String(conflicts)} ${conflicts === 1 ? 'edit was' : 'edits were'} also changed elsewhere and now ${conflicts === 1 ? 'shows' : 'show'} the newer saved value.`
                  : 'Settings changed elsewhere. Your changes were kept on top of the newer saved values.'
                : status === 'invalid' || errors.length
                  ? `${String(errors.length)} ${errors.length === 1 ? 'value needs' : 'values need'} fixing before saving: ${errors[0]?.message ?? ''}`
                  : null
  useEffect(() => {
    if (status === 'rebased' || status === 'unconfirmed') statusTarget.current?.focus()
  }, [status])
  const recordRow = (fieldId: string) => ({
    fieldId,
    error: fieldError(fieldId),
    changed: changedFields.has(fieldId),
    locked,
  })
  const numberRow = (field: NumberField) => (
    <FieldRow
      key={field.fieldId}
      {...recordRow(field.fieldId)}
      title={field.label}
      description={field.description}
    >
      {locked ? (
        <strong>{numberWords(field, readNumber(draft, field.fieldId))}</strong>
      ) : (
        <span className="settings-number">
          <FieldInput
            fieldId={field.fieldId}
            error={fieldError(field.fieldId)}
            unit
            type="number"
            min={shownNumber(field, field.minimum)}
            max={field.maximum === undefined ? undefined : shownNumber(field, field.maximum)}
            step="any"
            value={shownNumber(field, readNumber(draft, field.fieldId))}
            onChange={(event) =>
              update((value) => {
                // Only an edit writes, and it writes whole base units, so an
                // untouched field keeps its stored value exactly.
                writeNumber(
                  value,
                  field.fieldId,
                  Math.round(Number(event.currentTarget.value) * unitFactor[field.unit]),
                )
              })
            }
          />
          <small id={unitId(field.fieldId)}>
            {field.unit}
            {sizeAside(field, readNumber(draft, field.fieldId)) === null
              ? ''
              : ` (${String(sizeAside(field, readNumber(draft, field.fieldId)))})`}
          </small>
        </span>
      )}
    </FieldRow>
  )
  const sectionNumbers = (group: Group, advanced: boolean) =>
    numberFields
      .filter(
        (field) => sectionOf(field.fieldId) === group && (field.advanced ?? false) === advanced,
      )
      .map(numberRow)
  const currentSection = sections.find((section) => section.id === active) ?? sections[0]

  return (
    <section ref={routeSection} className="settings-route" data-route-focus tabIndex={-1}>
      <header className="settings-heading">
        <h1>Settings</h1>
      </header>
      <p className="sr-only" role="status">
        {announcement}
      </p>
      {locked ? (
        <section className="settings-file-banner" role="status">
          <strong>Managed by /data/config.json</strong>
          <p>
            These values are read-only while this deployment is managed by its configuration file.
          </p>
        </section>
      ) : null}
      {settled ? (
        <SavedNotice focusable ref={statusTarget}>
          {statusCopy}
        </SavedNotice>
      ) : null}
      <div className="settings-compact-sections">
        <Dropdown
          ariaLabel="Settings section"
          value={active}
          options={sections.map((section) => ({
            value: section.id,
            label: changeCount(section.id)
              ? `${section.label} · ${String(changeCount(section.id))} changed`
              : section.label,
          }))}
          onChange={(group) => {
            const section = sections.find((item) => item.id === group)
            if (section) void navigate({ search: { group: section.id } })
          }}
        />
      </div>
      <div className="settings-editor">
        <nav className="settings-sidebar" aria-label="Settings sections">
          {sections.map((section) => (
            <button
              type="button"
              key={section.id}
              aria-current={active === section.id ? 'page' : undefined}
              aria-describedby={`settings-${section.id}-changes`}
              onClick={() => void navigate({ search: { group: section.id } })}
            >
              <span>
                <strong>{section.label}</strong>
                <small>{section.description}</small>
              </span>
              <ChangeCount id={`settings-${section.id}-changes`} count={changeCount(section.id)} />
            </button>
          ))}
        </nav>
        <div className="settings-sheet" key={discardGeneration}>
          <header>
            <h2>{currentSection?.label}</h2>
          </header>
          <div className="settings-fields">
            {active === 'connection'
              ? draft.connections.map((connection, index) => (
                  <div className="settings-record" key={connection.name}>
                    {draft.connections.length > 1 ? <h3>{connection.name}</h3> : null}
                    <FieldRow
                      {...recordRow(`connection.${connection.name}.endpoint`)}
                      title="Audiobookshelf endpoint"
                      description="The service address used for catalogue and metadata work."
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
                    </FieldRow>
                    <FieldRow
                      {...recordRow(`connection.${connection.name}.key`)}
                      group={!locked}
                      title="Access key"
                    >
                      {locked ? (
                        <strong>
                          {view.secrets.find(
                            (item) => item.field_id === `connection.${connection.name}.key`,
                          )?.indicator ?? 'Not configured'}
                        </strong>
                      ) : (
                        <Secret
                          fieldId={`connection.${connection.name}.key`}
                          label="Audiobookshelf access key"
                          error={fieldError(`connection.${connection.name}.key`)}
                          view={view}
                          pending={secrets[`connection.${connection.name}.key`]}
                          onChange={(value) =>
                            setSecret(`connection.${connection.name}.key`, value)
                          }
                        />
                      )}
                    </FieldRow>
                  </div>
                ))
              : null}
            {active === 'providers' ? (
              <>
                {draft.providers.length === 0 ? (
                  <div className="settings-empty">
                    <p>
                      No metadata sources yet. More sources usually give review better evidence.
                    </p>
                  </div>
                ) : null}
                {draft.providers.map((provider, index) => {
                  const secretId = `provider.${provider.name}.key`
                  const label = providerLabel(provider.adapter)
                  return (
                    <div className="settings-record" key={provider.name}>
                      <h3>
                        {label} <code>{provider.name}</code>
                      </h3>
                      <FieldRow
                        {...recordRow(`provider.${provider.name}.enabled`)}
                        group
                        title="Use this source"
                        description="Off also removes it from every library that uses it."
                      >
                        {locked ? (
                          <strong>{onOffWord(provider.enabled)}</strong>
                        ) : (
                          <SegmentedControl
                            id={controlId(`provider.${provider.name}.enabled`)}
                            label={`Use ${label}`}
                            describedBy={descriptionId(`provider.${provider.name}.enabled`)}
                            options={offOn}
                            value={provider.enabled ? 'on' : 'off'}
                            onChange={(choice) =>
                              update((value) => {
                                const item = value.providers.at(index)
                                if (!item) return
                                item.enabled = choice === 'on'
                                if (item.enabled && item.adapter === 'inventaire')
                                  item.cover_enabled = true
                                if (!item.enabled) {
                                  item.cover_enabled = false
                                  for (const library of value.libraries)
                                    library.provider_connections =
                                      library.provider_connections.filter(
                                        (name) => name !== item.name,
                                      )
                                }
                              })
                            }
                          />
                        )}
                      </FieldRow>
                      {hasCoverSwitch(provider) ? (
                        <FieldRow
                          {...recordRow(`provider.${provider.name}.cover_enabled`)}
                          group
                          title="Cover evidence"
                          description="Consider this source’s own cover image. Stays off while the source is off."
                        >
                          {locked ? (
                            <strong>{onOffWord(provider.cover_enabled)}</strong>
                          ) : (
                            <SegmentedControl
                              id={controlId(`provider.${provider.name}.cover_enabled`)}
                              label={`${label} cover evidence`}
                              describedBy={descriptionId(`provider.${provider.name}.cover_enabled`)}
                              options={offOn}
                              value={provider.cover_enabled ? 'on' : 'off'}
                              disabled={!provider.enabled}
                              onChange={(choice) =>
                                update((value) => {
                                  const item = value.providers.at(index)
                                  if (item && hasCoverSwitch(item))
                                    item.cover_enabled = choice === 'on'
                                })
                              }
                            />
                          )}
                        </FieldRow>
                      ) : null}
                      {provider.adapter === 'audible_catalog' ? (
                        <FieldRow
                          title="Marketplace order"
                          description="Lookups try these marketplaces in this order."
                          managed
                          locked={locked}
                        >
                          <strong>{provider.marketplaces.join(', ')}</strong>
                        </FieldRow>
                      ) : null}
                      {settingsProviderKeyless(provider.adapter) ? null : 'contact_email' in
                        provider ? (
                        <FieldRow
                          {...recordRow(`provider.${provider.name}.contact_email`)}
                          title="Contact email"
                          description="Sent only to Open Library, as it asks; never kept in reports."
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
                        </FieldRow>
                      ) : (
                        <FieldRow {...recordRow(secretId)} group={!locked} title="API key">
                          {locked ? (
                            <strong>
                              {view.secrets.find((item) => item.field_id === secretId)?.indicator ??
                                'Not configured'}
                            </strong>
                          ) : (
                            <Secret
                              fieldId={secretId}
                              label={`${label} API key`}
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
                            Get a {label} key
                          </a>
                        </FieldRow>
                      )}
                    </div>
                  )
                })}
                {availableProviderCapabilities.length ? (
                  <section
                    className="settings-capabilities"
                    aria-label="Available metadata sources"
                  >
                    <h3>Add a metadata source</h3>
                    <p>Added sources start off until you turn them on and save.</p>
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
                    <div className="settings-record-meta">
                      <Disclosure summary="Identifiers and mount">
                        <ul>
                          <li>
                            Local identifier: <code>{library.name}</code>
                          </li>
                          <li>
                            Audiobookshelf identifier: <code>{library.library_id}</code>
                          </li>
                          <li>
                            Connection: <code>{library.connection}</code>
                          </li>
                          <li>
                            Media mount: {library.mount ?? 'none'} (managed by your deployment)
                          </li>
                        </ul>
                      </Disclosure>
                    </div>
                    <FieldRow
                      {...recordRow(`library.${library.name}.library_id`)}
                      title="Audiobookshelf library ID"
                      description="The exact Audiobookshelf library this collection reads."
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
                    </FieldRow>
                    <FieldRow
                      {...recordRow(`library.${library.name}.provider_connections`)}
                      group
                      title="Sources"
                      description="Only these sources give evidence for this library."
                    >
                      {locked ? (
                        <strong>
                          {library.provider_connections.length
                            ? library.provider_connections.join(', ')
                            : 'Audiobookshelf evidence only'}
                        </strong>
                      ) : draft.providers.some((provider) => provider.enabled) ? (
                        <fieldset
                          className="settings-choice"
                          id={controlId(`library.${library.name}.provider_connections`)}
                          aria-describedby={descriptionId(
                            `library.${library.name}.provider_connections`,
                          )}
                        >
                          <legend>Sources for {library.display_name ?? library.name}</legend>
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
                                {providerLabel(provider.adapter)}
                              </label>
                            ))}
                        </fieldset>
                      ) : (
                        <p className="settings-empty-associations">
                          Turn on a metadata source to use it here.
                        </p>
                      )}
                    </FieldRow>
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
            {active === 'reviewer' ? (
              <>
                <p className="settings-immediate">
                  Reviewer actions take effect as soon as you use them. They are not part of Save
                  settings or Discard all.
                </p>
                <ReviewerAccessPanel http={http} />
              </>
            ) : null}
            {active === 'background' ? (
              <>
                {sectionNumbers('background', false)}
                <details
                  className="settings-advanced"
                  open={advancedOpen}
                  onToggle={(event) => setAdvancedOpen(event.currentTarget.open)}
                >
                  <summary>
                    Response and file size limits
                    {numberFields.some(
                      (field) => field.advanced && changedFields.has(field.fieldId),
                    ) ? (
                      <small> · changed</small>
                    ) : null}
                  </summary>
                  {sectionNumbers('background', true)}
                </details>
              </>
            ) : null}
            {active === 'access' ? (
              <>
                {sectionNumbers('access', false)}
                <FieldRow
                  {...recordRow('server.diagnostic_timings_enabled')}
                  group
                  title="Diagnostic timings"
                  description="Record how long each stage takes, for support diagnostics."
                >
                  {locked ? (
                    <strong>{onOffWord(draft.server.diagnostic_timings_enabled)}</strong>
                  ) : (
                    <SegmentedControl
                      id={controlId('server.diagnostic_timings_enabled')}
                      label="Diagnostic timings"
                      describedBy={descriptionId('server.diagnostic_timings_enabled')}
                      options={offOn}
                      value={draft.server.diagnostic_timings_enabled ? 'on' : 'off'}
                      onChange={(choice) =>
                        update((value) => {
                          value.server.diagnostic_timings_enabled = choice === 'on'
                        })
                      }
                    />
                  )}
                </FieldRow>
              </>
            ) : null}
            {active === 'appearance' ? (
              <>
                <FieldRow
                  {...recordRow('reduced_motion')}
                  group
                  title="Motion"
                  description="Reduced whenever this setting or your system asks for it."
                >
                  {locked ? (
                    <strong>{motionWord(draft.reduced_motion)}</strong>
                  ) : (
                    <SegmentedControl
                      id={controlId('reduced_motion')}
                      label="Motion"
                      describedBy={descriptionId('reduced_motion')}
                      options={[
                        { value: 'follow_system', label: 'Follow system' },
                        { value: 'always_reduced', label: 'Always reduced' },
                      ]}
                      value={draft.reduced_motion}
                      onChange={(choice) =>
                        update((value) => {
                          value.reduced_motion = choice
                        })
                      }
                    />
                  )}
                </FieldRow>
                <FieldRow
                  title="Theme"
                  description="Light or dark is set for this browser from the theme button at the top of the page. It applies at once and is not part of Save settings."
                >
                  <span className="settings-immediate-value">Browser only</span>
                </FieldRow>
              </>
            ) : null}
          </div>
        </div>
      </div>
      {!locked &&
      (changed || errors.length > 0 || (status !== 'idle' && !settled) || mutation.isPending) ? (
        <FinishTray
          label="Unsaved settings"
          className="settings-tray"
          summary={
            <>
              <strong>
                {changed
                  ? `${String(changes.length)} unsaved ${changes.length === 1 ? 'change' : 'changes'}`
                  : 'No unsaved changes'}
              </strong>
              {changed ? <span>{changedSections.map((item) => item.label).join(', ')}</span> : null}
              {statusCopy ? (
                <p
                  ref={statusTarget}
                  tabIndex={-1}
                  className={`settings-tray-status${
                    ['failed', 'uncertain', 'stale', 'unconfirmed', 'invalid'].includes(status) ||
                    (errors.length > 0 && status === 'idle')
                      ? ' is-problem'
                      : ''
                  }`}
                  role={status === 'idle' && !errors.length ? 'status' : 'alert'}
                >
                  {statusCopy}
                </p>
              ) : null}
              {changed ? (
                <button
                  type="button"
                  className="settings-review-toggle"
                  aria-expanded={reviewing}
                  aria-controls="settings-change-review"
                  onClick={() => setReviewing((value) => !value)}
                >
                  {reviewing ? 'Hide changes' : 'Review changes'}
                </button>
              ) : null}
            </>
          }
          actions={
            <>
              <button
                type="button"
                disabled={(!changed && Object.keys(secrets).length === 0) || mutation.isPending}
                onClick={discard}
              >
                Discard all
              </button>
              <Button
                variant="primary"
                type="button"
                disabled={!changed || mutation.isPending || saveBlocked}
                onClick={save}
              >
                {mutation.isPending ? 'Saving…' : 'Save settings'}
              </Button>
            </>
          }
        >
          {reviewing && changed ? (
            <ul id="settings-change-review" className="settings-change-review">
              {changes.map((item) => (
                <li key={`${item.fieldId}:${item.label}`}>
                  <button type="button" onClick={() => goTo(item.fieldId)}>
                    <small>{sectionLabel(sectionOf(item.fieldId))}</small>
                    <strong>{item.label}</strong>
                  </button>
                  <span>
                    <del>{item.before}</del>
                    <span aria-hidden="true"> → </span>
                    <span className="sr-only"> becomes </span>
                    <ins>{item.after}</ins>
                  </span>
                </li>
              ))}
            </ul>
          ) : null}
        </FinishTray>
      ) : null}
    </section>
  )
}
