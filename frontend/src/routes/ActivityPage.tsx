import { invalidateForegroundQueries } from '../queryWork'
import { queryWorkClass, useRequiredQuery as useQuery } from '../queryWork'
import { useMutation } from '@tanstack/react-query'
import { useNavigate, useRouter, useRouterState } from '@tanstack/react-router'
import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import {
  AmbiguousMutationError,
  ApiError,
  BuildMismatchError,
  CsrfRenewedError,
  displayValue,
  sessionRefreshedCopy,
} from '../api/http'
import type {
  ActivityCategory,
  ActivityEventView,
  ActivityLibraryView,
  ActivityPermissionReadinessView,
  ActivitySearch,
  ActivityTransitionView,
  ActivityView,
  ProviderRecoveryDispatchReason,
  ProviderRecoveryFailureReason,
} from '../api/http'
import { activityOutcome, ledgerRows } from '../activityState'
import { Dropdown } from '../components/Dropdown'
import { useRouteReadyFocus } from '../focus'
import { fieldLabel, writeFieldLabel } from '../labels'
import { consentBasis, destinationReason, readWriteConsent } from '../writeConsent'
import {
  activityRoute,
  normalizeActivitySearch,
  PreparationNotice,
  useLiveStream,
  usePreparation,
  useReviewContext,
} from '../router'
// The activity response is validated into `ActivityDisplayView` at the
// transport boundary, so this route reads it as the typed view it already is
// rather than re-deriving `unknown` shapes a second time (#636).
const capabilityLabel = (state: string) =>
  ({
    allowed: 'Ready for consent',
    partially_allowed: 'Some destinations are ready',
    connection_unavailable: 'Connection needs checking',
    insufficient_permission: 'Permission is unavailable',
    field_unavailable: 'A field is unavailable',
    not_ready: 'Not ready yet',
  })[state] ?? 'Not ready yet'
const stateLabel = (state: string, advice?: 'description' | 'cover') =>
  (advice === 'description'
    ? {
        completed: 'Description advice completed',
        succeeded: 'Description advice completed',
        failed: 'Description advice failed safely',
      }[state]
    : advice === 'cover'
      ? {
          completed: 'Cover advice completed',
          unavailable: 'Cover advice unavailable',
          failed: 'Cover advice failed safely',
          selected: 'Owner cover choice recorded',
          retry: 'Manual cover retry',
        }[state]
      : undefined) ??
  {
    accept: 'Accepted',
    keep_current: 'Kept current',
    reject: 'Rejected',
    owner_entered: 'Owner-entered',
    withdraw: 'Withdrawn',
    completed: 'Completed',
    unavailable: 'Unavailable',
    failed: 'Failed safely',
    no_change_required: 'No change required',
    applying: 'Applying',
    claimed: 'Preparing',
    planned: 'Planned',
    reconciling: 'Checking',
    retryable: 'Waiting safely',
    verified: 'Verified',
    stale: 'Stale',
    superseded: 'Superseded',
    abandoned: 'Stopped safely',
  }[state] ??
  'Recorded'
// What the owner calls a record, on the row and on the control that filters
// for it, so a chip and the rows it selects never use different words. The
// filter axis is still the durable event type the ledger is indexed by; only
// the language is the owner's.
const categoryLabel: Record<ActivityCategory, string> = {
  owner: 'Owner choice',
  automatic: 'Automatic choice',
  policy: 'Policy change',
  recovery: 'Recovery',
}
const recoveryFailureLabel: Record<ProviderRecoveryFailureReason, string> = {
  authentication: 'Provider credentials need attention',
  authorization: 'Provider access was denied',
  configuration: 'Provider configuration needs attention',
  rate_limited: 'Provider rate limit',
  burst_throttled: 'Provider burst limit',
  quota_exhausted: 'Provider quota exhausted',
  transient: 'Provider temporarily unavailable',
}

const recoveryDispatchLabel: Record<ProviderRecoveryDispatchReason, string> = {
  missing_corroboration: 'needs independent evidence',
  zero_provider_evidence: 'no provider evidence yet',
  settleable_disagreement: 'evidence disagreement',
  blanket_scan: 'general recovery scan',
}
// A permission draft is the readiness check and the ticked libraries, kept
// against the consent basis it was checked on (#1279).
interface PermissionCheck extends ActivityPermissionReadinessView {
  readonly policyId: string
  readonly basis: string
}
// Raised by the verification read made on Confirm, before any grant is sent.
class ConsentBasisChanged extends Error {}
class ConsentBasisUnavailable extends Error {}
const basisChangedCopy = 'Your library settings changed. Review the permissions again.'
const readinessUnavailableCopy =
  'Permission readiness is unavailable. No permission was granted. Try again.'
const typeOptions = [
  { id: 'all', label: 'All' },
  { id: 'review', label: 'Owner choices' },
  { id: 'metadata', label: 'Automatic choices' },
  { id: 'policy', label: 'Policy changes' },
  { id: 'safety', label: 'Recovery' },
] as const
type ActivitySearchUpdate = Partial<Omit<ActivitySearch, 'q'>> & { q?: string | undefined }
const locationIdentity = (location: { href: string; state: { __TSR_key?: string } }) =>
  `${location.href}\u0000${location.state.__TSR_key ?? ''}`
