import { useEffect, useRef, type ReactNode } from 'react'

/**
 * Размытый радар за обзором: кольца, перекрестие, развёртка и мигающие цели.
 * Чисто декоративный слой — не ловит мышь и скрыт от скринридеров; при reduced-motion стоит на месте.
 */
export function RadarBackdrop() {
  return (
    <div className="radar" aria-hidden data-testid="radar-backdrop">
      {[28, 52, 76, 100].map((w) => (
        <div key={w} className="radar-ring" style={{ width: `${w}%` }} />
      ))}
      <div className="radar-cross-h" />
      <div className="radar-cross-v" />
      <div className="radar-sweep" />
      <i className="radar-blip" style={{ left: '68%', top: '32%' }} />
      <i className="radar-blip" style={{ left: '35%', top: '59%', animationDelay: '1.2s' }} />
      <i className="radar-blip" style={{ left: '57%', top: '74%', animationDelay: '2.1s' }} />
    </div>
  )
}

const FADE_LIMIT = 210

/** Центральный заголовок обзора: плавно растворяется и уезжает при прокрутке, как в макете. */
export function OverviewHero({
  title,
  eyebrow,
  description,
  meta,
  actions,
}: {
  title: string
  eyebrow: string
  description: ReactNode
  meta?: ReactNode
  /** кнопки под заголовком, например «Скачать отчёт» */
  actions?: ReactNode
}) {
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const el = ref.current
    if (!el) return
    const still = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    let frame = 0
    const apply = () => {
      frame = 0
      const y = window.scrollY
      const opacity = Math.max(0, 1 - y / FADE_LIMIT)
      el.style.opacity = String(opacity)
      if (!still) el.style.transform = `translateY(${Math.min(y * 0.35, 75)}px)`
      el.style.pointerEvents = opacity < 0.1 ? 'none' : ''
    }
    const onScroll = () => {
      if (!frame) frame = requestAnimationFrame(apply)
    }
    apply()
    window.addEventListener('scroll', onScroll, { passive: true })
    return () => {
      window.removeEventListener('scroll', onScroll)
      if (frame) cancelAnimationFrame(frame)
    }
  }, [])

  return (
    <>
      <RadarBackdrop />
      <div
        ref={ref}
        className="overview-hero flex min-h-[210px] flex-col items-center justify-center px-2 pt-2 pb-6 text-center sm:min-h-[260px] lg:min-h-[330px] lg:pt-4"
      >
        <p className="eyebrow mb-3.5">{eyebrow}</p>
        <h1 className="mb-2.5 text-[clamp(32px,4.2vw,48px)] leading-[1.1] font-extrabold tracking-[-1.1px]">{title}</h1>
        <p className="max-w-[520px] text-sm leading-6 text-muted-foreground">{description}</p>
        {meta && <div className="mt-3.5 flex flex-wrap items-center justify-center gap-x-3 gap-y-2 opacity-90">{meta}</div>}
        {actions && <div className="mt-4 flex flex-wrap items-center justify-center gap-2">{actions}</div>}
      </div>
    </>
  )
}
