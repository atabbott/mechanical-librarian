import { useMutation } from '@tanstack/react-query'
import type { QueryClient } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import { useEffect, useLayoutEffect, useRef, useState } from 'react'

import { refusalCopy, uncertainWriteOutcome } from '../api/http'
import type {
  PreparationActionPath,
  PreparationView,
  ProviderThrottlingView,
  ReviewHttp,
} from '../api/http'
import { scanStateLabels } from '../labels'
import { useLibraryName } from '../libraryNames'
import {
  entryKey,
  fileManagedRetry,
  mastheadState,
  needsYou,
  nextNoticeAt,
  providerLabels,
  remedyLabel,
  retryOutcome,
  retryRecovered,
  runScanIds,
  throttleLine,
  timerDelay,
  useProviderRetry,
} from '../providerThrottling'
import { invalidateBackgroundQueries, invalidateForegroundQueries } from '../queryWork'
import { IconButton, StatusIcon } from './MastheadIcons'

/**
 * The "Source lookups" part of the popover: every waiting library and source,
 * those that need the owner first, with Retry now and what it did.
 */
const SourceLookups = ({
  throttling,
  retry,
  name,
  now,
  onNavigate,
}: {
  throttling: ProviderThrottlingView | null
  retry: ReturnType<typeof useProviderRetry>
  name: (library: string) => string
  now: number
  onNavigate: () => void
}) => {
  const entries = throttling?.entries ?? []
  const refusal = retry.press?.origin === 'popover' ? retry.press.refusal : ''
  const command = useRef<HTMLButtonElement>(null)
  const outcomeLine = useRef<HTMLParagraphElement>(null)
  const commandFocused = useRef(false)
  // Retry now leaves when every pressed lookup got an answer; if it held
  // focus, the outcome line takes it, so the popover keeps focus and its keys.
  useLayoutEffect(() => {
    if (!commandFocused.current || command.current) return
    commandFocused.current = false
    const active = document.activeElement
    if (active && active !== document.body) return
    outcomeLine.current?.focus({ preventScroll: true })
  })
  if (!throttling || (!entries.length && !retry.status && !refusal)) return null
  const ordered = [
    ...entries.filter((entry) => needsYou(entry, now)),
    ...entries.filter((entry) => !needsYou(entry, now)),
  ]
  const live = retry.press === null || retry.press.origin === 'popover'
  return (
    <section className="source-lookups" aria-labelledby="source-lookups-heading">
      <h3 id="source-lookups-heading">Source lookups</h3>
      {ordered.length ? (
        <ul>
          {ordered.map((entry) => (
            <li key={entryKey(entry)} className={needsYou(entry, now) ? 'needs-you' : undefined}>
              <strong>
                {providerLabels[entry.adapter]} · {name(entry.library)}
              </strong>
              <span>{throttleLine(entry, new Date(now))}</span>
              {entry.remedy ? (
                <Link to="/settings" search={{ group: 'providers' }} onClick={onNavigate}>
                  {remedyLabel(entry.remedy)}
                </Link>
              ) : null}
            </li>
          ))}
        </ul>
      ) : null}
      {entries.length > 0 && throttling.retry === 'available' ? (
        <button
          ref={command}
          type="button"
          aria-disabled={retry.busy || undefined}
          aria-busy={retry.busy || undefined}
          onClick={retry.start}
          onFocus={() => {
            commandFocused.current = true
          }}
          onBlur={() => {
            // Still mounted a frame later, focus really moved; a button that
            // left keeps the flag for the hand-off above.
            requestAnimationFrame(() => {
              if (command.current) commandFocused.current = false
            })
          }}
        >
          {retry.busy ? 'Retrying…' : 'Retry now'}
        </button>
      ) : entries.length > 0 && throttling.retry === 'managed_by_file' ? (
        <p className="source-lookups-file">{fileManagedRetry}</p>
      ) : null}
      {/* Only the place Retry now was pressed announces; the other copy is
          text. Only the outcome that says every lookup got an answer is ticked. */}
      <p
        ref={outcomeLine}
        className="source-lookups-outcome"
        role={live ? 'status' : undefined}
        tabIndex={-1}
        data-outcome={retry.status === retryRecovered ? 'recovered' : undefined}
      >
        {retry.status}
      </p>
      {refusal ? (
        <p className="source-lookups-outcome" role="alert">
          {refusal}
        </p>
      ) : null}
    </section>
  )
}

