import { Info } from 'lucide-react'
import type { ReactNode } from 'react'
import { FlashOverlay } from '@/components/flash'
import { RiskGlyph } from '@/components/risk'
import { Card } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { Tooltip } from '@/components/ui/tooltip'
import { useChangeStamp } from '@/hooks/use-change-stamp'

export function KpiCard({
  label,
  value,
  sub,
  info,
  infoClassName,
  emphasis,
  testId,
  watch,
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
}) {
  const stamp = useChangeStamp(watch ?? null)
  return (
    <Card className="relative flex flex-col px-5 py-4" data-testid={testId}>
      {stamp > 0 && <FlashOverlay key={stamp} />}
      <div className="flex items-center gap-1.5 text-[13px] font-medium text-muted-foreground">
        {emphasis && <RiskGlyph level="high" className="text-risk-high" />}
        <span>{label}</span>
        {info && (
          <Tooltip content={info} className={infoClassName}>
            <button
              type="button"
              className="ml-auto -mr-1 inline-flex size-6 items-center justify-center rounded text-subtle-foreground outline-none hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring"
              aria-label={`Что такое «${label}»`}
            >
              <Info className="size-3.5" />
            </button>
          </Tooltip>
        )}
      </div>
      <div className="mt-2 text-[28px] leading-none font-semibold tracking-[-0.025em] num" data-role="value">
        {value}
      </div>
      {sub && <div className="mt-2.5 line-clamp-2 text-[13px] leading-5 text-muted-foreground">{sub}</div>}
    </Card>
  )
}

export function KpiSkeleton() {
  return (
    <Card className="px-5 py-4">
      <Skeleton className="h-4 w-36" />
      <Skeleton className="mt-3 h-7 w-28" />
      <Skeleton className="mt-3 h-4 w-44" />
    </Card>
  )
}
