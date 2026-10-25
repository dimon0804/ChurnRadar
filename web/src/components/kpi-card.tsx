import {
  BadgePercent,
  ChartNoAxesCombined,
  Coins,
  Info,
  PiggyBank,
  Scale,
  ShieldAlert,
  TrendingDown,
  UserMinus,
  Wallet,
  type LucideIcon,
} from 'lucide-react'
import { createElement, type CSSProperties, type ReactNode } from 'react'
import { FlashOverlay } from '@/components/flash'
import { Card } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { Tooltip } from '@/components/ui/tooltip'
import { useChangeStamp } from '@/hooks/use-change-stamp'
import { cn } from '@/lib/utils'

/** Иконка карточки по testId — страницы не обязаны её передавать. */
const ICON_BY_TEST_ID: Record<string, LucideIcon> = {
  'kpi-high': ShieldAlert,
  'kpi-expected-loss': Wallet,
  'mkt-mrr': Coins,
  'mkt-arpu': PiggyBank,
  'mkt-ltv': ChartNoAxesCombined,
  'mkt-customer-churn': UserMinus,
  'mkt-revenue-churn': TrendingDown,
  'mkt-ltv-cac': Scale,
}

/** Запасной вариант по подписи, если testId нет. */
function iconByLabel(label: string): LucideIcon {
  const l = label.toLowerCase()
  if (l.includes('экспозиц')) return ChartNoAxesCombined
  if (l.includes('доля') || l.includes('%')) return BadgePercent
  if (l.includes('отток')) return TrendingDown
  if (l.includes('потер')) return Wallet
  return ChartNoAxesCombined
}

function DefaultIcon({ testId, label }: { testId?: string; label: string }) {
  const Icon = (testId && ICON_BY_TEST_ID[testId]) || iconByLabel(label)
  return createElement(Icon)
}

export type KpiTone = 'brand' | 'high' | 'medium' | 'low'

const TONE_STYLE: Record<KpiTone, CSSProperties> = {
  brand: { '--metric-color': 'var(--brand)', '--metric-tint': 'var(--brand-soft)' } as CSSProperties,
  high: { '--metric-color': 'var(--risk-high-bar)', '--metric-tint': 'var(--risk-high-bg)' } as CSSProperties,
  medium: { '--metric-color': 'var(--risk-medium-bar)', '--metric-tint': 'var(--risk-medium-bg)' } as CSSProperties,
  low: { '--metric-color': 'var(--risk-low-bar)', '--metric-tint': 'var(--risk-low-bg)' } as CSSProperties,
}

const ICON_TEXT: Record<KpiTone, string> = {
  brand: 'text-brand',
  high: 'text-risk-high',
  medium: 'text-risk-medium',
  low: 'text-risk-low',
}

export function KpiCard({
  label,
  value,
  sub,
  info,
  infoClassName,
  emphasis,
  testId,
  watch,
  icon,
  tone,
}: {
  label: string
  value: ReactNode
  sub?: ReactNode
  info?: ReactNode
  /** Ширина подсказки: длинным пояснениям тесно в max-w-xs. */
  infoClassName?: string
  emphasis?: boolean
  testId?: string
  /** Число, при изменении которого карточка коротко подсвечивается (после пересчёта). */
  watch?: number | null
  /** Иконка в цветной плашке; по умолчанию подбирается по testId или подписи. */
  icon?: ReactNode
  /** Цвет плашки и круга в углу; emphasis — то же, что tone="high". */
  tone?: KpiTone
}) {
  const stamp = useChangeStamp(watch ?? null)
  const t: KpiTone = tone ?? (emphasis ? 'high' : 'brand')
  return (
    <Card
      className="metric relative flex min-h-[150px] min-w-0 flex-col overflow-hidden p-4 sm:min-h-[170px] sm:px-[22px] sm:py-[21px]"
      style={TONE_STYLE[t]}
      data-testid={testId}
    >
      {stamp > 0 && <FlashOverlay key={stamp} />}
      <div className="flex items-start gap-2 text-xs font-[650] text-muted-foreground sm:text-[13px]">
        <span className="min-w-0 flex-1 pt-0.5 leading-5 break-words hyphens-auto">{label}</span>
        {info && (
          <Tooltip content={info} className={infoClassName}>
            <button
              type="button"
              className="-mr-1 inline-flex size-6 shrink-0 items-center justify-center rounded-md text-subtle-foreground outline-none hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring"
              aria-label={`Что такое «${label}»`}
            >
              <Info className="size-3.5" />
            </button>
          </Tooltip>
        )}
        <span
          className={cn('grid size-9 shrink-0 place-items-center rounded-[11px] max-sm:hidden [&_svg]:size-[18px]', ICON_TEXT[t])}
          style={{ background: 'var(--metric-tint)' }}
          aria-hidden
        >
          {icon ?? <DefaultIcon testId={testId} label={label} />}
        </span>
      </div>
      <div
        className="mt-3 text-[27px] leading-[1.15] font-extrabold tracking-[-1px] break-words num sm:mt-[17px] sm:text-[clamp(27px,2.4vw,36px)]"
        data-role="value"
      >
        {value}
      </div>
      {sub && <div className="mt-2 line-clamp-2 text-xs leading-5 text-muted-foreground">{sub}</div>}
    </Card>
  )
}

export function KpiSkeleton() {
  return (
    <Card className="min-h-[150px] px-5 py-5 sm:min-h-[170px]">
      <div className="flex items-center justify-between gap-3">
        <Skeleton className="h-4 w-36 max-w-full" />
        <Skeleton className="size-9 rounded-[11px]" />
      </div>
      <Skeleton className="mt-5 h-8 w-28 max-w-full" />
      <Skeleton className="mt-3 h-4 w-44 max-w-full" />
    </Card>
  )
}
