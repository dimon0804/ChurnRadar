import { lazy, Suspense, type ReactNode } from 'react'
import { createBrowserRouter } from 'react-router'
import { isApiError } from '@/api/client'
import { AppShell, Logo } from '@/components/app-shell'
import { ErrorState, ForbiddenState, NotFoundState } from '@/components/states'
import { Skeleton } from '@/components/ui/skeleton'
import { DemoMode } from '@/demo/demo-mode'
import { AuthProvider, useAuth, useMeQuery } from '@/providers/auth'
import { LiveProvider } from '@/providers/live'
import { LoginPage } from '@/pages/login'

const OverviewPage = lazy(() => import('@/pages/overview'))
const CustomersPage = lazy(() => import('@/pages/customers'))
const CustomerSheet = lazy(() => import('@/pages/customer-detail'))
const ModelPage = lazy(() => import('@/pages/model'))
const DataPage = lazy(() => import('@/pages/data'))
const MailPage = lazy(() => import('@/pages/mail'))
const ModelQualityPage = lazy(() => import('@/pages/model-quality'))

function PageFallback() {
  return (
    <div className="space-y-4" aria-busy="true" aria-label="Загрузка">
      <Skeleton className="h-8 w-56" />
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        {Array.from({ length: 4 }, (_, i) => (
          <Skeleton key={i} className="h-28 rounded-xl" />
        ))}
      </div>
      <Skeleton className="h-80 rounded-xl" />
    </div>
  )
}

function Lazy({ children }: { children: ReactNode }) {
  return <Suspense fallback={<PageFallback />}>{children}</Suspense>
}

function BootScreen() {
  return (
    <div className="flex min-h-screen flex-col items-center justify-center gap-4" aria-busy="true">
      <Logo className="opacity-80" />
      <div className="h-0.5 w-32 overflow-hidden rounded-full bg-muted">
        <div className="h-full w-1/3 animate-pulse rounded-full bg-primary/50" />
      </div>
    </div>
  )
}

function RequireAuth() {
  const me = useMeQuery()
  if (me.isPending) return <BootScreen />
  if (me.isError) {
    // при 401 клиент уже уводит на /login
    if (isApiError(me.error) && me.error.status === 401) return <BootScreen />
    return (
      <div className="flex min-h-screen items-center justify-center">
        <ErrorState error={me.error} title="Панель недоступна" onRetry={() => void me.refetch()} />
      </div>
    )
  }
  return (
    <AuthProvider user={me.data}>
      <LiveProvider>
        <AppShell />
        <DemoMode />
      </LiveProvider>
    </AuthProvider>
  )
}

function AdminOnly({ children }: { children: ReactNode }) {
  const { isAdmin } = useAuth()
  return isAdmin ? <Lazy>{children}</Lazy> : <ForbiddenState />
}

export const router = createBrowserRouter([
  { path: '/login', element: <LoginPage /> },
  {
    path: '/',
    element: <RequireAuth />,
    children: [
      {
        index: true,
        element: (
          <Lazy>
            <OverviewPage />
          </Lazy>
        ),
      },
      {
        path: 'customers',
        element: (
          <Lazy>
            <CustomersPage />
          </Lazy>
        ),
        children: [
          {
            path: ':id',
            element: (
              <Suspense fallback={null}>
                <CustomerSheet />
              </Suspense>
            ),
          },
        ],
      },
      { path: 'model', element: <AdminOnly><ModelPage /></AdminOnly> },
      { path: 'data', element: <AdminOnly><DataPage /></AdminOnly> },
      {
        path: 'quality',
        element: (
          <Lazy>
            <ModelQualityPage />
          </Lazy>
        ),
      },
      {
        path: 'mail',
        element: (
          <Lazy>
            <MailPage />
          </Lazy>
        ),
      },
      { path: '*', element: <NotFoundState /> },
    ],
  },
])
