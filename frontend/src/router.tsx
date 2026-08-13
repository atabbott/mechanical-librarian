import { QueryClient, queryOptions, useMutation, useQuery } from '@tanstack/react-query'
import { Link, Navigate, Outlet, createRootRouteWithContext, createRoute, createRouter, useRouterState } from '@tanstack/react-router'
import { Suspense, useEffect, useRef, useState } from 'react'

import type { createMemoryHistory } from '@tanstack/react-router'
import { BuildMismatchError } from './api/http'
import type { ActivitySearch, AttentionSearch, AutomaticSafetyView, DetailSearch, LibrarySearch, LibraryWorkView, PolicySearch, PolicyStateView, ReviewHttp, ShadowPostureView } from './api/http'
import { ChunkRecoveryError, recoverableLazy, reloadCurrentApplication } from './chunkRecovery'

const LibraryPage = recoverableLazy(() => import('./routes/LibraryPage'))
const AttentionPage = recoverableLazy(() => import('./routes/AttentionPage'))
const DetailPage = recoverableLazy(() => import('./routes/DetailPage'))
const PolicyPage = recoverableLazy(() => import('./routes/PolicyPage'))
const ActivityPage = recoverableLazy(() => import('./routes/ActivityPage'))

export interface RouterContext { http: ReviewHttp; queryClient: QueryClient }
const bounded = (value: unknown) => typeof value === 'string' && value.length > 0 && value.length <= 128 ? value : undefined
export const normalizeLibrarySearch = (search: Record<string, unknown>): LibrarySearch => { const lens = search.lens === 'needs_attention' || search.lens === 'no_attention_needed' ? search.lens : 'all'; const q = bounded(search.q); const cursor = bounded(search.cursor); const preview = bounded(search.preview); const field = bounded(search.field); return { lens, ...(q ? { q } : {}), ...(cursor ? { cursor } : {}), ...(preview ? { preview } : {}), ...(field ? { field } : {}), ...(search.attention === 'open' ? { attention: 'open' } : {}) } }
export const normalizeAttentionSearch = (search: Record<string, unknown>): AttentionSearch => { const q = bounded(search.q); const cursor = bounded(search.cursor); return { ...(q ? { q } : {}), ...(cursor ? { cursor } : {}) } }
export const normalizeDetailSearch = (search: Record<string, unknown>): DetailSearch => { const field = bounded(search.field); const subview = search.subview === 'evidence' || search.subview === 'history' ? search.subview : 'review'; return { ...(field ? { field } : {}), subview } }
export const normalizePolicySearch = (search: Record<string, unknown>): PolicySearch => ({ section: search.section === 'publication' || search.section === 'curation' || search.section === 'unsupported' ? search.section : 'identity' })
export const normalizeActivitySearch = (search: Record<string, unknown>): ActivitySearch => {
  const eventType = ['review', 'policy', 'metadata', 'safety', 'unknown'].includes(String(search.eventType)) ? String(search.eventType) as ActivitySearch['eventType'] : 'all'
  const rangeDays = [7, 90].includes(Number(search.rangeDays)) ? Number(search.rangeDays) as 7 | 90 : 30
  const pageSize = [50, 100].includes(Number(search.pageSize)) ? Number(search.pageSize) as 50 | 100 : 25
  const requestedPage = Number(search.page)
  const page = Number.isSafeInteger(requestedPage) && requestedPage >= 1 && requestedPage <= 1_000_000 ? requestedPage : 1
  const q = bounded(search.q)
  return { eventType, rangeDays, pageSize, page, ...(q ? { q } : {}) }
}

