import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { RouterProvider } from '@tanstack/react-router'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'

import { PageCsrfTokenStore, ReviewApiClient } from './api/http'
import { configureChunkRecovery } from './chunkRecovery'
import { createLiveUpdateStream } from './liveUpdates'
import { createApplicationRouter } from './router'
import './styles.css'

const setupHandoffKey = 'mechanical-librarian:setup-handoff'
const disableCrossDocumentTransitions = () => {
  for (const sheet of document.styleSheets) {
    let rules: CSSRuleList
    try {
      rules = sheet.cssRules
    } catch {
      continue
    }
    for (let index = rules.length - 1; index >= 0; index -= 1)
      if (rules[index]?.cssText.startsWith('@view-transition')) sheet.deleteRule(index)
  }
}
const handoffDestination = sessionStorage.getItem(setupHandoffKey)
sessionStorage.removeItem(setupHandoffKey)
if (handoffDestination !== `${location.pathname}${location.search}`)
  disableCrossDocumentTransitions()
else
  addEventListener(
    'pagereveal',
    (event) => {
      const transition = event.viewTransition
      if (transition) void transition.finished.finally(disableCrossDocumentTransitions)
      else disableCrossDocumentTransitions()
    },
    { once: true },
  )

const mount = document.querySelector<HTMLElement>('#review-app')
if (!mount) throw new Error('Review application mount is missing')

const pageToken = document.body.dataset.csrfToken
const buildIdentity = document.body.dataset.buildIdentity
document.body.removeAttribute('data-csrf-token')
document.body.removeAttribute('data-build-identity')
configureChunkRecovery(buildIdentity)
const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
})
const http = new ReviewApiClient({
  csrf: new PageCsrfTokenStore(pageToken),
  ...(buildIdentity ? { buildIdentity } : {}),
  onBuildMismatch: () => dispatchEvent(new Event('ml:build-mismatch')),
})
// One stream for the tab, opened once and shared by every surface.
const updates = createLiveUpdateStream()
const router = createApplicationRouter(http, queryClient, undefined, updates)

createRoot(mount).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  </StrictMode>,
)
