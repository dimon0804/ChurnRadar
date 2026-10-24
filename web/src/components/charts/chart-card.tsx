import { BarChart3, Table2 } from 'lucide-react'
import { useState, type ReactNode } from 'react'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { cn } from '@/lib/utils'

/** Цвета графиков из CSS-переменных темы: тёмная тема подхватывается через useCssVars. */
export const CHART_VARS = [
  'risk-high-bar',
  'risk-medium-bar',
  'risk-low-bar',
  'risk-unknown-bar',
  'chart-fact',
  'chart-forecast',
  'chart-risk',
  'chart-rule-b',
  'chart-grid',
  'chart-axis',
  'muted-foreground',
  'foreground',
  'border',
  'card',
] as const

export const AXIS_TICK = { fontSize: 12 }

export function ViewToggle({ value, onChange, label }: { value: 'chart' | 'table'; onChange: (v: 'chart' | 'table') => void; label: string }) {
  return (
    <div className="inline-flex shrink-0 rounded-md border border-border p-0.5" role="group" aria-label={label}>
      {(
        [
          ['chart', 'График', BarChart3],
          ['table', 'Таблица', Table2],
        ] as const
      ).map(([key, text, Icon]) => (
        <button
          key={key}
          type="button"
          onClick={() => onChange(key)}
          aria-pressed={value === key}
          className={cn(
            'inline-flex h-7 items-center gap-1.5 rounded px-2 text-xs font-medium transition-colors outline-none focus-visible:ring-2 focus-visible:ring-ring',
            value === key ? 'bg-accent text-foreground' : 'text-muted-foreground hover:text-foreground',
          )}
        >
          <Icon className="size-3.5" aria-hidden />
          {text}
        </button>
      ))}
    </div>
  )
}

/**
 * Карточка графика: заголовок, переключатель «График / Таблица» и текстовая версия для тех,
 * кто не видит график. aria-label описывает главное словами.
 */
export function ChartCard({
  title,
  description,
  chart,
  table,
  legend,
  footer,
  ariaLabel,
  height = 260,
  className,
  testId,
  action,
}: {
  title: ReactNode
  description?: ReactNode
  chart: ReactNode
  table: ReactNode
  legend?: ReactNode
  footer?: ReactNode
  ariaLabel: string
  height?: number
  className?: string
  testId?: string
  action?: ReactNode
}) {
  const [view, setView] = useState<'chart' | 'table'>('chart')
  return (
    <Card className={cn('flex min-w-0 flex-col', className)} data-testid={testId}>
      <CardHeader className="flex-row flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 flex-1 basis-60 space-y-1">
          <CardTitle>{title}</CardTitle>
          {description && <CardDescription>{description}</CardDescription>}
        </div>
        <div className="flex items-center gap-2">
          {action}
          <ViewToggle value={view} onChange={setView} label={`Вид: ${typeof title === 'string' ? title : 'график'}`} />
        </div>
      </CardHeader>
      <CardContent className="flex min-w-0 flex-1 flex-col">
        {legend && view === 'chart' && <div className="mb-3 flex flex-wrap items-center gap-x-4 gap-y-1.5 text-[13px] text-muted-foreground">{legend}</div>}
        {view === 'chart' ? (
          <div className="w-full min-w-0" style={{ height }} role="img" aria-label={ariaLabel} data-chart>
            {chart}
          </div>
        ) : (
          <div className="overflow-auto rounded-lg border border-border" style={{ maxHeight: Math.max(height, 240) }}>
            {table}
          </div>
        )}
        {footer && <div className="mt-3 text-[13px] leading-5 text-muted-foreground">{footer}</div>}
      </CardContent>
    </Card>
  )
}

export function Swatch({ color, shape = 'square' }: { color: string; shape?: 'square' | 'line' | 'dot' }) {
  if (shape === 'line') {
    return (
      <svg width="18" height="8" viewBox="0 0 18 8" aria-hidden className="shrink-0">
        <line x1="1" y1="4" x2="17" y2="4" stroke={color} strokeWidth="2.5" strokeLinecap="round" />
      </svg>
    )
  }
  return <span className={cn('size-2.5 shrink-0', shape === 'dot' ? 'rounded-full' : 'rounded-[3px]')} style={{ background: color }} aria-hidden />
}

export function LegendItem({ color, shape, children }: { color: string; shape?: 'square' | 'line' | 'dot'; children: ReactNode }) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <Swatch color={color} shape={shape} />
      {children}
    </span>
  )
}

/** Всплывающая подсказка графика. */
export function ChartTip({ title, lines, hint }: { title: ReactNode; lines: [ReactNode, ReactNode][]; hint?: ReactNode }) {
  return (
    <div className="max-w-72 min-w-44 rounded-lg border border-border bg-popover px-3 py-2.5 text-[13px] shadow-pop">
      <p className="mb-1 font-medium">{title}</p>
      {lines.map(([k, v], i) => (
        <p key={i} className="flex justify-between gap-4">
          <span className="text-muted-foreground">{k}</span>
          <span className="font-semibold num">{v}</span>
        </p>
      ))}
      {hint && <p className="mt-1.5 text-xs text-muted-foreground">{hint}</p>}
    </div>
  )
}
