import type { ReactNode } from 'react'
import { RefreshCw } from 'lucide-react'
import { formatAgo, formatDateTime } from '@/lib/format'
import { useLive } from '@/providers/live'
import { useNow } from '@/hooks/use-now'
import { Tooltip } from '@/components/ui/tooltip'

export function PageHeader({
  title,
  description,
  actions,
  meta,
}: {
  title: string
  description?: ReactNode
  actions?: ReactNode
  meta?: ReactNode
}) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-x-6 gap-y-3">
      <div className="min-w-0">
        <h1 className="text-[22px] leading-tight font-semibold tracking-[-0.02em]">{title}</h1>
        {description && <p className="mt-1.5 max-w-2xl text-sm leading-6 text-muted-foreground">{description}</p>}
      </div>
      <div className="flex max-w-full min-w-0 flex-wrap items-center gap-3">
        {meta}
        {actions}
      </div>
    </div>
  )
}

/** «Пересчитано 12 с назад · модель v13» — из ответа API, а после SSE — из события. */
export function RecalcNote({ scoredAt, modelVersion }: { scoredAt: string | null | undefined; modelVersion: number | null | undefined }) {
  const { lastRecalc } = useLive()
  const now = useNow(1000)
  const apiTime = scoredAt ? new Date(scoredAt).getTime() : null
  const useLiveData = lastRecalc && (!apiTime || Number.isNaN(apiTime) || lastRecalc.modelVersion >= (modelVersion ?? 0))
  const fromMs = useLiveData ? lastRecalc.receivedAt : apiTime && !Number.isNaN(apiTime) ? apiTime : null
  const version = useLiveData ? lastRecalc.modelVersion : modelVersion

  if (fromMs === null && (version === null || version === undefined)) return null
  const exact = useLiveData ? formatDateTime(lastRecalc.scoredAt) : formatDateTime(scoredAt)

  return (
    <Tooltip content={`Последний пересчёт оценок: ${exact}`}>
      <span tabIndex={0} className="inline-flex items-center gap-1.5 rounded-md text-xs text-muted-foreground outline-none num focus-visible:ring-2 focus-visible:ring-ring">
        <RefreshCw className="size-3.5" />
        {fromMs !== null ? `пересчитано ${formatAgo(Math.min(fromMs, now), now)}` : 'ещё не пересчитано'}
        {typeof version === 'number' && <span className="text-subtle-foreground">· модель v{version}</span>}
      </span>
    </Tooltip>
  )
}
