import { useQuery } from '@tanstack/react-query'
import { CalendarClock, PlugZap } from 'lucide-react'
import { connectorsApi, connectorsQueryKey } from '@/api/connectors'
import { EmptyState, ErrorState } from '@/components/states'
import { Skeleton } from '@/components/ui/skeleton'
import { isRunActive } from '@/lib/sources'
import { cn } from '@/lib/utils'
import { ConnectorCard } from './connector-card'

/** Раздел «Источники»: коннекторы, через которые ingest сам забирает данные (биллинг по API, Яндекс Метрика). */
export function SourcesSection({ className }: { className?: string }) {
  const list = useQuery({
    queryKey: connectorsQueryKey,
    queryFn: ({ signal }) => connectorsApi.list(signal),
    // пока идёт запуск, опрашиваем часто, чтобы статус сменился на глазах
    refetchInterval: (q) => (q.state.data?.items.some((c) => isRunActive(c.status)) ? 1500 : 15000),
  })
  const data = list.data

  return (
    <section className={cn('space-y-3', className)} aria-labelledby="sources-title" data-testid="sources">
      <div className="flex flex-wrap items-end justify-between gap-x-4 gap-y-1">
        <div className="min-w-0">
          <h2 id="sources-title" className="flex items-center gap-2 text-[15px] font-semibold tracking-[-0.01em]">
            <PlugZap className="size-4 text-chart-forecast" aria-hidden />
            Источники
          </h2>
          <p className="mt-0.5 text-[13px] leading-5 text-muted-foreground">
            Сюда система ходит за данными сама: забирает изменения из биллинга по API и визиты из Яндекс Метрики по
            расписанию или по кнопке.
          </p>
        </div>
        {data && !data.scheduler.enabled && (
          <p className="flex items-center gap-1.5 text-xs text-risk-medium">
            <CalendarClock className="size-3.5" aria-hidden />
            Планировщик в ingest выключен, по расписанию ничего не запустится
          </p>
        )}
      </div>

      {list.isPending ? (
        <div className="grid gap-4 xl:grid-cols-2">
          <Skeleton className="h-64 rounded-xl" />
          <Skeleton className="h-64 rounded-xl" />
        </div>
      ) : list.isError || !data ? (
        <div className="rounded-xl border border-border bg-card">
          <ErrorState error={list.error} onRetry={() => void list.refetch()} title="Не удалось загрузить источники" />
        </div>
      ) : data.items.length === 0 ? (
        <div className="rounded-xl border border-border bg-card">
          <EmptyState
            icon={<PlugZap />}
            title="Источников пока нет"
            description="Коннекторы появляются из окружения ingest: задайте адрес биллинга или счётчик Метрики и перезапустите сервис."
          />
        </div>
      ) : (
        <div className="grid items-start gap-4 xl:grid-cols-2">
          {data.items.map((c) => (
            <ConnectorCard key={c.id} connector={c} schedulerEnabled={data.scheduler.enabled} />
          ))}
        </div>
      )}
    </section>
  )
}
