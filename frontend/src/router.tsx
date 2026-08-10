import { QueryClient, queryOptions, useQuery } from '@tanstack/react-query'
import {
  Link,
  Navigate,
  Outlet,
  createRootRouteWithContext,
  createRoute,
  createRouter,
  useRouterState,
} from '@tanstack/react-router'
import { lazy, Suspense, useEffect, useRef } from 'react'

import type { createMemoryHistory } from '@tanstack/react-router'

import type {
  CaseDetailView,
  LibrarySearch,
  LibraryView,
  ReviewHttp,
} from './api/http'

const LibraryPage = lazy(() => import('./routes/LibraryPage'))
const DetailPage = lazy(() => import('./routes/DetailPage'))

interface RouterContext {
  http: ReviewHttp
  queryClient: QueryClient
}

const boundedSearchValue = (value: unknown): string | undefined =>
  typeof value === 'string' && value.length > 0 && value.length <= 128 ? value : undefined

export const normalizeLibrarySearch = (search: Record<string, unknown>): LibrarySearch => {
  const lens =
    search.lens === 'all' || search.lens === 'no_attention_needed'
      ? search.lens
      : 'needs_attention'
  const q = boundedSearchValue(search.q)
  const cursor = boundedSearchValue(search.cursor)
  const preview = boundedSearchValue(search.preview)
  return { lens, ...(q ? { q } : {}), ...(cursor ? { cursor } : {}), ...(preview ? { preview } : {}) }
}

const libraryQuery = (http: ReviewHttp, search: LibrarySearch) =>
  queryOptions({
    queryKey: ['library', search.lens, search.q ?? null, search.cursor ?? null] as const,
    queryFn: ({ signal }) => http.getLibrary(search, signal),
    staleTime: 15_000,
  })

const detailQuery = (http: ReviewHttp, id: string) =>
  queryOptions({
    queryKey: ['review-case', id] as const,
    queryFn: ({ signal }) => http.getCaseDetail(id, signal),
    staleTime: 15_000,
  })

const titleForPath = (path: string): string =>
  path.startsWith('/review/') ? 'Review case' : path === '/library' ? 'Review library' : 'Page unavailable'

const Shell = () => {
  const main = useRef<HTMLElement>(null)
  const navigation = useRouterState({
    select: (state) => ({ href: state.location.href, status: state.status }),
  })
  const { http } = rootRoute.useRouteContext()
  const title = titleForPath(new URL(navigation.href, window.location.origin).pathname)

  useEffect(() => {
    if (navigation.status !== 'idle') return
    document.title = `${title} · Mechanical Librarian`
    const frame = requestAnimationFrame(() => main.current?.focus({ preventScroll: true }))
    return () => cancelAnimationFrame(frame)
  }, [navigation.status, title])

  const signOut = async () => {
    await http.logout()
    window.location.assign('/login')
  }

  return (
    <div className="app-shell">
      <a className="skip-link" href="#main-content">Skip to content</a>
      <header className="masthead">
        <Link to="/library" search={{ lens: 'needs_attention' }} preload="intent" className="brand">
          Mechanical Librarian
        </Link>
        <nav aria-label="Review workspace">
          <Link to="/library" search={{ lens: 'needs_attention' }} preload="intent">Library</Link>
          <button type="button" onClick={() => void signOut()}>Sign out</button>
        </nav>
      </header>
      <p className="sr-only" role="status" aria-live="polite">
        {navigation.status === 'idle' ? `${title} loaded` : ''}
      </p>
      <main id="main-content" ref={main} tabIndex={-1}>
        <Suspense fallback={<RoutePending />}><Outlet /></Suspense>
      </main>
    </div>
  )
}

const RoutePending = () => (
  <section aria-busy="true" aria-label="Loading review workspace" className="route-state">
    <p className="eyebrow">Authenticated review</p>
    <h1>Loading current review data…</h1>
  </section>
)

const RouteFailure = ({ error }: { error: Error }) => (
  <section className="route-state">
    <p className="eyebrow">Review unavailable</p>
    <h1>This workspace could not be loaded.</h1>
    <p role="alert">{error.message}</p>
    <button type="button" onClick={() => window.location.reload()}>Reload this page</button>
  </section>
)

const DirectEntryFallback = () => (
  <section className="route-state">
    <p className="eyebrow">Page unavailable</p>
    <h1>This review route does not exist.</h1>
    <p>The application shell loaded safely. Return to the current review library.</p>
    <Link to="/library" search={{ lens: 'needs_attention' }}>Open review library</Link>
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
  component: () => <Navigate to="/library" search={{ lens: 'needs_attention' }} replace />,
})

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
  component: () => {
    const data: LibraryView = libraryRoute.useLoaderData()
    return <LibraryPage data={data} />
  },
})

export const detailRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/review/$reviewCaseId',
  loader: ({ context, params, abortController }) =>
    context.queryClient.ensureQueryData({
      ...detailQuery(context.http, params.reviewCaseId),
      queryFn: () => context.http.getCaseDetail(params.reviewCaseId, abortController.signal),
    }),
  component: () => {
    const data: CaseDetailView = detailRoute.useLoaderData()
    return <DetailPage data={data} />
  },
})

const routeTree = rootRoute.addChildren([indexRoute, libraryRoute, detailRoute])

export const ProgressiveCasePanel = ({ id }: { id: string }) => {
  const { http } = rootRoute.useRouteContext()
  const result = useQuery(detailQuery(http, id))
  if (result.isPending) return <aside aria-busy="true"><h2>Loading preview…</h2></aside>
  if (result.isError) return <aside><h2>Preview unavailable</h2><p role="alert">Refresh to retry.</p></aside>
  return (
    <aside>
      <p className="eyebrow">Progressive detail</p>
      <h2>{result.data.title}</h2>
      <p>{result.data.subtitle}</p>
      <Link to="/review/$reviewCaseId" params={{ reviewCaseId: id }} preload="intent">Open full review</Link>
    </aside>
  )
}

export const createApplicationRouter = (
  http: ReviewHttp,
  queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } }),
  history?: ReturnType<typeof createMemoryHistory>,
) =>
  createRouter({
    routeTree,
    context: { http, queryClient },
    ...(history ? { history } : {}),
    defaultPreload: 'intent',
    defaultPreloadStaleTime: 15_000,
  })

export type ApplicationRouter = ReturnType<typeof createApplicationRouter>

declare module '@tanstack/react-router' {
  interface Register {
    router: ApplicationRouter
  }
}
