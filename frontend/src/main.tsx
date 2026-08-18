import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { RouterProvider } from '@tanstack/react-router'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'

import { PageCsrfTokenStore, ReviewApiClient } from './api/http'
import { configureChunkRecovery } from './chunkRecovery'
import { createLiveUpdateStream } from './liveUpdates'
import { createApplicationRouter } from './router'
import './styles.css'

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
