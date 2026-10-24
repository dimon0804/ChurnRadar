import { useQuery, useQueryClient } from '@tanstack/react-query'
import { createContext, useCallback, useContext, useMemo, type ReactNode } from 'react'
import { api, queryKeys } from '@/api/endpoints'
import type { Role, User } from '@/api/types'

/** Роли вложены: аналитик < маркетолог < администратор. */
export function topRole(roles: readonly Role[]): Role {
  if (roles.includes('admin')) return 'admin'
  if (roles.includes('marketer')) return 'marketer'
  return 'analyst'
}

interface AuthContextValue {
  user: User
  /** старшая роль пользователя */
  role: Role
  isAdmin: boolean
  /** писать клиентам, рассылки, отметки — маркетолог и администратор */
  canAct: boolean
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
    () => {
      const role = topRole(user.roles)
      return { user, role, isAdmin: role === 'admin', canAct: role === 'admin' || role === 'marketer', logout }
    },
    [user, logout],
  )
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth() {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used inside AuthProvider')
  return ctx
}
