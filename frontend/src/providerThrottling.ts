import { useIsMutating, useMutation } from '@tanstack/react-query'
import type { QueryClient } from '@tanstack/react-query'
import { useRef } from 'react'

import settingsVocabulary from '../../src/mechanical_librarian/interfaces/web/settings_vocabulary.json'
import { ApiError, refusalCopy } from './api/http'
import type {
  PreparationView,
  ProviderThrottleEntry,
  ProviderThrottlingView,
  ReviewHttp,
  SettingsView,
} from './api/http'
import { formatWhen } from './dates'
import { invalidateForegroundQueries, useRequiredQuery as useQuery } from './queryWork'

type Reason = ProviderThrottleEntry['reasons'][number]

// One sentence per recovery reason, in the order an entry names the first.
export const throttleReasonCopy: readonly (readonly [Reason, string])[] = [
  ['quota_exhausted', 'Its request quota is used up.'],
  ['burst_throttled', 'It is limiting requests.'],
  ['rate_limited', 'It is limiting requests.'],
  ['authentication', 'It refused the API key.'],
  ['authorization', 'It refused access for this API key.'],
  ['configuration', 'Its settings need checking.'],
  ['transient', 'It did not answer.'],
]
export const providerLabels: Readonly<Record<string, string>> = settingsVocabulary.provider_labels
export const throttleWhen = (entry: ProviderThrottleEntry, now: Date) =>
  entry.state === 'checking'
    ? 'Retrying now.'
    : entry.state === 'dormant'
      ? 'No automatic retry. Fix the source, then retry.'
      : entry.state === 'exhausted'
        ? 'No automatic retry remains.'
        : entry.next_retry_at === null || entry.next_retry_at * 1000 <= now.getTime()
          ? 'Retry due now.'
          : `Next automatic retry ${formatWhen(entry.next_retry_at, now)}.`
/** The entry's sentences after its heading, as the notice and the popover both say them. */
export const throttleLine = (entry: ProviderThrottleEntry, now: Date) => {
  const reason = throttleReasonCopy.find(([code]) => entry.reasons.includes(code))
  const count = `${entry.deferred.toLocaleString()} ${entry.deferred === 1 ? 'lookup' : 'lookups'}`
  return `${reason ? `${reason[1]} ` : ''}${count} waiting. ${throttleWhen(entry, now)}`
}
export const remedyLabel = (remedy: NonNullable<ProviderThrottleEntry['remedy']>) =>
  remedy === 'add_api_key' ? 'Add a Google Books API key' : 'Check the source’s API key'
export const fileManagedRetry =
  'To retry, raise provider_recovery_generation by one in /data/config.json and restart the service.'
export const retryPending = 'Every waiting lookup is retrying now.'
export const retryRefusal = (error: unknown) =>
  error instanceof ApiError && error.code === 'provider_recovery_stale'
    ? 'Retry now was already pressed elsewhere.'
    : error instanceof ApiError && error.code === 'settings_managed_by_file'
      ? fileManagedRetry
      : error instanceof ApiError && error.code === 'settings_stale'
        ? 'Settings changed elsewhere. Check the notice, then press Retry now again.'
        : refusalCopy(
            error,
            'No answer came back. Check whether the lookups are retrying before pressing Retry now again.',
          )

export const entryKey = (entry: Pick<ProviderThrottleEntry, 'library' | 'adapter'>) =>
  `${entry.library}\u0000${entry.adapter}`

/**
 * An entry needs the owner when nothing retries it unless they act, when a key
 * is missing or refused, or once its lookups have waited a day (#1494). A file-
 * managed retry is not a reason by itself: automatic retries still run there.
 */
export const needsYou = (entry: ProviderThrottleEntry, now: number) =>
  entry.state === 'dormant' ||
  entry.state === 'exhausted' ||
  entry.remedy !== null ||
  (entry.notice_at !== null && entry.notice_at * 1000 <= now)

/** When the next entry that does not need the owner yet will, in milliseconds. */
export const nextNoticeAt = (entries: readonly ProviderThrottleEntry[], now: number) =>
  Math.min(
    ...entries.flatMap((entry) =>
      entry.notice_at === null || needsYou(entry, now) ? [] : [entry.notice_at * 1000],
    ),
  )

/** A browser timer fires at once past this many milliseconds, so a wait is capped. */
export const timerDelay = (at: number, now: number) => Math.min(at - now, 2_147_483_647)

