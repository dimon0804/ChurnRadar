import { createContext, useCallback, useContext, useLayoutEffect, useMemo, useState, type ReactNode } from 'react'

export type ThemePreference = 'light' | 'dark' | 'system'
type Resolved = 'light' | 'dark'

interface ThemeContextValue {
  preference: ThemePreference
  resolved: Resolved
  setPreference: (value: ThemePreference) => void
  toggle: () => void
}

const STORAGE_KEY = 'cr-theme'
const ThemeContext = createContext<ThemeContextValue | null>(null)

function readPreference(): ThemePreference {
  try {
    const value = localStorage.getItem(STORAGE_KEY)
    if (value === 'light' || value === 'dark') return value
  } catch {
    // приватный режим или запрет хранилища
  }
  return 'system'
}

/**
 * Тема «по умолчанию» по макету — тёмная. Системную настройку не читаем: браузеры не отличают
 * «светлая выбрана явно» от «ничего не выбрано», и почти у всех вышло бы светлое оформление.
 * Явный выбор пользователя хранится в localStorage 'cr-theme' (его же читает public/theme-init.js).
 */
const DEFAULT_THEME: Resolved = 'dark'

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [preference, setPreferenceState] = useState<ThemePreference>(readPreference)

  const resolved: Resolved = preference === 'system' ? DEFAULT_THEME : preference

  useLayoutEffect(() => {
    document.documentElement.classList.toggle('dark', resolved === 'dark')
  }, [resolved])

  const setPreference = useCallback((value: ThemePreference) => {
    setPreferenceState(value)
    try {
      if (value === 'system') localStorage.removeItem(STORAGE_KEY)
      else localStorage.setItem(STORAGE_KEY, value)
    } catch {
      // настройка просто не переживёт перезагрузку
    }
  }, [])

  const value = useMemo<ThemeContextValue>(
    () => ({
      preference,
      resolved,
      setPreference,
      toggle: () => setPreference(resolved === 'dark' ? 'light' : 'dark'),
    }),
    [preference, resolved, setPreference],
  )

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>
}

export function useTheme() {
  const ctx = useContext(ThemeContext)
  if (!ctx) throw new Error('useTheme must be used inside ThemeProvider')
  return ctx
}