/** One library's line in the popover: its state, works checked, Scan now and Retry. */
const LibraryProgress = ({
  item,
  data,
  name,
  outsideRun,
  pending,
  command,
}: {
  item: PreparationView['libraries'][number]
  data: PreparationView
  name: string
  outsideRun: boolean
  pending: boolean
  command: (path: PreparationActionPath, refused: boolean) => () => void
}) => {
  const connection = encodeURIComponent(item.connection)
  const library = encodeURIComponent(item.library)
  const scanRefused =
    data.recovery_required || data.paused || item.state === 'queued' || item.state === 'discovering'
  return (
    <article>
      <strong>{name}</strong>
      <span>
        {data.recovery_required ? 'Last saved progress' : scanStateLabels[item.state]}
        {/*
         * The run line sums only the libraries its run enrolled, so a
         * library outside it is the one way the two totals differ.
         */}
        {outsideRun ? ' · not in this run' : ''}
      </span>
      <span>{`${item.checked.toLocaleString()} of ${item.total.toLocaleString()} works checked`}</span>
      <button
        type="button"
        aria-disabled={scanRefused || pending}
        aria-busy={pending || undefined}
        onClick={command(`${connection}/${library}/scan`, scanRefused)}
      >
        Scan now
      </button>
      {item.state === 'completed_with_failures' ? (
        <button
          type="button"
          aria-disabled={data.recovery_required || pending || data.paused}
          aria-busy={pending || undefined}
          onClick={command(`${connection}/${library}/retry`, data.recovery_required || data.paused)}
        >
          Retry
        </button>
      ) : null}
    </article>
  )
}

/**
 * The masthead's background-work control: one status icon whose popover holds
 * each library's progress, the source lookups waiting to retry, and Pause.
 * Shell owns the one preparation observer and passes its snapshot in.
 */
