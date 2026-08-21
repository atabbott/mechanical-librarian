import { QueryClient, queryOptions, useMutation, useQuery } from '@tanstack/react-query'
import {
  Link,
  Navigate,
  Outlet,
  createRootRouteWithContext,
  createRoute,
  createRouter,
  useRouterState,
} from '@tanstack/react-router'
import { Suspense, useEffect, useLayoutEffect, useRef, useState } from 'react'

import type { createMemoryHistory } from '@tanstack/react-router'
import { BuildMismatchError } from './api/http'
import type {
  ActivitySearch,
  AttentionSearch,
  AutomaticSafetyView,
  DetailSearch,
  LibrarySearch,
  PolicySearch,
  PolicyStateView,
  PreparationView,
  ReviewHttp,
  ShadowPostureView,
} from './api/http'
import { ChunkRecoveryError, recoverableLazy, reloadCurrentApplication } from './chunkRecovery'
import { LiveStreamNotice } from './components/LiveStreamNotice'
import { useLiveInvalidation, useLiveStreamState } from './liveInvalidation'
import type { LiveUpdateStream } from './liveUpdates'

const LibraryPage = recoverableLazy(() => import('./routes/LibraryPage'))
const AttentionPage = recoverableLazy(() => import('./routes/AttentionPage'))
const DetailPage = recoverableLazy(() => import('./routes/DetailPage'))
const PolicyPage = recoverableLazy(() => import('./routes/PolicyPage'))
const ActivityPage = recoverableLazy(() => import('./routes/ActivityPage'))

export interface RouterContext {
  http: ReviewHttp
  queryClient: QueryClient
  /**
   * The tab's one live-update stream, absent where a composition has none.
   *
   * Absent is a supported posture rather than a broken one: every surface falls
   * back to the polling cadence it had before the stream existed, and says it
   * is doing so. That is also exactly the degraded posture, so the fallback is
   * exercised by the ordinary case rather than only by a failure (#641).
   */
  updates?: LiveUpdateStream
}
const bounded = (value: unknown) =>
  typeof value === 'string' && value.length > 0 && value.length <= 128 ? value : undefined
export const normalizeLibrarySearch = (search: Record<string, unknown>): LibrarySearch => {
  const lens =
    search.lens === 'needs_attention' || search.lens === 'no_attention_needed' ? search.lens : 'all'
  const q = bounded(search.q)
  const cursor = bounded(search.cursor)
  const preview = bounded(search.preview)
  // A work with no current review case has no case identity to preview, so its
  // selection is owned by its own key rather than by borrowing ``preview``.
  const work = bounded(search.work)
  const field = bounded(search.field)
  return {
    lens,
    ...(q ? { q } : {}),
    ...(cursor ? { cursor } : {}),
    ...(preview ? { preview } : {}),
    ...(preview ? {} : work ? { work } : {}),
    ...(field ? { field } : {}),
    ...(search.attention === 'open' ? { attention: 'open' } : {}),
  }
}
export const normalizeAttentionSearch = (search: Record<string, unknown>): AttentionSearch => {
  const q = bounded(search.q)
  const cursor = bounded(search.cursor)
  return { ...(q ? { q } : {}), ...(cursor ? { cursor } : {}) }
}
export const normalizeDetailSearch = (search: Record<string, unknown>): DetailSearch => {
  const field = bounded(search.field)
  const subview = search.subview === 'history' ? 'history' : 'evidence'
  return { ...(field ? { field } : {}), subview }
}
export const normalizePolicySearch = (search: Record<string, unknown>): PolicySearch => ({
  section:
    search.section === 'publication' ||
    search.section === 'curation' ||
    search.section === 'unsupported'
      ? search.section
      : 'identity',
})
export const normalizeActivitySearch = (search: Record<string, unknown>): ActivitySearch => {
  const eventType = ['review', 'policy', 'metadata', 'safety', 'unknown'].includes(
    String(search.eventType),
  )
    ? (String(search.eventType) as ActivitySearch['eventType'])
    : 'all'
  const rangeDays = [7, 90].includes(Number(search.rangeDays))
    ? (Number(search.rangeDays) as 7 | 90)
    : 30
  const pageSize = [50, 100].includes(Number(search.pageSize))
    ? (Number(search.pageSize) as 50 | 100)
    : 25
  const requestedPage = Number(search.page)
  const page =
    Number.isSafeInteger(requestedPage) && requestedPage >= 1 && requestedPage <= 1_000_000
      ? requestedPage
      : 1
  const q = bounded(search.q)
  return { eventType, rangeDays, pageSize, page, ...(q ? { q } : {}) }
}