export const libraryQuery = (http: ReviewHttp, search: LibrarySearch) => queryOptions({ queryKey: ['library', search.lens, search.q ?? null, search.cursor ?? null] as const, queryFn: ({ signal }) => http.getLibrary(search, signal), staleTime: 15_000, retry: false })
export const attentionQuery = (http: ReviewHttp, search: AttentionSearch) => queryOptions({ queryKey: ['attention', search.q ?? null, search.cursor ?? null] as const, queryFn: ({ signal }) => http.getAttention(search, signal), staleTime: 15_000, retry: false })
export const detailQuery = (http: ReviewHttp, id: string) => queryOptions({ queryKey: ['review-case', id] as const, queryFn: ({ signal }) => http.getCaseDetail(id, signal), staleTime: 15_000, retry: false })
const policyStateQuery = (http: ReviewHttp) => queryOptions({ queryKey: ['policy', 'state'] as const, queryFn: ({ signal }) => http.getPolicyState(signal), staleTime: 15_000, retry: false })
const shadowQuery = (http: ReviewHttp) => queryOptions({ queryKey: ['policy', 'shadow'] as const, queryFn: ({ signal }) => http.getShadowPosture(signal), staleTime: 15_000, retry: false })
const safetyQuery = (http: ReviewHttp) => queryOptions({ queryKey: ['policy', 'safety'] as const, queryFn: ({ signal }) => http.getAutomaticSafety(signal), staleTime: 15_000, retry: false })
const activityQuery = (http: ReviewHttp, search: ActivitySearch) => queryOptions({ queryKey: ['activity', search.q ?? null, search.eventType, search.rangeDays, search.page, search.pageSize] as const, queryFn: ({ signal }) => http.getActivity(search, signal), staleTime: 4_000, retry: false })

const routeTitle = (path: string) => path.startsWith('/review/') ? 'Review case' : path === '/library' ? 'Library' : path === '/attention' ? 'Attention' : path === '/policy' ? 'Policy' : path === '/activity' ? 'Activity' : 'Page unavailable'

const PreparationActivity = () => {
  const { http, queryClient } = rootRoute.useRouteContext()
  const [open, setOpen] = useState(false)
  const trigger = useRef<HTMLButtonElement>(null)
  const previousSnapshot = useRef<string | null>(null)
  const query = useQuery({ queryKey: ['review-preparation'], queryFn: ({ signal }) => http.getPreparation(signal), staleTime: 1_000, retry: false, refetchInterval: (result) => { const value = result.state.data; return value?.active || value?.actionable || value?.paused ? 1_500 : 15_000 } })
  const action = useMutation({ mutationFn: (path: string) => http.preparationAction(path), retry: false, onSettled: async () => { await queryClient.invalidateQueries({ queryKey: ['review-preparation'] }); await Promise.all([queryClient.invalidateQueries({ queryKey: ['library'] }), queryClient.invalidateQueries({ queryKey: ['attention'] })]) } })
  const data = query.data
  const snapshot = data ? JSON.stringify(data) : null
  useEffect(() => {
    if (snapshot === null) return
    if (previousSnapshot.current === null) { previousSnapshot.current = snapshot; return }
    if (previousSnapshot.current === snapshot) return
    previousSnapshot.current = snapshot
    void Promise.all([
      queryClient.invalidateQueries({ queryKey: ['library'] }),
      queryClient.invalidateQueries({ queryKey: ['attention'] }),
    ])
  }, [queryClient, snapshot])
  const unavailable = !data || (!data.active && !data.actionable && !data.paused)
  useEffect(() => {
    if (!open || !unavailable) return
    requestAnimationFrame(() => { document.getElementById('main-content')?.focus({ preventScroll: true }); setOpen(false) })
  }, [open, unavailable])
  if (unavailable && !open) return null
  const close = () => { trigger.current?.focus(); setOpen(false) }
  return <div className="preparation"><button ref={trigger} type="button" aria-expanded={open} onClick={() => setOpen((value) => !value)}>{data?.paused ? 'Preparation paused' : data?.actionable ? 'Preparation needs attention' : 'Preparing libraries'}</button>{open && data ? <section aria-label="Background activity"><h2>Background activity</h2>{data.libraries.map((item) => <article key={`${item.connection}:${item.library}`}><strong>{item.library}</strong><span>{item.state} · {item.checked}/{item.total}</span><button disabled={action.isPending || data.paused || item.state === 'queued' || item.state === 'discovering'} onClick={() => action.mutate(`${encodeURIComponent(item.connection)}/${encodeURIComponent(item.library)}/scan`)}>Scan now</button>{item.state === 'completed_with_failures' ? <button disabled={action.isPending || data.paused} onClick={() => action.mutate(`${encodeURIComponent(item.connection)}/${encodeURIComponent(item.library)}/retry`)}>Retry</button> : null}</article>)}<button disabled={action.isPending} onClick={() => action.mutate(data.paused ? 'resume' : 'pause')}>{data.paused ? 'Resume' : 'Pause'}</button><button onClick={close}>Close</button>{action.isError ? <p role="alert">Preparation control failed safely. Try again.</p> : null}</section> : null}</div>
}

