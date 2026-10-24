import { useQuery } from '@tanstack/react-query'
import { AlarmClock, Check, ChevronDown, Mail, MailCheck, TrendingUp, Undo2 } from 'lucide-react'
import { useState } from 'react'
import { api, queryKeys } from '@/api/endpoints'
import { errorMessage } from '@/api/client'
import type { CustomerAction } from '@/api/types'
import { EmailDialog } from '@/components/email-dialog'
import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { useCustomerAction } from '@/hooks/use-customer-action'
import {
  actionStatus,
  emailedUpdate,
  logLine,
  resolvedUpdate,
  snoozeUpdate,
  SNOOZE_OPTIONS,
  type ActionTone,
} from '@/lib/contact-today'
import { formatDateTime } from '@/lib/format'
import { cn } from '@/lib/utils'
import { useAuth } from '@/providers/auth'

const TONE_CLASS: Record<ActionTone, string> = {
  emailed: 'bg-secondary text-secondary-foreground',
  resolved: 'bg-risk-low-bg text-risk-low',
  snoozed: 'bg-muted text-muted-foreground',
  returned: 'bg-risk-high-bg text-risk-high',
  expired: 'bg-muted text-muted-foreground',
}

const TONE_ICON: Record<ActionTone, typeof Check> = {
  emailed: MailCheck,
  resolved: Check,
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

/** Меню «Отметить»: клиент ответил, написали сами, отложить. */
export function MarkMenu({
  onResolved,
  onEmailed,
  onSnooze,
  disabled,
}: {
  onResolved: () => void
  onEmailed: () => void
  onSnooze: (days: number) => void
  disabled?: boolean
}) {
  return (
    // не модальное: после выбора строка исчезает вместе с кнопкой, и модальное меню
    // оставляло бы остальную страницу скрытой от скринридеров (aria-hidden)
    <DropdownMenu modal={false}>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" size="sm" disabled={disabled}>
          Отметить
          <ChevronDown className="-ml-1 text-muted-foreground" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent className="min-w-56">
        <DropdownMenuItem onSelect={onResolved}>
          <Check />
          Клиент ответил, решено
        </DropdownMenuItem>
        <DropdownMenuItem onSelect={onEmailed}>
          <MailCheck />
          Написал сам, ждём ответа
        </DropdownMenuItem>
        <DropdownMenuSeparator />
        <DropdownMenuLabel className="text-xs text-muted-foreground">Отложить</DropdownMenuLabel>
        {SNOOZE_OPTIONS.map((o) => (
          <DropdownMenuItem key={o.days} onSelect={() => onSnooze(o.days)}>
            <AlarmClock />
            {o.label}
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

/** Блок в карточке клиента: статус, «Написать письмо», отметки и короткий журнал. */
export function CustomerActionPanel({ id, email, atRisk }: { id: number; email: string; atRisk: boolean }) {
  const state = useQuery({ queryKey: queryKeys.customerAction(id), queryFn: ({ signal }) => api.customerAction(id, signal) })
  const mutation = useCustomerAction()
  const { canAct } = useAuth()
  const [writing, setWriting] = useState(false)
  const action = state.data?.action ?? null
  const history = state.data?.history ?? []
  const busy = mutation.isPending || state.isPending
  const status = actionStatus(action)

  if (state.isError) return null

  return (
    <section className="px-6 py-5" data-testid="customer-action">
      <div className="mb-3 flex items-baseline justify-between gap-3">
        <h3 className="text-[13px] font-semibold tracking-wide text-muted-foreground uppercase">Связь с клиентом</h3>
        {status && <ActionBadge action={action} />}
      </div>
      <p className="mb-3 text-sm text-muted-foreground">
        {action && action.active
          ? `${status?.detail}. Пока отметка действует, клиента нет в списке «С кем связаться сегодня».`
          : action
            ? `${status?.detail}. Клиент снова в списке.`
            : atRisk
              ? 'Напишите клиенту: текст подставится по главной причине риска. После отправки он уйдёт из списка, пока ждём ответа.'
              : 'Риска сейчас нет, но написать клиенту можно.'}
      </p>
      {!canAct && (
        <p className="text-[13px] text-muted-foreground">
          Писать клиентам и ставить отметки могут маркетолог и администратор. У вас роль аналитика — только просмотр.
        </p>
      )}
      {canAct && (
      <div className="flex flex-wrap items-center gap-2">
        <Button size="sm" onClick={() => setWriting(true)} disabled={state.isPending}>
          <Mail />
          Написать письмо
        </Button>
        <MarkMenu
          disabled={busy}
          onResolved={() => mutation.mutate({ id, update: resolvedUpdate() })}
          onEmailed={() => mutation.mutate({ id, update: emailedUpdate() })}
          onSnooze={(days) => mutation.mutate({ id, update: snoozeUpdate(days) })}
        />
        {action && action.active && (
          <Button variant="ghost" size="sm" disabled={busy} onClick={() => mutation.mutate({ id, update: null })}>
            <Undo2 />
            Вернуть в список
          </Button>
        )}
      </div>
      )}
      {mutation.isError && (
        <p role="alert" className="mt-2 text-[13px] text-risk-high">
          {errorMessage(mutation.error)}
        </p>
      )}
      {history.length > 0 && (
        <ul className="mt-3 space-y-1 border-t border-border pt-3" aria-label="Журнал">
          {history.slice(0, 6).map((h, i) => (
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
      <EmailDialog customerId={id} email={email} open={writing} onOpenChange={setWriting} />
    </section>
  )
}