export const libraryQuery = (http: ReviewHttp, search: LibrarySearch) =>
  queryOptions({
    queryKey: ['library', search.lens, search.q ?? null, search.cursor ?? null] as const,
    queryFn: ({ signal }) => http.getLibrary(search, signal),
    staleTime: 15_000,
    retry: false,
  })
export const attentionQuery = (http: ReviewHttp, search: AttentionSearch) =>
  queryOptions({
    queryKey: ['attention', search.q ?? null, search.cursor ?? null] as const,
    queryFn: ({ signal }) => http.getAttention(search, signal),
    staleTime: 15_000,
    retry: false,
  })
export const detailQuery = (http: ReviewHttp, id: string) =>
  queryOptions({
    queryKey: ['review-case', id] as const,
    queryFn: ({ signal }) => http.getCaseDetail(id, signal),
    staleTime: 15_000,
    retry: false,
  })
const policyStateQuery = (http: ReviewHttp) =>
  queryOptions({
    queryKey: ['policy', 'state'] as const,
    queryFn: ({ signal }) => http.getPolicyState(signal),
    staleTime: 15_000,
    retry: false,
  })
const shadowQuery = (http: ReviewHttp) =>
  queryOptions({
    queryKey: ['policy', 'shadow'] as const,
    queryFn: ({ signal }) => http.getShadowPosture(signal),
    staleTime: 15_000,
    retry: false,
  })
const safetyQuery = (http: ReviewHttp) =>
  queryOptions({
    queryKey: ['policy', 'safety'] as const,
    queryFn: ({ signal }) => http.getAutomaticSafety(signal),
    staleTime: 15_000,
    retry: false,
  })
const activityQuery = (http: ReviewHttp, search: ActivitySearch) =>
  queryOptions({
    queryKey: [
      'activity',
      search.q ?? null,
      search.eventType,
      search.rangeDays,
      search.page,
      search.pageSize,
    ] as const,
    queryFn: ({ signal }) => http.getActivity(search, signal),
    staleTime: 4_000,
    retry: false,
  })
export const preparationQuery = (http: ReviewHttp, live = false) =>
  queryOptions({
    queryKey: ['review-preparation'] as const,
    queryFn: ({ signal }) => http.getPreparation(signal),
    staleTime: 1_000,
    retry: false,
    // The cadence survives only as the degraded fallback. While the stream is
    // up the snapshot is refetched when preparation actually advances, which is
    // both sooner than 1.5 seconds and, for an idle library, never.
    refetchInterval: (result) => {
      if (live) return false
      const value = result.state.data
      return value?.active || value?.actionable || value?.paused ? 1_500 : 15_000
    },
  })

const routeTitle = (path: string) =>
  path.startsWith('/review/')
    ? 'Review case'
    : path === '/library'
      ? 'Library'
      : path === '/attention'
        ? 'Attention'
        : path === '/policy'
          ? 'Policy'
          : path === '/activity'
            ? 'Activity'
            : 'Page unavailable'

export const useLiveStream = () => {
  const { updates } = rootRoute.useRouteContext()
  return useLiveStreamState(updates)
}