const ThemeControl = () => {
  const cookie = /(?:^|; )ml_theme=(light|dark)(?:;|$)/.exec(document.cookie)?.[1]
  const [theme, setTheme] = useState(cookie === 'dark' ? 'dark' : 'light')
  useEffect(() => { document.documentElement.dataset.theme = theme; document.documentElement.style.colorScheme = theme }, [theme])
  const toggle = () => { const next = theme === 'dark' ? 'light' : 'dark'; document.cookie = `ml_theme=${next}; Path=/; Max-Age=31536000; SameSite=Strict; Secure`; setTheme(next) }
  return <button className="theme-toggle" type="button" aria-pressed={theme === 'dark'} aria-label={theme === 'dark' ? 'Use light mode' : 'Use dark mode'} onClick={toggle}><span aria-hidden="true">{theme === 'dark' ? '☀' : '◐'}</span><span>{theme === 'dark' ? 'Light' : 'Dark'}</span></button>
}

const Shell = () => {
  const main = useRef<HTMLElement>(null)
  const previousPath = useRef('')
  const popNavigation = useRef(false)
  const navigation = useRouterState({ select: (state) => ({ href: state.location.href, status: state.status }) })
  const { http } = rootRoute.useRouteContext()
  const path = new URL(navigation.href, window.location.origin).pathname
  const title = routeTitle(path)
  const [mismatch, setMismatch] = useState(false)
  const [shellError, setShellError] = useState('')
  useEffect(() => { const listener = () => setMismatch(true); addEventListener('ml:build-mismatch', listener); return () => removeEventListener('ml:build-mismatch', listener) }, [])
  useEffect(() => { const listener = () => { popNavigation.current = true }; addEventListener('popstate', listener); return () => removeEventListener('popstate', listener) }, [])
  useEffect(() => {
    if (navigation.status !== 'idle') return
    document.title = `${title} · Mechanical Librarian`
    const changed = previousPath.current !== path
    previousPath.current = path
    const frame = requestAnimationFrame(() => { const target = main.current?.querySelector<HTMLElement>('[data-route-focus]') ?? main.current; target?.focus({ preventScroll: true }); if (changed && !popNavigation.current) window.scrollTo({ top: 0 }); popNavigation.current = false; main.current?.setAttribute('data-restored-path', path); dispatchEvent(new Event('ml:route-restored')) })
    return () => cancelAnimationFrame(frame)
  }, [navigation.status, path, title])
  const signOut = async () => { setShellError(''); try { await http.logout(); window.location.assign('/login') } catch (error) { if (!(error instanceof BuildMismatchError)) setShellError('Sign out failed. Try again.') } }
  return <div className="app-shell"><a className="skip-link" href="#main-content">Skip to content</a><header className="masthead"><Link to="/library" search={{ lens: 'all' }} className="brand" aria-label="Mechanical Librarian home"><span className="brand-mark" aria-hidden="true">ML</span><span>Mechanical Librarian</span></Link><nav aria-label="Primary workspace"><Link to="/library" search={{ lens: 'all' }} activeOptions={{ includeSearch: false }} activeProps={{ 'aria-current': 'page' }}>Library</Link><Link to="/policy" search={{ section: 'identity' }} activeProps={{ 'aria-current': 'page' }}>Policy</Link><Link to="/activity" search={{ eventType: 'all', rangeDays: 30, page: 1, pageSize: 25 }} activeProps={{ 'aria-current': 'page' }}>Activity</Link></nav><div className="shell-actions"><PreparationActivity/><ThemeControl/><span className="private-session"><span aria-hidden="true">●</span> Private</span><button className="sign-out" type="button" onClick={() => void signOut()}>Sign out</button></div></header><p className="navigation-status" role="status" aria-live="polite">{navigation.status === 'idle' ? `${title} loaded` : `Loading ${title}`}</p>{shellError ? <p className="shell-error" role="alert">{shellError}</p> : null}{mismatch ? <section className="build-mismatch" role="alert"><strong>This page is from a different application build.</strong><span>Writes are blocked. Reload once to continue safely.</span><button onClick={() => window.location.reload()}>Reload application</button></section> : null}<main id="main-content" ref={main} tabIndex={-1} aria-busy={navigation.status !== 'idle'}><Suspense fallback={<RoutePending title={title}/>}><Outlet/></Suspense></main></div>
}

