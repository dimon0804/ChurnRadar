import { useQuery, useQueryClient } from '@tanstack/react-query'
import { createContext, useCallback, useContext, useMemo, type ReactNode } from 'react'
import { api, queryKeys } from '@/api/endpoints'
import type { User } from '@/api/types'

interface AuthContextValue {
  user: User
  isAdmin: boolean
  logout: () => Promise<void>
}

const AuthContext = createContext<AuthContextValue | null>(null)

export function useMeQuery() {
  return useQuery({
    queryKey: queryKeys.me,
    queryFn: () => api.me(),
    staleTime: 5 * 60_000,
    retry: false,
  })
}

export function AuthProvider({ user, children }: { user: User; children: ReactNode }) {
  const queryClient = useQueryClient()

  const logout = useCallback(async () => {
    try {
      await api.logout()
    } finally {
      queryClient.clear()
      window.location.assign('/login')
    }
  }, [queryClient])

  const value = useMemo<AuthContextValue>(
    () => ({ user, isAdmin: user.roles.includes('admin'), logout }),
    [user, logout],
  )
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth() {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used inside AuthProvider')
  return ctx
}