// ---- What Retry now did --------------------------------------------------

const outcomeClause: Readonly<Record<Reason, string>> = {
  quota_exhausted: 'still has no request quota left',
  burst_throttled: 'is still limiting requests',
  rate_limited: 'is still limiting requests',
  transient: 'still did not answer',
  authentication: 'refused the API key again',
  authorization: 'refused access for this API key again',
  configuration: 'still needs its settings checked',
}
type Settled = 'waiting' | 'dormant' | 'exhausted'
const outcomeTail: Readonly<Record<Settled, readonly [string, string]>> = {
  waiting: ['is waiting again', 'are waiting again'],
  dormant: ['did not run', 'did not run'],
  exhausted: ['has no automatic retry left', 'have no automatic retry left'],
}
/** The one outcome that says Retry now worked; only it carries the tick (#1494). */
export const retryRecovered = 'Retried. Every waiting lookup got an answer.'
export type RetryClass = 'recovered' | 'failed' | 'waiting_again' | 'pending'
export type PressedEntry = Pick<ProviderThrottleEntry, 'library' | 'adapter'>

/** How one pressed library and source stands once the raised generation is read. */
export const retryClass = (current: ProviderThrottleEntry | undefined): RetryClass =>
  current === undefined
    ? 'recovered'
    : current.state === 'dormant'
      ? 'failed'
      : current.reasons.length > 0 && (current.state === 'waiting' || current.state === 'exhausted')
        ? 'waiting_again'
        : 'pending'

/**
 * The sentence Retry now's outcome is said in, or null while the snapshot has
 * not reached the raised generation or any pressed entry is still pending.
 * Recovered sources are not named; each other source gets one sentence, its
 * count summed over the libraries in the state the sentence names.
 */
export const retryOutcome = (
  pressed: readonly PressedEntry[],
  expected: number,
  throttling: ProviderThrottlingView | null,
): string | null => {
  if (throttling?.generation !== expected + 1) return null
  const current = new Map(throttling.entries.map((entry) => [entryKey(entry), entry]))
  const unsettled: ProviderThrottleEntry[] = []
  for (const item of pressed) {
    const entry = current.get(entryKey(item))
    const settled = retryClass(entry)
    if (settled === 'pending') return null
    if (entry !== undefined && settled !== 'recovered') unsettled.push(entry)
  }
  if (unsettled.length === 0) return retryRecovered
  const sources = [...new Set(unsettled.map((entry) => entry.adapter))]
  return [
    'Retried.',
    ...sources.map((adapter) => {
      const members = unsettled.filter((entry) => entry.adapter === adapter)
      const reason =
        throttleReasonCopy.find(([code]) =>
          members.some((entry) => entry.reasons.includes(code)),
        )?.[0] ?? 'transient'
      // A source that did not run anywhere is said first, then one with no retry left.
      const state: Settled = members.some((entry) => entry.state === 'dormant')
        ? 'dormant'
        : members.some((entry) => entry.state === 'exhausted')
          ? 'exhausted'
          : 'waiting'
      // The count is of the libraries in that state alone, so a source that
      // did not run in one library and waits again in another says only the
      // first count.
      const count = members
        .filter((entry) => entry.state === state)
        .reduce((sum, entry) => sum + entry.deferred, 0)
      const [one, many] = outcomeTail[state]
      const lookups = count === 1 ? `1 lookup ${one}` : `${count.toLocaleString()} lookups ${many}`
      return `${providerLabels[adapter] ?? adapter} ${outcomeClause[reason]}, so ${lookups}.`
    }),
  ].join(' ')
}

// Retry now from the popover and from the page notice is one command: one
// mutation key for the shared pending state, and one cache entry for what was
// pressed and what came of it (#1494).
export const providerRetryKey = ['provider-recovery-retry'] as const
export const providerRetryPressKey = ['provider-recovery-press'] as const
export type RetryOrigin = 'popover' | 'notice'
export interface RetryPress {
  expected: number
  pressed: PressedEntry[]
  origin: RetryOrigin
  accepted: boolean
  outcome: string | null
  refusal: string
}

