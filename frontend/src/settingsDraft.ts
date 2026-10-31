// The Settings draft as pure values: the whole-number fields, what Save would
// change, what it would refuse, and the three-way merge onto newer saved
// values. The route renders them; nothing here touches the page.
import {
  settingsAudibleMarketplaceOrder,
  settingsEndpointValid,
  settingsIntegerValid,
  settingsOpenLibraryEmailValid,
  settingsProviderKeyRequired,
  settingsSecretValid,
  settingsTextValid,
  settingsValidationCopy,
} from './api/http'
import type { SettingsView, SettingsWrite } from './api/http'
import settingsVocabulary from '../../src/mechanical_librarian/interfaces/web/settings_vocabulary.json'
import { languageName } from './labels'
import type { SettingsGroup } from './router'
type Mutable<T> = T extends readonly (infer Item)[]
  ? Mutable<Item>[]
  : T extends object
    ? { -readonly [Key in keyof T]: Mutable<T[Key]> }
    : T
export type Document = Mutable<SettingsView['document']>
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
export const hasCoverSwitch = (provider: Provider): provider is CoverSwitchProvider =>
  coverSwitchAdapters.includes(provider.adapter)
export const providerLabel = (adapter: Provider['adapter']): string =>
  settingsVocabulary.provider_labels[adapter]
export type SecretWrite = SettingsWrite['secrets'][number]
export type Group = SettingsGroup
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

export const sections: readonly { id: Group; label: string; description: string }[] = [
  { id: 'connection', label: 'Audiobookshelf', description: 'Service address and access key' },
  { id: 'libraries', label: 'Libraries', description: 'Collections and their sources' },
  { id: 'providers', label: 'Metadata sources', description: 'Where evidence comes from' },
  { id: 'reviewer', label: 'Reviewer', description: 'Optional AI advice' },
  { id: 'background', label: 'Background work', description: 'Requests, limits and polling' },
  { id: 'access', label: 'Access and diagnostics', description: 'Sign-in length and timings' },
  { id: 'appearance', label: 'Appearance', description: 'Motion' },
]
export const sectionLabel = (id: Group) =>
  sections.find((section) => section.id === id)?.label ?? id
export const sectionOf = (fieldId: string): Group =>
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
export interface NumberField {
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
export const unitFactor = { seconds: 1, minutes: 60, MB: 1_048_576 } as const
// The shortest decimal of `value / factor` that reads back to exactly
// `value` whole units, so a shown number is the stored one: 65,536 bytes are
// 0.0625 MB and 128,975 bytes 0.123 MB; a value that is no whole number of
// units reads unrounded.
export const exactly = (value: number, factor: number) => {
  for (let digits = 0; digits <= 7; digits += 1) {
    const shown = Number((value / factor).toFixed(digits))
    if (Math.round(shown * factor) === value) return shown
  }
  return value / factor
}
// A stored value in the field's unit, as the field shows and the owner types
// it; what is shown is what is saved (#1286).
export const shownNumber = (field: NumberField, value: number) =>
  exactly(value, unitFactor[field.unit])
const grouped = (value: number) => value.toLocaleString(undefined, { maximumFractionDigits: 20 })
// Beside a size, the same size in the unit a person reads without arithmetic:
// KB below one MB, GB from 1,024 MB. A GB figure is read, not typed, so it
// stops at two decimals; the MB figure beside it stays exact (#1286).
export const sizeAside = (field: NumberField, value: number) =>
  field.unit !== 'MB' || !Number.isFinite(value) || value <= 0
    ? null
    : value < unitFactor.MB
      ? `${grouped(exactly(value, 1024))} KB`
      : value >= 1024 * unitFactor.MB
        ? `${(value / (1024 * unitFactor.MB)).toLocaleString(undefined, { maximumFractionDigits: 2 })} GB`
        : null
const unitWord = (field: NumberField, shown: number) =>
  field.unit === 'MB' ? 'MB' : shown === 1 ? field.unit.slice(0, -1) : field.unit
export const numberFields: readonly NumberField[] = [
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
export const readNumber = (document: Document, fieldId: string) =>
  fieldId
    .split('.')
    .reduce<unknown>((value, key) => (value as Record<string, unknown>)[key], document) as number
export const writeNumber = (document: Document, fieldId: string, value: number) => {
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
export const numberWords = (field: NumberField, value: number) => {
  if (!Number.isFinite(value)) return 'Not a number'
  const shown = shownNumber(field, value)
  const aside = sizeAside(field, value)
  return `${grouped(shown)} ${unitWord(field, shown)}${aside === null ? '' : ` (${aside})`}`
}
export const onOffWord = (value: boolean) => (value ? 'On' : 'Off')
export const motionWord = (value: Document['reduced_motion']) =>
  value === 'always_reduced' ? 'Always reduced' : 'Follow system'

export const clone = (value: SettingsView['document'] | Document): Document =>
  structuredClone(value) as Document

export const validationErrors = (
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

// The languages Settings offers a library (#1466): each favoured ISO 639 base
// the server accepts, which a Python test holds to the domain, that the
// browser can name, by name. Script and region variants are not offered.
export const libraryLanguageLimit = settingsVocabulary.library_languages.maximum
const offeredLanguages = settingsVocabulary.library_languages.codes
  .map((code) => ({ value: code, label: languageName(code) }))
  .filter((option) => option.label !== option.value)
  .sort((left, right) => left.label.localeCompare(right.label))
// The server refuses two entries of one language and script. An offered code
// is its own group, since the browser's aliases join languages the server
// keeps apart (`tl` and `fil`); any other tag, such as `deu` or `de-CH` from
// the file or the API, is grouped as the browser reads it, so it withholds `de`.
const offeredCodes: ReadonlySet<string> = new Set(settingsVocabulary.library_languages.codes)
const languageClass = (tag: string) => {
  if (offeredCodes.has(tag)) return `${tag}-`
  try {
    const locale = new Intl.Locale(tag)
    return `${locale.language}-${locale.script ?? ''}`
  } catch {
    return tag
  }
}
export const languageChoices = (listed: readonly string[]) => {
  const taken = new Set(listed.map(languageClass))
  return offeredLanguages.filter((option) => !taken.has(languageClass(option.value)))
}
const languageList = (tags: readonly string[]) =>
  tags.length ? tags.map(languageName).join(', ') : 'None declared'

// What Save would change, as the owner reads it. Secret values never appear:
// only whether a key will be set, replaced or cleared. A library's sources can
// change indirectly when a source is turned off, so that is named too.
export const describeChanges = (
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
    add(
      `library.${library.name}.languages`,
      `${title}: languages`,
      languageList(prior?.languages ?? []),
      languageList(library.languages),
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
export const same = (left: unknown, right: unknown) =>
  JSON.stringify(left) === JSON.stringify(right)
const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value)
const isNamedList = (value: unknown): value is Record<string, unknown>[] =>
  Array.isArray(value) && value.every((item) => isRecord(item) && typeof item.name === 'string')
export const rebase = (base: unknown, draft: unknown, latest: unknown): [unknown, number] => {
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
export const newProvider = (
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
