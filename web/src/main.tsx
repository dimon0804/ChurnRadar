import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { RouterProvider } from 'react-router'
import { isApiError, setUnauthorizedHandler } from '@/api/client'
import { TooltipProvider } from '@/components/ui/tooltip'
import { loginUrl } from '@/lib/url-state'
import { ThemeProvider } from '@/providers/theme'
import { router } from './router'
import './index.css'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 15_000,
      retry: (failureCount, error) => {
        if (isApiError(error) && error.status >= 400 && error.status < 500) return false
        return failureCount < 2
      },
    },
    mutations: { retry: false },
  },
})

let redirecting = false
setUnauthorizedHandler(() => {
  if (redirecting || window.location.pathname.startsWith('/login')) return
  redirecting = true
  queryClient.clear()
  window.location.assign(loginUrl(window.location.pathname + window.location.search))
})

const root = document.getElementById('root')
if (!root) throw new Error('root element missing')

createRoot(root).render(
  <StrictMode>
    <ThemeProvider>
      <QueryClientProvider client={queryClient}>
        <TooltipProvider delayDuration={250} skipDelayDuration={150}>
          <RouterProvider router={router} />
        </TooltipProvider>
      </QueryClientProvider>
    </ThemeProvider>
  </StrictMode>,
)