export const usePreparation = () => {
  const { http, queryClient } = rootRoute.useRouteContext()
  const live = useLiveStream() === 'live'
  const previousSnapshot = useRef<string | null>(null)
  const query = useQuery(preparationQuery(http, live))
  const data = query.data
  const snapshot = data ? JSON.stringify(data) : null
  useEffect(() => {
    if (snapshot === null) return
    if (previousSnapshot.current === null) {
      previousSnapshot.current = snapshot
      return
    }
    if (previousSnapshot.current === snapshot) return
    previousSnapshot.current = snapshot
    // Only while polling. A snapshot that changed used to be the only sign that
    // a work might have been published, so every poll that saw a difference
    // rebuilt the Library and the attention rail. With a stream, the cutover
    // announces itself and names the work, so this cascade would be a second,
    // coarser copy of an invalidation that already arrived (#641).
    if (live) return
    void Promise.all([
      queryClient.invalidateQueries({ queryKey: ['library'] }),
      queryClient.invalidateQueries({ queryKey: ['attention'] }),
    ])
  }, [live, queryClient, snapshot])
  return { data, queryClient, http, live }
}

const connectionCopy = (code: 'tls' | 'authentication' | 'authorization' | 'unavailable') =>
  ({
    tls: 'The library connection needs its secure connection settings checked by its owner.',
    authentication: 'The library connection needs its sign-in settings checked by its owner.',
    authorization: 'The library connection needs its access permission checked by its owner.',
    unavailable: 'The library connection is unavailable and needs an owner check.',
  })[code]

export const ConnectionNotice = ({
  data,
  checking,
  onCheck,
  error,
}: {
  data: PreparationView | undefined
  checking: boolean
  onCheck: (connection: string) => void
  error: boolean
}) => {
  const condition =
    data?.connections?.find((item) => item.state !== 'recovered') ?? data?.connections?.[0]
  if (!condition) return null
  const ready = data?.libraries.reduce((total, item) => total + item.ready, 0) ?? 0
  const paused = condition.state === 'recovered' ? 0 : condition.affected
  const title =
    condition.state === 'checking'
      ? 'Checking library connection'
      : condition.state === 'recovered'
        ? 'Library connection recovered'
        : 'Library connection needs attention'
  const copy =
    condition.state === 'checking'
      ? 'Preparation remains safely paused while the configured connection is checked.'
      : condition.state === 'recovered'
        ? 'Preparation resumed from its retained checkpoint. Completed reviews remain available.'
        : `${connectionCopy(condition.code)} ${String(paused)} preparation ${paused === 1 ? 'work is' : 'works are'} safely paused. Completed reviews remain available.`
  const canCheck = condition.state === 'paused' && !checking
  const action =
    condition.state === 'paused'
      ? 'Check connection again'
      : condition.state === 'recovered'
        ? 'Recovered'
        : 'Checking connection…'
  return (
    <section
      className={`connection-notice is-${condition.state}`}
      aria-live="polite"
      aria-label="Shared library connection status"
    >
      <div>
        <strong>{title}</strong>
        <span>{copy}</span>
      </div>
      <dl>
        <div>
          <dt>Ready</dt>
          <dd>{String(ready)}</dd>
        </div>
        <div>
          <dt>Safely paused</dt>
          <dd>{String(paused)}</dd>
        </div>
      </dl>
      <button
        type="button"
        aria-disabled={!canCheck}
        aria-busy={checking || undefined}
        onClick={() => {
          if (canCheck) onCheck(condition.connection)
        }}
      >
        {action}
      </button>
      {error ? (
        <p role="alert">
          Connection checking could not be completed. Preparation remains safely paused.
        </p>
      ) : null}
    </section>
  )
}

export const PreparationNotice = ({ routeIdentity }: { routeIdentity: string }) => {
  const { data, queryClient, http } = usePreparation()
  const action = useMutation({
    mutationFn: (path: string) => http.preparationAction(path),
    retry: false,
    onMutate: (path) => {
      const match = /^([^/]+)\/check-connection$/.exec(path)
      if (!match) return
      const encodedConnection = match[1]
      if (!encodedConnection) return
      const connection = decodeURIComponent(encodedConnection)
      const previous = queryClient.getQueryData<PreparationView>(['review-preparation'])
      queryClient.setQueryData<PreparationView>(['review-preparation'], (current) => {
        if (!current || !current.connections) return current
        return {
          ...current,
          connections: current.connections.map((condition) =>
            condition.connection === connection
              ? { ...condition, state: 'checking' as const }
              : condition,
          ),
        }
      })
      return { previous }
    },
    onError: (_error, _path, context) => {
      if (context?.previous) queryClient.setQueryData(['review-preparation'], context.previous)
    },
    onSettled: async () => {
      await queryClient.invalidateQueries({ queryKey: ['review-preparation'] })
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['library'] }),
        queryClient.invalidateQueries({ queryKey: ['attention'] }),
      ])
    },
  })
  return (
    <ConnectionNotice
      data={data}
      checking={action.isPending}
      error={action.isError}
      onCheck={(connection) => {
        const main = document.getElementById('main-content')
        if (main) main.dataset.routeFocusIdentity = `${window.location.pathname}:${routeIdentity}`
        action.mutate(`${encodeURIComponent(connection)}/check-connection`)
      }}
    />
  )
}

