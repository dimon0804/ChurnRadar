import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Bell,
  BellOff,
  CalendarClock,
  ChevronRight,
  Clock,
  Layers,
  PercentCircle,
  Settings2,
  Sigma,
  TrendingUp,
  TriangleAlert,
  Users,
  type LucideIcon,
} from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router'
import { alertsApi, inboxQueryKey, type NotificationItem } from '@/api/alerts'
import { EmptyState, ErrorState } from '@/components/states'
import { Button } from '@/components/ui/button'
import { Sheet, SheetContent, SheetDescription, SheetTitle } from '@/components/ui/sheet'
import { Skeleton } from '@/components/ui/skeleton'
import { Tooltip } from '@/components/ui/tooltip'
import { useNow } from '@/hooks/use-now'
import { badgeText, eventLabel } from '@/lib/alerts'
import { formatAgo } from '@/lib/format'
import { cn } from '@/lib/utils'
import { useLiveEvent } from '@/providers/live'

/** alerts разбирает то же событие scores.updated, что и панель: даём ему секунду дописать уведомления. */
const REFETCH_AFTER_RECALC_MS = 1500
const POLL_MS = 60_000

const EVENT_ICON: Record<string, { icon: LucideIcon; className: string }> = {
  loss_growth: { icon: TrendingUp, className: 'bg-risk-high-bg text-risk-high' },
  big_customer_high: { icon: TriangleAlert, className: 'bg-risk-high-bg text-risk-high' },
  risk_share: { icon: PercentCircle, className: 'bg-risk-medium-bg text-risk-medium' },
  high_count_growth: { icon: Users, className: 'bg-risk-medium-bg text-risk-medium' },
  stale_data: { icon: Clock, className: 'bg-muted text-foreground' },
  digest: { icon: CalendarClock, className: 'bg-muted text-foreground' },
  bulk: { icon: Layers, className: 'bg-risk-high-bg text-risk-high' },
}
const FALLBACK_ICON = { icon: Sigma, className: 'bg-muted text-foreground' }

function InboxRow({ item, now, onNavigate }: { item: NotificationItem; now: number; onNavigate: () => void }) {
  const { icon: Icon, className } = EVENT_ICON[item.event_type] ?? FALLBACK_ICON
  const createdAt = item.created_at ? Date.parse(item.created_at) : null
  return (
    <li>
      <Link
        to={item.link}
        onClick={onNavigate}
        className={cn(
          'group flex items-start gap-3 px-6 py-3.5 outline-none transition-colors hover:bg-accent/60 focus-visible:bg-accent',
          !item.read && 'bg-primary/[0.04]',
        )}
      >
        <span className={cn('mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-full [&_svg]:size-3.5', className)} aria-hidden>
          <Icon />
        </span>
        <span className="min-w-0 flex-1">
          <span className="flex items-center gap-2 text-xs text-muted-foreground">
            <span className="font-medium text-foreground">{eventLabel(item.event_type)}</span>
            {!item.read && <span className="size-1.5 rounded-full bg-primary" aria-label="новое" />}
            <span className="ml-auto shrink-0 num">{formatAgo(createdAt, now)}</span>
          </span>
          <span className="mt-1 block text-[13px] leading-5 font-medium text-pretty break-words">{item.title}</span>
          <span className="mt-0.5 block text-[13px] leading-5 text-pretty break-words text-muted-foreground">{item.message}</span>
        </span>
        <ChevronRight className="mt-1.5 size-4 shrink-0 text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100 group-focus-visible:opacity-100" aria-hidden />
      </Link>
    </li>
  )
}

/**
 * Колокольчик в шапке: личные уведомления пользователя (канал «в панели»). Что и с какими
 * порогами сюда попадает, каждый настраивает сам на странице «Уведомления».
 */
export function AlertsBell() {
  const queryClient = useQueryClient()
  const [open, setOpen] = useState(false)
  const timer = useRef<number | undefined>(undefined)
  const now = useNow(open ? 30_000 : null)

  const query = useQuery({
    queryKey: inboxQueryKey,
    queryFn: ({ signal }) => alertsApi.inbox(signal),
    refetchInterval: POLL_MS,
    staleTime: 5_000,
    retry: 1,
  })
  const markRead = useMutation({
    mutationFn: alertsApi.markRead,
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: inboxQueryKey }),
  })

  useEffect(() => () => window.clearTimeout(timer.current), [])

  useLiveEvent('scores.updated', () => {
    window.clearTimeout(timer.current)
    timer.current = window.setTimeout(
      () => void queryClient.invalidateQueries({ queryKey: inboxQueryKey }),
      REFETCH_AFTER_RECALC_MS,
    )
  })

  const items = query.data?.items ?? []
  const unread = query.data?.unread ?? 0

  const onOpenChange = (next: boolean) => {
    // закрыли панель — всё, что в ней было, прочитано
    if (!next && unread > 0) markRead.mutate()
    setOpen(next)
  }

  const label = unread > 0 ? `Уведомления, новых: ${unread}` : 'Уведомления'

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
          data-testid="alerts-bell"
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
        closeLabel="Закрыть уведомления"
        header={
          <div className="flex items-start gap-3">
            <div className="min-w-0 flex-1">
              <SheetTitle className="text-base font-semibold">Уведомления</SheetTitle>
              <SheetDescription className="mt-0.5 text-[13px] text-muted-foreground">
                Только критические события по вашим порогам.
              </SheetDescription>
            </div>
            <Button asChild variant="outline" size="sm" className="shrink-0">
              <Link to="/notifications" onClick={() => onOpenChange(false)}>
                <Settings2 />
                Настроить
              </Link>
            </Button>
          </div>
        }
      >
        {query.isPending ? (
          <div className="space-y-3 px-6 py-4" aria-busy="true" aria-label="Загрузка">
            {Array.from({ length: 4 }, (_, i) => (
              <Skeleton key={i} className="h-14 rounded-lg" />
            ))}
          </div>
        ) : query.isError ? (
          <ErrorState error={query.error} title="Уведомления недоступны" onRetry={() => void query.refetch()} />
        ) : items.length === 0 ? (
          <EmptyState
            icon={<BellOff />}
            title="Пока тихо"
            description="Здесь появится то, что вы выбрали в настройках: рост ожидаемой потери, крупный клиент в высоком риске, доля выручки под риском."
            action={
              <Button asChild variant="outline" size="sm">
                <Link to="/notifications" onClick={() => onOpenChange(false)}>
                  Выбрать события
                </Link>
              </Button>
            }
          />
        ) : (
          <ul className="divide-y divide-border" data-testid="alerts-inbox">
            {items.map((item) => (
              <InboxRow key={item.id} item={item} now={now} onNavigate={() => onOpenChange(false)} />
            ))}
          </ul>
        )}
      </SheetContent>
    </Sheet>
  )
}
