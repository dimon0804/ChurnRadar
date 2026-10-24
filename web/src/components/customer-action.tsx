import { useQuery } from '@tanstack/react-query'
import { AlarmClock, Check, ChevronDown, TrendingUp, Undo2 } from 'lucide-react'
import { api, queryKeys } from '@/api/endpoints'
import { errorMessage } from '@/api/client'
import type { CustomerAction, CustomerActionLogEntry } from '@/api/types'
import { Button } from '@/components/ui/button'
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from '@/components/ui/dropdown-menu'
import { useCustomerAction } from '@/hooks/use-customer-action'
import { actionStatus, doneUpdate, snoozeUpdate, SNOOZE_OPTIONS, type ActionTone } from '@/lib/call-today'
import { formatDate, formatDateTime } from '@/lib/format'
import { cn } from '@/lib/utils'

const TONE_CLASS: Record<ActionTone, string> = {
  done: 'bg-risk-low-bg text-risk-low',
  snoozed: 'bg-muted text-muted-foreground',
  returned: 'bg-risk-high-bg text-risk-high',
  expired: 'bg-muted text-muted-foreground',
}

const TONE_ICON: Record<ActionTone, typeof Check> = {
  done: Check,
  snoozed: AlarmClock,
  returned: TrendingUp,
  expired: AlarmClock,
}

export function ActionBadge({ action, className }: { action: CustomerAction | null | undefined; className?: string }) {
  const status = actionStatus(action)
  if (!status) return null
  const Icon = TONE_ICON[status.tone]
  return (
    <span
      className={cn(
        'inline-flex h-6 items-center gap-1 rounded-full px-2 text-xs font-medium whitespace-nowrap',
        TONE_CLASS[status.tone],
        className,
      )}
      title={status.detail}
      data-testid="action-badge"
    >
      <Icon className="size-3.5" aria-hidden />
      {status.label}
    </span>
  )
}

/** Кнопки «Сделано» и «Отложить ▾». */
export function ActionButtons({
  onDone,
  onSnooze,
  disabled,
  size = 'sm',
}: {
  onDone: () => void
  onSnooze: (days: number) => void
  disabled?: boolean
  size?: 'sm' | 'default'
}) {
  return (
    <div className="flex items-center gap-1.5">
      <Button variant="outline" size={size} onClick={onDone} disabled={disabled}>
        <Check />
        Сделано
      </Button>
      {/* не модальное: после выбора строка исчезает вместе с кнопкой, и модальное меню
          оставляло бы остальную страницу скрытой от скринридеров (aria-hidden) */}
      <DropdownMenu modal={false}>
        <DropdownMenuTrigger asChild>
          <Button variant="ghost" size={size} disabled={disabled}>
            <AlarmClock />
            Отложить
            <ChevronDown className="-ml-1 text-muted-foreground" />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent className="min-w-40">
          {SNOOZE_OPTIONS.map((o) => (
            <DropdownMenuItem key={o.days} onSelect={() => onSnooze(o.days)}>
              {o.label}
            </DropdownMenuItem>
          ))}
        </DropdownMenuContent>
      </DropdownMenu>
    </div>
  )
}

const LOG_LABEL: Record<CustomerActionLogEntry['status'], string> = {
  done: 'Сделано',
  snoozed: 'Отложили',
  cleared: 'Сняли отметку',
}

function logLine(entry: CustomerActionLogEntry): string {
  const what = entry.status === 'snoozed' ? `${LOG_LABEL.snoozed} до ${formatDate(entry.snoozed_until)}` : LOG_LABEL[entry.status]
  return entry.note ? `${what}: «${entry.note}»` : what
}

/** Блок в карточке клиента: текущая отметка, кнопки и короткий журнал. */
export function CustomerActionPanel({ id, atRisk }: { id: number; atRisk: boolean }) {
  const state = useQuery({ queryKey: queryKeys.customerAction(id), queryFn: ({ signal }) => api.customerAction(id, signal) })
  const mutation = useCustomerAction()
  const action = state.data?.action ?? null
  const history = state.data?.history ?? []
  const busy = mutation.isPending || state.isPending
  const status = actionStatus(action)

  if (state.isError) return null
  // без риска и без отметок блок не нужен: звонить незачем
  if (!atRisk && !action && history.length === 0) return null

  return (
    <section className="px-6 py-5" data-testid="customer-action">
      <div className="mb-3 flex items-baseline justify-between gap-3">
        <h3 className="text-[13px] font-semibold tracking-wide text-muted-foreground uppercase">Работа с клиентом</h3>
        {status && <ActionBadge action={action} />}
      </div>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        {action && action.active ? (
          <>
            <p className="min-w-0 flex-1 text-sm text-muted-foreground">
              {status?.detail}. Пока отметка действует, клиента нет в списке «Кому позвонить сегодня».
            </p>
            <Button variant="outline" size="sm" disabled={busy} onClick={() => mutation.mutate({ id, update: null })}>
              <Undo2 />
              Вернуть в список
            </Button>
          </>
        ) : (
          <>
            <p className="min-w-0 flex-1 text-sm text-muted-foreground">
              {action ? `${status?.detail}. Клиент снова в списке.` : 'Связались с клиентом — отметьте, и он уйдёт из списка «Кому позвонить сегодня».'}
            </p>
            <ActionButtons
              disabled={busy}
              onDone={() => mutation.mutate({ id, update: doneUpdate() })}
              onSnooze={(days) => mutation.mutate({ id, update: snoozeUpdate(days) })}
            />
          </>
        )}
      </div>
      {mutation.isError && (
        <p role="alert" className="mt-2 text-[13px] text-risk-high">
          {errorMessage(mutation.error)}
        </p>
      )}
      {history.length > 0 && (
        <ul className="mt-3 space-y-1 border-t border-border pt-3" aria-label="Журнал отметок">
          {history.slice(0, 5).map((h, i) => (
            <li key={`${h.changed_at}-${i}`} className="flex gap-2 text-[13px] leading-5">
              <span className="mt-2 size-1.5 shrink-0 rounded-full bg-subtle-foreground/60" aria-hidden />
              <span className="min-w-0 flex-1">{logLine(h)}</span>
              <span className="shrink-0 text-xs text-subtle-foreground num">
                {formatDateTime(h.changed_at)}
                {h.changed_by ? ` · ${h.changed_by}` : ''}
              </span>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
