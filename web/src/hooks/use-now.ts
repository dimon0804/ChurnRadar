import { useEffect, useState } from 'react'

/** Текущее время с перерисовкой раз в intervalMs; null — не тикать. */
export function useNow(intervalMs: number | null = 1000): number {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (intervalMs === null) return
    const id = window.setInterval(() => setNow(Date.now()), intervalMs)
    return () => window.clearInterval(id)
  }, [intervalMs])
  return now
}