const dateParts = (value: number | null) => {
  if (value === null) return { day: 'Date not recorded', time: '—' }
  const date = new Date(value * 1000)
  if (Number.isNaN(date.getTime())) return { day: 'Date not recorded', time: '—' }
  return {
    day: new Intl.DateTimeFormat(undefined, { dateStyle: 'medium' }).format(date),
    time: new Intl.DateTimeFormat(undefined, {
      hour: '2-digit',
      minute: '2-digit',
      hour12: false,
    }).format(date),
  }
}
// A label inside a sentence reads lowercased, unless it is an acronym, as the
// ledger's own sentences do.
const fieldPhrase = (label: string) => (/^[A-Z]+$/u.test(label) ? label : label.toLowerCase())
function PageJump({
  page,
  pageCount,
  onCommit,
  onEdit,
}: {
  page: number
  pageCount: number
  onCommit: (page: number, focusKey: string) => void
  onEdit: () => void
}) {
  const [value, setValue] = useState(String(page))
  const cancelCommit = useRef(false)
  const commit = () => {
    if (cancelCommit.current) {
      cancelCommit.current = false
      setValue(String(page))
      return
    }
    const requested = Number.parseInt(value, 10)
    if (!Number.isFinite(requested)) {
      setValue(String(page))
      return
    }
    const clamped = Math.max(1, Math.min(pageCount, requested))
    setValue(String(clamped))
    if (clamped !== page) onCommit(clamped, 'page-jump')
  }
  return (
    <label className="cm-activity-page-jump">
      <span>Page</span>
      <input
        data-activity-focus="page-jump"
        aria-label="Activity page number"
        inputMode="numeric"
        type="number"
        min="1"
        max={pageCount}
        value={value}
        onChange={(event) => {
          cancelCommit.current = false
          onEdit()
          setValue(event.target.value)
        }}
        onBlur={commit}
        onKeyDown={(event) => {
          if (event.key === 'Enter') event.currentTarget.blur()
          if (event.key === 'Escape') {
            event.preventDefault()
            cancelCommit.current = true
            setValue(String(page))
          }
        }}
      />
      <span>of {pageCount}</span>
    </label>
  )
}
function ActivityTransitions({
  eventId,
  advice,
}: {
  eventId: string
  advice: 'description' | 'cover' | undefined
}) {
  const { http } = useReviewContext()
  const [extraPages, setExtraPages] = useState<ActivityTransitionView[][]>([])
  const [hasMore, setHasMore] = useState(false)
  const query = useQuery({
    queryKey: ['activity-transitions', eventId],
    queryFn: (queryContext) =>
      http.getActivityTransitions(
        eventId,
        undefined,
        queryContext.signal,
        queryWorkClass(queryContext),
      ),
    retry: false,
  })
  const more = useMutation({
    mutationFn: (afterSequence: number) => http.getActivityTransitions(eventId, afterSequence),
    onSuccess: (page) => {
      setExtraPages((current) => [...current, [...page.events]])
      setHasMore(page.has_more)
    },
  })
  if (query.isLoading) return <p>Loading recorded transitions…</p>
  if (query.isError)
    return (
      <p role="alert">
        Recorded transitions could not be loaded.{' '}
        <button type="button" onClick={() => void query.refetch()}>
          Try again
        </button>
      </p>
    )
  const events = [...(query.data?.events ?? []), ...extraPages.flat()]
  const moreAvailable = extraPages.length ? hasMore : Boolean(query.data?.has_more)
  if (!events.length) return null
  return (
    <ol className="cm-activity-transitions" aria-label="Recorded lifecycle transitions">
      {events.map((transition) => (
        // One flow; the no-break space keeps each separator off a line start.
        <li key={transition.event_id}>
          <time>
            {dateParts(transition.occurred_at).day}&nbsp;· {dateParts(transition.occurred_at).time}
          </time>
          &nbsp;· {stateLabel(transition.state, advice)}
        </li>
      ))}
      {moreAvailable ? (
        <li>
          <button
            type="button"
            onClick={() => more.mutate(events[events.length - 1]?.sequence ?? 1)}
            disabled={more.isPending}
          >
            {more.isPending ? 'Loading transitions…' : 'Load more transitions'}
          </button>
          {more.isError ? (
            <p role="alert">
              More recorded transitions could not be loaded.{' '}
              <button
                type="button"
                onClick={() => more.mutate(events[events.length - 1]?.sequence ?? 1)}
              >
                Try again
              </button>
            </p>
          ) : null}
        </li>
      ) : null}
    </ol>
  )
}
// One durable record's row. It stands on its own in the ledger, or as one
// member of a preparation's checks row, with the same Details either way.
function ActivityEventRow({
  event,
  expanded,
  onToggle,
  day,
}: {
  event: ActivityEventView
  expanded: boolean
  onToggle: () => void
  /** A member recorded on another day than its checks row says so. */
  day?: string
}) {
  const changeId = event.change_id || event.event_id
  const advice =
    event.destination === 'description' && event.origin === 'adviser'
      ? 'description'
      : event.event_type !== 'policy' &&
          event.destination === 'cover' &&
          ['adviser', 'automatic_pipeline', 'owner_retry'].includes(event.origin)
        ? 'cover'
        : undefined
  return (
    <article className={expanded ? 'is-expanded' : ''}>
      <time>
        {day === undefined ? null : <span className="cm-activity-day">{day} </span>}
        {dateParts(event.occurred_at).time}
      </time>
      <span className={`cm-activity-category is-${event.category}`}>
        {categoryLabel[event.category]}
      </span>
      <div className="cm-activity-work">
        <strong title={event.work_label}>{event.work_label}</strong>
        <span>
          {event.field_label} · {event.library}
        </span>
      </div>
      {/*
      The arrow in `change` is a glyph most screen readers drop,
      so the announced row carries the same change as a whole
      sentence. Both come from the one projection, so they
      cannot come to disagree.
    */}
      <div className="cm-activity-change" aria-label={event.change_detail}>
        <span aria-hidden="true">{event.change}</span>
      </div>
      <b>{stateLabel(event.state, advice)}</b>
      <button
        data-activity-focus={`details-${changeId}`}
        aria-expanded={expanded}
        aria-controls={`activity-evidence-${event.event_id}`}
        onClick={onToggle}
      >
        {expanded ? 'Hide details' : 'Details'}
      </button>
      {expanded ? (
        <div
          className={`cm-activity-evidence${
            event.policy_changes.length ? ' has-policy-changes' : ''
          }`}
          id={`activity-evidence-${event.event_id}`}
        >
          {event.policy_changes.length ? (
            <section
              className="cm-activity-policy-changes"
              aria-label={`${String(event.policy_changes.length)} exact policy changes`}
            >
              <header>
                <strong>
                  {event.policy_changes.length} exact{' '}
                  {event.policy_changes.length === 1 ? 'change' : 'changes'}
                </strong>
                <span>Field and setting</span>
                <span>From</span>
                <span>To</span>
              </header>
              <ul>
                {event.policy_changes.map((change) => (
                  <li key={`${change.field}-${change.setting}`}>
                    <span>
                      <strong>{change.field}</strong>
                      <small>{change.setting}</small>
                    </span>
                    <b>{change.previous}</b>
                    <b>{change.next}</b>
                  </li>
                ))}
              </ul>
            </section>
          ) : null}
          <dl>
            {event.evidence.map((item) => (
              <div key={item.label}>
                <dt>{item.label}</dt>
                <dd>{item.value}</dd>
              </div>
            ))}
          </dl>
          {event.transition_id ? (
            <ActivityTransitions key={changeId} eventId={event.transition_id} advice={advice} />
          ) : null}
        </div>
      ) : null}
    </article>
  )
}
export default function ActivityPage({ initial }: { initial: ActivityView }) {
  const { http, queryClient } = useReviewContext()
  const router = useRouter()
  const navigate = useNavigate({ from: activityRoute.fullPath })
  const search = activityRoute.useSearch()
  const resolvedLocation = useRouterState({ select: (state) => state.resolvedLocation })
  const [queryText, setQueryText] = useState(search.q ?? '')
  const queryCommit = useRef<number | null>(null)
  const queryDraftVersion = useRef(0)
  const canonicalQuery = useRef(search.q ?? '')
  const pendingLocalNavigations = useRef<{ q: string; draftVersion: number }[]>([])
  const localNavigations = useRef(new Map<string, { q: string; draftVersion: number }>())
  const externalTarget = useRef<string | null>(null)
  const requestedPage = useRef(search.page)
  const pageNavigationVersion = useRef(0)
  const focusVersion = useRef(0)
  const [focusRequest, setFocusRequest] = useState<{ key: string; version: number } | null>(null)
  useEffect(
    () => () => {
      if (queryCommit.current !== null) window.clearTimeout(queryCommit.current)
    },
    [],
  )
  useEffect(
    () =>
      router.history.subscribe(({ action, location }) => {
        if (action.type === 'PUSH' || action.type === 'REPLACE') {
          const q = new URL(location.href, window.location.origin).searchParams.get('q') ?? ''
          const pending = pendingLocalNavigations.current
          const index = pending.findIndex((intent) => intent.q === q)
          if (index !== -1) {
            const [intent] = pending.splice(index, 1)
            if (intent) localNavigations.current.set(locationIdentity(location), intent)
          }
        }
        if (action.type === 'BACK' || action.type === 'FORWARD' || action.type === 'GO') {
          externalTarget.current = locationIdentity(location)
          if (queryCommit.current !== null) {
            window.clearTimeout(queryCommit.current)
            queryCommit.current = null
          }
          pendingLocalNavigations.current.length = 0
          localNavigations.current.clear()
          ++queryDraftVersion.current
          ++pageNavigationVersion.current
          ++focusVersion.current
        }
      }),
    [router],
  )
  useEffect(() => {
    if (!resolvedLocation) return
    const identity = locationIdentity(resolvedLocation)
    const current = search.q ?? ''
    // Route search and resolved location can publish in separate renders.
    // Acknowledge only matching queries, preserving a newer pending draft.
    if ((normalizeActivitySearch(resolvedLocation.search).q ?? '') !== current) return
    const external = externalTarget.current === identity
    if (external) {
      externalTarget.current = null
      if (queryCommit.current !== null) {
        window.clearTimeout(queryCommit.current)
        queryCommit.current = null
      }
      pendingLocalNavigations.current.length = 0
      localNavigations.current.clear()
      ++queryDraftVersion.current
      ++pageNavigationVersion.current
      ++focusVersion.current
      canonicalQuery.current = current
      setQueryText(current)
      requestedPage.current = search.page
      return
    }
    const local = localNavigations.current.get(identity)
    if (local) {
      localNavigations.current.delete(identity)
      canonicalQuery.current = local.q
      requestedPage.current = search.page
      return
    }
    if (canonicalQuery.current === current) {
      requestedPage.current = search.page
      return
    }
    externalTarget.current = null
    if (queryCommit.current !== null) {
      window.clearTimeout(queryCommit.current)
      queryCommit.current = null
    }
    pendingLocalNavigations.current.length = 0
    localNavigations.current.clear()
    ++queryDraftVersion.current
    ++pageNavigationVersion.current
    ++focusVersion.current
    canonicalQuery.current = current
    setQueryText(current)
    requestedPage.current = search.page
  }, [resolvedLocation, search.page, search.q])
  useEffect(() => {
    if (focusVersion.current !== focusRequest?.version) return
    let outer: number | null = null
    let inner: number | null = null
    let active = true
    outer = requestAnimationFrame(() => {
      outer = null
      inner = requestAnimationFrame(() => {
        inner = null
        if (active && focusVersion.current === focusRequest.version)
          document
            .querySelector<HTMLElement>(`[data-activity-focus="${focusRequest.key}"]`)
            ?.focus({ preventScroll: true })
      })
    })
    return () => {
      active = false
      if (outer !== null) cancelAnimationFrame(outer)
      if (inner !== null) cancelAnimationFrame(inner)
    }
  }, [focusRequest, search.eventType, search.page, search.pageSize, search.q, search.rangeDays])
  const routeIdentity = 'activity'
  useRouteReadyFocus(routeIdentity)
  const live = useLiveStream() === 'live'
  const query = useQuery({
    queryKey: [
      'activity',
      search.q ?? null,
      search.eventType,
      search.rangeDays,
      search.page,
      search.pageSize,
    ],
    queryFn: (queryContext) =>
      http.getActivity(search, queryContext.signal, queryWorkClass(queryContext)),
    initialData: initial,
    staleTime: 4_000,
    retry: false,
    // Five seconds was the only reason a write that started between polls ever
    // became visible. It survives as the fallback for a stream that is down;
    // while one is up, the write lane announces each transition itself (#641).
    refetchInterval: live ? false : 5_000,
  })
  const data = query.data
  const preparation = usePreparation().data
  const connectionConditions = preparation?.connections ?? []
  const connection = connectionConditions.find((item) => item.state !== 'recovered')
  const ledger = data.ledger
  const rows = ledgerRows(data.events)
  const eventsElement = useRef<HTMLDivElement | null>(null)
  // The focused ledger control, and the focus key of the checks toggle whose
  // list holds it, if any.
  const ledgerFocus = useRef<{ control: HTMLElement; toggle: string | undefined } | null>(null)
  // A focused ledger control that a changed page removed hands focus to its
  // replacement, then to its group's toggle, then to the ledger heading; it
  // never falls to the document.
  useLayoutEffect(() => {
    const removed = ledgerFocus.current
    if (!removed || removed.control.isConnected) return
    ledgerFocus.current = null
    if (document.activeElement !== null && document.activeElement !== document.body) return
    const controls = [
      ...(eventsElement.current?.querySelectorAll<HTMLElement>('[data-activity-focus]') ?? []),
    ]
    const find = (key: string | undefined) =>
      key ? controls.find((control) => control.dataset.activityFocus === key) : undefined
    ;(
      find(removed.control.dataset.activityFocus) ??
      find(removed.toggle) ??
      document.getElementById('activity-ledger-title')
    )?.focus({ preventScroll: true })
  }, [data])
  // Filled after mount, so the status region exists before what it announces.
  const unrecognisedFilter = search.filter === 'unrecognised'
  const [filterNotice, setFilterNotice] = useState('')
  useEffect(() => {
    const frame = requestAnimationFrame(() =>
      setFilterNotice(
        unrecognisedFilter ? 'That activity filter is not recognised. Showing all events.' : '',
      ),
    )
    return () => cancelAnimationFrame(frame)
  }, [unrecognisedFilter])
  const [consentOpen, setConsentOpen] = useState(
    () => router.state.location.hash === 'activity-consent',
  )
  const consentTrigger = useRef<HTMLElement | null>(null)
  const openingPermissionCheck = useRef(consentOpen)
  const nextPermissionRequest = useRef(0)
  const [permissionRequest, setPermissionRequest] = useState<number | null>(null)
  const permissions = useQuery({
    queryKey: ['activity-permissions', permissionRequest],
    queryFn: async (context): Promise<PermissionCheck> => {
      const [readiness, policy] = await Promise.all([
        http.getActivityPermissionReadiness(context.signal, queryWorkClass(context)),
        http.getPolicyState(context.signal, queryWorkClass(context)),
      ])
      return {
        ...readiness,
        policyId: policy.policy_id,
        basis: consentBasis(readiness, policy.policy_id),
      }
    },
    enabled: consentOpen && permissionRequest !== null,
    retry: false,
    staleTime: Infinity,
    gcTime: 0,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
  })
  // The draft outlives refetches, focus, live updates and same-basis retries;
  // only a result for the current request establishes or replaces it, and a
  // replacement keeps the ticks while the basis holds.
  const checked = permissionRequest === null ? undefined : permissions.data
  const [discarded, setDiscarded] = useState<object | null>(null)
  const [draft, setDraft] = useState<{
    check: PermissionCheck
    checkedAt: number
    names: ReadonlySet<string>
  } | null>(null)
  if (checked && draft?.check !== checked) {
    const kept = draft?.check.basis === checked.basis
    setDraft({
      check: checked,
      checkedAt: permissions.dataUpdatedAt,
      names: kept ? draft.names : new Set(),
    })
    // A retry that finds another basis clears the ticks, and says so.
    if (draft && !kept) setDiscarded({})
  }
  const permission = permissionRequest === null || permissions.isError ? undefined : draft?.check
  const checking = permissions.isFetching && permissionRequest !== null
  const confirming = useRef(false)
  const discardPermissions = () => {
    setPermissionRequest(null)
    setDraft(null)
  }
  const checkButton = useRef<HTMLButtonElement>(null)
  const discardChangedDraft = () => {
    discardPermissions()
    setDiscarded({})
  }
  // An Activity response settled after the check states other libraries or
  // destinations than the draft was checked against.
  if (
    draft &&
    query.dataUpdatedAt > draft.checkedAt &&
    consentBasis(data, draft.check.policyId) !== draft.check.basis
  )
    discardChangedDraft()
  useEffect(() => {
    if (!discarded) return
    // Focus that was in the panel, or fell when its controls unmounted, goes to
    // the one control that can start the next check.
    const active = document.activeElement
    if (
      active === null ||
      active === document.body ||
      document.getElementById('activity-consent')?.contains(active)
    )
      checkButton.current?.focus({ preventScroll: true })
  }, [discarded])
  useEffect(() => {
    if (!openingPermissionCheck.current || query.isFetching) return
    // Let the entering history query publish its final cache event before the
    // hash handoff starts a check. Later history keeps the draft unless its
    // consent basis changed.
    const frame = requestAnimationFrame(() => {
      if (!openingPermissionCheck.current) return
      openingPermissionCheck.current = false
      setPermissionRequest(++nextPermissionRequest.current)
    })
    return () => cancelAnimationFrame(frame)
  }, [query.isFetching])
  const checkPermissions = () => {
    openingPermissionCheck.current = false
    setDiscarded(null)
    setPermissionRequest(++nextPermissionRequest.current)
  }
  const openConsentReview = () => {
    consentTrigger.current =
      document.activeElement instanceof HTMLElement ? document.activeElement : null
    setConsentOpen(true)
    checkPermissions()
  }
  useEffect(() => {
    if (!consentOpen) return
    const section = document.getElementById('activity-consent')
    section?.focus({ preventScroll: true })
    section?.scrollIntoView({ block: 'nearest' })
  }, [consentOpen])
  const closeConsentReview = (succeeded = false) => {
    openingPermissionCheck.current = false
    setConsentOpen(false)
    setDiscarded(null)
    discardPermissions()
    // A hash handoff has no trigger of its own; the page's entry stands in.
    const trigger =
      consentTrigger.current ?? document.querySelector<HTMLElement>('[data-consent-entry]')
    requestAnimationFrame(() => {
      const target =
        trigger?.isConnected && !trigger.matches(':disabled')
          ? trigger
          : document.querySelector<HTMLElement>(
              succeeded ? '.cm-activity-route > .activity-command-status' : '[data-route-focus]',
            )
      target?.focus({ preventScroll: true })
      if (target !== trigger) target?.scrollIntoView({ block: 'nearest' })
    })
    if (router.state.location.hash === 'activity-consent')
      void navigate({ search: (current) => current, hash: '', replace: true })
  }
  const configured = permission?.configured_libraries ?? []
  const enabled = data.enabled_libraries
  const capability = (library: string) =>
    permission?.capabilities.find((item) => item.library === library)
  const preparedCount = (library: ActivityLibraryView) => library.prepared?.total ?? 0
  const consentCandidates = configured.filter((item) =>
    (capability(item.library)?.destinations ?? []).some(
      (destination) => destination.state === 'allowed' && !destination.consented,
    ),
  )
  const selectable = consentCandidates.filter((item) => {
    const state = capability(item.library)?.state ?? 'not_ready'
    return (
      permission?.backup_ready === true &&
      !connection &&
      ['allowed', 'partially_allowed'].includes(state) &&
      preparedCount(item) > 0
    )
  })
  const selectableNames = new Set(selectable.map((item) => item.library))
  const selected = permission && draft ? draft.names : new Set<string>()
  const [status, setStatus] = useState('')
  const [expandedId, setExpandedId] = useState<string | null>(null)
  // A checks row opens apart from any one record's Details, and both survive a
  // refetch; only a new search or page closes them.
  const [expandedChecks, setExpandedChecks] = useState<string | null>(null)
  const mutation = useMutation({
    mutationFn: async ({ path, body, basis }: { path: string; body?: unknown; basis?: string }) => {
      // Re-read the basis the draft was reviewed against, freshly and before
      // any grant is sent; the server still checks the grant itself.
      if (basis !== undefined) {
        let current: string
        try {
          const [state, policy] = await Promise.all([
            http.getActivityConsent(),
            http.getPolicyState(),
          ])
          current = consentBasis(state, policy.policy_id)
        } catch {
          throw new ConsentBasisUnavailable()
        }
        if (current !== basis) throw new ConsentBasisChanged()
      }
      return http.activityCommand(path, body)
    },
    retry: false,
    // A new command starts silent, so even an identical outcome is announced.
    onMutate: () => setStatus(''),
    onSuccess: async () => {
      closeConsentReview(true)
      setStatus('Activity state refreshed from the authoritative server.')
      await invalidateForegroundQueries(queryClient, { queryKey: ['activity'] })
    },
    onError: async (error, command) => {
      // Refused before any effect: keep the reviewed selection and readiness so
      // the same press can be repeated, and confirmation re-validates them (#1018).
      if (error instanceof CsrfRenewedError) {
        setStatus(sessionRefreshedCopy)
        return
      }
      if (error instanceof ConsentBasisUnavailable) {
        setStatus(readinessUnavailableCopy)
        return
      }
      if (error instanceof ConsentBasisChanged) {
        discardChangedDraft()
        await invalidateForegroundQueries(queryClient, { queryKey: ['activity'] })
        return
      }
      discardPermissions()
      if (
        error instanceof AmbiguousMutationError ||
        error instanceof BuildMismatchError ||
        (error instanceof ApiError && error.status >= 500)
      ) {
        try {
          const refreshed = await query.refetch()
          const outcome = refreshed.isError ? 'unknown' : activityOutcome(refreshed.data, command)
          setStatus(
            outcome === 'applied'
              ? 'The requested activity change was applied, confirmed by the authoritative server.'
              : 'The activity outcome could not be confirmed. No command was resent.',
          )
        } catch {
          setStatus('The activity outcome could not be confirmed. No command was resent.')
        }
        return
      }
      setStatus(
        error instanceof ApiError &&
          command.path === 'activity/consent' &&
          error.code === 'capability'
          ? 'The destinations changed since you reviewed them. No permission was granted. Check write permissions again, then confirm.'
          : error instanceof ApiError &&
              command.path === 'activity/consent' &&
              error.code === 'backup'
            ? 'Backup readiness changed. No permission was granted. Restore backup readiness before confirming again.'
            : 'The requested activity change failed safely. Try again.',
      )
      await invalidateForegroundQueries(queryClient, { queryKey: ['activity'] })
    },
  })
  const confirmBusy = mutation.isPending || permissions.isFetching
  const commandStatusRef = useRef<HTMLParagraphElement>(null)
  useEffect(() => {
    if (!mutation.isError || !status) return
    commandStatusRef.current?.focus({ preventScroll: true })
    commandStatusRef.current?.scrollIntoView({ block: 'center' })
  }, [mutation.isError, status])
  const selectedGrants = selectable.flatMap((library) => {
    const name = library.library
    return selected.has(name)
      ? [
          {
            library: name,
            destinations: (capability(name)?.destinations ?? [])
              .filter((destination) => destination.state === 'allowed')
              .map((destination) => destination.destination),
          },
        ]
      : []
  })
  const toggle = (library: string) => {
    if (!draft) return
    const next = new Set(selected)
    if (next.has(library)) next.delete(library)
    else next.add(library)
    setDraft({ ...draft, names: next })
  }
  const restoreFocus = (key: string, version: number) => {
    if (focusVersion.current !== version) return
    setFocusRequest({ key, version })
  }
  const changeSearch = (next: ActivitySearchUpdate, focusKey?: string) => {
    setExpandedId(null)
    setExpandedChecks(null)
    ++pageNavigationVersion.current
    const resetsPage =
      Object.hasOwn(next, 'q') ||
      Object.hasOwn(next, 'eventType') ||
      Object.hasOwn(next, 'rangeDays') ||
      Object.hasOwn(next, 'pageSize')
    if (resetsPage) requestedPage.current = 1
    const base: ActivitySearch = {
      eventType: next.eventType ?? search.eventType,
      rangeDays: next.rangeDays ?? search.rangeDays,
      pageSize: next.pageSize ?? search.pageSize,
      page: resetsPage ? 1 : (next.page ?? 1),
      ...(!Object.hasOwn(next, 'q') && search.q ? { q: search.q } : {}),
      ...(next.q ? { q: next.q } : {}),
    }
    const key = focusKey ?? document.activeElement?.getAttribute('data-activity-focus') ?? undefined
    const version = ++focusVersion.current
    pendingLocalNavigations.current.push({
      q: base.q ?? '',
      draftVersion: queryDraftVersion.current,
    })
    void navigate({ search: base, replace: true }).then(() => {
      if (key) restoreFocus(key, version)
    })
  }
  const changeQuery = (value: string) => {
    ++focusVersion.current
    setQueryText(value)
    ++queryDraftVersion.current
    if (queryCommit.current !== null) window.clearTimeout(queryCommit.current)
    queryCommit.current = window.setTimeout(() => {
      queryCommit.current = null
      // Activity stays painted while the next route's code loads, but no longer owns the URL.
      if (router.latestLocation.pathname !== '/activity') return
      changeSearch({ q: value || undefined }, 'search')
    }, 150)
  }
  const clearFilters = () => {
    ++queryDraftVersion.current
    if (queryCommit.current !== null) {
      window.clearTimeout(queryCommit.current)
      queryCommit.current = null
    }
    setQueryText('')
    changeSearch({ q: undefined, eventType: 'all' }, 'clear-filters')
  }
  const goToPage = (target: number | ((page: number) => number), focusKey?: string) => {
    const requested = typeof target === 'function' ? target(requestedPage.current) : target
    const clamped = Math.max(1, Math.min(ledger.page_count, requested))
    requestedPage.current = clamped
    ++pageNavigationVersion.current
    setExpandedId(null)
    setExpandedChecks(null)
    const key = focusKey ?? document.activeElement?.getAttribute('data-activity-focus') ?? undefined
    const restoredKey =
      (clamped === 1 && (key === 'first' || key === 'previous')) ||
      (clamped === ledger.page_count && (key === 'next' || key === 'last'))
        ? 'page-jump'
        : key
    const version = ++focusVersion.current
    // Paging is a navigation like any other, so it drops the unrecognised
    // filter flag along with the notice it raised.
    const next: ActivitySearch = {
      eventType: search.eventType,
      rangeDays: search.rangeDays,
      pageSize: search.pageSize,
      page: clamped,
      ...(search.q ? { q: search.q } : {}),
    }
    pendingLocalNavigations.current.push({
      q: next.q ?? '',
      draftVersion: queryDraftVersion.current,
    })
    void navigate({ search: () => next }).then(() => {
      if (restoredKey) restoreFocus(restoredKey, version)
    })
  }
  const suspensions = data.suspensions
  const writes = ['claimed', 'applying', 'reconciling'].reduce(
    (total, state) => total + (data.counts[state] ?? 0),
    0,
  )
  const providerRecovery = data.provider_recovery ?? []
  const dormantRecovery = providerRecovery
    .filter((item) => item.state === 'dormant')
    .reduce((total, item) => total + item.affected_count, 0)
  // One condition remains focused for the banner/action, but recovery totals
  // must reflect every independently paused/checking connection.
  const connectionRecovery = connectionConditions.reduce(
    (total, item) => total + (item.state === 'recovered' ? 0 : item.affected),
    0,
  )
  const recoveryCount = suspensions.length + dormantRecovery + connectionRecovery
  const reasons = data.waiting
  const waitingEvent = data.current_waiting
  const needsConsent = data.configured_libraries.length > 0
  const enabling = mutation.isPending && mutation.variables.path === 'activity/consent'
  // Library states the same offer compactly, from this same reading, so the two
  // routes into the decision can never disagree about it (#639).
  const consent = readWriteConsent(permission ? { ...data, ...permission } : data, {
    enabling,
    // A connection that needs an owner check is the one condition that makes
    // enabling writes the wrong next step. Preparation still running is not:
    // permission given now covers the work preparation is still finding.
    connectionUnavailable: Boolean(connection),
  })
  const offer = consent.scopes
  const offeredChanges = consent.changes
  const consentState = consent.state
  const consentBlocked = consent.blocked
  const consentActionable = consent.actionable
  // One reading of "prepared work is waiting on the owner", shared by the
  // masthead pill, the status panel and the current waiting item. #639 asks
  // that no screen say no action is required while its own displayed prepared
  // work requires consent, and three cascades each deciding that separately is
  // how one of them came to say it directly above the count that refutes it.
  const permissionNeeded = consentState === 'off' && reasons.awaiting_permission > 0
  // The disclosure holds pause, removal and recovery only; reviewing write
  // permissions has one entry, outside it (#1279).
  const hasAuthority = enabled.length > 0 || suspensions.length > 0
  const cardOffers = consentActionable && (consentState === 'off' || consentState === 'enabling')
  const statusEntry = needsConsent && !cardOffers
  const showOperationalSummary =
    !permissionNeeded || Boolean(connection) || recoveryCount > 0 || data.paused
  const consentFeedback = consentOpen && mutation.variables?.path === 'activity/consent'
  // Pause, resume and revoke report beside the controls that issued them. The
  // authority and page lines both stay mounted, so a live region is never
  // created at the moment it is filled; only the line that owns the outcome
  // carries it, the focus ref and the alert role.
  const authorityFeedback =
    hasAuthority &&
    mutation.variables !== undefined &&
    mutation.variables.path !== 'activity/consent'
  // The entry moves between the card and the status section as the state
  // changes; focus on the one that left follows it to the one that arrived.
  const focusedEntry = useRef<Element | null>(null)
  useEffect(() => {
    const last = focusedEntry.current
    if (!last || (last.isConnected && last.hasAttribute('data-consent-entry'))) return
    const active = document.activeElement
    if (active !== last && active !== document.body) return
    ;(
      document.querySelector<HTMLElement>('[data-consent-entry]') ??
      document.querySelector<HTMLElement>('[data-route-focus]')
    )?.focus({ preventScroll: true })
  }, [cardOffers, statusEntry])
  const commandStatus = (owner: boolean) => (
    <p
      className="activity-command-status"
      ref={owner ? commandStatusRef : undefined}
      tabIndex={-1}
      role={owner && mutation.isError ? 'alert' : 'status'}
    >
      {owner ? status : ''}
    </p>
  )
  return (
    <div
      className="cm-activity-route"
      onFocusCapture={(event) => {
        ++focusVersion.current
        focusedEntry.current = event.target.hasAttribute('data-consent-entry') ? event.target : null
      }}
    >
      <header className="cm-page-heading" tabIndex={-1} data-route-focus>
        <div>
          <h1>Activity and recovery</h1>
          <p>
            See what needs attention now and inspect the durable record of reviews, policy changes,
            safeguards, and verified updates.
          </p>
        </div>
        <span className="cm-activity-calm">
          {connection?.state === 'checking'
            ? 'Connection checking'
            : connection?.state === 'paused'
              ? 'Connection paused'
              : recoveryCount
                ? 'Recovery available'
                : permissionNeeded
                  ? 'Action needed'
                  : consentState === 'enabling'
                    ? 'Enabling writes'
                    : consentState === 'enabled' || consentState === 'applying'
                      ? 'Writes enabled'
                      : data.paused
                        ? 'New work paused'
                        : 'No action required'}
        </span>
      </header>
      <section className="cm-activity-ledger" aria-labelledby="activity-ledger-title">
        <header className="cm-activity-ledger-heading">
          <div>
            <h2 id="activity-ledger-title" tabIndex={-1}>
              Activity ledger
            </h2>
            <p>
              One record for review decisions, policy changes, safeguards, writes, and verification.
            </p>
          </div>
          <label className="cm-activity-range">
            <span>Date range</span>
            <Dropdown
              compact
              focusKey="date-range"
              ariaLabel="Activity date range"
              value={String(search.rangeDays)}
              options={[
                { value: '7', label: 'Last 7 days' },
                { value: '30', label: 'Last 30 days' },
                { value: '90', label: 'Last 90 days' },
              ]}
              onChange={(value) =>
                changeSearch(
                  { rangeDays: Number(value) as ActivitySearch['rangeDays'] },
                  'date-range',
                )
              }
            />
          </label>
        </header>
        <div className="cm-activity-tools">
          <label className="cm-activity-search">
            <span>Search activity</span>
            <input
              data-activity-focus="search"
              aria-label="Search activity"
              type="search"
              value={queryText}
              placeholder="Work, outcome, or status"
              onChange={(event) => changeQuery(event.target.value)}
            />
          </label>
          <div className="cm-activity-filters" aria-label="Filter activity by type">
            {typeOptions.map((item) => (
              <button
                data-activity-focus={`type-${item.id}`}
                key={item.id}
                aria-pressed={search.eventType === item.id}
                onClick={() => changeSearch({ eventType: item.id }, `type-${item.id}`)}
              >
                {item.label}
              </button>
            ))}
          </div>
        </div>
        <p className="cm-activity-filter-notice" role="status">
          {filterNotice}
        </p>
        <div className="cm-activity-columns" aria-hidden="true">
          <span>When</span>
          <span>Type</span>
          <span>Work and field</span>
          <span>Change</span>
          <span>Result</span>
          <span />
        </div>
        <div
          className="cm-activity-events"
          ref={eventsElement}
          onFocus={(event) => {
            // A member's list is `activity-checks-{key}`; its toggle's focus
            // key is `checks-{key}`.
            const list = event.target.closest('.cm-activity-checks')
            ledgerFocus.current = {
              control: event.target,
              toggle: list ? list.id.replace(/^activity-/u, '') : undefined,
            }
          }}
          onBlur={(event) => {
            // A control the user left is forgotten; one a refetch removed is
            // still remembered, whether or not the engine fired a blur for it.
            const left = event.target
            queueMicrotask(() => {
              // A window blur fires this while the control keeps focus; keep it.
              if (
                left.isConnected &&
                ledgerFocus.current?.control === left &&
                document.activeElement !== left
              )
                ledgerFocus.current = null
            })
          }}
        >
          {data.events.length ? (
            rows.map((row, index) => {
              const first = row.kind === 'event' ? row.event : row.members[0]
              if (!first) return null
              const day = dateParts(first.occurred_at).day
              const previous = rows[index - 1]
              const previousDay = previous
                ? dateParts(
                    (previous.kind === 'event' ? previous.event : previous.members[0])
                      ?.occurred_at ?? null,
                  ).day
                : null
              const heading = day !== previousDay ? <h3>{day}</h3> : null
              if (row.kind === 'event') {
                const changeId = row.event.change_id || row.event.event_id
                const expanded = expandedId === changeId
                return (
                  <div className="cm-activity-event-group" key={changeId}>
                    {heading}
                    <ActivityEventRow
                      event={row.event}
                      expanded={expanded}
                      onToggle={() => setExpandedId(expanded ? null : changeId)}
                    />
                  </div>
                )
              }
              const open = expandedChecks === row.key
              const fields = new Set(row.members.map((member) => member.destination)).size
              const sentence = `Checked ${String(fields)} ${fields === 1 ? 'field' : 'fields'}, no changes`
              return (
                <div className="cm-activity-event-group" key={`checks-${row.key}`}>
                  {heading}
                  <article className={`is-checks${open ? ' is-expanded' : ''}`}>
                    <time>{dateParts(first.occurred_at).time}</time>
                    <span className="cm-activity-category is-automatic">
                      {categoryLabel.automatic}
                    </span>
                    <div className="cm-activity-work">
                      <strong title={first.work_label}>{first.work_label}</strong>
                      <span>{first.library}</span>
                    </div>
                    <div className="cm-activity-change">
                      <span>{sentence}</span>
                    </div>
                    <b>{stateLabel('no_change_required')}</b>
                    <button
                      data-activity-focus={`checks-${row.key}`}
                      aria-expanded={open}
                      aria-controls={`activity-checks-${row.key}`}
                      onClick={() => setExpandedChecks(open ? null : row.key)}
                    >
                      {open ? 'Hide details' : 'Details'}
                    </button>
                  </article>
                  {open ? (
                    <ul className="cm-activity-checks" id={`activity-checks-${row.key}`}>
                      {row.members.map((member) => {
                        const changeId = member.change_id || member.event_id
                        const expanded = expandedId === changeId
                        const memberDay = dateParts(member.occurred_at).day
                        return (
                          <li key={changeId}>
                            <ActivityEventRow
                              event={member}
                              expanded={expanded}
                              onToggle={() => setExpandedId(expanded ? null : changeId)}
                              {...(memberDay === day ? {} : { day: memberDay })}
                            />
                          </li>
                        )
                      })}
                    </ul>
                  ) : null}
                </div>
              )
            })
          ) : (
            <div className="cm-activity-empty">
              <strong>No activity matches these filters</strong>
              <span>Try another term or activity type.</span>
              <button data-activity-focus="clear-filters" onClick={clearFilters}>
                Clear filters
              </button>
            </div>
          )}
        </div>
        <footer className="cm-activity-pagination">
          <div className="cm-activity-pagination-summary">
            <strong>
              {ledger.total
                ? `${String((ledger.page - 1) * ledger.page_size + 1)}–${String(Math.min(ledger.page * ledger.page_size, ledger.total))}`
                : '0'}
            </strong>
            <span>of {ledger.total} events</span>
            <label className="cm-activity-page-size">
              <span>Events</span>
              <Dropdown
                compact
                focusKey="page-size"
                ariaLabel="Events per activity page"
                value={String(search.pageSize)}
                options={[
                  { value: '25', label: '25 per page' },
                  { value: '50', label: '50 per page' },
                  { value: '100', label: '100 per page' },
                ]}
                onChange={(value) =>
                  changeSearch(
                    { pageSize: Number(value) as ActivitySearch['pageSize'] },
                    'page-size',
                  )
                }
              />
            </label>
          </div>
          <nav className="cm-activity-pagination-nav" aria-label="Activity pages">
            <button
              data-activity-focus="first"
              disabled={ledger.page === 1}
              onClick={() => goToPage(1, 'first')}
            >
              First
            </button>
            <button
              data-activity-focus="previous"
              disabled={ledger.page === 1}
              onClick={() => goToPage((page) => page - 1, 'previous')}
            >
              Previous
            </button>
            <PageJump
              key={`${String(ledger.page)}:${search.q ?? ''}:${search.eventType}:${String(search.rangeDays)}:${String(search.pageSize)}`}
              page={ledger.page}
              pageCount={ledger.page_count}
              onCommit={goToPage}
              onEdit={() => {
                ++focusVersion.current
              }}
            />
            <button
              data-activity-focus="next"
              disabled={ledger.page >= ledger.page_count}
              onClick={() => goToPage((page) => page + 1, 'next')}
            >
              Next
            </button>
            <button
              data-activity-focus="last"
              disabled={ledger.page >= ledger.page_count}
              onClick={() => goToPage(ledger.page_count, 'last')}
            >
              Last
            </button>
          </nav>
        </footer>
      </section>
      {consentActionable ? (
        <section
          className={`cm-write-consent is-${consentState}`}
          aria-labelledby="write-consent-title"
          aria-live="polite"
        >
          <div className="cm-write-consent-summary">
            <div>
              <h2 id="write-consent-title">
                {consentState === 'off'
                  ? permission?.backup_ready === true
                    ? `${String(offeredChanges)} prepared ${offeredChanges === 1 ? 'change is' : 'changes are'} ready. Writes are off.`
                    : 'Writes are off. Review permissions when you are ready.'
                  : consentState === 'enabling'
                    ? 'Enabling writes…'
                    : consentState === 'enabled'
                      ? 'Writes are enabled.'
                      : 'Applying prepared changes.'}
              </h2>
              <p>
                {consentState === 'off'
                  ? 'Open permission review to check backup readiness and the destinations available for writes.'
                  : consentState === 'enabling'
                    ? 'Confirming permission with the authoritative server. No change is sent twice.'
                    : consentState === 'enabled'
                      ? 'Permission is confirmed. Mechanical Librarian is starting the prepared changes.'
                      : `${String(reasons.applying)} ${reasons.applying === 1 ? 'change is' : 'changes are'} in progress; the remaining ${String(reasons.scheduled)} stay ready.`}
              </p>
            </div>
            <dl>
              <div>
                <dt>Awaiting permission</dt>
                <dd>{reasons.awaiting_permission}</dd>
              </div>
              <div>
                <dt>Ready to apply</dt>
                <dd>{reasons.scheduled}</dd>
              </div>
              <div>
                <dt>Applying now</dt>
                <dd>{reasons.applying}</dd>
              </div>
              <div>
                <dt>Retrying</dt>
                <dd>{reasons.retrying}</dd>
              </div>
              <div>
                <dt>Held for recovery</dt>
                <dd>{reasons.held_for_recovery}</dd>
              </div>
            </dl>
          </div>
          {!consentOpen
            ? offer.map((item) => (
                <div className="cm-write-consent-scope" key={item.library}>
                  <div>
                    <strong>{item.library}</strong>
                    <span>
                      {item.eligible
                        .map(
                          (entry) =>
                            `${String(entry.prepared)} ${fieldPhrase(writeFieldLabel(entry.destination))} ${entry.prepared === 1 ? 'change' : 'changes'}`,
                        )
                        .join(' · ') || 'No eligible destination'}
                    </span>
                  </div>
                  <dl>
                    {[...item.eligible, ...item.excluded].map((entry) => (
                      <div key={entry.destination}>
                        <dt>{writeFieldLabel(entry.destination)}</dt>
                        <dd>
                          {entry.state === 'allowed'
                            ? consentState === 'off' || consentState === 'enabling'
                              ? 'Writes off'
                              : 'Writes enabled'
                            : `Excluded — ${destinationReason(entry.state)}`}
                        </dd>
                      </div>
                    ))}
                  </dl>
                </div>
              ))
            : null}
          <div className="cm-write-consent-action">
            <button
              className="cm-primary"
              type="button"
              data-consent-entry={cardOffers ? '' : undefined}
              disabled={consentState !== 'off' || consentOpen}
              aria-expanded={consentOpen}
              aria-controls="activity-consent"
              onClick={openConsentReview}
            >
              {consentState === 'off'
                ? 'Review write permissions'
                : consentState === 'enabling'
                  ? 'Enabling writes…'
                  : consentState === 'applying'
                    ? 'Applying changes…'
                    : 'Writes enabled'}
            </button>
            {consentBlocked ? <p className="cm-write-consent-reason">{consentBlocked}</p> : null}
          </div>
        </section>
      ) : null}
      {consentOpen ? (
        <section
          className="activity-consent-ledger"
          id="activity-consent"
          tabIndex={-1}
          aria-labelledby="activity-consent-title"
        >
          <div>
            <p className="eyebrow">Prepared, read-only work</p>
            <h2 id="activity-consent-title">Enable guarded writes</h2>
            <p>
              Each write is checked before it starts, verified afterwards, and recorded durably.
            </p>
          </div>
          <p role="status">
            {discarded
              ? basisChangedCopy
              : permissionRequest === null
                ? 'Permission readiness has not been checked, or the library changed. Check again before selecting destinations.'
                : permissions.isFetching
                  ? 'Checking backup readiness and write permissions…'
                  : permissions.isError
                    ? readinessUnavailableCopy
                    : permission?.backup_ready === null
                      ? 'Readiness could not be confirmed for the current library state. Check again before enabling writes.'
                      : permission?.backup_ready === false
                        ? 'Backup readiness could not be confirmed. Writes cannot be enabled.'
                        : 'Backup readiness checked. Review the included and excluded destinations.'}
          </p>
          {/* Busy, not disabled: a disabled button would drop a keyboard owner's focus to <body>. */}
          <button
            ref={checkButton}
            type="button"
            onClick={checking ? undefined : checkPermissions}
            aria-disabled={checking || undefined}
          >
            {permissionRequest === null ? 'Check write permissions' : 'Retry permission check'}
          </button>
          {permission?.example ? (
            <p>
              Example: {writeFieldLabel(permission.example.destination)} for{' '}
              {permission.example.library} — {displayValue(permission.example.before)} →{' '}
              {displayValue(permission.example.after)}
            </p>
          ) : null}
          {configured.map((library) => {
            const name = library.library
            const state = capability(name)?.state ?? 'not_ready'
            const disabled = !selectableNames.has(name)
            return (
              <div className="activity-consent-library" key={name}>
                <label>
                  <input
                    type="checkbox"
                    disabled={disabled}
                    checked={selected.has(name)}
                    onChange={() => toggle(name)}
                  />
                  <strong>{name}</strong>
                  <small>
                    {preparedCount(library)} prepared ·{' '}
                    {permission?.backup_ready === true
                      ? capabilityLabel(state)
                      : 'Readiness unconfirmed'}
                  </small>
                </label>
                <ul aria-label={`${name} included and excluded destinations`}>
                  {capability(name)?.destinations.map((entry) => (
                    <li key={entry.destination}>
                      <span>{writeFieldLabel(entry.destination)}</span>
                      <small>
                        {entry.state === 'allowed'
                          ? `Included · ${String(entry.prepared)} prepared`
                          : `Excluded — ${destinationReason(entry.state)}`}
                      </small>
                    </li>
                  ))}
                </ul>
              </div>
            )
          })}
          {configured.some((library) =>
            capability(library.library)?.destinations.some((entry) => entry.state === 'allowed'),
          ) ? (
            <p>
              Only the included destinations shown above are enabled. Excluded destinations remain
              off. Backup and service capability are checked again on confirmation.
            </p>
          ) : null}
          {consentFeedback ? commandStatus(true) : null}
          <div className="activity-consent-actions">
            <button
              type="button"
              onClick={() => closeConsentReview()}
              disabled={mutation.isPending}
            >
              Cancel permission review
            </button>
            <p id="activity-consent-requirement">
              Select each library you want to include, then confirm.
            </p>
            <button
              className="primary-action"
              aria-describedby="activity-consent-requirement"
              // Busy, not disabled, so focus stays here; the ref refuses a
              // second press that lands before the pending state renders.
              aria-disabled={confirmBusy || undefined}
              disabled={
                !confirmBusy && (permission?.backup_ready !== true || selectedGrants.length === 0)
              }
              onClick={() => {
                if (confirmBusy || confirming.current || !draft) return
                confirming.current = true
                mutation.mutate(
                  {
                    path: 'activity/consent',
                    body: { grants: selectedGrants },
                    basis: draft.check.basis,
                  },
                  {
                    onSettled: () => {
                      confirming.current = false
                    },
                  },
                )
              }}
            >
              Confirm write permissions
            </button>
          </div>
        </section>
      ) : null}
      {/* Outcomes the card or a closed panel leave behind read directly under them. */}
      {commandStatus(!authorityFeedback && !consentFeedback)}
      <section
        className="cm-activity-status"
        aria-labelledby={showOperationalSummary ? 'activity-status-title' : undefined}
      >
        {showOperationalSummary ? (
          <div className="cm-activity-status-copy">
            <h2 id="activity-status-title">
              {connection?.state === 'checking'
                ? 'Checking library connection'
                : connection?.state === 'paused'
                  ? 'Library connection is paused'
                  : recoveryCount
                    ? 'Recovery needs attention'
                    : data.paused
                      ? 'New work is paused'
                      : 'No intervention needed'}
            </h2>
            <p>
              {connection?.state === 'checking'
                ? 'Preparation remains safely paused while the authoritative connection check settles.'
                : connection?.state === 'paused'
                  ? `${String(connection.affected)} preparation ${connection.affected === 1 ? 'work remains' : 'works remain'} safely paused for an owner connection check.`
                  : dormantRecovery
                    ? `${String(dormantRecovery)} provider ${dormantRecovery === 1 ? 'scope needs' : 'scopes need'} owner correction before a future check can become eligible.`
                    : recoveryCount
                      ? 'Affected work remains held until its current state can be reconciled safely.'
                      : data.paused
                        ? 'No new work will be claimed. Work already claimed may finish its safe checkpoint; resume new work when you are ready for planned work to continue.'
                        : 'Guarded work continues automatically. Recovery controls appear only when your decision is required.'}
            </p>
            {statusEntry ? (
              <button
                type="button"
                data-consent-entry=""
                onClick={() => (consentOpen ? closeConsentReview() : openConsentReview())}
                aria-expanded={consentOpen}
                aria-controls="activity-consent"
              >
                Review write permissions
              </button>
            ) : null}
          </div>
        ) : null}
        {!consentActionable ? (
          <dl>
            <div>
              <dt>Writes in progress</dt>
              <dd>{writes}</dd>
            </div>
            <div>
              <dt>Awaiting permission</dt>
              <dd>{reasons.awaiting_permission}</dd>
            </div>
            <div>
              <dt>Scheduled</dt>
              <dd>{reasons.scheduled}</dd>
            </div>
            <div>
              <dt>Retrying</dt>
              <dd>{reasons.retrying}</dd>
            </div>
            <div>
              <dt>Held for recovery</dt>
              <dd>{reasons.held_for_recovery}</dd>
            </div>
            <div>
              <dt>Recovery needed</dt>
              <dd>{recoveryCount}</dd>
            </div>
          </dl>
        ) : null}
        {waitingEvent && !permissionNeeded ? (
          <div className="cm-activity-waiting">
            <span className="cm-activity-pulse" aria-hidden="true" />
            <div>
              <strong>
                {waitingEvent.work_label} · {writeFieldLabel(waitingEvent.destination)} update
              </strong>
              <span>
                {data.paused
                  ? 'Resume new work when you are ready. No automatic check will start while new work is paused.'
                  : 'Next exact-current check runs automatically. No action is required.'}
              </span>
            </div>
          </div>
        ) : null}
        {providerRecovery.length ? (
          <section className="activity-recovery" aria-label="Provider recovery">
            <h3>Provider recovery</h3>
            {providerRecovery.map((item, index) => (
              <p key={`${item.state}:${String(index)}`}>
                <strong>
                  {item.state === 'waiting'
                    ? 'Waiting for a safe retry'
                    : item.state === 'checking'
                      ? 'Checking provider health'
                      : item.state === 'dormant'
                        ? 'Needs a configuration change'
                        : item.state === 'fanout'
                          ? 'Retrying affected work'
                          : item.state === 'exhausted'
                            ? 'No further retry scheduled'
                            : 'Recovered'}
                </strong>{' '}
                · {item.affected_count} affected{' '}
                {item.state === 'fanout'
                  ? item.affected_count === 1
                    ? 'work'
                    : 'works'
                  : item.affected_count === 1
                    ? 'scope'
                    : 'scopes'}
                {item.next_eligible_at === null
                  ? ''
                  : ` · next eligible ${dateParts(item.next_eligible_at).time}`}
                {item.failure_reasons?.length ? (
                  <span>
                    {' '}
                    ·{' '}
                    {item.failure_reasons.map((reason) => recoveryFailureLabel[reason]).join('; ')}
                  </span>
                ) : null}
                {item.dispatch_reasons?.length ? (
                  <span>
                    {' '}
                    · Priority:{' '}
                    {item.dispatch_reasons
                      .map((reason) => recoveryDispatchLabel[reason])
                      .join(', ')}
                  </span>
                ) : null}
              </p>
            ))}
          </section>
        ) : null}
        {hasAuthority ? (
          <details className="cm-activity-authority">
            <summary>
              <span>
                <strong>Authority and recovery</strong>
                <small>
                  {recoveryCount
                    ? `${String(recoveryCount)} recovery ${recoveryCount === 1 ? 'item' : 'items'} need review`
                    : data.paused
                      ? 'New work remains paused until you resume it'
                      : 'Write permissions and pause controls'}
                </small>
              </span>
            </summary>
            <div
              className="cm-activity-authority-actions"
              aria-label="Activity authority and recovery"
            >
              {enabled.length > 0 ? (
                <button
                  disabled={mutation.isPending}
                  onClick={() =>
                    mutation.mutate({ path: `field-effects/${data.paused ? 'resume' : 'pause'}` })
                  }
                >
                  {data.paused ? 'Resume new work' : 'Pause new work'}
                </button>
              ) : null}
              {enabled.map((library) => (
                <button
                  className="activity-revoke"
                  key={`${library.connection}:${library.library}`}
                  disabled={mutation.isPending}
                  onClick={() =>
                    mutation.mutate({
                      path: `activity/${encodeURIComponent(library.connection)}/${encodeURIComponent(library.library)}/revoke`,
                    })
                  }
                >
                  Remove write permission for {library.library}
                </button>
              ))}
              {commandStatus(authorityFeedback)}
              {suspensions.length ? (
                <div className="cm-activity-recovery-facts" aria-label="Recovery facts">
                  {suspensions.map((item, index) => (
                    <p key={`${item.connection}:${item.destination ?? ''}:${String(index)}`}>
                      <strong>Recovery:</strong>{' '}
                      {fieldLabel(item.reason || 'A shared condition is being checked')}. No
                      accepted decision is retried blindly.
                    </p>
                  ))}
                </div>
              ) : null}
            </div>
          </details>
        ) : null}
      </section>
      <PreparationNotice routeIdentity={routeIdentity} />
    </div>
  )
}