export const BackgroundActivity = ({
  data,
  http,
  queryClient,
}: {
  data: PreparationView | undefined
  http: ReviewHttp
  queryClient: QueryClient
}) => {
  const names = useLibraryName(http, Boolean(data?.libraries.length))
  const [open, setOpen] = useState(false)
  const trigger = useRef<HTMLButtonElement>(null)
  const popover = useRef<HTMLElement>(null)
  const pending = useRef(false)
  const shown = useRef(false)
  const throttling = data?.provider_throttling ?? null
  const retry = useProviderRetry(http, queryClient, throttling, 'popover')
  const [clock, setClock] = useState(0)
  const now = new Date().getTime()
  const action = useMutation({
    mutationFn: (path: PreparationActionPath) => http.preparationAction(path),
    retry: false,
    onSettled: async () => {
      pending.current = false
      await invalidateForegroundQueries(queryClient, { queryKey: ['review-preparation'] })
      await Promise.all([
        invalidateBackgroundQueries(queryClient, { queryKey: ['library'] }),
        invalidateBackgroundQueries(queryClient, { queryKey: ['attention'] }),
      ])
    },
  })
  // Retry now's outcome is read from the snapshot once the raised generation
  // arrives, and set once; later refetches do not change it.
  const press = retry.press
  useEffect(() => {
    if (!press?.accepted || press.outcome !== null) return
    const outcome = retryOutcome(press.pressed, press.expected, throttling)
    if (outcome !== null) retry.update({ outcome })
  })
  // Closing the popover after it showed an outcome clears that outcome. A new
  // press has shown nothing yet, so closing during it keeps it, wherever it
  // was pressed.
  useEffect(() => {
    if (!press?.outcome) shown.current = false
    else if (open) shown.current = true
    if (!open && shown.current) {
      shown.current = false
      retry.clear()
    }
  })
  // One timer to the earliest day-long wait still ahead re-renders it as needing the owner.
  const noticeDue = nextNoticeAt(throttling?.entries ?? [], now)
  useEffect(() => {
    if (!Number.isFinite(noticeDue)) return
    const timer = setTimeout(
      () => setClock((tick) => tick + 1),
      timerDelay(noticeDue, new Date().getTime()),
    )
    return () => clearTimeout(timer)
  }, [noticeDue, clock])
  // Idle is a state the owner can act on: Scan now and Pause stay reachable.
  const unavailable = !data || data.libraries.length === 0
  const runScans = runScanIds(data)
  // Each command is aria-disabled rather than disabled, so focus stays on it;
  // the guard drops a press while one is pending or the command is refused.
  const command = (path: PreparationActionPath, refused: boolean) => () => {
    if (refused || pending.current) return
    pending.current = true
    action.mutate(path)
  }
  useEffect(() => {
    if (!open || !unavailable) return
    requestAnimationFrame(() => {
      document.getElementById('main-content')?.focus({ preventScroll: true })
      setOpen(false)
    })
  }, [open, unavailable])
  if (unavailable && !open) return null
  const status = mastheadState(data, now)
  const close = () => {
    trigger.current?.focus()
    setOpen(false)
  }
  return (
    <div
      className="preparation-activity"
      // Escape closes from the trigger or from anywhere inside the popover,
      // and ``close`` already returns focus to the trigger. Tabbing off either
      // end closes it where it stands -- the Dropdown precedent -- so the
      // popover never stays open behind a focus ring that has left it; the
      // browser keeps the tab order it was going to follow. Nothing is bound
      // while the popover is shut, so both keys stay available to the page.
      onKeyDown={(event) => {
        if (!open) return
        if (event.key === 'Escape') {
          event.preventDefault()
          event.stopPropagation()
          close()
          return
        }
        if (event.key !== 'Tab') return
        const focusable = popover.current?.querySelectorAll<HTMLElement>(
          'button:not([disabled]),a[href]',
        )
        if (!focusable?.length) return
        const edge = event.shiftKey ? focusable[0] : focusable[focusable.length - 1]
        if (event.target === edge) setOpen(false)
      }}
    >
      <IconButton
        ref={trigger}
        className="status-trigger"
        label={status.name}
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
      >
        <StatusIcon centre={status.centre} active={status.active} fill={status.fill} />
      </IconButton>
      {open && data ? (
        <section ref={popover} className="preparation-popover" aria-label="Background activity">
          <h2>{data.recovery_required ? 'Preparation stopped' : 'Background activity'}</h2>
          {data.recovery_required ? (
            <p role="status">
              Restart the Mechanical Librarian service to resume preparation. Prepared works can
              still be reviewed.
            </p>
          ) : null}
          {data.libraries.map((item) => (
            <LibraryProgress
              key={`${item.connection}:${item.library}`}
              item={item}
              data={data}
              name={names.name(item.library)}
              outsideRun={Boolean(runScans && item.scan_id && !runScans.includes(item.scan_id))}
              pending={action.isPending}
              command={command}
            />
          ))}
          {/*
           * Scan now and Retry call the same operation, a new library cycle; the
           * recovery schedule they do not control is documented in
           * `docs/operations/provider-recovery.md`. Said once, below the list,
           * because Retry shows only on a library whose run had failures.
           */}
          {!data.recovery_required ? (
            <small className="activity-backup-note">
              Scan now prepares a library again, including the books that could not finish.
            </small>
          ) : null}
          <SourceLookups
            throttling={throttling}
            retry={retry}
            name={names.name}
            now={now}
            // Following the key link closes the popover and the route takes
            // focus. A link to the route already shown remounts nothing, so
            // focus left on the body goes to the route's focus target.
            onNavigate={() => {
              setOpen(false)
              requestAnimationFrame(() => {
                const active = document.activeElement
                if (active && active !== document.body) return
                const main = document.getElementById('main-content')
                ;(main?.querySelector<HTMLElement>('[data-route-focus]') ?? main)?.focus({
                  preventScroll: true,
                })
              })
            }}
          />
          <button
            type="button"
            aria-disabled={data.recovery_required || action.isPending}
            aria-busy={action.isPending || undefined}
            onClick={command(data.paused ? 'resume' : 'pause', data.recovery_required)}
          >
            {data.paused ? 'Resume' : 'Pause'}
          </button>
          {action.isError ? (
            <p role="alert">
              {uncertainWriteOutcome(action.error)
                ? 'No answer came back. Check whether the change took effect before pressing again.'
                : refusalCopy(action.error, 'Preparation did not change. Try again.')}
            </p>
          ) : null}
          {!data.recovery_required ? (
            <small className="activity-backup-note">
              Pause stops new scans. Lookups already waiting still retry on schedule.
            </small>
          ) : null}
          <button type="button" onClick={close}>
            Close
          </button>
        </section>
      ) : null}
    </div>
  )
}