/**
 * The eight buckets a work moves through, in the order an owner reads them.
 *
 * The stage names the store uses are engineering words; these are what the
 * owner is actually waiting for. Naming every bucket -- including the empty
 * ones -- keeps the pipeline in one stable order as counts move through it,
 * which is what makes a live update read as progress rather than as a redraw
 * (#641).
 */
const stageLabels: Readonly<Record<string, string>> = {
  queued: 'Not started',
  extracting: 'Reading your files',
  providers: 'Asking providers',
  proposals: 'Comparing sources',
  admission: 'Finding safe automatic choices',
  projection: 'Preparing the review',
  ready: 'Ready to browse',
  failed: 'Could not finish',
}

export const PreparationActivity = () => {
  const { data, queryClient, http } = usePreparation()
  const [open, setOpen] = useState(false)
  const trigger = useRef<HTMLButtonElement>(null)
  const action = useMutation({
    mutationFn: (path: string) => http.preparationAction(path),
    retry: false,
    onSettled: async () => {
      await queryClient.invalidateQueries({ queryKey: ['review-preparation'] })
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['library'] }),
        queryClient.invalidateQueries({ queryKey: ['attention'] }),
      ])
    },
  })
  const unavailable = !data || (!data.active && !data.actionable && !data.paused)
  useEffect(() => {
    if (!open || !unavailable) return
    requestAnimationFrame(() => {
      document.getElementById('main-content')?.focus({ preventScroll: true })
      setOpen(false)
    })
  }, [open, unavailable])
  if (unavailable && !open) return null
  const close = () => {
    trigger.current?.focus()
    setOpen(false)
  }
  return (
    <div className="preparation-activity">
      <button
        ref={trigger}
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
      >
        {data?.paused
          ? 'Preparation paused'
          : data?.actionable
            ? 'Preparation needs attention'
            : 'Preparing libraries'}
      </button>
      {open && data ? (
        <section className="preparation-popover" aria-label="Background activity">
          <h2>Background activity</h2>
          {data.libraries.map((item) => (
            <article key={`${item.connection}:${item.library}`}>
              <strong>{item.library}</strong>
              <span>
                {item.state} · {item.checked}/{item.total}
              </span>
              <dl className="preparation-stages">
                {(item.stages ?? []).map((stage) => (
                  <div key={stage.stage}>
                    <dt>{stageLabels[stage.stage] ?? stage.stage}</dt>
                    <dd>{stage.count}</dd>
                  </div>
                ))}
              </dl>
              <button
                disabled={
                  action.isPending ||
                  data.paused ||
                  item.state === 'queued' ||
                  item.state === 'discovering'
                }
                onClick={() =>
                  action.mutate(
                    `${encodeURIComponent(item.connection)}/${encodeURIComponent(item.library)}/scan`,
                  )
                }
              >
                Scan now
              </button>
              {item.state === 'completed_with_failures' ? (
                <button
                  disabled={action.isPending || data.paused}
                  onClick={() =>
                    action.mutate(
                      `${encodeURIComponent(item.connection)}/${encodeURIComponent(item.library)}/retry`,
                    )
                  }
                >
                  Retry
                </button>
              ) : null}
            </article>
          ))}
          <button
            disabled={action.isPending}
            onClick={() => action.mutate(data.paused ? 'resume' : 'pause')}
          >
            {data.paused ? 'Resume' : 'Pause'}
          </button>
          <button onClick={close}>Close</button>
          {action.isError ? (
            <p role="alert">Preparation control failed safely. Try again.</p>
          ) : null}
        </section>
      ) : null}
    </div>
  )
}

