import { useQuery } from '@tanstack/react-query'
import { CalendarClock, Radio } from 'lucide-react'
import { api, queryKeys } from '@/api/endpoints'
import { Tooltip } from '@/components/ui/tooltip'
import { clockFromReference, clockFromTimeline, dataClockLabel, resolveDataClock, type DataClock } from '@/lib/data-clock'
import { cn } from '@/lib/utils'

/**
 * reference_time из ответа API, если сервер его отдаёт (в контракте пока только у загрузки снимка).
 * undefined — поля нет, null — данные живые.
 */
export function referenceOf(value: unknown): string | null | undefined {
  if (!value || typeof value !== 'object' || !('reference_time' in value)) return undefined
  const ref = (value as { reference_time: unknown }).reference_time
  return typeof ref === 'string' || ref === null ? ref : undefined
}

/** Момент данных для всей панели: явное поле сводки, иначе — по таймлайну сводки. */
export function useDataClock(...preferred: Array<DataClock | null | undefined>): DataClock | null {
  const summary = useQuery({ queryKey: queryKeys.summary, queryFn: ({ signal }) => api.summary(signal) })
  const s = summary.data
  return resolveDataClock(...preferred, clockFromReference(referenceOf(s)), s ? clockFromTimeline(s.timeline) : null)
}

export function DataClockBadge({ clock, className }: { clock: DataClock | null; className?: string }) {
  if (!clock) return null
  const snapshot = clock.kind === 'snapshot'
  const hint = snapshot
    ? 'Это момент, на который сняты данные. Сроки услуг, «сегодня» на графике и даты в причинах считаются от него, а не от текущей даты.'
    : 'Сроки и прогноз считаются от текущего момента и обновляются вместе с данными.'
  return (
    <Tooltip content={hint}>
      <span
        tabIndex={0}
        data-testid="data-clock"
        className={cn(
          'inline-flex h-7 max-w-full items-center gap-1.5 rounded-full border px-2.5 text-xs font-medium outline-none num focus-visible:ring-2 focus-visible:ring-ring',
          snapshot ? 'border-border bg-card text-foreground' : 'border-risk-low/25 bg-risk-low-bg text-risk-low',
          className,
        )}
      >
        {snapshot ? <CalendarClock className="size-3.5 shrink-0 text-muted-foreground" aria-hidden /> : <Radio className="size-3.5 shrink-0" aria-hidden />}
        <span className="truncate">{dataClockLabel(clock)}</span>
      </span>
    </Tooltip>
  )
}
