import { useQuery } from '@tanstack/react-query'
import { ArrowRight, Download, PartyPopper, PhoneCall, PiggyBank, Undo2 } from 'lucide-react'
import { useState } from 'react'
import { Link } from 'react-router'
import { errorMessage } from '@/api/client'
import { api, queryKeys } from '@/api/endpoints'
import type { CallTodayItem } from '@/api/types'
import { ActionBadge, ActionButtons } from '@/components/customer-action'
import { RiskBadge } from '@/components/risk'
import { EmptyState, ErrorState } from '@/components/states'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { useCustomerAction, type ActionVariables } from '@/hooks/use-customer-action'
import { CALL_LIST_CSV_URL, doneUpdate, savingText, snoozeUpdate } from '@/lib/call-today'
import { countLabel, formatMoney } from '@/lib/format'

export const CALL_TODAY_LIMIT = 5
/** Карточку открываем поверх списка, отсортированного так же — по деньгам. */
const BY_LOSS = '?sort=expected_loss&order=desc'

export function CallTodayList({
  items,
  marked,
  busyId,
  onAction,
}: {
  items: CallTodayItem[]
  marked: boolean
  busyId?: number | null
  onAction: (vars: ActionVariables) => void
}) {
  return (
    <ol className="divide-y divide-border" data-testid="call-today-list">
      {items.map((c, i) => {
        const action = c.next_action
        const saving = savingText(action)
        const busy = busyId === c.id
        return (
          <li key={c.id} className="flex flex-col gap-3 py-3.5 first:pt-0 last:pb-0 sm:flex-row sm:items-center" data-testid="call-today-row">
            <span className="hidden w-5 shrink-0 text-right text-[13px] text-subtle-foreground num sm:block">{marked ? '' : i + 1}</span>
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-2">
                <Link
                  to={`/customers/${c.id}${BY_LOSS}`}
                  className="truncate text-sm font-medium text-foreground underline-offset-4 outline-none hover:underline focus-visible:ring-2 focus-visible:ring-ring"
                  title={c.email}
                >
                  {c.email}
                </Link>
                <RiskBadge level={c.risk_level} />
                <ActionBadge action={c.action} />
              </div>
              <p className="mt-1 line-clamp-2 text-[13px] leading-5 text-muted-foreground">
                {action?.title ?? c.main_reason ?? 'Готового действия нет — откройте карточку и посмотрите причины'}
              </p>
              {saving && (
                <p className="mt-1.5 inline-flex items-center gap-1.5 rounded-md bg-risk-low-bg px-2 py-0.5 text-xs font-medium text-risk-low num">
                  <PiggyBank className="size-3.5" aria-hidden />
                  {saving}
                </p>
              )}
            </div>
            <div className="flex items-center justify-between gap-4 sm:justify-end">
              <div className="text-left sm:w-28 sm:text-right">
                <p className="text-[17px] font-semibold tracking-[-0.01em] num" data-testid="call-today-loss">
                  {formatMoney(c.expected_loss, { cents: false })}
                </p>
                <p className="text-xs text-muted-foreground">ожидаемая потеря</p>
              </div>
              {marked ? (
                <Button variant="outline" size="sm" disabled={busy} onClick={() => onAction({ id: c.id, update: null })}>
                  <Undo2 />
                  Вернуть
                </Button>
              ) : (
                <ActionButtons
                  disabled={busy}
                  onDone={() => onAction({ id: c.id, update: doneUpdate() })}
                  onSnooze={(days) => onAction({ id: c.id, update: snoozeUpdate(days) })}
                />
              )}
            </div>
          </li>
        )
      })}
    </ol>
  )
}

function ListSkeleton() {
  return (
    <div className="space-y-4" aria-busy="true" aria-label="Загружаем список">
      {Array.from({ length: 3 }, (_, i) => (
        <div key={i} className="flex items-center gap-4">
          <div className="flex-1 space-y-2">
            <Skeleton className="h-4 w-48" />
            <Skeleton className="h-3.5 w-80 max-w-full" />
          </div>
          <Skeleton className="h-8 w-40" />
        </div>
      ))}
    </div>
  )
}

