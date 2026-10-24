import { useEffect, useRef, useState } from 'react'

function prefersReducedMotion() {
  return typeof window !== 'undefined' && window.matchMedia('(prefers-reduced-motion: reduce)').matches
}

/** Плавно докручивает число до нового значения (~0,5 с), форматирование снаружи. */
export function AnimatedNumber({
  value,
  format,
  duration = 520,
  className,
}: {
  value: number | null | undefined
  format: (value: number | null | undefined) => string
  duration?: number
  className?: string
}) {
  const target = typeof value === 'number' && Number.isFinite(value) ? value : null
  const [display, setDisplay] = useState<number | null>(target)
  const fromRef = useRef<number | null>(target)
  const frame = useRef<number | null>(null)

  useEffect(() => {
    const from = fromRef.current
    if (target === null || from === null || from === target || prefersReducedMotion()) {
      fromRef.current = target
      setDisplay(target)
      return
    }
    const start = performance.now()
    const step = (now: number) => {
      const t = Math.min(1, (now - start) / duration)
      const eased = 1 - Math.pow(1 - t, 3)
      const current = from + (target - from) * eased
      fromRef.current = current
      setDisplay(current)
      if (t < 1) frame.current = requestAnimationFrame(step)
      else fromRef.current = target
    }
    frame.current = requestAnimationFrame(step)
    return () => {
      if (frame.current !== null) cancelAnimationFrame(frame.current)
    }
  }, [target, duration])

  return <span className={className}>{format(display)}</span>
}