const ThemeControl = () => {
  const cookie = /(?:^|; )ml_theme=(light|dark)(?:;|$)/.exec(document.cookie)?.[1]
  const [theme, setTheme] = useState(cookie === 'dark' ? 'dark' : 'light')
  useEffect(() => {
    document.documentElement.dataset.theme = theme
    document.documentElement.style.colorScheme = theme
  }, [theme])
  const toggle = () => {
    const next = theme === 'dark' ? 'light' : 'dark'
    document.cookie = `ml_theme=${next}; Path=/; Max-Age=31536000; SameSite=Strict; Secure`
    setTheme(next)
  }
  return (
    <button
      className="theme-toggle"
      type="button"
      aria-pressed={theme === 'dark'}
      aria-label={theme === 'dark' ? 'Use light mode' : 'Use dark mode'}
      onClick={toggle}
    >
      <span aria-hidden="true">{theme === 'dark' ? '☀' : '◐'}</span>
      <span>{theme === 'dark' ? 'Light' : 'Dark'}</span>
    </button>
  )
}

const Shell = () => {
  const shell = useRef<HTMLDivElement>(null)
  const masthead = useRef<HTMLElement>(null)
  const main = useRef<HTMLElement>(null)
  const previousPath = useRef('')
  const popNavigation = useRef(false)
  const navigation = useRouterState({
    select: (state) => ({ href: state.location.href, status: state.status }),
  })
  const { http, queryClient, updates } = rootRoute.useRouteContext()
  useLiveInvalidation(updates, queryClient)
  const streamState = useLiveStreamState(updates)
  const path = new URL(navigation.href, window.location.origin).pathname
  const title = routeTitle(path)
  const [mismatch, setMismatch] = useState(false)
  const [shellError, setShellError] = useState('')
  useEffect(() => {
    const listener = () => setMismatch(true)
    addEventListener('ml:build-mismatch', listener)
    return () => removeEventListener('ml:build-mismatch', listener)
  }, [])
  useLayoutEffect(() => {
    const shellElement = shell.current
    const mastheadElement = masthead.current
    if (!shellElement || !mastheadElement) return
    const updateMastheadClearance = () => {
      const clearance = mastheadElement.getBoundingClientRect().height + 8
      shellElement.style.setProperty('--masthead-clearance', `${clearance.toString()}px`)
    }
    updateMastheadClearance()
    if (!('ResizeObserver' in window)) {
      return () => shellElement.style.removeProperty('--masthead-clearance')
    }
    const observer = new ResizeObserver(updateMastheadClearance)
    observer.observe(mastheadElement)
    return () => {
      observer.disconnect()
      shellElement.style.removeProperty('--masthead-clearance')
    }
  }, [])
  useEffect(() => {
    const listener = () => {
      popNavigation.current = true
    }
    addEventListener('popstate', listener)
    return () => removeEventListener('popstate', listener)
  }, [])
  useEffect(() => {
    if (navigation.status !== 'idle') return
    document.title = `${title} · Mechanical Librarian`
    const changed = previousPath.current !== path
    previousPath.current = path
    const frame = requestAnimationFrame(() => {
      if (changed) {
        const target =
          main.current?.querySelector<HTMLElement>('[data-route-focus]') ?? main.current
        target?.focus({ preventScroll: true })
      }
      if (changed && !popNavigation.current) window.scrollTo({ top: 0 })
      popNavigation.current = false
      main.current?.setAttribute('data-restored-path', path)
      dispatchEvent(new Event('ml:route-restored'))
    })
    return () => cancelAnimationFrame(frame)
  }, [navigation.status, path, title])
  const signOut = async () => {
    setShellError('')
    try {
      await http.logout()
      window.location.assign('/login')
    } catch (error) {
      if (!(error instanceof BuildMismatchError)) setShellError('Sign out failed. Try again.')
    }
  }
  return (
    <div ref={shell} className="app-shell">
      <a className="skip-link" href="#main-content">
        Skip to content
      </a>
      <header ref={masthead} className="masthead">
        <Link
          to="/library"
          search={{ lens: 'all' }}
          className="brand"
          aria-label="Mechanical Librarian home"
        >
          <span className="brand-mark" aria-hidden="true">
            ML
          </span>
          <span>Mechanical Librarian</span>
        </Link>
        <nav aria-label="Primary workspace">
          <Link
            to="/library"
            search={{ lens: 'all' }}
            activeOptions={{ includeSearch: false }}
            activeProps={{ 'aria-current': 'page' }}
          >
            Library
          </Link>
          <Link
            to="/policy"
            search={{ section: 'identity' }}
            activeProps={{ 'aria-current': 'page' }}
          >
            Policy
          </Link>
          <Link
            to="/activity"
            search={{ eventType: 'all', rangeDays: 30, page: 1, pageSize: 25 }}
            activeProps={{ 'aria-current': 'page' }}
          >
            Activity
          </Link>
        </nav>
        <div className="shell-actions">
          <LiveStreamNotice state={streamState} />
          <PreparationActivity />
          <ThemeControl />
          <span className="private-session">
            <span aria-hidden="true">●</span> Private
          </span>
          <button className="sign-out" type="button" onClick={() => void signOut()}>
            Sign out
          </button>
        </div>
      </header>
      <p className="navigation-status" role="status" aria-live="polite">
        {navigation.status === 'idle' ? `${title} loaded` : `Loading ${title}`}
      </p>
      {shellError ? (
        <p className="shell-error" role="alert">
          {shellError}
        </p>
      ) : null}
      {mismatch ? (
        <section className="build-mismatch" role="alert">
          <strong>This page is from a different application build.</strong>
          <span>Writes are blocked. Reload once to continue safely.</span>
          <button onClick={() => window.location.reload()}>Reload application</button>
        </section>
      ) : null}
      <main id="main-content" ref={main} tabIndex={-1} aria-busy={navigation.status !== 'idle'}>
        <Suspense fallback={<RoutePending title={title} />}>
          <Outlet />
        </Suspense>
      </main>
    </div>
  )
}

