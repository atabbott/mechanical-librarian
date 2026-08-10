import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { RouterProvider } from '@tanstack/react-router'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'

import { PageCsrfTokenStore, ReviewApiClient } from './api/http'
import { createApplicationRouter } from './router'
import './styles.css'

const mount = document.querySelector<HTMLElement>('#review-app')
if (!mount) throw new Error('Review application mount is missing')

const pageToken = document.body.dataset.csrfToken
document.body.removeAttribute('data-csrf-token')
const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
const http = new ReviewApiClient({ csrf: new PageCsrfTokenStore(pageToken) })
const router = createApplicationRouter(http, queryClient)

createRoot(mount).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  </StrictMode>,
)