const RoutePending = ({ title = 'workspace' }: { title?: string }) => <section aria-busy="true" className="route-state"><p className="eyebrow">Authenticated review</p><h1>Loading {title.toLowerCase()}…</h1></section>
const RouteFailure = ({ error, reset }: { error: Error; reset: () => void }) => error instanceof ChunkRecoveryError && error.exhausted
  ? <section className="route-state"><p className="eyebrow">Application update unavailable</p><h1>The current workspace assets could not be loaded.</h1><p role="alert">{error.message}</p><button onClick={() => reloadCurrentApplication()}>Reload the current application</button></section>
  : <section className="route-state"><p className="eyebrow">Review unavailable</p><h1>This workspace could not be loaded.</h1><p role="alert">{error.message}</p><button onClick={reset}>Try again</button></section>
const DirectEntryFallback = () => <section className="route-state"><p className="eyebrow">Page unavailable</p><h1>This review route does not exist.</h1><Link to="/library" search={{ lens: 'all' }}>Open review library</Link></section>

const rootRoute = createRootRouteWithContext<RouterContext>()({ component: Shell, pendingComponent: RoutePending, errorComponent: RouteFailure, notFoundComponent: DirectEntryFallback })
const indexRoute = createRoute({ getParentRoute: () => rootRoute, path: '/', component: () => <Navigate to="/library" search={{ lens: 'all' }} replace/> })
const LibraryRouteComponent = () => { const initial = libraryRoute.useLoaderData(); const { http } = rootRoute.useRouteContext(); const search = libraryRoute.useSearch(); const query = useQuery({ ...libraryQuery(http, search), initialData: initial }); return <LibraryPage data={query.data}/> }
const AttentionRouteComponent = () => { const initial = attentionRoute.useLoaderData(); const { http } = rootRoute.useRouteContext(); const search = attentionRoute.useSearch(); const query = useQuery({ ...attentionQuery(http, search), initialData: initial }); return <AttentionPage data={query.data}/> }
const DetailRouteComponent = () => { const initial = detailRoute.useLoaderData(); const { http } = rootRoute.useRouteContext(); const { reviewCaseId } = detailRoute.useParams(); const query = useQuery({ ...detailQuery(http, reviewCaseId), initialData: initial }); return <DetailPage data={query.data}/> }
export const libraryRoute = createRoute({ getParentRoute: () => rootRoute, path: '/library', validateSearch: normalizeLibrarySearch, loaderDeps: ({ search }) => search, loader: ({ context, deps, abortController }) => context.queryClient.ensureQueryData({ ...libraryQuery(context.http, deps), queryFn: () => context.http.getLibrary(deps, abortController.signal) }), component: LibraryRouteComponent })
export const attentionRoute = createRoute({ getParentRoute: () => rootRoute, path: '/attention', validateSearch: normalizeAttentionSearch, loaderDeps: ({ search }) => search, loader: ({ context, deps, abortController }) => context.queryClient.ensureQueryData({ ...attentionQuery(context.http, deps), queryFn: () => context.http.getAttention(deps, abortController.signal) }), component: AttentionRouteComponent })
export const detailRoute = createRoute({ getParentRoute: () => rootRoute, path: '/review/$reviewCaseId', validateSearch: normalizeDetailSearch, loader: ({ context, params, abortController }) => context.queryClient.ensureQueryData({ ...detailQuery(context.http, params.reviewCaseId), queryFn: () => context.http.getCaseDetail(params.reviewCaseId, abortController.signal) }), component: DetailRouteComponent })
export interface PolicyCritical { state: PolicyStateView; shadow: ShadowPostureView; safety: AutomaticSafetyView }
const PolicyRouteComponent = () => { const initial = policyRoute.useLoaderData(); const { http } = rootRoute.useRouteContext(); const state = useQuery({ ...policyStateQuery(http), initialData: initial.state }); const shadow = useQuery({ ...shadowQuery(http), initialData: initial.shadow }); const safety = useQuery({ ...safetyQuery(http), initialData: initial.safety }); return <PolicyPage data={{ state: state.data, shadow: shadow.data, safety: safety.data }}/> }
export const policyRoute = createRoute({ getParentRoute: () => rootRoute, path: '/policy', validateSearch: normalizePolicySearch, loader: async ({ context, abortController }) => { const [state, shadow, safety] = await Promise.all([context.queryClient.ensureQueryData({ ...policyStateQuery(context.http), queryFn: () => context.http.getPolicyState(abortController.signal) }), context.queryClient.ensureQueryData({ ...shadowQuery(context.http), queryFn: () => context.http.getShadowPosture(abortController.signal) }), context.queryClient.ensureQueryData({ ...safetyQuery(context.http), queryFn: () => context.http.getAutomaticSafety(abortController.signal) })]); return { state, shadow, safety } }, component: PolicyRouteComponent })
export const activityRoute = createRoute({ getParentRoute: () => rootRoute, path: '/activity', validateSearch: normalizeActivitySearch, loaderDeps: ({ search }) => search, loader: ({ context, deps, abortController }) => context.queryClient.ensureQueryData({ ...activityQuery(context.http, deps), queryFn: () => context.http.getActivity(deps, abortController.signal) }), component: () => <ActivityPage initial={activityRoute.useLoaderData()}/> })
const routeTree = rootRoute.addChildren([indexRoute, libraryRoute, attentionRoute, detailRoute, policyRoute, activityRoute])

