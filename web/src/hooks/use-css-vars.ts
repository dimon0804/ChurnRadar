import { useMemo, useSyncExternalStore } from 'react'

function subscribe(onChange: () => void) {
  const observer = new MutationObserver(onChange)
  observer.observe(document.documentElement, { attributes: true, attributeFilter: ['class'] })
  return () => observer.disconnect()
}

const themeSnapshot = () => (document.documentElement.classList.contains('dark') ? 'dark' : 'light')

/**
 * Значения CSS-переменных темы для библиотек, которым нужен готовый цвет (Recharts).
 * Перечитываются, когда на <html> реально сменился класс темы.
 */
export function useCssVars<const K extends string>(names: readonly K[]): Record<K, string> {
  const theme = useSyncExternalStore(subscribe, themeSnapshot, () => 'light')
  const key = names.join('|')
  return useMemo(() => {
    void theme
    const style = getComputedStyle(document.documentElement)
    const out = {} as Record<K, string>
    for (const name of key.split('|') as K[]) out[name] = style.getPropertyValue(`--${name}`).trim()
    return out
  }, [key, theme])
}
