import type { RiskLevel } from '@/api/types'
import { formatScore } from '@/lib/format'
import { RISK_LABEL } from '@/lib/labels'
import { cn } from '@/lib/utils'

const TEXT: Record<RiskLevel, string> = {
  high: 'text-risk-high',
  medium: 'text-risk-medium',
  low: 'text-risk-low',
  unknown: 'text-risk-unknown',
}

const BG: Record<RiskLevel, string> = {
  high: 'bg-risk-high-bg',
  medium: 'bg-risk-medium-bg',
  low: 'bg-risk-low-bg',
  unknown: 'bg-risk-unknown-bg',
}

export const RISK_BAR: Record<RiskLevel, string> = {
  high: 'bg-risk-high-bar',
  medium: 'bg-risk-medium-bar',
  low: 'bg-risk-low-bar',
  unknown: 'bg-risk-unknown-bar',
}

export function riskLevelOf(level: string | null | undefined): RiskLevel {
  return level === 'high' || level === 'medium' || level === 'low' ? level : 'unknown'
}

/** Форма значка дублирует цвет: треугольник, ромб, круг, пустой круг. */
export function RiskGlyph({ level, className }: { level: RiskLevel; className?: string }) {
  const cls = cn('size-2 shrink-0', className)
  switch (level) {
    case 'high':
      return (
        <svg viewBox="0 0 10 10" className={cls} aria-hidden>
          <path d="M5 0.8 9.4 9H0.6Z" fill="currentColor" />
        </svg>
      )
    case 'medium':
      return (
        <svg viewBox="0 0 10 10" className={cls} aria-hidden>
          <path d="M5 0.6 9.4 5 5 9.4 0.6 5Z" fill="currentColor" />
        </svg>
      )
    case 'low':
      return (
        <svg viewBox="0 0 10 10" className={cls} aria-hidden>
          <circle cx="5" cy="5" r="4.2" fill="currentColor" />
        </svg>
      )
    default:
      return (
        <svg viewBox="0 0 10 10" className={cls} aria-hidden>
          <circle cx="5" cy="5" r="3.6" fill="none" stroke="currentColor" strokeWidth="1.6" />
        </svg>
      )
  }
}

export function RiskBadge({
  level: rawLevel,
  className,
  size = 'sm',
}: {
  level: RiskLevel | string | null | undefined
  className?: string
  size?: 'sm' | 'md'
}) {
  const level = riskLevelOf(rawLevel)
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1.5 rounded-full font-medium whitespace-nowrap',
        size === 'sm' ? 'h-6 px-2 text-xs' : 'h-7 px-2.5 text-[13px]',
        TEXT[level],
        BG[level],
        className,
      )}
    >
      <RiskGlyph level={level} />
      {RISK_LABEL[level]}
    </span>
  )
}

export function ScoreBar({
  score,
  level: rawLevel,
  className,
}: {
  score: number | null | undefined
  level: RiskLevel | string | null | undefined
  className?: string
}) {
  const level = riskLevelOf(rawLevel)
  const known = typeof score === 'number' && Number.isFinite(score)
  const pct = known ? Math.min(100, Math.max(0, score)) : 0
  return (
    <div className={cn('flex items-center gap-2.5', className)}>
      <span
        className={cn(
          'inline-flex h-6 min-w-9 items-center justify-center rounded-md px-1.5 text-[13px] font-semibold num',
          TEXT[level],
          BG[level],
        )}
        aria-label={known ? `Риск ${formatScore(score)} из 100` : 'Оценки нет'}
      >
        {formatScore(score)}
      </span>
      <div className="h-1.5 w-16 overflow-hidden rounded-full bg-muted" aria-hidden>
        {known && (
          <div
            className={cn('h-full rounded-full transition-[width] duration-500 ease-out', RISK_BAR[level])}
            style={{ width: `${pct}%` }}
          />
        )}
      </div>
    </div>
  )
}

export { TEXT as RISK_TEXT, BG as RISK_BG }
