import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Bell, BellOff, ChevronRight, Layers, Sigma, TrendingUp, TriangleAlert } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router'
import { alertsQueryKey, fetchAlerts, type AlertFeed, type AlertItem } from '@/api/alerts'
import { EmptyState, ErrorState } from '@/components/states'
import { Button } from '@/components/ui/button'
import { Sheet, SheetContent, SheetDescription, SheetTitle } from '@/components/ui/sheet'
import { Skeleton } from '@/components/ui/skeleton'
import { Tooltip } from '@/components/ui/tooltip'
import { useNow } from '@/hooks/use-now'
import { badgeText, countUnread, KIND_LABEL, readSeenId, writeSeenId } from '@/lib/alerts'
import { formatAgo } from '@/lib/format'
import { cn } from '@/lib/utils'
import { useLiveEvent } from '@/providers/live'

/** alerts разбирает то же событие scores.updated, что и панель: даём ему секунду дописать ленту. */
const REFETCH_AFTER_RECALC_MS = 1500
const POLL_MS = 60_000

const KIND_ICON: Record<AlertItem['kind'], { icon: typeof Bell; className: string }> = {
  high_risk: { icon: TriangleAlert, className: 'bg-risk-high-bg text-risk-high' },
  loss_jump: { icon: TrendingUp, className: 'bg-risk-medium-bg text-risk-medium' },
  total_loss_jump: { icon: Sigma, className: 'bg-muted text-foreground' },
  bulk: { icon: Layers, className: 'bg-risk-high-bg text-risk-high' },
}

async function loadFeed(signal: AbortSignal): Promise<AlertFeed> {
  const feed = await fetchAlerts(signal)
  // первый визит: всё, что уже было, считаем просмотренным, новыми будут только следующие
  if (readSeenId() === null) writeSeenId(feed.latest_id ?? 0)
  return feed
}

function channelsLine(feed: AlertFeed | undefined): string {
  if (!feed) return 'Клиенты, которые перешли в высокий риск, и заметный рост ожидаемой потери.'
  const extra = [feed.channels.webhook && 'webhook', feed.channels.telegram && 'Telegram'].filter(Boolean)
  return extra.length
    ? `Те же оповещения уходят в ${extra.join(' и ')}.`
    : 'Внешние каналы не настроены, оповещения видны только здесь.'
}

function AlertRow({ item, fresh, now, onNavigate }: { item: AlertItem; fresh: boolean; now: number; onNavigate: () => void }) {
  const { icon: Icon, className } = KIND_ICON[item.kind]
  const createdAt = item.created_at ? Date.parse(item.created_at) : null
  return (
    <li>
      <Link
        to={item.link}
        onClick={onNavigate}
        className={cn(
          'group flex items-start gap-3 px-6 py-3.5 outline-none transition-colors hover:bg-accent/60 focus-visible:bg-accent',
          fresh && 'bg-primary/[0.04]',
        )}
      >
        <span className={cn('mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-full [&_svg]:size-3.5', className)} aria-hidden>
          <Icon />
        </span>
        <span className="min-w-0 flex-1">
          <span className="flex items-center gap-2 text-xs text-muted-foreground">
            <span className="font-medium text-foreground">{KIND_LABEL[item.kind]}</span>
            {fresh && <span className="size-1.5 rounded-full bg-primary" aria-label="новое" />}
            <span className="ml-auto shrink-0 num">{formatAgo(createdAt, now)}</span>
          </span>
          <span className="mt-1 block text-[13px] leading-5 text-pretty break-words">{item.message}</span>
        </span>
        <ChevronRight className="mt-1.5 size-4 shrink-0 text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100 group-focus-visible:opacity-100" aria-hidden />
      </Link>
    </li>
  )
}

export function AlertsBell() {
  const queryClient = useQueryClient()
  const [open, setOpen] = useState(false)
  const [seen, setSeen] = useState<number | null>(() => readSeenId())
  // что считалось просмотренным в момент открытия: эти записи подсвечиваем как новые
  const [freshAfter, setFreshAfter] = useState<number | null>(null)
  const timer = useRef<number | undefined>(undefined)
  const now = useNow(open ? 30_000 : null)

  const query = useQuery({
    queryKey: alertsQueryKey,
    queryFn: ({ signal }) => loadFeed(signal),
    refetchInterval: POLL_MS,
    staleTime: 5_000,
    retry: 1,
  })

  useEffect(() => () => window.clearTimeout(timer.current), [])

  useLiveEvent('scores.updated', () => {
    window.clearTimeout(timer.current)
    timer.current = window.setTimeout(
      () => void queryClient.invalidateQueries({ queryKey: alertsQueryKey }),
      REFETCH_AFTER_RECALC_MS,
    )
  })

  const items = query.data?.items ?? []
  const seenId = seen ?? readSeenId()
  const unread = countUnread(items, seenId)

  const markSeen = () => {
    const latest = query.data?.latest_id
    if (latest == null) return
    writeSeenId(latest)
    setSeen(latest)
  }

  const onOpenChange = (next: boolean) => {
    if (next) setFreshAfter(seenId)
    markSeen()
    setOpen(next)
  }

  const label = unread > 0 ? `Оповещения, новых: ${unread}` : 'Оповещения'

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <Tooltip content={label} side="bottom">
        <Button
          variant="ghost"
          size="icon-sm"
          onClick={() => onOpenChange(true)}
          aria-label={label}
          aria-haspopup="dialog"
          className="relative text-muted-foreground"
        >
          <Bell />
          {unread > 0 && (
            <span
              className="absolute -top-0.5 -right-0.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-risk-high-bar px-1 text-[10px] leading-none font-semibold text-white num"
              aria-hidden
            >
              {badgeText(unread)}
            </span>
          )}
        </Button>
      </Tooltip>
      <SheetContent
        className="max-w-[min(440px,100vw)]"
        closeLabel="Закрыть оповещения"
        header={
          <>
            <SheetTitle className="text-base font-semibold">Оповещения</SheetTitle>
            <SheetDescription className="mt-0.5 text-[13px] text-muted-foreground">{channelsLine(query.data)}</SheetDescription>
          </>
        }
      >
        {query.isPending ? (
          <div className="space-y-3 px-6 py-4" aria-busy="true" aria-label="Загрузка">
            {Array.from({ length: 4 }, (_, i) => (
              <Skeleton key={i} className="h-14 rounded-lg" />
            ))}
          </div>
        ) : query.isError ? (
          <ErrorState error={query.error} title="Лента оповещений недоступна" onRetry={() => void query.refetch()} />
        ) : items.length === 0 ? (
          <EmptyState
            icon={<BellOff />}
            title="Пока тихо"
            description="Здесь появятся клиенты, которые перешли в высокий риск или у которых заметно выросла ожидаемая потеря."
          />
        ) : (
          <>
            <ul className="divide-y divide-border">
              {items.map((item) => (
                <AlertRow
                  key={item.id}
                  item={item}
                  fresh={freshAfter !== null && item.id > freshAfter}
                  now={now}
                  onNavigate={() => onOpenChange(false)}
                />
              ))}
            </ul>
            {query.data && query.data.total > items.length && (
              <p className="px-6 py-4 text-xs text-muted-foreground">
                Показаны последние {items.length} из {query.data.total}.
              </p>
            )}
          </>
        )}
      </SheetContent>
    </Sheet>
  )
}