export function CallTodayCard() {
  const [marked, setMarked] = useState(false)
  const query = useQuery({
    queryKey: queryKeys.callToday(marked),
    queryFn: ({ signal }) => api.callToday({ limit: marked ? 50 : CALL_TODAY_LIMIT, marked }, signal),
  })
  const mutation = useCustomerAction()
  const data = query.data
  const items = data?.items ?? []
  const rest = data ? data.queue_total - items.length : 0

  return (
    <Card data-testid="call-today">
      <CardHeader className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <CardTitle className="flex items-center gap-2">
            <PhoneCall className="size-4 text-muted-foreground" aria-hidden />
            {marked ? 'Отмеченные клиенты' : 'Кому позвонить сегодня'}
          </CardTitle>
          <CardDescription className="mt-1 max-w-2xl">
            {marked
              ? 'Сделанные и отложенные. Отложенные вернутся в список сами, когда подойдёт срок, а сделанные — если риск снова вырастет.'
              : 'Сортируем по деньгам, которые можно спасти: сверху тот, на ком мы потеряем больше всего, если ничего не делать. Клиенты без оценки сюда не попадают.'}
          </CardDescription>
        </div>
        <div className="flex shrink-0 items-center gap-1.5">
          <Button variant="ghost" size="sm" onClick={() => setMarked((v) => !v)} aria-pressed={marked}>
            {marked ? 'К списку звонков' : `Показать отмеченные${data && data.marked_total > 0 ? ` · ${data.marked_total}` : ''}`}
          </Button>
          <Button asChild variant="outline" size="sm">
            <a href={CALL_LIST_CSV_URL} download data-testid="call-today-csv">
              <Download />
              Выгрузить CSV
            </a>
          </Button>
        </div>
      </CardHeader>
      <CardContent>
        {query.isPending ? (
          <ListSkeleton />
        ) : query.isError ? (
          <ErrorState error={query.error} onRetry={() => void query.refetch()} className="py-8" />
        ) : items.length === 0 ? (
          marked ? (
            <EmptyState icon={<PhoneCall />} title="Отмеченных нет" description="Нажмите «Сделано» или «Отложить» у клиента в списке." className="py-8" />
          ) : (
            <EmptyState
              icon={<PartyPopper />}
              title={data && data.marked_total > 0 ? 'Всех обзвонили' : 'Звонить некому'}
              description={
                data && data.marked_total > 0
                  ? 'Все клиенты с риском отмечены. Если у кого-то риск вырастет, он снова появится здесь.'
                  : 'Ни у одного клиента сейчас нет ожидаемой потери.'
              }
              className="py-8"
            />
          )
        ) : (
          <CallTodayList
            items={items}
            marked={marked}
            busyId={mutation.isPending ? mutation.variables?.id : null}
            onAction={(vars) => mutation.mutate(vars)}
          />
        )}
        {mutation.isError && (
          <p role="alert" className="mt-3 text-[13px] text-risk-high">
            {errorMessage(mutation.error)}
          </p>
        )}
        {!marked && data && rest > 0 && (
          <div className="mt-4 flex flex-wrap items-center justify-between gap-2 border-t border-border pt-3 text-[13px] text-muted-foreground">
            <span>
              В очереди ещё {countLabel(rest, ['клиент', 'клиента', 'клиентов'])}, всего под угрозой{' '}
              <span className="font-medium text-foreground num">{formatMoney(data.queue_expected_loss, { cents: false })}</span>
            </span>
            <Link
              to={`/customers${BY_LOSS}`}
              className="inline-flex items-center gap-1 text-link underline-offset-4 outline-none hover:underline focus-visible:ring-2 focus-visible:ring-ring"
            >
              Все клиенты по потере
              <ArrowRight className="size-3.5" />
            </Link>
          </div>
        )}
      </CardContent>
    </Card>
  )
}