export const useProviderRetry = (
  http: ReviewHttp,
  queryClient: QueryClient,
  throttling: ProviderThrottlingView | null,
  origin: RetryOrigin,
) => {
  const press = useQuery({
    queryKey: providerRetryPressKey,
    queryFn: (): Promise<RetryPress | null> => Promise.resolve(null),
    enabled: false,
    initialData: null,
  }).data
  const busy = useIsMutating({ mutationKey: providerRetryKey }) > 0
  const pending = useRef(false)
  const update = (change: Partial<RetryPress>) =>
    queryClient.setQueryData<RetryPress | null>(providerRetryPressKey, (current) =>
      current ? { ...current, ...change } : current,
    )
  const retry = useMutation({
    mutationKey: providerRetryKey,
    mutationFn: (expected: number) => http.retryProviderRecovery(expected),
    retry: false,
    onSuccess: (value) => {
      // A Retry now moves the settings revision; an open draft rebases over it.
      queryClient.setQueryData<SettingsView>(['settings'], value)
      update({ accepted: true })
    },
    onError: (error) => update({ refusal: retryRefusal(error) }),
    onSettled: async () => {
      pending.current = false
      await invalidateForegroundQueries(queryClient, { queryKey: ['review-preparation'] })
    },
  })
  const start = () => {
    if (!throttling || pending.current || queryClient.isMutating({ mutationKey: providerRetryKey }))
      return
    pending.current = true
    const pressed: RetryPress = {
      expected: throttling.generation,
      pressed: throttling.entries.map(({ library, adapter }) => ({ library, adapter })),
      origin,
      accepted: false,
      outcome: null,
      refusal: '',
    }
    queryClient.setQueryData<RetryPress | null>(providerRetryPressKey, pressed)
    retry.mutate(throttling.generation)
  }
  return {
    press,
    busy,
    start,
    status: press?.outcome ?? (press?.accepted ? retryPending : ''),
    clear: () => queryClient.setQueryData<RetryPress | null>(providerRetryPressKey, null),
    update,
  }
}

// ---- The masthead status icon ----------------------------------------------

export type StatusCentre =
  'stopped' | 'needs_you' | 'attention' | 'paused' | 'waiting' | 'idle' | 'none'

/** Today's label for the background work, in its own order of precedence. */
export const workLabel = (data: PreparationView | undefined) =>
  data?.recovery_required
    ? 'Preparation stopped'
    : // A run held by a connection check is paused too, and the Library's
      // preparation row says so in the same words (#1765).
      data?.paused || data?.presentation?.state === 'blocked'
      ? 'Preparation paused'
      : data?.actionable
        ? 'Preparation needs attention'
        : data?.active
          ? 'Preparing libraries'
          : 'Preparation idle'

/** The libraries the run line counts: its enrolled scans, else those preparing. */
export const runScanIds = (data: PreparationView | undefined) =>
  data?.presentation && data.presentation.state !== 'dismissed'
    ? data.presentation.now?.scan_ids
    : undefined

/**
 * How much of the ring is drawn: the works checked over the libraries the
 * popover's lines count, held between one fifth and nine tenths so it shows
 * from the first work and the turning gap stays visible to the last.
 */
export const ringFill = (data: PreparationView | undefined) => {
  const scans = runScanIds(data)
  const counted = (data?.libraries ?? []).filter((item) =>
    scans
      ? item.scan_id !== null && scans.includes(item.scan_id)
      : item.state === 'queued' || item.state === 'discovering' || item.state === 'processing',
  )
  const total = counted.reduce((sum, item) => sum + item.total, 0)
  const checked = counted.reduce((sum, item) => sum + item.checked, 0)
  return Math.min(0.9, Math.max(0.2, total > 0 ? checked / total : 0))
}

export const mastheadState = (data: PreparationView | undefined, now: number) => {
  const entries = data?.provider_throttling?.entries ?? []
  const needing = entries.filter((entry) => needsYou(entry, now)).length
  const label = workLabel(data)
  const sentences = [
    needing > 0 ? 'A source needs you.' : '',
    entries.length > needing ? 'Source lookups are waiting to retry.' : '',
  ].filter(Boolean)
  const active = Boolean(data?.active)
  const centre: StatusCentre = data?.recovery_required
    ? 'stopped'
    : needing > 0
      ? 'needs_you'
      : data?.actionable
        ? 'attention'
        : data?.paused || data?.presentation?.state === 'blocked'
          ? 'paused'
          : entries.length > 0
            ? 'waiting'
            : active
              ? 'none'
              : 'idle'
  return {
    // A single label keeps today's words with no full stop; two sentences are
    // punctuated as the page says them.
    name: sentences.length ? `${label}. ${sentences.join(' ')}` : label,
    centre,
    active,
    fill: ringFill(data),
  }
}