type WorkIdentity = Pick<LibraryWorkView, 'title' | 'authors' | 'library' | 'mediaKind'>
export const ProgressiveCasePanel = ({ id, identity }: { id: string; identity?: WorkIdentity }) => { const { http } = rootRoute.useRouteContext(); const result = useQuery(detailQuery(http, id)); if (result.isPending) return <aside aria-busy="true"><h2>Loading preview…</h2></aside>; if (result.isError) return <aside><h2>Preview unavailable</h2><button onClick={() => void result.refetch()}>Try again</button></aside>; return <aside><p className="eyebrow">Progressive detail</p><h2>{identity?.title ?? 'Selected work'}</h2><p>{identity ? `${identity.authors.join(', ') || 'Author details unavailable'} · ${identity.library} · ${identity.mediaKind.replaceAll('_', ' ')}` : 'Readable work identity is not available on this Library page.'}</p><Link to="/review/$reviewCaseId" params={{ reviewCaseId: id }} search={{ subview: 'review' }}>Open full review</Link></aside> }
export const useReviewContext = () => rootRoute.useRouteContext()

export const createApplicationRouter = (http: ReviewHttp, queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } }), history?: ReturnType<typeof createMemoryHistory>) => createRouter({ routeTree, context: { http, queryClient }, ...(history ? { history } : {}), defaultPreload: 'intent', defaultPreloadStaleTime: 15_000, defaultPendingMs: 140, defaultPendingMinMs: 220, scrollRestoration: true })
export type ApplicationRouter = ReturnType<typeof createApplicationRouter>
declare module '@tanstack/react-router' { interface Register { router: ApplicationRouter } }
