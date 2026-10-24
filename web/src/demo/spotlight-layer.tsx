import { useEffect, useState } from 'react'
import { findTargets, type Target } from './spotlight'

interface Box {
  top: number
  left: number
  width: number
  height: number
  radius: string
}

const SEARCH_MS = 8000
const SHOW_MS = 9000
const PAD = 6
const PULSE_CSS = `@keyframes cr-demo-pulse {
  0% { box-shadow: 0 0 0 0 color-mix(in srgb, var(--chart-forecast) 45%, transparent); }
  70% { box-shadow: 0 0 0 12px color-mix(in srgb, var(--chart-forecast) 0%, transparent); }
  100% { box-shadow: 0 0 0 0 transparent; }
}`

function sameBoxes(a: Box[], b: Box[]) {
  return (
    a.length === b.length &&
    a.every((x, i) => {
      const y = b[i]
      return y && x.top === y.top && x.left === y.left && x.width === y.width && x.height === y.height
    })
  )
}

function measure(elements: Element[]): Box[] {
  return elements
    .filter((el) => el.isConnected)
    .map((el) => {
      const r = el.getBoundingClientRect()
      const radius = Number.parseFloat(getComputedStyle(el).borderTopLeftRadius) || 8
      return {
        top: Math.round(r.top - PAD),
        left: Math.round(r.left - PAD),
        width: Math.round(r.width + PAD * 2),
        height: Math.round(r.height + PAD * 2),
        radius: `${radius + PAD}px`,
      }
    })
    .filter((b) => b.width > PAD * 2 && b.height > PAD * 2)
}

/** Рамка с пульсом вокруг элементов, о которых сейчас рассказываем. */
export function SpotlightLayer({ targets, nonce }: { targets: Target[]; nonce: number }) {
  const [boxes, setBoxes] = useState<Box[]>([])

  useEffect(() => {
    if (targets.length === 0) return
    let disposed = false
    let frame = 0
    let found: Element[] = []
    let scrolled = false
    let shownAt = 0
    const started = Date.now()

    const loop = () => {
      if (disposed) return
      const now = Date.now()
      if (found.length < targets.length && now - started < SEARCH_MS) {
        const next = findTargets(targets)
        if (next.length > found.length) found = next
      }
      if (found.length > 0 && !scrolled) {
        scrolled = true
        shownAt = now
        found[0]?.scrollIntoView({ behavior: 'smooth', block: 'center', inline: 'nearest' })
      }
      if (shownAt && now - shownAt > SHOW_MS) {
        setBoxes([])
        return
      }
      if (!shownAt && now - started > SEARCH_MS) return
      const measured = measure(found)
      setBoxes((prev) => (sameBoxes(prev, measured) ? prev : measured))
      frame = window.requestAnimationFrame(loop)
    }
    frame = window.requestAnimationFrame(loop)

    return () => {
      disposed = true
      window.cancelAnimationFrame(frame)
      setBoxes([])
    }
  }, [targets, nonce])

  if (boxes.length === 0) return null
  return (
    <div className="pointer-events-none fixed inset-0 z-[65]" aria-hidden data-demo-spotlight>
      <style>{PULSE_CSS}</style>
      {boxes.map((b, i) => (
        <div
          key={i}
          className="absolute border-2 border-chart-forecast bg-chart-forecast/[0.04] transition-[top,left,width,height] duration-150"
          style={{
            top: b.top,
            left: b.left,
            width: b.width,
            height: b.height,
            borderRadius: b.radius,
            animation: 'cr-demo-pulse 1.8s cubic-bezier(0.2, 0, 0, 1) infinite',
          }}
        />
      ))}
    </div>
  )
}
