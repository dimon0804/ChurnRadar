import { useQuery } from '@tanstack/react-query'
import { ArrowRight, Download, Mail, Mails, PartyPopper, PiggyBank, TrendingDown, Undo2, Users } from 'lucide-react'
import { useState } from 'react'
import { Link } from 'react-router'
import { errorMessage } from '@/api/client'
import { api, queryKeys } from '@/api/endpoints'
import type { ContactTodayItem } from '@/api/types'
import { CampaignDialog } from '@/components/campaign-dialog'
import { ActionBadge, MarkMenu } from '@/components/customer-action'
import { EmailDialog } from '@/components/email-dialog'
import { RiskBadge } from '@/components/risk'
import { EmptyState, ErrorState } from '@/components/states'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { useCustomerAction, type ActionVariables } from '@/hooks/use-customer-action'
import { CONTACT_LIST_CSV_URL, emailedUpdate, resolvedUpdate, savingText, snoozeUpdate, statsLine } from '@/lib/contact-today'
import { countLabel, formatMoney } from '@/lib/format'
import { useAuth } from '@/providers/auth'

export const CONTACT_TODAY_LIMIT = 5
/** Карточку открываем поверх списка, отсортированного так же — по деньгам. */
const BY_LOSS = '?sort=expected_loss&order=desc'

export function ContactTodayList({
  items,
  marked,
  busyId,
  onAction,
  onWrite,
}: {
  items: ContactTodayItem[]
  marked: boolean
  busyId?: number | null
  /** нет обработчиков — список только для чтения (аналитик) */
  onAction?: (vars: ActionVariables) => void
  onWrite?: (item: ContactTodayItem) => void
}) {
  return (
    <ol className="divide-y divide-border" data-testid="contact-today-list">
      {items.map((c, i) => {
        const action = c.next_action
        const saving = savingText(action)
        const busy = busyId === c.id
        return (
          <li key={c.id} className="flex flex-col gap-3 py-3.5 first:pt-0 last:pb-0 sm:flex-row sm:items-center" data-testid="contact-today-row">
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
                <p className="text-[17px] font-semibold tracking-[-0.01em] num" data-testid="contact-today-loss">
                  {formatMoney(c.expected_loss, { cents: false })}
                </p>
                <p className="text-xs text-muted-foreground">ожидаемая потеря</p>
              </div>
              {!onAction || !onWrite ? null : marked ? (
                <Button variant="outline" size="sm" disabled={busy} onClick={() => onAction({ id: c.id, update: null })}>
                  <Undo2 />
                  Вернуть
                </Button>
              ) : (
                <div className="flex items-center gap-1.5">
                  <Button variant="outline" size="sm" disabled={busy} onClick={() => onWrite(c)}>
                    <Mail />
                    Написать
                  </Button>
                  <MarkMenu
                    disabled={busy}
                    onResolved={() => onAction({ id: c.id, update: resolvedUpdate() })}
                    onEmailed={() => onAction({ id: c.id, update: emailedUpdate() })}
                    onSnooze={(days) => onAction({ id: c.id, update: snoozeUpdate(days) })}
                  />
                </div>
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

function StatsLine() {
  const stats = useQuery({ queryKey: queryKeys.mailStats, queryFn: ({ signal }) => api.mailStats(signal) })
  const line = statsLine(stats.data)
  if (!line) return null
  return (
    <p
      className="inline-flex items-center gap-1.5 text-[13px] text-muted-foreground"
      data-testid="mail-stats"
      title="Помогло — у клиента после письма снизился риск или вырос баланс. Это простая оценка: она не отделяет эффект письма от того, что клиент сделал бы и сам."
    >
      <TrendingDown className="size-3.5" aria-hidden />
      {line}
    </p>
  )
}

export function ContactTodayCard() {
  const [marked, setMarked] = useState(false)
  const [writeTo, setWriteTo] = useState<ContactTodayItem | null>(null)
  const [mass, setMass] = useState(false)
  const query = useQuery({
    queryKey: queryKeys.contactToday(marked),
    queryFn: ({ signal }) => api.contactToday({ limit: marked ? 50 : CONTACT_TODAY_LIMIT, marked }, signal),
  })
  const mutation = useCustomerAction()
  const { canAct } = useAuth()
  const data = query.data
  const items = data?.items ?? []
  const rest = data ? data.queue_total - items.length : 0

  return (
    <Card data-testid="contact-today">
      <CardHeader className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <CardTitle className="flex items-center gap-2">
            <Users className="size-4 text-muted-foreground" aria-hidden />
            {marked ? 'Отмеченные клиенты' : 'С кем связаться сегодня'}
          </CardTitle>
          <CardDescription className="mt-1 max-w-2xl">
            {marked
              ? 'Кому написали, кто ответил и кого отложили. Клиент вернётся в список сам, если на письмо не ответят за неделю, кончится отложка или вырастет риск.'
              : 'Сортируем по деньгам, которые можно спасти: сверху тот, на ком мы потеряем больше всего, если ничего не делать. Клиенты без оценки сюда не попадают.'}
          </CardDescription>
        </div>
        <div className="flex shrink-0 flex-wrap items-center gap-1.5">
          {canAct && !marked && data && data.queue_total > 0 && (
            <Button size="sm" onClick={() => setMass(true)} data-testid="contact-today-mass">
              <Mails />
              Написать всем · {data.queue_total}
            </Button>
          )}
          <Button variant="ghost" size="sm" onClick={() => setMarked((v) => !v)} aria-pressed={marked}>
            {marked ? 'К списку' : `Показать отмеченные${data && data.marked_total > 0 ? ` · ${data.marked_total}` : ''}`}
          </Button>
          <Button asChild variant="outline" size="sm">
            <a href={CONTACT_LIST_CSV_URL} download data-testid="contact-today-csv">
              <Download />
              CSV
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
            <EmptyState icon={<Mail />} title="Отмеченных нет" description="Напишите клиенту или отметьте его в списке." className="py-8" />
          ) : (
            <EmptyState
              icon={<PartyPopper />}
              title={data && data.marked_total > 0 ? 'Со всеми связались' : 'Писать некому'}
              description={
                data && data.marked_total > 0
                  ? 'Всем клиентам с риском уже написали или отметили их. Если кто-то не ответит или риск вырастет, он снова появится здесь.'
                  : 'Ни у одного клиента сейчас нет ожидаемой потери.'
              }
              className="py-8"
            />
          )
        ) : (
          <ContactTodayList
            items={items}
            marked={marked}
            busyId={mutation.isPending ? mutation.variables?.id : null}
            onAction={canAct ? (vars) => mutation.mutate(vars) : undefined}
            onWrite={canAct ? setWriteTo : undefined}
          />
        )}
        {mutation.isError && (
          <p role="alert" className="mt-3 text-[13px] text-risk-high">
            {errorMessage(mutation.error)}
          </p>
        )}
        <div className="mt-4 flex flex-wrap items-center justify-between gap-2 border-t border-border pt-3 text-[13px] text-muted-foreground empty:hidden">
          <StatsLine />
          {!marked && data && rest > 0 && (
            <span className="flex flex-wrap items-center gap-x-3 gap-y-1">
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
            </span>
          )}
        </div>
      </CardContent>
      {writeTo && (
        <EmailDialog
          customerId={writeTo.id}
          email={writeTo.email}
          open
          onOpenChange={(open) => !open && setWriteTo(null)}
        />
      )}
      <CampaignDialog target={{ worklist: true }} title="Написать всем в очереди" open={mass} onOpenChange={setMass} />
    </Card>
  )
}