const RoutePending = ({ title = 'workspace' }: { title?: string }) => (
  <section aria-busy="true" className="route-state">
    <p className="eyebrow">Authenticated review</p>
    <h1>Loading {title.toLowerCase()}…</h1>
  </section>
)
const RouteFailure = ({ error, reset }: { error: Error; reset: () => void }) =>
  error instanceof ChunkRecoveryError && error.exhausted ? (
    <section className="route-state">
      <p className="eyebrow">Application update unavailable</p>
      <h1>The current workspace assets could not be loaded.</h1>
      <p role="alert">{error.message}</p>
      <button onClick={() => reloadCurrentApplication()}>Reload the current application</button>
    </section>
  ) : (
    <section className="route-state">
      <p className="eyebrow">Review unavailable</p>
      <h1>This workspace could not be loaded.</h1>
      <p role="alert">{error.message}</p>
      <button onClick={reset}>Try again</button>
    </section>
  )
const DirectEntryFallback = () => (
  <section className="route-state">
    <p className="eyebrow">Page unavailable</p>
    <h1>This review route does not exist.</h1>
    <Link to="/library" search={{ lens: 'all' }}>
      Open review library
    </Link>
  </section>
)

const rootRoute = createRootRouteWithContext<RouterContext>()({
  component: Shell,
  pendingComponent: RoutePending,
  errorComponent: RouteFailure,
  notFoundComponent: DirectEntryFallback,
})
const indexRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/',
  component: () => <Navigate to="/library" search={{ lens: 'all' }} replace />,
})
const LibraryRouteComponent = () => {
  const initial = libraryRoute.useLoaderData()
  const { http } = rootRoute.useRouteContext()
  const search = libraryRoute.useSearch()
  const query = useQuery({ ...libraryQuery(http, search), initialData: initial })
  return <LibraryPage data={query.data} />
}
const AttentionRouteComponent = () => {
  const initial = attentionRoute.useLoaderData()
  const { http } = rootRoute.useRouteContext()
  const search = attentionRoute.useSearch()
  const query = useQuery({ ...attentionQuery(http, search), initialData: initial })
  return <AttentionPage data={query.data} />
}
const DiagnosticRouteComponent = () => {
  const { reviewCaseId } = detailRoute.useParams()
  const { http } = rootRoute.useRouteContext()
  const detail = useQuery(detailQuery(http, reviewCaseId))
  if (detail.isPending) return <RoutePending />
  if (detail.isError)
    return <RouteFailure error={detail.error} reset={() => void detail.refetch()} />
  return <DetailPage data={detail.data} />
}
export const libraryRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/library',
  validateSearch: normalizeLibrarySearch,
  loaderDeps: ({ search }) => search,
  loader: ({ context, deps, abortController }) =>
    context.queryClient.ensureQueryData({
      ...libraryQuery(context.http, deps),
      queryFn: () => context.http.getLibrary(deps, abortController.signal),
    }),
  component: LibraryRouteComponent,
})
export const attentionRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/attention',
  validateSearch: normalizeAttentionSearch,
  loaderDeps: ({ search }) => search,
  loader: ({ context, deps, abortController }) =>
    context.queryClient.ensureQueryData({
      ...attentionQuery(context.http, deps),
      queryFn: () => context.http.getAttention(deps, abortController.signal),
    }),
  component: AttentionRouteComponent,
})
export const detailRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/review/$reviewCaseId',
  validateSearch: normalizeDetailSearch,
  component: DiagnosticRouteComponent,
})
export interface PolicyCritical {
  state: PolicyStateView
  shadow: ShadowPostureView
  safety: AutomaticSafetyView
}
const PolicyRouteComponent = () => {
  const initial = policyRoute.useLoaderData()
  const { http } = rootRoute.useRouteContext()
  const state = useQuery({ ...policyStateQuery(http), initialData: initial.state })
  const shadow = useQuery({ ...shadowQuery(http), initialData: initial.shadow })
  const safety = useQuery({ ...safetyQuery(http), initialData: initial.safety })
  return <PolicyPage data={{ state: state.data, shadow: shadow.data, safety: safety.data }} />
}
export const policyRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/policy',
  validateSearch: normalizePolicySearch,
  loader: async ({ context, abortController }) => {
    const [state, shadow, safety] = await Promise.all([
      context.queryClient.ensureQueryData({
        ...policyStateQuery(context.http),
        queryFn: () => context.http.getPolicyState(abortController.signal),
      }),
      context.queryClient.ensureQueryData({
        ...shadowQuery(context.http),
        queryFn: () => context.http.getShadowPosture(abortController.signal),
      }),
      context.queryClient.ensureQueryData({
        ...safetyQuery(context.http),
        queryFn: () => context.http.getAutomaticSafety(abortController.signal),
      }),
    ])
    return { state, shadow, safety }
  },
  component: PolicyRouteComponent,
})
export const activityRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/activity',
  validateSearch: normalizeActivitySearch,
  loaderDeps: ({ search }) => search,
  loader: ({ context, deps, abortController }) =>
    context.queryClient.ensureQueryData({
      ...activityQuery(context.http, deps),
      queryFn: () => context.http.getActivity(deps, abortController.signal),
    }),
  pendingMs: Infinity,
  component: () => <ActivityPage initial={activityRoute.useLoaderData()} />,
})
const routeTree = rootRoute.addChildren([
  indexRoute,
  libraryRoute,
  attentionRoute,
  detailRoute,
  policyRoute,
  activityRoute,
])

export const useReviewContext = () => rootRoute.useRouteContext()

export const createApplicationRouter = (
  http: ReviewHttp,
  queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  }),
  history?: ReturnType<typeof createMemoryHistory>,
  updates?: LiveUpdateStream,
) =>
  createRouter({
    routeTree,
    context: { http, queryClient, ...(updates ? { updates } : {}) },
    ...(history ? { history } : {}),
    defaultPreload: 'intent',
    defaultPreloadStaleTime: 15_000,
    defaultPendingMs: 140,
    defaultPendingMinMs: 220,
    scrollRestoration: true,
  })
export type ApplicationRouter = ReturnType<typeof createApplicationRouter>
declare module '@tanstack/react-router' {
  interface Register {
    router: ApplicationRouter
  }
}
